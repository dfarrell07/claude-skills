# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a bulletproof "already operating at Principal" case for every Principal
Software Engineer criterion, drawn from the user's whole career. The skill is an
advocate. It makes the strongest case the evidence supports, and every claim traces
to real evidence; that traceability is what makes the case bulletproof. The only
criteria are the Principal level document. There is no nomination form and no
Senior assessment.

The skill becomes a thin layer over `work-summary`. work-summary finds what the
user did, decides what counts as the user's own work, and groups it into units.
career-advocate keeps that evidence for every period and argues it against the
criteria.

## Layers

Each layer reads only the one below it, through the interface named here.

### 1. Collection (work-summary)

career-advocate uses work-summary only through its scripts, never by importing its
modules:

- `work-summary.sh --since … --until … --author-email … --output data.json`
  collects a window and writes the capture atomically.
- `render-report.sh --credited p1.json p2.json …` prints, as JSON, the credited
  work across all the given captures, grouped into units.

Scripts resolve at `$SKILL_DIR/../work-summary/scripts/`, because both skills ship
in one plugin. Find `SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`.

Three work-summary changes come first.

**1. Bound searches by when the work happened.** In explicit-window mode:

- **GitHub `recent_*` searches:** put `created:<=until` in the root query. If that
  overflows, partition the base query by `created:a..b` over epoch to `until`
  (`github_sources.py` `search`), so the partition totals match the root.
- **Jira `recent` query:** add `created <= until`.
- **Local commits:** select by commit time from the window start, with no end
  bound, in both the git call (`local_sources.py`) and the post-filter
  (`work_summary.py` after local collection). That keeps commits written in the
  window but amended or rebased later.

Today a 2016 window sweeps every item from then to now, exhausting caps and rate
limits. An item created after a window cannot hold activity in it.

**2. `--author-email` (repeatable).** Sets the existing but unwired `author_emails`
in `collect_local`. Today only each repository's current `user.email` and the
GitHub profile count. Commits under the user's earlier addresses are mere name
matches, so most career-long local evidence is lost.

**3. One credit rule and one grouping.** Extract them from the digest's filters
(`Digest` events and merged PRs, `jira()` and `commits()` in `candidates.py`) into
`credited()` and `units()`. Then:

- the digest renders from them, including its family lines,
- `--credited` exposes them.

`references.active_items` and `active_tickets` stay as they are: they set the
collector's deliberately wider crawl, not credit.

**Credited work:**

- **GitHub or GitLab:** an item with a substantive event by the user in the window,
  or an authored PR merged in the window. Substantive means
  `candidates.SUBSTANTIVE`, so not mentions, subscriptions or labels. A comment
  that `note()` empties out, such as `/retest` or `/lgtm`, does not count.
- **Jira:** a ticket with the user's own comment or field change
  (`public_field_events`) inside the window. `updatedBy()` only finds candidates.
- **Local commits:** a commit by one of the user's emails, *authored* in the
  window. Copies with the same author, author time and subject are one commit,
  credited once, in the period it was written. Deduplication prefers a copy
  reachable from a remote-tracking ref.

**Units** are connected components over the credited work of all given captures.
Two items join when they share a member link, or when either of these holds:

- They are the user's authored PRs in the same repository with the same `family()`
  title key, created within 90 days of each other. That window covers a fix and
  its backports without joining same-named releases years apart.
- One is a local commit whose subject matches the other, a PR.

A credited ticket, and a credited review or comment on someone else's PR, are each
their own unit and never join authored work. Leadership through review and
coordination is cited this way.

**What a unit holds.** Facts only:

- member links: a commit gets a web URL only when it is reachable from a
  remote-tracking ref, and otherwise a local reference marked "not publicly
  verifiable",
- title, which sensitive Jira tickets omit,
- for authored PRs, the description line (`summary_line`),
- each of the user's actions with its date, its `note()` excerpt, or the Jira field
  names it changed.

### 2. Evidence (career-advocate)

**Periods.** `config.json` holds `start_year`, `quarterly_from` and the user's
`emails`:

- Each year before `quarterly_from` is one period, a coarser pass with the same
  collector over a longer window.
- From `quarterly_from` on, periods are quarters.
- The open quarter is not stored or mapped.

**`scripts/store.py`** is the only code that reads or writes evidence:

- **`close(period)`** collects a period that has ended. It writes
  `periods/<period>/data.json` through `--output`, so a period is either complete
  or absent. Coverage gaps become the period's stated limits. A closed period is
  never collected again unless the user asks. Each run lists periods with gaps so
  the user can decide.
