#!/usr/bin/env python3
"""Collect read-only work evidence and render validated, source-linked reports."""

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit


# Collection must not write bytecode into the installed skill or a scanned checkout.
sys.dont_write_bytecode = True

from evidence import (  # noqa: E402
    HOST, command, decode_json, integer, iso, network_calls, progress, read_json, require,
    reset_network_state, timestamp, is_pr_url,
)
from local_sources import collect_local  # noqa: E402
from capabilities import collect_local_safely, empty_local  # noqa: E402
from github_sources import Github  # noqa: E402
from jira_sources import collect_jira, add_referenced_jira  # noqa: E402
from work_links import associate_work, github_items, native_review_links  # noqa: E402
from extended_evidence import github_reference_comments  # noqa: E402
from gitlab_sources import SCOPE as GITLAB_REF_SCOPE, collect_gitlab_refs  # noqa: E402
from gitlab_api import collect_gitlab, public_get  # noqa: E402
from jira_public_history import collect_public_history, public_field_events  # noqa: E402
from provider_references import code_urls, verified_aliases  # noqa: E402
from references import follow_references  # noqa: E402
from reporting_window import resolve_window  # noqa: E402


def collect(days_text, roots, now=None, *, since=None, until=None):
    start, end, window = resolve_window(days_text, now, since=since, until=until)
    reset_network_state()
    progress(f"collecting {iso(start)} to {iso(end)}")
    warnings, printed, failed = [], set(), set()

    def warn(message):
        # The capture keeps every warning; stderr shows each distinct one once, bounded,
        # so per-item failures cannot flood the agent's context.
        warnings.append(message)
        if message not in printed and len(printed) < 40:
            printed.add(message)
            print(f"Warning: {message}", file=sys.stderr)

    def phase(name, run, fallback):
        """Run one source; an unexpected failure is a stated gap, never a lost collection."""
        try:
            return run()
        except Exception as error:  # noqa: BLE001 - any failure here must keep the other sources
            warn(f"{name} stopped early ({type(error).__name__}: {error}); evidence collected so far is kept.")
            failed.add(name)
            return fallback()

    host = os.environ.get("GH_HOST", "github.com").lower()
    github_host_valid = HOST.fullmatch(host) is not None
    if not github_host_valid:
        warn("Configured GitHub host is invalid; GitHub reads are unavailable.")
        host = "github.com"
    gh_env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}

    def api(*args):
        return decode_json(command(["gh", "api", "--hostname", host, *args], env=gh_env))

    github = {"prs": [], "issues": [], "context_prs": [], "context_issues": [], "activity": []}
    github_user, identity, ledger = "", {}, None
    if github_host_valid and shutil.which("gh"):
        try:
            identity = api("user")
            github_user = identity["login"]
            require(isinstance(github_user, str) and re.fullmatch(r"[A-Za-z0-9_-]+", github_user),
                    "Invalid GitHub login")
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
            warn("GitHub identity lookup failed; GitHub activity is unavailable.")
            github_user, identity = "", {}
    elif github_host_valid:
        warn("GitHub CLI is missing; GitHub activity is unavailable.")
    if github_user:
        progress("GitHub: searching your PRs, reviews, comments and open work")
        ledger = Github(api, host, github_user, start, end, warn)
        phase("GitHub discovery", lambda: ledger.discover(github), lambda: None)

    progress("scanning local repositories")
    local, local_status, local_capability = phase(
        "Local Git", lambda: collect_local_safely(collect_local, roots, start, end, warn,
                                                  api=api if github_user else None, identity=identity),
        lambda: (empty_local(), "unavailable", {"status": "unavailable", "reason": "collection failed"}))
    # Git timestamps have whole-second precision and its lower-bound parser truncates
    # fractions; keep exactly the commits inside the window.
    local['commits'] = {repo: selected for repo, rows in local['commits'].items()
                        if (selected := [row for row in rows if start <= timestamp(row['committed_at']) <= end])}
    local['metadata']['repos_with_commits'] = len(local['commits'])
    progress(f"local: {local['metadata']['repos_searched']} repositories, "
             f"{local['metadata']['repos_with_commits']} with commits in the window")
    gitlab_discovery = phase(
        "GitLab MR refs", lambda: collect_gitlab_refs(local['branches'], local['metadata']['repositories'], warn),
        lambda: {'status': 'unavailable', 'scope': GITLAB_REF_SCOPE, 'queries': [], 'routes': [], 'failures': []})
    for entry in local['metadata'].get('repositories', []):
        # Full ref listings and tips (most of a large capture) only serve local collection.
        entry.pop('refs', None)
        entry.pop('tips', None)
    progress("Jira: reading inventories and active tickets")
    jira = phase("Jira", lambda: collect_jira(start, end, warn),
                 lambda: {'metadata': {'status': 'unavailable', 'warnings': [], 'coverage': {}},
                          'issues': [], 'activity': [], 'linked_prs': []})

    progress(f"Jira: {len(jira.get('issues', []))} tickets; following links between tickets, PRs and MRs")
    public_cache = {}

    def read_public(url):
        if url not in public_cache:
            public_cache[url] = public_get(url)
        return public_cache[url]
    history_warnings = set()

    def read_history(tickets):
        def once(message):
            if message not in history_warnings:
                history_warnings.add(message)
                warn(message)
        # Only active tickets: all-time role inventories are context, and private tickets
        # are never publicly readable.
        jira['metadata']['public_history'] = collect_public_history(
            {'metadata': jira['metadata'], 'issues': tickets}, once, reuse=True, github_host=host)

    def no_references():
        # Expansion only adds counterparts and history; drop its partial results.
        for issue in jira.get('issues', []):
            issue.pop('public_history', None)
            issue.pop('history_review_links', None)
        jira.get('metadata', {}).pop('public_history', None)
        return collect_gitlab([], warn), {'status': 'failed'}

    gitlab, expansion = phase("Reference expansion", lambda: follow_references(
        jira, github, local['branches'],
        lambda keys: add_referenced_jira(jira, keys, start, end, warn),
        lambda urls: collect_gitlab(urls, warn, verified_emails=verified_aliases(local['metadata'], jira),
                                    since=start, read=read_public,
                                    trusted_hosts=trusted_gitlab_hosts(local['metadata'], jira, github, github_user)),
        read_history, host), no_references)
    phase("Linking tickets and PRs", lambda: associate_work(jira, github), lambda: None)
    if {"Jira", "Reference expansion"} & failed and jira.get('metadata', {}).get('status') == 'ok':
        jira['metadata']['status'] = 'partial'
    discovery = ledger.coverage if ledger else {}
    github_status = ("unavailable" if not github_user else
                     "partial" if {"GitHub discovery", "Reference expansion"} & failed
                     or any(info.get("status") == "partial" for info in discovery.values()) else "ok")
    progress(f"done; {len(warnings)} coverage warnings ({len(set(warnings))} distinct) are in metadata.warnings")
    return {
        "metadata": {**window, **local["metadata"], "network_requests": network_calls(),
                     "local_status": local_status, "capabilities": {"local_git": local_capability},
                     "github_discovery": discovery, "reference_expansion": expansion,
                     "gitlab_discovery": gitlab_discovery,
                     "github_status": github_status, "github_user": github_user, "github_host": host,
                     "warnings": warnings},
        "github": github, "jira": jira, "gitlab": gitlab,
        "commits": local["commits"], "branches": local["branches"],
        "unattributed_commits": local["unattributed_commits"],
    }


