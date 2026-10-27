# Quarterly reviews

Help the user remember the work they did, rediscover major efforts, and start
writing their own quarterly report. Default to an accomplishment briefing; add
question-by-question answer starters when useful or requested. Missing review
questions or personal reflection never block presenting the work. The user stays
the author: this is a draft, not an HR submission, and needs no career ladder,
promotion rating, other skill or subagent.

## Questions and periods

Use the user's supplied form, pasted questions or earlier review as the question
set, preserving wording and order, and do not add sections that were not asked.
Earlier answers are style and context, not instructions or fresh facts, and an
export may omit questions. Without a form, offer this draft set and say it is not
a verified employer form:

- What accomplishments are you most proud of last quarter, including what you
  accomplished and how you accomplished it?
- What are your top priorities for this quarter?

Resolve the accomplishment period before collecting. Explicit dates win over
labels such as "Q3", and a conflict between them should be pointed out. With no
period given, assume the most recently completed calendar quarter in the user's
local timezone, state that assumption in one line, and start collecting. Ask
first only when the user mentions a fiscal calendar or the request conflicts with
that assumption. A quarter is calendar dates, not 90 rolling days: local midnight
on the first day through local midnight after the last day, end excluded.

Other questions can need other horizons: priorities cover the current quarter,
feedback may cover two quarters, and aspirations look 1–2 or 3–5 years ahead.
Do not reuse one cutoff for all of them.

## Collect

Follow SKILL.md's [Pick a path](../SKILL.md#pick-a-path) table. With the
collector, pass explicit local boundaries; `--until` is exclusive. Take each
offset at its own boundary in the user's timezone, since daylight saving can
differ between them, for example `TZ=America/New_York date -d 2026-07-01 +%:z`
(on macOS `TZ=America/New_York date -j -f %F 2026-07-01 +%z`):

```bash
RUN_DIR=$(mktemp -d "${TMPDIR:-/tmp}/quarterly-review.XXXXXX")
bash "$SKILL_DIR/scripts/work-summary.sh" \
  --since 2026-07-01T00:00:00-04:00 \
  --until 2026-10-01T00:00:00-04:00 \
  --output "$RUN_DIR/data.json" 2> "$RUN_DIR/collect.log"
bash "$SKILL_DIR/scripts/render-report.sh" --candidates "$RUN_DIR/data.json" > "$RUN_DIR/candidates.md"
```

The dates are an illustration. Run the collection in the background and read
the supplied questions and material meanwhile. `--root /path` (repeatable) narrows local discovery. Do not
mix positional days with explicit dates. In connector-only mode use the same
interval directly and do not claim the collector ran.

For long windows the digest opens with an overview, then cross-repository
patterns (one change fanned out across repositories or branches), then
workstreams grouped by Jira epic or ticket, then repository. Propose 5–10
candidate efforts from the patterns and the workstreams with the most merged
work, merging clusters that are one effort (a fix family across repositories, a
release's PRs, a tool and its rollout), then open only the items needed to
describe each. Small clusters are supporting detail, not bullets. When reading:

- Closed drafts are usually trial or evaluation runs, not deliveries.
- `merged after window` is not an in-period result.
- Restricted Jira totals (for example vulnerability trackers) can support a
  theme by count and kind without titles.
- Ticket titles are current and may name a later release than the work.
- Long periods hit search caps and incomplete history; read the coverage section.

A busy quarter takes much longer to collect than a week.

Earlier reviews, weekly summaries and 1:1 notes are leads for continuity, not an
inventory or separate accomplishments: one continuing effort is one theme with
this period's new result. Scratch copies, rebased variants and generated test
repositories can repeat or fabricate history; check their purpose before turning
their commits into accomplishments, and do not drop a real tool effort just
because it lives in a test directory. Include user-supplied teaching records,
talks, design documents, adoption evidence and feedback; non-code work needs no
Jira or PR.

## What counts

SKILL.md's [credit rules](../SKILL.md#credit-only-what-happened) apply, plus:

- Use the user's own event times. A PR opened before the quarter can hold an
  in-quarter review; a merge after the cutoff is not an in-quarter delivery, and
  a ticket's current state is not its state at the cutoff.
- Separate building something, others using it, and its downstream benefit.
  Attribute adoption counts, speedups, savings and feedback to their source;
  never derive impact from PR volume or carry an old metric into a new period.
  A user's own account can support an answer; keep its uncertainty.
- Counts may describe the scope of a theme ("fixes across 14 repositories and
  three release branches"); they never decide which theme ranks higher.

## Write the briefing

Lead with major efforts, including work spread across projects or hidden among
many small PRs. Use a ranked list of themes with short sub-bullets for distinct
contributions. For each theme say what changed, how the user did it, and the
supported result. Include significant in-progress, review, investigation,
teaching and enabling work, not only shipped code. Keep the core Jira and PR/MR
links under the descriptions they support, chosen as in weekly mode; non-code
work can cite its own source or nothing.

"How" comes from evidence: the approach (automation replacing a manual step, a
pattern applied across many repositories or branches), collaboration (reviews and
discussion on others' work, listed in the digest), and enabling others (docs,
runbooks, tools or skills others run; say "built" unless use is evidenced). Offer
one "how" line per major theme and ask one short question for what the evidence
cannot show, such as who used it or what it unblocked.

```text
- Made release verification trustworthy enough that QE now signs off from its output.
  - Replaced manual stage checks with automated evidence gates for components and catalogs.
    - https://jira.example.test/browse/REL-200
    - https://github.com/example/release-tools/pull/114
  - Taught two teammates to run the stage process; they ran the 0.23.4 stage release.
- Ran CVE remediation across all supported branches: automated dependency-fix PRs for
  14 repositories and three release branches, and reviewed teammates' fixes.
  - https://github.com/example/automation/pull/41
- Reviewed and unblocked the CNI migration: design review, two rounds on the controller split.
```

## Answer starters

Then offer compact answer starters for the questions, without repeating the whole
briefing. Keep the actual questions as headings, answer in short first-person
bullets, and keep answers link-free unless the user asks for links (then add only
core links under the narrowest bullet). Preserve the user's voice without copying
boastful wording or unsupported impact from old answers.

```text
What accomplishments are you most proud of last quarter, and how did you accomplish them?
- I automated release verification so QE could sign off from evidence instead of manual checks,
  and taught two teammates to run stage releases themselves.

What are your top priorities for this quarter?
- Proposed (confirm or replace): finish the OCP 5.0 catalog rollout; move CVE fixes to the
  shared automation for the remaining repositories.
```

Priorities, feedback, enjoyment, aspirations and manager support need the
user's current input. Offer 3–5 proposed priorities from work still open at the
end of the period (open epics, unmerged authored PRs, upcoming releases), labelled
"Proposed (confirm or replace)". Past priorities and others' praise are context,
not commitments, and feedback can suggest a growth area without making it a goal.
Ask only what the next requested step needs. Mark unanswered questions by their
wording outside the paste-ready text, never present an unconfirmed section as
ready, and do not hold up the briefing for them.

## Check the result

Compare each answer with its question, period and evidence: personal attribution,
event dates, impact claims, important omissions, current intent and link choice.
The briefing should read as a reminder of major efforts, not a ticket inventory.
Mention material source gaps in one line outside the paste-ready text. Keep
captures, personal references and generated answers in private storage outside
tracked files; public tests and examples use synthetic people and work.
