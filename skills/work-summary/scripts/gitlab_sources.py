"""Read advertised GitLab MR heads using existing Git authentication, without fetch."""

import functools
import hashlib
import os
import re
import shutil
import subprocess
import tempfile
from urllib.parse import urlsplit

from evidence import GIT_ENV, HOST, require


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


@functools.lru_cache(maxsize=1)
def user_ssh_command():
    """The user's own global/system SSH command, such as one selecting a work key."""
    for scope in ('--global', '--system'):
        try:
            result = subprocess.run(['git', 'config', scope, '--get', 'core.sshCommand'], env=GIT_ENV,
                                    capture_output=True, timeout=5, check=False, cwd=os.path.expanduser('~'))
        except (OSError, subprocess.SubprocessError):
            continue
        value = result.stdout.decode('utf-8', errors='replace').strip()
        if result.returncode == 0 and value:
            return value
    return 'ssh'


def git_env():
    # ssh uses the first value of each option, so the user's explicit choices still apply.
    return {**GIT_ENV, 'GIT_ALLOW_PROTOCOL': 'ssh:https', 'GIT_TERMINAL_PROMPT': '0',
            'GIT_ASKPASS': 'false', 'SSH_ASKPASS': 'false', 'SSH_ASKPASS_REQUIRE': 'never',
            'GIT_SSH_COMMAND': (user_ssh_command() + ' -o BatchMode=yes -o StrictHostKeyChecking=yes '
                                '-o UpdateHostKeys=no -o ConnectTimeout=10 -o ClearAllForwardings=yes'),
            'GIT_SSH_VARIANT': 'ssh'}


def native_git(repo, *args, timeout=90, env=None):
    return subprocess.run(['git', '--no-pager', '--no-optional-locks', '-c', 'credential.interactive=false',
                           '-C', repo, *args],
                          env={**git_env(), **(env or {})}, capture_output=True, timeout=timeout, check=False)


def collect_gitlab_refs(branches, repositories, warn):
    """Add native head matches without inventing MR state, authorship, or dates."""
    selected = {branch['repo'] for branch in branches if branch['object_type'] == 'commit'}
    result = {'status': 'not_applicable', 'scope': SCOPE, 'queries': [], 'routes': [], 'failures': []}
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
            route_record = {'local_repo': path, 'remote': name, **route}
            if route_record not in result['routes']:
                result['routes'].append(route_record)
            push_result = native_git(path, 'remote', 'get-url', '--push', '--all', '--', name, timeout=15)
            require(push_result.returncode == 0, 'push_remote_scope_unavailable')
            extra_urls = urls[1:] + push_result.stdout.decode('utf-8').splitlines()
            for alternative in {tuple(sorted(value.items())) for url in extra_urls
                                if (value := remote_route(url)) is not None
                                and (value['host'], value['repo']) != (route['host'], route['repo'])}:
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
                # Read the verified URL from an empty directory: no repository-local config
                # (credential helpers, proxies, URL rewrites, SSH commands) can take part.
                with tempfile.TemporaryDirectory(prefix='work-summary-ls-remote.') as neutral:
                    # A temporary directory inside some checkout must not adopt that repository.
                    native = native_git(neutral, 'ls-remote', '--refs', '--upload-pack=git-upload-pack', '--',
                                        urls[0], 'refs/merge-requests/*/head', 'refs/merge-requests/*/merge',
                                        env={'GIT_CEILING_DIRECTORIES': os.path.dirname(neutral)})
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


