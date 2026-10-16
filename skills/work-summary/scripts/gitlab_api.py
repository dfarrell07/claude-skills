"""Anonymous native GitLab MR reads; public access is independent of /user login."""

import re
import ssl
from datetime import datetime
from pathlib import Path
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qsl, quote, urlencode, urljoin, urlsplit
from urllib.request import HTTPRedirectHandler, HTTPSHandler, Request, build_opener

from evidence import HOST, decode_json, integer, iso, require, timestamp
from gitlab_sources import project_path

SCOPE = 'explicit_public_merge_requests_and_verified_identity_inventories'
MAX_PAGES, MAX_REDIRECTS, MAX_BYTES = 100, 5, 20 * 1024 * 1024


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
    require(p.scheme == 'https' and p.netloc == host and p.hostname == host
            and p.path.startswith('/api/v4/') and not p.fragment
            and not any(c in url for c in '\r\n\x00'), 'Unsafe native API route')
    return url


class Redirects(HTTPRedirectHandler):
    def __init__(self, host):
        super().__init__()
        self.host, self.rows = host, []

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        target = safe_api(urljoin(req.full_url, newurl), self.host)
        require(len(self.rows) < MAX_REDIRECTS, 'Native redirect limit reached')
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
        require(len(raw) <= MAX_BYTES, 'Native response exceeds read limit')
        headers = {key.lower(): value for key, value in response.headers.items()
                   if key.lower() in {'link', 'x-next-page', 'x-page', 'x-total-pages', 'x-total'}}
        status = response.status
        value = decode_json(raw.decode('utf-8')) if status == 200 else None
        return {'url': safe_api(response.url, host), 'status': status, 'headers': headers,
                'source': value, 'redirects': redirects.rows}


def valid_mr(source):
    require(isinstance(source, dict) and all(integer(source.get(k), 1) for k in ('id', 'iid', 'project_id')),
            'Invalid native MR identity')
    host, repo, number = mr_route(source.get('web_url'))
    require(number == source['iid'] and source.get('state') in {'opened', 'closed', 'merged', 'locked'}
            and isinstance(source.get('title'), str)
            and (source.get('description') is None or isinstance(source['description'], str)), 'Invalid native MR')
    require(timestamp(source.get('created_at')) <= timestamp(source.get('updated_at')), 'Invalid MR dates')
    for field in ('merged_at', 'closed_at'):
        if source.get(field) is not None:
            timestamp(source[field])
    require(source.get('author') is None or isinstance(source['author'], dict)
            and integer(source['author'].get('id'), 1), 'Invalid MR author')
    return host, repo, number


def valid_note(note):
    require(isinstance(note, dict) and integer(note.get('id'), 1) and isinstance(note.get('body'), str)
            and 'author' in note and (note['author'] is None or isinstance(note['author'], dict)
            and integer(note['author'].get('id'), 1)), 'Invalid native note')
    require(timestamp(note.get('created_at')) <= timestamp(note.get('updated_at')), 'Invalid native note dates')


def valid_inventory_row(row, host, actor, role):
    native_host, _, _ = valid_mr(row)
    require(native_host == host, 'Inventory host mismatch')
    if role in {'authored_updates', 'open_authored'}:
        require((row.get('author') or {}).get('id') == actor, 'Inventory author mismatch')
    else:
        field = 'assignees' if role == 'open_assigned' else 'reviewers'
        require(isinstance(row.get(field), list)
                and any(isinstance(user, dict) and user.get('id') == actor for user in row[field]),
                'Inventory participant mismatch')
    if role != 'authored_updates':
        require(row['state'] == 'opened', 'Open inventory state mismatch')


