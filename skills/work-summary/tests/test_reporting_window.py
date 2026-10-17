#!/usr/bin/env python3
"""Exact reporting periods, native boundary reconciliation, and CLI regressions."""

import copy
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
from reporting_window import endpoint, resolve_window, search_bounds  # noqa: E402
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

    def test_native_query_bounds_expand_to_whole_seconds(self):
        start = endpoint('2026-01-01T00:00:00.000001Z')
        end = endpoint('2026-03-31T23:59:59.999999Z')
        self.assertEqual(search_bounds(start, end), (endpoint('2026-01-01T00:00:00Z'),
                                                   endpoint('2026-04-01T00:00:00Z')))

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
    rows = json.loads(os.environ.get('GH_ROWS', '[]')) if 'is:pr ' in query and '..' in query else []
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

    def test_cli_exact_quarter_real_git_boundaries_and_detailed_render(self):
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
        report = render(data, detailed=True)
        self.assertIn('Reporting Period', report)
        self.assertIn('start inclusive, end exclusive): 2026-01-01T00:00:00Z to 2026-04-01T00:00:00Z', report)
        self.assertNotIn('Last 90 Days', report)
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
                self.assertIn('Last 7 Days', render(data, detailed=True))

    def test_fractional_native_query_overfetch_is_counted_and_exactly_filtered(self):
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
        validate(data)
        self.assertEqual([row['number'] for row in data['github']['prs']], [2])
        info = data['metadata']['github_searches']['prs']
        self.assertEqual((info['status'], info['total_count'], info['returned_count'], info['outside_window_count']),
                         ('ok', 3, 1, 2))
        calls = [json.loads(line) for line in (self.root / 'gh.log').read_text().splitlines()]
        queries = [arg for call in calls for arg in call if arg.startswith('q=') and 'updated:' in arg]
        self.assertTrue(any('updated:2026-01-01T00:00:00Z..2026-04-01T00:00:00Z' in q for q in queries))
        self.assertTrue(any('updated:>=2026-01-01T00:00:00Z' in q and '..' not in q for q in queries))
        bad = copy.deepcopy(data)
        bad['metadata']['github_searches']['prs']['outside_window_count'] = True
        for mutation in ('missing_metadata', 'missing_context', 'missing_both', 'empty_metadata'):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(data)
                if mutation in ('missing_metadata', 'missing_both'):
                    bad['metadata']['github_searches']['prs'].pop('outside_window_items')
                if mutation in ('missing_context', 'missing_both'):
                    bad['github']['context_prs'] = []
                if mutation == 'empty_metadata':
                    bad['metadata']['github_searches']['prs']['outside_window_items'] = []

    def test_initial_incomplete_inventory_recovers_without_false_partial_and_retains_failures(self):
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
        validate(data)
        info = data['metadata']['github_searches']['prs']
        self.assertEqual(info['status'], 'ok')
        self.assertFalse(info['incomplete_results'])
        self.assertTrue(info['initial_incomplete_results'])
        self.assertEqual(info['partition_recovery']['status'], 'ok')
        self.assertTrue(info['partition_recovery']['created_partitions'])
        self.assertEqual(data['metadata']['github_status'], 'ok')
        self.assertFalse(any('GitHub' in warning for warning in data['metadata']['warnings']))
        self.env['GH_RECOVERY_FAIL'] = '1'
        failed = self.run_collect(*args)
        self.assertEqual(failed.returncode, 0, failed.stderr)
        partial = json.loads(failed.stdout)
        validate(partial)
        self.assertEqual(partial['metadata']['github_status'], 'partial')
        self.assertEqual([row['number'] for row in partial['github']['prs']], [1])
        self.assertTrue(any('partition recovery' in warning for warning in partial['metadata']['warnings']))

    def test_native_case_variants_across_initial_recovery_and_roles_preserve_one_selected_identity(self):
        from work_links import referenced_keys
        item = {'html_url': 'https://github.com/org/repo/pull/1', 'number': 1,
                'title': 'Work', 'body': 'PROJ-123', 'state': 'open',
                'created_at': '2026-02-01T00:00:00Z', 'updated_at': '2026-05-01T00:00:00Z',
                'closed_at': None, 'user': {'login': 'test-user'}, 'pull_request': {'merged_at': None}}
        gh = self.bin / 'gh'
        source = gh.read_text().replace(
            "value = {'total_count': len(rows)",
            "if 'created:' in query:\n"
            "        rows = [{**row, 'html_url': 'https://github.com/Org/Repo/pull/1', 'body': 'TASK-124'} for row in rows]\n"
            "    value = {'total_count': len(rows)")
        gh.write_text(source)
        self.env.update(GH_ROWS=json.dumps([item]), GH_RECOVERY='1')
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        validate(data)
        self.assertEqual(len(data['github']['context_prs']), 1)
        self.assertEqual(len(data['github']['activity']), 1)
        self.assertEqual(set(referenced_keys(data['github'])), {'PROJ-123', 'TASK-124'})
        self.assertEqual(data['github']['context_observations'][0]['url'], 'https://github.com/Org/Repo/pull/1')
        self.assertEqual(data['metadata']['github_searches']['prs']['outside_window_items'][0]['url'],
                         'https://github.com/org/repo/pull/1')

    def test_partition_recovery_retains_changed_native_reference_payload(self):
        from work_links import referenced_keys
        item = {'html_url': 'https://github.com/org/repo/pull/1', 'number': 1,
                'title': 'Work', 'body': 'Implements PROJ-123', 'state': 'open',
                'created_at': '2026-02-01T00:00:00Z', 'updated_at': '2026-03-01T00:00:00Z',
                'closed_at': None, 'user': {'login': 'test-user'}, 'pull_request': {'merged_at': None}}
        gh = self.bin / 'gh'
        gh.write_text(gh.read_text().replace(
            "value = {'total_count': len(rows)",
            "if 'created:' in query:\n"
            "        rows = [{**row, 'body': 'Implements TASK-124'} for row in rows]\n"
            "    value = {'total_count': len(rows)"))
        self.env.update(GH_ROWS=json.dumps([item]), GH_RECOVERY='1')
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        validate(data)
        self.assertEqual(set(referenced_keys(data['github'])), {'PROJ-123', 'TASK-124'})
        self.assertEqual(len(data['github']['prs']), 1)
        self.assertEqual(len(data['github']['context_observations']), 1)
        self.assertEqual(data['metadata']['github_searches']['prs']['status'], 'ok')

    def test_conflicting_initial_native_observations_keep_both_payloads_and_reference_frontier(self):
        from work_links import associate_work, complete_references, github_items, referenced_keys
        gh = self.bin / 'gh'
        source = gh.read_text().replace(
            "rows = json.loads(os.environ.get('GH_ROWS', '[]')) if 'is:pr ' in query and '..' in query else []",
            "kind = 'PRS' if 'is:pr ' in query else 'ISSUES'\n"
            "    rows = json.loads(os.environ.get(('INITIAL_' if '..' in query else 'LATER_') + kind, '[]'))")
        gh.write_text(source)
        for kind, route, key in [('PRS', 'pull', 'PROJ-123'), ('ISSUES', 'issues', 'TASK-124')]:
            item = {'html_url': f'https://github.com/org/repo/{route}/1', 'number': 1,
                    'title': 'Original native title', 'body': 'Implements ' + key + '.', 'state': 'open',
                    'created_at': '2026-02-01T00:00:00Z', 'updated_at': '2026-05-01T00:00:00Z',
                    'closed_at': None, 'user': {'login': 'test-user'}}
            if kind == 'PRS':
                item['pull_request'] = {'merged_at': None}
            self.env['INITIAL_' + kind] = json.dumps([item])
            self.env['LATER_' + kind] = json.dumps([{**item, 'title': 'Later native title',
                                                   'body': 'Revised body without the original reference.'}])
        result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                  '--root', str(self.home))
        self.assertEqual(result.returncode, 0, result.stderr)
        data = json.loads(result.stdout)
        validate(data)
        self.assertEqual(set(referenced_keys(data['github'])), {'PROJ-123', 'TASK-124'})
        observations = data['github']['context_observations']
        self.assertEqual(len(observations), 2)
        self.assertEqual({item['body'] for item in observations}, {'Implements PROJ-123.', 'Implements TASK-124.'})
        self.assertEqual(sum(item['title'] == 'Later native title'
                             for item in github_items(data['github'], include_index=False)), 2)
        self.assertEqual(len(data['github']['activity']), 2)
        self.assertTrue(all(event['action'] == 'opened' for event in data['github']['activity']))
        # Exercise the actual fixed point and relation proof against the retained
        # earlier body, without pretending these synthetic callbacks are live.
        jira = {'metadata': {'site': 'jira.example.test'}, 'issues': []}
        github = copy.deepcopy(data['github'])
        reads = []
        def read_jira(keys):
            reads.extend(keys)
            jira['issues'].extend({'key': key, 'url': 'https://jira.example.test/browse/' + key,
                                   'sensitive': False, 'detail': {'fields': {}}} for key in keys)
        complete_references(jira, github, read_jira)
        self.assertEqual(set(reads), {'PROJ-123', 'TASK-124'})
        associate_work(jira, github)
        linked = {'metadata': {'github_host': 'github.com'}, 'jira': jira, 'github': github}
        self.assertEqual(jira['issues'][0]['pr_links'][0]['proof'], 'github_body_key')
        for item in github['context_observations']:
            item['body'] = ''
        for mutation in ('metadata_body', 'retained_body', 'missing_snapshot', 'wrong_kind', 'unknown_url', 'not_list'):
            with self.subTest(mutation=mutation):
                bad = copy.deepcopy(data)
                if mutation == 'metadata_body':
                    bad['metadata']['github_searches']['prs']['outside_window_items'][0]['body'] = 'Forged body'
                elif mutation == 'retained_body':
                    bad['github']['context_observations'][0]['body'] = 'Forged body'
                elif mutation == 'missing_snapshot':
                    bad['github']['context_observations'] = []
                elif mutation == 'wrong_kind':
                    bad['github']['context_observations'][0]['url'] = 'https://github.com/org/repo/issues/1'
                elif mutation == 'unknown_url':
                    bad['github']['context_observations'][0]['url'] = 'https://github.com/org/repo/pull/999'
                else:
                    bad['github']['context_observations'] = {}

    def test_native_search_date_disagreement_retains_context_and_only_credits_exact_events(self):
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
        for recover in (False, True):
            with self.subTest(partition_recovery=recover):
                if recover:
                    self.env['GH_RECOVERY'] = '1'
                result = self.run_collect('--since', '2026-01-01T00:00:00Z', '--until', '2026-04-01T00:00:00Z',
                                          '--root', str(self.home))
                self.assertEqual(result.returncode, 0, result.stderr)
                data = json.loads(result.stdout)
                validate(data)
                self.assertEqual(data['metadata']['github_status'], 'ok')
                info = data['metadata']['github_searches']['prs']
                self.assertEqual((info['total_count'], info['returned_count'], info['outside_window_count']), (2, 0, 2))
                self.assertEqual([(row['number'], row['updated_at']) for row in info['outside_window_items']],
                                 [(row['number'], row['updated_at']) for row in rows])
                self.assertEqual({row['number'] for row in data['github']['context_prs']}, {1, 2})
                self.assertTrue(all('initial_dated_search_context' in row['roles']
                                    for row in data['github']['context_prs']))
                self.assertEqual([(event['action'], event['at']) for event in data['github']['activity']],
                                 [('opened', '2026-03-30T12:00:00Z')])
                report = render(data)
                self.assertIn('Opened: Future merge', report)
                self.assertNotIn('Merged:', report)
                self.assertNotIn('Old context without quarter activity', report)
                self.assertFalse(any('GitHub' in warning for warning in data['metadata']['warnings']))
                bad = copy.deepcopy(data)
                bad['metadata']['github_searches']['prs']['outside_window_items'][0]['updated_at'] = '2026-03-31T23:59:59Z'
                bad = copy.deepcopy(data)
                bad['metadata']['github_searches']['prs']['outside_window_items'].pop()


if __name__ == '__main__':
    unittest.main()
