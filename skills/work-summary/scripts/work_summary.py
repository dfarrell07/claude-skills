#!/usr/bin/env python3
"""Collect read-only work evidence and render validated, source-linked reports."""

import argparse
from datetime import datetime, timedelta, timezone
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import unicodedata
from urllib.parse import urlsplit


UTC = timezone.utc
LIMIT = 1000
PAGE_SIZE = 100
HASH = re.compile(r"[0-9a-f]{7,64}\Z")
REPO = re.compile(r"[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+\Z")
HOST = re.compile(r"[A-Za-z0-9.-]+(?::[0-9]+)?\Z")
GIT_ENV = {**os.environ, "GIT_TERMINAL_PROMPT": "0", "GIT_NO_LAZY_FETCH": "1", "GIT_ALLOW_PROTOCOL": ""}
for _key in ("GIT_DIR", "GIT_WORK_TREE", "GIT_COMMON_DIR", "GIT_INDEX_FILE",
             "GIT_OBJECT_DIRECTORY", "GIT_ALTERNATE_OBJECT_DIRECTORIES"):
    GIT_ENV.pop(_key, None)


def require(condition, message):
    if not condition:
        raise ValueError(message)


def integer(value, minimum=0):
    return type(value) is int and value >= minimum


def timestamp(value):
    require(isinstance(value, str), "Timestamp must be a string")
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timestamp needs a timezone")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat(timespec="seconds").replace("+00:00", "Z")


def unique_object(pairs):
    result = {}
    for key, value in pairs:
        require(key not in result, f"Duplicate JSON key: {key}")
        result[key] = value
    return result


def decode_json(raw):
    def invalid_number(value):
        raise ValueError(f"Invalid JSON number: {value}")

    return json.loads(raw, object_pairs_hook=unique_object, parse_constant=invalid_number)


def read_json(filename):
    return decode_json(Path(filename).read_text(encoding="utf-8"))


def command(args, timeout=120, env=None, stdin=None):
    return subprocess.run(args, capture_output=True, check=True, timeout=timeout,
                          env=env, encoding="utf-8", input=stdin).stdout


def git(repo, *args, stdin=None):
    return command(["git", "--no-optional-locks", "-C", str(repo), *args], env=GIT_ENV, stdin=stdin)


def valid_item(item, kind, host):
    require(isinstance(item, dict), "GitHub item must be an object")
    repo = item.get("repo")
    require(isinstance(repo, str) and REPO.fullmatch(repo)
            and all(part not in (".", "..") for part in repo.split("/")), "Invalid GitHub repository")
    require(integer(item.get("number"), 1), "Invalid GitHub item number")
    require(isinstance(item.get("title"), str), "Missing GitHub item title")
    require(item.get("state") in ({"open", "closed", "merged"} if kind == "prs" else {"open", "closed"}),
            "Invalid GitHub state")
    route = "pull" if kind == "prs" else "issues"
    expected = f"https://{host}/{repo}/{route}/{item['number']}"
    require(item.get("url") == expected, "GitHub citation does not match repository, number, and host")
    for field in ("created_at", "updated_at"):
        if field in item:
            require(item[field] is not None, f"Missing {field}")
    for field in ("created_at", "updated_at", "closed_at", "merged_at"):
        if item.get(field) is not None:
            timestamp(item[field])
    if "merged_at" in item:
        require(bool(item["merged_at"]) == (item["state"] == "merged"), "Merge state and timestamp disagree")
    if "closed_at" in item:
        require(bool(item["closed_at"]) == (item["state"] != "open"), "Closed state and timestamp disagree")
    if item.get("updated_at"):
        for field in ("created_at", "closed_at", "merged_at"):
            if item.get(field):
                require(timestamp(item[field]) <= timestamp(item["updated_at"]), "Inconsistent GitHub timestamps")


