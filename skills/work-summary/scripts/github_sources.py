"""Role-aware GitHub discovery, actor evidence, and batched ref-to-PR association."""

from datetime import datetime
import json
import subprocess
from urllib.parse import urlsplit

from evidence import HASH, LIMIT, PAGE_SIZE, REPO, UTC, decode_json, integer, is_pr_url, iso, require, timestamp, valid_item


ERRORS = (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, AttributeError)
MAX_LIST_PAGES = 100
BATCH_SIZE = 20


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
        self._repository_metadata = {}
        self._repository_aliases = {}

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

    def _comment_edit(self, event, category, key):
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
            require(isinstance(node, dict) and node.get('id') == node_id
                    and integer(node.get('databaseId'), 1) and node['databaseId'] == event.get('id')
                    and node.get('url') == event.get('html_url')
                    and 'author' in node and author_bound
                    and timestamp(node['createdAt']) == timestamp(event['created_at'])
                    and timestamp(node['updatedAt']) == timestamp(event['updated_at']),
                    'Comment edit identity or observation changed')
            require('lastEditedAt' in node and 'editor' in node, 'Missing native edit observation')
            edited, editor = node['lastEditedAt'], node['editor']
            if edited is None:
                require(editor is None, 'Comment edit metadata disagrees')
                return None
            when = timestamp(edited)
            require(timestamp(node['createdAt']) <= when <= timestamp(node['updatedAt']), 'Invalid last edit time')
            if not self.start <= when <= self.end:
                return None
            require(isinstance(editor, dict) and isinstance(editor.get('login'), str)
                    and editor['login'], 'Comment edit has no native editor')
            return node if editor['login'].casefold() == self.login.casefold() else None
        except ERRORS:
            self.problem(key, 'comment edit has no verified native editor/time; edit not credited')
            return None

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
                                proof = self._comment_edit(event, category, key)
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
                                                       **({'editor_evidence': editor_evidence} if editor_evidence else {}),
                                                       **({'review_state': event.get('state')} if category == 'review' else {})})
                    except (ValueError, TypeError, KeyError, AttributeError):
                        self.problem(key, 'an invalid actor event was skipped')

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
                owner, name = repo.split('/')
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
                        fields.append(alias + ':repository(owner:' + json.dumps(owner) + ',name:' + json.dumps(name)
                                      + '){id nameWithOwner object(expression:' + json.dumps(sha) + '){...on Commit{'
                                      'associatedPullRequests(first:100' + after + '){pageInfo{hasNextPage endCursor}'
                                      ' nodes{' + PR_FIELDS + '}}}}}')
            if not fields:
                break
            try:
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
                    if method == 'commit':
                        if (not affected and isinstance(connection, dict)
                                and isinstance(connection.get('id'), str) and connection['id']
                                and isinstance(connection.get('nameWithOwner'), str)
                                and REPO.fullmatch(connection['nameWithOwner'])
                                and connection['nameWithOwner'].casefold() != key[0].casefold()):
                            canonical = connection['nameWithOwner']
                            self._verify_repository_alias(key[0], canonical)
                            if states[key].get('search_repo') != canonical:
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

    def associate_branches(self, branches, repositories, github):
        """Match current native snapshots; no_match does not exclude historical PR use."""
        indices, pending = {}, {}
        by_path = {entry['path']: entry for entry in repositories}
        all_indexed, observations, seen_observations = {}, [], set()

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
            if not remotes:
                branch['pr_status'] = 'unsupported'
                continue
            branch['association_methods'], branch['association_lookups'], branch['related_prs'] = [], [], []
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
                exact = False
                for item in indices[remote]:
                    if branch['hash'] in (item['head_sha'], item['merge_sha']):
                        branch['prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                              'state': item['state'], 'proof': 'exact_head_or_merge_sha'})
                        exact = True
                    elif item['head_ref'] in names:
                        branch['related_prs'].append({'repo': item['repo'], 'number': item['number'], 'url': item['url'],
                                                      'state': item['state'], 'proof': 'head_name_only'})
                branch['association_methods'].append('authored_pr_index:' + remote)
                if not exact:
                    pending.setdefault((remote, branch['hash']), []).append(branch)
        # One index per remote, one lookup per distinct (remote,SHA), and bounded
        # GraphQL batches avoid a separate HTTP request for every local alias.
        requests = list(pending)
        for offset in range(0, len(requests), BATCH_SIZE):
            batch = requests[offset:offset + BATCH_SIZE]
            states = self._snapshot_batch(batch)
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
        # Redirects discovered while resolving snapshots also repair the all-state
        # authored index: GitHub search silently returns zero for historical names.
        for proof in list(self._repository_aliases.values()):
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
        for branch in branches:
            if 'association_lookups' in branch:
                if branch['prs']:
                    branch['pr_status'] = 'matched'
                elif any(info['status'] != 'ok' for info in branch['association_lookups']) or branch['related_prs']:
                    branch['pr_status'] = 'partial'
                else:
                    branch['pr_status'] = 'no_match'
                branch['prs'] = list({item['url']: item for item in branch['prs']}.values())
                branch['related_prs'] = list({item['url']: item for item in branch['related_prs']}.values())
        github['pr_index'] = list(all_indexed.values())
        github['pr_index_observations'] = observations
        for proof in self._repository_aliases.values():
            if proof is not None and proof not in github.setdefault('repository_aliases', []):
                github['repository_aliases'].append(proof)
