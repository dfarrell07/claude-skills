"""Anonymous native GitLab MR reads; public access is independent of /user login."""

import json
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

NOTES_QUERY = """query WorkSummaryNotes($fullPath:ID!,$iid:String!,$after:String){
  project(fullPath:$fullPath){id fullPath mergeRequest(iid:$iid){id iid webUrl createdAt updatedAt
    notes(first:100,after:$after){count pageInfo{hasNextPage endCursor}
      nodes{id body createdAt updatedAt system internal url noteableId noteableType
        project{id fullPath} author{id username} lastEditedAt lastEditedBy{id username}}}}}}"""


def graphql_notes_url(host, repo, number, cursor=None):
    variables = {'fullPath': repo, 'iid': str(number), 'after': cursor}
    return f'https://{host}/api/graphql?' + urlencode({'query': NOTES_QUERY,
        'variables': json.dumps(variables, separators=(',', ':'))})


def graphql_variables(url):
    pairs = parse_qsl(urlsplit(url).query)
    params = dict(pairs)
    require(len(params) == len(pairs) and set(params) == {'query', 'variables'}
            and params['query'] == NOTES_QUERY, 'Unsafe native GraphQL operation')
    variables = json.loads(params['variables'])
    require(isinstance(variables, dict) and set(variables) == {'fullPath', 'iid', 'after'}
            and project_path(variables['fullPath']) and isinstance(variables['iid'], str)
            and re.fullmatch(r'[1-9][0-9]{0,19}', variables['iid'])
            and (variables['after'] is None or isinstance(variables['after'], str)
                 and 0 < len(variables['after']) <= 4096
                 and not any(char in variables['after'] for char in '\r\n\x00')),
            'Invalid native GraphQL notes scope')
    return variables


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
            and (p.path.startswith('/api/v4/') or p.path == '/api/graphql') and not p.fragment
            and not any(c in url for c in '\r\n\x00'), 'Unsafe native API route')
    if p.path == '/api/graphql':
        graphql_variables(url)
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


def graphql_time_matches(graphql_value, rest_value):
    native, rest = timestamp(graphql_value), timestamp(rest_value)
    # GitLab GraphQL serializes second precision while REST retains milliseconds.
    return native == rest or (isinstance(graphql_value, str)
            and re.fullmatch(r'\d{4}-\d\d-\d\dT\d\d:\d\d:\d\dZ', graphql_value)
            and native == rest.replace(microsecond=0))


def native_graphql_note(node, host, repo, source):
    """Keep native GIDs and full camelCase source; never invent numeric REST IDs."""
    require(isinstance(node, dict) and isinstance(node.get('id'), str)
            and re.fullmatch(r'gid://gitlab/Note/[1-9][0-9]*', node['id'])
            and isinstance(node.get('body'), str), 'Invalid native GraphQL note')
    require(node.get('project') == {'id': 'gid://gitlab/Project/' + str(source['project_id']), 'fullPath': repo}
            and node.get('noteableType') == 'MergeRequest' and node.get('noteableId') == source['id']
            and node.get('url') == source['web_url'] + '#note_' + node['id'].rsplit('/', 1)[1],
            'GraphQL note target mismatch')
    require(timestamp(node.get('createdAt')) <= timestamp(node.get('updatedAt')),
            'Invalid native GraphQL note dates')
    require(type(node.get('system')) is bool and type(node.get('internal')) is bool,
            'Missing native GraphQL privacy or system fields')
    for field in ('author', 'lastEditedBy'):
        require(field in node and (node[field] is None or isinstance(node[field], dict)
                and isinstance(node[field].get('id'), str)
                and re.fullmatch(r'gid://gitlab/User/[1-9][0-9]*', node[field]['id'])
                and isinstance(node[field].get('username'), str) and bool(node[field]['username'])),
                'Invalid native GraphQL note actor')
    require('lastEditedAt' in node, 'Missing native GraphQL edit observation')
    if node['lastEditedAt'] is not None:
        require(timestamp(node['createdAt']) <= timestamp(node['lastEditedAt']) <= timestamp(node['updatedAt']),
                'Invalid native GraphQL edit time')
    if node['lastEditedBy'] is not None:
        require(node['lastEditedAt'] is not None, 'Native GraphQL editor lacks edit time')
    return {'id': node['id'], 'node_id': node['id'], 'source_kind': 'graphql', 'source': node,
            'body': node['body'], 'author': node['author'], 'created_at': node['createdAt'],
            'updated_at': node['updatedAt'], 'system': node['system'], 'internal': node['internal'],
            'url': node['url'], 'last_edited_at': node['lastEditedAt'], 'last_edited_by': node['lastEditedBy']}


def valid_note(note):
    if isinstance(note, dict) and note.get('source_kind') == 'graphql':
        require(isinstance(note.get('id'), str) and note.get('node_id') == note['id']
                and re.fullmatch(r'gid://gitlab/Note/[1-9][0-9]*', note['id'])
                and isinstance(note.get('source'), dict), 'Invalid normalized GraphQL note')
        return
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


