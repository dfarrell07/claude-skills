# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a bulletproof "already operating at Principal" case for every Principal
Software Engineer criterion, drawn from the user's whole career. The skill is an
advocate. It makes the strongest case the evidence supports, and every claim traces
to real evidence; that traceability is what makes the case bulletproof. The only
criteria are the Principal level document. There is no nomination form and no
Senior assessment.

Today's skill has its own weaker collector. In July 2026 it credited a Kubernetes
rebase as authored work, and it cannot see its private data once installed. Its
replacement is a thin layer over `work-summary`: work-summary finds what the user
did and decides what counts as the user's own work; career-advocate keeps that
evidence for every period and argues it against the criteria.

## Layers

Each layer reads only the one below it, through the interface named here.

### 1. Collection (work-summary)

career-advocate calls work-summary's explicit-window collection as
`references/quarterly-reviews.md` shows. It resolves the scripts at
`$SKILL_DIR/../work-summary/scripts/` (both skills ship in one plugin) and finds
`SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`.

Two work-summary changes come first.

**1. Bound searches by when the work happened.** In explicit-window mode:

- add `created:<=until` to the GitHub `recent_*` searches and `created <= until`
  to the Jira `recent` query,
- select local commits by commit time from the window start with no end bound
  (`local_sources.py`), so a commit written in the window but amended or rebased
  later is still found.

Today a 2016 window sweeps every GitHub and Jira item from then to now, which
exhausts caps and rate limits. An item created after a window cannot hold activity
in it.

**2. One credit rule.** Today the digest's filters decide what counts as the
user's work (`Digest` events and merged PRs, `jira()`, `commits()` in
`candidates.py`). Extract them into `credited(data)`, used by the digest and
career-advocate. `references.active_items` and `active_tickets` stay as they
are: they set the collector's deliberately wider crawl, not credit.

`credited(data)` returns one record per unit of work. A unit is an item together
with the commits, ticket and backports its `related` links name. Title similarity
(`family()`) is not used, because it can join unrelated PRs, including other
people's. Each record holds facts only: all member links, title, the user's
actions, and their dates. Credited work is:

- **GitHub or GitLab:** an item with a substantive event by the user in the window
  (`candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels), or an
  authored PR merged in the window.
- **Jira:** a ticket the user commented on in the window, or one found by
  `updatedBy()` when that search's coverage is `ok`, never the broader fallback.
- **Local commits:** a commit by the user's verified email, *authored* in the
  window. Copies with the same author, author time and subject are one commit.
  An amended, cherry-picked or rebased copy is therefore credited once, in the
  period it was written, and never as recent work.

### 2. Evidence (career-advocate)

**Periods.** Fixed when the store is created. Years before the first run's year
are one period each: a coarser pass, using the same collector over a longer
window. From that year on, periods are quarters. A period never changes shape. The
open quarter is not stored or mapped.

**`scripts/store.py`** is the only code that reads or writes evidence:

- **`close(period)`** collects a period that has ended and writes
  `periods/<period>/data.json` (provenance) and `credited.json` (frozen
  `credited()` output). Coverage gaps are frozen with it as the period's stated
  limits. A closed period is never collected again unless the user asks, and each
  run lists periods with gaps so the user can decide.
- **`credited(period)`** returns a period's frozen records.
- **`resolve(ref)`**: any member link, or a recorded slug, is a reference. Records
  that share a member link, in any period, are one unit. `resolve()` returns that
  unit with its periods, latest in-window date and content: actions, or the
  recorded entry's text.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- Each entry names its source: a link, a named person, or "own account".
- Ask the user for entries era by era, never infer them from commits, and check
  that a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document and split each numbered
item in each of its sections (responsibilities, skills, and any metrics or goals)
into clauses. Review the split with the user. Write it into `mapping.json`, which
is from then on the only copy of the criteria, as `{key: {text, rating, argument,
refs}}`. A key names the section, item and clause, such as `Skills/10.d`. Keys and
text never change.

**Ratings.** Each clause gets an argument, its references and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

A claim about scope, reach, adoption or impact cites a source that shows it,
usually a recorded entry, because credited records show only what the user did.
Arguments answer the clause in its own words. They lead with the most recent work,
which shows the user operates at Principal now, and use earlier periods to show it
has been sustained.

**Each run:**

1. **Close.** In the background, newest first, `close()` every ended period that is
   not yet closed. An interrupted run resumes where it stopped.
2. **Fold.** Read each newly closed period's `credited(period)` and all of
   `recorded.md`. Add their references to clauses, and record the folded periods in
   `mapping.json`. Then rewrite the arguments and ratings.
3. **Review.** A reviewer subagent acts as a skeptical committee member. For each
   clause it sees only the clause text and `resolve()` of its references. It
   downgrades any claim or "exceeds" the evidence does not carry, and challenges
   arguments that rest only on the user's own account.
4. **Packet.** `scripts/packet.py` has two functions:
   - `check(mapping)` fails unless every clause has a rating and no two references
     resolve to the same unit.
   - `render(mapping)` runs only after a passing check and builds the packet from
     `mapping.json` and `resolve()` alone:
     - a case summary assembled from the reviewed clause ratings and arguments,
     - one section per section of the level document, in its order, with each
       item as a subsection holding its clause arguments, links and any next
       steps,
     - the periods' stated limits.

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

The first run asks for the Principal level document's path and the career start
year. Copy anything useful from `career-context.md` into `recorded.md` by hand.
Nothing in the store is committed; tests use synthetic people and criteria.
`/career-advocate` runs every step and writes a new packet.

## Steps

1. **work-summary.** Two changes, each with tests: the window bounds, then
   `credited()` with the digest switched over to it. Then time one real
   calendar-year collection and read its coverage. If it is unusable, fix
   work-summary further; never add a second collector here.
2. **career-advocate.** `store.py` and `packet.py` with unit tests on synthetic
   captures, then setup, fold and review.
3. **Removal.**
   - Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
     `reference/analysis-guide.md` and `tests/TEST-PLAN.md`.
   - Remove the `private/` gitignore entry.
   - Drop the `assess`, `meeting` and `scan` modes, which work-summary's quarterly,
     weekly and discovery modes cover.
   - Rewrite SKILL.md's description and argument hint, and update
     `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
