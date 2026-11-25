"""Recover retained committed work and inspect owned metadata without mutation."""

from datetime import datetime, timedelta, timezone
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

    def stale(self, ref_format=None):
        repo, tree = self.root / 'repo', self.root / 'gone'
        repo.mkdir()
        self.git(repo, 'init', '-q', '--initial-branch=main', *(['--ref-format=' + ref_format] if ref_format else []))
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

    def capture(self, repo, **kwargs):
        end = datetime.now(timezone.utc).replace(microsecond=0)
        warnings = []
        data = local_sources.collect_local([str(repo)], end - timedelta(days=7), end, warnings.append, **kwargs)
        return data, warnings

    def test_stale_admin_recovers_reflog_work_and_leaves_registration_unchanged(self):
        repo, tree, former = self.stale()
        admin = repo / '.git' / 'worktrees' / 'gone'
        before = {name: (admin / name).read_bytes() for name in ('HEAD', 'gitdir', 'logs/HEAD')}
        data, warnings = self.capture(repo)
        self.assertEqual(warnings, [])
        self.assertFalse(tree.exists())
        record = next(row for row in data['metadata']['repositories'][0]['worktrees'] if row['path'] == str(tree))
        self.assertIn(former, record['recovered_heads'])
        self.assertIn(former, data['metadata']['repositories'][0]['tips'])
        self.assertIn(former, {row['hash'] for row in data['commits'][str(repo)]})
        self.assertEqual(before, {name: (admin / name).read_bytes() for name in before})

    def test_missing_reflog_object_retains_available_data_and_a_genuine_gap(self):
        repo, tree, former = self.stale()
        (repo / '.git' / 'objects' / former[:2] / former[2:]).unlink()
        data, warnings = self.capture(repo)
        record = next(row for row in data['metadata']['repositories'][0]['worktrees']
                      if row['path'] == str(tree))
        self.assertNotIn(former, record['recovered_heads'])
        self.assertTrue(record['recovered_heads'])
        self.assertEqual(data['metadata']['repositories'][0]['status'], 'ok')
        self.assertTrue(any('retained history is unavailable' in warning for warning in warnings))

    def test_missing_stale_head_does_not_drop_healthy_reflog_or_shared_history(self):
        repo, tree, former = self.stale()
        admin = repo / '.git' / 'worktrees' / 'gone'
        (admin / 'HEAD').write_text('f' * 40 + '\n')
        data, warnings = self.capture(repo)
        entry = data['metadata']['repositories'][0]
        record = next(row for row in entry['worktrees'] if row['path'] == str(tree))
        self.assertEqual(entry['status'], 'ok')
        self.assertIn(former, record['recovered_heads'])
        self.assertTrue(any(row['hash'] == former for row in data['commits'][str(repo)]))
        self.assertTrue(any('HEADs are unavailable' in warning for warning in warnings))

    def test_malformed_reflog_lines_are_skipped_and_valid_rows_kept(self):
        repo, tree, former = self.stale()
        log = repo / '.git' / 'worktrees' / 'gone' / 'logs' / 'HEAD'
        with log.open('a') as stream:
            stream.write('malformed native tail\n')
        data, warnings = self.capture(repo)
        record = next(row for row in data['metadata']['repositories'][0]['worktrees'] if row['path'] == str(tree))
        self.assertIn(former, record['recovered_heads'])
        self.assertTrue(any(row['hash'] == former for row in data['commits'][str(repo)]))
        self.assertEqual(warnings, [])

    def test_missing_reflog_is_a_gap(self):
        repo, tree, _ = self.stale()
        (repo / '.git' / 'worktrees' / 'gone' / 'logs' / 'HEAD').unlink()
        data, warnings = self.capture(repo)
        record = next(row for row in data['metadata']['repositories'][0]['worktrees'] if row['path'] == str(tree))
        self.assertEqual(record['recovered_heads'], [])
        self.assertTrue(any('retained history is unavailable' in warning for warning in warnings))

    def test_foreign_common_directory_adds_no_identity(self):
        repo, tree, former = self.stale()
        foreign = self.root / 'foreign'
        shutil.copytree(repo, foreign)
        self.git(foreign, 'config', 'user.email', 'foreign@example.test')
        admin = repo / '.git' / 'worktrees' / 'gone'
        (admin / 'commondir').write_text(str(foreign / '.git') + '\n')
        data, _ = self.capture(repo)
        entry = data['metadata']['repositories'][0]
        self.assertNotIn('foreign@example.test', entry['identities'])
        record = next(row for row in entry['worktrees'] if row['path'] == str(tree))
        self.assertIn(former, record['recovered_heads'])  # read from this repository's own objects

    def admin(self, repo):
        return repo / '.git' / 'worktrees' / 'gone'

    def recovered(self, data, tree):
        return next(row for row in data['metadata']['repositories'][0]['worktrees']
                    if row['path'] == str(tree))['recovered_heads']

    def reflog_line(self, old, new):
        return f'{old} {new} Fixture Author <fixture@example.test> 1700000000 +0000\tentry\n'

    def test_reftable_stale_worktree_reflog_is_read_through_git(self):
        try:
            repo, tree, former = self.stale('reftable')
        except subprocess.CalledProcessError:
            self.skipTest('Git cannot create reftable repositories')
        self.assertFalse((self.admin(repo) / 'logs' / 'HEAD').exists())
        data, warnings = self.capture(repo)
        self.assertIn(former, self.recovered(data, tree))
        self.assertIn(former, {row['hash'] for row in data['commits'][str(repo)]})
        self.assertEqual(warnings, [])

    def test_failed_reftable_reflog_read_is_a_gap(self):
        try:
            repo, tree, former = self.stale('reftable')
        except subprocess.CalledProcessError:
            self.skipTest('Git cannot create reftable repositories')
        real = local_sources.git

        def git(path, *args, **kwargs):
            if 'reflog' in args:
                raise subprocess.CalledProcessError(1, 'git reflog')
            return real(path, *args, **kwargs)
        with patch.object(local_sources, 'git', git):
            _, warnings = self.capture(repo)
        self.assertTrue(warnings)

    def test_lost_commit_named_only_as_old_or_only_as_new_hash_is_recovered(self):
        repo, tree, former = self.stale()
        initial = self.git(repo, 'rev-parse', 'main')
        for line in (self.reflog_line(former, initial), self.reflog_line('0' * 40, former)):
            with self.subTest(line=line):
                (self.admin(repo) / 'logs' / 'HEAD').write_text(line)
                data, _ = self.capture(repo)
                self.assertIn(former, self.recovered(data, tree))
                self.assertIn(former, {row['hash'] for row in data['commits'][str(repo)]})

    def test_admin_head_missing_from_a_truncated_reflog_is_recovered(self):
        repo, tree, former = self.stale()
        initial = self.git(repo, 'rev-parse', 'main')
        (self.admin(repo) / 'HEAD').write_text(former + '\n')
        (self.admin(repo) / 'logs' / 'HEAD').write_text(self.reflog_line('0' * 40, initial))
        data, warnings = self.capture(repo)
        self.assertEqual(sorted(self.recovered(data, tree)), sorted({former, initial}))
        self.assertEqual(warnings, [])

    def test_empty_reflog_keeps_the_registered_head(self):
        repo, tree, _ = self.stale()
        initial = self.git(repo, 'rev-parse', 'main')
        (self.admin(repo) / 'logs' / 'HEAD').write_text('')
        data, warnings = self.capture(repo)
        self.assertEqual(self.recovered(data, tree), [initial])
        self.assertEqual(warnings, [])

    def test_stale_worktree_recovers_only_its_own_reflog(self):
        repo, tree, former = self.stale()
        live = self.root / 'live'
        self.git(repo, 'worktree', 'add', '--detach', str(live))
        self.git(live, 'commit', '--allow-empty', '-qm', 'Live work')
        unique = self.git(live, 'rev-parse', 'HEAD')
        data, _ = self.capture(repo)
        self.assertIn(former, self.recovered(data, tree))
        self.assertNotIn(unique, self.recovered(data, tree))
        live_record = next(row for row in data['metadata']['repositories'][0]['worktrees'] if row['path'] == str(live))
        self.assertNotIn('recovered_heads', live_record)
        self.assertIn(unique, {row['hash'] for row in data['commits'][str(repo)]})

    def test_failed_ref_listing_keeps_a_live_detached_worktree_commit(self):
        repo, tree, former = self.stale()
        live = self.root / 'live'
        self.git(repo, 'worktree', 'add', '--detach', str(live))
        self.git(live, 'commit', '--allow-empty', '-qm', 'Live detached work')
        unique = self.git(live, 'rev-parse', 'HEAD')
        (self.admin(repo) / 'HEAD').write_text('f' * 40 + '\n')
        data, warnings = self.capture(repo)
        self.assertTrue(any('HEADs are unavailable' in warning for warning in warnings))
        hashes = {row['hash'] for row in data['commits'][str(repo)]}
        self.assertIn(unique, hashes)
        self.assertIn(former, hashes)

    def test_configless_bare_repository_is_discovered(self):
        repo, _, former = self.stale()
        bare = self.root / 'bare-without-config'
        shutil.copytree(repo / '.git', bare)
        shutil.rmtree(bare / 'worktrees')
        (bare / 'config').unlink()
        self.assertEqual(self.git(bare, 'rev-parse', '--is-bare-repository'), 'true')
        data, _ = self.capture(self.root, author_emails=('fixture@example.test',))
        entry = next(row for row in data['metadata']['repositories'] if row['path'] == str(bare))
        self.assertTrue(entry['bare'])
        self.assertEqual(entry['status'], 'ok')


class SafeGitTest(unittest.TestCase):
    def test_git_runs_without_prompts_lazy_fetches_protocols_or_inherited_repository(self):
        self.assertEqual({key: evidence.GIT_ENV[key] for key in ('GIT_TERMINAL_PROMPT', 'GIT_NO_LAZY_FETCH',
                                                                  'GIT_ALLOW_PROTOCOL')},
                         {'GIT_TERMINAL_PROMPT': '0', 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''})
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE', 'GIT_OBJECT_DIRECTORY',
                    'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            self.assertNotIn(key, evidence.GIT_ENV)
        with patch.object(evidence, 'command', return_value='') as command:
            evidence.git('/some/repo', 'status')
        self.assertEqual(command.call_args.args[0], ['git', '--no-optional-locks', '-C', '/some/repo', 'status'])
        self.assertIs(command.call_args.kwargs['env'], evidence.GIT_ENV)

if __name__ == '__main__':
    unittest.main()
