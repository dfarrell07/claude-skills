"""Role-aware GitHub discovery, actor evidence, and batched ref-to-PR association."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
import json
import subprocess
from threading import BoundedSemaphore, RLock
from urllib.parse import urlsplit

from extended_evidence import comment_history_state, comment_node_bound
from evidence import HASH, LIMIT, PAGE_SIZE, REPO, UTC, decode_json, integer, is_pr_url, iso, require, timestamp, valid_item


ERRORS = (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, AttributeError)
MAX_LIST_PAGES = 100
BATCH_SIZE = 20
SNAPSHOT_WORKERS = 4


PR_FIELDS = '''url title body number state createdAt updatedAt closedAt mergedAt isDraft headRefOid headRefName
repository { nameWithOwner } headRepository { nameWithOwner } mergeCommit { oid } baseRefName'''


def rest_item(row, kind, host):
    body = row.get('body')
    require(body is None or isinstance(body, str), 'Invalid GitHub body')
    url = row['html_url']
    parts = urlsplit(url).path.strip('/').split('/')
    require(len(parts) == 4, 'Malformed GitHub item URL')
    is_pr = 'pull_request' in row
    require(is_pr == (kind == 'prs'), 'Wrong GitHub item kind')
    merged = row['pull_request']['merged_at'] if is_pr else None
    item = {'repo': '/'.join(parts[:2]), 'number': row['number'], 'title': row['title'],
            'state': 'merged' if merged else row['state'], 'url': url, 'body': '' if body is None else body,
            'created_at': row['created_at'],
            'updated_at': row['updated_at'], 'closed_at': row['closed_at'], 'merged_at': merged,
            'draft': row.get('draft', False), 'author_login': (row.get('user') or {}).get('login')}
    valid_item(item, kind, host)
    require(item['author_login'] is None or isinstance(item['author_login'], str), 'Invalid GitHub author login')
    require(type(item['draft']) is bool, 'Invalid GitHub draft flag')
    return item


def graphql_item(row, host):
    body = row.get('body')
    require(body is None or isinstance(body, str), 'Invalid GitHub body')
    item = {'repo': row['repository']['nameWithOwner'], 'number': row['number'], 'title': row['title'],
            'state': row['state'].lower(), 'url': row['url'], 'body': '' if body is None else body,
            'created_at': row['createdAt'],
            'updated_at': row['updatedAt'], 'closed_at': row['closedAt'], 'merged_at': row['mergedAt'],
            'draft': row['isDraft'], 'head_sha': row['headRefOid'], 'head_ref': row['headRefName'],
            'head_repo': (row.get('headRepository') or {}).get('nameWithOwner'),
            'merge_sha': (row.get('mergeCommit') or {}).get('oid'), 'base_ref': row['baseRefName']}
    valid_item(item, 'prs', host)
    require(isinstance(item['head_sha'], str) and HASH.fullmatch(item['head_sha']), 'Invalid PR head SHA')
    require(item['merge_sha'] is None or isinstance(item['merge_sha'], str) and HASH.fullmatch(item['merge_sha']),
            'Invalid PR merge SHA')
    require(isinstance(item['head_ref'], str) and item['head_ref'], 'Invalid PR head ref')
    return item


def cross_reference_source(event, host):
    """Retain native source identity for timeline events that have no event ID."""
    source = event['source']
    require(source['type'] == 'issue', 'Invalid cross-reference source type')
    issue = source['issue']
    require(integer(issue.get('id'), 1) and integer(issue.get('number'), 1),
            'Missing cross-reference source identity')
    parsed = urlsplit(issue['html_url'])
    parts = parsed.path.strip('/').split('/')
    require(len(parts) == 4 and REPO.fullmatch('/'.join(parts[:2]))
            and all(part not in ('.', '..') for part in parts[:2])
            and parts[2] in {'pull', 'issues'} and parts[3] == str(issue['number'])
            and parsed.netloc.casefold() == host.casefold()
            and issue['html_url'] == f'https://{parsed.netloc}/{"/".join(parts)}',
            'Invalid cross-reference source URL')
    return {'type': 'issue', 'issue': {field: issue[field] for field in ('id', 'number', 'html_url')}}


class Github:
    def __init__(self, api, host, login, start, end, warn):
        self.api, self.host, self.login = api, host, login
        self.start, self.end, self.warn = start, end, warn
        self.coverage = {}
        self.cache = {}
        self._linked_attempts = set()
        self._activity_items = set()
        self._activity_seen = set()
        self._comment_seen = set()
        self._global_snapshots = {}
        self._head_seen = set()
        self._history_read = set()
        self._branch_snapshots = None
        self._repository_metadata = {}
        self._repository_aliases = {}
        self._alias_lock = RLock()
        self._snapshot_io = BoundedSemaphore(SNAPSHOT_WORKERS)

    def graphql(self, *args):
        # gh exits nonzero for GraphQL errors even when stdout retains successful
        # sibling aliases. Recover that structured evidence instead of discarding it.
        try:
            return self.api(*args)
        except subprocess.CalledProcessError as error:
            if not error.stdout:
                raise
            response = decode_json(error.stdout)
            require(isinstance(response, dict) and isinstance(response.get('data'), dict), 'No partial GraphQL data')
            if not response.get('errors'):
                response['errors'] = [{'message': 'GraphQL command failed with partial data'}]
            return response

    def problem(self, key, message):
        info = self.coverage.setdefault(key, {'status': 'ok', 'pages': 0, 'count': 0})
        info['status'] = 'partial'
        self.warn(f'GitHub {key}: {message}')

    def listing(self, endpoint, key):
        info = self.coverage.setdefault(key, {'status': 'ok', 'pages': 0, 'count': 0})
        result, fingerprints = [], set()
        page = 1
        while page <= MAX_LIST_PAGES:
            try:
                rows = self.api('--method', 'GET', endpoint, '-F', 'per_page=100', '-F', f'page={page}')
                require(isinstance(rows, list) and len(rows) <= PAGE_SIZE, 'Malformed GitHub list page')
                fingerprint = json.dumps(rows, sort_keys=True)
                require(not rows or fingerprint not in fingerprints, 'Repeated GitHub page')
                fingerprints.add(fingerprint)
                result.extend(rows)
                info['pages'] += 1
                info['count'] = len(result)
                if len(rows) < PAGE_SIZE:
                    break
                page += 1
            except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError):
                self.problem(key, 'read failed or paging did not terminate; retained earlier pages')
                break
        else:
            self.problem(key, 'list reached the 10,000-row safety cap; coverage is incomplete')
        return result

    def search(self, query, key, kind):
        info = self.coverage.setdefault(key, {'status': 'ok', 'pages': 0, 'count': 0, 'query': query, 'incomplete_results': False})
        found, seen, first_total = [], set(), None
        for page in range(1, LIMIT // PAGE_SIZE + 1):
            try:
                response = self.api('--method', 'GET', 'search/issues', '-f', 'q=' + query,
                                    '-f', 'sort=updated', '-f', 'order=desc', '-F', 'per_page=100', '-F', f'page={page}')
                require(isinstance(response, dict) and integer(response.get('total_count'))
                        and type(response.get('incomplete_results')) is bool and isinstance(response.get('items'), list)
                        and len(response['items']) <= PAGE_SIZE,
                        'Malformed GitHub search')
                total = response['total_count']
                info['pages'] += 1
                info['total_count'] = total
                if first_total is not None and total != first_total:
                    self.problem(key, 'result count changed during collection')
                first_total = total if first_total is None else first_total
                info['incomplete_results'] |= response['incomplete_results']
                if response['incomplete_results'] or total > LIMIT:
                    self.problem(key, 'search is incomplete or exceeds the 1,000-result limit')
                for row in response['items']:
                    try:
                        item = rest_item(row, kind, self.host)
                        require(item['url'] not in seen, 'Duplicate item during paging')
                        seen.add(item['url'])
                        found.append(item)
                    except (ValueError, KeyError, TypeError, AttributeError):
                        self.problem(key, 'an invalid/duplicate item was skipped')
                info['count'] = len(found)
                expected = min(PAGE_SIZE, max(0, min(total, LIMIT) - (page - 1) * PAGE_SIZE))
                if len(response['items']) < expected:
                    self.problem(key, 'server returned fewer rows than its reported count')
                    break
                if page * PAGE_SIZE >= min(total, LIMIT):
                    if len(found) != min(total, LIMIT):
                        self.problem(key, 'search count disagrees with retained rows')
                    break
            except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError):
                self.problem(key, 'search failed; retained earlier pages')
                break
        return found

    def discover(self, github, linked_prs=()):
        github['context_prs'], github['context_issues'], github['activity'] = [], [], []
        all_items = {item['url']: item for kind in ('prs', 'issues') for item in github[kind]}
        since = iso(self.start)
        specifications = [
            ('open_authored_prs', 'prs', f'is:pr author:{self.login} is:open'),
            ('open_assigned_prs', 'prs', f'is:pr assignee:{self.login} is:open'),
            ('requested_reviews', 'prs', f'is:pr review-requested:{self.login} is:open'),
            ('recent_authored_prs', 'prs', f'is:pr author:{self.login} updated:>={since}'),
            ('recent_reviews', 'prs', f'is:pr reviewed-by:{self.login} updated:>={since}'),
            ('recent_comments', 'prs', f'is:pr commenter:{self.login} updated:>={since}'),
            ('recent_involvement', 'prs', f'is:pr involves:{self.login} updated:>={since}'),
            ('open_authored_issues', 'issues', f'is:issue author:{self.login} is:open'),
            ('open_assigned_issues', 'issues', f'is:issue assignee:{self.login} is:open'),
            ('recent_issue_involvement', 'issues', f'is:issue involves:{self.login} updated:>={since}'),
        ]
        for key, kind, query in specifications:
            for item in self.search(query, key, kind):
                if item['url'] not in all_items:
                    item['roles'] = []
                    item['observed_at'] = iso(datetime.now(UTC))
                    github['context_' + kind].append(item)
                    all_items[item['url']] = item
                original = all_items[item['url']]
                original.setdefault('roles', [])
                if key not in original['roles']:
                    original['roles'].append(key)
                original.setdefault('author_login', item.get('author_login'))
        for item in self.add_linked_prs(github, linked_prs):
            all_items[item['url']] = item
        self._inspect_activity(github, all_items.values())
        return all_items

    def add_linked_prs(self, github, urls):
        """Fetch only new Jira-linked PRs, preserving captured context and events.

        Existing native items gain a role without rereading their endpoints.
        New items receive actor inspection once; failures remain visible in this
        instance's coverage and are not silently retried by another frontier.
        """
        for key in ('context_prs', 'context_issues', 'activity'):
            github.setdefault(key, [])
        all_items = {item['url']: item for kind in ('prs', 'issues', 'context_prs', 'context_issues')
                     for item in github.get(kind, [])}
        by_identity = {(urlsplit(item['url']).netloc.casefold(), item['repo'].casefold(), item['number']): item
                       for item in all_items.values() if is_pr_url(item['url'])}
        new_items = []
        for url in urls:
            key = 'jira_links'
            try:
                require(isinstance(url, str), 'Invalid linked PR URL')
                if url in self._linked_attempts:
                    continue
                self._linked_attempts.add(url)
                parsed = urlsplit(url)
                parts = parsed.path.strip('/').split('/')
                require(len(parts) == 4 and parts[2] == 'pull' and parts[3].isascii()
                        and parts[3].isdigit() and int(parts[3]) > 0, 'Invalid linked PR URL')
                repo, number = '/'.join(parts[:2]), int(parts[3])
                require(REPO.fullmatch(repo) and all(part not in ('.', '..') for part in repo.split('/'))
                        and parsed.netloc.casefold() == self.host.casefold()
                        and url == f'https://{parsed.netloc}/{repo}/pull/{number}', 'Invalid linked PR host or path')
                key = f'jira_link:{repo}#{number}'
                identity = (self.host.casefold(), repo.casefold(), number)
                original = by_identity.get(identity)
                if original is None:
                    row = self.api('--method', 'GET', f'repos/{repo}/issues/{number}')
                    item = rest_item(row, 'prs', self.host)
                    require(item['number'] == number, 'Linked PR response number disagrees')
                    if item['repo'].casefold() != repo.casefold():
                        proof = self._verify_repository_alias(repo, item['repo'])
                        if proof not in github.setdefault('repository_aliases', []):
                            github['repository_aliases'].append(proof)
                    canonical_identity = (self.host.casefold(), item['repo'].casefold(), number)
                    original = by_identity.get(canonical_identity)
                    if original is None:
                        item['roles'], item['observed_at'] = [], iso(datetime.now(UTC))
                        github['context_prs'].append(item)
                        all_items[item['url']] = item
                        by_identity[canonical_identity] = original = item
                        new_items.append(item)
                    by_identity[identity] = original
                    self.coverage[key] = {'status': 'ok', 'pages': 1, 'count': 1,
                                          'requested_url': url, 'resolved_url': item['url']}
                if url not in original.setdefault('jira_seed_urls', []):
                    original['jira_seed_urls'].append(url)
                if 'jira_link' not in original.setdefault('roles', []):
                    original['roles'].append('jira_link')
            except ERRORS:
                self.problem(key, 'linked PR unavailable or invalid; omitted link is a coverage gap')
        self._inspect_activity(github, new_items)
        return new_items

    def _verify_repository_alias(self, requested, canonical):
        """Prove a repository redirect with independently fetched native IDs."""
        key = (requested.casefold(), canonical.casefold())
        if key in self._repository_aliases:
            proof = self._repository_aliases[key]
            require(proof is not None, 'Repository alias could not be verified')
            return proof
        # Failed identity reads remain unknown for this observation; repeated
        # old links must not cause two extra repository reads each time.
        self._repository_aliases[key] = None
        records = []
        for repo in (requested, canonical):
            cache_key = repo.casefold()
            if cache_key not in self._repository_metadata:
                row = self.api('--method', 'GET', 'repos/' + repo)
                require(isinstance(row, dict), 'Invalid native repository metadata')
                self._repository_metadata[cache_key] = {field: row[field] for field in ('id', 'full_name', 'html_url')}
            native = self._repository_metadata[cache_key]
            require(integer(native['id'], 1) and native['full_name'] == canonical
                    and native['html_url'] == f'https://{self.host}/{canonical}',
                    'Repository redirect identity disagrees')
            records.append({'path': 'repos/' + repo, **native})
        require(records[0]['id'] == records[1]['id'], 'Repository redirect IDs disagree')
        proof = {'host': self.host, 'requested_repo': requested, 'canonical_repo': canonical,
                 'requested': records[0], 'canonical': records[1]}
        self._repository_aliases[key] = proof
        return proof

    def _comment_edit(self, event, category, key, github=None, item=None):
        """Resolve native last-edit evidence without treating updatedAt as an edit."""
        try:
            node_id = event.get('node_id')
            require(isinstance(node_id, str) and node_id, 'Missing native comment node ID')
            node_type = 'IssueComment' if category == 'issue_comment' else 'PullRequestReviewComment'
            query = 'query($id:ID!){node(id:$id){...on ' + node_type + '''{
              id databaseId url author{login ...on Node{id}} editor{login} createdAt updatedAt lastEditedAt
            }}}'''
            result = self.graphql('graphql', '-f', 'query=' + query, '-f', 'id=' + node_id)
            require(isinstance(result, dict) and not result.get('errors'), 'Comment edit query failed')
            node = result['data']['node']
            native_author = node.get('author') if isinstance(node, dict) else None
            rest_author = event.get('user')
            author_bound = native_author is None and rest_author is None
            if isinstance(native_author, dict) and isinstance(rest_author, dict):
                native_id, rest_id = native_author.get('id'), rest_author.get('node_id')
                author_bound = (native_id == rest_id if native_id and rest_id else
                                isinstance(native_author.get('login'), str) and bool(native_author['login'])
                                and native_author['login'].casefold() == rest_author.get('login', '').casefold())
            comment_node_bound(node, event)
            require(author_bound, 'Comment edit author changed')
            if timestamp(node['updatedAt']) != timestamp(event['updated_at']):
                require(github is not None and item is not None, 'No native history destination')
                self._comment_history(github, item, event, category, key, node)
                return None
            require('lastEditedAt' in node and 'editor' in node, 'Missing native edit observation')
            edited, editor = node['lastEditedAt'], node['editor']
            if edited is None:
                require(editor is None, 'Comment edit metadata disagrees')
                return None
            when = timestamp(edited)
            require(timestamp(node['createdAt']) <= when <= timestamp(node['updatedAt']), 'Invalid last edit time')
            editor_known = isinstance(editor, dict) and isinstance(editor.get('login'), str) and bool(editor['login'])
            latest = (node if editor_known and self.start <= when <= self.end
                      and editor['login'].casefold() == self.login.casefold() else None)
            if when >= self.start and timestamp(node['createdAt']) <= self.end and github is not None:
                self._comment_history(github, item, event, category, key, node, latest)
            else:
                require(editor_known, 'Comment edit has no native editor')
            return latest
        except ERRORS:
            self.problem(key, 'comment edit has no verified native editor/time; edit not credited')
            return None

    def _comment_history(self, github, item, event, category, key, initial, latest=None):
        """Retain superseded personal-edit leads without borrowing the current body."""
        record = {'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
                  'kind': category, 'comment_id': event['id'], 'node_id': event['node_id'],
                  'rest_observation': event, 'current_observation': initial, 'pages': [],
                  'status': 'partial', 'no_personal_edits_in_window': False,
                  'personal_edit_ids': None, 'unresolved_personal_edit_ids': None}
        github.setdefault('comment_edit_histories', []).append(record)
        cursor, seen = None, set()
        try:
            node_type = 'IssueComment' if category == 'issue_comment' else 'PullRequestReviewComment'
            for _ in range(MAX_LIST_PAGES):
                query = 'query($id:ID!,$after:String){node(id:$id){...on ' + node_type + """{
                  id databaseId url body author{login ...on Node{id}} editor{login}
                  createdAt updatedAt lastEditedAt includesCreatedEdit
                  userContentEdits(first:100,after:$after){totalCount pageInfo{hasNextPage endCursor}
                    nodes{id editedAt createdAt updatedAt deletedAt editor{login ...on Node{id}}}}
                }}}"""
                args = ['graphql', '-f', 'query=' + query, '-f', 'id=' + event['node_id']]
                if cursor is not None:
                    args.extend(['-f', 'after=' + cursor])
                response = self.graphql(*args)
                record['pages'].append({'after': cursor, 'response': response})
                require(isinstance(response, dict) and not response.get('errors'), 'Native history query failed')
                node = response['data']['node']
                comment_node_bound(node, event)
                info = node['userContentEdits']['pageInfo']
                require(type(info.get('hasNextPage')) is bool, 'Missing native history page status')
                if not info['hasNextPage']:
                    break
                cursor = info.get('endCursor')
                require(isinstance(cursor, str) and cursor and cursor not in seen,
                        'Repeated native edit history cursor')
                seen.add(cursor)
            negative, personal_ids, unresolved_ids = comment_history_state(record, self.start, self.end,
                                                                          self.login, latest)
            record.update(no_personal_edits_in_window=negative, personal_edit_ids=personal_ids,
                          unresolved_personal_edit_ids=unresolved_ids)
            if not unresolved_ids:
                record['status'] = 'ok'
                return
        except ERRORS:
            pass
        self.problem(key, 'native editor history incomplete or contains unresolved personal revisions; historical edit bodies not credited')

    def _retain_comment(self, github, item, event, category, key):
        """Retain native discussion references independently of actor attribution."""
        try:
            require(integer(event.get('id'), 1), 'Missing comment identity')
            url, body = event.get('html_url'), event.get('body')
            fragment = 'issuecomment-' if category == 'issue_comment' else 'discussion_r'
            require(isinstance(url, str) and url == item['url'] + '#' + fragment + str(event['id']),
                    'Comment URL does not identify this item')
            require(body is None or isinstance(body, str), 'Invalid comment body')
            created, updated = event['created_at'], event['updated_at']
            require(timestamp(created) <= timestamp(updated), 'Invalid comment dates')
            author = (event.get('user') or {}).get('login')
            author_node_id = (event.get('user') or {}).get('node_id')
            node_id = event.get('node_id')
            require(author is None or isinstance(author, str) and author, 'Invalid comment author')
            require(node_id is None or isinstance(node_id, str) and node_id, 'Invalid comment node ID')
            require(author_node_id is None or isinstance(author_node_id, str) and author_node_id,
                    'Invalid native comment author ID')
            identity = item['url'], category, event['id']
            if identity in self._comment_seen:
                return
            self._comment_seen.add(identity)
            github.setdefault('reference_comments', []).append({
                'item_url': item['url'], 'repo': item['repo'], 'number': item['number'], 'kind': category,
                'id': event['id'], 'node_id': node_id, 'url': url, 'body': '' if body is None else body,
                'author_login': author, 'author_node_id': author_node_id, 'created_at': created, 'updated_at': updated})
        except ERRORS:
            self.problem(key, 'invalid native comment reference skipped; discussion coverage is incomplete')

    def _retain_head_event(self, github, item, source, kind, key, source_path=None):
        """Preserve native force-push context without attributing weekly activity."""
        try:
            require(isinstance(source, dict), 'Invalid native head event')
            identity = source.get('id')
            if kind == 'rest_force_push':
                require(source.get('event') == 'head_ref_force_pushed' and integer(identity, 1),
                        'Invalid REST head event identity')
                expected_path = f"repos/{item['repo']}/issues/{item['number']}/timeline"
                api_root = 'https://api.github.com' if self.host == 'github.com' else f'https://{self.host}/api/v3'
                require(source_path == expected_path and isinstance(source.get('node_id'), str) and source['node_id']
                        and source.get('url') == api_root + f"/repos/{item['repo']}/issues/events/{identity}",
                        'REST head event route or native ID disagrees')
                timestamp(source.get('created_at'))
                sha = source.get('commit_id')
                require('commit_id' in source and (sha is None or isinstance(sha, str) and HASH.fullmatch(sha)),
                        'Invalid historical commit SHA')
            else:
                require(kind == 'graphql_force_push' and source.get('__typename') == 'HeadRefForcePushedEvent'
                        and isinstance(identity, str) and identity, 'Invalid typed head event identity')
                timestamp(source.get('createdAt'))
                require((source.get('pullRequest') or {}).get('url', '').casefold() == item['url'].casefold(),
                        'Head event belongs to another PR')
                for field in ('beforeCommit', 'afterCommit'):
                    require(field in source and (source[field] is None or isinstance(source[field], dict)
                            and isinstance(source[field].get('oid'), str) and HASH.fullmatch(source[field]['oid'])),
                            'Invalid native before/after head')
                if source['beforeCommit'] is None or source['afterCommit'] is None:
                    self.problem(key, 'a historical head object is unavailable; retained the other native head')
            token = kind, item['url'], identity, json.dumps(source, sort_keys=True)
            if token not in self._head_seen:
                self._head_seen.add(token)
                record = {'item_url': item['url'], 'repo': item['repo'],
                          'number': item['number'], 'kind': kind, 'source': source}
                if kind == 'rest_force_push':
                    record['source_path'] = source_path
                github.setdefault('head_events', []).append(record)
            return True
        except ERRORS:
            self.problem(key, 'invalid native head event skipped; historical association coverage is incomplete')
            return False

    def _head_history_batch(self, github, items):
        """Read bounded candidate PR histories; totalCount includes other item types."""
        states = {item['url']: {'item': item, 'cursor': None, 'cursors': set(), 'ids': set(), 'done': False}
                  for item in items if item['url'] not in self._history_read}
        for item in items:
            self.coverage.setdefault('head_history:' + item['url'], {'status': 'ok', 'pages': 0, 'count': 0})
        for _ in range(MAX_LIST_PAGES):
            active = [state for state in states.values() if not state['done']]
            if not active:
                break
            fields = []
            for index, state in enumerate(active):
                item = state['item']
                owner, name = item['repo'].split('/')
                after = ',after:' + json.dumps(state['cursor']) if state['cursor'] else ''
                fields.append('p' + str(index) + ':repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name)
                    + '){pullRequest(number:' + str(item['number']) + '){url number repository{nameWithOwner} '
                    'timelineItems(itemTypes:[HEAD_REF_FORCE_PUSHED_EVENT],first:100' + after + '){totalCount '
                    'pageInfo{hasNextPage endCursor} nodes{__typename ...on HeadRefForcePushedEvent{id createdAt '
                    'beforeCommit{oid} afterCommit{oid} pullRequest{url}}}}}}')
            try:
                result = self.graphql('graphql', '-f', 'query=query{' + ''.join(fields) + '}')
                require(isinstance(result, dict) and isinstance(result.get('data'), dict), 'Missing native history result')
            except ERRORS:
                for state in active:
                    self.problem('head_history:' + state['item']['url'], 'history request failed; retained earlier events')
                    state['done'] = True
                continue
            for index, state in enumerate(active):
                item, alias = state['item'], 'p' + str(index)
                key = 'head_history:' + item['url']
                info = self.coverage[key]
                try:
                    errors = result.get('errors') or []
                    require(isinstance(errors, list), 'Malformed native history errors')
                    if any(not isinstance(error, dict) or not isinstance(error.get('path'), list)
                           or not error['path'] or error['path'][0] == alias for error in errors):
                        self.problem(key, 'history returned GraphQL errors; retaining readable events')
                    pr = result['data'][alias]['pullRequest']
                    require(pr['url'].casefold() == item['url'].casefold() and pr['number'] == item['number']
                            and pr['repository']['nameWithOwner'].casefold() == item['repo'].casefold(),
                            'Historical PR route mismatch')
                    connection = pr['timelineItems']
                    require(isinstance(connection, dict) and isinstance(connection.get('nodes'), list)
                            and len(connection['nodes']) <= PAGE_SIZE and integer(connection.get('totalCount')),
                            'Malformed native history connection')
                    info['pages'] += 1
                    # Native totalCount counts the broader timeline, not just the
                    # filtered force-push nodes. Cursor exhaustion is authoritative.
                    info['unfiltered_total_count'] = connection['totalCount']
                    for event in connection['nodes']:
                        if self._retain_head_event(github, item, event, 'graphql_force_push', key):
                            if event['id'] in state['ids']:
                                self.problem(key, 'a duplicate native history event was observed')
                            state['ids'].add(event['id'])
                    info['count'] = len(state['ids'])
                    page = connection['pageInfo']
                    require(type(page.get('hasNextPage')) is bool, 'Invalid native history pagination')
                    state['done'] = not page['hasNextPage']
                    if not state['done']:
                        cursor = page.get('endCursor')
                        require(isinstance(cursor, str) and cursor and cursor not in state['cursors'],
                                'Native history cursor did not advance')
                        state['cursors'].add(cursor)
                        state['cursor'] = cursor
                except ERRORS:
                    self.problem(key, 'history lookup incomplete; retained readable events')
                    state['done'] = True
        for url, state in states.items():
            if not state['done']:
                self.problem('head_history:' + url, 'native history reached its page safety cap')
            self._history_read.add(url)

    @staticmethod
    def _head_shas(record):
        source = record['source']
        candidates = ([source.get('commit_id')] if record['kind'] == 'rest_force_push' else
                      [(source.get(field) or {}).get('oid') for field in ('beforeCommit', 'afterCommit')])
        return {sha for sha in candidates if isinstance(sha, str) and HASH.fullmatch(sha)}

    def associate_head_events(self, branches, github):
        """Apply captured historical head identities globally, independently of local remotes."""
        by_sha = {}
        for branch in branches:
            if branch['object_type'] == 'commit':
                by_sha.setdefault(branch['hash'], []).append(branch)
        indexed = {item['url']: item for item in github.get('pr_index', [])}
        for record in github.get('head_events', []):
            matched = self._head_shas(record) & by_sha.keys()
            if not matched:
                continue
            item = indexed.get(record['item_url'])
            if item is None:
                key = 'head_history_metadata:' + record['item_url']
                try:
                    owner, name = record['repo'].split('/')
                    query = ('query{repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name)
                             + '){pullRequest(number:' + str(record['number']) + '){' + PR_FIELDS + '}}}')
                    result = self.graphql('graphql', '-f', 'query=' + query)
                    require(not result.get('errors'), 'Historical PR metadata failed')
                    item = graphql_item(result['data']['repository']['pullRequest'], self.host)
                    require(item['url'].casefold() == record['item_url'].casefold(), 'Historical metadata route mismatch')
                    github.setdefault('pr_index', []).append(item)
                    indexed[item['url']] = item
                    self.coverage[key] = {'status': 'ok', 'pages': 1, 'count': 1}
                except ERRORS:
                    self.problem(key, 'native PR metadata unavailable; historical positive remains contextual')
                    continue
            for sha in matched:
                for branch in by_sha[sha]:
                    if not any(pr['url'] == item['url'] for pr in branch.setdefault('prs', [])):
                        branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                            'state': item['state'], 'proof': 'historical_head_event', 'head_event_id': record['source']['id']})
                    branch['pr_status'] = 'matched'
                    if 'historical_head_events' not in branch.setdefault('association_methods', []):
                        branch['association_methods'].append('historical_head_events')

    def _expand_head_context(self, branches, github, candidates=()):
        """Expand actual local-name/REST-SHA leads once and reuse their history."""
        candidates = set(candidates)
        local_shas = {branch['hash'] for branch in branches if branch['object_type'] == 'commit'}
        candidates.update(record['item_url'] for record in github.get('head_events', [])
                          if self._head_shas(record) & local_shas)
        self.associate_head_events(branches, github)
        indexed = {item['url']: item for item in github.get('pr_index', [])}
        items = [indexed[url] for url in sorted(candidates) if url in indexed and url not in self._history_read]
        for offset in range(0, len(items), 15):
            self._head_history_batch(github, items[offset:offset + 15])
        self.associate_head_events(branches, github)

    def _inspect_activity(self, github, items):
        seen = self._activity_seen
        for item in items:
            if item['url'] in self._activity_items:
                continue
            self._activity_items.add(item['url'])
            if (item.get('author_login') or '').casefold() == self.login.casefold() and item.get('created_at'):
                try:
                    created = timestamp(item['created_at'])
                    if self.start <= created <= self.end:
                        github['activity'].append({'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
                                                   'actor': item['author_login'], 'action': 'opened', 'at': iso(created),
                                                   'url': item['url'], 'id': item['number'], 'body': ''})
                except ERRORS:
                    self.problem('activity:' + item['url'], 'item creation evidence is invalid')
            route = f"repos/{item['repo']}/issues/{item['number']}"
            endpoints = [('issue_comment', route + '/comments'), ('timeline', route + '/timeline')]
            if is_pr_url(item['url']):
                endpoints.extend([('review', f"repos/{item['repo']}/pulls/{item['number']}/reviews"),
                                  ('review_comment', f"repos/{item['repo']}/pulls/{item['number']}/comments")])
            for category, endpoint in endpoints:
                key = f"activity:{item['repo']}#{item['number']}:{category}"
                for event in self.listing(endpoint, key):
                    if category == 'timeline' and isinstance(event, dict) and event.get('event') == 'head_ref_force_pushed':
                        self._retain_head_event(github, item, event, 'rest_force_push', key, endpoint)
                    if category in {'issue_comment', 'review_comment'}:
                        self._retain_comment(github, item, event, category, key)
                    try:
                        actor = (event.get('actor') or event.get('user') or {}).get('login', '')
                        is_author = actor.casefold() == self.login.casefold()
                        if not is_author and category not in {'issue_comment', 'review_comment'}:
                            continue
                        if category == 'timeline' and event.get('event') in {'commented', 'reviewed', 'committed'}:
                            continue
                        verb = event.get('event', category)
                        dates = [('submitted_at' if category == 'review' else 'created_at', verb, actor, None)] if is_author else []
                        if category in {'issue_comment', 'review_comment'}:
                            edited = event.get('updated_at')
                            # updated_at can differ from lastEditedAt and can move
                            # after this window. Same-second edits are possible too.
                            if (edited and event.get('created_at') and timestamp(event['created_at']) <= self.end
                                    and timestamp(edited) >= self.start):
                                proof = self._comment_edit(event, category, key, github, item)
                                if proof is not None:
                                    dates.append(('updated_at', category + '_edited', proof['editor']['login'], proof))
                        for field, action, event_actor, editor_evidence in dates:
                            if event.get(field) is None:
                                continue
                            when = timestamp(editor_evidence['lastEditedAt'] if editor_evidence else event[field])
                            if not self.start <= when <= self.end:
                                continue
                            url = event.get('html_url') or item['url']
                            require(url == item['url'] or url.startswith(item['url'] + '#'), 'Invalid event URL')
                            require(isinstance(action, str) and action, 'Invalid actor action')
                            source = None
                            event_identity = event.get('id')
                            if category == 'timeline' and action == 'cross-referenced' and event_identity is None:
                                # REST cross-reference rows have no event ID or permalink.
                                # Keep their native source identity, cite the target timeline,
                                # and never manufacture a GitHub event ID.
                                source = cross_reference_source(event, self.host)
                                event_identity = ('source_issue', source['issue']['id'])
                            else:
                                require(integer(event_identity, 1), 'Missing actor event identity')
                            require(event.get('body') is None or isinstance(event['body'], str), 'Invalid event body')
                            identity = (item['url'], category, event_identity, action, iso(when))
                            if identity in seen:
                                continue
                            seen.add(identity)
                            github['activity'].append({'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
                                                       'actor': event_actor, 'action': action, 'at': iso(when), 'url': url,
                                                       'id': event.get('id'), 'body': event.get('body') or '',
                                                       **({'source': source} if source is not None else {}),
                                                       **({'body_source': 'rest_current_observation'}
                                                          if category in {'issue_comment', 'review_comment'} else {}),
                                                       **({'editor_evidence': editor_evidence} if editor_evidence else {}),
                                                       **({'review_state': event.get('state')} if category == 'review' else {})})
                    except (ValueError, TypeError, KeyError, AttributeError):
                        self.problem(key, 'an invalid actor event was skipped')

        if self._branch_snapshots is not None:
            self._expand_head_context(self._branch_snapshots, github)

    def authored_index(self, repo):
        """Cache all states, retaining valid pages when the index is incomplete."""
        key = 'pr_index:' + repo
        if key in self.cache:
            return self.cache[key]
        require(REPO.fullmatch(repo), 'Invalid PR index repository')
        info = self.coverage.setdefault(key, {'status': 'ok', 'pages': 0, 'count': 0})
        query = '''query($q:String!,$after:String){ search(query:$q,type:ISSUE,first:100,after:$after){
          issueCount pageInfo {hasNextPage endCursor} nodes {... on PullRequest {''' + PR_FIELDS + '''}}}}'''
        rows, seen, cursors, cursor, first_total = [], set(), set(), None, None
        for _ in range(LIMIT // PAGE_SIZE):
            try:
                args = ['graphql', '-f', 'query=' + query, '-f', f'q=is:pr repo:{repo} author:{self.login} sort:created-asc']
                if cursor:
                    args.extend(['-f', 'after=' + cursor])
                result = self.graphql(*args)
                require(isinstance(result, dict), 'Malformed GraphQL result')
                if result.get('errors'):
                    self.problem(key, 'GraphQL index returned errors; retaining readable results')
                search = result['data']['search']
                require(integer(search.get('issueCount')) and isinstance(search.get('nodes'), list)
                        and len(search['nodes']) <= PAGE_SIZE, 'Malformed PR index')
                total = search['issueCount']
                info['total_count'] = total
                info['pages'] += 1
                if first_total is not None and total != first_total:
                    self.problem(key, 'PR index count changed during paging')
                if first_total is None:
                    first_total = total
                if total > LIMIT:
                    self.problem(key, 'authored PR index exceeds 1,000 results; snapshot lookups remain separate')
                for row in search['nodes']:
                    try:
                        item = graphql_item(row, self.host)
                        require(item['url'] not in seen, 'Duplicate indexed PR')
                        seen.add(item['url'])
                        rows.append(item)
                    except ERRORS:
                        self.problem(key, 'invalid or duplicate indexed PR skipped')
                info['count'] = len(rows)
                page = search['pageInfo']
                require(type(page.get('hasNextPage')) is bool, 'Invalid PR index pagination')
                if not page['hasNextPage']:
                    if info['count'] != total:
                        self.problem(key, 'PR index count disagrees with results')
                    break
                require(isinstance(page.get('endCursor'), str) and page['endCursor']
                        and page['endCursor'] not in cursors, 'PR index did not advance')
                cursors.add(page['endCursor'])
                cursor = page['endCursor']
            except ERRORS:
                self.problem(key, 'authored PR index failed; retained earlier pages')
                break
        else:
            self.problem(key, 'PR index reached the search page cap')
        self.cache[key] = rows
        return rows

    def _snapshot_batch(self, tuples, global_search=False):
        """Read each alias independently; an error never erases another alias's evidence."""
        states = {}
        coverage_keys = {key: ('global_snapshot:' + key[1] if global_search else 'snapshot:' + ':'.join(key))
                         for key in tuples}
        for repo, sha in tuples:
            coverage_key = coverage_keys[(repo, sha)]
            self.coverage.setdefault(coverage_key, {'status': 'ok', 'pages': 0, 'count': 0})
            states[(repo, sha)] = {'matches': {}, 'observations': [], 'search': {'cursor': None, 'cursors': set(), 'seen': set(),
                                                          'total': None, 'pages': 0, 'done': False},
                                  'commit': {'cursor': None, 'cursors': set(), 'seen': set(),
                                             'pages': 0, 'done': global_search, 'failed': False}}
        # Both connections expose at most 100 rows per page. Bound pathological
        # servers while explicitly marking unconsumed cursors as incomplete.
        for _ in range(MAX_LIST_PAGES):
            fields, aliases = [], {}
            for index, key in enumerate(tuples):
                repo, sha = key
                for method, prefix in (('search', 's'), ('commit', 'c')):
                    state = states[key][method]
                    if state['done']:
                        continue
                    alias = prefix + str(index)
                    aliases[alias] = (key, method)
                    after = ',after:' + json.dumps(state['cursor']) if state['cursor'] else ''
                    if method == 'search':
                        fields.append(alias + ':search(query:' + json.dumps(('' if global_search else 'repo:' + states[key].get('search_repo', repo) + ' ') + 'is:pr ' + sha)
                                      + ',type:ISSUE,first:100' + after + '){issueCount pageInfo{hasNextPage endCursor}'
                                      ' nodes{...on PullRequest{' + PR_FIELDS + '}}}')
                    else:
                        owner, name = repo.split('/')
                        fields.append(alias + ':repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name)
                                      + '){id nameWithOwner object(expression:' + json.dumps(sha) + '){...on Commit{'
                                      'associatedPullRequests(first:100' + after + '){pageInfo{hasNextPage endCursor}'
                                      ' nodes{' + PR_FIELDS + '}}}}}')
            if not fields:
                break
            try:
                with self._snapshot_io:
                    result = self.graphql('graphql', '-f', 'query=query{' + ' '.join(fields) + '}')
                require(isinstance(result, dict) and isinstance(result.get('data'), dict), 'Missing batch result')
            except ERRORS:
                for alias, (key, method) in aliases.items():
                    self.problem(coverage_keys[key], method + ' request failed; absence is not established')
                    states[key][method]['done'] = True
                    states[key][method]['failed'] = True
                continue
            for alias, (key, method) in aliases.items():
                state = states[key][method]
                coverage_key = coverage_keys[key]
                info = self.coverage[coverage_key]
                try:
                    errors = result.get('errors') or []
                    require(isinstance(errors, list), 'Malformed GraphQL errors')
                    affected = any(not isinstance(error, dict) or not isinstance(error.get('path'), list)
                                   or not error['path'] or error['path'][0] == alias for error in errors)
                    if affected:
                        self.problem(coverage_key, method + ' returned GraphQL errors; retaining readable results')
                    connection = result['data'][alias]
                    context_only = bool(state.get('alias_unverified')) if method == 'commit' else False
                    if method == 'commit':
                        if (isinstance(connection, dict)
                                and isinstance(connection.get('nameWithOwner'), str)
                                and REPO.fullmatch(connection['nameWithOwner'])
                                and connection['nameWithOwner'].casefold() != key[0].casefold()):
                            canonical = connection['nameWithOwner']
                            try:
                                self._verify_repository_alias(key[0], canonical)
                            except ERRORS:
                                # The native connection still contains readable
                                # PRs. Preserve them without asserting that this
                                # local remote is the returned repository.
                                context_only = True
                                state['alias_unverified'] = True
                                state['failed'] = True
                                self.problem(coverage_key, 'repository alias unverified; associated PRs retained as context')
                            else:
                                context_only = False
                                state['alias_unverified'] = False
                            if not context_only and states[key].get('search_repo') != canonical:
                                # Search does not follow repository redirects. A
                                # valid zero for the historical name proves nothing
                                # about the canonical repository's PRs.
                                states[key]['search_repo'] = canonical
                                info['search_repo'] = canonical
                                states[key]['search'] = {'cursor': None, 'cursors': set(), 'seen': set(),
                                                         'total': None, 'pages': 0, 'done': False}
                        if (not affected and isinstance(connection, dict)
                                and isinstance(connection.get('id'), str) and connection['id']
                                and isinstance(connection.get('nameWithOwner'), str)
                                and REPO.fullmatch(connection['nameWithOwner'])
                                and 'object' in connection and connection['object'] is None):
                            # An accessible native repository explicitly has no object
                            # for this SHA. This is a completed negative lookup, not
                            # a transport/access failure or proof of unpublished work.
                            if connection['nameWithOwner'].casefold() != key[0].casefold():
                                self._verify_repository_alias(key[0], connection['nameWithOwner'])
                            info['commit_object'] = 'absent'
                            info['repository_node_id'] = connection['id']
                            info['pages'] += 1
                            state['pages'] += 1
                            state['done'] = True
                            continue
                        require(isinstance(connection, dict) and isinstance(connection.get('object'), dict),
                                'Commit object inaccessible')
                        info['commit_object'] = 'present'
                        connection = connection['object']['associatedPullRequests']
                    require(isinstance(connection, dict) and isinstance(connection.get('nodes'), list)
                            and len(connection['nodes']) <= PAGE_SIZE, 'Malformed snapshot connection')
                    state['pages'] += 1
                    info['pages'] += 1
                    for row in connection['nodes']:
                        try:
                            item = graphql_item(row, self.host)
                            require(item['url'] not in state['seen'], 'Duplicate snapshot PR')
                            state['seen'].add(item['url'])
                            if context_only:
                                if item not in states[key]['observations']:
                                    states[key]['observations'].append(item)
                                continue
                            proof = ('global_any_author_sha_search' if global_search else 'any_author_sha_search') if method == 'search' else 'commit_association'
                            previous = states[key]['matches'].setdefault(item['url'], (item, proof))[0]
                            if item != previous and item not in states[key]['observations']:
                                states[key]['observations'].append(item)
                        except ERRORS:
                            self.problem(coverage_key, method + ' contained an invalid or duplicate PR')
                    if method == 'search':
                        total = connection.get('issueCount')
                        require(integer(total), 'Invalid SHA search total')
                        if state['total'] is not None and state['total'] != total:
                            self.problem(coverage_key, 'SHA search count changed during paging')
                        state['total'] = total
                        if total > LIMIT:
                            self.problem(coverage_key, 'SHA search exceeds 1,000 results')
                    page = connection['pageInfo']
                    require(type(page.get('hasNextPage')) is bool, 'Invalid snapshot pagination')
                    state['done'] = not page['hasNextPage']
                    if state['done']:
                        if method == 'search' and len(state['seen']) != state['total']:
                            self.problem(coverage_key, 'SHA search count disagrees with results')
                        continue
                    require(isinstance(page.get('endCursor'), str) and page['endCursor']
                            and page['endCursor'] not in state['cursors'], 'Snapshot cursor did not advance')
                    state['cursors'].add(page['endCursor'])
                    state['cursor'] = page['endCursor']
                    if method == 'search' and state['pages'] >= LIMIT // PAGE_SIZE:
                        self.problem(coverage_key, 'SHA search reached its page cap')
                        state['done'] = True
                except ERRORS:
                    self.problem(coverage_key, method + ' lookup incomplete; absence is not established')
                    state['done'] = True
                    state['failed'] = True
        for key, state in states.items():
            coverage_key = coverage_keys[key]
            if any(not state[method]['done'] for method in ('search', 'commit')):
                self.problem(coverage_key, 'snapshot pagination reached its safety cap')
            self.coverage[coverage_key]['count'] = len(state['matches'])
        return states

    def _snapshot_batches(self, requests, global_search=False):
        """Overlap bounded native reads, applying results and warnings in source order."""
        batches = (requests[offset:offset + BATCH_SIZE] for offset in range(0, len(requests), BATCH_SIZE))

        def read(batch):
            warnings = []
            worker = Github(self.api, self.host, self.login, self.start, self.end, warnings.append)
            worker._snapshot_io = self._snapshot_io

            def alias(requested, canonical):
                # Native repository identity reads/cache are shared, unlike
                # coverage or warnings. Serialize this rare two-read proof.
                with self._alias_lock, self._snapshot_io:
                    return self._verify_repository_alias(requested, canonical)

            worker._verify_repository_alias = alias
            states = worker._snapshot_batch(batch, global_search=global_search)
            return states, worker.coverage, warnings

        with ThreadPoolExecutor(max_workers=SNAPSHOT_WORKERS) as pool:
            pending = deque()
            for _ in range(SNAPSHOT_WORKERS):
                batch = next(batches, None)
                if batch is None:
                    break
                pending.append((batch, pool.submit(read, batch)))
            while pending:
                batch, future = pending.popleft()
                # Ordinary native errors already preserve successful aliases
                # inside _snapshot_batch. Unexpected programming errors propagate.
                states, coverage, warnings = future.result()
                self.coverage.update(coverage)
                for warning in warnings:
                    self.warn(warning)
                following = next(batches, None)
                if following is not None:
                    pending.append((following, pool.submit(read, following)))
                yield batch, states

    def associate_branches(self, branches, repositories, github):
        """Match current native snapshots; no_match does not exclude historical PR use."""
        self._branch_snapshots = branches
        indices, pending = {}, {}
        by_path = {entry['path']: entry for entry in repositories}
        all_indexed, observations, seen_observations = {}, [], set()
        unrouted = []

        def retain_index(item):
            original = all_indexed.setdefault(item['url'], item)
            if item != original:
                fingerprint = json.dumps(item, sort_keys=True)
                if fingerprint not in seen_observations:
                    seen_observations.add(fingerprint)
                    observations.append(item)

        for branch in branches:
            if branch['object_type'] != 'commit':
                continue
            branch.setdefault('prs', [])
            remotes = {entry['repo'] for entry in by_path[branch['repo']].get('remotes', [])
                       if entry['kind'] == 'github' and entry.get('host', self.host) == self.host}
            branch['association_methods'], branch['association_lookups'], branch['related_prs'] = [], [], []
            if not remotes:
                branch['association_scope'] = 'github_global_without_repository'
                unrouted.append(branch)
                continue
            names = set()
            for ref in branch['refs']:
                if ref.startswith('refs/heads/'):
                    names.add(ref[len('refs/heads/'):])
                elif ref.startswith('refs/remotes/') and '/' in ref[len('refs/remotes/'):]:
                    names.add(ref[len('refs/remotes/'):].split('/', 1)[1])
            for remote in sorted(remotes):
                if remote not in indices:
                    indices[remote] = self.authored_index(remote)
                    for item in indices[remote]:
                        retain_index(item)
                for item in indices[remote]:
                    if branch['hash'] in (item['head_sha'], item['merge_sha']):
                        branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                              'state': item['state'], 'proof': 'exact_head_or_merge_sha'})
                    elif item['head_ref'] in names:
                        branch['related_prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                                      'state': item['state'], 'proof': 'head_name_only'})
                branch['association_methods'].append('authored_pr_index:' + remote)
                pending.setdefault((remote, branch['hash']), []).append(branch)
        # One index per remote, one lookup per distinct (remote,SHA), and bounded
        # GraphQL batches avoid a separate HTTP request for every local alias.
        requests = list(pending)
        for batch, states in self._snapshot_batches(requests):
            # An inaccessible old fork may have accessible PRs elsewhere. Global
            # SHA search supplies positive evidence while the remote access gap
            # remains partial; an empty global search never proves private absence.
            fallback = {sha: (repo, sha) for repo, sha in batch
                        if states[(repo, sha)]['commit'].get('failed') and sha not in self._global_snapshots}
            if fallback:
                recovered = self._snapshot_batch(list(fallback.values()), global_search=True)
                self._global_snapshots.update({key[1]: value for key, value in recovered.items()})
            for key in batch:
                if states[key]['commit'].get('failed'):
                    global_state = self._global_snapshots[key[1]]
                    for url, value in global_state['matches'].items():
                        previous = states[key]['matches'].setdefault(url, value)[0]
                        if value[0] != previous:
                            states[key]['observations'].append(value[0])
                    states[key]['observations'].extend(global_state['observations'])
                    self.coverage['snapshot:' + ':'.join(key)]['global_search'] = dict(
                        self.coverage['global_snapshot:' + key[1]])
            for key in batch:
                repo, sha = key
                info = self.coverage['snapshot:' + repo + ':' + sha]
                matches = states[key]['matches']
                for item, proof in matches.values():
                    retain_index(item)
                for item in states[key]['observations']:
                    retain_index(item)
                for branch in pending[key]:
                    branch['association_lookups'].append({'repo': repo, **info})
                    for item, proof in matches.values():
                        branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                              'state': item['state'], 'proof': proof})
                    branch['association_methods'].extend(['any_author_sha_search:' + repo, 'commit_association:' + repo])
                    if 'global_search' in info:
                        branch['association_methods'].append('global_any_author_sha_search')
        # Local backup clones and repositories without a GitHub remote can still
        # share a native Git object with an accessible PR. Query each distinct
        # SHA once globally, preserving the unresolved provider/repository scope.
        requests = [(None, sha) for sha in sorted({branch['hash'] for branch in unrouted})
                    if sha not in self._global_snapshots]
        for _, states in self._snapshot_batches(requests, global_search=True):
            self._global_snapshots.update({sha: state for (_, sha), state in states.items()})
        route_gaps = set()
        for branch in unrouted:
            sha = branch['hash']
            state, info = self._global_snapshots[sha], self.coverage['global_snapshot:' + sha]
            branch['association_methods'].append('global_any_author_sha_search')
            branch['association_lookups'].append({'scope': 'github_global', **info})
            for item, proof in state['matches'].values():
                retain_index(item)
                branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                      'state': item['state'], 'proof': proof})
            for item in state['observations']:
                retain_index(item)
            if branch['repo'] not in route_gaps:
                route_gaps.add(branch['repo'])
                self.problem('unrouted:' + branch['repo'],
                             'global GitHub SHA search was attempted; no configured GitHub repository or '
                             'other-provider reader establishes exhaustive PR history')

        # Redirects discovered while resolving snapshots also repair the all-state
        # authored index: GitHub search silently returns zero for historical names.
        for _, proof in sorted(self._repository_aliases.items()):
            if proof is None or proof['requested_repo'] not in indices:
                continue
            requested, canonical = proof['requested_repo'], proof['canonical_repo']
            if canonical not in indices:
                indices[canonical] = self.authored_index(canonical)
                for item in indices[canonical]:
                    retain_index(item)
            for branch in branches:
                if not any(method == 'authored_pr_index:' + requested
                           for method in branch.get('association_methods', [])):
                    continue
                branch['association_methods'].append('authored_pr_index:' + canonical)
                names = {ref.removeprefix('refs/heads/') for ref in branch['refs'] if ref.startswith('refs/heads/')}
                names.update(ref[len('refs/remotes/'):].split('/', 1)[1] for ref in branch['refs']
                             if ref.startswith('refs/remotes/') and '/' in ref[len('refs/remotes/'):])
                for item in indices[canonical]:
                    if branch['hash'] in (item['head_sha'], item['merge_sha']):
                        if any(pr['url'] == item['url'] for pr in branch['prs']):
                            # An earlier SHA-search observation already proves the
                            # relation; do not upgrade it using a later head value.
                            continue
                        branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                              'state': item['state'], 'proof': 'exact_head_or_merge_sha'})
                    elif item['head_ref'] in names:
                        branch['related_prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                                      'state': item['state'], 'proof': 'head_name_only'})
        # Every globally proven SHA relationship also applies to routed clones
        # of the same immutable object, even when their narrower lookups succeeded.
        for branch in branches:
            state = self._global_snapshots.get(branch['hash']) if branch['object_type'] == 'commit' else None
            if state is None:
                continue
            info = self.coverage['global_snapshot:' + branch['hash']]
            lookup = {'scope': 'github_global', **info}
            if lookup not in branch.setdefault('association_lookups', []):
                branch['association_lookups'].append(lookup)
            if 'global_any_author_sha_search' not in branch.setdefault('association_methods', []):
                branch['association_methods'].append('global_any_author_sha_search')
            for item, proof in state['matches'].values():
                retain_index(item)
                if not any(pr['url'] == item['url'] for pr in branch.setdefault('prs', [])):
                    branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                          'state': item['state'], 'proof': proof})
            for item in state['observations']:
                retain_index(item)
        for branch in branches:
            if 'association_lookups' in branch:
                if branch['prs']:
                    branch['pr_status'] = 'matched'
                elif (branch.get('association_scope') == 'github_global_without_repository'
                      or any(info['status'] != 'ok' for info in branch['association_lookups']) or branch['related_prs']):
                    branch['pr_status'] = 'partial'
                else:
                    branch['pr_status'] = 'no_match'
                branch['prs'] = list({item['url']: item for item in branch['prs']}.values())
                branch['related_prs'] = list({item['url']: item for item in branch['related_prs']}.values())
        github['pr_index'] = list(all_indexed.values())
        github['pr_index_observations'] = observations
        for _, proof in sorted(self._repository_aliases.items()):
            if proof is not None and proof not in github.setdefault('repository_aliases', []):
                github['repository_aliases'].append(proof)
        self._expand_head_context(branches, github, {
            pr['url'] for branch in branches for pr in branch.get('related_prs', [])})
