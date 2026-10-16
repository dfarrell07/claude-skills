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
from github_sources import Github, PR_FIELDS, branch_association_status, graphql_item, rest_item  # noqa: E402

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
        client = Github(api, 'github.com', 'me', START, END, warnings.append,
                        timeline_read=lambda url: {'url': url, 'status': 404, 'headers': {}, 'body': ''})
        client.calls, client.warnings = calls, warnings
        return client

    def test_role_case_variants_preserve_native_observation_and_inspect_one_identity(self):
        from extended_evidence import validate_github
        from work_links import referenced_keys
        def api(args):
            if 'search/issues' in args:
                query = next(arg[2:] for arg in args if arg.startswith('q='))
                if not query.startswith('is:pr '):
                    return {'total_count': 0, 'incomplete_results': False, 'items': []}
                first = 'author:me is:open' in query
                row = {**rest(repo='ORG/REPO' if first else 'org/repo'),
                       'created_at': '2026-10-10T00:00:00Z', 'user': {'login': 'me'},
                       'body': 'PROJ-123' if first else 'TASK-124'}
                return {'total_count': 1, 'incomplete_results': False, 'items': [row]}
            return []
        client = self.client(api)
        github = {'prs': [], 'issues': []}
        client.discover(github)
        self.assertEqual(len(github['context_prs']), 1)
        self.assertEqual(len(github['activity']), 1)
        self.assertEqual(github['context_prs'][0]['url'], 'https://github.com/ORG/REPO/pull/1')
        self.assertEqual(github['context_observations'][0]['url'], 'https://github.com/org/repo/pull/1')
        self.assertEqual(set(referenced_keys(github)), {'PROJ-123', 'TASK-124'})
        reads = [args[2].casefold() for args in client.calls if len(args) > 2 and args[2].startswith('repos/')]
        self.assertEqual(len(reads), len(set(reads)))
        self.assertEqual(len(github['context_prs'][0]['roles']), 7)
        validate_github({'metadata': {'github_host': 'github.com', 'github_user': 'me'}, 'github': github},
                        {}, START, END)
        github['context_prs'].append({**github['context_prs'][0],
                                      'url': 'https://github.com/org/repo/pull/1', 'repo': 'org/repo'})
        with self.assertRaisesRegex(ValueError, 'Duplicate'):
            validate_github({'metadata': {'github_host': 'github.com', 'github_user': 'me'}, 'github': github},
                            {}, START, END)

    def test_case_variant_native_comments_reviews_keep_activity_and_exact_proof_fragments(self):
        from extended_evidence import same_github_item_url, validate_github
        from work_links import referenced_keys
        for kind in ('prs', 'issues'):
            with self.subTest(kind=kind):
                route, number = ('pull', 1) if kind == 'prs' else ('issues', 2)
                lower = f'https://github.com/org/repo/{route}/{number}'
                comments = {}
                for category, identity, fragment, body in [
                        ('issue_comment', 42, 'issuecomment-', 'COMMENT-7'),
                        ('review_comment', 43, 'discussion_r', 'INLINE-9')]:
                    comments[category] = {'id': identity, 'node_id': 'N' + str(identity),
                        'user': {'login': 'me'}, 'body': body, 'created_at': '2026-10-10T00:00:00Z',
                        'updated_at': '2026-10-10T00:00:00Z', 'html_url': lower + '#' + fragment + str(identity)}
                def api(args):
                    if args[0] == 'graphql':
                        identity = next(arg[3:] for arg in args if arg.startswith('id='))
                        row = next(row for row in comments.values() if row['node_id'] == identity)
                        return {'data': {'node': {'id': identity, 'databaseId': row['id'], 'url': row['html_url'],
                            'author': {'login': 'me'}, 'editor': None, 'lastEditedAt': None,
                            'createdAt': row['created_at'], 'updatedAt': row['updated_at']}}}
                    if 'search/issues' in args:
                        query = next(arg[2:] for arg in args if arg.startswith('q='))
                        if query.startswith('is:pr ') != (kind == 'prs'):
                            return {'total_count': 0, 'incomplete_results': False, 'items': []}
                        first = 'author:me is:open' in query
                        row = {**rest(number), 'user': {'login': 'me'}, 'created_at': '2026-10-10T00:00:00Z',
                               'html_url': lower.replace('/org/repo/', '/ORG/REPO/') if first else lower}
                        if kind == 'issues':
                            row.pop('pull_request')
                        return {'total_count': 1, 'incomplete_results': False, 'items': [row]}
                    endpoint = args[2].casefold()
                    if f'/issues/{number}/comments' in endpoint:
                        return [comments['issue_comment']]
                    if endpoint.endswith('/reviews'):
                        return [{'id': 44, 'user': {'login': 'me'}, 'body': 'REVIEW-8', 'state': 'APPROVED',
                                 'submitted_at': '2026-10-11T00:00:00Z',
                                 'html_url': lower + '#pullrequestreview-44',
                                 'pull_request_url': f'https://api.github.com/repos/org/repo/pulls/{number}'}]
                    if endpoint.endswith('/pulls/1/comments'):
                        return [comments['review_comment']]
                    return []
                client = self.client(api)
                github = {'prs': [], 'issues': []}
                client.discover(github)
                self.assertEqual(len(github['context_' + kind]), 1)
                self.assertEqual(len(github['activity']), 4 if kind == 'prs' else 2)
                self.assertEqual(len(github['reference_comments']), 2 if kind == 'prs' else 1)
                self.assertEqual(set(referenced_keys(github)),
                                 {'COMMENT-7', 'REVIEW-8', 'INLINE-9'} if kind == 'prs' else {'COMMENT-7'})
                self.assertEqual(github['reference_comments'][0]['url'], lower + '#issuecomment-42')
                self.assertEqual(github['reference_comments'][0]['node_id'], 'N42')
                validate_github({'metadata': {'github_host': 'github.com', 'github_user': 'me'}, 'github': github},
                                {}, START, END)
                self.assertEqual(client.warnings, [])
                for bad in [lower.replace('/org/repo/', '/org/wrong/') + '#issuecomment-42',
                            lower[:-1] + '9#issuecomment-42', lower + '#issuecomment-43',
                            lower + '#ISSUECOMMENT-42', lower.replace('/org/', '/or\ng/') + '#issuecomment-42', lower.replace('/' + route + '/', '/' + route.upper() + '/')
                            + '#issuecomment-42', lower + '?x=1#issuecomment-42']:
                    altered = copy.deepcopy(github)
                    altered['reference_comments'][0]['url'] = bad
                    with self.assertRaises(ValueError):
                        validate_github({'metadata': {'github_host': 'github.com', 'github_user': 'me'},
                                         'github': altered}, {}, START, END)
                self.assertFalse(same_github_item_url(lower + '#ISSUECOMMENT-42', lower + '#issuecomment-42'))

    def test_index_case_drift_preserves_native_head_state_pairs_and_one_identity(self):
        from extended_evidence import validate_github
        for same_head in (False, True):
            with self.subTest(same_head=same_head):
                first = gql(repo='ORG/REPO', sha=SHA, head='unrelated')
                second_sha = SHA if same_head else 'b' * 40
                later = gql(repo='org/repo', sha=second_sha, state='CLOSED', head='unrelated')
                def api(args):
                    if any(arg.startswith('q=is:pr repo:') for arg in args):
                        after = 'after=next' in args
                        return {'data': {'search': connection([later] if after else [first], total=2,
                                                              more=not after, cursor=None if after else 'next')}}
                    return snapshot_data(args)
                client = self.client(api)
                github = {'prs': [], 'issues': [], 'activity': []}
                branches = [branch()] if same_head else [branch(), branch(sha=second_sha)]
                client.associate_branches(branches, [repository()], github)
                self.assertEqual(len(github['pr_index']), 1)
                self.assertEqual(len(github['pr_index_observations']), 1)
                self.assertEqual(github['pr_index'][0]['url'], first['url'])
                self.assertEqual(github['pr_index_observations'][0]['url'], later['url'])
                self.assertTrue(all(len(row['prs']) == 1 for row in branches))
                self.assertEqual(branches[0]['prs'][0]['state'], 'open')
                if not same_head:
                    self.assertEqual(branches[1]['prs'][0]['state'], 'closed')
                self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')
                data = {'metadata': {'github_host': 'github.com', 'github_user': 'me',
                    'github_status': 'partial', 'warnings': ['Native paging drift'],
                    'github_discovery': client.coverage}, 'github': github, 'branches': branches}
                validate_github(data, {}, START, END)
                duplicate = copy.deepcopy(data)
                duplicate['github']['pr_index'].append(duplicate['github']['pr_index_observations'][0])
                with self.assertRaisesRegex(ValueError, 'Duplicate indexed'):
                    validate_github(duplicate, {}, START, END)
                duplicate = copy.deepcopy(data)
                duplicate['branches'][0]['prs'].append(copy.deepcopy(duplicate['branches'][0]['prs'][0]))
                with self.assertRaisesRegex(ValueError, 'Duplicate branch'):
                    validate_github(duplicate, {}, START, END)
                if not same_head:
                    forged = copy.deepcopy(data)
                    forged['branches'][0]['prs'][0].update(repo='org/repo', url=later['url'], state='closed')
                    with self.assertRaises(ValueError):
                        validate_github(forged, {}, START, END)

    def test_snapshot_case_drift_retains_alternate_and_partial_one_branch_edge(self):
        first = gql(repo='ORG/REPO')
        later = gql(repo='org/repo', state='CLOSED')
        def api(args):
            result = snapshot_data(args)
            result['data']['s0'] = connection([later] if 'after:' in query_text(args) else [first],
                total=2, more='after:' not in query_text(args), cursor='next')
            return result
        client, branches, github = self.associate(api)
        self.assertEqual(len(github['pr_index']), 1)
        self.assertEqual(len(github['pr_index_observations']), 1)
        self.assertEqual(len(branches[0]['prs']), 1)
        self.assertEqual(branches[0]['prs'][0]['state'], 'open')
        self.assertEqual(client.coverage['snapshot:org/repo:' + SHA]['status'], 'partial')

    def test_case_variant_historical_head_urls_preserve_exact_native_event_proof(self):
        from extended_evidence import historical_head_hashes, same_github_api_repo_url
        item = graphql_item(gql(repo='ORG/REPO'), 'github.com')
        event = {'id': 42, 'node_id': 'native-rest-42', 'event': 'head_ref_force_pushed',
                 'created_at': '2025-01-01T00:00:00Z', 'commit_id': SHA,
                 'url': 'https://api.github.com/repos/org/repo/issues/events/42'}
        client = self.client(lambda args: [])
        github = {}
        self.assertTrue(client._retain_head_event(github, item, event, 'rest_force_push',
                                                 'test', 'repos/ORG/REPO/issues/1/timeline'))
        record = github['head_events'][0]
        self.assertEqual(record['source'], event)
        self.assertEqual(historical_head_hashes(record, {item['url']: item}), {SHA})
        for bad in [event['url'].replace('/org/repo/', '/other/repo/'), event['url'][:-2] + '43',
                    event['url'].replace('/issues/events/', '/ISSUES/events/'),
                    event['url'] + '#42', event['url'] + '?page=1']:
            altered = copy.deepcopy(record)
            altered['source']['url'] = bad
            with self.assertRaises(ValueError):
                historical_head_hashes(altered, {item['url']: item})
        graph = self.head_node()
        graph['pullRequest']['url'] = item['url'].lower()
        self.assertTrue(client._retain_head_event(github, item, graph, 'graphql_force_push', 'test'))
        self.assertEqual(historical_head_hashes(github['head_events'][1], {item['url']: item}), {SHA, 'b' * 40})
        self.assertTrue(same_github_api_repo_url(
            'https://ghe.example.test/api/v3/repos/org/repo/issues/events/42',
            'https://ghe.example.test/api/v3/repos/ORG/REPO/issues/events/42'))
        self.assertFalse(same_github_api_repo_url(
            'https://ghe.example.test/api/v3/repos/org/wrong/issues/events/42',
            'https://ghe.example.test/api/v3/repos/ORG/REPO/issues/events/42'))

    def test_alias_metadata_case_drift_keeps_ids_and_same_row_html_name_binding(self):
        from extended_evidence import repository_aliases
        from work_links import referenced_keys
        current = rest_item({**rest(), 'body': 'PROJ-123'}, 'prs', 'github.com')
        def api(args):
            if args[2] == 'repos/old/repo/issues/1':
                return {**rest(), 'body': 'TASK-124'}
            if args[2] in {'repos/old/repo', 'repos/org/repo'}:
                return {'id': 7, 'full_name': 'Org/Repo', 'html_url': 'https://github.com/Org/Repo'}
            self.fail('No repeated activity reads: ' + args[2])
        client = self.client(api)
        github = {'prs': [current], 'issues': []}
        client.add_linked_prs(github, ['https://github.com/old/repo/pull/1'])
        self.assertEqual(set(referenced_keys(github)), {'PROJ-123', 'TASK-124'})
        alias = github['repository_aliases'][0]
        self.assertEqual(alias['requested']['full_name'], 'Org/Repo')
        self.assertEqual(alias['canonical']['html_url'], 'https://github.com/Org/Repo')
        self.assertEqual(repository_aliases(github, 'github.com'), {('github.com', 'old/repo'): 'org/repo'})
        for mutation in ('id', 'full_name', 'html_url'):
            altered = copy.deepcopy(github)
            altered['repository_aliases'][0]['canonical'][mutation] = {
                'id': 8, 'full_name': 'other/repo', 'html_url': 'https://github.com/other/repo'}[mutation]
            with self.assertRaises(ValueError):
                repository_aliases(altered, 'github.com')
        self.assertEqual(client.warnings, [])

    def test_redirect_repair_matches_later_canonical_index_head_observation(self):
        first = gql(repo='new/repo', sha='b' * 40, head='unrelated')
        later = gql(repo='new/repo', sha=SHA, head='unrelated')
        def api(args):
            if args[0] != 'graphql':
                return {'id': 7, 'full_name': 'new/repo', 'html_url': 'https://github.com/new/repo'}
            if any(arg.startswith('q=is:pr repo:') for arg in args):
                if 'q=is:pr repo:old/repo author:me sort:created-asc' in args:
                    return {'data': {'search': connection()}}
                after = 'after=next' in args
                return {'data': {'search': connection([later] if after else [first], total=2,
                                                      more=not after, cursor=None if after else 'next')}}
            result = snapshot_data(args)
            for alias in result['data']:
                if alias.startswith('c'):
                    result['data'][alias] = {'id': 'native-repo', 'nameWithOwner': 'new/repo', 'object': None}
            return result
        client = self.client(api)
        github = {'prs': [], 'issues': [], 'activity': []}
        branches = [branch()]
        client.associate_branches(branches, [repository(remotes=('old/repo',))], github)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'exact_head_or_merge_sha')
        self.assertEqual(github['pr_index'][0]['head_sha'], 'b' * 40)
        self.assertEqual(github['pr_index_observations'][0]['head_sha'], SHA)
        self.assertEqual(client.coverage['pr_index:new/repo']['status'], 'partial')

    def test_role_snapshots_keep_pr_and_issue_reference_frontiers_without_duplicate_activity(self):
        from work_links import associate_work, complete_references, referenced_keys, validate_links
        from provider_references import gitlab_seed_urls
        reads = []
        def api(args):
            if 'search/issues' in args:
                query = next(arg[2:] for arg in args if arg.startswith('q='))
                kind = 'pr' if query.startswith('is:pr ') else 'issue'
                # Several roles return the same later observation; retain it once.
                later = 'updated:' in query
                key = ('TASK-124' if kind == 'pr' else 'TASK-126') if later else (
                    'PROJ-123' if kind == 'pr' else 'PROJ-125')
                item = {**rest(), 'body': f'Implements {key}; https://gitlab.example.test/team/app/-/merge_requests/7',
                        'created_at': '2026-10-10T00:00:00Z', 'user': {'login': 'me'}}
                if kind == 'issue':
                    item.pop('pull_request')
                    item['number'] = 2
                    item['html_url'] = 'https://github.com/org/repo/issues/2'
                return {'total_count': 1, 'incomplete_results': False, 'items': [item]}
            if len(args) > 2:
                reads.append(args[2])
            return []
        client = self.client(api)
        github = {'prs': [], 'issues': []}
        client.discover(github)
        github['pr_index'] = [{**github['context_prs'][0], 'repo': 'ORG/REPO',
            'url': 'https://github.com/ORG/REPO/pull/1',
            'body': 'Implements INDEX-127; https://gitlab.example.test/team/app/-/merge_requests/8'}]
        self.assertEqual(len(github['context_observations']), 2)
        self.assertEqual(set(referenced_keys(github)),
                         {'PROJ-123', 'PROJ-125', 'TASK-124', 'TASK-126', 'INDEX-127'})
        self.assertEqual(len(github['activity']), 2)
        self.assertEqual(len(reads), len(set(reads)))
        self.assertEqual(set(github['context_prs'][0]['roles']),
                         {'open_authored_prs', 'open_assigned_prs', 'requested_reviews',
                          'recent_authored_prs', 'recent_reviews', 'recent_comments', 'recent_involvement'})
        self.assertEqual(gitlab_seed_urls({'issues': []}, github, []),
                         {'https://gitlab.example.test/team/app/-/merge_requests/7',
                          'https://gitlab.example.test/team/app/-/merge_requests/8'})
        jira = {'metadata': {'site': 'jira.example.test'}, 'issues': []}
        attempted = []
        def tickets(keys):
            attempted.extend(keys)
            jira['issues'].extend({'key': key, 'url': 'https://jira.example.test/browse/' + key,
                                   'sensitive': False, 'detail': {'fields': {}}} for key in keys)
        complete_references(jira, github, tickets)
        self.assertEqual(set(attempted), {'PROJ-123', 'PROJ-125', 'TASK-124', 'TASK-126', 'INDEX-127'})
        associate_work(jira, github)
        validate_links({'metadata': {'github_host': 'github.com'}, 'jira': jira, 'github': github})
        self.assertEqual({issue['key'] for issue in jira['issues'] if issue['pr_links']},
                         {'PROJ-123', 'TASK-124', 'INDEX-127'})

    def test_jira_redirect_retains_new_canonical_payload_without_rereading_activity(self):
        from work_links import referenced_keys
        from extended_evidence import validate_github
        current = rest_item({**rest(repo='org/Repo'), 'body': 'Implements PROJ-123'}, 'prs', 'github.com')
        def api(args):
            route = args[2]
            if route == 'repos/old/repo/issues/1':
                return {**rest(), 'body': 'Implements TASK-124'}
            if route in {'repos/old/repo', 'repos/org/repo'}:
                return {'id': 7, 'full_name': 'org/repo', 'html_url': 'https://github.com/org/repo'}
            self.fail('An already inspected canonical PR must not reread activity: ' + route)
        client = self.client(api)
        github = {'prs': [current], 'issues': []}
        client.add_linked_prs(github, ['https://github.com/old/repo/pull/1'])
        self.assertEqual(set(referenced_keys(github)), {'PROJ-123', 'TASK-124'})
        self.assertEqual(len(github['context_observations']), 1)
        self.assertEqual(current['roles'], ['jira_link'])
        self.assertEqual(github['repository_aliases'][0]['requested_repo'], 'old/repo')
        github['pr_index'] = [{**current, 'repo': 'old/repo',
                               'url': 'https://github.com/old/repo/pull/1',
                               'body': 'Implements INDEX-127'}]
        self.assertEqual(set(referenced_keys(github)), {'PROJ-123', 'TASK-124', 'INDEX-127'})
        # Native repository capitalization can differ between observations;
        # retain its exact URL while binding the same host/repository/number.
        validate_github({'metadata': {'github_host': 'github.com'}, 'github': github},
                        {current['url']: current}, START, END)

    def test_index_duplicate_page_preserves_changed_body_and_head_with_partial_coverage(self):
        first = {**gql(sha='b' * 40), 'body': 'Implements PROJ-123'}
        later = {**gql(sha=SHA), 'body': 'Implements TASK-124'}
        def api(args):
            if 'q=is:pr repo:org/repo author:me sort:created-asc' in args:
                after = 'after=next' in args
                return {'data': {'search': connection([later] if after else [first], total=2,
                                                      more=not after, cursor=None if after else 'next')}}
            return snapshot_data(args)
        client = self.client(api)
        github = {'prs': [], 'issues': [], 'activity': []}
        branches = [branch()]
        client.associate_branches(branches, [repository()], github)
        self.assertEqual(github['pr_index'][0]['body'], 'Implements PROJ-123')
        self.assertEqual(github['pr_index_observations'][0]['body'], 'Implements TASK-124')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'exact_head_or_merge_sha')
        self.assertEqual(client.coverage['pr_index:org/repo']['status'], 'partial')

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
                        return native_comment_response(args, proof, event)
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
                            return native_comment_response(args, native, event)
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
            if 'timelineItems(' in query_text(args):
                return {'data': {'p0': {'pullRequest': {'url': indexed['url'], 'number': 8,
                    'repository': {'nameWithOwner': 'canonical/repo'}, 'timelineItems': {
                        'totalCount': 9, 'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}}}
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
        self.assertTrue(all(info.get('global_search', info)['count'] == 0
                            for info in branches[0]['association_lookups']))

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
                        return native_comment_response(args, native, event)
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
                return native_comment_response(args, native, event)
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
        self.assertEqual(branches[0]['pr_status'], 'unsupported')
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'ok')
        self.assertEqual(client.coverage['global_snapshot:' + SHA]['status'], 'ok')
        self.assertEqual(client.coverage['unrouted:/local/repo']['status'], 'unsupported')
        self.assertEqual(client.coverage['unrouted:/local/repo']['scope'], 'github_global_without_repository')
        self.assertFalse(client.warnings)

    def test_failed_global_read_remains_partial_despite_unsupported_repository_scope(self):
        def api(args):
            raise subprocess.CalledProcessError(1, list(args), stderr='native global read unavailable')
        client, branches, _ = self.associate(api, repos=[repository(remotes=())])
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(client.coverage['global_snapshot:' + SHA]['status'], 'partial')
        self.assertEqual(client.coverage['unrouted:/local/repo']['status'], 'unsupported')
        self.assertTrue(client.warnings)

    def test_unrouted_classification_preserves_fixture_and_real_local_work_without_path_filters(self):
        def api(args):
            return {'data': {'s0': connection()}}
        paths = ['/work/backup', '/work/implementation-fixtures/network-controller']
        inputs = [branch(path) for path in paths]
        _, branches, _ = self.associate(api, branches=inputs, repos=[repository(path, remotes=()) for path in paths])
        self.assertEqual([row['repo'] for row in branches], paths)
        self.assertEqual([row['pr_status'] for row in branches], ['unsupported', 'unsupported'])
        self.assertTrue(all(row['association_lookups'][0]['status'] == 'ok' for row in branches))
        incomplete = copy.deepcopy(branches[0])
        incomplete['association_lookups'] = []
        self.assertEqual(branch_association_status(incomplete), 'partial')
        incomplete['association_lookups'] = [{'repo': 'unrelated/repository', 'status': 'ok'}]
        self.assertEqual(branch_association_status(incomplete), 'partial')

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

    def test_historical_before_and_after_match_all_local_copies_without_weekly_credit(self):
        native = self.head_node()
        client = self.client(lambda args: self.head_result([native]))
        data = {'pr_index': [graphql_item(gql(), 'github.com')], 'activity': []}
        branches = [branch('/local-only'), branch('/foreign-provider', sha='b' * 40), branch('/other-copy')]
        client._head_history_batch(data, data['pr_index'])
        client.associate_head_events(branches, data)
        self.assertTrue(all(b['pr_status'] == 'matched' for b in branches))
        self.assertTrue(all(b['prs'][0]['proof'] == 'historical_head_event' for b in branches))
        self.assertTrue(all(b['prs'][0]['head_event_id'] == native['id'] for b in branches))
        self.assertEqual(data['head_events'][0]['source'], native)
        self.assertEqual(data['activity'], [])
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'ok')
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['unfiltered_total_count'], 999)
        client._head_history_batch(data, data['pr_index'])
        self.assertEqual(len(client.calls), 1)

    def test_historical_paging_failure_retains_first_page_positive(self):
        def api(args):
            if 'after:' in query_text(args):
                raise subprocess.CalledProcessError(1, 'gh')
            return self.head_result([self.head_node()], more=True, cursor='next')
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        branches = [branch()]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertEqual(len(data['head_events']), 1)
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')
        self.assertIn('after:"next"', query_text(client.calls[1]))

    def test_repeated_history_cursor_retains_both_pages_and_marks_incomplete(self):
        def api(args):
            later = 'after:' in query_text(args)
            return self.head_result([self.head_node(identity='event2' if later else 'event1')],
                                    more=True, cursor='same')
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(len(data['head_events']), 2)
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')

    def test_new_context_rest_head_lead_expands_history_once_for_all_snapshots(self):
        event = {'id': 42, 'node_id': 'native-rest-42', 'event': 'head_ref_force_pushed',
                 'actor': {'login': 'other'}, 'created_at': '2025-01-01T00:00:00Z',
                 'commit_id': SHA, 'commit_url': 'https://api.github.com/repos/org/repo/commits/' + SHA,
                 'url': 'https://api.github.com/repos/org/repo/issues/events/42'}
        def api(args):
            if args[0] == 'graphql':
                return self.head_result([self.head_node()])
            return [event] if args[2].endswith('/timeline') else []
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')], 'activity': []}
        branches = [branch('/old'), branch('/new', sha='b' * 40)]
        client._branch_snapshots = branches
        client._inspect_activity(data, [rest_item(rest(), 'prs', 'github.com')])
        self.assertTrue(all(b['pr_status'] == 'matched' for b in branches))
        self.assertEqual(branches[1]['prs'][0]['head_event_id'], 'FP_native')
        before = len(client.calls)
        client._inspect_activity(data, [rest_item(rest(), 'prs', 'github.com')])
        self.assertEqual(len(client.calls), before)
        self.assertEqual(data['activity'], [])

    def test_later_readable_duplicate_head_observation_retains_new_sha_positive(self):
        def api(args):
            later = 'after:' in query_text(args)
            return self.head_result([self.head_node(before=SHA if later else None)],
                                    more=not later, cursor='next' if not later else None)
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        branches = [branch(), branch('/second', sha='b' * 40)]
        client.associate_head_events(branches, data)
        self.assertTrue(all(b['pr_status'] == 'matched' for b in branches))
        self.assertEqual(len(data['head_events']), 2)
        self.assertIsNone(data['head_events'][0]['source']['beforeCommit'])
        self.assertEqual(data['head_events'][1]['source']['beforeCommit']['oid'], SHA)
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')

    def test_null_historical_object_retains_known_sibling_and_gap(self):
        native = self.head_node(before=None)
        client = self.client(lambda args: self.head_result([native]))
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        branches = [branch(sha='b' * 40)]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['pr_status'], 'matched')
        self.assertIsNone(data['head_events'][0]['source']['beforeCommit'])
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')

    def test_wrong_historical_target_type_and_sha_never_create_edges(self):
        for field, value in [('pullRequest', {'url': gql(2)['url']}),
                             ('__typename', 'IssueComment'), ('beforeCommit', {'oid': 'not-sha'})]:
            with self.subTest(field=field):
                native = self.head_node()
                native[field] = value
                client = self.client(lambda args: self.head_result([native]))
                data = {'pr_index': [graphql_item(gql(), 'github.com')]}
                client._head_history_batch(data, data['pr_index'])
                branches = [branch()]
                client.associate_head_events(branches, data)
                self.assertEqual(branches[0]['prs'], [])
                self.assertEqual(data.get('head_events', []), [])
                self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')

    def test_rest_old_other_actor_head_context_has_native_route_binding(self):
        event = {'id': 42, 'node_id': 'native-rest-42', 'event': 'head_ref_force_pushed',
                 'actor': {'login': 'other'}, 'created_at': '2025-01-01T00:00:00Z',
                 'commit_id': SHA, 'commit_url': 'https://api.github.com/repos/org/repo/commits/' + SHA,
                 'url': 'https://api.github.com/repos/org/repo/issues/events/42'}
        def api(args):
            return [event] if args[2].endswith('/timeline') else []
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')], 'activity': []}
        client._inspect_activity(data, [rest_item(rest(), 'prs', 'github.com')])
        branches = [branch()]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['prs'][0]['proof'], 'historical_head_event')
        self.assertEqual(data['head_events'][0]['source_path'], 'repos/org/repo/issues/1/timeline')
        self.assertEqual(data['activity'], [])
        for field, value in [('url', 'https://api.github.com/repos/wrong/repo/issues/events/42'),
                             ('node_id', None)]:
            bad = dict(event, **{field: value})
            self.assertFalse(client._retain_head_event({}, rest_item(rest(), 'prs', 'github.com'),
                bad, 'rest_force_push', 'test', 'repos/org/repo/issues/1/timeline'))
        bad = dict(event)
        del bad['commit_id']
        retained = {}
        self.assertFalse(client._retain_head_event(retained, rest_item(rest(), 'prs', 'github.com'),
            bad, 'rest_force_push', 'missing-head', 'repos/org/repo/issues/1/timeline'))
        self.assertEqual(retained, {})
        self.assertEqual(client.coverage['missing-head']['status'], 'partial')

    def test_historical_positive_requires_real_native_metadata_when_not_indexed(self):
        client = self.client(lambda args: {'data': {'repository': {'pullRequest': gql(state='CLOSED')}}})
        data = {'head_events': [{'item_url': gql()['url'], 'repo': 'org/repo', 'number': 1,
                                'kind': 'graphql_force_push', 'source': self.head_node()}]}
        branches = [branch()]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['prs'][0]['state'], 'closed')
        self.assertEqual(data['pr_index'][0]['head_sha'], SHA)
        self.assertIn(PR_FIELDS, query_text(client.calls[0]))

    def test_exact_index_positive_does_not_skip_other_pr_sha_associations(self):
        indexed = graphql_item(gql(), 'github.com')
        def api(args):
            return snapshot_data(args, lambda i, repo, data: data.update({'s' + i: connection([gql(2)])}))
        _, branches, _ = self.associate(api, index={'org/repo': [indexed]})
        self.assertEqual({pr['number'] for pr in branches[0]['prs']}, {1, 2})
        self.assertEqual(branches[0]['prs'][0]['proof'], 'exact_head_or_merge_sha')

    def test_cached_global_positive_shared_to_routed_snapshot_after_successful_narrow_search(self):
        def api(args):
            if 'query:"is:pr ' in query_text(args):
                return {'data': {'s0': connection([gql(2, repo='other/upstream')])}}
            return snapshot_data(args)
        _, branches, _ = self.associate(api, branches=[branch('/routed'), branch('/local')],
            repos=[repository('/routed'), repository('/local', ())])
        self.assertTrue(all(b['prs'][0]['number'] == 2 for b in branches))
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'ok')
        self.assertEqual(branches[0]['prs'][0]['proof'], 'global_any_author_sha_search')

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

            for alias in result['data']:
                if alias.startswith('s'):
                    result['data'][alias] = connection([gql(sha='b' * 40, state='CLOSED')])
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
        def api(args):
            return self.head_result([]) if 'HEAD_REF_FORCE_PUSHED_EVENT' in query_text(args) else snapshot_data(args)
        client, branches, _ = self.associate(api,
            branches=[branch(refs=['refs/remotes/upstream/feature'])], index={'org/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'related_only')
        self.assertEqual(branches[0]['prs'], [])
        self.assertEqual(branches[0]['related_prs'][0]['proof'], 'head_name_only')
        self.assertTrue(all(info['status'] == 'ok' for info in branches[0]['association_lookups']))

    def test_branch_name_only_with_failed_native_lookup_remains_partial(self):
        from github_sources import graphql_item
        indexed = graphql_item(gql(sha='b' * 40, head='feature'), 'github.com')
        def api(args):
            if 'HEAD_REF_FORCE_PUSHED_EVENT' in query_text(args):
                return self.head_result([])
            result = snapshot_data(args)
            result['errors'] = [{'path': ['s0'], 'message': 'Native SHA search unavailable'}]
            return result
        client, branches, _ = self.associate(api,
            branches=[branch(refs=['refs/remotes/upstream/feature'])], index={'org/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertEqual(branches[0]['prs'], [])
        self.assertEqual(branches[0]['related_prs'][0]['proof'], 'head_name_only')
        self.assertTrue(any(info['status'] == 'partial' for info in branches[0]['association_lookups']))
        self.assertTrue(client.warnings)

    def test_branch_name_only_with_failed_native_history_remains_partial(self):
        from github_sources import branch_association_status, graphql_item
        indexed = graphql_item(gql(sha='b' * 40, head='feature'), 'github.com')
        def api(args):
            if 'HEAD_REF_FORCE_PUSHED_EVENT' in query_text(args):
                raise subprocess.CalledProcessError(1, 'gh')
            return snapshot_data(args)
        client, branches, _ = self.associate(api,
            branches=[branch(refs=['refs/remotes/upstream/feature'])], index={'org/repo': [indexed]})
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertTrue(all(info['status'] == 'ok' for info in branches[0]['association_lookups']))
        self.assertEqual(client.coverage['head_history:' + indexed['url']]['status'], 'partial')
        # An unrelated PR's failed history must not contaminate this candidate.
        self.assertEqual(branch_association_status(branches[0], {
            'head_history:https://github.com/elsewhere/project/pull/99': {'status': 'partial'}}), 'related_only')

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
        self.assertEqual(sorted(query_text(call).count(':search(') for call in client.calls), [1, 20])


class ReviewContextTest(unittest.TestCase):
    client = GithubSourcesTest.client

    @staticmethod
    def native_review(body='Review context', identity=7):
        return {'id': identity, 'node_id': 'REVIEW' + str(identity), 'user': {'login': 'other', 'id': 123},
                'html_url': gql()['url'] + '#pullrequestreview-' + str(identity), 'body': body,
                'state': 'COMMENTED', 'submitted_at': '2025-01-01T00:00:00Z', 'commit_id': SHA,
                'pull_request_url': 'https://api.github.com/repos/org/repo/pulls/1'}

    def test_other_author_review_only_references_reach_jira_and_gitlab_without_actor_credit(self):
        from extended_evidence import github_reference_comments, validate_github
        from provider_references import gitlab_seed_urls
        from work_links import associate_work, referenced_keys, validate_links
        mr = 'https://code.company.test/team/project/-/merge_requests/7'
        review = self.native_review('https://jira.example/browse/CORE-123 ' + mr)
        item = rest_item(rest(), 'prs', 'github.com')
        client = self.client(lambda args: [review] if args[2].endswith('/reviews') else [])
        data = {'prs': [item], 'issues': [], 'activity': []}
        client._inspect_activity(data, [item])
        self.assertEqual(data['reference_reviews'][0]['source'], review)
        self.assertEqual(data['activity'], [])
        self.assertEqual(referenced_keys(data, 'jira.example'), ['CORE-123'])
        jira = {'issues': [{'key': 'CORE-123', 'url': 'https://jira.example/browse/CORE-123', 'sensitive': False}],
                'metadata': {'site': 'jira.example'}}
        self.assertEqual(gitlab_seed_urls(jira, data, []), {mr})
        common = list(github_reference_comments(data))[0]
        self.assertEqual(common['kind'], 'review')
        self.assertNotIn('created_at', common)
        self.assertNotIn('updated_at', common)
        source = {'github': data, 'jira': jira, 'metadata': {'github_user': 'me'}}
        validate_github(source, {item['url']: item}, START, END)
        associate_work(jira, data)
        validate_links(source)
        self.assertEqual(jira['issues'][0]['pr_links'][0]['evidence_url'], review['html_url'])
        self.assertEqual(client.warnings, [])

    def test_exact_duplicate_review_deduplicates_and_changed_native_body_survives(self):
        from extended_evidence import github_reference_comments, validate_github
        client = self.client(lambda args: [])
        item = graphql_item(gql(), 'github.com')
        first, second = self.native_review('First native body'), self.native_review('Later native body')
        data = {'pr_index': [item], 'activity': []}
        for review in (first, copy.deepcopy(first), second):
            client._retain_review(data, item, review, 'reviews')
        self.assertEqual(len(data['reference_reviews']), 2)
        self.assertEqual([row['body'] for row in github_reference_comments(data)],
                         ['First native body', 'Later native body'])
        validate_github({'github': data, 'metadata': {'github_user': 'me'}}, {}, START, END)
        forged = copy.deepcopy(data)
        forged['reference_reviews'].append(copy.deepcopy(forged['reference_reviews'][0]))
        with self.assertRaises(ValueError):
            validate_github({'github': forged, 'metadata': {'github_user': 'me'}}, {}, START, END)

    def test_invalid_review_routes_and_observations_never_enter_reference_frontier(self):
        item = graphql_item(gql(), 'github.com')
        for field, value in [('html_url', gql(2)['url'] + '#pullrequestreview-7'),
                             ('pull_request_url', 'https://api.github.com/repos/wrong/repo/pulls/1'),
                             ('id', True), ('body', {}), ('user', {'login': ''}),
                             ('state', 'SHIP_IT'), ('submitted_at', 'not a date'), ('node_id', '')]:
            with self.subTest(field=field):
                review = self.native_review()
                review[field] = value
                client, data = self.client(lambda args: []), {}
                client._retain_review(data, item, review, 'reviews')
                self.assertFalse(data.get('reference_reviews'))
                self.assertEqual(client.coverage['reviews']['status'], 'partial')
                self.assertTrue(client.warnings)

    def test_fallback_reviewed_event_and_pending_null_submission_remain_native_context(self):
        from extended_evidence import github_reference_comments, validate_github
        review = self.native_review('Native timeline review')
        review.update(event='reviewed', state='commented')
        item = graphql_item(gql(), 'github.com')
        client = self.client(lambda args: [review])
        data = {'pr_index': [item], 'activity': []}
        client._rest_head_history(data, item)
        pending = self.native_review(None, identity=8)
        pending.update(user=None, state='PENDING', submitted_at=None)
        client._retain_review(data, item, pending, 'pending')
        self.assertEqual([row['source'] for row in data['reference_reviews']], [review, pending])
        self.assertIsNone(list(github_reference_comments(data))[1]['submitted_at'])
        self.assertEqual(data['activity'], [])
        validate_github({'github': data, 'metadata': {'github_user': 'me'}}, {}, START, END)
        self.assertEqual(client.warnings, [])

    def test_ordinary_timeline_retains_distinct_review_snapshot_without_actor_credit(self):
        from extended_evidence import github_reference_comments, validate_github
        from work_links import referenced_keys
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
        validate_github({'github': data, 'metadata': {'github_user': 'me'}}, {item['url']: item}, START, END)
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

    def test_public_native_full_before_after_resolves_only_the_null_projection_gap(self):
        from test_github_timeline import event_html, page, WHEN
        for other_graph_gap in (False, True):
            with self.subTest(other_graph_gap=other_graph_gap):
                graph = self.head_node(before=None, after=None, identity='NATIVE42')
                graph['createdAt'] = WHEN
                native = self.rest_head()
                native['created_at'] = WHEN
                result = self.head_result([graph])
                if other_graph_gap:
                    result['errors'] = [{'path': ['p0', 'pullRequest', 'timelineItems'], 'message': 'Native partial graph'}]
                client = self.client(lambda args: result if args[0] == 'graphql' else [native])
                reads = []
                def read(url):
                    reads.append(url)
                    return page(event_html(after='c' * 40, page_url=url), url)
                client.timeline_read = read
                data = {'pr_index': [graphql_item(gql(), 'github.com')], 'activity': []}
                client._head_history_batch(data, data['pr_index'])
                info = client.coverage['head_history:' + gql()['url']]
                self.assertEqual(info['status'], 'partial' if other_graph_gap else 'ok')
                self.assertEqual(info['projection_status'], 'partial')
                self.assertEqual(info['fallback'], 'public_native_timeline')
                self.assertEqual(data['timeline_html'][0]['status'], 'ok')
                self.assertEqual(data['head_events'][-1]['kind'], 'html_force_push')
                branches = [branch(sha='a' * 40), branch('/after', sha='c' * 40)]
                client.associate_head_events(branches, data)
                self.assertTrue(all(row['pr_status'] == 'matched' for row in branches))
                self.assertEqual(data['activity'], [])
                self.assertEqual(bool(client.warnings), other_graph_gap)
                client._head_history_batch(data, data['pr_index'])
                self.assertEqual(reads, [gql()['url']])

    def test_native_rest_after_hash_recovers_null_graph_object_without_clearing_before_gap(self):
        graph = self.head_node(before=None, after=None, identity='NATIVE42')
        native = self.rest_head()
        comment = {'event': 'commented', 'id': 43, 'node_id': 'COMMENT43', 'user': {'login': 'other'},
                   'html_url': gql()['url'] + '#issuecomment-43',
                   'body': 'Context https://jira.example/browse/CORE-123',
                   'created_at': '2025-01-01T00:00:00Z', 'updated_at': '2025-01-01T00:00:00Z'}
        client = self.client(lambda args: self.head_result([graph]) if args[0] == 'graphql' else [native, comment])
        data = {'pr_index': [graphql_item(gql(), 'github.com')], 'activity': []}
        client._head_history_batch(data, data['pr_index'])
        branches = [branch(sha='c' * 40)]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['prs'][0]['head_event_id'], 42)
        self.assertEqual(branches[0]['prs'][0]['proof'], 'historical_head_event')
        self.assertEqual(data['head_events'][0]['source'], graph)
        self.assertIsNone(data['head_events'][0]['source']['beforeCommit'])
        self.assertIsNone(data['head_events'][0]['source']['afterCommit'])
        self.assertEqual(data['head_events'][1]['source'], native)
        self.assertEqual(data['head_events'][1]['source_path'], 'repos/org/repo/issues/1/timeline')
        self.assertEqual(data['reference_comments'][0]['body'], comment['body'])
        self.assertEqual(data['activity'], [])
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')
        self.assertEqual(client.coverage['rest_head_history:' + gql()['url']]['status'], 'ok')
        self.assertIn('page=1', client.calls[1])
        client._head_history_batch(data, data['pr_index'])
        client._rest_head_history(data, data['pr_index'][0])
        self.assertEqual(len(client.calls), 2)

    def test_rest_pagination_failure_keeps_earlier_hash_and_coverage_gap(self):
        for fail in (False, True):
            def api(args):
                if args[0] == 'graphql':
                    return self.head_result([self.head_node(before=None, after=None)])
                if 'page=1' in args:
                    return [self.rest_head()] + [{'event': 'labeled'} for _ in range(99)]
                self.assertIn('page=2', args)
                if fail:
                    raise subprocess.CalledProcessError(403, 'native read')
                return [self.rest_head(44, 'd' * 40)]
            with self.subTest(fail=fail):
                client = self.client(api)
                data = {'pr_index': [graphql_item(gql(), 'github.com')]}
                client._head_history_batch(data, data['pr_index'])
                branches = [branch(sha='c' * 40), branch('/second', sha='d' * 40)]
                client.associate_head_events(branches, data)
                self.assertEqual(branches[0]['pr_status'], 'matched')
                self.assertEqual(branches[1]['pr_status'], 'unchecked' if fail else 'matched')
                info = client.coverage['rest_head_history:' + gql()['url']]
                self.assertEqual(info['status'], 'partial' if fail else 'ok')
                self.assertEqual(info['pages'], 1 if fail else 2)

    def test_wrong_rest_route_cannot_manufacture_recovery(self):
        event = self.rest_head()
        event['url'] = 'https://api.github.com/repos/wrong/repo/issues/events/42'
        client = self.client(lambda args: self.head_result([self.head_node(before=None, after=None)])
                             if args[0] == 'graphql' else [event])
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        branches = [branch(sha='c' * 40)]
        client.associate_head_events(branches, data)
        self.assertEqual(branches[0]['prs'], [])
        self.assertEqual(len(data['head_events']), 1)
        self.assertEqual(client.coverage['rest_head_history:' + gql()['url']]['status'], 'partial')

    def test_missing_before_reads_bounded_native_routes_without_clearing_an_unresolved_gap(self):
        def api(args):
            if args[0] == 'graphql':
                return self.head_result([self.head_node(before=None)])
            self.assertIn('repos/org/repo/issues/1/timeline', args)
            return []
        client = self.client(api)
        data = {'pr_index': [graphql_item(gql(), 'github.com')]}
        client._head_history_batch(data, data['pr_index'])
        self.assertEqual(len(client.calls), 2)
        self.assertEqual(client.coverage['head_history:' + gql()['url']]['status'], 'partial')


class SnapshotConcurrencyTest(unittest.TestCase):
    client = GithubSourcesTest.client
    associate = GithubSourcesTest.associate

    def test_four_native_reads_overlap_but_results_and_warnings_apply_in_source_order(self):
        from threading import Barrier, Event, Lock, get_ident
        barrier, lock = Barrier(4), Lock()
        done = [Event() for _ in range(4)]
        active, maximum, completed = 0, 0, []
        owner, warning_threads = get_ident(), []
        def api(args):
            nonlocal active, maximum
            number = int(re.search(r'is:pr ([0-9a-f]{40})', query_text(args))[1], 16)
            with lock:
                active += 1
                maximum = max(maximum, active)
            try:
                if number <= 4:
                    barrier.wait(timeout=3)
                    if number < 4:
                        self.assertTrue(done[number].wait(timeout=3))
                result = snapshot_data(args)
                result['data']['s0'] = connection([gql(number)])
                result['errors'] = [{'path': ['s0'], 'message': 'partial native sibling'}]
                with lock:
                    completed.append(number)
                return result
            finally:
                with lock:
                    active -= 1
                if number <= 4:
                    done[number - 1].set()
        client = self.client(api)
        client.authored_index = lambda repo: []
        original_warn = client.warn
        client.warn = lambda warning: (warning_threads.append(get_ident()), original_warn(warning))
        branches = [branch(sha=f'{number:040x}') for number in range(1, 9)]
        data = {}
        with patch('github_sources.BATCH_SIZE', 1):
            client.associate_branches(branches, [repository()], data)
        self.assertEqual(maximum, 4)
        self.assertEqual(completed[:4], [4, 3, 2, 1])
        self.assertEqual([row['number'] for row in data['pr_index']], list(range(1, 9)))
        self.assertEqual(len(client.warnings), 8)
        self.assertEqual([warning.split(':')[-2] for warning in client.warnings], [f'{number:040x}' for number in range(1, 9)])
        self.assertTrue(all(thread == owner for thread in warning_threads))
        self.assertTrue(all(row['pr_status'] == 'matched' and row['association_lookups'][0]['status'] == 'partial'
                            for row in branches))

    def test_parallel_and_serial_results_preserve_partial_positives_identically(self):
        def api(args):
            number = int(re.search(r'is:pr ([0-9a-f]{40})', query_text(args))[1], 16)
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql(number)])
            result['errors'] = [{'path': ['s0'], 'message': 'native partial result'}]
            return result
        outcomes = []
        for workers in (1, 4):
            with patch('github_sources.BATCH_SIZE', 1), patch('github_sources.SNAPSHOT_WORKERS', workers):
                client, branches, data = self.associate(api,
                    branches=[branch(sha=f'{number:040x}') for number in range(1, 9)])
            outcomes.append((client.coverage, client.warnings, branches, data))
        self.assertEqual(outcomes[0], outcomes[1])

    def test_alias_identity_reads_are_serialized_cached_and_exported_once(self):
        from threading import Lock
        lock, reads = Lock(), []
        def api(args):
            if args[0] != 'graphql':
                with lock:
                    reads.append(args[2])
                return {'id': 123, 'full_name': 'org/canonical', 'html_url': 'https://github.com/org/canonical'}
            result = snapshot_data(args)
            for index in re.findall(r'c(\d+):repository', query_text(args)):
                result['data']['c' + index] = {'id': 'R123', 'nameWithOwner': 'org/canonical', 'object': None}
            return result
        with patch('github_sources.BATCH_SIZE', 1):
            client, branches, data = self.associate(api,
                branches=[branch(sha=f'{number:040x}') for number in range(1, 9)])
        self.assertEqual(sorted(reads), ['repos/org/canonical', 'repos/org/repo'])
        self.assertEqual(len(data['repository_aliases']), 1)
        self.assertEqual(data['repository_aliases'][0]['canonical_repo'], 'org/canonical')
        self.assertTrue(all(row['pr_status'] == 'no_match' for row in branches))
        self.assertTrue(all(row['association_lookups'][0]['search_repo'] == 'org/canonical' for row in branches))
        self.assertEqual(client.warnings, [])

    def test_failed_shared_alias_proof_does_not_retry_or_erase_parallel_positives(self):
        from threading import Lock
        lock, reads = Lock(), []
        def api(args):
            if args[0] != 'graphql':
                with lock:
                    reads.append(args[2])
                if args[2] == 'repos/org/canonical':
                    raise subprocess.CalledProcessError(403, 'native read')
                return {'id': 123, 'full_name': 'org/canonical', 'html_url': 'https://github.com/org/canonical'}
            query = query_text(args)
            if 'query:"is:pr ' in query:
                return {'data': {'s0': connection()}}
            number = int(re.search(r'is:pr ([0-9a-f]{40})', query)[1], 16)
            result = snapshot_data(args)
            result['data']['s0'] = connection([gql(number, repo='org/canonical')])
            result['data']['c0'] = {'id': 'R123', 'nameWithOwner': 'org/canonical', 'object': None}
            return result
        with patch('github_sources.BATCH_SIZE', 1):
            client, branches, data = self.associate(api,
                branches=[branch(sha=f'{number:040x}') for number in range(1, 9)])
        self.assertEqual(sorted(reads), ['repos/org/canonical', 'repos/org/repo'])
        self.assertEqual(len(data['pr_index']), 8)
        self.assertTrue(all(row['pr_status'] == 'matched' and row['association_lookups'][0]['status'] == 'partial'
                            for row in branches))
        self.assertTrue(client.warnings)
        self.assertFalse(data.get('repository_aliases'))

    def test_global_fallback_remains_deduplicated_under_parallel_routed_batches(self):
        from threading import Lock
        lock, global_reads = Lock(), []
        def api(args):
            query = query_text(args)
            sha = re.search(r'is:pr ([0-9a-f]{40})', query)[1]
            if 'query:"is:pr ' in query:
                with lock:
                    global_reads.append(sha)
                return {'data': {'s0': connection([gql(int(sha, 16), repo='other/upstream')])}}
            result = snapshot_data(args)
            result['data']['c0'] = None
            return result
        with patch('github_sources.BATCH_SIZE', 1):
            _, branches, data = self.associate(api,
                branches=[branch(sha=f'{number:040x}') for number in range(1, 5)],
                repos=[repository(remotes=('old/fork', 'org/repo'))])
        self.assertEqual(sorted(global_reads), [f'{number:040x}' for number in range(1, 5)])
        self.assertEqual(len(data['pr_index']), 4)
        self.assertTrue(all(row['pr_status'] == 'matched' and len(row['prs']) == 1 for row in branches))
        self.assertTrue(all(all(info['status'] == 'partial' for info in row['association_lookups']
                                if info.get('repo')) for row in branches))

    def test_unverified_canonical_associated_pr_survives_as_context_without_branch_credit(self):
        for affected, native_id in ((False, 'NATIVE_REPO'), (True, 'NATIVE_REPO'), (True, None)):
            def api(args):
                if args[0] != 'graphql':
                    raise OSError('native repository identity unavailable')
                query = query_text(args)
                if 'query:"is:pr ' in query:
                    return {'data': {'s0': connection()}}
                result = snapshot_data(args)
                if 'repo:example/previous ' in query:
                    result['data']['c0'] = {'id': native_id, 'nameWithOwner': 'example/canonical',
                        'object': {'associatedPullRequests': connection([gql(7, repo='example/canonical')])}}
                    if affected:
                        result['errors'] = [{'path': ['c0', 'object', 'associatedPullRequests'],
                                             'message': 'partial canonical connection'}]
                else:
                    result['data']['s0'] = connection([gql(8, repo='example/healthy', sha='b' * 40)])
                return result
            with self.subTest(affected=affected, native_id=native_id), patch('github_sources.BATCH_SIZE', 1):
                client, branches, data = self.associate(api,
                    branches=[branch('/old'), branch('/healthy', sha='b' * 40)],
                    repos=[repository('/old', ('example/previous',)), repository('/healthy', ('example/healthy',))])
                self.assertEqual([row['number'] for row in data['pr_index']], [7, 8])
                self.assertEqual(branches[0]['prs'], [])
                self.assertEqual(branches[0]['pr_status'], 'partial')
                self.assertEqual(branches[1]['prs'][0]['number'], 8)
                self.assertEqual(branches[1]['pr_status'], 'matched')
                self.assertTrue(client.warnings)
                self.assertFalse(data.get('repository_aliases'))

    def test_unverified_alias_cannot_gain_branch_credit_from_later_partial_metadata(self):
        def api(args):
            if args[0] != 'graphql':
                raise OSError('native alias identity unavailable')
            query = query_text(args)
            if 'query:"is:pr ' in query:
                return {'data': {'s0': connection()}}
            result = snapshot_data(args)
            if 'after:"next"' in query:
                # A readable association page survives errors in sibling repo metadata.
                result['data']['c0'] = {'object': {'associatedPullRequests':
                    connection([gql(8, repo='example/canonical')])}}
                result['errors'] = [{'path': ['c0', 'nameWithOwner'], 'message': 'partial metadata'}]
            else:
                result['data']['c0'] = {'id': 'NATIVE_REPO', 'nameWithOwner': 'example/canonical',
                    'object': {'associatedPullRequests': connection([gql(7, repo='example/canonical')],
                                                                   more=True, cursor='next')}}
            return result
        client, branches, data = self.associate(api, repos=[repository(remotes=('example/previous',))])
        self.assertEqual([row['number'] for row in data['pr_index']], [7, 8])
        self.assertEqual(branches[0]['prs'], [])
        self.assertEqual(branches[0]['pr_status'], 'partial')
        self.assertTrue(client.warnings)
        self.assertFalse(data.get('repository_aliases'))

    def test_partial_graphql_canonical_connection_requires_independent_alias_proof(self):
        reads = []
        def api(args):
            if args[0] != 'graphql':
                reads.append(args[2])
                return {'id': 42, 'full_name': 'example/canonical',
                        'html_url': 'https://github.com/example/canonical'}
            result = snapshot_data(args)
            if 'c0:repository' in query_text(args):
                result['data']['c0'] = {'id': 'NATIVE_REPO', 'nameWithOwner': 'example/canonical',
                    'object': {'associatedPullRequests': connection([gql(7, repo='example/canonical')])}}
                result['errors'] = [{'path': ['c0', 'object', 'associatedPullRequests'], 'message': 'partial'}]
            return result
        client, branches, data = self.associate(api, repos=[repository(remotes=('example/previous',))])
        self.assertEqual(reads, ['repos/example/previous', 'repos/example/canonical'])
        self.assertEqual(branches[0]['prs'][0]['number'], 7)
        self.assertEqual(branches[0]['prs'][0]['proof'], 'commit_association')
        self.assertEqual(len(data['repository_aliases']), 1)
        self.assertEqual(branches[0]['association_lookups'][0]['status'], 'partial')
        self.assertTrue(client.warnings)

    def test_unexpected_worker_exception_propagates(self):
        def api(args):
            raise RuntimeError('unexpected programmer failure')
        with patch('github_sources.BATCH_SIZE', 1), self.assertRaisesRegex(RuntimeError, 'programmer failure'):
            self.associate(api, branches=[branch(), branch(sha='b' * 40)])


class CommentHistoryTest(unittest.TestCase):
    client = GithubSourcesTest.client
    def history_fixture(self):
        event = {'id': 51, 'node_id': 'native51', 'user': {'login': 'bot', 'node_id': 'BOT51'},
                 'body': 'Earlier source body', 'html_url': 'https://github.com/org/repo/pull/1#issuecomment-51',
                 'created_at': '2026-10-01T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z'}
        node = {'id': 'native51', 'databaseId': 51, 'url': event['html_url'],
                'author': {'login': 'bot', 'id': 'BOT51'}, 'editor': {'login': 'bot'},
                'createdAt': event['created_at'], 'updatedAt': '2026-10-16T01:00:00Z',
                'lastEditedAt': '2026-10-16T01:00:00Z'}
        def row(identity, when, actor='bot'):
            return {'id': identity, 'editedAt': when, 'createdAt': when, 'updatedAt': when,
                    'deletedAt': None, 'editor': {'login': actor, 'id': 'native-' + actor}}
        rows = [row('edit2', node['lastEditedAt']), row('edit1', event['updated_at']),
                row('created', event['created_at'])]
        page = {**node, 'body': 'Current source https://gitlab.com/group/project/-/merge_requests/2',
                'includesCreatedEdit': True, 'userContentEdits': {'totalCount': 3,
                'nodes': rows, 'pageInfo': {'hasNextPage': False, 'endCursor': 'last'}}}
        return event, node, page

    def run_history(self, event, node, pages):
        responses = iter(pages)
        def api(args):
            if args[0] == 'graphql':
                if 'userContentEdits' in query_text(args):
                    return next(responses)
                return {'data': {'node': node}}
            return [event] if args[2] == 'repos/org/repo/issues/1/comments' else []
        client = self.client(api)
        data = {'activity': []}
        client._inspect_activity(data, [{'url': 'https://github.com/org/repo/pull/1', 'repo': 'org/repo', 'number': 1}])
        return client, data

    def test_complete_bot_history_recovers_drift_and_preserves_both_reference_bodies(self):
        from extended_evidence import github_reference_comments, validate_github
        event, node, page = self.history_fixture()
        client, data = self.run_history(event, node, [{'data': {'node': page}}])
        self.assertEqual(client.warnings, [])
        self.assertEqual(data['activity'], [])
        self.assertTrue(data['comment_edit_histories'][0]['no_personal_edits_in_window'])
        self.assertEqual([row['body'] for row in github_reference_comments(data)], [event['body'], page['body']])
        item = rest_item(rest(), 'prs', 'github.com')
        source = {'github': data, 'metadata': {'github_user': 'me'}}
        validate_github(source, {item['url']: item}, START, END)
        for mutation in ('actor', 'count', 'rest', 'status'):
            forged = copy.deepcopy(source)
            proof = forged['github']['comment_edit_histories'][0]
            if mutation == 'actor':
                proof['pages'][0]['response']['data']['node']['userContentEdits']['nodes'][1]['editor']['login'] = 'me'
            elif mutation == 'count':
                proof['pages'][0]['response']['data']['node']['userContentEdits']['totalCount'] = 4
            elif mutation == 'rest':
                proof['rest_observation']['user']['node_id'] = 'other'
            else:
                proof['status'] = 'partial'
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_github(forged, {item['url']: item}, START, END)

    def test_pagination_native_cursor_and_complete_count_are_required(self):
        event, node, page = self.history_fixture()
        first, second = copy.deepcopy(page), copy.deepcopy(page)
        first['userContentEdits']['nodes'] = first['userContentEdits']['nodes'][:1]
        first['userContentEdits']['pageInfo'] = {'hasNextPage': True, 'endCursor': 'next-cursor'}
        second['userContentEdits']['nodes'] = second['userContentEdits']['nodes'][1:]
        client, data = self.run_history(event, node, [{'data': {'node': first}}, {'data': {'node': second}}])
        self.assertEqual(client.warnings, [])
        self.assertTrue(any('after=next-cursor' in call for call in client.calls))
        self.assertEqual([p['after'] for p in data['comment_edit_histories'][0]['pages']], [None, 'next-cursor'])

    def test_unknown_deleted_personal_and_malformed_history_never_certify_negative(self):
        event, node, page = self.history_fixture()
        for mutation in ('self', 'unknown', 'deleted', 'count', 'duplicate', 'created', 'future', 'identity', 'errors', 'cursor'):
            altered = copy.deepcopy(page)
            rows = altered['userContentEdits']['nodes']
            if mutation == 'self':
                rows[1]['editor']['login'] = 'Me'
            elif mutation == 'unknown':
                rows[1]['editor'] = None
            elif mutation == 'deleted':
                rows[1]['deletedAt'] = '2026-10-14T00:00:00Z'
            elif mutation == 'count':
                altered['userContentEdits']['totalCount'] = 4
            elif mutation == 'duplicate':
                rows[1]['id'] = rows[0]['id']
            elif mutation == 'created':
                rows[-1]['editedAt'] = '2026-10-01T00:00:01Z'
                rows[-1]['createdAt'] = rows[-1]['updatedAt'] = rows[-1]['editedAt']
            elif mutation == 'future':
                rows[1]['editedAt'] = '2026-11-01T00:00:00Z'
            elif mutation == 'identity':
                altered['author']['id'] = 'another-author'
            elif mutation == 'cursor':
                altered['userContentEdits']['pageInfo'] = {'hasNextPage': True, 'endCursor': None}
            response = {'data': {'node': altered}}
            if mutation == 'errors':
                response['errors'] = [{'message': 'incomplete native history'}]
            with self.subTest(mutation=mutation):
                client, data = self.run_history(copy.deepcopy(event), copy.deepcopy(node), [response])
                self.assertTrue(client.warnings)
                self.assertEqual(data['activity'], [])
                self.assertEqual(data['comment_edit_histories'][0]['status'], 'partial')

    def test_native_initial_revision_cannot_disappear_from_later_complete_history(self):
        event, node, page = self.history_fixture()
        for mutation in ('time', 'actor'):
            altered = copy.deepcopy(node)
            if mutation == 'time':
                altered['lastEditedAt'] = '2026-10-15T01:00:00Z'
            else:
                altered['editor']['login'] = 'me'
            with self.subTest(mutation=mutation):
                client, data = self.run_history(event, altered, [{'data': {'node': page}}])
                self.assertTrue(client.warnings)
                self.assertFalse(data['comment_edit_histories'][0]['no_personal_edits_in_window'])
                self.assertIsNone(data['comment_edit_histories'][0]['personal_edit_ids'])

    def test_matching_latest_other_editor_cannot_hide_earlier_personal_revision(self):
        event, node, page = self.history_fixture()
        event['updated_at'] = node['updatedAt']
        page['userContentEdits']['nodes'][1]['editor']['login'] = 'ME'
        client, data = self.run_history(event, node, [{'data': {'node': page}}])
        proof = data['comment_edit_histories'][0]
        self.assertEqual(proof['personal_edit_ids'], ['edit1'])
        self.assertEqual(proof['unresolved_personal_edit_ids'], ['edit1'])
        self.assertEqual(proof['status'], 'partial')
        self.assertTrue(client.warnings)
        self.assertEqual(data['activity'], [])

    def test_current_own_positive_survives_history_failure_and_resolves_only_its_revision(self):
        from extended_evidence import validate_github
        event, node, page = self.history_fixture()
        event['updated_at'] = node['updatedAt'] = node['lastEditedAt'] = '2026-10-15T00:00:00Z'
        node['editor']['login'] = 'me'
        page.update(node)
        page['body'] = event['body']
        rows = page['userContentEdits']['nodes']
        rows[0].update(editedAt=node['lastEditedAt'], createdAt=node['lastEditedAt'], updatedAt=node['lastEditedAt'])
        rows[0]['editor']['login'] = 'me'
        for mutation in ('known', 'earlier', 'failed', 'body'):
            altered = copy.deepcopy(page)
            if mutation == 'earlier':
                altered['userContentEdits']['nodes'][1]['editor']['login'] = 'me'
            elif mutation == 'body':
                altered['body'] = 'A different current body'
            response = {'data': {'node': altered}}
            if mutation == 'failed':
                response['errors'] = [{'message': 'history unavailable'}]
            with self.subTest(mutation=mutation):
                client, data = self.run_history(event, node, [response])
                self.assertEqual([row['action'] for row in data['activity']], ['issue_comment_edited'])
                self.assertEqual(data['activity'][0]['body_source'], 'rest_current_observation')
                self.assertEqual(data['activity'][0]['body'], event['body'])
                proof = data['comment_edit_histories'][0]
                self.assertEqual(proof['status'], 'ok' if mutation == 'known' else 'partial')
                self.assertFalse(proof['no_personal_edits_in_window'])
                if mutation == 'known':
                    self.assertEqual(proof['personal_edit_ids'], ['edit2'])
                    self.assertEqual(proof['unresolved_personal_edit_ids'], [])
                    self.assertEqual(client.warnings, [])
                else:
                    self.assertTrue(client.warnings)
                item = rest_item(rest(), 'prs', 'github.com')
                source = {'github': data, 'metadata': {'github_user': 'me', 'github_status': 'partial',
                                                      'warnings': client.warnings}}
                validate_github(source, {item['url']: item}, START, END)
                forged_body = copy.deepcopy(source)
                forged_body['github']['activity'][0]['body_source'] = 'exact_authored_revision'
                with self.assertRaises(ValueError):
                    validate_github(forged_body, {item['url']: item}, START, END)
                legacy = copy.deepcopy(source)
                legacy['github']['activity'][0].pop('body_source')
                validate_github(legacy, {item['url']: item}, START, END)
                if mutation == 'known':
                    forged = copy.deepcopy(source)
                    forged['github']['activity'] = []
                    with self.assertRaises(ValueError):
                        validate_github(forged, {item['url']: item}, START, END)

    def test_capped_and_changed_native_pages_remain_explicit_gaps(self):
        event, node, page = self.history_fixture()
        first, second = copy.deepcopy(page), copy.deepcopy(page)
        first['userContentEdits']['nodes'] = first['userContentEdits']['nodes'][:1]
        first['userContentEdits']['pageInfo'] = {'hasNextPage': True, 'endCursor': 'next'}
        second['userContentEdits']['nodes'] = second['userContentEdits']['nodes'][1:]
        for mutation in ('cap', 'repeat', 'count', 'body', 'latest'):
            altered = copy.deepcopy(second)
            if mutation == 'repeat':
                altered['userContentEdits']['pageInfo'] = {'hasNextPage': True, 'endCursor': 'next'}
            elif mutation == 'count':
                altered['userContentEdits']['totalCount'] = 4
            elif mutation == 'body':
                altered['body'] = 'Changed while paging'
            elif mutation == 'latest':
                altered['lastEditedAt'] = '2026-10-15T00:00:00Z'
            with self.subTest(mutation=mutation):
                responses = [{'data': {'node': first}}, {'data': {'node': altered}}]
                with patch('github_sources.MAX_LIST_PAGES', 1 if mutation == 'cap' else 100):
                    client, data = self.run_history(event, node, responses)
                self.assertTrue(client.warnings)
                self.assertFalse(data['comment_edit_histories'][0]['no_personal_edits_in_window'])

    def test_self_edit_outside_window_and_creation_by_self_do_not_imply_weekly_edit(self):
        event, node, page = self.history_fixture()
        page['userContentEdits']['nodes'][-1]['editor']['login'] = 'me'
        page['userContentEdits']['nodes'][0]['editedAt'] = '2026-10-16T01:00:00Z'
        # END is midnight: the latest self edit lies after the exact end.
        page['editor']['login'] = 'me'
        page['userContentEdits']['nodes'][0]['editor']['login'] = 'me'
        client, data = self.run_history(event, node, [{'data': {'node': page}}])
        self.assertEqual(client.warnings, [])
        self.assertEqual(data['activity'], [])


if __name__ == '__main__':
    unittest.main()
