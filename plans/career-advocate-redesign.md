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

- `work-summary.sh --since … --until … --author-email … --output data.json`
  collects a window.
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
GitHub profile count, so commits under the user's earlier addresses are mere name
matches and most career-long local evidence is lost.

**3. One credit rule, `credited(data)`.** Extract it from the digest's filters
(`Digest` events and merged PRs, `jira()` and `commits()` in `candidates.py`),
switch the digest to it, and expose it as `--credited`.
`references.active_items` and `active_tickets` stay as they are: they set the
collector's deliberately wider crawl, not credit.

It returns one record per unit of work, holding facts only: member links, context
links, title, the user's actions, and their dates. Units are built only from the
user's own credited work and only from what the capture holds. Within one
repository, credited items sharing the digest's `family()` title key form one unit,
so a fix and its per-branch backports count once. A local commit joins a PR unit
when its subject matches. In a repository whose GitHub remote was captured, an
unmatched commit is context, never a unit of its own, because squash and rebase
merges leave no other link. Tickets and other people's PRs are context and never
join units. Credited work is:

- **GitHub or GitLab:** an item with a substantive event by the user in the window
  (`candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels), or an
  authored PR merged in the window.
- **Jira:** a ticket with the user's own comment or field change
  (`public_field_events`) inside the window. `updatedBy()` only finds candidates.
- **Local commits:** a commit by one of the user's emails, *authored* in the
  window. Copies with the same author, author time and subject are one commit, so
  an amended, cherry-picked or rebased copy is credited once, in the period it was
  written.

### 2. Evidence (career-advocate)

**Periods.** `config.json` holds `start_year`, `quarterly_from` and the user's
`emails`. Each year before `quarterly_from` is one period: a coarser pass with the
same collector over a longer window. After that, periods are quarters. The open
quarter is neither stored nor mapped.

**`scripts/store.py`** is the only code that reads or writes evidence:

- **`close(period)`** collects a period that has ended and writes
  `periods/<period>/data.json`, with coverage gaps as the period's stated limits.
  A closed period is never collected again unless the user asks. Each run lists
  the periods with gaps so the user can decide.
- **`index()`** runs `--credited` once per closed period, a few seconds each,
  and builds a single index from member link or slug to records. A later fix to
  the credit rule then applies to every period without recollecting. The index
  also returns a hash of each period's credited output and of `recorded.md`.
- **`resolve(ref)`** looks a member link or slug up in the index. It returns every
  matching record across periods, or the recorded entry with its source kind,
  each with its dates. Context links never resolve, so others' work cannot be
  cited.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- Its first line is `Source:` followed by a link, a named person, or "own account".
- Ask the user for entries era by era. Never infer them from commits. Check that a
  link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document and split each numbered
item in each of its sections (responsibilities, skills, and any metrics or goals)
into clauses. Review the split with the user. Write it to `mapping.json`, which is
then the only copy of the criteria, as `{key: {text, rating, argument}}`. A key
names the section, item and clause, such as `Skills/10.d`. Keys and text never
change. A clause's references are the links and slugs written in its argument.

**Ratings.** Each clause gets an argument and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

A claim about scope, reach, adoption or impact cites a source that shows it, usually
a recorded entry with a named source, because credited records show only what the
user did. Arguments answer the clause in its own words. They lead with the most
recent work, which shows the user operates at Principal now, and use earlier
periods to show it has been sustained. One unit may support several clauses.

**Each run:**

1. **Close.** In the background, newest first, `close()` every ended period that
   is not yet closed.
2. **Fold.** `mapping.json` keeps the hash of each evidence source it last folded:
   each period's credited output, and `recorded.md`. One source at a time, newest
   first, fold every source whose hash is missing or changed. Read its records, add
   references to clause arguments, and save. A newly closed period, an edited
   `recorded.md` and a credit-rule fix are all handled this way. Then rewrite
   arguments and ratings.
3. **Review.** A reviewer subagent acts as a skeptical committee member. For each
   clause it sees only the clause text, the argument and `resolve()` of its
   references. It downgrades anything that breaks the rating rules above. The main
   agent may answer a challenge only by editing the argument, for example adding
   evidence, and every edit is reviewed again. When nothing is challenged, the
   reviewer stamps each clause with a hash of exactly what it saw: text, rating,
   argument and resolved evidence.
4. **Render.** `scripts/packet.py render(mapping)` refuses unless:
   - every clause's stamp matches a fresh hash of the same things, which covers
     edits, new evidence and credit-rule changes,
   - every reference resolves,
   - every "meets" or "exceeds" cites at least one reference,
   - no "exceeds" rests only on "own account" entries.

   It then builds the packet from `mapping.json` and `resolve()` alone, listing
   each unit once per clause:
   - a case summary assembled from the reviewed ratings and arguments,
   - one section per section of the level document, in its order, with each item
     as a subsection holding its clause arguments, links and any next steps,
   - the periods' stated limits.

   The packet carries no activity statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
config.json        # emails, start_year, quarterly_from
recorded.md
periods/<YYYY or YYYY-QN>/data.json
mapping.json
packets/<date>.md
```

The first run asks for the Principal level document, the career start year and
every email address the user has committed under. It sets `quarterly_from` to the
current year. Copy anything useful from `career-context.md` into `recorded.md` by
hand. Nothing in the store is committed; tests use synthetic people and criteria.
`/career-advocate` runs every step and writes a new packet.

## Steps

1. **work-summary.** Make the three changes, each with tests: window bounds,
   `--author-email`, and `credited()` with `--credited` and the digest switched to
   it. Then time one real calendar-year collection and read its coverage. If it is
   unusable, fix work-summary further; never add a second collector here.
2. **career-advocate.** Build `store.py` and `packet.py` with unit tests on
   synthetic captures, then setup, fold and review.
3. **Removal.** Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
   `reference/analysis-guide.md`, `tests/TEST-PLAN.md` and the `private/` gitignore
   entry. Drop the `assess`, `meeting` and `scan` modes; work-summary's quarterly,
   weekly and discovery modes cover them. Rewrite SKILL.md's description and
   argument hint, and update `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July draft. It passes when every claim is traceable, nothing is inflated by
   rebases or volume, and every clause is argued or its gap is named with a next
   step.
