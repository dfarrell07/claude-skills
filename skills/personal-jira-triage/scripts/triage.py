#!/usr/bin/env python3
"""Read-only personal Jira evidence collection and offline packet validation."""
import argparse
import concurrent.futures
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import urllib.parse

VERSION = 1
KINDS = {'jira_identity', 'jira_inventory', 'jira_context', 'jira_issue', 'jira_comments',
         'jira_history', 'jira_metadata', 'git_identity', 'git_search',
         'git_pr', 'git_checks', 'artifact_identity', 'artifact'}
MUTABLE = KINDS - {'jira_identity', 'git_identity'}
KEY = re.compile(r'^[A-Z][A-Z0-9_]*-[1-9][0-9]*$')


def now():
    return dt.datetime.now(dt.timezone.utc).isoformat()


def jira_site(value):
    parsed = urllib.parse.urlsplit(value if '://' in value else 'https://' + value)
    if parsed.scheme != 'https' or not parsed.hostname or parsed.username or parsed.password or parsed.path not in {'', '/'} or parsed.query or parsed.fragment:
        raise ValueError('Jira site must identify one exact HTTPS host')
    return 'https://' + parsed.netloc.lower()


def timestamp(value):
    parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('capture timestamp must include timezone')
    return parsed


def canonical_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False,
                                     separators=(',', ':')).encode()).hexdigest()


def fingerprint(value):
    """Ignore native transport metadata, never document/user-content links."""
    def clean(v):
        if isinstance(v, list):
            return [clean(x) for x in v]
        if isinstance(v, dict):
            if v.get('type') == 'doc':
                return v
            return {k: x if k.startswith('customfield_') else clean(x) for k, x in v.items()
                    if k not in {'self', 'iconUrl', 'avatarUrls', 'lastViewed'}}
        return v
    return canonical_hash(clean(value))


def load_json(path):
    return json.loads(Path(path).read_text(encoding='utf-8'))


def decode_comments(text):
    """Only the known acli comment-page stream permits multiple JSON documents."""
    decoder, offset, values = json.JSONDecoder(), 0, []
    while offset < len(text):
        while offset < len(text) and text[offset].isspace():
            offset += 1
        if offset == len(text):
            break
        value, offset = decoder.raw_decode(text, offset)
        values.append(value)
    if not values:
        raise ValueError('empty native comment JSON stream')
    if len(values) == 1:
        return values[0]
    if not all(isinstance(v, dict) and isinstance(v.get('comments'), list) for v in values):
        raise ValueError('multiple JSON documents only supported for native comment pages')
    rows(values, 'jira_comments')
    return values


def read_source(path, source):
    text = Path(path).read_bytes().decode('utf-8-sig')
    if source.get('kind') == 'jira_comments' and source.get('schema') == 'acli-comments-json-stream-v1':
        return decode_comments(text)
    value = json.loads(text)
    if source.get('kind') == 'jira_comments' and source.get('schema') == 'acli-issue-comments-json-v1':
        validate_shape('jira_issue', value)
        resource_identity('jira_issue', source['resource'], source['site'], value)
        return issue_comments(value)
    return value


def source_fingerprint(value, source):
    native = value if source.get('kind') in {'artifact', 'artifact_identity'} else fingerprint(value)
    bound = {'native': native, **{k: source.get(k) for k in
                                ['service', 'site', 'kind', 'resource', 'schema', 'access', 'identity_ref',
                                 'complete', 'pagination', 'fidelity']}}
    if source.get('kind') == 'artifact':
        bound['raw_sha256'] = source.get('sha256')  # OCI manifests are byte-addressed, not merely equivalent JSON.
    return canonical_hash(bound)


def valid_service(service, kind):
    if kind in {'artifact', 'artifact_identity'}:
        return isinstance(service, str) and bool(service) and service not in {'jira', 'git'}
    return service == ('jira' if kind.startswith('jira_') else 'git')


def save_json(path, value):
    path = Path(path)
    path.write_text(json.dumps(value, indent=2, ensure_ascii=False) + '\n', encoding='utf-8')
    path.chmod(0o600)


def contained_file(root, relative):
    root = Path(root).resolve()
    rel = Path(relative)
    if rel.is_absolute() or '..' in rel.parts:
        raise ValueError('raw path must be relative and contained')
    candidate = root / rel
    if any(p.is_symlink() for p in [candidate, *candidate.parents] if p != root.parent):
        raise ValueError('symlink raw paths are not permitted')
    result = candidate.resolve()
    if not result.is_relative_to(root) or not result.is_file():
        raise ValueError('raw file missing or outside packet')
    return result


def rows(value, kind):
    """Explicit supported native CLI and REST pagination shapes."""
    field = {'jira_inventory': 'issues', 'jira_context': 'issues', 'jira_comments': 'comments',
             'jira_history': 'values', 'git_search': 'items'}[kind]
    if isinstance(value, list):
        if kind == 'jira_comments' and value and all(isinstance(x, dict) and isinstance(x.get('comments'), list) for x in value):
            collected, expected_start, total = [], 0, None
            for index, page in enumerate(value):
                size = page.get('maxResults')
                count = page.get('total')
                if type(size) is not int or size <= 0 or type(count) is not int or count < 0 or type(page.get('startAt')) is not int or page['startAt'] != expected_start:
                    raise ValueError('missing/repeated/skipped native comment page offset')
                if total is not None and count != total:
                    raise ValueError('comment total changed during paging')
                total = count
                if len(page['comments']) != min(size, max(0, total - expected_start)):
                    raise ValueError('native comment page advertised rows are missing or extra')
                if page.get('isLast') is not (index == len(value) - 1):
                    raise ValueError('native comment page termination missing/contradictory')
                collected.extend(page['comments'])
                expected_start += size
            if len(collected) != total:
                raise ValueError('native comment page count does not match total')
            return collected
        if all(isinstance(x, dict) for x in value):
            return value
        if all(isinstance(x, list) and all(isinstance(y, dict) for y in x) for x in value):
            return [y for x in value for y in x]
        raise ValueError('unsupported paginated list shape')
    if isinstance(value, dict) and isinstance(value.get(field), list):
        result = value[field]
        if kind == 'jira_comments' and (type(value.get('total')) is not int or value['total'] < 0 or type(value.get('startAt')) is not int or value['startAt'] != 0):
            raise ValueError('native comment page requires total/count proof and first-page offset zero')
        if kind == 'git_search' and (type(value.get('total_count')) is not int or value['total_count'] < 0 or len(result) != value['total_count']):
            raise ValueError('Git search capture contains only a partial page or lacks count')
        if 'total' in value and (type(value['total']) is not int or value['total'] < 0 or type(value.get('startAt', 0)) is not int or value.get('startAt', 0) != 0 or len(result) != value['total']):
            raise ValueError('capture contains only a partial page')
        if value.get('incomplete_results') or value.get('nextPageToken') or value.get('isLast') is False:
            raise ValueError('capture is incomplete')
        return result
    raise ValueError('unsupported native response shape for ' + kind)


