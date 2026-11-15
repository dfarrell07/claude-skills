---
name: token-stats
description: Show usage left and reset times for Claude, Codex and Antigravity (agy) on the work, personal and personal2 accounts. Use before routing or delegating work, so tokens that reset soon get spent first.
version: 0.1.0
argument-hint: "[tool:account ...]"
user-invocable: true
allowed-tools: Bash
---

# Token stats

```bash
python3 -I "$SKILL_DIR/scripts/token_stats.py" [tool:account ...]   # default: all nine, ~7 s
```

**Arguments:** $ARGUMENTS

`SKILL_DIR` is the directory containing this file. Run the script and show its table. Targets are
`<claude|codex|agy>:<work|personal|personal2>`. Each line is one limit: `left` is percent **remaining** (all tools normalized), `resets` is UTC, `in` is time until reset. It asks
each CLI for its own usage (`/usage`, codex `account/rateLimits/read`), so it spends no model tokens and reads no
credential files. Never print tokens, keys or auth files when extending it.

Non-quota lines are expected: `claude:work` is Vertex, billed per token; `codex:work` uses an API key with an org
budget (only a probe shows if it works: delegate's `quota.sh codex:work`, ~12k tokens); `agy:work` and
`codex:personal2` have no login on this machine.

## Routing

- A target is usable only if **every** limit on it has room: the lowest `left` wins (a 0% 5h session blocks a
  target with a full week).
- Spend first what resets soonest with the most left: unused quota is lost at reset. A full pool resetting in hours
  beats a half-full one resetting in a week.
- Keep a margin on pools that reset in days; don't drain a weekly pool to 0% early.
- Per-model pools (agy argon vs Gemini vs Claude/GPT) are separate: pick the model whose pool has room.
- Claude accounts are shown for completeness. `/delegate` still never uses claude targets unless the user names them.
