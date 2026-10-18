#!/usr/bin/env python3
"""Offline adversarial tests for roles, actor dates, and ref associations."""
import copy
import os
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from github_sources import Github, rest_item  # noqa: E402

START = datetime(2026, 10, 9, tzinfo=timezone.utc)
END = datetime(2026, 10, 16, tzinfo=timezone.utc)
SHA = 'a' * 40


def rest(number=1, repo='org/repo'):
    return {'html_url': f'https://github.com/{repo}/pull/{number}', 'number': number,
            'title': 'Work', 'state': 'open', 'created_at': '2026-01-01T00:00:00Z',
            'updated_at': '2026-10-16T03:00:00Z', 'closed_at': None,
            'pull_request': {'merged_at': None}, 'user': {'login': 'other'}}


def gql(number=1, repo='org/repo', sha=SHA, state='OPEN', head='feature'):
    closed = None if state == 'OPEN' else '2026-10-10T00:00:00Z'
    return {'url': f'https://github.com/{repo}/pull/{number}', 'number': number, 'title': 'Work',
            'state': state, 'createdAt': '2026-01-01T00:00:00Z', 'updatedAt': '2026-10-16T03:00:00Z',
            'closedAt': closed, 'mergedAt': closed if state == 'MERGED' else None, 'isDraft': False,
            'headRefOid': sha, 'headRefName': head, 'repository': {'nameWithOwner': repo},
            'headRepository': None, 'mergeCommit': None, 'baseRefName': 'main'}


