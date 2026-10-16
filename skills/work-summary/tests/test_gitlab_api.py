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
        api.validate_gitlab_api({'gitlab': result, 'metadata': {'author_aliases': [
            {'email': email, 'source': 'authenticated_github_profile'} for email in emails]}})
        return result, calls, warnings

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

    def test_forged_note_identity_and_public_identity_rejected(self):
        result, _, _ = self.collect()
        bad = json.loads(json.dumps(result))
        bad['items'][0]['notes'][0]['body'] = 'invented'
        with self.assertRaises(ValueError):
            api.validate_gitlab_api({'gitlab': bad})
        result['metadata']['identities'] = [{'host': HOST, 'user_id': 41, 'source': mr()['author'],
            'matched_email': 'own@example.test', 'basis': 'native_mr_author_public_email_matches_verified_alias'}]
        with self.assertRaises(ValueError):
            api.validate_gitlab_api({'gitlab': result})

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

    def test_configured_git_or_self_declared_alias_cannot_establish_target(self):
        def read(url):
            return response(url, [] if '/notes?' in url or '/merge_requests?' in url else mr())
        result, _, _ = self.collect(read, emails=['own@example.test'])
        for rows in ([], [{'email': 'own@example.test', 'source': 'configured_git'}]):
            with self.assertRaises(ValueError):
                api.validate_gitlab_api({'gitlab': result, 'metadata': {'author_aliases': rows}})
        api.validate_gitlab_api({'gitlab': result, 'metadata': {}, 'jira': {'metadata': {
            'identity_verified': True, 'account_id': 'native-account', 'user': 'own@example.test'}}})

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


if __name__ == '__main__':
    unittest.main()
