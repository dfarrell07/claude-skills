---
name: work-summary
description: "Show recent work: git commits and GitHub activity (PRs, issues)"
version: 1.0.0
argument-hint: "[days]"
user-invocable: true
allowed-tools: Bash, Read, Write
---

# Work Summary

```bash
/work-summary           # Last 7 days (default)
/work-summary 14        # Last 14 days
```

**Arguments:** $ARGUMENTS

---

## Step 0: Collect Work Data

```bash
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

# Parse days argument (default: 7)
DAYS="$ARGUMENTS"
[[ -z "$DAYS" || ! "$DAYS" =~ ^[0-9]+$ ]] && DAYS=7

# Run collection script, save JSON for Step 1
JSON_OUTPUT=$(bash "$SCRIPT_DIR/scripts/work-summary.sh" "$DAYS")
echo "$JSON_OUTPUT" > /tmp/work-summary-data.json
cat /tmp/work-summary-data.json
```

---

## Step 1: Analyze and Enrich Data

Read `/tmp/work-summary-data.json` and analyze to identify themes and group related PRs/issues.

Use Write tool to save enriched JSON to `/tmp/work-summary-enriched.json` with this structure:

```json
{
  "metadata": {copy from input},
  "github": {copy from input},
  "commits": {copy from input},
  "analysis": {
    "themes": [
      {
        "name": "CI/Infrastructure",
        "description": "Brief accomplishment description",
        "items": [
          {"repo": "org/repo", "number": 123, "url": "https://github.com/org/repo/pull/123"}
        ]
      }
    ],
    "in_progress": [
      {
        "description": "Brief description",
        "repo": "org/repo",
        "number": 456,
        "url": "https://github.com/org/repo/pull/456"
      }
    ]
  }
}
```

Themes: security, CI/infrastructure, features, bugs, documentation, releases. Group merged/closed items by theme. List open items in `in_progress`.

---

## Step 2: Render Report

```bash
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
bash "$SCRIPT_DIR/scripts/render-report.sh" /tmp/work-summary-enriched.json
```
