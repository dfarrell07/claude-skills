"""Recover opaque force-push hashes from GitHub's public, native timeline UI."""

from html.parser import HTMLParser
import json
from pathlib import Path
import re
import ssl
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urljoin, urlsplit
from urllib.request import build_opener, HTTPRedirectHandler, HTTPSHandler, Request

from evidence import HASH, HOST, REPO, integer, require, timestamp
from extended_evidence import same_github_api_repo_url, same_github_item_url

MAX_PAGES = 100
MAX_BYTES = 20 * 1024 * 1024
VOID = {'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link', 'meta', 'param', 'source', 'track', 'wbr'}
BLOCKED = {'js-comment-container', 'markdown-body', 'comment-body', 'js-comment-body',
           'timeline-comment', 'timeline-comment-wrapper', 'js-comment', 'user-content'}
BLOCKED_TAGS = {'script', 'style', 'template', 'code', 'pre', 'textarea', 'xmp', 'iframe', 'noscript', 'svg', 'math'}


def safe_url(url, item):
    parts, target = urlsplit(url), urlsplit(item['url'])
    require(parts.scheme == target.scheme == 'https' and parts.netloc.casefold() == target.netloc.casefold()
            and HOST.fullmatch(parts.netloc) and not parts.fragment
            and not any(char in url for char in '\r\n\x00'), 'Foreign timeline URL')
    if parts.port is not None:
        require(1 <= parts.port <= 65535, 'Invalid timeline authority')
    require(target.path == f"/{item['repo']}/pull/{item['number']}" and not target.query,
            'Invalid native timeline item')
    base = parts._replace(query='').geturl()
    if same_github_item_url(base, item['url']):
        require(not parts.query, 'Unexpected timeline page query')
    else:
        require(parts.path.endswith('/timeline_more_items')
                and same_github_item_url(parts._replace(path=parts.path.removesuffix('/timeline_more_items'),
                                                       query='').geturl(), item['url']),
                'Foreign timeline pagination route')
        query = parse_qs(parts.query, keep_blank_values=True)
        require(query and set(query) <= {'after_cursor', 'before_cursor'}
                and all(len(values) == 1 and values[0] and len(values[0]) <= 4096
                        and not any(ord(char) < 32 for char in values[0]) for values in query.values()),
                'Invalid timeline pagination cursor')
    return url


def _same_page_url(left, right):
    """Only repository casing may differ in a native originating-page URL."""
    if (not isinstance(left, str) or not isinstance(right, str)
            or any(ord(char) < 32 or ord(char) == 127 for char in left + right)):
        return False
    try:
        first, second = urlsplit(left), urlsplit(right)
    except ValueError:
        return False
    suffix = '/timeline_more_items'
    if first.path.endswith(suffix) != second.path.endswith(suffix):
        return False
    return same_github_item_url(first._replace(path=first.path.removesuffix(suffix)).geturl(),
                                second._replace(path=second.path.removesuffix(suffix)).geturl())


class NoRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def public_get(url):
    """Read public HTML without cookies, authentication, redirects or netrc."""
    parts = urlsplit(url)
    match = re.fullmatch(r'/([^/]+/[^/]+)/pull/([1-9][0-9]*)(?:/timeline_more_items)?', parts.path)
    require(match is not None, 'Invalid public timeline route')
    item = {'repo': match[1], 'number': int(match[2]),
            'url': f'https://{parts.netloc}/{match[1]}/pull/{match[2]}'}
    safe_url(url, item)
    context = ssl.create_default_context()
    for bundle in ('/etc/pki/tls/cert.pem', '/etc/ssl/certs/ca-certificates.crt'):
        if Path(bundle).is_file():
            context.load_verify_locations(bundle)
    opener = build_opener(NoRedirect(), HTTPSHandler(context=context))
    request = Request(url, headers={'Accept': 'text/html', 'User-Agent': 'work-summary-native-timeline'})
    try:
        response = opener.open(request, timeout=60)
    except HTTPError as error:
        response = error
    except (OSError, URLError) as error:
        return {'url': url, 'status': None, 'headers': {}, 'body': '', 'error': type(error).__name__}
    with response:
        raw = response.read(MAX_BYTES + 1)
        result = {'url': url, 'status': response.status,
                  'headers': {key.lower(): value for key, value in response.headers.items()
                              if key.lower() in {'content-type', 'date', 'etag', 'location'}},
                  'body': raw[:MAX_BYTES].decode('utf-8', errors='replace')}
    if len(raw) > MAX_BYTES:
        result['error'] = 'response_size_limit'
    return result


