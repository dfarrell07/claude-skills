#!/usr/bin/env python3
"""Collect read-only work evidence and render validated, source-linked reports."""

import argparse
from datetime import datetime, timedelta
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit


# Collection must not write bytecode into the installed skill or a scanned checkout.
sys.dont_write_bytecode = True

from evidence import (  # noqa: E402
    HASH, HOST, LIMIT, PAGE_SIZE, UTC, command, decode_json, integer, iso,
    read_json, require, timestamp, valid_item, is_pr_url,
)
from local_sources import collect_local  # noqa: E402
from capabilities import collect_local_safely  # noqa: E402
from github_sources import Github  # noqa: E402
from jira_sources import collect_jira, add_referenced_jira  # noqa: E402
from work_links import associate_work, complete_references, github_items, validate_links  # noqa: E402
from extended_evidence import validate_github, validate_jira, jira_reference  # noqa: E402


def collect(days_text, roots, now=None):
    require(re.fullmatch(r"[0-9]+", days_text) is not None and int(days_text) > 0,
            "Days must be a positive integer")
    days = int(days_text)
    end = (now or datetime.now(UTC)).replace(microsecond=0)
    try:
        start = end - timedelta(days=days)
    except OverflowError as error:
        raise ValueError("Days exceed the supported date range") from error
    warnings = []

    def warn(message):
        warnings.append(message)
        print(f"Warning: {message}", file=sys.stderr)

    host = os.environ.get("GH_HOST", "github.com").lower()
    github_host_valid = HOST.fullmatch(host) is not None
    if not github_host_valid:
        warn("Configured GitHub host is invalid; GitHub reads are unavailable.")
        host = "github.com"
    github = {"prs": [], "issues": []}
    github_status = "unavailable"
    github_user = ""
    searches = {}
    gh_env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}

    def api(*args):
        return decode_json(command(["gh", "api", "--hostname", host, *args], env=gh_env))

    if github_host_valid and shutil.which("gh"):
        try:
            identity = api("user")
            github_user = identity["login"]
            require(isinstance(github_user, str) and re.fullmatch(r"[A-Za-z0-9_-]+", github_user),
                    "Invalid GitHub login")
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
            warn("GitHub identity lookup failed; GitHub activity is unavailable.")
            github_user = ""
    elif github_host_valid:
        warn("GitHub CLI is missing; GitHub activity is unavailable.")
    if github_user:
        github_status = "ok"
        for kind, qualifier in (("prs", "pr"), ("issues", "issue")):
            info = {"total_count": None, "returned_count": 0, "incomplete_results": False, "pages": 0, "status": "ok"}
            searches[kind] = info
            seen = set()
            first_total = None
            for page in range(1, LIMIT // PAGE_SIZE + 1):
                try:
                    response = api("--method", "GET", "search/issues", "-f",
                                   f"q=is:{qualifier} author:{github_user} updated:{iso(start)}..{iso(end)}",
                                   "-f", "sort=updated", "-f", "order=desc", "-F", f"per_page={PAGE_SIZE}",
                                   "-F", f"page={page}")
                    require(isinstance(response, dict) and integer(response.get("total_count"))
                            and type(response.get("incomplete_results")) is bool
                            and isinstance(response.get("items"), list), "Malformed search response")
                    info["pages"] += 1
                    total = response["total_count"]
                    info["total_count"] = total
                    if first_total is None:
                        first_total = total
                    elif first_total != total:
                        info["status"] = "partial"
                        warn(f"GitHub {kind} changed while paging; coverage may be incomplete.")
                    if response["incomplete_results"]:
                        info["incomplete_results"] = True
                        info["status"] = "partial"
                        warn(f"GitHub {kind} search reported incomplete results.")
                    if total > LIMIT and page == 1:
                        info["status"] = "partial"
                        warn(f"GitHub {kind} exceeds the {LIMIT}-result cap; coverage is incomplete.")
                    rows = response["items"]
                    require(len(rows) <= PAGE_SIZE, "Oversized search page")
                    invalid_rows = 0
                    for row in rows:
                        try:
                            require(isinstance(row, dict), "Malformed search item")
                            url = row["html_url"]
                            parts = urlsplit(url).path.strip("/").split("/")
                            require(len(parts) == 4, "Malformed item URL")
                            is_pr = "pull_request" in row
                            require(is_pr == (kind == "prs"), "Wrong search item kind")
                            if is_pr:
                                require(isinstance(row["pull_request"], dict) and "merged_at" in row["pull_request"],
                                        "Missing PR merge evidence")
                            merged = row["pull_request"]["merged_at"] if is_pr else None
                            require(row.get("body") is None or isinstance(row["body"], str), "Invalid GitHub body")
                            item = {"repo": "/".join(parts[:2]), "number": row["number"], "title": row["title"],
                                    "state": "merged" if merged else row["state"], "url": url,
                                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                                    "closed_at": row["closed_at"], "merged_at": merged,
                                    "body": row.get("body") or "", "draft": row.get("draft", False),
                                    "author_login": (row.get("user") or {}).get("login")}
                            require(isinstance(item["body"], str), "Invalid GitHub body")
                            valid_item(item, kind, host)
                            require(start <= timestamp(item["updated_at"]) <= end, "Item outside requested window")
                            key = (item["repo"], item["number"])
                            require(key not in seen, "Duplicate search item")
                            seen.add(key)
                            github[kind].append(item)
                        except (ValueError, KeyError, TypeError, AttributeError):
                            info["status"] = "partial"
                            invalid_rows += 1
                    if invalid_rows:
                        warn(f"GitHub {kind} page {page} contained {invalid_rows} invalid, duplicate, or out-of-window items; they were skipped.")
                    expected = min(PAGE_SIZE, max(0, min(total, LIMIT) - (page - 1) * PAGE_SIZE))
                    if len(rows) < expected:
                        info["status"] = "partial"
                        warn(f"GitHub {kind} returned fewer rows than its result count; coverage is incomplete.")
                        break
                    if page * PAGE_SIZE >= min(total, LIMIT):
                        break
                except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
                    info["status"] = "partial"
                    warn(f"GitHub {kind} search failed; this source is missing or incomplete, not empty.")
                    break
            info["returned_count"] = len(github[kind])
            if info["status"] != "ok":
                github_status = "partial"

    local, local_status, local_capability = collect_local_safely(
        collect_local, roots, start, end, warn, api=api if github_user else None,
        identity=identity if github_user else {})
    jira = collect_jira(start, end, warn)
    discovery = {}
    if github_user:
        ledger = Github(api, host, github_user, start, end, warn)
        ledger.discover(github, jira["linked_prs"])
        ledger.associate_branches(local["branches"], local["metadata"]["repositories"], github)
        discovery = ledger.coverage
        if any(info["status"] != "ok" for info in discovery.values()):
            github_status = "partial"
    expansion = complete_references(jira, github,
                                    lambda keys: add_referenced_jira(jira, keys, start, end, warn),
                                    (lambda urls: ledger.add_linked_prs(github, urls)) if github_user else None)
    if github_user:
        if any(info["status"] != "ok" for info in ledger.coverage.values()):
            github_status = "partial"
    associate_work(jira, github)
    return {
        "metadata": {"days": days, "since_date": start.date().isoformat(), "to_date": end.date().isoformat(),
                     "since_at": iso(start), "to_at": iso(end), **local["metadata"],
                     "local_status": local_status, "capabilities": {"local_git": local_capability},
                     "github_discovery": discovery, "reference_expansion": expansion,
                     "github_status": github_status, "github_user": github_user, "github_host": host,
                     "github_searches": searches, "search_limit": LIMIT, "warnings": warnings},
        "github": github, "jira": jira, "commits": local["commits"], "branches": local["branches"],
        "unattributed_commits": local["unattributed_commits"],
    }


def validate(data, source=None):
    require(isinstance(data, dict), "Capture must be an object")
    metadata = data.get("metadata")
    require(isinstance(metadata, dict), "Missing metadata")
    for key in ("days", "repos_searched", "repos_with_commits"):
        require(integer(metadata.get(key), 1 if key == "days" else 0), f"Invalid metadata.{key}")
    for key in ("since_date", "to_date"):
        require(isinstance(metadata.get(key), str) and re.fullmatch(r"\d{4}-\d{2}-\d{2}", metadata[key]),
                f"Invalid metadata.{key}")
        datetime.strptime(metadata[key], "%Y-%m-%d")
    require(metadata["since_date"] <= metadata["to_date"], "Reversed date window")
    start = timestamp(metadata["since_at"]) if "since_at" in metadata else None
    end = timestamp(metadata["to_at"]) if "to_at" in metadata else None
    if start or end:
        require(start is not None and end is not None and end - start == timedelta(days=metadata["days"]),
                "Invalid rolling date window")
        require(start.date().isoformat() == metadata["since_date"] and end.date().isoformat() == metadata["to_date"],
                "Date labels do not match timestamps")
    warnings = metadata.get("warnings", [])
    require(isinstance(warnings, list) and all(isinstance(w, str) and w for w in warnings), "Invalid coverage warnings")
    for key, states in (("github_status", {"ok", "partial", "unavailable"}),
                        ("local_status", {"ok", "partial", "unavailable"})):
        if key in metadata:
            require(metadata[key] in states and (metadata[key] == "ok" or warnings), f"Invalid {key} or missing coverage gap")
    if "repos_skipped" in metadata:
        require(integer(metadata["repos_skipped"]) and metadata["repos_skipped"] <= metadata["repos_searched"],
                "Invalid skipped repository count")
        require(not metadata["repos_skipped"] or warnings, "Skipped repositories need a coverage gap")
    host = metadata.get("github_host", "github.com")
    require(isinstance(host, str) and HOST.fullmatch(host), "Invalid GitHub host")
    github = data.get("github")
    require(isinstance(github, dict), "Missing GitHub evidence")
    evidence = {}
    for kind in ("prs", "issues"):
        require(isinstance(github.get(kind), list), f"Invalid github.{kind}")
        for item in github[kind]:
            valid_item(item, kind, host)
            require(item["url"] not in evidence, "Duplicate GitHub item")
            evidence[item["url"]] = item
            if start and item.get("updated_at"):
                require(start <= timestamp(item["updated_at"]) <= end, "GitHub item outside capture window")
    validate_github(data, evidence, start, end)
    jira_items = validate_jira(data, start, end)
    validate_links(data)
    commits = data.get("commits")
    require(isinstance(commits, dict), "Missing commit evidence")
    require(metadata["repos_with_commits"] == len(commits) <= metadata["repos_searched"], "Repository counts do not match evidence")
    for repo, entries in commits.items():
        require(isinstance(repo, str) and Path(repo).is_absolute() and isinstance(entries, list) and entries,
                "Invalid commit repository")
        seen = set()
        for entry in entries:
            require(isinstance(entry, dict) and isinstance(entry.get("hash"), str) and HASH.fullmatch(entry["hash"])
                    and isinstance(entry.get("message"), str), "Invalid commit evidence")
            require(entry["hash"] not in seen, "Duplicate commit in repository")
            seen.add(entry["hash"])
            if entry.get("authored_at") is not None:
                timestamp(entry["authored_at"])
            if entry.get("committed_at") is not None:
                when = timestamp(entry["committed_at"])
                if start:
                    require(start <= when <= end, "Commit outside capture window")
    if "repositories" in metadata:
        entries = metadata["repositories"]
        require(isinstance(entries, list) and len(entries) == metadata["repos_searched"], "Invalid repository inventory")
        paths, common_dirs = set(), set()
        for entry in entries:
            require(isinstance(entry, dict) and isinstance(entry.get("path"), str)
                    and Path(entry["path"]).is_absolute() and isinstance(entry.get("common_dir"), str)
                    and Path(entry["common_dir"]).is_absolute(), "Invalid repository inventory path")
            require(entry["path"] not in paths and entry["common_dir"] not in common_dirs, "Duplicate repository inventory")
            paths.add(entry["path"])
            common_dirs.add(entry["common_dir"])
            require(entry.get("status") in {"ok", "skipped"}, "Invalid repository collection status")
            if "tips" in entry:
                require(isinstance(entry["tips"], list) and all(isinstance(tip, str) and HASH.fullmatch(tip)
                        for tip in entry["tips"]), "Invalid reference snapshot")
            require(isinstance(entry.get("identities"), list)
                    and all(isinstance(email, str) and email for email in entry["identities"]), "Invalid repository identities")
            require(isinstance(entry.get("paths"), list) and entry["path"] in entry["paths"]
                    and all(isinstance(path, str) and Path(path).is_absolute() for path in entry["paths"]),
                    "Invalid discovered worktree paths")
            if entry["path"] in commits:
                require(entry["status"] == "ok", "Skipped repository contains commits")
                require(all(commit.get("author_email") in entry["identities"] for commit in commits[entry["path"]]),
                        "Commit author is absent from collected identities")
        require(set(commits).issubset(paths), "Commit repository is absent from inventory")
        require(sum(entry["status"] == "skipped" for entry in entries) == metadata.get("repos_skipped", 0),
                "Skipped repository count disagrees with inventory")
    if "branches" in data:
        require(isinstance(data["branches"], list), "Invalid branch inventory")
        inventory = {entry["path"]: entry for entry in metadata.get("repositories", [])}
        for branch in data["branches"]:
            require(isinstance(branch, dict) and branch.get("repo") in inventory
                    and isinstance(branch.get("hash"), str) and HASH.fullmatch(branch["hash"]), "Invalid branch snapshot")
            require(branch.get("object_type") in {"commit", "tree", "blob", "tag"}
                    and isinstance(branch.get("refs"), list) and branch["refs"], "Invalid branch refs")
            refs = {ref["name"]: ref for ref in inventory[branch["repo"]].get("refs", [])}
            worktrees = {"worktree:" + tree["path"]: tree.get("head")
                         for tree in inventory[branch["repo"]].get("worktrees", [])}
            require(all((name in refs and branch["hash"] in {refs[name]["hash"], refs[name].get("peeled_hash")})
                        or (name in worktrees and worktrees[name] == branch["hash"])
                        for name in branch["refs"]), "Branch snapshot is absent from ref inventory")
            require(branch.get("pr_status") in {"unchecked", "not_applicable", "matched", "no_match", "partial", "unsupported"},
                    "Invalid branch PR coverage")
    if "unattributed_commits" in data:
        leads = data["unattributed_commits"]
        require(isinstance(leads, list), "Invalid authorship leads")
        inventory = {entry["path"] for entry in metadata.get("repositories", [])}
        for lead in leads:
            require(isinstance(lead, dict) and lead.get("repo") in inventory
                    and isinstance(lead.get("hash"), str) and HASH.fullmatch(lead["hash"])
                    and isinstance(lead.get("author_email"), str) and lead["author_email"], "Invalid authorship lead")
    if "github_searches" in metadata:
        searches = metadata["github_searches"]
        require(isinstance(searches, dict), "Invalid GitHub search coverage")
        if metadata.get("github_status") != "unavailable":
            require(set(searches) == {"prs", "issues"}, "Missing GitHub search coverage")
        for kind, info in searches.items():
            require(kind in {"prs", "issues"} and isinstance(info, dict), "Invalid GitHub search metadata")
            require(info.get("status") in {"ok", "partial"} and integer(info.get("pages"))
                    and type(info.get("incomplete_results")) is bool, "Invalid GitHub search status")
            require(integer(info.get("returned_count")) and info["returned_count"] == len(github[kind]),
                    "GitHub search count disagrees with evidence")
            total = info.get("total_count")
            require(total is None or integer(total), "Invalid GitHub total count")
            if info["status"] == "ok":
                require(total == len(github[kind]) and not info["incomplete_results"], "Incomplete search marked complete")
            else:
                require(metadata.get("github_status") == "partial" and warnings, "Partial search needs a coverage gap")
    if "analysis" not in data:
        require(source is None, "Source comparison is only for enriched captures")
        return
    require(source is not None, "An enriched capture requires its original source capture")
    validate(source)
    require(set(data) == set(source) | {"analysis"}, "Enrichment changed capture sections")
    for key, original in source.items():
        require(data.get(key) == original, f"Enrichment changed source {key}")
    analysis = data["analysis"]
    require(isinstance(analysis, dict) and isinstance(analysis.get("themes"), list)
            and isinstance(analysis.get("in_progress"), list), "Invalid analysis")

    def reference(item):
        require(isinstance(item, dict), "Invalid analysis citation")
        require(integer(item.get("number"), 1) and isinstance(item.get("url"), str), "Invalid analysis citation identity")
        original = evidence.get(item.get("url"))
        require(original is not None and all(item.get(key) == original[key] for key in ("repo", "number", "url")),
                "Analysis citation is absent from source evidence")
        return original

    for theme in analysis["themes"]:
        require(isinstance(theme, dict) and all(isinstance(theme.get(key), str) and theme[key].strip()
                for key in ("name", "description")) and isinstance(theme.get("items"), list), "Invalid theme")
        for item in theme["items"]:
            reference(item)
        jira_refs = theme.get("jira", [])
        require(isinstance(jira_refs, list), "Invalid Jira citations")
        for item in jira_refs:
            require(isinstance(item, dict) and item.get("key") in jira_items
                    and item.get("url") == jira_items[item["key"]]["url"], "Jira citation absent from capture")
        branch_refs = theme.get("branches", [])
        require(isinstance(branch_refs, list), "Invalid branch citations")
        for item in branch_refs:
            require(isinstance(item, dict) and any(item.get("repo") == branch["repo"]
                    and item.get("hash") == branch["hash"] for branch in data.get("branches", [])),
                    "Branch citation absent from capture")
        local = theme.get("commits", [])
        require(isinstance(local, list), "Invalid local commit citations")
        for item in local:
            require(isinstance(item, dict) and isinstance(item.get("repo"), str)
                    and any(entry["hash"] == item.get("hash") for entry in commits.get(item["repo"], [])),
                    "Analysis commit is absent from source evidence")
        require(theme["items"] or local or jira_refs or branch_refs, "A theme needs at least one source citation")
    for item in analysis["in_progress"]:
        require(isinstance(item, dict) and isinstance(item.get("description"), str) and item["description"].strip(),
                "Invalid in-progress item")
        if "key" in item:
            require(item["key"] in jira_items and item.get("url") == jira_items[item["key"]]["url"]
                    and jira_items[item["key"]].get("status_category") in {"new", "indeterminate"}, "In-progress Jira must be active")
        else:
            require(reference(item)["state"] == "open", "In-progress citation must refer to an open item")


def markdown(text):
    # Keep untrusted text on one line; encode HTML and escape Markdown syntax.
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in str(text))
    text = " ".join(text.split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~])", r"\\\1", text)


