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
year are one period each, a coarser pass that uses the same collector over a longer
window. From that year on, periods are quarters. A period never changes shape, so
the `periods/` directory listing is the period scheme.

**Closing a period.** A period closes once it has ended and one collection of it
has finished. At that moment, write its capture and its credited work (below) to
`periods/<period>/`. It is never collected or reinterpreted again unless the user
asks. The open quarter is not stored or mapped.

**Running it.** Call work-summary's explicit-window collection as
`references/quarterly-reviews.md` shows. Resolve its scripts at
`$SKILL_DIR/../work-summary/scripts/` (both skills ship in one plugin), finding
`SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`. On the first run,
backfill in the background, newest first. Each period is written as it closes, so
an interrupted backfill resumes where it stopped.

**Two prerequisite work-summary changes:**

1. **Bound "recent" searches at the window end.** In explicit-window mode, add
   `created:<=until` to the GitHub `recent_*` searches and `created <= until` to the
   Jira `recent` query. Without it, a 2016 window sweeps every item from then to
   now and exhausts caps and rate limits. An item created after a window cannot
   hold activity in it.
2. **One credit rule.** Today what counts as the user's work is split between
   `references.active_items` and the `candidates` digest. Consolidate it into
   `credited(data)` and use it in both the digest and career-advocate. It returns
   one record per piece of credited work: reference, title, link, the user's
   actions, their dates, and the digest's line for it. Credited work is:
   - a GitHub or GitLab item with a substantive event by the user in the window
     (`candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels), or an
     authored PR merged in the window,
   - a Jira ticket the user commented on in the window, or found by `updatedBy()`
     when that search's coverage is `ok`, never the broader fallback,
   - a local commit by the user's verified email committed in the window, keyed by
     its identity (author, author time, subject), so amended, cherry-picked and
     rebased copies are one piece of work dated by author time.

### 2. Evidence (career-advocate)

The evidence is each closed period's `credited.json`, the frozen output of
`credited()`, and `recorded.md`. Captures (`data.json`) are kept only as
provenance. One small module, `scripts/store.py`, is the layer's only interface:

- `credited(period)`: that period's credited work,
- `resolve(ref)`: the work merged across every period it appears in, with its
  periods, latest in-window date (author time for commits) and content. Content is
  the digest line or the recorded entry's text,
- `check(mapping)`: fails unless every clause has a rating, every cited reference
  resolves, and no two cited references resolve to the same work.

`recorded.md` holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work
on Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- Each entry names its source: a link, a named person, or "own account".
- Ask the user for entries era by era, never infer them from commits, and check
  that a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document. Split each numbered item
in each of its sections (responsibilities, skills, and any metrics or goals) into
clauses, and review the split with the user. Write them into `mapping.json` as
`{key: {text, rating, argument, refs}}`. `mapping.json` is then the only copy of
the criteria. A key names the section, item and clause, such as `Skills/10.d`, so
the Collaboration and Business Impact items in both lists stay apart. Keys and
text never change.

**Ratings.** Each clause gets an argument, its references and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

Arguments answer the clause in its own words. They lead with the most recent work,
which shows the user operates at Principal now, and use earlier periods to show it
has been sustained.

**Each run:**

1. **Fold.** Read each newly closed period's `credited(period)` once, and all of
   `recorded.md`, which is small. Add their references to clauses. `mapping.json`
   lists the folded periods. Then rewrite arguments and ratings over everything
   cited.
2. **Review.** A reviewer subagent acts as a skeptical committee member. For each
   clause it sees only the clause text and `resolve()` of its references. It
   downgrades any claim or "exceeds" the evidence does not carry, and challenges
   arguments that rest only on the user's own account or lack recent work.
3. **Check.** Run `store.check(mapping)`.
4. **Render.** Only after a passing check, write the packet:
   - a one-page summary citing only the mapping's references,
   - one section per category in the document's order, with one subsection per
     item holding its clause arguments, links and any next steps,
   - the periods' coverage limits.

   The packet carries no activity statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
recorded.md
periods/<YYYY or YYYY-QN>/{data.json, credited.json}
mapping.json
packets/<date>.md
```

**Setup.** The first run asks for the Principal level document's path and the
career start year. Copy anything useful from `career-context.md` into
`recorded.md` by hand.

**Privacy.** Nothing in the store is committed. Tests use synthetic people and
criteria.

**Usage.** `/career-advocate` runs every layer and writes a new packet.

## Steps

1. **work-summary.** Two changes, each with tests: the window bound, then
   `credited()` with the digest switched over to it. Then time one real
   calendar-year collection and read its coverage. If it is unusable, fix
   work-summary further; never add a second collector here.
2. **career-advocate.** Build the store, periods, backfill, `store.py` with unit
   tests on synthetic captures, then setup, fold, review and render.
3. **Removal.** Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
   `reference/analysis-guide.md`, `tests/TEST-PLAN.md` and the `private/` gitignore
   entry. Drop the `assess`, `meeting` and `scan` modes, which work-summary's
   quarterly, weekly and discovery modes cover. Rewrite SKILL.md's description and
   argument hint. Update `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
