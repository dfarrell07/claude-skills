# Adversarial review panel

Use this after a draft packet exists. A panel attacks the draft so the committee
can't. Run it in rounds until a round comes back clean.

## Prompt

Give each reviewer the level document, the draft, `recorded.md`, any research
indexes and the period digests (`periods/*/candidates.md`, searched with grep, not
read whole). Ask each reviewer to play four seats:

1. **A skeptical promotion committee member:** claims that are vague, inflated,
   old, single-team or unsupported, and clauses never argued.
2. **The manager arguing the case:** impact that is undersold, and work in the
   digests that never reached the packet.
3. **A senior Principal or Distinguished engineer:** whether the scope is really
   multi-component and multi-team, and where "exceeds" is earned.
4. **A cross-org stakeholder** (product security, release engineering, docs, QE,
   partner teams): cross-team impact that is invisible.

Rules for reviewers:

- Every proposed claim needs a checkable source: a URL, a Jira key, or a
  `file:line`.
- Unverified items are labelled as leads.
- Prefer new evidence to rewording.
- Flag overstatement: an inflated claim costs more with a committee than a
  missing one.
- No raw activity counts. Sourced authorship proportions are fine.
- First line `VERDICT: STRONGEST` or `VERDICT: NEEDS_WORK`, then numbered findings
  with seat, section, problem, fix, evidence and status.

Run two independent reviewers per round, through the delegate skill.

## Rounds

- **Rounds 1–3:** open. Reviewers find omitted evidence and overstatements.
- **Round 4:** add readability. Name cuts and merges that lose no evidence.
- **Round 5 on:** materiality bar. Report only what would change a committee's
  judgment of a criterion, or what is factually wrong. Allow at most three polish
  notes.
- **Every round:** pass the earlier rounds' findings files and list what is
  settled, so reviewers don't repeat it. Settled items include points flagged
  for the user, known gaps and rejected leads.
- **Stop:** when a round's only findings are decisions that belong to the user,
  such as a rating the user has argued. Record them for the user; don't keep
  re-running.

## Verify before applying

Reviewers invent and misread. Before a finding changes the draft:

- **Check merge state:** `gh pr view` for author, state and merge date. A closed,
  unmerged PR is not delivered work.
- **Separate tracker status from delivery:** a closed release tracker can be an
  abandoned attempt. Read the closing comment.
- **Read the sentence the claim rests on:** for example, an advisory's fixed-issue
  list, or a PR body's "Testing" section.
- **Credit others:** who ran a release, who wrote the original gate, and who
  co-authored a spec.
- **Keep proposals as proposals:** a design that was proposed is not one that was
  adopted, and documenting a decision is not making it.
- **Recheck dates:** what merged after a release did not happen "during" it.
