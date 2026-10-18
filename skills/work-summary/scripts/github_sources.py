"""Role-aware GitHub discovery, actor evidence, and batched ref-to-PR association."""

from collections import deque
from datetime import datetime, timedelta
import json
import subprocess
from urllib.parse import urlsplit

from extended_evidence import (same_github_item_url, validate_review_context)
from evidence import LIMIT, PAGE_SIZE, REPO, UTC, decode_json, integer, is_pr_url, iso, require, timestamp, valid_item


ERRORS = (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, AttributeError)
MAX_LIST_PAGES = 100
MAX_SEARCH_REQUESTS = 128


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
        self._review_seen = set()

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
        """Recover capped inventories with bounded native creation-time partitions.

        Keep the original query intact: an upper updated cutoff would hide old
        personal activity on items subsequently updated by somebody else.
        Creation timestamps are immutable, and native rows validate every slice.
        """
        info = self.coverage.setdefault(key, {'status': 'ok', 'pages': 0, 'count': 0,
                                             'query': query, 'incomplete_results': False})
        found, seen = [], set()
        requests = 0
        observed = datetime.now(UTC).replace(microsecond=0)
        epoch = datetime(1970, 1, 1, tzinfo=UTC)
        info['partition_observed_at'] = iso(observed)

        def read(search_query, lower=None, upper=None):
            nonlocal requests
            record = {'query': search_query, 'pages': 0, 'count': 0,
                      'status': 'ok', 'incomplete_results': False}
            if lower is not None:
                record.update(since_at=iso(lower), to_at=iso(upper))
                info.setdefault('created_partitions', []).append(record)
            local_seen, first_total, capped, in_bounds = set(), None, False, True
            for page in range(1, LIMIT // PAGE_SIZE + 1):
                if requests >= MAX_SEARCH_REQUESTS:
                    record['status'] = 'partial'
                    self.problem(key, 'creation-time recovery reached its request budget; retained successful pages')
                    return record, True, False
                requests += 1
                try:
                    response = self.api('--method', 'GET', 'search/issues', '-f', 'q=' + search_query,
                                        '-f', 'sort=updated', '-f', 'order=desc', '-F', 'per_page=100', '-F', f'page={page}')
                    require(isinstance(response, dict) and integer(response.get('total_count'))
                            and type(response.get('incomplete_results')) is bool and isinstance(response.get('items'), list)
                            and len(response['items']) <= PAGE_SIZE, 'Malformed GitHub search')
                    total = response['total_count']
                    record['pages'] += 1
                    info['pages'] += 1
                    record['total_count'] = total
                    if lower is None:
                        info['total_count'] = total
                    if first_total is not None and total != first_total:
                        record['status'] = 'partial'
                        self.problem(key, 'result count changed during collection')
                    first_total = total if first_total is None else first_total
                    record['incomplete_results'] |= response['incomplete_results']
                    info['incomplete_results'] |= response['incomplete_results']
                    capped |= response['incomplete_results'] or total > LIMIT
                    for row in response['items']:
                        try:
                            item = rest_item(row, kind, self.host)
                            identity = (self.host.casefold(), item['repo'].casefold(), item['number'])
                            require(identity not in local_seen, 'Duplicate item during paging')
                            local_seen.add(identity)
                            if lower is not None and not lower <= timestamp(item['created_at']) <= upper:
                                in_bounds = False
                                record['status'] = 'partial'
                            if identity not in seen:
                                seen.add(identity)
                                found.append(item)
                        except (ValueError, KeyError, TypeError, AttributeError):
                            record['status'] = 'partial'
                            self.problem(key, 'an invalid/duplicate item was skipped')
                    record['count'] = len(local_seen)
                    info['count'] = len(found)
                    # A capped child needs narrower queries, not another nine
                    # pages of the same truncated inventory.
                    if lower is not None and capped and in_bounds:
                        break
                    expected = min(PAGE_SIZE, max(0, min(total, LIMIT) - (page - 1) * PAGE_SIZE))
                    if len(response['items']) < expected:
                        if capped:
                            break
                        record['status'] = 'partial'
                        self.problem(key, 'server returned fewer rows than its reported count')
                        break
                    if page * PAGE_SIZE >= min(total, LIMIT):
                        if not capped and len(local_seen) != min(total, LIMIT):
                            record['status'] = 'partial'
                            self.problem(key, 'search count disagrees with retained rows')
                        break
                except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError):
                    record['status'] = 'partial'
                    self.problem(key, 'search failed; retained earlier pages')
                    break
            if not in_bounds:
                self.problem(key, 'native search returned rows outside its creation-time partition')
            return record, capped, in_bounds

        root, capped, _ = read(query)
        if not capped:
            return found
        pending = deque([(epoch, observed)])
        while pending and requests < MAX_SEARCH_REQUESTS:
            lower, upper = pending.popleft()
            if lower == upper:
                self.problem(key, 'search is incomplete or exceeds the 1,000-result limit within one creation second')
                continue
            middle = lower + timedelta(seconds=int((upper - lower).total_seconds()) // 2)
            for lo, hi in ((lower, middle), (middle + timedelta(seconds=1), upper)):
                partition = query + ' created:' + iso(lo) + '..' + iso(hi)
                record, overflow, in_bounds = read(partition, lo, hi)
                if overflow and in_bounds:
                    record['partitioned'] = True
                    pending.append((lo, hi))
                elif overflow:
                    self.problem(key, 'search is incomplete or exceeds the 1,000-result limit; partition could not be verified')
        if pending:
            self.problem(key, 'creation-time recovery reached its request budget; retained successful pages')
        if len(found) != root.get('total_count', 0):
            self.problem(key, 'partitioned search count disagrees with its original inventory')
        return found

    def own(self, item):
        return (item.get('author_login') or '').casefold() == self.login.casefold()

    def touched(self, item):
        try:
            return any(item.get(field) and self.start <= timestamp(item[field]) <= self.end
                       for field in ('created_at', 'updated_at', 'merged_at', 'closed_at'))
        except ERRORS:
            return False

    def discover(self, github):
        """Search the user's PRs, reviews, comments and open work, then read their activity.

        Items the user authored and touched in the window are primary work; the rest is
        context. Search dates only select candidates: native event times decide credit.
        """
        github.update(prs=[], issues=[], context_prs=[], context_issues=[], activity=[])
        since = iso(self.start.replace(microsecond=0))
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
        items = {}
        for key, kind, query in specifications:
            for item in self.search(query, key, kind):
                original = items.setdefault(item['url'].casefold(), {**item, 'roles': []})
                if key not in original['roles']:
                    original['roles'].append(key)
        for item in items.values():
            kind = 'prs' if is_pr_url(item['url']) else 'issues'
            github[kind if self.own(item) and self.touched(item) else 'context_' + kind].append(item)
        # PRs found only through involvement, assignment or review requests had no creditable
        # events in a real quarter (0 of 276); their activity reads cost four calls each.
        worked = {'open_authored_prs', 'recent_authored_prs', 'recent_reviews', 'recent_comments'}
        self._inspect_activity(github, [item for item in items.values()
                                        if not is_pr_url(item['url']) or worked & set(item['roles'])])
        return items

    def _retain_comment(self, github, item, event, category, key):
        """Retain native discussion references independently of actor attribution."""
        try:
            require(integer(event.get('id'), 1), 'Missing comment identity')
            url, body = event.get('html_url'), event.get('body')
            fragment = 'issuecomment-' if category == 'issue_comment' else 'discussion_r'
            require(same_github_item_url(url, item['url'] + '#' + fragment + str(event['id'])),
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

    def _retain_review(self, github, item, source, key):
        """Preserve all readable review bodies before any personal-actor filtering."""
        try:
            validate_review_context(source, item, self.host)
            identity = item['url'], json.dumps(source, sort_keys=True)
            if identity not in self._review_seen:
                self._review_seen.add(identity)
                github.setdefault('reference_reviews', []).append({
                    'item_url': item['url'], 'repo': item['repo'], 'number': item['number'], 'source': source})
        except ERRORS:
            self.problem(key, 'invalid native review context skipped; discussion coverage is incomplete')

    def _inspect_activity(self, github, items):
        seen = self._activity_seen
        for item in items:
            if item['url'].casefold() in self._activity_items:
                continue
            self._activity_items.add(item['url'].casefold())
            try:
                # New comments, reviews and timeline events all advance updated_at, so an
                # item last updated before the window has no in-window activity to read.
                inactive = timestamp(item['updated_at']) < self.start
            except ERRORS:
                inactive = False
            if inactive:
                self.coverage.setdefault('activity_skipped_inactive', {'status': 'ok', 'pages': 0, 'count': 0})['count'] += 1
                continue
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
                    elif category == 'review' or category == 'timeline' and event.get('event') == 'reviewed':
                        self._retain_review(github, item, event, key)
                    try:
                        actor = (event.get('actor') or event.get('user') or {}).get('login', '')
                        is_author = actor.casefold() == self.login.casefold()
                        if not is_author and category not in {'issue_comment', 'review_comment'}:
                            continue
                        if category == 'timeline' and event.get('event') in {'commented', 'reviewed', 'committed'}:
                            continue
                        verb = event.get('event', category)
                        # Comment edits are not credited: who edited, and when, needs a revision
                        # history read per comment, and an edit is rarely work worth reporting.
                        dates = [('submitted_at' if category == 'review' else 'created_at', verb, actor)] if is_author else []
                        for field, action, event_actor in dates:
                            if event.get(field) is None:
                                continue
                            when = timestamp(event[field])
                            if not self.start <= when <= self.end:
                                continue
                            url = event.get('html_url') or item['url']
                            require(isinstance(url, str) and same_github_item_url(url.partition('#')[0], item['url']),
                                    'Invalid event URL')
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
                                                       **({'review_state': event.get('state')} if category == 'review' else {})})
                    except (ValueError, TypeError, KeyError, AttributeError):
                        self.problem(key, 'an invalid actor event was skipped')


