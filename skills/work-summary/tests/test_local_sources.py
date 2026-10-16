"""Local-source regressions using disposable repositories and no network."""

from datetime import datetime, timedelta, timezone
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import evidence  # noqa: E402
import local_sources  # noqa: E402


class LocalSourcesTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='work-summary-local-test.')
        self.addCleanup(temporary.cleanup)
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.env = {**os.environ, 'HOME': str(self.home), 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_CONFIG_GLOBAL': str(self.home / '.gitconfig')}
        for key in ('GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                    'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES',
                    'GIT_CONFIG_PARAMETERS', 'GIT_CONFIG_COUNT'):
            self.env.pop(key, None)
        environment = patch.dict(evidence.GIT_ENV, {**self.env, 'GIT_TERMINAL_PROMPT': '0',
                                 'GIT_NO_LAZY_FETCH': '1', 'GIT_ALLOW_PROTOCOL': ''}, clear=True)
        environment.start()
        self.addCleanup(environment.stop)

    def git(self, repo, *args):
        return subprocess.run(['git', '--no-optional-locks', '-C', str(repo), *args],
                              capture_output=True, text=True, check=True, env=self.env).stdout.strip()

    def repo(self, path):
        path.mkdir(parents=True)
        self.git(path, 'init', '-q', '--initial-branch=main')
        self.git(path, 'config', 'user.name', 'Own Author')
        self.git(path, 'config', 'user.email', 'own@example.test')
        self.git(path, 'commit', '--allow-empty', '-qm', 'Initial')
        return path

    def capture(self, root):
        end = datetime.now(timezone.utc).replace(microsecond=0)
        return local_sources.collect_local([str(root)], end - timedelta(days=7), end, lambda message: None)

    def test_symlink_cannot_bypass_state_directory_exclusions(self):
        home = self.root / 'home'
        for name in ('.ssh', '.aws', '.docker'):
            self.repo(home / name / 'private-repository')
            (home / ('workspace-' + name[1:])).symlink_to(home / name, target_is_directory=True)
        with patch.object(local_sources.Path, 'home', return_value=home):
            data = self.capture(home)
        self.assertEqual(data['metadata']['repositories'], [])
        for name in ('ssh', 'aws', 'docker'):
            self.assertIn(str(home / ('workspace-' + name)), data['metadata']['scan_exclusions'])

    def test_unowned_remote_refs_do_not_walk_ancestry(self):
        repo = self.repo(self.root / 'repository')
        initial = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'update-ref', 'refs/remotes/origin/main', initial)
        self.git(repo, 'checkout', '-qb', 'temporary')
        self.git(repo, '-c', 'user.name=Other Author', '-c', 'user.email=other@example.test',
                 'commit', '--allow-empty', '-qm', 'Other work')
        foreign = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'checkout', '-q', 'main')
        self.git(repo, 'branch', '-D', 'temporary')
        for index in range(25):
            self.git(repo, 'update-ref', f'refs/remotes/origin/topic-{index}', foreign)
        with patch.object(local_sources, 'git', wraps=local_sources.git) as calls:
            data = self.capture(repo)
        self.assertFalse(any(call.args[1:4] == ('merge-base', '--is-ancestor', foreign)
                             for call in calls.call_args_list))
        self.assertFalse(any(branch['hash'] == foreign for branch in data['branches']))

    def test_foreign_tip_retains_own_history_proof_and_backup_ref(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'update-ref', 'refs/remotes/origin/main', self.git(repo, 'rev-parse', 'HEAD'))
        self.git(repo, 'checkout', '-qb', 'feature')
        self.git(repo, 'commit', '--allow-empty', '-qm', 'Own feature')
        own = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, '-c', 'user.name=Other Author', '-c', 'user.email=other@example.test',
                 'commit', '--allow-empty', '-qm', 'Other followup')
        foreign = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'update-ref', 'refs/codex/backup', foreign)
        with patch.object(local_sources, 'git', wraps=local_sources.git) as calls:
            data = self.capture(repo)
        branch = next(branch for branch in data['branches'] if branch['hash'] == foreign)
        self.assertEqual(branch['attribution'], 'contains_configured_or_verified_author')
        self.assertEqual(branch['attribution_commit']['hash'], own)
        self.assertEqual(branch['attribution_commit']['author_email'], 'own@example.test')
        self.assertEqual(set(branch['refs']), {'refs/heads/feature', 'refs/codex/backup'})
        ancestry_calls = [call for call in calls.call_args_list
                          if call.args[1:4] == ('merge-base', '--is-ancestor', foreign)]
        self.assertEqual(len(ancestry_calls), 1)

    def test_alias_only_on_older_backup_is_independently_verified(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'checkout', '-qb', 'backup/historical')
        with patch.dict(self.env, {'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z',
                                  'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}):
            self.git(repo, '-c', 'user.email=historical@example.test', 'commit',
                     '--allow-empty', '-qm', 'Historical backup')
        historical = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'checkout', '-q', 'main')
        def api(*args):
            return {'items': [{'author': {'login': 'own-user'},
                               'commit': {'author': {'email': 'historical@example.test'}},
                               'sha': historical, 'html_url': 'https://github.com/own-user/repository/commit/' + historical}]}
        end = datetime.now(timezone.utc).replace(microsecond=0)
        data = local_sources.collect_local([str(repo)], end - timedelta(days=7), end,
                                          lambda message: None, api, {'login': 'own-user'})
        alias = next((alias for alias in data['metadata']['author_aliases']
                      if alias['email'] == 'historical@example.test'), None)
        self.assertIsNotNone(alias)
        self.assertEqual(alias['source'], 'github_commit_account')
        backup = next(branch for branch in data['branches'] if branch['hash'] == historical)
        self.assertFalse(backup['recent_tip'])
        self.assertNotIn(historical, {commit['hash'] for commit in data['commits'][str(repo)]})


if __name__ == '__main__':
    unittest.main()
