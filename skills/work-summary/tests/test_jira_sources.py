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


def detail(row, comments=(), total=None):
    result = copy.deepcopy(row)
    result["fields"].update(updated="2026-01-12T12:00:00Z", created="2025-01-01T00:00:00Z",
                            security=None, description={"type": "doc", "content": []}, issuelinks=[],
                            comment={"comments": list(comments), "startAt": 0, "maxResults": 100,
                                     "total": len(comments) if total is None else total})
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
elif args[:3] == ["jira", "workitem", "view"]:
    emit(data["details"].get(args[3], {"_exit": 1}))
elif args[:4] == ["jira", "workitem", "comment", "list"]:
    assert "--paginate" in args
    value = data.get("comment_pages", {"_exit": 1})
    if isinstance(value, dict):
        emit(value)
    else:
        for page in value:
            print(json.dumps(page))
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

    def test_read_only_identity_and_native_activity_with_explicit_scope_gap(self):
        result = self.collect()
        self.assertTrue(result["metadata"]["identity_verified"])
        self.assertEqual(result["metadata"]["account_id"], "self-id")
        self.assertEqual(result["metadata"]["site"], "jira.example.test")
        self.assertEqual(result["issues"][0]["source"], issue())
        self.assertEqual(result["activity"][0]["actor_account_id"], "self-id")
        self.assertEqual(result["activity"][0]["comment_id"], "c1")
        self.assertEqual(result["metadata"]["coverage"]["assigned"]["count_after"], 1)
        self.assertEqual(result["metadata"]["coverage"]["changelog"]["status"], "unavailable")
        self.assertEqual(result["metadata"]["status"], "partial")
        for call in self.calls():
            self.assertIn(call[:3], [["jira", "auth", "status"], ["jira", "workitem", "search"],
                                    ["jira", "workitem", "view"]])

    def test_all_projects_and_old_completed_work_get_full_details_and_pr_links(self):
        old = issue("OTHER-2", "done")
        self.data["searches"]["assigned"].append(old)
        self.data["counts"] = [2, 2]
        self.data["details"]["OTHER-2"] = detail(old)
        self.data["details"]["OTHER-2"]["fields"]["description"] = {
            "text": "Historical work https://github.com/old/project/pull/37"}
        result = self.collect()
        self.assertEqual([row["key"] for row in result["issues"]], ["OTHER-2", "TEST-1"])
        self.assertEqual(result["issues"][0]["detail_status"], "ok")
        self.assertFalse(result["issues"][0]["sensitive"])
        self.assertEqual(result["issues"][0]["pr_links"][0]["url"], "https://github.com/old/project/pull/37")
        self.assertEqual(sum(call[:3] == ["jira", "workitem", "view"] for call in self.calls()), 2)

    def test_nonassigned_activity_lead_and_old_active_context_are_detailed(self):
        for role, key in [("updated_by", "SIDE-2"), ("reporter", "SIDE-3"),
                          ("former_assignee", "SIDE-4"), ("watcher", "SIDE-5"), ("recent", "SIDE-6")]:
            row = issue(key, "done" if role == "recent" else "indeterminate", OTHER)
            self.data["searches"][role] = [row]
            self.data["details"][key] = detail(row, [comment(key, ME)])
        result = self.collect()
        self.assertEqual(len(result["issues"]), 6)
        self.assertEqual(len(result["activity"]), 6)
        self.assertTrue(all(row["detail_status"] == "ok" for row in result["issues"]))
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
        result = {"metadata": metadata, "issues": selected, "activity": [], "linked_prs": []}
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

    def test_all_status_role_inventory_preserves_more_than_one_page(self):
        for role, prefix in [("reporter", "REPORTED"), ("creator", "CREATED"),
                             ("former_assignee", "FORMER"), ("watcher", "WATCHED")]:
            self.data["searches"][role] = [issue(f"{prefix}-{n}", "done", OTHER) for n in range(1, 102)]
            for row in self.data["searches"][role]:
                self.data["details"][row["key"]] = detail(row)
        # Searches still execute the disposable acli, exercising --paginate and
        # its full 101-row arrays; avoid 405 redundant subprocesses for details.
        real_command, viewed = jira.command, []

        def command(args, **kwargs):
            if args[:4] == ["acli", "jira", "workitem", "view"]:
                viewed.append(args[4])
                return json.dumps(self.data["details"][args[4]])
            return real_command(args, **kwargs)

        with patch.object(jira, "command", side_effect=command):
            result = self.collect()
        self.assertEqual(len(result["issues"]), 405)
        for role in ["reporter", "creator", "former_assignee", "watcher"]:
            self.assertEqual(result["metadata"]["coverage"][role]["returned_count"], 101)
            self.assertEqual(result["metadata"]["coverage"][role]["count_before"], 101)
            self.assertEqual(result["metadata"]["coverage"][role]["count_after"], 101)
            self.assertEqual(result["metadata"]["coverage"][role]["status"], "ok")
            query = result["metadata"]["coverage"][role]["query"]
            self.assertNotIn("statusCategory", query)
            self.assertNotIn("assignee !=", query)
        self.assertEqual(len(viewed), 405)
        self.assertTrue(all(item["detail_status"] == "ok" for item in result["issues"]))
        recent = result["metadata"]["coverage"]["recent"]["query"]
        self.assertIn("creator = currentUser()", recent)

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
        self.data["searches"]["watcher"] = {"_exit": 1, "_stdout": json.dumps([issue("SIDE-2", author=OTHER)])}
        self.data["details"]["SIDE-2"] = detail(issue("SIDE-2", author=OTHER))
        result = self.collect()
        self.assertEqual(len(result["issues"]), 2)
        self.assertEqual(result["metadata"]["coverage"]["watcher"]["status"], "partial")

    def test_each_role_inventory_count_drift_is_named_without_discarding_leads(self):
        row = issue("SIDE-2", "done", OTHER)
        self.data["searches"]["creator"] = [row]
        self.data["details"]["SIDE-2"] = detail(row)
        self.data["role_counts"] = {"creator": [1, 2]}
        result = self.collect()
        self.assertIn("SIDE-2", {item["key"] for item in result["issues"]})
        self.assertEqual(result["metadata"]["coverage"]["creator"]["status"], "partial")
        self.assertTrue(any("creator counts changed" in warning for warning in self.warnings))

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
        self.assertTrue(any("inventory lead" in warning for warning in self.warnings))

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
        self.assertFalse(result["issues"][0]["comment_coverage"]["native_fidelity"])

    def test_native_jira_compact_timezone_offsets_preserve_details_and_activity(self):
        self.data["details"]["TEST-1"]["fields"]["updated"] = "2026-01-12T12:00:00.123+0000"
        self.data["details"]["TEST-1"]["fields"]["comment"] = {
            "comments": [comment("native", created="2026-01-08T07:00:00.123-0500")],
            "total": 1, "startAt": 0, "maxResults": 100}
        result = self.collect()
        self.assertEqual(result["issues"][0]["detail_status"], "ok")
        self.assertEqual(len(result["activity"]), 1)
        self.assertEqual(result["activity"][0]["occurred_at"], "2026-01-08T12:00:00.123000Z")

    def test_edits_credit_update_author_and_missing_editor_is_a_gap(self):
        old = "2026-01-01T00:00:00Z"
        comments = [comment("edited-by-me", OTHER, old, updated="2026-01-10T00:00:00Z", updateAuthor=ME),
                    comment("edited-by-other", ME, old, updated="2026-01-10T00:00:00Z", updateAuthor=OTHER),
                    comment("editor-missing", ME, old, updated="2026-01-10T00:00:00Z")]
        self.data["details"]["TEST-1"] = detail(issue(), comments)
        result = self.collect()
        self.assertEqual([(event["comment_id"], event["kind"]) for event in result["activity"]],
                         [("edited-by-me", "comment_edited")])
        self.assertFalse(result["issues"][0]["comment_coverage"]["native_fidelity"])

    def test_sensitive_issue_and_comment_visibility_keep_private_link_proofs(self):
        private = comment("private", body="https://github.com/example/private/pull/88", visibility={"type": "group", "value": "private"})
        public = comment("public", body="https://github.com/example/public/pull/22")
        self.data["details"]["TEST-1"] = detail(issue(), [private, public])
        result = self.collect()
        self.assertEqual(result["linked_prs"], ["https://github.com/example/private/pull/88",
                                              "https://github.com/example/public/pull/22"])
        self.assertEqual([event["sensitive"] for event in result["activity"]], [True, False])
        self.assertEqual([relation["sensitive"] for relation in result["issues"][0]["pr_links"]], [True, False])
        self.log.unlink()
        self.data["details"]["TEST-1"]["fields"]["security"] = {"id": "1", "name": "Private"}
        result = self.collect()
        self.assertTrue(all(event["sensitive"] for event in result["activity"]))
        self.assertEqual(len(result["linked_prs"]), 2)
        self.assertTrue(all(relation["sensitive"] for relation in result["issues"][0]["pr_links"]))

    def test_security_labels_and_unknown_sensitivity_never_become_public(self):
        for label in ["SecurityTracking", "CVE-2099-1234"]:
            self.log.unlink(missing_ok=True)
            self.data["details"]["TEST-1"]["fields"]["labels"] = [label]
            self.assertTrue(self.collect()["issues"][0]["sensitive"])
        self.data["details"]["TEST-1"]["fields"].pop("security")
        self.assertEqual(self.collect()["issues"][0]["detail_status"], "failed")

    def test_paginated_native_comments_add_activity_and_validate_terminal_page(self):
        first, second = comment(), comment("c2", created="2026-01-11T00:00:00Z")
        self.data["details"]["TEST-1"] = detail(issue(), [first], total=2)
        self.data["comment_pages"] = [
            {"comments": [first], "startAt": 0, "maxResults": 1, "total": 2, "isLast": False},
            {"comments": [second], "startAt": 1, "maxResults": 1, "total": 2, "isLast": True}]
        result = self.collect()
        self.assertEqual(len(result["activity"]), 2)
        self.assertTrue(result["issues"][0]["comment_coverage"]["complete"])
        self.assertTrue(result["issues"][0]["comment_coverage"]["native_fidelity"])

    def test_rendered_comment_projection_does_not_invent_timestamps_or_account_ids(self):
        first = comment()
        self.data["details"]["TEST-1"] = detail(issue(), [first], total=2)
        projection = [{"id": "c1", "author": "Person", "body": "older native comment"},
                      {"id": "c2", "author": "Person", "body": "2026-01-11: Completed work"}]
        self.data["comment_pages"] = [{"comments": projection, "startAt": 0, "maxResults": 50,
                                      "total": 2, "isLast": True}]
        result = self.collect()
        self.assertEqual(len(result["activity"]), 1)
        coverage = result["issues"][0]["comment_coverage"]
        self.assertTrue(coverage["complete"])
        self.assertFalse(coverage["native_fidelity"])
        self.assertTrue(any("cannot prove" in warning for warning in self.warnings))

    def test_native_suffix_page_merges_projected_prefix_without_inventing_activity(self):
        suffix = comment("c2", body="Native https://github.com/org/repo/pull/2",
                         visibility={"type": "role", "value": "reviewers"})
        native = detail(issue(), [suffix], total=2)
        native["fields"]["comment"].update(startAt=1, maxResults=1)
        original = copy.deepcopy(native)
        self.data["details"]["TEST-1"] = native
        projected = [{"id": "c1", "author": "Person", "body": "Earlier https://github.com/org/repo/pull/1",
                      "visibility": {"type": "role", "value": "reviewers"}},
                     {"id": "c2", "author": "Person", "body": "Projected content differs", "visibility": None}]
        self.data["comment_pages"] = [{"comments": projected, "startAt": 0, "maxResults": 50,
                                      "total": 2, "isLast": True}]
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual(item["detail"], original)
        self.assertEqual({pr["url"] for pr in item["pr_links"]},
                         {"https://github.com/org/repo/pull/1", "https://github.com/org/repo/pull/2"})
        self.assertTrue(all(pr["sensitive"] for pr in item["pr_links"]))
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["c2"])
        self.assertEqual(result["activity"][0]["source"], suffix)
        self.assertEqual(item["comment_coverage"], {"complete": True, "native_fidelity": False,
                                                   "captured_count": 2, "total": 2})
        self.assertTrue(any("comment" in call and "list" in call for call in self.calls()))

    def test_native_suffix_survives_unavailable_comment_pagination(self):
        suffix = comment("c2", body="Available https://github.com/org/repo/pull/2")
        native = detail(issue(), [suffix], total=2)
        native["fields"]["comment"].update(startAt=1, maxResults=1)
        original = copy.deepcopy(native)
        self.data["details"]["TEST-1"] = native
        self.data["comment_pages"] = {"_exit": 1}
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual(item["detail"], original)
        self.assertEqual([pr["url"] for pr in item["pr_links"]], ["https://github.com/org/repo/pull/2"])
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["c2"])
        self.assertEqual(item["comment_coverage"], {"complete": False, "native_fidelity": True,
                                                   "captured_count": 1, "total": 2})
        self.assertEqual(result["metadata"]["status"], "partial")
        self.assertTrue(any("comment" in call and "list" in call for call in self.calls()))

    def test_native_suffix_count_drift_keeps_positive_comment_evidence(self):
        suffix = comment("c2", body="Retain https://github.com/org/repo/pull/2")
        native = detail(issue(), [suffix], total=1)
        native["fields"]["comment"].update(startAt=1, maxResults=1)
        original = copy.deepcopy(native)
        self.data["details"]["TEST-1"] = native
        self.data["comment_pages"] = {"_exit": 1}
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual(item["detail"], original)
        self.assertEqual([pr["url"] for pr in item["pr_links"]], ["https://github.com/org/repo/pull/2"])
        self.assertEqual(len(result["activity"]), 1)
        self.assertEqual(result["activity"][0]["source"], suffix)
        self.assertEqual(item["comment_coverage"]["captured_count"], 1)
        self.assertFalse(item["comment_coverage"]["complete"])
        self.assertEqual(result["metadata"]["status"], "partial")
        self.assertTrue(any("available native comments were retained" in warning for warning in self.warnings))

    def test_native_suffix_identity_drift_retains_union_without_claiming_completeness(self):
        suffix = comment("retained", body="Retain https://github.com/org/repo/pull/1")
        native = detail(issue(), [suffix], total=2)
        native["fields"]["comment"].update(startAt=1, maxResults=1)
        self.data["details"]["TEST-1"] = native
        self.data["comment_pages"] = [{"comments": [
            comment("new1", body="New https://github.com/org/repo/pull/2"),
            comment("new2", body="New https://github.com/org/repo/pull/3")],
            "startAt": 0, "maxResults": 50, "total": 2, "isLast": True}]
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual({pr["url"] for pr in item["pr_links"]},
                         {f"https://github.com/org/repo/pull/{number}" for number in (1, 2, 3)})
        self.assertEqual({event["comment_id"] for event in result["activity"]}, {"retained", "new1", "new2"})
        self.assertEqual(item["comment_coverage"], {"complete": False, "native_fidelity": True,
                                                   "captured_count": 3, "total": 2})
        self.assertEqual(result["metadata"]["status"], "partial")
        self.assertTrue(any("available native comments were retained" in warning for warning in self.warnings))

    def test_duplicate_or_changed_comment_pages_keep_available_native_activity(self):
        first = comment()
        self.data["details"]["TEST-1"] = detail(issue(), [first], total=2)
        self.data["comment_pages"] = [
            {"comments": [first], "startAt": 0, "maxResults": 1, "total": 2, "isLast": False},
            {"comments": [first], "startAt": 1, "maxResults": 1, "total": 2, "isLast": True}]
        result = self.collect()
        self.assertEqual(len(result["activity"]), 1)
        self.assertFalse(result["issues"][0]["comment_coverage"]["complete"])

    def test_later_failed_or_malformed_comment_page_retains_prefix_positive(self):
        original = comment("1", OTHER)
        positive = comment("2", ME)
        page = {"comments": [original, positive], "startAt": 0, "maxResults": 2,
                "total": 3, "isLast": False}
        for following in [{"_exit": 1, "_stdout": json.dumps(page) + "\n"},
                          [page, {"malformed": "page"}],
                          {"_exit": 0, "_stdout": json.dumps(page) + "\n{truncated"}]:
            with self.subTest(following=following):
                self.log.unlink(missing_ok=True)
                self.data["details"]["TEST-1"] = detail(issue(), [original], total=3)
                self.data["comment_pages"] = following
                result = self.collect()
                self.assertEqual([event["comment_id"] for event in result["activity"]], ["2"])
                coverage = result["issues"][0]["comment_coverage"]
                self.assertEqual(coverage["captured_count"], 2)
                self.assertFalse(coverage["complete"])
                self.assertEqual(len(result["issues"][0]["comment_pages"]), 1)

    def test_changed_comment_total_preserves_valid_page_positive_as_partial(self):
        first, positive = comment("1", OTHER), comment("2", ME)
        self.data["details"]["TEST-1"] = detail(issue(), [first], total=2)
        self.data["comment_pages"] = [
            {"comments": [first], "startAt": 0, "maxResults": 1, "total": 2, "isLast": False},
            {"comments": [positive], "startAt": 1, "maxResults": 1, "total": 3, "isLast": True}]
        result = self.collect()
        self.assertEqual([event["comment_id"] for event in result["activity"]], ["2"])
        coverage = result["issues"][0]["comment_coverage"]
        self.assertEqual(coverage["captured_count"], 2)
        self.assertFalse(coverage["complete"])
        self.assertEqual(len(result["issues"][0]["comment_pages"]), 2)

    def test_pr_links_come_from_source_not_titles_and_malformed_suffix_is_not_cited(self):
        self.data["details"]["TEST-1"]["fields"]["description"] = {
            "type": "doc", "content": [{"text": "https://github.com/org/repo/pull/12evil"},
            {"attrs": {"href": "https://github.com/org/repo/pull/13?tab=files"}}]}
        self.data["details"]["TEST-1"]["fields"]["summary"] = "https://github.com/org/title/pull/99"
        self.assertEqual(self.collect()["linked_prs"], ["https://github.com/org/repo/pull/13"])

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
        self.assertEqual(result["linked_prs"], ["https://github.com/org/private/pull/4", "https://github.com/org/private/pull/66"]
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
        self.assertEqual(result["linked_prs"], ["https://git.example.test/team/repo/pull/7"])
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
        self.assertEqual(len(result["activity"]), 2)

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

    def test_native_complete_changelog_is_retained_instead_of_unconditionally_unavailable(self):
        history = {"startAt": 0, "total": 1, "histories": [{"id": "h1", "author": ME,
            "created": "2026-01-12T12:00:00Z", "items": [{"field": "status", "toString": "Active"}]}]}
        self.data["details"]["TEST-1"]["changelog"] = history
        result = self.collect()
        self.assertEqual(result["metadata"]["coverage"]["changelog"]["status"], "ok")
        self.assertEqual(result["metadata"]["coverage"]["changelog"]["captured_keys"], ["TEST-1"])
        self.assertEqual(result["issues"][0]["detail"]["changelog"], history)
        self.assertEqual(result["metadata"]["status"], "ok")

    def test_projected_comment_gaps_name_exact_native_rest_recovery_targets(self):
        projected = {"id": "opaque/id", "author": "Display name", "body": "Native context"}
        self.data["details"]["TEST-1"] = detail(issue(), [projected])
        result = self.collect()
        item = result["issues"][0]
        self.assertEqual(item["comment_native_gaps"], [{"comment_id": "opaque/id",
            "missing_fields": ["author.accountId", "created", "updated"],
            "required_read": "/rest/api/3/issue/TEST-1/comment/opaque%2Fid"}])
        self.assertEqual(result["activity"], [])
        self.assertFalse(item["comment_coverage"]["native_fidelity"])

    def test_native_changelog_requires_complete_pagination_and_actor_time_fidelity(self):
        native = {"startAt": 0, "total": 1, "histories": [{"id": "h1", "author": ME,
            "created": "2026-01-12T12:00:00Z", "items": [{"field": "status"}]}]}
        for bad in ({**native, "startAt": 1}, {**native, "total": 2},
                    {**native, "histories": [{"id": "h1"}]}):
            with self.subTest(history=bad):
                result = {"metadata": {"status": "ok", "coverage": {}, "warnings": []},
                          "issues": [{"key": "TEST-1", "detail": {"changelog": bad}}]}
                warnings = []
                jira._changelog_coverage(result, warnings.append)
                self.assertEqual(result["metadata"]["coverage"]["changelog"]["status"], "partial")
                self.assertEqual(result["metadata"]["coverage"]["changelog"]["incomplete_keys"], ["TEST-1"])
                self.assertEqual(result["issues"][0]["detail"]["changelog"], bad)
                self.assertTrue(warnings)

    def test_malformed_comments_and_missing_editors_name_recovery_targets_without_losing_creation(self):
        malformed = comment("invalid-created", created="not-a-time")
        rows = [malformed, comment("invalid-update", updated="not-a-time"),
                comment("reversed", updated="2026-01-09T12:00:00Z"),
                comment("missing-editor", updated="2026-01-11T12:00:00Z")]
        missing_body = comment("missing-body")
        missing_body.pop("body")
        rows.append(missing_body)
        self.data["details"]["TEST-1"] = detail(issue(), rows)
        original = copy.deepcopy(rows)
        result = self.collect()
        item = result["issues"][0]
        gaps = {entry["comment_id"]: entry for entry in item["comment_native_gaps"]}
        self.assertEqual(gaps["invalid-created"]["invalid_fields"], ["created", "updated"])
        for ident in ("invalid-update", "reversed"):
            self.assertEqual(gaps[ident]["invalid_fields"], ["updated"])
        self.assertEqual(gaps["missing-editor"]["missing_fields"], ["updateAuthor.accountId"])
        self.assertEqual(gaps["missing-body"]["missing_fields"], ["body"])
        for ident, gap in gaps.items():
            self.assertEqual(gap["required_read"], "/rest/api/3/issue/TEST-1/comment/" + ident)
        self.assertEqual({(event["comment_id"], event["kind"]) for event in result["activity"]},
                         {(ident, "comment_created") for ident in ("invalid-update", "reversed", "missing-editor")})
        self.assertEqual(item["detail"]["fields"]["comment"]["comments"], original)
        self.assertFalse(item["comment_coverage"]["native_fidelity"])

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
