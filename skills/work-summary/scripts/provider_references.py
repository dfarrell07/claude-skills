"""Close native Jira, GitHub and publicly readable GitLab reference frontiers."""

import re
from urllib.parse import urlsplit

from evidence import HOST, require
from extended_evidence import github_reference_comments, jira_key_aliases
from work_links import (URL, classify_reference_failures, complete_references, confirmed_keys, discovery_keys,
                        github_selected_observations, native_review_links)


def gitlab_texts(gitlab):
    for item in gitlab.get('items', []):
        for field in ('title', 'description'):
            yield item, field, item['url'], item['source'].get(field) or '', None
        for note in item.get('notes', []):
            url = note['url'] if note.get('source_kind') == 'graphql' else item['url']
            yield item, 'note', url, note.get('body') or '', note['id']


def code_urls(text, github_host=None):
    """Only native HTTPS review URLs become read targets; fragments stay context."""
    for match in URL.finditer(text or ''):
        try:
            parts = urlsplit(match.group().rstrip('.,;:!?`\'’”'))
            if (parts.scheme != 'https' or not HOST.fullmatch(parts.netloc)
                    or parts.username or parts.password or any(part in {'.', '..'} for part in parts.path.split('/'))):
                continue
            if parts.port is not None and not 1 <= parts.port <= 65535:
                continue
        except ValueError:
            continue
        if (github_host and parts.netloc.casefold() == github_host.casefold()
                and re.fullmatch(r'/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*', parts.path)):
            provider = 'github'
        elif re.fullmatch(r'/(?:[A-Za-z0-9_.-]+/){2,}-/merge_requests/[1-9][0-9]*', parts.path):
            provider = 'gitlab'
        else:
            continue
        yield provider, 'https://' + parts.netloc.lower() + parts.path


def gitlab_seed_urls(jira, github, branches):
    urls = {link['url'] for issue in jira.get('issues', []) for link in native_review_links(issue)}
    urls.update(link['url'] for issue in jira.get('issues', [])
                for link in issue.get('history_review_links', []) if link['provider'] == 'gitlab')
    urls.update(link['url'] for branch in branches for link in branch.get('merge_requests', []))
    observations = github_selected_observations(github)
    texts = [item.get(field) or '' for item in observations
             for field in ('title', 'body')]
    texts.extend(comment['body'] for comment in github_reference_comments(github))
    urls.update(url for text in texts for provider, url in code_urls(text) if provider == 'gitlab')
    return urls


def verified_aliases(metadata, jira=None):
    native = {'authenticated_github_profile', 'authenticated_github_noreply', 'github_commit_account'}
    result = {row['email'] for row in metadata.get('author_aliases', [])
              if isinstance(row, dict) and row.get('source') in native and isinstance(row.get('email'), str)}
    identity = (jira or {}).get('metadata', {})
    if identity.get('identity_verified') is True and identity.get('account_id') and isinstance(identity.get('user'), str):
        result.add(identity['user'])
    return sorted(result)


def complete_provider_references(jira, github, branches, read_tickets, read_prs,
                                 read_gitlab, github_host='github.com', read_history=None):
    """Follow each observed target once; a failed user endpoint cannot veto item reads."""
    attempted_keys, attempted_prs, unread_prs, attempted_gitlab = set(), set(), set(), set()
    gitlab = read_gitlab([])
    site = jira.get('metadata', {}).get('site')
    def tickets_once(keys):
        fresh = set(keys) - attempted_keys
        attempted_keys.update(fresh)
        if fresh:
            read_tickets(sorted(fresh))
    def prs_once(urls):
        fresh = set(urls) - attempted_prs
        attempted_prs.update(fresh)
        if fresh:
            read_prs(sorted(fresh))
    while True:
        if read_history is not None:
            read_history()
        expansion = complete_references(jira, github, tickets_once, prs_once if read_prs is not None else None)
        attempted_keys.update(expansion['attempted_jira_keys'])
        attempted_prs.update(expansion['attempted_pr_urls'])
        unread_prs.update(expansion['unread_pr_urls'])
        if read_history is not None and any('public_history' not in issue for issue in jira.get('issues', [])):
            continue
        targets = gitlab_seed_urls(jira, github, branches)
        keys, prs = set(), set()
        for _, _, _, text, _ in gitlab_texts(gitlab):
            keys.update(discovery_keys(text, site))
            for provider, url in code_urls(text, github_host):
                (targets if provider == 'gitlab' else prs).add(url)
        keys -= attempted_keys
        prs -= attempted_prs | unread_prs
        new_targets = targets - attempted_gitlab
        if not keys and not prs and not new_targets:
            break
        if keys:
            tickets_once(keys)
        if prs:
            if read_prs is None:
                unread_prs.update(prs)
            else:
                prs_once(prs)
        if new_targets:
            attempted_gitlab.update(targets)
            gitlab = read_gitlab(sorted(attempted_gitlab))
    associate_gitlab(jira, gitlab)
    classify_reference_failures(jira, github, gitlab)
    return gitlab, {'attempted_jira_keys': sorted(attempted_keys), 'attempted_pr_urls': sorted(attempted_prs),
                    'unread_pr_urls': sorted(unread_prs), 'attempted_gitlab_urls': sorted(attempted_gitlab)}


def associate_gitlab(jira, gitlab):
    issues = {issue['key']: issue for issue in jira.get('issues', [])}
    site = jira.get('metadata', {}).get('site')
    aliases = jira_key_aliases(jira)
    relations = {item['url']: {} for item in gitlab.get('items', [])}
    for item, field, url, text, note_id in gitlab_texts(gitlab):
        for source_key in confirmed_keys(text, site):
            key = aliases.get(source_key, source_key)
            if key in issues:
                relation = {'key': key, 'url': issues[key]['url'], 'proof': 'gitlab_' + field + '_key',
                            'evidence_url': url, 'sensitive': issues[key]['sensitive']}
                if note_id is not None:
                    relation['note_id'] = note_id
                if key != source_key:
                    relation.update(source_key=source_key, jira_source_url=f'https://{site}/browse/{source_key}')
                relations[item['url']].setdefault(key, relation)
    for item in gitlab.get('items', []):
        item['jira_links'] = [relations[item['url']][key] for key in sorted(relations[item['url']])]


def validate_gitlab_links(data):
    gitlab = data.get('gitlab', {'items': []})
    expected = {'items': [dict(item) for item in gitlab.get('items', [])]}
    associate_gitlab(data.get('jira', {'issues': []}), expected)
    for item, rebuilt in zip(gitlab.get('items', []), expected['items']):
        require(item.get('jira_links', []) == rebuilt['jira_links'],
                'GitLab Jira links lack matching native description or note evidence')