def validate_shape(kind, data):
    if kind not in KINDS:
        raise ValueError('unsupported resource kind: ' + str(kind))
    if kind in {'jira_inventory', 'jira_context', 'jira_comments', 'jira_history', 'git_search'}:
        values = rows(data, kind)
        ids = [str(v.get('id', v.get('key', ''))) for v in values]
        if any(not i for i in ids) or len(ids) != len(set(ids)):
            raise ValueError('missing/repeated resource identities')
        if kind == 'jira_comments' and any(not isinstance(v.get('body'), str) and not valid_adf(v.get('body')) for v in values):
            raise ValueError('native comment body missing or malformed')
        if kind == 'jira_history' and any(not isinstance(v.get('items'), list) for v in values):
            raise ValueError('native changelog items missing')
    elif kind == 'artifact_identity':
        if not isinstance(data, str) or not data.strip():
            raise ValueError('artifact reader identity must retain a nonempty native principal string')
    elif kind == 'artifact' and (not isinstance(data, dict) or not data):
        raise ValueError('artifact capture must retain a nonempty native JSON object')
    elif not isinstance(data, dict):
        raise ValueError('native resource must be an object')
    elif kind == 'jira_identity' and not (isinstance(data.get('accountId'), str) and data['accountId'].strip()):
        raise ValueError('native Jira accountId is required')
    elif kind == 'git_identity' and not (isinstance(data.get('login'), str) and data['login'].strip() and type(data.get('id')) is int and data['id'] > 0):
        raise ValueError('native Git identity is required')
    elif kind == 'jira_issue' and not (KEY.fullmatch(data.get('key', '')) and isinstance(data.get('fields'), dict)):
        raise ValueError('native issue key/fields are required')
    elif kind == 'git_pr' and not (data.get('number') and isinstance(data.get('head'), dict) and data['head'].get('sha') and isinstance(data.get('base'), dict) and data['base'].get('sha') and data['base'].get('repo', {}).get('full_name') and data.get('html_url')):
        raise ValueError('native PR number/head/base SHAs/repository/URL are required')
    elif kind == 'git_checks' and not (isinstance(data.get('check_runs'), list) and isinstance(data.get('statuses'), list)):
        raise ValueError('native checks and commit statuses must be separate lists')


def comment_fidelity(data):
    return 'native' if all(valid_adf(v.get('body')) and isinstance(v.get('author'), dict)
                           and v['author'].get('accountId') and ('visibility' not in v or v['visibility'] is None or isinstance(v['visibility'], dict))
                           for v in rows(data, 'jira_comments')) else 'rendered'


def issue_comments(issue):
    comments = issue['fields'].get('comment')
    if not isinstance(comments, dict):
        raise ValueError('native issue comments require an object with count/offset proof')
    try:
        validate_shape('jira_comments', comments)
    except (KeyError, TypeError, AttributeError) as e:
        raise ValueError('malformed native issue comment page') from e
    return comments


def resource_identity(kind, resource, site, data):
    if kind == 'jira_inventory' and resource != 'assigned':
        raise ValueError('assigned inventory resource must be assigned; use jira_context for other roles')
    if kind == 'jira_context' and resource not in {'reporter', 'watcher', 'former-assignee', 'explicit'}:
        raise ValueError('unsupported Jira context role')
    if kind == 'jira_issue' and data['key'] != resource:
        raise ValueError('native issue/resource identity mismatch')
    if kind == 'git_pr':
        match = re.fullmatch(r'([^/]+)/([^/#]+)#([1-9][0-9]*)', resource)
        if not match:
            raise ValueError('PR resource must be owner/repository#number')
        repository = match.group(1) + '/' + match.group(2)
        parsed = urllib.parse.urlsplit(data['html_url'])
        if data['number'] != int(match.group(3)) or data['base']['repo']['full_name'].casefold() != repository.casefold() or parsed.scheme != 'https' or parsed.hostname != site or parsed.path != '/' + repository + '/pull/' + match.group(3):
            raise ValueError('native PR/resource repository/number/host mismatch')


def adf_links(value):
    found = set()
    def walk(v):
        if isinstance(v, dict):
            if v.get('type') in {'inlineCard', 'blockCard'}:
                url = v.get('attrs', {}).get('url')
                if isinstance(url, str):
                    found.add(url)
            for mark in v.get('marks', []):
                if mark.get('type') == 'link' and isinstance(mark.get('attrs', {}).get('href'), str):
                    found.add(mark['attrs']['href'])
            for x in v.get('content', []):
                walk(x)
        elif isinstance(v, list):
            for x in v:
                walk(x)
    walk(value)
    return found


def adf_text(value):
    if isinstance(value, list):
        return ''.join(adf_text(x) for x in value)
    if not isinstance(value, dict):
        return ''
    if value.get('type') == 'text':
        return value.get('text', '')
    if value.get('type') in {'inlineCard', 'blockCard'}:
        return value.get('attrs', {}).get('url', '')
    if value.get('type') == 'mention':
        return value.get('attrs', {}).get('text', '')
    if value.get('type') == 'hardBreak':
        return '\n'
    text = adf_text(value.get('content', []))
    return text + ('\n' if value.get('type') in {'paragraph', 'heading', 'listItem', 'tableCell', 'tableHeader', 'tableRow'} else '')


def valid_adf(value):
    def node(v):
        if not isinstance(v, dict) or not isinstance(v.get('type'), str):
            return False
        if v['type'] == 'text' and not isinstance(v.get('text'), str):
            return False
        if 'attrs' in v and not isinstance(v['attrs'], dict):
            return False
        if 'marks' in v and (not isinstance(v['marks'], list) or not all(isinstance(m, dict) and isinstance(m.get('type'), str) for m in v['marks'])):
            return False
        return 'content' not in v or (isinstance(v['content'], list) and all(node(n) for n in v['content']))
    return isinstance(value, dict) and value.get('type') == 'doc' and type(value.get('version')) is int and value['version'] == 1 and isinstance(value.get('content'), list) and node(value)


def source_id(service, site, kind, resource):
    return canonical_hash([service, site, kind, resource])[:24]


def pr_resource(url, host):
    parsed = urllib.parse.urlsplit(url)
    match = re.fullmatch(r'/([^/]+)/([^/]+)/pull/([1-9][0-9]*)/?', parsed.path)
    if parsed.scheme != 'https' or parsed.netloc != host or parsed.username or parsed.password or parsed.query or parsed.fragment or not match:
        raise ValueError('PR URL must identify exact selected HTTPS Git host/repository/number')
    owner, repo, number = match.groups()
    return owner + '/' + repo + '#' + number