class Timeline(HTMLParser):
    def __init__(self, item, page_url):
        super().__init__(convert_charrefs=True)
        self.item, self.page_url = item, page_url
        self.stack, self.events, self.next_urls, self.errors = [], [], [], []
        self.current = None

    def handle_starttag(self, tag, attributes):
        attrs = dict(attributes)
        classes = set((attrs.get('class') or '').split())
        blocked = tag in BLOCKED_TAGS or bool(classes & BLOCKED) or any(node['blocked'] for node in self.stack)
        node = {'tag': tag, 'attrs': attrs, 'blocked': blocked}
        event = re.fullmatch(r'event-([1-9][0-9]*)', attrs.get('id', ''))
        if (not blocked and tag == 'div' and event and {'TimelineItem', 'js-targetable-element'} <= classes
                and self.current is None):
            self.current = {'id': int(event[1]), 'depth': len(self.stack),
                            'compares': [], 'commits': [], 'times': []}
        if self.current is not None and not blocked:
            if tag == 'a':
                href = attrs.get('href', '')
                commit = re.fullmatch(r'/([^/]+/[^/]+)/commit/([0-9a-f]{40})', href)
                if (commit and REPO.fullmatch(commit[1])
                        and commit[1].casefold() == self.item['repo'].casefold()):
                    self.current['commits'].append(commit[2])
                if attrs.get('data-hydro-click'):
                    try:
                        hydro = json.loads(attrs['data-hydro-click'])
                        pair = re.fullmatch(r'/([^/]+/[^/]+)/compare/([0-9a-f]{40})\.\.([0-9a-f]{40})', href)
                        payload = hydro['payload']
                        if (hydro['event_type'] == 'force_push_timeline_diff.click' and pair
                                and REPO.fullmatch(pair[1])
                                and pair[1].casefold() == self.item['repo'].casefold()
                                and payload.get('event_id') == self.current['id']
                                and integer(payload.get('event_id'), 1)
                                and _same_page_url(payload.get('originating_url'), self.page_url)
                                and integer(payload.get('repository_id'), 1)
                                and integer(payload.get('pull_request_id'), 1)):
                            self.current['compares'].append((pair[2], pair[3], href, hydro))
                    except (ValueError, KeyError, TypeError, AttributeError):
                        pass
            if tag == 'relative-time' and any(node['tag'] == 'a' and node['attrs'].get('href')
                                            == '#event-' + str(self.current['id']) for node in self.stack):
                value = attrs.get('datetime')
                try:
                    timestamp(value)
                    self.current['times'].append(value)
                except (ValueError, TypeError, OverflowError):
                    pass
        if not blocked and tag == 'form' and {'ajax-pagination-form', 'js-ajax-pagination'} <= classes:
            if (attrs.get('method', '').lower() == 'get'
                    and any(node['attrs'].get('id') == 'js-progressive-timeline-item-container'
                            for node in self.stack)):
                try:
                    target = urljoin(self.item['url'], attrs.get('action', ''))
                    safe_url(target, self.item)
                    require(urlsplit(target).path.endswith('/timeline_more_items'), 'Missing pagination route')
                    self.next_urls.append(target)
                except (ValueError, TypeError):
                    self.errors.append('invalid_native_pagination')
        if tag not in VOID:
            self.stack.append(node)

    def handle_endtag(self, tag):
        found = next((index for index in range(len(self.stack) - 1, -1, -1)
                      if self.stack[index]['tag'] == tag), None)
        if found is None:
            return
        if self.current is not None and found <= self.current['depth']:
            current, self.current = self.current, None
            pairs = {(before, after, path) for before, after, path, _ in current['compares']}
            native_ids = {(hydro['payload']['repository_id'], hydro['payload']['pull_request_id'])
                          for _, _, _, hydro in current['compares']}
            if len(pairs) == len(native_ids) == 1 and len(set(current['times'])) == 1:
                before, after, path = next(iter(pairs))
                if current['commits'] == [before, after]:
                    self.events.append({'id': current['id'], 'created_at': current['times'][0],
                        'before_sha': before, 'after_sha': after, 'page_url': self.page_url,
                        'compare_path': path, 'hydro': current['compares'][0][3]})
        del self.stack[found:]


def parse_page(item, page, *, include_errors=False):
    safe_url(page['url'], item)
    require(page.get('status') == 200 and isinstance(page.get('body'), str), 'Unavailable public timeline HTML')
    parser = Timeline(item, page['url'])
    parser.feed(page['body'])
    parser.close()
    result = parser.events, sorted(set(parser.next_urls))
    return (*result, parser.errors) if include_errors else result


