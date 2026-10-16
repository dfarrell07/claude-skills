"""Read-only local capability checks; unavailable sources must not block others."""

import re
import shutil
import subprocess

from evidence import command


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
