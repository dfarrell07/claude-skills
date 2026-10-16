"""Validation and citations for additional source inventories."""

import re
from urllib.parse import quote, urlsplit

from evidence import HASH, HOST, integer, require, timestamp, valid_item


def validate_github(data, evidence, start, end):
    github, metadata = data['github'], data['metadata']
    host = metadata.get('github_host', 'github.com')
    for kind in ('prs', 'issues'):
        for item in github.get('context_' + kind, []):
            valid_item(item, kind, host)
            require(item['url'] not in evidence, 'Duplicate GitHub context item')
            require(isinstance(item.get('roles'), list) and all(isinstance(role, str) for role in item['roles']),
                    'Invalid GitHub context roles')
            timestamp(item['observed_at'])
            evidence[item['url']] = item
    indexed = {}
    for item in github.get('pr_index', []):
        valid_item(item, 'prs', host)
        require(item['url'] not in indexed, 'Duplicate indexed PR')
        for field in ('head_sha', 'merge_sha'):
            require(item.get(field) is None or isinstance(item[field], str) and HASH.fullmatch(item[field]),
                    'Invalid indexed PR snapshot')
        indexed[item['url']] = item
    for event in github.get('activity', []):
        require(isinstance(event, dict) and event.get('item_url') in evidence, 'Unknown GitHub activity item')
        item = evidence[event['item_url']]
        require(event.get('repo') == item['repo'] and event.get('number') == item['number'], 'Activity item mismatch')
        require(isinstance(event.get('actor'), str)
                and bool(event['actor']) and bool(metadata.get('github_user'))
                and event['actor'].casefold() == metadata.get('github_user', '').casefold(), 'Activity actor mismatch')
        require(start is not None and start <= timestamp(event['at']) <= end, 'Activity outside capture window')
        require(isinstance(event.get('action'), str) and event['action'], 'Invalid activity action')
        require(isinstance(event.get('body'), str), 'Invalid activity body')
        require(isinstance(event.get('url'), str) and (event['url'] == item['url']
                or event['url'].startswith(item['url'] + '#')), 'Invalid activity URL')
        require(event['url'] == item['url'] or re.fullmatch(r'[A-Za-z0-9_-]+', urlsplit(event['url']).fragment),
                'Invalid activity fragment')
    for key, info in metadata.get('github_discovery', {}).items():
        require(isinstance(key, str) and isinstance(info, dict) and info.get('status') in {'ok', 'partial'}
                and integer(info.get('pages')) and integer(info.get('count')), 'Invalid GitHub discovery coverage')
        if info['status'] != 'ok':
            require(metadata.get('github_status') == 'partial' and metadata.get('warnings'), 'Missing discovery gap')
    for branch in data.get('branches', []):
        prs = branch.get('prs', [])
        require(isinstance(prs, list), 'Invalid branch PR associations')
        for pr in prs:
            original = indexed.get(pr.get('url'))
            require(original is not None and all(pr.get(key) == original[key] for key in ('repo', 'number', 'url'))
                    and pr.get('state') in {'open', 'closed', 'merged'},
                    'Branch PR absent from index')
            require(pr.get('proof') in {'exact_head_or_merge_sha', 'any_author_sha_search', 'commit_association'},
                    'Invalid branch PR proof')
            if pr['proof'] == 'exact_head_or_merge_sha':
                require(branch['hash'] in (original.get('head_sha'), original.get('merge_sha')), 'Branch SHA mismatch')
        if branch['pr_status'] == 'matched':
            require(bool(prs), 'Matched branch needs PR evidence')
        if branch['pr_status'] == 'no_match':
            lookups = branch.get('association_lookups', [])
            require(bool(lookups) and all(info.get('status') == 'ok' for info in lookups) and not prs,
                    'No-match branch needs completed lookups')
    # Indexed snapshots remain citations, but use the primary observed state when available.
    for url, item in indexed.items():
        evidence.setdefault(url, item)


def jira_reference(item):
    return f"[{item['key']}]({item['url']})"


def validate_jira(data, start, end):
    jira = data.get('jira')
    if jira is None:
        return {}
    require(isinstance(jira, dict) and isinstance(jira.get('metadata'), dict), 'Invalid Jira capture')
    meta = jira['metadata']
    require(meta.get('status') in {'ok', 'partial', 'unavailable'}, 'Invalid Jira status')
    site = meta.get('site')
    require(isinstance(site, str) and (not site and meta['status'] == 'unavailable'
            or HOST.fullmatch(site) and ':' not in site), 'Invalid Jira hostname')
    if meta.get('identity_verified'):
        require(isinstance(meta.get('account_id'), str) and bool(meta['account_id']), 'Missing native Jira principal')
    result = {}
    for item in jira.get('issues', []):
        require(isinstance(item, dict) and isinstance(item.get('key'), str)
                and re.fullmatch(r'[A-Z][A-Z0-9_]*-[1-9][0-9]*', item['key'])
                and isinstance(item.get('title'), str) and isinstance(item.get('url'), str), 'Invalid Jira issue')
        parts = urlsplit(item['url'])
        require(parts.scheme == 'https' and parts.hostname == meta.get('site') and parts.netloc == parts.hostname
                and parts.path == '/browse/' + item['key'] and not parts.query and not parts.fragment,
                'Invalid Jira issue URL')
        require(item['key'] not in result, 'Duplicate Jira issue')
        require(isinstance(item.get('roles'), list) and all(isinstance(role, str) for role in item['roles']),
                'Invalid Jira roles')
        require(type(item.get('sensitive')) is bool, 'Missing Jira sensitivity evidence')
        require(isinstance(item.get('status'), str) and isinstance(item.get('status_category'), str), 'Invalid Jira status')
        result[item['key']] = item
    for event in jira.get('activity', []):
        require(isinstance(event, dict) and event.get('key') in result, 'Unknown Jira activity issue')
        require(start is not None and start <= timestamp(event['occurred_at']) <= end, 'Jira activity outside capture window')
        require(meta.get('identity_verified') is True and isinstance(event.get('actor_account_id'), str)
                and bool(event['actor_account_id']) and event['actor_account_id'] == meta.get('account_id'),
                'Unverified Jira activity actor')
        require(isinstance(event.get('kind'), str) and event['kind'] in {'comment_created', 'comment_edited'},
                'Invalid Jira activity kind')
        require(isinstance(event.get('comment_id'), str) and bool(event['comment_id']),
                'Invalid Jira comment identity')
        require(event.get('url') == result[event['key']]['url'] + '?focusedCommentId=' + quote(event['comment_id'], safe=''),
                'Invalid Jira activity URL')
    return result
