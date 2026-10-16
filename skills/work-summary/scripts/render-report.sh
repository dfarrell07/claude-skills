#!/bin/bash
set -euo pipefail

# Require an explicit capture so an old report cannot be reused accidentally.
JSON_FILE="${1:-}"
if [[ -z "$JSON_FILE" || ! -f "$JSON_FILE" ]]; then
  echo "Usage: render-report.sh path-to-json (existing capture required)" >&2
  exit 1
fi

# Validate before printing: malformed evidence must not produce a success report.
if ! jq -e '
  (.metadata | type == "object") and
  (.metadata.days | type == "number") and
  (.metadata.since_date | type == "string") and
  (.metadata.to_date | type == "string") and
  (.metadata.repos_searched | type == "number") and
  (.metadata.repos_with_commits | type == "number") and
  (.github.prs | type == "array") and (.github.issues | type == "array") and
  (.commits | type == "object") and
  (all(.commits[]; type == "array")) and
  (if has("analysis") then
    (.analysis.themes | type == "array") and
    (.analysis.in_progress | type == "array")
  else true end)
' "$JSON_FILE" >/dev/null; then
  echo "Error: Invalid work-summary capture: $JSON_FILE" >&2
  exit 1
fi

# Extract all values in single jq call
read -r DAYS SINCE TO REPOS_SEARCHED REPOS_WITH_COMMITS PR_COUNT PR_MERGED PR_OPEN PR_CLOSED ISSUE_COUNT COMMIT_COUNT HAS_ANALYSIS <<< \
  "$(jq -r '[
    .metadata.days,
    .metadata.since_date,
    .metadata.to_date,
    .metadata.repos_searched,
    .metadata.repos_with_commits,
    (.github.prs | length),
    ([.github.prs[] | select(.state == "merged")] | length),
    ([.github.prs[] | select(.state == "open")] | length),
    ([.github.prs[] | select(.state == "closed")] | length),
    (.github.issues | length),
    ([.commits | to_entries[] | .value | length] | add // 0),
    (has("analysis") | tostring)
  ] | join(" ")' "$JSON_FILE")"

echo "# Work Summary: Last $DAYS Days ($SINCE to $TO)"
echo ""
echo "GitHub scope: authored PRs/issues updated during this period. Updates may come from other people or automation."
echo "Local scope: commits matching each repository’s configured author email, across all local refs."
echo ""
if [[ $(jq '(.metadata.warnings // []) | length' "$JSON_FILE") -gt 0 ]]; then
  echo "## Coverage Gaps"
  echo ""
  jq -r '.metadata.warnings[] | "- \(.)"' "$JSON_FILE"
  echo ""
fi
echo "## Executive Summary"
echo ""

if [[ "$HAS_ANALYSIS" == "true" ]]; then
  echo "### Key Accomplishments"
  echo ""
  jq -r '.analysis.themes[] | "- **\(.name)**: \(.description)" + (if (.items | length) > 0 then " (\(.items | map("[\(.repo)#\(.number)](\(.url))") | join(", ")))" else "" end)' "$JSON_FILE"
  echo ""
  echo "### In Progress"
  echo ""
  jq -r '.analysis.in_progress[] | "- \(.description) ([\(.repo)#\(.number)](\(.url)))"' "$JSON_FILE"
  echo ""
fi

echo "### Activity Stats"
echo ""
echo "- $PR_COUNT PRs ($PR_MERGED merged, $PR_OPEN open, $PR_CLOSED closed without merge) | $ISSUE_COUNT issues | $COMMIT_COUNT commits across $REPOS_WITH_COMMITS repos"
echo "- Period: $SINCE to $TO"
echo ""
echo "---"
echo ""
echo "## Detailed Work Log"
echo ""
echo "### GitHub Activity"
echo ""
echo "#### Pull Requests ($PR_COUNT total)"
echo ""

jq -r '.github.prs[] | "- [\(.repo)#\(.number)](\(.url)) \(.title) (\(.state))"' "$JSON_FILE"

echo ""
echo "#### Issues ($ISSUE_COUNT total)"
echo ""

if [[ "$ISSUE_COUNT" -eq 0 ]]; then
  echo "No authored issues found in this capture; check coverage gaps above."
else
  jq -r '.github.issues[] | "- [\(.repo)#\(.number)](\(.url)) \(.title) (\(.state))"' "$JSON_FILE"
fi

echo ""
echo "### Git Commits"
echo ""
echo "**Searched $REPOS_SEARCHED repositories, found commits in $REPOS_WITH_COMMITS**"
echo ""

if [[ "$COMMIT_COUNT" -eq 0 ]]; then
  echo "No matching local commits found."
else
  jq -r '.commits | to_entries[] | "#### \(.key)\n\n" + (.value | map("- `\(.hash)` \(.message)") | join("\n")) + "\n"' "$JSON_FILE"
fi
