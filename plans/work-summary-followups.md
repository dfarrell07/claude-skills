# Work-summary follow-ups

Status: open, October 18, 2026. Recorded after the simplification and review rounds on
PR #39. Each item came from an expert review measured against real weekly and quarterly
captures; none should change the digest unless stated. Verify every change by diffing
`render --candidates` on a real weekly and quarterly capture before and after.

## Simplification (about −750 script and −900 test lines)

1. **Jira comments:** drop comment paging, comment-fidelity bookkeeping and
   `_changelog_coverage` in `jira_sources.py`. Replace with a small extractor: credit
   comments whose `author.accountId` is the user and `created` is in the window. This
   also removes the recurring "comment stream lacks native actor identities" warning.
   Decide whether Jira `comment_edited` stays (GitHub edits are no longer credited).
2. **Jira public history:** keep `public_field_events`; remove identity requests,
   `inspect_pages`, `native_bases` and `history_review_links`; read the changelog by
   numeric issue ID. Optionally show field names in the digest ("status→Closed").
3. **Contextual links:** remove `contextual_jira_links`, `local_context_links`,
   `native_https_urls` and the "state is unverified for contextual links" notes
   (one changed digest line on a real quarter).
4. **Modules:** dissolve `extended_evidence.py` and `provider_references.py` into
   `github_sources`, `jira_sources`, `gitlab_api`, `work_links` and `references`. Store
   Jira key aliases as a plain dict and drop the second-view alias proof.
5. **One URL scanner** for GitHub PR, GitLab MR and Jira browse links, and one Jira
   text walker shared by `_source_strings` and `native_review_links`.
6. **Local sources:** drop `unattributed_commits` (no consumer); keep only own branch
   heads with in-window tips; shrink stale-worktree recovery to reflog parsing.
   Decide whether `gitlab_sources.py` (MR head probe, 0 unique MRs measured) stays.
7. **Dead code:** `link()`, `work_links.strings`, `include_numeric_fragments`,
   `jira.linked_prs`, the enriched-capture source comparison. Consider replacing the
   no-analysis fallback renderer with the digest.
8. **Wording:** replace "native" in user-visible warnings with plain language.
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
