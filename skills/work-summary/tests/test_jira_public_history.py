"""Anonymous Jira history is supplemental native context, never an auth replacement."""

import copy
from email.message import Message
import io
from pathlib import Path
import ssl
import sys
import unittest
from unittest.mock import patch
from urllib.error import HTTPError
from urllib.request import Request

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import jira_public_history as ph  # noqa: E402


SITE = 'tenant.atlassian.net'
BASE = 'https://' + SITE


def issue(number=1):
    source = {'id': str(number), 'key': f'ACM-{number}',
              'self': f'https://jira-prod-us-mc-44-4.prod.atl-paas.net/rest/api/3/issue/{number}',
              'fields': {'summary': 'Private authenticated title', 'comment': {'total': 159, 'comments': [
                  {'id': '17997086', 'body': 'Authenticated private comment.'}]}}}
    return {'key': source['key'], 'url': f"{BASE}/browse/{source['key']}", 'source': copy.deepcopy(source),
            'detail': source, 'comment_pages': [{'values': ['preserved native comments']}]}


def identity(number=1):
    return {'id': str(number), 'key': f'ACM-{number}', 'self': f'{BASE}/rest/api/3/issue/{number}',
            'fields': {'summary': 'Current public title'}}


def history(history_id):
    return {'id': str(history_id), 'created': '2026-10-12T12:34:56.926+0000',
            'author': {'accountId': 'native-account', 'displayName': 'Native actor'},
            'items': [{'field': 'status', 'fromString': 'New', 'toString': 'Closed'}]}


def page(values, offset=0, total=None, last=True, capacity=100):
    return {'self': f'{BASE}/rest/api/3/issue/ACM-1/changelog?maxResults=100&startAt={offset}',
            'startAt': offset, 'maxResults': capacity, 'total': len(values) if total is None else total,
            'isLast': last, 'values': values}


