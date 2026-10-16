#!/usr/bin/env python3
"""Offline adversarial tests for roles, actor dates, and ref associations."""
import copy
import json
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
from github_sources import Github, PR_FIELDS, graphql_item, rest_item  # noqa: E402

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

    def test_lost_transport_or_thin_connector_result_never_erases_or_invents_context(self):
        for failure in ('missing', 'thin'):
            with self.subTest(failure=failure):
                def api(args):
                    if args[2] == 'repos/org/repo/issues/1':
                        return rest()
                    if args[2] == 'repos/org/repo/issues/2':
                        if failure == 'missing':
                            raise FileNotFoundError('transport no longer available')
                        return {'url': 'https://github.com/org/repo/pull/2', 'title': 'Search excerpt'}
                    return []
                client = self.client(api)
                data = {'prs': [], 'issues': []}
                client.add_linked_prs(data, ['https://github.com/org/repo/pull/1'])
                before = copy.deepcopy(data)
                self.assertEqual(client.add_linked_prs(data, ['https://github.com/org/repo/pull/2']), [])
                self.assertEqual(data, before)
                self.assertEqual(client.coverage['jira_link:org/repo#2']['status'], 'partial')
                self.assertEqual(len(client.warnings), 1)

    def test_native_bodies_are_preserved_exactly_for_cross_source_proof(self):
        body = 'Implements TEST-123.\n\nNative reference: https://jira.example.test/browse/TEST-123\n**Details** ~ remain unchanged. '
        for normalize, row, extra in [(rest_item, rest(), ('prs', 'github.com')),
                                      (graphql_item, gql(), ('github.com',))]:
            with self.subTest(source=normalize.__name__):
                row['body'] = body
                self.assertEqual(normalize(row, *extra)['body'], body)
                row['body'] = None
                self.assertEqual(normalize(row, *extra)['body'], '')
                del row['body']
                self.assertEqual(normalize(row, *extra)['body'], '')
                for invalid in (False, 0, [], {}):
                    row['body'] = invalid
                    with self.assertRaises(ValueError):
                        normalize(row, *extra)
        self.assertIn('body', PR_FIELDS.split())

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
        self.assertEqual(len(client.calls), 10)
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
                data = {'prs': [item] if kind == 'prs' else [], 'issues': [item] if kind == 'issues' else []}
                client.discover(data)
                self.assertEqual(len([args for args in client.calls if 'search/issues' not in args and args[0] != 'graphql']), expected_reads)
                self.assertEqual(len(data['activity']), 1)
                self.assertEqual(data['activity'][0]['url'], item['url'] + '#issuecomment-1')
                self.assertEqual(client.warnings, [])

    def test_rest_editor_does_not_replace_native_last_edit_identity_or_time(self):
        for native_editor, expected in [(None, 0), ({'login': 'other'}, 0), ({'login': 'me'}, 1)]:
            with self.subTest(native_editor=native_editor):
                event = {'id': 1, 'node_id': 'native1', 'user': {'login': 'me'}, 'editor': {'login': 'me'},
                         'html_url': 'https://github.com/org/repo/pull/1#issuecomment-1',
                         'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z'}
                proof = {'id': 'native1', 'databaseId': 1, 'url': event['html_url'], 'author': {'login': 'me'},
                         'editor': native_editor, 'createdAt': event['created_at'], 'updatedAt': event['updated_at'],
                         'lastEditedAt': '2026-10-11T23:59:59Z'}
                def api(args):
                    if args[0] == 'graphql':
                        return {'data': {'node': proof}}
                    return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
                client = self.client(api)
                item = {'url': 'https://github.com/org/repo/pull/1', 'repo': 'org/repo', 'number': 1}
                data = {'activity': []}
                client._inspect_activity(data, [item])
                self.assertEqual(len(data['activity']), expected)
                self.assertTrue(any(args[0] == 'graphql' for args in client.calls))
                if expected:
                    self.assertEqual(data['activity'][0]['at'], proof['lastEditedAt'])
                    self.assertEqual(data['activity'][0]['editor_evidence'], proof)
                if native_editor is None:
                    self.assertTrue(any('editor' in x for x in client.warnings))
                elif not expected:
                    self.assertEqual(client.warnings, [])

    def test_native_graphql_comment_editor_uses_last_edit_time_and_accepts_foreign_author(self):
        for category in ('issue_comment', 'review_comment'):
            for author in ('me', 'other'):
                with self.subTest(category=category, author=author):
                    event = {'id': 51, 'node_id': 'native-comment', 'user': {'login': author}, 'body': 'Native body',
                             'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:01Z',
                             'html_url': 'https://github.com/org/repo/pull/1#' + ('issuecomment-' if category == 'issue_comment' else 'discussion_r') + '51'}
                    native = {'id': event['node_id'], 'databaseId': 51, 'url': event['html_url'],
                              'author': {'login': author}, 'editor': {'login': 'Me'},
                              'createdAt': event['created_at'], 'updatedAt': event['updated_at'],
                              'lastEditedAt': '2026-10-12T00:00:00Z'}
                    endpoint = 'repos/org/repo/' + ('issues' if category == 'issue_comment' else 'pulls') + '/1/comments'
                    def api(args):
                        if args[0] == 'graphql':
                            self.assertIn('id=native-comment', args)
                            self.assertIn('IssueComment' if category == 'issue_comment' else 'PullRequestReviewComment', query_text(args))
                            return {'data': {'node': copy.deepcopy(native)}}
                        return [copy.deepcopy(event)] if args[2] == endpoint else []
                    client = self.client(api)
                    data = {'activity': []}
                    client._inspect_activity(data, [{'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}])
                    self.assertEqual(len(data['activity']), 1)
                    self.assertEqual(data['activity'][0]['action'], category + '_edited')
                    self.assertEqual(data['activity'][0]['actor'], 'Me')
                    self.assertEqual(data['activity'][0]['at'], native['lastEditedAt'])
                    self.assertEqual(data['activity'][0]['editor_evidence'], native)
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

    def test_editor_recovery_failure_or_identity_drift_retains_creation_and_marks_gap(self):
        event = {'id': 51, 'node_id': 'native-comment', 'user': {'login': 'me'},
                 'created_at': '2026-10-12T00:00:00Z', 'updated_at': '2026-10-12T00:00:02Z',
                 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-51'}
        native = {'id': event['node_id'], 'databaseId': 51, 'url': event['html_url'],
                  'author': {'login': 'me'}, 'editor': {'login': 'me'}, 'createdAt': event['created_at'],
                  'updatedAt': event['updated_at'], 'lastEditedAt': '2026-10-12T00:00:01Z'}
        for field, value in [('id', 'other'), ('databaseId', 52), ('url', event['html_url'] + 'x'),
                             ('author', {'login': 'other'}), ('createdAt', '2026-10-11T00:00:00Z'),
                             ('updatedAt', '2026-10-12T00:00:03Z'), ('editor', None),
                             ('lastEditedAt', '2026-10-12T00:00:03Z'), ('errors', True), ('transport', True)]:
            with self.subTest(field=field):
                node = copy.deepcopy(native)
                node[field] = value
                def api(args):
                    if args[0] == 'graphql':
                        if field == 'transport':
                            raise OSError('offline')
                        return {'data': {'node': node}, **({'errors': [{'message': 'denied'}]} if field == 'errors' else {})}
                    return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
                client = self.client(api)
                data = {'activity': []}
                client._inspect_activity(data, [{'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}])
                self.assertEqual([x['action'] for x in data['activity']], ['issue_comment'])
                self.assertEqual(client.coverage['activity:org/repo#1:issue_comment']['status'], 'partial')

    def test_verified_absent_remote_commit_is_completed_narrow_negative(self):
        def api(args):
            return snapshot_data(args, lambda i, repo, data: data.update({
                'c' + i: {'id': 'native-repository', 'nameWithOwner': repo.upper(), 'object': None}}))
        client, branches, _ = self.associate(api)
        lookup = branches[0]['association_lookups'][0]
        self.assertEqual(branches[0]['pr_status'], 'no_match')
        self.assertEqual(lookup['status'], 'ok')
        self.assertEqual(lookup['commit_object'], 'absent')
        self.assertEqual(lookup['repository_node_id'], 'native-repository')
        self.assertEqual(lookup['pages'], 2)
        self.assertEqual(client.warnings, [])

    def test_absent_object_needs_successful_bound_repository_and_complete_search(self):
        for failure in ('wrong_repo', 'missing_object', 'missing_id', 'numeric_id', 'repo_null', 'alias_error', 'search_error'):
            with self.subTest(failure=failure):
                def api(args):
                    if args[0] != 'graphql':
                        raise OSError('Repository identity unavailable')
                    result = snapshot_data(args)
                    repo = {'id': 'native-repository', 'nameWithOwner': 'org/repo', 'object': None}
                    result['data']['c0'] = repo
                    if failure == 'wrong_repo':
                        repo['nameWithOwner'] = 'wrong/repo'
                    elif failure == 'missing_object':
                        del repo['object']
                    elif failure == 'missing_id':
                        del repo['id']
                    elif failure == 'numeric_id':
                        repo['id'] = 12
                    elif failure == 'repo_null':
                        result['data']['c0'] = None
                    else:
                        alias = 'c0' if failure == 'alias_error' else 's0'
                        result['errors'] = [{'path': [alias], 'message': 'denied'}]
                    return result
                client, branches, _ = self.associate(api)
                self.assertEqual(branches[0]['pr_status'], 'partial')
                self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_positive_search_survives_confirmed_absent_object(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql(state='CLOSED')])
            result['data']['c0'] = {'id': 'native-repository', 'nameWithOwner': 'org/repo', 'object': None}
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'any_author_sha_search')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'ok')
        self.assertEqual(branches[0]['association_lookups'][0]['commit_object'], 'absent')

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

    def test_bot_comment_editor_identity_uses_native_author_node_not_login_spelling(self):
        event = {'id': 30, 'node_id': 'comment-id', 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-30',
                 'user': {'login': 'helper[bot]', 'node_id': 'bot-id'},
                 'created_at': '2026-10-10T00:00:00Z', 'updated_at': '2026-10-12T00:00:01Z'}
        node = {'id': 'comment-id', 'databaseId': 30, 'url': event['html_url'],
                'author': {'login': 'helper', 'id': 'bot-id'}, 'editor': {'login': 'me'},
                'createdAt': event['created_at'], 'updatedAt': event['updated_at'], 'lastEditedAt': '2026-10-12T00:00:00Z'}
        client = self.client(lambda args: {'data': {'node': copy.deepcopy(node)}})
        self.assertEqual(client._comment_edit(event, 'issue_comment', 'edits'), node)
        self.assertEqual(client.warnings, [])
        node['author'] = {'login': 'helper[bot]', 'id': 'different-native-id'}
        self.assertIsNone(client._comment_edit(event, 'issue_comment', 'edits'))
        self.assertEqual(client.coverage['edits']['status'], 'partial')

    def test_renamed_repository_absence_requires_native_equal_id_proof_and_caches_reads(self):
        reads = []
        def api(args):
            if args[0] != 'graphql':
                reads.append(args[2])
                return {'id': 17, 'full_name': 'canonical/repo', 'html_url': 'https://github.com/canonical/repo'}
            return snapshot_data(args, lambda i, repo, data: data.update({
                'c' + i: {'id': 'native-repository', 'nameWithOwner': 'canonical/repo', 'object': None}}))
        client, branches, data = self.associate(api, branches=[branch(sha='a' * 40), branch(sha='b' * 40)])
        self.assertEqual([x['pr_status'] for x in branches], ['no_match', 'no_match'])
        self.assertEqual(reads, ['repos/org/repo', 'repos/canonical/repo'])
        self.assertEqual(len(data['repository_aliases']), 1)
        self.assertEqual(data['repository_aliases'][0]['requested']['id'], 17)
        self.assertEqual(data['repository_aliases'][0]['canonical']['id'], 17)
        self.assertEqual(client.warnings, [])

    def test_repository_redirect_repeats_sha_search_canonically_and_recovers_authored_index(self):
        calls = []
        target = gql(number=7, repo='canonical/repo', state='CLOSED')
        indexed = gql(number=8, repo='canonical/repo', sha='b' * 40, head='backup/feature')
        def api(args):
            if args[0] != 'graphql':
                return {'id': 17, 'full_name': 'canonical/repo', 'html_url': 'https://github.com/canonical/repo'}
            calls.append(query_text(args))
            if 'q=is:pr repo:canonical/repo author:me sort:created-asc' in args:
                return {'data': {'search': connection([indexed])}}
            if any(arg.startswith('q=') for arg in args):
                return {'data': {'search': connection()}}
            def change(i, repo, data):
                if repo == 'org/repo':
                    data['c' + i] = {'id': 'native-repository', 'nameWithOwner': 'canonical/repo', 'object': None}
                else:
                    data.pop('c' + i)
                    data['s' + i] = connection([target])
            return snapshot_data(args, change)
        client = self.client(api)
        branches = [branch()]
        data = {}
        client.associate_branches(branches, [repository()], data)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['url'], target['url'])
        self.assertEqual(branches[0]['prs'][0]['proof'], 'any_author_sha_search')
        self.assertEqual(branches[0]['related_prs'][0]['url'], indexed['url'])
        self.assertEqual(branches[0]['association_lookups'][0]['search_repo'], 'canonical/repo')
        self.assertEqual({item['url'] for item in data['pr_index']}, {target['url'], indexed['url']})
        self.assertTrue(any('repo:canonical/repo is:pr ' in query for query in calls))
        self.assertEqual(client.warnings, [])

    def test_canonical_sha_search_failure_does_not_become_negative_after_alias(self):
        def api(args):
            if args[0] != 'graphql':
                return {'id': 17, 'full_name': 'canonical/repo', 'html_url': 'https://github.com/canonical/repo'}
            if 'repo:canonical/repo is:pr ' in query_text(args):
                raise OSError('Canonical search unavailable')
            return snapshot_data(args, lambda i, repo, data: data.update({
                'c' + i: {'id': 'native-repository', 'nameWithOwner': 'canonical/repo', 'object': None}}))
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['association_lookups'][0]['commit_object'], 'absent')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_inaccessible_fork_global_sha_positive_is_kept_without_erasing_remote_gap(self):
        def api(args):
            query = query_text(args)
            if 'query:"is:pr ' in query:
                self.assertNotIn('repository(', query)
                return {'data': {'s0': connection([gql(repo='upstream/repo', state='CLOSED')])}}
            result = snapshot_data(args)
            result['data']['c0'] = None
            result['errors'] = [{'path': ['c0'], 'message': 'Repository unavailable'}]
            return result
        client, branches, data = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['repo'], 'upstream/repo')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'global_any_author_sha_search')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')
        self.assertEqual(branches[0]['association_lookups'][0]['global_search']['status'], 'ok')
        self.assertEqual(data['pr_index'][0]['repo'], 'upstream/repo')
        self.assertEqual(client.coverage['global_snapshot:' + SHA]['count'], 1)

    def test_empty_global_sha_search_keeps_unknown_private_fork_and_deduplicates_hashes(self):
        global_calls = []
        def api(args):
            if 'query:"is:pr ' in query_text(args):
                global_calls.append(args)
                return {'data': {'s0': connection()}}
            result = snapshot_data(args)
            for key in list(result['data']):
                if key.startswith('c'):
                    result['data'][key] = None
            return result
        client, branches, _ = self.associate(api, repos=[repository(remotes=('old/fork1', 'old/fork2'))])
        self.assertEqual(len(global_calls), 1)
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['prs'], [])
        self.assertTrue(all(info['global_search']['count'] == 0 for info in branches[0]['association_lookups']))

    def test_global_sha_partial_failure_preserves_prior_page_and_remote_unknown(self):
        calls = []
        def api(args):
            query = query_text(args)
            if 'query:"is:pr ' in query:
                calls.append(args)
                if len(calls) == 1:
                    return {'data': {'s0': connection([gql(repo='upstream/repo')], total=2, more=True, cursor='next')}}
                raise subprocess.CalledProcessError(403, 'gh')
            result = snapshot_data(args)
            result['data']['c0'] = None
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'global_any_author_sha_search')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')
        self.assertEqual(branches[0]['association_lookups'][0]['global_search']['status'], 'partial')
        self.assertEqual(client.coverage['global_snapshot:' + SHA]['count'], 1)

    def test_native_edit_window_uses_safe_updated_envelope_and_same_second_edits(self):
        for created, updated, edited, expected_creation in [
                ('2026-10-01T00:00:00Z', '2026-10-16T00:00:01Z', '2026-10-16T00:00:00Z', False),
                ('2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', '2026-10-12T00:00:00Z', True)]:
            with self.subTest(created=created, updated=updated):
                item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}
                event = {'id': 51, 'node_id': 'native-comment', 'user': {'login': 'me'},
                         'created_at': created, 'updated_at': updated, 'html_url': item['url'] + '#issuecomment-51'}
                native = {'id': event['node_id'], 'databaseId': 51, 'url': event['html_url'],
                          'author': {'login': 'me'}, 'editor': {'login': 'me'},
                          'createdAt': created, 'updatedAt': updated, 'lastEditedAt': edited}
                def api(args):
                    if args[0] == 'graphql':
                        return {'data': {'node': native}}
                    return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
                client = self.client(api)
                data = {'activity': []}
                client._inspect_activity(data, [item])
                actions = [x['action'] for x in data['activity']]
                self.assertEqual(actions, (['issue_comment'] if expected_creation else []) + ['issue_comment_edited'])
                self.assertEqual(data['activity'][-1]['at'], edited)
                self.assertEqual(client.warnings, [])

    def test_matching_native_null_author_does_not_hide_verified_own_edit(self):
        item = {'repo': 'org/repo', 'number': 1, 'url': 'https://github.com/org/repo/pull/1'}
        event = {'id': 51, 'node_id': 'native-comment', 'user': None,
                 'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:01Z',
                 'html_url': item['url'] + '#issuecomment-51'}
        native = {'id': event['node_id'], 'databaseId': 51, 'url': event['html_url'],
                  'author': None, 'editor': {'login': 'me'}, 'createdAt': event['created_at'],
                  'updatedAt': event['updated_at'], 'lastEditedAt': '2026-10-12T00:00:00Z'}
        def api(args):
            if args[0] == 'graphql':
                return {'data': {'node': native}}
            return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual([x['action'] for x in data['activity']], ['issue_comment_edited'])
        self.assertIsNone(data['activity'][0]['editor_evidence']['author'])
        self.assertIsNone(data['reference_comments'][0]['author_login'])
        self.assertEqual(client.warnings, [])
        native['author'] = {'login': 'another-user'}
        self.assertIsNone(client._comment_edit(event, 'issue_comment', 'edited'))
        self.assertEqual(client.coverage['edited']['status'], 'partial')

    def test_later_canonical_index_does_not_upgrade_earlier_sha_search_proof(self):
        snapshot = gql(repo='canonical/repo', sha='c' * 40)
        indexed = graphql_item(gql(repo='canonical/repo', sha=SHA), 'github.com')
        def api(args):
            if args[0] != 'graphql':
                return {'id': 17, 'full_name': 'canonical/repo', 'html_url': 'https://github.com/canonical/repo'}
            def change(i, repo, data):
                if repo == 'org/repo':
                    data['c' + i] = {'id': 'native-repository', 'nameWithOwner': 'canonical/repo', 'object': None}
                else:
                    data.pop('c' + i)
                    data['s' + i] = connection([snapshot])
            return snapshot_data(args, change)
        client, branches, data = self.associate(api, index={'canonical/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'any_author_sha_search')
        self.assertEqual(data['pr_index'][0]['head_sha'], 'c' * 40)
        self.assertEqual(client.warnings, [])

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

    def test_canonical_index_conflicting_head_keeps_both_native_observations_for_two_branches(self):
        snapshot = gql(repo='canonical/repo', sha='c' * 40)
        snapshot['body'] = 'Earlier native TEST-81 context'
        indexed = graphql_item({**gql(repo='canonical/repo', sha='b' * 40),
                                'body': 'Later native TEST-82 context'}, 'github.com')
        def api(args):
            if args[0] != 'graphql':
                return {'id': 17, 'full_name': 'canonical/repo', 'html_url': 'https://github.com/canonical/repo'}
            def change(i, repo, data):
                if repo == 'org/repo':
                    data['c' + i] = {'id': 'native-repository', 'nameWithOwner': 'canonical/repo', 'object': None}
                else:
                    data.pop('c' + i)
                    if i == '0':
                        data['s' + i] = connection([snapshot])
            return snapshot_data(args, change)
        client, branches, data = self.associate(api, branches=[branch(), branch(sha='b' * 40)],
                                               index={'canonical/repo': [indexed]})
        self.assertEqual([br['pr_status'] for br in branches], ['matched', 'matched'])
        self.assertEqual(branches[0]['prs'][0]['proof'], 'any_author_sha_search')
        self.assertEqual(branches[1]['prs'][0]['proof'], 'exact_head_or_merge_sha')
        self.assertEqual(data['pr_index'][0]['head_sha'], 'c' * 40)
        self.assertEqual(data['pr_index'][0]['body'], snapshot['body'])
        self.assertEqual(data['pr_index_observations'], [indexed])
        native = data['pr_index'] + data['pr_index_observations']
        self.assertTrue(all(any(br['hash'] in (row['head_sha'], row['merge_sha']) for row in native)
                            for br in branches if br['prs'][0]['proof'] == 'exact_head_or_merge_sha'))
        self.assertEqual(client.warnings, [])

    def test_conflicting_snapshot_observations_deduplicate_complete_identical_native_rows(self):
        first = gql(repo='org/repo')
        second = {**first, 'body': 'Later native TEST-83 context', 'headRefOid': 'c' * 40}
        def api(args):
            def change(i, repo, data):
                data['s' + i] = connection([first])
                data['c' + i]['object']['associatedPullRequests'] = connection([second])
            return snapshot_data(args, change)
        client, branches, data = self.associate(api, branches=[branch(), branch(sha='b' * 40)])
        self.assertEqual(len(data['pr_index']), 1)
        self.assertEqual(len(data['pr_index_observations']), 1)
        self.assertEqual(data['pr_index'][0], graphql_item(first, 'github.com'))
        self.assertEqual(data['pr_index_observations'][0], graphql_item(second, 'github.com'))
        self.assertTrue(all(br['pr_status'] == 'matched' for br in branches))
        self.assertEqual(client.warnings, [])

    def test_local_no_remote_and_other_provider_snapshots_receive_native_global_sha_lookup(self):
        for kind in (None, 'local', 'other'):
            with self.subTest(kind=kind):
                repo = repository(remotes=())
                if kind:
                    repo['remotes'] = [{'kind': kind, 'host': 'other.example.test' if kind == 'other' else None,
                                        'repo': None, 'name': 'origin'}]
                calls = []
                def api(args):
                    calls.append(args)
                    self.assertIn('query:"is:pr ' + SHA, query_text(args))
                    self.assertNotIn('repository(', query_text(args))
                    return {'data': {'s0': connection([gql(repo='upstream/repo')])}}
                client, branches, data = self.associate(api, repos=[repo])
                self.assertEqual(branches[0]['pr_status'], 'matched')
                self.assertEqual(branches[0]['association_scope'], 'github_global_without_repository')
                self.assertEqual(branches[0]['prs'][0]['proof'], 'global_any_author_sha_search')
                self.assertEqual(branches[0]['prs'][0]['repo'], 'upstream/repo')
                self.assertEqual(branches[0]['association_lookups'][0]['scope'], 'github_global')
                self.assertEqual(data['pr_index'][0]['repo'], 'upstream/repo')
                self.assertEqual(len(calls), 1)

    def test_unrouted_global_negative_does_not_claim_no_private_or_other_provider_pr(self):
        def api(args):
            return {'data': {'s0': connection()}}
        client, branches, _ = self.associate(api, repos=[repository(remotes=())])
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'ok')
        self.assertEqual(client.coverage['global_snapshot:' + SHA]['status'], 'ok')
        self.assertEqual(client.coverage['unrouted:/local/repo']['status'], 'partial')
        self.assertTrue(any('no configured GitHub repository' in message for message in client.warnings))

    def test_global_sha_result_is_shared_across_inaccessible_fork_and_local_backup_clone(self):
        global_calls = []
        def api(args):
            if 'query:"is:pr ' in query_text(args):
                global_calls.append(args)
                return {'data': {'s0': connection([gql(repo='upstream/repo')])}}
            result = snapshot_data(args)
            result['data']['c0'] = None
            return result
        client, branches, _ = self.associate(api, branches=[branch('/fork'), branch('/backup')],
                                             repos=[repository('/fork'), repository('/backup', remotes=())])
        self.assertEqual(len(global_calls), 1)
        self.assertEqual([br['pr_status'] for br in branches], ['matched', 'matched'])
        self.assertEqual({br['prs'][0]['url'] for br in branches}, {'https://github.com/upstream/repo/pull/1'})

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

    def test_jira_seeds_validate_host_preserve_roles_and_cover_missing_links(self):
        valid = 'https://github.com/org/repo/pull/1'
        fetched = []
        def api(args):
            if 'search/issues' in args:
                return {'total_count': 0, 'incomplete_results': False, 'items': []}
            endpoint = args[2]
            if endpoint in ('repos/org/repo/issues/1', 'repos/org/repo/issues/2'):
                fetched.append(endpoint)
                if endpoint.endswith('/2'):
                    raise subprocess.CalledProcessError(404, 'gh')
                return rest()
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        client.discover(data, [valid, valid, 'https://evil.test/org/repo/pull/9',
                               'https://github.com/org/repo/pull/2', None, {},
                               'https://github.com/org/repo/pull/1#fragment'])
        self.assertEqual(fetched, ['repos/org/repo/issues/1', 'repos/org/repo/issues/2'])
        self.assertEqual(data['context_prs'][0]['roles'], ['jira_link'])
        self.assertEqual(data['activity'], [])
        self.assertEqual(client.coverage['jira_link:org/repo#2']['status'], 'partial')
        self.assertEqual(client.coverage['jira_links']['status'], 'partial')

    def test_mixed_case_seed_keeps_native_url_body_and_input_provenance(self):
        native = rest()
        native['body'] = 'Exact native body TEST-1.\nSecond line stays intact.'
        original = copy.deepcopy(native)
        def api(args):
            if args[2] == 'repos/ORG/Repo/issues/1':
                return native
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        seed = 'https://GITHUB.com/ORG/Repo/pull/1'
        client.add_linked_prs(data, [seed])
        item = data['context_prs'][0]
        self.assertEqual(item['url'], native['html_url'])
        self.assertEqual(item['repo'], 'org/repo')
        self.assertEqual(item['body'], native['body'])
        self.assertEqual(item['jira_seed_urls'], [seed])
        self.assertEqual(native, original)
        info = client.coverage['jira_link:ORG/Repo#1']
        self.assertEqual((info['requested_url'], info['resolved_url']), (seed, native['html_url']))
        before = len(client.calls)
        client.add_linked_prs(data, [native['html_url']])
        self.assertEqual(len(client.calls), before)
        self.assertEqual(item['jira_seed_urls'], [seed, native['html_url']])
        self.assertEqual(len(data['context_prs']), 1)

    def test_existing_case_equivalent_native_pr_is_not_refetched(self):
        client = self.client(lambda args: self.fail('Existing native item must not be reread'))
        item = rest_item(rest(), 'prs', 'github.com')
        data = {'prs': [item], 'issues': [], 'activity': [{'preserved': True}]}
        seed = 'https://github.com/ORG/Repo/pull/1'
        self.assertEqual(client.add_linked_prs(data, [seed]), [])
        self.assertEqual(data['context_prs'], [])
        self.assertEqual(item['jira_seed_urls'], [seed])
        self.assertEqual(item['roles'], ['jira_link'])
        self.assertEqual(data['activity'], [{'preserved': True}])

    def test_transferred_repo_requires_native_equal_ids_and_caches_proof(self):
        metadata = {'id': 12345, 'full_name': 'new/project', 'html_url': 'https://github.com/new/project'}
        def api(args):
            endpoint = args[2]
            if endpoint in ('repos/old/project', 'repos/new/project'):
                return copy.deepcopy(metadata)
            if endpoint.startswith('repos/old/project/issues/'):
                return rest(int(endpoint.rsplit('/', 1)[1]), repo='new/project')
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        seeds = [f'https://github.com/old/project/pull/{number}' for number in (1, 2)]
        client.add_linked_prs(data, seeds)
        self.assertEqual([item['url'] for item in data['context_prs']],
                         ['https://github.com/new/project/pull/1', 'https://github.com/new/project/pull/2'])
        self.assertEqual([item['jira_seed_urls'] for item in data['context_prs']], [[url] for url in seeds])
        self.assertEqual(data['repository_aliases'], [{
            'host': 'github.com', 'requested_repo': 'old/project', 'canonical_repo': 'new/project',
            'requested': {'path': 'repos/old/project', **metadata},
            'canonical': {'path': 'repos/new/project', **metadata}}])
        self.assertEqual(sum(args[2] in ('repos/old/project', 'repos/new/project') for args in client.calls), 2)
        before = len(client.calls)
        client.add_linked_prs(data, ['https://github.com/new/project/pull/1', *seeds])
        self.assertEqual(len(client.calls), before)
        self.assertEqual(client.warnings, [])

    def test_transferred_repo_keeps_existing_native_context_and_events(self):
        metadata = {'id': 12345, 'full_name': 'new/project', 'html_url': 'https://github.com/new/project'}
        item = rest_item(rest(repo='new/project'), 'prs', 'github.com')
        item['body'] = 'Preserved earlier native observation'
        event = {'preserved': True}
        def api(args):
            if args[2] in ('repos/old/project', 'repos/new/project'):
                return metadata
            if args[2] == 'repos/old/project/issues/1':
                return rest(repo='new/project')
            self.fail('Existing canonical context actor endpoints must not be reread')
        client = self.client(api)
        data = {'prs': [item], 'issues': [], 'activity': [event]}
        seed = 'https://github.com/old/project/pull/1'
        self.assertEqual(client.add_linked_prs(data, [seed]), [])
        self.assertEqual(data['context_prs'], [])
        self.assertEqual(data['activity'], [event])
        self.assertEqual(item['body'], 'Preserved earlier native observation')
        self.assertEqual(item['jira_seed_urls'], [seed])
        self.assertEqual(client.warnings, [])

    def test_unproven_repository_redirect_remains_a_gap_and_failure_is_cached(self):
        metadata = {'id': 12345, 'full_name': 'new/project', 'html_url': 'https://github.com/new/project'}
        for field, value in [('id', 54321), ('id', True), ('full_name', 'unrelated/project'),
                             ('full_name', 'NEW/Project'), ('html_url', 'https://evil.test/new/project')]:
            with self.subTest(field=field, value=value):
                def api(args):
                    endpoint = args[2]
                    if endpoint == 'repos/old/project':
                        return metadata
                    if endpoint == 'repos/new/project':
                        return {**metadata, field: value}
                    if endpoint.startswith('repos/old/project/issues/'):
                        return rest(int(endpoint.rsplit('/', 1)[1]), repo='new/project')
                    return []
                client = self.client(api)
                data = {'prs': [], 'issues': []}
                client.add_linked_prs(data, [f'https://github.com/old/project/pull/{number}' for number in (1, 2)])
                self.assertEqual(data['context_prs'], [])
                self.assertNotIn('repository_aliases', data)
                self.assertEqual(sum(args[2] in ('repos/old/project', 'repos/new/project') for args in client.calls), 2)
                self.assertEqual(len(client.warnings), 2)

    def test_case_equivalence_never_accepts_wrong_repository_number_host_or_kind(self):
        wrong_host = rest()
        wrong_host['html_url'] = 'https://evil.test/org/repo/pull/1'
        wrong_kind = rest()
        wrong_kind.pop('pull_request')
        wrong_kind['html_url'] = 'https://github.com/org/repo/issues/1'
        for row in [rest(repo='org/other'), rest(2), wrong_host, wrong_kind]:
            with self.subTest(response=row['html_url']):
                client = self.client(lambda args: row)
                data = {'prs': [], 'issues': []}
                client.add_linked_prs(data, ['https://github.com/ORG/Repo/pull/1'])
                self.assertEqual(data['context_prs'], [])
                self.assertEqual(data['activity'], [])
                self.assertEqual(client.coverage['jira_link:ORG/Repo#1']['status'], 'partial')
                expected = ['repos/ORG/Repo/issues/1']
                if row['html_url'] == 'https://github.com/org/other/pull/1':
                    expected.append('repos/ORG/Repo')
                self.assertEqual([args[2] for args in client.calls], expected)

    def test_existing_jira_seed_is_not_refetched_or_duplicated(self):
        client = self.client(lambda args: {'total_count': 0, 'incomplete_results': False, 'items': []}
                             if 'search/issues' in args else [])
        item = {'url': 'https://github.com/org/repo/pull/1', 'repo': 'org/repo', 'number': 1}
        data = {'prs': [item], 'issues': []}
        client.discover(data, [item['url']])
        self.assertEqual(data['context_prs'], [])
        self.assertEqual(item['roles'], ['jira_link'])

    def test_creation_in_window_survives_later_item_update(self):
        def api(args):
            if 'search/issues' in args:
                return {'total_count': 0, 'incomplete_results': False, 'items': []}
            if args[2] == 'repos/org/repo/issues/1':
                row = rest()
                row['created_at'] = '2026-10-12T00:00:00Z'
                row['user']['login'] = 'me'
                return row
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        client.discover(data, ['https://github.com/org/repo/pull/1'])
        self.assertEqual([(x['action'], x['at']) for x in data['activity']], [('opened', '2026-10-12T00:00:00Z')])

    def test_incremental_links_preserve_existing_data_and_inspect_only_new_urls(self):
        def api(args):
            endpoint = args[2]
            if endpoint == 'repos/org/repo/issues/3':
                return rest(3)
            if endpoint == 'repos/org/repo/issues/3/comments':
                return [{'id': 31, 'user': {'login': 'me'}, 'created_at': '2026-10-12T00:00:00Z',
                         'updated_at': '2026-10-12T00:00:00Z', 'body': 'Native follow-up',
                         'html_url': 'https://github.com/org/repo/pull/3#issuecomment-31'}]
            return []
        client = self.client(api)
        old = rest_item(rest(1), 'prs', 'github.com')
        context = rest_item(rest(2), 'prs', 'github.com')
        context['roles'] = ['open_authored_prs']
        old_event = {'item_url': old['url'], 'action': 'review', 'at': '2026-10-11T00:00:00Z'}
        data = {'prs': [old], 'issues': [], 'context_prs': [context], 'context_issues': [],
                'activity': [old_event], 'pr_index': [{'url': old['url'], 'body': 'Indexed observation'}]}
        urls = [old['url'], context['url'], 'https://github.com/org/repo/pull/3',
                'https://github.com/org/repo/pull/3']
        new_items = client.add_linked_prs(data, urls)
        self.assertEqual([x['number'] for x in new_items], [3])
        self.assertEqual(data['prs'], [old])
        self.assertEqual([x['number'] for x in data['context_prs']], [2, 3])
        self.assertEqual(data['context_prs'][0]['roles'], ['open_authored_prs', 'jira_link'])
        self.assertIs(data['activity'][0], old_event)
        self.assertEqual(len(data['activity']), 2)
        self.assertEqual(data['activity'][1]['body'], 'Native follow-up')
        self.assertEqual(data['pr_index'][0]['body'], 'Indexed observation')
        self.assertEqual(len(client.calls), 5)
        self.assertTrue(all('org/repo/issues/3' in args[2] or 'org/repo/pulls/3' in args[2]
                            for args in client.calls))
        client.add_linked_prs(data, urls)
        self.assertEqual(len(client.calls), 5)
        self.assertEqual(len(data['activity']), 2)

    def test_incremental_link_failure_keeps_positive_and_does_not_repeat_attempt(self):
        def api(args):
            if args[2] == 'repos/org/repo/issues/2':
                raise subprocess.CalledProcessError(404, 'gh')
            if args[2] == 'repos/org/repo/issues/1':
                return rest(1)
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': [], 'activity': [{'existing': True}]}
        urls = ['https://github.com/org/repo/pull/1', 'https://github.com/org/repo/pull/2']
        client.add_linked_prs(data, urls)
        self.assertEqual([x['number'] for x in data['context_prs']], [1])
        self.assertEqual(data['activity'], [{'existing': True}])
        self.assertEqual(client.coverage['jira_link:org/repo#2']['status'], 'partial')
        before = len(client.calls)
        client.add_linked_prs(data, urls)
        self.assertEqual(len(client.calls), before)
        self.assertTrue(client.warnings)

    def test_discover_seed_actor_endpoints_are_not_read_twice(self):
        def api(args):
            if 'search/issues' in args:
                return {'total_count': 0, 'incomplete_results': False, 'items': []}
            if args[2] == 'repos/org/repo/issues/1':
                return rest(1)
            return []
        client = self.client(api)
        data = {'prs': [], 'issues': []}
        result = client.discover(data, ['https://github.com/org/repo/pull/1'])
        self.assertEqual(len(result), 1)
        endpoints = [args[2] for args in client.calls if 'search/issues' not in args]
        self.assertEqual(len(endpoints), 5)
        self.assertEqual(len(set(endpoints)), 5)
        searches_before = sum('search/issues' in args for args in client.calls)
        client.add_linked_prs(data, ['https://github.com/org/repo/pull/1'])
        self.assertEqual(sum('search/issues' in args for args in client.calls), searches_before)
        self.assertEqual(len(client.calls), 15)

    def test_incremental_bad_host_warns_without_mutating_old_capture(self):
        client = self.client(lambda args: self.fail('No API request expected'))
        data = {'prs': [], 'issues': [], 'context_prs': [], 'context_issues': [], 'activity': [{'old': True}]}
        original = copy.deepcopy(data)
        self.assertEqual(client.add_linked_prs(data, ['https://evil.test/org/repo/pull/1']), [])
        self.assertEqual(data, original)
        self.assertEqual(client.coverage['jira_links']['status'], 'partial')

    def test_authored_index_retains_valid_rows_and_count_drift(self):
        def api(args):
            if any(a.startswith('after=') for a in args):
                return {'data': {'search': connection([gql(2)], total=3)}}
            return {'data': {'search': connection([None, gql(1)], total=2, more=True, cursor='next')}}
        client = self.client(api)
        rows = client.authored_index('org/repo')
        self.assertEqual([r['number'] for r in rows], [1, 2])
        self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')
        self.assertTrue(any('changed' in x for x in client.warnings))
        self.assertEqual(client.authored_index('org/repo'), rows)
        self.assertEqual(len(client.calls), 2)

    def test_authored_index_repeated_cursor_terminates_and_preserves(self):
        client = self.client(lambda args: {'data': {'search': connection([gql()], total=3, more=True, cursor='same')}})
        self.assertEqual(len(client.authored_index('org/repo')), 1)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')

    def test_authored_index_search_cap_cannot_look_complete(self):
        client = self.client(lambda args: {'data': {'search': connection([], total=1001)}})
        self.assertEqual(client.authored_index('org/repo'), [])
        self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')

    def test_authored_index_keeps_data_returned_with_graphql_errors(self):
        client = self.client(lambda args: {'errors': [{'message': 'partial'}], 'data': {'search': connection([gql()])}})
        self.assertEqual(len(client.authored_index('org/repo')), 1)
        self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')

    def associate(self, handler, branches=None, repos=None, index=None):
        client = self.client(handler)
        client.authored_index = lambda repo: copy.deepcopy((index or {}).get(repo, []))
        branches = branches if branches is not None else [branch()]
        data = {}
        client.associate_branches(branches, repos or [repository()], data)
        return client, branches, data

    def test_closed_unmerged_pr_found_by_sha_search_with_empty_commit_association(self):
        def api(args):
            return snapshot_data(args, lambda i, repo, data: data.update({'s' + i: connection([gql(state='CLOSED')])}))
        client, branches, data = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'any_author_sha_search')
        self.assertEqual(data['pr_index'][0]['state'], 'closed')

    def test_null_commit_is_partial_not_no_match(self):
        def api(args):
            return snapshot_data(args, lambda i, repo, data: data.update({'c' + i: {'object': None}}))
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_missing_graphql_response_is_partial(self):
        client, branches, _ = self.associate(lambda args: None)
        self.assertEqual(branches[0]['pr_status'], 'partial')

    def test_positive_commit_result_survives_search_alias_error(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = None
            result['data']['c0']['object']['associatedPullRequests'] = connection([gql()])
            result['errors'] = [{'path': ['s0'], 'message': 'search unavailable'}]
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'commit_association')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_gh_nonzero_partial_graphql_stdout_retains_positive_alias(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql()])
            result['data']['c0'] = None
            result['errors'] = [{'path': ['c0'], 'message': 'private object inaccessible'}]
            raise subprocess.CalledProcessError(1, 'gh', output=json.dumps(result))
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_positive_search_survives_null_commit(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql()])
            result['data']['c0'] = {'object': None}
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['association_lookups'][0]['count'], 1)
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_deduplicates_same_sha_across_clones_and_checks_every_remote(self):
        branches = [branch('/clone1'), branch('/clone2')]
        repos = [repository('/clone1', ('org/repo', 'me/fork')), repository('/clone2', ('org/repo',))]
        client, branches, _ = self.associate(lambda args: snapshot_data(args), branches, repos)
        self.assertEqual(len(client.calls), 1)
        self.assertEqual(query_text(client.calls[0]).count(':search('), 2)
        self.assertEqual(len(branches[0]['association_lookups']), 2)
        self.assertTrue(all(b['pr_status'] == 'no_match' for b in branches))

    def test_index_match_on_one_remote_does_not_hide_other_remote_failure(self):
        from github_sources import graphql_item
        indexed = graphql_item(gql(), 'github.com')
        def api(args):
            result = snapshot_data(args)
            result['data']['c0'] = None
            return result
        client, branches, _ = self.associate(api, repos=[repository(remotes=('org/repo', 'me/fork'))],
                                             index={'org/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['association_lookups'][0]['repo'], 'me/fork')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_later_snapshot_observation_cannot_overwrite_exact_index_proof(self):
        from github_sources import graphql_item
        indexed = graphql_item(gql(), 'github.com')
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql(sha='b' * 40, state='CLOSED')])
            return result
        client, branches, data = self.associate(api,
            branches=[branch('/old'), branch('/new', sha='b' * 40)],
            repos=[repository('/old'), repository('/new', ('me/fork',))], index={'org/repo': [indexed]})
        self.assertEqual([x['pr_status'] for x in branches], ['matched', 'matched'])
        self.assertEqual(data['pr_index'][0]['head_sha'], SHA)
        self.assertEqual(data['pr_index'][0]['state'], 'open')
        self.assertEqual(branches[1]['prs'][0]['state'], 'closed')

    def test_branch_name_only_match_is_not_exact_association(self):
        from github_sources import graphql_item
        indexed = graphql_item(gql(sha='b' * 40, head='feature'), 'github.com')
        client, branches, _ = self.associate(lambda args: snapshot_data(args),
            branches=[branch(refs=['refs/remotes/upstream/feature'])], index={'org/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['prs'], [])
        self.assertEqual(branches[0]['related_prs'][0]['proof'], 'head_name_only')

    def test_snapshot_search_and_commit_pagination_follow_cursors(self):
        def api(args):
            if 'after:' not in query_text(args):
                result = snapshot_data(args)
                result['data']['s0'] = connection([gql(1)], total=2, more=True, cursor='search-next')
                result['data']['c0']['object']['associatedPullRequests'] = connection([gql(3)], more=True, cursor='commit-next')
                return result
            self.assertIn('after:"search-next"', query_text(args))
            self.assertIn('after:"commit-next"', query_text(args))
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql(2)], total=2)
            result['data']['c0']['object']['associatedPullRequests'] = connection([gql(4)])
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(branches[0]['prs']), 4)
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'ok')

    def test_snapshot_repeated_cursor_is_partial_but_retains_matches(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql()], total=2, more=True, cursor='same')
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_positive_sibling_tuple_survives_other_tuple_error(self):
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = None
            result['data']['c0'] = None
            result['data']['s1'] = connection([gql(2)])
            result['errors'] = [{'path': ['s0'], 'message': 'failed'}, {'path': ['c0'], 'message': 'failed'}]
            return result
        client, branches, _ = self.associate(api, branches=[branch(), branch(sha='b' * 40)])
        self.assertEqual([x['pr_status'] for x in branches], ['partial', 'matched'])
        self.assertEqual(branches[1]['association_lookups'][0]['status'], 'ok')

    def test_snapshot_count_drift_visible_and_positive_retained(self):
        def api(args):
            result = snapshot_data(args)
            if 'after:' not in query_text(args):
                result['data']['s0'] = connection([gql(1)], total=2, more=True, cursor='next')
            else:
                result['data']['s0'] = connection([gql(2)], total=3)
            return result
        client, branches, _ = self.associate(api)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')

    def test_batches_more_than_twenty_distinct_snapshots(self):
        branches = [branch(sha=f'{i:040x}') for i in range(21)]
        client, _, _ = self.associate(lambda args: snapshot_data(args), branches=branches)
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(query_text(client.calls[0]).count(':search('), 20)
        self.assertEqual(query_text(client.calls[1]).count(':search('), 1)


if __name__ == '__main__':
    unittest.main()