def trusted_gitlab_hosts(local_metadata, jira, github=None, login=''):
    """GitLab hosts the user already works with: remotes, Jira review fields, own PR text."""
    hosts = {'gitlab.com'}
    for item in (github or {}).get('prs', []):
        if login and (item.get('author_login') or '').casefold() == login.casefold():
            hosts.update(urlsplit(url).hostname or '' for text in (item.get('title'), item.get('body'))
                         for provider, url in code_urls(text or '') if provider == 'gitlab')
    hosts.update(remote['host'].casefold() for entry in local_metadata.get('repositories', [])
                 for remote in entry.get('remotes', []) if isinstance(remote.get('host'), str))
    for issue in jira.get('issues', []):
        for link in native_review_links(issue):
            if link.get('provider') == 'gitlab':
                hosts.add(urlsplit(link['url']).hostname or '')
    return hosts


def validate(data, source=None):
    """Check an agent's analysis against the capture it cites.

    The capture itself is the collector's own output and is trusted; what needs
    checking is that every theme cites captured evidence and every displayed link
    is supported by those citations.
    """
    require(isinstance(data, dict) and isinstance(data.get("metadata"), dict), "Capture must be an object with metadata")
    if "analysis" not in data:
        require(source is None, "Source comparison is only for enriched captures")
        return
    if source is not None:
        require({key: value for key, value in data.items() if key != "analysis"} == source,
                "Enrichment changed capture sections")
    evidence = {item["url"]: item for item in reversed(github_items(data.get("github", {})))}
    jira_items = {item["key"]: item for item in data.get("jira", {}).get("issues", [])}
    commits = data.get("commits", {})
    analysis = data["analysis"]
    if isinstance(analysis, dict):
        analysis.setdefault("in_progress", [])
    require(isinstance(analysis, dict) and isinstance(analysis.get("themes"), list)
            and isinstance(analysis.get("in_progress"), list),
            'Invalid analysis: expected {"themes": [...], "in_progress": [...]}')

    def reference(item):
        require(isinstance(item, dict), "Invalid analysis citation")
        require(integer(item.get("number"), 1) and isinstance(item.get("url"), str), "Invalid analysis citation identity")
        original = evidence.get(item.get("url"))
        require(original is not None and all(item.get(key) == original[key] for key in ("repo", "number", "url")),
                "Analysis citation is absent from source evidence")
        return original

    def theme_node(theme):
        try:
            check_theme(theme)
        except (ValueError, KeyError, TypeError) as error:
            name = theme.get('name') if isinstance(theme, dict) else None
            raise ValueError(f"Theme {name!r}: {error}") from error

    def check_theme(theme):
        if isinstance(theme, dict):
            theme.setdefault("items", [])
        require(isinstance(theme, dict) and all(isinstance(theme.get(key), str) and theme[key].strip()
                for key in ("name", "description")) and isinstance(theme.get("items"), list), "Invalid theme")
        for item in theme["items"]:
            reference(item)
        jira_refs = theme.get("jira", [])
        require(isinstance(jira_refs, list), "Invalid Jira citations")
        for item in jira_refs:
            require(isinstance(item, dict) and item.get("key") in jira_items
                    and item.get("url") == jira_items[item["key"]]["url"], "Jira citation absent from capture")
        branch_refs = theme.get("branches", [])
        require(isinstance(branch_refs, list), "Invalid branch citations")
        for item in branch_refs:
            require(isinstance(item, dict) and any(item.get("repo") == branch["repo"]
                    and item.get("hash") == branch["hash"] for branch in data.get("branches", [])),
                    "Branch citation absent from capture")
        mr_refs = theme.get('merge_requests', [])
        require(isinstance(mr_refs, list), 'Invalid GitLab MR citations')
        native_mrs = {item['url']: item for item in data.get('gitlab', {}).get('items', [])}
        for reference_mr in mr_refs:
            require(isinstance(reference_mr, dict) and reference_mr.get('url') in native_mrs,
                    'GitLab MR citation absent from capture')
            native_mr = native_mrs[reference_mr['url']]
            require(all(reference_mr.get(key, native_mr[key]) == native_mr[key] for key in ('host', 'repo', 'number')),
                    'GitLab MR citation identity mismatch')
        local = theme.get("commits", [])
        require(isinstance(local, list), "Invalid local commit citations")
        for item in local:
            require(isinstance(item, dict) and isinstance(item.get("repo"), str)
                    and any(entry["hash"] == item.get("hash") for entry in commits.get(item["repo"], [])),
                    f"Commit {item.get('hash')} is not in this capture's commits for {item.get('repo')}; "
                    "cite a full 40-character hash listed in the digest")
        children = theme.get("subtopics", [])
        require(isinstance(children, list), "Invalid subtopics")
        for child in children:
            theme_node(child)
        require(theme["items"] or local or jira_refs or branch_refs or mr_refs or children,
                "A theme needs at least one source citation or evidenced subtopic")
        validate_selected_links(data, theme)

    for theme in analysis["themes"]:
        theme_node(theme)
    for item in analysis["in_progress"]:
        require(isinstance(item, dict) and isinstance(item.get("description"), str) and item["description"].strip(),
                "Invalid in-progress item")
        if "key" in item:
            require(item["key"] in jira_items and item.get("url") == jira_items[item["key"]]["url"]
                    and jira_items[item["key"]].get("status_category") in {"new", "indeterminate"}, "In-progress Jira must be active")
        else:
            require(reference(item)["state"] == "open", "In-progress citation must refer to an open item")
        validate_selected_links(data, ongoing_topic(item))


