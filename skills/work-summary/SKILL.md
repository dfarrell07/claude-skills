---
name: work-summary
description: "Summarize recent work into a weekly what-I-did report using local git commits and authored GitHub PRs/issues."
version: 1.0.0
argument-hint: "[days]"
user-invocable: true
allowed-tools: Bash, Read, Write
---

# Work Summary

Use for weekly updates, “what did I do?”, and recent-work summaries.
Default to the last 7 rolling days; accept a positive integer for another period.
Reject invalid arguments instead of silently changing the requested period.

```text
/work-summary           # Last 7 days
/work-summary 14        # Last 14 days
```

Arguments: `$ARGUMENTS` (when supplied by the host).
If the host does not expand that placeholder, use the user's requested days or 7.

## Collect Evidence

Resolve this skill's installed directory from the location of this `SKILL.md`.
Do not use `BASH_SOURCE` in an interactive tool shell: it does not identify the skill.
Set `SKILL_DIR` to that absolute path and `DAYS` to the requested positive integer.

Check for Bash 4+, Git 2.37+ (for `--since-as-filter`), and jq. GitHub collection additionally needs `gh` authenticated
with access to the relevant repositories. Missing authentication still allows a
local-only report, with an explicit coverage gap. No fetches or external writes are needed.

Create a separate directory for this run and keep its path for subsequent tool calls:

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/work-summary.XXXXXX")
bash "$SKILL_DIR/scripts/work-summary.sh" "$DAYS" > "$RUN_DIR/data.json"
```

Read `data.json` from that exact directory. Stop if collection fails; do not reuse an
older capture. Shell variables may not persist between tool calls, so retain the
absolute paths rather than assuming they remain set.

Default local scan roots: `~/go/src`, `~/konflux`, `~/projects`, `~/repos`, and the current
directory, up to five levels deep. The enclosing repository is included when invoked
from a subdirectory. Clones and worktrees are supported; worktrees sharing a common Git directory are counted once. For different locations,
pass explicit roots after days; these replace the defaults:

```bash
bash "$SKILL_DIR/scripts/work-summary.sh" "$DAYS" /path/to/work /path/to/another/repo > "$RUN_DIR/data.json"
```

Local commits match each repository's configured `user.email` across all refs and are
grouped by absolute repository path. Other author emails, clones outside scan roots,
uncommitted changes, reviews, Jira, and other work sources are outside this capture.
Read the metadata warnings and report any skipped sources or result caps.

## Analyze the Evidence

Preserve `metadata`, `github`, and `commits` unchanged in `enriched.json` in the same
run directory, adding an `analysis` object. Group related work into a few useful
themes and describe concrete results supported by the evidence. Use local commits
as well as PRs; avoid counting a commit and its PR as separate accomplishments.

GitHub scope is **authored PRs/issues updated in the window**. An update may come from
someone else or automation. A PR's present state is not proof that it merged during
this period; `closed_at` can help check timing but does not establish deployment.
Merged PRs can support delivered code changes. Closed unmerged PRs and closed issues
need context before describing them as accomplishments. Titles alone do not establish
impact, testing, release, or deployment; inspect relevant evidence if those claims matter.
Open items belong in `in_progress`, phrased as work under review or ongoing work rather
than necessarily work performed this week. Link claims to their supporting sources.

Example `analysis` structure (the rest of the capture is copied unchanged):

```json
{
  "themes": [
    {
      "name": "CI reliability",
      "description": "Merged a fix that propagates verification failures.",
      "items": [
        {"repo": "org/repo", "number": 123, "url": "https://github.com/org/repo/pull/123"}
      ]
    }
  ],
  "in_progress": [
    {
      "description": "Upgrade baseline changes under review",
      "repo": "org/repo",
      "number": 456,
      "url": "https://github.com/org/repo/pull/456"
    }
  ]
}
```

For themes supported only by local commits, use an empty `items` array and name the
repository and commit hashes in the description. Empty evidence calls for an empty
analysis and a clear coverage statement, not inferred accomplishments.

## Render and Deliver

```bash
bash "$SKILL_DIR/scripts/render-report.sh" "$RUN_DIR/enriched.json" > "$RUN_DIR/report.md"
```

Read the rendered report to check that claims match evidence, links identify the
right items, and coverage gaps remain visible. Deliver a concise weekly summary
and the report path. Keep collection artifacts local unless sharing is requested.

For a quick report without agent analysis, run `make work-summary` (or `DAYS=14`).
Run `make work-summary-test` for offline collection/rendering regression tests.
