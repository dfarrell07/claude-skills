"""Validation and citations for additional source inventories."""

import re
from urllib.parse import quote, urlsplit

from evidence import HASH, HOST, REPO, integer, require, timestamp, valid_item


def repository_aliases(github, host):
    """Bind renamed repository routes only through two native repository IDs."""
    records = github.get('repository_aliases', [])
    require(isinstance(records, list), 'Invalid GitHub repository aliases')
    aliases, identities, route_ids = {}, {}, {}
    for record in records:
        require(isinstance(record, dict) and record.get('host') == host,
                'Invalid GitHub repository alias host')
        requested, canonical = record.get('requested_repo'), record.get('canonical_repo')
        for repo in (requested, canonical):
            require(isinstance(repo, str) and REPO.fullmatch(repo)
                    and all(part not in {'.', '..'} for part in repo.split('/')),
                    'Invalid GitHub repository alias route')
        proofs = []
        for field, repo in (('requested', requested), ('canonical', canonical)):
            proof = record.get(field)
            require(isinstance(proof, dict) and proof.get('path') == 'repos/' + repo
                    and integer(proof.get('id'), 1) and proof.get('full_name') == canonical
                    and proof.get('html_url') == f'https://{host}/{canonical}',
                    'Invalid native GitHub repository alias proof')
            proofs.append(proof)
        require(proofs[0]['id'] == proofs[1]['id'], 'GitHub repository alias ID mismatch')
        for repo in (requested, canonical):
            route = host.casefold(), repo.casefold()
            require(route not in route_ids or route_ids[route] == proofs[0]['id'],
                    'Conflicting GitHub repository route IDs')
            route_ids[route] = proofs[0]['id']
        key = (host.casefold(), requested.casefold())
        identity = canonical.casefold(), proofs[0]['id']
        require(key not in identities or identities[key] == identity, 'Conflicting GitHub repository alias')
        identities[key] = identity
        aliases[key] = canonical.casefold()
    return aliases


def validate_activity_identity(event, item, host):
    """Native cross-reference rows have source identity, not an event ID."""
    if event.get('id') is not None:
        require(integer(event['id'], 1), 'Invalid GitHub activity identity')
        require('source' not in event, 'Unexpected GitHub activity source')
        return
    require('id' in event and event['action'] == 'cross-referenced'
            and event['url'] == item['url'], 'Missing GitHub activity identity')
    source = event.get('source')
    require(isinstance(source, dict) and set(source) == {'type', 'issue'}
            and source.get('type') == 'issue', 'Invalid GitHub cross-reference source')
    issue = source['issue']
    require(isinstance(issue, dict) and set(issue) == {'id', 'number', 'html_url'}
            and integer(issue.get('id'), 1) and integer(issue.get('number'), 1)
            and isinstance(issue.get('html_url'), str), 'Invalid GitHub cross-reference identity')
    parts = urlsplit(issue['html_url'])
    path = parts.path.strip('/').split('/')
    require(len(path) == 4 and REPO.fullmatch('/'.join(path[:2]))
            and all(part not in {'.', '..'} for part in path[:2])
            and path[2] in {'pull', 'issues'} and path[3] == str(issue['number'])
            and parts.netloc.casefold() == host.casefold()
            and issue['html_url'] == f'https://{parts.netloc}/{"/".join(path)}',
            'Invalid GitHub cross-reference URL')


def validate_editor_evidence(event):
    """Bind a recovered native edit to the comment, editor and exact edit time."""
    if 'editor_evidence' not in event:
        return
    proof = event['editor_evidence']
    require(event['action'] in {'issue_comment_edited', 'review_comment_edited'}
            and isinstance(proof, dict), 'Unexpected GitHub editor evidence')
    require(isinstance(proof.get('id'), str) and bool(proof['id'])
            and integer(proof.get('databaseId'), 1) and proof['databaseId'] == event['id']
            and proof.get('url') == event['url'], 'GitHub editor comment mismatch')
    require('author' in proof and (proof['author'] is None or isinstance(proof['author'], dict)
            and isinstance(proof['author'].get('login'), str) and bool(proof['author']['login'])),
            'Missing native GitHub comment author observation')
    require(isinstance(proof.get('editor'), dict) and isinstance(proof['editor'].get('login'), str)
            and bool(proof['editor']['login']), 'Missing native GitHub comment editor')
    require(proof['editor']['login'].casefold() == event['actor'].casefold(),
            'GitHub comment editor mismatch')
    created = timestamp(proof.get('createdAt'))
    edited = timestamp(proof.get('lastEditedAt'))
    updated = timestamp(proof.get('updatedAt'))
    require(created <= edited <= updated and edited == timestamp(event['at']),
            'GitHub native edit time mismatch')