def markdown(text):
    # Keep untrusted text on one line; encode HTML and escape Markdown syntax.
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in str(text))
    text = " ".join(text.split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    text = re.sub(r"([\\`*_{}\[\]()#+!|~])", r"\\\1", text)
    # Only a leading ordered-list marker makes a period structural here.
    return re.sub(r"^([0-9]{1,9})\.(?=\s|$)", r"\1\\.", text)


def link(item):
    return f"[{markdown(item['repo'])}#{item['number']}]({item['url']})"


def native_https_urls(text):
    """Parse complete native URLs without trusting malformed authorities."""
    for match in re.finditer(r'https://[^\s<>"\[\]()]+', text):
        original = match.group().rstrip('.,;:!?`\'’”')
        try:
            parts = urlsplit(original)
            if parts.port is not None and not 1 <= parts.port <= 65535:
                continue
        except ValueError:
            continue
        if HOST.fullmatch(parts.netloc) and parts.username is None and parts.password is None:
            yield original, parts


def contextual_jira_links(item, comments=()):
    """Retain explicit native browse URLs without inventing a Jira identity."""
    if not is_pr_url(item['url']):
        return []
    result = {}
    sources = [(field, item['url'], item.get(field, '')) for field in ('title', 'body')]
    sources.extend(('comment', comment['url'], comment['body']) for comment in comments
                   if comment['item_url'] == item['url'])
    for field, evidence_url, text in sources:
        for original, parts in native_https_urls(text):
            if re.fullmatch(r'/browse/[A-Z][A-Z0-9_]*-[1-9][0-9]*', parts.path, re.I):
                url = 'https://' + parts.netloc.lower() + '/browse/' + parts.path.rsplit('/', 1)[1].upper()
                result[url] = {'url': url, 'source_url': original, 'proof': 'github_' + field + '_url',
                               'evidence_url': evidence_url}
    return [result[url] for url in sorted(result)]


def local_context_links(data, topic):
    """Retain explicit URLs only from selected, native author-verified subjects."""
    refs = {(item['repo'], item['hash']) for item in topic.get('commits', [])}
    identities = {item['path']: item['identities'] for item in data['metadata'].get('repositories', [])}
    host = data['metadata'].get('github_host', 'github.com').casefold()
    result = {}
    for repo, entries in data['commits'].items():
        for entry in entries:
            if ((repo, entry['hash']) not in refs
                    or entry.get('author_email') not in identities.get(repo, [])):
                continue
            for original, parts in native_https_urls(entry['message']):
                if re.fullmatch(r'/browse/[A-Z][A-Z0-9_]*-[1-9][0-9]*', parts.path, re.I):
                    provider = 'jira'
                    path = '/browse/' + parts.path.rsplit('/', 1)[1].upper()
                elif (parts.netloc.casefold() == host
                      and re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*', parts.path)
                      and not any(part in {'.', '..'} for part in parts.path.split('/'))):
                    provider, path = 'github', parts.path
                else:
                    continue
                url = 'https://' + parts.netloc.lower() + path
                result[url] = {'url': url, 'source_url': original, 'provider': provider,
                               'proof': 'local_commit_subject_url', 'repo': repo, 'hash': entry['hash']}
    return [result[url] for url in sorted(result)]


def topic_github_items(data, topic):
    by_url = {item['url']: item for item in reversed(github_items(data['github']))}
    return list({item['url']: by_url[item['url']] for item in topic.get('items', [])}.values())


def topic_merge_requests(data, topic):
    """Native advertised heads identify counterparts, without supplying MR state."""
    refs = {(item['repo'], item['hash']) for item in topic.get('commits', []) + topic.get('branches', [])}
    by_url = {item['url']: item for item in data.get('gitlab', {}).get('items', [])}
    selected = {reference['url']: by_url[reference['url']] for reference in topic.get('merge_requests', [])}
    for branch in data.get('branches', []):
        if (branch['repo'], branch['hash']) in refs:
            for mr in branch.get('merge_requests', []):
                selected[mr['url']] = by_url.get(mr['url'], mr)
    return list(selected.values())


def work_urls(data, topic):
    """Return only captured citations and their validated counterpart links."""
    jira = {item['key']: item for item in data.get('jira', {}).get('issues', [])}
    ticket_urls, change_urls, context_urls = set(), set(), set()

    def ticket(item, include_prs):
        ticket_urls.add(item['url'])
        if include_prs:
            change_urls.update(link['url'] for link in item.get('pr_links', []))
        context_urls.update(link['url'] for link in item.get('review_links', []))

    def github(item):
        change_urls.add(item['url'])
        ticket_urls.update(relation['url'] for relation in contextual_jira_links(
            item, github_reference_comments(data['github'])))
        for relation in item.get('jira_links', []):
            ticket(jira[relation['key']], False)

    for item in topic_github_items(data, topic):
        github(item)
    for reference in topic.get('jira', []):
        ticket(jira[reference['key']], True)
    for relation in local_context_links(data, topic):
        (ticket_urls if relation['provider'] == 'jira' else change_urls).add(relation['url'])
    for mr in topic_merge_requests(data, topic):
        change_urls.add(mr['url'])
        for relation in mr.get('jira_links', []):
            ticket(jira[relation['key']], False)
    return sorted(ticket_urls) + sorted(change_urls - ticket_urls) + sorted(context_urls - ticket_urls - change_urls)


def validate_selected_links(data, topic):
    if 'links' not in topic:
        return
    selected = topic['links']
    require(isinstance(selected, list) and all(isinstance(url, str) for url in selected),
            "Invalid selected links")
    candidates = set(work_urls(data, topic))
    missing = [url for url in selected if url not in candidates]
    require(not missing, f"Selected link {missing[0] if missing else ''} is not among this topic's cited "
            f"evidence; cite its item, ticket, MR or commit in the same topic (candidates: {sorted(candidates)})")


def ongoing_topic(item, prefix='Ongoing: '):
    topic = {'description': prefix + item['description'], 'items': []}
    if 'key' in item:
        topic['jira'] = [{key: item[key] for key in ('key', 'url')}]
    else:
        topic['items'] = [{key: item[key] for key in ('repo', 'number', 'url')}]
    if 'links' in item:
        topic['links'] = item['links']
    return topic


def direct_urls(topic):
    """Exact cited artifacts establish work identity without traversing relations."""
    return list(dict.fromkeys(item['url'] for field in ('items', 'jira', 'merge_requests')
                              for item in topic.get(field, [])))


def display_urls(data, topic):
    """Use deliberate selections; legacy topics show only directly cited artifacts.

    work_urls remains the complete source-backed candidate API. Its relations
    prove that a URL may be selected, not that it belongs in the work narrative.
    """
    return list(dict.fromkeys(topic['links'] if 'links' in topic else direct_urls(topic)))


def topic_nodes(topics):
    """Walk authored order without inheriting evidence or links between nodes."""
    for topic in topics:
        yield topic
        yield from topic_nodes(topic.get('subtopics', []))


def authored_line(text):
    """The agent's own description: one line, pasteable, without Markdown escapes.

    Only a leading list, heading or quote marker is neutralized so the bullet
    structure cannot change; text inside the line stays as written.
    """
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in str(text))
    text = " ".join(text.split())
    # Constructs that hide or fetch content when the list is rendered: raw HTML,
    # links and images, strikethrough. Prose echoing untrusted titles must not gain them.
    text = re.sub(r"<(?=[A-Za-z/!?])", r"\\<", text)
    text = re.sub(r"(!?)\[([^\]]*)\]\(", r"\1\\[\2\\](", text)
    text = text.replace("~", "\\~")
    text = re.sub(r"^([0-9]{1,9})([.)])(?=\s|$)", r"\1\\\2", text)
    return re.sub(r"^([-+*#>])(?=\s|$)", r"\\\1", text)


