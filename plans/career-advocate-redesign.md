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

career-advocate uses work-summary only through its scripts, never by importing its
modules:

- `work-summary.sh --since … --until … --output data.json` collects a window,
- `render-report.sh --credited data.json` prints the credited work as JSON.

Scripts resolve at `$SKILL_DIR/../work-summary/scripts/`, because both skills ship
in one plugin. Find `SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`.

Three work-summary changes come first.

**1. Bound searches by when the work happened.** In explicit-window mode:

- Add `created:<=until` to the GitHub `recent_*` searches and `created <= until`
  to the Jira `recent` query. Today a 2016 window sweeps every item from then to
  now, exhausting caps and rate limits. An item created after a window cannot
  hold activity in it.
- Select local commits by commit time from the window start, with no end bound,
  in both the git call (`local_sources.py`) and the post-filter
  (`work_summary.py` after local collection). This keeps commits written in the
  window but amended or rebased later.

**2. `--author-email` (repeatable).** Sets the existing but unwired `author_emails`
in `collect_local`. Today only each repository's current `user.email` and the
GitHub profile count. Commits under the user's earlier addresses become mere name
matches, and most career-long local evidence is lost.

**3. One credit rule, `credited(data)`.** Extract it from the digest's filters
(`Digest` events and merged PRs, `jira()`, `commits()` in `candidates.py`). Switch
the digest to use it, and expose it as `render-report.sh --credited`.
`references.active_items` and `active_tickets` stay as they are: they set the
collector's deliberately wider crawl, not credit.

Credited work is:

- **GitHub or GitLab item:** a substantive event by the user in the window
  (`candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels), or an
  authored PR merged in the window.
- **Jira ticket:** the user commented on it in the window, or `updatedBy()`
  found it while that search's coverage is `ok`, never the broader fallback.
- **Local commit:** by one of the user's emails, *authored* in the window. Copies
  with the same author, author time and subject count as one commit. An amended,
  cherry-picked or rebased copy is therefore credited once, in the period it was
  written.

**Units of work.** `credited(data)` returns one record per unit of work: credited
items joined by their `related` links. Only credited links join a unit. Others'
PRs on a shared ticket stay context and never join or enter a unit, and neither
does title similarity. Each record holds facts only: member links, context links,
title, the user's actions, and their dates.

### 2. Evidence (career-advocate)

**Periods.** Fixed when the store is created. Years before the first run's year
are one period each: a coarser pass with the same collector over a longer window.
From that year on, periods are quarters. A period never changes shape. The open
quarter is not stored or mapped.

**`scripts/store.py`** is the only code that reads or writes evidence:

- **`close(period)`** collects a period that has ended, passing the user's emails
  from `config.json`. It writes `periods/<period>/data.json` and its credited JSON
  as `credited.json`, with coverage gaps frozen as stated limits. A closed period
  is never collected again unless the user asks. Each run lists periods with gaps
  so the user can decide. `data.json` lets `credited.json` be rebuilt after a
  credit-rule fix without collecting again.
- **`credited(period)`** returns a period's frozen records.
- **`resolve(ref)`** maps any member link or recorded slug to one canonical
  reference: the earliest member link of the unit across all periods, or the
  slug. It returns that unit's periods, latest in-window date and content: actions,
  or the recorded entry's text.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- Each entry names its source: a link, a named person, or "own account".
- Ask the user for entries era by era. Never infer them from commits. Check that
  a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document. Split each numbered
item in each of its sections (responsibilities, skills, and any metrics or goals)
into clauses, and review the split with the user. Write it to `mapping.json`, which
is then the only copy of the criteria, as
`{key: {text, rating, argument, refs}}`. A key names the section, item and clause,
such as `Skills/10.d`. Keys and text never change.

**Ratings.** Each clause gets an argument, a set of canonical references and one
rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

A claim about scope, reach, adoption or impact cites a source that shows it, usually
a recorded entry, because credited records show only what the user did. Arguments
answer the clause in its own words. They lead with the most recent work, which
shows the user operates at Principal now, and use earlier periods to show it has
been sustained. One unit may support several clauses.

**Each run:**

1. **Close.** In the background, newest first, `close()` every ended period that is
   not yet closed.
2. **Fold.** One newly closed period at a time, newest first: read
   `credited(period)` and add canonical references to clauses, then save
   `mapping.json` with the period marked folded. Fold all of `recorded.md` the
   same way. Then rewrite the arguments and ratings.
3. **Review.** A reviewer subagent acts as a skeptical committee member. For each
   clause it sees only the clause text and `resolve()` of its references. It:
   - downgrades any claim or "exceeds" the evidence does not carry,
   - challenges arguments that rest only on the user's own account.

   The main agent applies or rebuts each challenge in `mapping.json` and records
   that the review followed the latest fold.
4. **Render.** `scripts/packet.py render(mapping)` refuses unless:
   - the review followed the latest fold,
   - every clause has a rating,
   - every "meets" or "exceeds" has references,
   - every reference resolves,
   - every argument mentions only links and slugs among its own references.

   It then builds the packet from `mapping.json` and `resolve()` alone:
   - a case summary assembled from the reviewed clause ratings and arguments,
   - one section per section of the level document, in its order, with each item
     as a subsection holding its clause arguments, links and any next steps,
   - the periods' stated limits.

   The packet carries no activity statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
config.json        # the user's author emails, past and present
recorded.md
periods/<YYYY or YYYY-QN>/{data.json, credited.json}
mapping.json
packets/<date>.md
```

The first run asks for three things:

- the Principal level document's path,
- the career start year,
- every email address the user has committed under.

Copy anything useful from `career-context.md` into `recorded.md` by hand. Nothing
in the store is committed; tests use synthetic people and criteria.
`/career-advocate` runs every step and writes a new packet.

## Steps

1. **work-summary.** Make the three changes, each with tests: window bounds,
   `--author-email`, and `credited()` with `--credited` and the digest switched
   over to it. Then time one real calendar-year collection and read its coverage.
   If it is unusable, fix work-summary further; never add a second collector here.
2. **career-advocate.** Build `store.py` and `packet.py` with unit tests on
   synthetic captures, then setup, fold and review.
3. **Removal.**
   - Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
     `reference/analysis-guide.md` and `tests/TEST-PLAN.md`.
   - Remove the `private/` gitignore entry.
   - Drop the `assess`, `meeting` and `scan` modes; work-summary's quarterly,
     weekly and discovery modes cover them.
   - Rewrite SKILL.md's description and argument hint, and update
     `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
