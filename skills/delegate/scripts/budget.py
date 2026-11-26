#!/usr/bin/env python3
"""Where delegated work should go now: live usage per target, judged by the timing rules.

Usage: budget.py [personal|work|<tool>:<account>|subagent ...]   (default: personal and work)
Prints each group's targets best first (`ok` > `hold` > `blocked`, then pace ratio R), each followed by its
pools: percent LEFT, reset time (UTC) and time until reset. Asks each CLI for its own usage, so reading usage
spends no model tokens and reads no credential file. Starts any unstarted agy weekly clock with one tiny background
prompt.
"""
import json
import os
import re
import signal
import subprocess
import sys
import threading
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo

HOME = os.path.expanduser("~")
HERE = os.path.dirname(os.path.abspath(__file__))
H5, WEEK = timedelta(hours=5), timedelta(days=7)
FLOOR_5H, FLOOR_5H_ORCH = 10, 25  # a 5h pool is blocked at or below this percent left; hold below twice it
HOLD_BELOW, HOLD_BELOW_ORCH = 0.9, 1.0  # R below this: behind pace
UNSTARTED_SLACK = timedelta(minutes=2)
GROUPS = {"personal": ["agy:personal", "agy:personal2", "codex:personal", "subagent"],
          "work": ["codex:work"]}
KICK_MODEL = "gemini-3.8-flash"
KICK_COOLDOWN = 15 * 60  # seconds; a kicked pool still reads 100% and looks unstarted for a while


@dataclass
class Pool:
    name: str
    left: float  # percent remaining
    window: timedelta
    reset: datetime | None
    model: str | None = None  # set when the limit covers one model only


# ---- policy (pure; tests call these with a fixed `now`) ----

def fresh(p, now):
    """The pool as it stands now: a reset already past means a full, unstarted pool."""
    if p.reset and p.reset <= now:
        return Pool(p.name, 100.0, p.window, now + p.window, p.model)
    return p


def unstarted(p, now):
    p = fresh(p, now)
    return p.left >= 100 and p.reset is not None and p.reset - now >= p.window - UNSTARTED_SLACK


def pace(p, now):
    """R = L*W / (100*T): the burn rate that ends at zero at reset, over the steady rate."""
    p = fresh(p, now)
    t = max((p.reset - now).total_seconds(), 60)
    return p.left * p.window.total_seconds() / (100 * t)


def assess(pools, now, orchestrator=False):
    """(verdict, R or None, reason) for one target from the pools it draws on."""
    floor = FLOOR_5H_ORCH if orchestrator else FLOOR_5H
    blocked, near, worst = [], [], None
    for p in map(lambda p: fresh(p, now), pools):
        if p.reset is None:
            continue
        if p.window <= H5:
            if p.left <= floor:
                why = f"{p.name} {p.left:.0f}% <= {floor}% floor, rolls over in {until(p.reset, now)}"
                blocked.append((p.reset, why))
            elif p.left <= 2 * floor:  # room for a run or two; don't hand it a full share
                near.append(f"{p.name} {p.left:.0f}% near its {floor}% floor, rolls over in {until(p.reset, now)}")
        elif p.left <= 0:
            blocked.append((p.reset, f"{p.name} empty, resets in {until(p.reset, now)}"))
        else:
            r = pace(p, now)
            if worst is None or r < worst[0]:
                worst = (r, p.name)
    if blocked:
        return "blocked", None, max(blocked)[1]  # the last pool to free up decides
    r, name = worst or (None, "no long pool reported")
    if near:
        return "hold", r, near[0]
    hold = HOLD_BELOW_ORCH if orchestrator else HOLD_BELOW
    return ("hold" if r is not None and r < hold else "ok"), r, name


RANK = {"ok": 0, "hold": 1, "blocked": 2}


def order(results):
    """results: [(target, verdict, R, reason)] -> best first. Status-only targets (str verdict) go last."""
    return sorted(results, key=lambda x: (RANK.get(x[1], 3), -(x[2] or 0)))


def routed(tool, pool):
    """Pools a target draws on: agy runs Gemini models only (its Claude/GPT pool is never routed); elsewhere the
    account-wide limits, not ones for a single model the default doesn't use."""
    return pool.name.startswith("Gemini") if tool == "agy" else pool.model is None


