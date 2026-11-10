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
replacement is a thin layer over `work-summary`: work-summary finds what the user
did and decides what counts as the user's own work; career-advocate keeps that
evidence for every period and argues it against the criteria.

## Layers

Each layer reads only the one below it.

### 1. Collection (work-summary)

**Period scheme.** The periods are fixed when the store is created:

- years before the first run's year get one period each, a coarser pass that
  uses the same collector over a longer window,
- from that year on, every period is a quarter.

A period never changes shape, so the `periods/` directory listing is the period
scheme.

**Closed periods.** A period is written to `periods/` after it has ended and one
collection of it has finished. It is never collected again unless the user asks.
Sources the collection could not reach are recorded in its coverage section, and
the packet states them as limits, never as zero activity. The open quarter is not
stored or mapped.

**Running it.**

- Call work-summary's explicit-window collection as
  `references/quarterly-reviews.md` shows.
- Resolve its scripts at `$SKILL_DIR/../work-summary/scripts/`; both skills ship in
  one plugin. Find `SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`.
- On the first run, backfill in the background, newest first. Write each period as
  soon as it finishes, so an interrupted backfill resumes where it stopped.

**Two prerequisite work-summary changes:**

1. **Bound "recent" searches at the window end.** In explicit-window mode, add
   `created:<=until` to the GitHub `recent_*` searches and `created <= until` to the
   Jira `recent` query. Without an upper bound, a 2016 window sweeps every item
   from then to now and exhausts caps and rate limits. An item created after a
   window cannot hold activity inside it.
2. **Make one function decide credit.** Today the rules for what counts as the
   user's own work are split between `references.active_items` and the
   `candidates` digest (`SUBSTANTIVE`, rebase detection). Consolidate them into
   `credited(data)`, used by both work-summary's digest and career-advocate. It
   returns:
   - items with a substantive event by the user inside the window, which
     excludes mentions, subscriptions and labels,
   - local commits whose author time falls inside the window, which excludes
     rebases, cherry-picks and backup branches,
   - Jira tickets the user commented on or changed.

### 2. Evidence (career-advocate)

The evidence is the closed periods' captures (`periods/<period>/data.json`) and
`recorded.md`. A reference is one of:

- **Captured work:** a URL, or `repo@hash` for a local commit, that `credited()`
  returns for the cited period.
- **Recorded work:** a `## YYYY-MM-DD title` heading in `recorded.md`.

`recorded.md` holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites. Each entry names its source: a link, a
named person, or "own account". Ask the user for entries era by era, never infer
them from commits, and check that a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, split each numbered item in `levels/target.md` into
clauses once and annotate them in that file. A clause key gives the section, item
and clause, such as `Skills/10.d`. Sections are read from the document (its
responsibilities, skills, and any metrics or goals section), never hardcoded.
Section keys keep apart the Collaboration and Business Impact items that appear in
both lists. The annotated file is reviewed with the user once, so clause keys stay
stable.

**Ratings.** For each clause, `mapping.json` holds an argument, its references and
one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

Arguments answer the clause in its own words. They lead with the most recent
periods, which show the user operates at Principal now, and use earlier periods to
show it has been sustained.

**Each run:**

1. **Fold.** Read each newly closed period's digest once and add its references to
   clauses. `mapping.json` lists the periods it has folded, so no pass ever reads
   the whole career at once. Then rewrite arguments and ratings over everything
   cited.
2. **Review.** A reviewer subagent acts as a skeptical committee member. It sees
   only each clause's text and the evidence that clause cites. It downgrades any
   claim or "exceeds" the evidence does not carry, and challenges arguments that
   rest only on the user's own account or lack recent evidence.
3. **Check.** `scripts/check.py` (about 40 lines) fails when:
   - the mapping's clause keys differ from the annotated criteria,
   - a clause has no rating,
   - a reference does not resolve,
   - a `recorded.md` heading appears twice.
4. **Render.** Render runs only after a passing check, and writes the packet:
   - a one-page summary citing only references from the mapping,
   - one section per category in the document's order, each with one subsection
     per item holding its clause arguments, links and any next steps,
   - a statement of the coverage limits.

   Numbers come only from cited sources or coverage sections. The packet has no
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
packet. The first run asks for the career start year if none is given.

**Removed modes.** `assess`, `meeting` and `scan` go, because work-summary's
quarterly, weekly and discovery modes cover them.

## Steps

1. **work-summary changes.** Each is its own change with tests: the window bound,
   then `credited()`, with the digest switched over to it. Then time one real
   calendar-year collection and read its coverage. If it is unusable, fix
   work-summary further; never add a second collector here.
2. **Evidence.** The period scheme, closing, backfill, and `check.py`'s reference
   resolution, with unit tests on synthetic captures.
3. **Mapping.** Clause annotation, the rest of `check.py`, the fold, review and
   render steps.
4. **Removal.** Delete `scripts/`, `lenses/`, `reference/analysis-guide.md`,
   `tests/TEST-PLAN.md` and the `private/` gitignore entry. Rewrite SKILL.md's
   description and argument hint. Update `.claude/SKILLS.md` and the README.
5. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
