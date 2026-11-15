#!/usr/bin/env python3
"""List live Claude Code sessions across all config dirs (profiles) and
print each one's SendMessage address.

The built-in ListAgents only shows sessions that share this session's
CLAUDE_CONFIG_DIR. SendMessage still delivers to any session's messaging
socket when given `to="uds:<socket path>"`, so this script only has to find
that path. It reads `<config dir>/sessions/<pid>.json` and nothing else
(never the `.key` files beside them).

Usage: peers.py   (one line per session: name, profile, status, pid,
                   address, cwd; profiles are ~/.claude, ~/.claude-* and
                   $CLAUDE_CONFIG_DIR)
"""
import glob
import json
import os
import re
import stat
import sys


def config_dirs():
    home = os.path.expanduser("~")
    dirs = [os.environ.get("CLAUDE_CONFIG_DIR", ""), os.path.join(home, ".claude")]
    dirs += sorted(glob.glob(os.path.join(home, ".claude-*")))
    seen, out = set(), []
    for d in dirs:
        if not d or not os.path.isdir(os.path.join(d, "sessions")):
            continue
        real = os.path.realpath(d)
        if real not in seen:
            seen.add(real)
            out.append(d)
    return out


def alive(pid, proc_start):
    """True if PID runs and, where /proc exists, started when the record says."""
    if not os.path.isdir("/proc"):
        try:
            os.kill(pid, 0)
            return True
        except PermissionError:
            return True
        except OSError:
            return False
    try:
        with open(f"/proc/{pid}/stat") as f:
            data = f.read()
    except OSError:
        return False
    # Field 22 is the start time; comm (field 2) may hold spaces or ')'.
    return data[data.rindex(")") + 2:].split()[19] == str(proc_start)


def is_socket(path):
    try:
        return stat.S_ISSOCK(os.stat(path).st_mode)
    except OSError:
        return False


def clean(text):
    return re.sub(r"[\x00-\x1f\x7f]", "?", str(text))


def live_sessions():
    out = []
    for d in config_dirs():
        for path in sorted(glob.glob(os.path.join(d, "sessions", "*.json"))):
            try:
                with open(path) as f:
                    rec = json.load(f)
                pid, sock, start = rec["pid"], rec["messagingSocketPath"], rec["procStart"]
            except (OSError, ValueError, KeyError, TypeError, RecursionError):
                continue
            if type(pid) is not int or not isinstance(sock, str) or not start:
                continue
            if not alive(pid, start) or not is_socket(sock):
                continue
            out.append({
                "name": rec.get("name") or "",
                "profile": os.path.basename(os.path.realpath(d)),
                "status": rec.get("status") or "?",
                "pid": pid,
                "cwd": rec.get("cwd") or "",
                "address": "uds:" + sock,
            })
    return out


def main(argv):
    if argv:
        print(__doc__.strip(), file=sys.stderr)
        return 2
    for s in live_sessions():
        print("\t".join(clean(v) for v in (
            s["name"] or "(unnamed)", s["profile"], s["status"],
            f"pid {s['pid']}", s["address"], s["cwd"])))
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
