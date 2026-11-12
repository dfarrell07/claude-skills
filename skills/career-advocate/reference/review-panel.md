# Adversarial review panel

A panel attacks the draft so the committee can't. Run it through the delegate
skill, with two independent reviewers per round.

## Prompt

Give each reviewer the level document, the draft, `recorded.md`, the research
files and the period digests. The digests are large, so tell reviewers to search
them, not read them whole. Write the prompt once and save it in `panels/`. Each
reviewer plays four seats:

1. **A skeptical committee member:** claims that are vague, inflated, stale or
   unsupported, and criteria never argued.
2. **The person's manager arguing the case:** impact that is undersold, and work
   in the evidence that never reached the draft.
3. **An engineer one or two levels above the target:** whether the claimed scope
   and ratings meet what the criteria actually say.
4. **A stakeholder from a partner function:** for example security, release, docs,
   QE or another team. Their cross-team impact is often invisible.

Rules for reviewers:

- Every proposed claim cites a checkable source: a URL, an issue key or a
  `file:line`. Anything unverified is labelled a lead.
- Prefer new evidence to rewording, and flag overstatement.
- Report only material findings: ones that would change a committee's judgment of
  a criterion, or that are wrong. Also propose cuts that lose no evidence.
- First line `VERDICT: STRONGEST` or `VERDICT: NEEDS_WORK`, then numbered findings
  with seat, section, problem, fix, evidence and status.

## Rounds

Every round, pass the earlier rounds' findings and list what is settled: points
flagged for the person, known gaps and rejected leads. Repeat after each material
change. Stop when no verified, actionable findings remain, or when the only ones
left are the person's decisions. Record those for the person.

Verify each finding before it changes the draft, using the rules in `SKILL.md`.
Reviewers misread and invent.
