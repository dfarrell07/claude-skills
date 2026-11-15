#!/usr/bin/env python3
"""Show usage left and reset times for Claude, Codex and Antigravity (agy) on every account.

Usage: token_stats.py [tool:account ...]   (default: all nine)
One line per limit: target, pool, percent LEFT, reset time (UTC), time until reset. Sorted so the pool that
resets soonest comes first within each target. Asks each CLI for its own usage numbers, so no model tokens are
spent and no credential file is read. Accounts with no login or no usage source say so instead.
"""
import json
import os
import re
import subprocess
import sys
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HOME = os.path.expanduser("~")
TOOLS, ACCOUNTS = ("claude", "codex", "agy"), ("work", "personal", "personal2")
NOW = datetime.now(timezone.utc)


def run(cmd, env=None, timeout=90):
    e = dict(os.environ, **(env or {}))
    for k in [k for k in e if k.startswith(("CLAUDE_CODE_SESSION", "CLAUDE_CODE_MESSAGING")) or k in (
            "CLAUDE_CONFIG_DIR", "CLAUDE_CODE_CHILD_SESSION")]:
        if k not in (env or {}):
            del e[k]
    return subprocess.run(cmd, env=e, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                          timeout=timeout).stdout


def claude_reset(text):
    """'Nov 15, 5:59am (America/New_York)' -> UTC datetime; the year is the one that puts it nearest now."""
    m = re.match(r"(\w{3}) (\d+), (\d+)(?::(\d+))?(am|pm) \(([^)]+)\)", text)
    if not m:
        return None
    mon, day, hour, mins, ampm, tz = m.groups()
    hour = int(hour) % 12 + (12 if ampm == "pm" else 0)
    local = NOW.astimezone(ZoneInfo(tz))
    best = None
    for year in (local.year - 1, local.year, local.year + 1):
        t = datetime.strptime(f"{year} {mon} {day}", "%Y %b %d").replace(
            hour=hour, minute=int(mins or 0), tzinfo=ZoneInfo(tz))
        if best is None or abs(t - local) < abs(best - local):
            best = t
    return best.astimezone(timezone.utc)


def claude(account):
    if account == "work":
        return "no quota: Vertex, billed per token (org model allow-list)"
    env = {"CLAUDE_CODE_USE_VERTEX": "0"}
    if account == "personal2":
        env["CLAUDE_CONFIG_DIR"] = f"{HOME}/.claude-per2"
    out = run(["claude", "-p", "/usage", "--no-session-persistence"], env)
    rows = []
    for m in re.finditer(r"^Current (.+?): (\d+)% used · resets (.+)$", out, re.M):
        name = "session 5h" if m[1] == "session" else m[1].replace("week (", "week ").rstrip(")")
        rows.append((name, 100 - int(m[2]), claude_reset(m[3])))
    return rows or f"no usage figures ({out.strip()[:80] or 'empty reply'})"


def codex(account):
    home = {"work": ".codex", "personal": ".codex-personal", "personal2": ".codex-personal2"}[account]
    if not os.path.isdir(f"{HOME}/{home}"):
        return f"not set up (~/{home} missing)"
    proc = subprocess.Popen(["codex", "app-server"], env=dict(os.environ, CODEX_HOME=f"{HOME}/{home}"),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True)
    for msg in ({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "token-stats", "version": "1"}}},
                {"method": "initialized"}, {"id": 2, "method": "account/rateLimits/read"}):
        proc.stdin.write(json.dumps(msg) + "\n")
    proc.stdin.flush()
    reply = {}
    try:
        for line in proc.stdout:  # skip notifications until our reply
            msg = json.loads(line)
            if msg.get("id") == 2:
                reply = msg
                break
    finally:
        proc.kill()
    limits = (reply.get("result") or {}).get("rateLimits")
    if not limits:
        return "no usage source: API-key auth, billed to the org budget (probe: delegate quota.sh)"
    rows = []
    for key in ("primary", "secondary"):
        w = limits.get(key)
        if w:
            mins = w["windowDurationMins"]
            window = f"{mins // 1440}d" if mins >= 1440 else f"{mins // 60}h"
            rows.append((f"{window} window", 100 - int(w["usedPercent"]),
                         datetime.fromtimestamp(w["resetsAt"], timezone.utc)))
    return rows


def agy(account):
    dirs = {"personal": ".gemini", "personal2": ".gemini-per2"}
    if account not in dirs or not os.path.isdir(f"{HOME}/{dirs[account]}"):
        return "not set up (no agy login for this account)"
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/dev/null"} if account == "personal2" else {}
    out = run(["agy", "--prompt=/usage", f"--gemini_dir={HOME}/{dirs[account]}", "--mode=plan"], env)
    rows = []
    for line in out.splitlines():  # "<pool>\t<limit name>\t<N>%\t<ISO time>" with percent remaining
        f = line.split("\t")
        if len(f) == 4 and f[2].endswith("%"):
            pool = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude/GPT"}.get(f[0], f[0])
            window = "5h" if "Five Hour" in f[1] else "week" if "Weekly" in f[1] else f[1]
            rows.append((f"{pool} {window}", int(f[2].rstrip("%")),
                         datetime.fromisoformat(f[3].replace("Z", "+00:00"))))
    return rows or f"no usage figures ({out.strip()[:80] or 'empty reply'})"


def until(t):
    s = int((t - NOW).total_seconds())
    if s <= 0:
        return "now"
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d{h:02d}h" if d else f"{h}h{m:02d}m"


def check(target):
    tool, account = target.split(":")
    try:
        return target, {"claude": claude, "codex": codex, "agy": agy}[tool](account)
    except Exception as e:  # one broken CLI must not hide the others
        return target, f"error: {type(e).__name__}: {str(e)[:80]}"


def main():
    targets = sys.argv[1:] or [f"{t}:{a}" for t in TOOLS for a in ACCOUNTS]
    for t in targets:
        tool, _, account = t.partition(":")
        if tool not in TOOLS or account not in ACCOUNTS:
            sys.exit(f"bad target {t}: use <{'|'.join(TOOLS)}>:<{'|'.join(ACCOUNTS)}>")
    print(f"now {NOW:%Y-%m-%dT%H:%MZ}")
    print(f"{'target':<17} {'pool':<22} {'left':>4}  {'resets (UTC)':<17} in")
    with ThreadPoolExecutor(len(targets)) as pool:
        for target, rows in pool.map(check, targets):
            if isinstance(rows, str):
                print(f"{target:<17} {rows}")
                continue
            for name, left, reset in sorted(rows, key=lambda r: r[2] or NOW + timedelta(days=999)):
                when = f"{reset:%Y-%m-%dT%H:%MZ}  {until(reset)}" if reset else "unknown"
                print(f"{target:<17} {name:<22} {left:>3}%  {when}")


if __name__ == "__main__":
    main()
