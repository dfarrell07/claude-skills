#!/usr/bin/env python3
"""Offline regression tests: real Git repositories and a deterministic gh stub."""

import copy
import json
import os
import shutil
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
        self.root = Path(self.temp.name).resolve()
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
        gh.write_text(f"#!{sys.executable}\n" + r'''
from datetime import datetime, timezone
import json, os, sys
args = sys.argv[1:]
with open(os.environ["GH_LOG"], "a") as log:
    log.write(json.dumps(args) + "\n")
if args[-1] == "user":
    if os.environ.get("GH_FAIL") in ("auth", "identity"):
        sys.exit(1)
    print(json.dumps({"login": os.environ.get("GH_LOGIN", "test-user")}))
    sys.exit(0)
if "search/commits" in args:
    email=os.environ.get("GH_ALIAS_EMAIL")
    items=[] if not email else [{"sha":"a"*40,"html_url":"https://github.com/org/project/commit/"+"a"*40,
                                "author":{"login":os.environ.get("GH_ALIAS_LOGIN","test-user")},
                                "commit":{"author":{"email":email}}}]
    print(json.dumps({"total_count":len(items),"incomplete_results":False,"items":items}))
    sys.exit(0)
if "graphql" in args:
    print(json.dumps({"data":{"search":{"issueCount":0,"nodes":[],"pageInfo":{"hasNextPage":False,"endCursor":None}}}}))
    sys.exit(0)
if "search/issues" not in args:
    print("[]")
    sys.exit(0)
query = next(value[2:] for value in args if value.startswith("q="))
if ".." not in query:
    print(json.dumps({"total_count":0,"incomplete_results":False,"items":[]}))
    sys.exit(0)
kind = "prs" if "is:pr " in query else "issues"
page = int(next(value[5:] for value in args if value.startswith("page=")))
if os.environ.get("GH_FAIL") == kind and page == int(os.environ.get("GH_FAIL_PAGE", "1")):
    print("API unavailable", file=sys.stderr)
    sys.exit(1)
mode = os.environ.get("GH_MODE", "")
if mode == "malformed":
    print("{bad json")
    sys.exit(0)
window = query.split("updated:")[1].split("..")
start, end = [datetime.fromisoformat(value.replace("Z", "+00:00")) for value in window]
updated = (start + (end - start) / 2).isoformat(timespec="seconds").replace("+00:00", "Z")
host = args[args.index("--hostname") + 1]
total = int(os.environ.get("GH_TOTAL", "1" if kind == "prs" else "0"))
if os.environ.get("GH_CAP"):
    total = 1001
rows = []
for index in range((page-1)*100, min(page*100, total)):
    number = index + 7
    route = "pull" if kind == "prs" else "issues"
    state = os.environ.get("GH_STATE", "merged" if kind == "prs" else "closed")
    item = {"number": number, "title": "Fix checks", "state": "open" if state == "open" else "closed",
            "html_url": f"https://{host}/org/project/{route}/{number}",
            "created_at": "2020-01-01T00:00:00Z", "updated_at": updated,
            "closed_at": None if state == "open" else updated}
    if kind == "prs":
        item["pull_request"] = {"merged_at": updated if state == "merged" else None}
    rows.append(item)
# The CLI cap check exercises reported coverage, while the direct Github tests
# traverse all 1,000 rows without thousands of unrelated subprocess launches.
if os.environ.get("GH_CAP"):
    rows = rows[:1]
if mode == "duplicate" and len(rows) > 1:
    rows[1] = rows[0]
if mode == "invalid_item" and rows:
    rows[0]["html_url"] = "javascript:alert(1)"
if mode == "out_of_window" and rows:
    rows[0]["updated_at"] = "2000-01-01T00:00:00Z"
if mode == "count_mismatch":
    rows = []
if mode == "drift" and page > 1:
    total += 1
print(json.dumps({"total_count": total, "incomplete_results": mode == "incomplete", "items": rows}))
''')
        gh.chmod(0o755)
        # An absent Jira integration is deterministic even on a signed-in developer host.
        acli = self.bin / "acli"
        acli.write_text("#!/bin/sh\nexit 1\n")
        acli.chmod(0o755)

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
        searches = [args for args in calls if "search/issues" in args and any(".." in v for v in args)]
        self.assertEqual(len(searches), 2)
        for args in searches:
            self.assertIn("sort=updated", args)
            self.assertIn("order=desc", args)
            query = next(value[2:] for value in args if value.startswith("q="))
            self.assertIn("author:test-user", query)
            self.assertIn("updated:" + data["metadata"]["since_at"] + ".." + data["metadata"]["to_at"], query)
            self.assertIn("per_page=100", args)
            self.assertIn("page=1", args)

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
                self.assertIn("Coverage Gaps", report)

    def test_search_cap_is_reported(self):
        self.env["GH_CAP"] = "1"
        data = self.capture()
        self.assertEqual(data["metadata"]["github_status"], "partial")
        self.assertEqual(len([w for w in data["metadata"]["warnings"] if "exceeds the 1000-result cap" in w]), 2)

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
        data["github"]["prs"] += [dict(data["github"]["prs"][0], number=8, state="closed", merged_at=None, url="https://github.com/org/project/pull/8")]
        data["metadata"]["github_searches"]["prs"].update(returned_count=2, total_count=2)
        self.assertIn("1 currently merged, 0 open, 1 closed without merge", self.render(data).stdout)
        source = self.root / "source.json"
        source.write_text(json.dumps(data))
        data["analysis"] = {"themes": [{"name": "CI", "description": "Fix checks", "items": [
            {key: data["github"]["prs"][0][key] for key in ("repo", "number", "url")}
        ]}], "in_progress": []}
        capture = self.root / "enriched.json"
        capture.write_text(json.dumps(data))
        report = self.run_script(RENDER, capture, source).stdout
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

    def render_result(self, data, source=None):
        capture = self.root / "candidate.json"
        capture.write_text(json.dumps(data))
        args = [capture]
        if source is not None:
            original = self.root / "original.json"
            original.write_text(json.dumps(source))
            args.append(original)
        return self.run_script(RENDER, *args, check=False)

    def test_server_incompleteness_and_bad_rows_are_visible(self):
        for mode in ["incomplete", "malformed", "invalid_item", "out_of_window", "count_mismatch", "duplicate"]:
            with self.subTest(mode=mode):
                self.env.update(GH_MODE=mode, GH_TOTAL="2")
                data = self.capture()
                self.assertEqual(data["metadata"]["github_status"], "partial")
                self.assertTrue(data["metadata"]["warnings"])
                self.assertLessEqual(len(data["github"]["prs"]), 2)
                self.assertIn("Coverage Gaps", self.render(data).stdout)

    def test_pagination_and_midstream_failure_preserve_evidence(self):
        self.env["GH_TOTAL"] = "101"
        data = self.capture()
        self.assertEqual(len(data["github"]["prs"]), 101)
        self.assertEqual(data["metadata"]["github_searches"]["prs"]["pages"], 2)
        self.env.update(GH_FAIL="prs", GH_FAIL_PAGE="2")
        data = self.capture()
        self.assertEqual(len(data["github"]["prs"]), 100)
        self.assertEqual(data["metadata"]["github_status"], "partial")

    def test_changing_page_totals_are_visible(self):
        self.env.update(GH_TOTAL="101", GH_MODE="drift")
        data = self.capture()
        self.assertEqual(data["metadata"]["github_status"], "partial")
        self.assertTrue(any("changed while paging" in warning for warning in data["metadata"]["warnings"]))

    def test_managed_user_login_is_preserved_and_queried(self):
        self.env["GH_LOGIN"] = "mona-cat_octo"
        data = self.capture()
        self.assertEqual(data["metadata"]["github_user"], "mona-cat_octo")
        self.assertEqual(data["metadata"]["github_status"], "ok")
        self.assertEqual(len(data["github"]["prs"]), 1)
        calls = [json.loads(line) for line in (self.root / "gh.log").read_text().splitlines()]
        queries = [value for args in calls for value in args if value.startswith("q=") and ".." in value]
        self.assertEqual(len(queries), 2)
        self.assertTrue(all("author:mona-cat_octo" in query for query in queries))

    def test_enterprise_host_is_used_for_identity_and_search(self):
        self.env["GH_HOST"] = "git.example.test"
        data = self.capture()
        self.assertEqual(data["metadata"]["github_host"], "git.example.test")
        self.assertTrue(data["github"]["prs"][0]["url"].startswith("https://git.example.test/"))
        self.assertIn("https://git.example.test/", self.render(data).stdout)

    def test_missing_explicit_root_is_a_coverage_gap(self):
        data = self.capture(self.home / "does-not-exist")
        self.assertEqual(data["metadata"]["local_status"], "partial")
        self.assertTrue(data["metadata"]["warnings"])
        self.assertEqual(data["commits"], {})

    def test_invalid_git_marker_is_a_coverage_gap(self):
        root = self.home / "broken"
        root.mkdir()
        (root / ".git").write_text("gitdir: /does/not/exist")
        data = self.capture(root)
        self.assertEqual(data["metadata"]["local_status"], "partial")
        self.assertTrue(data["metadata"]["warnings"])

    def test_symlink_roots_and_metacharacter_paths(self):
        repo = self.repo("work with spaces/$literal`text`/project")
        alias = self.home / "alias"
        alias.symlink_to(repo, target_is_directory=True)
        data = self.capture(alias, repo)
        self.assertEqual(data["metadata"]["repos_searched"], 1)
        self.assertEqual(set(data["commits"]), {str(repo)})

    def test_worktree_identities_and_detached_heads(self):
        repo = self.repo("main", "Main work")
        tree = self.home / "other-worktree"
        self.git(repo, "config", "extensions.worktreeConfig", "true")
        self.git(repo, "worktree", "add", "--detach", str(tree))
        self.git(tree, "config", "--worktree", "user.email", "second@example.test")
        self.git(tree, "commit", "--allow-empty", "-qm", "Detached work")
        data = self.capture(repo, tree)
        self.assertEqual(data["metadata"]["repos_searched"], 1)
        self.assertEqual({entry["message"] for entry in data["commits"][str(repo)]}, {"Main work", "Detached work"})
        self.assertEqual(data["metadata"]["repositories"][0]["identities"], ["me@example.test", "second@example.test"])

    def test_unborn_repository_is_empty_without_failure(self):
        repo = self.home / "unborn"
        repo.mkdir()
        self.git(repo, "init", "-q")
        self.git(repo, "config", "user.email", "me@example.test")
        data = self.capture(repo)
        self.assertEqual(data["commits"], {})
        self.assertEqual(data["metadata"]["local_status"], "ok")

    def test_routing_environment_cannot_redirect_scan(self):
        requested = self.repo("requested", "Requested work")
        unrelated = self.repo("unrelated", "Other work")
        self.env["GIT_DIR"] = str(unrelated / ".git")
        self.env["GIT_WORK_TREE"] = str(unrelated)
        data = self.capture(requested)
        self.assertEqual(set(data["commits"]), {str(requested)})

    def test_exact_time_boundaries_and_future_commits(self):
        repo = self.repo("boundaries")
        for date, message in [("2030-01-01T00:00:00Z", "At start"),
                              ("2029-12-31T23:59:59Z", "Before start"),
                              ("2030-01-08T00:00:00Z", "At end"),
                              ("2030-01-08T00:00:01Z", "After end")]:
            self.env.update(GIT_AUTHOR_DATE=date, GIT_COMMITTER_DATE=date)
            self.git(repo, "commit", "--allow-empty", "-qm", message)
        code = ("import sys,json; from datetime import datetime,timezone; "
                "sys.path.insert(0,sys.argv[1]); from work_summary import collect,validate; "
                "data=collect('7',sys.argv[2:],now=datetime(2030,1,8,tzinfo=timezone.utc)); "
                "validate(data); print(json.dumps(data))")
        result = subprocess.run([sys.executable, "-c", code, str(SKILL / "scripts"), str(repo)],
                                env={**self.env, "PYTHONDONTWRITEBYTECODE": "1"}, text=True,
                                capture_output=True, check=True)
        data = json.loads(result.stdout)
        self.assertEqual({item["message"] for item in data["commits"][str(repo)]}, {"At start", "At end"})

    def test_enrichment_requires_original_and_preserves_it(self):
        source = self.capture()
        data = copy.deepcopy(source)
        data["analysis"] = {"themes": [], "in_progress": []}
        self.assertNotEqual(self.render_result(data).returncode, 0)
        self.assertEqual(self.render_result(data, source).returncode, 0)
        data["metadata"]["github_user"] = "invented"
        result = self.render_result(data, source)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_analysis_rejects_forged_and_closed_in_progress_citations(self):
        source = self.capture()
        ref = {key: source["github"]["prs"][0][key] for key in ("repo", "number", "url")}
        for invalid in [dict(ref, repo="invented/repo"), dict(ref, number=999), dict(ref, url="javascript:alert(1)")]:
            data = {**source, "analysis": {"themes": [{"name": "Claim", "description": "Result", "items": [invalid]}],
                                         "in_progress": []}}
            result = self.render_result(data, source)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        data = {**source, "analysis": {"themes": [], "in_progress": [{**ref, "description": "Ongoing"}]}}
        self.assertNotEqual(self.render_result(data, source).returncode, 0)

    def test_analysis_commit_citations_are_verified(self):
        repo = self.repo("local")
        source = self.capture(repo)
        commit = source["commits"][str(repo)][0]
        data = {**source, "analysis": {"themes": [{"name": "Local", "description": "Prepared change",
                 "items": [], "commits": [{"repo": str(repo), "hash": commit["hash"]}]}], "in_progress": []}}
        result = self.render_result(data, source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(commit["hash"][:12], result.stdout)
        data["analysis"]["themes"][0]["commits"][0]["hash"] = "0" * 40
        self.assertNotEqual(self.render_result(data, source).returncode, 0)

    def test_empty_or_malformed_themes_do_not_invent_accomplishments(self):
        source = self.capture()
        for theme in [{"name": "CI", "description": "Uncited", "items": []}, None,
                      {"name": "CI", "description": None, "items": []}]:
            data = {**source, "analysis": {"themes": [theme], "in_progress": []}}
            result = self.render_result(data, source)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")
        data = {**source, "analysis": {"themes": [], "in_progress": []}}
        self.assertIn("No evidence-backed accomplishments", self.render_result(data, source).stdout)

    def test_untrusted_titles_and_subjects_cannot_change_report_structure(self):
        repo = self.repo("text", "A [link](javascript:bad) <script> & `code` | tail")
        data = self.capture(repo)
        data["github"]["prs"][0]["title"] = "Title\n\n# Forged heading <script>\x1b[31m\u202e"
        report = self.render(data).stdout
        self.assertNotIn("\n# Forged", report)
        self.assertNotIn("<script>", report)
        self.assertNotIn("\x1b", report)
        self.assertNotIn("\u202e", report)
        self.assertNotIn("[link](javascript:bad)", report)
        self.assertIn("&lt;script&gt;", report)
        self.assertIn("tail", report)

    def test_gfm_strikethrough_cannot_change_plaintext_meaning(self):
        text = "Shipped ~~not~~ to production"
        repo = self.repo("gfm", text)
        source = self.capture(repo)
        source["github"]["prs"][0]["title"] = text
        ref = {key: source["github"]["prs"][0][key] for key in ("repo", "number", "url")}
        data = {**source, "analysis": {"themes": [{"name": "~~not~~", "description": text,
                 "items": [ref], "commits": [{"repo": str(repo), "hash": source["commits"][str(repo)][0]["hash"]}]}],
                 "in_progress": []}}
        result = self.render_result(data, source)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertNotIn("~~not~~", result.stdout)
        self.assertGreaterEqual(result.stdout.count(r"\~\~not\~\~"), 4)

    def test_schema_rejects_bad_shapes_counts_and_urls_before_output(self):
        source = self.capture()
        mutations = [lambda d: d["metadata"].update(days=True),
                     lambda d: d["metadata"].update(repos_with_commits=1),
                     lambda d: d["metadata"].update(since_date="2030-01-01"),
                     lambda d: d["metadata"].update(github_status="partial", warnings=[]),
                     lambda d: d["github"]["prs"].append(None),
                     lambda d: d["github"]["prs"][0].update(url="https://evil.test/org/project/pull/7"),
                     lambda d: d["github"]["prs"][0].update(number=True),
                     lambda d: d.update(commits={"relative/path": []})]
        for mutate in mutations:
            with self.subTest(mutate=mutate):
                data = copy.deepcopy(source)
                mutate(data)
                result = self.render_result(data)
                self.assertNotEqual(result.returncode, 0)
                self.assertEqual(result.stdout, "")
                self.assertNotIn("Traceback", result.stderr)

    def test_strict_json_rejects_duplicate_keys_multiple_captures_and_constants(self):
        source = json.dumps(self.capture())
        invalid = [source + "\n" + source, source.replace('"days": 7', '"days": 7, "days": 7'),
                   source.replace('"days": 7', '"days": NaN')]
        for text in invalid:
            capture = self.root / "invalid.json"
            capture.write_text(text)
            result = self.run_script(RENDER, capture, check=False)
            self.assertNotEqual(result.returncode, 0)
            self.assertEqual(result.stdout, "")

    def test_inaccessible_scan_root_preserves_other_sources(self):
        root = self.home / "denied"
        root.mkdir()
        root.chmod(0)
        try:
            if os.access(root, os.R_OK | os.X_OK):
                self.skipTest("This user bypasses directory permissions")
            data = self.capture(root)
            self.assertEqual(data["metadata"]["local_status"], "partial")
            self.assertEqual(len(data["github"]["prs"]), 1)
            self.assertTrue(data["metadata"]["warnings"])
        finally:
            root.chmod(0o700)

    def test_captured_tips_can_replay_before_later_branch_changes(self):
        repo = self.repo("snapshot", "First")
        data = self.capture(repo)
        tips = data["metadata"]["repositories"][0]["tips"]
        first = self.git(repo, "rev-parse", "HEAD")
        self.assertIn(first, tips)
        self.git(repo, "commit", "--allow-empty", "-qm", "Later")
        replay = self.git(repo, "log", *tips, "--format=%H")
        self.assertEqual(replay.splitlines(), [first])
        self.assertEqual([commit["hash"] for commit in data["commits"][str(repo)]], [first])

    def test_mailmap_does_not_change_raw_author_scope(self):
        for direction in ("own-away", "other-to-own"):
            with self.subTest(direction=direction):
                repo = self.repo(direction, "Own work")
                self.git(repo, "config", "log.mailmap", "true")
                if direction == "own-away":
                    mapping = "Mapped <canonical@example.test> <me@example.test>\n"
                else:
                    self.git(repo, "-c", "user.email=other@example.test", "commit", "--allow-empty", "-qm", "Other work")
                    mapping = "Mapped <me@example.test> <other@example.test>\n"
                (repo / ".mailmap").write_text(mapping)
                data = self.capture(repo)
                self.assertEqual(data["metadata"]["local_status"], "ok")
                self.assertEqual([item["message"] for item in data["commits"].get(str(repo), [])], ["Own work"])

    def test_signed_commits_ignore_signature_display_configuration(self):
        keygen = shutil.which("ssh-keygen")
        if not keygen:
            self.skipTest("ssh-keygen is required for a disposable SSH-signed commit")
        repo = self.repo("signed", "Initial work")
        key = self.root / "signing-key"
        subprocess.run([keygen, "-q", "-t", "ed25519", "-N", "", "-f", str(key)],
                       env=self.env, check=True, capture_output=True)
        allowed = self.root / "allowed-signers"
        allowed.write_text("me@example.test " + key.with_suffix(".pub").read_text())
        self.git(repo, "config", "gpg.format", "ssh")
        self.git(repo, "config", "user.signingkey", str(key))
        self.git(repo, "config", "gpg.ssh.allowedSignersFile", str(allowed))
        self.git(repo, "config", "log.showSignature", "true")
        self.git(repo, "commit", "-S", "--allow-empty", "-qm", "Signed work")
        data = self.capture(repo)
        self.assertEqual(data["metadata"]["local_status"], "ok")
        self.assertEqual({item["message"] for item in data["commits"].get(str(repo), [])}, {"Initial work", "Signed work"})

    def test_log_encoding_configuration_preserves_unicode_subjects(self):
        repo = self.repo("latin", "Réparer checks café")
        self.git(repo, "config", "i18n.logOutputEncoding", "ISO-8859-1")
        data = self.capture(repo)
        self.assertEqual(data["metadata"]["local_status"], "ok")
        self.assertEqual(data["commits"][str(repo)][0]["message"], "Réparer checks café")

    def test_trailing_whitespace_repository_and_worktree_paths(self):
        repo = self.repo("repository ", "Spaced path")
        tree = self.home / "worktree "
        self.git(repo, "worktree", "add", "-qb", "spaced", str(tree))
        data = self.capture(repo, tree)
        self.assertEqual(data["metadata"]["local_status"], "ok")
        self.assertEqual(set(data["commits"]), {str(repo)})
        self.assertEqual(data["metadata"]["repositories"][0]["paths"], sorted([str(repo), str(tree)]))

    def test_malformed_enclosing_marker_is_a_coverage_gap(self):
        parent = self.home / "broken-enclosing"
        sub = parent / "subdirectory"
        sub.mkdir(parents=True)
        (parent / ".git").write_text("gitdir: /does/not/exist")
        data = self.capture(sub)
        self.assertEqual(data["metadata"]["local_status"], "partial")
        self.assertTrue(data["metadata"]["warnings"])
        self.assertEqual(len(data["github"]["prs"]), 1)

    def test_inaccessible_enclosing_repository_is_a_coverage_gap(self):
        repo = self.repo("denied-enclosing")
        sub = repo / "subdirectory"
        sub.mkdir()
        marker = repo / ".git"
        original_mode = marker.stat().st_mode
        marker.chmod(0)
        try:
            if os.access(marker, os.R_OK | os.X_OK):
                self.skipTest("This user bypasses directory permissions")
            data = self.capture(sub)
            self.assertEqual(data["metadata"]["local_status"], "partial")
            self.assertEqual(data["commits"], {})
            self.assertTrue(data["metadata"]["warnings"])
        finally:
            marker.chmod(original_mode)

    def test_sha256_repository_keeps_full_commit_identity(self):
        repo = self.home / "sha256"
        repo.mkdir()
        self.git(repo, "init", "-q", "--object-format=sha256")
        self.git(repo, "config", "user.name", "Test User")
        self.git(repo, "config", "user.email", "me@example.test")
        self.git(repo, "commit", "--allow-empty", "-qm", "SHA-256 work")
        data = self.capture(repo)
        self.assertEqual(data["metadata"]["local_status"], "ok")
        self.assertEqual(len(data["commits"][str(repo)][0]["hash"]), 64)
        self.assertEqual(data["commits"][str(repo)][0]["hash"], self.git(repo, "rev-parse", "HEAD"))

    def test_top_level_and_deep_workspace_clones_are_discovered(self):
        top = self.repo("laptop-setup", "Top-level work")
        deep = self.repo("backup/a/b/c/d/e/f/project", "Deep backup work")
        data = self.capture()
        self.assertEqual(set(data["commits"]), {str(top), str(deep)})

    def test_registered_worktree_outside_explicit_roots_supplies_identity(self):
        repo = self.repo("workspace/main", "Main work")
        tree = self.root / "external-worktree"
        self.git(repo, "config", "extensions.worktreeConfig", "true")
        self.git(repo, "worktree", "add", "--detach", str(tree))
        self.git(tree, "config", "--worktree", "user.email", "second@example.test")
        self.git(tree, "commit", "--allow-empty", "-qm", "External work")
        data = self.capture(repo)
        self.assertEqual({item["message"] for item in data["commits"][str(repo)]}, {"Main work", "External work"})
        self.assertIn(str(tree), data["metadata"]["repositories"][0]["paths"])

    def test_missing_registered_worktree_is_visible(self):
        repo = self.repo("registered")
        tree = self.root / "removed-tree"
        self.git(repo, "worktree", "add", "--detach", str(tree))
        shutil.rmtree(tree)
        data = self.capture(repo)
        self.assertEqual(data["metadata"]["local_status"], "partial")
        self.assertTrue(any("Registered worktree is missing" in warning for warning in data["metadata"]["warnings"]))
        self.assertTrue(any(not item["exists"] for item in data["metadata"]["repositories"][0]["worktrees"]))

    def test_hidden_backup_and_symlink_workspace_cycles(self):
        repo = self.repo(".backups/project", "Hidden backup work")
        (self.home / "cycle").symlink_to(self.home, target_is_directory=True)
        alias = self.home / "external-link"
        external = self.root / "outside"
        external.mkdir()
        alias.symlink_to(external, target_is_directory=True)
        self.git(external, "init", "-q")
        self.git(external, "config", "user.name", "Test User")
        self.git(external, "config", "user.email", "me@example.test")
        self.git(external, "commit", "--allow-empty", "-qm", "Linked work")
        data = self.capture()
        self.assertEqual(set(data["commits"]), {str(repo), str(external)})

    def test_bare_backup_is_discovered(self):
        source = self.repo("source", "Bare backup work")
        bare = self.home / "backup.git"
        subprocess.run(["git", "clone", "--bare", str(source), str(bare)], env=self.env, check=True, capture_output=True)
        self.git(bare, "config", "user.email", "me@example.test")
        data = self.capture(bare)
        self.assertEqual(data["commits"][str(bare)][0]["message"], "Bare backup work")
        self.assertTrue(data["metadata"]["repositories"][0]["bare"])

    def test_backup_ref_and_noncommit_tree_are_retained(self):
        repo = self.repo("ref-backup", "Snapshot work")
        sha = self.git(repo, "rev-parse", "HEAD")
        tree = self.git(repo, "rev-parse", "HEAD^{tree}")
        self.git(repo, "update-ref", "refs/heads/summary-bak0", sha)
        self.git(repo, "update-ref", "refs/codex/reviewed-tree", tree)
        data = self.capture(repo)
        self.assertTrue(any("refs/heads/summary-bak0" in item["refs"] for item in data["branches"]))
        snapshot = next(item for item in data["branches"] if "refs/codex/reviewed-tree" in item["refs"])
        self.assertEqual(snapshot["object_type"], "tree")
        self.assertEqual(snapshot["attribution"], "undated_noncommit_snapshot")
        self.assertEqual(snapshot["pr_status"], "not_applicable")

    def test_remote_credentials_are_not_captured(self):
        repo = self.repo("remote")
        self.git(repo, "remote", "add", "origin", "https://secret-token:password@github.com/org/project.git")
        data = self.capture(repo)
        self.assertNotIn("secret-token", json.dumps(data))
        self.assertNotIn("password", json.dumps(data))
        self.assertEqual(data["metadata"]["repositories"][0]["remotes"][0]["repo"], "org/project")

    def test_alias_needs_native_account_attribution(self):
        repo = self.repo("alias", "Configured work")
        self.git(repo, "-c", "user.email=historical@example.test", "commit", "--allow-empty", "-qm", "Historical alias")
        unverified = self.capture(repo)
        self.assertEqual(len(unverified["commits"][str(repo)]), 1)
        self.assertEqual(len(unverified["unattributed_commits"]), 1)
        self.env["GH_ALIAS_EMAIL"] = "historical@example.test"
        verified = self.capture(repo)
        self.assertEqual(len(verified["commits"][str(repo)]), 2)
        self.assertTrue(any(item["source"] == "github_commit_account" for item in verified["metadata"]["author_aliases"]))
        self.env["GH_ALIAS_LOGIN"] = "someone-else"
        wrong_actor = self.capture(repo)
        self.assertEqual(len(wrong_actor["commits"][str(repo)]), 1)

    def test_author_and_committer_dates_are_retained_separately(self):
        repo = self.repo("rebase", "Initial")
        self.env["GIT_AUTHOR_DATE"] = "2000-01-01T00:00:00Z"
        self.git(repo, "commit", "--allow-empty", "-qm", "Rebased old change")
        data = self.capture(repo)
        item = next(item for item in data["commits"][str(repo)] if item["message"] == "Rebased old change")
        self.assertEqual(item["authored_at"], "2000-01-01T00:00:00Z")
        self.assertNotEqual(item["committed_at"], item["authored_at"])

    def test_collection_does_not_write_skill_bytecode(self):
        copied = self.root / "installed-scripts"
        shutil.copytree(SKILL / "scripts", copied, ignore=shutil.ignore_patterns("__pycache__"))
        repo = self.repo("no-bytecode")
        self.run_script(copied / "work-summary.sh", "7", repo)
        self.assertEqual(list(copied.rglob("__pycache__")), [])

    def test_additional_context_can_support_citations_without_becoming_weekly_updates(self):
        data = self.capture()
        item = copy.deepcopy(data["github"]["prs"][0])
        item.update(number=77, url="https://github.com/org/project/pull/77", state="open",
                    updated_at="2020-01-01T00:00:00Z", closed_at=None, merged_at=None,
                    roles=["open_authored_prs"], observed_at=data["metadata"]["to_at"])
        data["github"]["context_prs"] = [item]
        enriched = copy.deepcopy(data)
        enriched["analysis"] = {"themes": [], "in_progress": [{"repo":item["repo"], "number":77,
                                    "url":item["url"], "description":"Older work under review"}]}
        source = self.root / "source.json"
        source.write_text(json.dumps(data))
        report = self.root / "enriched.json"
        report.write_text(json.dumps(enriched))
        output = self.run_script(RENDER, report, source).stdout
        self.assertIn("Older work under review", output)
        self.assertIn("older open work", output)
        enriched["github"]["context_prs"][0]["title"] = "Altered"
        report.write_text(json.dumps(enriched))
        self.assertNotEqual(self.run_script(RENDER, report, source, check=False).returncode, 0)

    def test_jira_citations_and_restricted_details(self):
        data = self.capture()
        item = {"key":"EXAMPLE-123", "url":"https://jira.example.test/browse/EXAMPLE-123",
                "title":"Private title must stay hidden", "status":"In Progress", "status_category":"indeterminate",
                "roles":["assigned"], "sensitive":True}
        data["jira"] = {"metadata":{"status":"ok", "site":"jira.example.test", "identity_verified":True,
                                    "account_id":"test-account"}, "issues":[item], "activity":[{
                    "key":item["key"], "comment_id":"42", "url":item["url"]+"?focusedCommentId=42", "kind":"comment_created",
                    "actor_account_id":"test-account", "occurred_at":data["metadata"]["to_at"]}]}
        report = self.render(data).stdout
        self.assertIn("Restricted ticket", report)
        self.assertNotIn("Private title must stay hidden", report)
        broken = copy.deepcopy(data)
        broken["jira"]["metadata"]["account_id"] = None
        broken["jira"]["activity"][0]["actor_account_id"] = None
        invalid = self.root / "missing-principal.json"
        invalid.write_text(json.dumps(broken))
        self.assertNotEqual(self.run_script(RENDER, invalid, check=False).returncode, 0)
        enriched = copy.deepcopy(data)
        citation = {"key":item["key"], "url":item["url"]}
        enriched["analysis"] = {"themes":[{"name":"Investigation", "description":"Investigated reported behavior",
                                          "items":[], "jira":[citation]}],
                               "in_progress":[{**citation, "description":"Investigation continues"}]}
        source = self.root / "jira-source.json"
        source.write_text(json.dumps(data))
        capture = self.root / "jira-enriched.json"
        capture.write_text(json.dumps(enriched))
        self.assertIn("Investigated reported behavior", self.run_script(RENDER, capture, source).stdout)
        enriched["analysis"]["themes"][0]["jira"][0]["key"] = "EXAMPLE-999"
        capture.write_text(json.dumps(enriched))
        self.assertNotEqual(self.run_script(RENDER, capture, source, check=False).returncode, 0)

    def test_work_lists_and_analysis_show_both_jira_and_pr_links(self):
        data = self.capture()
        pr = data["github"]["prs"][0]
        pr["body"] = "References EXAMPLE-123"
        issue = {"key":"EXAMPLE-123", "url":"https://jira.example.test/browse/EXAMPLE-123",
                 "title":"Synthetic linked work", "status":"Active", "status_category":"indeterminate",
                 "roles":["referenced_by_github"], "sensitive":False,
                 "pr_links":[{"url":pr["url"], "proof":"github_body_key", "evidence_url":pr["url"], "sensitive":False}]}
        pr["jira_links"] = [{"key":issue["key"], "url":issue["url"], "proof":"github_body_key",
                              "evidence_url":pr["url"], "sensitive":False}]
        data["jira"] = {"metadata":{"status":"ok", "site":"jira.example.test"}, "issues":[issue], "activity":[]}
        report = self.render(data).stdout
        github_line = next(line for line in report.splitlines() if "Fix checks" in line)
        jira_line = next(line for line in report.splitlines() if "Synthetic linked work" in line)
        for line in (github_line, jira_line):
            self.assertIn(pr["url"], line)
            self.assertIn(issue["url"], line)
        enriched = copy.deepcopy(data)
        enriched["analysis"] = {"themes":[{"name":"Linked work", "description":"Investigated behavior",
            "items":[{"repo":pr["repo"], "number":pr["number"], "url":pr["url"]}]}], "in_progress":[]}
        source, capture = self.root / "paired-source.json", self.root / "paired.json"
        source.write_text(json.dumps(data))
        capture.write_text(json.dumps(enriched))
        output = self.run_script(RENDER, capture, source).stdout
        line = next(line for line in output.splitlines() if "Investigated behavior" in line)
        self.assertIn(pr["url"], line)
        self.assertIn(issue["url"], line)

    def test_additional_sections_reject_markdown_injection(self):
        repo = self.repo("validation")
        data = self.capture(repo)
        data["unattributed_commits"] = [{"repo":str(repo), "hash":"`\n# Forged", "author_email":"other@example.test"}]
        capture = self.root / "invalid-lead.json"
        capture.write_text(json.dumps(data))
        result = self.run_script(RENDER, capture, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")
        data["unattributed_commits"] = []
        site = "jira.example.test) **pwned** ("
        data["jira"] = {"metadata":{"status":"ok", "site":site}, "issues":[{
            "key":"EXAMPLE-1", "url":"https://"+site+"/browse/EXAMPLE-1", "title":"Title",
            "status":"Active", "status_category":"indeterminate", "roles":["assigned"], "sensitive":False}], "activity":[]}
        capture.write_text(json.dumps(data))
        result = self.run_script(RENDER, capture, check=False)
        self.assertNotEqual(result.returncode, 0)
        self.assertEqual(result.stdout, "")

    def test_actor_events_validate_identity_time_and_url(self):
        data = self.capture()
        item = data["github"]["prs"][0]
        event = {"repo":item["repo"], "number":item["number"], "item_url":item["url"],
                 "url":item["url"]+"#issuecomment-1", "actor":"test-user", "action":"issue_comment",
                 "at":data["metadata"]["to_at"], "body":"verified"}
        data["github"]["activity"] = [event]
        self.assertIn("Verified GitHub Actor Events (1)", self.render(data).stdout)
        for field, value in (("actor", "someone-else"), ("at", "2000-01-01T00:00:00Z"),
                             ("url", "https://example.test/forged")):
            broken = copy.deepcopy(data)
            broken["github"]["activity"][0][field] = value
            capture = self.root / "broken.json"
            capture.write_text(json.dumps(broken))
            self.assertNotEqual(self.run_script(RENDER, capture, check=False).returncode, 0)
        data["metadata"]["github_user"] = ""
        data["github"]["activity"][0]["actor"] = ""
        capture.write_text(json.dumps(data))
        self.assertNotEqual(self.run_script(RENDER, capture, check=False).returncode, 0)

    def test_collection_does_not_modify_working_tree(self):
        repo = self.repo("read-only")
        (repo / "dirty.txt").write_text("uncommitted")
        before = self.git(repo, "status", "--porcelain=v1")
        self.capture(repo)
        self.assertEqual(self.git(repo, "status", "--porcelain=v1"), before)


if __name__ == "__main__":
    unittest.main()