class Collector:
    def __init__(self, directory, timeout):
        self.root = Path(directory).expanduser().resolve()
        if self.root.exists() and any(self.root.iterdir()):
            raise ValueError('run directory must be new or empty')
        if any((p / '.git').exists() for p in [self.root, *self.root.parents]):
            raise ValueError('run directory must be outside a Git checkout')
        if any((p / 'SKILL.md').exists() or (p.name == 'skills' and p.parent.name in {'.agents', '.claude', '.codex'}) for p in [self.root, *self.root.parents]):
            raise ValueError('run directory must be outside installed skill roots')
        self.root.mkdir(parents=True, exist_ok=True, mode=0o700)
        self.root.chmod(0o700)
        (self.root / 'raw').mkdir(mode=0o700)
        self.timeout = timeout
        self.packet = {'schema_version': VERSION, 'collected_at': now(), 'identities': {},
                       'sources': [], 'inventory': [], 'gaps': [], 'leads': [], 'local': [],
                       'scope': {'selected_context_keys': []}}
        self.data = {}
        self.cli_jira_identity_verified = False
        self.cli_jira_site = None
        self.jira_import_site_hints = set()
        self.cli_git_identity_ref = None

    def gap(self, source, reason):
        entry = {'source': source, 'reason': str(reason)[:500]}
        if entry not in self.packet['gaps']:
            self.packet['gaps'].append(entry)

    def command(self, argv, json_output=True, capture=False, comment_stream=False):
        try:
            env = dict(os.environ, GIT_OPTIONAL_LOCKS='0')
            r = subprocess.run(argv, capture_output=True, timeout=self.timeout, check=False, env=env)
        except (OSError, subprocess.TimeoutExpired) as e:
            raise ValueError(type(e).__name__ + ': native read unavailable') from e
        if r.returncode:
            # Do not copy arbitrary client errors (which may contain credentials).
            raise ValueError('native read exit ' + str(r.returncode) + ': ' + ' '.join(argv[:4]))
        output = r.stdout.decode('utf-8-sig')
        if not json_output:
            return output
        try:
            value = decode_comments(output) if comment_stream else json.loads(output)
            return (value, r.stdout) if capture else value
        except (json.JSONDecodeError, ValueError) as e:
            raise ValueError('malformed native JSON') from e

    def add(self, service, site, kind, resource, data, complete=True, schema='normalized-native-json-v1',
            captured_at=None, identity_ref=None, ident=None, pagination=None, raw_bytes=None, access=None,
            replace_incomplete=False):
        validate_shape(kind, data)
        if type(complete) is not bool:
            raise ValueError('source complete must be boolean')
        if not valid_service(service, kind):
            raise ValueError('resource service/kind mismatch')
        timestamp(captured_at or now())
        if service == 'jira' or kind in {'artifact', 'artifact_identity'}:
            site = jira_site(site)
        resource_identity(kind, resource, site, data)
        ident = ident or source_id(service, site, kind, resource)
        logical_prior = next((s for s in self.packet['sources']
                              if (s['service'], s['site'], s['kind'], s['resource']) == (service, site, kind, resource)), None)
        replacing = logical_prior and replace_incomplete and complete and not logical_prior['complete']
        if replacing:
            ident = logical_prior['id']  # Keep imported references valid in this new packet.
        path = 'raw/' + canonical_hash(ident)[:24] + '.json'
        record = {'id': ident, 'service': service, 'site': site, 'kind': kind,
                  'resource': resource, 'schema': schema, 'captured_at': captured_at or now(),
                  'raw_path': path, 'complete': complete,
                  'pagination': pagination or {'terminated': bool(complete)}}
        if identity_ref:
            record['identity_ref'] = identity_ref
        if kind == 'artifact':
            if access not in {'authenticated', 'anonymous_public'}:
                raise ValueError('artifact capture must explicitly declare reader access')
            if access == 'anonymous_public' and identity_ref:
                raise ValueError('anonymous public capture must not invent an authenticated principal')
            record['access'] = access
        if kind == 'jira_comments':
            record['fidelity'] = comment_fidelity(data)
            pages = [data] if isinstance(data, dict) else data
            if isinstance(pages, list) and pages and all(isinstance(p, dict) and 'comments' in p for p in pages):
                record['pagination']['pages'] = [{k: p.get(k) for k in ['startAt', 'maxResults', 'total', 'isLast']} for p in pages]
        if raw_bytes is None:
            raw_bytes = (json.dumps(data, indent=2, ensure_ascii=False) + '\n').encode('utf-8')
        record['sha256'] = hashlib.sha256(raw_bytes).hexdigest()
        record['fingerprint'] = source_fingerprint(data, record)
        if logical_prior and not replacing and logical_prior['fingerprint'] != record['fingerprint']:
            raise ValueError('contradictory duplicate native resource: ' + kind + ':' + resource)
        if logical_prior and not replacing and not complete and logical_prior['id'] != ident:
            raise ValueError('duplicate incomplete native resource alias: ' + kind + ':' + resource)
        prior = next((s for s in self.packet['sources'] if s['id'] == ident), None)
        if prior and not replacing:
            if prior['fingerprint'] != record['fingerprint']:
                raise ValueError('contradictory duplicate source ' + ident)
            return ident
        (self.root / path).write_bytes(raw_bytes)
        (self.root / path).chmod(0o600)
        if replacing:
            self.packet['sources'].remove(logical_prior)
            self.packet['gaps'] = [g for g in self.packet['gaps'] if g['source'] != ident]
        if kind == 'jira_comments':
            if record['fidelity'] != 'native':
                self.gap(ident, 'complete rendered comment projection; native ADF/author/visibility needed for audited operations')
        self.packet['sources'].append(record)
        self.data[ident] = data
        if not complete:
            self.gap(ident, 'source capture explicitly incomplete')
        return ident

    def imports(self, manifest, fresh_issue_keys=(), fresh_pr_resources=()):
        path = Path(manifest).resolve()
        doc = load_json(path)
        if type(doc.get('schema_version')) is not int or doc['schema_version'] != VERSION or not isinstance(doc.get('sources'), list):
            raise ValueError('unsupported import manifest')
        for s in doc['sources']:
            try:
                if s.get('kind') in {'jira_issue', 'jira_comments'} and s.get('resource') in fresh_issue_keys:
                    self.jira_import_site_hints.add(jira_site(s['site']))
                    continue  # Original import directory remains immutable; this new run requests a live capture.
                if s.get('kind') == 'git_pr' and (s.get('site'), s.get('resource')) in fresh_pr_resources:
                    continue
                raw = contained_file(path.parent, s['raw_path'])
                if hashlib.sha256(raw.read_bytes()).hexdigest() != s['sha256']:
                    raise ValueError('import raw hash mismatch')
                if not all(s.get(k) for k in ['service', 'site', 'kind', 'resource', 'schema', 'captured_at']):
                    raise ValueError('missing source provenance')
                if type(s.get('complete')) is not bool:
                    raise ValueError('source complete must be boolean')
                paginated = s['kind'] in {'jira_inventory', 'jira_context', 'jira_comments', 'jira_history', 'git_search'}
                complete = s.get('complete') is True and (not paginated or s.get('pagination', {}).get('terminated') is True)
                self.add(s['service'], s['site'], s['kind'], s['resource'], read_source(raw, s), complete,
                         s['schema'], s['captured_at'], s.get('identity_ref'), s.get('id'), s.get('pagination'), raw.read_bytes(), s.get('access'))
            except (ValueError, KeyError, OSError) as e:
                self.gap('import:' + str(s.get('resource', '?')), e)
        self.identities()

    def identities(self):
        for s in self.packet['sources']:
            if s['kind'] == 'jira_identity':
                if not s['complete']:
                    self.gap(s['id'], 'incomplete native Jira identity')
                    continue
                identity = {'site': s['site'], 'account_id': self.data[s['id']]['accountId']}
                old = self.packet['identities'].get('jira')
                if old and old != identity:
                    raise ValueError('conflicting native Jira identities')
                self.packet['identities']['jira'] = identity
            elif s['kind'] == 'git_identity':
                if not s['complete']:
                    self.gap(s['id'], 'incomplete native Git identity')
                    continue
                d = self.data[s['id']]
                identity = {'host': s['site'], 'login': d['login'], 'id': d['id']}
                old = self.packet['identities'].get('github')
                if old and old != identity:
                    raise ValueError('conflicting native Git identities')
                self.packet['identities']['github'] = identity
        by_id = {s['id']: s for s in self.packet['sources']}
        for s in self.packet['sources']:
            if s['kind'].endswith('identity'):
                continue
            if s['kind'] == 'artifact' and s.get('access') == 'anonymous_public':
                continue
            ref = by_id.get(s.get('identity_ref'))
            required_kind = 'artifact_identity' if s['kind'] == 'artifact' else s['service'] + '_identity'
            if ref and (ref['service'] != s['service'] or ref['site'] != s['site'] or ref['kind'] != required_kind):
                raise ValueError('source authenticated identity/site mismatch')
            if not ref:
                self.gap(s['id'], 'native authenticated account identity unavailable')

    def native_jira(self):
        self.cli_jira_identity_verified = False
        self.cli_jira_site = None
        site = self.packet['identities'].get('jira', {}).get('site')
        try:
            source_sites = self.jira_import_site_hints | {s['site'] for s in self.packet['sources'] if s['service'] == 'jira'}
            if site:
                source_sites.add(site)
            if len(source_sites) > 1:
                raise ValueError('ambiguous imported Jira source sites')
            if source_sites:
                site = next(iter(source_sites))
            auth = self.command(['acli', 'jira', 'auth', 'status'], False)
            match = re.search(r'^\s*Site:\s*(\S+)\s*$', auth, re.M)
            if not match:
                raise ValueError('native Jira auth status lacks Site')
            native_site = jira_site(match.group(1))
            if site and jira_site(site) != native_site:
                raise ValueError('CLI/connector Jira site mismatch')
            site = native_site
            self.cli_jira_site = site
            self.packet['identities'].setdefault('jira', {'site': site})
            email_match = re.search(r'^\s*Email:\s*(\S+)\s*$', auth, re.M)
            native_identity = next((self.data[s['id']] for s in self.packet['sources'] if s['kind'] == 'jira_identity' and s['complete']), {})
            native_email = email_match.group(1) if email_match else None
            imported_email = native_identity.get('emailAddress')
            if native_email and imported_email:
                if native_email.casefold() != imported_email.casefold():
                    raise ValueError('CLI/connector Jira account mismatch')
                self.cli_jira_identity_verified = True
            elif native_identity:
                self.gap('jira_identity', 'CLI email cannot be matched to native /myself account; CLI evidence remains provisional')
            if not self.packet['identities']['jira'].get('account_id'):
                self.gap('jira_identity', 'acli auth status has no native accountId; import /myself for audited ownership')
            existing = [s for s in self.packet['sources'] if s['kind'] == 'jira_inventory' and s['complete']]
            if not existing:
                argv = ['acli', 'jira', 'workitem', 'search', '--jql', 'assignee = currentUser() ORDER BY key',
                        '--fields', 'key,summary,status,assignee', '--json', '--paginate']
                data, raw = self.command(argv, capture=True)
                self.add('jira', site, 'jira_inventory', 'assigned', data, schema='acli-search-json-v1', raw_bytes=raw,
                         identity_ref=self.identity_ref('jira') if self.cli_jira_identity_verified else None, replace_incomplete=True)
            for role, clause in [('reporter', 'reporter = currentUser()'), ('watcher', 'watcher = currentUser()'), ('former-assignee', 'assignee WAS currentUser()')]:
                if any(s['kind'] == 'jira_context' and s['resource'] == role and s['complete'] for s in self.packet['sources']):
                    continue
                query = clause + ' AND statusCategory != Done AND (assignee != currentUser() OR assignee IS EMPTY) ORDER BY key'
                try:
                    data, raw = self.command(['acli', 'jira', 'workitem', 'search', '--jql', query, '--fields', 'key,summary,status,assignee', '--json', '--paginate'], capture=True)
                    self.add('jira', site, 'jira_context', role, data, identity_ref=self.identity_ref('jira') if self.cli_jira_identity_verified else None,
                             pagination={'terminated': True, 'query': query}, schema='acli-search-json-v1', raw_bytes=raw, replace_incomplete=True)
                except ValueError as e:
                    self.gap('jira_context:' + role, e)
        except ValueError as e:
            self.gap('jira_inventory', e)

    def identity_ref(self, service):
        return next((s['id'] for s in self.packet['sources'] if s['service'] == service and s['kind'].endswith('identity')), None)

    def native_git(self, host):
        self.cli_git_identity_ref = None
        try:
            data, raw = self.command(['gh', 'api', '--hostname', host, '--method', 'GET', 'user'], capture=True)
            validate_shape('git_identity', data)
            existing_identity = next((s for s in self.packet['sources'] if s['kind'] == 'git_identity' and s['site'] == host and s['complete']), None)
            if existing_identity:
                native = self.data[existing_identity['id']]
                if (native['login'], native['id']) != (data['login'], data['id']):
                    raise ValueError('CLI/connector Git account mismatch')
                identity = existing_identity['id']
            else:
                identity = self.add('git', host, 'git_identity', 'user', data, schema='gh-rest-json-v1', raw_bytes=raw, replace_incomplete=True)
            self.identities()
            self.cli_git_identity_ref = identity
            login = data['login']
        except ValueError as e:
            self.gap('git_identity', e)
            return
        for role in ['author', 'assignee', 'review-requested', 'involves']:
            if any(s['kind'] == 'git_search' and s['site'] == host and s['resource'] == role and s['complete'] for s in self.packet['sources']):
                continue
            query = 'is:pr is:open ' + role + ':' + login
            pages = []
            total, complete, page = None, False, 0
            try:
                for page in range(1, 11):
                    result = self.command(['gh', 'api', '--hostname', host, '--method', 'GET', 'search/issues',
                                           '-f', 'q=' + query, '-f', 'per_page=100', '-f', 'page=' + str(page)])
                    if not isinstance(result, dict) or not isinstance(result.get('items'), list):
                        raise ValueError('unsupported Git search shape')
                    pages.extend(result['items'])
                    count = result.get('total_count')
                    if type(count) is not int or count < 0 or result.get('incomplete_results'):
                        raise ValueError('Git search incomplete or missing count')
                    if total is not None and total != count:
                        raise ValueError('Git search membership count changed while paging')
                    total = count
                    if len(pages) >= total:
                        complete = len(pages) == total and total <= 1000
                        break
                if not complete:
                    raise ValueError('Git search cap/incomplete coverage; split query through native imports')
            except ValueError as e:
                self.gap('git_search:' + role, e)
            # Partial accessible data remains usable as leads, with failed coverage explicit.
            try:
                self.add('git', host, 'git_search', role, pages, complete, identity_ref=identity,
                         pagination={'terminated': complete, 'pages': page, 'total_count': total, 'query': query}, replace_incomplete=True)
            except ValueError as e:
                self.gap('git_search:' + role, e)

    def inventory(self):
        found = {}
        for s in self.packet['sources']:
            if s['kind'] != 'jira_inventory':
                continue
            for item in rows(self.data[s['id']], 'jira_inventory'):
                key = item.get('key', '')
                if not KEY.fullmatch(key):
                    raise ValueError('invalid inventory Jira key')
                fields = item.get('fields', {})
                category = (fields.get('status') or {}).get('statusCategory', {}).get('key')
                if category not in {'new', 'indeterminate', 'done'}:
                    self.gap(key, 'native status category missing; active membership provisional')
                value = {'key': key, 'site': s['site'], 'issue_id': item.get('id'),
                         'role': 'assigned', 'active': category != 'done'}
                if key in found and found[key] != value:
                    raise ValueError('contradictory inventory membership')
                found[key] = value
        self.packet['inventory'] = list(found.values())
        for s in self.packet['sources']:
            if s['kind'] != 'jira_context':
                continue
            for item in rows(self.data[s['id']], 'jira_context'):
                key = item.get('key', '')
                if not KEY.fullmatch(key):
                    raise ValueError('invalid contextual Jira key')
                if key not in found:
                    category = (item.get('fields', {}).get('status') or {}).get('statusCategory', {}).get('key')
                    found[key] = {'key': key, 'site': s['site'], 'issue_id': item.get('id'), 'role': 'context', 'active': category != 'done', 'context_roles': []}
                    self.packet['inventory'].append(found[key])
                found[key].setdefault('context_roles', []).append(s['resource'])

    def enrich(self, keys):
        site = self.packet['identities'].get('jira', {}).get('site', '')
        existing = {(s['kind'], s['resource']): s for s in self.packet['sources']
                    if s['complete'] and s['site'] == site}
        def read(key):
            results = []
            issue_capture = None
            cached = existing.get(('jira_issue', key))
            if cached:
                issue_capture = (self.data[cached['id']], contained_file(self.root, cached['raw_path']).read_bytes(),
                                 {'captured_at': cached['captured_at'], 'identity_ref': cached.get('identity_ref')})
            for kind, argv in [('jira_issue', ['acli', 'jira', 'workitem', 'view', key, '--fields', '*all', '--json']),
                               ('jira_comments', ['acli', 'jira', 'workitem', 'comment', 'list', '--key', key, '--json', '--paginate'])]:
                if (kind, key) in existing:
                    continue
                if kind == 'jira_comments' and issue_capture:
                    issue, issue_raw, provenance = issue_capture
                    try:
                        comments = issue_comments(issue)
                        if comment_fidelity(comments) == 'native':
                            results.append((kind, comments, issue_raw, None,
                                            dict(provenance, schema='acli-issue-comments-json-v1')))
                            continue
                    except ValueError:
                        pass  # Missing/partial/malformed native fields still need the paginated reader.
                if self.cli_jira_site != site or not self.cli_jira_site:
                    results.append((kind, None, None, 'selected Jira site not established by current CLI authentication', {}))
                    continue
                try:
                    data, raw = self.command(argv, capture=True, comment_stream=kind == 'jira_comments')
                    provenance = {'captured_at': now(),
                                  'identity_ref': self.identity_ref('jira') if self.cli_jira_identity_verified else None,
                                  'schema': 'acli-comments-json-stream-v1' if kind == 'jira_comments' else 'acli-issue-json-v1'}
                    if kind == 'jira_issue':
                        validate_shape(kind, data)
                        resource_identity(kind, key, site, data)
                        issue_capture = (data, raw, provenance)
                    results.append((kind, data, raw, None, provenance))
                except ValueError as e:
                    results.append((kind, None, None, str(e), {}))
            return key, results
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            for key, results in pool.map(read, sorted(set(keys))):
                for kind, data, raw, error, provenance in results:
                    if error:
                        self.gap(key + ':' + kind, error)
                    else:
                        try:
                            self.add('jira', site, kind, key, data, raw_bytes=raw,
                                     replace_incomplete=True, **provenance)
                        except ValueError as e:
                            self.gap(key + ':' + kind, e)

    def pr(self, url, host):
        try:
            resource = pr_resource(url, host)
        except ValueError as e:
            self.gap(url, e)
            return
        repository, number = resource.split('#')
        try:
            data, raw = self.command(['gh', 'api', '--hostname', host, '--method', 'GET', 'repos/' + repository + '/pulls/' + number], capture=True)
            self.add('git', host, 'git_pr', resource, data, schema='gh-rest-json-v1', raw_bytes=raw, identity_ref=self.cli_git_identity_ref, replace_incomplete=True)
            self.gap(resource, 'PR comments/review threads/checks/current target ancestry require complete native enrichment imports')
        except ValueError as e:
            self.gap(resource, e)

    def local(self, roots, plan_roots):
        seen = set()
        plan_roots = list(plan_roots)
        def traversal_error(error):
            self.gap('local:' + str(error.filename), 'local traversal inaccessible: ' + str(error))
        for name in roots:
            root = Path(name).expanduser().resolve()
            candidates = [root]
            self.packet['local'].append({'search_root': str(root), 'maximum_depth': 4, 'excluded_directories': ['.git', 'node_modules', '.venv', '__pycache__']})
            if not root.is_dir():
                self.gap('local:' + str(root), 'local root inaccessible or not a directory')
                continue
            if not (root / '.git').exists():
                for current, directories, files in os.walk(root, followlinks=False, onerror=traversal_error):
                    path = Path(current)
                    depth = len(path.relative_to(root).parts)
                    if '.git' in directories or '.git' in files:
                        candidates.append(path)
                        directories[:] = []
                    else:
                        directories[:] = [d for d in directories if d not in {'.git', 'node_modules', '.venv', '__pycache__'} and not (path / d).is_symlink()]
                        if depth >= 4:
                            if directories:
                                self.gap('local:' + str(root), 'configured depth 4 omits deeper repositories')
                            directories[:] = []
            for path in candidates:
                if path in seen:
                    continue
                if not (path / '.git').exists():
                    continue
                seen.add(path)
                try:
                    worktrees = self.command(['git', '-C', str(path), 'worktree', 'list', '--porcelain', '-z'], False)
                    for entry in worktrees.split('\0\0'):
                        fields = entry.split('\0')
                        if not fields[0].startswith('worktree ') or 'bare' in fields:
                            continue
                        worktree = Path(fields[0][len('worktree '):]).resolve()
                        if not worktree.is_dir() or not (worktree / '.git').exists():
                            self.gap('local:' + str(worktree), 'registered worktree inaccessible')
                        elif worktree not in seen and worktree not in candidates:
                            candidates.append(worktree)
                except ValueError as e:
                    self.gap('local:' + str(path), 'registered worktree discovery unavailable: ' + str(e))
                try:
                    remotes = self.command(['git', '-C', str(path), 'remote', '-v'], False)
                    def safe_remote(match):
                        url = urllib.parse.urlsplit(match.group(0))
                        return urllib.parse.urlunsplit((url.scheme, url.netloc.rsplit('@', 1)[-1], url.path, '', ''))
                    remotes = re.sub(r'[A-Za-z][A-Za-z0-9+.-]*://[^\s]+', safe_remote, remotes)
                    remotes = re.sub(r'[^\s@/:]+@([^\s:]+):', r'<user>@\1:', remotes)
                    state = self.command(['git', '-C', str(path), 'status', '--porcelain=v1'], False)
                    branches = self.command(['git', '-C', str(path), 'branch', '--format=%(refname:short)'], False)
                    self.packet['local'].append({'path': str(path), 'remotes': remotes, 'dirty': state, 'branches': branches.splitlines()})
                    if (path / 'plans').is_dir():
                        plan_roots.append(str(path / 'plans'))
                except ValueError as e:
                    self.gap('local:' + str(path), e)
        for name in set(plan_roots):
            root = Path(name).expanduser().resolve()
            if not root.is_dir():
                self.gap('plans:' + str(root), 'plan root inaccessible')
                continue
            for current, directories, files in os.walk(root, followlinks=False, onerror=traversal_error):
                directories[:] = [d for d in directories if not (Path(current) / d).is_symlink()]
                for filename in files:
                    path = Path(current) / filename
                    if path.suffix != '.md' or path.is_symlink():
                        continue
                    try:
                        text = path.read_text(encoding='utf-8', errors='replace')
                    except OSError as e:
                        self.gap('plans:' + str(path), 'plan read inaccessible: ' + str(e))
                        continue
                    keys = sorted(set(re.findall(r'\b[A-Z][A-Z0-9_]*-[1-9][0-9]*\b', text)))
                    if keys:
                        self.packet['leads'].append({'path': str(path), 'candidate_keys': keys, 'verified': False})

    def finish(self):
        self.packet['collected_at'] = now()
        save_json(self.root / 'packet.json', self.packet)
        return 2 if self.packet['gaps'] else 0


