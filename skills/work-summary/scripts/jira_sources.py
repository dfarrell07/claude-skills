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
    HOST, command, decode_json, integer, iso, require, timestamp, unique_object,
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


def _changelog_coverage(result, warn):
    """Inspect actual native history, rather than declare it absent unconditionally."""
    captured, missing, incomplete = [], [], []
    for item in result["issues"]:
        history = item.get("detail", {}).get("changelog")
        if history is None:
            missing.append(item["key"])
            continue
        try:
            require(isinstance(history, dict) and isinstance(history.get("histories"), list)
                    and integer(history.get("startAt")) and integer(history.get("total")), "Invalid native changelog")
            ids = [row.get("id") for row in history["histories"] if isinstance(row, dict)]
            require(len(ids) == len(history["histories"]) and all(isinstance(ident, str) and ident for ident in ids)
                    and len(set(ids)) == len(ids) and history["startAt"] == 0
                    and len(ids) == history["total"], "Incomplete native changelog")
            for row in history["histories"]:
                timestamp(row.get("created"))
                require(isinstance(row.get("author"), dict)
                        and isinstance(row["author"].get("accountId"), str) and row["author"]["accountId"].strip()
                        and isinstance(row.get("items"), list) and all(isinstance(change, dict)
                        and isinstance(change.get("field"), str) for change in row["items"]),
                        "Changelog lacks native field/actor evidence")
            captured.append(item["key"])
        except ERRORS:
            incomplete.append(item["key"])
    coverage = {"status": "partial" if incomplete or missing and captured else "unavailable" if missing else "ok",
                "captured_keys": sorted(captured), "unexpanded_keys": sorted(missing),
                "incomplete_keys": sorted(incomplete),
                "scope": "Actual top-level native detail.changelog objects; no inferred field events",
                "required_read": "GET /rest/api/3/issue/{issueIdOrKey}/changelog with complete pagination"}
    result["metadata"]["coverage"]["changelog"] = coverage
    if missing or incomplete:
        message = ("Native Jira detail responses omit or truncate changelogs; complete field history requires "
                   "an authenticated changelog REST reader. Deleted comments cannot be reconstructed from present comments.")
        result["metadata"]["status"] = "partial"
        if message not in result["metadata"]["warnings"]:
            result["metadata"]["warnings"].append(message)
            warn("Jira: " + message)


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


def _comment_pages(raw):
    """Return validated prefix pages and completeness, even after later failure."""
    def invalid(value):
        raise ValueError(f"Invalid JSON number: {value}")

    decoder = json.JSONDecoder(object_pairs_hook=unique_object, parse_constant=invalid)
    pages, offset, expected, total, seen, stable = [], 0, 0, None, set(), True
    while raw[offset:].strip():
        try:
            offset += len(raw[offset:]) - len(raw[offset:].lstrip())
            page, offset = decoder.raw_decode(raw, offset)
            require(isinstance(page, dict) and isinstance(page.get("comments"), list), "Invalid comment page")
            require(integer(page.get("startAt")) and page["startAt"] == expected
                    and integer(page.get("maxResults"), 1) and integer(page.get("total"))
                    and type(page.get("isLast")) is bool, "Invalid comment pagination")
            require(not pages or not pages[-1]["isLast"], "Comment data after terminal page")
            if total is not None and total != page["total"]:
                stable = False
            require(len(page["comments"]) <= page["maxResults"], "Oversized comment page")
            page_ids = set()
            for comment in page["comments"]:
                require(isinstance(comment, dict) and isinstance(comment.get("id"), str)
                        and comment["id"] and comment["id"] not in seen
                        and comment["id"] not in page_ids, "Invalid or repeated comment")
                page_ids.add(comment["id"])
            if total is None:
                total = page["total"]
            seen.update(page_ids)
            pages.append(page)
            expected += page["maxResults"]
        except ERRORS:
            return pages, False
    return pages, bool(stable and pages and pages[-1]["isLast"] and len(seen) == total)


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


