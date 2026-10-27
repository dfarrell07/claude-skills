#!/usr/bin/env python3
"""Exact reporting periods, native boundary reconciliation, and CLI regressions."""

from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
SKILL = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SKILL / 'scripts'))
from reporting_window import endpoint, resolve_window  # noqa: E402
from github_sources import Github  # noqa: E402
from work_summary import collect, render, validate  # noqa: E402


class ReportingWindowTest(unittest.TestCase):
    def test_quarters_and_offset_dst_are_exact_elapsed_intervals(self):
        cases = [
            ('2024-01-01T00:00:00Z', '2024-04-01T00:00:00Z', 91),
            ('2025-01-01T00:00:00Z', '2025-04-01T00:00:00Z', 90),
            ('2026-07-01T00:00:00Z', '2026-10-01T00:00:00Z', 92),
            ('2025-10-01T00:00:00Z', '2026-01-01T00:00:00Z', 92),
            ('2026-01-01T00:00:00-05:00', '2026-04-01T00:00:00-04:00', 90),
            ('2026-10-01T00:00:00-04:00', '2027-01-01T00:00:00-05:00', 93),
        ]
        for since, until, days in cases:
            with self.subTest(since=since, until=until):
                start, end, metadata = resolve_window(None, since=since, until=until)
                self.assertEqual(metadata['days'], days)
                self.assertEqual(end + timedelta(microseconds=1), endpoint(until))
                self.assertEqual(start, endpoint(since))
                self.assertEqual(metadata['window_kind'], 'explicit')

    def test_microsecond_window_and_offsets_normalize_before_day_count(self):
        start, end, metadata = resolve_window(None, since='2026-04-01T01:00:00.000001+01:00',
                                             until='2026-04-01T00:00:00.000002Z')
        self.assertEqual(start, end)
        self.assertEqual(metadata['days'], 1)
        self.assertEqual(metadata['since_at'], '2026-04-01T00:00:00.000001Z')
        self.assertEqual(metadata['until_exclusive'], '2026-04-01T00:00:00.000002Z')

    def test_legacy_window_metadata_and_leading_zeroes_are_unchanged(self):
        now = datetime(2026, 10, 16, 12, 34, 56, 123, tzinfo=timezone.utc)
        for days in ('7', '007', None):
            start, end, metadata = resolve_window(days, now)
            self.assertEqual(end - start, timedelta(days=7))
            self.assertEqual(metadata, {'days': 7, 'since_date': '2026-10-09', 'to_date': '2026-10-16',
                                        'since_at': '2026-10-09T12:34:56Z', 'to_at': '2026-10-16T12:34:56Z'})

    def test_invalid_input_fails_before_any_capability_or_native_read(self):
        cases = [
            {'since': '2026-01-01T00:00:00Z'},
            {'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-04-01T00:00:00Z', 'until': '2026-01-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00Z', 'until': '2026-01-01T00:00:00Z'},
            {'since': '2026-01-01', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00.0000001Z', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-02-30T00:00:00Z', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00+25:00', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00+00:60', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00+01:99', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00-04:99', 'until': '2026-04-01T00:00:00Z'},
            {'since': '2026-01-01T00:00:00Z', 'until': '2026-04-01T00:00:00+00:60'},
        ]
        with patch('work_summary.command') as command, patch('work_summary.shutil.which') as capability:
            for kwargs in cases:
                with self.subTest(kwargs=kwargs), self.assertRaises(ValueError):
                    collect(None, [], **kwargs)
            with self.assertRaises(ValueError):
                collect('7', [], since='2026-01-01T00:00:00Z', until='2026-04-01T00:00:00Z')
            command.assert_not_called()
            capability.assert_not_called()

    def test_later_updated_item_keeps_only_native_events_inside_exact_period(self):
        start, end, _ = resolve_window(None, since='2026-01-01T00:00:00Z', until='2026-04-01T00:00:00Z')
        item = {'html_url': 'https://github.com/org/repo/pull/1', 'number': 1, 'title': 'Quarter work',
                'state': 'closed', 'created_at': '2025-01-01T00:00:00Z', 'updated_at': '2026-05-01T00:00:00Z',
                'closed_at': '2026-04-03T00:00:00Z', 'pull_request': {'merged_at': '2026-04-03T00:00:00Z'},
                'user': {'login': 'other'}}
        moments = ['2025-12-31T23:59:59.999999Z', '2026-01-01T00:00:00Z',
                   '2026-03-31T23:59:59.999999Z', '2026-04-01T00:00:00Z']

        def api(*args):
            if 'search/issues' in args:
                query = next(arg[2:] for arg in args if arg.startswith('q='))
                if 'updated:' in query:
                    self.assertIn('updated:>=2026-01-01T00:00:00Z', query)
                    self.assertNotIn('..', query)
                rows = [item] if 'is:pr ' in query else []
                return {'total_count': len(rows), 'incomplete_results': False, 'items': rows}
            if any(arg.endswith('/reviews') for arg in args):
                return [{'id': index, 'user': {'login': 'me'}, 'submitted_at': at, 'body': 'Review evidence',
                         'state': 'APPROVED', 'html_url': item['html_url'] + f'#pullrequestreview-{index}'}
                        for index, at in enumerate(moments, 1)]
            return []

        client = Github(api, 'github.com', 'me', start, end, lambda message: self.fail(message))
        data = {'prs': [], 'issues': []}
        client.discover(data)
        self.assertEqual({event['at'] for event in data['activity']}, set(moments[1:3]))
        self.assertEqual({event['action'] for event in data['activity']}, {'review'})
        self.assertEqual(data['context_prs'][0]['merged_at'], '2026-04-03T00:00:00Z')


