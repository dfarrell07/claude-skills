"""Anonymous Jira history is supplemental native context, never an auth replacement."""

import copy
from datetime import datetime, timezone
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
START = datetime(2026, 10, 12, tzinfo=timezone.utc)
END = datetime(2026, 10, 13, tzinfo=timezone.utc)


def issue(number=1):
    source = {'id': str(number), 'key': f'ACM-{number}',
              'fields': {'summary': 'Private authenticated title', 'comment': {'total': 1, 'comments': [
                  {'id': '77', 'body': 'Authenticated private comment.'}]}}}
    return {'key': source['key'], 'url': f"{BASE}/browse/{source['key']}", 'source': copy.deepcopy(source),
            'detail': source}


def history(history_id):
    return {'id': str(history_id), 'created': '2026-10-12T12:34:56.926+0000',
            'author': {'accountId': 'native-account', 'displayName': 'Native actor'},
            'items': [{'field': 'status', 'fromString': 'New', 'toString': 'Closed'}]}


def page(values, offset=0, last=True, capacity=100):
    return {'startAt': offset, 'maxResults': capacity, 'isLast': last, 'values': values}


def changelog_url(issue_id, offset=0):
    return f'{BASE}/rest/api/3/issue/{issue_id}/changelog?maxResults=100&startAt={offset}'