def _full_detail_fields(item, native, site):
    require(isinstance(site, str) and HOST.fullmatch(site) and site == site.casefold()
            and item.get('url') == f"https://{site}/browse/{item['key']}", 'Invalid full Jira tenant binding')
    normalized = _normalize(native, site)
    require(normalized['key'] == item['key'], 'Full Jira detail key mismatch')
    basis = next((item[name] for name in ('detail', 'source') if isinstance(item.get(name), dict)
                  and item[name].get('key') == item['key']
                  and not isinstance(item[name].get('id'), bool)
                  and re.fullmatch(r'[1-9][0-9]*', str(item[name].get('id')))), None)
    require(basis is not None and not isinstance(native.get('id'), bool)
            and str(native.get('id')) == str(basis['id']), 'Full Jira detail native ID mismatch')
    return native['fields']


def validate_full_details(result):
    """Validate the optional supplemental all-fields observations and coverage."""
    metadata = result.get('metadata', {})
    issues = result.get('issues', [])
    coverage = metadata.get('coverage', {}).get('full_details')
    if coverage is None:
        require(not any(any(key in item for key in ('full_detail', 'full_detail_status', 'full_detail_error'))
                        for item in issues), 'Full Jira fields lack coverage')
        return
    captured, incomplete, failed = [], [], []
    for item in issues:
        status = item.get('full_detail_status')
        require(status in {'ok', 'partial', 'failed'}, 'Invalid full Jira detail status')
        if status in {'ok', 'partial'}:
            fields = _full_detail_fields(item, item.get('full_detail'), metadata.get('site', ''))
            captured.append(item['key'])
            privacy_complete = ('security' in fields and isinstance(fields.get('labels'), list)
                                and all(isinstance(label, str) for label in fields['labels']))
            if status == 'ok':
                require(privacy_complete and item.get('sensitivity_verified') is True,
                        'Complete full Jira fields lack privacy evidence')
            private = not privacy_complete or bool(fields.get('security')) or any(
                isinstance(label, str) and ('security' in label.casefold() or label.upper().startswith('CVE-'))
                for label in fields.get('labels', []) if isinstance(fields.get('labels'), list))
            for old in (item.get('detail') or item.get('source'),):
                old_fields = old.get('fields', {}) if isinstance(old, dict) else {}
                private = private or bool(old_fields.get('security')) or any(
                    isinstance(label, str) and ('security' in label.casefold() or label.upper().startswith('CVE-'))
                    for label in (old_fields['labels'] if isinstance(old_fields.get('labels'), list) else []))
            require(type(item.get('sensitive')) is bool and (not private or item['sensitive']),
                    'Full Jira detail privacy was downgraded')
        else:
            require('full_detail' not in item, 'Failed full Jira read contains accepted native fields')
        if status == 'ok':
            require('full_detail_error' not in item, 'Successful full Jira read retains a failure')
        else:
            error = item.get('full_detail_error')
            require(isinstance(error, dict) and set(error) <= {'type', 'message', 'returncode', 'stderr', 'stdout', 'timeout'}
                    and isinstance(error.get('type'), str) and bool(error['type'])
                    and isinstance(error.get('message'), str), 'Missing full Jira failure evidence')
            if 'returncode' in error:
                require(type(error['returncode']) is int and error['returncode'] != 0
                        and isinstance(error.get('stdout'), str) and isinstance(error.get('stderr'), str),
                        'Invalid native full Jira command failure')
            if 'timeout' in error:
                require(isinstance(error['timeout'], (int, float)) and not isinstance(error['timeout'], bool)
                        and error['timeout'] > 0, 'Invalid full Jira timeout')
            (incomplete if status == 'partial' else failed).append(item['key'])
    requested = [item['key'] for item in issues]
    require(len(set(requested)) == len(requested), 'Repeated full Jira coverage key')
    expected = {'status': 'ok' if len(captured) == len(issues) and not incomplete else
                'partial' if captured else 'unavailable', 'scope': 'authenticated_all_fields',
                'requested_keys': sorted(requested), 'captured_keys': sorted(captured),
                'incomplete_keys': sorted(incomplete), 'failed_keys': sorted(failed)}
    require(coverage == expected, 'Full Jira fields coverage differs from native observations')


