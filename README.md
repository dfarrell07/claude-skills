# Claude Skills

Claude Code skills for work summaries, Jira, notes, CVE triage, delegation to
other agent CLIs, and finding other Claude sessions.

## Installation

```text
/plugin marketplace add dfarrell07/claude-skills
/plugin install claude-skills@claude-skills
```

Each skill's `SKILL.md` is the full reference. `make test` runs the linters and
every offline test.

## /work-summary

Draft a ranked, link-selective list of recent work for a 1:1, or a quarterly
accomplishment briefing. Uses whatever is available: Python 3.10+ with Git
2.37+, authenticated `gh` and `acli`, GitHub/Jira connectors, or supplied notes;
missing sources reduce coverage instead of blocking the rest. Read-only; writes
only a private run directory. Local collection scans your home and current
directories; paths given after the day count replace them.

```text
/work-summary
/work-summary 14
/work-summary quarterly
make work-summary [DAYS=14]   # conservative fallback list, no agent analysis
```

## /jira

Query, view and update Jira issues via `acli`. Every update is shown and
confirmed before it is written.

```text
/jira [my-issues]                  # My open issues
/jira search <JQL>                 # First 25 matches
/jira view <issue-key>
/jira update <issue-key> transition <status> | comment <text> | assign <user>
```

`assign` takes an email, an account ID or `@me`, not a display name.

## /notes

Persistent markdown notes by topic in `~/notes-ai/<topic>/`. Saving a note
commits it locally there and never pushes.

```text
/notes <topic> <what to note>
/notes <topic> list | view [N] | search <query>
/notes [list]
```

## /delegate

Run a task on another agent CLI (agy, codex; never claude unless named) or a
built-in subagent, picked from live quota. The answer goes to a file and only
its path is printed. codex and claude runs are read-only unless
`DELEGATE_WRITE=1`; agy always runs with edits allowed. Agents read everything
under the working directory, git-ignored files too, and a run never mixes work
and personal accounts. `parallel` runs N agents on one prompt; `chain` has each
verify and improve the previous result.

```text
/delegate [parallel|chain] [tool:account ...] <task>
/delegate budget   # where work should go now, per account
```

## /peers

Find and message live Claude Code sessions in any `~/.claude*` profile,
including ones `ListAgents` does not show. Lists each session's `uds:` address
for `SendMessage`.

```text
/peers [session name]
```

## /personal-jira-triage

Audit assigned Jira work against related PRs and current evidence, then
propose minimal updates with adversarial review. It never updates Jira, PRs or
assignments; it writes only a private run directory and stops at a summary and
a first batch of drafts for review.

```text
/personal-jira-triage
/personal-jira-triage PROJECT-123       # Triage only this issue
/personal-jira-triage first batch of 2
```

## /cve-agent

Submariner security agent: discovers, triages, verifies and audits CVE
trackers across active branches. Designed to run under `/loop`, for example
`/loop 1h /cve-agent --auto`. It comments
on, transitions and reassigns Jira issues (approval-gated unless `--auto`),
and commits its reports and caches to `~/notes-ai/cve-agent`.

```text
/cve-agent                # One cycle, interactive
/cve-agent --auto         # Act on safe items unattended
/cve-agent --dry-run      # Full cycle, no Jira changes
/cve-agent --clear-cache  # Delete caches, then exit
```

## /cve-jira-triage

Triage one Submariner CVE tracker with image scans and source checks, or scan
a component. Every Jira change needs explicit approval. Its reference docs
and scripts are shared with `/cve-agent`.

```text
/cve-jira-triage <issue-key>
/cve-jira-triage scan <component> <rhacm-version>
/cve-jira-triage status
```

## /career-advocate

Gather contribution evidence and build a promotion case, self-assessment or
1:1 prep document. Before the first run, create `private/` with your current
level and job expectations (see `reference/analysis-guide.md`) and run
`/career-advocate scan` to build the repository registry. Its data and saved
documents live in `skills/career-advocate/private/` (git-ignored).

```text
/career-advocate [promote|assess|meeting|scan] [--since YYYY-MM-DD] [--until YYYY-MM-DD]
```