def read_notes(reader, host, repo, source):
    """Try independent public routes; a denied endpoint cannot erase its siblings."""
    notes, sources = {}, []
    expected = source.get('user_notes_count')
    for kind in ('notes', 'discussions'):
        for project in (str(source['project_id']), quote(repo, safe='')):
            endpoint = f'https://{host}/api/v4/projects/{project}/merge_requests/{source["iid"]}/{kind}'
            def accept(row):
                if kind == 'notes':
                    valid_note(row)
                else:
                    require(isinstance(row, dict) and isinstance(row.get('id'), str) and row['id']
                            and isinstance(row.get('notes'), list), 'Invalid native discussion')
            rows, status = reader.pages(endpoint, accept)
            for row in rows:
                for note in ([row] if kind == 'notes' else row['notes']):
                    try:
                        valid_note(note)
                        old = notes.get(note['id'])
                        if old is None or timestamp(note['updated_at']) > timestamp(old['updated_at']):
                            notes[note['id']] = note
                    except (ValueError, TypeError, KeyError, AttributeError):
                        status = 'partial'
                        reader.fail(endpoint, 'invalid_native_discussion_note')
            sources.append({'endpoint': endpoint, 'kind': kind, 'status': status})
            count = sum(not note.get('system', False) for note in notes.values())
            if status == 'ok' and (not integer(expected) or count == expected):
                return list(notes.values()), 'ok', sources
    if integer(expected) and expected != sum(not note.get('system', False) for note in notes.values()):
        reader.fail(sources[-1]['endpoint'], 'native_user_note_count_disagrees_or_visibility_differs')
    return list(notes.values()), 'partial', sources


class PublicReader:
    def __init__(self, result, read):
        self.result, self.read = result, read

    def get(self, url):
        host = urlsplit(url).hostname
        safe_api(url, host)
        response = self.read(url)
        require(isinstance(response, dict), 'Invalid native response')
        safe_api(response['url'], host)
        require(type(response.get('status')) is int and isinstance(response.get('headers'), dict),
                'Invalid native response metadata')
        redirects = response.get('redirects', [])
        require(isinstance(redirects, list) and len(redirects) <= MAX_REDIRECTS, 'Invalid native redirects')
        previous = url
        for step in redirects:
            require(step.get('url') == previous and step.get('status') in {301, 302, 303, 307, 308},
                    'Invalid redirect provenance')
            previous = safe_api(step['location'], host)
        require(previous == response['url'], 'Unbound native response route')
        record = {'url': url, **response}
        record['requested_url'] = url
        self.result['metadata']['queries'].append(record)
        require(response['status'] == 200, 'native_http_' + str(response['status']))
        return response

    def pages(self, endpoint, accept):
        """Preserve readable rows on malformed siblings, later errors and paging drift."""
        expected = urlsplit(endpoint)
        base = dict(parse_qsl(expected.query))
        base.update(per_page='100')
        url = endpoint.split('?', 1)[0] + '?' + urlencode({**base, 'page': '1'})
        rows, seen_pages, seen_ids, partial = [], set(), set(), False
        totals, observed_count = {}, 0
        for _ in range(MAX_PAGES):
            try:
                require(url not in seen_pages, 'Repeated native page')
                seen_pages.add(url)
                response = self.get(url)
                require(urlsplit(response['url']).path == expected.path, 'Paged endpoint route changed')
                page = response['source']
                require(isinstance(page, list) and len(page) <= 100, 'Invalid native list page')
                observed_count += len(page)
                for row in page:
                    try:
                        accept(row)
                        require(row['id'] not in seen_ids, 'Duplicate native row')
                        seen_ids.add(row['id'])
                        rows.append(row)
                    except (ValueError, TypeError, KeyError, AttributeError):
                        partial = True
                        self.fail(url, 'invalid_or_duplicate_native_row')
                headers = {str(k).lower(): v for k, v in response['headers'].items()}
                current = int(dict(parse_qsl(urlsplit(url).query))['page'])
                if headers.get('x-page'):
                    require(headers['x-page'] == str(current), 'Native page identity drift')
                for field in ('x-total', 'x-total-pages'):
                    if headers.get(field):
                        require(re.fullmatch(r'[0-9]{1,12}', headers[field]), 'Invalid native count')
                        require(field not in totals or totals[field] == headers[field], 'Native count drift')
                        totals[field] = headers[field]
                next_page = headers.get('x-next-page')
                links = re.findall(r'<([^>]+)>\s*;\s*rel="?next"?', headers.get('link', ''), re.I)
                last_links = re.findall(r'<([^>]+)>\s*;\s*rel="?last"?', headers.get('link', ''), re.I)
                if last_links:
                    require(len(last_links) == 1, 'Ambiguous last page')
                    last_url = safe_api(urljoin(url, last_links[0]), expected.hostname)
                    last_parts = urlsplit(last_url)
                    last_page = dict(parse_qsl(last_parts.query)).get('page')
                    require(last_parts.path == expected.path and isinstance(last_page, str)
                            and re.fullmatch(r'[1-9][0-9]{0,8}', last_page), 'Invalid last page scope')
                    require(int(last_page) >= current, 'Native last page precedes current page')
                    require(next_page or links or int(last_page) == current,
                            'Native last page shows missing continuation')
                if links:
                    require(len(links) == 1, 'Ambiguous next page')
                    target = safe_api(urljoin(url, links[0]), expected.hostname)
                    parsed = urlsplit(target)
                    params = dict(parse_qsl(parsed.query))
                    require(parsed.path == expected.path, 'Next page scope changed')
                    for key, value in base.items():
                        other = params.get(key)
                        require(other is not None and (timestamp(other) == timestamp(value)
                                if key in {'updated_after', 'created_after', 'updated_before', 'created_before'}
                                else other == value), 'Next page scope changed')
                    # GitLab adds harmless defaults to its Link URLs. Only use
                    # their page number, reconstructing the next request from
                    # our original scope rather than sending added parameters.
                    require('x-next-page' not in headers or params.get('page') == str(next_page),
                            'Pagination headers disagree')
                    next_page = params.get('page')
                if next_page:
                    require(isinstance(next_page, str) and re.fullmatch(r'[1-9][0-9]{0,8}', next_page), 'Invalid next page')
                    require(int(next_page) == current + 1, 'Native page did not advance')
                    url = endpoint.split('?', 1)[0] + '?' + urlencode({**base, 'page': next_page})
                else:
                    # A full page with no native termination metadata is ambiguous.
                    require('x-next-page' in headers or 'link' in headers or len(page) < 100,
                            'Native pagination termination unavailable')
                    require('x-total' not in totals or observed_count == int(totals['x-total']),
                            'Native count disagrees with received rows')
                    require('x-total-pages' not in totals or current == max(1, int(totals['x-total-pages'])),
                            'Native page count disagrees with termination')
                    return rows, 'partial' if partial else 'ok'
            except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
                self.fail(url, type(error).__name__ if not isinstance(error, ValueError) else str(error))
                return rows, 'partial'
        self.fail(url, 'native_page_limit')
        return rows, 'partial'

    def fail(self, url, reason):
        self.result['metadata']['failures'].append({'url': url, 'reason': reason})


