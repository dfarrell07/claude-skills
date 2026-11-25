"""Optional, read-only Jira discovery through the installed Atlassian CLI.

Search membership is a discovery lead, not proof of work by the authenticated
person. Only native comment actors and timestamps establish activity events.
All returned evidence is private capture data, including sensitivity markers.
"""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import json
import os
import re
import shutil
import subprocess
import sys
from urllib.parse import quote, urlsplit

sys.dont_write_bytecode = True

from evidence import (  # noqa: E402
    HOST, command, decode_json, integer, iso, require, timestamp,
)


KEY = re.compile(r"[A-Z][A-Z0-9_]*-[1-9][0-9]*\Z")
BACKEND = re.compile(r"jira-prod-[a-z0-9]+(?:-[a-z0-9]+)*\.prod\.atl-paas\.net\Z")
PR_URL = re.compile(r"https://[A-Za-z0-9.-]+(?::[0-9]+)?/[A-Za-z0-9_.-]+/[A-Za-z0-9_.-]+/pull/[1-9][0-9]*(?=$|[\s/?#\"<>),.;\]])")
SEARCH_FIELDS = "key,summary,status,assignee,reporter,labels"
DETAIL_FIELDS = "*all"
ERRORS = (OSError, ValueError, KeyError, TypeError, OverflowError, subprocess.SubprocessError)


def _failure(error):
    """Keep private native failure evidence without inventing an HTTP status."""
    result = {"type": type(error).__name__, "message": str(error)}
    if isinstance(error, subprocess.CalledProcessError):
        result["returncode"] = error.returncode
        result["stderr"] = error.stderr if isinstance(error.stderr, str) else ""
        result["stdout"] = error.stdout if isinstance(error.stdout, str) else ""
    elif isinstance(error, subprocess.TimeoutExpired):
        result["timeout"] = error.timeout
    return result


def _source_strings(value, sensitive=False):
    if isinstance(value, str):
        yield value, sensitive
    elif isinstance(value, dict):
        fields = value.get("fields", value)
        labels = fields.get("labels", []) if isinstance(fields, dict) else []
        restricted_labels = isinstance(labels, list) and any(
            isinstance(label, str) and ("security" in label.casefold() or label.upper().startswith("CVE-"))
            for label in labels)
        sensitive = sensitive or bool(value.get("visibility")) or bool(
            isinstance(fields, dict) and fields.get("security")) or restricted_labels
        for child in value.values():
            yield from _source_strings(child, sensitive)
    elif isinstance(value, list):
        for child in value:
            yield from _source_strings(child, sensitive)


def _quoted(value):
    """JQL string quoting; never interpolate an identity as executable JQL."""
    return json.dumps(value, ensure_ascii=True)


def _auth_context(auth):
    site_match = re.search(r"^\s*Site:\s*(\S+)\s*$", auth, re.M)
    user_match = re.search(r"^\s*Email:\s*(\S+)\s*$", auth, re.M)
    require(site_match and user_match, "Missing authenticated site/email")
    site = site_match.group(1)
    parts = urlsplit(site if "://" in site else "https://" + site)
    require(parts.scheme == "https" and parts.netloc and HOST.fullmatch(parts.netloc)
            and not parts.path.strip("/") and not parts.query and not parts.fragment,
            "Invalid authenticated site")
    user = user_match.group(1)
    require(re.fullmatch(r"[^\s\x00-\x1f]+@[^\s\x00-\x1f]+", user), "Invalid auth email")
    return parts.netloc.lower(), user