def check(directory):
    root = Path(directory).resolve()
    errors = []
    provisional = []
    def require(condition, message):
        if not condition:
            errors.append(message)
    packet = load_json(root / 'packet.json')
    require(type(packet.get('schema_version')) is int and packet['schema_version'] == VERSION, 'unsupported packet schema')
    sources, data = {}, {}
    resources = {}
    for s in packet.get('sources', []):
        try:
            require(s.get('id') not in sources, 'duplicate source id')
            path = contained_file(root, s['raw_path'])
            require(hashlib.sha256(path.read_bytes()).hexdigest() == s['sha256'], 'raw evidence hash mismatch: ' + s['id'])
            value = read_source(path, s)
            validate_shape(s['kind'], value)
            require(type(s.get('complete')) is bool, 'source complete must be boolean')
            resource_identity(s['kind'], s['resource'], s['site'], value)
            require(all(s.get(k) for k in ['service', 'site', 'resource', 'schema', 'captured_at']), 'missing native source provenance')
            timestamp(s['captured_at'])
            if s['kind'] == 'jira_issue':
                require(value['key'] == s['resource'], 'native issue/resource identity mismatch')
            if s['kind'] in {'jira_inventory', 'jira_context', 'jira_comments', 'jira_history', 'git_search'} and s.get('complete'):
                require(s.get('pagination', {}).get('terminated') is True, 'complete source lacks pagination proof')
            require(source_fingerprint(value, s) == s['fingerprint'], 'source fingerprint mismatch: ' + s['id'])
            resource_key = (s['service'], s['site'], s['kind'], s['resource'])
            require(resource_key not in resources or resources[resource_key] == s['fingerprint'], 'contradictory duplicate native resource')
            resources[resource_key] = s['fingerprint']
            require(valid_service(s['service'], s['kind']), 'resource service/kind mismatch')
            if s['service'] == 'jira' or s['kind'] in {'artifact', 'artifact_identity'}:
                require(jira_site(s['site']) == s['site'], 'source site must identify canonical HTTPS host')
            if s['kind'] == 'artifact':
                require(s.get('access') in {'authenticated', 'anonymous_public'}, 'artifact reader access undeclared')
                require(s.get('access') != 'anonymous_public' or not s.get('identity_ref'), 'anonymous public artifact must not invent reader identity')
            sources[s['id']], data[s['id']] = s, value
        except (KeyError, ValueError, OSError) as e:
            errors.append('invalid source: ' + str(e))
    for s in sources.values():
        if s['kind'].endswith('identity'):
            if s['kind'] == 'artifact_identity':
                continue  # Reader provenance is never a Jira/Git account or issue-ownership assertion.
            expected = packet.get('identities', {}).get('jira' if s['service'] == 'jira' else 'github', {})
            if s['service'] == 'jira':
                require(expected.get('account_id') == data[s['id']].get('accountId') and expected.get('site') == s['site'], 'packet/native Jira identity mismatch')
            else:
                require(expected.get('login') == data[s['id']].get('login') and expected.get('host') == s['site'], 'packet/native Git identity mismatch')
            continue
        identity = sources.get(s.get('identity_ref'))
        if s['kind'] == 'artifact' and s.get('access') == 'anonymous_public':
            continue  # Actual publicness remains an evidence/reviewer judgment, not an authenticated-user claim.
        require(not s.get('identity_ref') or identity is not None, 'dangling authenticated identity reference')
        if identity:
            required_kind = 'artifact_identity' if s['kind'] == 'artifact' else s['service'] + '_identity'
            require(identity['service'] == s['service'] and identity['site'] == s['site'] and identity['kind'] == required_kind, 'mixed authenticated identity/site')
        else:
            provisional.append('missing native identity: ' + s['id'])
    inventory = packet.get('inventory', [])
    require(len({i['key'] for i in inventory}) == len(inventory), 'duplicate inventory key')
    native_assigned = set()
    native_members = {}
    for s in sources.values():
        if s['kind'] == 'jira_inventory':
            for item in rows(data[s['id']], 'jira_inventory'):
                native_assigned.add(item['key'])
                native_members[item['key']] = (s['site'], (item.get('fields', {}).get('status') or {}).get('statusCategory', {}).get('key'))
    if any(s['kind'] == 'jira_inventory' for s in sources.values()):
        require(native_assigned == {i['key'] for i in inventory if i.get('role') in {'assigned', 'specialist'}}, 'inventory omits/adds assigned native members')
        for item in inventory:
            if item['key'] in native_members and item.get('role') in {'assigned', 'specialist'}:
                site, category = native_members[item['key']]
                require(item.get('site') == site, 'inventory site differs from native query')
                if category in {'new', 'indeterminate', 'done'}:
                    require(item.get('active') == (category != 'done'), 'inventory active category differs from native query')
                else:
                    provisional.append('unknown native assigned status category: ' + item['key'])
                    require(item.get('active') is True, 'unknown assigned status category cannot exclude decision accounting')
    active = {i['key'] for i in inventory if i.get('active') and i.get('role') in {'assigned', 'specialist'}}
    require(all(i.get('role') in {'assigned', 'context', 'specialist'} for i in inventory), 'unsupported inventory role')
    decisions = load_json(root / 'decisions.json') if (root / 'decisions.json').exists() else []
    drafts = load_json(root / 'drafts.json') if (root / 'drafts.json').exists() else []
    reviews = load_json(root / 'reviews.json') if (root / 'reviews.json').exists() else []
    if not isinstance(decisions, list) or not isinstance(drafts, list) or not isinstance(reviews, list):
        raise ValueError('decision/draft/review files must be lists')
    if (root / 'decisions.json').exists() or drafts:
        require(len({d['key'] for d in decisions}) == len(decisions), 'duplicate decision key')
        require(active <= {d['key'] for d in decisions}, 'missing active assigned decision')
    else:
        provisional.append('collection only; deep triage not yet recorded')
    allowed_dispositions = {'no_change', 'candidate', 'held', 'needs_decision', 'specialist'}
    for decision in decisions:
        require(decision.get('key') in {i['key'] for i in inventory}, 'orphan decision outside inventory')
        role = next((i.get('role') for i in inventory if i['key'] == decision.get('key')), None)
        if role == 'specialist':
            require(decision.get('disposition') == 'specialist' and not decision.get('draft_ids'), 'specialist work cannot enter generic draft batch')
        require(decision.get('disposition') in allowed_dispositions, 'invalid disposition')
        require(bool(decision.get('reason')), 'missing decision reason')
        require(all(i in sources for i in decision.get('evidence', [])), 'unknown decision evidence')
    require(len({d['id'] for d in drafts}) == len(drafts), 'duplicate draft id')
    for draft in drafts:
        ident = draft.get('id', '?')
        key = draft.get('key')
        audited = draft.get('ready', 'provisional') == 'audited'
        if audited:
            user_request = packet.get('scope', {}).get('user_request')
            require(isinstance(user_request, str) and bool(user_request.strip()), 'audited draft lacks recorded user request')
        if audited or 'scope_hash' in draft:
            require(draft.get('scope_hash') == canonical_hash(packet.get('scope', {})), 'draft user scope changed since review')
        decision = next((d for d in decisions if d['key'] == key), None)
        require(decision is not None and decision.get('disposition') == 'candidate' and ident in decision.get('draft_ids', []), 'orphan/noncandidate draft: ' + ident)
        hashed = {k: v for k, v in draft.items() if k != 'hash'}
        require(draft.get('hash') == canonical_hash(hashed), 'draft hash mismatch: ' + ident)
        inputs = draft.get('decision_inputs', [])
        require(bool(inputs) and all(i in sources for i in inputs), 'missing/unknown decision inputs: ' + ident)
        require(set(draft.get('evidence', [])) <= set(inputs), 'draft evidence missing from decision inputs')
        if decision is not None:
            require(set(decision.get('evidence', [])) <= set(inputs), 'candidate decision evidence missing from decision inputs')
        baseline = draft.get('baseline', {})
        issue_id, comment_id, metadata_id = (baseline.get(k) for k in ['issue', 'comments', 'metadata'])
        require({issue_id, comment_id} <= set(inputs), 'native issue/comments baseline missing from decision inputs')
        if audited:
            require(metadata_id in inputs, 'native operation metadata missing from decision inputs')
            require(all(sources[i].get('identity_ref') in inputs for i in inputs if i in sources and sources[i].get('identity_ref')), 'native reader identity missing from decision inputs')
        for sid, kind in [(issue_id, 'jira_issue'), (comment_id, 'jira_comments')]:
            require(sid in sources and sources[sid]['kind'] == kind and sources[sid]['resource'] == key, 'missing/wrong draft baseline ' + kind)
        if issue_id not in data or comment_id not in data:
            continue
        fields = data[issue_id]['fields']
        for sid in [comment_id, metadata_id]:
            if sid in sources:
                require(sources[sid]['site'] == sources[issue_id]['site'] and sources[sid].get('identity_ref') == sources[issue_id].get('identity_ref'), 'cross-site/account draft baseline')
        owner = (fields.get('assignee') or {}).get('accountId')
        identity = packet.get('identities', {}).get('jira', {})
        in_scope = key in packet.get('scope', {}).get('selected_context_keys', [])
        owned = bool(identity.get('account_id')) and owner == identity['account_id']
        if audited:
            require(owned or in_scope, 'audited draft lacks ownership/explicit context scope')
            require(all(sources[i].get('complete') for i in inputs if i in sources), 'audited draft has incomplete evidence')
            require(all(sources[i].get('identity_ref') in sources or (sources[i]['kind'] == 'artifact' and sources[i].get('access') == 'anonymous_public') for i in inputs if i in sources and not sources[i]['kind'].endswith('identity')), 'audited draft lacks authenticated source identity')
            require(all(sources[sources[i]['identity_ref']].get('complete') for i in inputs if i in sources and sources[i].get('identity_ref') in sources), 'audited draft relies on incomplete identity')
            require(comment_fidelity(data[comment_id]) == 'native', 'audited operation requires complete native comment ADF/authors/visibility, not rendered CLI projection')
        elif not owned and not in_scope:
            provisional.append('unverified ownership/scope: ' + ident)
        request = draft.get('request', {})
        kind = draft.get('kind')
        metadata = data.get(metadata_id, {})
        if audited:
            require(metadata_id in sources and sources[metadata_id]['kind'] == 'jira_metadata' and sources[metadata_id]['resource'] == key and sources[metadata_id].get('complete'), 'audited draft lacks native metadata')
        if kind == 'fields':
            changes = request.get('fields', {})
            require(bool(changes) and set(request) == {'fields'}, 'invalid fields request')
            for field, value in changes.items():
                require(field in {'summary', 'description'} or field.startswith('customfield_'), 'unsupported field mutation: ' + field)
                require(field not in {'status', 'resolution', 'assignee'}, 'direct ownership/status mutation forbidden')
                require(field in fields, 'field missing from native baseline: ' + field)
                require(value != fields.get(field), 'no-op field mutation: ' + field)
                if field == 'summary':
                    require(isinstance(value, str) and bool(value.strip()), 'invalid summary')
                elif field == 'description' or field.startswith('customfield_'):
                    require(valid_adf(value), 'only native ADF description/link changes supported')
                    require(adf_links(fields.get(field)) <= adf_links(value), 'existing content link removed')
                    # Agent must declare exact changed nodes, preserving the rest structurally.
                    patches = draft.get('adf_changes', {}).get(field, [])
                    expected = json.loads(json.dumps(fields.get(field)))
                    if expected in (None, ''):
                        if field.startswith('customfield_'):
                            fieldmeta = metadata.get('editmeta', {}).get('fields', {}).get(field, {})
                            schema = fieldmeta.get('schema', {})
                            require(schema.get('type') == 'string' and schema.get('custom') == 'com.atlassian.jira.plugin.system.customfieldtypes:textarea' and 'set' in fieldmeta.get('operations', []), 'empty custom ADF field requires editable native textarea metadata')
                        expected = {'type': 'doc', 'version': 1, 'content': []}
                    try:
                        require(bool(patches), 'ADF changes require explicit exact patches')
                        for patch in patches:
                            cursor = expected
                            path = patch['path']
                            valid_path = (isinstance(path, list) and bool(path) and len(path) % 2 == 1
                                          and path[-1] in {'text', 'content'}
                                          and all(path[index] == 'content' and type(path[index + 1]) is int
                                                  and path[index + 1] >= 0 for index in range(0, len(path) - 1, 2)))
                            require(valid_path, 'ADF patch path must traverse only content children with nonnegative integer indices')
                            if not valid_path:
                                continue
                            for part in path[:-1]:
                                cursor = cursor[part]
                            last = path[-1]
                            if patch.get('op') == 'append':
                                require(last == 'content' and isinstance(cursor[last], list), 'append target must be ADF content list')
                                require(not (adf_links(patch['value']) & adf_links(expected)), 'existing content link appended again')
                                cursor[last].append(patch['value'])
                            else:
                                require(last == 'text' and cursor.get('type') == 'text' and isinstance(patch.get('value'), str), 'ADF replacements only change existing text values; attrs/marks/unknown nodes preserved')
                                require(patch.get('op') == 'replace' and cursor[last] == patch['old'], 'ADF exact old text mismatch')
                                cursor[last] = patch['value']
                        require(expected == value, 'ADF diff exceeds declared exact patches')
                    except (KeyError, IndexError, TypeError) as e:
                        errors.append('invalid ADF patch: ' + str(e))
                if audited:
                    fieldmeta = metadata.get('editmeta', {}).get('fields', {}).get(field, {})
                    require('set' in fieldmeta.get('operations', []), 'field not editable in native metadata')
                    if field.startswith('customfield_'):
                        schema = fieldmeta.get('schema', {})
                        supported = schema.get('type') == 'string' and schema.get('custom') == 'com.atlassian.jira.plugin.system.customfieldtypes:textarea'
                        require(valid_adf(fields.get(field)) or supported, 'custom field lacks native ADF/textarea support')
            for field in draft.get('preserved_fields', []):
                require(field not in changes, 'preserved field included in mutation')
            permission = 'EDIT_ISSUES'
        elif kind == 'comment':
            require(set(request) <= {'body', 'visibility'} and valid_adf(request.get('body')), 'invalid native comment request')
            require(bool(adf_text(request.get('body')).strip() or adf_links(request.get('body'))), 'empty native comment')
            existing = rows(data[comment_id], 'jira_comments')
            require(not any(c.get('body') == request.get('body') and c.get('visibility') == request.get('visibility') for c in existing), 'exact duplicate comment')
            if audited:
                require('comment_visibility' in metadata and request.get('visibility') == metadata['comment_visibility'].get('requested'), 'unverified comment visibility policy')
            permission = 'ADD_COMMENTS'
        elif kind == 'transition':
            require(set(request) <= {'transition', 'fields'} and isinstance(request.get('transition'), dict), 'invalid transition request')
            require(set(request.get('fields', {})) <= {'resolution'}, 'unexpected transition fields')
            transitions = metadata.get('transitions', [])
            if isinstance(transitions, dict):
                transitions = transitions.get('transitions', [])
            transition = next((t for t in transitions if t.get('id') == request.get('transition', {}).get('id')), None)
            if audited:
                require(transition is not None, 'transition not in current native metadata')
            if transition:
                specs = transition.get('fields', {})
                resolution = request.get('fields', {}).get('resolution')
                same_status = (fields.get('status') or {}).get('id') == transition.get('to', {}).get('id')
                same_resolution = not resolution or (isinstance(resolution, dict) and (fields.get('resolution') or {}).get('id') == resolution.get('id'))
                require(not (same_status and same_resolution), 'no-op workflow transition')
                require(all(k in request.get('fields', {}) for k, v in specs.items() if v.get('required')), 'missing required transition field')
                if specs.get('resolution', {}).get('required') or transition.get('to', {}).get('statusCategory', {}).get('key') == 'done':
                    require(isinstance(resolution, dict) and bool(resolution.get('id')), 'required/terminal transition lacks explicit resolution')
                if resolution:
                    require(isinstance(resolution, dict) and any(v.get('id') == resolution.get('id') for v in specs.get('resolution', {}).get('allowedValues', [])), 'resolution not in current allowed values')
            permission = 'TRANSITION_ISSUES'
        else:
            require(False, 'unsupported operation kind')
            permission = ''
        if audited:
            permissions = metadata.get('permissions', {})
            permissions = permissions.get('permissions', permissions)
            require(permissions.get(permission, {}).get('havePermission') is True, 'native operation permission missing')
            matching = [r for r in reviews if r.get('draft_id') == ident]
            accepting = []
            for r in matching:
                fingerprints = r.get('evidence_fingerprints', {})
                current = r.get('draft_hash') == draft.get('hash') and all(fingerprints.get(i) == sources[i]['fingerprint'] for i in inputs if i in sources)
                current = current and all(i in sources and h == sources[i]['fingerprint'] for i, h in fingerprints.items())
                if not current:
                    continue
                valid = r.get('context_mode') == 'history_free' and r.get('verdict') == 'accept' and isinstance(r.get('execution_id'), str) and bool(r['execution_id'].strip()) and r['execution_id'] == r['execution_id'].strip()
                require(r.get('verdict') not in {'hold', 'revise'}, 'unresolved material reviewer objection')
                if valid:
                    accepting.append(r)
            evidence = next((r for r in accepting if r.get('lens') == 'evidence'), None)
            preservation = next((r for r in accepting if r.get('lens') == 'preservation'), None)
            require(evidence is not None and preservation is not None and bool(evidence.get('reviewer_id')) and bool(preservation.get('reviewer_id')) and evidence.get('reviewer_id') != preservation.get('reviewer_id'), 'two independent revision-bound accepting reviews required')
            require(evidence is not None and preservation is not None and evidence.get('execution_id') != preservation.get('execution_id'), 'review lenses require distinct actual returned context/execution IDs')
            refresh = packet.get('refresh', {})
            require(bool(refresh.get('captured_at')), 'missing final refresh cutoff')
            cutoff = timestamp(refresh['captured_at']) if refresh.get('captured_at') else None
            for sid in inputs:
                if sid in sources and sources[sid]['kind'] in MUTABLE:
                    require(sid in refresh.get('source_ids', []) and refresh.get('fingerprints', {}).get(sid) == sources[sid]['fingerprint'], 'missing/stale decision-input final refresh: ' + sid)
                    require(cutoff is not None and cutoff >= timestamp(sources[sid]['captured_at']), 'refresh cutoff predates decision-input capture')
    for decision in decisions:
        for ident in decision.get('draft_ids', []):
            require(any(d.get('id') == ident and d.get('key') == decision['key'] for d in drafts), 'orphan decision draft reference')
    targets = [(d.get('key'), d.get('kind')) for d in drafts]
    require(len(targets) == len(set(targets)), 'conflicting/uncombined operations on same issue')
    return {'valid': not errors, 'errors': errors, 'provisional': sorted(set(provisional)),
            'audited_drafts': [d['id'] for d in drafts if d.get('ready') == 'audited'] if not errors else [],
            'freshness': 'saved evidence cutoff only; no live reads'}


