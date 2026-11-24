"""Timing rules and parsers in budget.py, on real CLI output captured 2026-11-24 and a fixed clock."""
import json
import os
import shutil
import sys
import tempfile
import unittest
from datetime import datetime, timedelta, timezone

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "scripts"))
import budget as b  # noqa: E402

NOW = datetime(2026, 11, 24, 18, 38, tzinfo=timezone.utc)  # when the fixtures were captured
H = timedelta(hours=1)


def fixture(name):
    with open(os.path.join(HERE, "fixtures", name)) as f:
        return f.read()


def week(left, t):
    return b.Pool("week", left, b.WEEK, NOW + t)


def five(left, t):
    return b.Pool("5h", left, b.H5, NOW + t)


class Pace(unittest.TestCase):
    def test_fresh_pool_is_on_pace(self):
        self.assertAlmostEqual(b.pace(week(100, b.WEEK), NOW), 1.0)

    def test_mid_window_behind_and_ahead(self):
        self.assertAlmostEqual(b.pace(week(40, b.WEEK / 2), NOW), 0.8)
        self.assertAlmostEqual(b.pace(week(60, b.WEEK / 2), NOW), 1.2)

    def test_ratio_climbs_toward_reset(self):
        self.assertAlmostEqual(b.pace(week(30, H), NOW), 50.4)  # 30% left, 1h to go: spend hard

    def test_comparable_across_window_lengths(self):
        month = b.Pool("30d", 50, timedelta(days=30), NOW + timedelta(days=15))
        self.assertAlmostEqual(b.pace(month, NOW), b.pace(week(50, b.WEEK / 2), NOW))

    def test_past_reset_is_a_full_unstarted_pool(self):
        p = week(0, -H)
        self.assertAlmostEqual(b.pace(p, NOW), 1.0)
        self.assertTrue(b.unstarted(p, NOW))


class Assess(unittest.TestCase):
    def test_5h_at_floor_blocks_and_says_when_it_rolls_over(self):
        verdict, r, why = b.assess([five(10, 2 * H), week(90, b.WEEK)], NOW)
        self.assertEqual(verdict, "blocked")
        self.assertIn("rolls over in 2h00m", why)

    def test_5h_near_floor_holds_and_clear_of_it_is_fine(self):
        verdict, r, why = b.assess([five(15, 2 * H), week(90, b.WEEK)], NOW)
        self.assertEqual((verdict, round(r, 2)), ("hold", 0.9))
        self.assertIn("near", why)
        self.assertEqual(b.assess([five(21, 2 * H), week(90, b.WEEK)], NOW)[0], "ok")

    def test_no_release_near_rollover(self):
        self.assertEqual(b.assess([five(5, timedelta(minutes=10))], NOW)[0], "blocked")

    def test_empty_long_pool_blocks(self):
        self.assertEqual(b.assess([week(0, 2 * H)], NOW)[0], "blocked")

    def test_behind_pace_holds(self):
        self.assertEqual(b.assess([week(40, b.WEEK / 2)], NOW)[0], "hold")  # R 0.8
        self.assertEqual(b.assess([week(47, b.WEEK / 2)], NOW)[0], "ok")    # R 0.94: noise, not behind

    def test_first_job_of_the_week_does_not_flip_to_hold(self):
        self.assertEqual(b.assess([week(97, b.WEEK - H)], NOW)[0], "ok")

    def test_worst_long_pool_decides(self):
        _, r, why = b.assess([week(90, b.WEEK), b.Pool("other", 20, b.WEEK, NOW + b.WEEK / 2)], NOW)
        self.assertAlmostEqual(r, 0.4)
        self.assertIn("other", why)

    def test_orchestrator_keeps_a_bigger_5h_floor_and_the_pace_line(self):
        self.assertEqual(b.assess([five(20, 2 * H)], NOW, orchestrator=True)[0], "blocked")
        self.assertEqual(b.assess([week(48, b.WEEK / 2)], NOW, orchestrator=True)[0], "hold")  # R 0.96
        self.assertEqual(b.assess([week(10, 2 * H)], NOW, orchestrator=True)[0], "ok")  # ends near zero


class Order(unittest.TestCase):
    def test_verdict_then_ratio(self):
        got = b.order([("a", "hold", 0.5, ""), ("b", "ok", 1.1, ""), ("c", "blocked", None, ""),
                       ("d", "ok", 3.0, ""), ("e", "not set up", None, "")])
        self.assertEqual([x[0] for x in got], ["d", "b", "a", "c", "e"])


