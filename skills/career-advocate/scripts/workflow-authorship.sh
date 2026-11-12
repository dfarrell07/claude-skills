#!/bin/bash
# Who created, and who changes, the GitHub Actions workflows in a set of repositories.
#
# Usage: workflow-authorship.sh <github-login> <owner/repo>...
#
# For each workflow file on the default branch, the creator is the author of its
# oldest commit, reading every page of its history. Human changes exclude bot
# logins (names ending in [bot], plus BOTS, a space-separated list of extra bot
# logins). Prints one line per repository and a
# total, as sourced, reproducible evidence of CI ownership.
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: $0 <github-login> <owner/repo>..." >&2; exit 2; }
user=$1; shift
bots=${BOTS:-}
files_total=0 created_total=0 human_total=0 user_total=0
for repo in "$@"; do
    files=0 created=0 human=0 mine=0
    while IFS= read -r path; do
        [[ -n $path ]] || continue
        authors=$(gh api --paginate "repos/$repo/commits?path=$path&per_page=100" \
            --jq '[.[] | .author.login // "?"]' | jq -s 'add // []')
        [[ $(jq -r 'last' <<<"$authors") == "$user" ]] && created=$((created + 1))
        files=$((files + 1))
        human=$((human + $(jq --arg bots "$bots" '[.[] | select((endswith("[bot]") | not) and (. as $a | ($bots | split(" ") | index($a)) | not))] | length' <<<"$authors")))
        mine=$((mine + $(jq --arg u "$user" '[.[] | select(. == $u)] | length' <<<"$authors")))
    done < <(gh api "repos/$repo/contents/.github/workflows" --jq '.[].path' 2>/dev/null || true)
    echo "$repo files=$files created_by_$user=$created human_commits=$human ${user}_commits=$mine"
    files_total=$((files_total + files)) created_total=$((created_total + created))
    human_total=$((human_total + human)) user_total=$((user_total + mine))
done
echo "total files=$files_total created_by_$user=$created_total human_commits=$human_total ${user}_commits=$user_total"