def parser():
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest='command', required=True)
    c = sub.add_parser('collect', help='collect read-only evidence into a private run directory')
    default = Path(os.environ.get('XDG_STATE_HOME', str(Path.home() / '.local/state'))) / 'personal-jira-triage' / (dt.datetime.now().strftime('%Y%m%dT%H%M%S') + '-' + os.urandom(4).hex())
    c.add_argument('--run-dir', default=str(default))
    c.add_argument('--imports')
    c.add_argument('--offline', action='store_true')
    for arg in ['issue', 'pr', 'root', 'plan-root']:
        c.add_argument('--' + arg, action='append', default=[])
    c.add_argument('--github-host', default='github.com')
    c.add_argument('--timeout', type=float, default=30)
    ck = sub.add_parser('check', help='validate a saved packet offline')
    ck.add_argument('packet_dir')
    return p


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        if args.command == 'check':
            report = check(args.packet_dir)
            print(json.dumps(report, indent=2))
            return 0 if report['valid'] else 2
        collector = Collector(args.run_dir, args.timeout)
        if args.imports:
            fresh_pr_resources = set()
            if not args.offline:
                for url in args.pr:
                    try:
                        fresh_pr_resources.add((args.github_host, pr_resource(url, args.github_host)))
                    except ValueError:
                        pass  # pr() records the invalid explicit URL without discarding imports.
            collector.imports(args.imports, args.issue if not args.offline else (), fresh_pr_resources)
        if not args.offline:
            collector.native_jira()
            collector.native_git(args.github_host)
        collector.inventory()
        known = {i['key'] for i in collector.packet['inventory']}
        for key in args.issue:
            if not KEY.fullmatch(key):
                raise ValueError('invalid explicit Jira key')
            if key not in known:
                collector.packet['inventory'].append({'key': key, 'role': 'context', 'active': True})
        selected = args.issue or [i['key'] for i in collector.packet['inventory'] if i.get('active') and i.get('role') in {'assigned', 'specialist'}]
        if not args.offline:
            collector.enrich(selected)
            for url in args.pr:
                collector.pr(url, args.github_host)
        else:
            resources = {(s['kind'], s['resource']) for s in collector.packet['sources']}
            for key in selected:
                for kind in ['jira_issue', 'jira_comments']:
                    if (kind, key) not in resources:
                        collector.gap(key + ':' + kind, 'selected issue capture unavailable in offline imports')
            for url in args.pr:
                try:
                    resource = pr_resource(url, args.github_host)
                    if not any(s['kind'] == 'git_pr' and s['site'] == args.github_host and s['resource'] == resource and s['complete']
                               for s in collector.packet['sources']):
                        collector.gap(resource, 'selected PR complete capture unavailable in offline imports')
                except ValueError as e:
                    collector.gap(url, e)
        collector.local(args.root or [str(Path.cwd())], args.plan_root)
        if not any(s['kind'] == 'jira_inventory' and s['complete'] for s in collector.packet['sources']):
            collector.gap('jira_inventory', 'complete assigned inventory unavailable')
        for role in ['reporter', 'watcher', 'former-assignee']:
            if not any(s['kind'] == 'jira_context' and s['resource'] == role and s['complete'] for s in collector.packet['sources']):
                collector.gap('jira_context:' + role, 'active contextual role discovery not completely captured')
        for role in ['author', 'assignee', 'review-requested', 'involves']:
            if not any(s['kind'] == 'git_search' and s['resource'] == role and s['complete'] for s in collector.packet['sources']):
                collector.gap('git_search:' + role, 'open Git role discovery not completely captured')
        collector.identities()
        code = collector.finish()
        print(json.dumps({'packet_dir': str(collector.root), 'inventory_count': len(collector.packet['inventory']), 'gaps': collector.packet['gaps']}, indent=2))
        return code
    except (ValueError, KeyError, OSError, TypeError, AttributeError, IndexError, RecursionError) as e:
        print(json.dumps({'valid': False, 'errors': [str(e)]}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
