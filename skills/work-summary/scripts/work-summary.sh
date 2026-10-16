#!/bin/bash
set -euo pipefail
if (( BASH_VERSINFO[0] < 4 )); then
  echo "Error: Bash 4 or newer is required" >&2
  exit 1
fi

# Usage: work-summary.sh [days] [repository-or-scan-root ...]
DAYS="${1:-7}"
if (( $# > 0 )); then shift; fi
if ! [[ "$DAYS" =~ ^0*[1-9][0-9]*$ ]]; then
  echo "Error: Days must be a positive integer (got: $DAYS)" >&2
  exit 1
fi
for dependency in jq git; do
  if ! command -v "$dependency" &>/dev/null; then
    echo "Error: $dependency is required but not installed" >&2
    exit 1
  fi
done
DAYS=$(jq -nr --arg days "$DAYS" '$days | tonumber')
TO_AT=$(date -u +%Y-%m-%dT%H:%M:%SZ)
if ! SINCE_AT=$(date -u -d "$DAYS days ago" +%Y-%m-%dT%H:%M:%SZ 2>/dev/null ||
  date -u -v-"${DAYS}"d +%Y-%m-%dT%H:%M:%SZ 2>/dev/null); then
  echo "Error: Cannot calculate date for $DAYS days" >&2
  exit 1
fi

# Keep evidence of partial coverage in the JSON as well as stderr.
WARNINGS=()
warn() {
  WARNINGS+=("$1")
  echo "Warning: $1" >&2
}

PRS_JSON='[]'
ISSUES_JSON='[]'
GH_STATUS=unavailable
GH_USER=''
SEARCH_LIMIT=1000
if command -v gh &>/dev/null && gh auth status &>/dev/null; then
  GH_USER=$(gh api user --jq .login 2>/dev/null || true)
  if [[ -n "$GH_USER" ]]; then
    GH_STATUS=ok
    # Apply dates on the server before limiting results. Sort deterministically.
    for kind in prs issues; do
      if result=$(gh search "$kind" --author="$GH_USER" --limit "$SEARCH_LIMIT" \
        --updated="$SINCE_AT..$TO_AT" --sort=updated --order=desc \
        --json number,title,repository,state,createdAt,closedAt,updatedAt,url \
        --jq '[.[] | {repo: .repository.nameWithOwner, number, title,
          state: (.state | ascii_downcase), url, created_at: .createdAt,
          closed_at: .closedAt, updated_at: .updatedAt}]'); then
        if [[ $(jq 'length' <<< "$result") -ge "$SEARCH_LIMIT" ]]; then
          GH_STATUS=partial
          warn "GitHub $kind reached the $SEARCH_LIMIT-result cap; coverage may be incomplete."
        fi
        if [[ "$kind" == prs ]]; then PRS_JSON="$result"; else ISSUES_JSON="$result"; fi
      else
        GH_STATUS=partial
        warn "GitHub $kind search failed; this source is missing, not empty."
      fi
    done
  else
    warn "GitHub identity lookup failed; GitHub activity is unavailable."
  fi
else
  warn "GitHub CLI is missing or unauthenticated; GitHub activity is unavailable."
fi

# .git can be a directory (clone) or a file (worktree). Canonical paths
# deduplicate overlapping roots while keeping same-named repositories separate.
declare -A SEEN_REPOS
REPO_LIST=()
add_repo() {
  local canonical common_dir
  canonical=$(git -C "$1" rev-parse --show-toplevel 2>/dev/null) || return 0
  canonical=$(cd "$canonical" && pwd -P)
  common_dir=$(git -C "$canonical" rev-parse --git-common-dir)
  [[ "$common_dir" == /* ]] || common_dir="$canonical/$common_dir"
  common_dir=$(cd "$common_dir" && pwd -P)
  # Worktrees share refs and commits; collect their common repository once.
  if [[ -z "${SEEN_REPOS[$common_dir]:-}" ]]; then
    SEEN_REPOS[$common_dir]=1
    REPO_LIST+=("$canonical")
  fi
}
if (( $# > 0 )); then
  ROOTS=("$@")
else
  ROOTS=("$HOME/go/src" "$HOME/konflux" "$HOME/projects" "$HOME/repos" "$PWD")
fi
for base_dir in "${ROOTS[@]}"; do
  [[ -d "$base_dir" ]] || continue
  add_repo "$base_dir"
  while IFS= read -r -d '' git_entry; do
    add_repo "${git_entry%/.git}"
  done < <(find "$base_dir" -maxdepth 5 \
    \( -name node_modules -o -name vendor \) -prune -o \
    -name .git -print0 -prune 2>/dev/null)
done

COMMITS_PARTS=()
SKIPPED_REPOS=0
for repo_path in "${REPO_LIST[@]}"; do
  # Honor repository-local identity overrides, including different work emails.
  author_email=$(git -C "$repo_path" config user.email || true)
  if [[ -z "$author_email" ]]; then
    SKIPPED_REPOS=$((SKIPPED_REPOS + 1))
    warn "No Git author email configured for $repo_path; commits were not collected."
    continue
  fi
  # All refs include work on unmerged local branches. Git visits each commit once.
  # A tab separates the hash from the subject; preserve pipes and tabs in subjects.
  if ! commits=$(git -C "$repo_path" log --all --fixed-strings --author="<$author_email>" \
    --since-as-filter="$SINCE_AT" --until="$TO_AT" --format='%h%x09%s'); then
    warn "Git log failed for $repo_path; commits were not collected."
    SKIPPED_REPOS=$((SKIPPED_REPOS + 1))
    continue
  fi
  if [[ -n "$commits" ]]; then
    COMMITS_PARTS+=("$(jq -Rn --arg repo "$repo_path" '
      [inputs | index("\t") as $tab | {hash: .[:$tab], message: .[$tab + 1:]}]
      | {($repo): .}' <<< "$commits")")
  fi
done
COMMITS_JSON=$(printf '%s\n' "${COMMITS_PARTS[@]}" | jq -s 'add // {}')
WARNINGS_JSON=$(printf '%s\n' "${WARNINGS[@]}" | jq -Rsc 'split("\n") | map(select(length > 0))')

jq -n \
  --argjson days "$DAYS" --arg since_at "$SINCE_AT" --arg to_at "$TO_AT" \
  --argjson repos_searched "${#REPO_LIST[@]}" --argjson repos_skipped "$SKIPPED_REPOS" \
  --arg github_status "$GH_STATUS" --arg github_user "$GH_USER" \
  --argjson search_limit "$SEARCH_LIMIT" \
  --slurpfile warnings <(printf '%s\n' "$WARNINGS_JSON") \
  --slurpfile prs <(printf '%s\n' "$PRS_JSON") \
  --slurpfile issues <(printf '%s\n' "$ISSUES_JSON") \
  --slurpfile commits <(printf '%s\n' "$COMMITS_JSON") \
  '{
    metadata: {
      days: $days, since_date: $since_at[:10], to_date: $to_at[:10],
      since_at: $since_at, to_at: $to_at,
      repos_searched: $repos_searched, repos_skipped: $repos_skipped,
      repos_with_commits: ($commits[0] | length), github_status: $github_status,
      github_user: $github_user, search_limit: $search_limit, warnings: $warnings[0]
    },
    github: {prs: $prs[0], issues: $issues[0]}, commits: $commits[0]
  }'