- **`units()`** runs `--credited` once over every closed period's capture, then
  adds the `recorded.md` entries with their source kind. A later credit-rule fix
  thus applies to every period without recollecting.
- **`resolve(ref)`** returns the unit or entry that a member link or slug names.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- The entry's first line is `Source:` and a link, a named person, or "own
  account".
- Ask the user for entries era by era. Never infer them from commits. Check that a
  link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document. Split each numbered item
in each of its sections (responsibilities, skills, and any metrics or goals) into
clauses, and review the split with the user. Write it to `mapping.json`, which is
then the only copy of the criteria, as `{key: {text, rating, argument}}`.

- A key names the section, item and clause, such as `Skills/10.d`.
- Keys and text never change.
- A clause's references are the links and slugs written in its argument.

**Ratings.** Each clause gets an argument and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

Every reference must resolve. A "meets" or "exceeds" cites at least one reference.
An "exceeds" cannot rest only on "own account" entries or on work that is "not
publicly verifiable". A claim about scope, reach, adoption or impact cites a source
that shows it, usually a recorded entry with a named source, because units show
only what the user did.

Arguments answer the clause in its own words. They lead with the most recent work,
which shows the user operates at Principal now, and use earlier periods to show it
has been sustained. One unit may support several clauses.

**`/career-advocate` runs every step and writes a new packet:**

1. **Close.** In the background, newest first, `close()` every ended period that
   is not yet closed.
2. **Fold.** `mapping.json` keeps the set of actions already folded, each as
   member link, action and date, plus the slugs already folded. A unit or entry
   with anything unseen is folded again, so a PR that gained a merge in a later
   period is reread.
   - Read such units newest first, in batches that fit in context.
   - Add references to clause arguments, and save after each batch.
   - Then rewrite arguments and ratings.
3. **Review.** A reviewer subagent acts as a skeptical committee member.
   - For each clause it sees only the clause text, the argument and `resolve()`
     of its references.
   - It downgrades anything that breaks the rating rules above.
   - The main agent may answer a challenge only by editing the argument, for
     example by adding evidence. Every edit is reviewed again.
   - When nothing is challenged, the reviewer stamps each clause with a hash of
     exactly what it saw: text, rating, argument and resolved evidence.
4. **Render.** `scripts/packet.py render(mapping)` mechanically enforces the rating
   rules' checkable parts. It refuses unless:
   - every clause's stamp matches a fresh hash,
   - every reference in every argument resolves,
   - every "meets" or "exceeds" cites a reference,
   - no "exceeds" rests only on "own account" or "not publicly verifiable"
     evidence.

   It then builds the packet from `mapping.json` and `resolve()` alone, listing
   each unit once per clause:
   - a case summary assembled from the reviewed ratings and arguments,
   - one section per section of the level document, in its order, with each item
     as a subsection holding its clause arguments, links and any next steps,
   - the periods' stated limits.

   The packet carries no activity statistics.

## Store

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
config.json
recorded.md
periods/<YYYY or YYYY-QN>/data.json
mapping.json
packets/<date>.md
```

The first run asks for the Principal level document, the career start year and
every email address the user has committed under. It sets `quarterly_from` to the
current year. Copy anything useful from `career-context.md` into `recorded.md` by
hand. Nothing in the store is committed. Tests use synthetic people and criteria.

## Steps

1. **work-summary.** Each change comes with tests:
   - the window bounds,
   - `--author-email`,
   - `credited()` and `units()` with multi-capture `--credited`, with the digest
     switched to them.

   Captures record a format version. A saved synthetic capture for each version is
   tested against `--credited`, so stored periods keep working as the collector
   changes. Then time one real calendar-year collection and read its coverage. If
   it is unusable, fix work-summary further; never add a second collector here.
2. **career-advocate.** Build `store.py` and `packet.py` with unit tests on
   synthetic captures. Then build setup, fold and review.
3. **Removal.**
   - Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
     `reference/analysis-guide.md` and `tests/TEST-PLAN.md`.
   - Remove the `private/` entry from `.gitignore`.
   - Drop the `assess`, `meeting` and `scan` modes; work-summary's quarterly,
     weekly and discovery modes cover them.
   - Rewrite SKILL.md's description and argument hint, and update
     `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July 2026 draft, which credited a Kubernetes rebase as authored work. It
   passes when:
   - every claim is traceable,
   - nothing is inflated by rebases or volume,
   - every clause is argued or its gap is named with a next step.
