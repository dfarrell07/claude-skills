#!/bin/bash
set -euo pipefail

# Discovers repos the user contributes to and maintains a persistent
# registry at private/repos.yml. Run explicitly via /career-advocate scan.
#
# Discovery strategies:
#   1. GitHub GraphQL — which repos did I contribute to?
#   2. Directory scan — find .git dirs under configured/default paths
#   3. Existing registry — preserve previously discovered repos
#
# Each repo entry records: path, remote URL, hosting type, last seen date.

SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
PRIVATE_DIR="$SCRIPT_DIR/../private"
REPOS_FILE="$PRIVATE_DIR/repos.yml"

if ! command -v jq &>/dev/null; then
  echo "Error: jq is required" >&2
  exit 1
fi

AUTHOR_EMAIL=$(git config user.email 2>/dev/null || echo "")
TODAY=$(date +%Y-%m-%d)

# --- Load existing registry ---
declare -A KNOWN_REPOS
if [[ -f "$REPOS_FILE" ]]; then
  while IFS= read -r path; do
    [[ -n "$path" ]] && KNOWN_REPOS[$path]=1
  done < <(grep -E '^\s+/' "$REPOS_FILE" | sed 's/:.*//' | sed 's/^ *//' 2>/dev/null || true)
  echo "Loaded ${#KNOWN_REPOS[@]} repos from registry" >&2
fi

# --- Helper ---
declare -A DISCOVERED
add_discovered() {
  local repo_path="$1"
  [[ -d "$repo_path/.git" ]] || return 0
  [[ -n "${DISCOVERED[$repo_path]:-}" ]] && return 0
  DISCOVERED[$repo_path]=1
}

# --- Strategy 1: GitHub GraphQL ---
if command -v gh &>/dev/null && gh auth status &>/dev/null 2>&1; then
  echo "Querying GitHub for contributed repos..." >&2
  GH_REPOS=$(gh api graphql -f query='
    { viewer { contributionsCollection {
      commitContributionsByRepository(maxRepositories: 100) {
        repository { nameWithOwner }
      }
    }}}' --jq '[.data.viewer.contributionsCollection
      .commitContributionsByRepository[].repository.nameWithOwner]' \
    2>/dev/null || echo "[]")

  REPO_NAMES=$(echo "$GH_REPOS" | jq -r '.[] | split("/")[1]' 2>/dev/null || echo "")

  for repo_name in $REPO_NAMES; do
    [[ -z "$repo_name" ]] && continue
    while IFS= read -r -d '' dir; do
      [[ -d "$dir/.git" ]] && add_discovered "$dir"
    done < <(find "$HOME" -maxdepth 5 -type d -name "$repo_name" \
      -not -path "*/.cache/*" -not -path "*/node_modules/*" \
      -not -path "*/.local/*" -print0 2>/dev/null)
  done
  echo "  GitHub: found $(echo "$GH_REPOS" | jq 'length') contributed repos" >&2
else
  echo "Warning: gh CLI not available, skipping GitHub discovery" >&2
fi

# --- Strategy 2: Directory scan ---
SCAN_DIRS=("$HOME/go/src" "$HOME/konflux" "$HOME/projects" "$PWD")

CONFIG_FILE="$PRIVATE_DIR/config.yml"
if [[ -f "$CONFIG_FILE" ]] && command -v yq &>/dev/null; then
  while IFS= read -r dir; do
    expanded="${dir/#\~/$HOME}"
    [[ -d "$expanded" ]] && SCAN_DIRS+=("$expanded")
  done < <(yq -r '.scan_dirs[]?' "$CONFIG_FILE" 2>/dev/null || true)
fi

echo "Scanning directories..." >&2
for base_dir in "${SCAN_DIRS[@]}"; do
  [[ ! -d "$base_dir" ]] && continue
  while IFS= read -r -d '' git_dir; do
    add_discovered "${git_dir%/.git}"
  done < <(find "$base_dir" -maxdepth 5 -type d -name ".git" -print0 2>/dev/null)
done

# --- Strategy 3: Re-verify existing registry entries ---
for repo_path in "${!KNOWN_REPOS[@]}"; do
  if [[ -d "$repo_path/.git" ]]; then
    add_discovered "$repo_path"
  fi
done

# --- Detect hosting type from remote URL ---
detect_hosting() {
  local url="$1"
  case "$url" in
    *github.com*) echo "github" ;;
    *gitlab*) echo "gitlab" ;;
    *gerrit*|*code.engineering*|*review.*) echo "gerrit" ;;
    *) echo "other" ;;
  esac
}

# --- Filter to repos with user's commits ---
echo "Checking ${#DISCOVERED[@]} repos for your commits..." >&2
ACTIVE_REPOS=()
for repo_path in "${!DISCOVERED[@]}"; do
  if [[ -n "$AUTHOR_EMAIL" ]]; then
    has_commits=$(git -C "$repo_path" log --author="$AUTHOR_EMAIL" \
      --max-count=1 --format='%h' 2>/dev/null || echo "")
    [[ -z "$has_commits" ]] && continue
  fi
  ACTIVE_REPOS+=("$repo_path")
done

# --- Write registry ---
mkdir -p "$PRIVATE_DIR"

{
  echo "---"
  echo "# Career Advocate — Known Repositories"
  echo "# Auto-updated by /career-advocate scan"
  echo "# Add repos manually: just add the path with remote/hosting."
  echo "# Run /career-advocate scan to refresh after cloning new repos."
  echo "#"
  echo "# last_scan: $TODAY"
  echo "# total: ${#ACTIVE_REPOS[@]}"
  echo ""
  echo "repos:"

  GITHUB_COUNT=0
  GITLAB_COUNT=0
  OTHER_COUNT=0
  NEW_COUNT=0

  while IFS= read -r repo_path; do
    remote=$(git -C "$repo_path" remote get-url origin 2>/dev/null || echo "local")
    hosting=$(detect_hosting "$remote")
    echo "  $repo_path:"
    echo "    remote: $remote"
    echo "    hosting: $hosting"
    echo "    last_seen: $TODAY"

    case "$hosting" in
      github) GITHUB_COUNT=$((GITHUB_COUNT + 1)) ;;
      gitlab) GITLAB_COUNT=$((GITLAB_COUNT + 1)) ;;
      *) OTHER_COUNT=$((OTHER_COUNT + 1)) ;;
    esac
    [[ -z "${KNOWN_REPOS[$repo_path]:-}" ]] && NEW_COUNT=$((NEW_COUNT + 1))
  done < <(printf '%s\n' "${ACTIVE_REPOS[@]}" | sort)
} > "$REPOS_FILE"

echo "" >&2
echo "Registry updated: $REPOS_FILE" >&2
echo "  Total: ${#ACTIVE_REPOS[@]} repos with your commits" >&2
echo "  GitHub: $GITHUB_COUNT | GitLab: $GITLAB_COUNT | Other: $OTHER_COUNT" >&2
echo "  New since last scan: $NEW_COUNT" >&2
