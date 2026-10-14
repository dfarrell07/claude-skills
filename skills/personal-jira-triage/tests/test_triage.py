#!/usr/bin/env python3
"""Offline contracts for personal triage; all accounts and resources are synthetic."""

import copy
import concurrent.futures
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest


SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "triage.py"
sys.dont_write_bytecode = True
SPEC = importlib.util.spec_from_file_location("personal_triage", SCRIPT)
triage = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = triage
SPEC.loader.exec_module(triage)

NOW = "2026-01-02T03:04:05Z"
KEY = "EXAMPLE-17"
SITE = "https://jira.example.invalid"


def paragraph(text):
    return {"type": "paragraph", "content": [{"type": "text", "text": text}]}


def document(text):
    return {"version": 1, "type": "doc", "content": [paragraph(text)]}


def write_json(path, value):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, ensure_ascii=False), encoding="utf-8")


class PacketCase(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix="personal-triage-test-")
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.run = self.root / "packet with spaces; literal $(false)"
        self.run.mkdir()
        self.packet = {
            "schema_version": 1,
            "collected_at": NOW,
            "identities": {"jira": {"site": SITE, "account_id": "synthetic-user"}},
            "sources": [],
            "inventory": [{"key": KEY, "role": "assigned", "active": True, "site": SITE}],
            "gaps": [], "leads": [], "local": [],
            "scope": {"selected_context_keys": []},
        }
        self.identity = self.source("identity", "jira_identity", "myself", {
            "accountId": "synthetic-user", "emailAddress": "synthetic@example.invalid", "active": True,
        })
        self.issue = self.source("issue", "jira_issue", KEY, {
            "id": "101", "key": KEY, "fields": {
                "summary": "Fix the synthetic title typo",
                "description": document("Keep this supported scope."),
                "assignee": {"accountId": "synthetic-user"},
                "status": {"id": "3", "name": "In Progress", "statusCategory": {"key": "indeterminate"}},
                "customfield_keep": {"untouched": ["value", 1]},
            },
        })
        self.comments = self.source("comments", "jira_comments", KEY, {
            "comments": [], "startAt": 0, "maxResults": 100, "total": 0,
        })
        self.metadata = self.source("metadata", "jira_metadata", KEY, {
            "editmeta": {"fields": {"summary": {"operations": ["set"], "schema": {"type": "string"}},
                                     "description": {"operations": ["set"], "schema": {"type": "string"}}}},
            "permissions": {"EDIT_ISSUES": {"havePermission": True}, "ADD_COMMENTS": {"havePermission": True},
                            "TRANSITION_ISSUES": {"havePermission": True}},
            "transitions": [{"id": "31", "name": "Done", "to": {
                "id": "100", "name": "Done", "statusCategory": {"key": "done"}},
                "fields": {"resolution": {"required": True, "allowedValues": [{"id": "1", "name": "Fixed"}]}},
            }],
        })
        self.decisions = [{"key": KEY, "disposition": "no_change", "reason": "Current tracking is accurate.",
                           "evidence": ["issue", "comments"], "draft_ids": []}]
        self.drafts = []
        self.reviews = []
        self.bin = self.root / "bin"
        self.bin.mkdir()
        self.log = self.root / "calls.jsonl"
        self.env = os.environ.copy()
        self.env["PATH"] = str(self.bin) + os.pathsep + self.env.get("PATH", "")
        self.env["TRIAGE_TEST_LOG"] = str(self.log)
        self.env["PYTHONDONTWRITEBYTECODE"] = "1"
        guard = self.root / "offline-guard"
        guard.mkdir()
        (guard / "sitecustomize.py").write_text(
            "import socket\n"
            "def forbidden(*args,**kwargs): raise RuntimeError('Offline test attempted network access')\n"
            "socket.socket=forbidden\nsocket.create_connection=forbidden\n")
        self.env["PYTHONPATH"] = str(guard)
        self.forbid_clients()

    def forbid_clients(self):
        for command in ("acli", "gh", "git", "curl"):
            path = self.bin / command
            path.write_text("#!" + sys.executable + "\nimport json,os,sys\n"
                            "with open(os.environ['TRIAGE_TEST_LOG'],'a') as f: f.write(json.dumps(sys.argv)+'\\n')\n"
                            "print('external command forbidden in this test',file=sys.stderr)\nsys.exit(97)\n")
            path.chmod(0o700)

    def stub_clients(self, responses):
        fixture = self.root / "client-responses.json"
        write_json(fixture, responses)
        self.env["TRIAGE_TEST_RESPONSES"] = str(fixture)
        stub = ("#!" + sys.executable + "\nimport json,os,sys\nfrom pathlib import Path\n"
                "name=Path(sys.argv[0]).name\n"
                "with open(os.environ['TRIAGE_TEST_LOG'],'a') as f: f.write(json.dumps([name]+sys.argv[1:])+'\\n')\n"
                "responses=json.load(open(os.environ['TRIAGE_TEST_RESPONSES']))\n"
                "key=json.dumps([name]+sys.argv[1:])\n"
                "if key not in responses:\n print('unexpected or forbidden command: '+key,file=sys.stderr);sys.exit(97)\n"
                "response=responses[key]\n"
                "sys.stdout.write(response.get('stdout',''))\n"
                "sys.stderr.write(response.get('stderr',''))\n"
                "sys.exit(response.get('exit',0))\n")
        for command in ("acli", "gh", "git"):
            path = self.bin / command
            path.write_text(stub)
            path.chmod(0o700)

    def import_manifest(self):
        manifest = self.run / "imports.json"
        write_json(manifest, {"schema_version": 1, "sources": self.packet["sources"]})
        return manifest

    def offline_collect(self, manifest=None, destination=None):
        destination = destination or self.root / "collected"
        result = self.invoke("collect", "--offline", "--imports", manifest or self.import_manifest(),
                             "--run-dir", destination)
        self.assertFalse(self.log.exists(), "Import-only collection called authenticated clients")
        self.assertTrue((destination / "packet.json").exists(), result.stdout + result.stderr)
        return result, json.loads((destination / "packet.json").read_text())

    def test_partial_import_pagination_cannot_be_empty_success(self):
        self.source("inventory", "jira_inventory", "assigned", {
            "issues": [{"id": "101", "key": KEY, "fields": self.issue["fields"]}],
            "startAt": 0, "total": 2, "maxResults": 1,
        })
        result, packet = self.offline_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"], "A missing inventory page must be disclosed")

    def test_imported_github_search_count_requires_all_members(self):
        self.source("inventory", "jira_inventory", "assigned", [])
        self.source("git-identity", "git_identity", "user", {"id": 77, "login": "synthetic-user"})
        self.packet["sources"][-1].update(service="git", site="github.com", identity_ref="git-identity")
        self.source("git-search", "git_search", "author", {
            "total_count": 1001, "incomplete_results": False, "items": [{"id": 90, "number": 3}],
        })
        self.packet["sources"][-1].update(service="git", site="github.com", identity_ref="git-identity")
        result, packet = self.offline_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("author" in gap["source"] for gap in packet["gaps"]))

    def test_import_rejects_forged_hash_and_still_records_failure(self):
        self.source("inventory", "jira_inventory", "assigned", [])
        self.packet["sources"][0]["sha256"] = "0" * 64
        result, packet = self.offline_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"])

    def test_import_path_escape_is_not_read(self):
        self.source("inventory", "jira_inventory", "assigned", [])
        outside = self.root / "outside.json"
        write_json(outside, self.issue)
        source = next(item for item in self.packet["sources"] if item["id"] == "issue")
        source["raw_path"] = "../outside.json"
        source["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        result, packet = self.offline_collect()
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"])

    def native_responses(self, issues):
        responses = {}

        def response(argv, data=None, stdout=None, exit_code=0):
            responses[json.dumps(argv)] = {"stdout": stdout if stdout is not None else json.dumps(data),
                                          "exit": exit_code}

        response(["acli", "jira", "auth", "status"],
                 stdout="Authenticated\nSite: " + SITE + "\nEmail: synthetic@example.invalid\n")
        search = ["acli", "jira", "workitem", "search", "--jql", "assignee = currentUser() ORDER BY key",
                  "--fields", "key,summary,status,assignee", "--json", "--paginate"]
        response(search, issues)
        for role_query in ("reporter = currentUser()", "watcher = currentUser()", "assignee WAS currentUser()"):
            response(["acli", "jira", "workitem", "search", "--jql",
                      role_query + " AND statusCategory != Done AND (assignee != currentUser() OR assignee IS EMPTY) ORDER BY key",
                      "--fields", "key,summary,status,assignee", "--json", "--paginate"], [])
        response(["gh", "api", "--hostname", "github.com", "--method", "GET", "user"],
                 {"id": 77, "login": "synthetic-user"})
        for role in ("author", "assignee", "review-requested", "involves"):
            response(["gh", "api", "--hostname", "github.com", "--method", "GET", "search/issues", "-f",
                      "q=is:pr is:open " + role + ":synthetic-user", "-f", "per_page=100", "-f", "page=1"],
                     {"total_count": 0, "incomplete_results": False, "items": []})
        for issue in issues:
            key = issue["key"]
            if issue["fields"]["status"]["statusCategory"]["key"] == "done":
                continue
            response(["acli", "jira", "workitem", "view", key, "--fields", "*all", "--json"], issue)
            response(["acli", "jira", "workitem", "comment", "list", "--key", key, "--json", "--paginate"], [])
        return responses, search

    def native_collect(self, responses, destination=None):
        self.stub_clients(responses)
        manifest = self.run / "identity-import.json"
        write_json(manifest, {"schema_version": 1, "sources": [self.packet["sources"][0]]})
        destination = destination or self.root / "native-collected"
        result = self.invoke("collect", "--imports", manifest, "--run-dir", destination)
        self.assertTrue((destination / "packet.json").exists(), result.stdout + result.stderr)
        return result, json.loads((destination / "packet.json").read_text()), destination

    def test_native_inventory_has_no_50_item_or_named_status_cutoff(self):
        issues = []
        for index in range(1, 53):
            issue = copy.deepcopy(self.issue)
            issue.update(key="EXAMPLE-" + str(index), id=str(100 + index))
            issue["fields"]["status"]["name"] = "Quiet custom status"
            if index == 52:
                issue["fields"]["status"] = {"id": "99", "name": "Archived successfully", "statusCategory": {"key": "done"}}
            issues.append(issue)
        responses, _ = self.native_responses(issues)
        result, packet, _ = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual({item["key"] for item in packet["inventory"]}, {issue["key"] for issue in issues})
        self.assertFalse(next(item for item in packet["inventory"] if item["key"] == "EXAMPLE-52")["active"])
        self.assertEqual(sum(source["kind"] == "jira_comments" for source in packet["sources"]), 51)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertTrue(all(call[0] in ("acli", "gh") for call in calls))
        self.assertTrue(all("created" not in " ".join(call) for call in calls))

    def test_nonzero_query_exit_with_valid_empty_json_is_failure(self):
        responses, search = self.native_responses([])
        responses[json.dumps(search)] = {"stdout": "[]", "exit": 1}
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("jira_inventory" in gap["source"] for gap in packet["gaps"]))

    def test_native_empty_inventory_is_success(self):
        responses, _ = self.native_responses([])
        result, packet, _ = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual(packet["inventory"], [])
        self.assertEqual(packet["gaps"], [])

    def test_reporter_lead_is_context_without_claiming_ownership_or_deep_crawling(self):
        responses, _ = self.native_responses([])
        context = copy.deepcopy(self.issue)
        context.update(key="EXAMPLE-24", id="124")
        context["fields"]["assignee"] = {"accountId": "another-owner"}
        argv = ["acli", "jira", "workitem", "search", "--jql",
                "reporter = currentUser() AND statusCategory != Done AND (assignee != currentUser() OR assignee IS EMPTY) ORDER BY key",
                "--fields", "key,summary,status,assignee", "--json", "--paginate"]
        responses[json.dumps(argv)]["stdout"] = json.dumps([context])
        result, packet, _ = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        member = next(item for item in packet["inventory"] if item["key"] == "EXAMPLE-24")
        self.assertEqual(member["role"], "context")
        self.assertFalse(any(source["kind"] == "jira_issue" for source in packet["sources"]))

    def test_flat_and_paginated_cli_inventory_shapes_remain_explicit(self):
        responses, search = self.native_responses([self.issue])
        responses[json.dumps(search)]["stdout"] = json.dumps([[self.issue], []])
        result, packet, _ = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        self.assertEqual([item["key"] for item in packet["inventory"]], [KEY])

    def test_selected_online_refresh_replaces_imported_target_without_rewriting_original(self):
        self.source("inventory", "jira_inventory", "assigned", [self.issue])
        manifest = self.import_manifest()
        original = {path: path.read_bytes() for path in self.run.rglob("*.json")}
        fresh = copy.deepcopy(self.issue)
        fresh["fields"]["summary"] = "Current native title after the imported capture"
        responses, _ = self.native_responses([fresh])
        self.stub_clients(responses)
        destination = self.root / "refreshed-selected-target"
        result = self.invoke("collect", "--imports", manifest, "--run-dir", destination, "--issue", KEY)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        packet = json.loads((destination / "packet.json").read_text())
        source = next(source for source in packet["sources"] if source["kind"] == "jira_issue" and source["resource"] == KEY)
        self.assertEqual(json.loads((destination / source["raw_path"]).read_text())["fields"]["summary"],
                         fresh["fields"]["summary"])
        self.assertEqual({path: path.read_bytes() for path in original}, original)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertIn(["acli", "jira", "workitem", "view", KEY, "--fields", "*all", "--json"], calls)
        self.assertIn(["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"], calls)

    def test_comment_last_page_is_retained(self):
        responses, _ = self.native_responses([self.issue])
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        owner_report = {"id": "900", "body": document("Owner confirms unmet acceptance."),
                        "author": {"accountId": "synthetic-owner"}}
        responses[json.dumps(argv)]["stdout"] = json.dumps([[{"id": "1", "body": document("Early note."),
                                                            "author": {"accountId": "synthetic-user"}}], [owner_report]])
        result, packet, destination = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = next(source for source in packet["sources"] if source["kind"] == "jira_comments")
        raw = json.loads((destination / source["raw_path"]).read_text())
        self.assertIn(owner_report, triage.rows(raw, "jira_comments"))

    def test_concatenated_native_comment_objects_require_real_terminal_page(self):
        responses, _ = self.native_responses([self.issue])
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        report = {"id": "900", "body": document("Owner confirms outstanding acceptance."),
                  "author": {"accountId": "synthetic-owner"}}
        pages = [{"comments": [report], "startAt": 0, "maxResults": 50, "total": 1, "isLast": False},
                 {"comments": [], "startAt": 50, "maxResults": 50, "total": 1, "isLast": True}]
        responses[json.dumps(argv)]["stdout"] = "\r\n".join(json.dumps(page) for page in pages) + "\r\n"
        result, packet, destination = self.native_collect(responses)
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        source = next(source for source in packet["sources"] if source["kind"] == "jira_comments")
        self.assertTrue(source["complete"])
        self.assertTrue(source["pagination"]["terminated"])
        self.assertEqual((destination / source["raw_path"]).read_bytes(), responses[json.dumps(argv)]["stdout"].encode())
        self.assertIn(report, triage.rows(pages, "jira_comments"))

    def test_concatenated_comment_objects_reject_offsets_missing_end_and_bad_counts(self):
        report = {"id": "900", "body": document("An accessible comment."),
                  "author": {"accountId": "synthetic-owner"}}
        first = {"comments": [report], "startAt": 0, "maxResults": 50, "total": 1, "isLast": False}
        final = {"comments": [], "startAt": 50, "maxResults": 50, "total": 1, "isLast": True}
        cases = ([first, {**final, "startAt": 0}], [first, {**final, "startAt": 1}],
                 [first, {**final, "isLast": False}], [first],
                 [{**first, "total": 2}, {**final, "total": 2}],
                 [{**first, "total": 2}, {**final, "total": 2, "comments": [report]}])
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        for index, pages in enumerate(cases):
            with self.subTest(pages=pages):
                responses, _ = self.native_responses([self.issue])
                responses[json.dumps(argv)]["stdout"] = "\n".join(json.dumps(page) for page in pages)
                result, packet, _ = self.native_collect(responses, self.root / ("bad-pages-" + str(index)))
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(any("jira_comments" in gap["source"] for gap in packet["gaps"]))

    def test_rendered_comment_strings_cannot_support_audited_native_baseline(self):
        self.candidate()
        self.check()
        self.comments["comments"] = [{"id": "7", "author": "synthetic-owner", "body": "Rendered useful owner evidence."}]
        self.comments["total"] = 1
        next(source for source in self.packet["sources"] if source["id"] == "comments")["fidelity"] = "native"
        self.update_source("comments", self.comments)
        self.review_and_refresh()
        self.check(False)

    def test_rendered_cli_comment_stream_is_retained_as_provisional_evidence(self):
        responses, _ = self.native_responses([self.issue])
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        pages = [{"comments": [{"id": "900", "author": "synthetic-owner", "body": "Useful rendered owner report."}],
                  "startAt": 0, "maxResults": 50, "total": 1, "isLast": False},
                 {"comments": [], "startAt": 50, "maxResults": 50, "total": 1, "isLast": True}]
        responses[json.dumps(argv)]["stdout"] = "\n".join(json.dumps(page) for page in pages)
        result, packet, destination = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0, "Rendered evidence must not be labeled native-ready")
        source = next(source for source in packet["sources"] if source["kind"] == "jira_comments")
        self.assertTrue(source["complete"], "Native pagination can be complete while ADF fidelity is unavailable")
        self.assertEqual(source["fidelity"], "rendered")
        self.assertEqual((destination / source["raw_path"]).read_text(), responses[json.dumps(argv)]["stdout"])
        self.assertTrue(any(gap["source"] == source["id"] for gap in packet["gaps"]))

    def test_malformed_query_output_is_saved_as_incomplete_coverage(self):
        responses, search = self.native_responses([])
        responses[json.dumps(search)]["stdout"] = '{"issues": ['
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("malformed" in gap["reason"] for gap in packet["gaps"]))

    def test_github_incomplete_result_cannot_be_empty_success(self):
        responses, _ = self.native_responses([])
        argv = ["gh", "api", "--hostname", "github.com", "--method", "GET", "search/issues", "-f",
                "q=is:pr is:open author:synthetic-user", "-f", "per_page=100", "-f", "page=1"]
        responses[json.dumps(argv)]["stdout"] = json.dumps({"total_count": 0, "incomplete_results": True, "items": []})
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("git_search" in gap["source"] for gap in packet["gaps"]))

    def test_cli_and_connector_account_mismatch_stays_provisional(self):
        responses, _ = self.native_responses([])
        auth = ["acli", "jira", "auth", "status"]
        responses[json.dumps(auth)]["stdout"] = "Authenticated\nSite: " + SITE + "\nEmail: someoneelse@example.invalid\n"
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"])
        inventory = [source for source in packet["sources"] if source["kind"] == "jira_inventory"]
        self.assertTrue(all(not source.get("identity_ref") for source in inventory))

    def test_hidden_connector_email_does_not_falsely_bind_cli_account(self):
        self.identity.pop("emailAddress")
        self.update_source("identity", self.identity)
        responses, _ = self.native_responses([])
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"])

    def test_worktree_file_and_metacharacter_root_are_read_without_checkout_changes(self):
        checkout = self.root / "work tree; $(literal)"
        checkout.mkdir()
        gitfile = checkout / ".git"
        gitfile.write_text("gitdir: /synthetic/unavailable/worktree\n")
        dirty = checkout / "dirty.txt"
        dirty.write_text("Unrelated user's edits stay here.\n")
        original = (gitfile.read_bytes(), dirty.read_bytes())
        self.source("inventory", "jira_inventory", "assigned", [])
        manifest = self.import_manifest()
        responses = {}
        remote = "origin\tssh://synthetic-user:fake-secret@example.invalid:2222/example/demo.git?token=fake-token (fetch)\n"
        for command, stdout in ((["remote", "-v"], remote),
                                (["status", "--porcelain=v1"], " M dirty.txt\n"),
                                (["branch", "--format=%(refname:short)"], "feature/example\n")):
            responses[json.dumps(["git", "-C", str(checkout)] + command)] = {"stdout": stdout}
        self.stub_clients(responses)
        destination = self.root / "local-discovery"
        result = self.invoke("collect", "--offline", "--imports", manifest, "--run-dir", destination,
                             "--root", checkout)
        self.assertTrue((destination / "packet.json").exists(), result.stdout + result.stderr)
        packet = json.loads((destination / "packet.json").read_text())
        self.assertEqual([entry["path"] for entry in packet["local"] if "path" in entry], [str(checkout)])
        saved_remote = next(entry["remotes"] for entry in packet["local"] if "path" in entry)
        self.assertIn("ssh://example.invalid:2222/example/demo.git", saved_remote)
        self.assertNotIn("fake-secret", saved_remote)
        self.assertNotIn("fake-token", saved_remote)
        self.assertEqual((gitfile.read_bytes(), dirty.read_bytes()), original)
        calls = [json.loads(line) for line in self.log.read_text().splitlines()]
        self.assertEqual(len(calls), 3)
        self.assertTrue(all(call[:3] == ["git", "-C", str(checkout)] for call in calls))

    def test_duplicate_inventory_ids_are_not_silently_deduplicated(self):
        responses, search = self.native_responses([self.issue])
        responses[json.dumps(search)]["stdout"] = json.dumps([self.issue, self.issue])
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(packet["gaps"])

    def test_separate_collectors_do_not_share_files_or_receipts(self):
        self.source("inventory", "jira_inventory", "assigned", [])
        for role in ("reporter", "watcher", "former-assignee"):
            self.source("jira-" + role, "jira_context", role, [])
        self.source("git-identity", "git_identity", "user", {"id": 77, "login": "synthetic-user"})
        git_identity = self.packet["sources"][-1]
        git_identity.update(service="git", site="github.com", identity_ref="git-identity")
        for role in ("author", "assignee", "review-requested", "involves"):
            self.source("git-" + role, "git_search", role, [])
            self.packet["sources"][-1].update(service="git", site="github.com", identity_ref="git-identity")
        manifest = self.import_manifest()
        destinations = [self.root / "concurrent one", self.root / "concurrent two"]
        with concurrent.futures.ThreadPoolExecutor(max_workers=2) as pool:
            results = list(pool.map(lambda dest: self.invoke("collect", "--offline", "--imports", manifest,
                                                            "--run-dir", dest), destinations))
        for destination, result in zip(destinations, results):
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
            packet = json.loads((destination / "packet.json").read_text())
            self.assertEqual(packet["inventory"], [])
            self.assertEqual(packet["gaps"], [])
            self.assertTrue(all((destination / source["raw_path"]).is_file() for source in packet["sources"]))
        self.assertFalse(self.log.exists())

    def source(self, identifier, kind, resource, payload):
        raw = Path("raw") / (identifier + ".json")
        write_json(self.run / raw, payload)
        git = kind.startswith("git_")
        item = {"id": identifier, "service": "git" if git else "jira", "site": "github.com" if git else SITE, "kind": kind,
                "resource": resource, "captured_at": NOW, "schema": "github-rest-v3" if git else "jira-rest-v3",
                "identity_ref": "git-identity" if git else "identity", "raw_path": str(raw),
                "sha256": hashlib.sha256((self.run / raw).read_bytes()).hexdigest(),
                "fingerprint": triage.fingerprint(payload), "complete": True,
                "pagination": {"terminated": True}}
        if kind == "jira_comments":
            item["fidelity"] = "native"
        item["fingerprint"] = triage.source_fingerprint(payload, item)
        self.packet["sources"].append(item)
        return payload

    def update_source(self, identifier, payload):
        item = next(source for source in self.packet["sources"] if source["id"] == identifier)
        write_json(self.run / item["raw_path"], payload)
        item["sha256"] = hashlib.sha256((self.run / item["raw_path"]).read_bytes()).hexdigest()
        item["fingerprint"] = triage.source_fingerprint(payload, item)

    def save(self):
        for name, value in (("packet.json", self.packet), ("decisions.json", self.decisions),
                            ("drafts.json", self.drafts), ("reviews.json", self.reviews)):
            write_json(self.run / name, value)

    def invoke(self, *args):
        return subprocess.run([sys.executable, str(SCRIPT), *map(str, args)], cwd=self.root,
                              env=self.env, text=True, capture_output=True, timeout=15)

    def check(self, success=True):
        self.save()
        result = self.invoke("check", self.run)
        if success:
            self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        else:
            self.assertNotEqual(result.returncode, 0, result.stdout + result.stderr)
            self.assertTrue(result.stdout.strip() or result.stderr.strip(), "Failure must be explained")
        self.assertFalse(self.log.exists(), "Offline check attempted an external command")
        return json.loads(result.stdout or result.stderr)

    def candidate(self, kind="fields", request=None, ready="audited"):
        draft = {"id": "draft-1", "key": KEY, "kind": kind,
                 "request": request or {"fields": {"summary": "Fix the synthetic title"}},
                 "baseline": {"issue": "issue", "comments": "comments", "metadata": "metadata"},
                 "evidence": ["issue", "comments", "metadata"],
                 "decision_inputs": ["identity", "issue", "comments", "metadata"],
                 "preserved_fields": ["description", "customfield_keep"], "ready": ready,
                 "scope_hash": triage.canonical_hash(self.packet["scope"])}
        draft["hash"] = triage.canonical_hash(draft)
        self.drafts = [draft]
        self.decisions[0].update(disposition="candidate", draft_ids=[draft["id"]])
        self.review_and_refresh()
        return draft

    def review_and_refresh(self):
        fingerprints = {source["id"]: source["fingerprint"] for source in self.packet["sources"]}
        self.reviews = [{"reviewer_id": "review-" + lens, "execution_id": "synthetic-execution-" + lens,
                         "lens": lens, "context_mode": "history_free",
                         "draft_id": draft["id"], "draft_hash": draft["hash"],
                         "evidence_fingerprints": fingerprints, "verdict": "accept"}
                        for draft in self.drafts for lens in ("evidence", "preservation")]
        self.packet["refresh"] = {"captured_at": NOW, "source_ids": list(fingerprints),
                                  "fingerprints": fingerprints}

    def artifact_candidate(self, anonymous=False):
        site = "https://cluster.example.invalid"
        if not anonymous:
            principal = "system:serviceaccount:synthetic:read-only"
            self.source("artifact-reader", "artifact_identity", "whoami", principal)
            source = self.packet["sources"][-1]
            source.update(service="kubernetes", site=site, schema="oc-whoami-json-v1")
            source.pop("identity_ref", None)
            self.update_source("artifact-reader", principal)
        artifact = {"apiVersion": "appstudio.redhat.com/v1alpha1", "kind": "Release",
                    "metadata": {"name": "synthetic-stage", "namespace": "synthetic"},
                    "status": {"conditions": [{"type": "Released", "status": "True"}]}}
        self.source("artifact", "artifact", "namespaces/synthetic/releases/synthetic-stage", artifact)
        source = self.packet["sources"][-1]
        source.update(service="kubernetes", site=site, schema="oc-release-json-v1",
                      access="anonymous_public" if anonymous else "authenticated")
        if anonymous:
            source.pop("identity_ref", None)
        else:
            source["identity_ref"] = "artifact-reader"
        self.update_source("artifact", artifact)
        draft = self.candidate()
        extra = ["artifact"] if anonymous else ["artifact-reader", "artifact"]
        draft["evidence"].extend(extra)
        draft["decision_inputs"].extend(extra)
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        return source, artifact

    def test_authenticated_artifact_reader_is_bound_to_supporting_evidence(self):
        self.artifact_candidate()
        self.check()

    def test_artifact_import_preserves_native_payload_and_reader_capture(self):
        _, artifact = self.artifact_candidate()
        self.source("inventory", "jira_inventory", "assigned", [self.issue])
        for role in ("reporter", "watcher", "former-assignee"):
            self.source("jira-" + role, "jira_context", role, [])
        self.source("git-identity", "git_identity", "user", {"id": 77, "login": "synthetic-user"})
        for role in ("author", "assignee", "review-requested", "involves"):
            self.source("git-" + role, "git_search", role, [])
        result, packet = self.offline_collect()
        self.assertEqual(result.returncode, 0, result.stdout + result.stderr)
        for identifier, expected in (("artifact", artifact), ("artifact-reader", "system:serviceaccount:synthetic:read-only")):
            source = next(source for source in packet["sources"] if source["id"] == identifier)
            actual = json.loads((self.root / "collected" / source["raw_path"]).read_text())
            self.assertEqual(actual, expected)
            original = next(source for source in self.packet["sources"] if source["id"] == identifier)
            self.assertEqual((self.root / "collected" / source["raw_path"]).read_bytes(),
                             (self.run / original["raw_path"]).read_bytes())

    def test_declared_anonymous_public_artifact_does_not_invent_a_principal(self):
        self.artifact_candidate(anonymous=True)
        self.check()

    def test_artifact_reader_from_another_site_cannot_authorize_capture(self):
        source, artifact = self.artifact_candidate()
        self.check()
        source["site"] = "https://other-cluster.example.invalid"
        self.update_source("artifact", artifact)
        self.review_and_refresh()
        self.check(False)

    def test_incomplete_supporting_artifact_cannot_be_audited(self):
        source, _ = self.artifact_candidate()
        self.check()
        source["complete"] = False
        self.check(False)

    def test_anonymous_public_capture_cannot_claim_an_authenticated_reader(self):
        source, artifact = self.artifact_candidate()
        source["access"] = "anonymous_public"
        self.update_source("artifact", artifact)
        self.review_and_refresh()
        self.check(False)

    def test_accurate_tracking_needs_no_drafts(self):
        self.check()

    def test_held_issue_is_valid_not_a_forced_batch(self):
        self.decisions[0]["disposition"] = "held"
        self.decisions[0]["reason"] = "Owner evidence is inaccessible."
        self.check()

    def test_minimal_title_edit_is_audited_at_capture_cutoff(self):
        self.candidate()
        self.check()

    def test_tampered_raw_evidence_is_rejected(self):
        self.candidate()
        write_json(self.run / "raw/issue.json", {"key": KEY, "fields": {"summary": "forged"}})
        self.check(False)

    def test_missing_and_duplicate_decisions_are_rejected(self):
        original = copy.deepcopy(self.decisions)
        for decisions in ([], original * 2):
            with self.subTest(decisions=decisions):
                self.decisions = decisions
                self.check(False)

    def test_native_status_category_cannot_be_changed_in_inventory_to_skip_triage(self):
        self.source("inventory", "jira_inventory", "assigned", [self.issue])
        self.packet["inventory"][0]["active"] = False
        self.decisions = []
        self.check(False)

    def test_assigned_specialist_keeps_coverage_and_requires_a_disposition(self):
        self.source("inventory", "jira_inventory", "assigned", [self.issue])
        self.packet["inventory"][0]["role"] = "specialist"
        self.decisions[0]["disposition"] = "specialist"
        self.decisions[0]["reason"] = "Substantive specialist audit is outside this skill."
        self.check()
        self.decisions = []
        self.check(False)

    def test_other_owner_needs_explicit_selected_scope(self):
        self.candidate()
        self.issue["fields"]["assignee"] = {"accountId": "another-user"}
        self.update_source("issue", self.issue)
        self.review_and_refresh()
        self.check(False)

    def test_two_lenses_from_same_reviewer_do_not_make_independent_review(self):
        self.candidate()
        self.reviews[1]["reviewer_id"] = self.reviews[0]["reviewer_id"]
        self.check(False)

    def test_missing_reviewer_identity_is_not_independent_review(self):
        self.candidate()
        del self.reviews[0]["reviewer_id"]
        self.check(False)

    def test_reviewer_execution_is_required_and_cannot_be_shared_by_two_lenses(self):
        self.candidate()
        self.check()
        self.reviews[1]["execution_id"] = self.reviews[0]["execution_id"]
        self.check(False)
        self.review_and_refresh()
        del self.reviews[0]["execution_id"]
        self.check(False)

    def test_inherited_context_does_not_count_as_independent_review(self):
        self.candidate()
        self.reviews[1]["context_mode"] = "inherited"
        self.check(False)

    def test_request_changed_after_review_is_rejected(self):
        draft = self.candidate()
        draft["request"]["fields"]["summary"] = "Different unreviewed title"
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.check(False)

    def test_saved_refresh_is_required_for_audited_claim(self):
        self.candidate()
        del self.packet["refresh"]
        self.check(False)

    def test_final_refresh_cutoff_cannot_precede_captured_inputs(self):
        self.candidate()
        self.packet["refresh"]["captured_at"] = "2025-12-31T23:00:00Z"
        self.check(False)

    def test_invalid_refresh_cutoff_is_rejected(self):
        self.candidate()
        self.packet["refresh"]["captured_at"] = "not-a-timestamp"
        self.check(False)

    def test_changed_comments_invalidate_old_review_and_refresh(self):
        self.candidate()
        self.comments["comments"] = [{"id": "9", "body": document("New blocking owner evidence."),
                                      "author": {"accountId": "synthetic-owner"}}]
        self.comments["total"] = 1
        self.update_source("comments", self.comments)
        self.check(False)

    def test_no_permission_for_edit_cannot_be_audited(self):
        self.candidate()
        self.metadata["permissions"]["EDIT_ISSUES"]["havePermission"] = False
        self.update_source("metadata", self.metadata)
        self.review_and_refresh()
        self.check(False)

    def test_terminal_transition_requires_explicit_valid_resolution(self):
        self.candidate("transition", {"transition": {"id": "31"}})
        self.check(False)

    def test_terminal_transition_with_valid_resolution(self):
        self.candidate("transition", {"transition": {"id": "31"}, "fields": {"resolution": {"id": "1"}}})
        self.check()

    def test_exact_existing_comment_is_not_a_new_delta(self):
        body = document("The merged source already meets the original acceptance.")
        self.metadata["comment_visibility"] = {"requested": None}
        self.update_source("metadata", self.metadata)
        self.candidate("comment", {"body": body})
        self.check()
        self.comments["comments"] = [{"id": "7", "body": body, "author": {"accountId": "synthetic-owner"}}]
        self.comments["total"] = 1
        self.update_source("comments", self.comments)
        self.review_and_refresh()
        self.check(False)

    def test_invalid_resolution_is_rejected_even_with_review(self):
        self.candidate("transition", {"transition": {"id": "31"}, "fields": {"resolution": {"id": "unknown"}}})
        self.check(False)

    def test_incomplete_comments_cannot_support_an_audited_operation(self):
        self.candidate()
        next(source for source in self.packet["sources"] if source["id"] == "comments")["complete"] = False
        self.check(False)

    def test_missing_edit_metadata_allows_only_provisional_draft(self):
        draft = self.candidate(ready="provisional")
        del draft["baseline"]["metadata"]
        draft["evidence"] = ["issue", "comments"]
        draft["decision_inputs"] = ["issue", "comments"]
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.reviews = []
        del self.packet["refresh"]
        self.check()

    def test_orphan_decision_and_unknown_evidence_are_rejected(self):
        for mutate in (lambda: self.decisions.append({"key": "EXAMPLE-999", "disposition": "no_change",
                                                     "reason": "Orphan", "evidence": [], "draft_ids": []}),
                       lambda: self.decisions[0]["evidence"].append("not-collected")):
            with self.subTest(mutate=mutate):
                original = copy.deepcopy(self.decisions)
                mutate()
                self.check(False)
                self.decisions = original

    def test_cross_issue_baseline_cannot_authorize_selected_issue(self):
        self.candidate()
        self.issue["key"] = "EXAMPLE-99"
        self.update_source("issue", self.issue)
        self.review_and_refresh()
        self.check(False)

    def test_description_edit_preserves_cards_unknown_nodes_and_content_links(self):
        old = self.issue["fields"]["description"]
        old["content"].append({"type": "paragraph", "content": [
            {"type": "inlineCard", "attrs": {"url": "https://github.example.invalid/a/b/pull/3"}},
            {"type": "futureNode", "attrs": {"opaque": {"keep": [1, "value"]}}},
        ]})
        self.update_source("issue", self.issue)
        edited = copy.deepcopy(old)
        edited["content"][0]["content"][0]["text"] = "Keep the corrected supported scope."
        draft = self.candidate(request={"fields": {"description": edited}})
        draft["preserved_fields"] = ["customfield_keep"]
        draft["adf_changes"] = {"description": [{"op": "replace", "path": ["content", 0, "content", 0, "text"],
                                                 "old": "Keep this supported scope.",
                                                 "value": "Keep the corrected supported scope."}]}
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check()
        edited["content"][1]["content"][0]["attrs"]["url"] = "https://wrong.invalid/a"
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_declared_text_change_cannot_also_change_unrelated_text_or_opaque_attrs(self):
        old = self.issue["fields"]["description"]
        old["content"].extend([paragraph("Unrelated commitment must remain."),
                               {"type": "futureNode", "attrs": {"opaque": "preserve"}}])
        self.update_source("issue", self.issue)
        edited = copy.deepcopy(old)
        edited["content"][0]["content"][0]["text"] = "Corrected scope."
        draft = self.candidate(request={"fields": {"description": edited}})
        draft["preserved_fields"] = ["customfield_keep"]
        draft["adf_changes"] = {"description": [{"op": "replace", "path": ["content", 0, "content", 0, "text"],
                                                 "old": "Keep this supported scope.", "value": "Corrected scope."}]}
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check()
        for index, change in ((1, paragraph("Removed the unrelated commitment.")),
                              (2, {"type": "futureNode", "attrs": {"opaque": "changed"}})):
            with self.subTest(node=index):
                original = edited["content"][index]
                edited["content"][index] = change
                draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
                self.review_and_refresh()
                self.check(False)
                edited["content"][index] = original

    def test_private_comment_visibility_requires_native_policy(self):
        self.candidate("comment", {"body": document("Restricted owner report."),
                                   "visibility": {"type": "role", "value": "Engineering"}})
        self.check(False)

    def test_comment_document_cannot_contain_scalar_adf_nodes(self):
        self.metadata["comment_visibility"] = {"requested": None}
        self.update_source("metadata", self.metadata)
        body = document("Useful update.")
        draft = self.candidate("comment", {"body": body})
        self.check()
        body["content"].append("not-an-ADF-node")
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_missing_pr_link_in_empty_native_textarea_can_be_drafted(self):
        field = "customfield_10123"
        link = "https://github.com/example/demo/pull/42"
        node = {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {"url": link}}]}
        self.metadata["editmeta"]["fields"][field] = {
            "operations": ["set"], "schema": {"type": "string",
                                               "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textarea"},
        }
        self.update_source("metadata", self.metadata)
        for old in (None, ""):
            with self.subTest(old=old):
                self.issue["fields"][field] = old
                self.update_source("issue", self.issue)
                new = {"version": 1, "type": "doc", "content": [node]}
                draft = self.candidate(request={"fields": {field: new}})
                draft["adf_changes"] = {field: [{"op": "append", "path": ["content"], "value": node}]}
                draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
                self.review_and_refresh()
                self.check()

    def test_existing_pr_link_is_not_appended_again_to_custom_field(self):
        field = "customfield_10123"
        node = {"type": "paragraph", "content": [{"type": "inlineCard", "attrs": {
            "url": "https://github.com/example/demo/pull/42"}}]}
        self.issue["fields"][field] = {"version": 1, "type": "doc", "content": [node]}
        self.update_source("issue", self.issue)
        self.metadata["editmeta"]["fields"][field] = {
            "operations": ["set"], "schema": {"type": "string",
                                               "custom": "com.atlassian.jira.plugin.system.customfieldtypes:textarea"},
        }
        self.update_source("metadata", self.metadata)
        new = {"version": 1, "type": "doc", "content": [node, copy.deepcopy(node)]}
        draft = self.candidate(request={"fields": {field: new}})
        draft["adf_changes"] = {field: [{"op": "append", "path": ["content"], "value": node}]}
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_numeric_custom_field_cannot_be_replaced_with_adf(self):
        field = "customfield_10123"
        self.issue["fields"][field] = 7
        self.update_source("issue", self.issue)
        self.metadata["editmeta"]["fields"][field] = {"operations": ["set"], "schema": {"type": "number"}}
        self.update_source("metadata", self.metadata)
        node = paragraph("A number field is not a native PR link document.")
        draft = self.candidate(request={"fields": {field: {"version": 1, "type": "doc", "content": [node]}}})
        draft["adf_changes"] = {field: [{"op": "append", "path": ["content"], "value": node}]}
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_bogus_writer_command_never_falls_through_to_service(self):
        result = self.invoke("apply", self.run)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(self.log.exists())

    def test_identity_conflict_is_not_silently_combined(self):
        self.candidate()
        self.update_source("identity", {"accountId": "different-authenticated-user", "active": True})
        self.review_and_refresh()
        self.check(False)

    def test_native_pr_identity_must_match_envelope_repository_and_number(self):
        self.packet["identities"]["github"] = {"host": "github.com", "login": "synthetic-user", "id": 77}
        self.source("git-identity", "git_identity", "user", {"id": 77, "login": "synthetic-user"})
        self.packet["sources"][-1].update(service="git", site="github.com", identity_ref="git-identity")
        pr = {"number": 42, "html_url": "https://github.com/example/demo/pull/42", "state": "open",
              "merged_at": None, "draft": True,
              "head": {"sha": "a" * 40, "repo": {"full_name": "example/demo"}},
              "base": {"sha": "b" * 40, "ref": "main", "repo": {"full_name": "example/demo"}}}
        self.source("pr", "git_pr", "example/demo#42", pr)
        self.packet["sources"][-1].update(service="git", site="github.com", identity_ref="git-identity")
        draft = self.candidate()
        draft["evidence"].append("pr")
        draft["decision_inputs"].extend(["git-identity", "pr"])
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check()
        pr.update(number=43, html_url="https://github.com/elsewhere/other/pull/43")
        self.update_source("pr", pr)
        self.review_and_refresh()
        self.check(False)

    def test_packet_raw_path_must_stay_inside_packet(self):
        source = next(item for item in self.packet["sources"] if item["id"] == "issue")
        outside = self.root / "outside.json"
        write_json(outside, self.issue)
        source["raw_path"] = "../outside.json"
        source["sha256"] = hashlib.sha256(outside.read_bytes()).hexdigest()
        self.check(False)

    def test_symlink_raw_file_escape_is_rejected(self):
        outside = self.root / "outside.json"
        write_json(outside, self.issue)
        inside = self.run / "raw/issue.json"
        inside.unlink()
        inside.symlink_to(outside)
        self.check(False)

    def test_conflicting_duplicate_sources_are_rejected(self):
        changed = copy.deepcopy(self.issue)
        changed["fields"]["summary"] = "Contradictory copy"
        self.source("second-issue", "jira_issue", KEY, changed)
        self.check(False)

    def test_baselines_cannot_be_omitted_from_reviewed_and_refreshed_inputs(self):
        draft = self.candidate()
        draft["decision_inputs"] = ["issue"]
        draft["evidence"] = ["issue"]
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_readiness_change_after_review_invalidates_hash(self):
        draft = self.candidate(ready="provisional")
        draft["ready"] = "audited"
        self.check(False)

    def test_dangling_native_identity_reference_is_rejected(self):
        self.packet["sources"][1]["identity_ref"] = "missing-authentication"
        self.check(False)

    def test_same_key_from_another_site_cannot_supply_baseline(self):
        self.candidate()
        next(source for source in self.packet["sources"] if source["id"] == "issue")["site"] = "https://other.invalid"
        self.check(False)

    def test_comments_and_metadata_must_belong_to_target_issue(self):
        self.candidate()
        for identifier in ("comments", "metadata"):
            with self.subTest(source=identifier):
                source = next(item for item in self.packet["sources"] if item["id"] == identifier)
                source["resource"] = "EXAMPLE-99"
                self.check(False)
                source["resource"] = KEY


    def test_per_page_comment_deficit_cannot_be_compensated_by_later_overrun(self):
        comments = [{"id": str(number), "body": document("Accessible report " + str(number)),
                     "author": {"accountId": "synthetic-owner"}} for number in (1, 3, 4)]
        pages = [{"comments": comments[:1], "startAt": 0, "maxResults": 2, "total": 3, "isLast": False},
                 {"comments": comments[1:], "startAt": 2, "maxResults": 2, "total": 3, "isLast": True}]
        responses, _ = self.native_responses([self.issue])
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        responses[json.dumps(argv)]["stdout"] = "\n".join(json.dumps(page) for page in pages)
        result, packet, _ = self.native_collect(responses)
        self.assertNotEqual(result.returncode, 0)
        self.assertTrue(any("jira_comments" in gap["source"] for gap in packet["gaps"]))

    def test_singleton_native_comment_page_needs_total_and_initial_offset(self):
        comment = {"id": "9", "body": document("A visible comment."), "author": {"accountId": "synthetic-owner"}}
        cases = ({"comments": [comment], "startAt": 0, "maxResults": 50, "isLast": True},
                 {"comments": [comment], "startAt": 50, "maxResults": 50, "isLast": True},
                 {"comments": [comment], "total": 1, "startAt": 50, "maxResults": 50, "isLast": True})
        argv = ["acli", "jira", "workitem", "comment", "list", "--key", KEY, "--json", "--paginate"]
        for index, page in enumerate(cases):
            with self.subTest(page=page):
                responses, _ = self.native_responses([self.issue])
                responses[json.dumps(argv)]["stdout"] = json.dumps(page)
                result, packet, _ = self.native_collect(responses, self.root / ("bad-singleton-" + str(index)))
                self.assertNotEqual(result.returncode, 0)
                self.assertTrue(any("jira_comments" in gap["source"] for gap in packet["gaps"]))

    def test_whitespace_execution_alias_is_not_an_independent_context(self):
        self.candidate()
        self.reviews[1]["execution_id"] = self.reviews[0]["execution_id"] + " "
        self.check(False)

    def test_changed_user_scope_invalidates_existing_draft_and_reviews(self):
        self.candidate()
        self.check()
        self.packet["scope"]["selected_context_keys"].append("EXAMPLE-999")
        self.check(False)

    def test_source_identity_change_invalidates_review_even_when_native_payload_matches(self):
        self.candidate()
        self.check()
        for source in self.packet["sources"]:
            source["site"] = "https://another-site.example.invalid"
            self.update_source(source["id"], self.identity if source["id"] == "identity" else
                               {"issue": self.issue, "comments": self.comments, "metadata": self.metadata}[source["id"]])
        self.packet["identities"]["jira"]["site"] = "https://another-site.example.invalid"
        self.packet["inventory"][0]["site"] = "https://another-site.example.invalid"
        self.check(False)

    def test_ordinary_native_transport_and_raw_format_changes_preserve_semantic_review(self):
        self.candidate()
        self.check()
        transport = copy.deepcopy(self.issue)
        transport.update(self="https://new-gateway.invalid/issue/101", lastViewed="later transport metadata")
        source = next(source for source in self.packet["sources"] if source["id"] == "issue")
        raw = self.run / source["raw_path"]
        raw.write_text(json.dumps(transport, indent=4) + "\n", encoding="utf-8")
        source["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        source["fingerprint"] = triage.source_fingerprint(transport, source)
        self.check()

    def test_artifact_same_json_with_different_raw_bytes_invalidates_old_review(self):
        source, artifact = self.artifact_candidate()
        self.check()
        raw = self.run / source["raw_path"]
        raw.write_text(json.dumps(artifact, indent=4) + "\n", encoding="utf-8")
        source["sha256"] = hashlib.sha256(raw.read_bytes()).hexdigest()
        source["fingerprint"] = triage.source_fingerprint(artifact, source)
        self.check(False)


    def test_authenticated_reader_must_be_a_reviewed_decision_input(self):
        draft = self.candidate()
        self.check()
        draft["decision_inputs"].remove("identity")
        draft["hash"] = triage.canonical_hash({key: value for key, value in draft.items() if key != "hash"})
        self.review_and_refresh()
        self.check(False)

    def test_reader_principal_change_invalidates_old_reviews_for_selected_context(self):
        self.packet["scope"]["selected_context_keys"] = [KEY]
        self.candidate()
        self.check()
        self.identity["accountId"] = "another-native-reader"
        self.packet["identities"]["jira"]["account_id"] = "another-native-reader"
        self.update_source("identity", self.identity)
        self.check(False)

    def test_changed_coverage_proof_invalidates_review_even_when_raw_content_matches(self):
        self.candidate()
        self.check()
        source = next(source for source in self.packet["sources"] if source["id"] == "comments")
        source["pagination"]["capture_method"] = "new-complete-native-capture"
        self.update_source("comments", self.comments)
        self.check(False)

    def test_boolean_schema_version_and_nonboolean_completeness_are_invalid(self):
        self.packet["schema_version"] = True
        self.check(False)
        self.packet["schema_version"] = 1
        source = next(source for source in self.packet["sources"] if source["id"] == "issue")
        source["complete"] = "true"
        self.update_source("issue", self.issue)
        self.check(False)


class PureContracts(unittest.TestCase):
    def test_native_identity_and_pagination_numbers_have_exact_json_types(self):
        cases = (("jira_identity", {"accountId": True}),
                 ("git_identity", {"login": "synthetic-user", "id": True}),
                 ("jira_comments", {"comments": [], "startAt": 0, "maxResults": 50, "total": False, "isLast": True}))
        for kind, payload in cases:
            with self.subTest(kind=kind):
                with self.assertRaises(ValueError):
                    triage.validate_shape(kind, payload)

    def test_empty_object_is_not_native_check_evidence(self):
        with self.assertRaises(ValueError):
            triage.validate_shape("git_checks", {})

    def test_card_and_mark_links_are_evidence_and_unknown_nodes_are_preserved(self):
        adf = {"type": "doc", "version": 1, "content": [
            {"type": "heading", "attrs": {"level": 2}, "content": [{"type": "text", "text": "Scope"}]},
            {"type": "paragraph", "content": [
                {"type": "inlineCard", "attrs": {"url": "https://github.example.invalid/a/b/pull/3"}},
                {"type": "text", "text": " link", "marks": [{"type": "link", "attrs": {
                    "href": "https://docs.example.invalid/preserved?x=1&y=2"}}]},
                {"type": "futureExtension", "attrs": {"url": "literal untouched", "opaque": [1, 2]}},
            ]},
            paragraph("Acceptance"),
        ]}
        original = copy.deepcopy(adf)
        links = triage.adf_links(adf)
        self.assertIn("https://github.example.invalid/a/b/pull/3", links)
        self.assertIn("https://docs.example.invalid/preserved?x=1&y=2", links)
        text = triage.adf_text(adf)
        self.assertIn("Scope", text)
        self.assertIn("Acceptance", text)
        self.assertNotIn("ScopeAcceptance", text)
        self.assertEqual(adf, original, "Evidence extraction changed native ADF")

    def test_fingerprint_ignores_transport_but_preserves_native_content_urls(self):
        original = {"lastViewed": "old", "self": "https://gateway1.invalid/status/3", "description": document("Supported.")}
        transport = copy.deepcopy(original)
        transport.update(lastViewed="new", self="https://gateway2.invalid/status/3")
        self.assertEqual(triage.fingerprint(original), triage.fingerprint(transport))
        changed = copy.deepcopy(original)
        changed["description"]["content"].append({"type": "inlineCard", "attrs": {"url": "https://content.invalid/a"}})
        self.assertNotEqual(triage.fingerprint(original), triage.fingerprint(changed))

    def test_transport_named_attributes_inside_adf_remain_meaningful(self):
        baseline = {"fields": {"description": {"type": "doc", "version": 1, "content": [
            {"type": "futureNode", "attrs": {"self": "content identity", "iconUrl": "https://content.invalid/icon",
                                            "lastViewed": "literal user content"}},
        ]}}}
        for name in ("self", "iconUrl", "lastViewed"):
            with self.subTest(attribute=name):
                changed = copy.deepcopy(baseline)
                changed["fields"]["description"]["content"][0]["attrs"][name] = "changed content"
                self.assertNotEqual(triage.fingerprint(baseline), triage.fingerprint(changed))

    def test_transport_named_keys_in_custom_json_fields_are_content(self):
        baseline = {"fields": {"customfield_10666": {
            "self": "https://content.invalid/source", "iconUrl": "https://content.invalid/icon", "lastViewed": "literal value"}}}
        for name in ("self", "iconUrl", "lastViewed"):
            with self.subTest(key=name):
                changed = copy.deepcopy(baseline)
                changed["fields"]["customfield_10666"][name] = "changed user content"
                self.assertNotEqual(triage.fingerprint(baseline), triage.fingerprint(changed))


if __name__ == "__main__":
    suite = unittest.defaultTestLoader.loadTestsFromModule(sys.modules[__name__])
    if suite.countTestCases() == 0:
        raise SystemExit("No personal-jira-triage tests were collected")
    result = unittest.TextTestRunner(verbosity=2).run(suite)
    raise SystemExit(0 if result.wasSuccessful() else 1)