class JiraPublicHistoryTest(unittest.TestCase):
    def setUp(self):
        self.warnings, self.calls = [], []

    def collect(self, pages=None, jira=None):
        pages = [page([history(1)])] if pages is None else pages
        jira = {'metadata': {'site': SITE}, 'issues': [issue()], 'activity': [{'native': 'unchanged'}]} if jira is None else jira
        position = iter(pages)

        def read(url):
            self.calls.append(url)
            value = next(position)
            if isinstance(value, Exception):
                raise value
            return copy.deepcopy(value)

        jira['metadata']['public_history'] = ph.collect_public_history(jira, self.warnings.append, workers=1, reader=read)
        return jira

    def events(self, jira):
        jira['metadata'].update(identity_verified=True, account_id='native-account')
        return list(ph.public_field_events(jira, START, END))

    def test_reads_the_changelog_by_numeric_id_and_keeps_authenticated_evidence(self):
        original = issue()
        jira = self.collect()
        captured = jira['issues'][0]
        self.assertEqual(self.calls, [changelog_url(1)])
        self.assertEqual({key: captured[key] for key in original}, original)
        self.assertEqual(jira['activity'], [{'native': 'unchanged'}])
        self.assertEqual(captured['public_history']['status'], 'ok')
        self.assertEqual(captured['public_history']['pages'][0]['values'][0]['created'], '2026-10-12T12:34:56.926+0000')
        self.assertEqual(jira['metadata']['public_history']['history_rows'], 1)
        self.assertFalse(self.warnings)

    def test_search_identity_is_used_when_no_detail_was_read(self):
        searched = issue(7)
        searched.pop('detail')
        self.collect(jira={'metadata': {'site': SITE}, 'issues': [searched]})
        self.assertEqual(self.calls, [changelog_url(7)])
        for broken in ({'key': 'ACM-8', 'id': '7'}, {'key': 'ACM-7', 'id': True}, {'key': 'ACM-7', 'id': '07'}):
            with self.subTest(broken=broken):
                self.calls.clear()
                ticket = {'key': 'ACM-7', 'url': BASE + '/browse/ACM-7', 'source': broken}
                summary = ph.collect_public_history({'metadata': {'site': SITE}, 'issues': [ticket]}, self.warnings.append,
                                                    reader=lambda url: self.fail('no ID, no request'))
                self.assertEqual(summary['status'], 'unavailable')

    def test_reuse_keeps_records_reads_only_new_issues_and_rebuilds_totals(self):
        jira = self.collect()
        first = copy.deepcopy(jira['issues'][0]['public_history'])
        jira['issues'].append(issue(2))
        calls = []

        def read(url):
            calls.append(url)
            return page([history(2)])

        summary = ph.collect_public_history(jira, self.warnings.append, workers=1, reader=read, reuse=True)
        self.assertEqual(calls, [changelog_url(2)])
        self.assertEqual(jira['issues'][0]['public_history'], first)
        self.assertEqual((summary['issues_attempted'], summary['issues_complete'], summary['history_rows']), (2, 2, 2))

    def test_pages_follow_offsets_until_the_last_page(self):
        jira = self.collect([page([history(1), history(2)], last=False, capacity=3),
                             page([history(3)], offset=3, capacity=3)])
        self.assertEqual(self.calls, [changelog_url(1), changelog_url(1, 3)])
        self.assertEqual(jira['metadata']['public_history']['history_rows'], 3)
        self.assertEqual(jira['issues'][0]['public_history']['status'], 'ok')

    def test_page_limit_and_malformed_pages_are_partial_and_warned(self):
        with patch.object(ph, 'MAX_PAGES', 2):
            jira = self.collect([page([history(1)], last=False, capacity=1),
                                 page([history(2)], offset=1, last=False, capacity=1)])
        record = jira['issues'][0]['public_history']
        self.assertEqual((record['status'], record['errors'][-1]['reason']), ('partial', 'page_limit_reached'))
        self.assertEqual(len(self.calls), 2)
        no_size = page([])
        no_size.pop('maxResults')
        for bad in (page([history(1)], offset=100), {**page([]), 'isLast': 'yes'}, {**page([]), 'values': None},
                    {'errorMessages': ['unexpected']}, page([], capacity=0), no_size, ['not', 'a', 'page']):
            with self.subTest(bad=bad):
                self.warnings.clear()
                record = self.collect([bad])['issues'][0]['public_history']
                self.assertEqual((record['status'], record['pages']), ('partial', []))
                self.assertEqual(record['errors'][0]['reason'], 'invalid_history_page')
                self.assertEqual(len(self.warnings), 1)

    def test_http_failure_after_a_page_keeps_it_and_warns(self):
        for status in (403, 503):
            with self.subTest(status=status):
                self.warnings.clear()
                jira = self.collect([page([history(1), history(2)], last=False, capacity=2),
                                     ph.PublicReadError('http_error', status)])
                record = jira['issues'][0]['public_history']
                self.assertEqual((len(record['pages']), record['status']), (1, 'partial'))
                self.assertEqual(record['errors'][0]['http_status'], status)
                self.assertEqual(record['errors'][0]['request_url'], changelog_url(1, 2))
                self.assertEqual(len(self.warnings), 1)
                self.assertEqual(len(self.events(jira)), 2)

    def test_private_ticket_answers_are_expected_and_not_warned(self):
        for status in (401, 403, 404):
            with self.subTest(status=status):
                record = self.collect([ph.PublicReadError('http_error', status)])['issues'][0]['public_history']
                self.assertEqual((record['status'], record['pages']), ('unavailable', []))
                self.assertEqual(record['errors'][0]['http_status'], status)
                self.assertEqual(self.warnings, [])

    def test_other_first_page_failures_are_warned(self):
        for error in (ph.PublicReadError('http_error', 429), ph.PublicReadError('http_error', 503),
                      ph.PublicReadError('transport_error'), ph.PublicReadError('invalid_json', 200)):
            with self.subTest(error=error.reason, status=error.status):
                self.warnings.clear()
                record = self.collect([error])['issues'][0]['public_history']
                self.assertEqual((record['status'], record['pages']), ('failed', []))
                self.assertEqual(len(self.warnings), 1)

    def test_mixed_tickets_give_partial_summary_and_one_warning(self):
        def run(second):
            self.warnings.clear()
            answers = {changelog_url(1): page([history(1)]), changelog_url(2): second}

            def read(url):
                value = answers[url]
                if isinstance(value, Exception):
                    raise value
                return value
            jira = {'metadata': {'site': SITE}, 'issues': [issue(1), issue(2)]}
            return ph.collect_public_history(jira, self.warnings.append, workers=1, reader=read)

        summary = run(ph.PublicReadError('http_error', 404))
        self.assertEqual((summary['status'], summary['issues_attempted'], summary['issues_complete']), ('partial', 2, 1))
        self.assertEqual(self.warnings, [])
        summary = run(ph.PublicReadError('http_error', 429))
        self.assertEqual(summary['status'], 'partial')
        self.assertEqual(len(self.warnings), 1)
        jira = {'metadata': {'site': SITE}, 'issues': [issue(1), issue(2)]}
        summary = ph.collect_public_history(jira, self.warnings.append, workers=1,
                                            reader=lambda url: (_ for _ in ()).throw(ph.PublicReadError('http_error', 404)))
        self.assertEqual(summary['status'], 'unavailable')

    def test_unknown_site_makes_no_network_requests(self):
        for site in (None, 'Tenant.atlassian.net', 'not a host'):
            with self.subTest(site=site):
                jira = {'metadata': {'site': site}, 'issues': [issue()]}
                with patch.object(ph, 'read_json') as read:
                    summary = ph.collect_public_history(jira, self.warnings.append)
                read.assert_not_called()
                self.assertEqual(summary['status'], 'unavailable')
                self.assertEqual(self.warnings, [])

    def test_issue_id_prefers_detail_and_falls_back_to_a_matching_search_row(self):
        ticket = issue(1)
        ticket['source']['id'] = '9'
        self.assertEqual(ph.issue_id(ticket), '1')
        ticket['detail'] = {'key': 'OTHER-1', 'id': '5'}
        self.assertEqual(ph.issue_id(ticket), '9')
        ticket['detail'] = {'key': 'ACM-1', 'id': 'not-a-number'}
        self.assertEqual(ph.issue_id(ticket), '9')

    def test_shared_unreachable_transport_does_not_spend_one_timeout_per_issue(self):
        jira = {'metadata': {'site': SITE}, 'issues': [issue(number) for number in range(1, 11)]}
        with patch.object(ph, 'read_json', side_effect=ph.PublicReadError('transport_error')) as read:
            summary = ph.collect_public_history(jira, self.warnings.append, workers=1)
        self.assertEqual(read.call_count, 3)
        self.assertEqual(summary['status'], 'unavailable')
        self.assertTrue(all(record['public_history']['status'] == 'failed' for record in jira['issues']))
        self.assertEqual(len(self.warnings), 1)

    def test_answered_requests_keep_the_transport_breaker_closed(self):
        for first in (page([history(1)]), ph.PublicReadError('http_error', 404), ph.PublicReadError('http_error', 500)):
            with self.subTest(first=first):
                answers = [first] + [ph.PublicReadError('transport_error')] * 3 + [page([history(5)])]
                calls = []

                def read(url):
                    calls.append(url)
                    value = answers[len(calls) - 1]
                    if isinstance(value, Exception):
                        raise value
                    return value
                jira = {'metadata': {'site': SITE}, 'issues': [issue(number) for number in range(1, 6)]}
                ph.collect_public_history(jira, self.warnings.append, workers=1, reader=read)
                self.assertEqual(len(calls), 5)
                self.assertEqual(jira['issues'][4]['public_history']['status'], 'ok')

    def test_public_field_events_bind_exact_actor_and_native_time_boundaries(self):
        rows = [history(index) for index in range(1, 7)]
        for row, at in zip(rows, ['2026-10-12T00:00:00.000+0000', '2026-10-13T00:00:00.000+0000',
                                 '2026-10-11T23:59:59.999+0000', '2026-10-13T00:00:00.001+0000',
                                 '2026-10-12T12:00:00.000+0000', '2026-10-12T12:00:00.000+0000']):
            row['created'] = at
        rows[4]['author']['accountId'] = 'different-actor'
        rows[5]['author'] = None
        jira = self.collect([page(rows)])
        events = self.events(jira)
        self.assertEqual([event['history_id'] for event in events], ['1', '2'])
        self.assertEqual(events[0], {'key': 'ACM-1', 'url': BASE + '/browse/ACM-1',
                                    'history_id': '1', 'occurred_at': rows[0]['created'],
                                    'fields': rows[0]['items'], 'actor_account_id': 'native-account'})
        jira['metadata']['identity_verified'] = False
        self.assertEqual(list(ph.public_field_events(jira, START, END)), [])

    def test_public_field_events_need_a_verified_account(self):
        jira = self.collect()
        self.assertEqual(len(self.events(jira)), 1)
        jira['issues'][0]['public_history']['pages'][0]['values'][0]['author'] = {}
        for metadata in ({'identity_verified': 'yes'}, {'account_id': ''}, {'account_id': None}):
            with self.subTest(metadata=metadata):
                broken = {**jira, 'metadata': {**jira['metadata'], **metadata}}
                self.assertEqual(list(ph.public_field_events(broken, START, END)), [])
        self.assertEqual(list(ph.public_field_events(jira, None, END)), [])

    def test_public_field_events_are_per_ticket(self):
        jira = self.collect([page([history(1)]), page([history(1)])],
                            jira={'metadata': {'site': SITE}, 'issues': [issue(1), issue(2)]})
        self.assertEqual([event['key'] for event in self.events(jira)], ['ACM-1', 'ACM-2'])
        jira['issues'][0]['public_history']['pages'].append({'values': None})
        self.assertEqual(len(self.events(jira)), 2)

    def test_public_field_events_skip_bad_rows_and_deduplicate(self):
        row = history(1)
        jira = self.collect([page([row, copy.deepcopy(row), {'id': 'bad native tail'}, {**history(2), 'created': 'bad'},
                                   {**history(3), 'items': []}, {**history(4), 'id': True},
                                   {**history(5), 'id': 'abc'}, {**history(6), 'id': '06'}])])
        self.assertEqual([event['history_id'] for event in self.events(jira)], ['1'])
        for value in jira['issues'][0]['public_history']['pages'][0]['values'][:2]:
            value['items'][0]['field'] = None
        self.assertEqual(self.events(jira), [])

    def test_unsafe_redirects_never_change_origin_or_scheme(self):
        handler = ph.SameOriginRedirect()
        request = Request(BASE + '/rest/api/3/issue/1')
        for target in ('http://' + SITE + '/x', 'https://foreign.test/x', 'https://user:secret@' + SITE + '/x',
                       'https://user@' + SITE + '/x', 'https://:secret@' + SITE + '/x', BASE + '/x#part',
                       BASE + ':8443/x'):
            with self.subTest(target=target):
                with self.assertRaises(ph.PublicReadError):
                    handler.redirect_request(request, None, 302, '', {}, target)
        for target in (BASE + '/x', BASE + ':443/x'):
            with self.subTest(target=target):
                self.assertEqual(handler.redirect_request(request, None, 302, '', {}, target).full_url, target)

    def test_reader_is_anonymous_verified_tls_and_rejects_non_json(self):
        class Response:
            status = 200

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return None

            def read(self, maximum):
                return b'not json'

        with patch.object(ph, 'build_opener') as build:
            build.return_value.open.return_value = Response()
            with self.assertRaises(ph.PublicReadError) as caught:
                ph.read_json(changelog_url(1))
            request = build.return_value.open.call_args.args[0]
            self.assertEqual(request.get_method(), 'GET')
            self.assertNotIn('Authorization', dict(request.header_items()))
            self.assertNotIn('Cookie', dict(request.header_items()))
            self.assertIsInstance(build.call_args.args[0], ph.SameOriginRedirect)
            self.assertEqual(build.return_value.open.call_args.kwargs, {'timeout': 10})
            context = build.call_args.args[1]._context
            self.assertTrue(context.check_hostname)
            self.assertEqual(context.verify_mode, ssl.CERT_REQUIRED)
        self.assertEqual((caught.exception.reason, caught.exception.status), ('invalid_json', 200))
        for body, reason in ((b'[1]', 'invalid_json'), (b'{"a": "' + b'x' * 64 + b'"}', 'response_size_limit')):
            with self.subTest(reason=reason), patch.object(ph, 'MAX_BODY', 32), \
                    patch.object(ph, 'build_opener') as build:
                Response.read = lambda self, maximum, body=body: body[:maximum]
                build.return_value.open.return_value = Response()
                with self.assertRaises(ph.PublicReadError) as caught:
                    ph.read_json(changelog_url(1))
                self.assertEqual(caught.exception.reason, reason)
        with patch.object(ph, 'build_opener') as build:
            build.return_value.open.side_effect = OSError('unreachable')
            with self.assertRaises(ph.PublicReadError) as caught:
                ph.read_json(changelog_url(1))
        self.assertEqual((caught.exception.reason, caught.exception.status), ('transport_error', None))

    def test_reader_http_error_keeps_status(self):
        headers = Message()
        headers['Content-Type'] = 'application/json'
        error = HTTPError(changelog_url(1), 404, 'Missing', headers, io.BytesIO(b'{"error":"private"}'))
        with patch.object(ph, 'build_opener') as build:
            build.return_value.open.side_effect = error
            with self.assertRaises(ph.PublicReadError) as caught:
                ph.read_json(error.url)
        self.assertEqual((caught.exception.reason, caught.exception.status), ('http_error', 404))


if __name__ == '__main__':
    unittest.main()
