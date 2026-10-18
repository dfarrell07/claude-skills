"""Anonymous GitLab MR detail reads and verified-email identity binding."""

import copy
from pathlib import Path
import sys
import unittest
from urllib.error import URLError

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gitlab_api as api  # noqa: E402

HOST = 'gitlab.example.test'
WEB = 'https://' + HOST + '/group/project/-/merge_requests/7'
DETAIL = 'https://' + HOST + '/api/v4/projects/group%2Fproject/merge_requests/7'


def mr(number=7, repo='group/project', user=41, email='own@example.test'):
    return {'id': number + 100, 'iid': number, 'project_id': 12,
            'web_url': f'https://{HOST}/{repo}/-/merge_requests/{number}', 'title': 'MR work',
            'description': 'Implements NET-5.', 'state': 'opened',
            'created_at': '2026-10-10T00:00:00Z', 'updated_at': '2026-10-12T00:00:00Z',
            'author': {'id': user, 'username': 'someone', 'public_email': email}}


def response(url, source, status=200, redirects=None):
    return {'url': url, 'source': copy.deepcopy(source), 'status': status, 'redirects': redirects or []}


class GitLabApiTest(unittest.TestCase):
    def collect(self, read, urls=(WEB,), emails=('own@example.test',), trusted=None):
        calls, warnings = [], []

        def transport(url):
            calls.append(url)
            return read(url)
        result = api.collect_gitlab(list(urls), warnings.append, verified_emails=emails, read=transport,
                                    trusted_hosts=trusted)
        return result, calls, warnings

    def test_detail_and_verified_public_email_bind_the_author(self):
        result, calls, warnings = self.collect(lambda url: response(url, mr()))
        self.assertEqual(calls, [DETAIL])
        self.assertEqual([item['url'] for item in result['items']], [WEB])
        self.assertEqual(result['metadata']['identities'],
                         [{'host': HOST, 'user_id': 41, 'matched_email': 'own@example.test'}])
        self.assertEqual(result['metadata']['status'], 'ok')
        self.assertEqual(warnings, [])

    def test_foreign_email_is_not_bound_and_hidden_email_needs_a_user_lookup(self):
        result, calls, _ = self.collect(lambda url: response(url, mr(email='other@example.test')))
        self.assertEqual(result['metadata']['identities'], [])
        self.assertEqual(calls, [DETAIL])

        def read(url):
            if url.endswith('/users/41'):
                return response(url, {'id': 41, 'public_email': 'own@example.test'})
            return response(url, mr(email=None))
        result, calls, _ = self.collect(read)
        self.assertEqual(calls, [DETAIL, f'https://{HOST}/api/v4/users/41'])
        self.assertEqual(len(result['metadata']['identities']), 1)

    def test_unconfigured_host_is_never_contacted(self):
        result, calls, _ = self.collect(lambda url: self.fail('no read expected'), trusted={'gitlab.com'})
        self.assertEqual(calls, [])
        self.assertEqual(result['metadata']['failures'], [{'url': WEB, 'reason': 'unconfigured_gitlab_host'}])

    def test_unreachable_host_is_read_once(self):
        def unreachable(url):
            raise URLError('timed out')
        other = 'https://' + HOST + '/group/project/-/merge_requests/8'
        result, calls, warnings = self.collect(unreachable, urls=(WEB, other))
        self.assertEqual(len(calls), 1)
        self.assertEqual(result['items'], [])
        self.assertEqual(len(result['metadata']['failures']), 2)
        self.assertEqual(result['metadata']['status'], 'partial')
        self.assertTrue(warnings)

    def test_failed_read_keeps_other_mrs(self):
        other = 'https://' + HOST + '/group/project/-/merge_requests/8'

        def read(url):
            return response(url, None, status=404) if url == DETAIL else response(url, mr(8))
        result, _, _ = self.collect(read, urls=(WEB, other))
        self.assertEqual([item['number'] for item in result['items']], [8])
        self.assertEqual(result['metadata']['failures'][0]['reason'], 'http_404')

    def test_project_rename_needs_a_redirect_and_identity_must_match(self):
        renamed = mr(repo='group/renamed')
        result, _, _ = self.collect(lambda url: response(url, renamed))
        self.assertEqual(result['items'], [])
        result, _, _ = self.collect(lambda url: response(url, renamed, redirects=[{'url': DETAIL}]))
        self.assertEqual(result['items'][0]['repo'], 'group/renamed')
        result, _, _ = self.collect(lambda url: response(url, mr(number=9)))
        self.assertEqual(result['items'], [])

    def test_unsafe_urls_are_rejected_without_requests(self):
        for url in ('http://' + HOST + '/g/p/-/merge_requests/1', 'https://user:pw@' + HOST + '/g/p/-/merge_requests/1',
                    'https://' + HOST + '/g/p/-/merge_requests/1?x=1', 'https://' + HOST + '/g/../p/-/merge_requests/1'):
            with self.subTest(url=url):
                result, calls, _ = self.collect(lambda url: self.fail('no read expected'), urls=(url,))
                self.assertEqual(calls, [])
                self.assertEqual(len(result['metadata']['failures']), 1)


if __name__ == '__main__':
    unittest.main()
