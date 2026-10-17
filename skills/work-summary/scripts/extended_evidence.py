"""Validation and citations for additional source inventories."""

import re
from urllib.parse import urlsplit

from evidence import HOST, REPO, integer, is_pr_url, require, timestamp


def same_github_item_url(left, right):
    """GitHub owner/repo casing is equivalent; native route/fragment stay exact."""
    if (not isinstance(left, str) or not isinstance(right, str)
            or any(ord(char) < 32 or ord(char) == 127 for char in left + right)):
        return False
    try:
        first, second = urlsplit(left), urlsplit(right)
    except ValueError:
        return False
    a, b = first.path.split('/'), second.path.split('/')
    return (first.scheme == second.scheme == 'https'
            and HOST.fullmatch(first.netloc) is not None
            and first.netloc.casefold() == second.netloc.casefold()
            and len(a) == len(b) == 5 and a[0] == b[0] == ''
            and REPO.fullmatch('/'.join(a[1:3])) is not None
            and [part.casefold() for part in a[1:3]] == [part.casefold() for part in b[1:3]]
            and a[3:] == b[3:] and a[3] in {'pull', 'issues'}
            and first.query == second.query and first.fragment == second.fragment)


def same_github_api_repo_url(left, right):
    """Bind an API repository route while retaining exact event/path identity."""
    if (not isinstance(left, str) or not isinstance(right, str)
            or any(ord(char) < 32 or ord(char) == 127 for char in left + right)):
        return False
    try:
        first, second = urlsplit(left), urlsplit(right)
    except ValueError:
        return False
    a, b = first.path.split('/'), second.path.split('/')
    if b[:2] == ['', 'repos']:
        position = 2
    elif b[:4] == ['', 'api', 'v3', 'repos']:
        position = 4
    else:
        return False
    return (first.scheme == second.scheme == 'https'
            and HOST.fullmatch(first.netloc) is not None
            and first.netloc.casefold() == second.netloc.casefold()
            and len(a) == len(b) and len(a) > position + 2
            and a[:position] == b[:position] and a[position + 2:] == b[position + 2:]
            and REPO.fullmatch('/'.join(a[position:position + 2])) is not None
            and [part.casefold() for part in a[position:position + 2]]
            == [part.casefold() for part in b[position:position + 2]]
            and first.query == second.query and first.fragment == second.fragment)


def validate_review_context(source, item, host='github.com'):
    """A review's submitted time is native context, not a text-revision timestamp."""
    require(isinstance(source, dict) and is_pr_url(item['url']) and integer(source.get('id'), 1),
            'Invalid native review identity')
    require(same_github_item_url(source.get('html_url'), item['url'] + '#pullrequestreview-' + str(source['id'])),
            'Native review target mismatch')
    require('body' in source and (source['body'] is None or isinstance(source['body'], str)),
            'Invalid native review body')
    require('user' in source and (source['user'] is None or isinstance(source['user'], dict)
            and isinstance(source['user'].get('login'), str) and bool(source['user']['login'])),
            'Invalid native review author')
    author = source['user']
    if author is not None:
        require('id' not in author or integer(author['id'], 1), 'Invalid native review author ID')
        require(author.get('node_id') is None or isinstance(author['node_id'], str) and bool(author['node_id']),
                'Invalid native review author node ID')
    require(source.get('node_id') is None or isinstance(source['node_id'], str) and bool(source['node_id']),
            'Invalid native review node ID')
    require(isinstance(source.get('state'), str)
            and source['state'].upper() in {'APPROVED', 'CHANGES_REQUESTED', 'COMMENTED', 'DISMISSED', 'PENDING'},
            'Invalid native review state')
    require('submitted_at' in source, 'Missing native review submission observation')
    if source['submitted_at'] is not None:
        timestamp(source['submitted_at'])
    if source.get('updated_at') is not None:
        timestamp(source['updated_at'])
    if 'pull_request_url' in source:
        api = 'https://api.github.com' if host.casefold() == 'github.com' else f'https://{host}/api/v3'
        require(same_github_api_repo_url(source['pull_request_url'], f"{api}/repos/{item['repo']}/pulls/{item['number']}"),
                'Native review API route mismatch')


def github_reference_comments(github):
    """Yield saved discussion bodies and identity-bound current native bodies."""
    yield from github.get('reference_comments', [])
    for record in github.get('reference_reviews', []):
        source = record['source']
        yield {'item_url': record['item_url'], 'repo': record['repo'], 'number': record['number'],
               'kind': 'review', 'id': source['id'], 'node_id': source.get('node_id'),
               'url': source['html_url'], 'body': source['body'] or '',
               'author_login': (source['user'] or {}).get('login'),
               'author_node_id': (source['user'] or {}).get('node_id'), 'submitted_at': source['submitted_at']}



def jira_reference(item):
    return f"[{item['key']}]({item['url']})"


def jira_key_aliases(jira):
    """Resolve moved keys only through two authenticated native issue identities."""
    from jira_sources import KEY, _normalize
    records = jira.get('key_aliases', [])
    require(isinstance(records, list), 'Invalid Jira key aliases')
    known = {item['key']: item for item in jira.get('issues', [])}
    aliases, ids = {}, {}
    for record in records:
        require(isinstance(record, dict), 'Invalid Jira key alias')
        requested, canonical = record.get('requested_key'), record.get('canonical_key')
        require(isinstance(requested, str) and KEY.fullmatch(requested)
                and isinstance(canonical, str) and KEY.fullmatch(canonical)
                and requested != canonical and canonical in known, 'Invalid Jira alias keys')
        native_ids = []
        for field in ('requested', 'canonical'):
            raw = record.get(field)
            normalized = _normalize(raw, jira['metadata']['site'])
            require(normalized['key'] == canonical and not isinstance(raw.get('id'), bool)
                    and re.fullmatch(r'[1-9][0-9]*', str(raw.get('id'))), 'Invalid native Jira alias identity')
            native_ids.append(str(raw['id']))
        require(native_ids[0] == native_ids[1], 'Jira alias native ID mismatch')
        target = known[canonical]
        require(any(isinstance(target.get(field), dict) and str(target[field].get('id')) == native_ids[0]
                    for field in ('source', 'detail')), 'Jira alias target ID mismatch')
        require(requested not in aliases or aliases[requested] == canonical,
                'Conflicting Jira key aliases')
        aliases[requested], ids[requested] = canonical, native_ids[0]
    resolved = {}
    for key in aliases:
        target, seen = key, set()
        while target in aliases:
            require(target not in seen and ids[target] == ids[key], 'Cyclic or conflicting Jira key alias chain')
            seen.add(target)
            target = aliases[target]
        resolved[key] = target
    return resolved


