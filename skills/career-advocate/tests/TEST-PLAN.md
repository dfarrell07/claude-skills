# Career Advocate Test Plan

Run all tests from `/home/dfarrell07/claude-skills`.

## Prerequisites

```bash
# Tools required
command -v jq && command -v gh && command -v acli && echo "OK"

# Registry must exist (run scan first if needed)
test -f skills/career-advocate/private/repos.yml && echo "OK"
```

---

## 1. Collection Script — Basic Execution

### 1.1 Valid JSON output

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-01 2>/dev/null | jq . > /dev/null
echo "Exit: $?"
# Expected: 0
```

### 1.2 All top-level keys present

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-01 2>/dev/null | jq 'keys'
# Expected: 11 keys — metadata, git_commits, git_stats,
#   github_prs, github_prs_open, github_prs_closed,
#   github_pr_comments, github_issues, github_reviews,
#   user_branches, jira_issues
```

### 1.3 Default dates (no arguments)

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  2>/dev/null | jq '.metadata.since'
# Expected: 180 days ago from today
```

### 1.4 Custom date range

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-01 --until 2026-07-15 2>/dev/null \
  | jq '.metadata | {since, until}'
# Expected: {"since":"2026-07-01","until":"2026-07-15"}
```

---

## 2. Argument Parsing

### 2.1 Invalid date format

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since "last week" 2>&1
# Expected: "Error: invalid date 'last week', expected YYYY-MM-DD"
# Exit: 1
```

### 2.2 Missing --since value

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2>&1
# Expected: "Error: --since requires a value"
# Exit: 1
```

### 2.3 Missing --until value

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --until 2>&1
# Expected: "Error: --until requires a value"
# Exit: 1
```

### 2.4 SKILL.md mode parsing

```bash
test_mode() {
  ARGS="$(echo "$1" | xargs)"
  MODE="${ARGS%% *}"
  [[ "$MODE" == --* ]] && MODE=""
  MODE="$(echo "$MODE" | xargs)"
  [[ -z "$MODE" ]] && MODE="promote"
  echo "'$1' => MODE=$MODE"
}

test_mode ""                    # promote
test_mode "promote"             # promote
test_mode "assess"              # assess
test_mode "meeting"             # meeting
test_mode "scan"                # scan
test_mode "help"                # help
test_mode "--since 2026-01-01"  # promote (flags-only)
test_mode "  meeting  "         # meeting (whitespace)
test_mode "promote --since 2026-01-01"  # promote
```

---

## 3. GitHub Data

### 3.1 Merged PRs collected

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-01-01 2>/dev/null \
  | jq '.github_prs | length'
# Expected: > 300 (full year ~553)
```

### 3.2 Open PRs collected

```bash
# same output | jq '.github_prs_open | length'
# Expected: > 0
```

### 3.3 Closed-not-merged PRs collected

```bash
# same output | jq '.github_prs_closed | length'
# Expected: > 100
```

### 3.4 Reviews collected (self-reviews filtered)

```bash
# same output | jq '.github_reviews | length'
# Expected: > 200
```

### 3.5 PR comments collected (all pages merged)

```bash
# same output | jq '.github_pr_comments | length'
# Expected: > 100 (was 14 before pagination fix)
```

### 3.6 --until applied to GitHub searches

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-01 --until 2026-07-10 2>/dev/null \
  | jq '[.github_prs[] | select(.created_at > "2026-07-10")] | length'
