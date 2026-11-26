# Work-summary follow-ups

Status: open, November 25, 2026. Items 1–8 of the simplification round are
done (PRs 59–68); their history is in git. Verify every change by diffing
`render --candidates` on a real weekly and quarterly capture before and after.

## Open

- **Tests:** one shared capture builder in `tests/`; collapse the scanner tests
  into one table-driven test.
- **Digest:** optionally show Jira field names ("status→Closed").
- **Fallback renderer:** consider replacing the no-analysis list with the digest.

## Behaviour to decide

- Maintainer actions on others' PRs (merge, force-push, close without review or
  comment) are not read when the PR is found only through involvement, assignment or
  review requests. None appeared in three real captures; revisit if users report gaps.
- Bot-authored backport PRs carrying the user's commits no longer appear in the digest.
- Branch-name ticket keys no longer pair PRs with tickets (search results lack head refs).

## Quarterly refinements, from use

- Calendar: a reusable private fiscal calendar, or resolve each request explicitly.
- Questions: a saved private question set, if repeated use justifies it.
- Output: answer length and grouping; core links stay under briefing themes, and
  answer starters keep a separate evidence view unless requested otherwise.
- Continuity: a private theme and commitment index across runs, and how the user
  corrects it.
- Supplemental evidence: which sources to include for teaching, adoption and feedback.

## Qualification still needed

Another round of independent reviews (correctness, design, docs, real 1:1 and
quarterly forward tests) until reviews find nothing material.
