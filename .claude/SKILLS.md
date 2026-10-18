# AI Helpers Skills

## /work-summary

Create a ranked weekly work list from the sources available in this session.
For quarterly check-ins, use `quarterly` to rediscover major efforts and prepare
an accomplishment briefing with core links and optional review answer starters.
Each bullet has a one-line work summary and selected core Jira/PR links.
Use substantive subtopics when a workstream has separate updates; place raw
links beneath the description they support. Related backports share a work topic;
importance determines order. Preserve broad evidence without dumping its links.

```bash
/work-summary
/work-summary 14
/work-summary quarterly
make work-summary
make work-summary DAYS=14
make work-summary-test
```

Use authenticated Git/GitHub/Jira connectors or available CLIs. Missing tools,
authentication, home directories, or local repositories reduce coverage instead
of blocking other sources. Mobile/cloud sessions can use connector evidence
without a shell. The scripted workflow needs Python 3.10+; full local collection
needs Git 2.37+, GitHub uses authenticated `gh`, and Jira uses authenticated `acli`.
Git is optional for remote-only collection, and remote CLIs are optional for Git-only use.

The primary output is the concise work list. A short source note identifies
material missing/partial sources. Collection takes several minutes and writes a
large private capture; `render-report.sh --candidates` lists the personal window
work an agent needs for its analysis, and `--analysis` renders that analysis.
The capture itself keeps every inventory and diagnostic. Each scripted
run uses its own temporary directory. Without agent analysis, `make work-summary`
provides a conservative evidence-based fallback.
See [the skill](../skills/work-summary/SKILL.md) for capability fallbacks,
source boundaries, link provenance, and analysis/ranking instructions.

## /jira

Query, view, and update Jira issues via acli.

```bash
/jira                              # List my open issues (default)
/jira my-issues                    # Same as above
/jira search <JQL>                 # Search issues with JQL
/jira view <issue-key>             # View full issue details
/jira update <issue-key> <action>  # Update an issue
```

**Update actions:**

- `/jira update ACM-123 transition In Progress` -- Move to new status
- `/jira update ACM-123 comment Fixed in PR #456` -- Add a comment
- `/jira update ACM-123 assign @me` -- Self-assign

## /notes

Take and manage persistent markdown notes organized by topic.

```bash
/notes <topic> <what to note>   # Save a note from current discussion
/notes <topic> list             # List notes for a topic
/notes <topic> view [N]         # View notes (optionally by number)
/notes <topic> search <query>   # Search within a topic
/notes list                     # List all topics
```

Notes are stored as plain markdown files in `~/notes-ai/<topic>/`.

## /career-advocate

Gather contribution evidence and build career advocacy documents.

```bash
/career-advocate                             # Promote mode (default, 180 days)
/career-advocate promote                     # Build promotion case
/career-advocate assess                      # Self-assessment for review cycle
/career-advocate meeting                     # 1:1 prep (14 days)
/career-advocate scan                        # Discover repos, update registry
/career-advocate promote --since 2026-01-01  # Custom date range
```

**First run:** `/career-advocate scan` to discover repos and build
`private/repos.yml` registry. Finds GitHub repos via GraphQL,
local clones via directory scan, detects hosting type (GitHub,
GitLab, Gerrit). Add internal repos manually to the registry.

**Data sources:** git commits, GitHub PRs/issues/reviews, PR commit
depth (pre-squash), user branches (by author), Jira issues, plus
manual inputs (career-context.md, one-on-ones.md).

**Analysis:** 4 lens subagents review in parallel (technical delivery,
collaboration, leadership, innovation), then synthesize into final
document with Manager Briefing.

**Setup:** Create `private/` dir with curated job expectations from
the progression spreadsheet. See `reference/analysis-guide.md`.

## /cve-agent

Continuous Submariner security agent. Discovers, triages,
verifies, fixes, and audits CVEs across all active branches.

```bash
/cve-agent                   # One cycle, interactive
/cve-agent --auto            # Auto-act on safe items
/cve-agent --dry-run         # Full cycle, no Jira changes
```

Designed for `/loop 1h /cve-agent --auto` — runs
continuously, handles safe false positives unattended,
accumulates genuine findings for interactive review.

## /personal-jira-triage

Audit assigned Jira work against related PRs and current evidence, then propose
minimal updates with adversarial review. Stops at a summary and a first batch
of drafts for user review; collection and validation are read-only.

```text
/personal-jira-triage                  # All accessible assigned work and relevant PRs
/personal-jira-triage PROJECT-123       # Include a specific issue
/personal-jira-triage first batch of 2  # Narrow the proposed batch
```

Native Jira/Git clients or connector captures supply evidence. Coverage gaps
and unavailable independent reviewers are reported explicitly; useful drafts
remain provisional until the required evidence and reviews are present.