class Local(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.saved = b.HOME, b.subprocess.Popen, dict(os.environ)
        b.HOME = self.tmp

    def tearDown(self):
        b.HOME, b.subprocess.Popen = self.saved[:2]
        os.environ.clear()
        os.environ.update(self.saved[2])
        shutil.rmtree(self.tmp)

    def test_kick_waits_out_its_cooldown(self):
        launched = []
        b.subprocess.Popen = lambda cmd, **k: launched.append(cmd)
        self.assertTrue(b.kick("personal"))
        self.assertFalse(b.kick("personal"))  # rerun right after: the pool still reads unstarted
        self.assertTrue(b.kick("personal2"))
        self.assertEqual(len(launched), 2)

    def test_orchestrator_account(self):
        for env, want in [({"CLAUDE_CONFIG_DIR": f"{self.tmp}/.claude"}, "personal"),
                          ({"CLAUDE_CONFIG_DIR": f"{self.tmp}/.claude-per2"}, "personal2"),
                          ({"CLAUDE_CONFIG_DIR": f"{self.tmp}/.claude-other"}, None),
                          ({"CLAUDE_CONFIG_DIR": f"{self.tmp}/.claude", "CLAUDE_CODE_USE_VERTEX": "true"}, None)]:
            os.environ.pop("CLAUDE_CODE_USE_VERTEX", None)
            os.environ.update(env)
            self.assertEqual(b.orchestrator_account(), want, env)


class Parsers(unittest.TestCase):
    def test_agy(self):
        pools = b.parse_agy(fixture("agy-usage.tsv"))
        self.assertEqual([p.name for p in pools], ["Gemini week", "Gemini 5h", "Claude/GPT week", "Claude/GPT 5h"])
        self.assertEqual([p.window for p in pools], [b.WEEK, b.H5, b.WEEK, b.H5])

    def test_agy_live_case_is_blocked_on_its_5h_pool(self):
        pools = [p for p in b.parse_agy(fixture("agy-usage.tsv")) if b.routed("agy", p)]
        verdict, _, why = b.assess(pools, NOW)
        self.assertEqual(verdict, "blocked")
        self.assertIn("Gemini 5h", why)

    def test_agy_unstarted_pools_and_kick(self):
        pools = b.parse_agy(fixture("agy-usage.tsv"))
        self.assertEqual([p.name for p in pools if b.unstarted(p, NOW)], ["Claude/GPT week", "Claude/GPT 5h"])
        self.assertFalse(b.kicks("agy", pools, NOW))  # Claude/GPT is never routed, so never kicked
        gem = b.Pool("Gemini week", 100, b.WEEK, NOW + b.WEEK)
        self.assertTrue(b.kicks("agy", [gem], NOW))
        self.assertFalse(b.kicks("codex", [gem], NOW))
        self.assertFalse(b.kicks("agy", [b.Pool("Gemini 5h", 100, b.H5, NOW + b.H5)], NOW))  # weekly only

    def test_agy_logged_out(self):
        hint = b.parse_agy(fixture("agy-loggedout.txt"), "personal2")
        self.assertTrue(hint.startswith("logged out"))
        self.assertIn("DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null agy --gemini_dir=~/.gemini-per2", hint)

    def test_codex_30_day_window(self):
        pools = b.parse_codex(json.loads(fixture("codex-ratelimits.json")))
        self.assertEqual(len(pools), 1)
        self.assertEqual((pools[0].name, pools[0].left, pools[0].window), ("30d", 0.0, timedelta(days=30)))

    def test_codex_api_key(self):
        reply = {"error": {"message": "chatgpt authentication required to read rate limits"}}
        self.assertTrue(b.parse_codex(reply).startswith("no usage source"))

    def test_claude_weekly_only(self):
        pools = b.parse_claude(fixture("claude-usage.txt"), NOW)
        self.assertEqual([(p.name, p.left, p.window) for p in pools],
                         [("week all models", 89.0, b.WEEK), ("week Fable", 100.0, b.WEEK)])
        self.assertEqual(pools[0].reset, datetime(2026, 12, 1, 9, 0, tzinfo=timezone.utc))

    def test_a_single_model_limit_does_not_block_the_target(self):
        out = fixture("claude-usage.txt").replace("Current week (Fable): 0% used", "Current week (Fable): 100% used")
        pools = b.parse_claude(out, NOW)
        self.assertEqual([p.model for p in pools], [None, "Fable"])
        self.assertEqual(b.assess([p for p in pools if b.routed("claude", p)], NOW)[0], "ok")
        reply = {"result": {"rateLimitsByLimitId": {"codex": {"primary": {
            "usedPercent": 10, "windowDurationMins": 43200, "resetsAt": 1795716627}}, "x": {"limitName": "gpt-x",
            "primary": {"usedPercent": 100, "windowDurationMins": 10080, "resetsAt": 1795716627}}}}}
        pools = b.parse_codex(reply)
        self.assertEqual([p.name for p in pools if b.routed("codex", p)], ["30d"])
        self.assertEqual(pools[1].name, "gpt-x 7d")

    def test_claude_session_row(self):
        out = "Current session: 70% used · resets 4am (America/New_York)\n"
        (p,) = b.parse_claude(out, NOW)
        self.assertEqual((p.name, p.left, p.window), ("session 5h", 30.0, b.H5))

    def test_claude_retries_a_reply_without_figures(self):
        # 2026-11-24 20:54Z: /usage replied with its header but no usage rows, twice in a row, then worked.
        replies = iter(["You are currently using your subscription\n\nWhat's contributing", "",
                        fixture("claude-usage.txt")])
        run, sleep = b.run, b.time.sleep
        b.run, b.time.sleep = (lambda *a, **k: next(replies)), (lambda s: None)
        try:
            pools = b.claude("personal", NOW)
        finally:
            b.run, b.time.sleep = run, sleep
        self.assertEqual(len(pools), 2)

    def test_claude_without_subscription(self):
        self.assertTrue(b.parse_claude(fixture("claude-usage-nosub.txt"), NOW).startswith("no subscription"))
        self.assertTrue(b.parse_claude("", NOW).startswith("error"))


if __name__ == "__main__":
    unittest.main()
