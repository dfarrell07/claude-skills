"""Close native Jira, GitHub and publicly readable GitLab reference frontiers."""

import re
from urllib.parse import urlsplit

from evidence import HOST
from extended_evidence import jira_key_aliases
from work_links import (URL, confirmed_keys)


def gitlab_texts(gitlab):
    for item in gitlab.get('items', []):
        for field in ('title', 'description'):
            yield item, field, item['url'], item['source'].get(field) or '', None


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


def verified_aliases(metadata, jira=None):
    native = {'authenticated_github_profile', 'authenticated_github_noreply', 'github_commit_account'}
    result = {row['email'] for row in metadata.get('author_aliases', [])
              if isinstance(row, dict) and row.get('source') in native and isinstance(row.get('email'), str)}
    identity = (jira or {}).get('metadata', {})
    if identity.get('identity_verified') is True and identity.get('account_id') and isinstance(identity.get('user'), str):
        result.add(identity['user'])
    return sorted(result)


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