def link(item):
    return f"[{markdown(item['repo'])}#{item['number']}]({item['url']})"


def contextual_jira_links(item):
    """Retain explicit native browse URLs without inventing a Jira identity."""
    if not is_pr_url(item['url']):
        return []
    result = {}
    for field in ('title', 'body'):
        for match in re.finditer(r'https://[^\s<>"\[\]()]+', item.get(field, '')):
            original = match.group().rstrip('.,;:!?`\'’”')
            parts = urlsplit(original)
            if (HOST.fullmatch(parts.netloc) and parts.username is None and parts.password is None
                    and re.fullmatch(r'/browse/[A-Z][A-Z0-9_]*-[1-9][0-9]*', parts.path, re.I)):
                try:
                    if parts.port is not None and not 1 <= parts.port <= 65535:
                        continue
                except ValueError:
                    continue
                url = 'https://' + parts.netloc + parts.path
                result[url] = {'url': url, 'source_url': original, 'proof': 'github_' + field + '_url',
                               'evidence_url': item['url']}
    return [result[url] for url in sorted(result)]


def topic_github_items(data, topic):
    by_url = {item['url']: item for item in reversed(github_items(data['github']))}
    selected = {item['url']: by_url[item['url']] for item in topic.get('items', [])}
    refs = {(item['repo'], item['hash']) for item in topic.get('commits', []) + topic.get('branches', [])}
    for branch in data.get('branches', []):
        if (branch['repo'], branch['hash']) in refs:
            for pr in branch.get('prs', []):
                selected[pr['url']] = by_url[pr['url']]
    return list(selected.values())