def _normalize(row, site):
    require(isinstance(row, dict) and isinstance(row.get("key"), str)
            and KEY.fullmatch(row["key"]) and isinstance(row.get("fields"), dict), "Invalid Jira issue")
    fields = row["fields"]
    status = fields.get("status")
    require(isinstance(fields.get("summary"), str) and isinstance(status, dict)
            and isinstance(status.get("name"), str) and isinstance(status.get("statusCategory"), dict)
            and status["statusCategory"].get("key") in {"new", "indeterminate", "done"}, "Invalid Jira issue fields")
    if row.get("self") is not None:
        require(isinstance(row["self"], str), "Invalid Jira issue self URL")
        parts = urlsplit(row["self"])
        # acli can return Atlassian's production REST backend in `self` while
        # authentication names the tenant's browser site. Keep that API URL as
        # provenance, never as the tenant or browser citation authority.
        backend = (site.endswith(".atlassian.net") and parts.hostname is not None
                   and BACKEND.fullmatch(parts.hostname) is not None
                   and parts.netloc.lower() == parts.hostname)
        require(parts.scheme == "https" and parts.username is None and parts.password is None
                and not parts.query and not parts.fragment
                and (parts.netloc.lower() == site or backend), "Jira issue/site mismatch")
        route = re.fullmatch(r"/rest/api/(?:2|3)/issue/([1-9][0-9]*)", parts.path)
        require(route is not None and not isinstance(row.get("id"), bool)
                and str(row.get("id")) == route[1], "Jira REST resource identity mismatch")
    return {"key": row["key"], "url": f"https://{site}/browse/{row['key']}",
            "title": fields["summary"], "status": status["name"],
            "status_category": status["statusCategory"]["key"], "roles": [],
            "sensitive": True, "sensitivity_verified": False, "updated_at": None,
            "detail_status": "not_selected", "pr_links": [], "source": row}


def _custom_pr_links(item, fields, source_kind, github_host):
    result = {}
    for field_id, value in fields.items():
        if not re.fullmatch(r'customfield_[1-9][0-9]*', field_id):
            continue
        for text, private in _source_strings(value, item.get('sensitive', True)):
            for url in PR_URL.findall(text.replace('|', ' ')):
                parts = urlsplit(url)
                if (parts.netloc.casefold() != github_host.casefold()
                        or any(part in {'.', '..'} for part in parts.path.split('/')[1:3])):
                    continue
                url = 'https://' + parts.netloc.lower() + parts.path
                row = {'url': url, 'proof': 'custom_field', 'field_id': field_id,
                       'source_kind': source_kind, 'evidence_url': item['url'], 'sensitive': private}
                result[url, field_id, private] = row
    return list(result.values())