class ReportingWindowCliTest(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory(prefix='work-summary-window.')
        self.addCleanup(temporary.cleanup)
        # macOS exposes temporary directories through /var -> /private/var;
        # collector repository identities deliberately use canonical paths.
        self.root = Path(temporary.name).resolve()
        self.home = self.root / 'home'
        self.home.mkdir()
        self.bin = self.root / 'bin'
        self.bin.mkdir()
        self.env = {**os.environ, 'HOME': str(self.home), 'PATH': str(self.bin) + os.pathsep + os.environ['PATH'],
                    'GIT_CONFIG_NOSYSTEM': '1', 'GIT_CONFIG_GLOBAL': str(self.home / '.gitconfig'),
                    'GH_LOG': str(self.root / 'gh.log'), 'GH_HOST': 'github.com'}
        gh = self.bin / 'gh'
        gh.write_text('#!' + sys.executable + '\n' + '''
from datetime import datetime
import json, os, sys
args = sys.argv[1:]
with open(os.environ['GH_LOG'], 'a') as log:
    log.write(json.dumps(args) + '\\n')
if args[-1] == 'user':
    value = {'login': 'test-user'}
elif 'search/issues' in args:
    query = next(arg[2:] for arg in args if arg.startswith('q='))
    authored = 'is:pr author:' in query and 'updated:>=' in query
    rows = json.loads(os.environ.get('GH_ROWS', '[]')) if authored else []
    if 'created:' in query:
        if os.environ.get('GH_RECOVERY_FAIL'):
            sys.exit(1)
        lower, upper = [datetime.fromisoformat(at.replace('Z', '+00:00'))
                        for at in query.split('created:')[1].split(' ')[0].split('..')]
        rows = [row for row in rows if lower <= datetime.fromisoformat(row['created_at'].replace('Z', '+00:00')) <= upper]
    value = {'total_count': len(rows), 'incomplete_results': bool(os.environ.get('GH_RECOVERY')) and 'created:' not in query and bool(rows),
             'items': rows}
elif 'search/commits' in args:
    value = {'total_count': 0, 'incomplete_results': False, 'items': []}
elif 'graphql' in args:
    value = {'data': {'search': {'issueCount': 0, 'nodes': [], 'pageInfo': {'hasNextPage': False, 'endCursor': None}}}}
else:
    value = []
print(json.dumps(value))
''')
        gh.chmod(0o755)
        acli = self.bin / 'acli'
        acli.write_text('#!/bin/sh\nexit 1\n')
        acli.chmod(0o755)

    def run_collect(self, *args):
        return subprocess.run(['bash', str(SKILL / 'scripts/work-summary.sh'), *args], cwd=self.home,
                              env=self.env, text=True, capture_output=True)

    def git(self, repo, *args, dates=None):
        return subprocess.run(['git', '-C', str(repo), *args], env={**self.env, **(dates or {})},
                              check=True, text=True, capture_output=True).stdout.strip()

    def test_cli_exact_quarter_real_git_boundaries(self):
        repo = self.home / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        self.git(repo, 'config', 'user.name', 'Test User')
        self.git(repo, 'config', 'user.email', 'me@example.test')
        dates = ['2025-12-31T23:59:59Z', '2026-01-01T00:00:00Z',
                 '2026-03-31T23:59:59Z', '2026-04-01T00:00:00Z']
        for index, date in enumerate(dates):
            self.git(repo, 'commit', '--allow-empty', '-qm', f'Event {index}',
                     dates={'GIT_AUTHOR_DATE': date, 'GIT_COMMITTER_DATE': date})
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(repo), '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        validate(data)
        self.assertEqual({row['message'] for row in data['commits'][str(repo)]}, {'Event 1', 'Event 2'})
        self.assertEqual(data['metadata']['days'], 90)
        self.assertEqual(data['metadata']['repos_searched'], 1)
        # Git's second-precision lower filter can overfetch this commit; capture
        # reconciliation must remove it without damaging repository accounting.
        fraction = self.run_collect('--since', '2026-01-01T00:00:00.000001Z',
                                   '--until', '2026-04-01T00:00:00Z', '--root', str(repo))
        self.assertEqual(fraction.returncode, 0, fraction.stderr)
        selected = json.loads(fraction.stdout)
        self.assertEqual([row['message'] for row in selected['commits'][str(repo)]], ['Event 2'])

    def test_cli_rejects_ambiguous_roots_and_mixed_modes_before_native_reads(self):
        pairs = ['--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z']
        for args in [('7', *pairs), (str(self.home), *pairs), (*pairs, str(self.home)),
                     ('--root', str(self.home)), ('--since', '2026-01-01T00:00:00Z')]:
            with self.subTest(args=args):
                result = self.run_collect(*args)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, '')
                self.assertNotIn('Traceback', result.stderr)
                self.assertFalse((self.root / 'gh.log').exists())

    def test_explicit_symlink_roots_preserve_canonical_repository_identity(self):
        repo = self.home / 'repo'
        repo.mkdir()
        self.git(repo, 'init', '-q')
        self.git(repo, 'config', 'user.name', 'Test User')
        self.git(repo, 'config', 'user.email', 'me@example.test')
        self.git(repo, 'commit', '--allow-empty', '-qm', 'Quarter accomplishment',
                 dates={'GIT_AUTHOR_DATE': '2026-02-01T00:00:00Z',
                        'GIT_COMMITTER_DATE': '2026-02-01T00:00:00Z'})
        alias = self.root / 'home-alias'
        alias.symlink_to(self.home, target_is_directory=True)
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(alias / 'repo'), '--root', str(repo))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        validate(data)
        self.assertEqual(data['metadata']['repos_searched'], 1)
        self.assertEqual(data['metadata']['repos_with_commits'], 1)
        self.assertEqual(set(data['commits']), {str(repo.resolve())})
        self.assertEqual([row['message'] for row in data['commits'][str(repo.resolve())]],
                         ['Quarter accomplishment'])

    def test_cli_default_and_positional_rolling_roots_still_work(self):
        for args in [(), ('007', str(self.home))]:
            with self.subTest(args=args):
                result = self.run_collect(*args)
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                self.assertEqual(data['metadata']['days'], 7)
                self.assertNotIn('window_kind', data['metadata'])
                self.assertNotIn('until_exclusive', data['metadata'])

    def test_explicit_window_filters_items_exactly_and_queries_whole_seconds(self):
        def row(number, at):
            return {'html_url': f'https://github.com/org/repo/pull/{number}', 'number': number,
                    'title': f'Event {number}', 'state': 'open', 'created_at': '2020-01-01T00:00:00Z',
                    'updated_at': at, 'closed_at': None, 'pull_request': {'merged_at': None},
                    'user': {'login': 'test-user'}}
        self.env['GH_ROWS'] = json.dumps([row(1, '2026-01-01T00:00:00Z'),
                                         row(2, '2026-02-01T00:00:00Z'),
                                         row(3, '2026-04-01T00:00:00Z')])
        result = self.run_collect('--since', '2026-01-01T00:00:00.000001Z',
                                  '--until', '2026-04-01T00:00:00Z', '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([row['number'] for row in data['github']['prs']], [2])
        self.assertEqual(sorted(row['number'] for row in data['github']['context_prs']), [1, 3])
        calls = [json.loads(line) for line in (self.root / 'gh.log').read_text().splitlines()]
        queries = [arg for call in calls for arg in call if arg.startswith('q=') and 'updated:' in arg]
        self.assertTrue(any('updated:>=2026-01-01T00:00:00Z' in q for q in queries))

    def test_capped_inventory_recovers_through_creation_partitions(self):
        self.env['GH_ROWS'] = json.dumps([
            {'html_url': 'https://github.com/org/repo/pull/1', 'number': 1,
             'title': 'Quarter work', 'state': 'open', 'created_at': '2020-01-01T00:00:00Z',
             'updated_at': '2026-02-01T00:00:00Z', 'closed_at': None,
             'pull_request': {'merged_at': None}, 'user': {'login': 'test-user'}}])
        self.env['GH_RECOVERY'] = '1'
        args = ('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z', '--root', str(self.home))
        result = self.run_collect(*args)
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual([row['number'] for row in data['github']['prs']], [1])
        self.assertTrue(data['metadata']['github_discovery']['recent_authored_prs']['created_partitions'])
        self.assertEqual(data['metadata']['github_status'], 'ok')
        self.env['GH_RECOVERY_FAIL'] = '1'
        failed = self.run_collect(*args)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        partial = json.loads(failed.stdout)
        self.assertEqual(partial['metadata']['github_status'], 'partial')
        self.assertEqual([row['number'] for row in partial['github']['prs']], [1])

    def test_merge_after_the_window_is_not_credited(self):
        rows = [
            {'html_url': 'https://github.com/org/repo/pull/1', 'number': 1,
             'title': 'Future merge', 'state': 'closed', 'created_at': '2026-03-30T12:00:00Z',
             'updated_at': '2026-05-01T20:48:13Z', 'closed_at': '2026-04-02T00:00:00Z',
             'pull_request': {'merged_at': '2026-04-02T00:00:00Z'}, 'user': {'login': 'test-user'}},
            {'html_url': 'https://github.com/org/repo/pull/2', 'number': 2,
             'title': 'Old context without quarter activity', 'state': 'open', 'created_at': '1999-01-01T00:00:00Z',
             'updated_at': '2000-01-01T00:00:00Z', 'closed_at': None,
             'pull_request': {'merged_at': None}, 'user': {'login': 'test-user'}},
        ]
        self.env['GH_ROWS'] = json.dumps(rows)
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        self.assertEqual(data['metadata']['github_status'], 'ok')
        self.assertEqual([row['number'] for row in data['github']['prs']], [1])
        self.assertEqual([row['number'] for row in data['github']['context_prs']], [2])
        self.assertEqual([(event['action'], event['at']) for event in data['github']['activity']],
                         [('opened', '2026-03-30T12:00:00Z')])
        report = render(data)
        self.assertIn('Opened: Future merge', report)
        self.assertNotIn('Merged:', report)
        self.assertNotIn('Old context without quarter activity', report)

if __name__ == '__main__':
    unittest.main()
