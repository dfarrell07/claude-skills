"""Missing capabilities isolate a source and never fabricate empty successful work."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import Mock, patch

sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / 'scripts'
sys.path.insert(0, str(SCRIPTS))
import capabilities  # noqa: E402
import evidence  # noqa: E402
import local_sources  # noqa: E402


class CapabilitiesTest(unittest.TestCase):
    def test_git_versions_require_supported_native_features(self):
        for text, expected in [('git version 2.37.7\n', 'ok'),
                               ('git version 2.50.1 (Apple Git-155)\n', 'ok'),
                               ('git version 3.0\n', 'ok'), ('git version 2.36.9\n', 'unavailable'),
                               ('unexpected executable output\n', 'unavailable')]:
            with self.subTest(version=text), patch.object(capabilities.shutil, 'which', return_value='/bin/git'), \
                    patch.object(capabilities, 'command', return_value=text) as command:
                self.assertEqual(capabilities.git_capability()['status'], expected)
                command.assert_called_once_with(['git', '--version'], timeout=5)

    def test_failed_git_probe_is_bounded_and_unavailable(self):
        for error in (OSError('unavailable'), subprocess.TimeoutExpired(['git'], 5),
                      subprocess.CalledProcessError(1, ['git'])):
            with self.subTest(error=type(error).__name__), \
                    patch.object(capabilities.shutil, 'which', return_value='/bin/git'), \
                    patch.object(capabilities, 'command', side_effect=error):
                self.assertEqual(capabilities.git_capability()['status'], 'unavailable')

    def test_missing_git_does_not_enter_collector_or_claim_a_search(self):
        collector, warnings = Mock(), []
        with patch.object(capabilities.shutil, 'which', return_value=None):
            local, status, capability = capabilities.collect_local_safely(collector, [], None, None, warnings.append)
        collector.assert_not_called()
        self.assertEqual(status, 'unavailable')
        self.assertEqual(capability['status'], 'unavailable')
        self.assertEqual(local['metadata']['repos_searched'], 0)
        self.assertEqual(local['metadata']['scan_roots'], [])
        self.assertEqual(local['commits'], {})
        self.assertIn('not searched', warnings[0])

    def test_failed_local_source_and_partial_local_source_keep_distinct_status(self):
        for error in (OSError('private host details'), RuntimeError('unresolvable home')):
            with self.subTest(error=type(error).__name__), \
                    patch.object(capabilities, 'git_capability', return_value={'status': 'ok', 'version': '2.37'}):
                warnings = []
                local, status, capability = capabilities.collect_local_safely(
                    Mock(side_effect=error), [], None, None, warnings.append)
                self.assertEqual(status, 'unavailable')
                self.assertEqual(local['branches'], [])
                self.assertNotIn(str(error), warnings[0])
                self.assertEqual(capability['status'], 'unavailable')
        local = capabilities.empty_local()
        warnings = []

        def collect(roots, start, end, warn, **kwargs):
            self.assertEqual(kwargs, {'identity': {}})
            warn('One registered worktree was inaccessible.')
            return local

        with patch.object(capabilities, 'git_capability', return_value={'status': 'ok', 'version': '2.37'}):
            result, status, capability = capabilities.collect_local_safely(
                collect, [], None, None, warnings.append, identity={})
        self.assertIs(result, local)
        self.assertEqual(status, 'partial')
        self.assertEqual(capability['status'], 'ok')
        self.assertEqual(len(warnings), 1)

    def test_native_git_only_capture_works_without_home_environment_variable(self):
        with tempfile.TemporaryDirectory(prefix='work-summary-capabilities.') as directory:
            root = Path(directory).resolve()
            repo = root / 'repo'
            repo.mkdir()
            env = {**os.environ, 'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(root / 'empty-config'),
                   'GIT_TERMINAL_PROMPT': '0', 'GIT_ALLOW_PROTOCOL': '', 'GIT_NO_LAZY_FETCH': '1'}
            for key in ('HOME', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                        'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                        'GIT_CONFIG', 'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'):
                env.pop(key, None)

            def git(*args):
                return subprocess.run(['git', '-C', str(repo), *args], env=env,
                                      check=True, capture_output=True, text=True).stdout.strip()

            git('init', '-q', '--initial-branch=main')
            git('config', 'user.name', 'Fixture Author')
            git('config', 'user.email', 'fixture@example.test')
            git('commit', '--allow-empty', '-qm', 'Concrete local work')
            sha = git('rev-parse', 'HEAD')
            end = datetime.now(timezone.utc).replace(microsecond=0)
            warnings = []
            with patch.dict(os.environ, env, clear=True), patch.dict(evidence.GIT_ENV, env, clear=True):
                local, status, capability = capabilities.collect_local_safely(
                    local_sources.collect_local, [str(repo)], end - timedelta(days=7), end, warnings.append)
            self.assertEqual(status, 'ok', warnings)
            self.assertEqual(capability['status'], 'ok')
            self.assertEqual(local['metadata']['repos_searched'], 1)
            self.assertEqual([row['hash'] for row in local['commits'][str(repo)]], [sha])
            self.assertEqual(local['commits'][str(repo)][0]['author_email'], 'fixture@example.test')
            # HOME plus passwd lookup can be unavailable in an isolated runtime.
            # Explicit roots and cwd must still yield real Git evidence.
            expanduser = Path.expanduser

            def expand(path):
                if str(path).startswith('~'):
                    raise RuntimeError('No home')
                return expanduser(path)

            for roots in ([str(repo)], [], ['~/unavailable', str(repo)]):
                with self.subTest(home_lookup='unavailable', roots=roots), \
                        patch.dict(os.environ, env, clear=True), patch.dict(evidence.GIT_ENV, env, clear=True), \
                        patch.object(local_sources.Path, 'home', side_effect=RuntimeError('No home')), \
                        patch.object(local_sources.Path, 'expanduser', new=expand), \
                        patch.object(local_sources.Path, 'cwd', return_value=repo):
                    warnings = []
                    local, status, capability = capabilities.collect_local_safely(
                        local_sources.collect_local, roots, end - timedelta(days=7), end, warnings.append)
                self.assertEqual(status, 'partial', warnings)
                self.assertEqual(capability['status'], 'ok')
                self.assertEqual(local['metadata']['scan_roots'], [str(repo)])
                self.assertEqual([row['hash'] for row in local['commits'][str(repo)]], [sha])
                self.assertTrue(any('Home directory could not be resolved' in warning for warning in warnings))
            with patch.dict(os.environ, env, clear=True), patch.dict(evidence.GIT_ENV, env, clear=True), \
                    patch.object(local_sources.Path, 'home', return_value=root / 'missing-home'), \
                    patch.object(local_sources.Path, 'cwd', return_value=repo):
                warnings = []
                local, status, _ = capabilities.collect_local_safely(
                    local_sources.collect_local, [], end - timedelta(days=7), end, warnings.append)
            self.assertEqual(status, 'partial', warnings)
            self.assertEqual([row['hash'] for row in local['commits'][str(repo)]], [sha])
            self.assertTrue(any('Default scan root is missing' in warning for warning in warnings))

    def test_shell_entrypoints_missing_or_unusable_python_have_actionable_empty_stdout(self):
        with tempfile.TemporaryDirectory(prefix='work-summary-launcher.') as directory:
            root = Path(directory)
            env = {'PATH': str(root), 'HOME': str(root)}
            for script in ('work-summary.sh', 'render-report.sh'):
                with self.subTest(script=script, capability='missing'):
                    result = subprocess.run(['/bin/bash', str(SCRIPTS / script)], env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 127)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Python 3.10+', result.stderr)
            python = root / 'python3'
            python.write_text('#!/bin/sh\nexit 1\n')
            python.chmod(0o755)
            for script in ('work-summary.sh', 'render-report.sh'):
                with self.subTest(script=script, capability='unusable'):
                    result = subprocess.run(['/bin/bash', str(SCRIPTS / script)], env=env,
                                            capture_output=True, text=True)
                    self.assertEqual(result.returncode, 1)
                    self.assertEqual(result.stdout, '')
                    self.assertIn('Python 3.10+', result.stderr)

    def test_shell_entrypoint_basename_invocation_needs_no_dirname_executable(self):
        with tempfile.TemporaryDirectory(prefix='work-summary-launcher.') as directory:
            root = Path(directory)
            (root / 'python3').symlink_to(shutil.which('python3'))
            result = subprocess.run(['/bin/bash', 'work-summary.sh', 'invalid-days'], cwd=SCRIPTS,
                                    env={**os.environ, 'PATH': str(root), 'PYTHONDONTWRITEBYTECODE': '1'},
                                    capture_output=True, text=True)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, '')
            self.assertIn('Days must be a positive integer', result.stderr)
            self.assertNotIn('dirname', result.stderr)


if __name__ == '__main__':
    unittest.main()
