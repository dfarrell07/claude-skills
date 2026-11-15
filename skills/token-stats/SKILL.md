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
`<claude|codex|agy>:<work|personal|personal2>`. Each line is one limit: `left` is percent **remaining** (all tools
normalized), `resets` is UTC, `in` is time until reset. It asks each CLI for its own usage (`/usage`, codex
`account/rateLimits/read`), so it spends no model tokens and reads no credential files. Never print tokens, keys or
auth files when extending it.

Non-quota lines are expected: `claude:work` is Vertex, billed per token; `codex:work` uses an API key with an org
budget (only a probe shows if it works: delegate's `quota.sh codex:work`, ~12k tokens); `agy:work` and
`codex:personal2` have no login on this machine.

## Routing

- A target's room is its **lowest** `left`: every limit must have room (a 0% 5h session blocks a target with a
  full week).
- Spend first what resets soonest with the most left: unused quota is lost at reset. A full pool resetting in hours
  beats a half-full one resetting in a week.
- Keep a margin on pools that reset in days; don't drain a weekly pool to 0% early.
- A pool at 100% whose `in` equals its full window (e.g. `5h00m` on a 5h pool) has not started; its clock only
  starts on first use, so nothing is lost by leaving it idle.
- Rank pools only inside one account group: all `work`, or all `personal`/`personal2` (delegate's data boundary).
- agy pools are per model family: `gemini-4-argon` needs room in `Gemini 4 Argon week` (count the `Gemini` pools
  too, to be safe), other Gemini models in the `Gemini` pools, GPT models in `Claude/GPT`. Each delegate call uses
  one model, so split work by pool.
- Claude accounts are shown for completeness. `/delegate` never uses claude unless the user names it, and that
  includes `claude-*` models in agy's Claude/GPT pool (GPT there is fine).
