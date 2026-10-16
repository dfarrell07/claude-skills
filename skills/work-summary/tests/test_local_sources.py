"""Local-source regressions using disposable repositories and no network."""

from datetime import datetime, timedelta, timezone
import copy
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

    def test_private_worktree_ref_adds_weekly_and_historical_evidence_without_mutation(self):
        repo, tree, saved = self.private_fixture()
        before = self.git(tree, 'for-each-ref')
        data = self.capture(repo)
        entry = data['metadata']['repositories'][0]
        pointer = 'worktree-ref:' + str(tree) + ':refs/worktree/backup'
        self.assertIn(saved, [row['hash'] for row in data['commits'][str(repo)]])
        self.assertIn(pointer, next(row for row in data['branches'] if row['hash'] == saved)['refs'])
        self.assertEqual(local_sources.private_ref_pointers(entry)[pointer], saved)
        end = datetime.now(timezone.utc) + timedelta(days=30)
        old = local_sources.collect_local([str(repo)], end - timedelta(days=7), end, lambda message: None)
        self.assertNotIn(str(repo), old['commits'])
        self.assertIn(pointer, next(row for row in old['branches'] if row['hash'] == saved)['refs'])
        self.assertEqual(before, self.git(tree, 'for-each-ref'))

    def test_private_refs_same_name_different_worktrees_and_stale_admin(self):
        repo, tree, saved = self.private_fixture()
        second = self.root / 'other'
        self.git(repo, 'worktree', 'add', '--detach', str(second), 'main')
        self.git(second, 'commit', '--allow-empty', '-qm', 'Other private work')
        other = self.git(second, 'rev-parse', 'HEAD')
        self.git(second, 'update-ref', 'refs/worktree/backup', other)
        shutil.rmtree(tree)
        data = self.capture(repo)
        entry = data['metadata']['repositories'][0]
        pointers = local_sources.private_ref_pointers(entry)
        self.assertEqual(pointers['worktree-ref:' + str(tree) + ':refs/worktree/backup'], saved)
        self.assertEqual(pointers['worktree-ref:' + str(second) + ':refs/worktree/backup'], other)
        self.assertFalse(tree.exists())

    def test_private_ref_proof_rejects_forged_rows_route_and_common_binding(self):
        repo, tree, _ = self.private_fixture()
        entry = self.capture(repo)['metadata']['repositories'][0]
        for field, value in (('common_dir', '/foreign'), ('command_path', str(repo)),
                             ('native_output', ''), ('refs', [])):
            forged = copy.deepcopy(entry)
            proof = next(row for row in forged['worktrees'] if row['path'] == str(tree))['private_refs']
            proof[field] = value
            with self.subTest(field=field), self.assertRaises(ValueError):
                local_sources.private_ref_pointers(forged)

    def test_private_ref_malformed_native_tail_retains_healthy_positive(self):
        repo, tree, saved = self.private_fixture()
        original = local_sources.git

        def malformed(path, *args, **kwargs):
            raw = original(path, *args, **kwargs)
            if str(path) == str(tree) and 'for-each-ref' in args:
                raw += 'malformed tail\n'
            return raw

        with patch.object(local_sources, 'git', side_effect=malformed):
            entry = self.capture(repo)['metadata']['repositories'][0]
        proof = next(row for row in entry['worktrees'] if row['path'] == str(tree))['private_refs']
        self.assertEqual(proof['status'], 'partial')
        self.assertEqual(len(proof['parse_errors']), 1)
        self.assertIn(saved, local_sources.private_ref_pointers(entry).values())

    def test_private_ref_foreign_admin_cannot_add_foreign_objects(self):
        repo, tree, saved = self.private_fixture()
        foreign = self.repo(self.root / 'foreign')
        shutil.rmtree(tree)
        admin = repo / '.git' / 'worktrees' / 'linked'
        (admin / 'commondir').write_text(str(foreign / '.git') + '\n')
        entry = self.capture(repo)['metadata']['repositories'][0]
        proof = next(row for row in entry['worktrees'] if row['path'] == str(tree))['private_refs']
        self.assertEqual(proof['status'], 'partial')
        self.assertEqual(proof['refs'], [])
        self.assertNotIn(saved, local_sources.private_ref_pointers(entry).values())

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

    def test_distinct_push_fork_and_fetch_upstream_are_both_retained(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'remote', 'add', 'origin', 'https://github.com/upstream/repository.git')
        self.git(repo, 'remote', 'set-url', '--push', 'origin', 'git@github.com:own-user/repository.git')
        data = self.capture(repo)
        remotes = {remote['repo']: remote for remote in data['metadata']['repositories'][0]['remotes']}
        self.assertEqual(set(remotes), {'upstream/repository', 'own-user/repository'})
        self.assertEqual(remotes['upstream/repository']['directions'], ['fetch'])
        self.assertEqual(remotes['own-user/repository']['directions'], ['push'])

    def test_old_unverified_backup_alias_remains_a_historical_lead(self):
        repo = self.repo(self.root / 'repository')
        self.git(repo, 'checkout', '-qb', 'backup/historical')
        with patch.dict(self.env, {'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z',
                                  'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}):
            self.git(repo, '-c', 'user.email=unverified@example.test', 'commit',
                     '--allow-empty', '-qm', 'Unverified historical backup')
        historical = self.git(repo, 'rev-parse', 'HEAD')
        self.git(repo, 'checkout', '-q', 'main')
        data = self.capture(repo)
        lead = next(lead for lead in data['unattributed_commits'] if lead['hash'] == historical)
        self.assertEqual(lead['scope'], 'historical_snapshot')
        self.assertEqual(lead['committed_at'], '2000-01-01T00:00:00Z')
        self.assertEqual(lead['refs'], ['refs/heads/backup/historical'])
        self.assertFalse(any(branch['hash'] == historical for branch in data['branches']))
        self.assertNotIn(historical, {commit['hash'] for commit in data['commits'][str(repo)]})

    def test_old_detached_head_is_a_snapshot_without_creating_named_refs(self):
        repo = self.repo(self.root / 'repository')
        worktree = self.root / 'detached-worktree'
        self.git(repo, 'worktree', 'add', '--detach', str(worktree))
        with patch.dict(self.env, {'GIT_AUTHOR_DATE': '2000-01-01T00:00:00Z',
                                  'GIT_COMMITTER_DATE': '2000-01-01T00:00:00Z'}):
            self.git(worktree, 'commit', '--allow-empty', '-qm', 'Old detached work')
        historical = self.git(repo, 'rev-parse', 'HEAD')
        detached = self.git(worktree, 'rev-parse', 'HEAD')
        refs_before = self.git(repo, 'for-each-ref', '--format=%(refname):%(objectname)')
        data = self.capture(repo)
        self.assertEqual(self.git(repo, 'for-each-ref', '--format=%(refname):%(objectname)'), refs_before)
        snapshot = next(branch for branch in data['branches'] if branch['hash'] == detached)
        self.assertEqual(snapshot['refs'], ['worktree:' + str(worktree)])
        self.assertTrue(snapshot['detached'])
        self.assertTrue(snapshot['backup'])
        self.assertFalse(snapshot['recent_tip'])
        self.assertEqual({commit['hash'] for commit in data['commits'][str(repo)]}, {historical})

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
