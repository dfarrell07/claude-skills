"""Discover working repositories and retain commit, ref, and worktree provenance."""

from datetime import datetime
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import urlsplit

from evidence import HASH, REPO, UTC, git, iso, require
from capabilities import inspect_unreadable_tree


PRUNED = {'.git', 'node_modules', 'vendor', '__pycache__', '.pytest_cache', '.venv', 'venv', 'go-cache'}
STATE_DIRS = {'.cache', '.local', '.config', '.ssh', '.gnupg', '.npm', '.cargo', '.rustup', '.mozilla',
              '.pki', '.kube', '.aws', '.azure', '.docker', '.password-store', '.claude', '.codex',
              '.android', '.gradle', '.m2', '.sdkman', '.vscode'}
BACKUP = re.compile(r'(backup|(?:^|[/_-])bak\d*(?:$|[/_-])|archive|rescue|preserv|saved)', re.I)
PRIVATE_NAMESPACES = ('refs/bisect/', 'refs/rewritten/', 'refs/worktree/')
REF_FORMAT = '%(refname)%00%(objectname)%00%(objecttype)%00%(*objectname)%00%(*objecttype)%00%(upstream)%00%(upstream:remotename)%00%(upstream:remoteref)'
FULL_HASH = re.compile(r'(?:[0-9a-f]{40}|[0-9a-f]{64})')


def parse_private_refs(raw):
    refs, errors = [], []
    seen = set()
    for number, line in enumerate(raw.splitlines(), 1):
        fields = line.split('\0')
        if (len(fields) != 8 or not fields[0].startswith(PRIVATE_NAMESPACES)
                or not FULL_HASH.fullmatch(fields[1]) or fields[2] not in {'commit', 'tag', 'tree', 'blob'}
                or (fields[3] and not FULL_HASH.fullmatch(fields[3]))
                or bool(fields[3]) != bool(fields[4])
                or (fields[4] and fields[4] not in {'commit', 'tag', 'tree', 'blob'})
                or fields[0] in seen):
            errors.append({'line': number, 'reason': 'invalid_private_ref_record'})
            continue
        seen.add(fields[0])
        refs.append(dict(zip(('name', 'hash', 'object_type', 'peeled_hash', 'peeled_type',
                              'upstream', 'upstream_remote', 'upstream_ref'),
                             [field or None for field in fields])))
    return refs, errors


def capture_private_refs(entry, tree):
    """Read the worktree-specific namespaces without creating or repairing refs."""
    proof = {'status': 'partial', 'route': 'worktree', 'command_path': tree['path'],
             'admin_dir': None, 'common_dir_output': None, 'common_dir': None,
             'gitdir_output': None, 'native_output': '', 'returncode': None,
             'refs': [], 'parse_errors': [], 'errors': []}
    try:
        options = ()
        if not Path(tree['path']).is_dir():
            admin = tree.get('recovery', {}).get('admin_dir')
            if admin is None:
                # A worktree may disappear after worktree-list was captured.
                for candidate in (Path(entry['common_dir']) / 'worktrees').iterdir():
                    try:
                        target = (candidate / 'gitdir').read_text(encoding='utf-8').removesuffix('\n')
                        if Path(target).resolve() == (Path(tree['path']) / '.git').resolve():
                            admin = str(candidate)
                            break
                    except OSError:
                        continue
            require(isinstance(admin, str), 'Missing retained worktree admin route')
            proof.update(route='retained_admin', command_path=entry['path'], admin_dir=admin)
            require(Path(admin).resolve().parent == (Path(entry['common_dir']) / 'worktrees').resolve(),
                    'Foreign retained worktree admin route')
            proof['gitdir_output'] = (Path(admin) / 'gitdir').read_text(encoding='utf-8')
            require(Path(proof['gitdir_output'].removesuffix('\n')).resolve()
                    == (Path(tree['path']) / '.git').resolve(), 'Foreign retained worktree path')
            options = ('--git-dir=' + admin,)
        raw_common = git(proof['command_path'], *options, 'rev-parse', '--git-common-dir')
        common = Path(raw_common.removesuffix('\n'))
        common = (common if common.is_absolute() else Path(proof['command_path']) / common).resolve()
        proof.update(common_dir_output=raw_common, common_dir=str(common))
        require(common == Path(entry['common_dir']).resolve(), 'Foreign worktree common repository')
        try:
            proof['native_output'] = git(proof['command_path'], *options, 'for-each-ref',
                                         '--format=' + REF_FORMAT, *PRIVATE_NAMESPACES)
            proof['returncode'] = 0
        except subprocess.CalledProcessError as error:
            proof['native_output'] = error.stdout if isinstance(error.stdout, str) else ''
            proof['returncode'] = error.returncode
            proof['errors'].append('private_ref_command')
        proof['refs'], proof['parse_errors'] = parse_private_refs(proof['native_output'])
        if not proof['errors'] and not proof['parse_errors']:
            proof['status'] = 'ok'
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        proof['errors'].append('private_ref_route_or_read')
    return proof


