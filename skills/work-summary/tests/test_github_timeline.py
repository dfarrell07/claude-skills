"""Native public UI proof must survive exact event binding and untrusted HTML."""

import copy
from html import escape
import json
from pathlib import Path
import sys
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from github_timeline import collect_timeline, NoRedirect, public_get, safe_url, validate_timeline  # noqa: E402

ITEM = {'url': 'https://github.com/org/repo/pull/7', 'repo': 'org/repo', 'number': 7}
WHEN = '2026-10-12T12:00:00Z'


def rest_event(identity=42, after='b' * 40):
    return {'id': identity, 'node_id': 'NATIVE' + str(identity), 'event': 'head_ref_force_pushed',
            'created_at': WHEN, 'commit_id': after,
            'url': 'https://api.github.com/repos/org/repo/issues/events/' + str(identity)}


def event_html(identity=42, before='a' * 40, after='b' * 40, page_url=None):
    hydro = {'event_type': 'force_push_timeline_diff.click', 'payload': {
        'event_id': identity, 'originating_url': page_url or ITEM['url'],
        'repository_id': 100, 'pull_request_id': 700}}
    return (f'<div id="event-{identity}" class="TimelineItem js-targetable-element">'
            f'<a data-hydro-click="{escape(json.dumps(hydro), quote=True)}" '
            f'href="/org/repo/compare/{before}..{after}">force-pushed</a>'
            f'<a href="/org/repo/commit/{before}"><code>{before[:7]}</code></a>'
            f'<a href="/org/repo/commit/{after}"><code>{after[:7]}</code></a>'
            f'<a href="#event-{identity}"><relative-time datetime="{WHEN}"></relative-time></a></div>')


def page(body, url=None, status=200):
    return {'url': url or ITEM['url'], 'status': status, 'headers': {}, 'body': body}


def paginate(body, target):
    return ('<div id="js-progressive-timeline-item-container">' + body
            + '<form class="ajax-pagination-form js-ajax-pagination" method="get" '
            + f'action="{escape(target, quote=True)}"><button>Load more</button></form></div>')


