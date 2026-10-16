"""Read advertised GitLab MR heads using existing Git authentication, without fetch."""

import hashlib
import re
import shutil
import subprocess
from urllib.parse import urlsplit

from evidence import GIT_ENV, HOST, integer, require


PART = re.compile(r'[A-Za-z0-9_.-]+\Z')
REMOTE = re.compile(r'[A-Za-z0-9_.][A-Za-z0-9_./-]*\Z')
MR_REF = re.compile(r'refs/merge-requests/([1-9][0-9]*)/(head|merge)\Z')
FULL_HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})\Z')
SCOPE = 'advertised_current_merge_request_refs'


def gitlab_host(host):
    return isinstance(host, str) and HOST.fullmatch(host) and 'gitlab' in host.casefold().split('.')


def project_path(value):
    return (isinstance(value, str) and len(value.split('/')) >= 2
            and all(PART.fullmatch(part) and part not in {'.', '..'} for part in value.split('/')))


def remote_route(url):
    """Discard all userinfo; retain only a conservative provider route."""
    if not isinstance(url, str) or any(char in url for char in '\r\n\x00'):
        return None
    try:
        if '://' in url:
            parts = urlsplit(url)
            if parts.scheme not in {'ssh', 'https'} or parts.query or parts.fragment:
                return None
            host, path, transport = parts.hostname, parts.path, parts.scheme
            # A nondefault HTTPS origin cannot be cited as the default web host.
            if parts.scheme == 'https' and parts.port not in {None, 443}:
                return None
            parts.port  # Reject invalid ports without exposing credential-bearing URLs.
        else:
            match = re.fullmatch(r'(?:[^@/:\s]+@)?([^/:\s]+):(.+)', url)
            if not match:
                return None
            host, path = match.groups()
            transport = 'ssh'
        host = host.casefold() if host else None
        path = path.removeprefix('/').removesuffix('.git')
        if not gitlab_host(host) or not project_path(path):
            return None
        return {'host': host, 'repo': path, 'transport': transport}
    except (ValueError, AttributeError):
        return None


def parse_refs(output):
    """Retain every independently valid advertisement and account for bad rows."""
    rows, errors, seen = [], [], {}
    for number, line in enumerate(output.splitlines(), 1):
        fields = line.split('\t')
        match = MR_REF.fullmatch(fields[1]) if len(fields) == 2 else None
        if not match or len(match[1]) > 20 or not FULL_HASH.fullmatch(fields[0]):
            errors.append({'line': number, 'reason': 'invalid_advertised_ref'})
            continue
        sha, ref = fields
        if ref in seen:
            errors.append({'line': number, 'reason': 'duplicate_advertised_ref'})
            if seen[ref] == sha:
                continue
        seen[ref] = sha
        rows.append({'hash': sha, 'ref': ref, 'number': int(match[1]), 'kind': match[2]})
    return rows, errors


def source_key(host, repo, local_repo, remote):
    suffix = hashlib.sha256((local_repo + '\0' + remote).encode()).hexdigest()[:16]
    return host + ':' + repo + ':' + suffix


def git_env():
    return {**GIT_ENV, 'GIT_ALLOW_PROTOCOL': 'ssh:https', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_SSH_COMMAND': ('ssh -o BatchMode=yes -o StrictHostKeyChecking=yes -o UpdateHostKeys=no '
                                '-o ConnectTimeout=10 -o ClearAllForwardings=yes'),
            'GIT_SSH_VARIANT': 'ssh'}


def native_git(repo, *args, timeout=90):
    return subprocess.run(['git', '--no-pager', '--no-optional-locks', '-C', repo, *args],
                          env=git_env(), capture_output=True, timeout=timeout, check=False)