def private_ref_pointers(entry):
    """Validate retained native namespace evidence and return synthetic pointers."""
    result = {}
    for tree in entry.get('worktrees', []):
        proof = tree.get('private_refs')
        if proof is None:
            continue  # Older captures did not inspect worktree-private refs.
        require(proof.get('status') in {'ok', 'partial'}, 'Invalid private ref status')
        refs, errors = parse_private_refs(proof['native_output'])
        require(refs == proof['refs'] and errors == proof['parse_errors'], 'Private ref native rows differ')
        require(proof['status'] == ('partial' if proof['errors'] or errors else 'ok'), 'Private ref completeness differs')
        if proof['returncode'] is None:
            require(not refs and not proof['native_output'] and 'private_ref_route_or_read' in proof['errors'],
                    'Private refs lack a completed native read')
            continue
        if proof['common_dir_output'] is not None:
            common = Path(proof['common_dir_output'].removesuffix('\n'))
            common = (common if common.is_absolute() else Path(proof['command_path']) / common).resolve()
            require(str(common) == proof['common_dir'] and common == Path(entry['common_dir']).resolve(),
                    'Private refs belong to a different common repository')
        else:
            require(not refs and proof['status'] == 'partial', 'Unbound private refs')
        if proof['route'] == 'worktree':
            require(proof['command_path'] == tree['path'] and proof['admin_dir'] is None
                    and proof['gitdir_output'] is None, 'Private worktree route differs')
        else:
            require(proof['route'] == 'retained_admin' and proof['command_path'] == entry['path'],
                    'Invalid private admin route')
            admin = proof['admin_dir']
            recorded_admin = tree.get('recovery', {}).get('admin_dir')
            require((recorded_admin is None or admin == recorded_admin)
                    and Path(admin).resolve().parent == (Path(entry['common_dir']) / 'worktrees').resolve(),
                    'Private admin route differs')
            require(isinstance(proof['gitdir_output'], str) and
                    Path(proof['gitdir_output'].removesuffix('\n')).resolve()
                    == (Path(tree['path']) / '.git').resolve(), 'Private admin worktree differs')
        require((proof['returncode'] == 0) or proof['status'] == 'partial', 'Private command failed without qualification')
        for ref in refs:
            name = 'worktree-ref:' + tree['path'] + ':' + ref['name']
            require(name not in result, 'Duplicate private worktree pointer')
            result[name] = ref['hash'] if ref['object_type'] == 'commit' or ref['peeled_type'] != 'commit' else ref['peeled_hash']
    return result


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


