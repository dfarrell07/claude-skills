#!/usr/bin/env python3
"""Keep an interview of questions that would close the gaps in a promotion packet.

Reads the target level document and the draft packet, asks for evidence wherever
a criterion is "not yet shown" (a gap) or only "meets" (a stretch toward
"exceeds"), and keeps the questions in a JSON state file across runs. Each run
adds questions for new gaps, retires questions whose gap closed, links reworded
questions to the ones they replace, and never drops an answer.
"""

import argparse
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import sys

ITEM = re.compile(r'^(\d+)\.\s+([^:]+):\s*(.*)$')
DRAFT_ITEM = re.compile(r'^###\s+(\d+)\.\s+(.+?)\s*$')
RATING = re.compile(r'^\s*-\s*Rating:\s*\**\s*(exceeds|meets|not yet shown)', re.I)
LEAD = re.compile(r'\*\(lead\)\*\s*(.+?)\s*(?:Next step.*)?$')
NEXT = re.compile(r'Next step:\s*(.+?)\s*$')  # "Next step (collect):" is skipped


def logical_lines(text):
    """Join wrapped Markdown lines so a bullet or paragraph is one line."""
    joined = []
    for line in text.splitlines():
        stripped = line.strip()
        starts = (not stripped or stripped.startswith(('#', '- ', '* ', '|'))
                  or ITEM.match(stripped) or not joined or not joined[-1].strip())
        if starts:
            joined.append(line)
        else:
            joined[-1] += ' ' + stripped
    return joined
SOURCE = ('For every answer, give a link or a person who can confirm it: an '
          '"exceeds" needs evidence someone else can check.')

STRETCH = ('This is rated "meets". What evidence would show you go beyond it in '
           'scope, reach or duration: more teams, a wider community, or years of '
           'sustained work?')


def criteria(level_text):
    """Numbered items of the level document as {key: (name, text)}, by section."""
    items, section, key = {}, None, None
    for line in level_text.splitlines():
        if line.startswith('## '):
            section, key = line[3:].strip(), None
            continue
        match = ITEM.match(line.strip())
        if section and match and not line.startswith(' '):
            key = f'{section}/{match.group(1)}'
            items[key] = [match.group(2).strip(), match.group(3).strip()]
        elif key and line.startswith(' ') and line.strip():
            items[key][1] += ' ' + line.strip()
        elif not line.strip():
            key = None
    return {key: tuple(value) for key, value in items.items()}


def ratings(draft_text):
    """Ratings, unverified leads and next steps from the draft, keyed like criteria().

    A missing rating means a gap.
    """
    found, leads, steps, section, key = {}, {}, {}, None, None
    for line in logical_lines(draft_text):
        if line.startswith('## '):
            section, key = line[3:].strip(), None
        elif (match := DRAFT_ITEM.match(line)) and section:
            key = f'{section}/{match.group(1)}'
        elif key:
            if match := LEAD.search(line):
                leads.setdefault(key, []).append(match.group(1))
            if match := NEXT.search(line):
                steps.setdefault(key, match.group(1))
            if match := RATING.search(line):
                found.setdefault(key, match.group(1).lower())
    return found, leads, steps


def wanted(level, rated, leads=None, steps=None):
    """Desired questions as {text: (priority, item keys)}."""
    questions, leads, steps = {}, leads or {}, steps or {}
    for key, (name, text) in level.items():
        rating = rated.get(key, 'not yet shown')
        step = steps.get(key, '')
        if step.lower().startswith('interview'):
            step = ''
        texts = []
        if rating == 'meets':
            priority = 'stretch'
            texts.append(f'{name} meets the bar. To show it exceeds: {step} What can '
                         'you point to?' if step else f'{name}: {STRETCH}')
        elif rating != 'exceeds':
            priority = 'gap'
            texts.append(f'The target level says: "{text}" ' + (
                f'{step} What can you point to?' if step else
                'Describe one or two times you did this: what you did, which teams '
                'or components it spanned, and what changed.'))
        else:
            priority = 'gap'
        for lead in leads.get(key, []):
            texts.append(f'About "{lead}": {step} What can you point to?' if step else
                         f'Your notes say: "{lead}" Who or what confirms it, and what '
                         'exactly was your part?')
        for question in texts:
            old_priority, keys = questions.get(question, (priority, []))
            best = 'gap' if 'gap' in (old_priority, priority) else 'stretch'
            questions[question] = (best, sorted(set(keys) | {key}))
    return questions