def work_urls(data, topic):
    """Return only captured citations and their validated counterpart links."""
    jira = {item['key']: item for item in data.get('jira', {}).get('issues', [])}
    ticket_urls, change_urls, context_urls = set(), set(), set()

    def ticket(item, include_prs):
        ticket_urls.add(item['url'])
        if include_prs:
            change_urls.update(link['url'] for link in item.get('pr_links', []))
        context_urls.update(link['url'] for link in item.get('review_links', []))

    def github(item):
        change_urls.add(item['url'])
        ticket_urls.update(relation['url'] for relation in contextual_jira_links(item))
        for relation in item.get('jira_links', []):
            ticket(jira[relation['key']], False)

    for item in topic_github_items(data, topic):
        github(item)
    for reference in topic.get('jira', []):
        ticket(jira[reference['key']], True)
    return sorted(ticket_urls) + sorted(change_urls - ticket_urls) + sorted(context_urls - ticket_urls - change_urls)


def fallback_topics(data, limit=12):
    """Select a small deterministic list from event proof, never backlog roles."""
    metadata = data['metadata']
    start = timestamp(metadata['since_at']) if metadata.get('since_at') else None
    end = timestamp(metadata['to_at']) if metadata.get('to_at') else None
    items = {item['url']: item for item in reversed(github_items(data['github']))}
    jira = {item['key']: item for item in data.get('jira', {}).get('issues', [])}
    candidates = {}

    def add(key, priority, when, topic):
        rank = priority, -when.timestamp(), key
        if key not in candidates or rank < candidates[key][0]:
            candidates[key] = rank, topic

    def github_topic(item, description):
        return {'description': description, 'items': [{key: item[key] for key in ('repo', 'number', 'url')}]}

    for item in data['github']['prs']:
        if item.get('merged_at') and start is not None and start <= timestamp(item['merged_at']) <= end:
            add(item['url'], 0, timestamp(item['merged_at']), github_topic(item, 'Merged: ' + item['title']))
    actions = {'opened': (1, 'Opened'), 'review': (2, 'Reviewed'),
               'review_comment': (3, 'Discussed'), 'issue_comment': (3, 'Discussed'),
               'review_comment_edited': (3, 'Updated discussion on'),
               'issue_comment_edited': (3, 'Updated discussion on'),
               'cross-referenced': (4, 'Referenced'), 'closed': (4, 'Closed'),
               'reopened': (4, 'Reopened')}
    for event in data['github'].get('activity', []):
        item = items[event['item_url']]
        priority, verb = actions.get(event['action'], (4, 'Updated'))
        add(item['url'], priority, timestamp(event['at']), github_topic(item, verb + ': ' + item['title']))
    for event in data.get('jira', {}).get('activity', []):
        item = jira[event['key']]
        title = 'restricted Jira ticket' if item['sensitive'] else item['title']
        verb = 'Commented on' if event['kind'] == 'comment_created' else 'Edited a comment on'
        add(item['url'], 3, timestamp(event['occurred_at']),
            {'description': verb + ': ' + title, 'items': [],
             'jira': [{key: item[key] for key in ('key', 'url')}]})
    identities = {item['path']: item['identities'] for item in metadata.get('repositories', [])}
    seen_hashes = set()
    for repo, entries in sorted(data['commits'].items()):
        verified = [entry for entry in entries if entry.get('author_email') in identities.get(repo, [])
                    and entry.get('committed_at') and start is not None
                    and start <= timestamp(entry['committed_at']) <= end]
        verified.sort(key=lambda entry: (timestamp(entry['committed_at']), entry['hash']), reverse=True)
        entry = next((entry for entry in verified if entry['hash'] not in seen_hashes), None)
        seen_hashes.update(entry['hash'] for entry in verified)
        if entry:
            topic = {'description': 'Local change in ' + Path(repo).name + ': ' + entry['message'], 'items': [],
                     'commits': [{'repo': repo, 'hash': entry['hash']}]}
            urls = work_urls(data, topic)
            if not any(url in candidates for url in urls):
                add(repo, 5, timestamp(entry['committed_at']), topic)
    return [topic for _, topic in sorted(candidates.values(), key=lambda value: value[0])[:limit]]