def recover_stale_worktree(repo, common, record):
    """Read retained admin data without repairing or pruning a missing worktree."""
    recovery = {'status': 'partial', 'method': 'retained_worktree_admin', 'reflog_heads': [],
                'available_heads': []}
    try:
        for admin in (Path(common) / 'worktrees').iterdir():
            if not admin.is_dir():
                continue
            try:
                gitdir = (admin / 'gitdir').read_text(encoding='utf-8')
            except OSError:
                continue
            if Path(gitdir.removesuffix('\n')).resolve() != (Path(record['path']) / '.git').resolve():
                continue
            recovery.update(admin_dir=str(admin), gitdir=gitdir)
            options = ('--git-dir=' + str(admin),)
            native_common = git(repo, *options, 'rev-parse', '--git-common-dir')
            native_path = Path(native_common.removesuffix('\n'))
            native_path = (native_path if native_path.is_absolute() else Path(repo) / native_path).resolve()
            recovery.update(common_dir_output=native_common, common_dir=str(native_path))
            require(native_path == Path(common).resolve(), 'Retained admin belongs to a different common repository')
            errors = []
            head = None
            try:
                head = git(repo, *options, 'rev-parse', '--verify', 'HEAD').removesuffix('\n')
                require(HASH.fullmatch(head), 'Invalid retained worktree HEAD')
                recovery['head'] = head
            except (OSError, ValueError, subprocess.SubprocessError):
                head = None
                errors.append('head')
            for key, variable in (('configured_author_email', 'user.email'), ('configured_author_name', 'user.name')):
                try:
                    value = git(repo, *options, 'config', '--get', variable).removesuffix('\n')
                    require(value and '\n' not in value, 'Invalid retained worktree identity')
                    recovery[key] = value
                except subprocess.CalledProcessError as error:
                    if error.returncode != 1:
                        errors.append('identity:' + variable)
                    recovery[key] = None
                except (OSError, ValueError, subprocess.SubprocessError):
                    errors.append('identity:' + variable)
                    recovery[key] = None
            args = (*options, 'reflog', 'show', '--no-use-mailmap', '--no-show-signature',
                    '--encoding=UTF-8', '--no-color', '--format=%H', 'HEAD')
            try:
                raw = git(repo, *args)
            except subprocess.CalledProcessError as error:
                raw = error.stdout if isinstance(error.stdout, str) else ''
                errors.append('reflog_command')
            except (OSError, ValueError, subprocess.SubprocessError):
                raw = ''
                errors.append('reflog_command')
            file_path = admin / 'logs' / 'HEAD'
            try:
                file_raw = file_path.read_text(encoding='utf-8')
            except FileNotFoundError:
                file_raw = ''
            except (OSError, ValueError):
                file_raw = ''
                errors.append('reflog_file')
            file_heads = set()
            parse_errors = []
            for number, line in enumerate(file_raw.splitlines(), 1):
                try:
                    before, after, _ = line.split(' ', 2)
                    require(HASH.fullmatch(before) and HASH.fullmatch(after), 'Invalid retained reflog record')
                    file_heads.update(sha for sha in (before, after) if set(sha) != {'0'})
                except ValueError:
                    parse_errors.append({'line': number, 'reason': 'invalid_object_ids_or_record_shape'})
            # `git reflog show` silently omits entries whose objects are gone.
            # The retained native file preserves those identities and old OIDs.
            if any(not HASH.fullmatch(sha) for sha in raw.splitlines()):
                errors.append('reflog_output')
            heads = sorted({sha for sha in raw.splitlines() if HASH.fullmatch(sha)} | file_heads)
            recovery.update(reflog_heads=heads, reflog_output=raw, reflog_command=list(args),
                            reflog_file=file_raw, reflog_file_path=str(file_path), reflog_parse_errors=parse_errors,
                            errors=errors)
            candidates = sorted(set(heads + ([head] if head else []) + ([record['head']] if record.get('head') else [])))
            require(all(HASH.fullmatch(sha) for sha in candidates), 'Invalid registered HEAD')
            checked = git(repo, 'cat-file', '--batch-check=%(objectname) %(objecttype)',
                          stdin='\n'.join(candidates) + '\n').splitlines()
            available = [sha for sha, line in zip(candidates, checked) if line == sha + ' commit']
            recovery['available_heads'] = available
            if not errors and not parse_errors and len(available) == len(candidates) == len(checked):
                recovery['status'] = 'ok'
            return recovery
    except (OSError, RuntimeError, ValueError, subprocess.SubprocessError):
        pass
    return recovery


