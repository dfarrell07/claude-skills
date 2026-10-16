"""Recover retained committed work and inspect owned metadata without mutation."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import capabilities  # noqa: E402
import evidence  # noqa: E402
import local_sources  # noqa: E402


class LocalRecoveryTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='work-summary-recovery-test.')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.env = {**os.environ, 'HOME': str(self.root), 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': str(self.root / 'empty-config')}
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'):
            self.env.pop(key, None)
        environment = patch.dict(evidence.GIT_ENV, {**self.env, 'GIT_TERMINAL_PROMPT': '0',
                                 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_LAZY_FETCH': '1'}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, repo, *args):
        return subprocess.run(['git', '--no-optional-locks', '-C', str(repo), *args],
                              env=self.env, check=True, capture_output=True, text=True).stdout.strip()

    def stale(self):
        repo, tree = self.root / 'repo', self.root / 'gone'
        repo.mkdir()
        self.git(repo, 'init', '-q', '--initial-branch=main')
        self.git(repo, 'config', 'user.name', 'Fixture Author')
        self.git(repo, 'config', 'user.email', 'fixture@example.test')
        self.git(repo, 'commit', '--allow-empty', '-qm', 'Shared history')
        initial = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'worktree', 'add', '--detach', str(tree), initial)
        self.git(tree, 'commit', '--allow-empty', '-qm', 'Retained former work')
        former = self.git(tree, 'rev-parse', 'HEAD')
        self.git(tree, 'reset', '--hard', initial)
        shutil.rmtree(tree)
        return repo, tree, former

    def capture(self, repo):
        end = datetime.now(timezone.utc).replace(microsecond=0)
        warnings = []
        data = local_sources.collect_local([str(repo)], end - timedelta(days=7), end, warnings.append)
        return data, warnings

    def test_stale_admin_recovers_reflog_work_and_leaves_registration_unchanged(self):
        repo, tree, former = self.stale()
        admin = repo / '.git' / 'worktrees' / 'gone'
        before = {name: (admin / name).read_bytes() for name in ('HEAD', 'gitdir', 'logs/HEAD')}
        data, warnings = self.capture(repo)
        self.assertEqual(warnings, [])
        self.assertFalse(tree.exists())
        recovery = next(row for row in data['metadata']['repositories'][0]['worktrees']
                        if row['path'] == str(tree))['recovery']
        self.assertEqual(recovery['status'], 'ok')
        self.assertIn(former, recovery['reflog_heads'])
        self.assertEqual(set(recovery['reflog_output'].splitlines()), set(recovery['reflog_heads']))
        self.assertEqual(recovery['configured_author_email'], 'fixture@example.test')
        self.assertIn(former, data['metadata']['repositories'][0]['tips'])
        snapshot = next(row for row in data['branches'] if row['hash'] == former)
        self.assertIn('worktree-reflog:' + str(tree) + ':' + former, snapshot['refs'])
        self.assertEqual(snapshot['author_email'], 'fixture@example.test')
        self.assertEqual(before, {name: (admin / name).read_bytes() for name in before})

    def test_missing_reflog_object_retains_available_data_and_a_genuine_gap(self):
        repo, tree, former = self.stale()
        (repo / '.git' / 'objects' / former[:2] / former[2:]).unlink()
        data, warnings = self.capture(repo)
        record = next(row for row in data['metadata']['repositories'][0]['worktrees']
                      if row['path'] == str(tree))
        self.assertEqual(record['recovery']['status'], 'partial')
        self.assertIn(former, record['recovery']['reflog_heads'])
        self.assertNotIn(former, record['recovery']['available_heads'])
        self.assertEqual(data['metadata']['repositories'][0]['status'], 'ok')
        self.assertTrue(any('admin data is incomplete' in warning for warning in warnings))

    def test_namespace_probe_is_optional_and_rejects_incomplete_or_unsafe_walks(self):
        directory = self.root / 'directory'
        directory.mkdir()
        value = directory.stat()
        payload = {'entries': [{'device': value.st_dev, 'inode': value.st_ino}],
                   'repository_candidates': [], 'symlinks': [], 'errors': [], 'truncated': False, 'complete': True}
        with patch.object(capabilities.sys, 'platform', 'darwin'), \
                patch.object(capabilities, 'command') as command:
            self.assertEqual(capabilities.inspect_unreadable_tree(directory)['status'], 'unavailable')
            command.assert_not_called()
        for field in (None, 'repository_candidates', 'symlinks', 'errors', 'truncated'):
            evidence = {**payload, **({field: True if field == 'truncated' else ['unknown']} if field else {})}
            with self.subTest(field=field), patch.object(capabilities.sys, 'platform', 'linux'), \
                    patch.object(capabilities.shutil, 'which', return_value='/usr/bin/unshare'), \
                    patch.object(capabilities, 'command', return_value=json.dumps(evidence)) as command:
                result = capabilities.inspect_unreadable_tree(directory)
                self.assertEqual(result['status'], 'ok' if field is None else 'partial')
                self.assertEqual(command.call_args.kwargs['timeout'], 10)

    def test_native_namespace_metadata_recovery_preserves_permissions_and_detects_git(self):
        directory = self.root / 'fixture'
        directory.mkdir()
        (directory / 'generated').mkdir()
        directory.chmod(0)
        try:
            recovery = capabilities.inspect_unreadable_tree(directory)
            if recovery['status'] == 'unavailable':
                self.skipTest('Unprivileged user namespaces are unavailable in this environment')
            self.assertEqual(recovery['status'], 'ok', recovery)
            self.assertEqual(directory.stat().st_mode & 0o777, 0)
            self.assertEqual(len(recovery['entries']), 2)
            data, warnings = self.capture(self.root)
            self.assertEqual(warnings, [])
            self.assertEqual(data['metadata']['local_recoveries'][0]['status'], 'ok')
        finally:
            directory.chmod(0o700)
        (directory / '.git').mkdir()
        directory.chmod(0)
        try:
            recovery = capabilities.inspect_unreadable_tree(directory)
            self.assertEqual(recovery['status'], 'partial')
            self.assertEqual(recovery['repository_candidates'], [str(directory)])
            self.assertEqual(directory.stat().st_mode & 0o777, 0)
            data, warnings = self.capture(self.root)
            self.assertTrue(any('Cannot scan' in warning for warning in warnings))
            self.assertEqual(data['metadata']['local_recoveries'][0]['status'], 'partial')
        finally:
            directory.chmod(0o700)


if __name__ == '__main__':
    unittest.main()
