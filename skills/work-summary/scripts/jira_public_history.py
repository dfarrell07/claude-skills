"""Supplement authenticated Jira evidence with separately scoped anonymous history."""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import ssl
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from evidence import HOST, decode_json, integer, require, timestamp
from jira_sources import BACKEND, KEY


SCOPE = 'anonymous_visible_history'
PAGE_SIZE = 100
MAX_PAGES = 1000
MAX_BODY = 16 * 1024 * 1024
POSITIVE_ID = re.compile(r'[1-9][0-9]*\Z')
SAFE_HEADERS = {'content-type', 'date', 'location', 'link', 'retry-after',
                'x-page', 'x-next-page', 'x-total', 'x-total-pages'}


def safe_headers(headers):
    return {key.lower(): value for key, value in headers.items() if key.lower() in SAFE_HEADERS}


class PublicReadError(Exception):
    def __init__(self, reason, status=None):
        super().__init__(reason)
        self.reason, self.status = reason, status


class SameOriginRedirect(HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        original, target = urlsplit(req.full_url), urlsplit(newurl)
        if (target.scheme != 'https' or target.hostname != original.hostname
                or target.port not in {None, 443} or target.username is not None
                or target.password is not None or target.fragment):
            raise PublicReadError('unsafe_redirect')
        return super().redirect_request(req, fp, code, msg, headers, newurl)


def read_json(url, receipt=None):
    """Anonymous, verified-TLS GET; no cookie jar, netrc, or auth handlers."""
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'work-summary-history/1'}, method='GET')
    record = {'url': url, 'status': None, 'headers': {}, 'body': ''}
    try:
        context = ssl.create_default_context()
        # Some Python distributions use a separate CA store from the host OS.
        for bundle in ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt'):
            if Path(bundle).is_file():
                context.load_verify_locations(cafile=bundle)
        opener = build_opener(SameOriginRedirect(), HTTPSHandler(context=context))
        with opener.open(request, timeout=10) as response:
            record['status'] = response.status
            record['headers'] = safe_headers(response.headers)
            record['final_url'] = response.geturl() if callable(getattr(response, 'geturl', None)) else url
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise PublicReadError('response_size_limit', response.status)
            record['body'] = raw.decode('utf-8')
    except HTTPError as error:
        record.update(status=error.code, headers=safe_headers(error.headers), final_url=error.geturl(),
                      body=error.read(MAX_BODY).decode('utf-8', errors='replace'), error='http_error')
        if receipt:
            receipt(record)
        raise PublicReadError('http_error', error.code) from error
    except (OSError, URLError, UnicodeError, PublicReadError) as error:
        reason = error.reason if isinstance(error, PublicReadError) else 'transport_error'
        record['error'] = reason
        if receipt:
            receipt(record)
        raise PublicReadError(reason, record['status']) from error
    if receipt:
        receipt(record)
    try:
        value = decode_json(record['body'])
        require(isinstance(value, dict), 'Invalid public JSON object')
        return value
    except (ValueError, TypeError) as error:
        raise PublicReadError('invalid_json', record['status']) from error


def native_basis(issue, site):
    require(isinstance(site, str) and HOST.fullmatch(site) and site == site.casefold(), 'missing_canonical_site')
    require(isinstance(issue.get('key'), str) and KEY.fullmatch(issue['key'])
            and issue.get('url') == f"https://{site}/browse/{issue['key']}", 'invalid_authenticated_issue')
    for name in ('detail', 'source'):
        value = issue.get(name)
        if not isinstance(value, dict) or value.get('key') != issue['key']:
            continue
        issue_id = value.get('id')
        if isinstance(issue_id, bool) or not POSITIVE_ID.fullmatch(str(issue_id)):
            continue
        if value.get('self') is not None:
            require(isinstance(value['self'], str), 'invalid_authenticated_issue_route')
            parts = urlsplit(value['self'])
            backend = site.endswith('.atlassian.net') and BACKEND.fullmatch(parts.hostname or '')
            require(parts.scheme == 'https' and parts.username is None and parts.password is None
                    and not parts.query and not parts.fragment and parts.port in {None, 443}
                    and (parts.netloc.casefold() == site or backend)
                    and re.fullmatch(r'/rest/api/(?:2|3)/issue/' + re.escape(str(issue_id)), parts.path),
                    'invalid_authenticated_issue_route')
        return name, str(issue_id)
    raise ValueError('missing_authenticated_issue_identity')


def public_identity(identity, issue, site, issue_id):
    require(isinstance(identity, dict) and not isinstance(identity.get('id'), bool)
            and str(identity.get('id')) == issue_id and identity.get('key') == issue['key']
            and identity.get('self') == f'https://{site}/rest/api/3/issue/{issue_id}'
            and isinstance(identity.get('fields'), dict) and isinstance(identity['fields'].get('summary'), str),
            'public_identity_mismatch')


