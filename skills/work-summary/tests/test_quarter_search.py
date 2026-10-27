"""Native query partition regressions for quarter-long GitHub inventories."""
from datetime import datetime, timezone
from pathlib import Path
import re
import subprocess
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evidence import timestamp  # noqa: E402
from github_sources import Github, MAX_SEARCH_REQUESTS  # noqa: E402


def item(number, created):
    return {'html_url': f'https://github.com/org/repo/pull/{number}', 'number': number,
            'title': 'Synthetic work', 'state': 'open', 'created_at': created,
            'updated_at': '2026-10-16T03:00:00Z', 'closed_at': None,
            'pull_request': {'merged_at': None}, 'user': {'login': 'me'}}


class QuarterSearchTest(unittest.TestCase):
    def client(self, rows, mutate=None):
        calls, warnings = [], []

        def api(*args):
            query = next(value[2:] for value in args if value.startswith('q='))
            page = int(next(value[5:] for value in args if value.startswith('page=')))
            calls.append((query, page))
            selected = rows
            match = re.search(r' created:(\S+)\.\.(\S+)$', query)
            if match:
                lo, hi = map(timestamp, match.groups())
                selected = [row for row in rows if lo <= timestamp(row['created_at']) <= hi]
            response = {'total_count': len(selected), 'incomplete_results': False,
                        'items': selected[(page - 1) * 100:page * 100]}
            return mutate(query, page, response) if mutate else response

        client = Github(api, 'github.com', 'me', datetime(2026, 7, 1, tzinfo=timezone.utc),
                        datetime(2026, 10, 1, tzinfo=timezone.utc), warnings.append)
        return client, calls, warnings

    def test_recovers_1001_preserving_unbounded_updated_query_and_deduplicating_root(self):
        rows = [item(i, '2000-01-01T00:00:00Z' if i <= 500 else '2020-01-01T00:00:00Z')
                for i in range(1, 1002)]
        client, calls, warnings = self.client(rows)
        original = 'is:pr author:me updated:>=2026-07-01T00:00:00Z'
        found = client.search(original, 'quarter', 'prs')
        self.assertEqual(len(found), 1001)
        self.assertEqual(len({row['number'] for row in found}), 1001)
        self.assertEqual(client.coverage['quarter']['status'], 'ok')
        self.assertEqual(warnings, [])
        self.assertTrue(all(query == original or query.startswith(original + ' created:') for query, _ in calls))
        self.assertFalse(any('updated:<=' in query for query, _ in calls))
        self.assertLess(len(calls), MAX_SEARCH_REQUESTS)

    def test_failed_partition_page_keeps_other_partitions_and_original_positives(self):
        rows = [item(i, '2000-01-01T00:00:00Z' if i <= 500 else '2020-01-01T00:00:00Z')
                for i in range(1, 1002)]

        def fail(query, page, response):
            if ' created:' in query and page == 2:
                raise subprocess.CalledProcessError(1, 'gh')
            return response

        client, _, warnings = self.client(rows, fail)
        found = client.search('is:pr author:me', 'quarter', 'prs')
        self.assertGreaterEqual(len(found), 1000)
        self.assertEqual(client.coverage['quarter']['status'], 'partial')
        self.assertTrue(any('search failed' in warning for warning in warnings))

    def test_same_creation_second_cannot_hide_cap(self):
        rows = [item(i, '2020-01-01T00:00:00Z') for i in range(1, 1002)]
        client, calls, warnings = self.client(rows)
        found = client.search('is:pr author:me', 'quarter', 'prs')
        self.assertEqual(len(found), 1000)
        self.assertEqual(client.coverage['quarter']['status'], 'partial')
        self.assertLessEqual(len(calls), MAX_SEARCH_REQUESTS)
        self.assertTrue(any('one creation second' in warning for warning in warnings))

    def test_incomplete_search_recovers_even_below_result_cap(self):
        rows = [item(1, '2000-01-01T00:00:00Z'), item(2, '2020-01-01T00:00:00Z')]

        def incomplete(query, page, response):
            if ' created:' not in query:
                return {**response, 'incomplete_results': True, 'items': [rows[0]]}
            return response

        client, _, warnings = self.client(rows, incomplete)
        found = client.search('is:pr author:me', 'quarter', 'prs')
        self.assertEqual({row['number'] for row in found}, {1, 2})
        # The native incomplete observation remains, while verified partitions
        # recover the full original inventory without an unresolved gap.
        self.assertEqual(client.coverage['quarter']['status'], 'ok')
        self.assertTrue(client.coverage['quarter']['incomplete_results'])
        self.assertEqual(warnings, [])

    def test_persistently_incomplete_empty_slices_stop_at_request_budget(self):
        def incomplete(query, page, response):
            return {'total_count': 0, 'incomplete_results': True, 'items': []}

        client, calls, warnings = self.client([], incomplete)
        self.assertEqual(client.search('is:pr author:me', 'quarter', 'prs'), [])
        self.assertEqual(len(calls), MAX_SEARCH_REQUESTS)
        self.assertEqual(client.coverage['quarter']['status'], 'partial')
        self.assertTrue(any('request budget' in warning for warning in warnings))

    def test_source_ignoring_partition_is_bounded_and_retains_positive_rows(self):
        rows = [item(i, '2020-01-01T00:00:00Z') for i in range(1, 1002)]

        def ignore(query, page, response):
            return {'total_count': len(rows), 'incomplete_results': False,
                    'items': rows[(page - 1) * 100:page * 100]}

        client, calls, warnings = self.client(rows, ignore)
        found = client.search('is:pr author:me', 'quarter', 'prs')
        self.assertEqual(len(found), 1000)
        self.assertEqual(client.coverage['quarter']['status'], 'partial')
        self.assertLessEqual(len(calls), MAX_SEARCH_REQUESTS)
        self.assertTrue(any('outside its creation-time partition' in warning for warning in warnings))


if __name__ == '__main__':
    unittest.main()