def collect_gitlab(urls, warn, *, verified_emails=(), since=None, read=None):
    verified_emails = tuple(verified_emails)
    result = {'items': [], 'metadata': {'status': 'not_applicable', 'scope': SCOPE,
              'queries': [], 'failures': [], 'identities': [], 'identity_leads': [],
              'inventories': [], 'verified_aliases': sorted({email.casefold() for email in verified_emails
                                          if isinstance(email, str) and email})}}
    reader = PublicReader(result, read or public_get)
    pending, attempted, actors, users_read, fallback_sources = [], set(), set(), set(), {}
    for url in urls:
        if url not in pending:
            pending.append(url)
    aliases = {email.casefold() for email in verified_emails if isinstance(email, str) and email}
    if since is not None:
        since = iso(since) if isinstance(since, datetime) else iso(timestamp(since))
    while pending:
        original = pending.pop(0)
        try:
            host, repo, number = mr_route(original)
            if original in attempted:
                continue
            attempted.add(original)
            endpoint = f'https://{host}/api/v4/projects/{quote(repo, safe="")}/merge_requests/{number}'
            source_kind = 'detail'
            try:
                response = reader.get(endpoint)
                source = response['source']
                native_host, native_repo, native_number = valid_mr(source)
                require(native_host == host and native_number == number, 'MR response identity mismatch')
                final_path = urlsplit(response['url']).path
                allowed = {f'/api/v4/projects/{quote(repo, safe="")}/merge_requests/{number}',
                           f'/api/v4/projects/{source["project_id"]}/merge_requests/{number}'}
                require(final_path in allowed, 'MR response project redirect mismatch')
                require(native_repo == repo or response.get('redirects'), 'Unverified MR project rename')
            except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
                require(original in fallback_sources, 'Native MR detail unavailable without readable inventory source')
                reader.fail(endpoint, str(error) if isinstance(error, ValueError) else type(error).__name__)
                source = fallback_sources[original]
                native_host, native_repo, native_number = valid_mr(source)
                source_kind = 'inventory'
            existing = next((item for item in result['items'] if item['url'] == source['web_url']), None)
            if existing:
                if original not in existing['requested_urls']:
                    existing['requested_urls'].append(original)
            else:
                item = {'url': source['web_url'], 'host': native_host, 'repo': native_repo, 'number': native_number,
                    'source': source, 'source_kind': source_kind, 'requested_urls': [original],
                    'notes': [], 'notes_status': 'partial'}
                result['items'].append(item)
                item['notes'], item['notes_status'], item['notes_sources'] = read_notes(reader, host, native_repo, source)
            author = source.get('author') or {}
            actor = author.get('id')
            public_email = author.get('public_email')
            foreign_email = (isinstance(public_email, str) and public_email
                             and public_email.casefold() not in aliases)
            if actor and aliases and not foreign_email and (host, actor) not in users_read:
                users_read.add((host, actor))
                try:
                    email = author.get('public_email')
                    author_basis = isinstance(email, str) and email.casefold() in aliases
                    user = author if author_basis else reader.get(f'https://{host}/api/v4/users/{actor}')['source']
                    require(isinstance(user, dict) and user.get('id') == actor, 'Public user identity mismatch')
                    email = user.get('public_email')
                    if isinstance(email, str) and email.casefold() in aliases:
                        result['metadata']['identities'].append({'host': host, 'user_id': actor,
                            'source': user, 'basis': ('native_mr_author_public_email_matches_verified_alias' if author_basis
                            else 'native_public_email_matches_verified_alias'), 'matched_email': email})
                        actors.add((host, actor))
                except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
                    result['metadata']['identity_leads'].append({'host': host, 'user_id': actor,
                        'endpoint': f'https://{host}/api/v4/users/{actor}', 'status': 'unavailable',
                        'reason': type(error).__name__})
        except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
            # Never save a rejected URL that could contain userinfo or tokens.
            reader.fail(original if isinstance(original, str) and re.fullmatch(r'https://[A-Za-z0-9./_-]+', original) else '',
                        str(error) if isinstance(error, ValueError) else type(error).__name__)
        if not pending and actors:
            for host, actor in sorted(actors):
                queries = [('authored_updates', {'author_id': actor, 'state': 'all', **({'updated_after': since} if since else {})}),
                           ('open_authored', {'author_id': actor, 'state': 'opened'}),
                           ('open_assigned', {'assignee_id': actor, 'state': 'opened'}),
                           ('open_reviews', {'reviewer_id': actor, 'state': 'opened'})]
                for role, params in queries:
                    def inventory_row(row):
                        valid_inventory_row(row, host, actor, role)
                    endpoint = f'https://{host}/api/v4/merge_requests?' + urlencode({'scope': 'all', **params})
                    rows, status = reader.pages(endpoint, inventory_row)
                    result['metadata']['inventories'].append({'host': host, 'user_id': actor, 'role': role,
                        'scope': 'public_host', 'endpoint': endpoint, 'status': status})
                    if status != 'ok':
                        projects = sorted({item['repo'] for item in result['items'] if item['host'] == host})
                        for project in projects:
                            project_endpoint = (f'https://{host}/api/v4/projects/{quote(project, safe="")}/merge_requests?'
                                                + urlencode({'scope': 'all', **params}))
                            recovered, project_status = reader.pages(project_endpoint, inventory_row)
                            result['metadata']['inventories'].append({'host': host, 'user_id': actor, 'role': role,
                                'scope': 'known_public_project', 'repo': project, 'endpoint': project_endpoint,
                                'status': project_status})
                            rows.extend(recovered)
                    for row in rows:
                        first_source = row['web_url'] not in fallback_sources
                        fallback_sources.setdefault(row['web_url'], row)
                        existing_urls = {item['url'] for item in result['items']}
                        if first_source and row['web_url'] not in existing_urls and row['web_url'] not in pending:
                            attempted.discard(row['web_url'])
                            pending.append(row['web_url'])
            actors.clear()
    result['metadata']['status'] = ('partial' if result['metadata']['failures'] else 'ok') if attempted else (
        'partial' if result['metadata']['failures'] else 'not_applicable')
    if result['metadata']['status'] == 'partial':
        warn('GitLab public MR reads are incomplete; readable native details and notes were retained.')
    return result


