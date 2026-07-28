# AI Helpers Skills

## /work-summary

Show recent work: git commits and GitHub activity (PRs, issues).

```bash
/work-summary           # Last 7 days with AI analysis (default)
/work-summary 14        # Last 14 days with AI analysis
make work-summary       # Last 7 days without AI analysis
make work-summary DAYS=14  # Last 14 days without AI analysis
```

**With skill (AI analysis):**

- Executive Summary with themed accomplishments
- In Progress section
- Activity Stats
- Detailed Work Log

**With make target (no AI):**

- Activity Stats
- Detailed Work Log only

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
