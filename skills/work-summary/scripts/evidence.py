"""Shared read-only command and evidence-format primitives."""

from datetime import datetime, timezone
import json
import os
from pathlib import Path
import re
import subprocess
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


def is_pr_url(url):
    return re.fullmatch(r'/[^/]+/[^/]+/pull/[1-9][0-9]*', urlsplit(url).path) is not None


def timestamp(value):
    require(isinstance(value, str), "Timestamp must be a string")
    require(re.fullmatch(r"\d{4}-\d{2}-\d{2}[Tt ]\d{2}:\d{2}:\d{2}(?:\.\d+)?(?:Z|[+-]\d{2}:?\d{2})", value),
            "Timestamp must include an ISO date, time, and timezone")
    # Jira emits RFC 822-style offsets; Python 3.10 needs the colon form.
    value = re.sub(r"([+-]\d{2})(\d{2})$", r"\1:\2", value)
    result = datetime.fromisoformat(value.replace("Z", "+00:00"))
    require(result.tzinfo is not None, "Timestamp needs a timezone")
    return result.astimezone(UTC)


def iso(value):
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


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
    if "body" in item:
        require(isinstance(item["body"], str), "Invalid GitHub body")
    if "draft" in item:
        require(type(item["draft"]) is bool, "Invalid GitHub draft state")
    if "author_login" in item:
        require(item["author_login"] is None or isinstance(item["author_login"], str) and bool(item["author_login"]),
                "Invalid GitHub native author")
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

