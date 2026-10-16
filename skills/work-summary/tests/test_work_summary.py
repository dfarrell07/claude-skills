#!/usr/bin/env python3
"""Offline regression tests: real Git repositories and a deterministic gh stub."""

import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SKILL = Path(__file__).resolve().parents[1]
COLLECT = SKILL / "scripts/work-summary.sh"
RENDER = SKILL / "scripts/render-report.sh"


class WorkSummaryTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="work-summary-test.")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.home = self.root / "home"
        self.home.mkdir()
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.env = {
            **os.environ,
            "HOME": str(self.home),
            "PATH": f"{self.bin}:{os.environ['PATH']}",
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_CONFIG_GLOBAL": str(self.home / ".gitconfig"),
            "GH_LOG": str(self.root / "gh.log"),
        }
        # Tests never use host credentials or the real GitHub CLI.
        gh = self.bin / "gh"
        gh.write_text(f"#!{sys.executable}\n" + '''
import json, os, subprocess, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\\n")
if args[:2] == ["auth", "status"]:
    sys.exit(1 if os.environ.get("GH_FAIL") == "auth" else 0)
if args[:2] == ["api", "user"]:
    if os.environ.get("GH_FAIL") == "identity":
        sys.exit(1)
    print("test-user")
    sys.exit(0)
kind = args[1]
if os.environ.get("GH_FAIL") == kind:
    print("API unavailable", file=sys.stderr)
    sys.exit(1)
item = {"number": 7, "title": "Fix checks", "state": "MERGED",
        "repository": {"nameWithOwner": "org/project"},
        "createdAt": "2026-01-01T00:00:00Z", "closedAt": None,
        "updatedAt": "2026-10-15T00:00:00Z",
        "url": "https://github.com/org/project/pull/7"}
data = [] if kind == "issues" else [item]
if os.environ.get("GH_CAP"):
    data = [dict(item, number=n) for n in range(1000)]
query = args[args.index("--jq") + 1]
result = subprocess.run(["jq", query], input=json.dumps(data), text=True,
                        stdout=subprocess.PIPE, check=True)
print(result.stdout)
''')
        gh.chmod(0o755)

    def run_script(self, script, *args, check=True):
        return subprocess.run(
            ["bash", str(script), *map(str, args)],
            cwd=self.home, env=self.env, text=True, capture_output=True, check=check,
        )

    def git(self, repo, *args):
        return subprocess.run(
            ["git", "-C", str(repo), *args], env=self.env,
            text=True, capture_output=True, check=True,
        ).stdout.strip()

    def repo(self, relative, message="Fix checks", email="me@example.test"):
        repo = self.home / relative
        repo.mkdir(parents=True)
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.name", "Test User")
        self.git(repo, "config", "user.email", email)
        self.git(repo, "commit", "--allow-empty", "-qm", message)
        return repo

    def capture(self, *roots, days="7"):
        return json.loads(self.run_script(COLLECT, days, *roots).stdout)

    def render(self, data):
        capture = self.root / "data.json"
        capture.write_text(json.dumps(data))
        return self.run_script(RENDER, capture)

    def test_query_window_and_state_normalization(self):
        data = self.capture()
        self.assertEqual(data["github"]["prs"][0]["state"], "merged")
        self.assertEqual(data["metadata"]["github_status"], "ok")
        calls = [json.loads(line) for line in (self.root / "gh.log").read_text().splitlines()]
        searches = [args for args in calls if args[0] == "search"]
        self.assertEqual(len(searches), 2)
        for args in searches:
            self.assertIn("--sort=updated", args)
            self.assertIn("--order=desc", args)
            self.assertIn("--author=test-user", args)
            self.assertIn("--updated=" + data["metadata"]["since_at"] + ".." + data["metadata"]["to_at"], args)
            self.assertEqual(args[args.index("--limit") + 1], "1000")

    def test_deep_clones_worktrees_and_overlapping_roots(self):
        repo = self.repo("go/src/github.test/org/project", "Keep | the entire | subject")
        worktree = self.home / "repos/worktree"
        self.git(repo, "worktree", "add", "-qb", "work", str(worktree))
        data = self.capture(self.home / "go/src", repo, worktree)
        self.assertEqual(data["metadata"]["repos_searched"], 1)
        self.assertEqual(len(data["commits"]), 1)
        self.assertEqual(len(self.capture(worktree)["commits"]), 1)
        self.assertEqual(data["commits"][str(repo)][0]["message"], "Keep | the entire | subject")

    def test_default_roots_and_enclosing_repository(self):
        repo = self.repo("go/src/github.test/org/project")
        sibling = self.repo("repos/other")
        data = self.capture()
        self.assertEqual(set(data["commits"]), {str(repo), str(sibling)})
        nested = repo / "subdirectory"
        nested.mkdir()
        self.assertEqual(set(self.capture(nested)["commits"]), {str(repo)})

    def test_same_names_and_unmerged_branches_and_local_emails(self):
        first = self.repo("one/project", "First", email="first@example.test")
        second = self.repo("two/project", "Second", email="second@example.test")
        head = self.git(first, "rev-parse", "HEAD")
        self.git(first, "checkout", "-qb", "feature")
        self.git(first, "commit", "--allow-empty", "-qm", "Branch work")
        self.git(first, "checkout", "--detach", head)
        self.git(second, "-c", "user.email=other@example.test", "commit", "--allow-empty", "-qm", "Other author")
        data = self.capture(first, second)
        self.assertEqual(set(data["commits"]), {str(first), str(second)})
        self.assertEqual({c["message"] for c in data["commits"][str(first)]}, {"First", "Branch work"})
        self.assertEqual([c["message"] for c in data["commits"][str(second)]], ["Second"])

    def test_old_commits_excluded(self):
        repo = self.repo("old")
        self.env.update(GIT_AUTHOR_DATE="2000-01-01T00:00:00Z", GIT_COMMITTER_DATE="2000-01-01T00:00:00Z")
        self.git(repo, "commit", "--allow-empty", "-qm", "Old")
        messages = [c["message"] for c in self.capture(repo)["commits"][str(repo)]]
        self.assertEqual(messages, ["Fix checks"])

    def test_partial_and_unavailable_sources_stay_visible(self):
        for failure, status in [("auth", "unavailable"), ("identity", "unavailable"), ("prs", "partial"), ("issues", "partial")]:
            with self.subTest(failure=failure):
                self.env["GH_FAIL"] = failure
                data = self.capture()
                self.assertEqual(data["metadata"]["github_status"], status)
                self.assertTrue(data["metadata"]["warnings"])
                report = self.render(data).stdout
                self.assertIn(data["metadata"]["warnings"][0], report)

    def test_search_cap_is_reported(self):
        self.env["GH_CAP"] = "1"
        data = self.capture()
        self.assertEqual(data["metadata"]["github_status"], "partial")
        self.assertEqual(len(data["metadata"]["warnings"]), 2)

    def test_missing_author_is_a_gap(self):
        repo = self.repo("missing-author")
        self.git(repo, "config", "--unset", "user.email")
        data = self.capture(repo)
        self.assertEqual(data["commits"], {})
        self.assertEqual(data["metadata"]["repos_skipped"], 1)
        self.assertTrue(data["metadata"]["warnings"])

    def test_invalid_days_fail_and_leading_zeroes_work(self):
        for days in ["0", "-1", "seven", "1.5", "7; echo bad"]:
            with self.subTest(days=days):
                result = self.run_script(COLLECT, days, check=False)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
        self.assertEqual(self.capture(days="007")["metadata"]["days"], 7)

    def test_raw_and_enriched_reports(self):
        data = self.capture()
        data["github"]["prs"] += [dict(data["github"]["prs"][0], number=8, state="closed")]
        self.assertIn("1 merged, 0 open, 1 closed without merge", self.render(data).stdout)
        data["analysis"] = {"themes": [{"name": "CI", "description": "Fix checks", "items": []}], "in_progress": []}
        report = self.render(data).stdout
        self.assertIn("**CI**: Fix checks", report)
        self.assertIn("No matching local commits found.", report)

    def test_renderer_requires_explicit_valid_capture(self):
        result = self.run_script(RENDER, check=False)
        self.assertNotEqual(result.returncode, 0)
        for value in ["{bad json", "{}", '{"metadata": null}']:
            capture = self.root / "bad.json"
            capture.write_text(value)
            result = self.run_script(RENDER, capture, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")


if __name__ == "__main__":
    unittest.main()
