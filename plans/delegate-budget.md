# Delegate picks targets from live budget

Status: built, 2026-11-24.

## Problem

Quota knowledge was split across two skills and three scripts. `/token-stats` read usage; `/delegate` had its own
`quota.sh` and `codex-usage.py` reading the same sources a second way; the routing rules ("weigh the reset time",
"keep a margin") were prose in both SKILL.md files, applied loosely. Live checks on 2026-11-24 found what that missed:

- `agy:personal` Gemini 5h pool at 0%: locked out for 2h16m while the weekly pool still had 73%.
- `codex:personal` 30-day pool at 0% (`rate_limit_reached`) until 11-26.
- `agy:personal2` logged out: agy prints an OAuth URL, waits 60 s, exits 0. Reported as "empty reply".
- `claude:personal2` `/usage` shows only cost lines (not a subscription login). Reported as an error.
- The separate argon weekly pool is gone; argon now draws on agy's `Gemini` pools.
- `claude:personal` shows no `Current session` (5h) row on an active session; the CLI prints one only when the
  server returns it. A missing 5h row means "none reported", not a parse failure.

## How each source reports and resets (verified 2026-11-24)

| Target | Source | Pools and window length `W` | Reset |
| --- | --- | --- | --- |
| `claude:*` | `claude -p /usage` | `Current session` = 5h, `Current week` = 7d (by label) | weekly fixed (Tue 09:00Z) |
| `codex:personal` | app-server `rateLimits/read` | `windowDurationMins` (free: one 30-day) | `resetsAt`; first-use unverified |
| `codex:work` | none: API-key auth | org budget | unknown; denial falls back sol→luna |
| `agy:*` | `agy --prompt=/usage` TSV | `Five Hour` = 5h, `Weekly` = 7d (by label) | **first use**: untouched: 100%, reset = now + `W` |

## Decision: one skill, policy in code

Routing is the only consumer of the numbers, so quota awareness goes inside `/delegate`. The split worth keeping is
**facts vs policy**, inside one script: `budget.py` has collectors that return `(pool, left, W, reset)` and a pure
policy over them. Tests import the policy and feed fixtures with a fixed clock; no CLI needed. SKILL.md stops
describing rules and says: run `budget.py`, follow its order.

`/token-stats` is folded in as `/delegate budget`; the delegate description keeps the "how much quota is left"
trigger. Rejected: a separate sensor skill. Its one prospective other consumer (the sessions orchestrator) can call
the script by path, and a second SKILL.md just duplicated routing prose.

## Timing rules

`L` = percent left (float), `W` = window, `T` = time to reset. A reset time already past means the pool has reset:
treat it as `L` = 100, unstarted.

1. **5-hour pools are ceilings.** `L` ≤ 10 → `blocked`, printing `T` (when it rolls over); `L` ≤ 20 → `hold`, since a
   run or two could fill it. No release near the reset: an agy run takes 7-17 min and can't stop halfway. Only
   account-wide limits count; a limit for a single model (claude `week Fable`, a named codex limit) is not routed.
2. **Long pools (weekly, 30-day) burn down to zero at reset.** Pace ratio `R = L·W / (100·T)`: the burn rate that ends
   at zero, over the steady rate. `R` = 1 is on pace; it grows by itself as the reset nears (30% left with 1 h to go on
   a week: `R` = 50), so spending rises toward reset and leftover reaches about zero. One number for weekly and monthly,
   comparable across window lengths. `L` = 0 → `blocked`; `R` < 0.9 → `hold` (behind pace: only if no target is
   `ok`); otherwise `ok`. Percentages can't compare absolute pool sizes; nothing here measures that.
3. **The orchestrator's own account** (the `subagent` row) gets a 25% 5h floor, and its long pools are `hold` below
   pace (`R` < 1). Built-in subagents then spend only the surplus above the pace line, and the main session never
   locks itself out. The pace line still ends at zero, so that pool isn't wasted either.
4. **First-use clocks get kicked.** An agy weekly pool that delegate routes to (Gemini) reading `L` = 100 with
   `T` ≥ `W` − 2 min has not started. Each `budget.py` run fires one tiny background prompt on `gemini-3.8-flash` at
   low effort for that account, so the new week starts at once, not at the next real job. That also starts the Gemini
   5h clock, which is harmless. At most one kick per account per 15 min: a kicked pool still reads unstarted for a
   while. Not kicked: agy Claude/GPT pools (never routed, not shown) and codex (small pool, behavior unverified).
5. **Order:** verdict (`ok` > `hold` > `blocked`), then a target's `R` (its lowest across long pools), highest first.
   Give more runs to higher `R`, and split N runs across agy and codex for independent errors.

## Verification

- `make delegate-test`: config-dir test (updated) plus policy tests on fixtures (real output captured 2026-11-24,
  identifiers removed): 5h floor, `R` at start/middle/end, past reset, unstarted detection and kick choice, orchestrator
  rules, ordering, each parser, logged-out and no-subscription replies.
- Live: `budget.py` against every account. Kick verified on agy's unstarted Claude/GPT pool (one `gpt-oss` "ok"
  prompt through `delegate.sh`): its reset fixed at the prompt's time and stayed there 4 min later, though the pool
  still read 100%, so the 2-minute rule, not the percentage, is what marks it started.

## Not in scope

- A timer that runs the kick even when delegate isn't used belongs in laptop-setup. Proposed, not built.
- Choosing among the new Gemini 4 models (neon, carbon, hydrogen) as defaults: needs a measured audit like the
  argon one.