def collect_local(roots, start, end, warn, api=None, identity=None, author_emails=()):
    identity = identity or {}
    host = os.environ.get('GH_HOST', 'github.com').lower()
    entries, inspected, queue, recoveries = {}, set(), [], []
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
                            recovered = record['recovery'] = recover_stale_worktree(canonical, common, record)
                            if recovered.get('configured_author_email'):
                                entry['identities'].add(recovered['configured_author_email'])
                            if recovered.get('configured_author_name'):
                                configured_names.add(' '.join(recovered['configured_author_name'].casefold().split()))
                            if recovered['status'] != 'ok':
                                warn(f'Registered worktree is missing: {path}; retained Git admin data is incomplete.')
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
    exclusions = []
    visited = set()

    def scan_error(error):
        recovered = inspect_unreadable_tree(error.filename)
        recoveries.append(recovered)
        if recovered['status'] != 'ok':
            warn(f'Cannot scan {error.filename}; local coverage is incomplete.')

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
                parts = target.relative_to(root).parts if target.is_relative_to(root) else target.parts
                dependency_cache = any(target.is_relative_to(dependency_root)
                                       and not root.is_relative_to(dependency_root)
                                       for dependency_root in dependency_roots)
                hidden_state = name in STATE_DIRS or any(part in STATE_DIRS for part in parts)
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
            recovered_tips = {sha for tree in entry['worktrees']
                              for sha in tree.get('recovery', {}).get('available_heads', [])}
            private_snapshot_refs = []
            for tree in entry['worktrees']:
                tree['private_refs'] = capture_private_refs(entry, tree)
                if tree['private_refs']['status'] != 'ok':
                    warn(f'Cannot completely inspect private refs for {tree["path"]}; healthy refs were retained.')
                private_snapshot_refs.extend({**ref, 'name': 'worktree-ref:' + tree['path'] + ':' + ref['name']}
                                             for ref in tree['private_refs']['refs'])
            private_tips = {ref['hash'] if ref['object_type'] == 'commit' else ref['peeled_hash']
                            for ref in private_snapshot_refs
                            if ref['object_type'] == 'commit' or ref['peeled_type'] == 'commit'}
            entry['tips'] = sorted(set(ordinary_tips) | recovered_tips | private_tips)
            require(all(HASH.fullmatch(tip) for tip in entry['tips']), 'Invalid reference tip')
            if recovered_tips:
                revisions = '\n'.join(sorted(recovered_tips)) + '\n' + ''.join('^' + sha + '\n' for sha in ordinary_tips)
                unreferenced = set(git(entry['path'], 'rev-list', '--stdin', stdin=revisions).splitlines())
                for tree in entry['worktrees']:
                    recovery = tree.get('recovery')
                    if recovery is not None:
                        recovery['unreferenced_reflog_heads'] = sorted(
                            unreferenced & set(recovery['reflog_heads']) & set(recovery['available_heads']))
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
            entry['default_ref'] = None
            for candidate in ('refs/remotes/upstream/HEAD', 'refs/remotes/origin/HEAD', 'refs/remotes/origin/main',
                              'refs/remotes/origin/master', 'refs/remotes/origin/devel', 'refs/heads/main', 'refs/heads/master', 'refs/heads/devel'):
                try:
                    sha = git(entry['path'], 'rev-parse', '--verify', candidate + '^{commit}').strip()
                    entry['default_ref'] = {'name': candidate, 'hash': sha, 'source': 'cached_local_ref'}
                    break
                except subprocess.SubprocessError:
                    continue
            recent_records = log_records(entry['path'], entry['tips'], start, end)
            heads = sorted({ref['hash'] if ref['object_type'] == 'commit' else ref['peeled_hash']
                            for ref in entry['refs']
                            if ref['object_type'] == 'commit' or ref['peeled_type'] == 'commit'}
                           | recovered_tips | private_tips | {tree['head'] for tree in entry['worktrees']
                              if tree['detached'] and tree.get('head_available', True)
                              and isinstance(tree['head'], str) and HASH.fullmatch(tree['head'])})
            tip_records[entry['path']] = log_records(entry['path'], heads, no_walk=True)
            records[entry['path']] = recent_records
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
        snapshot_refs = list(entry.get('refs', [])) + [
            {'name': 'worktree:' + tree['path'], 'hash': tree['head'],
             'object_type': 'commit', 'peeled_type': None}
            for tree in entry['worktrees']
            if tree['detached'] and tree.get('head_available', True)
            and isinstance(tree['head'], str) and HASH.fullmatch(tree['head'])]
        snapshot_refs += [
            {'name': 'worktree-reflog:' + tree['path'] + ':' + sha, 'hash': sha,
             'object_type': 'commit', 'peeled_type': None}
            for tree in entry['worktrees']
            for sha in tree.get('recovery', {}).get('unreferenced_reflog_heads', [])]
        snapshot_refs += [{**ref, 'name': 'worktree-ref:' + tree['path'] + ':' + ref['name']}
                          for tree in entry['worktrees'] for ref in tree.get('private_refs', {}).get('refs', [])]
        leads_seen = set()
        for record in records.get(entry['path'], []) + tip_records.get(entry['path'], []):
            if record['author_email'] in unknown and record['author_email'] not in entry['identities']:
                if record['hash'] in leads_seen:
                    continue
                leads_seen.add(record['hash'])
                in_window = start <= datetime.fromisoformat(record['committed_at'].replace('Z', '+00:00')) <= end
                unattributed.append({'repo': entry['path'], **record,
                                     'scope': 'recent_commit' if in_window else 'historical_snapshot',
                                     'refs': [ref['name'] for ref in snapshot_refs
                                              if record['hash'] in (ref['hash'], ref.get('peeled_hash'))],
                                     'reason': 'name_match_only; account attribution unverified'})
        if entry.get('status') != 'ok':
            continue
        try:
            info = {record['hash']: record for record in tip_records.get(entry['path'], [])}
            grouped = {}
            base = entry.get('default_ref')
            for ref in snapshot_refs:
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
                if any(ref['name'].startswith('worktree:') for ref in refs):
                    record['detached'] = True
                    record['snapshot_reason'] = 'detached_worktree_head'
                if proof:
                    record['attribution_commit'] = proof
                branches.append(record)
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, OverflowError):
            warn(f'Cannot classify ref snapshots for {entry["path"]}; branch coverage is incomplete.')
    return {'metadata': {'repos_searched': len(entries), 'repos_skipped': skipped, 'repos_with_commits': len(commits),
                         'scan_roots': [str(root.absolute()) for root in scan_roots], 'scan_exclusions': sorted(set(exclusions)),
                         'repositories': list(entries.values()), 'author_aliases': list(aliases.values()),
                         'local_recoveries': recoveries},
            'commits': commits, 'branches': branches, 'unattributed_commits': unattributed}
