# Models and defaults per account

Verified 2026-10-27 with tiny "reply ok" calls. Availability changes with budgets, org policy and releases, so
re-probe before trusting a denial. The defaults live in `scripts/delegate.sh` (the `case $tool:$account` block).
`DELEGATE_MODEL` bypasses them and uses only that model; `DELEGATE_EFFORT` overrides the default effort.

## Defaults

- `agy:personal`, `agy:personal2`: `gemini-4-argon`, then `gemini-3.8-flash`, medium. agy tokens are plentiful and wasted
  on weak models, so use the strongest Gemini. Medium beat high on a real audit (7.5 min and no wrong claims, vs 17.5 min
  and 3 wrong); pass `DELEGATE_EFFORT=high` to try it. `agy /usage` shows two pools, each 5-hour and weekly: Gemini
  models (argon and flash share it) and Claude/GPT models. A 429 "Individual quota reached" names the reset; flash is
  the fallback for a per-model denial. `agy models` also lists Gemini 4 neon, carbon and hydrogen, not yet measured.
- `codex:personal`: `gpt-6-luna`, medium. The free personal account has one 30-day window with few tokens, so keep
  prompts tight.
- `codex:work`: `gpt-6.1-sol`, then `gpt-5.6-luna`, high. Sol when the budget allows; luna is what the work project can use.
- `claude:personal`, `claude:personal2`: `claude-sonnet-5-5`, medium; `claude:work`: `haiku`, high, the only model
  the org policy allows. Off-limits unless the user names them (see SKILL.md).
- `agy:work` and `codex:personal2`: not set up (no login on this machine).

A failed model falls through to the next candidate; the last error is what `FAIL` prints. Work is normally out of
budget, so `codex:work` usually lands on `gpt-5.6-luna`; sol's 403 costs about 25 seconds (codex retries), so set
`DELEGATE_MODEL=gpt-5.6-luna` to skip it when you know. High effort is the work default because it is cheap to ask
for; on a trivial prompt it changed nothing measurable, so whether it helps is unproven.

## agy (Antigravity)

- `agy models` lists the ids. Names carry an effort suffix: `gemini-4-argon-{low,medium,high}`,
  `gemini-3.8-flash-{low,medium,high}`, `claude-opus-5-5-{low,medium,high}`, `claude-sonnet-5-5-{low,medium,high}`,
  `gpt-oss-120b-medium`.
- Pass either a suffixed id alone, or the base name plus `--effort` (the default does the latter). A suffixed id plus
  `--effort` errors. `max` and `xhigh` depend on the model; argon and flash stop at high.
- `claude-opus-5-5-high` through agy spends Google-side tokens on a Claude model, but it is the same family as a Claude
  orchestrator, so its errors correlate with yours. Prefer Gemini for diversity.
- `personal2` keeps its login in `~/.gemini-per2` (the null DBUS address disables the shared keyring); `budget.py`
  says when either account is logged out.
- Measured 2026-10-27 on one repo-wide audit, graded by a stronger model: `gemini-4-argon-medium` was best (7 of 12
  findings real, none wrong, 7.5 min); high was slower and worse; flash needs a separate grading pass; `gpt-oss-120b`
  got every claim wrong, so never use it for audits. agy may narrate before the `VERDICT` line, so `grep` for it.
- No flag turns off telemetry. `--mode=plan` blocks edits but answers with a plan instead of the reply (and is
  ignored with `--disable-slash-commands`), so delegate runs agy with edits allowed.

## codex

- Personal (`~/.codex-personal`, free account) offers `gpt-6-luna`, `gpt-5.6-terra`, `gpt-5.6-luna`. All cheap tier.
- Work (`~/.codex`) lists more (`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-5.6-sol`, `gpt-5.5`, ...)
  but the project returned 403 "does not have access" for every one of those and for `gpt-6-luna` and `gpt-5.6-terra`.
  `gpt-5.6-luna` works at low, medium, high, xhigh and max.
- `codex debug models` with `CODEX_HOME` set prints the full list with supported efforts.
- Efforts: low, medium, high, xhigh, max (some models add ultra, and `minimal` is rejected).

## claude

Off-limits as a delegate target unless the user names it (scripts need `DELEGATE_ALLOW_CLAUDE=1`). Kept for reference.

- Personal accounts: any model, e.g. `claude-sonnet-5-5`, `opus`. `personal2` uses `~/.claude-per2`.
- Work runs on Vertex under an org allow-list: `haiku` works at every effort. These all failed:
  `sonnet`, `claude-sonnet-4-5`, `claude-sonnet-4-6`, `claude-sonnet-5`, `claude-sonnet-5-5`, `claude-haiku-5-5`,
  `claude-opus-4`, `claude-opus-4-1`, `claude-opus-5`, `claude-opus-5-5` (policy denial, "not available on your vertex
  deployment", or retired). Opus 4 and Sonnet 4 were retired on 2026-09-14.
- A bad effort value only warns and uses the default, so a typo is silent; the script validates `DELEGATE_EFFORT`.
- The CLI exits 0 on API and quota errors and prints them as the answer; the script turns those into `FAIL`.

## Re-probing

```bash
echo "Reply with exactly: ok" > /tmp/p.md
DELEGATE_MODEL=<model> DELEGATE_EFFORT=low bash scripts/delegate.sh <tool>:<account> /tmp/p.md
```

Work accounts spend real budget, so probe a model once rather than sweeping.
