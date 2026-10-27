"""Anonymous GitLab MR detail reads for MRs linked from the user's work."""

import re
import socket
import ssl
import threading
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import quote, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from evidence import HOST, decode_json, integer, require, timestamp
from gitlab_sources import project_path

SCOPE = 'explicit_public_merge_requests'
MAX_REDIRECTS, MAX_BYTES = 5, 20 * 1024 * 1024
ERRORS = (OSError, URLError, ValueError, TypeError, KeyError, AttributeError)


def mr_route(url):
    require(isinstance(url, str), 'Invalid MR URL')
    p = urlsplit(url)
    match = re.fullmatch(r'/(.+)/-/merge_requests/([1-9][0-9]*)', p.path)
    require(p.scheme == 'https' and p.netloc == p.hostname and p.hostname and HOST.fullmatch(p.hostname)
            and not p.query and not p.fragment and match and project_path(match[1])
            and len(match[2]) <= 20, 'Invalid MR route')
    return p.hostname, match[1], int(match[2])


def safe_api(url, host):
    p = urlsplit(url)
    require(p.scheme == 'https' and p.netloc == host and p.hostname == host and p.path.startswith('/api/v4/')
            and not p.fragment and not any(c in url for c in '\r\n\x00'), 'Unsafe API route')
    return url


class Redirects(HTTPRedirectHandler):
    def __init__(self, host):
        super().__init__()
        self.host, self.rows = host, []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = safe_api(urljoin(req.full_url, newurl), self.host)
        require(len(self.rows) < MAX_REDIRECTS, 'Redirect limit reached')
        self.rows.append({'url': req.full_url, 'status': code, 'location': target})
        return super().redirect_request(req, fp, code, msg, headers, target)


def public_get(url):
    host = urlsplit(url).hostname
    safe_api(url, host)
    redirects = Redirects(host)
    context = ssl.create_default_context()
    # Python distributions can use a separate CA store from the operating system.
    # Add installed public trust bundles; never disable certificate verification.
    for bundle in ('/etc/pki/tls/certs/ca-bundle.crt', '/etc/ssl/certs/ca-certificates.crt'):
        if Path(bundle).is_file():
            context.load_verify_locations(cafile=bundle)
    opener = build_opener(redirects, HTTPSHandler(context=context))
    # No cookie jar, auth handler, credential files, CLI or authenticated principal.
    request = Request(url, headers={'Accept': 'application/json', 'User-Agent': 'work-summary-native-read'})
    try:
        response = opener.open(request, timeout=20)
    except HTTPError as error:
        response = error
    with response:
        raw = response.read(MAX_BYTES + 1)
        require(len(raw) <= MAX_BYTES, 'Response exceeds read limit')
        status = response.status
        value = decode_json(raw.decode('utf-8')) if status == 200 else None
        return {'url': safe_api(response.url, host), 'status': status, 'source': value,
                'redirects': redirects.rows}


def valid_mr(source):
    require(isinstance(source, dict) and all(integer(source.get(k), 1) for k in ('id', 'iid', 'project_id')),
            'Invalid MR identity')
    host, repo, number = mr_route(source.get('web_url'))
    require(number == source['iid'] and source.get('state') in {'opened', 'closed', 'merged', 'locked'}
            and isinstance(source.get('title'), str)
            and (source.get('description') is None or isinstance(source['description'], str)), 'Invalid MR')
    require(timestamp(source.get('created_at')) <= timestamp(source.get('updated_at')), 'Invalid MR dates')
    for field in ('merged_at', 'closed_at'):
        if source.get(field) is not None:
            timestamp(source[field])
    require(source.get('author') is None or isinstance(source['author'], dict)
            and integer(source['author'].get('id'), 1), 'Invalid MR author')
    return host, repo, number


def resolve(host, timeout=5):
    """Name lookup ignores socket timeouts; an internal host off VPN can stall it for minutes."""
    outcome = []
    # A daemon thread: a stalled lookup must not hold up the result or interpreter exit.
    worker = threading.Thread(target=lambda: outcome.append(_lookup(host)), daemon=True)
    worker.start()
    worker.join(timeout)
    if not outcome:
        raise URLError(f'name lookup for {host} timed out')
    if isinstance(outcome[0], OSError):
        raise URLError(outcome[0])