class NativeTimelineTest(unittest.TestCase):
    def collect(self, body, native=None):
        return collect_timeline(ITEM, native or [rest_event()], [42], lambda url: page(body, url))

    def test_native_repository_case_variants_preserve_rest_and_paged_html_head_proof(self):
        item = {**ITEM, 'repo': 'ORG/REPO', 'url': ITEM['url'].replace('/org/repo/', '/ORG/REPO/')}
        following = ITEM['url'] + '/timeline_more_items?after_cursor=next'
        bodies = {item['url']: paginate(event_html(), following),
                  following: event_html(identity=43, page_url=following)}
        result = collect_timeline(item, [rest_event(), rest_event(43)], [42, 43],
                                  lambda url: page(bodies[url], url))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([event['id'] for event in result['events']], [42, 43])
        self.assertEqual(result['events'][0]['compare_path'], '/org/repo/compare/' + 'a' * 40 + '..' + 'b' * 40)
        self.assertEqual(result['pages'][0]['body'], bodies[item['url']])
        validate_timeline(result, item, [rest_event(), rest_event(43)], [42, 43])
        for url in [following.replace('/org/repo/', '/wrong/repo/'),
                    following.replace('/pull/', '/PULL/'),
                    following.replace('/timeline_more_items', '/TIMELINE_more_items')]:
            with self.assertRaises(ValueError):
                safe_url(url, item)

    def test_native_full_pairs_bind_rest_identity_and_validate_without_losing_pages(self):
        result = self.collect(event_html())
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(result['events'][0]['before_sha'], 'a' * 40)
        self.assertEqual(result['events'][0]['after_sha'], 'b' * 40)
        self.assertEqual(result['pages'][0]['body'], event_html())
        validate_timeline(result, ITEM, [rest_event()], [42])
        for mutate in (lambda r: r['events'][0].update(before_sha='c' * 40),
                       lambda r: r.update(target_event_ids=[]),
                       lambda r: r['pages'][0].update(url='https://foreign.example.test/'),
                       lambda r: r.update(status='partial')):
            broken = copy.deepcopy(result)
            mutate(broken)
            with self.assertRaises(ValueError):
                validate_timeline(broken, ITEM, [rest_event()], [42])

    def test_user_comment_inert_markup_and_display_abbreviations_cannot_prove_hashes(self):
        for wrapper in ('<div class="markdown-body">{}</div>',
                        '<div class="js-comment-container">{}</div>',
                        '<div class="timeline-comment">{}</div>', '<div class="js-comment">{}</div>', '<pre>{}</pre>',
                        '<code>{}</code>', '<template>{}</template>', '<textarea>{}</textarea>',
                        '<script>{}</script>', '<svg>{}</svg>'):
            with self.subTest(wrapper=wrapper):
                result = self.collect(wrapper.format(event_html()))
                self.assertEqual(result['events'], [])
                self.assertEqual(result['status'], 'unavailable')
        result = self.collect(event_html(before='a' * 7))
        self.assertFalse(result['events'])

    def test_foreign_or_conflicting_native_provenance_is_not_a_head_pair(self):
        body = event_html()
        variants = [body.replace('/org/repo/compare/', '/foreign/repo/compare/'),
                    body.replace('/org/repo/commit/', '/foreign/repo/commit/'),
                    body.replace('https://github.com/org/repo/pull/7', 'https://github.com/org/repo/pull/8'),
                    body.replace('&quot;event_id&quot;: 42', '&quot;event_id&quot;: 43'),
                    body.replace('href="#event-42"', 'href="#event-43"'),
                    body.replace('datetime="' + WHEN + '"', 'datetime="2026-10-13T12:00:00Z"'),
                    body.replace('/commit/' + 'a' * 40, '/commit/' + 'c' * 40)]
        for source in variants:
            with self.subTest(source=source[:90]):
                self.assertFalse(self.collect(source)['events'])
        self.assertFalse(self.collect(body, [rest_event(after='c' * 40)])['events'])

    def test_pagination_retains_pairs_and_identical_boundary_overlap_without_false_partial(self):
        next_url = ITEM['url'] + '/timeline_more_items?after_cursor=native'
        first = paginate(event_html(), next_url)
        second = event_html(page_url=next_url) + event_html(43, 'c' * 40, 'd' * 40, next_url)
        calls = []
        def read(url):
            calls.append(url)
            return page(first if url == ITEM['url'] else second, url)
        native = [rest_event(), rest_event(43, 'd' * 40)]
        result = collect_timeline(ITEM, native, [42, 43], read)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual([row['id'] for row in result['events']], [42, 43])
        self.assertEqual(calls, [ITEM['url'], next_url])
        self.assertEqual(len(result['pages']), 2)
        validate_timeline(result, ITEM, native, [42, 43])

    def test_boundary_overlap_accepts_equivalent_repository_and_time_and_preserves_pages(self):
        next_url = ITEM['url'] + '/timeline_more_items?after_cursor=native'
        first = paginate(event_html(), next_url)
        second = event_html(page_url=next_url).replace('/org/repo/', '/ORG/REPO/').replace(
            WHEN, '2026-10-12T12:00:00+00:00')
        pages = {ITEM['url']: first, next_url: second}
        result = collect_timeline(ITEM, [rest_event()], [42], lambda url: page(pages[url], url))
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['events']), 1)
        self.assertEqual(result['events'][0]['compare_path'], '/org/repo/compare/' + 'a' * 40 + '..' + 'b' * 40)
        self.assertEqual(result['pages'][1]['body'], second)
        self.assertEqual(result['events'][0]['created_at'], WHEN)
        validate_timeline(result, ITEM, [rest_event()], [42])
        pages[next_url] = second.replace('2026-10-12T12:00:00+00:00', '2026-10-12T13:00:00Z')
        invalid_time = collect_timeline(ITEM, [rest_event()], [42], lambda url: page(pages[url], url))
        self.assertEqual(invalid_time['status'], 'partial')
        self.assertEqual(len(invalid_time['events']), 1)
        self.assertTrue(any(error['reason'] == 'native_event_binding_failed' for error in invalid_time['errors']))
        # A genuinely different before hash still invalidates the overlapping
        # event even though both versions independently bind the native after SHA.
        pages[next_url] = second.replace('a' * 40, 'c' * 40)
        conflict = collect_timeline(ITEM, [rest_event()], [42], lambda url: page(pages[url], url))
        self.assertEqual(conflict['status'], 'unavailable')
        self.assertEqual(conflict['events'], [])
        self.assertTrue(any(error['reason'] == 'native_event_observation_changed' for error in conflict['errors']))

    def test_later_failed_page_retains_earlier_native_positive_and_qualifies_gap(self):
        next_url = ITEM['url'] + '/timeline_more_items?after_cursor=native'
        def read(url):
            if url == ITEM['url']:
                return page(paginate(event_html(), next_url), url)
            raise OSError('Native fixture transport failure')
        native = [rest_event(), rest_event(43, 'd' * 40)]
        result = collect_timeline(ITEM, native, [42, 43], read)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([row['id'] for row in result['events']], [42])
        self.assertEqual(len(result['pages']), 2)
        validate_timeline(result, ITEM, native, [42, 43])

    def test_conflicting_pair_withholds_that_before_hash_without_dropping_other_events(self):
        next_url = ITEM['url'] + '/timeline_more_items?after_cursor=native'
        def read(url):
            body = (paginate(event_html(), next_url) if url == ITEM['url'] else
                    event_html(before='c' * 40, page_url=url) + event_html(43, 'c' * 40, 'd' * 40, url))
            return page(body, url)
        native = [rest_event(), rest_event(43, 'd' * 40)]
        result = collect_timeline(ITEM, native, [42, 43], read)
        self.assertEqual(result['status'], 'partial')
        self.assertEqual([row['id'] for row in result['events']], [43])
        self.assertEqual(len(result['pages']), 2)

    def test_foreign_pagination_is_not_requested_and_never_claimed_exhaustive(self):
        calls = []
        def read(url):
            calls.append(url)
            return page(paginate(event_html(), 'https://foreign.example.test/'), url)
        result = collect_timeline(ITEM, [rest_event()], [42], read)
        self.assertEqual(calls, [ITEM['url']])
        self.assertEqual(result['status'], 'partial')
        self.assertEqual(len(result['events']), 1)
        for url in (ITEM['url'] + '?untrusted=yes', ITEM['url'] + '/timeline_more_items?after_cursor=',
                    ITEM['url'] + '/timeline_more_items?extra=1', 'https://user@github.com/org/repo/pull/7',
                    'https://github.com/org/repo/pull/8/timeline_more_items?after_cursor=native'):
            with self.subTest(url=url), self.assertRaises(ValueError):
                safe_url(url, ITEM)

    def test_native_public_transport_preserves_tls_and_never_reads_cookies_or_follows_redirects(self):
        import ssl
        class Response:
            status = 200
            headers = {'Content-Type': 'text/html', 'Set-Cookie': 'not-retained'}
            def __enter__(self):
                return self
            def __exit__(self, *args):
                pass
            def read(self, size):
                return b'Native public fixture'
        class Opener:
            def open(self, request, timeout):
                self.request = request
                return Response()
        opener = Opener()
        with patch('github_timeline.build_opener', return_value=opener) as build:
            result = public_get(ITEM['url'])
        self.assertEqual(result['body'], 'Native public fixture')
        self.assertEqual(result['headers'], {'content-type': 'text/html'})
        self.assertFalse(any(key.lower() in {'authorization', 'cookie'} for key in opener.request.headers))
        handlers = build.call_args.args
        self.assertIsInstance(handlers[0], NoRedirect)
        self.assertTrue(handlers[1]._context.check_hostname)
        self.assertEqual(handlers[1]._context.verify_mode, ssl.CERT_REQUIRED)
        self.assertIsNone(handlers[0].redirect_request(None, None, 302, '', {}, 'https://foreign.example.test/'))


if __name__ == '__main__':
    unittest.main()
