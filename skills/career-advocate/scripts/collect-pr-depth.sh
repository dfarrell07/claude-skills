#!/bin/bash
set -euo pipefail

# Enriches merged PRs with pre-squash commit histories.
# Reads PR JSON array on stdin, outputs enriched array on stdout.

MAX_PRS=200
DELAY=0.5

if ! command -v jq &>/dev/null; then
  cat
  exit 0
fi

PRS_INPUT=$(cat)
TOTAL=$(echo "$PRS_INPUT" | jq 'length')

if [[ "$TOTAL" -eq 0 ]]; then
  echo "$PRS_INPUT"
  exit 0
fi

LIMIT=$((TOTAL < MAX_PRS ? TOTAL : MAX_PRS))
if [[ "$TOTAL" -gt "$MAX_PRS" ]]; then
  echo "PR depth: processing $MAX_PRS of $TOTAL merged PRs" >&2
fi

RESULT="$PRS_INPUT"

for i in $(seq 0 $((LIMIT - 1))); do
  repo=$(echo "$PRS_INPUT" | jq -r ".[$i].repo")
  number=$(echo "$PRS_INPUT" | jq -r ".[$i].number")

  depth_commits=$(gh pr view "$number" --repo "$repo" \
    --json commits \
    --jq '[.commits[] | {hash: .oid[0:7], message: .messageHeadline}]' \
    2>/dev/null || echo "[]")

  RESULT=$(echo "$RESULT" | jq --argjson idx "$i" \
    --argjson dc "$depth_commits" \
    '.[$idx].depth_commits = $dc')

  sleep "$DELAY"
done

echo "$RESULT"