def render(data, source=None, detailed=False):
    if detailed:
        return render_detailed(data, source)
    validate(data, source)
    topics = list(data['analysis']['themes']) if 'analysis' in data else fallback_topics(data)
    represented = {url for topic in topics for url in work_urls(data, topic)}
    for ongoing in data.get('analysis', {}).get('in_progress', []):
        if ongoing['url'] in represented:
            continue
        topic = {'description': 'Ongoing: ' + ongoing['description'], 'items': []}
        if 'key' in ongoing:
            topic['jira'] = [{key: ongoing[key] for key in ('key', 'url')}]
        else:
            topic['items'] = [{key: ongoing[key] for key in ('repo', 'number', 'url')}]
        topics.append(topic)
        represented.update(work_urls(data, topic))
    lines = []
    for topic in topics:
        lines.append('- ' + markdown(topic['description']))
        for url in work_urls(data, topic):
            # Autolink brackets retain the displayed raw URL when repository
            # paths could otherwise activate emphasis or strikethrough.
            lines.append('  - ' + (f'<{url}>' if any(char in url for char in '_~') else url))
    if not topics:
        lines.append('No supported work found in the available evidence.')
    known_jira_urls = {item['url'] for item in data.get('jira', {}).get('issues', [])}
    contextual = set()
    for topic in topics:
        for item in topic_github_items(data, topic):
            contextual.update(link['url'] for link in contextual_jira_links(item))
    notes = []
    if contextual - known_jira_urls:
        notes.append('Jira state is unverified for contextual links.')
    states = [('GitHub', data['metadata'].get('github_status')),
              ('Jira', data.get('jira', {}).get('metadata', {}).get('status')),
              ('local Git', data['metadata'].get('local_status'))]
    gaps = [name + ' ' + status for name, status in states if status in {'partial', 'unavailable'}]
    if gaps:
        notes.append('Coverage is incomplete: ' + '; '.join(gaps) + '.')
    elif data['metadata'].get('warnings'):
        notes.append('Coverage is incomplete; some work may be missing.')
    if notes:
        lines += ['', ' '.join(notes)]
    return '\n'.join(lines)