def _detail_items(result, selected, start, end, warn, preloaded=None):
    metadata = result["metadata"]
    site = metadata["site"]
    account_id = metadata.get("account_id")
    verified = (metadata.get("identity_verified") is True and isinstance(account_id, str)
                and bool(account_id.strip()))

    def gap(message):
        metadata["status"] = "partial"
        metadata["warnings"].append(message)
        warn(f"Jira: {message}")

    def cli(*args):
        return command(["acli", "jira", *args], stdin="")

    def read_detail(item):
        """Only independent CLI reads run in workers; all state updates follow."""
        try:
            detail = (preloaded or {}).get(item["key"])
            if detail is None:
                detail = decode_json(cli("workitem", "view", item["key"], "--fields", DETAIL_FIELDS, "--json"))
            return detail, None
        except ERRORS as error:
            return None, error

    # executor.map preserves input order. No worker normalizes records, emits
    # warnings or mutates counters.
    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(read_detail, selected))
    for item, (detail, read_error) in zip(selected, reads):
        try:
            if read_error is not None:
                raise read_error
            normalized = _normalize(detail, site)
            require(normalized["key"] == item["key"], "Jira detail identity mismatch")
            fields = detail["fields"]
            updated = timestamp(fields["updated"])
            require("security" in fields and isinstance(fields.get("labels"), list)
                    and all(isinstance(label, str) for label in fields["labels"]), "Missing sensitivity evidence")
            item.update(title=normalized["title"], status=normalized["status"],
                        status_category=normalized["status_category"], updated_at=iso(updated),
                        sensitive=bool(fields["security"]) or any(
                            "security" in label.casefold() or label.upper().startswith("CVE-") for label in fields["labels"]),
                        sensitivity_verified=True, detail_status="ok", detail=detail)
            item.pop("detail_error", None)
            metadata["coverage"]["details"]["returned_count"] += 1
        except ERRORS as error:
            item["detail_status"] = "failed"
            item["detail_error"] = _failure(error)
            metadata["coverage"]["details"]["status"] = "partial"
            gap("One ticket's details could not be read; the ticket is still listed.")
            continue
        # Jira embeds every comment in the detail read (195 of 195 real tickets, up to 97
        # comments each); if it ever returns fewer than its total, say so rather than page.
        native = fields.get("comment")
        listed = native.get("comments") if isinstance(native, dict) else None
        listed = listed if isinstance(listed, list) else []
        by_id = {}
        for comment in listed:
            if isinstance(comment, dict) and isinstance(comment.get("id"), str) and comment["id"]:
                by_id.setdefault(comment["id"], comment)
        comments = list(by_id.values())
        # Repeated, id-less or undated comments also count as not read.
        complete = (integer(native.get("startAt")) and native["startAt"] == 0 and integer(native.get("total"))
                    and native["total"] == len(comments) == len(listed)) if isinstance(native, dict) else False
        for comment in comments:
            author = comment.get("author")
            try:
                created = timestamp(comment.get("created"))
            except ERRORS:
                complete = False
                continue
            if (verified and isinstance(author, dict) and author.get("accountId") == account_id
                    and start <= created <= end):
                result["activity"].append({"kind": "comment_created", "key": item["key"], "comment_id": comment["id"],
                                           "url": item["url"] + "?focusedCommentId=" + quote(comment["id"], safe=""),
                                           "actor_account_id": account_id, "occurred_at": iso(created),
                                           "sensitive": item["sensitive"] or bool(comment.get("visibility")),
                                           "source": comment})
        if not complete:
            gap("One ticket's comments could not all be read; the comments that were read are kept.")
        sources = [("description", item["url"], fields.get("description"), item["sensitive"]),
                   ("issue_link", item["url"], fields.get("issuelinks"), item["sensitive"])]
        # A comment without an author object may be a restricted projection: keep its links private.
        sources += [("comment", item["url"] + "?focusedCommentId=" + quote(comment["id"], safe=""),
                     comment.get("body"), item["sensitive"] or bool(comment.get("visibility"))
                     or not isinstance(comment.get("author"), dict))
                    for comment in comments]
        proofs = set()
        for proof, evidence_url, source, sensitive in sources:
            for text, source_sensitive in _source_strings(source, sensitive):
                for url in PR_URL.findall(text):
                    parts = urlsplit(url)
                    if (parts.netloc == metadata.get("github_host", "github.com")
                            and all(part not in {".", ".."} for part in parts.path.split("/")[1:3])):
                        proofs.add((url, proof, evidence_url, source_sensitive))
        item["pr_links"] = [{"url": url, "proof": proof, "evidence_url": evidence_url, "sensitive": sensitive}
                            for url, proof, evidence_url, sensitive in sorted(proofs)]
        custom = _custom_pr_links(item, fields, 'detail', metadata.get('github_host', 'github.com'))
        item['pr_links'].extend(custom)


