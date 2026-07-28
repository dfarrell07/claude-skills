#!/bin/bash
set -euo pipefail

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"

SINCE=""
UNTIL=""
while [[ $# -gt 0 ]]; do
  case "$1" in
    --since) [[ $# -ge 2 ]] || { echo "Error: --since requires a date (YYYY-MM-DD)" >&2; exit 1; }
             SINCE="$2"; shift 2 ;;
    --until) [[ $# -ge 2 ]] || { echo "Error: --until requires a date (YYYY-MM-DD)" >&2; exit 1; }
             UNTIL="$2"; shift 2 ;;
    *) shift ;;
  esac
done

validate_date() {
  if [[ ! "$1" =~ ^[0-9]{4}-[0-9]{2}-[0-9]{2}$ ]]; then
    echo "Error: invalid date '$1', expected YYYY-MM-DD" >&2
    exit 1
  fi
}

if [[ -z "$SINCE" ]]; then
  SINCE=$(date -d '180 days ago' +%Y-%m-%d 2>/dev/null \
    || date -v-180d +%Y-%m-%d)
fi
UNTIL="${UNTIL:-$(date +%Y-%m-%d)}"
validate_date "$SINCE"
validate_date "$UNTIL"

if [[ "$SINCE" > "$UNTIL" ]]; then
  echo "Error: --since ($SINCE) is after --until ($UNTIL)" >&2
  exit 1
fi

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required (dnf install jq / apt install jq / brew install jq)" >&2
  exit 1
fi

WORK_DIR=$(mktemp -d /tmp/career-collect-XXXXXXXXXX)
chmod 700 "$WORK_DIR"
trap 'rm -rf "$WORK_DIR"' EXIT

AUTHOR_EMAIL=$(git config user.email 2>/dev/null || echo "")
AUTHOR_NAME=$(git config user.name 2>/dev/null || echo "")
GH_USER=""
GH_LIMIT=1000

# ============================================================
# Phase 1: Query GitHub for activity overview (source of truth)
# ============================================================

GH_CONTRIB_REPOS="[]"

if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
  GH_USER=$(gh api user --jq .login 2>/dev/null || echo "")

  if [[ -n "$GH_USER" ]]; then
    GH_CONTRIB_REPOS=$(gh api graphql -f query="
      { viewer { contributionsCollection(from: \"${SINCE}T00:00:00Z\", to: \"${UNTIL}T23:59:59Z\") {
        commitContributionsByRepository(maxRepositories: 100) {
          repository { nameWithOwner }
          contributions { totalCount }
        }
      }}}" --jq '[.data.viewer.contributionsCollection
        .commitContributionsByRepository[]
        | {repo: .repository.nameWithOwner, commits: .contributions.totalCount}]' \
      2>/dev/null || echo "[]")

    GH_DELAY=2

    gh search prs --author="$GH_USER" --merged \
      --merged-at="$SINCE..$UNTIL" --limit "$GH_LIMIT" \
      --json number,title,repository,state,createdAt,url \
      --jq "[.[] | {repo: .repository.nameWithOwner, number, title,
            state, url, created_at: .createdAt, role: \"author\"}]" \
      > "$WORK_DIR/prs.json" 2>/dev/null || echo "[]" > "$WORK_DIR/prs.json"

    PRS_COUNT=$(jq 'length' "$WORK_DIR/prs.json")
    if [[ "$PRS_COUNT" -ge "$GH_LIMIT" ]]; then
      echo "Warning: merged PR results hit ${GH_LIMIT}-item limit" >&2
    fi

    sleep "$GH_DELAY"
    gh search prs --author="$GH_USER" --state=open \
      --created="$SINCE..$UNTIL" --limit "$GH_LIMIT" \
      --json number,title,repository,state,createdAt,url \
      --jq "[.[] | {repo: .repository.nameWithOwner, number, title,
            state: \"open\", url, created_at: .createdAt, role: \"author\"}]" \
      > "$WORK_DIR/open_prs.json" 2>/dev/null \
      || echo "[]" > "$WORK_DIR/open_prs.json"

    sleep "$GH_DELAY"
    gh search prs --author="$GH_USER" --state=closed \
      --created="$SINCE..$UNTIL" --limit "$GH_LIMIT" \
      --json number,title,repository,state,createdAt,url \
      --jq "[.[] | {repo: .repository.nameWithOwner, number, title,
            state: \"closed\", url, created_at: .createdAt, role: \"author\"}]" \
      > "$WORK_DIR/closed_prs_raw.json" 2>/dev/null \
      || echo "[]" > "$WORK_DIR/closed_prs_raw.json"
    MERGED_COUNT=$(jq 'length' "$WORK_DIR/prs.json" 2>/dev/null || echo "0")
    if [[ "$MERGED_COUNT" -gt 0 ]]; then
      jq --slurpfile merged "$WORK_DIR/prs.json" \
        '[.[] | select(.number as $n | .repo as $r |
          ($merged[0] | map(select(.number == $n and .repo == $r)) | length) == 0)]' \
        "$WORK_DIR/closed_prs_raw.json" \
        > "$WORK_DIR/closed_prs.json" 2>/dev/null \
        || echo "[]" > "$WORK_DIR/closed_prs.json"
    else
      echo "[]" > "$WORK_DIR/closed_prs.json"
      echo "Warning: merged PRs query returned empty, skipping closed-not-merged diff" >&2
    fi

    sleep "$GH_DELAY"
    gh search issues --author="$GH_USER" \
      --created="$SINCE..$UNTIL" --limit "$GH_LIMIT" \
      --json number,title,repository,state,createdAt,url \
      --jq "[.[] | {repo: .repository.nameWithOwner, number, title,
            state, url, created_at: .createdAt}]" \
      > "$WORK_DIR/issues.json" 2>/dev/null \
      || echo "[]" > "$WORK_DIR/issues.json"

    sleep "$GH_DELAY"
    gh search prs --reviewed-by="$GH_USER" \
      --created="$SINCE..$UNTIL" --limit "$GH_LIMIT" \
      --json number,title,repository,state,createdAt,url,author \
      --jq "[.[] | select(.author.login != \"$GH_USER\") |
            {repo: .repository.nameWithOwner, number, title,
            state, url, created_at: .createdAt, role: \"reviewer\"}]" \
      > "$WORK_DIR/reviews.json" 2>/dev/null \
      || echo "[]" > "$WORK_DIR/reviews.json"

    sleep "$GH_DELAY"
    gh api search/issues --method GET --paginate \
      -f q="commenter:$GH_USER type:pr created:$SINCE..$UNTIL" \
      -f per_page=100 \
      --jq '[.items[] | {
        repo: (.repository_url | ltrimstr("https://api.github.com/repos/")),
        number, title, state, url: .html_url,
        created_at}]' 2>/dev/null \
      | jq -s 'add // []' \
      > "$WORK_DIR/pr_comments.json" \
      || echo "[]" > "$WORK_DIR/pr_comments.json"
  fi