def render_topic_lines(data, topic, depth=0, authored=False):
    label = authored_line(topic['description']) if authored else markdown(topic['description'])
    lines = ['  ' * depth + '- ' + label]
    for url in display_urls(data, topic):
        # Intraword underscores (merge_requests) are already literal Markdown.
        # Delimiter-position underscores and tildes may still need an autolink.
        lines.append('  ' * (depth + 1) + '- ' +
                     (f'<{url}>' if re.search(r'(?<![A-Za-z0-9])_|_(?![A-Za-z0-9])|~', url) else url))
    for child in topic.get('subtopics', []):
        lines.extend(render_topic_lines(data, child, depth + 1, authored))
    return lines


def fallback_topics(data, limit=12):
    """Select a small deterministic list from event proof, never backlog roles."""
    metadata = data['metadata']
    start = timestamp(metadata['since_at']) if metadata.get('since_at') else None
    end = timestamp(metadata['to_at']) if metadata.get('to_at') else None
    items = {item['url']: item for item in reversed(github_items(data['github']))}
    jira = {item['key']: item for item in data.get('jira', {}).get('issues', [])}
    candidates = {}

    def add(key, priority, when, topic):
        rank = priority, -when.timestamp(), key
        if key not in candidates or rank < candidates[key][0]:
            candidates[key] = rank, topic

    def github_topic(item, description):
        return {'description': description, 'items': [{key: item[key] for key in ('repo', 'number', 'url')}]}

    for item in data['github']['prs']:
        if item.get('merged_at') and start is not None and start <= timestamp(item['merged_at']) <= end:
            add(item['url'], 0, timestamp(item['merged_at']), github_topic(item, 'Merged: ' + item['title']))
    actions = {'opened': (1, 'Opened'), 'review': (2, 'Reviewed'),
               'review_comment': (3, 'Discussed'), 'issue_comment': (3, 'Discussed'),
               'review_comment_edited': (3, 'Updated discussion on'),
               'issue_comment_edited': (3, 'Updated discussion on'),
               'closed': (4, 'Closed'),
               'reopened': (4, 'Reopened')}
    for event in data['github'].get('activity', []):
        # Actor/time proof establishes participation, not substantive work.
        # Passive notifications and unknown timeline actions stay in the audit.
        if event['action'] not in actions or event['item_url'] not in items:
            continue
        item = items[event['item_url']]
        priority, verb = actions[event['action']]
        add(item['url'], priority, timestamp(event['at']), github_topic(item, verb + ': ' + item['title']))
    for event in data.get('jira', {}).get('activity', []):
        item = jira[event['key']]
        title = 'restricted Jira ticket' if item['sensitive'] else item['title']
        verb = 'Commented on' if event['kind'] == 'comment_created' else 'Edited a comment on'
        add(item['url'], 3, timestamp(event['occurred_at']),
            {'description': verb + ': ' + title, 'items': [],
             'jira': [{key: item[key] for key in ('key', 'url')}]})
    for event in public_field_events(data.get('jira', {}), start, end):
        item = jira[event['key']]
        title = 'restricted Jira ticket' if item['sensitive'] else item['title']
        add(item['url'], 4, timestamp(event['occurred_at']),
            {'description': 'Updated tracker: ' + title, 'items': [],
             'jira': [{key: item[key] for key in ('key', 'url')}], 'links': [item['url']]})
    gitlab = data.get('gitlab', {})
    principals = {(row['host'], row['user_id']) for row in gitlab.get('metadata', {}).get('identities', [])}
    graph_principals = {(host, f'gid://gitlab/User/{identity}') for host, identity in principals}
    for item in gitlab.get('items', []):
        native = item['source']
        def mr_topic(description):
            return {'description': description, 'items': [], 'merge_requests': [{'url': item['url']}]}
        if (item['host'], (native.get('author') or {}).get('id')) in principals:
            for field, priority, verb in [('merged_at', 0, 'Merged'), ('created_at', 1, 'Opened')]:
                if native.get(field) and start is not None and start <= timestamp(native[field]) <= end:
                    add(item['url'], priority, timestamp(native[field]), mr_topic(verb + ': ' + native['title']))
        for note in item.get('notes', []):
            note_principals = graph_principals if note.get('source_kind') == 'graphql' else principals
            if (note.get('system') is False
                    and (item['host'], (note.get('author') or {}).get('id')) in note_principals
                    and start is not None and start <= timestamp(note['created_at']) <= end):
                add(item['url'], 3, timestamp(note['created_at']), mr_topic('Discussed: ' + native['title']))
            if (note.get('source_kind') == 'graphql' and note.get('system') is False
                    and (item['host'], (note.get('last_edited_by') or {}).get('id')) in graph_principals
                    and note.get('last_edited_at') and start is not None
                    and start <= timestamp(note['last_edited_at']) <= end):
                add(item['url'], 3, timestamp(note['last_edited_at']),
                    mr_topic('Edited a discussion: ' + native['title']))
    identities = {item['path']: item['identities'] for item in metadata.get('repositories', [])}
    seen_hashes = set()
    for repo, entries in sorted(data['commits'].items()):
        verified = [entry for entry in entries if entry.get('author_email') in identities.get(repo, [])
                    and entry.get('committed_at') and start is not None
                    and start <= timestamp(entry['committed_at']) <= end]
        verified.sort(key=lambda entry: (timestamp(entry['committed_at']), entry['hash']), reverse=True)
        entry = next((entry for entry in verified if entry['hash'] not in seen_hashes), None)
        seen_hashes.update(entry['hash'] for entry in verified)
        if entry:
            topic = {'description': 'Local change in ' + Path(repo).name + ': ' + entry['message'], 'items': [],
                     'commits': [{'repo': repo, 'hash': entry['hash']}]}
            native_mrs = topic_merge_requests(data, topic)
            if native_mrs:
                topic['links'] = [mr['url'] for mr in native_mrs]
            urls = work_urls(data, topic)
            if not any(url in candidates for url in urls):
                add(repo, 5, timestamp(entry['committed_at']), topic)
    return [topic for _, topic in sorted(candidates.values(), key=lambda value: value[0])[:limit]]