def collect(days_text, roots, now=None):
    require(re.fullmatch(r"[0-9]+", days_text) is not None and int(days_text) > 0,
            "Days must be a positive integer")
    days = int(days_text)
    end = (now or datetime.now(UTC)).replace(microsecond=0)
    try:
        start = end - timedelta(days=days)
    except OverflowError as error:
        raise ValueError("Days exceed the supported date range") from error
    require(shutil.which("git"), "git is required but not installed")
    version = command(["git", "--version"]).strip()
    match = re.search(r"(\d+)\.(\d+)", version)
    require(match and tuple(map(int, match.groups())) >= (2, 37), "Git 2.37 or newer is required")
    warnings = []

    def warn(message):
        warnings.append(message)
        print(f"Warning: {message}", file=sys.stderr)

    host = os.environ.get("GH_HOST", "github.com").lower()
    require(HOST.fullmatch(host), "Invalid GH_HOST")
    github = {"prs": [], "issues": []}
    github_status = "unavailable"
    github_user = ""
    searches = {}
    gh_env = {**os.environ, "GH_PROMPT_DISABLED": "1", "GH_PAGER": "cat"}

    def api(*args):
        return decode_json(command(["gh", "api", "--hostname", host, *args], env=gh_env))

    if shutil.which("gh"):
        try:
            identity = api("user")
            github_user = identity["login"]
            require(isinstance(github_user, str) and re.fullmatch(r"[A-Za-z0-9_-]+", github_user),
                    "Invalid GitHub login")
        except (subprocess.SubprocessError, OSError, ValueError, KeyError, TypeError):
            warn("GitHub identity lookup failed; GitHub activity is unavailable.")
            github_user = ""
    else:
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
                            item = {"repo": "/".join(parts[:2]), "number": row["number"], "title": row["title"],
                                    "state": "merged" if merged else row["state"], "url": url,
                                    "created_at": row["created_at"], "updated_at": row["updated_at"],
                                    "closed_at": row["closed_at"], "merged_at": merged}
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

    local_warning_start = len(warnings)
    repositories = {}
    inspected_paths = set()

    def add_repo(directory, marked=False):
        if directory in inspected_paths:
            return
        inspected_paths.add(directory)
        try:
            canonical = Path(git(directory, "rev-parse", "--show-toplevel").removesuffix("\n")).resolve()
            common = Path(git(canonical, "rev-parse", "--git-common-dir").removesuffix("\n"))
            common = (common if common.is_absolute() else canonical / common).resolve()
            entry = repositories.setdefault(str(common), {"path": str(canonical), "common_dir": str(common),
                                                           "identities": set(), "paths": set()})
            if str(canonical) in entry["paths"]:
                return
            entry["paths"].add(str(canonical))
            try:
                email = git(canonical, "config", "--get", "user.email").strip()
                require(email and "\n" not in email, "Missing author email")
                entry["identities"].add(email)
            except (subprocess.SubprocessError, ValueError):
                warn(f"No Git author email configured for {canonical}; that worktree's identity is unavailable.")
        except (subprocess.SubprocessError, OSError, UnicodeError):
            if marked:
                warn(f"Cannot inspect Git repository at {directory}; local coverage is incomplete.")

    scan_roots = [Path(root).expanduser() for root in roots] if roots else [
        Path.home() / relative for relative in ("go/src", "konflux", "projects", "repos")
    ] + [Path.cwd()]
    for root in scan_roots:
        try:
            if not root.is_dir():
                if roots:
                    warn(f"Requested scan root is missing or not a directory: {root}")
                continue
            root = root.resolve()
        except (OSError, RuntimeError):
            warn(f"Cannot access scan root {root}; local coverage is incomplete.")
            continue
        # A failed probe inside an enclosing repository is a coverage gap too;
        # walking below the requested subdirectory cannot find its ancestor marker.
        marked = any(os.path.lexists(ancestor / ".git") for ancestor in (root, *root.parents))
        add_repo(root, marked=marked)
        for directory, dirs, files in os.walk(root, onerror=lambda error: warn(
                f"Cannot scan {error.filename}; local coverage is incomplete.")):
            current = Path(directory)
            if ".git" in dirs or ".git" in files:
                add_repo(current, marked=True)
            dirs[:] = sorted(name for name in dirs if name not in (".git", "node_modules", "vendor"))
            if len(current.relative_to(root).parts) >= 4:
                dirs[:] = []

    commits = {}
    skipped = 0
    for entry in repositories.values():
        entry["identities"] = sorted(entry["identities"])
        entry["paths"] = sorted(entry["paths"])
        if not entry["identities"]:
            skipped += 1
            entry["status"] = "skipped"
            continue
        try:
            tips = sorted(set(git(entry["path"], "rev-list", "--all", "--no-walk=unsorted").splitlines()))
            require(all(HASH.fullmatch(tip) for tip in tips), "Invalid reference tip")
            entry["tips"] = tips
            # Disable human log presentation and mailmap rewriting: %ae and the
            # configured identities describe raw authors, and the stream is UTF-8.
            raw = git(entry["path"], "log", "--stdin", "--fixed-strings", "--no-use-mailmap",
                      "--no-show-signature", "--no-color", "--encoding=UTF-8",
                      *[f"--author=<{email}>" for email in entry["identities"]],
                      f"--since-as-filter={iso(start)}", f"--until={iso(end)}", "-z",
                      "--format=%H%x00%ct%x00%ae%x00%s", stdin="\n".join(tips) + "\n") if tips else ""
            fields = raw.split("\0")
            require(fields.pop() == "" and len(fields) % 4 == 0, "Malformed Git log output")
            found = []
            for index in range(0, len(fields), 4):
                commit_hash, epoch, email, subject = fields[index:index + 4]
                require(HASH.fullmatch(commit_hash), "Invalid commit hash")
                committed_at = datetime.fromtimestamp(int(epoch), UTC)
                require(start <= committed_at <= end and email in entry["identities"], "Unexpected Git commit")
                found.append({"hash": commit_hash, "message": subject, "author_email": email,
                              "committed_at": iso(committed_at)})
            if found:
                commits[entry["path"]] = found
            entry["status"] = "ok"
        except (subprocess.SubprocessError, OSError, ValueError, OverflowError):
            skipped += 1
            entry["status"] = "skipped"
            warn(f"Git log failed for {entry['path']}; commits were not collected.")
    return {
        "metadata": {"days": days, "since_date": start.date().isoformat(), "to_date": end.date().isoformat(),
                     "since_at": iso(start), "to_at": iso(end), "repos_searched": len(repositories),
                     "repos_skipped": skipped, "repos_with_commits": len(commits),
                     "local_status": "partial" if len(warnings) > local_warning_start else "ok",
                     "scan_roots": [str(root.absolute()) for root in scan_roots],
                     "repositories": list(repositories.values()), "github_status": github_status,
                     "github_user": github_user, "github_host": host, "github_searches": searches,
                     "search_limit": LIMIT, "warnings": warnings},
        "github": github, "commits": commits,
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
    for key, states in (("github_status", {"ok", "partial", "unavailable"}), ("local_status", {"ok", "partial"})):
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
    for key in ("metadata", "github", "commits"):
        require(data.get(key) == source.get(key), f"Enrichment changed source {key}")
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
        local = theme.get("commits", [])
        require(isinstance(local, list), "Invalid local commit citations")
        for item in local:
            require(isinstance(item, dict) and isinstance(item.get("repo"), str)
                    and any(entry["hash"] == item.get("hash") for entry in commits.get(item["repo"], [])),
                    "Analysis commit is absent from source evidence")
        require(theme["items"] or local, "A theme needs at least one source citation")
    for item in analysis["in_progress"]:
        require(isinstance(item, dict) and isinstance(item.get("description"), str) and item["description"].strip(),
                "Invalid in-progress item")
        require(reference(item)["state"] == "open", "In-progress citation must refer to an open item")


def markdown(text):
    # Keep untrusted text on one line; encode HTML and escape Markdown syntax.
    text = "".join(" " if unicodedata.category(char) in {"Cc", "Cf", "Cs"} else char for char in str(text))
    text = " ".join(text.split())
    text = text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")
    return re.sub(r"([\\`*_{}\[\]()#+.!|~])", r"\\\1", text)


def link(item):
    return f"[{markdown(item['repo'])}#{item['number']}]({item['url']})"


def render(data, source=None):
    validate(data, source)
    m = data["metadata"]
    prs, issues = data["github"]["prs"], data["github"]["issues"]
    lines = [f"# Work Summary: Last {m['days']} Days ({m['since_date']} to {m['to_date']})", "",
             "GitHub scope: authored PRs/issues updated during this period. Updates may come from other people or automation.",
             "Local scope: commits matching configured author emails across all local refs, grouped once per common Git directory.", ""]
    if "since_at" in m:
        lines += [f"Window (UTC, inclusive): {m['since_at']} to {m['to_at']}.", ""]
    if m.get("warnings"):
        lines += ["## Coverage Gaps", "", *[f"- {markdown(warning)}" for warning in m["warnings"]], ""]
    lines += ["## Executive Summary", ""]
    if "analysis" in data:
        lines += ["### Key Accomplishments", ""]
        for theme in data["analysis"]["themes"]:
            citations = [link(item) for item in theme["items"]]
            citations += [f"{markdown(item['repo'])} `{item['hash'][:12]}`" for item in theme.get("commits", [])]
            lines.append(f"- **{markdown(theme['name'])}**: {markdown(theme['description'])} ({', '.join(citations)})")
        if not data["analysis"]["themes"]:
            lines.append("No evidence-backed accomplishments identified in this capture.")
        lines += ["", "### In Progress", ""]
        for item in data["analysis"]["in_progress"]:
            lines.append(f"- {markdown(item['description'])} ({link(item)})")
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
        lines += [f"- {link(item)} {markdown(item['title'])} ({item['state']})" for item in entries]
        if not entries:
            lines.append(f"No authored {title.lower()} found in this capture. Coverage gaps, if any, are listed above.")
        lines.append("")
    lines += ["### Git Commits", "", f"Searched {m['repos_searched']} repositories; found commits in {len(data['commits'])}.", ""]
    for repo, entries in data["commits"].items():
        lines += [f"#### {markdown(repo)}", ""]
        lines += [f"- `{entry['hash'][:12]}` {markdown(entry['message'])}" for entry in entries]
        lines.append("")
    if not data["commits"]:
        lines += ["No matching local commits found.", ""]
    return "\n".join(lines)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    modes = parser.add_subparsers(dest="mode", required=True)
    collector = modes.add_parser("collect", help="Read local Git and authored GitHub work")
    collector.add_argument("days", nargs="?", default="7")
    collector.add_argument("roots", nargs="*")
    renderer = modes.add_parser("render", help="Validate evidence and render Markdown")
    renderer.add_argument("capture")
    renderer.add_argument("source", nargs="?", help="Original capture; required for enriched input")
    args = parser.parse_args()
    try:
        if args.mode == "collect":
            data = collect(args.days, args.roots)
            validate(data)
            output = json.dumps(data, indent=2, ensure_ascii=True) + "\n"
        else:
            output = render(read_json(args.capture), read_json(args.source) if args.source else None) + "\n"
    except (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.SubprocessError) as error:
        print(f"Error: {error}", file=sys.stderr)
        return 1
    sys.stdout.write(output)
    return 0


if __name__ == "__main__":
    sys.exit(main())
