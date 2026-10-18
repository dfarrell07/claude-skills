"""Native timestamp compatibility and machine-output safety."""

from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
import evidence  # noqa: E402
from evidence import iso, timestamp  # noqa: E402


class NetworkCommandTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        evidence.reset_network_state()
        self.addCleanup(evidence.reset_network_state)
        self.log = Path(self.tmp.name) / 'calls'

    def tool(self, script):
        path = Path(self.tmp.name) / 'gh'
        path.write_text('#!/bin/sh\necho call >> ' + str(self.log) + '\n' + script)
        path.chmod(0o700)
        return str(path)

    def calls(self):
        return len(self.log.read_text().splitlines()) if self.log.exists() else 0

    def test_repeated_timeouts_stop_further_reads_of_that_provider(self):
        gh = self.tool('sleep 5\n')
        for _ in range(evidence.BREAKER_LIMIT + 2):
            with self.assertRaises(subprocess.TimeoutExpired):
                evidence.command([gh, 'api', 'user'], timeout=0.2)
        self.assertEqual(self.calls(), evidence.BREAKER_LIMIT)
        self.assertEqual(evidence.command(['true']), '')

    def test_success_resets_the_timeout_count(self):
        gh = self.tool('[ "$1" = slow ] && sleep 5\necho ok\n')
        for _ in range(3):
            for _ in range(evidence.BREAKER_LIMIT - 1):
                with self.assertRaises(subprocess.TimeoutExpired):
                    evidence.command([gh, 'slow'], timeout=0.2)
            self.assertEqual(evidence.command([gh, 'fast']), 'ok\n')

    def test_rate_limit_is_retried_after_waiting_and_other_errors_are_not(self):
        marker = Path(self.tmp.name) / 'limited'
        gh = self.tool(f'if [ ! -e {marker} ]; then touch {marker}; '
                       'echo "HTTP 403: API rate limit exceeded" >&2; exit 1; fi\necho ok\n')
        waits = []
        with patch.object(evidence, 'sleep', waits.append):
            self.assertEqual(evidence.command([gh, 'api', 'search/issues']), 'ok\n')
        self.assertEqual(waits, [evidence.RATE_LIMIT_WAITS[0]])
        body = self.tool('echo \'{"data": {"body": "Handle API rate limit exceeded"}}\'; '
                         'echo "GraphQL: Could not resolve to a Commit" >&2; exit 1\n')
        with patch.object(evidence, 'sleep', waits.append), self.assertRaises(subprocess.CalledProcessError):
            evidence.command([body, 'api', 'graphql'])
        self.assertEqual(len(waits), 1)
        failing = self.tool('echo "HTTP 404: Not Found" >&2; exit 1\n')
        with patch.object(evidence, 'sleep', waits.append), self.assertRaises(subprocess.CalledProcessError):
            evidence.command([failing, 'api', 'repos/x/y'])
        self.assertEqual(len(waits), 1)


class EvidenceTest(unittest.TestCase):
    def test_native_jira_offsets_work_on_minimum_python(self):
        self.assertEqual(iso(timestamp('2026-10-15T12:00:00.123+0000')), '2026-10-15T12:00:00.123000Z')
        self.assertEqual(iso(timestamp('2026-10-15T12:00:00.123-0400')), '2026-10-15T16:00:00.123000Z')
        self.assertEqual(timestamp('2026-10-15T12:00:00.123+0000'), timestamp('2026-10-15T12:00:00.123+00:00'))

    def test_normalization_preserves_native_instant_and_window_boundaries(self):
        boundary = timestamp('2026-10-15T12:00:00Z')
        for native in ('2026-10-15T12:00:00.000001Z', '2026-10-15T12:00:00.926+0000',
                       '2026-10-15T08:00:00.123456-0400'):
            normalized = timestamp(iso(timestamp(native)))
            self.assertEqual(normalized, timestamp(native))
            self.assertGreater(normalized, boundary)
        self.assertEqual(iso(boundary), '2026-10-15T12:00:00Z')

    def test_dates_cannot_inject_control_or_markdown_separators(self):
        for value in ('2026-10-15\n12:00:00+00:00', '2026-10-15*12:00:00+00:00',
                      '2026-10-15T12:00:00', '2026-10-15T12:00:00Z\n# Forged'):
            with self.assertRaises(ValueError):
                timestamp(value)


if __name__ == '__main__':
    unittest.main()