# Expected: 0 (no PRs after the until date)
```

### 3.7 PR depth enrichment

```bash
# full output | jq '[.github_prs[]
#   | select(.depth_commits != null)
#   | select((.depth_commits | length) > 0)] | length'
# Expected: > 0 (up to 200, MAX_PRS)
```

### 3.8 GitHub contributed repos in metadata

```bash
# full output | jq '.metadata.github_contributed_repos | length'
# Expected: > 10
```

---

## 4. Git Data

### 4.1 Commits collected across repos

```bash
# full output | jq '{
#   repos: (.git_commits | keys | length),
#   total: ([.git_commits[] | length] | add)
# }'
# Expected: repos > 20, total > 500
```

### 4.2 Automated commits filtered

```bash
# full output | jq '[.git_commits[][]
#   | select(.message | startswith("cve-agent run"))] | length'
# Expected: 0
```

### 4.3 User branches detected by author

```bash
# full output | jq '{
#   repos: (.user_branches | keys | length),
#   total: ([.user_branches[] | length] | add)
# }'
# Expected: repos > 15, total > 500
```

### 4.4 Main/master/release branches excluded

```bash
# full output | jq '[.user_branches[][]
#   | select(.branch == "main" or .branch == "master"
#     or (.branch | startswith("release-")))] | length'
# Expected: 0
```

---

## 5. Git Stats

### 5.1 Stats present

```bash
# full output | jq 'has("git_stats")'
# Expected: true
```

### 5.2 Insertions and deletions

```bash
# full output | jq '{
#   ins: [.git_stats[].insertions] | add,
#   del: [.git_stats[].deletions] | add
# }'
# Expected: ins > 100000, del > 50000
```

### 5.3 File types extracted

```bash
# full output | jq '[.git_stats[].file_types
#   | to_entries[]] | group_by(.key)
#   | map({ext: .[0].key, n: map(.value) | add})
#   | sort_by(-.n) | .[0:5]'
# Expected: includes .go, .yaml, .md, .sh
```

### 5.4 New files counted

```bash
# full output | jq '[.git_stats[].new_files] | add'
# Expected: > 0
```

### 5.5 Zero-insertion repos filtered out

```bash
# full output | jq '[.git_stats
#   | to_entries[] | select(.value.insertions == 0)] | length'
# Expected: 0
```

---

## 6. Jira Data

### 6.1 Issues collected

```bash
# full output | jq '.jira_issues | length'
# Expected: > 400
```

### 6.2 Enriched fields present

```bash
# full output | jq '.jira_issues[0]
#   | {has_components: has("components"),
#      has_created: has("created"),
#      has_resolved: has("resolved"),
#      has_comments: has("comment_count")}'
# Expected: all true
```

### 6.3 Summary and status non-null

```bash
# full output | jq '[.jira_issues[]
#   | select(.summary == null or .status == null)] | length'
# Expected: 0
```

### 6.4 Components distribution

```bash
# full output | jq '[.jira_issues[].components[]?]
#   | group_by(.) | map({c: .[0], n: length})
#   | sort_by(-.n)'
# Expected: includes Multicluster Networking, Security
```

---

## 7. Jira Cache

### 7.1 Cache directory exists after run

```bash
ls skills/career-advocate/private/jira-cache/ | wc -l
# Expected: > 0
```

### 7.2 Cached run skips fetching

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-20 2>&1 | grep "cached"
# Expected: "N cached, 0 to fetch"
```

### 7.3 Cache file format valid

```bash
cat skills/career-advocate/private/jira-cache/$(
  ls skills/career-advocate/private/jira-cache/ | head -1
) | jq .
# Expected: valid JSON with components, created, resolved,
#   comment_count
```

---

## 8. Scan Command

### 8.1 Creates registry

```bash
rm -f skills/career-advocate/private/repos.yml
bash skills/career-advocate/scripts/scan-repos.sh 2>&1 | tail -4
# Expected: "Registry updated", total > 40 repos
```

### 8.2 Valid YAML

```bash
python3 -c "import yaml; \
  yaml.safe_load(open('skills/career-advocate/private/repos.yml'))"
echo "Exit: $?"
# Expected: 0
```

### 8.3 Idempotent re-scan

```bash
bash skills/career-advocate/scripts/scan-repos.sh 2>&1 \
  | grep "New since"
# Expected: "New since last scan: 0"
```

### 8.4 Hosting types detected

```bash
grep -c 'hosting: github' skills/career-advocate/private/repos.yml
grep -c 'hosting: gitlab' skills/career-advocate/private/repos.yml
grep -c 'hosting: gerrit' skills/career-advocate/private/repos.yml
# Expected: github > 35, gitlab >= 1, gerrit >= 1
```

### 8.5 Collection reads from registry

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-20 2>&1 | head -1
# Expected: "Loaded N repos from registry"
```

---

## 9. Persistent Notes

### 9.1 Cache file created

```bash
test -f skills/career-advocate/private/collection-cache.json \
  && echo PASS
# Expected: PASS
```

### 9.2 Cache is valid JSON

```bash
jq . skills/career-advocate/private/collection-cache.json \
  > /dev/null && echo PASS
# Expected: PASS
```

### 9.3 Contribution log created

```bash
test -f skills/career-advocate/private/contribution-log.md \
  && echo PASS
# Expected: PASS
```

### 9.4 Log has dated section

```bash
grep -c '^## ' skills/career-advocate/private/contribution-log.md
# Expected: >= 1
```

### 9.5 Second run appends delta

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-20 2>/dev/null > /dev/null
grep -c "Delta since" \
  skills/career-advocate/private/contribution-log.md
# Expected: >= 1
```

---

## 10. Graceful Degradation

### 10.1 No repos.yml fallback

```bash
mv skills/career-advocate/private/repos.yml /tmp/repos.yml.bak
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-20 2>&1 | head -2
mv /tmp/repos.yml.bak skills/career-advocate/private/repos.yml
# Expected: "No repo registry found" + "Falling back"
```

### 10.2 Future dates produce empty but valid JSON

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2099-01-01 --until 2099-12-31 2>/dev/null \
  | jq '{prs: (.github_prs|length),
         stats: (.git_stats|keys|length),
         jira: (.jira_issues|length),
         comments: (.github_pr_comments|length)}'