def collect_gitlab_refs(branches, repositories, warn):
    """Add native head matches without inventing MR state, authorship, or dates."""
    selected = {branch['repo'] for branch in branches if branch['object_type'] == 'commit'}
    result = {'status': 'not_applicable', 'scope': SCOPE, 'queries': [], 'failures': []}
    candidates = [(entry, remote) for entry in repositories if entry['path'] in selected
                  for remote in entry.get('remotes', []) if gitlab_host(remote.get('host'))]
    if not candidates:
        return result
    if shutil.which('git') is None:
        result['status'] = 'unavailable'
        warn('GitLab MR refs: Git is unavailable; existing GitLab remote refs could not be read.')
        return result
    complete, requested = set(), set()
    for entry, remote in candidates:
        path, name = entry['path'], remote.get('name')
        failure = {'local_repo': path, 'remote': name, 'host': remote['host'], 'repo': remote.get('repo')}
        try:
            require(isinstance(name, str) and REMOTE.fullmatch(name), 'unsafe_remote_name')
            route_result = native_git(path, 'remote', 'get-url', '--all', '--', name, timeout=15)
            require(route_result.returncode == 0, 'effective_remote_unavailable')
            urls = route_result.stdout.decode('utf-8').splitlines()
            require(bool(urls), 'effective_remote_unavailable')
            route = remote_route(urls[0])
            require(route is not None and route['host'] == remote['host'].casefold(), 'effective_remote_route_mismatch')
            # Git fetch/ls-remote uses the first fetch URL, never a different push URL.
            # Preserve other configured routes as explicit unsearched scopes.
            expected = remote.get('repo')
            require(expected is None or expected == route['repo'], 'effective_remote_route_mismatch')
            push_result = native_git(path, 'remote', 'get-url', '--push', '--all', '--', name, timeout=15)
            require(push_result.returncode == 0, 'push_remote_scope_unavailable')
            extra_urls = urls[1:] + push_result.stdout.decode('utf-8').splitlines()
            for alternative in {tuple(sorted(value.items())) for url in extra_urls
                                if (value := remote_route(url)) is not None and value != route}:
                other = dict(alternative)
                result['failures'].append({**failure, 'host': other['host'], 'repo': other['repo'],
                                           'reason': 'additional_url_not_addressable_by_remote_name'})
            pair = route['host'], route['repo']
            requested.add(pair)
            if pair in complete:
                continue
            key = source_key(*pair, path, name)
            require(not any(query['source_key'] == key for query in result['queries']), 'duplicate_remote_candidate')
            query = {'source_key': key, 'local_repo': path, 'remote': name, **route,
                     'effective_remote': route, 'scope': SCOPE, 'status': 'partial',
                     'returncode': None, 'native_output': '', 'errors': [], 'refs': []}
            result['queries'].append(query)
            try:
                native = native_git(path, 'ls-remote', '--refs', '--upload-pack=git-upload-pack', '--', name,
                                    'refs/merge-requests/*/head', 'refs/merge-requests/*/merge')
                output, code = native.stdout, native.returncode
            except subprocess.TimeoutExpired as error:
                output, code = error.stdout or b'', None
                query['errors'].append({'reason': 'native_read_timeout'})
            query['returncode'] = code
            query['native_output'] = output.decode('utf-8', errors='surrogateescape')
            query['refs'], parse_errors = parse_refs(query['native_output'])
            query['errors'].extend(parse_errors)
            if code not in {0, None}:
                query['errors'].append({'reason': 'native_read_failed'})
            if code == 0 and not query['errors']:
                query['status'] = 'ok'
                complete.add(pair)
        except (OSError, UnicodeError, ValueError, subprocess.SubprocessError) as error:
            failure['reason'] = str(error) if type(error) is ValueError else type(error).__name__
            result['failures'].append(failure)
    heads = {}
    for query in result['queries']:
        for row in query['refs']:
            if row['kind'] == 'head':
                heads.setdefault(row['hash'], []).append((query, row))
    for branch in branches:
        if branch['object_type'] != 'commit':
            continue
        matches = branch.setdefault('merge_requests', [])
        seen = {(row['url'], row['hash']) for row in matches}
        for query, row in heads.get(branch['hash'], []):
            url = f"https://{query['host']}/{query['repo']}/-/merge_requests/{row['number']}"
            if (url, row['hash']) not in seen:
                matches.append({'host': query['host'], 'repo': query['repo'], 'number': row['number'], 'url': url,
                                'hash': row['hash'], 'ref': row['ref'], 'proof': 'native_merge_request_head',
                                'source_key': query['source_key']})
                seen.add((url, row['hash']))
    result['status'] = 'ok' if requested and complete == requested and not result['failures'] else 'partial'
    if result['status'] == 'partial':
        warn('GitLab MR refs: some configured project advertisements remain incomplete; retained native head matches.')
    return result