def kicks(tool, pools, now):
    """agy weekly pools that delegate uses and whose first-use clock hasn't started."""
    return tool == "agy" and any(p.window > H5 and routed(tool, p) and unstarted(p, now) for p in pools)


# ---- collectors (one per CLI; return [Pool] or a status string) ----

def child_env(extra=None):
    """The parent session's CLAUDE* vars (config dir, session ids, tokens) must not reach the CLIs."""
    return {**{k: v for k, v in os.environ.items() if not k.startswith("CLAUDE")}, **(extra or {})}


def kill_group(proc):
    """The whole process group: the npm `codex` is a node wrapper whose child would keep the pipe open."""
    try:
        os.killpg(proc.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def run(cmd, env=None, timeout=90, stop=None):
    """stdout and stderr together, up to the first line containing `stop` or until `timeout` seconds."""
    proc = subprocess.Popen(cmd, env=child_env(env), stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                            stderr=subprocess.STDOUT, text=True, start_new_session=True)
    watchdog = threading.Timer(timeout, kill_group, [proc])
    watchdog.start()
    out = []
    try:
        for line in proc.stdout:
            out.append(line)
            if stop and stop in line:
                break
    finally:
        watchdog.cancel()
        kill_group(proc)
        proc.wait()
    return "".join(out)


def no_figures(out):
    return f"error: no usage figures ({' '.join(out.split())[:80] or 'empty reply'})"


def claude_reset(s, now):
    """'Nov 15, 5:59am (America/New_York)' or '4am (...)' -> UTC datetime. A missing date means the next such
    time; a missing year means the year that puts it nearest now."""
    m = re.match(r"(?:(\w{3}) (\d+),? )?(?:at )?(\d+)(?::(\d+))?\s*(am|pm) \(([^)]+)\)", s, re.I)
    if not m:
        return None
    mon, day, hour, mins, ampm, tz = m.groups()
    tz = ZoneInfo(tz)
    local = now.astimezone(tz)
    hm = {"hour": int(hour) % 12 + (12 if ampm.lower() == "pm" else 0), "minute": int(mins or 0)}
    if not mon:
        t = local.replace(**hm, second=0, microsecond=0)
        return (t if t > local else t + timedelta(days=1)).astimezone(timezone.utc)
    best = None
    for year in (local.year - 1, local.year, local.year + 1):
        try:  # Feb 29 only exists in some years
            t = datetime.strptime(f"{year} {mon} {day}", "%Y %b %d").replace(**hm, tzinfo=tz)
        except ValueError:
            continue
        if best is None or abs(t - local) < abs(best - local):
            best = t
    return best and best.astimezone(timezone.utc)


def parse_claude(out, now):
    pools = []
    for m in re.finditer(r"^Current (.+?): (\d+)% used · resets (.+)$", out, re.M):
        session = m[1] == "session"
        name = "session 5h" if session else m[1].replace("week (", "week ").rstrip(")")
        model = re.search(r"\((.+)\)", m[1])
        model = model[1] if model and model[1] != "all models" else None
        pools.append(Pool(name, 100.0 - int(m[2]), H5 if session else WEEK, claude_reset(m[3], now), model))
    if pools:
        return pools
    if re.search(r"cost:", out, re.I):  # cost lines only: an API-key or logged-out config, not a subscription
        return "no subscription usage (API key or logged out)"
    if "using your subscription" in out:  # since late 2026 /usage in -p mode shows a breakdown but no limits
        return "usage not reported (no limit rows)"
    return no_figures(out)


def claude(account, now):
    if account == "work":
        return "no quota: Vertex, billed per token (org model allow-list)"
    env = {"CLAUDE_CODE_USE_VERTEX": "0", "DISABLE_TELEMETRY": "1", "CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC": "1"}
    # Always name the config dir: on macOS an unset CLAUDE_CONFIG_DIR is a separate Keychain login from an explicit
    # ~/.claude (the one cper uses), and it may be logged out.
    env["CLAUDE_CONFIG_DIR"] = f"{HOME}/.claude-per2" if account == "personal2" else f"{HOME}/.claude"
    return parse_claude(run(["claude", "-p", "/usage", "--no-session-persistence", "--strict-mcp-config"], env), now)


def parse_codex(reply):
    err = (reply.get("error") or {}).get("message", "")
    if "chatgpt authentication required" in err.lower():
        return "no usage source: API-key auth, billed to the org budget"
    result = reply.get("result") or {}
    limits = result.get("rateLimitsByLimitId") or {"codex": result.get("rateLimits")}
    pools = []
    for lid, lim in limits.items():  # one entry per limit; model-specific ones are named
        model = None if lid == "codex" else (lim or {}).get("limitName") or lid
        prefix = f"{model} " if model else ""
        for key in ("primary", "secondary"):
            w = (lim or {}).get(key)
            if w:
                mins = w["windowDurationMins"]
                label = f"{mins // 1440}d" if mins >= 1440 else f"{mins // 60}h"
                reset = w.get("resetsAt") and datetime.fromtimestamp(w["resetsAt"], timezone.utc)
                pools.append(Pool(f"{prefix}{label}", 100.0 - w["usedPercent"], timedelta(minutes=mins), reset, model))
    return pools or f"error: {err[:80] or 'no rate limits in codex reply'}"


def codex(account, now):
    home = {"work": ".codex", "personal": ".codex-personal", "personal2": ".codex-personal2"}[account]
    if not os.path.isdir(f"{HOME}/{home}"):
        return f"not set up (~/{home} missing)"
    proc = subprocess.Popen(["codex", "app-server"], env=child_env({"CODEX_HOME": f"{HOME}/{home}"}),
                            stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, text=True,
                            start_new_session=True)
    reply = {}
    watchdog = threading.Timer(30, kill_group, [proc])  # a wedged server must not stall every other target
    watchdog.start()
    try:
        hello = {"clientInfo": {"name": "delegate-budget", "version": "1"}}
        for msg in ({"id": 1, "method": "initialize", "params": hello}, {"method": "initialized"},
                    {"id": 2, "method": "account/rateLimits/read"}):
            proc.stdin.write(json.dumps(msg) + "\n")
        proc.stdin.flush()
        for line in proc.stdout:  # skip notifications until our reply
            msg = json.loads(line)
            if msg.get("id") == 2:
                reply = msg
                break
    finally:
        watchdog.cancel()
        kill_group(proc)
        proc.wait()
    return parse_codex(reply)


AGY_DIRS = {"personal": ".gemini", "personal2": ".gemini-per2"}


def parse_agy(out, account="personal"):
    if "Authentication required" in out:
        # personal2's login must stay in its own dir, off the shared keyring
        env = "DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null " if account == "personal2" else ""
        return f"logged out: run `{env}agy --gemini_dir=$HOME/{AGY_DIRS.get(account, '.gemini')}` once to log in"
    pools = []
    for line in out.splitlines():  # "<pool>\t<limit name>\t<N>%\t<ISO time>" with percent remaining
        f = line.split("\t")
        if len(f) == 4 and f[2].endswith("%"):
            group = {"Gemini Models": "Gemini", "Claude and GPT models": "Claude/GPT"}.get(f[0], f[0])
            five = "Five Hour" in f[1]
            label = "5h" if five else "week" if "Weekly" in f[1] else f[1]
            pools.append(Pool(f"{group} {label}", float(f[2].rstrip("%")), H5 if five else WEEK,
                              datetime.fromisoformat(f[3].replace("Z", "+00:00"))))
    return pools or no_figures(out)


def agy(account, now):
    if account not in AGY_DIRS or not os.path.isdir(f"{HOME}/{AGY_DIRS[account]}"):
        return "not set up (no agy login for this account)"
    env = {"DBUS_SESSION_BUS_ADDRESS": "unix:path=/dev/null"} if account == "personal2" else {}
    # A logged-out agy prints a login URL and waits 60 s for it, so stop reading there; a good reply takes ~2 s.
    out = run(["agy", "--prompt=/usage", f"--gemini_dir={HOME}/{AGY_DIRS[account]}", "--mode=plan"], env,
              timeout=20, stop="Authentication required")
    return parse_agy(out, account)


def kick(account):
    """Start an agy first-use clock: one tiny prompt, detached, in its own dir so it reads nothing of yours.
    False if this account was kicked within KICK_COOLDOWN (the prompt file's mtime is the stamp)."""
    d = f"{HOME}/.cache/delegate/kick-{account}"
    os.makedirs(d, mode=0o700, exist_ok=True)
    prompt = f"{d}/prompt.md"
    if os.path.exists(prompt) and datetime.now().timestamp() - os.path.getmtime(prompt) < KICK_COOLDOWN:
        return False
    with open(prompt, "w") as f:
        f.write("Reply with exactly: ok\n")
    env = child_env({"DELEGATE_MODEL": KICK_MODEL, "DELEGATE_EFFORT": "low", "DELEGATE_TIMEOUT": "300"})
    subprocess.Popen(["bash", f"{HERE}/delegate.sh", f"agy:{account}", prompt, f"{d}/out.md"], cwd=d,
                     env=env, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                     start_new_session=True)
    return True


# ---- report ----

def until(t, now):
    s = int((t - now).total_seconds())
    if s <= 0:
        return "now"
    d, h, m = s // 86400, s % 86400 // 3600, s % 3600 // 60
    return f"{d}d{h:02d}h" if d else f"{h}h{m:02d}m"


def orchestrator_account():
    """The claude account this session runs on, which built-in subagents spend; None if not a known one."""
    if os.environ.get("CLAUDE_CODE_USE_VERTEX", "").lower() in ("1", "true", "yes"):
        return None  # Vertex (work): no subagent budget here
    cfg = os.path.realpath(os.environ.get("CLAUDE_CONFIG_DIR") or f"{HOME}/.claude")
    known = {f"{HOME}/.claude": "personal", f"{HOME}/.claude-per2": "personal2"}
    return {os.path.realpath(d): a for d, a in known.items()}.get(cfg)


def collect(target, now):
    """-> (target, tool, [Pool] or status string)."""
    if target == "subagent":
        acct = orchestrator_account()
        if acct is None:
            return target, "claude", "session not on a personal account"
        tool, account = "claude", acct
    else:
        tool, account = target.split(":")
    try:
        return target, tool, {"claude": claude, "codex": codex, "agy": agy}[tool](account, now)
    except Exception as e:  # one broken CLI must not hide the others
        return target, tool, f"error: {type(e).__name__}: {str(e)[:80]}"


def report(title, collected, now):
    print(f"{title}:")
    results, pools_of = [], {}
    for target, tool, pools in collected:
        if isinstance(pools, str):
            results.append((target, pools, None, ""))
            continue
        mine = [p for p in pools if routed(tool, p)]
        results.append((target, *assess(mine, now, orchestrator=target == "subagent")))
        pools_of[target] = [p for p in pools if routed(tool, p) or p.model]  # model-only pools: shown, not judged
    for target, verdict, r, why in order(results):
        if verdict not in RANK:
            print(f"  {target:<16} {verdict}")
            continue
        print(f"  {verdict:<7} {target:<16} R={'-' if r is None else f'{r:.2f}':<6} {why}")
        for p in sorted(pools_of[target], key=lambda p: p.reset or now + timedelta(days=999)):
            when = f"{p.reset:%Y-%m-%dT%H:%MZ}  {until(p.reset, now)}" if p.reset else "unknown"
            tag = " (model only)" if p.model else " unstarted" if unstarted(p, now) else ""
            print(f"            {p.name:<22} {p.left:>3.0f}%  {when}{tag}")
    return any(isinstance(c[2], str) and c[2].startswith("error") for c in collected)


def main():
    args = sys.argv[1:] or ["personal", "work"]
    groups = []  # (title, targets)
    for a in args:
        if a in GROUPS:
            groups.append((f"{a} group", GROUPS[a]))
        elif a == "subagent" or re.fullmatch(r"(claude|codex|agy):(work|personal|personal2)", a):
            groups.append((a, [a]))
        else:
            sys.exit(f"bad argument {a}: use personal, work, subagent or <claude|codex|agy>:<work|personal|personal2>")
    now = datetime.now(timezone.utc)
    targets = list(dict.fromkeys(t for _, ts in groups for t in ts))
    with ThreadPoolExecutor(len(targets)) as ex:
        got = {c[0]: c for c in ex.map(lambda t: collect(t, now), targets)}
    kicked = [t for t, tool, pools in got.values()
              if not isinstance(pools, str) and kicks(tool, pools, now) and kick(t.split(":")[1])]
    note = f"  kicked: {', '.join(kicked)} (started the weekly clock)" if kicked else ""
    print(f"now {now:%Y-%m-%dT%H:%MZ}{note}")
    failed = False
    for title, ts in groups:
        failed |= report(title, [got[t] for t in ts], now)
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
