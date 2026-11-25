"""Field changes on publicly visible Jira tickets, read anonymously.

acli exposes no changelog. Jira serves a public ticket's changelog without login, by the
numeric issue ID the authenticated read returned. A private ticket answers 404 (401 or 403
where the site allows no anonymous reads), which is expected; any other failure is a gap.
"""

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
import re
import ssl
from threading import Lock
from urllib.error import HTTPError, URLError
from urllib.parse import urlencode, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from evidence import HOST, decode_json, integer, require, timestamp


PAGE_SIZE = 100
MAX_PAGES = 1000
MAX_BODY = 16 * 1024 * 1024
POSITIVE_ID = re.compile(r'[1-9][0-9]*\Z')
NOT_PUBLIC = {401, 403, 404}  # private ticket, or a site that allows no anonymous reads


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


def read_json(url):
    """Anonymous, verified-TLS GET; no cookie jar, netrc, or auth handlers."""
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'work-summary-history/1'}, method='GET')
    status = None
    try:
        context = ssl.create_default_context()
        # Some Python distributions use a separate CA store from the host OS.
        for bundle in ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt'):
            if Path(bundle).is_file():
                context.load_verify_locations(cafile=bundle)
        opener = build_opener(SameOriginRedirect(), HTTPSHandler(context=context))
        with opener.open(request, timeout=10) as response:
            status = response.status
            raw = response.read(MAX_BODY + 1)
            if len(raw) > MAX_BODY:
                raise PublicReadError('response_size_limit', status)
            body = raw.decode('utf-8')
    except HTTPError as error:
        raise PublicReadError('http_error', error.code) from error
    except (OSError, URLError, UnicodeError, PublicReadError) as error:
        raise PublicReadError(error.reason if isinstance(error, PublicReadError) else 'transport_error',
                              status) from error
    try:
        value = decode_json(body)
        require(isinstance(value, dict), 'Invalid public JSON object')
        return value
    except (ValueError, TypeError) as error:
        raise PublicReadError('invalid_json', status) from error


def issue_id(issue):
    """The numeric ID the authenticated detail or search read returned for this key."""
    for name in ('detail', 'source'):
        value = issue.get(name)
        if isinstance(value, dict) and value.get('key') == issue.get('key'):
            ident = value.get('id')
            if not isinstance(ident, bool) and POSITIVE_ID.fullmatch(str(ident)):
                return str(ident)
    return None


def public_field_events(jira, start, end):
    """Yield own native field actions; a tracker change does not prove delivery."""
    metadata = jira.get('metadata', {})
    principal = metadata.get('account_id')
    if (metadata.get('identity_verified') is not True or not isinstance(principal, str)
            or not principal or start is None or end is None):
        return
    seen = set()
    for issue in jira.get('issues', []):
        record = issue.get('public_history')
        if not isinstance(record, dict):
            continue
        for page in record.get('pages', []):
            values = page.get('values')
            if not isinstance(values, list):
                continue
            for history in values:
                try:
                    require(isinstance(history, dict) and not isinstance(history.get('id'), bool)
                            and POSITIVE_ID.fullmatch(str(history.get('id')))
                            and isinstance(history.get('author'), dict)
                            and history['author'].get('accountId') == principal
                            and isinstance(history.get('items'), list) and history['items']
                            and all(isinstance(item, dict) and isinstance(item.get('field'), str)
                                    for item in history['items']), 'invalid_own_history_row')
                    at = timestamp(history.get('created'))
                    require(start <= at <= end, 'outside_requested_window')
                except (ValueError, TypeError):
                    continue
                key = issue['key'], str(history['id'])
                if key in seen:
                    continue
                seen.add(key)
                yield {'key': issue['key'], 'url': issue['url'], 'history_id': str(history['id']),
                       'occurred_at': history['created'], 'fields': history['items'],
                       'actor_account_id': principal}


def collect_public_history(jira, warn, workers=4, reader=None, reuse=False):
    """Attach each ticket's public changelog pages; never replace authenticated evidence."""
    site = jira['metadata'].get('site')
    valid_site = isinstance(site, str) and HOST.fullmatch(site) and site == site.casefold()
    reader = reader or read_json
    lock, transport = Lock(), {'failures': 0, 'responses': 0}

    def read(url):
        with lock:
            if transport['failures'] >= 3 and not transport['responses']:
                raise PublicReadError('shared_transport_unavailable')
        try:
            value = reader(url)
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
        record = {'status': 'unavailable', 'pages': [], 'errors': []}
        request = None
        try:
            require(valid_site, 'missing_canonical_site')
            ident = issue_id(issue)
            require(ident is not None, 'missing_issue_id')
            offset = 0
            for _ in range(MAX_PAGES):
                request = f'https://{site}/rest/api/3/issue/{ident}/changelog?' + urlencode(
                    {'maxResults': PAGE_SIZE, 'startAt': offset})
                page = read(request)
                record['status'] = 'partial'  # Jira answered, so the ticket is public
                require(isinstance(page, dict) and integer(page.get('startAt')) and page['startAt'] == offset
                        and integer(page.get('maxResults'), 1) and type(page.get('isLast')) is bool
                        and isinstance(page.get('values'), list), 'invalid_history_page')
                record['pages'].append(page)
                if page['isLast']:
                    record['status'] = 'ok'
                    break
                offset += page['maxResults']
            else:
                record['errors'].append({'reason': 'page_limit_reached'})
        except PublicReadError as error:
            record['errors'].append({'reason': error.reason, 'http_status': error.status, 'request_url': request})
            if record['status'] == 'unavailable' and error.status not in NOT_PUBLIC:
                record['status'] = 'failed'  # not known to be private: rate limit, outage, bad response
        except (ValueError, TypeError) as error:
            record['errors'].append({'reason': str(error), 'request_url': request})
        return record

    issues = jira.get('issues', [])
    pending = [issue for issue in issues if not reuse or issue.get('public_history') is None]
    with ThreadPoolExecutor(max_workers=max(1, min(4, workers))) as pool:
        collected = list(pool.map(collect, pending))
    for issue, record in zip(pending, collected):
        issue['public_history'] = record
    records = [issue['public_history'] for issue in issues]
    complete = sum(record['status'] == 'ok' for record in records)
    status = ('unavailable' if not valid_site else 'ok' if complete == len(issues) else
              'partial' if any(record['pages'] for record in records) else 'unavailable')
    summary = {'status': status, 'issues_attempted': len(issues), 'issues_complete': complete,
               'history_rows': sum(len(page['values']) for record in records for page in record['pages'])}
    # A private ticket's 404 is expected; any other failure may hide your field changes.
    if any(record['status'] in ('partial', 'failed') for record in records):
        warn("Jira public history: some tickets' change history could not be fully read; your status and "
             "field changes on public tickets may be missing.")
    return summary
