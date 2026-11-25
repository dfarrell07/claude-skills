# Work-summary follow-ups

Status: open, October 18, 2026. Recorded after the simplification and review rounds on
PR #39. Each item came from an expert review measured against real weekly and quarterly
captures; none should change the digest unless stated. Verify every change by diffing
`render --candidates` on a real weekly and quarterly capture before and after.

## Simplification (about −750 script and −900 test lines)

1. **Jira comments:** done. Comment edits are no longer credited anywhere.
2. **Jira public history:** done, except: optionally show field names in the digest
   ("status→Closed").
3. **Contextual links:** done.
4. **Modules:** done.
5. **One URL scanner:** done.
6. **Local sources:** done. The GitLab MR head probe is removed.
7. **Dead code:** done, except: consider replacing the no-analysis fallback renderer
   with the digest.
8. **Wording:** done.
9. **Tests:** one shared capture builder in `tests/`; collapse scanner tests into one
   table-driven test.

## Behaviour to decide

- Maintainer actions on others' PRs (merge, force-push, close without review or
  comment) are not read when the PR is found only through involvement, assignment or
  review requests. None appeared in three real captures; revisit if users report gaps.
- Bot-authored backport PRs carrying the user's commits no longer appear in the digest.
- Branch-name ticket keys no longer pair PRs with tickets (search results lack head refs).

## Qualification still needed

- Time a real quarterly collection on the final code (73 minutes before the last
  speedups) and update the quarterly reference's timing.
- Run another round of independent reviews (correctness, design, docs, real 1:1 and
  quarterly forward tests) after the items above, until reviews find nothing material.
- The `plans/quarterly-review-support.md` qualification notes predate the
  simplification; summarize them against the current design.
