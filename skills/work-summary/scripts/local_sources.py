"""Discover working repositories and retain commit, ref, and worktree provenance."""

from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from evidence import HASH, REPO, UTC, git, iso, require


PRUNED = {'.git', 'node_modules', 'vendor', '__pycache__', '.pytest_cache', '.venv', 'venv', 'go-cache'}
STATE_DIRS = {'.cache', '.local', '.config', '.ssh', '.gnupg', '.npm', '.cargo', '.rustup', '.mozilla',
              '.pki', '.kube', '.aws', '.azure', '.docker', '.password-store', '.claude', '.codex',
              '.android', '.gradle', '.m2', '.sdkman', '.vscode'}
BACKUP = re.compile(r'(backup|(?:^|[/_-])bak\d*(?:$|[/_-])|archive|rescue|preserv|saved)', re.I)


def remote_info(name, url, host):
    """Retain routing evidence without exposing credential-bearing remote URLs."""
    if '://' in url:
        parts = urlsplit(url)
        hostname, path = parts.hostname, parts.path
    else:
        match = re.fullmatch(r'(?:[^@/]+@)?([^:/]+):(.+)', url)
        if not match:
            return {'name': name, 'host': None, 'repo': None, 'kind': 'local'}
        hostname, path = match.groups()
    hostname = hostname.lower() if hostname else None
    path = path.strip('/').removesuffix('.git')
    return {'name': name, 'host': hostname, 'repo': path if REPO.fullmatch(path) else None,
            'kind': 'github' if hostname == host and REPO.fullmatch(path) else 'other'}


def log_records(repo, tips, start=None, end=None, emails=None, exclude=None, limit=None, no_walk=False):
    if not tips:
        return []
    args = ['log', '--stdin', '--fixed-strings', '--no-use-mailmap', '--no-show-signature',
            '--no-color', '--encoding=UTF-8', '-z', '--format=%H%x00%ct%x00%at%x00%an%x00%ae%x00%s']
    if no_walk:
        args.append('--no-walk=unsorted')
    if start:
        args.append('--since-as-filter=' + iso(start))
    if end:
        args.append('--until=' + iso(end))
    if emails:
        args.extend('--author=<' + email + '>' for email in emails)
    if limit:
        args.append('--max-count=' + str(limit))
    revisions = '\n'.join(tips) + '\n'
    if exclude:
        revisions += '^' + exclude + '\n'
    raw = git(repo, *args, stdin=revisions)
    fields = raw.split('\0')
    require(fields.pop() == '' and len(fields) % 6 == 0, 'Malformed Git log output')
    result = []
    for offset in range(0, len(fields), 6):
        sha, committed, authored, name, email, message = fields[offset:offset + 6]
        require(HASH.fullmatch(sha), 'Invalid commit identity')
        result.append({'hash': sha, 'message': message, 'author_name': name, 'author_email': email,
                       'authored_at': iso(datetime.fromtimestamp(int(authored), UTC)),
                       'committed_at': iso(datetime.fromtimestamp(int(committed), UTC))})
    return result