def validate_editor_comment(event, comment):
    """A saved edit proof and its retained discussion row are one native observation."""
    proof = event['editor_evidence']
    require(comment is not None and proof['id'] == comment.get('node_id')
            and proof['url'] == comment['url']
            and event['body'] == comment['body']
            and timestamp(proof['createdAt']) == timestamp(comment['created_at'])
            and timestamp(proof['updatedAt']) == timestamp(comment['updated_at']),
            'GitHub editor proof disagrees with retained comment')
    author = proof['author']
    if author is None:
        require(comment.get('author_login') is None and comment.get('author_node_id') is None,
                'GitHub deleted author observation mismatch')
    else:
        native_id = comment.get('author_node_id')
        if native_id and author.get('id'):
            require(native_id == author['id'], 'GitHub native comment author ID mismatch')
        else:
            require(isinstance(comment.get('author_login'), str)
                    and comment['author_login'].casefold() == author['login'].casefold(),
                    'GitHub native comment author mismatch')


def validate_github(data, evidence, start, end):
    github, metadata = data['github'], data['metadata']
    host = metadata.get('github_host', 'github.com')
    repository_aliases(github, host)
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
    comments_seen = {}
    comments = github.get('reference_comments', [])
    require(isinstance(comments, list), 'Invalid GitHub reference comments')
    for comment in comments:
        require(isinstance(comment, dict), 'Invalid native GitHub comment')
        item = evidence.get(comment.get('item_url')) or indexed.get(comment.get('item_url'))
        require(item is not None and comment.get('repo') == item['repo']
                and comment.get('number') == item['number'], 'GitHub comment item mismatch')
        require(comment.get('kind') in {'issue_comment', 'review_comment'}
                and integer(comment.get('id'), 1), 'Invalid GitHub comment identity')
        fragment = 'issuecomment-' if comment['kind'] == 'issue_comment' else 'discussion_r'
        require(comment.get('url') == item['url'] + '#' + fragment + str(comment['id']),
                'Invalid native GitHub comment URL')
        require(isinstance(comment.get('body'), str)
                and (comment.get('author_login') is None or isinstance(comment['author_login'], str)
                     and bool(comment['author_login']))
                and (comment.get('node_id') is None or isinstance(comment['node_id'], str)
                     and bool(comment['node_id'])), 'Invalid native GitHub comment fields')
        require(comment.get('author_node_id') is None or isinstance(comment['author_node_id'], str)
                and bool(comment['author_node_id']), 'Invalid native GitHub author node ID')
        require(timestamp(comment.get('created_at')) <= timestamp(comment.get('updated_at')),
                'Reversed native GitHub comment timestamps')
        identity = comment['item_url'], comment['kind'], comment['id']
        require(identity not in comments_seen, 'Duplicate native GitHub comment')
        comments_seen[identity] = comment
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
        validate_activity_identity(event, item, host)
        validate_editor_evidence(event)
        if 'editor_evidence' in event and 'reference_comments' in github:
            kind = event['action'].removesuffix('_edited')
            validate_editor_comment(event, comments_seen.get((event['item_url'], kind, event['id'])))
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
            require(pr.get('proof') in {'exact_head_or_merge_sha', 'any_author_sha_search',
                                       'global_any_author_sha_search', 'commit_association'},
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
        require(isinstance(item.get('status'), str) and item.get('status_category') in {'new', 'indeterminate', 'done'},
                'Invalid Jira status')
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
