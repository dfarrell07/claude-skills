#!/usr/bin/env python3
"""Print a codex account's rate-limit usage from `codex app-server`, spending no model tokens.

Usage: CODEX_HOME=<dir> codex-usage.py
Prints one line per window (`<used>% used, <window>, resets <UTC time>`). Exits 3 when the account has
no usage API (API-key or project auth), so the caller can fall back to a probe prompt.
"""
import json
import subprocess
import sys
from datetime import datetime, timezone

proc = subprocess.Popen(["codex", "app-server"], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                        stderr=subprocess.DEVNULL, text=True)
send = lambda msg: (proc.stdin.write(json.dumps(msg) + "\n"), proc.stdin.flush())
send({"id": 1, "method": "initialize", "params": {"clientInfo": {"name": "delegate-usage", "version": "1"}}})
send({"method": "initialized"})
send({"id": 2, "method": "account/rateLimits/read"})

reply = None
for line in proc.stdout:  # the server also emits notifications; wait for our id
    msg = json.loads(line)
    if msg.get("id") == 2:
        reply = msg
        break
proc.kill()

limits = ((reply or {}).get("result") or {}).get("rateLimits")
if not limits:
    sys.exit(3)
for key in ("primary", "secondary"):
    w = limits.get(key)
    if w:
        mins = w["windowDurationMins"]
        window = f"{mins // 1440}d window" if mins >= 1440 else f"{mins // 60}h window"
        reset = datetime.fromtimestamp(w["resetsAt"], timezone.utc).strftime("%Y-%m-%dT%H:%MZ")
        print(f"{w['usedPercent']}% used, {window}, resets {reset}")
if limits.get("rateLimitReachedType"):
    print(f"LIMIT REACHED: {limits['rateLimitReachedType']}")