def render(data, source=None):
    validate(data, source)
    topics = list(data['analysis']['themes']) if 'analysis' in data else fallback_topics(data)
    represented = {url for topic in topic_nodes(topics)
                   for url in direct_urls(topic) + display_urls(data, topic)}
    for ongoing in data.get('analysis', {}).get('in_progress', []):
        if ongoing['url'] in represented:
            continue
        topic = ongoing_topic(ongoing)
        topics.append(topic)
        represented.update(direct_urls(topic) + display_urls(data, topic))
    lines = []
    for topic in topics:
        lines.extend(render_topic_lines(data, topic, authored='analysis' in data))
    if not topics:
        lines.append('No supported work found in the available evidence.')
    known_jira_urls = {item['url'] for item in data.get('jira', {}).get('issues', [])}
    contextual = set()
    local_github_context = set()
    for topic in topic_nodes(topics):
        displayed = set(display_urls(data, topic))
        for item in topic_github_items(data, topic):
            contextual.update(link['url'] for link in contextual_jira_links(
                item, github_reference_comments(data['github']))
                              if link['url'] in displayed)
        for relation in local_context_links(data, topic):
            if relation['url'] in displayed:
                (contextual if relation['provider'] == 'jira' else local_github_context).add(relation['url'])
    notes = []
    if contextual - known_jira_urls:
        notes.append('Jira state is unverified for contextual links.')
    known_github_urls = {item['url'].casefold() for item in github_items(data['github'])}
    if {url.casefold() for url in local_github_context} - known_github_urls:
        notes.append('GitHub state is unverified for contextual links.')
    states = [('GitHub', data['metadata'].get('github_status')),
              ('Jira', data.get('jira', {}).get('metadata', {}).get('status')),
              ('local Git', data['metadata'].get('local_status')),
              ('GitLab refs', data['metadata'].get('gitlab_discovery', {}).get('status')),
              ('GitLab API', data.get('gitlab', {}).get('metadata', {}).get('status'))]
    unavailable = [name + ' unavailable' for name, status in states if status == 'unavailable']
    # With an analysis the agent writes the one source sentence the reader needs.
    if unavailable and 'analysis' not in data:
        notes.append('Coverage is incomplete: ' + '; '.join(unavailable) + '.')
    if any(status == 'partial' for _, status in states) and 'analysis' not in data:
        notes.append('Some sources were only partly read; the run directory has the details.')
    if notes:
        lines += ['', ' '.join(notes)]
    return '\n'.join(lines)