def _lookup(host):
    try:
        return socket.getaddrinfo(host, 443)
    except OSError as error:
        return error


class PublicReader:
    def __init__(self, read):
        self.read, self.unreachable = read, set()

    def get(self, url):
        host = urlsplit(url).hostname
        safe_api(url, host)
        # A host that cannot be reached (VPN down, firewall) times out on every request;
        # one failure is enough to skip the rest of its reads in this run.
        if host in self.unreachable:
            raise URLError('host unreachable earlier in this run')
        try:
            if self.read is public_get:
                resolve(host)
            response = self.read(url)
        except (URLError, OSError) as error:
            if not isinstance(error, HTTPError):
                self.unreachable.add(host)
            raise
        require(isinstance(response, dict) and type(response.get('status')) is int, 'Invalid response')
        safe_api(response['url'], host)
        require(response['status'] == 200, 'http_' + str(response['status']))
        return response


def collect_gitlab(urls, warn, *, verified_emails=(), read=None, trusted_hosts=None):
    """Read each MR's public detail and bind its author only through a verified email."""
    aliases = {email.casefold() for email in verified_emails if isinstance(email, str) and email}
    result = {'items': [], 'metadata': {'status': 'not_applicable', 'scope': SCOPE, 'failures': [],
                                        'identities': [], 'verified_aliases': sorted(aliases)}}
    reader, users_read = PublicReader(read or public_get), set()
    for original in dict.fromkeys(urls):
        try:
            host, repo, number = mr_route(original)
            # Hosts named only by other people's comments are context, not read targets:
            # contacting them would disclose the run and let that host claim identities.
            require(trusted_hosts is None or host in trusted_hosts, 'unconfigured_gitlab_host')
            endpoint = f'https://{host}/api/v4/projects/{quote(repo, safe="")}/merge_requests/{number}'
            response = reader.get(endpoint)
            source = response['source']
            native_host, native_repo, native_number = valid_mr(source)
            require(native_host == host and native_number == number, 'MR response identity mismatch')
            require(native_repo == repo or response.get('redirects'), 'Unverified MR project rename')
            existing = next((item for item in result['items'] if item['url'] == source['web_url']), None)
            if existing:
                existing['requested_urls'].append(original)
                continue
            result['items'].append({'url': source['web_url'], 'host': native_host, 'repo': native_repo,
                                    'number': native_number, 'source': source, 'requested_urls': [original]})
            actor = (source.get('author') or {}).get('id')
            if actor and aliases and (host, actor) not in users_read:
                users_read.add((host, actor))
                identity(result, reader, host, source['author'], aliases)
        except ERRORS as error:
            # Never save a rejected URL that could contain userinfo or tokens.
            url = original if isinstance(original, str) and re.fullmatch(r'https://[A-Za-z0-9./_-]+', original) else ''
            result['metadata']['failures'].append(
                {'url': url, 'reason': str(error) if isinstance(error, ValueError) else type(error).__name__})
    metadata = result['metadata']
    metadata['status'] = 'partial' if metadata['failures'] else 'ok' if result['items'] else 'not_applicable'
    if metadata['failures']:
        warn('GitLab public MR reads are incomplete; readable MR details were retained.')
    return result


def identity(result, reader, host, author, aliases):
    """Credit a GitLab account only when its public email is an already verified alias."""
    try:
        email = author.get('public_email')
        if isinstance(email, str) and email and email.casefold() not in aliases:
            return
        user = author if isinstance(email, str) and email else reader.get(
            f"https://{host}/api/v4/users/{author['id']}")['source']
        require(isinstance(user, dict) and user.get('id') == author['id'], 'Public user identity mismatch')
        email = user.get('public_email')
        if isinstance(email, str) and email.casefold() in aliases:
            result['metadata']['identities'].append({'host': host, 'user_id': author['id'], 'matched_email': email})
    except ERRORS:
        return