else
  echo "Warning: gh CLI not available or not authenticated" >&2
fi

for f in prs open_prs closed_prs issues reviews pr_comments; do
  [[ -f "$WORK_DIR/$f.json" ]] || echo "[]" > "$WORK_DIR/$f.json"
done

# ============================================================
# Phase 2: Load repo list from registry (or run scan if missing)
# ============================================================

REPOS_FILE="$SCRIPT_DIR/../private/repos.yml"
declare -A SEEN_REPOS
REPO_LIST=()

add_repo() {
  local repo_path="$1"
  [[ -d "$repo_path/.git" ]] || return 0
  [[ -n "${SEEN_REPOS[$repo_path]:-}" ]] && return 0
  SEEN_REPOS[$repo_path]=1
  REPO_LIST+=("$repo_path")
}

if [[ -f "$REPOS_FILE" ]]; then
  while IFS= read -r path; do
    [[ -n "$path" ]] && add_repo "$path"
  done < <(grep -E '^\s+/' "$REPOS_FILE" | sed 's/:.*//' | sed 's/^ *//' 2>/dev/null || true)
  echo "Loaded ${#REPO_LIST[@]} repos from registry" >&2
else
  echo "No repo registry found. Run /career-advocate scan first." >&2
  echo "Falling back to directory scan..." >&2

  if [[ -n "$AUTHOR_EMAIL" ]]; then
    for base_dir in "$HOME/go/src" "$HOME/konflux" "$HOME/projects" "$PWD"; do
      [[ ! -d "$base_dir" ]] && continue
      while IFS= read -r -d '' git_dir; do
        add_repo "${git_dir%/.git}"
      done < <(find "$base_dir" -maxdepth 5 -type d -name ".git" -print0 2>/dev/null)
    done
  fi
  echo "Found ${#REPO_LIST[@]} repos via fallback scan" >&2
