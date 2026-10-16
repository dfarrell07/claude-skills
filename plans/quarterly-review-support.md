# Plan for quarterly review support

Status: initial planning, October 16, 2026. No quarterly mode is implemented by this document.

Extend the evidence gathering behind weekly 1:1 briefings to help prepare quarterly review answers.
The result should sound like the user's own concise review: a few meaningful accomplishments,
how they were achieved, and thoughtful answers to the current review questions. This plan is a place
to collect requirements and decisions before implementation.

## Reference documents inspected

Four quarterly DOCX exports were found in Downloads: the three historical examples and a newer Q3 file.
Names below identify local references; the documents and personal responses remain outside the repository.
Their labels identify the examples, not verified reporting dates or the organization's fiscal calendar.

All four ask about accomplishments, including how they were achieved, and priorities.

| Local reference | Additional questions present | Planning lesson |
| --- | --- | --- |
| `QC Q4 2025.docx` | Enjoyment and energy; manager support | Include reflection when requested. |
| `QC Q1 2026.docx` | None | A short question set can be sufficient. |
| `OC 2026 Q2.docx` | Feedback and growth; career aspirations; manager support | Questions use different time horizons. |
| `QC 2026 Q3.docx` | None | The newest example continues the concise format. |

All four contain question paragraphs followed by bulleted answers. None contains Jira or PR hyperlinks,
tables, comments, or tracked changes. This describes the exports; it does not prove that every form
question was exported or that links are undesirable in a supporting evidence view.
The existing `Daniel_Roshni.docx` remains the reference for the separate weeklyish 1:1 briefing.

The examples emphasize automation, teaching, enabling other contributors, wider adoption, and technical
delivery. These themes help identify valuable evidence, but must not become a fixed taxonomy or a claim
that the same accomplishments occurred in another quarter. Historical answers establish voice and context;
they do not independently verify a new result, adoption count, time saving, or piece of feedback.

## Intended experience

Start from the review's actual questions and the period being reviewed. Gather relevant work and supplied
context, propose a small set of evidence-backed themes, resolve important missing personal input, and draft
answers in question order. The user should be able to paste the answers into the review form with little editing.

- Keep answers direct, personal, and concise; use first person where natural.
- Rank accomplishments by supported impact, responsibility, and relevance rather than activity counts.
- Explain both what changed and how the user contributed: design judgment, collaboration, teaching,
  enabling others, execution, and follow-through where supported.
- Consolidate related work into durable themes. Use sublists when they clarify distinct contributions,
  rather than forcing every theme into one sentence or repeating weekly updates.
- Separate achieved results, work still in progress, and future priorities. A prior priority is not proof of delivery.
- Preserve the user's degree of certainty. Do not inflate plausible benefits into measured outcomes.
- Keep personal reflection, feedback, aspirations, and manager requests grounded in supplied input.
  Ask focused questions for missing material rather than inferring feelings or promotion readiness from commits.

The quarterly answer format can differ from the weekly format. Weekly briefings retain their ranked work
bullets with selective raw Jira and PR/MR links. For quarterly reviews, the working proposal is concise
question-and-answer text plus a separate private evidence view. Whether final answers include selected
links should follow the user's destination and preference, rather than automatically inserting every ticket.

## Periods and continuity

Resolve the named review cycle, accomplishment period, priority period, and timezone explicitly.
The examples say accomplishments are from the previous quarter and priorities are for the current quarter;
the label on a file alone cannot establish which dates that means. A quarter is not a rolling 90-day window.
Use supplied dates or a verified calendar; ask when unresolved dates would change the work included.

Different questions may need different windows. In particular, the Q2 example asks about feedback over two
quarters and career aspirations over 1–2 and 3–5 years. Aspirations describe future horizons rather than collection cutoffs.
Retain those distinctions
instead of applying one start date to every answer.

Use earlier reviews and 1:1 notes to understand commitments, carryover, and changes in scope. Track a
continuing effort once, with the contribution and outcome specific to this period. Earlier work can explain
context; later work can clarify subsequent state, but neither should be credited as this quarter's delivery.
Preserve event dates and observation dates so a PR merged after the quarter does not become a shipped result
inside it. Define and test exact interval boundaries, including timezone conversion, before collection changes.

## Evidence to gather and how to use it

Reuse [work-summary](../skills/work-summary/SKILL.md) discovery, native attribution, relation checks,
selective links, and capability fallbacks. Include Jira-only work, reviews, design contributions, unmerged
work, and local or backup branches where relevant. Existing weekly reports are useful leads and indexes;
missing weekly reports must not exclude work, and repeated bullets must not multiply credit.

Supplement engineering records with user-selected 1:1 notes, prior reviews, design documents, presentation
materials, teaching records, feedback, and adoption evidence when accessible or supplied. Do not require
every source to be available. Distinguish a recorded personal account from independently verified technical
evidence, and keep the attribution and date of feedback. Reviewing someone else's implementation may be
a meaningful contribution without making the user its author.

