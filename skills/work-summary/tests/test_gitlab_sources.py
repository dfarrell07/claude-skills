"""GitLab native advertisements: no fetch, exact evidence, graceful partial reads."""

import copy
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import gitlab_sources as gl  # noqa: E402


SHA = 'a' * 40
OTHER = 'b' * 40
HEAD = SHA + '\trefs/merge-requests/42/head\n'
MERGE = OTHER + '\trefs/merge-requests/42/merge\n'


class GitLabSourcesTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.env = {**os.environ, 'HOME': str(self.root), 'GIT_CONFIG_GLOBAL': os.devnull,
                    'GIT_CONFIG_SYSTEM': os.devnull, 'GIT_CONFIG_NOSYSTEM': '1',
                    'GIT_TERMINAL_PROMPT': '0'}
        for name in ('GIT_CONFIG_COUNT', 'GIT_DIR', 'GIT_WORK_TREE', 'GIT_COMMON_DIR', 'GIT_INDEX_FILE',
                     'GIT_OBJECT_DIRECTORY', 'GIT_ALTERNATE_OBJECT_DIRECTORIES'):
            self.env.pop(name, None)
        self.addCleanup(patch.stopall)
        patch.object(gl, 'GIT_ENV', self.env).start()
        self.repo = self.root / 'repo'
        self.repo.mkdir()
        self.git('init', '-q')
        self.git('remote', 'add', 'origin', 'ssh://git@gitlab.example.test/group/sub/project.git')
        self.native = gl.native_git
        self.calls, self.warnings = [], []

    def git(self, *args):
        return subprocess.run(['git', '-C', str(self.repo), *args], env=self.env,
                              capture_output=True, check=True).stdout

    def inputs(self):
        repos = [{'path': str(self.repo), 'remotes': [
            {'name': 'origin', 'host': 'gitlab.example.test', 'repo': None, 'kind': 'other'}]}]
        branches = [{'repo': str(self.repo), 'hash': SHA, 'object_type': 'commit', 'pr_status': 'partial'},
                    {'repo': '/another/local/backup', 'hash': SHA, 'object_type': 'commit', 'pr_status': 'partial'}]
        return branches, repos

    def collect(self, output=HEAD + MERGE, code=0, timeout=False):
        branches, repos = self.inputs()

        def native(path, *args, **kwargs):
            if args[0] != 'ls-remote':
                return self.native(path, *args, **kwargs)
            self.calls.append((path, args, kwargs))
            if timeout:
                raise subprocess.TimeoutExpired(args, 90, output=output.encode())
            return subprocess.CompletedProcess(args, code, output.encode(), b'')

        with patch.object(gl, 'native_git', side_effect=native):
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        data = {'metadata': {'repositories': repos, 'gitlab_discovery': result}, 'branches': branches}
        return data

    def test_nested_routes_and_credentials_are_sanitized(self):
        route = gl.remote_route('https://user:secret@gitlab.example.test/group/sub/project.git')
        self.assertEqual(route, {'host': 'gitlab.example.test', 'repo': 'group/sub/project', 'transport': 'https'})
        self.assertEqual(gl.remote_route('git@gitlab.com:group/project.git')['repo'], 'group/project')
        for url in ('https://notgitlab.example.test/g/p', 'file:///tmp/gitlab/g/p',
                    'ext::gitlab.example.test/g/p', 'https://gitlab.example.test/g/../p',
                    'https://gitlab.example.test/g/%2fp', 'https://gitlab.example.test/g/p?token=secret',
                    'https://gitlab.example.test:8443/g/p',
                    'ssh://git@gitlab.example.test:bad/g/p', 'https://gitlab.example.test//g/p'):
            with self.subTest(url=url):
                self.assertIsNone(gl.remote_route(url))

    def test_real_git_remote_lookup_global_head_join_and_no_fetch(self):
        before = self.git('for-each-ref')
        data = self.collect()
        result = data['metadata']['gitlab_discovery']
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(self.calls), 1)
        self.assertEqual(self.calls[0][1], ("ls-remote", "--refs", "--upload-pack=git-upload-pack", "--",
                                           "ssh://git@gitlab.example.test/group/sub/project.git",
                                           'refs/merge-requests/*/head', 'refs/merge-requests/*/merge'))
        self.assertEqual(before, self.git('for-each-ref'))
        self.assertEqual([len(row['merge_requests']) for row in data['branches']], [1, 1])
        self.assertTrue(all(row['pr_status'] == 'partial' for row in data['branches']))
        self.assertEqual(result['queries'][0]['native_output'], HEAD + MERGE)
        self.assertFalse(self.warnings)

    def test_native_read_environment_is_noninteractive_and_cannot_update_host_keys(self):
        env = gl.git_env()
        self.assertEqual(env['GIT_ALLOW_PROTOCOL'], 'ssh:https')
        self.assertIn('BatchMode=yes', env['GIT_SSH_COMMAND'])
        self.assertIn('StrictHostKeyChecking=yes', env['GIT_SSH_COMMAND'])
        self.assertIn('UpdateHostKeys=no', env['GIT_SSH_COMMAND'])
        self.assertEqual(env['GIT_TERMINAL_PROMPT'], '0')

    def test_native_credential_fill_cannot_launch_configured_askpass(self):
        helper, marker = self.root / 'askpass', self.root / 'asked'
        helper.write_text('#!/usr/bin/env python3\nfrom pathlib import Path\n'
                          + f'Path({str(marker)!r}).write_text("invoked")\nprint("fixture-value")\n')
        helper.chmod(0o700)
        args = ['git', '-C', str(self.repo), '-c', 'credential.helper=',
                '-c', 'core.askPass=' + str(helper), 'credential', 'fill']
        request = b'protocol=https\nhost=gitlab.example.test\n\n'
        positive = subprocess.run(args, input=request, env=self.env, capture_output=True, timeout=10)
        self.assertEqual(positive.returncode, 0)
        self.assertTrue(marker.exists())
        marker.unlink()
        blocked = subprocess.run(args, input=request, env=gl.git_env(), capture_output=True, timeout=10)
        self.assertNotEqual(blocked.returncode, 0)
        self.assertFalse(marker.exists())

    def test_repository_config_cannot_take_part_in_the_network_read(self):
        marker = self.root / 'helper-ran'
        self.git('config', 'credential.helper', f'!touch {marker}')
        self.git('config', 'http.proxy', 'http://127.0.0.1:9')
        data = self.collect()
        (path, args, kwargs), = self.calls
        self.assertNotEqual(path, str(self.repo))
        self.assertFalse(Path(path).exists())
        self.assertEqual(args[4], 'ssh://git@gitlab.example.test/group/sub/project.git')
        self.assertEqual(kwargs['env']['GIT_CEILING_DIRECTORIES'], os.path.dirname(path))
        self.assertEqual(data['metadata']['gitlab_discovery']['status'], 'ok')
        neutral = Path(tempfile.mkdtemp(dir=self.root))
        config = gl.native_git(str(neutral), 'config', '--get', 'credential.helper',
                               env={'GIT_CEILING_DIRECTORIES': str(neutral.parent)})
        self.assertEqual(config.stdout, b'')
        self.assertFalse(marker.exists())

    def test_users_global_ssh_command_is_kept_with_batch_options(self):
        gl.user_ssh_command.cache_clear()
        self.addCleanup(gl.user_ssh_command.cache_clear)
        config = self.root / 'global-config'
        config.write_text('[core]\n\tsshCommand = ssh -i ~/.ssh/work_key\n')
        with patch.dict(self.env, {'GIT_CONFIG_GLOBAL': str(config)}):
            command = gl.git_env()['GIT_SSH_COMMAND']
        self.assertTrue(command.startswith('ssh -i ~/.ssh/work_key -o BatchMode=yes'))

    def test_malformed_row_retains_other_positive_and_exact_parse_gap(self):
        data = self.collect(HEAD + 'bad native row\n' + MERGE)
        query = data['metadata']['gitlab_discovery']['queries'][0]
        self.assertEqual(query['status'], 'partial')
        self.assertEqual(query['errors'], [{'line': 2, 'reason': 'invalid_advertised_ref'}])
        self.assertEqual(len(data['branches'][0]['merge_requests']), 1)
        self.assertTrue(self.warnings)
        broken = copy.deepcopy(data)
        broken['metadata']['gitlab_discovery']['queries'][0]['errors'][0]['line'] = 1

    def test_abbreviated_hash_and_enormous_iid_are_partial_not_false_matches(self):
        rows, errors = gl.parse_refs(HEAD + 'abcdef0\trefs/merge-requests/1/head\n'
                                     + SHA + '\trefs/merge-requests/' + '1' * 5000 + '/head\n')
        self.assertEqual(len(rows), 1)
        self.assertEqual([error['line'] for error in errors], [2, 3])
        rows, errors = gl.parse_refs('a' * 64 + '\trefs/merge-requests/1/head\n')
        self.assertEqual(len(rows), 1)
        self.assertFalse(errors)

    def test_failure_and_timeout_retain_native_positive(self):
        for options in ({'code': 128}, {'timeout': True}):
            with self.subTest(options=options):
                data = self.collect(**options)
                self.assertEqual(data['metadata']['gitlab_discovery']['status'], 'partial')
                self.assertEqual(len(data['branches'][0]['merge_requests']), 1)

    def test_changed_effective_project_does_not_get_mislabelled(self):
        branches, repos = self.inputs()
        repos[0]['remotes'][0]['repo'] = 'different/project'
        with patch.object(gl, 'native_git', wraps=self.native) as run:
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        self.assertEqual(result['status'], 'partial')
        self.assertFalse(result['queries'])
        self.assertFalse(any(call.args[1] == 'ls-remote' for call in run.call_args_list))

    def test_multiple_fetch_or_push_projects_remain_an_explicit_gap(self):
        self.git('remote', 'set-url', '--add', 'origin', 'ssh://git@gitlab.example.test/another/project.git')
        data = self.collect()
        self.assertEqual(data['metadata']['gitlab_discovery']['status'], 'partial')
        self.assertEqual(data['metadata']['gitlab_discovery']['failures'][0]['reason'],
                         'additional_url_not_addressable_by_remote_name')
        self.assertEqual(len(data['branches'][0]['merge_requests']), 1)

    def test_same_project_https_push_and_ssh_fetch_do_not_make_a_false_gap(self):
        self.git('remote', 'set-url', '--push', 'origin', 'https://gitlab.example.test/group/sub/project.git')
        data = self.collect()
        self.assertEqual(data['metadata']['gitlab_discovery']['status'], 'ok')
        self.assertFalse(data['metadata']['gitlab_discovery']['failures'])

    def test_deduplicate_complete_project_advertisements(self):
        branches, repos = self.inputs()
        repos.append(copy.deepcopy(repos[0]))
        original = gl.native_git
        with patch.object(gl, 'native_git', side_effect=lambda path, *args, **kw:
                          subprocess.CompletedProcess(args, 0, HEAD.encode(), b'') if args[0] == 'ls-remote'
                          else original(path, *args, **kw)) as run:
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(sum(call.args[1] == 'ls-remote' for call in run.call_args_list), 1)

    def test_failed_clone_retries_another_existing_remote_without_losing_positive(self):
        branches, repos = self.inputs()
        other = self.root / 'other'
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(other)], env=self.env,
                       capture_output=True, check=True)
        subprocess.run(['git', '-C', str(other), 'remote', 'set-url', 'origin',
                        'ssh://git@gitlab.example.test/group/sub/project.git'], env=self.env, check=True)
        repos.append({'path': str(other), 'remotes': copy.deepcopy(repos[0]['remotes'])})
        branches.append({'repo': str(other), 'hash': SHA, 'object_type': 'commit', 'pr_status': 'partial'})

        attempts = []

        def native(path, *args, **kwargs):
            if args[0] == 'ls-remote':
                attempts.append(path)
                # A transient failure on the first read is retried through the next checkout.
                return subprocess.CompletedProcess(args, 128 if len(attempts) == 1 else 0, HEAD.encode(), b'')
            return self.native(path, *args, **kwargs)

        with patch.object(gl, 'native_git', side_effect=native):
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        self.assertEqual(result['status'], 'ok')
        self.assertEqual(len(result['queries']), 2)
        self.assertEqual([query['status'] for query in result['queries']], ['partial', 'ok'])
        self.assertFalse(self.warnings)

    def test_unreachable_host_is_tried_once_per_run(self):
        branches, repos = self.inputs()
        other = self.root / 'other'
        subprocess.run(['git', 'clone', '-q', str(self.repo), str(other)], env=self.env, capture_output=True, check=True)
        subprocess.run(['git', '-C', str(other), 'remote', 'set-url', 'origin',
                        'ssh://git@gitlab.example.test/group/other.git'], env=self.env, check=True)
        repos.append({'path': str(other), 'remotes': [
            {'name': 'origin', 'host': 'gitlab.example.test', 'repo': None, 'kind': 'other'}]})
        branches.append({'repo': str(other), 'hash': SHA, 'object_type': 'commit', 'pr_status': 'partial'})
        attempts = []

        def native(path, *args, **kwargs):
            if args[0] == 'ls-remote':
                attempts.append(kwargs['timeout'])
                raise subprocess.TimeoutExpired(args, kwargs['timeout'])
            return self.native(path, *args, **kwargs)

        with patch.object(gl, 'native_git', side_effect=native):
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        self.assertEqual(attempts, [gl.LS_REMOTE_TIMEOUT])
        self.assertEqual([f['reason'] for f in result['failures']], ['host_unreachable_earlier_in_run'])
        self.assertEqual(result['status'], 'partial')

    def test_missing_git_and_no_provider_degrade_without_inventing_links(self):
        branches, repos = self.inputs()
        with patch.object(gl.shutil, 'which', return_value=None):
            result = gl.collect_gitlab_refs(branches, repos, self.warnings.append)
        self.assertEqual(result['status'], 'unavailable')
        self.assertFalse(any(branch.get('merge_requests') for branch in branches))
        self.assertEqual(gl.collect_gitlab_refs(branches, [], self.warnings.append)['status'], 'not_applicable')

    def test_credentials_do_not_enter_captured_scope(self):
        self.git('remote', 'set-url', 'origin', 'https://user:secret@gitlab.example.test/group/sub/project.git')
        data = self.collect()
        self.assertNotIn('secret', json.dumps(data))
        self.assertNotIn('user:', json.dumps(data))

    def test_non_utf8_bad_row_does_not_drop_healthy_ascii_refs(self):
        rows, errors = gl.parse_refs(HEAD + '\udcff\trefs/merge-requests/1/head\n')
        self.assertEqual(rows[0]['hash'], SHA)
        self.assertEqual(len(errors), 1)


if __name__ == '__main__':
    unittest.main()
