"""Anonymous public GitLab reads: independent endpoint and native identity coverage."""
import copy
import json
from pathlib import Path
import sys
import unittest
from urllib.parse import parse_qs, urlsplit

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gitlab_api as api  # noqa: E402

HOST = 'gitlab.example.test'
WEB = 'https://' + HOST + '/group/project/-/merge_requests/7'
DETAIL = 'https://' + HOST + '/api/v4/projects/group%2Fproject/merge_requests/7'
NOTES = 'https://' + HOST + '/api/v4/projects/12/merge_requests/7/notes'


def mr(number=7, repo='group/project', user=41):
    return {'id': number + 100, 'iid': number, 'project_id': 12,
            'web_url': f'https://{HOST}/{repo}/-/merge_requests/{number}', 'title': 'Native work',
            'description': 'Native text\nwith links.', 'state': 'opened',
            'created_at': '2026-10-10T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z',
            'author': {'id': user, 'username': 'different-from-github', 'public_email': 'own@example.test'},
            'assignees': [], 'reviewers': []}


def note(number=1, user=999):
    return {'id': number, 'body': 'Native note TASK-42', 'author': {'id': user, 'username': 'other'},
            'created_at': '2020-01-01T00:00:00Z', 'updated_at': '2020-01-02T00:00:00Z', 'system': False}


def response(url, source, status=200, headers=None, redirects=None):
    return {'url': url, 'source': copy.deepcopy(source), 'status': status,
            'headers': headers or {}, 'redirects': redirects or []}


