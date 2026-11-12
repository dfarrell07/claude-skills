---
name: career-advocate
description: "Build an evidence-backed promotion case against a target level's criteria: collect the person's work across their career, find evidence the collectors miss, draft the packet by the level's own sections, keep an interview of open questions, and harden it with an adversarial review panel. Use for promotion packets and career advocacy, not weekly summaries (see work-summary)."
version: 2.0.0
argument-hint: "[target level document]"
user-invocable: true
allowed-tools: Bash, Read, Write, Edit, Agent
---

# Career Advocate

Argue one case: the person already operates at the target level. Every claim
traces to evidence someone else can check. Where the evidence supports more than
the bar, say so; where it doesn't, name the gap and the next step.

**Arguments:** $ARGUMENTS

## Inputs

- **Target level document:** any ladder. Its numbered items under `##` sections
  (for example Responsibilities and Skills) are the criteria, and the packet's
  sections follow them.
- **The person's identities:** GitHub login, Git emails, Jira user, and older
  community handles (Gerrit, IRC, mailing lists).
- **Store:** `${CAREER_ADVOCATE_STORE:-~/.local/share/career-advocate}`, mode 700,
  a local Git repository with no remote. It holds:
  - `target-level.md`;
  - `periods/<window>/` captures;
  - `research/` with raw findings and their sources;
  - `recorded.md` for the person's own account;
  - `packets/draft.md`;
  - the interview's `questions.json` and `questions.md`;
  - `panels/` for review prompts and findings.

  Commit there small and often. Keep large captures out of Git, and nothing from
  the store ever goes into a public repository.

## Workflow

1. **Collect.** Run work-summary over explicit windows: quarters for the current
   year, calendar years before that. A closed period is collected once and kept.
   - Validate each period against a count you can check independently, such as
     merged authored PRs from `gh search prs --author <login> --merged-at <range>`.
   - Retry rate-limited windows one at a time.
   - Render each digest with `render-report.sh --candidates`.
2. **Research what the collectors miss.** Use
   `reference/evidence-sources.md`. Save raw findings with their source URLs under
   `research/`. Record the person's own statements in `recorded.md`, labelled as
   their account until a link or a named person confirms them.
3. **Draft.** Write `packets/draft.md` with one subsection per criterion, in the
   level document's order. Each subsection gets:
   - the argument;
   - the evidence, as links or `file:line`;
   - one rating line, `- Rating: exceeds`, `- Rating: meets` or
     `- Rating: not yet shown`;
   - a `Next step:` line where the case is short.

   Mark unconfirmed items `*(lead)*`. Use no raw activity counts: impact, scope
   and sourced proportions (`scripts/workflow-authorship.sh`) only. Open with a
   short case summary.
4. **Interview.** Turn the packet's gaps into questions for the person:

   ```bash
   python3 scripts/interview.py --level "$STORE/target-level.md" \
     --draft "$STORE/packets/draft.md" --state "$STORE/questions.json" \
     --out "$STORE/questions.md"
   ```

   Ask one question at a time, highest value first. Fold answers into
   `recorded.md` and the draft. Note details the person can't recall as things to
   look up; don't block on them.
5. **Review.** Run the adversarial panel in `reference/review-panel.md`. Verify
   every finding before it changes the draft, and iterate until a round comes back
   clean.

Steps 2–5 repeat as evidence arrives.

## Rules

- **True and checkable.** Find evidence rather than inflate. An overstated claim
  costs more with a committee than a missing one.
- **Credit others:** co-authors, the people who ran a release with the tool, and
  whoever wrote what the person extended.
- **Keep status distinct:**
  - proposed versus adopted;
  - merged versus closed;
  - a closed tracker versus delivered work.
- **Delegated research is untrusted.** Open the cited source for anything
  load-bearing before using it.
- **Ask before contacting anyone or requesting access.** That includes
  references, colleagues and IT.