def collect_jira(start, end, warn):
    """Return private issue evidence, verified comment activity, and PR URLs.

    ``metadata.site`` is the authenticated hostname; ``user`` is its auth email.
    ``account_id`` is correlated only through complete currentUser() assignment
    evidence, and is never guessed from an arbitrary reporter or comment author.
    """
    metadata = {"status": "ok", "site": "", "user": "", "account_id": None,
                "identity_verified": False, "coverage": {}, "warnings": []}
    result = {"metadata": metadata, "issues": [], "activity": []}

    def gap(message):
        metadata["status"] = "partial"
        metadata["warnings"].append(message)
        warn(f"Jira: {message}")

    def cli(*args):
        return command(["acli", "jira", *args], stdin="")

    if not shutil.which("acli"):
        gap("Atlassian CLI is missing; Jira evidence is unavailable.")
        metadata["status"] = "unavailable"
        return result
    metadata["github_host"] = os.environ.get("GH_HOST", "github.com").lower()
    if not HOST.fullmatch(metadata["github_host"]):
        gap("Configured GitHub host is invalid; Jira PR links cannot be associated.")
        metadata["github_host"] = ""
    try:
        auth = cli("auth", "status")
        metadata["site"], metadata["user"] = _auth_context(auth)
    except ERRORS:
        gap("Authentication/site lookup failed; Jira evidence is unavailable.")
        metadata["status"] = "unavailable"
        return result

    site, user = metadata["site"], metadata["user"]
    try:
        # Text such as SHA-256, UTF-8 or RHSA-2026 looks like an issue key. Knowing the
        # tenant's projects avoids reading and warning about keys that cannot exist.
        projects = decode_json(cli("project", "list", "--json", "--paginate"))
        require(isinstance(projects, list) and all(isinstance(row, dict) and isinstance(row.get("key"), str)
                                                   for row in projects), "Malformed project list")
        metadata["project_keys"] = sorted({row["key"] for row in projects})
    except ERRORS:
        metadata["project_keys"] = None
    # JQL updatedBy has whole-day granularity in the account's timezone. Widen
    # boundaries; native event timestamps below enforce the exact UTC window.
    since = (start - timedelta(days=1)).date().isoformat()
    until = (end + timedelta(days=1)).date().isoformat()
    metadata["discovery_dates"] = {"since": since, "until": until}
    metadata["window"] = {"since_at": iso(start), "to_at": iso(end)}
    issues, assigned_rows = {}, []

    def count(query):
        output = cli("workitem", "search", "--jql", query, "--count")
        match = re.search(r"Number of work items in the search:\s*([0-9]+)\s*$", output)
        require(match is not None, "Unrecognized Jira count output")
        return int(match.group(1))

    def search(role, query, checked=True):
        coverage = {"query": query, "status": "ok", "returned_count": 0,
                    "pagination": "acli --paginate combined array; native pages not exposed"}
        metadata["coverage"][role] = coverage
        before = after = None
        if checked:
            try:
                before = count(query)
            except ERRORS:
                coverage["status"] = "partial"
                gap(f"{role} count lookup failed; some results may be missing.")
        raw, rows = "", []
        try:
            raw = cli("workitem", "search", "--jql", query, "--fields", SEARCH_FIELDS, "--json", "--paginate")
        except subprocess.CalledProcessError as error:
            raw = error.stdout if isinstance(error.stdout, str) else ""
            coverage["status"] = "partial"
            gap(f"{role} search failed; accessible evidence may be incomplete.")
        except ERRORS:
            coverage["status"] = "partial"
            gap(f"{role} search failed; evidence is missing or incomplete.")
        try:
            rows = decode_json(raw)
            require(isinstance(rows, list), "Jira search must be a combined array")
        except ERRORS:
            rows = []
            coverage["status"] = "partial"
            gap(f"{role} search response is unavailable or malformed.")
        found, seen = [], set()
        for row in rows:
            try:
                item = _normalize(row, site)
                require(item["key"] not in seen, "Repeated Jira search item")
                seen.add(item["key"])
                found.append(row)
                current = issues.setdefault(item["key"], item)
                if role not in current["roles"]:
                    current["roles"].append(role)
            except ERRORS:
                coverage["status"] = "partial"
                gap(f"{role} search contained an invalid or duplicate issue; it was skipped.")
        if checked:
            try:
                after = count(query)
            except ERRORS:
                coverage["status"] = "partial"
                gap(f"{role} recount failed; paging changes are unverified.")
            coverage.update(count_before=before, count_after=after)
            if before is not None and after is not None and not (before == after == len(found)):
                coverage["status"] = "partial"
                gap(f"{role}: Jira's ticket count changed or did not match the tickets read; some results may be missing.")
        coverage["returned_count"] = len(found)
        return found

    assigned_rows = search("assigned", "assignee = currentUser() ORDER BY key", checked=True)
    # Every row must corroborate the same account and the authenticated email.
    # A foreign reporter in another query cannot become the activity principal.
    identities = set()
    identity_valid = bool(assigned_rows) and metadata["coverage"]["assigned"]["status"] == "ok"
    for row in assigned_rows:
        author = row["fields"].get("assignee")
        if not (isinstance(author, dict) and isinstance(author.get("accountId"), str) and author["accountId"].strip()
                and (author.get("emailAddress") is None or isinstance(author["emailAddress"], str)
                     and author["emailAddress"].casefold() == user.casefold())):
            identity_valid = False
        else:
            identities.add(author["accountId"])
    if identity_valid and len(identities) == 1:
        metadata["account_id"] = next(iter(identities))
        metadata["identity_verified"] = True
        metadata["identity_basis"] = "complete currentUser assigned native assignees; visible emails matched auth; not /myself"
    else:
        gap("Your Jira account ID could not be confirmed, so your Jira comments and field changes are not credited to you; matching tickets are still listed.")

    queries = [
        ("updated_by", f"issuekey IN updatedBy({_quoted(user)}, {_quoted(since)}, {_quoted(until)}) ORDER BY key"),
        ("recent", "(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser() "
         f"OR creator = currentUser() OR assignee WAS currentUser()) AND updated >= {_quoted(since)} ORDER BY key"),
    ]
    # All-time reporter, creator, watcher and former-assignee inventories reached back years
    # (about 2,000 tickets) without contributing activity; tickets in those roles updated in
    # the window arrive through "recent". Only the identity-bearing assigned search is recounted.
    for role, query in queries:
        # updatedBy() drives field-update credit; its recount catches silent truncation.
        search(role, query, checked=role == "updated_by")
    # Read details for recently updated tickets and for unfinished assigned work, whose PR
    # fields can pair with current changes; done assignments stay as role context.
    owned = {"assigned"}
    selected = [item for item in issues.values()
                if {"updated_by", "recent"} & set(item["roles"])
                or (owned & set(item["roles"]) and item.get("status_category") != "done")]
    selected.sort(key=lambda item: ("updated_by" not in item["roles"], item["key"]))
    metadata["coverage"]["details"] = {"requested_count": len(selected), "returned_count": 0,
                                         "status": "ok", "skipped_inactive_count": len(issues) - len(selected)}
    _detail_items(result, selected, start, end, warn)
    metadata["inventory_count"] = len(assigned_rows)
    metadata["issue_count"] = len(issues)
    result["issues"] = sorted(issues.values(), key=lambda item: item["key"])
    result["activity"].sort(key=lambda event: (timestamp(event["occurred_at"]), event["key"], event["comment_id"], event["kind"]))
    return result