def validate_gitlab_api(data):
    """Validate native item bindings; no claim that public endpoints inventory private work."""
    capture = data.get('gitlab')
    if capture is None:
        return
    require(isinstance(capture, dict) and isinstance(capture.get('items'), list)
            and isinstance(capture.get('metadata'), dict), 'Invalid GitLab API capture')
    meta = capture['metadata']
    require(meta.get('scope') == SCOPE and meta.get('status') in {'ok', 'partial', 'not_applicable'}
            and isinstance(meta.get('queries'), list) and isinstance(meta.get('failures'), list)
            and isinstance(meta.get('identities'), list) and isinstance(meta.get('inventories'), list)
            and isinstance(meta.get('identity_leads'), list)
            and isinstance(meta.get('verified_aliases'), list)
            and all(isinstance(value, str) and value for value in meta['verified_aliases']),
            'Invalid GitLab API coverage')
    require(meta['status'] != 'ok' or not meta['failures'], 'Incomplete public GitLab reads marked complete')
    trusted = {row['email'].casefold() for row in data.get('metadata', {}).get('author_aliases', [])
               if isinstance(row, dict) and row.get('source') in {
                   'authenticated_github_profile', 'authenticated_github_noreply', 'github_commit_account'}
               and isinstance(row.get('email'), str) and row['email']}
    jira_identity = data.get('jira', {}).get('metadata', {})
    if (jira_identity.get('identity_verified') is True and jira_identity.get('account_id')
            and isinstance(jira_identity.get('user'), str) and jira_identity['user']):
        trusted.add(jira_identity['user'].casefold())
    require(set(meta['verified_aliases']) <= trusted,
            'GitLab target aliases lack authenticated parent-account evidence')
    queries = {}
    for query in meta['queries']:
        require(isinstance(query, dict) and isinstance(query.get('requested_url'), str), 'Invalid native API query')
        url = query['requested_url']
        dummy = {'metadata': {'queries': [], 'failures': []}}
        reader = PublicReader(dummy, lambda _: query)
        try:
            reader.get(url)
        except ValueError as error:
            require(str(error) == 'native_http_' + str(query.get('status')), 'Invalid native query receipt')
        queries.setdefault(url, []).append(query)
    inventory_sources, inventory_urls, global_roles = [], set(), set()
    for inventory in meta['inventories']:
        require(isinstance(inventory, dict) and inventory.get('scope') in {'public_host', 'known_public_project'}
                and inventory.get('role') in {'authored_updates', 'open_authored', 'open_assigned', 'open_reviews'}
                and inventory.get('status') in {'ok', 'partial'}
                and any(identity['host'] == inventory.get('host') and identity['user_id'] == inventory.get('user_id')
                        for identity in meta['identities']), 'Unbound GitLab inventory identity')
        host, actor, role = inventory['host'], inventory['user_id'], inventory['role']
        endpoint = inventory.get('endpoint')
        safe_api(endpoint, host)
        parts = urlsplit(endpoint)
        pairs = parse_qsl(parts.query)
        params = dict(pairs)
        require(len(params) == len(pairs), 'Duplicate inventory query parameters')
        identity_field = 'assignee_id' if role == 'open_assigned' else 'reviewer_id' if role == 'open_reviews' else 'author_id'
        expected = {'scope': 'all', identity_field: str(actor), 'state': 'all' if role == 'authored_updates' else 'opened'}
        if 'updated_after' in params and role == 'authored_updates':
            timestamp(params['updated_after'])
            expected['updated_after'] = params['updated_after']
        require(params == expected, 'Inventory endpoint identity or filter mismatch')
        path = '/api/v4/merge_requests'
        if inventory['scope'] == 'known_public_project':
            require(project_path(inventory.get('repo')), 'Invalid inventory project')
            path = '/api/v4/projects/' + quote(inventory['repo'], safe='') + '/merge_requests'
        else:
            key = host, actor, role
            require(key not in global_roles, 'Duplicate global inventory role')
            global_roles.add(key)
        require(parts.path == path, 'Inventory endpoint scope mismatch')
        counters = {}
        def replay_inventory(url):
            position = counters.get(url, 0)
            counters[url] = position + 1
            require(url in queries and position < len(queries[url]), 'Missing native inventory page')
            return queries[url][position]
        reader = PublicReader({'metadata': {'queries': [], 'failures': []}}, replay_inventory)
        rows, status = reader.pages(endpoint, lambda row: valid_inventory_row(row, host, actor, role))
        require(status == inventory['status'], 'Inventory coverage differs from native pages')
        inventory_sources.extend(rows)
        inventory_urls.update(row['web_url'] for row in rows)
    for identity in meta['identities']:
        require(all((identity['host'], identity['user_id'], role) in global_roles
                    for role in ('authored_updates', 'open_authored', 'open_assigned', 'open_reviews')),
                'Missing native public GitLab role inventory')
    seen = set()
    for item in capture['items']:
        host, repo, number = valid_mr(item['source'])
        require(item.get('url') == item['source']['web_url'] and item.get('host') == host
                and item.get('repo') == repo and item.get('number') == number and item['url'] not in seen,
                'GitLab API item mismatch')
        seen.add(item['url'])
        require(isinstance(item.get('requested_urls'), list) and item['requested_urls'], 'Missing requested MR URL')
        for original in item['requested_urls']:
            require(mr_route(original)[0] == host and mr_route(original)[2] == number, 'MR seed identity mismatch')
        require(isinstance(item.get('notes'), list) and item.get('notes_status') in {'ok', 'partial'}, 'Invalid MR notes')
        ids = set()
        for note in item['notes']:
            valid_note(note)
            require(note['id'] not in ids, 'Duplicate MR note')
            ids.add(note['id'])
        receipt = next((query for query in meta['queries'] if query.get('status') == 200
                        and query.get('source') == item['source']
                        and re.fullmatch(r'/api/v4/projects/[^/]+/merge_requests/' + str(number),
                                         urlsplit(query['requested_url']).path)), None)
        require(item.get('source_kind') in {'detail', 'inventory'}, 'Invalid native MR source kind')
        if item['source_kind'] == 'detail':
            require(receipt is not None, 'MR source absent from native detail query')
            request_host = urlsplit(receipt['requested_url']).hostname
            require(request_host == host, 'MR detail host mismatch')
            original_repo = mr_route(item['requested_urls'][0])[1]
            final_path = urlsplit(receipt['url']).path
            require(final_path in {f'/api/v4/projects/{quote(original_repo, safe="")}/merge_requests/{number}',
                                   f'/api/v4/projects/{item["source"]["project_id"]}/merge_requests/{number}'},
                    'MR detail project route mismatch')
            require(repo == original_repo or receipt.get('redirects'), 'MR rename lacks native redirect')
        else:
            require(item['source'] in inventory_sources, 'MR fallback source absent from native inventories')
        counters = {}
        def replay(url):
            position = counters.get(url, 0)
            counters[url] = position + 1
            require(url in queries and position < len(queries[url]), 'Missing native notes page')
            return queries[url][position]
        replay_reader = PublicReader({'metadata': {'queries': [], 'failures': []}}, replay)
        notes, notes_status, notes_sources = read_notes(replay_reader, host, repo, item['source'])
        require(notes == item['notes'] and notes_status == item['notes_status']
                and notes_sources == item.get('notes_sources'), 'MR notes differ from native pages')
    for identity in meta['identities']:
        require(identity.get('basis') in {'native_public_email_matches_verified_alias',
                                         'native_mr_author_public_email_matches_verified_alias'}
                and identity.get('source', {}).get('id') == identity.get('user_id')
                and identity.get('source', {}).get('public_email') == identity.get('matched_email')
                and isinstance(identity.get('matched_email'), str) and identity['matched_email'],
                'Invalid public GitLab identity evidence')
        require(identity['matched_email'].casefold() in meta['verified_aliases']
                and isinstance(identity.get('host'), str) and HOST.fullmatch(identity['host'])
                and ':' not in identity['host'], 'GitLab identity lacks verified alias basis')
        if identity['basis'].startswith('native_mr_author_'):
            require(any(item['host'] == identity['host'] and item['source'].get('author') == identity['source']
                        for item in capture['items']), 'GitLab identity author absent from native MR')
        else:
            endpoint = f'https://{identity["host"]}/api/v4/users/{identity["user_id"]}'
            require(any(query.get('status') == 200 and query.get('source') == identity['source']
                        for query in queries.get(endpoint, [])), 'GitLab identity absent from native public user')
    require(inventory_urls <= seen, 'Readable native inventory MR was dropped')
    for lead in meta['identity_leads']:
        require(isinstance(lead, dict) and lead.get('status') == 'unavailable'
                and integer(lead.get('user_id'), 1)
                and lead.get('endpoint') == f'https://{lead.get("host")}/api/v4/users/{lead["user_id"]}'
                and any(item['host'] == lead.get('host')
                        and (item['source'].get('author') or {}).get('id') == lead['user_id']
                        and not (item['source'].get('author') or {}).get('public_email')
                        for item in capture['items']), 'Unbound GitLab public identity lead')
