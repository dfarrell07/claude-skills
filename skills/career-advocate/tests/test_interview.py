#!/usr/bin/env python3
"""Offline tests for the interview generator; all criteria are synthetic."""

import importlib.util
import json
from pathlib import Path
import sys
import tempfile
import unittest

SCRIPT = Path(__file__).resolve().parents[1] / 'scripts' / 'interview.py'
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location('interview', SCRIPT)
interview = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(interview)

LEVEL = """# Example Level

## Summary

Prose that is not a criterion.

## Responsibilities

1. Mentoring: Across teams, coaches and mentors senior
   engineers.
2. Widgets: Builds widgets for several teams.

## Skills

1. Mentoring: Coaches senior engineers.
"""


def draft(**ratings):
    lines = ['# Draft', '', '## Responsibilities', '']
    for number, rating in ratings.items():
        lines += [f'### {number[1:]}. Something', '', f'- Rating: {rating}.', '']
    return '\n'.join(lines)


class InterviewTest(unittest.TestCase):
    def test_criteria_by_section_with_wrapped_text(self):
        level = interview.criteria(LEVEL)
        self.assertEqual(set(level), {'Responsibilities/1', 'Responsibilities/2',
                                      'Skills/1'})
        self.assertEqual(level['Responsibilities/1'],
                         ('Mentoring', 'Across teams, coaches and mentors senior engineers.'))

    def test_ratings_default_to_gap(self):
        rated, leads, steps = interview.ratings(draft(r1='**meets**'))
        self.assertEqual(rated, {'Responsibilities/1': 'meets'})
        self.assertEqual((leads, steps), ({}, {}))

    def test_leads_become_confirmation_questions(self):
        text = draft(r2='not yet shown').replace(
            '- Rating', '- *(lead)* Built the widget pipeline.\n- Rating')
        rated, leads, _ = interview.ratings(text)
        self.assertEqual(leads, {'Responsibilities/2': ['Built the widget pipeline.']})
        desired = interview.wanted(interview.criteria(LEVEL), rated, leads)
        self.assertTrue(any('Built the widget pipeline.' in question
                            for question in desired))

    def test_gap_stretch_and_exceeds(self):
        level = interview.criteria(LEVEL)
        desired = interview.wanted(level, {'Responsibilities/1': 'exceeds',
                                           'Responsibilities/2': 'meets'})
        keys = {key for _, (_, keys) in desired.items() for key in keys}
        self.assertNotIn('Responsibilities/1', keys)
        priorities = {keys[0]: priority for priority, keys in desired.values()
                      if keys == ['Responsibilities/2']}
        self.assertEqual(priorities, {'Responsibilities/2': 'stretch'})

    def test_unknown_criterion_falls_back_to_quoting_it(self):
        level = interview.criteria(LEVEL)
        desired = interview.wanted(level, {})
        widgets = [text for text, (_, keys) in desired.items()
                   if keys == ['Responsibilities/2']]
        self.assertEqual(len(widgets), 1)
        self.assertIn('Builds widgets for several teams.', widgets[0])

    def test_gap_questions_quote_the_criterion_and_use_the_next_step(self):
        text = draft(r2='not yet shown').replace(
            '- Rating', 'Next step: name the teams that use it.\n- Rating')
        rated, leads, steps = interview.ratings(text)
        desired = interview.wanted(interview.criteria(LEVEL), rated, leads, steps)
        widgets = [q for q, (_, keys) in desired.items() if keys == ['Responsibilities/2']]
        self.assertEqual(len(widgets), 1)
        self.assertIn('Builds widgets for several teams.', widgets[0])
        self.assertIn('name the teams that use it.', widgets[0])

    def test_only_the_rating_line_counts(self):
        text = draft(r1='not yet shown').replace(
            '- Rating', "Last year's rating: exceeds.\n- Rating")
        rated, _, _ = interview.ratings(text)
        self.assertEqual(rated, {'Responsibilities/1': 'not yet shown'})

    def test_inline_leads_and_next_steps(self):
        text = draft(r2='meets').replace(
            '- Rating', 'Not shown. *(lead)* Ran the widget guild. Next step: name the '
            'teams that use it.\n- Rating')
        rated, leads, steps = interview.ratings(text)
        self.assertEqual(leads, {'Responsibilities/2': ['Ran the widget guild.']})
        self.assertEqual(steps, {'Responsibilities/2': 'name the teams that use it.'})
        desired = interview.wanted(interview.criteria(LEVEL), rated, leads, steps)
        stretch = [text for text, (priority, _) in desired.items() if priority == 'stretch']
        self.assertTrue(any('name the teams that use it.' in text for text in stretch))
        self.assertFalse(any(interview.STRETCH in text for text in stretch))

    def test_wrapped_text_and_collect_steps(self):
        text = draft(r2='meets').replace(
            '- Rating', '- *(lead)* Ran the widget guild\n  for two years.\n'
            'Next step (collect): earlier periods.\n- Rating')
        rated, leads, steps = interview.ratings(text)
        self.assertEqual(leads, {'Responsibilities/2': ['Ran the widget guild for two years.']})
        self.assertEqual(steps, {})

    def test_linked_leads_ask_the_next_step_not_confirmation(self):
        text = draft(r2='not yet shown').replace(
            '- Rating', '- *(lead)* Ran it: https://example.test/1. Next step: name '
            'the teams that use it.\n- *(lead)* Linked, no step: https://example.test/2\n'
            '- Rating')
        rated, leads, steps = interview.ratings(text)
        desired = interview.wanted(interview.criteria(LEVEL), rated, leads, steps)
        about = [text for text in desired if text.startswith('About')]
        self.assertEqual(len(about), 2)
        self.assertTrue(all('name the teams that use it.' in text for text in about))

    def test_duplicate_criteria_and_conflicting_ratings_are_errors(self):
        with self.assertRaises(ValueError):
            interview.criteria(LEVEL + '1. Again: Duplicate number.\n')
        with self.assertRaises(ValueError):
            interview.ratings(draft(r1='exceeds') + '- Rating: not yet shown.\n')

    def test_linked_lead_without_a_step_still_needs_confirmation(self):
        text = draft(r2='meets').replace(
            '- Rating', '- *(lead)* Ran it: https://example.test/1\n- Rating')
        rated, leads, steps = interview.ratings(text)
        desired = interview.wanted(interview.criteria(LEVEL), rated, leads, steps)
        self.assertTrue(any('Who or what confirms' in q and 'example.test' in q
                            for q in desired))

    def test_answered_questions_are_not_asked_again(self):
        level = interview.criteria(LEVEL)
        state = {}
        interview.update(state, interview.wanted(level, {}))
        question = state['questions'][0]
        question['answer'] = 'synthetic answer'
        interview.update(state, interview.wanted(level, {}))
        self.assertNotIn(question['id'], interview.render(state, level))

    def test_runs_add_retire_reopen_and_keep_answers(self):
        level = interview.criteria(LEVEL)
        state = {}
        added, retired = interview.update(state, interview.wanted(level, {}))
        self.assertEqual((len(added), len(retired)), (len(state['questions']), 0))
        answered = next(q for q in state['questions'] if 'Skills/1' in q['items'])
        answered['answer'] = 'synthetic answer'
        closed = {key: 'exceeds' for key in level}
        _, retired = interview.update(state, interview.wanted(level, closed))
        self.assertTrue(all(q['status'] == 'retired' for q in state['questions']))
        self.assertEqual(answered['answer'], 'synthetic answer')
        added, _ = interview.update(state, interview.wanted(level, {}))
        self.assertEqual(added, [])
        self.assertTrue(all(q['status'] == 'open' for q in state['questions']))
        self.assertEqual(state['runs'], 3)

    def test_reworded_question_links_to_the_one_it_replaces(self):
        level = interview.criteria(LEVEL)
        state = {}
        interview.update(state, interview.wanted(level, {}))
        old = {q['id'] for q in state['questions'] if 'Responsibilities/2' in q['items']}
        before = next(q['text'] for q in state['questions']
                      if 'Responsibilities/2' in q['items'])
        reworded = {before.replace('Describe', 'Tell us about'): ('gap', ['Responsibilities/2']),
                    'An unrelated new question.': ('gap', ['Responsibilities/2'])}
        others = {text: value for text, value in interview.wanted(level, {}).items()
                  if 'Responsibilities/2' not in value[1]}
        added, _ = interview.update(state, {**others, **reworded})
        links = {q['text']: set(q.get('replaces', [])) for q in added}
        self.assertEqual(links['An unrelated new question.'], set())
        self.assertEqual(links[before.replace('Describe', 'Tell us about')], old)

    def test_main_writes_state_and_markdown(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp = Path(tmp)
            (tmp / 'level.md').write_text(LEVEL)
            (tmp / 'draft.md').write_text(draft(r2='meets'))
            args = ['--level', str(tmp / 'level.md'), '--draft', str(tmp / 'draft.md'),
                    '--state', str(tmp / 'q.json'), '--out', str(tmp / 'q.md')]
            interview.main(args)
            interview.main(args)
            state = json.loads((tmp / 'q.json').read_text())
            self.assertEqual(state['runs'], 2)
            text = (tmp / 'q.md').read_text()
            self.assertIn('## Gaps', text)
            self.assertIn('## Stretch', text)
            self.assertIn('Responsibilities/1 Mentoring', text)
            self.assertEqual(text.count(interview.SOURCE), 1)


if __name__ == '__main__':
    unittest.main()
