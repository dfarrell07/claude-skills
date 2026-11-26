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
# Home-level application data (macOS Library and Trash) holds no clones and can prompt for access.
HOME_STATE_DIRS = {'Library', '.Trash'}
REF_FORMAT = '%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)'
FULL_HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')


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
    project = (path if re.fullmatch(r'(?:[A-Za-z0-9_.-]+/)+[A-Za-z0-9_.-]+', path)
               and all(part not in {'.', '..'} for part in path.split('/')) else None)
    return {'name': name, 'host': hostname, 'repo': project,
            'kind': 'github' if hostname == host and project is not None and REPO.fullmatch(path) else 'other'}


def log_records(repo, tips, start=None, end=None, no_walk=False):
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
    raw = git(repo, *args, stdin='\n'.join(tips) + '\n')
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


def stale_worktree_heads(repo, common, record):
    """Commits a missing worktree's retained HEAD reflog names, read without repairing or pruning it.

    Returns the available commits and whether every commit the reflog names is still present.
    """
    try:
        for admin in (Path(common) / 'worktrees').iterdir():
            try:
                gitdir = (admin / 'gitdir').read_text(encoding='utf-8').removesuffix('\n')
                if Path(gitdir).resolve() != (Path(record['path']) / '.git').resolve():
                    continue
            except OSError:
                continue
            failed = False
            try:
                lines = (admin / 'logs' / 'HEAD').read_text(encoding='utf-8').splitlines()
                # Each reflog line starts "<old> <new> "; a malformed line names nothing.
                named = {sha for line in lines for sha in line.split(' ', 2)[:2]
                         if HASH.fullmatch(sha) and set(sha) != {'0'}}
            except FileNotFoundError:
                if not (admin / 'reftable').is_dir():
                    return [], False  # a files-backend reflog that is gone
                # A reftable repository keeps the reflog in admin/reftable; only Git reads it
                # (and it omits entries whose objects are gone).
                try:
                    raw = git(repo, '--git-dir=' + str(admin), 'reflog', 'show', '--no-color', '--format=%H', 'HEAD')
                except subprocess.SubprocessError:
                    raw, failed = '', True
                named = {sha for sha in raw.split() if HASH.fullmatch(sha)}
            if isinstance(record.get('head'), str) and HASH.fullmatch(record['head']):
                named.add(record['head'])
            candidates = sorted(named)
            checked = git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                          stdin='\n'.join(candidates) + '\n').splitlines() if candidates else []
            available = [sha for sha, line in zip(candidates, checked) if line == sha + ' commit']
            return available, not failed and bool(candidates) and len(available) == len(candidates)
    except (OSError, ValueError, subprocess.SubprocessError):
        pass
    return [], False


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
                                  'branch': next((field[7:] for field in fields if field.startswith('branch ')), None),
                                  'detached': 'detached' in fields,
                                  'prunable': next((field[9:] for field in fields if field.startswith('prunable ')), None)}
                        entry['worktrees'].append(record)
                        if record['exists'] and 'bare' not in fields:
                            queue.append(path)
                        elif not record['exists']:
                            record['recovered_heads'], complete = stale_worktree_heads(canonical, common, record)
                            if not complete:
                                warn(f'Registered worktree is missing: {path}; some of its retained history is '
                                     'unavailable.')
                except (subprocess.SubprocessError, OSError, ValueError):
                    warn(f'Cannot enumerate registered worktrees for {canonical}.')
        except (subprocess.SubprocessError, OSError, UnicodeError):
            if marked:
                warn(f'Cannot inspect Git repository at {directory}; local coverage is incomplete.')

    try:
        home = Path.home()
    except (OSError, RuntimeError):
        home = None
        warn('Home directory could not be resolved; home-wide discovery and home-based cache exclusions are unavailable.')
    scan_roots = []
    if roots:
        for root in roots:
            try:
                scan_roots.append(Path(root).expanduser())
            except (OSError, RuntimeError):
                warn(f'Cannot expand requested scan root {root}; other roots remain usable.')
    else:
        if home is not None:
            scan_roots.append(home)
        try:
            scan_roots.append(Path.cwd())
        except (OSError, RuntimeError):
            warn('Current directory could not be resolved; current-directory discovery is unavailable.')
    dependency_roots = []
    for relative in ('go/pkg', 'go/.cache-go-build-container') if home is not None else ():
        candidate = home / relative
        try:
            dependency_roots.append(candidate.resolve())
        except (OSError, RuntimeError):
            dependency_roots.append(candidate.absolute())
    exclusions, unreadable = [], []
    visited = set()
    try:
        home_real = home.resolve() if home is not None else None
    except (OSError, RuntimeError):
        home_real = None

    def scan_error(error):
        # Respect the user's permissions. A directory another account owns (container or
        # build output) cannot hold the user's own clones and is only excluded; one the
        # user owns but cannot read is a stated gap.
        try:
            foreign = os.lstat(error.filename).st_uid != os.getuid()
        except (OSError, TypeError, AttributeError):
            foreign = False
        (exclusions if foreign else unreadable).append(str(error.filename))

    for root in dict.fromkeys(scan_roots):
        try:
            if not root.is_dir():
                scope = 'Requested' if roots else 'Default'
                warn(f'{scope} scan root is missing or not a directory: {root}')
                continue
            root = root.resolve()
        except (OSError, RuntimeError):
            warn(f'Cannot access scan root {root}; local coverage is incomplete.')
            continue
        add_repo(root, marked=any(os.path.lexists(parent / '.git') for parent in (root, *root.parents)))
        # String prefix checks: pathlib containment tests dominated large home scans.
        root_text = str(root)
        root_prefix = root_text.rstrip(os.sep) + os.sep
        dependency_prefixes = [text.rstrip(os.sep) + os.sep for text in map(str, dependency_roots)
                               if not (root_text + os.sep).startswith(text.rstrip(os.sep) + os.sep)]
        walk_targets = {root: root}
        for directory, dirs, files in os.walk(root, followlinks=True, onerror=scan_error):
            current = Path(directory)
            real = walk_targets.pop(current, None)
            if real is None:
                try:
                    real = current.resolve()
                except (OSError, RuntimeError):
                    warn(f'Cannot resolve scan directory {current}; local coverage is incomplete.')
                    dirs[:] = []
                    continue
            if real in visited:
                dirs[:] = []
                continue
            visited.add(real)
            if '.git' in dirs or '.git' in files:
                add_repo(current, marked=True)
            # Bare backups have no .git child. Detect their native shape before pruning objects.
            bare = 'HEAD' in files and 'objects' in dirs and 'refs' in dirs
            if bare:
                try:
                    verified_bare = git(current, 'rev-parse', '--is-bare-repository').strip() == 'true'
                except (OSError, ValueError, subprocess.SubprocessError):
                    verified_bare = False
                if verified_bare:
                    add_repo(current, marked=True)
                    dirs[:] = []
                    continue
            keep = []
            for name in sorted(dirs):
                child = current / name
                try:
                    target = child.resolve() if child.is_symlink() else real / name
                except (OSError, RuntimeError):
                    warn(f'Cannot resolve scan directory {child}; local coverage is incomplete.')
                    exclusions.append(str(child))
                    continue
                # Apply exclusions to symlink destinations before os.walk enters
                # them. An innocuously named alias must not expose tool state.
                target_text = str(target)
                if target_text == root_text:
                    parts = ()
                elif target_text.startswith(root_prefix):
                    parts = tuple(target_text[len(root_prefix):].split(os.sep))
                else:
                    parts = target.parts
                dependency_cache = any((target_text + os.sep).startswith(prefix) for prefix in dependency_prefixes)
                hidden_state = (name in STATE_DIRS or any(part in STATE_DIRS for part in parts)
                                or (name in HOME_STATE_DIRS and real == home_real))
                excluded_target = any(part in PRUNED or part.startswith('pytest-of-') for part in parts)
                if name in PRUNED or name.startswith('pytest-of-') or dependency_cache or hidden_state or excluded_target:
                    if name != '.git':
                        exclusions.append(str(child))
                else:
                    keep.append(name)
                    walk_targets[child] = target
            dirs[:] = keep
    while queue:
        add_repo(queue.pop(), marked=True)
    if unreadable:
        warn(f'Cannot scan {len(unreadable)} unreadable director{"y" if len(unreadable) == 1 else "ies"} '
             f'(first: {unreadable[0]}); local coverage is incomplete.')

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
            try:
                ordinary_tips = sorted(set(git(entry['path'], 'rev-list', '--all', '--no-walk=unsorted').splitlines()))
            except subprocess.SubprocessError:
                ordinary_tips = git(entry['path'], 'rev-list', '--single-worktree', '--all', '--no-walk=unsorted').splitlines()
                candidates = sorted({tree['head'] for tree in entry['worktrees']
                                     if isinstance(tree.get('head'), str) and HASH.fullmatch(tree['head'])})
                checked = git(entry['path'], 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                              stdin='\n'.join(candidates) + '\n').splitlines() if candidates else []
                available = {sha for sha, line in zip(candidates, checked) if line == sha + ' commit'}
                ordinary_tips = sorted(set(ordinary_tips) | available)
                for tree in entry['worktrees']:
                    tree['head_available'] = tree.get('head') in available
                warn(f'Some registered HEADs are unavailable for {entry["path"]}; healthy refs and admin histories were retained.')
            recovered_tips = {sha for tree in entry['worktrees'] for sha in tree.get('recovered_heads', [])}
            entry['tips'] = sorted(set(ordinary_tips) | recovered_tips)
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
                routing = {}
                for direction, options in (('fetch', []), ('push', ['--push'])):
                    for url in git(entry['path'], 'remote', 'get-url', *options, '--all', name).splitlines():
                        remote = remote_info(name, url, host)
                        key = tuple(remote.values())
                        record = routing.setdefault(key, {**remote, 'directions': []})
                        if direction not in record['directions']:
                            record['directions'].append(direction)
                entry['remotes'].extend(routing.values())
            recent_records = log_records(entry['path'], entry['tips'], start, end)
            heads = sorted({ref['hash'] if ref['object_type'] == 'commit' else ref['peeled_hash']
                            for ref in entry['refs']
                            if ref['object_type'] == 'commit' or ref['peeled_type'] == 'commit'}
                           | recovered_tips | {tree['head'] for tree in entry['worktrees']
                              if tree['detached'] and tree.get('head_available', True)
                              and isinstance(tree['head'], str) and HASH.fullmatch(tree['head'])})
            tip_records[entry['path']] = log_records(entry['path'], heads, no_walk=True)
            records[entry['path']] = recent_records
            entry['status'] = 'ok'
        except (subprocess.SubprocessError, OSError, ValueError, OverflowError):
            skipped += 1
            entry['status'] = 'skipped'
            warn(f'Could not read Git data for {entry["path"]}; local work there may be missing.')

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

    commits = {}
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
    return {'metadata': {'repos_searched': len(entries), 'repos_skipped': skipped, 'repos_with_commits': len(commits),
                         'scan_roots': [str(root.absolute()) for root in scan_roots], 'scan_exclusions': sorted(set(exclusions)),
                         'scan_unreadable': sorted(set(unreadable)),
                         'repositories': list(entries.values()), 'author_aliases': list(aliases.values())},
            'commits': commits}