For each candidate theme, retain a private record of the user's actions, dates, outcome, evidence, and limits.
Distinguish building a capability from others adopting it, teaching from demonstrated learning, and a release
from deployment or downstream benefit. Claims about speed, quality, cost, reach, or time saved need a
supporting measurement or an explicitly attributed user account. Do not manufacture metrics from PR counts.
Tie each final factual claim to evidence or supplied input without making the user sort through the full inventory.

Choose core Jira and PR/MR links at the narrowest useful scope. Exclude incidental references and redundant
backports unless they substantiate a distinct result. A non-code contribution can use its actual source
instead of acquiring an unrelated Jira link. Keep detailed provenance separate from the paste-ready answers.

Quarter-long collection must be qualified separately from a weekly run: search caps, date slicing, pagination,
comment/history retrieval, memory use, and resumability can change at this scale. Split bounded native queries
where required, reconcile overlaps by native identity, and retain failures without discarding successful pages.
Do not concatenate summaries and call that complete coverage. A weekly run passing on this machine does not
establish a passing quarterly run.

Retain useful output when a host has connectors but no shell, Git but no `gh`, or no Jira tool/authentication.
Use the capabilities actually available; preserve source-specific limits and distinguish inaccessible evidence
from no activity. Missing optional sources should not trigger installation or login as a prerequisite.

## Fit with existing skills

The repository already has [career-advocate](../skills/career-advocate/SKILL.md), including `assess`,
`promote`, and `meeting` modes and career review lenses. Its generic assessment format and default 180-day
window do not establish support for these quarterly question sets. Its collection path also differs from the
newer work-summary path. Do not make running both collectors a prerequisite or silently treat them as equivalent.

Working direction: share the reliable evidence foundation with weekly briefings and add a separate quarterly
question-answering workflow. Decide whether that belongs in work-summary, career-advocate, or a small
companion skill after comparing a representative quarterly draft with the examples. Reuse useful review lenses
without imposing promotion ratings, a career ladder, or a mandatory subagent runtime on quarterly answers.
Keep existing weekly invocations and their default output intact.

Start with read-only collection and a local Markdown draft. Creating or submitting a review in an HR system,
editing the supplied documents, and building a promotion packet are separate capabilities, not part of this first increment.
Keep raw reviews, feedback, collected work evidence, and generated personal answers in private storage outside
the tracked plan. Use synthetic fixtures for committed tests; do not depend on these Downloads paths at runtime.

## Decisions still to make

- Entry point and ownership: which skill hosts the workflow, and how explicit dates and question sets are supplied.
- Calendar: how a named cycle maps to accomplishment and priority dates, including fiscal calendars.
- Questions: whether to accept a supplied form, pasted questions, or a reusable private question set;
  which missing information warrants a question before drafting.
- Output: desired answer length and whether core links belong inline, in indented evidence bullets,
  or only in a separate evidence view. The examples support concise answers but do not settle link placement.
- Continuity: whether to retain a private theme/commitment index across runs, and how the user corrects it.
- Supplemental evidence: which accessible sources the user wants included for teaching, adoption, and feedback.

## Implementation sequence and acceptance

1. Agree on one representative question set and explicit reporting periods. Produce a private manual draft
   using available evidence to test usefulness before designing new command arguments or storage formats.
2. Choose skill ownership and document the quarterly workflow. Specify the minimal evidence and date contract;
   reuse existing collection where it is adequate, and identify changes justified by the longer period.
3. Add only the necessary collection and synthesis support. Preserve weekly behavior and connector-only operation.
   Establish a practical long-period retrieval and review strategy using observed limits rather than guessed caps.
4. Test with synthetic cases, then perform an authorized real quarterly run. Compare every drafted claim with
   its source and every supplied question with its answer. Record actual coverage and unresolved personal input.
5. Review from evidence, editorial, performance-review, privacy, and portability perspectives. When independent
   reviewers are available, use them to challenge the draft and implementation; otherwise disclose review limits.
   Fix material findings and rerun affected checks before calling the capability ready.

Acceptance should include:

- The two-question and expanded forms both work; absent export questions are not assumed to be a universal template.
- Previous-quarter accomplishments, current-quarter priorities, and two-quarter feedback use the right periods.
- A cross-quarter project, a merge after the cutoff, and a repeated weekly accomplishment receive accurate credit.
- Teaching and adoption can be represented without PRs; unsupported impact numbers and invented feedback are rejected.
- Older aspirations and priorities are not silently presented as current intent; missing personal answers stay explicit.
- Important work is not buried by many small PRs, and each included link substantiates its associated claim.
- Long-period caps, failed pages, unavailable tools/auth, and notes-only inputs produce useful, honestly scoped drafts.
- Sensitive source text stays private, existing weekly output remains correct, and reviewers can trace the final claims.

Planning is complete enough to begin a worked example when requested. Skill placement, interface, calendar defaults,
and final quarterly output conventions remain proposals until that example is reviewed.