def supplement_full_details(result, warn):
    """Read omitted native fields without replacing prior observations or activity."""
    metadata = result['metadata']
    site = metadata.get('site', '')
    issues = result.get('issues', [])

    def bind(item, native):
        return _full_detail_fields(item, native, site)

    pending = []
    for item in issues:
        if 'full_detail' in item:
            bind(item, item['full_detail'])
        if item.get('full_detail_status') == 'ok':
            bind(item, item.get('full_detail'))
        else:
            pending.append(item)
    auth_error = None
    if pending:
        try:
            auth = command(['acli', 'jira', 'auth', 'status'], stdin='')
            actual_site, actual_user = _auth_context(auth)
            require(actual_site == site and actual_user.casefold() == metadata.get('user', '').casefold(),
                    'Jira site/account changed before full-field read')
        except ERRORS as error:
            auth_error = error

    def read(item):
        if auth_error is not None:
            return None, auth_error
        try:
            return decode_json(command(['acli', 'jira', 'workitem', 'view', item['key'],
                                        '--fields', '*all', '--json'], stdin='')), None
        except subprocess.CalledProcessError as error:
            try:
                native = decode_json(error.stdout) if isinstance(error.stdout, str) else None
            except (ValueError, TypeError):
                native = None
            return native, error
        except ERRORS as error:
            return None, error

    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(read, pending))
    for item, (native, error) in zip(pending, reads):
        try:
            if error is not None and native is None:
                raise error
            fields = bind(item, native)
            item['full_detail'] = native
            item['full_detail_status'] = 'partial'
            require('security' in fields and isinstance(fields.get('labels'), list)
                    and all(isinstance(label, str) for label in fields['labels']), 'Missing full-field sensitivity evidence')
            private = bool(fields['security']) or any('security' in label.casefold() or label.upper().startswith('CVE-')
                                                     for label in fields['labels'])
            item['sensitive'] = bool(item.get('sensitive', True)) or private
            item['sensitivity_verified'] = True
            if error is not None:
                raise error
            item['full_detail_status'] = 'ok'
            item.pop('full_detail_error', None)
        except ERRORS as failure:
            item['full_detail_status'] = 'partial' if 'full_detail' in item else 'failed'
            item['full_detail_error'] = _failure(failure)
            # Incomplete sensitivity evidence can never lower existing privacy.
            item['sensitive'] = True

    linked = set(result.get('linked_prs', []))
    for item in issues:
        if item.get('sensitive'):
            for row in item.get('pr_links', []):
                row['sensitive'] = True
        if item.get('full_detail_status') not in {'ok', 'partial'} or 'full_detail' not in item:
            continue
        fields = bind(item, item['full_detail'])
        custom = _custom_pr_links(item, fields, 'full_detail', metadata.get('github_host', 'github.com'))
        old = [row for row in item.get('pr_links', [])
               if not (row.get('proof') == 'custom_field' and row.get('source_kind') == 'full_detail')]
        item['pr_links'] = old + custom
        linked.update(row['url'] for row in custom)
    # Privacy may have risen since these derived records were first built.
    from jira_public_history import public_history_review_links
    for item in issues:
        if 'history_review_links' in item:
            item['history_review_links'] = public_history_review_links(item, metadata.get('github_host', 'github.com'))
        if item.get('sensitive'):
            for row in item.get('review_links', []):
                row['sensitive'] = True
    result['linked_prs'] = sorted(linked)
    complete = [item['key'] for item in issues if item.get('full_detail_status') == 'ok']
    captured = [item['key'] for item in issues if item.get('full_detail_status') in {'ok', 'partial'}]
    incomplete = [item['key'] for item in issues if item.get('full_detail_status') == 'partial']
    failed = [item['key'] for item in issues if item.get('full_detail_status') == 'failed']
    coverage = {'status': 'ok' if len(complete) == len(issues) else 'partial' if captured else 'unavailable',
                'scope': 'authenticated_all_fields', 'requested_keys': sorted(item['key'] for item in issues),
                'captured_keys': sorted(captured), 'incomplete_keys': sorted(incomplete), 'failed_keys': sorted(failed)}
    metadata.setdefault('coverage', {})['full_details'] = coverage
    message = 'Some authenticated full-field Jira reads are unavailable or incomplete; prior observations remain preserved.'
    if coverage['status'] == 'ok' and message in metadata.get('warnings', []):
        metadata['warnings'].remove(message)
    if coverage['status'] != 'ok':
        metadata['status'] = 'partial'
        if message not in metadata.setdefault('warnings', []):
            metadata['warnings'].append(message)
            warn('Jira: ' + message)
    validate_full_details(result)
    return result


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
    # warnings, mutates counters, or handles comment pagination.
    with ThreadPoolExecutor(max_workers=4) as pool:
        reads = list(pool.map(read_detail, selected))
    linked_prs = set(result["linked_prs"])
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
            gap("One selected Jira detail is inaccessible or malformed; its inventory lead was retained.")
            continue
        native = fields.get("comment")
        comments, complete, restricted_comments = {}, False, set()
        try:
            require(isinstance(native, dict) and isinstance(native.get("comments"), list)
                    and integer(native.get("total")) and integer(native.get("startAt")),
                    "Missing native comment page")
            for comment in native["comments"]:
                require(isinstance(comment, dict) and isinstance(comment.get("id"), str)
                        and comment["id"] and comment["id"] not in comments, "Invalid native comment")
                comments[comment["id"]] = comment
                if comment.get("visibility"):
                    restricted_comments.add(comment["id"])
            # Native detail responses may contain the final page rather than a
            # prefix. Retain that evidence and request the missing history.
            consistent = native["startAt"] + len(comments) <= native["total"]
            complete = consistent and native["startAt"] == 0 and len(comments) == native["total"]
            if not complete:
                failed = False
                try:
                    raw_pages = cli("workitem", "comment", "list", "--key", item["key"], "--json", "--paginate")
                except subprocess.CalledProcessError as error:
                    raw_pages = error.stdout if isinstance(error.stdout, str) else ""
                    failed = True
                pages, complete = _comment_pages(raw_pages)
                item["comment_pages"] = pages
                if failed or not pages or pages[0]["total"] != native["total"] or not consistent:
                    complete = False
                for page in pages:
                    for comment in page["comments"]:
                        comments.setdefault(comment["id"], comment)
                        if comment.get("visibility") or (not isinstance(comment.get("author"), dict) and "visibility" not in comment):
                            restricted_comments.add(comment["id"])
                complete = complete and len(comments) == native["total"]
                require(complete, "Incomplete comment pagination")
        except ERRORS:
            gap("One Jira comment capture is missing or incompletely paginated; available native comments were retained.")
        native_fidelity = True
        native_gaps = []
        for comment in comments.values():
            missing_fields, invalid_fields = [], []
            if not (isinstance(comment.get("author"), dict)
                    and isinstance(comment["author"].get("accountId"), str)
                    and comment["author"]["accountId"].strip()):
                missing_fields.append("author.accountId")
            for field in ("created", "updated"):
                if not comment.get(field):
                    missing_fields.append(field)
                else:
                    try:
                        timestamp(comment[field])
                    except ERRORS:
                        invalid_fields.append(field)
            if "body" not in comment:
                missing_fields.append("body")
            if not any(field in missing_fields + invalid_fields for field in ("created", "updated")):
                created, modified = timestamp(comment["created"]), timestamp(comment["updated"])
                if modified < created:
                    invalid_fields.append("updated")
                elif modified != created:
                    editor = comment.get("updateAuthor")
                    if not (isinstance(editor, dict) and isinstance(editor.get("accountId"), str)
                            and editor["accountId"].strip()):
                        missing_fields.append("updateAuthor.accountId")
            if missing_fields or invalid_fields:
                native_fidelity = False
                recovery = {"comment_id": comment["id"], "missing_fields": missing_fields,
                            "required_read": "/rest/api/3/issue/" + item["key"] + "/comment/"
                            + quote(comment["id"], safe="")}
                if invalid_fields:
                    recovery["invalid_fields"] = invalid_fields
                native_gaps.append(recovery)
            try:
                require(isinstance(comment.get("author"), dict)
                        and isinstance(comment["author"].get("accountId"), str)
                        and comment["author"]["accountId"].strip() and "body" in comment,
                        "Comment lacks native actor/body")
                created = timestamp(comment["created"])
                events = [("comment_created", comment["author"], created)]
                if comment.get("updated") and "updated" not in invalid_fields:
                    modified = timestamp(comment["updated"])
                    require(modified >= created, "Reversed comment timestamps")
                    if modified != created:
                        editor = comment.get("updateAuthor")
                        if isinstance(editor, dict) and isinstance(editor.get("accountId"), str) and editor["accountId"].strip():
                            events.append(("comment_edited", editor, modified))
                        else:
                            native_fidelity = False
                for kind, actor, when in events:
                    if (verified and actor.get("accountId") == account_id
                            and start <= when <= end):
                        result["activity"].append({"kind": kind, "key": item["key"], "comment_id": comment["id"],
                                                   "url": item["url"] + "?focusedCommentId=" + quote(comment["id"], safe=""),
                                                   "actor_account_id": actor["accountId"], "occurred_at": iso(when),
                                                   "sensitive": item["sensitive"] or comment["id"] in restricted_comments,
                                                   "source": comment})
            except ERRORS:
                native_fidelity = False
        item["comment_coverage"] = {"complete": complete, "native_fidelity": native_fidelity,
                                    "captured_count": len(comments), "total": native.get("total") if isinstance(native, dict) else None}
        if native_gaps:
            item["comment_native_gaps"] = native_gaps
        else:
            item.pop("comment_native_gaps", None)
        if not native_fidelity:
            gap("One Jira comment stream lacks native actor identities or timestamps; those comments cannot prove exact-window activity.")
        sources = [("description", item["url"], fields.get("description"), item["sensitive"]),
                   ("issue_link", item["url"], fields.get("issuelinks"), item["sensitive"])]
        sources += [("comment", item["url"] + "?focusedCommentId=" + quote(comment["id"], safe=""),
                     comment.get("body"), item["sensitive"] or comment["id"] in restricted_comments)
                    for comment in comments.values()]
        proofs = set()
        for proof, evidence_url, source, sensitive in sources:
            for text, source_sensitive in _source_strings(source, sensitive):
                for url in PR_URL.findall(text):
                    parts = urlsplit(url)
                    if (parts.netloc == metadata.get("github_host", "github.com")
                            and all(part not in {".", ".."} for part in parts.path.split("/")[1:3])):
                        linked_prs.add(url)
                        proofs.add((url, proof, evidence_url, source_sensitive))
        item["pr_links"] = [{"url": url, "proof": proof, "evidence_url": evidence_url, "sensitive": sensitive}
                            for url, proof, evidence_url, sensitive in sorted(proofs)]
        custom = _custom_pr_links(item, fields, 'detail', metadata.get('github_host', 'github.com'))
        item['pr_links'].extend(custom)
        linked_prs.update(row['url'] for row in custom)
    result["linked_prs"] = sorted(linked_prs)