def question_id(text):
    return 'q-' + hashlib.sha256(text.encode()).hexdigest()[:8]


def update(state, desired):
    """Fold one run's desired questions into the state; returns what changed."""
    run = state.get('runs', 0) + 1
    state['runs'] = run
    by_id = {q['id']: q for q in state.setdefault('questions', [])}
    added, retired = [], []
    for text, (priority, keys) in desired.items():
        qid = question_id(text)
        if qid in by_id:
            question = by_id[qid]
            if question['status'] == 'retired':
                question['status'] = 'open'
            question.update(priority=priority, items=keys, last_seen=run)
        else:
            question = {'id': qid, 'text': text, 'items': keys, 'priority': priority,
                        'status': 'open', 'answer': None, 'first_seen': run,
                        'last_seen': run}
            state['questions'].append(question)
            by_id[qid] = question
            added.append(question)
    for question in state['questions']:
        if question['status'] == 'open' and question['last_seen'] != run:
            question.update(status='retired', retired_run=run)
            retired.append(question)
    for new in added:
        for old in retired:
            if (set(old['items']) & set(new['items']) and not old.get('answer')
                    and SequenceMatcher(None, old['text'], new['text']).ratio() > 0.6):
                new.setdefault('replaces', []).append(old['id'])
    return added, retired


def render(state, level):
    """Questions for the user, gaps first, in the level document's order."""
    order = {key: index for index, key in enumerate(level)}
    lines = ['# Interview', '',
             f'Run {state["runs"]}. Answers become recorded evidence. {SOURCE}', '']
    for priority, title in (('gap', 'Gaps (criterion not yet shown)'),
                            ('stretch', 'Stretch (meets, could exceed)')):
        open_questions = [q for q in state['questions'] if q['status'] == 'open'
                          and not q.get('answer') and q['priority'] == priority]
        if not open_questions:
            continue
        lines += [f'## {title}', '']
        for question in sorted(open_questions, key=lambda q: (
                min(order.get(key, len(order)) for key in q['items']), q['id'])):
            names = ', '.join(f'{key} {level[key][0]}' if key in level else key
                              for key in question['items'])
            lines += [f'- **{names}** ({question["id"]}): {question["text"]}']
        lines.append('')
    answered = sum(1 for q in state['questions'] if q.get('answer'))
    retired = sum(1 for q in state['questions'] if q['status'] == 'retired')
    lines.append(f'{answered} answered, {retired} retired.')
    return '\n'.join(lines) + '\n'


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument('--level', required=True, type=Path)
    parser.add_argument('--draft', required=True, type=Path)
    parser.add_argument('--state', required=True, type=Path)
    parser.add_argument('--out', required=True, type=Path)
    args = parser.parse_args(argv)
    level = criteria(args.level.read_text())
    if not level:
        sys.exit(f'No numbered criteria found in {args.level}')
    state = json.loads(args.state.read_text()) if args.state.exists() else {}
    rated, leads, steps = ratings(args.draft.read_text())
    added, retired = update(state, wanted(level, rated, leads, steps))
    args.state.write_text(json.dumps(state, indent=2) + '\n')
    args.out.write_text(render(state, level))
    open_count = sum(1 for q in state['questions']
                     if q['status'] == 'open' and not q.get('answer'))
    replaced = sum(1 for q in added if q.get('replaces'))
    print(f'run {state["runs"]}: {open_count} open, {len(added)} added '
          f'({replaced} rewording earlier questions), {len(retired)} retired')


if __name__ == '__main__':
    main()
