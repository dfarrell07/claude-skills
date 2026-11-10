# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a bulletproof "already operating at Principal" case for every Principal
Software Engineer criterion, drawn from the user's whole career. The skill is an
advocate. It makes the strongest case the evidence supports, and every claim traces
to real evidence; that traceability is what makes the case bulletproof. The only
criteria are the Principal level document. There is no nomination form and no
Senior assessment.

Rebuild `career-advocate` as a thin layer over `work-summary`: work-summary finds
and verifies what the user did; career-advocate keeps that evidence for every
period and maps it to the Principal criteria.

## Why change

- **Weaker collector.** `collect-contributions.sh` credits commits by author email
  with no rebase detection. Most of its searches cap silently, and failures become
  `[]`. The July 2026 draft counted a Kubernetes rebase as authored work and quoted
  PR and review totals that its own collection log does not contain.
- **Volume stands in for level.** Line counts, commit counts and "N/18 categories"
  show nothing about scope or level.
- **Private data is unreachable when installed.** `private/` lives in the skill
  directory, so the plugin's installed copy has no level document.
- **Evidence does not accumulate.** Each run re-collects 180 days. Mentoring,
  feedback and talks, which Principal weighs most, sat in free-text sections that
  stayed empty.

## Layers

Each layer reads only the one below it.

### 1. Collection (work-summary)

**Period scheme.** The whole career is split into fixed periods:

- **Current year: one period per quarter.** Recent work carries "already operating
  at", so it gets the finest detail.
- **Earlier years: one period per calendar year.** The same collector over a longer
  window is the coarser pass, and its digest already groups long windows by
  workstream.

**Collect each closed period once.** A period counts as closed once a capture
exists that was taken after the period ended. From then on it is never collected
again. Only open periods are refreshed.

**Prerequisite work-summary change.** In explicit-window mode, bound the "recent"
searches at the end of the window:

- GitHub: `created:<=until` (`github_sources.py` `recent_*`).
- Jira: `created <= until` (the `recent` query in `jira_sources.py`).

Today those searches have only a lower date bound. A 2016 window would therefore
sweep every item from 2016 to the present and exhaust caps and rate limits. The
change is safe because an item created after a window cannot hold activity inside
it. Weekly and quarterly runs are unaffected.

**Running it.** Call work-summary's explicit-window collection and its candidate
digest, as `references/quarterly-reviews.md` shows:

- **Script paths.** Resolve scripts at `$SKILL_DIR/../work-summary/scripts/`,
  because both skills ship together in the plugin.
- **`SKILL_DIR`.** Find it the way work-summary does, not with `BASH_SOURCE`,
  which is `.` in an inline block.
- **Backfill.** The first run collects many periods. Run them in the background,
  newest first, and save each period the moment it finishes, so an interrupted run
  resumes where it stopped.

**Sources the collector can't reach.** Older work often lives on Gerrit, mailing
lists, other Jira sites, talks and blog posts. It goes into recorded evidence
(see layer 2). The collector's coverage gaps stay stated limits, never zero
activity.

### 2. Evidence (career-advocate)

Each period has a directory, `periods/2019/` or `periods/2026-Q3/`, holding:

- **`data.json`**: the capture.
- **`analysis.json`**: the period's main efforts, chosen the way work-summary's
  quarterly briefing chooses them, with the Principal criteria in view. It uses
  work-summary's analysis schema, so `render --analysis` proves every citation is
  in the capture.

An evidence reference is one of three kinds:

- **A theme:** `2019/<id>`. Each theme gets an `id` slug, unique within its period.
  work-summary's validator ignores fields it does not know (`work_summary.py`
  `check_theme`).
- **A captured item:** `2019/<url>`. Some evidence never becomes a theme: reviews
  of others' work, review-based mentoring, cross-team discussion. Principal weighs
  exactly these, so the mapping can cite them straight from the capture.
- **A recorded entry:** its heading in `recorded.md`.

`recorded.md` holds evidence no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older
upstream work. Each entry is:

- a `## YYYY-MM-DD title` heading, which is also its reference,
- its source: a link, a named person, or "own account".

How to fill it:

- Ask the user for entries, era by era. Never infer them from commits.
- Never search external services for the user's name without the user's approval.
- Check that a link opens before citing it.

### 3. Mapping (career-advocate)