def collect_jira(start, end, warn):
    """Return private issue evidence, verified comment activity, and PR URLs.

    ``metadata.site`` is the authenticated hostname; ``user`` is its auth email.
    ``account_id`` is correlated only through complete currentUser() assignment
    evidence, and is never guessed from an arbitrary reporter or comment author.
    """
    metadata = {"status": "ok", "site": "", "user": "", "account_id": None,
                "identity_verified": False, "coverage": {}, "warnings": []}
    result = {"metadata": metadata, "issues": [], "activity": [], "linked_prs": []}

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
                gap(f"{role} count lookup failed; pagination completeness is unverified.")
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
                gap(f"{role} counts changed or disagree with distinct captured issues.")
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
        gap("Native Jira account identity could not be corroborated; exact actor attribution is unavailable.")

    queries = [
        ("updated_by", f"issuekey IN updatedBy({_quoted(user)}, {_quoted(since)}, {_quoted(until)}) ORDER BY key"),
        ("recent", "(assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser() "
         f"OR creator = currentUser() OR assignee WAS currentUser()) AND updated >= {_quoted(since)} ORDER BY key"),
    ]
    for role, clause in (("reporter", "reporter = currentUser()"), ("creator", "creator = currentUser()"),
                         ("former_assignee", "assignee WAS currentUser()"),
                         ("watcher", "watcher = currentUser()")):
        queries.append((role, clause + " ORDER BY key"))
    for role, query in queries:
        search(role, query)
    selected = list(issues.values())
    selected.sort(key=lambda item: ("updated_by" not in item["roles"], "recent" not in item["roles"], item["key"]))
    metadata["coverage"]["details"] = {"requested_count": len(selected), "returned_count": 0,
                                         "status": "ok"}
    _detail_items(result, selected, start, end, warn)
    metadata["inventory_count"] = len(assigned_rows)
    metadata["issue_count"] = len(issues)
    result["issues"] = sorted(issues.values(), key=lambda item: item["key"])
    _changelog_coverage(result, warn)
    result["activity"].sort(key=lambda event: (timestamp(event["occurred_at"]), event["key"], event["comment_id"], event["kind"]))
    return result