def collect_local(roots, start, end, warn, api=None, identity=None, author_emails=()):
    identity = identity or {}
    host = os.environ.get('GH_HOST', 'github.com').lower()
    entries, inspected, queue = {}, set(), []
    configured_names = {' '.join(identity['name'].casefold().split())} if identity.get('name') else set()

    def add_repo(directory, marked=False):
        directory = Path(directory)
        if directory in inspected:
            return
        inspected.add(directory)
        try:
            bare = git(directory, 'rev-parse', '--is-bare-repository').strip() == 'true'
            if bare:
                canonical = directory.resolve()
            else:
                canonical = Path(git(directory, 'rev-parse', '--show-toplevel').removesuffix('\n')).resolve()
            common = Path(git(canonical, 'rev-parse', '--git-common-dir').removesuffix('\n'))
            common = (common if common.is_absolute() else canonical / common).resolve()
            entry = entries.setdefault(str(common), {'path': str(canonical), 'common_dir': str(common), 'bare': bare,
                                                      'identities': set(), 'paths': set(), 'worktrees': []})
            if str(canonical) in entry['paths']:
                return
            entry['paths'].add(str(canonical))
            try:
                email = git(canonical, 'config', '--get', 'user.email').strip()
                require(email and '\n' not in email, 'Missing author email')
                entry['identities'].add(email)
            except (subprocess.SubprocessError, ValueError):
                warn(f'No Git author email configured for {canonical}; that worktree identity is unavailable.')
            try:
                name = git(canonical, 'config', '--get', 'user.name').strip()
                if name:
                    configured_names.add(' '.join(name.casefold().split()))
            except subprocess.SubprocessError:
                pass
            if not entry.get('worktrees_inspected'):
                entry['worktrees_inspected'] = True
                try:
                    raw = git(canonical, 'worktree', 'list', '--porcelain', '-z')
                    for block in raw.split('\0\0'):
                        fields = block.strip('\0').split('\0')
                        if not fields or not fields[0].startswith('worktree '):
                            continue
                        path = Path(fields[0][9:])
                        record = {'path': str(path), 'exists': path.is_dir(),
                                  'head': next((field[5:] for field in fields if field.startswith('HEAD ')), None),
                                  'branch': next((field[7:] for field in fields if field.startswith('branch ')), None)}
                        entry['worktrees'].append(record)
                        if record['exists'] and 'bare' not in fields:
                            queue.append(path)
                        elif not record['exists']:
                            warn(f'Registered worktree is missing: {path}; available Git objects are still inventoried.')
                except (subprocess.SubprocessError, OSError, ValueError):
                    warn(f'Cannot enumerate registered worktrees for {canonical}.')
        except (subprocess.SubprocessError, OSError, UnicodeError):
            if marked:
                warn(f'Cannot inspect Git repository at {directory}; local coverage is incomplete.')

    scan_roots = [Path(root).expanduser() for root in roots] if roots else [Path.home(), Path.cwd()]
    exclusions = []
    visited = set()
    for root in dict.fromkeys(scan_roots):
        try:
            if not root.is_dir():
                if roots:
                    warn(f'Requested scan root is missing or not a directory: {root}')
                continue
            root = root.resolve()
        except (OSError, RuntimeError):
            warn(f'Cannot access scan root {root}; local coverage is incomplete.')
            continue
        add_repo(root, marked=any(os.path.lexists(parent / '.git') for parent in (root, *root.parents)))
        for directory, dirs, files in os.walk(root, followlinks=True, onerror=lambda error: warn(f'Cannot scan {error.filename}; local coverage is incomplete.')):
            current = Path(directory)
            real = current.resolve()
            if real in visited:
                dirs[:] = []
                continue
            visited.add(real)
            if '.git' in dirs or '.git' in files:
                add_repo(current, marked=True)
            # Bare backups have no .git child. Detect their native shape before pruning objects.
            bare = 'HEAD' in files and 'objects' in dirs and 'refs' in dirs and 'config' in files
            if bare:
                add_repo(current, marked=True)
                dirs[:] = []
                continue
            keep = []
            for name in sorted(dirs):
                child = current / name
                try:
                    target = child.resolve()
                except (OSError, RuntimeError):
                    warn(f'Cannot resolve scan directory {child}; local coverage is incomplete.')
                    exclusions.append(str(child))
                    continue
                # Apply exclusions to symlink destinations before os.walk enters
                # them. An innocuously named alias must not expose tool state.
                parts = target.relative_to(root).parts if target.is_relative_to(root) else target.parts
                dependency_root = Path.home() / 'go/pkg'
                dependency_cache = (target.is_relative_to(dependency_root)
                                    and not root.is_relative_to(dependency_root))
                hidden_state = name in STATE_DIRS or any(part in STATE_DIRS for part in parts)
                excluded_target = any(part in PRUNED or part.startswith('pytest-of-') for part in parts)
                if name in PRUNED or name.startswith('pytest-of-') or dependency_cache or hidden_state or excluded_target:
                    if name != '.git':
                        exclusions.append(str(child))
                else:
                    keep.append(name)
            dirs[:] = keep
    while queue:
        add_repo(queue.pop(), marked=True)

    aliases = {email: {'email': email, 'source': 'explicit'} for email in author_emails}
    if identity.get('email'):
        aliases[identity['email']] = {'email': identity['email'], 'source': 'authenticated_github_profile'}
    if identity.get('login'):
        login = identity['login']
        for email in ([f"{identity['id']}+{login}@users.noreply.github.com"] if host == 'github.com' and isinstance(identity.get('id'), int) else []):
            aliases[email] = {'email': email, 'source': 'authenticated_github_noreply'}
    records, tip_records, skipped = {}, {}, 0
    for entry in entries.values():
        entry['identities'] = sorted(entry['identities'])
        entry['paths'] = sorted(entry['paths'])
        entry.pop('worktrees_inspected', None)
        try:
            entry['tips'] = sorted(set(git(entry['path'], 'rev-list', '--all', '--no-walk=unsorted').splitlines()))
            require(all(HASH.fullmatch(tip) for tip in entry['tips']), 'Invalid reference tip')
            raw = git(entry['path'], 'for-each-ref', '--format=%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)')
            entry['refs'] = []
            for line in raw.splitlines():
                name, sha, kind, peeled, peeled_kind, upstream, remote, remote_ref = line.split('\0')
                entry['refs'].append({'name': name, 'hash': sha, 'object_type': kind, 'peeled_hash': peeled or None,
                                      'peeled_type': peeled_kind or None, 'upstream': upstream or None,
                                      'upstream_remote': remote or None, 'upstream_ref': remote_ref or None})
            entry['remotes'] = []
            for name in git(entry['path'], 'remote').splitlines():
                for url in git(entry['path'], 'remote', 'get-url', '--all', name).splitlines():
                    entry['remotes'].append(remote_info(name, url, host))
            entry['default_ref'] = None
            for candidate in ('refs/remotes/upstream/HEAD', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main',
                              'refs/remotes/origin/master', 'refs/remotes/origin/devel', 'refs/heads/main', 'refs/heads/master', 'refs/heads/devel'):
                try:
                    sha = git(entry['path'], 'rev-parse', '--verify', candidate + '^{commit}').strip()
                    entry['default_ref'] = {'name': candidate, 'hash': sha, 'source': 'cached_local_ref'}
                    break
                except subprocess.SubprocessError:
                    continue
            records[entry['path']] = log_records(entry['path'], entry['tips'], start, end)
            heads = sorted({ref['hash'] if ref['object_type'] == 'commit' else ref['peeled_hash']
                            for ref in entry['refs']
                            if ref['object_type'] == 'commit' or ref['peeled_type'] == 'commit'})
            tip_records[entry['path']] = log_records(entry['path'], heads, no_walk=True)
            entry['status'] = 'ok'
        except (subprocess.SubprocessError, OSError, ValueError, OverflowError):
            skipped += 1
            entry['status'] = 'skipped'
            warn(f'Git inventory failed for {entry["path"]}; local evidence is incomplete.')

    # Names select verification leads only. They never establish ownership.
    unknown = {}
    for entry in entries.values():
        # Historical backup tips can supply alias leads even when no commit
        # using that email falls inside the requested rolling window.
        for commit in records.get(entry['path'], []) + tip_records.get(entry['path'], []):
            email = commit['author_email']
            if email not in entry['identities'] and email not in aliases and ' '.join(commit['author_name'].casefold().split()) in configured_names:
                unknown.setdefault(email, (entry, commit))
    for email, (entry, commit) in unknown.items():
        if api and identity.get('login'):
            try:
                response = api('--method', 'GET', 'search/commits', '-f',
                               f'q=author:{identity["login"]} author-email:{email}', '-F', 'per_page=1')
                for item in response.get('items', []):
                    if item.get('author', {}).get('login', '').casefold() == identity['login'].casefold() and item.get('commit', {}).get('author', {}).get('email') == email:
                        aliases[email] = {'email': email, 'source': 'github_commit_account', 'url': item.get('html_url'), 'hash': item['sha']}
                        break
            except (subprocess.SubprocessError, OSError, ValueError, TypeError, KeyError, AttributeError):
                pass

    commits, unattributed, branches = {}, [], []
    for entry in entries.values():
        configured = list(entry['identities'])
        entry['configured_identities'] = configured
        entry['identities'] = sorted(set(configured) | set(aliases))
        if not entry['identities'] and entry.get('status') == 'ok':
            entry['status'] = 'skipped'
            skipped += 1
        found = [record for record in records.get(entry['path'], []) if record['author_email'] in entry['identities']]
        if found:
            commits[entry['path']] = found
        for record in records.get(entry['path'], []):
            if record['author_email'] in unknown and record['author_email'] not in entry['identities']:
                unattributed.append({'repo': entry['path'], **record, 'reason': 'name_match_only; account attribution unverified'})
        if entry.get('status') != 'ok':
            continue
        try:
            info = {record['hash']: record for record in tip_records.get(entry['path'], [])}
            grouped = {}
            base = entry.get('default_ref')
            for ref in entry['refs']:
                if ref['object_type'] != 'commit' and ref['peeled_type'] != 'commit':
                    if ref['object_type'] != 'tag':
                        branches.append({'repo': entry['path'], 'refs': [ref['name']], 'hash': ref['hash'],
                                         'object_type': ref['object_type'], 'attribution': 'undated_noncommit_snapshot',
                                         'pr_status': 'not_applicable', 'prs': []})
                    continue
                sha = ref['hash'] if ref['object_type'] == 'commit' else ref['peeled_hash']
                if ref['name'].startswith('refs/tags/') and not BACKUP.search(ref['name']):
                    continue
                grouped.setdefault(sha, []).append(ref)
            for sha, refs in grouped.items():
                tip = info[sha]
                owner = tip['author_email'] in entry['identities']
                backup = any(bool(BACKUP.search(ref['name'])) or not ref['name'].startswith(
                    ('refs/heads/', 'refs/remotes/', 'refs/tags/')) for ref in refs)
                history_candidate = bool(base and entry['identities'] and
                                         (backup or any(ref['name'].startswith('refs/heads/') for ref in refs)))
                # Unrelated remote refs cannot gain attribution through this
                # path. Avoid walking their histories merely to discard them.
                if not owner and not history_candidate:
                    continue
                contained = None
                if base:
                    try:
                        git(entry['path'], 'merge-base', '--is-ancestor', sha, base['hash'])
                        contained = True
                    except subprocess.CalledProcessError as error:
                        if error.returncode == 1:
                            contained = False
                        else:
                            raise
                proof = None
                if not owner and history_candidate and contained is False:
                    matches = log_records(entry['path'], [sha], emails=entry['identities'],
                                          exclude=base['hash'], limit=1)
                    proof = next((record for record in matches if record['author_email'] in entry['identities']), None)
                    owner = proof is not None
                recent = start <= datetime.fromisoformat(tip['committed_at'].replace('Z', '+00:00')) <= end
                if not owner or (contained is True and not backup and not recent):
                    continue
                record = {'repo': entry['path'], **tip, 'object_type': 'commit',
                          'refs': [ref['name'] for ref in refs], 'backup': backup,
                          'contained_in_cached_default': contained, 'recent_tip': recent,
                          'attribution': ('contains_configured_or_verified_author' if proof
                                          else 'configured_or_verified_author'),
                          'pr_status': 'unchecked', 'prs': []}
                if proof:
                    record['attribution_commit'] = proof
                branches.append(record)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, OverflowError):
            warn(f'Cannot classify ref snapshots for {entry["path"]}; branch coverage is incomplete.')
    return {'metadata': {'repos_searched': len(entries), 'repos_skipped': skipped, 'repos_with_commits': len(commits),
                         'scan_roots': [str(root.absolute()) for root in scan_roots], 'scan_exclusions': sorted(set(exclusions)),
                         'repositories': list(entries.values()), 'author_aliases': list(aliases.values())},
            'commits': commits, 'branches': branches, 'unattributed_commits': unattributed}