class JiraPublicHistoryTest(unittest.TestCase):
    def setUp(self):
        self.warnings, self.calls = [], []

    def collect(self, pages=None, public=None, jira=None):
        pages = [page([history(1)])] if pages is None else pages
        jira = {'metadata': {'site': SITE}, 'issues': [issue()], 'activity': [{'native': 'unchanged'}]} if jira is None else jira
        position = iter(pages)

        def read(url, receipt=None):
            self.calls.append(url)
            value = (identity() if public is None else public) if '?fields=' in url else next(position)
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)

        result = ph.collect_public_history(jira, self.warnings.append, workers=1, reader=read)
        jira['metadata']['public_history'] = result
        ph.validate_public_history({'jira': jira})
        return jira

    def test_backend_identity_and_native_precision_preserve_authenticated_evidence(self):
        original = issue()
        jira = self.collect(jira={'metadata': {'site': SITE}, 'issues': [copy.deepcopy(original)],
                                 'activity': [{'native': 'unchanged'}]})
        captured = jira['issues'][0]
        self.assertEqual({key: captured[key] for key in original}, original)
        self.assertEqual(jira['activity'], [{'native': 'unchanged'}])
        self.assertEqual(captured['detail']['fields']['comment']['total'], 159)
        self.assertEqual(captured['public_history']['pages'][0]['values'][0]['created'],
                         '2026-10-12T12:34:56.926+0000')
        self.assertEqual(captured['public_history']['status'], 'ok')
        self.assertEqual(captured['public_history']['authenticated_history'], 'unverified')
        self.assertFalse(self.warnings)

    def test_capped_pages_follow_native_offsets_and_canonical_key_self(self):
        jira = self.collect([page([history(1), history(2)], total=3, last=False, capacity=2),
                             page([history(3)], offset=2, total=3, capacity=2)])
        self.assertTrue(self.calls[-1].endswith('maxResults=100&startAt=2'))
        self.assertEqual(jira['metadata']['public_history']['history_rows'], 3)
        self.assertEqual(jira['issues'][0]['public_history']['status'], 'ok')

    def test_native_count_drift_duplicate_and_bad_rows_remain_qualified_partial(self):
        cases = [
            [page([history(1), history(2)], total=3, last=False, capacity=2),
             page([history(3), history(4)], offset=2, total=4, capacity=2)],
            [page([history(1), history(2)], total=3, last=False, capacity=2),
             page([history(2)], offset=2, total=3, capacity=2)],
            [page([history(1), {**history(2), 'created': 'invalid'}])],
        ]
        for pages in cases:
            with self.subTest(pages=pages):
                jira = self.collect(pages)
                record = jira['issues'][0]['public_history']
                self.assertEqual(record['pages'], pages)
                self.assertEqual(record['status'], 'partial')
                self.assertTrue(record['errors'])

    def test_public_identity_mismatch_never_attaches_other_issue_history(self):
        jira = self.collect(public=identity(2))
        record = jira['issues'][0]['public_history']
        self.assertEqual(len(self.calls), 1)
        self.assertIsNone(record['identity'])
        self.assertFalse(record['pages'])
        self.assertEqual(record['status'], 'unavailable')

    def test_http_failure_preserves_previous_native_page(self):
        pages = [page([history(1), history(2)], total=3, last=False, capacity=2),
                 ph.PublicReadError('http_error', 403)]
        jira = self.collect(pages)
        record = jira['issues'][0]['public_history']
        self.assertEqual(len(record['pages']), 1)
        self.assertEqual(record['status'], 'partial')
        self.assertEqual(record['errors'][0]['http_status'], 403)

    def test_foreign_self_page_is_not_bound_to_known_issue(self):
        foreign = page([history(1)])
        foreign['self'] = foreign['self'].replace('ACM-1', 'ACM-999')
        jira = self.collect([foreign])
        self.assertFalse(jira['issues'][0]['public_history']['pages'])
        self.assertEqual(jira['issues'][0]['public_history']['status'], 'partial')

    def test_page_limit_and_wrong_offsets_do_not_claim_complete(self):
        pages = [page([history(1)], total=3, last=False, capacity=1),
                 page([history(2)], offset=1, total=3, last=False, capacity=1)]
        with patch.object(ph, 'MAX_PAGES', 2):
            jira = self.collect(pages)
        self.assertEqual(jira['issues'][0]['public_history']['errors'][-1]['reason'], 'page_limit_reached')
        wrong = page([history(1)], offset=100)
        jira = self.collect([wrong])
        self.assertEqual(jira['issues'][0]['public_history']['status'], 'partial')

    def test_failed_identity_has_no_history_and_retains_known_request(self):
        jira = self.collect(public=ph.PublicReadError('http_error', 404))
        record = jira['issues'][0]['public_history']
        self.assertEqual(record['status'], 'unavailable')
        self.assertIsNone(record['identity'])
        self.assertEqual(record['identity_url'], f'{BASE}/rest/api/3/issue/1?fields=summary')

    def test_unknown_site_and_missing_identity_make_no_network_requests(self):
        for jira in ({'metadata': {'site': None}, 'issues': [issue()]},
                     {'metadata': {'site': SITE}, 'issues': [{'key': 'ACM-1', 'url': BASE + '/browse/ACM-1'}]}):
            with self.subTest(jira=jira):
                with patch.object(ph, 'read_json') as read:
                    summary = ph.collect_public_history(jira, self.warnings.append)
                read.assert_not_called()
                self.assertEqual(summary['status'], 'unavailable')
                ph.validate_public_history({'jira': jira})

    def test_shared_unreachable_transport_does_not_spend_one_timeout_per_issue(self):
        jira = {'metadata': {'site': SITE}, 'issues': [issue(number) for number in range(1, 11)]}
        with patch.object(ph, 'read_json', side_effect=ph.PublicReadError('transport_error')) as read:
            summary = ph.collect_public_history(jira, self.warnings.append, workers=1)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(summary['status'], 'unavailable')
        self.assertTrue(all(record['public_history']['status'] == 'unavailable' for record in jira['issues']))

    def test_forged_identity_native_pages_and_summary_are_rejected(self):
        jira = self.collect()
        for field, value in [('identity_url', 'https://foreign.test/rest/api/3/issue/1?fields=summary'),
                             ('identity_basis', 'fabricated'), ('authenticated_history', 'complete'),
                             ('page_requests', ['https://foreign.test/history'])]:
            with self.subTest(field=field):
                bad = copy.deepcopy(jira)
                bad['issues'][0]['public_history'][field] = value
                with self.assertRaises(ValueError):
                    ph.validate_public_history({'jira': bad})
        bad = copy.deepcopy(jira)
        bad['issues'][0]['public_history']['pages'] = []
        bad['issues'][0]['public_history']['page_requests'] = []
        with self.assertRaises(ValueError):
            ph.validate_public_history({'jira': bad})
        bad = copy.deepcopy(jira)
        bad['metadata']['public_history']['history_rows'] += 1
        with self.assertRaises(ValueError):
            ph.validate_public_history({'jira': bad})

    def test_unsafe_redirects_never_change_origin_or_scheme(self):
        handler = ph.SameOriginRedirect()
        request = Request(BASE + '/rest/api/3/issue/1')
        for target in ('http://' + SITE + '/x', 'https://foreign.test/x',
                       'https://user:secret@' + SITE + '/x', BASE + ':8443/x'):
            with self.subTest(target=target):
                with self.assertRaises(ph.PublicReadError):
                    handler.redirect_request(request, None, 302, '', {}, target)

    def test_native_reader_is_verified_tls_and_records_before_json_failure(self):
        class Response:
            status = 200
            headers = {'Content-Type': 'application/json'}

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, maximum):
                return b'not json'

        receipts = []
        with patch.object(ph, 'build_opener') as build:
            build.return_value.open.return_value = Response()
            with self.assertRaises(ph.PublicReadError):
                ph.read_json(BASE + '/rest/api/3/issue/1?fields=summary', receipt=receipts.append)
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertNotIn('Authorization', dict(request.header_items()))
            self.assertNotIn('Cookie', dict(request.header_items()))
            context = build.call_args.args[1]._context
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual(receipts[0]['body'], 'not json')

    def test_native_http_error_receipt_keeps_actual_status_and_body(self):
        headers = Message()
        headers['Content-Type'] = 'application/json'
        error = HTTPError(BASE + '/rest/api/3/issue/1', 404, 'Missing', headers, io.BytesIO(b'{"error":"private"}'))
        receipts = []
        with patch.object(ph, 'build_opener') as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(ph.PublicReadError):
                ph.read_json(error.url, receipt=receipts.append)
        self.assertEqual(receipts[0]['status'], 404)
        self.assertEqual(receipts[0]['body'], '{"error":"private"}')


if __name__ == '__main__':
    unittest.main()