class GitLabApiTest(unittest.TestCase):
    def collect(self, read=None, urls=None, emails=()):
        calls, warnings = [], []
        def transport(url):
            calls.append(url)
            if read:
                return read(url)
            return response(url, [note()] if '/notes?' in url else mr())
        result = api.collect_gitlab([WEB] if urls is None else urls, warnings.append,
                                    verified_emails=emails, since='2026-10-09T00:00:00Z', read=transport)
        return result, calls, warnings

    def test_unreachable_host_is_read_once(self):
        from urllib.error import URLError
        calls = []

        def unreachable(url):
            calls.append(url)
            raise URLError('timed out')
        other = 'https://' + HOST + '/group/project/-/merge_requests/8'
        result = api.collect_gitlab([WEB, other], [].append, read=unreachable)
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['items'], [])
        self.assertEqual(len(result['metadata']['failures']), 2)

    def test_unconfigured_host_is_never_contacted(self):
        calls, warnings = [], []
        result = api.collect_gitlab([WEB], warnings.append, verified_emails=('own@example.test',),
                                    read=lambda url: calls.append(url), trusted_hosts={'gitlab.com'})
        self.assertEqual(calls, [])
        self.assertEqual(result['items'], [])
        self.assertEqual(result['metadata']['failures'], [{'url': WEB, 'reason': 'unconfigured_gitlab_host'}])
        self.assertEqual(result['metadata']['identities'], [])

    def test_public_details_and_all_author_all_date_notes_preserved(self):
        result, _, warnings = self.collect()
        self.assertEqual(result['items'][0]['source'], mr())
        self.assertEqual(result['items'][0]['notes'], [note()])
        self.assertEqual(result['metadata']['identities'], [])
        self.assertEqual(result['metadata']['status'], 'ok')
        self.assertFalse(warnings)

    def test_user_login_endpoint_not_a_prerequisite_and_exact_public_email_enables_inventory(self):
        def read(url):
            if '/notes?' in url:
                return response(url, [])
            if '/merge_requests?' in url:
                return response(url, [], headers={'x-next-page': '', 'x-total': '0'})
            self.assertNotIn('/users/', url)
            return response(url, mr())
        result, calls, _ = self.collect(read, emails=['OWN@example.test'])
        self.assertEqual(len(result['metadata']['identities']), 1)
        self.assertEqual(len(result['metadata']['inventories']), 4)
        self.assertFalse(any('/users/' in url for url in calls))
        self.assertEqual(result['metadata']['identities'][0]['basis'],
                         'native_mr_author_public_email_matches_verified_alias')

    def test_missing_email_profile_failure_preserves_native_mr_without_guessing_username(self):
        def read(url):
            if '/users/' in url:
                return response(url, None, status=403)
            if '/notes?' in url:
                return response(url, [])
            value = mr()
            value['author'].pop('public_email')
            return response(url, value)
        result, _, _ = self.collect(read, emails=['own@example.test'])
        self.assertEqual(len(result['items']), 1)
        self.assertFalse(result['metadata']['identities'])
        self.assertEqual(result['metadata']['status'], 'ok')
        self.assertEqual(len(result['metadata']['identity_leads']), 1)

    def test_failed_sibling_keeps_valid_detail_and_notes(self):
        def read(url):
            if '/8' in url:
                return response(url, None, status=404)
            return response(url, [] if '/notes?' in url else mr())
        result, _, _ = self.collect(read, urls=[WEB, WEB[:-1] + '8'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['metadata']['status'], 'partial')

    def test_numeric_project_redirect_binds_native_renamed_route(self):
        def read(url):
            if '/notes?' in url:
                return response(url, [])
            final = f'https://{HOST}/api/v4/projects/12/merge_requests/7'
            return response(final, mr(repo='new/project'), redirects=[{'url': url, 'status': 301, 'location': final}])
        result, _, _ = self.collect(read)
        self.assertEqual(result['items'][0]['repo'], 'new/project')
        self.assertEqual(result['items'][0]['requested_urls'], [WEB])
        forged = json.loads(json.dumps(result))
        forged['items'][0]['requested_urls'] = [f'https://{HOST}/unrelated/project/-/merge_requests/7']

    def test_foreign_redirect_wrong_iid_and_unproved_rename_rejected(self):
        for kind in ('foreign', 'iid', 'rename'):
            def read(url):
                value = mr()
                if kind == 'foreign':
                    return response('https://evil.example/api/v4/projects/12/merge_requests/7', value)
                if kind == 'iid':
                    value['iid'] = 8
                else:
                    value['web_url'] = f'https://{HOST}/new/project/-/merge_requests/7'
                return response(url, value)
            result, _, _ = self.collect(read)
            self.assertEqual(result['items'], [])
            self.assertEqual(result['metadata']['status'], 'partial')

    def test_header_paging_keeps_native_rows_and_duplicates_are_partial(self):
        def read(url):
            if '/notes?' not in url:
                return response(url, mr())
            page = parse_qs(urlsplit(url).query)['page'][0]
            return response(url, [note(1)] if page == '1' else [note(1), note(2)],
                            headers={'x-next-page': '2' if page == '1' else '', 'x-total': '3'})
        result, _, _ = self.collect(read)
        self.assertEqual([n['id'] for n in result['items'][0]['notes']], [1, 2])
        self.assertEqual(result['items'][0]['notes_status'], 'partial')

    def test_later_notes_failure_preserves_first_page(self):
        def read(url):
            if '/notes?' not in url:
                return response(url, mr())
            if 'page=2' in url:
                raise TimeoutError('no native response')
            return response(url, [note()], headers={'x-next-page': '2'})
        result, _, _ = self.collect(read)
        self.assertEqual(result['items'][0]['notes'], [note()])
        self.assertEqual(result['items'][0]['notes_status'], 'partial')

    def test_link_scope_change_and_count_drift_preserve_readable_rows(self):
        for kind in ('scope', 'count'):
            def read(url):
                if '/notes?' not in url:
                    return response(url, mr())
                if kind == 'scope':
                    return response(url, [note()], headers={'link': '<https://evil.example/api/v4/notes?page=2>; rel="next"'})
                page = parse_qs(urlsplit(url).query)['page'][0]
                return response(url, [note(int(page))], headers={'x-next-page': '2' if page == '1' else '',
                                                                'x-total': '2' if page == '1' else '3'})
            result, _, _ = self.collect(read)
            self.assertTrue(result['items'][0]['notes'])
            self.assertEqual(result['items'][0]['notes_status'], 'partial')

    def test_global_inventory_failure_tries_known_public_project(self):
        def read(url):
            if '/merge_requests?' in url:
                return response(url, None, status=403) if '/projects/' not in url else response(url, [])
            return response(url, [] if '/notes?' in url else mr())
        result, calls, _ = self.collect(read, emails=['own@example.test'])
        self.assertEqual(len(result['metadata']['inventories']), 8)
        self.assertEqual(result['metadata']['status'], 'partial')
        self.assertTrue(any('/projects/group%2Fproject/merge_requests?' in url for url in calls))

    def test_invalid_native_note_does_not_erase_sibling(self):
        result, _, _ = self.collect(lambda url: response(url, [note(), {'id': 2}] if '/notes?' in url else mr()))
        self.assertEqual(result['items'][0]['notes'], [note()])
        self.assertEqual(result['items'][0]['notes_status'], 'partial')

    def test_reject_credentials_http_encoded_paths_and_no_network_for_invalid_inputs(self):
        for url in ('http://gitlab.example.test/g/p/-/merge_requests/1',
                    'https://token@gitlab.example.test/g/p/-/merge_requests/1',
                    'https://gitlab.example.test/g/../p/-/merge_requests/1',
                    'https://gitlab.example.test/g/%2fp/-/merge_requests/1'):
            result, calls, _ = self.collect(urls=[url])
            self.assertFalse(calls)
            self.assertFalse(result['items'])

    def test_duplicate_seeds_do_not_skip_verified_identity_inventory(self):
        def read(url):
            return response(url, [] if '/notes?' in url or '/merge_requests?' in url else mr())
        result, _, _ = self.collect(read, urls=[WEB, WEB], emails=['own@example.test'])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(len(result['metadata']['inventories']), 4)

    def test_link_next_page_with_native_defaults_preserves_scope(self):
        def read(url):
            if '/notes?' not in url:
                return response(url, mr())
            page = parse_qs(urlsplit(url).query)['page'][0]
            headers = {'link': '<' + NOTES + '?page=2&per_page=100&order_by=created_at&sort=asc>; rel="next"'} if page == '1' else {'x-next-page': ''}
            return response(url, [note(int(page))], headers=headers)
        result, _, _ = self.collect(read)
        self.assertEqual([n['id'] for n in result['items'][0]['notes']], [1, 2])
        self.assertEqual(result['items'][0]['notes_status'], 'ok')

    def test_native_user_note_count_visibility_gap_stays_partial(self):
        def read(url):
            if '/notes?' in url:
                return response(url, [note()])
            value = mr()
            value['user_notes_count'] = 9
            return response(url, value)
        result, _, _ = self.collect(read)
        self.assertEqual(result['items'][0]['notes_status'], 'partial')
        self.assertEqual(result['items'][0]['notes'], [note()])

    def test_own_inventory_rejects_other_author_without_fabricating_ownership(self):
        def read(url):
            if '/merge_requests?' in url:
                return response(url, [mr(8, user=999)] if 'author_id=' in url else [])
            return response(url, [] if '/notes?' in url else mr())
        result, _, _ = self.collect(read, emails=['own@example.test'])
        self.assertEqual([i['number'] for i in result['items']], [7])
        self.assertEqual(result['metadata']['status'], 'partial')

    def test_safe_api_and_redirects_block_foreign_hosts_credentials_and_login_routes(self):
        for target in ('https://evil.example/api/v4/projects/1',
                       f'https://token@{HOST}/api/v4/projects/1', f'https://{HOST}/users/sign_in',
                       f'http://{HOST}/api/v4/projects/1'):
            with self.assertRaises(ValueError):
                api.safe_api(target, HOST)

    def test_explicit_custom_host_does_not_need_gitlab_brand_label(self):
        self.assertEqual(api.mr_route('https://code.company.test/team/project/-/merge_requests/3'),
                         ('code.company.test', 'team/project', 3))

    def test_nonmatching_native_public_email_does_not_probe_irrelevant_user_profile(self):
        def read(url):
            self.assertNotIn('/users/', url)
            if '/notes?' in url:
                return response(url, [])
            value = mr()
            value['author']['public_email'] = 'someone-else@example.test'
            return response(url, value)
        result, _, _ = self.collect(read, emails=['own@example.test'])
        self.assertEqual(result['metadata']['status'], 'ok')
        self.assertFalse(result['metadata']['identities'])
        self.assertFalse(result['metadata']['identity_leads'])

    def test_last_page_header_cannot_silently_truncate_notes(self):
        def read(url):
            if '/notes?' not in url:
                return response(url, mr())
            return response(url, [note()], headers={'link': '<' + url.split('?')[0]
                            + '?per_page=100&page=9>; rel="last"'})
        result, _, _ = self.collect(read)
        self.assertEqual(result['items'][0]['notes_status'], 'partial')
        self.assertTrue(result['items'][0]['notes'])

    def inventory_fallback_capture(self, urls=None, failed_later_page=False):
        def read(url):
            if '/notes?' in url:
                return response(url, [])
            if '/merge_requests?' in url:
                params = parse_qs(urlsplit(url).query)
                if 'author_id' in params and params.get('state') == ['all'] and '/projects/' not in url:
                    if params['page'] == ['2']:
                        return response(url, None, status=403)
                    return response(url, [mr(8)], headers={'x-next-page': '2' if failed_later_page else ''})
                return response(url, [])
            if urlsplit(url).path.endswith('/merge_requests/8'):
                return response(url, None, status=403)
            return response(url, mr())
        return self.collect(read, urls=urls, emails=['own@example.test'])[0]

    def checked(self, result):
        pass

    def test_full_native_inventory_source_survives_failed_detail_read(self):
        result = self.inventory_fallback_capture()
        retained = next(item for item in result['items'] if item['number'] == 8)
        self.assertEqual(retained['source'], mr(8))
        self.assertEqual(retained['source_kind'], 'inventory')
        self.assertEqual(result['metadata']['status'], 'partial')

    def test_earlier_failed_explicit_seed_recovers_from_later_native_inventory(self):
        result = self.inventory_fallback_capture(urls=[WEB[:-1] + '8', WEB])
        self.assertEqual({item['number'] for item in result['items']}, {7, 8})

    def test_not_applicable_requires_no_native_work_or_queries(self):
        empty, _, _ = self.collect(urls=[])
        self.assertEqual(empty['metadata']['status'], 'not_applicable')
        populated, _, _ = self.collect()
        populated['metadata']['status'] = 'not_applicable'


class GraphqlNotesTest(unittest.TestCase):
    collect = GitLabApiTest.collect
    checked = GitLabApiTest.checked

    def native_note(self, number=1):
        return {'id': f'gid://gitlab/Note/{number}', 'body': 'Other-author native TASK-73',
                'createdAt': '2026-10-10T01:00:00Z', 'updatedAt': '2026-10-11T01:00:00Z',
                'system': False, 'internal': False, 'url': WEB + f'#note_{number}',
                'noteableId': 107, 'noteableType': 'MergeRequest',
                'project': {'id': 'gid://gitlab/Project/12', 'fullPath': 'group/project'},
                'author': {'id': 'gid://gitlab/User/999', 'username': 'other'},
                'lastEditedAt': '2026-10-11T01:00:00Z',
                'lastEditedBy': {'id': 'gid://gitlab/User/999', 'username': 'other'}}

    def graph(self, nodes, more=False, cursor=None, count=None):
        return {'data': {'project': {'id': 'gid://gitlab/Project/12', 'fullPath': 'group/project',
            'mergeRequest': {'id': 'gid://gitlab/MergeRequest/107', 'iid': '7', 'webUrl': WEB,
                'createdAt': mr()['created_at'], 'updatedAt': mr()['updated_at'],
                'notes': {'nodes': nodes, 'count': len(nodes) if count is None else count,
                          'pageInfo': {'hasNextPage': more, 'endCursor': cursor}}}}}}

    def graph_capture(self, graph_read):
        def read(url):
            if urlsplit(url).path == '/api/graphql':
                return response(url, graph_read(api.graphql_variables(url)))
            return response(url, mr()) if url == DETAIL else response(url, None, status=401)
        return self.collect(read)

    def test_denied_rest_recovers_complete_native_graphql_notes_with_all_receipts(self):
        native = self.native_note()
        result, calls, warnings = self.graph_capture(lambda variables: self.graph([native]))
        item = result['items'][0]
        self.assertEqual(item['notes_status'], 'ok')
        self.assertEqual(result['metadata']['status'], 'ok')
        self.assertFalse(warnings)
        self.assertEqual(item['notes'][0]['source'], native)
        self.assertEqual(item['notes'][0]['id'], native['id'])
        self.assertEqual(item['notes'][0]['url'], native['url'])
        self.assertFalse(item['notes'][0]['internal'])
        self.assertEqual(len(result['metadata']['failures']), 4)
        self.assertEqual(len(calls), 6)
        for field in ('body', 'author', 'internal', 'last_edited_at', 'url', 'source'):
            forged = copy.deepcopy(result)
            forged['items'][0]['notes'][0][field] = 'forged'

    def test_graphql_paginates_native_cursor_and_binds_every_note(self):
        def graph(variables):
            return self.graph([self.native_note(2 if variables['after'] else 1)],
                              more=variables['after'] is None, cursor='native-next', count=2)
        result, calls, _ = self.graph_capture(graph)
        self.assertEqual(result['items'][0]['notes_status'], 'ok')
        self.assertEqual([n['id'] for n in result['items'][0]['notes']],
                         ['gid://gitlab/Note/1', 'gid://gitlab/Note/2'])
        self.assertEqual(api.graphql_variables(calls[-1])['after'], 'native-next')

    def test_invalid_actor_privacy_target_time_and_graphql_errors_preserve_siblings(self):
        for kind in ('actor', 'privacy', 'target', 'time', 'errors'):
            bad = self.native_note(2)
            if kind == 'actor':
                bad['author']['id'] = '999'
            elif kind == 'privacy':
                bad.pop('internal')
            elif kind == 'target':
                bad['noteableId'] = 999
            elif kind == 'time':
                bad['lastEditedAt'] = '2027-01-01T00:00:00Z'
            graph = self.graph([self.native_note(), bad])
            if kind == 'errors':
                graph['errors'] = [{'message': 'native partial'}]
            result, _, _ = self.graph_capture(lambda variables: graph)
            self.assertEqual(result['items'][0]['notes_status'], 'partial', kind)
            self.assertEqual(result['items'][0]['notes'][0]['id'], 'gid://gitlab/Note/1')
            self.assertEqual(result['metadata']['status'], 'partial')

    def test_graphql_missing_repeated_cursor_count_and_wrong_mr_cannot_claim_complete(self):
        for kind in ('missing', 'repeat', 'count', 'mr', 'project'):
            def graph(variables):
                value = self.graph([self.native_note()], more=kind in {'missing', 'repeat'},
                                   cursor=None if kind == 'missing' else 'same', count=2)
                target = value['data']['project']
                if kind == 'mr':
                    target['mergeRequest']['id'] = 'gid://gitlab/MergeRequest/999'
                elif kind == 'project':
                    target['id'] = 'gid://gitlab/Project/999'
                return value
            result, _, _ = self.graph_capture(graph)
            self.assertEqual(result['items'][0]['notes_status'], 'partial', kind)

    def test_malformed_native_errors_field_cannot_claim_complete(self):
        graph = self.graph([self.native_note()])
        graph['errors'] = {}
        result, _, _ = self.graph_capture(lambda variables: graph)
        self.assertEqual(result['items'][0]['notes_status'], 'partial')
        self.assertEqual(result['items'][0]['notes'][0]['id'], 'gid://gitlab/Note/1')

    def test_same_host_other_mr_graphql_failure_is_not_recovered_by_success(self):
        result, _, _ = self.graph_capture(lambda variables: self.graph([self.native_note()]))
        failure = {'url': api.graphql_notes_url(HOST, 'group/project', 8, 'second-page'), 'reason': 'native_http_403'}
        result['metadata']['failures'].append(failure)
        self.assertEqual(api.unresolved_failures(result), [failure])
        result['metadata']['status'] = 'ok'

    def test_native_count_or_snapshot_drift_keeps_readable_later_notes(self):
        for kind in ('count', 'snapshot'):
            def graph(variables):
                later = variables['after'] is not None
                value = self.graph([self.native_note(2 if later else 1)], more=not later,
                                   cursor='next', count=3 if later and kind == 'count' else 2)
                if later and kind == 'snapshot':
                    value['data']['project']['mergeRequest']['updatedAt'] = '2026-10-13T00:00:00Z'
                return value
            result, _, _ = self.graph_capture(graph)
            self.assertEqual(result['items'][0]['notes_status'], 'partial')
            self.assertEqual([note['id'] for note in result['items'][0]['notes']],
                             ['gid://gitlab/Note/1', 'gid://gitlab/Note/2'])

    def test_rest_positive_is_preserved_when_complete_graph_connection_omits_it(self):
        def read(url):
            if url == DETAIL:
                return response(url, mr())
            if urlsplit(url).path == '/api/graphql':
                return response(url, self.graph([self.native_note()]))
            if urlsplit(url).path.endswith('/notes'):
                if 'page=2' in url:
                    return response(url, None, status=401)
                return response(url, [note(42)], headers={'x-next-page': '2'})
            return response(url, None, status=401)
        result, _, _ = self.collect(read)
        self.assertEqual(result['items'][0]['notes_status'], 'partial')
        self.assertEqual([note['id'] for note in result['items'][0]['notes']], [42, 'gid://gitlab/Note/1'])

    def test_native_second_precision_is_bound_without_arbitrary_time_tolerance(self):
        self.assertTrue(api.graphql_time_matches('2026-10-10T00:00:00Z', '2026-10-10T00:00:00.632Z'))
        self.assertFalse(api.graphql_time_matches('2026-10-10T00:00:01Z', '2026-10-10T00:00:00.632Z'))
        self.assertFalse(api.graphql_time_matches('2026-10-10T00:00:00.001Z', '2026-10-10T00:00:00.632Z'))

    def test_only_exact_native_read_query_is_allowed(self):
        url = api.graphql_notes_url(HOST, 'group/project', 7)
        api.safe_api(url, HOST)
        for forged in (url.replace('WorkSummaryNotes', 'mutation'), url + '&query=mutation',
                       url.replace('group%2Fproject', '..%2Fproject')):
            with self.assertRaises((ValueError, json.JSONDecodeError)):
                api.safe_api(forged, HOST)


if __name__ == '__main__':
    unittest.main()
