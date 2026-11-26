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
    def test_nested_remote_projects_preserve_provider_route_without_credentials(self):
        result = local_sources.remote_info('origin', 'https://user:secret@gitlab.example.test/group/team/project.git',
                                           'github.com')
        self.assertEqual(result, {'name': 'origin', 'host': 'gitlab.example.test',
                                  'repo': 'group/team/project', 'kind': 'other'})
        for path in ('group/../project', 'group/./project', 'group//project'):
            self.assertIsNone(local_sources.remote_info('origin', 'git@gitlab.example.test:' + path,
                                                       'github.com')['repo'])

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

    def private_fixture(self):
        repo = self.repo(self.root / 'repository')
        initial = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'commit', '--allow-empty', '-qm', 'Private saved work')
        saved = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'reset', '--hard', initial)
        tree = self.root / 'linked'
        self.git(repo, 'worktree', 'add', '--detach', str(tree), initial)
        self.git(tree, 'update-ref', 'refs/worktree/backup', saved)
        return repo, tree, saved

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

    def test_precise_go_build_cache_exclusion_preserves_backups_and_explicit_roots(self):
        home = self.home
        cache = home / 'go' / '.cache-go-build-container'
        cached_repo = self.repo(cache / 'fixture')
        backup = self.repo(home / '.cache-project-backup' / 'repository')
        (home / 'cache-alias').symlink_to(cache, target_is_directory=True)
        with patch.object(local_sources.Path, 'home', return_value=home):
            data = self.capture(home)
            explicit = self.capture(cache)
        self.assertEqual(set(data['commits']), {str(backup)})
        self.assertIn(str(cache), data['metadata']['scan_exclusions'])
        self.assertIn(str(home / 'cache-alias'), data['metadata']['scan_exclusions'])
        self.assertEqual(set(explicit['commits']), {str(cached_repo)})

    def test_unreadable_directories_owned_by_another_account_are_excluded_not_gaps(self):
        locked = self.root / 'container-volume'
        locked.mkdir()
        locked.chmod(0)
        self.addCleanup(locked.chmod, 0o700)
        if os.access(locked, os.R_OK | os.X_OK):
            self.skipTest('This user bypasses directory permissions')
        warnings = []
        end = datetime.now(timezone.utc)
        owner = locked.stat().st_uid
        with patch.object(local_sources.os, 'getuid', return_value=owner + 1):
            data = local_sources.collect_local([str(self.root)], end - timedelta(days=7), end, warnings.append)
        self.assertIn(str(locked), data['metadata']['scan_exclusions'])
        self.assertEqual(data['metadata']['scan_unreadable'], [])
        self.assertEqual(warnings, [])

    def test_home_application_data_is_pruned_but_nested_library_projects_remain(self):
        home = self.home
        app_data = self.repo(home / 'Library' / 'Application Support' / 'tool')
        project = self.repo(home / 'src' / 'Library')
        with patch.object(local_sources.Path, 'home', return_value=home):
            data = self.capture(home)
        self.assertNotIn(str(app_data), data['commits'])
        self.assertIn(str(project), data['commits'])
        self.assertIn(str(home / 'Library'), data['metadata']['scan_exclusions'])

    def test_branch_whose_tip_is_someone_elses_is_not_a_lead_but_its_own_commits_count(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'checkout', '-qb', 'feature')
        self.git(repo, 'commit', '--allow-empty', '-qm', 'Own feature')
        own = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, '-c', 'user.name=Other Author', '-c', 'user.email=other@example.test',
                 'commit', '--allow-empty', '-qm', 'Other followup')
        foreign = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'update-ref', 'refs/codex/backup', foreign)
        data = self.capture(repo)
        self.assertIn(own, {commit['hash'] for commit in data['commits'][str(repo)]})
        self.assertNotIn(foreign, {commit['hash'] for commit in data['commits'][str(repo)]})

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
        self.assertNotIn(historical, {commit['hash'] for commit in data['commits'][str(repo)]})

    def test_distinct_push_fork_and_fetch_upstream_are_both_retained(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'remote', 'add', 'origin', 'https://github.com/upstream/repository.git')
        self.git(repo, 'remote', 'set-url', '--push', 'origin', 'git@github.com:own-user/repository.git')
        data = self.capture(repo)
        remotes = {remote['repo']: remote for remote in data['metadata']['repositories'][0]['remotes']}
        self.assertEqual(set(remotes), {'upstream/repository', 'own-user/repository'})
        self.assertEqual(remotes['upstream/repository']['directions'], ['fetch'])
        self.assertEqual(remotes['own-user/repository']['directions'], ['push'])

    def test_old_unverified_backup_alias_is_not_credited(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'checkout', '-qb', 'backup/historical')
        with patch.dict(self.env, {'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z',
                                  'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}):
            self.git(repo, '-c', 'user.email=unverified@example.test', 'commit',
                     '--allow-empty', '-qm', 'Unverified historical backup')
        historical = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'checkout', '-q', 'main')
        data = self.capture(repo)
        self.assertNotIn(historical, {commit['hash'] for commit in data['commits'][str(repo)]})

    def test_detached_head_commits_count_without_creating_refs(self):
        repo = self.repo(self.root / 'repository')
        worktree = self.root / 'detached-worktree'
        self.git(repo, 'worktree', 'add', '--detach', str(worktree))
        self.git(worktree, 'commit', '--allow-empty', '-qm', 'Detached work')
        detached = self.git(worktree, 'rev-parse', 'HEAD')
        refs_before = self.git(repo, 'for-each-ref', '--format=%(refname):%(objectname)')
        data = self.capture(repo)
        self.assertEqual(self.git(repo, 'for-each-ref', '--format=%(refname):%(objectname)'), refs_before)
        self.assertIn(detached, {commit['hash'] for commit in data['commits'][str(repo)]})

    def test_mailmap_cannot_change_attribution(self):
        repo = self.repo(self.root / 'repository')
        (repo / '.mailmap').write_text('Other Author <other@example.test> <own@example.test>\n')
        data = self.capture(repo)
        commits = data['commits'][str(repo)]
        self.assertEqual({commit['author_email'] for commit in commits}, {'own@example.test'})

    def test_commit_window_bounds_on_branch_tips(self):
        repo = self.repo(self.root / 'repository')
        for name, when in (('at-start', '2026-01-08T12:00:00Z'), ('future', '2026-01-15T12:00:01Z')):
            self.git(repo, 'checkout', '-qb', name, 'main')
            with patch.dict(self.env, {'GIT_AUTHOR_DATE': when, 'GIT_COMMITTER_DATE': when}):
                self.git(repo, 'commit', '--allow-empty', '-qm', name)
        self.git(repo, 'checkout', '-q', 'main')
        at_start = self.git(repo, 'rev-parse', 'at-start')
        self.git(repo, 'update-ref', 'refs/heads/also-at-start', at_start)
        start = datetime(2026, 1, 8, 12, tzinfo=timezone.utc)
        data = local_sources.collect_local([str(repo)], start, start + timedelta(days=7), lambda message: None)
        future = self.git(repo, 'rev-parse', 'future')
        hashes = [commit['hash'] for commit in data['commits'][str(repo)]]
        self.assertEqual(hashes.count(at_start), 1)
        self.assertNotIn(future, hashes)

    def test_regular_walk_directories_reuse_canonical_parent(self):
        root = self.root / 'workspace'
        repo = self.repo(root / 'one' / 'two' / 'three' / 'repository')
        original = local_sources.Path.resolve
        calls = []
        def resolve(path, *args, **kwargs):
            calls.append(path)
            return original(path, *args, **kwargs)
        with patch.object(local_sources.Path, 'resolve', resolve):
            data = self.capture(root)
        self.assertEqual(set(data['commits']), {str(repo)})
        self.assertNotIn(root / 'one', calls)
        self.assertNotIn(root / 'one' / 'two', calls)
        self.assertNotIn(root / 'one' / 'two' / 'three', calls)


if __name__ == '__main__':
    unittest.main()
