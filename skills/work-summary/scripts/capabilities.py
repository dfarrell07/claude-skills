"""Read-only local capability checks; unavailable sources must not block others."""

import re
import json
from pathlib import Path
import shutil
import subprocess
import sys

from evidence import command


NAMESPACE_INVENTORY = r'''
import json, os, stat, sys
path = sys.argv[1]
entries, candidates, errors, links = [], [], [], []
truncated = False
def record(path):
    value = os.lstat(path)
    kind = 'directory' if stat.S_ISDIR(value.st_mode) else 'symlink' if stat.S_ISLNK(value.st_mode) else 'other'
    entries.append({'path': path, 'type': kind, 'mode': stat.S_IMODE(value.st_mode),
                    'device': value.st_dev, 'inode': value.st_ino})
    if kind == 'symlink': links.append(path)
record(path)
for current, dirs, files in os.walk(path, followlinks=False, onerror=lambda error: errors.append(error.filename)):
    if '.git' in dirs or '.git' in files or ('HEAD' in files and {'objects', 'refs'} <= set(dirs)):
        candidates.append(current)
    for name in dirs + files:
        if len(entries) >= 10000:
            truncated = True
            break
        try: record(os.path.join(current, name))
        except OSError: errors.append(os.path.join(current, name))
    if truncated: break
print(json.dumps({'entries': entries, 'repository_candidates': candidates, 'symlinks': links,
                  'errors': errors, 'truncated': truncated,
                  'complete': not errors and not links and not truncated}))
'''


def inspect_unreadable_tree(path):
    """Inspect metadata in an optional Linux namespace without changing permissions.

    A successful complete marker-free walk can close a repository-discovery gap.
    Symlinks, repository markers, errors and bounded/truncated walks stay unknown.
    No file contents are read, no repository is modified and no root login occurs.
    """
    result = {'path': str(path), 'method': 'linux_user_namespace_metadata', 'status': 'unavailable'}
    executable = shutil.which('unshare') if sys.platform.startswith('linux') else None
    if not executable:
        return {**result, 'reason': 'Read-only user-namespace inspection is unavailable on this platform.'}
    try:
        before = Path(path).stat()
        raw = command([executable, '--user', '--map-root-user', '--', sys.executable,
                       '-I', '-B', '-c', NAMESPACE_INVENTORY, str(path)], timeout=10)
        evidence = json.loads(raw)
        after = Path(path).stat()
        root = evidence['entries'][0]
        stable = (before.st_dev, before.st_ino, before.st_mode) == (after.st_dev, after.st_ino, after.st_mode)
        stable = stable and (root['device'], root['inode']) == (before.st_dev, before.st_ino)
        complete = (evidence['complete'] is True and stable and not evidence['repository_candidates']
                    and not evidence['symlinks'] and not evidence['errors'] and evidence['truncated'] is False)
        return {**result, **evidence, 'status': 'ok' if complete else 'partial'}
    except (OSError, UnicodeError, ValueError, KeyError, IndexError, TypeError, subprocess.SubprocessError):
        return {**result, 'reason': 'Read-only namespace inspection failed; permissions were left unchanged.'}


def git_capability():
    """Check the Git features this collector needs without opening a repository."""
    if not shutil.which('git'):
        return {'status': 'unavailable', 'reason': 'Git is not installed or is not on PATH.'}
    try:
        raw = command(['git', '--version'], timeout=5)
        match = re.fullmatch(r'git version (\d+)\.(\d+)(?:\.(\d+))?[^\r\n]*\n?', raw)
        if not match:
            return {'status': 'unavailable', 'reason': 'Git version could not be established.'}
        version = '.'.join(part for part in match.groups() if part is not None)
        if tuple(map(int, match.groups()[:2])) < (2, 37):
            return {'status': 'unavailable', 'version': version,
                    'reason': 'Local collection needs Git 2.37 or newer.'}
        return {'status': 'ok', 'version': version}
    except (OSError, UnicodeError, subprocess.SubprocessError):
        return {'status': 'unavailable', 'reason': 'Git could not be run or its version check failed.'}


def empty_local():
    """An unavailable inventory contains no claims about repositories searched."""
    return {'metadata': {'repos_searched': 0, 'repos_skipped': 0, 'repos_with_commits': 0,
                         'scan_roots': [], 'scan_exclusions': [], 'repositories': [], 'author_aliases': []},
            'commits': {}, 'branches': [], 'unattributed_commits': []}


def collect_local_safely(collector, roots, start, end, warn, **kwargs):
    """Return local evidence, source status and capability without blocking remote reads."""
    capability = git_capability()
    if capability['status'] != 'ok':
        warn('Local Git unavailable: ' + capability['reason'] + ' Local work was not searched.')
        return empty_local(), 'unavailable', capability
    warnings = []

    def local_warning(message):
        warnings.append(message)
        warn(message)

    try:
        local = collector(roots, start, end, local_warning, **kwargs)
    except (OSError, RuntimeError, ValueError, TypeError, KeyError, OverflowError, subprocess.SubprocessError):
        capability = {**capability, 'status': 'unavailable',
                      'reason': 'Local repository collection failed; its inventory is unavailable.'}
        warn('Local Git unavailable: ' + capability['reason'] + ' Other sources remain usable.')
        return empty_local(), 'unavailable', capability
    return local, 'partial' if warnings else 'ok', capability