def _replay(item, rest_events, target_ids, pages):
    native = {}
    host = urlsplit(item['url']).netloc
    api_root = 'https://api.github.com' if host.casefold() == 'github.com' else 'https://' + host + '/api/v3'
    for event in rest_events:
        require(integer(event.get('id'), 1) and event.get('event') == 'head_ref_force_pushed'
                and same_github_api_repo_url(event.get('url'), api_root + f"/repos/{item['repo']}/issues/events/{event['id']}"),
                'Invalid native force-push target identity')
        timestamp(event.get('created_at'))
        native[event['id']] = event
    require(all(integer(identity, 1) for identity in target_ids) and set(target_ids) <= native.keys(),
            'Missing native force-push target identity')
    events, errors, next_url, seen, conflicts = {}, [], item['url'], set(), set()
    for position, page in enumerate(pages):
        if page.get('url') != next_url or next_url in seen:
            errors.append({'url': page.get('url'), 'reason': 'pagination_route_mismatch'})
            break
        seen.add(next_url)
        try:
            parsed, targets, page_errors = parse_page(item, page, include_errors=True)
            errors.extend({'url': next_url, 'reason': reason} for reason in page_errors)
            if page.get('error'):
                errors.append({'url': next_url, 'reason': 'partial_native_response'})
            for event in parsed:
                source = native.get(event['id'])
                if (source is None or timestamp(event['created_at']) != timestamp(source['created_at'])
                        or event['after_sha'] != source.get('commit_id')):
                    errors.append({'url': next_url, 'reason': 'native_event_binding_failed'})
                    continue
                previous = events.get(event['id'])
                # Parsing already binds each compare path to this repository
                # and exact hash pair. Repository casing alone is not a conflict.
                if previous is not None and any(previous[field] != event[field] for field in (
                        'created_at', 'before_sha', 'after_sha')):
                    errors.append({'url': next_url, 'reason': 'native_event_observation_changed'})
                    conflicts.add(event['id'])
                    events.pop(event['id'], None)
                elif previous is None and event['id'] not in conflicts:
                    events[event['id']] = event
            require(len(targets) <= 1, 'Ambiguous native pagination')
            next_url = targets[0] if targets else None
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            errors.append({'url': page.get('url'), 'reason': 'unavailable_or_invalid_native_page'})
            next_url = None
            break
        if next_url is None:
            if position != len(pages) - 1:
                errors.append({'reason': 'pages_after_pagination_exhausted'})
            break
    missing = set(target_ids) - events.keys()
    if missing:
        errors.append({'reason': 'target_events_not_recovered', 'event_ids': sorted(missing)})
    if next_url is not None:
        errors.append({'url': next_url, 'reason': 'pagination_incomplete'})
    status = 'partial' if errors and events else 'unavailable' if errors else 'ok'
    return {'item_url': item['url'], 'repo': item['repo'], 'number': item['number'],
            'target_event_ids': sorted(set(target_ids)), 'status': status, 'pages': pages,
            'events': [events[key] for key in sorted(events)], 'errors': errors}


def collect_timeline(item, rest_events, target_ids, read=None):
    """Retain positive native events while qualifying failed or incomplete pages."""
    reader = read or public_get
    pages, url, seen = [], item['url'], set()
    for _ in range(MAX_PAGES):
        if url in seen:
            break
        seen.add(url)
        try:
            page = reader(url)
        except (OSError, ValueError, TypeError) as error:
            page = {'url': url, 'status': None, 'headers': {}, 'body': '', 'error': type(error).__name__}
        if not isinstance(page, dict):
            page = {'url': url, 'status': None, 'headers': {}, 'body': '', 'error': 'invalid_reader_response'}
        pages.append(page)
        try:
            _, targets = parse_page(item, page)
            if len(targets) != 1:
                break
            url = targets[0]
        except (ValueError, TypeError, KeyError, OverflowError, AttributeError):
            break
    return _replay(item, rest_events, target_ids, pages)


def validate_timeline(record, item, rest_events, target_ids):
    require(isinstance(record, dict) and isinstance(record.get('pages'), list), 'Invalid native timeline observation')
    require(len(record['pages']) <= MAX_PAGES, 'Excessive native timeline pages')
    for page in record['pages']:
        require(isinstance(page, dict) and isinstance(page.get('body'), str)
                and (page.get('status') is None or integer(page.get('status'), 100)),
                'Invalid native timeline response')
        safe_url(page['url'], item)
    require(record == _replay(item, rest_events, target_ids, record['pages']),
            'Public timeline observation is not bound to native pages and events')
    for event in record['events']:
        require(HASH.fullmatch(event['before_sha']) and HASH.fullmatch(event['after_sha']),
                'Incomplete historical hash proof')
