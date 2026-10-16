"""Validation and citations for additional source inventories."""

import json
import re
from urllib.parse import quote, urlsplit

from evidence import HASH, HOST, REPO, integer, is_pr_url, require, timestamp, valid_item


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


def comment_node_bound(node, rest):
    """Bind a later native observation to the same comment and native author."""
    require(isinstance(node, dict) and node.get('id') == rest.get('node_id')
            and integer(node.get('databaseId'), 1) and node['databaseId'] == rest.get('id')
            and node.get('url') == rest.get('html_url')
            and timestamp(node.get('createdAt')) == timestamp(rest.get('created_at'))
            and timestamp(node.get('createdAt')) <= timestamp(node.get('updatedAt')),
            'Native comment history identity mismatch')
    require('author' in node, 'Missing native history author')
    author, original = node['author'], rest.get('user')
    if author is None or original is None:
        require(author is None and original is None, 'Native history deleted author mismatch')
    else:
        require(isinstance(author, dict) and isinstance(original, dict), 'Invalid native history author')
        if author.get('id') and original.get('node_id'):
            require(author['id'] == original['node_id'], 'Native history author ID mismatch')
        else:
            require(isinstance(author.get('login'), str) and bool(author['login'])
                    and author['login'].casefold() == original.get('login', '').casefold(),
                    'Native history author mismatch')


def comment_history_personal_edits(record, start, end, login):
    """Replay complete native edit pages and retain exact-window personal revisions."""
    rest, initial = record['rest_observation'], record['current_observation']
    require(rest.get('body') is None or isinstance(rest['body'], str), 'Invalid native REST history body')
    comment_node_bound(initial, rest)
    pages = record['pages']
    require(isinstance(pages, list) and 0 < len(pages) <= 100, 'Missing/bounded native edit history')
    cursor, cursors, ids, rows, snapshot, total = None, set(), set(), [], None, None
    for index, page in enumerate(pages):
        require(isinstance(page, dict) and page.get('after') == cursor, 'Native edit cursor mismatch')
        response = page.get('response')
        require(isinstance(response, dict) and not response.get('errors'), 'Native edit history query failed')
        node = response['data']['node']
        comment_node_bound(node, rest)
        require(isinstance(node.get('body'), str), 'Missing native current comment body')
        current = {field: node[field] for field in ('id', 'databaseId', 'url', 'author', 'editor',
                   'createdAt', 'updatedAt', 'lastEditedAt', 'includesCreatedEdit', 'body')}
        require(type(current['includesCreatedEdit']) is bool, 'Missing native creation-edit flag')
        require(snapshot is None or current == snapshot, 'Native edit history changed during paging')
        snapshot = current
        require(timestamp(node['updatedAt']) >= max(timestamp(initial['updatedAt']), timestamp(rest['updated_at'])),
                'Native history observation regressed')
        connection = node['userContentEdits']
        require(isinstance(connection, dict) and integer(connection.get('totalCount'))
                and isinstance(connection.get('nodes'), list) and len(connection['nodes']) <= 100,
                'Invalid native edit page')
        require(total is None or total == connection['totalCount'], 'Native edit count changed')
        total = connection['totalCount']
        for row in connection['nodes']:
            require(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']
                    and row['id'] not in ids, 'Duplicate/missing native edit ID')
            ids.add(row['id'])
            edited, created, updated = (timestamp(row.get(field)) for field in ('editedAt', 'createdAt', 'updatedAt'))
            require(timestamp(node['createdAt']) <= edited <= created <= updated
                    and edited <= timestamp(node['updatedAt'])
                    and 'deletedAt' in row and row['deletedAt'] is None,
                    'Deleted or invalid native edit revision')
            require(not rows or timestamp(rows[-1]['editedAt']) >= edited, 'Native edit ordering changed')
            require(isinstance(row.get('editor'), dict) and isinstance(row['editor'].get('login'), str)
                    and bool(row['editor']['login']), 'Missing native historical editor')
            rows.append(row)
        info = connection.get('pageInfo')
        require(isinstance(info, dict) and type(info.get('hasNextPage')) is bool, 'Invalid native edit page info')
        if not info['hasNextPage']:
            require(index == len(pages) - 1 and len(rows) == total, 'Native edit total mismatch')
            break
        cursor = info.get('endCursor')
        require(isinstance(cursor, str) and cursor and cursor not in cursors
                and connection['nodes'] and len(rows) < total and index < len(pages) - 1,
                'Incomplete native edit pagination')
        cursors.add(cursor)
    else:
        raise ValueError('Native edit pagination did not terminate')
    creation_rows = ([row for row in rows if timestamp(row['editedAt']) == timestamp(snapshot['createdAt'])]
                     if snapshot['includesCreatedEdit'] else [])
    require(len(creation_rows) == int(snapshot['includesCreatedEdit']), 'Ambiguous native creation revision')
    edits = [row for row in rows if row not in creation_rows]
    if edits:
        require(timestamp(snapshot['lastEditedAt']) == max(timestamp(row['editedAt']) for row in edits)
                and isinstance(snapshot['editor'], dict)
                and snapshot['editor'].get('login', '').casefold() == edits[0]['editor']['login'].casefold(),
                'Latest native history edit mismatch')
    else:
        require(snapshot['lastEditedAt'] is None and snapshot['editor'] is None, 'Missing native edit revisions')
    require('lastEditedAt' in initial and 'editor' in initial, 'Missing initial native edit observation')
    if initial['lastEditedAt'] is not None:
        initial_time = timestamp(initial['lastEditedAt'])
        require(timestamp(initial['createdAt']) <= initial_time <= timestamp(initial['updatedAt']),
                'Invalid initial native edit time')
        observed = [row for row in edits if timestamp(row['editedAt']) == initial_time]
        require(observed, 'Earlier observed native edit disappeared from history')
        if isinstance(initial['editor'], dict) and initial['editor'].get('login'):
            require(any(row['editor']['login'].casefold() == initial['editor']['login'].casefold()
                        for row in observed), 'Earlier observed native editor disappeared from history')
    else:
        require(initial['editor'] is None, 'Inconsistent initial native edit observation')
    return [row for row in edits if start <= timestamp(row['editedAt']) <= end
            and row['editor']['login'].casefold() == login.casefold()]