def add_referenced_jira(result, keys, start, end, warn):
    """Read explicit GitHub-mentioned issue keys without rerunning inventories.

    Mutates and returns the capture. Invalid/inaccessible keys are coverage gaps,
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
            gap("One GitHub-mentioned Jira key is invalid; no issue was invented.")
    requested = sorted(requested)
    for key in requested:
        if key not in coverage["requested_keys"]:
            coverage["requested_keys"].append(key)
    if not requested:
        return result
    if metadata["status"] == "unavailable":
        coverage["status"] = "partial"
        gap("GitHub-mentioned Jira tickets could not be read because Jira is unavailable.")
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
    for key in requested:
        if key not in coverage["requested_keys"]:
            coverage["requested_keys"].append(key)
        try:
            require(isinstance(key, str) and KEY.fullmatch(key), "Invalid referenced issue key")
            item = by_key.get(key_aliases.get(key, key))
            alias_error = None
            if item is None:
                raw = decode_json(command(["acli", "jira", "workitem", "view", key,
                                           "--fields", DETAIL_FIELDS, "--json"], stdin=""))
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
            gap("One GitHub-mentioned Jira key is inaccessible or invalid; no issue was invented.")
    metadata["coverage"]["details"]["requested_count"] += len(selected)
    _detail_items(result, selected, start, end, warn, preloaded)
    metadata["issue_count"] = len(result["issues"])
    result["issues"].sort(key=lambda item: item["key"])
    _changelog_coverage(result, warn)
    result["activity"].sort(key=lambda event: (timestamp(event["occurred_at"]), event["key"], event["comment_id"], event["kind"]))
    return result
