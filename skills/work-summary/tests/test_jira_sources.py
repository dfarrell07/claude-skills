"""Offline Jira collection tests; a disposable acli executable never uses credentials."""

import copy
from datetime import datetime, timezone
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import threading
import unittest
from unittest.mock import patch


sys.dont_write_bytecode = True
SCRIPTS = Path(__file__).resolve().parents[1] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))
SPEC = importlib.util.spec_from_file_location("work_summary_jira", SCRIPTS / "jira_sources.py")
jira = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(jira)
START = datetime(2026, 1, 8, 12, tzinfo=timezone.utc)
END = datetime(2026, 1, 15, 12, tzinfo=timezone.utc)
ME = {"accountId": "self-id", "emailAddress": "person@example.test"}
OTHER = {"accountId": "other-id", "emailAddress": "other@example.test"}


def linked_prs(result):
    return sorted({link["url"] for item in result["issues"] for link in item.get("pr_links", [])})

def issue(key="TEST-1", category="indeterminate", author=None):
    return {"key": key, "id": key.split("-")[1],
            "self": "https://jira.example.test/rest/api/3/issue/" + key.split("-")[1],
            "fields": {"summary": "Synthetic work " + key, "assignee": copy.deepcopy(author or ME),
                       "reporter": copy.deepcopy(OTHER), "status": {"name": "Closed" if category == "done" else "Active",
                       "statusCategory": {"key": category}}, "labels": []}}


def comment(ident="c1", author=None, created="2026-01-10T12:00:00Z", **extra):
    return {"id": ident, "author": copy.deepcopy(author or ME), "created": created,
            "updated": created, "body": {"type": "doc", "content": [{"type": "text", "text": "Prepared change"}]},
            **extra}


def detail(row, comments=()):
    result = copy.deepcopy(row)
    result["fields"].update(updated="2026-01-12T12:00:00Z", created="2025-01-01T00:00:00Z",
                            security=None, description={"type": "doc", "content": []}, issuelinks=[],
                            comment={"comments": list(comments), "startAt": 0, "maxResults": 100,
                                     "total": len(comments)})
    return result