def validate_gitlab(data):
    """Bind every MR citation to its captured native route and exact advertised head."""
    discovery = data['metadata'].get('gitlab_discovery')
    if discovery is None:
        require(not any(branch.get('merge_requests') for branch in data.get('branches', [])),
                'Merge request lacks native discovery')
        return
    require(isinstance(discovery, dict) and discovery.get('scope') == SCOPE
            and discovery.get('status') in {'ok', 'partial', 'not_applicable', 'unavailable'}
            and isinstance(discovery.get('queries'), list) and isinstance(discovery.get('failures'), list),
            'Invalid GitLab discovery')
    repositories = {entry['path']: entry for entry in data['metadata'].get('repositories', [])}
    queries, requested, complete = {}, set(), set()
    for query in discovery['queries']:
        require(isinstance(query, dict) and query.get('local_repo') in repositories, 'Unknown GitLab local repository')
        host, repo = query.get('host'), query.get('repo')
        require(gitlab_host(host) and project_path(repo) and query.get('scope') == SCOPE,
                'Invalid GitLab project route')
        require(query.get('effective_remote') == {'host': host, 'repo': repo, 'transport': query.get('transport')}
                and query.get('transport') in {'ssh', 'https'}, 'Invalid effective GitLab route')
        require(isinstance(query.get('remote'), str) and REMOTE.fullmatch(query['remote']), 'Invalid GitLab remote name')
        key = source_key(host, repo, query['local_repo'], query.get('remote', ''))
        require(query.get('source_key') == key and key not in queries, 'Duplicate or invalid GitLab source key')
        require(any(remote.get('name') == query.get('remote') and isinstance(remote.get('host'), str)
                    and remote['host'].casefold() == host
                    and remote.get('repo') in {None, repo}
                    for remote in repositories[query['local_repo']].get('remotes', [])),
                'GitLab query absent from native remote inventory')
        require(isinstance(query.get('native_output'), str) and isinstance(query.get('errors'), list)
                and query.get('status') in {'ok', 'partial'}
                and (query.get('returncode') is None or type(query['returncode']) is int), 'Invalid native GitLab result')
        rows, errors = parse_refs(query['native_output'])
        require(query.get('refs') == rows and [error for error in query['errors'] if 'line' in error] == errors,
                'GitLab refs differ from native advertisement')
        allowed = {'native_read_timeout', 'native_read_failed'}
        require(all(isinstance(error, dict) and (error in errors or set(error) == {'reason'}
                    and error['reason'] in allowed) for error in query['errors']), 'Invalid GitLab parse gaps')
        reasons = {error.get('reason') for error in query['errors']}
        require(('native_read_timeout' in reasons) == (query['returncode'] is None)
                and ('native_read_failed' in reasons) == (query['returncode'] not in {0, None}),
                'Unaccounted native GitLab command failure')
        require(query['status'] == ('ok' if query['returncode'] == 0 and not query['errors'] else 'partial'),
                'GitLab native result status mismatch')
        queries[key] = query
        pair = host, repo
        requested.add(pair)
        if query['status'] == 'ok':
            complete.add(pair)
    if discovery['status'] == 'ok':
        require(requested and requested == complete and not discovery['failures'], 'Incomplete GitLab discovery marked complete')
    if discovery['status'] in {'unavailable', 'not_applicable'}:
        require(not queries, 'Unavailable GitLab discovery has native queries')
    for branch in data.get('branches', []):
        seen = set()
        require(isinstance(branch.get('merge_requests', []), list), 'Invalid native MR associations')
        for match in branch.get('merge_requests', []):
            require(isinstance(match, dict), 'Invalid native MR association')
            query = queries.get(match.get('source_key'))
            require(query is not None and branch['object_type'] == 'commit', 'MR lacks a native GitLab query')
            number = match.get('number')
            require(integer(number, 1) and match.get('host') == query['host'] and match.get('repo') == query['repo']
                    and match.get('hash') == branch['hash']
                    and match.get('ref') == f'refs/merge-requests/{number}/head'
                    and match.get('proof') == 'native_merge_request_head'
                    and match.get('url') == f"https://{query['host']}/{query['repo']}/-/merge_requests/{number}"
                    and {'hash': branch['hash'], 'ref': match['ref'], 'number': number, 'kind': 'head'} in query['refs'],
                    'MR link differs from advertised native head')
            require(not any(field in match for field in ('state', 'actor', 'authored_at', 'created_at', 'updated_at')),
                    'Native Git MR refs do not prove state, actor, or time')
            identity = match['url'], match['hash']
            require(identity not in seen, 'Duplicate MR branch association')
            seen.add(identity)