def add_referenced_jira(result, keys, start, end, warn):
    """Read captured issue-key candidates without rerunning inventories.

    Mutates and returns the capture. Unresolved or inaccessible candidates are coverage gaps,
    never invented tickets. Existing completed detail reads are reused.
    """
    metadata = result["metadata"]

    def gap(message):
        metadata["status"] = "partial" if metadata["status"] != "unavailable" else "unavailable"
        metadata["warnings"].append(message)
        warn(f"Jira: {message}")

    coverage = metadata["coverage"].setdefault("referenced_by_github", {
        "status": "ok", "requested_keys": [], "returned_keys": []})
    failures = coverage.setdefault("failures", {})
    requested = set()
    for key in ([keys] if isinstance(keys, str) else keys):
        if isinstance(key, str) and KEY.fullmatch(key):
            requested.add(key)
        else:
            coverage["status"] = "partial"
            gap("One possible key found in your work is not a valid key; it was skipped.")
    requested = sorted(requested)
    for key in requested:
        if key not in coverage["requested_keys"]:
            coverage["requested_keys"].append(key)
    if not requested:
        return result
    if metadata["status"] == "unavailable":
        coverage["status"] = "partial"
        gap("Keys found in your work could not be checked because Jira is unavailable.")
        return result
    try:
        site, user = _auth_context(command(["acli", "jira", "auth", "status"], stdin=""))
        require(site == metadata["site"] and user.casefold() == metadata["user"].casefold(),
                "Jira authentication changed between collection stages")
    except ERRORS:
        coverage["status"] = "partial"
        gap("Jira site/account changed or became unavailable; referenced tickets were not read.")
        return result
    by_key = {item["key"]: item for item in result["issues"]}
    key_aliases = {row['requested_key']: row['canonical_key'] for row in result.get('key_aliases', [])}
    selected, preloaded = [], {}
    projects = set(metadata.get("project_keys") or ())
    known = set(by_key) | set(key_aliases)
    unreadable = 0

    def view(key):
        try:
            return decode_json(command(["acli", "jira", "workitem", "view", key,
                                        "--fields", DETAIL_FIELDS, "--json"], stdin=""))
        except ERRORS as error:
            return error

    # Independent reads run together; results are applied in key order below.
    unread = [key for key in requested if by_key.get(key_aliases.get(key, key)) is None
              and not (projects and key.split("-")[0] not in projects and key not in known)]
    with ThreadPoolExecutor(max_workers=4) as pool:
        fetched = dict(zip(unread, pool.map(view, unread)))
    for key in requested:
        if key not in coverage["requested_keys"]:
            coverage["requested_keys"].append(key)
        if projects and key.split("-")[0] not in projects and key not in known:
            not_issues = coverage.setdefault("not_a_project", [])
            if key not in not_issues:
                not_issues.append(key)
            continue
        try:
            require(isinstance(key, str) and KEY.fullmatch(key), "Invalid referenced issue key")
            item = by_key.get(key_aliases.get(key, key))
            alias_error = None
            if item is None:
                raw = fetched[key]
                if isinstance(raw, Exception):
                    raise raw
                item = _normalize(raw, site)
                canonical_key = item['key']
                existing = by_key.get(canonical_key)
                if existing is None:
                    by_key[canonical_key] = item
                    result['issues'].append(item)
                else:
                    item = existing
                preloaded[canonical_key] = raw
                if canonical_key != key:
                    try:
                        canonical = decode_json(command(['acli', 'jira', 'workitem', 'view', canonical_key,
                                                         '--fields', DETAIL_FIELDS, '--json'], stdin=''))
                        normalized = _normalize(canonical, site)
                        require(normalized['key'] == canonical_key
                                and re.fullmatch(r'[1-9][0-9]*', str(raw.get('id')))
                                and not isinstance(raw.get('id'), bool)
                                and str(raw['id']) == str(canonical.get('id')), 'Jira key alias identity mismatch')
                        result.setdefault('key_aliases', []).append({'requested_key': key,
                            'canonical_key': canonical_key, 'requested': raw, 'canonical': canonical})
                        key_aliases[key] = canonical_key
                        preloaded[canonical_key] = canonical
                    except ERRORS as error:
                        alias_error = error
            if "referenced_by_github" not in item["roles"]:
                item["roles"].append("referenced_by_github")
            if item["detail_status"] != "ok" and item not in selected:
                selected.append(item)
            if alias_error is not None:
                raise alias_error
            if key not in coverage["returned_keys"]:
                coverage["returned_keys"].append(key)
            failures.pop(key, None)
        except ERRORS as error:
            failures[key] = _failure(error)
            coverage["status"] = "partial"
            unreadable += 1
    if unreadable:
        gap(f"{unreadable} referenced ticket(s) could not be read (restricted, deleted or not tickets); "
            "failures are in coverage.referenced_by_github.failures.")
    metadata["coverage"]["details"]["requested_count"] += len(selected)
    _detail_items(result, selected, start, end, warn, preloaded)
    metadata["issue_count"] = len(result["issues"])
    result["issues"].sort(key=lambda item: item["key"])
    result["activity"].sort(key=lambda event: (timestamp(event["occurred_at"]), event["key"], event["comment_id"], event["kind"]))
    return result