fi

# ============================================================
# Phase 3: Collect local git data from discovered repos
# ============================================================

# --- Git commits (use full path as key — no collisions) ---
mkdir -p "$WORK_DIR/commits"

if [[ -z "$AUTHOR_EMAIL" ]]; then
  echo "Warning: git user.email not set, skipping commit collection" >&2
  echo '{}' > "$WORK_DIR/commits.json"
  REPOS_WITH_COMMITS=0
else
  IDX=0
  for repo_path in "${REPO_LIST[@]}"; do
    commits=$(git -C "$repo_path" log --author="$AUTHOR_EMAIL" \
      --since="$SINCE" --until="$UNTIL" --format='%h|%s' 2>/dev/null \
      | grep -v '^[a-f0-9]*|cve-agent run' || true)
    [[ -z "$commits" ]] && continue

    echo "$commits" | jq -R -s '
      split("\n") |
      map(select(length > 0) | split("|") | {hash: .[0], message: .[1:]|join("|")})
    ' > "$WORK_DIR/commits/$IDX.json"
    echo "$repo_path" > "$WORK_DIR/commits/$IDX.key"
    IDX=$((IDX + 1))
  done

  if ls "$WORK_DIR/commits"/*.json &>/dev/null; then
    for f in "$WORK_DIR/commits"/*.json; do
      idx=$(basename "$f" .json)
      rkey=$(cat "$WORK_DIR/commits/$idx.key")
      jq -n --arg repo "$rkey" --slurpfile c "$f" '{($repo): $c[0]}'
    done | jq -s 'reduce .[] as $item ({}; . + $item)' \
      > "$WORK_DIR/commits.json"
  else
    echo '{}' > "$WORK_DIR/commits.json"
  fi
  REPOS_WITH_COMMITS=$(jq 'keys | length' "$WORK_DIR/commits.json")
fi

# --- Git stats (lines changed, file types, new files per repo) ---
mkdir -p "$WORK_DIR/stats"

if [[ -n "$AUTHOR_EMAIL" ]]; then
  IDX=0
  for repo_path in "${REPO_LIST[@]}"; do
    stats=$(git -C "$repo_path" log --author="$AUTHOR_EMAIL" \
      --since="$SINCE" --until="$UNTIL" --numstat --format="" 2>/dev/null \
      | awk '$1 != "-" && NF == 3 {
          ins += $1; del += $2
          # Extract extension from basename only
          n = split($3, pathparts, "/")
          fname = pathparts[n]
          m = split(fname, fparts, ".")
          if (m > 1) ext["." fparts[m]]++
        }
        END {
          printf "{\"insertions\":%d,\"deletions\":%d,\"file_types\":{", ins, del
          first = 1
          for (e in ext) {
            if (!first) printf ","
            printf "\"%s\":%d", e, ext[e]
            first = 0
          }
          print "}}"
        }' 2>/dev/null || echo '{"insertions":0,"deletions":0,"file_types":{}}')

    new_files=$(git -C "$repo_path" log --author="$AUTHOR_EMAIL" \
      --since="$SINCE" --until="$UNTIL" --diff-filter=A \
      --name-only --format="" 2>/dev/null | wc -l | tr -d ' ')

    echo "$stats" | jq --argjson nf "$new_files" '. + {new_files: $nf}' \
      > "$WORK_DIR/stats/$IDX.json"
    echo "$repo_path" > "$WORK_DIR/stats/$IDX.key"
    IDX=$((IDX + 1))
  done
fi

if ls "$WORK_DIR/stats"/*.json &>/dev/null; then
  for f in "$WORK_DIR/stats"/*.json; do
    idx=$(basename "$f" .json)
    rkey=$(cat "$WORK_DIR/stats/$idx.key")
    ins=$(jq '.insertions' "$f")
    del=$(jq '.deletions' "$f")
    [[ "$ins" -eq 0 && "$del" -eq 0 ]] && continue
    jq -n --arg repo "$rkey" --slurpfile s "$f" '{($repo): $s[0]}'
  done | jq -s 'reduce .[] as $item ({}; . + $item)' \
    > "$WORK_DIR/git_stats.json"
else
  echo '{}' > "$WORK_DIR/git_stats.json"
fi

# --- User branches (author-based, no pattern matching) ---
mkdir -p "$WORK_DIR/branches"

IDX=0
for repo_path in "${REPO_LIST[@]}"; do
  branch_file="$WORK_DIR/branches/${IDX}.jsonl"
  echo "$repo_path" > "$WORK_DIR/branches/${IDX}.key"
  IDX=$((IDX + 1))

  [[ -z "$AUTHOR_NAME" ]] && continue

  while IFS='|' read -r branch author date subject; do
    [[ -z "$branch" || -z "$date" ]] && continue
    [[ "$author" != *"$AUTHOR_NAME"* ]] && continue
    [[ "$date" < "$SINCE" || "$date" > "$UNTIL" ]] && continue

    # Skip main/master/release branches (we want feature/dev branches)
    case "$branch" in
      main|master|release-*|HEAD) continue ;;
    esac

    commit_count=$(git -C "$repo_path" rev-list --count "$branch" \
      --not "$(git -C "$repo_path" rev-parse HEAD 2>/dev/null)" \
      2>/dev/null || echo "0")

    jq -n --arg branch "$branch" --arg date "$date" \
      --arg subject "$subject" --argjson count "$commit_count" \
      '{branch: $branch, last_date: $date, subject: $subject,
        commit_count: $count}' >> "$branch_file"
  done < <(git -C "$repo_path" for-each-ref \
    --format='%(refname:short)|%(authorname)|%(committerdate:short)|%(subject)' \
    refs/heads/ 2>/dev/null || true)
done

if ls "$WORK_DIR/branches"/*.jsonl &>/dev/null; then
  for f in "$WORK_DIR/branches"/*.jsonl; do
    idx=$(basename "$f" .jsonl)
    rkey=$(cat "$WORK_DIR/branches/$idx.key")
    jq -s --arg repo "$rkey" '{($repo): .}' "$f"
  done | jq -s 'reduce .[] as $item ({}; . + $item)' \
    > "$WORK_DIR/user_branches.json"
else
  echo '{}' > "$WORK_DIR/user_branches.json"
fi

# ============================================================
# Phase 4: Jira issues via acli
# ============================================================

JIRA_LIMIT=500

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
  echo "Warning: Jira command failed after 2 attempts" >&2
  return 1
}

JIRA_JQ='[.[] | {key, summary: .fields.summary,
  status: .fields.status.name, type: .fields.issuetype.name,
  priority: .fields.priority.name}]'

if command -v acli &>/dev/null; then
  run_with_retry acli jira workitem search \
    --jql "assignee = currentUser() AND updated >= \"$SINCE\" ORDER BY updated DESC" \
    --json --limit "$JIRA_LIMIT" 2>/dev/null \
    | jq "$JIRA_JQ" > "$WORK_DIR/jira_assigned.json" \
    2>/dev/null || echo "[]" > "$WORK_DIR/jira_assigned.json"

  run_with_retry acli jira workitem search \
    --jql "assignee = currentUser() AND resolution IS NOT EMPTY AND resolved >= \"$SINCE\" ORDER BY resolved DESC" \
    --json --limit "$JIRA_LIMIT" 2>/dev/null \
    | jq "$JIRA_JQ" > "$WORK_DIR/jira_resolved.json" \
    2>/dev/null || echo "[]" > "$WORK_DIR/jira_resolved.json"

  WATCHED_JQ='[.[] | {key, summary: .fields.summary,
    status: .fields.status.name, type: .fields.issuetype.name,
    priority: .fields.priority.name, involvement: "watched"}]'
  run_with_retry acli jira workitem search \
    --jql "watcher = currentUser() AND updated >= \"$SINCE\" AND assignee != currentUser() ORDER BY updated DESC" \
    --json --limit "$JIRA_LIMIT" 2>/dev/null \
    | jq "$WATCHED_JQ" > "$WORK_DIR/jira_watched.json" \
    2>/dev/null || echo "[]" > "$WORK_DIR/jira_watched.json"
else
  echo "Warning: acli not available, Jira data will be empty" >&2
  echo "[]" > "$WORK_DIR/jira_assigned.json"
  echo "[]" > "$WORK_DIR/jira_resolved.json"
  echo "[]" > "$WORK_DIR/jira_watched.json"
fi

jq -s '.[0] + .[1] + .[2] | unique_by(.key)' \
  "$WORK_DIR/jira_assigned.json" \
  "$WORK_DIR/jira_resolved.json" \
  "$WORK_DIR/jira_watched.json" > "$WORK_DIR/jira.json"

# --- Jira enrichment: components, dates, comment count ---
PRIVATE_DIR="$SCRIPT_DIR/../private"
JIRA_CACHE="$PRIVATE_DIR/jira-cache"

if command -v acli &>/dev/null; then
  JIRA_COUNT=$(jq 'length' "$WORK_DIR/jira.json")
  if [[ "$JIRA_COUNT" -gt 0 ]]; then
    mkdir -p "$JIRA_CACHE"
    ENRICH_DIR="$WORK_DIR/jira-enrich"
    mkdir -p "$ENRICH_DIR"

    CACHED=0
    jq -r '.[].key' "$WORK_DIR/jira.json" > "$WORK_DIR/jira_keys.txt"
    : > "$WORK_DIR/jira_uncached.txt"

    while IFS= read -r key; do
      if [[ -f "$JIRA_CACHE/$key.json" ]]; then
        cp "$JIRA_CACHE/$key.json" "$ENRICH_DIR/$key.json"
        CACHED=$((CACHED + 1))
      else
        echo "$key" >> "$WORK_DIR/jira_uncached.txt"
      fi
    done < "$WORK_DIR/jira_keys.txt"

    NEEDED=$(wc -l < "$WORK_DIR/jira_uncached.txt" | tr -d ' ')
    echo "Jira enrichment: $CACHED cached, $NEEDED to fetch..." >&2

    if [[ "$NEEDED" -gt 0 ]]; then
      xargs -P4 -I{} bash -c '
        key="$1"; outdir="$2"; cachedir="$3"
        result=$(acli jira workitem view "$key" --json \
          --fields "components,created,resolutiondate,comment" \
          </dev/null 2>/dev/null) || exit 0
        enriched=$(echo "$result" | jq "{
          components: [.fields.components[]?.name],
          created: (.fields.created // null |
            if . then split(\"T\")[0] else null end),
          resolved: (.fields.resolutiondate // null |
            if . then split(\"T\")[0] else null end),
          comment_count: ([.fields.comment.comments[]?] | length)
        }" 2>/dev/null) || exit 0
        echo "$enriched" | jq . > /dev/null 2>&1 || exit 0
        echo "$enriched" > "$outdir/$key.json"
        cp "$outdir/$key.json" "$cachedir/$key.json" 2>/dev/null
      ' _ {} "$ENRICH_DIR" "$JIRA_CACHE" < "$WORK_DIR/jira_uncached.txt"
    fi

    echo "Jira enrichment complete" >&2

    if ls "$ENRICH_DIR"/*.json &>/dev/null; then
      for f in "$ENRICH_DIR"/*.json; do
        key=$(basename "$f" .json)
        jq --arg k "$key" '{($k): .}' "$f"
      done | jq -s 'reduce .[] as $item ({}; . + $item)' \
        > "$WORK_DIR/jira_enrichments.json"

      jq --slurpfile e "$WORK_DIR/jira_enrichments.json" \
        '[.[] | . + ($e[0][.key] // {})]' \
        "$WORK_DIR/jira.json" > "$WORK_DIR/jira_enriched.json" \
        && mv "$WORK_DIR/jira_enriched.json" "$WORK_DIR/jira.json"
    fi

    rm -rf "$ENRICH_DIR"
  fi
fi

# ============================================================
# Phase 5: PR depth enrichment (optional, can be slow)
# ============================================================

PRS_COUNT=$(jq 'length' "$WORK_DIR/prs.json")
if [[ -f "$SCRIPT_DIR/collect-pr-depth.sh" ]] && [[ "$PRS_COUNT" -gt 0 ]]; then
  bash "$SCRIPT_DIR/collect-pr-depth.sh" < "$WORK_DIR/prs.json" \
    > "$WORK_DIR/prs_depth.json" 2>/dev/null \
    && mv "$WORK_DIR/prs_depth.json" "$WORK_DIR/prs.json" \
    || true
fi

# ============================================================
# Phase 6: Assemble final JSON
# ============================================================

jq -n \
  --arg since "$SINCE" \
  --arg until "$UNTIL" \
  --arg author_email "$AUTHOR_EMAIL" \
  --arg github_user "${GH_USER:-}" \
  --argjson repos_searched "${#REPO_LIST[@]}" \
  --argjson repos_with_commits "$REPOS_WITH_COMMITS" \
  --slurpfile contrib_repos <(echo "$GH_CONTRIB_REPOS") \
  --slurpfile prs "$WORK_DIR/prs.json" \
  --slurpfile open_prs "$WORK_DIR/open_prs.json" \
  --slurpfile closed_prs "$WORK_DIR/closed_prs.json" \
  --slurpfile issues "$WORK_DIR/issues.json" \
  --slurpfile reviews "$WORK_DIR/reviews.json" \
  --slurpfile pr_comments "$WORK_DIR/pr_comments.json" \
  --slurpfile user_branches "$WORK_DIR/user_branches.json" \
  --slurpfile jira_issues "$WORK_DIR/jira.json" \
  --slurpfile commits "$WORK_DIR/commits.json" \
  --slurpfile git_stats "$WORK_DIR/git_stats.json" \
  '{
    metadata: {
      since: $since,
      until: $until,
      author_email: $author_email,
      github_user: $github_user,
      repos_searched: $repos_searched,
      repos_with_commits: $repos_with_commits,
      github_contributed_repos: $contrib_repos[0]
    },
    git_commits: $commits[0],
    git_stats: $git_stats[0],
    github_prs: $prs[0],
    github_prs_open: $open_prs[0],
    github_prs_closed: $closed_prs[0],
    github_issues: $issues[0],
    github_reviews: $reviews[0],
    github_pr_comments: $pr_comments[0],
    user_branches: $user_branches[0],
    jira_issues: $jira_issues[0]
  }' | tee "$WORK_DIR/final_output.json"

# ============================================================
# Phase 7: Persistent notes — cache + contribution log
# ============================================================

CACHE_FILE="$PRIVATE_DIR/collection-cache.json"
LOG_FILE="$PRIVATE_DIR/contribution-log.md"
TODAY=$(date +%Y-%m-%d)

mkdir -p "$PRIVATE_DIR"

if [[ -f "$CACHE_FILE" ]] && [[ -f "$WORK_DIR/final_output.json" ]]; then
  CURR_PRS=$(jq '.github_prs | length' "$WORK_DIR/final_output.json")
  CURR_JIRA=$(jq '.jira_issues | length' "$WORK_DIR/final_output.json")
  CURR_COMMITS=$(jq '[.git_commits[]? | length] | add // 0' "$WORK_DIR/final_output.json")
  PREV_SINCE=$(jq -r '.metadata.since' "$CACHE_FILE" 2>/dev/null || echo "")

  {
    echo ""
    echo "## $TODAY $(date +%H:%M:%S) (since: $SINCE, until: ${UNTIL:-$TODAY})"
    echo ""
    echo "**Collection summary:**"
    echo ""
    echo "- $CURR_PRS merged PRs, $(jq '.github_prs_closed | length' "$WORK_DIR/final_output.json") closed, $(jq '.github_prs_open | length' "$WORK_DIR/final_output.json") open"
    echo "- $(jq '.github_reviews | length' "$WORK_DIR/final_output.json") reviews, $(jq '.github_pr_comments | length' "$WORK_DIR/final_output.json") PRs commented on"
    echo "- $CURR_COMMITS commits across $(jq '.git_commits | keys | length' "$WORK_DIR/final_output.json") repos"
    echo "- +$(jq '[.git_stats[]?.insertions] | add // 0' "$WORK_DIR/final_output.json") / -$(jq '[.git_stats[]?.deletions] | add // 0' "$WORK_DIR/final_output.json") lines"
    echo "- $CURR_JIRA Jira issues"

    if [[ "$PREV_SINCE" == "$SINCE" ]]; then
      PREV_PRS=$(jq '.github_prs | length' "$CACHE_FILE" 2>/dev/null || echo "0")
      PREV_JIRA=$(jq '.jira_issues | length' "$CACHE_FILE" 2>/dev/null || echo "0")
      PREV_COMMITS=$(jq '[.git_commits[]? | length] | add // 0' "$CACHE_FILE" 2>/dev/null || echo "0")
      echo ""
      echo "**Delta since last collection (same range):**"
      echo ""
      echo "- $((CURR_PRS - PREV_PRS)) new merged PRs"
      echo "- $((CURR_JIRA - PREV_JIRA)) new Jira issues"
      echo "- $((CURR_COMMITS - PREV_COMMITS)) new commits"
    fi
  } >> "$LOG_FILE"
  echo "Updated contribution log: $LOG_FILE" >&2
elif [[ -f "$WORK_DIR/final_output.json" ]]; then
  {
    echo "# Contribution Log"
    echo ""
    echo "## $TODAY $(date +%H:%M:%S) (since: $SINCE, until: ${UNTIL:-$TODAY})"
    echo ""
    echo "**First collection:**"
    echo ""
    echo "- $(jq '.github_prs | length' "$WORK_DIR/final_output.json") merged PRs"
    echo "- $(jq '.github_reviews | length' "$WORK_DIR/final_output.json") reviews"
    echo "- $(jq '[.git_commits[]? | length] | add // 0' "$WORK_DIR/final_output.json") commits"
    echo "- +$(jq '[.git_stats[]?.insertions] | add // 0' "$WORK_DIR/final_output.json") / -$(jq '[.git_stats[]?.deletions] | add // 0' "$WORK_DIR/final_output.json") lines"
    echo "- $(jq '.jira_issues | length' "$WORK_DIR/final_output.json") Jira issues"
  } > "$LOG_FILE"
  echo "Created contribution log: $LOG_FILE" >&2
fi

cp "$WORK_DIR/final_output.json" "$CACHE_FILE" 2>/dev/null || true