class JiraSourcesTest(unittest.TestCase):
    def setUp(self):
        temp = tempfile.TemporaryDirectory(prefix="work-summary-jira-test.")
        self.addCleanup(temp.cleanup)
        self.root = Path(temp.name)
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.fixture = self.root / "fixture.json"
        self.log = self.root / "calls.jsonl"
        self.data = {"auth": "✓ Authenticated\n  Site: jira.example.test\n  Email: person@example.test\n",
                     "searches": {"assigned": [issue()], "updated_by": [], "recent": [],
                                  "reporter": [], "creator": [], "former_assignee": [], "watcher": []},
                     "counts": [1, 1], "details": {"TEST-1": detail(issue(), [comment()])}}
        executable = self.bin / "acli"
        executable.write_text(f"#!{sys.executable}\n" + r'''
import json, os, sys
from pathlib import Path
args = sys.argv[1:]
data = json.loads(Path(os.environ["JIRA_FIXTURE"]).read_text())
log = Path(os.environ["JIRA_LOG"])
calls = [json.loads(line) for line in log.read_text().splitlines()] if log.exists() else []
with log.open("a") as stream:
    stream.write(json.dumps(args) + "\n")
def emit(value):
    if isinstance(value, dict) and "_exit" in value:
        sys.stdout.write(value.get("_stdout", ""))
        sys.stderr.write(value.get("_stderr", ""))
        sys.exit(value["_exit"])
    print(json.dumps(value))
if args == ["jira", "auth", "status"]:
    if data.get("auth_exit"):
        sys.exit(1)
    print(data["auth"])
elif args[:3] == ["jira", "workitem", "search"]:
    query = args[args.index("--jql") + 1]
    if query == "assignee = currentUser() ORDER BY key":
        role = "assigned"
    elif "updatedBy(" in query:
        role = "updated_by"
    elif query.startswith("("):
        role = "recent"
    elif query.startswith("reporter"):
        role = "reporter"
    elif query.startswith("creator"):
        role = "creator"
    elif query.startswith("assignee WAS"):
        role = "former_assignee"
    elif query.startswith("watcher"):
        role = "watcher"
    else:
        sys.exit(99)
    if "--count" in args:
        index = sum("--count" in call and query in call for call in calls)
        counts = data["counts"] if role == "assigned" else data.get("role_counts", {}).get(
            role, [len(data["searches"][role])]*2)
        count = counts[min(index, len(counts)-1)]
        if count is None:
            sys.exit(1)
        print("✓ Number of work items in the search: " + str(count))
    else:
        assert "--paginate" in args and "--json" in args
        assert args[args.index("--fields")+1] == "key,summary,status,assignee,reporter,labels"
        emit(data["searches"][role])
elif args[:3] == ["jira", "project", "list"]:
    emit(data.get("projects", {"_exit": 1}))
elif args[:3] == ["jira", "workitem", "view"]:
    emit(data["details"].get(args[3], {"_exit": 1}))
else:
    sys.exit(99)
''')
        executable.chmod(0o755)
        environment = patch.dict(os.environ, {"PATH": f"{self.bin}:{os.environ['PATH']}",
                                             "JIRA_FIXTURE": str(self.fixture), "JIRA_LOG": str(self.log),
                                             "GH_HOST": "github.com",
                                             "PYTHONDONTWRITEBYTECODE": "1"})
        environment.start()
        self.addCleanup(environment.stop)

    def collect(self):
        self.fixture.write_text(json.dumps(self.data))
        self.warnings = []
        return jira.collect_jira(START, END, self.warnings.append)

    def calls(self):
        return [json.loads(line) for line in self.log.read_text().splitlines()]

    def test_all_field_detail_selection_retains_native_custom_pr_proof(self):
        self.data['details']['TEST-1']['fields']['customfield_10875'] = {
            'content': [{'text': '[https://github.com/org/repo/pull/4051|PR]',
                         'marks': [{'attrs': {'href': 'https://github.com/org/repo/pull/4051'}}]}]}
        result = self.collect()
        calls = [call for call in self.calls() if call[:3] == ['jira', 'workitem', 'view']]
        self.assertTrue(calls)
        self.assertTrue(all(call[call.index('--fields') + 1] == '*all' for call in calls))
        self.assertEqual(result['issues'][0]['pr_links'], [{
            'url': 'https://github.com/org/repo/pull/4051', 'proof': 'custom_field',
            'field_id': 'customfield_10875', 'source_kind': 'detail',
            'evidence_url': 'https://jira.example.test/browse/TEST-1', 'sensitive': False}])

    def test_read_only_identity_and_native_activity_with_explicit_scope_gap(self):
        result = self.collect()
        self.assertTrue(result["metadata"]["identity_verified"])
        self.assertEqual(result["metadata"]["account_id"], "self-id")
        self.assertEqual(result["metadata"]["site"], "jira.example.test")
        self.assertEqual(result["issues"][0]["source"], issue())
        event = result["activity"][0]
        self.assertEqual(event["actor_account_id"], "self-id")
        self.assertEqual(event["comment_id"], "c1")
        self.assertEqual(event["kind"], "comment_created")
        self.assertEqual(event["url"], "https://jira.example.test/browse/TEST-1?focusedCommentId=c1")
        self.assertEqual(event["source"], comment())
        self.assertEqual(result["metadata"]["coverage"]["assigned"]["count_after"], 1)
        self.assertEqual(result["metadata"]["status"], "ok")
        for call in self.calls():
            self.assertIn(call[:3], [["jira", "auth", "status"], ["jira", "workitem", "search"],
                                    ["jira", "workitem", "view"], ["jira", "project", "list"]])

    def test_all_projects_get_details_but_old_completed_work_stays_inventory_context(self):
        old = issue("OTHER-2", "done")
        active = issue("OTHER-3", "indeterminate")
        self.data["searches"]["assigned"].extend([old, active])
        self.data["counts"] = [3, 3]
        for row in (old, active):
            self.data["details"][row["key"]] = detail(row)
            self.data["details"][row["key"]]["fields"]["description"] = {
                "text": "Work https://github.com/old/project/pull/37"}
        result = self.collect()
        by_key = {row["key"]: row for row in result["issues"]}
        self.assertEqual(sorted(by_key), ["OTHER-2", "OTHER-3", "TEST-1"])
        self.assertEqual(by_key["OTHER-2"]["detail_status"], "not_selected")
        self.assertTrue(by_key["OTHER-2"]["sensitive"])
        self.assertEqual(by_key["OTHER-3"]["detail_status"], "ok")
        self.assertEqual(by_key["OTHER-3"]["pr_links"][0]["url"], "https://github.com/old/project/pull/37")
        self.assertEqual(sum(call[:3] == ["jira", "workitem", "view"] for call in self.calls()), 2)
        self.assertEqual(result["metadata"]["coverage"]["details"]["skipped_inactive_count"], 1)

    def test_nonassigned_activity_lead_and_old_active_context_are_detailed(self):
        for role, key in [("updated_by", "SIDE-2"), ("recent", "SIDE-6")]:
            row = issue(key, "done" if role == "recent" else "indeterminate", OTHER)
            self.data["searches"][role] = [row]
            self.data["details"][key] = detail(row, [comment(key, ME)])
        result = self.collect()
        self.assertEqual(len(result["issues"]), 3)
        self.assertEqual(len(result["activity"]), 3)
        self.assertTrue(all(row["detail_status"] == "ok" for row in result["issues"]))
        queries = [call[call.index("--jql") + 1] for call in self.calls() if "--jql" in call]
        self.assertFalse(any(query.startswith(("reporter", "creator", "watcher", "assignee WAS")) for query in queries))
        self.assertEqual(result["metadata"]["account_id"], "self-id")

    def test_query_dates_are_widened_and_identity_is_quoted(self):
        self.collect()
        queries = [call[call.index("--jql") + 1] for call in self.calls() if "--jql" in call]
        updated = next(query for query in queries if "updatedBy(" in query)
        self.assertIn('updatedBy("person@example.test", "2026-01-07", "2026-01-16")', updated)
        self.assertTrue(any("assignee WAS currentUser()" in query for query in queries))

    def test_missing_cli_and_failed_auth_are_unavailable(self):
        with patch.object(jira.shutil, "which", return_value=None):
            result = self.collect()
        self.assertEqual(result["metadata"]["status"], "unavailable")
        self.assertTrue(self.warnings)
        self.data["auth_exit"] = True
        self.assertEqual(self.collect()["metadata"]["status"], "unavailable")

    def test_jira_collection_needs_neither_git_nor_github_cli(self):
        # The fixture executable has an absolute interpreter; it is the only
        # executable on PATH, as in a Jira-only execution environment.
        with patch.dict(os.environ, {"PATH": str(self.bin)}):
            self.assertIsNone(jira.shutil.which("gh"))
            self.assertIsNone(jira.shutil.which("git"))
            result = self.collect()
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["issues"][0]["detail_status"], "ok")
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["c1"])
        self.assertTrue(all(call[0] == "jira" for call in self.calls()))

    def test_lost_cli_or_auth_during_reference_expansion_preserves_native_capture(self):
        for failure in (FileNotFoundError("acli disappeared"),
                        jira.subprocess.CalledProcessError(1, "acli", stderr="authentication expired")):
            with self.subTest(failure=type(failure).__name__):
                result = self.collect()
                native_issues, native_events = copy.deepcopy(result["issues"]), copy.deepcopy(result["activity"])
                with patch.object(jira, "command", side_effect=failure):
                    jira.add_referenced_jira(result, ["MENTIONED-2"], START, END, self.warnings.append)
                self.assertEqual(result["issues"], native_issues)
                self.assertEqual(result["activity"], native_events)
                self.assertEqual(result["metadata"]["coverage"]["referenced_by_github"]["status"], "partial")
                self.assertEqual(result["metadata"]["coverage"]["referenced_by_github"]["requested_keys"], ["MENTIONED-2"])

    def test_auth_site_injection_is_rejected(self):
        self.data["auth"] = "Site: https://jira.example.test@evil.example\nEmail: person@example.test\n"
        result = self.collect()
        self.assertEqual(result["metadata"]["status"], "unavailable")
        self.assertFalse(any("search" in call for call in self.calls()))

    def test_provider_backend_self_keeps_authenticated_browser_site_and_identity(self):
        site = "tenant-fixture.atlassian.net"
        backend = "https://jira-prod-synthetic-region-1.prod.atl-paas.net/rest/api/3/issue/1"
        self.data["auth"] = f"Site: {site}\nEmail: person@example.test\n"
        self.data["searches"]["assigned"][0]["self"] = backend
        self.data["details"]["TEST-1"]["self"] = backend
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual(item["url"], f"https://{site}/browse/TEST-1")
        self.assertEqual(item["source"]["self"], backend)
        self.assertEqual(item["detail"]["self"], backend)
        self.assertEqual(item["detail_status"], "ok")
        self.assertTrue(result["metadata"]["identity_verified"])
        self.assertEqual(result["metadata"]["coverage"]["assigned"]["status"], "ok")
        self.assertEqual(len(result["activity"]), 1)

    def test_backend_self_exception_requires_canonical_resource_and_atlassian_context(self):
        site = "tenant-fixture.atlassian.net"
        root = "https://jira-prod-synthetic-region-1.prod.atl-paas.net"
        valid = root + "/rest/api/3/issue/1"
        for url in [valid.replace("https:", "http:"), valid + "?fields=summary", valid + "#fragment",
                    valid.replace("https://", "https://user:secret@"),
                    valid.replace("/issue/1", "/issue/2"), root + "/browse/TEST-1",
                    valid.replace(".atl-paas.net/", ".atl-paas.net.evil.example/"),
                    valid.replace("jira-prod-", "jira-stage-"), valid.replace("/issue/1", "/issue/%31")]:
            with self.subTest(url=url):
                row = issue()
                row["self"] = url
                with self.assertRaises(ValueError):
                    jira._normalize(row, site)
        row = issue()
        row["self"] = valid
        with self.assertRaises(ValueError):
            jira._normalize(row, "jira.example.test")

    def test_four_worker_reads_finish_before_sequential_normalization_and_warnings(self):
        rows = [issue(f"TEST-{n}") for n in range(1, 9)]
        selected = [jira._normalize(row, "jira.example.test") for row in rows]
        metadata = {"site": "jira.example.test", "status": "ok", "account_id": "self-id",
                    "identity_verified": True, "warnings": [], "coverage": {
                        "details": {"status": "ok", "requested_count": 8, "returned_count": 0}}}
        result = {"metadata": metadata, "issues": selected, "activity": []}
        barrier, lock = threading.Barrier(4), threading.Lock()
        active, peak, completed = 0, 0, 0
        main_thread = threading.get_ident()
        warnings, normalized = [], []
        real_normalize = jira._normalize

        def read(args, **kwargs):
            nonlocal active, peak, completed
            self.assertEqual(args[:4], ["acli", "jira", "workitem", "view"])
            with lock:
                active += 1
                peak = max(peak, active)
            try:
                barrier.wait(timeout=10)
                return "{malformed" if args[4] == "TEST-8" else json.dumps(detail(issue(args[4]), [comment()]))
            finally:
                with lock:
                    active -= 1
                    completed += 1

        def normalize(*args):
            self.assertEqual(threading.get_ident(), main_thread)
            self.assertEqual(completed, 8)
            normalized.append(args[0]["key"])
            return real_normalize(*args)

        def warn(message):
            self.assertEqual(threading.get_ident(), main_thread)
            warnings.append(message)

        with patch.object(jira, "command", side_effect=read), patch.object(jira, "_normalize", side_effect=normalize):
            jira._detail_items(result, selected, START, END, warn)
        self.assertEqual(peak, 4)
        self.assertEqual(normalized, [f"TEST-{n}" for n in range(1, 8)])
        self.assertEqual(metadata["coverage"]["details"]["returned_count"], 7)
        self.assertEqual(len(result["activity"]), 7)
        self.assertTrue(warnings)
        self.assertEqual(selected[-1]["detail_status"], "failed")

    def test_account_conflict_or_missing_email_never_guesses_activity_actor(self):
        anonymous = comment("no-account")
        anonymous["author"] = {}
        self.data["details"]["TEST-1"] = detail(issue(), [comment(), anonymous])
        for author in [OTHER, {"accountId": "   "}, {"accountId": "self-id", "emailAddress": ""}]:
            with self.subTest(author=author):
                self.data["searches"]["assigned"] = [issue(author=author)]
                result = self.collect()
                self.assertFalse(result["metadata"]["identity_verified"])
                self.assertEqual(result["activity"], [])
        # No owned assignments must not make a reporter or comment the principal.
        self.data["searches"]["assigned"] = []
        self.data["counts"] = [0, 0]
        self.data["searches"]["reporter"] = [issue(author=OTHER)]
        self.assertFalse(self.collect()["metadata"]["identity_verified"])

    def test_hidden_assignee_email_keeps_current_user_native_identity_proof(self):
        self.data["searches"]["assigned"][0]["fields"]["assignee"].pop("emailAddress")
        result = self.collect()
        self.assertTrue(result["metadata"]["identity_verified"])
        self.assertEqual(result["metadata"]["account_id"], "self-id")
        self.assertEqual(len(result["activity"]), 1)

    def test_count_drift_or_failure_preserves_inventory_and_blocks_identity_proof(self):
        for counts in [[1, 2], [None, 1], [1, None], ["not-a-count", 1]]:
            with self.subTest(counts=counts):
                self.log.unlink(missing_ok=True)
                self.data["counts"] = counts
                result = self.collect()
                self.assertEqual(len(result["issues"]), 1)
                self.assertFalse(result["metadata"]["identity_verified"])
                self.assertEqual(result["metadata"]["coverage"]["assigned"]["status"], "partial")

    def test_failed_search_retains_valid_stdout_as_partial_evidence(self):
        self.data["searches"]["recent"] = {"_exit": 1, "_stdout": json.dumps([issue("SIDE-2", author=OTHER)])}
        self.data["details"]["SIDE-2"] = detail(issue("SIDE-2", author=OTHER))
        result = self.collect()
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(result["metadata"]["coverage"]["recent"]["status"], "partial")

    def test_invalid_duplicate_and_wrong_tenant_rows_are_skipped_without_losing_good_rows(self):
        wrong_site = issue("BAD-3")
        wrong_site["self"] = "https://other.example/rest/api/3/issue/3"
        self.data["searches"]["assigned"] += [None, issue(), wrong_site]
        result = self.collect()
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["metadata"]["coverage"]["assigned"]["status"], "partial")

    def test_bad_detail_retains_private_inventory_lead_and_other_sources(self):
        self.data["details"]["TEST-1"] = detail(issue("WRONG-1"))
        result = self.collect()
        self.assertEqual(result["issues"][0]["detail_status"], "failed")
        self.assertTrue(result["issues"][0]["sensitive"])
        self.assertEqual(result["activity"], [])
        self.assertTrue(any("details could not be read" in warning for warning in self.warnings))

    def test_activity_is_exact_inclusive_utc_window_and_requires_native_actor(self):
        comments = [comment("start", created="2026-01-08T07:00:00-05:00"),
                    comment("end", created="2026-01-15T12:00:00Z"),
                    comment("old", created="2026-01-08T11:59:59Z"),
                    comment("future", created="2026-01-15T12:00:01Z"),
                    comment("other", OTHER),
                    comment("projection", author="Person")]
        self.data["details"]["TEST-1"] = detail(issue(), comments)
        result = self.collect()
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["start", "end"])

    def test_native_jira_compact_timezone_offsets_preserve_details_and_activity(self):
        self.data["details"]["TEST-1"]["fields"]["updated"] = "2026-01-12T12:00:00.123+0000"
        self.data["details"]["TEST-1"]["fields"]["comment"] = {
            "comments": [comment("native", created="2026-01-08T07:00:00.123-0500")],
            "total": 1, "startAt": 0, "maxResults": 100}
        result = self.collect()
        self.assertEqual(result["issues"][0]["detail_status"], "ok")
        self.assertEqual(len(result["activity"]), 1)
        self.assertEqual(result["activity"][0]["occurred_at"], "2026-01-08T12:00:00.123000Z")

    def test_comment_edits_are_not_credited(self):
        old = "2026-01-01T00:00:00Z"
        comments = [comment("edited-by-me", OTHER, old, updated="2026-01-10T00:00:00Z", updateAuthor=ME),
                    comment("mine-edited-later", ME, old, updated="2026-01-10T00:00:00Z", updateAuthor=ME)]
        self.data["details"]["TEST-1"] = detail(issue(), comments)
        result = self.collect()
        self.assertEqual(result["activity"], [])
        self.assertEqual(result["metadata"]["status"], "ok")

    def test_sensitive_issue_and_comment_visibility_keep_private_link_proofs(self):
        private = comment("private", body="https://github.com/example/private/pull/88", visibility={"type": "group", "value": "private"})
        public = comment("public", body="https://github.com/example/public/pull/22")
        self.data["details"]["TEST-1"] = detail(issue(), [private, public])
        result = self.collect()
        self.assertEqual(linked_prs(result), ["https://github.com/example/private/pull/88",
                                              "https://github.com/example/public/pull/22"])
        self.assertEqual([event["sensitive"] for event in result["activity"]], [True, False])
        self.assertEqual([relation["sensitive"] for relation in result["issues"][0]["pr_links"]], [True, False])
        self.log.unlink()
        self.data["details"]["TEST-1"]["fields"]["security"] = {"id": "1", "name": "Private"}
        result = self.collect()
        self.assertTrue(all(event["sensitive"] for event in result["activity"]))
        self.assertEqual(len(linked_prs(result)), 2)
        self.assertTrue(all(relation["sensitive"] for relation in result["issues"][0]["pr_links"]))

    def test_security_labels_and_unknown_sensitivity_never_become_public(self):
        for label in ["SecurityTracking", "CVE-2099-1234"]:
            self.log.unlink(missing_ok=True)
            self.data["details"]["TEST-1"]["fields"]["labels"] = [label]
            self.assertTrue(self.collect()["issues"][0]["sensitive"])
        self.data["details"]["TEST-1"]["fields"].pop("security")
        self.assertEqual(self.collect()["issues"][0]["detail_status"], "failed")

    def test_newest_page_missing_only_comments_older_than_the_window_is_complete(self):
        # Jira embeds the newest 100 comments: startAt 59 of 159 leaves out the oldest 59.
        before, during = comment("old", created="2026-01-01T00:00:00Z"), comment("new", created="2026-01-10T00:00:00Z")
        at_start = comment("edge", OTHER, created="2026-01-08T12:00:00Z")  # a missing one could share this instant
        # (embedded rows, total Jira reports, warning expected)
        for rows, total, warned in (([before, during], 61, False), ([during], 60, True), ([before, during], 62, True),
                                    ([at_start, during], 61, True)):
            with self.subTest(rows=[row["id"] for row in rows], total=total):
                self.data["details"]["TEST-1"] = detail(issue(), rows)
                self.data["details"]["TEST-1"]["fields"]["comment"].update(startAt=59, total=total)
                result = self.collect()
                self.assertEqual([event["comment_id"] for event in result["activity"]], ["new"])
                self.assertEqual(any("the comments that were read are kept" in warning for warning in self.warnings),
                                 warned)
        self.data["details"]["TEST-1"] = detail(issue())
        self.data["details"]["TEST-1"]["fields"]["comment"].update(startAt=5, total=5)
        self.collect()
        self.assertTrue(any("the comments that were read are kept" in warning for warning in self.warnings))

    def test_fewer_embedded_comments_than_total_is_a_gap_without_paging(self):
        positive = comment("c2", created="2026-01-11T00:00:00Z", body="https://github.com/org/repo/pull/2")
        for native in ({"startAt": 0, "total": 2}, {"startAt": 1, "total": 1}, {"startAt": 0, "total": 0},
                       {"startAt": 0, "total": True}, {"startAt": False, "total": 1}):
            with self.subTest(native=native):
                self.log.unlink(missing_ok=True)
                self.data["details"]["TEST-1"] = detail(issue(), [positive])
                self.data["details"]["TEST-1"]["fields"]["comment"].update(native)
                result = self.collect()
                self.assertEqual([event["comment_id"] for event in result["activity"]], ["c2"])
                self.assertEqual(linked_prs(result), ["https://github.com/org/repo/pull/2"])
                self.assertEqual(result["metadata"]["status"], "partial")
                self.assertTrue(any("the comments that were read are kept" in warning for warning in self.warnings))
                self.assertFalse(any("comment" in call for call in self.calls()))
        for missing in ({}, {"comment": None}, {"comment": {"comments": None, "startAt": 0, "total": 1}},
                        {"comment": "text"}, {"comment": [1]}):
            with self.subTest(missing=missing):
                self.data["details"]["TEST-1"] = detail(issue())
                self.data["details"]["TEST-1"]["fields"].pop("comment")
                self.data["details"]["TEST-1"]["fields"].update(missing)
                result = self.collect()
                self.assertEqual(result["issues"][0]["detail_status"], "ok")
                self.assertEqual(result["activity"], [])
                self.assertTrue(any("the comments that were read are kept" in warning for warning in self.warnings))

    def test_pr_links_come_from_source_not_titles_and_malformed_suffix_is_not_cited(self):
        self.data["details"]["TEST-1"]["fields"]["description"] = {
            "type": "doc", "content": [{"text": "https://github.com/org/repo/pull/12evil"},
            {"attrs": {"href": "https://github.com/org/repo/pull/13?tab=files"}}]}
        self.data["details"]["TEST-1"]["fields"]["summary"] = "https://github.com/org/title/pull/99"
        self.assertEqual(linked_prs(self.collect()), ["https://github.com/org/repo/pull/13"])

    def test_per_issue_pr_proofs_preserve_description_comment_and_issue_link_sources(self):
        fields = self.data["details"]["TEST-1"]["fields"]
        fields["description"] = {"content": [{"attrs": {"href": "https://github.com/org/repo/pull/1"}}]}
        fields["issuelinks"] = [{"outwardIssue": {"fields": {"summary": "Related https://github.com/org/repo/pull/2"}}}]
        fields["issuelinks"].append({"outwardIssue": {"fields": {"security": {"id": "private"},
                                    "summary": "Restricted https://github.com/org/private/pull/66"}}})
        fields["comment"] = {"comments": [comment("7", body="https://github.com/org/repo/pull/3"),
                                              comment("8", body="https://github.com/org/private/pull/4", visibility={"type": "group"})],
                             "total": 2, "startAt": 0, "maxResults": 100}
        result = self.collect()
        self.assertEqual(linked_prs(result), ["https://github.com/org/private/pull/4", "https://github.com/org/private/pull/66"]
                         + [f"https://github.com/org/repo/pull/{n}" for n in (1, 2, 3)])
        self.assertEqual(result["issues"][0]["pr_links"], [
            {"url": "https://github.com/org/private/pull/4", "proof": "comment", "evidence_url": "https://jira.example.test/browse/TEST-1?focusedCommentId=8", "sensitive": True},
            {"url": "https://github.com/org/private/pull/66", "proof": "issue_link", "evidence_url": "https://jira.example.test/browse/TEST-1", "sensitive": True},
            {"url": "https://github.com/org/repo/pull/1", "proof": "description", "evidence_url": "https://jira.example.test/browse/TEST-1", "sensitive": False},
            {"url": "https://github.com/org/repo/pull/2", "proof": "issue_link", "evidence_url": "https://jira.example.test/browse/TEST-1", "sensitive": False},
            {"url": "https://github.com/org/repo/pull/3", "proof": "comment", "evidence_url": "https://jira.example.test/browse/TEST-1?focusedCommentId=7", "sensitive": False}])

    def test_enterprise_github_links_use_configured_host_and_reject_other_hosts(self):
        self.data["details"]["TEST-1"]["fields"]["description"] = {
            "text": "https://git.example.test/team/repo/pull/7 https://github.com/team/repo/pull/8 "
                    "https://evil.example/team/repo/pull/9 https://git.example.test/../repo/pull/10"}
        with patch.dict(os.environ, {"GH_HOST": "git.example.test"}):
            result = self.collect()
        self.assertEqual(linked_prs(result), ["https://git.example.test/team/repo/pull/7"])
        self.assertEqual(result["issues"][0]["pr_links"][0]["proof"], "description")

    def test_collection_and_referenced_activity_sort_mixed_precision_chronologically(self):
        self.data["details"]["TEST-1"] = detail(issue(), [
            comment("micro", created="2026-01-10T12:00:00.000001Z"),
            comment("whole", created="2026-01-10T12:00:00Z")])
        self.data["details"]["MENTIONED-2"] = detail(issue("MENTIONED-2"), [
            comment("later", created="2026-01-10T12:00:00.000002Z")])
        result = self.collect()
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["whole", "micro"])
        jira.add_referenced_jira(result, ["MENTIONED-2"], START, END, self.warnings.append)
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["whole", "micro", "later"])
        self.assertEqual([event["occurred_at"] for event in result["activity"]], [
            "2026-01-10T12:00:00Z", "2026-01-10T12:00:00.000001Z", "2026-01-10T12:00:00.000002Z"])

    def test_referenced_key_targeted_capture_reuses_inventory_and_adds_native_proof(self):
        row = issue("MENTIONED-2", "done", OTHER)
        self.data["details"]["MENTIONED-2"] = detail(row, [comment("77", ME)])
        result = self.collect()
        before = len(self.calls())
        jira.add_referenced_jira(result, ["MENTIONED-2", "TEST-1"], START, END, self.warnings.append)
        extra_calls = self.calls()[before:]
        self.assertFalse(any("search" in call for call in extra_calls))
        self.assertEqual(sum(call[:3] == ["jira", "workitem", "view"] for call in extra_calls), 1)
        item = next(item for item in result["issues"] if item["key"] == "MENTIONED-2")
        self.assertEqual(item["roles"], ["referenced_by_github"])
        self.assertEqual(item["detail_status"], "ok")
        self.assertEqual(len(result["activity"]), 2)
        self.assertEqual(result["metadata"]["inventory_count"], 1)
        self.assertEqual(result["metadata"]["issue_count"], 2)
        before = len(self.calls())
        jira.add_referenced_jira(result, ["MENTIONED-2"], START, END, self.warnings.append)
        self.assertEqual(self.calls()[before:], [["jira", "auth", "status"]])

    def test_key_shaped_text_outside_tenant_projects_is_not_read_or_warned(self):
        self.data["projects"] = [{"key": "TEST"}, {"key": "MENTIONED"}]
        self.data["details"]["MENTIONED-2"] = detail(issue("MENTIONED-2", "done", OTHER))
        result = self.collect()
        self.assertEqual(result["metadata"]["project_keys"], ["MENTIONED", "TEST"])
        warnings_before, before = len(self.warnings), len(self.calls())
        jira.add_referenced_jira(result, ["SHA-256", "UTF-8", "MENTIONED-2", "MENTIONED-3"], START, END,
                                 self.warnings.append)
        views = [call[3] for call in self.calls()[before:] if call[:3] == ["jira", "workitem", "view"]]
        self.assertEqual(sorted(views), ["MENTIONED-2", "MENTIONED-3"])
        coverage = result["metadata"]["coverage"]["referenced_by_github"]
        self.assertEqual(coverage["not_a_project"], ["SHA-256", "UTF-8"])
        self.assertEqual(list(coverage["failures"]), ["MENTIONED-3"])
        self.assertEqual(len(self.warnings) - warnings_before, 1)
        self.assertIn("1 referenced ticket(s) could not be read", self.warnings[-1])

    def test_moved_key_retains_canonical_ticket_and_records_the_move(self):
        native = detail(issue('NEW-3'), [comment('moved-comment', ME)])
        native['fields']['description'] = {'text': 'https://github.com/org/repo/pull/15'}
        self.data['details'].update({'OLD-2': native, 'NEW-3': copy.deepcopy(native)})
        result = self.collect()
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        self.assertEqual({item['key'] for item in result['issues']}, {'TEST-1', 'NEW-3'})
        self.assertEqual(result['key_aliases'], {'OLD-2': 'NEW-3'})
        self.assertEqual(linked_prs(result), ['https://github.com/org/repo/pull/15'])
        self.assertIn('OLD-2', result['metadata']['coverage']['referenced_by_github']['returned_keys'])
        before, warned = len(self.calls()), len(self.warnings)
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        self.assertEqual(self.calls()[before:], [['jira', 'auth', 'status']])
        self.assertEqual(len(self.warnings), warned)
        self.assertEqual(result['metadata']['coverage']['referenced_by_github']['failures'], {})

    def test_moved_key_to_a_captured_ticket_keeps_one_ticket(self):
        self.data['details']['OLD-2'] = copy.deepcopy(self.data['details']['TEST-1'])
        result = self.collect()
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        self.assertEqual([item['key'] for item in result['issues']], ['TEST-1'])
        self.assertEqual(result['key_aliases'], {'OLD-2': 'TEST-1'})
        self.assertIn('referenced_by_github', result['issues'][0]['roles'])

    def test_moved_keys_from_unlisted_projects_are_skipped_unless_already_known(self):
        self.data['projects'] = [{'key': 'TEST'}, {'key': 'NEW'}]
        self.data['details']['OLD-2'] = detail(issue('NEW-3'))
        result = self.collect()
        before = len(self.calls())
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        self.assertFalse(any(call[:3] == ['jira', 'workitem', 'view'] for call in self.calls()[before:]))
        coverage = result['metadata']['coverage']['referenced_by_github']
        self.assertEqual(coverage['not_a_project'], ['OLD-2'])
        self.data['details']['NEW-3'] = detail(issue('NEW-3'))
        self.fixture.write_text(json.dumps(self.data))
        jira.add_referenced_jira(result, ['NEW-3'], START, END, self.warnings.append)
        result['key_aliases']['OLD-2'] = 'NEW-3'
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        self.assertIn('OLD-2', coverage['returned_keys'])

    def test_key_redirect_is_read_once(self):
        native = detail(issue('NEW-3'))
        self.data['details']['OLD-2'] = native
        result = self.collect()
        before = len(self.calls())
        jira.add_referenced_jira(result, ['OLD-2'], START, END, self.warnings.append)
        views = [call[3] for call in self.calls()[before:] if call[:3] == ['jira', 'workitem', 'view']]
        self.assertEqual(views, ['OLD-2'])
        retained = next(item for item in result['issues'] if item['key'] == 'NEW-3')
        self.assertEqual(retained['source'], native)
        self.assertEqual(retained['detail_status'], 'ok')
        self.assertEqual(result['key_aliases'], {'OLD-2': 'NEW-3'})
        self.assertIn('OLD-2', result['metadata']['coverage']['referenced_by_github']['returned_keys'])
        self.assertNotIn('OLD-2', result['metadata']['coverage']['referenced_by_github']['failures'])

    def test_referenced_failure_retains_native_diagnostic_and_successful_retry_clears_it(self):
        result = self.collect()
        message = "Issue does not exist or you do not have permission to see it."
        self.data["details"]["RESTRICTED-2"] = {"_exit": 1, "_stderr": message}
        self.fixture.write_text(json.dumps(self.data))
        jira.add_referenced_jira(result, ["RESTRICTED-2"], START, END, self.warnings.append)
        coverage = result["metadata"]["coverage"]["referenced_by_github"]
        failure = coverage["failures"]["RESTRICTED-2"]
        self.assertEqual(failure["returncode"], 1)
        self.assertEqual(failure["stderr"], message)
        self.assertNotIn("http_status", failure)
        self.assertNotIn("RESTRICTED-2", coverage["returned_keys"])
        self.assertEqual(len(result["issues"]), 1)
        self.data["details"]["RESTRICTED-2"] = detail(issue("RESTRICTED-2"))
        self.fixture.write_text(json.dumps(self.data))
        jira.add_referenced_jira(result, ["RESTRICTED-2"], START, END, self.warnings.append)
        self.assertNotIn("RESTRICTED-2", coverage["failures"])
        self.assertIn("RESTRICTED-2", coverage["returned_keys"])

    def test_malformed_comments_are_kept_but_only_well_formed_ones_are_credited(self):
        rows = [comment("invalid-created", created="not-a-time"),
                comment("display-author", author="Person", body="https://github.com/org/repo/pull/5"),
                comment("good"), comment("good"), comment(""), {"author": ME, "created": "2026-01-10T12:00:00Z"},
                "not a comment"]
        self.data["details"]["TEST-1"] = detail(issue(), rows)
        original = copy.deepcopy(rows)
        result = self.collect()
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["good"])
        self.assertEqual(result["issues"][0]["detail"]["fields"]["comment"]["comments"], original)
        self.assertEqual([(link["url"], link["sensitive"]) for link in result["issues"][0]["pr_links"]],
                         [("https://github.com/org/repo/pull/5", True)])
        self.assertTrue(any("the comments that were read are kept" in warning for warning in self.warnings))

    def test_each_unreadable_comment_alone_is_a_gap(self):
        good = comment("good")
        for rows, total in (([good, "not a comment"], 1), ([good, comment("undated", created=None)], 2),
                            ([good, good], 1)):
            with self.subTest(rows=rows):
                self.data["details"]["TEST-1"] = detail(issue(), rows)
                self.data["details"]["TEST-1"]["fields"]["comment"]["total"] = total
                result = self.collect()
                self.assertEqual([event["comment_id"] for event in result["activity"]], ["good"])
                self.assertEqual(result["metadata"]["status"], "partial")

    def test_referenced_key_failure_and_account_switch_are_named_gaps(self):
        result = self.collect()
        jira.add_referenced_jira(result, ["MISSING-2"], START, END, self.warnings.append)
        self.assertEqual(len(result["issues"]), 1)
        self.assertEqual(result["metadata"]["coverage"]["referenced_by_github"]["status"], "partial")
        self.data["auth"] = "Site: other.example.test\nEmail: person@example.test\n"
        self.fixture.write_text(json.dumps(self.data))
        before = len(self.calls())
        jira.add_referenced_jira(result, ["MISSING-3"], START, END, self.warnings.append)
        self.assertEqual(self.calls()[before:], [["jira", "auth", "status"]])
        self.assertTrue(any("site/account changed" in warning for warning in self.warnings))


if __name__ == "__main__":
    unittest.main()
