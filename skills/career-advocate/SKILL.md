---
name: career-advocate
description: "Gather contribution evidence and build career advocacy documents"
version: 1.0.0
argument-hint: "<mode> [--since YYYY-MM-DD] [--until YYYY-MM-DD]"
user-invocable: true
allowed-tools: Bash, Read, Write, Agent
# Also uses acli for Jira queries in the collection script
---

# Career Advocate

```bash
/career-advocate                         # Promote mode (default, 180 days)
/career-advocate promote                 # Same
/career-advocate assess                  # Self-assessment (180 days)
/career-advocate meeting                 # 1:1 prep (14 days)
/career-advocate scan                    # Discover repos, update registry
/career-advocate promote --since 2026-01-01  # Custom date range
```

**Arguments:** $ARGUMENTS

---

## Step 0: Collect Contributions

```bash
SCRIPT_DIR="$(dirname "${BASH_SOURCE[0]}")"
ARGS="$(echo "$ARGUMENTS" | xargs)"

# Parse mode (first word, default: promote)
MODE="${ARGS%% *}"
[[ "$MODE" == --* ]] && MODE=""
MODE="$(echo "$MODE" | xargs)"
[[ -z "$MODE" ]] && MODE="promote"

VALID_MODES="promote assess meeting scan help"
if [[ "$MODE" == "help" ]]; then
  echo "Usage: /career-advocate <mode> [--since YYYY-MM-DD] [--until YYYY-MM-DD]"
  echo ""
  echo "Modes:"
  echo "  promote  Build promotion case (default, 180 days)"
  echo "  assess   Self-assessment for review cycle (180 days)"
  echo "  meeting  1:1 prep (14 days)"
  echo "  scan     Discover repos and update registry"
  echo "  help     Show this message"
  exit 0
fi

if [[ "$MODE" == "scan" ]]; then
  bash "$SCRIPT_DIR/scripts/scan-repos.sh"
  exit 0
fi

if ! echo "$VALID_MODES" | grep -qw "$MODE"; then
  echo "Unknown mode '$MODE'. Valid: $VALID_MODES"
  exit 1
fi

# Extract --since and --until (portable, no grep -oP)
SINCE=$(echo "$ARGS" | sed -n 's/.*--since \([^ ]*\).*/\1/p')
UNTIL=$(echo "$ARGS" | sed -n 's/.*--until \([^ ]*\).*/\1/p')

# Goal-dependent defaults
if [[ -z "$SINCE" ]]; then
  case "$MODE" in
    meeting) SINCE=$(date -d '14 days ago' +%Y-%m-%d 2>/dev/null \
               || date -v-14d +%Y-%m-%d) ;;
    *)       SINCE=$(date -d '180 days ago' +%Y-%m-%d 2>/dev/null \
               || date -v-180d +%Y-%m-%d) ;;
  esac
fi

# Create secure temp directory
CA_TMPDIR=$(mktemp -d /tmp/career-advocate-XXXXXXXXXX)
chmod 700 "$CA_TMPDIR"
trap 'rm -rf "$CA_TMPDIR"' EXIT

# Collect contributions
SINCE_FLAG=""
UNTIL_FLAG=""
[[ -n "$SINCE" ]] && SINCE_FLAG="--since $SINCE"
[[ -n "$UNTIL" ]] && UNTIL_FLAG="--until $UNTIL"

# shellcheck disable=SC2086
bash "$SCRIPT_DIR/scripts/collect-contributions.sh" $SINCE_FLAG $UNTIL_FLAG \
  > "$CA_TMPDIR/contributions.json"

echo "MODE=$MODE"
echo "SINCE=$SINCE"
echo "UNTIL=${UNTIL:-$(date +%Y-%m-%d)}"
echo "CA_TMPDIR=$CA_TMPDIR"
echo "SCRIPT_DIR=$SCRIPT_DIR"

cat "$CA_TMPDIR/contributions.json"
```

---

## Step 1: Analyze via Lens Subagents

Read `$CA_TMPDIR/contributions.json` from Step 0. It contains
these data sections — ensure ALL are passed to subagents:
`git_commits`, `git_stats`, `github_prs`, `github_prs_open`,
`github_prs_closed`, `github_pr_comments`, `github_issues`,
`github_reviews`, `user_branches`, `jira_issues`.

Read these files if they exist (do not error if missing):

- `$SCRIPT_DIR/private/current-level.md`
- `$SCRIPT_DIR/private/target-level.md`
- `$SCRIPT_DIR/private/career-context.md`
- `$SCRIPT_DIR/private/one-on-ones.md`

**For `meeting` mode:** Skip subagents. Read
`$SCRIPT_DIR/reference/analysis-guide.md` for the meeting template.
Produce a short 1:1 prep doc directly from the contribution data +
one-on-ones.md. Go to Step 2.

**For `promote` and `assess` modes:**

Find the lens prompts: `ls $SCRIPT_DIR/lenses/*.md`

Read each lens `.md` file. **Launch one Agent subagent per lens file in a single parallel wave.** In each subagent's prompt, include:

1. The full lens prompt from the `.md` file
2. The contribution JSON from `$CA_TMPDIR/contributions.json`
3. The current-level and target-level expectations (if available)
4. The career-context.md and one-on-ones.md content (if available)

Each subagent should write its structured assessment (RATINGS, NARRATIVE, GAPS) as its final output. Collect all lens results.

---

## Step 2: Synthesize & Present

Read `$SCRIPT_DIR/reference/analysis-guide.md` for the output template.

If previous runs exist in `$SCRIPT_DIR/private/history/`, read the
most recent one matching the current mode for delta analysis. Also
read `$SCRIPT_DIR/private/contribution-log.md` if it exists for
what changed since the last collection.

**For `promote`:** Synthesize all lens reports into a final
document following the promote template. Start with the Manager
Briefing (2-minute read), then full evidence by category, then
gaps. Be honest -- state gaps clearly.

**For `assess`:** Synthesize into self-assessment format per the assess template.

**For `meeting`:** You already produced the document in Step 1.

Save the output to `$SCRIPT_DIR/private/history/` using Write tool:

```bash
mkdir -p "$SCRIPT_DIR/private/history"
```

Filename: `YYYY-MM-DD-<mode>.md` (e.g., `2026-07-24-promote.md`).

Present the full document to the user. Print a summary line:

```text
Saved to private/history/YYYY-MM-DD-<mode>.md (N words, SINCE to UNTIL)
```

Clean up is handled by the trap in Step 0.
