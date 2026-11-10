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

Each layer reads only the one below it, through the interface named here.

### 1. Collection (work-summary)

**Period scheme.** Fixed when the store is created: years before the first run's
year are one period each (a coarser pass, using the same collector over a longer
window); from that year on, periods are quarters. A period never changes shape,
so the `periods/` directory listing is the period scheme.

**Closed periods.** A period is written to `periods/` once it has ended and one
collection of it has finished. It is never collected again unless the user asks.
Sources the collection could not reach stay in its coverage section as stated
limits, never as zero activity. The open quarter is not stored or mapped.

**Running it.** Call work-summary's explicit-window collection as
`references/quarterly-reviews.md` shows. Resolve its scripts at
`$SKILL_DIR/../work-summary/scripts/` (both skills ship in one plugin), finding
`SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`. On the first run,
backfill in the background, newest first. Each period is written as it finishes,
so an interrupted backfill resumes where it stopped.

**Two prerequisite work-summary changes:**

1. **Bound "recent" searches at the window end.** In explicit-window mode, add
   `created:<=until` to the GitHub `recent_*` searches and `created <= until` to the
   Jira `recent` query. Without it, a 2016 window sweeps every item from then to
   now and exhausts caps and rate limits. An item created after a window cannot
   hold activity in it.
2. **One credit rule.** Today what counts as the user's work is split between
   `references.active_items` and the `candidates` digest. Consolidate it into
   `credited(data)`, which returns one reference string per credited item, and use
   it in both the digest and career-advocate:
   - a GitHub or GitLab URL, for a substantive event by the user in the window
     (`candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels), or for
     an authored PR merged in the window,
   - a Jira URL, for the user's own comment in the window, or for `updatedBy()`
     only when that search's coverage is `ok`, never the broader fallback,
   - `repo@hash`, for a local commit by the user's verified email, committed in
     the window, with duplicates of the same author time and subject dropped.

### 2. Evidence (career-advocate)

The evidence is the closed periods' captures (`periods/<period>/data.json`) and
`recorded.md`. One small module, `scripts/evidence.py`, is the layer's only
interface:

- `references(period)`: `credited()` for that capture,
- `resolve(ref)`: title, link, period, source, and the date the work was done,
  which for a commit is its author time, so a rebased old commit is never
  presented as recent work.

`recorded.md` holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work
on Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference and is
  never edited.
- Each entry names its source: a link, a named person, or "own account".
- Ask the user for entries era by era, never infer them from commits, and check
  that a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** `levels/target.md` is a verbatim copy of the Principal level
document. At setup, split each numbered item in each of its sections
(responsibilities, skills, and any metrics or goals) into clauses once, write them
into `mapping.json` as `{key: {text, rating, argument, refs}}`, and review the
split with the user. A key gives the section, item and clause, such as
`Skills/10.d`, so the Collaboration and Business Impact items in both lists stay
apart. Keys and text never change after setup.

**Ratings.** Each clause gets an argument, its references and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

Arguments answer the clause in its own words. They lead with the most recent work,
by the date `resolve()` gives, which shows the user operates at Principal now.
Earlier periods show the level has been sustained.

**Each run:**

1. **Fold.** Read each newly closed period's digest (`render-report.sh
   --candidates`, generated on demand), and all of `recorded.md`, which is small.
   Add their references to clauses. `mapping.json` lists the folded periods. Then
   rewrite arguments and ratings over everything cited.
2. **Review.** A reviewer subagent acts as a skeptical committee member. It sees
   only each clause's text and its cited evidence, through `resolve()`. It
   downgrades any claim or "exceeds" the evidence does not carry, and challenges
   arguments that rest only on the user's own account or lack recent work.
3. **Check.** `scripts/check.py` fails unless:
   - every clause has a rating,
   - every reference resolves exactly once.
4. **Render.** Only after a passing check, write the packet:
   - a one-page summary citing only the mapping's references,
   - one section per category in the document's order, with one subsection per
     item holding its clause arguments, links and any next steps,
   - the coverage limits.

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
`career-context.md`, into the store by hand once. The first run asks for the
career start year.

**Privacy.** Nothing in the store is committed. Tests use synthetic people and
criteria.

**Usage.** `/career-advocate` runs every layer and writes a new packet.

**Removed modes.** `assess`, `meeting` and `scan` go, because work-summary's
quarterly, weekly and discovery modes cover them.

## Steps

1. **work-summary.** Two changes, each with tests: the window bound, then
   `credited()` with the digest switched over to it. Then time one real
   calendar-year collection and read its coverage. If it is unusable, fix
   work-summary further; never add a second collector here.
2. **career-advocate.** Build the store, periods, backfill, `evidence.py`,
   `check.py`, setup, fold, review and render, with unit tests for the two
   scripts.
3. **Removal.** Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
   `reference/analysis-guide.md`, `tests/TEST-PLAN.md` and the `private/` gitignore
   entry. Rewrite SKILL.md's description and argument hint. Update
   `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