**Categories.** The level document's own categories are the structure: every `##`
section holding numbered items (responsibilities, skills, and any metrics or goals
section), then each numbered item within it. They are read from the document, not
hardcoded, so the case follows the ladder as written. Items are keyed by section
(`Skills/5`) because both Responsibilities and Skills contain Collaboration and
Business Impact.

**Clauses.** Many items are compound. Skills/10 alone asks for design documents,
blog posts, documentation mentoring and conference talks. So `mapping.json` splits
each item into its clauses (`Skills/10.d`) and rates each clause on its own.

**Ratings.** Each clause gets an argument, cited references and one rating:

- **exceeds**: cited evidence goes beyond the clause in scope, reach or duration,
- **meets**: cited evidence shows the clause as written,
- **not yet shown**: plus the specific evidence or action that would show it.

**How arguments are written.**

- Lead with recent evidence, to show the user operates at Principal now.
- Cite earlier periods to show that has been sustained for years. An argument that
  cites themes from several periods is the multi-year arc.
- Use the clause's own words, so the committee sees each requirement answered
  directly.

**`scripts/check.py`** (about 60 lines) fails when:

- an item has no clauses, or a clause has no rating,
- the mapping has a missing or extra item,
- a reference resolves to nothing,
- a theme `id` or `recorded.md` heading is duplicated.

It also flags two weak spots: arguments resting only on "own account" entries, and
arguments with no evidence from the last two years.

**Reviewer pass.** A reviewer subagent then acts as a skeptical committee member.
It gets each clause's text, the cited evidence, and an index of every period's
themes, every period's digest and the `recorded.md` headings. It then:

- downgrades any claim it cannot trace, and any "exceeds" the evidence does not
  carry,
- checks that each item's clauses cover its full text,
- challenges arguments a committee would dispute,
- points to stronger uncited evidence.

This pass replaces the four lens subagents and targets July's failure,
overclaiming.

**Packet.** The packet is rendered from the mapping. Its sections roughly follow
the level document's categories:

1. a one-page summary of the case,
2. one section per category, in the document's order, holding one subsection per
   item with its clause arguments and links (closely overlapping items, such as the
   two Collaboration entries, may share evidence but each is answered),
3. the "not yet shown" clauses, each with its next step.

Numbers come only from cited sources or a capture's coverage section. The packet
carries no activity statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
levels/target.md
recorded.md
periods/<YYYY or YYYY-QN>/{data.json, analysis.json}
mapping.json
packets/<date>.md
```

**Setup.** Copy the Principal level document into the store by hand once, along
with anything useful from `career-context.md`. Include every category the source
ladder defines for Principal, such as metrics or goals, not only responsibilities
and skills.

**Privacy.** Nothing in the store is committed. Tests use synthetic people and
criteria.

**Usage.** `/career-advocate [start year]` does everything in one run:

1. collects any missing periods and refreshes the open ones,
2. writes the mapping,
3. runs the review,
4. writes the packet, with its "not yet shown" items and their next steps.

The first run asks for the career start year if it is not given. After that, the
collected periods mark how far back the store goes.

**Removed modes.** `assess`, `meeting` and `scan` go, because work-summary's
quarterly, weekly and discovery modes cover them.

## Steps

1. **work-summary window bound.** Add the upper `created` bound in
   explicit-window mode as its own work-summary change, with tests. Then time one
   real calendar-year collection and read its coverage. If it is still unusable,
   fix that in work-summary too; never add a second collector here.
2. **Evidence.** Build the period layout, the closed-period rule, the backfill, the
   `analysis.json` theme `id`s and `recorded.md`. Add a contract test that runs
   work-summary's renderer on
   `skills/work-summary/tests/fixtures/quarterly-review.json` with a theme carrying
   `id`, so a work-summary change that breaks this skill fails CI.
3. **Mapping.** Write `check.py` with unit tests on a synthetic ladder and evidence,
   then the mapping pass, the reviewer pass and the packet template.
4. **Removal.** Delete `scripts/`, `lenses/`, `reference/analysis-guide.md`,
   `tests/TEST-PLAN.md` and the `private/` gitignore entry. Rewrite SKILL.md's
   description and argument hint. Update `.claude/SKILLS.md` and the README.
5. **Qualification.** Build the real career packet privately and compare it with
   the July draft. Pass means:
   - every claim is traceable,
   - nothing is inflated by rebases or volume,
   - every clause is argued, or its gap is named with a next step.