def page_issue_route(page, issue, site, issue_id):
    require(isinstance(page, dict) and isinstance(page.get('self'), str), 'invalid_history_page_route')
    parts = urlsplit(page['self'])
    require(parts.scheme == 'https' and parts.netloc.casefold() == site and parts.username is None
            and parts.password is None and not parts.fragment
            and parts.path in {f'/rest/api/3/issue/{issue_id}/changelog',
                               f"/rest/api/3/issue/{issue['key']}/changelog"}
            and set(parse_qs(parts.query)) <= {'maxResults', 'startAt'}, 'invalid_history_page_route')


def inspect_pages(pages, requests, issue, site, issue_id):
    """Describe malformed native rows without dropping independently readable pages."""
    errors, seen, offset, total, ended = [], set(), 0, None, False
    require(len(pages) == len(requests), 'Public history request/page mismatch')
    for index, (page, request) in enumerate(zip(pages, requests)):
        page_issue_route(page, issue, site, issue_id)
        expected_url = f'https://{site}/rest/api/3/issue/{issue_id}/changelog?' + urlencode(
            {'maxResults': PAGE_SIZE, 'startAt': offset})
        if request != expected_url or ended:
            errors.append({'stage': 'pagination', 'reason': 'request_offset_mismatch', 'page': index})
        valid_shape = (integer(page.get('startAt')) and integer(page.get('maxResults'), 1)
                       and page['maxResults'] <= PAGE_SIZE and integer(page.get('total'))
                       and type(page.get('isLast')) is bool and isinstance(page.get('values'), list)
                       and len(page['values']) <= page['maxResults'])
        if not valid_shape:
            errors.append({'stage': 'pagination', 'reason': 'invalid_page_shape', 'page': index})
            continue
        query = parse_qs(urlsplit(page['self']).query)
        if (page['startAt'] != offset or query.get('startAt') != [str(page['startAt'])]
                or query.get('maxResults') not in ([str(page['maxResults'])], [str(PAGE_SIZE)])):
            errors.append({'stage': 'pagination', 'reason': 'page_offset_mismatch', 'page': index})
        if total is None:
            total = page['total']
        elif page['total'] != total:
            errors.append({'stage': 'pagination', 'reason': 'total_changed', 'page': index})
        span = page['startAt'] + len(page['values'])
        if span > page['total']:
            errors.append({'stage': 'pagination', 'reason': 'page_span_exceeds_total', 'page': index})
        if not page['isLast'] and len(page['values']) < page['maxResults']:
            errors.append({'stage': 'pagination', 'reason': 'nonterminal_short_page', 'page': index})
        if page['isLast'] != (span == page['total']):
            errors.append({'stage': 'pagination', 'reason': 'terminal_count_mismatch', 'page': index})
        for row_index, row in enumerate(page['values']):
            try:
                require(isinstance(row, dict) and not isinstance(row.get('id'), bool)
                        and POSITIVE_ID.fullmatch(str(row.get('id'))) and isinstance(row.get('items'), list)
                        and all(isinstance(item, dict) for item in row['items'])
                        and (row.get('author') is None or isinstance(row['author'], dict)), 'invalid_history_row')
                timestamp(row.get('created'))
                identity = str(row['id'])
                if identity in seen:
                    errors.append({'stage': 'history', 'reason': 'duplicate_history_id', 'page': index, 'row': row_index})
                seen.add(identity)
            except (ValueError, TypeError):
                errors.append({'stage': 'history', 'reason': 'invalid_history_row', 'page': index, 'row': row_index})
        offset = page['startAt'] + page['maxResults']
        ended = page['isLast']
    if pages and ended and len(seen) != total:
        errors.append({'stage': 'pagination', 'reason': 'total_row_count_mismatch'})
    return errors, bool(pages and ended and not errors), len(seen)