def render_detailed(data, source=None):
    validate(data, source)
    m = data["metadata"]
    prs, issues = data["github"]["prs"], data["github"]["issues"]
    by_url = {item["url"]: item for item in reversed(github_items(data["github"]))}
    jira_by_key = {item["key"]: item for item in data.get("jira", {}).get("issues", [])}

    def linked(item):
        original = by_url.get(item["url"], item)
        related = original.get("jira_links", [])
        suffix = " · " + ", ".join(jira_reference(entry) for entry in related) if related else (
            " (Jira: no verified link found)" if is_pr_url(item["url"]) else "")
        return link(item) + suffix

    def jira_work(item):
        original = jira_by_key.get(item["key"], item)
        related = original.get("pr_links", [])
        links = []
        for entry in related:
            parts = urlsplit(entry["url"]).path.strip("/").split("/")
            links.append(link({"repo": "/".join(parts[:2]), "number": int(parts[3]), "url": entry["url"]}))
        seen_reviews = set()
        for entry in original.get("review_links", []):
            if entry["url"] in seen_reviews:
                continue
            seen_reviews.add(entry["url"])
            number = urlsplit(entry["url"]).path.rsplit("/", 1)[1]
            links.append(f"[GitLab MR !{number}]({entry['url']}) (referenced; state/ownership unverified)")
        return jira_reference(item) + (" · " + ", ".join(links) if links else " (PR: no verified link found)")

    lines = [f"# Work Summary: Last {m['days']} Days ({m['since_date']} to {m['to_date']})", "",
             "GitHub scope: recently updated authored items, older open work, role context, and verified personal events.",
             "Updates and review requests alone do not establish personal work during this period.",
             "Private report: linked Jira context may include restricted tickets; restricted titles are hidden.",
             "Comment edits require native editor proof; missing editor data and deleted events remain outside verified activity.",
             "Local scope: configured or account-verified author emails across recorded refs and registered worktrees.", ""]
    if "since_at" in m:
        lines += [f"Window (UTC, inclusive): {m['since_at']} to {m['to_at']}.", ""]
    if m.get("warnings"):
        lines += [f"Coverage is incomplete: {len(m['warnings'])} recorded warnings. "
                  "Full coverage details follow the work log.", ""]
    lines += ["## Executive Summary", ""]
    if "analysis" in data:
        lines += ["### Key Accomplishments", ""]
        for theme in data["analysis"]["themes"]:
            citations = [linked(item) for item in theme["items"]]
            citations += [f"{markdown(item['repo'])} `{item['hash'][:12]}`" for item in theme.get("commits", []) + theme.get("branches", [])]
            citations += [jira_work(item) for item in theme.get("jira", [])]
            lines.append(f"- **{markdown(theme['name'])}**: {markdown(theme['description'])} ({', '.join(citations)})")
        if not data["analysis"]["themes"]:
            lines.append("No evidence-backed accomplishments identified in this capture.")
        lines += ["", "### In Progress", ""]
        for item in data["analysis"]["in_progress"]:
            citation = jira_work(item) if "key" in item else linked(item)
            lines.append(f"- {markdown(item['description'])} ({citation})")
        if not data["analysis"]["in_progress"]:
            lines.append("No open items selected for this summary.")
        lines.append("")
    counts = {state: sum(pr["state"] == state for pr in prs) for state in ("merged", "open", "closed")}
    commit_count = sum(len(entries) for entries in data["commits"].values())
    lines += ["### Activity Stats", "",
              f"- {len(prs)} PRs ({counts['merged']} currently merged, {counts['open']} open, "
              f"{counts['closed']} closed without merge) | {len(issues)} issues | "
              f"{commit_count} local commits across {len(data['commits'])} repos",
              "- PR counts describe current state; merged totals are not necessarily merges during this period.",
              "", "---", "", "## Detailed Work Log", "", "### GitHub Activity", ""]
    for title, entries in (("Pull Requests", prs), ("Issues", issues)):
        lines += [f"#### {title} ({len(entries)} total)", ""]
        lines += [f"- {linked(item)} {markdown(item['title'])} ({item['state']})" for item in entries]
        if not entries:
            lines.append(f"No authored {title.lower()} found in this capture. Check the coverage details for any gaps.")
        lines.append("")
    for kind in ("prs", "issues"):
        entries = data["github"].get("context_" + kind, [])
        if entries:
            lines += [f"#### Additional GitHub {kind.title()} Context ({len(entries)})", "",
                      "Includes older open work and involvement leads; role matches do not establish weekly activity.", ""]
            for item in entries:
                roles = ", ".join(markdown(role) for role in item["roles"])
                lines.append(f"- {linked(item)} {markdown(item['title'])} ({item['state']}; {roles})")
            lines.append("")
    if "activity" in data["github"]:
        events = data["github"]["activity"]
        lines += [f"#### Verified GitHub Actor Events ({len(events)})", ""]
        for event in events:
            action = event['action'] + (": " + event['review_state'] if event.get('review_state') else "")
            reference = event.get('source', {}).get('issue')
            source_link = f"; [referencing item]({reference['html_url']})" if reference else ""
            lines.append(f"- {event['at']} [{markdown(event['repo'])}#{event['number']}]({event['url']}) {markdown(action)} — {linked(by_url[event['item_url']])}{source_link}")
        if not events:
            lines.append("No verified personal events captured; check coverage gaps before interpreting absence.")
        lines.append("")
    if "jira" in data:
        jira = data["jira"]
        entries = jira.get("issues", [])
        lines += ["### Jira Inventory and Participation", "",
                  f"Source status: {jira['metadata']['status']}; {len(entries)} tickets captured. Assignment and watcher roles are context, not weekly accomplishments.", ""]
        for item in entries:
            title = markdown(item['title'])
            if item["sensitive"]:
                title = ("Restricted ticket (details retained in private capture)" if item.get("sensitivity_verified")
                         else "Private ticket (sensitivity unverified; details retained in capture)")
            lines.append(f"- {jira_work(item)} {title} ({markdown(item.get('status', 'unknown'))}; {', '.join(markdown(role) for role in item['roles'])})")
        lines += ["", f"Verified Jira actor events: {len(jira.get('activity', []))}.", ""]
        for event in jira.get("activity", []):
            lines.append(f"- {event['occurred_at']} [{markdown(event['key'])}]({event['url']}) {markdown(event['kind'])} — {jira_work(jira_by_key[event['key']])}")
        lines.append("")
    lines += ["### Git Commits", "", f"Searched {m['repos_searched']} repositories; found commits in {len(data['commits'])}.", ""]
    for repo, entries in data["commits"].items():
        lines += [f"#### {markdown(repo)}", ""]
        for entry in entries:
            dates = ""
            if entry.get("authored_at", m.get("since_at", "")) < m.get("since_at", ""):
                dates = f" (authored {entry['authored_at']}; committed {entry['committed_at']})"
            lines.append(f"- `{entry['hash'][:12]}` {markdown(entry['message'])}{dates}")
        lines.append("")
    if not data["commits"]:
        lines += ["No matching local commits found.", ""]
    if "branches" in data:
        branches = data["branches"]
        ref_count = sum(len(entry.get("refs", [])) for entry in m.get("repositories", []))
        selected = [item for item in branches if item["object_type"] != "commit" or item.get("backup")
                    or (item.get("recent_tip") and item.get("contained_in_cached_default") is not True)]
        lines += ["### Local Branch and Backup Inventory", "",
                  f"Recorded {ref_count} named refs and {len(branches)} relevant snapshots; {len(selected)} backup/recent snapshots are listed below.",
                  "Older branch context remains in the saved evidence. Cached default-branch containment does not establish PR or release delivery.", ""]
        for item in selected:
            names = ", ".join(markdown(name) for name in item["refs"])
            associations = ", ".join(linked(pr) for pr in item.get("prs", []))
            lines.append(f"- {markdown(item['repo'])}: {names} — `{item['hash'][:12]}` "
                         f"({item['object_type']}; PR association: {item['pr_status']})" + (f" {associations}" if associations else ""))
        lines += ["", "PR no_match means completed exact-snapshot lookups found no association; partial/unchecked/unsupported remain unresolved. It does not prove unpublished work."]
        lines.append("")
    if data.get("unattributed_commits"):
        lines += ["### Unverified Authorship Leads", "",
                  "These name-matching commits are not credited as your work without identity evidence.", ""]
        for item in data["unattributed_commits"]:
            lines.append(f"- {markdown(item['repo'])} `{item['hash'][:12]}` {markdown(item['author_email'])}")
        lines.append("")
    if m.get("warnings"):
        lines += ["## Coverage Gaps", "", *[f"- {markdown(warning)}" for warning in m["warnings"]], ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    collector = modes.add_parser("collect", help="Read local Git and authored GitHub work")
    collector.add_argument("days", nargs="?", default="7")
    collector.add_argument("roots", nargs="*")
    renderer = modes.add_parser("render", help="Validate evidence and render Markdown")
    renderer.add_argument("--detailed", action="store_true", help="Include the complete evidence and coverage audit")
    renderer.add_argument("capture")
    renderer.add_argument("source", nargs="?", help="Original capture; required for enriched input")
    args = parser.parse_args()
    try:
        if args.mode == "collect":
            data = collect(args.days, args.roots)
            validate(data)
            output = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
        else:
            output = render(read_json(args.capture), read_json(args.source) if args.source else None,
                            detailed=args.detailed) + "\n"
    except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