def read_graphql_notes(reader, host, repo, source):
    """Bound every complete native notes connection to its REST MR and project."""
    notes, seen, cursors, cursor, snapshot, total = [], set(), set(), None, None, None
    partial = False
    for _ in range(MAX_PAGES):
        url = graphql_notes_url(host, repo, source['iid'], cursor)
        try:
            response = reader.get(url)
            require(urlsplit(response['url']).path == '/api/graphql'
                    and graphql_variables(response['url']) == graphql_variables(url),
                    'GraphQL response scope changed')
            result = response['source']
            require(isinstance(result, dict), 'Invalid native GraphQL response')
            errors = result.get('errors', [])
            if not isinstance(errors, list) or errors:
                partial = True
                reader.fail(url, 'native_graphql_errors' if isinstance(errors, list)
                            else 'invalid_native_graphql_errors')
            project = result['data']['project']
            require(isinstance(project, dict) and project.get('id') == 'gid://gitlab/Project/' + str(source['project_id'])
                    and project.get('fullPath') == repo, 'GraphQL project identity mismatch')
            mr = project['mergeRequest']
            require(isinstance(mr, dict) and mr.get('id') == 'gid://gitlab/MergeRequest/' + str(source['id'])
                    and str(mr.get('iid')) == str(source['iid']) and mr.get('webUrl') == source['web_url']
                    and graphql_time_matches(mr.get('createdAt'), source['created_at'])
                    and timestamp(mr.get('createdAt')) <= timestamp(mr.get('updatedAt')),
                    'GraphQL MR identity mismatch')
            current = {field: mr[field] for field in ('id', 'iid', 'webUrl', 'createdAt', 'updatedAt')}
            if snapshot is not None and current != snapshot:
                partial = True
                reader.fail(url, 'GraphQL MR changed during notes paging')
            snapshot = current
            connection = mr['notes']
            require(isinstance(connection, dict) and isinstance(connection.get('nodes'), list)
                    and len(connection['nodes']) <= 100, 'Invalid native GraphQL notes page')
            if not integer(connection.get('count')) or total is not None and total != connection['count']:
                partial = True
                reader.fail(url, 'Native GraphQL note count missing or changed')
            if integer(connection.get('count')):
                total = connection['count']
            for node in connection['nodes']:
                try:
                    note = native_graphql_note(node, host, repo, source)
                    require(note['id'] not in seen, 'Duplicate native GraphQL note')
                    seen.add(note['id'])
                    notes.append(note)
                except (ValueError, TypeError, KeyError, AttributeError) as error:
                    partial = True
                    reader.fail(url, str(error) if isinstance(error, ValueError) else type(error).__name__)
            info = connection.get('pageInfo')
            require(isinstance(info, dict) and type(info.get('hasNextPage')) is bool,
                    'Invalid native GraphQL pagination')
            if not info['hasNextPage']:
                require(len(notes) == total, 'Native GraphQL note count disagrees with retained rows')
                expected = source.get('user_notes_count')
                require(not integer(expected) or expected == sum(not note['system'] for note in notes),
                        'Native GraphQL user note count disagrees or visibility differs')
                return notes, 'partial' if partial else 'ok'
            cursor = info.get('endCursor')
            require(isinstance(cursor, str) and cursor and cursor not in cursors
                    and connection['nodes'] and (total is None or len(notes) < total),
                    'Native GraphQL cursor did not advance')
            cursors.add(cursor)
        except (OSError, URLError, ValueError, TypeError, KeyError, AttributeError) as error:
            reader.fail(url, str(error) if isinstance(error, ValueError) else type(error).__name__)
            return notes, 'partial'
    reader.fail(url, 'native_graphql_page_limit')
    return notes, 'partial'


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
    recovered, status = read_graphql_notes(reader, host, repo, source)
    sources.append({'endpoint': graphql_notes_url(host, repo, source['iid']), 'kind': 'graphql_notes', 'status': status})
    graph_ids = {note['id'].rsplit('/', 1)[1] for note in recovered}
    for note in recovered:
        notes[note['id']] = note
    if status == 'ok' and all(str(identity) in graph_ids for identity in notes if isinstance(identity, int)):
        return list(notes.values()), 'ok', sources
    if status == 'ok':
        reader.fail(sources[-1]['endpoint'], 'readable_rest_note_absent_from_graphql_connection')
        sources[-1]['status'] = 'partial'
    return list(notes.values()), 'partial', sources


class PublicReader:
    def __init__(self, result, read):
        self.result, self.read, self.unreachable = result, read, set()

    def get(self, url):
        host = urlsplit(url).hostname
        safe_api(url, host)
        # A host that cannot be reached (VPN down, firewall) times out on every request;
        # one timeout is enough to skip the rest of its reads in this run.
        if host in self.unreachable:
            raise URLError('host unreachable earlier in this run')
        try:
            response = self.read(url)
        except (URLError, OSError) as error:
            if not isinstance(error, HTTPError):
                self.unreachable.add(host)
            raise
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


def notes_failure_scope(url):
    parsed = urlsplit(url)
    if parsed.path == '/api/graphql':
        variables = graphql_variables(url)
        return parsed.hostname, parsed.path, variables['fullPath'], variables['iid']
    return parsed.hostname, parsed.path


def unresolved_failures(capture):
    recovered = set()
    for item in capture['items']:
        if item['notes_status'] == 'ok':
            recovered.update(notes_failure_scope(source['endpoint'])
                             for source in item.get('notes_sources', []))
    return [failure for failure in capture['metadata']['failures']
            if notes_failure_scope(failure['url']) not in recovered]


def collect_gitlab(urls, warn, *, verified_emails=(), since=None, read=None, trusted_hosts=None):
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
            # Hosts named only by other people's comments are context, not read targets:
            # contacting them would disclose the run and let that host claim identities.
            require(trusted_hosts is None or host in trusted_hosts, 'unconfigured_gitlab_host')
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
    result['metadata']['status'] = ('partial' if unresolved_failures(result) else 'ok') if attempted else (
        'partial' if unresolved_failures(result) else 'not_applicable')
    if result['metadata']['status'] == 'partial':
        warn('GitLab public MR reads are incomplete; readable native details and notes were retained.')
    return result


