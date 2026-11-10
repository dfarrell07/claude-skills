# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a bulletproof "already operating at Principal" case for every Principal
Software Engineer criterion, drawn from the user's whole career. The skill is an
advocate. It makes the strongest case the evidence supports, and every claim traces
to real evidence; that traceability is what makes the case bulletproof. The only
criteria are the Principal level document. There is no nomination form and no
Senior assessment.

Today's skill has its own weaker collector. It credited a Kubernetes rebase as
authored work in July 2026, and it cannot see its private data once installed. Its
replacement is a thin layer over `work-summary`: work-summary finds and verifies
what the user did, and career-advocate keeps that evidence for every period and
argues it against the criteria.

## Layers

Each layer reads only the one below it.

### 1. Collection (work-summary)

**Period scheme.** Periods are fixed when the store is created:

- **Before the first run's year: one period per calendar year.** This is the
  coarser pass. It uses the same collector over a longer window, and the digest
  already groups long windows by workstream.
- **From that year on: one period per quarter, permanently.**

A period never changes shape, so the `periods/` directory listing is the period
scheme, and the end of a year needs no special handling.

**Closed periods.** A period is written to `periods/` only after it has ended and
every source in its capture reports `ok`, or the user accepts the gap. Once written,
it is never collected again. Anything else is collected again on the next run:

- an incomplete capture,
- the open quarter, which lives only in the run directory.

**Prerequisite work-summary change.** Today the "recent" searches have only a lower
date bound. A 2016 window would therefore sweep every item from then to now and
exhaust caps and rate limits. In explicit-window mode, bound them at the window end:

- GitHub `recent_*` searches: `created:<=until`,
- the Jira `recent` query: `created <= until`.

An item created after a window cannot hold activity inside it, and weekly and
quarterly runs are unaffected.

**Running it.**

- Call work-summary's explicit-window collection as
  `references/quarterly-reviews.md` shows.
- Resolve its scripts at `$SKILL_DIR/../work-summary/scripts/`, because both
  skills ship in one plugin. Find `SKILL_DIR` the way work-summary does, not with
  `BASH_SOURCE`.
- On the first run, backfill in the background, newest first. Each period is
  written as soon as it is done, so an interrupted backfill resumes where it
  stopped.

### 2. Evidence (career-advocate)

The evidence is the closed periods' captures (`periods/<period>/data.json`) and
`recorded.md`. Nothing is summarized per period. When a pass needs a period's
digest, it regenerates it with `render-report.sh --candidates`.

A reference is one of:

- **Captured work:** a URL, or `repo@hash` for a local commit, that a capture
  credits to the user. That means the capture records the user's own event on it
  inside that period. Items that appear only as context, such as mentions or review
  requests, do not count.
- **Recorded work:** a `## YYYY-MM-DD title` heading in `recorded.md`.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry names its source: a link, a named person, or "own account".
- Ask the user for entries era by era. Never infer them from commits.
- Search external services for the user's name only with the user's approval.
- Check that a link opens before citing it.

### 3. Mapping (career-advocate)

**Categories.** The Principal level document defines the structure: each `##`
section holding numbered items (responsibilities, skills, and any metrics or goals
section), then each item within it. Categories are read from the document, never
hardcoded. Items are keyed by section, such as `Skills/5`, because Collaboration
and Business Impact appear under both Responsibilities and Skills.

**Clauses.** Many items are compound. Skills/10 asks for design documents, blog
posts, documentation mentoring and conference talks. So `mapping.json` splits each
item into clauses (`Skills/10.d`) that together cover the item's full text.

**Ratings.** Each clause gets an argument, its references and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

**How arguments are written.**

- Lead with recent evidence, which shows the user operates at Principal now.
- Use earlier periods to show this has been sustained for years.
- Answer the clause in its own words.

**How the mapping is built.** The mapping accumulates. Each newly closed period, and
the open quarter on every run, is read once to add references to clauses. Arguments
and ratings are then rewritten over everything cited, so no pass ever has to read
the whole career at once.

**`scripts/check.py`** (about 50 lines) fails when:

- the mapping has a missing or extra item,
- a clause has no rating,
- a reference does not resolve under the rules above,
- a `recorded.md` heading is duplicated.

**Reviewer pass.** A reviewer subagent then acts as a skeptical committee member.
It receives each clause's text and cited evidence, plus an index of every period's
digest and of `recorded.md`. It:

- downgrades any claim or "exceeds" the evidence does not carry,
- challenges arguments that rest only on the user's own account or lack recent
  evidence,
- points to stronger evidence that went uncited.

**The packet**, rendered from the mapping:

- a one-page summary of the case,
- one section per category, in the document's order,
- within each section, one subsection per item with its clause arguments, links and
  any next steps.

Numbers come only from cited sources or a capture's coverage section. There are no
activity statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
levels/target.md
recorded.md
periods/<YYYY or YYYY-QN>/data.json
mapping.json
packets/<date>.md
```

**Setup.** Copy the Principal level document, and anything useful from
`career-context.md`, into the store by hand once.

**Privacy.** Nothing in the store is committed. Tests use synthetic people and
criteria.

**Usage.** `/career-advocate [start year]` runs every layer and writes a new
packet. The first run asks for the career start year if it isn't given.

**Removed modes.** `assess`, `meeting` and `scan` are dropped; work-summary's
quarterly, weekly and discovery modes cover them.

## Steps

1. **work-summary window bound.** Make it its own change, with tests. Then time one
   real calendar-year collection and read its coverage. If it is still unusable,
   fix work-summary further; never add a second collector here.
2. **Evidence.** Build the period scheme, the closed-period rule and the backfill.
   Write `check.py`'s reference resolution, with unit tests on synthetic captures.
3. **Mapping.** Finish `check.py`, then the incremental mapping pass, the reviewer
   pass and the packet.
4. **Removal.** Delete `scripts/`, `lenses/`, `reference/analysis-guide.md`,
   `tests/TEST-PLAN.md` and the `private/` gitignore entry. Rewrite SKILL.md's
   description and argument hint. Update `.claude/SKILLS.md` and the README.
5. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