def comment_history_state(record, start, end, login, latest_proof=None):
    """Only an independently credited current observation resolves its own revision."""
    rows = comment_history_personal_edits(record, start, end, login)
    resolved = set()
    if latest_proof is not None:
        require(latest_proof == record['current_observation'], 'Different native latest edit proof')
        rest = record['rest_observation']
        require(timestamp(latest_proof['updatedAt']) == timestamp(rest['updated_at'])
                and isinstance(latest_proof.get('editor'), dict)
                and latest_proof['editor'].get('login', '').casefold() == login.casefold(),
                'Unbound native latest edit proof')
        current = record['pages'][0]['response']['data']['node']
        matches = [row for row in rows if timestamp(row['editedAt']) == timestamp(latest_proof['lastEditedAt'])]
        if (len(matches) == 1 and current['body'] == (rest.get('body') or '')
                and timestamp(current['updatedAt']) == timestamp(latest_proof['updatedAt'])):
            resolved.add(matches[0]['id'])
    ids = [row['id'] for row in rows]
    return not rows, ids, [identity for identity in ids if identity not in resolved]


def validate_review_context(source, item, host='github.com'):
    """A review's submitted time is native context, not a text-revision timestamp."""
    require(isinstance(source, dict) and is_pr_url(item['url']) and integer(source.get('id'), 1),
            'Invalid native review identity')
    require(source.get('html_url') == item['url'] + '#pullrequestreview-' + str(source['id']),
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
        require(source['pull_request_url'] == f"{api}/repos/{item['repo']}/pulls/{item['number']}",
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
    for record in github.get('comment_edit_histories', []):
        for page in record.get('pages', []):
            try:
                node = page['response']['data']['node']
                comment_node_bound(node, record['rest_observation'])
                require(isinstance(node.get('body'), str), 'Missing native body')
                yield {'item_url': record['item_url'], 'repo': record['repo'], 'number': record['number'],
                       'kind': record['kind'], 'id': node['databaseId'], 'node_id': node['id'],
                       'url': node['url'], 'body': node['body'],
                       'author_login': (node['author'] or {}).get('login'),
                       'author_node_id': (node['author'] or {}).get('id'),
                       'created_at': node['createdAt'], 'updated_at': node['updatedAt']}
            except (KeyError, ValueError, TypeError, AttributeError):
                continue


def historical_head_hashes(record, known, host='github.com', html_events=None):
    """Native force-push history proves a former head, without proving weekly work."""
    require(isinstance(record, dict), 'Invalid native head event')
    item = known.get(record.get('item_url'))
    require(item is not None and is_pr_url(item['url']) and record.get('repo') == item['repo']
            and record.get('number') == item['number'], 'Native head event PR mismatch')
    source = record.get('source')
    require(isinstance(source, dict), 'Missing native head event source')
    if record.get('kind') == 'html_force_push':
        require(html_events is not None
                and html_events.get((item['url'], source.get('id'))) == source,
                'Historical HTML head lacks replayed native page and event proof')
        return {source['before_sha'], source['after_sha']}
    if record.get('kind') == 'graphql_force_push':
        require(source.get('__typename') == 'HeadRefForcePushedEvent'
                and isinstance(source.get('id'), str) and bool(source['id'])
                and isinstance(source.get('pullRequest'), dict)
                and isinstance(source['pullRequest'].get('url'), str)
                and source['pullRequest']['url'].casefold() == item['url'].casefold(),
                'Invalid native force-push identity')
        timestamp(source.get('createdAt'))
        heads = []
        for field in ('beforeCommit', 'afterCommit'):
            value = source.get(field)
            require(field in source and (value is None or isinstance(value, dict)
                    and isinstance(value.get('oid'), str) and HASH.fullmatch(value['oid'])),
                    'Invalid native force-push head')
            if value is not None:
                heads.append(value['oid'])
    else:
        require(record.get('kind') == 'rest_force_push' and source.get('event') == 'head_ref_force_pushed'
                and integer(source.get('id'), 1), 'Invalid native REST force-push identity')
        api_base = 'https://api.github.com' if host.casefold() == 'github.com' else f'https://{host}/api/v3'
        require(isinstance(source.get('url'), str)
                and source['url'].casefold() == f"{api_base}/repos/{item['repo']}/issues/events/{source['id']}".casefold()
                and isinstance(source.get('node_id'), str) and bool(source['node_id'])
                and record.get('source_path') == f"repos/{item['repo']}/issues/{item['number']}/timeline",
                'Native REST force-push route mismatch')
        timestamp(source.get('created_at'))
        head = source.get('commit_id')
        require('commit_id' in source and (head is None or isinstance(head, str) and HASH.fullmatch(head)),
                'Invalid native REST head')
        heads = [head] if head is not None else []
    return set(heads)


def validated_timeline_events(github, known, host='github.com'):
    """Replay public pages against already validated native API event identities."""
    from github_timeline import validate_timeline
    timelines = github.get('timeline_html', [])
    require(isinstance(timelines, list), 'Invalid native HTML timeline observations')
    native, opaque, complete = {}, {}, {}
    for record in github.get('head_events', []):
        require(isinstance(record, dict), 'Invalid native head event')
        if record.get('kind') == 'html_force_push':
            continue
        historical_head_hashes(record, known, host)
        source = record['source']
        if record['kind'] == 'rest_force_push':
            native.setdefault(record['item_url'], []).append(source)
        elif source['beforeCommit'] is None or source['afterCommit'] is None:
            opaque.setdefault(record['item_url'], set()).add(source['id'])
        else:
            complete.setdefault(record['item_url'], set()).add(source['id'])
    events, seen = {}, set()
    for record in timelines:
        require(isinstance(record, dict), 'Invalid native HTML timeline')
        item = known.get(record.get('item_url'))
        require(item is not None and record.get('item_url') not in seen
                and record.get('repo') == item['repo'] and record.get('number') == item['number'],
                'Native HTML timeline PR mismatch')
        seen.add(item['url'])
        sources = native.get(item['url'], [])
        possible = {source['id'] for source in sources
                    if source['node_id'] in opaque.get(item['url'], set())}
        required = {source['id'] for source in sources if source['id'] in possible
                    and source['node_id'] not in complete.get(item['url'], set())}
        targets = record.get('target_event_ids')
        require(isinstance(targets, list) and bool(targets) and all(integer(value, 1) for value in targets)
                and required <= set(targets) <= possible, 'Native HTML timeline lacks unresolved API event targets')
        # Preserve an earlier native HTML observation when a later complete
        # GraphQL view resolves one of its original targets.
        validate_timeline(record, item, sources, targets)
        events.update({(item['url'], event['id']): event for event in record['events']})
    return events


def validate_github(data, evidence, start, end):
    github, metadata = data['github'], data['metadata']
    host = metadata.get('github_host', 'github.com')
    inventory = {row.get('path'): row for row in metadata.get('repositories', []) if isinstance(row, dict)}
    routes = {path: {remote.get('repo') for remote in row.get('remotes', [])
                    if isinstance(remote, dict) and remote.get('kind') == 'github'
                    and remote.get('host', host) == host} for path, row in inventory.items()}
    unrouted = {path for path, repositories in routes.items() if not repositories}
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
    observations = github.get('pr_index_observations', [])
    require(isinstance(observations, list), 'Invalid native PR index observations')
    indexed_observations = {url: [item] for url, item in indexed.items()}
    for item in observations:
        valid_item(item, 'prs', host)
        require(item['url'] in indexed, 'Native PR observation absent from index')
        for field in ('head_sha', 'merge_sha'):
            require(item.get(field) is None or isinstance(item[field], str) and HASH.fullmatch(item[field]),
                    'Invalid native PR observation snapshot')
        indexed_observations[item['url']].append(item)
    head_events = github.get('head_events', [])
    require(isinstance(head_events, list), 'Invalid native head history')
    known = {**indexed, **evidence}
    html_events = validated_timeline_events(github, known, host)
    historical_heads = [(record, historical_head_hashes(record, known, host, html_events)) for record in head_events]
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
    reviews = github.get('reference_reviews', [])
    require(isinstance(reviews, list), 'Invalid native review contexts')
    reviews_seen = set()
    for record in reviews:
        require(isinstance(record, dict), 'Invalid native review context')
        item = evidence.get(record.get('item_url')) or indexed.get(record.get('item_url'))
        require(item is not None and record.get('repo') == item['repo']
                and record.get('number') == item['number'], 'Native review item mismatch')
        validate_review_context(record.get('source'), item, host)
        identity = item['url'], json.dumps(record['source'], sort_keys=True)
        require(identity not in reviews_seen, 'Duplicate native review observation')
        reviews_seen.add(identity)
    histories = github.get('comment_edit_histories', [])
    require(isinstance(histories, list), 'Invalid native comment edit histories')
    histories_seen = set()
    for record in histories:
        require(isinstance(record, dict), 'Invalid native comment edit history')
        identity = record.get('item_url'), record.get('kind'), record.get('comment_id')
        comment = comments_seen.get(identity)
        require(comment is not None and identity not in histories_seen
                and record.get('repo') == comment['repo'] and record.get('number') == comment['number']
                and record.get('node_id') == comment.get('node_id'), 'Native edit history target mismatch')
        histories_seen.add(identity)
        rest = record.get('rest_observation')
        require(isinstance(rest, dict) and rest.get('id') == comment['id']
                and rest.get('node_id') == comment.get('node_id') and rest.get('html_url') == comment['url']
                and (rest.get('body') or '') == comment['body']
                and timestamp(rest.get('created_at')) == timestamp(comment['created_at'])
                and timestamp(rest.get('updated_at')) == timestamp(comment['updated_at']),
                'Native history REST observation mismatch')
        author = rest.get('user')
        require((author or {}).get('login') == comment.get('author_login')
                and (author or {}).get('node_id') == comment.get('author_node_id'),
                'Native history REST author mismatch')
        comment_node_bound(record.get('current_observation'), rest)
        require(record.get('status') in {'ok', 'partial'}
                and type(record.get('no_personal_edits_in_window')) is bool,
                'Invalid native history status')
        latest = None
        for event in github.get('activity', []):
            if (event.get('item_url') == comment['item_url'] and event.get('id') == comment['id']
                    and event.get('action') == comment['kind'] + '_edited'
                    and event.get('editor_evidence') == record['current_observation']):
                validate_editor_evidence(event)
                validate_editor_comment(event, comment)
                require(start <= timestamp(event['at']) <= end
                        and event['actor'].casefold() == metadata['github_user'].casefold(),
                        'Invalid resolved native latest edit')
                latest = event['editor_evidence']
        try:
            negative, personal_ids, unresolved_ids = comment_history_state(record, start, end,
                                                                          metadata['github_user'], latest)
        except (KeyError, ValueError, TypeError, AttributeError):
            negative, personal_ids, unresolved_ids = False, None, None
        complete = personal_ids is not None
        require(record['no_personal_edits_in_window'] == negative
                and record.get('personal_edit_ids') == personal_ids
                and record.get('unresolved_personal_edit_ids') == unresolved_ids
                and record['status'] == ('ok' if complete and not unresolved_ids else 'partial'),
                'Unproven native edit-history status')
        if not complete or unresolved_ids:
            require(metadata.get('github_status') == 'partial' and metadata.get('warnings'),
                    'Missing native edit-history coverage gap')
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
        if 'body_source' in event:
            require(event['body_source'] == 'rest_current_observation'
                    and event['action'] in {'issue_comment', 'review_comment',
                                            'issue_comment_edited', 'review_comment_edited'},
                    'Invalid native comment body provenance')
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
        require(isinstance(key, str) and isinstance(info, dict) and info.get('status') in {'ok', 'partial', 'unsupported'}
                and integer(info.get('pages')) and integer(info.get('count')), 'Invalid GitHub discovery coverage')
        if info['status'] == 'unsupported':
            require(key.startswith('unrouted:') and info.get('scope') == 'github_global_without_repository'
                    and info['pages'] == info['count'] == 0
                    and isinstance(info.get('reason'), str) and bool(info['reason'].strip())
                    and key.removeprefix('unrouted:') in unrouted, 'Invalid unsupported GitHub repository scope')
        if info['status'] == 'partial':
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
                                       'global_any_author_sha_search', 'commit_association',
                                       'historical_head_event'},
                    'Invalid branch PR proof')
            if pr['proof'] == 'exact_head_or_merge_sha':
                require(any(branch['hash'] in (row.get('head_sha'), row.get('merge_sha'))
                            for row in indexed_observations[pr['url']]), 'Branch SHA mismatch')
            if pr['proof'] == 'historical_head_event':
                require(any(record['item_url'] == pr['url'] and branch['hash'] in hashes
                            and ('head_event_id' not in pr or pr['head_event_id'] == record['source']['id'])
                            for record, hashes in historical_heads), 'Historical branch head lacks native event proof')
        if branch['pr_status'] == 'matched':
            require(bool(prs), 'Matched branch needs PR evidence')
        if branch['pr_status'] == 'no_match':
            lookups = branch.get('association_lookups', [])
            require(bool(lookups) and all(info.get('status') == 'ok' for info in lookups) and not prs,
                    'No-match branch needs completed lookups')
        if branch['pr_status'] == 'unsupported':
            lookups = branch.get('association_lookups', [])
            require(branch.get('association_scope') == 'github_global_without_repository'
                    and branch.get('repo') in unrouted
                    and bool(lookups) and all(info.get('status') == 'ok' for info in lookups)
                    and any(info.get('scope') == 'github_global' for info in lookups)
                    and not prs, 'Unsupported branch needs completed global lookups without repository scope')
        if branch['pr_status'] == 'related_only':
            lookups, related = branch.get('association_lookups', []), branch.get('related_prs', [])
            require(branch.get('repo') in inventory and branch['repo'] not in unrouted
                    and bool(lookups) and all(isinstance(info, dict) and info.get('status') == 'ok' for info in lookups)
                    and isinstance(related, list) and bool(related) and not prs,
                    'Related-only branch needs completed repository lookups and native candidates')
            require(routes[branch['repo']] <= {info.get('repo') for info in lookups},
                    'Related-only branch lacks completed configured repository scopes')
            names = {ref.removeprefix('refs/heads/') for ref in branch.get('refs', [])
                     if ref.startswith('refs/heads/')}
            names.update(ref.removeprefix('refs/remotes/').split('/', 1)[1]
                         for ref in branch.get('refs', []) if ref.startswith('refs/remotes/')
                         and '/' in ref.removeprefix('refs/remotes/'))
            for candidate in related:
                require(isinstance(candidate, dict) and candidate.get('proof') == 'head_name_only'
                        and any(row.get('head_ref') in names and all(candidate.get(field) == row.get(field)
                                for field in ('repo', 'number', 'url', 'state'))
                                for row in indexed_observations.get(candidate.get('url'), [])),
                        'Related-only candidate lacks a captured native matching branch name')
                require(all(metadata.get('github_discovery', {}).get(prefix + candidate['url'], {}).get('status', 'ok') == 'ok'
                            for prefix in ('head_history:', 'rest_head_history:', 'html_head_history:')),
                        'Related-only candidate has an incomplete historical read')
    # Indexed snapshots remain citations, but use the primary observed state when available.
    for url, item in indexed.items():
        evidence.setdefault(url, item)


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


def validate_jira(data, start, end):
    jira = data.get('jira')
    if jira is None:
        return {}
    from jira_sources import validate_full_details
    validate_full_details(jira)
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
    jira_key_aliases(jira)
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