def collect_public_history(jira, warn, workers=4, receipt=None, reader=None):
    """Preserve public history separately; never replace authenticated details/comments."""
    site = jira['metadata'].get('site')
    valid_site = isinstance(site, str) and HOST.fullmatch(site) and site == site.casefold()
    reader = reader or read_json
    lock, transport = Lock(), {'failures': 0, 'responses': 0}

    def read(url):
        with lock:
            if transport['failures'] >= 3 and not transport['responses']:
                raise PublicReadError('shared_transport_unavailable')
        try:
            value = reader(url, receipt=receipt)
        except PublicReadError as error:
            with lock:
                if error.reason == 'transport_error':
                    transport['failures'] += 1
                elif error.status is not None:
                    transport['responses'] += 1
            raise
        with lock:
            transport['responses'] += 1
        return value

    def collect(issue):
        record = {'scope': SCOPE, 'status': 'unavailable', 'identity_basis': None, 'identity_url': None,
                  'identity': None, 'pages': [], 'page_requests': [], 'errors': [],
                  'authenticated_history': 'unverified'}
        stage, request = 'identity', None
        try:
            require(valid_site, 'missing_canonical_site')
            basis, issue_id = native_basis(issue, site)
            record['identity_basis'] = basis
            request = f'https://{site}/rest/api/3/issue/{issue_id}?fields=summary'
            record['identity_url'] = request
            identity = read(request)
            public_identity(identity, issue, site, issue_id)
            record['identity'], record['status'] = identity, 'partial'
            stage, offset = 'page', 0
            for _ in range(MAX_PAGES):
                request = f'https://{site}/rest/api/3/issue/{issue_id}/changelog?' + urlencode(
                    {'maxResults': PAGE_SIZE, 'startAt': offset})
                page = read(request)
                page_issue_route(page, issue, site, issue_id)
                record['pages'].append(page)
                record['page_requests'].append(request)
                if not (integer(page.get('startAt')) and integer(page.get('maxResults'), 1)
                        and page['maxResults'] <= PAGE_SIZE and type(page.get('isLast')) is bool):
                    break
                if page['isLast'] or page['startAt'] != offset:
                    break
                offset = page['startAt'] + page['maxResults']
            errors, complete, _ = inspect_pages(record['pages'], record['page_requests'], issue, site, issue_id)
            record['errors'].extend(errors)
            if complete:
                record['status'] = 'ok'
            elif not errors:
                record['errors'].append({'stage': 'pagination', 'reason': 'page_limit_reached'})
        except PublicReadError as error:
            record['errors'].append({'stage': stage, 'reason': error.reason, 'http_status': error.status,
                                     'request_url': request})
        except (ValueError, TypeError, KeyError, OSError) as error:
            record['errors'].append({'stage': stage, 'reason': str(error) if type(error) is ValueError else type(error).__name__,
                                     'request_url': request})
        return record

    issues = jira.get('issues', [])
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        records = list(pool.map(collect, issues))
    for issue, record in zip(issues, records):
        issue['public_history'] = record
    complete = sum(record['status'] == 'ok' for record in records)
    status = ('unavailable' if not valid_site else 'ok' if complete == len(issues) else
              'unavailable' if all(record['status'] == 'unavailable' for record in records) else 'partial')
    summary = {'scope': SCOPE, 'status': status,
               'issues_attempted': len(issues), 'issues_complete': complete,
               'history_rows': sum(len(page.get('values', [])) for record in records for page in record['pages']
                                   if isinstance(page.get('values'), list)),
               'authenticated_history': 'unverified'}
    if complete != len(issues):
        warn('Jira anonymous history: some public-visible streams remain incomplete; authenticated history coverage stays separate.')
    return summary


def validate_public_history(data):
    jira = data.get('jira')
    if not isinstance(jira, dict):
        return
    site = jira['metadata'].get('site')
    for issue in jira.get('issues', []):
        record = issue.get('public_history')
        if record is None:
            continue
        require(isinstance(record, dict) and set(record) == {
                    'scope', 'status', 'identity_basis', 'identity_url', 'identity', 'pages', 'page_requests',
                    'errors', 'authenticated_history'} and record.get('scope') == SCOPE
                and record.get('status') in {'ok', 'partial', 'unavailable'}
                and record.get('authenticated_history') == 'unverified'
                and isinstance(record.get('pages'), list) and isinstance(record.get('page_requests'), list)
                and isinstance(record.get('errors'), list), 'Invalid public history scope')
        try:
            basis, issue_id = native_basis(issue, site)
        except (ValueError, TypeError):
            basis, issue_id = None, None
        if basis is not None:
            require(record.get('identity_basis') == basis
                    and record.get('identity_url') == f'https://{site}/rest/api/3/issue/{issue_id}?fields=summary',
                    'Public issue request identity mismatch')
        else:
            require(record.get('identity_basis') is None and record.get('identity_url') is None,
                    'Unknown authenticated issue has a public request')
        if record['identity'] is None:
            require(record['status'] == 'unavailable' and not record['pages'] and not record['page_requests']
                    and record['errors'], 'Unverified public identity has history')
            continue
        require(basis is not None, 'Public identity lacks authenticated source identity')
        public_identity(record['identity'], issue, site, issue_id)
        errors, complete, _ = inspect_pages(record['pages'], record['page_requests'], issue, site, issue_id)
        require(all(error in record['errors'] for error in errors), 'Unqualified public history parsing gap')
        if record['status'] == 'ok':
            require(complete and not record['errors'], 'Incomplete public history marked complete')
        else:
            require(record['status'] == 'partial' and record['errors'], 'Missing public history failure evidence')
    summary = jira['metadata'].get('public_history')
    if summary is not None:
        records = [issue.get('public_history') for issue in jira.get('issues', [])]
        require(all(isinstance(record, dict) for record in records), 'Missing public issue history coverage')
        complete = sum(record['status'] == 'ok' for record in records)
        valid_site = isinstance(site, str) and HOST.fullmatch(site) and site == site.casefold()
        status = ('unavailable' if not valid_site else 'ok' if complete == len(records) else
                  'unavailable' if all(record['status'] == 'unavailable' for record in records) else 'partial')
        expected = {'scope': SCOPE, 'status': status, 'issues_attempted': len(records), 'issues_complete': complete,
                    'history_rows': sum(len(page.get('values', [])) for record in records for page in record['pages']
                                        if isinstance(page.get('values'), list)),
                    'authenticated_history': 'unverified'}
        require(summary == expected, 'Public history summary differs from retained native streams')