def write_atomic(filename, text):
    partial = filename + ".partial"
    with open(partial, "w", encoding="utf-8") as handle:
        handle.write(text)
    os.replace(partial, filename)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    collector = modes.add_parser("collect", help="Read local Git and authored GitHub work")
    collector.add_argument("days", nargs="?")
    collector.add_argument("roots", nargs="*")
    collector.add_argument("--since", help="Inclusive RFC3339 reporting start; requires --until")
    collector.add_argument("--until", help="Exclusive RFC3339 reporting end; requires --since")
    collector.add_argument("--root", action="append", default=[], dest="explicit_roots",
                           help="Repository scan root (repeatable; use with explicit dates)")
    collector.add_argument("--output", help="Write the capture atomically to this file instead of stdout")
    renderer = modes.add_parser("render", help="Validate evidence and render Markdown")
    renderer.add_argument("--analysis", help="Analysis JSON to apply to the unchanged capture")
    renderer.add_argument("--candidates", action="store_true",
                          help="List cite-ready personal window evidence for writing an analysis")
    renderer.add_argument("capture")
    renderer.add_argument("source", nargs="?", help="Original capture; required for enriched input")
    args = parser.parse_args()
    try:
        if args.mode == "collect":
            explicit = args.since is not None or args.until is not None
            require(not explicit or not args.roots, "Use repeatable --root with --since/--until")
            require(explicit or not args.explicit_roots, "--root requires --since and --until; rolling roots are positional")
            data = collect(args.days, args.explicit_roots if explicit else args.roots,
                           since=args.since, until=args.until)
            output = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
            if args.output:
                write_atomic(args.output, output)
                return 0
        elif args.candidates:
            from candidates import candidates
            data = read_json(args.capture)
            require(args.source is None and not args.analysis and "analysis" not in data,
                    "--candidates takes only the original capture")
            validate(data)
            output = candidates(data)
        elif args.analysis:
            require(args.source is None, "--analysis takes only the original capture")
            analysis = read_json(args.analysis)
            if isinstance(analysis, dict) and set(analysis) == {"analysis"}:
                analysis = analysis["analysis"]
            source = read_json(args.capture)
            data = {**source, "analysis": analysis}
            output = render(data, source) + "\n"
        else:
            output = render(read_json(args.capture), read_json(args.source) if args.source else None) + "\n"
    except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