def connection(rows=(), total=None, more=False, cursor=None):
    return {'nodes': list(rows), 'issueCount': len(rows) if total is None else total,
            'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}


def query_text(args):
    return next(x[6:] for x in args if x.startswith('query='))


def native_comment_response(args, node, event):
    """Native one-revision fixture; creation is explicitly absent from this history."""
    native = copy.deepcopy(node)
    if 'userContentEdits' in query_text(args):
        edited = native['lastEditedAt']
        rows = [] if edited is None else [{'id': 'revision-' + native['id'], 'editedAt': edited,
                 'createdAt': edited, 'updatedAt': edited, 'deletedAt': None, 'editor': native['editor']}]
        native.update(body=event.get('body') or '', includesCreatedEdit=False,
                      userContentEdits={'nodes': rows, 'totalCount': len(rows),
                                        'pageInfo': {'hasNextPage': False, 'endCursor': None}})
    return {'data': {'node': native}}


def snapshot_data(args, change=None):
    data = {}
    for index, query in re.findall(r's(\d+):search\(query:"([^"]+)"', query_text(args)):
        repo = query.split()[0][5:]
        data['s' + index] = connection()
        data['c' + index] = {'object': {'associatedPullRequests': connection()}}
        if change:
            change(index, repo, data)
    return {'data': data}


def branch(path='/local/repo', sha=SHA, refs=None):
    return {'repo': path, 'hash': sha, 'refs': refs or ['refs/heads/backup/feature'],
            'object_type': 'commit', 'prs': [], 'pr_status': 'unchecked', 'backup': True}


def repository(path='/local/repo', remotes=('org/repo',)):
    return {'path': path, 'remotes': [{'kind': 'github', 'host': 'github.com', 'repo': r,
                                     'name': 'remote' + str(i)} for i, r in enumerate(remotes)]}


class GithubSourcesTest(unittest.TestCase):
    def client(self, handler):
        calls, warnings = [], []
        def api(*args):
            calls.append(args)
            return handler(args)
        client = Github(api, 'github.com', 'me', START, END, warnings.append)
        client.calls, client.warnings = calls, warnings
        return client

        # Native repository capitalization can differ between observations;
        # retain its exact URL while binding the same host/repository/number.

    def test_native_api_callable_collects_without_any_cli_or_shell(self):
        native = rest()
        native['body'] = 'Native API body retained without a local CLI.'
        def api(args):
            if args[0] == 'graphql':
                return {'data': {'node': {'id': 'native42', 'databaseId': 42,
                    'url': native['html_url'] + '#issuecomment-42', 'author': {'login': 'me'},
                    'editor': None, 'lastEditedAt': None, 'createdAt': '2026-10-12T00:00:00Z',
                    'updatedAt': '2026-10-12T00:00:00Z'}}}
            if 'search/issues' in args:
                query = next(value for value in args if value.startswith('q='))
                rows = [native] if 'is:pr' in query else []
                return {'total_count': len(rows), 'incomplete_results': False, 'items': rows}
            if args[2] == 'repos/org/repo/issues/1/comments':
                return [{'id': 42, 'node_id': 'native42', 'user': {'login': 'me'}, 'body': 'Native actor evidence',
                         'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z',
                         'html_url': native['html_url'] + '#issuecomment-42'}]
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        with patch.dict(os.environ, {'PATH': ''}), patch('subprocess.run', side_effect=AssertionError('No shell transport')):
            client.discover(data)
        self.assertEqual(data['context_prs'][0]['body'], native['body'])
        self.assertEqual([(event['id'], event['body']) for event in data['activity']], [(42, 'Native actor evidence')])
        self.assertTrue(all(info['status'] == 'ok' for info in client.coverage.values()))
        self.assertEqual(client.warnings, [])

    def test_forbidden_later_actor_page_preserves_verified_events_and_other_endpoints(self):
        item = rest_item(rest(), 'prs', 'github.com')
        def api(args):
            if args[2].endswith('/issues/1/comments'):
                if 'page=2' in args:
                    raise subprocess.CalledProcessError(1, 'native-api', stderr='HTTP 403 rate limit')
                return [{'id': number, 'user': {'login': 'me'}, 'body': f'Comment {number}',
                         'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z',
                         'html_url': item['url'] + f'#issuecomment-{number}'} for number in range(1, 101)]
            if args[2].endswith('/reviews'):
                return [{'id': 200, 'user': {'login': 'me'}, 'body': 'Verified review', 'state': 'APPROVED',
                         'submitted_at': '2026-10-13T00:00:00Z', 'html_url': item['url'] + '#pullrequestreview-200'}]
            return []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual(len(data['activity']), 101)
        self.assertEqual(data['activity'][0]['body'], 'Comment 1')
        self.assertEqual(data['activity'][-1]['review_state'], 'APPROVED')
        self.assertEqual(client.coverage['activity:org/repo#1:issue_comment']['status'], 'partial')
        self.assertEqual(client.coverage['activity:org/repo#1:review']['status'], 'ok')

    def test_search_preserves_successful_pages_when_next_request_fails(self):
        def api(args):
            if 'page=2' in args:
                raise subprocess.CalledProcessError(1, 'gh')
            return {'total_count': 101, 'incomplete_results': False, 'items': [rest(i) for i in range(1, 101)]}
        client = self.client(api)
        self.assertEqual(len(client.search('query', 'roles', 'prs')), 100)
        self.assertEqual(client.coverage['roles']['status'], 'partial')

    def test_search_drift_incomplete_and_invalid_rows_retain_valid_evidence(self):
        def api(args):
            if 'page=1' in args:
                rows = [rest(i) for i in range(1, 101)]
                rows[1] = rest(1)
                return {'total_count': 101, 'incomplete_results': True, 'items': rows}
            return {'total_count': 102, 'incomplete_results': False, 'items': [rest(101), rest(102)]}
        client = self.client(api)
        found = client.search('query', 'roles', 'prs')
        self.assertEqual(len(found), 101)
        self.assertTrue(client.coverage['roles']['incomplete_results'])
        self.assertEqual(client.coverage['roles']['status'], 'partial')
        self.assertTrue(any('changed' in x for x in client.warnings))

    def test_search_cap_is_explicit_and_bounded(self):
        def api(args):
            page = int(next(x[5:] for x in args if x.startswith('page=')))
            return {'total_count': 1001, 'incomplete_results': False,
                    'items': [rest(i) for i in range((page - 1) * 100 + 1, page * 100 + 1)]}
        client = self.client(api)
        self.assertEqual(len(client.search('query', 'roles', 'prs')), 1000)
        self.assertLessEqual(len(client.calls), 128)
        self.assertEqual(client.coverage['roles']['status'], 'partial')
        self.assertTrue(any('1,000-result limit' in warning for warning in client.warnings))

    def test_search_oversized_page_is_not_marked_complete(self):
        client = self.client(lambda args: {'total_count': 101, 'incomplete_results': False,
                                          'items': [rest(i) for i in range(101)]})
        self.assertEqual(client.search('query', 'roles', 'prs'), [])
        self.assertEqual(client.coverage['roles']['status'], 'partial')

    def test_listing_detects_repeated_page_and_preserves_first(self):
        client = self.client(lambda args: [{'id': i} for i in range(100)])
        self.assertEqual(len(client.listing('endpoint', 'events')), 100)
        self.assertEqual(client.coverage['events']['status'], 'partial')

    def test_listing_reads_next_page(self):
        client = self.client(lambda args: [{'id': i} for i in range(100)] if 'page=1' in args else [{'id': 101}])
        self.assertEqual(len(client.listing('endpoint', 'events')), 101)
        self.assertEqual(client.coverage['events']['status'], 'ok')

    def test_discovery_uses_lower_bound_and_exact_actor_dates_not_item_updated_date(self):
        def api(args):
            if 'search/issues' in args:
                query = next(x[2:] for x in args if x.startswith('q='))
                self.assertNotIn('..', query)
                if 'updated:' in query:
                    self.assertIn('updated:>=2026-10-09T00:00:00Z', query)
                return {'total_count': 1 if 'is:pr' in query else 0, 'incomplete_results': False,
                        'items': [rest()] if 'is:pr' in query else []}
            endpoint = args[2]
            if endpoint.endswith('/reviews'):
                return [{'id': 1, 'user': {'login': 'Me'}, 'submitted_at': '2026-10-09T00:00:00Z',
                         'state': 'APPROVED', 'html_url': 'https://github.com/org/repo/pull/1#pullrequestreview-1'},
                        {'id': 2, 'user': {'login': 'other'}, 'submitted_at': '2026-10-12T00:00:00Z'},
                        {'id': 3, 'user': {'login': 'me'}, 'submitted_at': '2026-10-16T00:00:01Z'}]
            if endpoint.endswith('/issues/1/comments'):
                return [{'id': 4, 'user': {'login': 'me'}, 'created_at': '2026-10-16T00:00:00Z',
                         'updated_at': '2026-10-16T00:00:00Z', 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-4'}]
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        client.discover(data)
        self.assertEqual(len(data['context_prs']), 1)
        self.assertGreater(len(data['context_prs'][0]['roles']), 1)
        self.assertEqual(len(data['activity']), 2)
        self.assertEqual(next(x for x in data['activity'] if x['action'] == 'review')['review_state'], 'APPROVED')
        self.assertEqual({x['at'] for x in data['activity']}, {'2026-10-09T00:00:00Z', '2026-10-16T00:00:00Z'})

    def test_repo_named_pull_does_not_turn_issue_into_pr_actor_reads(self):
        for kind, route, expected_reads in [('issues', 'issues', 2), ('prs', 'pull', 4)]:
            with self.subTest(kind=kind):
                native = rest(repo='org/pull')
                if kind == 'issues':
                    native.pop('pull_request')
                native['html_url'] = f'https://github.com/org/pull/{route}/1'
                item = rest_item(native, kind, 'github.com')
                def api(args):
                    if args[0] == 'graphql':
                        return {'data': {'node': {'id': 'native1', 'databaseId': 1,
                            'url': item['url'] + '#issuecomment-1', 'author': {'login': 'me'},
                            'editor': None, 'lastEditedAt': None, 'createdAt': '2026-10-12T00:00:00Z',
                            'updatedAt': '2026-10-12T00:00:00Z'}}}
                    if 'search/issues' in args:
                        return {'total_count': 0, 'incomplete_results': False, 'items': []}
                    if kind == 'issues':
                        self.assertNotIn('repos/org/pull/pulls/', args[2])
                    if args[2] == 'repos/org/pull/issues/1/comments':
                        return [{'id': 1, 'node_id': 'native1', 'user': {'login': 'me'}, 'body': 'Native issue comment',
                                 'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z',
                                 'html_url': item['url'] + '#issuecomment-1'}]
                    return []
                client = self.client(api)
                data = {'activity': []}
                client._inspect_activity(data, [item])
                self.assertEqual(len([args for args in client.calls if 'search/issues' not in args and args[0] != 'graphql']), expected_reads)
                self.assertEqual(len(data['activity']), 1)
                self.assertEqual(data['activity'][0]['url'], item['url'] + '#issuecomment-1')
                self.assertEqual(client.warnings, [])

    def test_native_unedited_comment_does_not_confuse_updated_time_with_edit(self):
        event = {'id': 51, 'node_id': 'native-comment', 'user': {'login': 'me'}, 'editor': {'login': 'me'},
                 'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:00:01Z',
                 'html_url': 'https://github.com/org/repo/pull/1#discussion_r51'}
        native = {'id': event['node_id'], 'databaseId': 51, 'url': event['html_url'],
                  'author': {'login': 'me'}, 'editor': None, 'createdAt': event['created_at'],
                  'updatedAt': event['updated_at'], 'lastEditedAt': None}
        def api(args):
            if args[0] == 'graphql':
                return {'data': {'node': native}}
            return [event] if args[2] == 'repos/org/repo/pulls/1/comments' else []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [{'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}])
        self.assertEqual([x['action'] for x in data['activity']], ['review_comment'])
        self.assertEqual(client.warnings, [])

    def test_all_native_comment_references_survive_without_becoming_user_activity(self):
        item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}
        comments = [{'id': 30, 'node_id': 'native-comment', 'html_url': item['url'] + '#issuecomment-30',
                     'body': 'Native TEST-88 https://jira.example.test/browse/TEST-88',
                     'user': {'login': 'other'}, 'created_at': '2020-01-01T00:00:00Z',
                     'updated_at': '2020-01-01T00:00:00Z'}]
        def api(args):
            if args[2] == 'repos/org/repo/issues/1/comments':
                return comments + copy.deepcopy(comments)
            if args[2] == 'repos/org/repo/pulls/1/comments':
                return [{**comments[0], 'id': 31, 'body': 'Another native TEST-99 reference',
                         'html_url': item['url'] + '#discussion_r31', 'user': None, 'node_id': None}]
            return []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual(data['activity'], [])
        self.assertEqual(len(data['reference_comments']), 2)
        first, second = data['reference_comments']
        self.assertEqual(first['body'], comments[0]['body'])
        self.assertEqual(first['author_login'], 'other')
        self.assertEqual(first['item_url'], item['url'])
        self.assertEqual(first['kind'], 'issue_comment')
        self.assertEqual(second['kind'], 'review_comment')
        self.assertIsNone(second['author_login'])
        self.assertEqual(client.warnings, [])

    def test_invalid_comment_reference_does_not_erase_valid_sibling(self):
        item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}
        comment = {'id': 30, 'html_url': item['url'] + '#issuecomment-30', 'body': 'Native TEST-88',
                   'user': {'login': 'other'}, 'created_at': '2020-01-01T00:00:00Z', 'updated_at': '2020-01-01T00:00:00Z'}
        for change in ({'id': None}, {'body': {}}, {'html_url': item['url'] + '2#issuecomment-30'},
                       {'created_at': 'invalid'}, {'updated_at': '2019-01-01T00:00:00Z'}):
            with self.subTest(change=change):
                def api(args):
                    return [{**comment, **change}, comment] if args[2] == 'repos/org/repo/issues/1/comments' else []
                client = self.client(api)
                data = {'activity': []}
                client._inspect_activity(data, [item])
                self.assertEqual(len(data['reference_comments']), 1)
                self.assertEqual(client.coverage['activity:org/repo#1:issue_comment']['status'], 'partial')

    def test_items_untouched_since_before_window_need_no_activity_reads(self):
        stale = {'repo': 'org/repo', 'number': 2, 'url': 'https://github.com/org/repo/issues/2',
                 'updated_at': '2025-01-01T00:00:00Z'}
        client = self.client(lambda args: [])
        data = {'activity': []}
        client._inspect_activity(data, [stale])
        self.assertEqual(client.calls, [])
        self.assertEqual(client.coverage['activity_skipped_inactive']['count'], 1)

    def test_native_context_author_node_is_retained_and_malformed_identity_rejected(self):
        item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}
        event = {'id': 30, 'node_id': 'comment-id', 'html_url': item['url'] + '#issuecomment-30',
                 'body': 'Native TEST-88', 'user': {'login': 'helper[bot]', 'node_id': 'bot-id'},
                 'created_at': '2020-01-01T00:00:00Z', 'updated_at': '2020-01-01T00:00:00Z'}
        client = self.client(lambda args: [])
        data = {}
        client._retain_comment(data, item, event, 'issue_comment', 'context')
        self.assertEqual(data['reference_comments'][0]['author_node_id'], 'bot-id')
        self.assertEqual(data['reference_comments'][0]['author_login'], 'helper[bot]')
        event['id'] = 31
        event['html_url'] = item['url'] + '#issuecomment-31'
        event['user']['node_id'] = 42
        client._retain_comment(data, item, event, 'issue_comment', 'context')
        self.assertEqual(len(data['reference_comments']), 1)
        self.assertEqual(client.coverage['context']['status'], 'partial')

    def test_rest_editor_cannot_credit_a_native_edit_outside_window(self):
        event = {'id': 30, 'node_id': 'comment-id', 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-30',
                 'user': {'login': 'me'}, 'editor': {'login': 'me'},
                 'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z'}
        node = {'id': 'comment-id', 'databaseId': 30, 'url': event['html_url'],
                'author': {'login': 'me'}, 'editor': {'login': 'me'},
                'createdAt': event['created_at'], 'updatedAt': event['updated_at'], 'lastEditedAt': '2026-10-02T00:00:00Z'}
        def api(args):
            if args[0] == 'graphql':
                return {'data': {'node': node}}
            return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [{'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}])
        self.assertEqual(data['activity'], [])
        self.assertEqual(client.warnings, [])

    def test_native_cross_reference_without_event_id_preserves_source_and_distinct_events(self):
        first = {'actor': {'login': 'Me'}, 'event': 'cross-referenced',
                 'created_at': '2026-10-12T23:32:33Z', 'updated_at': '2026-10-12T23:32:33Z',
                 'source': {'type': 'issue', 'issue': {'id': 123, 'number': 2,
                            'html_url': 'https://github.com/other/source/pull/2'}}}
        second = copy.deepcopy(first)
        second['source']['issue'].update(id=124, number=3, html_url='https://github.com/other/source/issues/3')
        old = copy.deepcopy(first)
        old['created_at'] = '2026-10-01T00:00:00Z'
        foreign = copy.deepcopy(first)
        foreign['actor']['login'] = 'other'
        client = self.client(lambda args: [first, second, copy.deepcopy(first), old, foreign]
                             if args[2].endswith('/timeline') else [])
        data = {'activity': []}
        client._inspect_activity(data, [rest_item(rest(), 'prs', 'github.com')])
        self.assertEqual(len(data['activity']), 2)
        self.assertEqual([event['source'] for event in data['activity']], [first['source'], second['source']])
        self.assertTrue(all(event['id'] is None and event['url'] == 'https://github.com/org/repo/pull/1'
                            and event['action'] == 'cross-referenced' for event in data['activity']))
        self.assertEqual(client.warnings, [])

    def test_idless_cross_reference_requires_native_source_issue_identity(self):
        native = {'actor': {'login': 'me'}, 'event': 'cross-referenced', 'created_at': '2026-10-12T00:00:00Z',
                  'source': {'type': 'issue', 'issue': {'id': 123, 'number': 2,
                             'html_url': 'https://github.com/other/source/pull/2'}}}
        invalid = [dict(native, event='closed'), dict(native, source=None)]
        for field, value in [('id', None), ('id', True), ('number', 3),
                             ('html_url', 'https://other.test/other/source/pull/2'),
                             ('html_url', 'https://github.com/other/source/pull/2#comment'),
                             ('html_url', 'https://github.com/other/source/pull/2?x=y')]:
            row = copy.deepcopy(native)
            row['source']['issue'][field] = value
            invalid.append(row)
        for row in invalid:
            with self.subTest(row=row):
                client = self.client(lambda args: [row] if args[2].endswith('/timeline') else [])
                data = {'activity': []}
                client._inspect_activity(data, [rest_item(rest(), 'prs', 'github.com')])
                self.assertEqual(data['activity'], [])
                self.assertEqual(client.coverage['activity:org/repo#1:timeline']['status'], 'partial')

    def test_role_only_item_does_not_become_personal_activity(self):
        def api(args):
            if 'search/issues' in args:
                q = next(x for x in args if x.startswith('q='))
                rows = [rest()] if 'review-requested:' in q else []
                return {'total_count': len(rows), 'incomplete_results': False, 'items': rows}
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        client.discover(data)
        self.assertEqual(data['context_prs'][0]['roles'], ['requested_reviews'])
        self.assertEqual(data['activity'], [])

    def test_one_failing_search_keeps_the_other_results(self):
        mine = {**rest(5), 'user': {'login': 'me'}, 'updated_at': '2026-10-12T00:00:00Z'}

        def api(args):
            query = next((arg for arg in args if arg.startswith('q=')), '')
            if 'reviewed-by:' in query:
                raise RuntimeError('unexpected')
            if 'author:me updated' in query:
                return {'total_count': 1, 'incomplete_results': False, 'items': [mine]}
            if 'search/issues' in args:
                return {'total_count': 0, 'incomplete_results': False, 'items': []}
            return []
        client = self.client(api)
        data = {}
        client.discover(data)
        self.assertEqual([item['number'] for item in data['prs']], [5])
        self.assertTrue(any('search stopped early (RuntimeError)' in warning for warning in client.warnings))

    def test_creation_in_window_survives_later_item_update(self):
        client = self.client(lambda args: [])
        item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1', 'author_login': 'me',
                'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-20T00:00:00Z'}
        data = {'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual([(x['action'], x['at']) for x in data['activity']], [('opened', '2026-10-12T00:00:00Z')])

    @staticmethod
    def head_node(item=None, before=SHA, after='b' * 40, identity='FP_native'):
        return {'__typename': 'HeadRefForcePushedEvent', 'id': identity,
                'createdAt': '2025-01-01T00:00:00Z',
                'beforeCommit': None if before is None else {'oid': before},
                'afterCommit': None if after is None else {'oid': after},
                'pullRequest': {'url': (item or gql())['url']}}

    @staticmethod
    def head_result(nodes, more=False, cursor=None, total=999, item=None):
        native = item or gql()
        return {'data': {'p0': {'pullRequest': {'url': native['url'], 'number': native['number'],
            'repository': native['repository'], 'timelineItems': {'nodes': nodes, 'totalCount': total,
                'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}}}}

    def associate(self, handler, branches=None, repos=None, index=None):
        client = self.client(handler)
        client.authored_index = lambda repo: copy.deepcopy((index or {}).get(repo, []))
        branches = branches if branches is not None else [branch()]
        data = {}
        client.associate_branches(branches, repos or [repository()], data)
        return client, branches, data

class ReviewContextTest(unittest.TestCase):
    client = GithubSourcesTest.client

    @staticmethod
    def native_review(body='Review context', identity=7):
        return {'id': identity, 'node_id': 'REVIEW' + str(identity), 'user': {'login': 'other', 'id': 123},
                'html_url': gql()['url'] + '#pullrequestreview-' + str(identity), 'body': body,
                'state': 'COMMENTED', 'submitted_at': '2025-01-01T00:00:00Z', 'commit_id': SHA,
                'pull_request_url': 'https://api.github.com/repos/org/repo/pulls/1'}

    def test_ordinary_timeline_retains_distinct_review_snapshot_without_actor_credit(self):
        from extended_evidence import github_reference_comments
        from references import referenced_keys
        listing = self.native_review('Current review body')
        timeline = self.native_review('https://jira.example/browse/CORE-321')
        timeline.update(event='reviewed', state='commented', updated_at='2026-10-12T00:00:00Z')
        item = rest_item(rest(), 'prs', 'github.com')

        def handler(args):
            if args[2].endswith('/timeline'):
                return [timeline, copy.deepcopy(timeline)]
            if args[2].endswith('/reviews'):
                return [listing]
            return []

        client = self.client(handler)
        data = {'prs': [item], 'issues': [], 'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual([row['source'] for row in data['reference_reviews']], [timeline, listing])
        self.assertEqual([row['body'] for row in github_reference_comments(data)],
                         [timeline['body'], listing['body']])
        self.assertEqual(referenced_keys(data, 'jira.example'), ['CORE-321'])
        self.assertEqual(data['activity'], [])
        self.assertEqual(client.warnings, [])


class RestHeadRecoveryTest(unittest.TestCase):
    client = GithubSourcesTest.client
    head_node = staticmethod(GithubSourcesTest.head_node)
    head_result = staticmethod(GithubSourcesTest.head_result)

    @staticmethod
    def rest_head(identity=42, sha='c' * 40):
        return {'id': identity, 'node_id': 'NATIVE' + str(identity), 'event': 'head_ref_force_pushed',
                'created_at': '2025-01-01T00:00:00Z', 'actor': {'login': 'other'},
                'commit_id': sha, 'url': 'https://api.github.com/repos/org/repo/issues/events/' + str(identity)}

class SnapshotConcurrencyTest(unittest.TestCase):
    client = GithubSourcesTest.client
    associate = GithubSourcesTest.associate

class CommentEditTest(unittest.TestCase):
    client = GithubSourcesTest.client

    def test_comment_edits_are_not_credited_and_need_no_history_reads(self):
        event = {'id': 51, 'node_id': 'native51', 'user': {'login': 'me'}, 'body': 'Edited later',
                 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-51',
                 'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z'}
        client = self.client(lambda args: [event] if args[2] == 'repos/org/repo/issues/1/comments' else [])
        data = {'activity': []}
        client._inspect_activity(data, [{'url': 'https://github.com/org/repo/pull/1', 'repo': 'org/repo', 'number': 1}])
        self.assertEqual(data['activity'], [])
        self.assertFalse(any(call[0] == 'graphql' for call in client.calls))
        self.assertEqual(client.warnings, [])


if __name__ == '__main__':
    unittest.main()
