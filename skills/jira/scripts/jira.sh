#!/bin/bash
# Jira helper script — wraps acli commands with retry logic
set -euo pipefail

run_with_retry() {
  local OUTPUT
  for ATTEMPT in 1 2; do
    if OUTPUT=$("$@" </dev/null); then
      echo "$OUTPUT"
      return 0
    fi
    if [[ "$ATTEMPT" -eq 1 ]]; then
      echo "Jira query failed, retrying..." >&2
      sleep 2
    fi
  done
  echo "ERROR: Jira command failed after 2 attempts" >&2
  echo "Command: $*" >&2
  return 1
}

cmd_my_issues() {
  run_with_retry acli jira workitem search \
    --jql 'assignee = currentUser() AND status NOT IN (Closed, Done, Resolved) ORDER BY updated DESC' \
    --json --paginate
}

cmd_search() {
  local jql="$1"
  run_with_retry acli jira workitem search \
    --jql "$jql" \
    --json --limit 25
}

VIEW_FIELDS='summary,status,assignee,reporter,priority,issuetype,description,comment,labels,components,fixVersions,issuelinks,updated,created,resolution'

cmd_view() {
  local key="$1"
  local output
  output=$(run_with_retry acli jira workitem view "$key" \
    --fields "$VIEW_FIELDS" \
    --json)

  if echo "$output" | jq -e \
    '([.fields.labels[]? | select(test("^CVE-"))] | length > 0) and .fields.resolution == null' \
    > /dev/null 2>&1; then
    echo "WARNING: Unresolved CVE — this issue may be under embargo. Do not share details externally." >&2
  elif echo "$output" | jq -e \
    '[.fields.labels[]? | select(test("^Security"))] | length > 0' \
    > /dev/null 2>&1; then
    echo "WARNING: This issue has security labels. Data may be restricted — review before sharing." >&2
  fi

  echo "$output"
}

usage() {
  cat <<'EOF'
Usage: jira.sh <command> [args]

Commands:
  my-issues              List open issues assigned to current user
  search <JQL>           Search issues with JQL query
  view <issue-key>       View full issue details
EOF
}

COMMAND="${1:-my-issues}"
shift || true

case "$COMMAND" in
  my-issues)
    cmd_my_issues
    ;;
  search)
    if [[ $# -lt 1 ]]; then
      echo "ERROR: search requires a JQL query argument" >&2
      exit 1
    fi
    cmd_search "$1"
    ;;
  view)
    if [[ $# -lt 1 ]]; then
      echo "ERROR: view requires an issue key argument" >&2
      exit 1
    fi
    cmd_view "$1"
    ;;
  help|--help|-h)
    usage
    ;;
  *)
    echo "ERROR: Unknown command '$COMMAND'" >&2
    usage >&2
    exit 1
    ;;
esac
