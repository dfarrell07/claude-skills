#!/bin/bash
# Who first authored, and who changes, the GitHub Actions workflows in a set of
# repositories: sourced, reproducible evidence of CI ownership.
#
# Usage: workflow-authorship.sh <github-login> <owner/repo>...
#
# For each .yml/.yaml workflow on the default branch it reads the file's whole
# history. "first" is the oldest recorded author of the current path (a rename
# credits the renamer, so confirm creation before claiming it). "changes" are
# file changes, so a commit that touches two workflows counts twice. Bots (logins
# ending in [bot], plus BOTS, a space-separated list) and commits with no linked
# GitHub account are left out of the human totals; the unlinked ones are reported.
set -euo pipefail
[[ $# -ge 2 ]] || { echo "usage: $0 <github-login> <owner/repo>..." >&2; exit 2; }
user=$(tr '[:upper:]' '[:lower:]' <<<"$1"); shift
bots=$(tr '[:upper:]' '[:lower:]' <<<"${BOTS:-}")
# shellcheck disable=SC2016  # jq variables, not shell
human_filter='[.[] | ascii_downcase | select(. != "?" and (endswith("[bot]") | not)
                and (. as $a | ($bots | split(" ") | index($a)) | not))]'
totals=(0 0 0 0 0)
for repo in "$@"; do
    paths=$(gh api "repos/$repo/contents/.github/workflows" \
        --jq '.[] | select(.type == "file" and (.name | test("\\.ya?ml$"))) | .path') \
        || { echo "error: cannot list workflows in $repo" >&2; exit 1; }
    files=0 first=0 human=0 mine=0 unlinked=0
    while IFS= read -r path; do
        [[ -n $path ]] || continue
        authors=$(gh api --paginate -X GET "repos/$repo/commits" -f path="$path" -f per_page=100 \
            --jq '[.[] | .author.login // "?"]' | jq -s 'add // []')
        files=$((files + 1))
        [[ $(jq -r 'last | ascii_downcase' <<<"$authors") == "$user" ]] && first=$((first + 1))
        human=$((human + $(jq --arg bots "$bots" "$human_filter | length" <<<"$authors")))
        mine=$((mine + $(jq --arg u "$user" '[.[] | select(ascii_downcase == $u)] | length' <<<"$authors")))
        unlinked=$((unlinked + $(jq '[.[] | select(. == "?")] | length' <<<"$authors")))
    done <<<"$paths"
    echo "$repo workflows=$files first_authored=$first human_file_changes=$human user_file_changes=$mine unlinked=$unlinked"
    totals=($((totals[0] + files)) $((totals[1] + first)) $((totals[2] + human)) $((totals[3] + mine)) $((totals[4] + unlinked)))
done
echo "total workflows=${totals[0]} first_authored=${totals[1]} human_file_changes=${totals[2]} user_file_changes=${totals[3]} unlinked=${totals[4]}"
