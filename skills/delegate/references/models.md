# Models and defaults per account

Verified 2026-10-27 with tiny "reply ok" calls. Availability changes with budgets, org policy and releases, so
re-probe before trusting a denial. The defaults live in `scripts/delegate.sh` (the `case $tool:$account` block).
`DELEGATE_MODEL` bypasses them and uses only that model; `DELEGATE_EFFORT` overrides the default effort.

## Defaults

- `agy:personal`, `agy:personal2`: `gemini-4-argon`, medium. agy tokens are plentiful and wasted on weak models, so use the
  strongest Gemini. Medium beat high on a real audit (7.5 min and no wrong claims, vs 17.5 min and 3 wrong); pass
  `DELEGATE_EFFORT=high` to try it.
- `codex:personal`: `gpt-6-luna`, medium. The free personal account has few tokens.
- `codex:work`: `gpt-6.1-sol`, then `gpt-5.6-luna`, high. Sol when the budget allows; luna is what the work project can use.
- `claude:personal`, `claude:personal2`: `claude-sonnet-5-5`, medium. Good quality per token; pass `DELEGATE_MODEL=opus` for
  hard work.
- `claude:work`: `haiku`, high. The only model the org policy allows.
- `agy:work` and `codex:personal2`: not set up (no login on this machine).

A failed model falls through to the next candidate; the last error is what `FAIL` prints. A 403 takes about 25 seconds
(codex retries), so when you know sol is out of budget set `DELEGATE_MODEL=gpt-5.6-luna` to skip it.

**Work, out of budget (the normal state here):** the strong work models are unavailable, so work targets default to the
basic ones at high effort: `gpt-5.6-luna` for codex and `haiku` for claude. Codex still tries `gpt-6.1-sol` first and falls
back automatically, so no setting changes when budget returns. Both basic models accept every effort level, so high is
the default. On a trivial arithmetic prompt effort changed nothing measurable (codex luna 11,605 tokens at low vs 11,618 at
max; haiku 152 vs 171 output tokens), so it is unproven whether effort helps these models. It is cheap to ask for; test
on a hard task before relying on it.

## agy (Antigravity)

- `agy models` lists the ids. Names carry an effort suffix: `gemini-4-argon-{low,medium,high}`,
  `gemini-3.8-flash-{low,medium,high}`, `claude-opus-5-5-{low,medium,high}`, `claude-sonnet-5-5-{low,medium,high}`,
  `gpt-oss-120b-medium`.
- Pass either a suffixed id alone, or the base name plus `--effort` (the default does the latter). A suffixed id plus
  `--effort` errors. `max` and `xhigh` depend on the model; argon and flash stop at high.
- `claude-opus-5-5-high` through agy spends Google-side tokens on a Claude model, but it is the same family as a Claude
  orchestrator, so its errors correlate with yours. Prefer Gemini for diversity.
- Both accounts work. `personal2` keeps its login in `~/.gemini-per2` (the null DBUS address disables the shared keyring).
- Measured on a repo-wide bloat audit (9k lines of Python, 2026-10-27), same prompt for all, one run each. Every item was
  then graded against the code by Opus at high effort (REAL = a maintainer would accept it, TRIVIAL = true but taste, WRONG =
  false or would break something), and the top REAL items were applied to a clean checkout and the test suite run (all passed):

  - `gemini-4-argon-medium`: 7.5 min; 7 REAL, 5 TRIVIAL, 0 WRONG of 12. Best result.
  - `gemini-4-argon-low`: 5 min; 5 REAL, 3 TRIVIAL, 2 WRONG of 10.
  - `gemini-4-argon-high`: 17.5 min alone; 4 REAL, 5 TRIVIAL, 3 WRONG of 12. Slower and no better: it proposed bigger
    restructurings, which were the wrong ones. (Hit a 10 min cap when run alongside six others.)
  - `claude-sonnet-5-5-medium`: 40 s; 0 REAL, 3 TRIVIAL, 1 WRONG of 4. Ran ruff and reference-count scripts; honest that
    there was little to find. Same family as claude.
  - `gemini-3.8-flash-low`: 7 min; 0 REAL, 3 TRIVIAL, 4 WRONG of 7.
  - `gpt-oss-120b-medium`: 7.5 min; 0 REAL, 0 TRIVIAL, 7 WRONG of 7. Every claim was false (it called helpers with many
    callers one-use). Do not use it for audits.
  - `gemini-3.8-flash-high`: timed out at 10 min.

  One run per model, so treat differences as indicative, not settled. The strongest finding is that a separate grading
  pass flagged 17 of 52 items as wrong cheaply; I spot-checked a sample and the test run agreed where it could.
  The same REAL items recurred across argon runs (duplicated test constants,
  unused `GuardCase.bash`, unused `self.origin`, redundant `commit_env`), which is itself a good sign of a real finding.

  Higher effort is slower, not hung, and here not better. agy may print narration
  before the `VERDICT` line and wraps references in long `file://` links, so find the verdict with `grep`, not `head`.
- No flag makes agy read-only or turns off telemetry.

## codex

- Personal (`~/.codex-personal`, free account) offers `gpt-6-luna`, `gpt-5.6-terra`, `gpt-5.6-luna`. All cheap tier.
- Work (`~/.codex`) lists more (`gpt-6.1-sol`, `gpt-6-astra`, `gpt-6.1-terra`, `gpt-6-sol`, `gpt-5.6-sol`, `gpt-5.5`, ...)
  but the project returned 403 "does not have access" for every one of those and for `gpt-6-luna` and `gpt-5.6-terra`.
  `gpt-5.6-luna` works at low, medium, high, xhigh and max. `gpt-6.1-sol` failed this way even though it is the configured
  default; if it recovers (the 403 may be budget, not entitlement) the default order will start using it.
- `codex debug models` with `CODEX_HOME` set prints the full list with supported efforts.
- Efforts: low, medium, high, xhigh, max (some models add ultra, and `minimal` is rejected).

## claude

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