# Expected: all 0
```

### 10.3 Temp file cleanup

```bash
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2026-07-20 2>/dev/null > /dev/null
ls /tmp/career-collect-* /tmp/jira-enrich-* 2>/dev/null | wc -l
# Expected: 0
```

### 10.4 collect-pr-depth empty input

```bash
echo '[]' | bash skills/career-advocate/scripts/collect-pr-depth.sh
echo "Exit: $?"
# Expected: [] and exit 0
```

---

## 11. Lens Prompts

### 11.1 All 4 lens files exist

```bash
ls skills/career-advocate/lenses/*.md | wc -l
# Expected: 4
```

### 11.2 Category count is 18

```bash
total=0
for f in skills/career-advocate/lenses/*.md; do
  n=$(sed -n '/Categories You Cover/,/What to Look For/p' "$f" \
    | grep -cE '^[0-9]+\.')
  total=$((total + n))
done
echo "Total categories: $total"
# Expected: 18
```

### 11.3 No stale field names

```bash
grep -rn 'backup_branches' skills/career-advocate/lenses/
# Expected: no output
```

### 11.4 All lenses reference git_stats

```bash
grep -l 'git_stats' skills/career-advocate/lenses/*.md | wc -l
# Expected: >= 2
```

### 11.5 All lenses have output format section

```bash
grep -l 'Output Format' skills/career-advocate/lenses/*.md | wc -l
# Expected: 4
```

---

## 12. Analysis Guide

### 12.1 Templates for all 3 modes

```bash
grep -c '### .* Mode' \
  skills/career-advocate/reference/analysis-guide.md
# Expected: 3 (Promote, Assess, Meeting)
```

### 12.2 Quick Stats includes new fields

```bash
grep -c 'lines of code\|File types\|components\|commented on' \
  skills/career-advocate/reference/analysis-guide.md
# Expected: 4
```

### 12.3 Synthesis mentions contribution log

```bash
grep -c 'contribution-log' \
  skills/career-advocate/reference/analysis-guide.md
# Expected: >= 1
```

---

## 13. Linters

### 13.1 shellcheck passes

```bash
shellcheck skills/career-advocate/scripts/*.sh
echo "Exit: $?"
# Expected: 0 (info-level findings are OK)
```

### 13.2 markdownlint passes

```bash
npx markdownlint-cli2 'skills/career-advocate/**/*.md'
echo "Exit: $?"
# Expected: 0
```

### 13.3 Full lint suite

```bash
make lint
echo "Exit: $?"
# Expected: 0
```

---

## 14. SKILL.md Structure

### 14.1 Frontmatter complete

```bash
head -10 skills/career-advocate/SKILL.md
# Expected: name, description, version, argument-hint,
#   user-invocable, allowed-tools all present
```

### 14.2 Scan mode works

```bash
# Simulated — scan-repos.sh runs and exits
bash skills/career-advocate/scripts/scan-repos.sh 2>&1 \
  | tail -1
# Expected: summary line with repo counts
```

### 14.3 Help mode output

```bash
grep -A10 '"help"' skills/career-advocate/SKILL.md \
  | grep -c "promote\|assess\|meeting\|scan\|help"
# Expected: 5 (all modes listed)
```

---

## 15. Full Year Integration Test

```bash
# Run once, save output, verify everything
bash skills/career-advocate/scripts/collect-contributions.sh \
  --since 2025-07-27 --until 2026-07-27 \
  2>/dev/null > /tmp/ca-full-year.json

jq '{
  valid: true,
  repos: .metadata.repos_searched,
  repos_with_commits: .metadata.repos_with_commits,
  gh_repos: (.metadata.github_contributed_repos | length),
  prs_merged: (.github_prs | length),
  prs_open: (.github_prs_open | length),
  prs_closed: (.github_prs_closed | length),
  reviews: (.github_reviews | length),
  pr_comments: (.github_pr_comments | length),
  issues: (.github_issues | length),
  commits: ([.git_commits[] | length] | add),
  commit_repos: (.git_commits | keys | length),
  stats_repos: (.git_stats | keys | length),
  total_ins: ([.git_stats[].insertions] | add),
  total_del: ([.git_stats[].deletions] | add),
  total_new_files: ([.git_stats[].new_files] | add),
  branch_repos: (.user_branches | keys | length),
  branches: ([.user_branches[] | length] | add),
  jira: (.jira_issues | length),
  jira_enriched: (.jira_issues[0] | has("components")),
  json_size_kb: (. | tostring | length / 1024 | floor)
}' /tmp/ca-full-year.json

rm /tmp/ca-full-year.json

# Expected thresholds:
#   repos >= 40
#   prs_merged >= 500
#   prs_closed >= 100
#   reviews >= 200
#   pr_comments >= 100
#   commits >= 500
#   stats_repos >= 20
#   total_ins >= 100000
#   branches >= 500
#   jira >= 400
#   jira_enriched: true
```
