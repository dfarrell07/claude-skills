"""Native timestamp compatibility and machine-output safety."""

from pathlib import Path
import sys
import unittest

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'scripts'))
from evidence import iso, timestamp  # noqa: E402


class EvidenceTest(unittest.TestCase):
    def test_native_jira_offsets_work_on_minimum_python(self):
        self.assertEqual(iso(timestamp('2026-10-15T12:00:00.123+0000')), '2026-10-15T12:00:00Z')
        self.assertEqual(iso(timestamp('2026-10-15T12:00:00.123-0400')), '2026-10-15T16:00:00Z')
        self.assertEqual(timestamp('2026-10-15T12:00:00.123+0000'), timestamp('2026-10-15T12:00:00.123+00:00'))

    def test_dates_cannot_inject_control_or_markdown_separators(self):
        for value in ('2026-10-15\n12:00:00+00:00', '2026-10-15*12:00:00+00:00',
                      '2026-10-15T12:00:00', '2026-10-15T12:00:00Z\n# Forged'):
            with self.assertRaises(ValueError):
                timestamp(value)


if __name__ == '__main__':
    unittest.main()
