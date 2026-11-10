# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a bulletproof "already operating at Principal" case for every Principal
Software Engineer criterion, drawn from the user's whole career. The skill is an
advocate. It makes the strongest case the evidence supports, and every claim traces
to real evidence; that traceability is what makes the case bulletproof. The only
criteria are the Principal level document. There is no nomination form and no
Senior assessment.

The skill becomes a thin layer over `work-summary`. work-summary finds what the user
did, decides what counts as the user's own work, and groups it into units.
career-advocate keeps that evidence for every period and argues it against the
criteria.

## Layers

Each layer reads only the one below it, through the interface named here.

### 1. Collection (work-summary)

career-advocate uses work-summary only through its scripts, never by importing its
modules:

- `work-summary.sh --since … --until … --author-email … --root … --output data.json`
  collects a window and writes the capture atomically.
- `render-report.sh --candidates --json p1.json p2.json …` is the only reader of
  captures. It is the existing digest, extended to accept several captures and
  print JSON. It decides credit for each capture separately, against that capture's
  own window, and keeps only the small credited sets. It then groups those sets
  into units. It returns each capture's window, roots and coverage limits, plus the
  units.

Scripts resolve at `$SKILL_DIR/../work-summary/scripts/`, because both skills ship
in one plugin. Find `SKILL_DIR` the way work-summary does, not with `BASH_SOURCE`.

Four work-summary changes come first.

**1. Bound searches by when the work happened.** In explicit-window mode:

- **GitHub `recent_*` searches:** put `created:<=until` in the root query. If that
  overflows, partition the base query by `created:a..b` over epoch to `until`
  (`github_sources.py` `search`), so the partition totals match the root.
- **Jira `recent` query:** add `created <= until`.
- **Local commits:** select by commit time from the window start with no end bound,
  in both the git call (`local_sources.py`) and the post-filter (`work_summary.py`
  after local collection).

Today a 2016 window sweeps every item from then to now, exhausting caps and rate
limits. An item created after a window cannot hold activity in it.

**2. `--author-email` (repeatable).** Sets the existing but unwired `author_emails`
in `collect_local`. Today only each repository's current `user.email` and the
GitHub profile count. Commits under the user's earlier addresses become mere name
matches, and most career-long local evidence is lost.

**3. Commit web URLs at collection time.** For each remote, run
`git rev-list --remotes=<name>` once. Store a commit's web URL only when a remote
whose set contains the commit is on GitHub or GitLab, whose URL format is known.
Captures drop refs today (`work_summary.py` after local collection), and the
checkout may be gone years later, so this can only be decided while collecting.

**4. One credit rule and one grouping.** Extract them from the digest's filters
(`Digest` events and merged PRs, `jira()` and `commits()` in `candidates.py`) into
`credited()` and `units()`. Both the markdown and JSON digests render from them.
`references.active_items` and `active_tickets` stay as they are: they set the
collector's deliberately wider crawl, not credit.

**Credited work:**

- **GitHub or GitLab:** an item with a substantive event by the user in the window
  (`candidates.SUBSTANTIVE`), or an authored PR merged in the window.
  - Mentions, subscriptions and labels do not count.
  - Neither does a comment that `note()` empties out, such as `/retest`.
- **Jira:** a ticket with the user's own comment or field change
  (`public_field_events`) inside the window. `updatedBy()` only finds candidates.
- **Local commits:** a commit by one of the user's emails, *authored* in the
  window.
  - Copies with the same author, author time and subject are one commit.
  - It is credited once, in the period it was written.
  - Deduplication prefers a copy with a web URL.

**Units** are connected components over the credited work of all given captures.
Two items join when:

- they share a member link,
- they are the user's authored PRs in one repository, with the same `family()`
  title key, created within 90 days of the group's earliest PR. That covers a fix
  and its backports, never a chain of monthly releases.
- one is a local commit whose remote is the other's repository and whose subject
  matches the other (a PR), authored no more than 90 days before that PR's merge
  or close. Commits usually predate their PR, and rebases keep the author time.

A credited ticket, and a credited review or comment on someone else's PR, are each
their own unit. They never join authored work. Review and coordination are cited
this way.

**Unit contents.** A unit holds facts only:

- member links,
- title,
- for authored PRs, the description line (`summary_line`),
- the user's actions, each with its date and either its `note()` excerpt or the Jira
  field names it changed.

A sensitive Jira ticket keeps only its key, URL, action kinds and dates: never a
title or an excerpt.

### 2. Evidence (career-advocate)

**Periods.** `config.json` holds `start_year`, `quarterly_from`, the user's `emails`
and repository `roots`. Each year before `quarterly_from` is one period: a coarser
pass with the same collector over a longer window. From then on, periods are
quarters.

**`scripts/store.py`** is the only code that reads or writes evidence. It never
opens a capture itself.

- **`close(period)`** collects a period one full quarter after it ends, so commits
  rebased, merged or pushed in that time are included. It writes
  `periods/<period>/data.json` through `--output`, so a period is either complete
  or absent.
  - If GitHub or local Git reports `unavailable`, the capture is discarded and the
    period stays open for the next run.
  - A `partial` source, which local Git reports for routine warnings, is kept as a
    stated limit.
  - A closed period is never collected again unless the user asks.
- **`snapshot()`** runs once per run, after closing:
  - It collects the current window, from the last closed period's end to today,
    into `periods/current/data.json`, overwritten each run. Its units are marked
    provisional, which keeps the "operating now" evidence current.
  - It runs the JSON digest once over every capture and adds the `recorded.md`
    entries.
  - It writes `units.json`: the units with a content hash each, each period's
    limits, and the ended periods since `start_year` with no capture yet.

  Every later step reads only `units.json`, so all hashes in a run come from one
  snapshot. A later credit-rule fix applies to every period without recollecting.
- **`resolve(ref)`** returns, from `units.json`, the unit or entry that any member
  link or slug names.

**`recorded.md`** holds what no collector sees: mentoring, feedback quotes, talks,
design documents, adoption by other teams, decisions influenced, and older work on
Gerrit, mailing lists or other Jira sites.

- Each entry is `## YYYY-MM-DD {#slug} title`. The slug is its reference, unique
  and never edited.
- The entry's first line is `Source:` and then a link, a named person, or "own
  account".
- Ask the user for entries era by era, and never infer them from commits. Check
  that a link opens before citing it.

### 3. Mapping (career-advocate)

**Criteria.** At setup, read the Principal level document. Split each numbered
item in each of its sections (responsibilities, skills, and any metrics or goals)
into clauses, and review the split with the user. Write it to `mapping.json`, which
is from then on the only copy of the criteria:

- each clause is stored as `{key: {text, rating, argument, stamp}}`,
- a key names the section, item and clause, such as `Skills/10.d`,
- keys and text never change,
- a clause's references are the links and slugs written in its argument.

**Ratings.** Each clause gets an argument and one rating:

- **exceeds**: the evidence goes beyond the clause in scope, reach or duration,
- **meets**: the evidence shows the clause as written,
- **not yet shown**: also names the specific evidence or action that would show it.

The checkable rules are functions in `scripts/packet.py`:

- every reference resolves,
- a "meets" or "exceeds" cites at least one reference,
- an "exceeds" cites at least one verifiable reference: a web link, or a recorded
  entry whose source is not "own account".

The reviewer adds one judgment rule. A claim about scope, reach, adoption or impact
must cite a source that shows it, usually a recorded entry with a named source,
because units show only what the user did.

Arguments answer the clause in its own words. They lead with the most recent work,
which shows the user operates at Principal now, and use earlier periods to show it
has been sustained. One unit may support several clauses.

**`/career-advocate` runs every step and writes a new packet:**

1. **Close and snapshot.** Close every eligible period, newest first, then run
   `snapshot()`.
2. **Fold.** `mapping.json` keeps `{member link or slug: unit hash}` for everything
   folded.
   - Read units that are new or whose hash changed, newest first, in batches that
     fit in context. This includes a PR that gained actions or a unit that merged
     with another.
   - Add references to clause arguments, and save after each batch.
   - Then rewrite arguments and ratings.
3. **Review.** A reviewer subagent acts as a skeptical committee member.
   - It reviews each clause whose stamp no longer matches. It sees only the clause
     text, the argument and `resolve()` of its references.
   - It downgrades whatever breaks the judgment rule. The main agent may answer a
     challenge only by editing the argument, for example by adding evidence.
   - Once the reviewer accepts a clause, it runs `packet.py stamp <key>`. That
     command runs the checkable rules and refuses if any fails. Otherwise it stores
     a hash of the clause's text, rating, argument and the cited units' hashes.
4. **Render.** `packet.py render` refuses unless every stamp matches a fresh hash,
   so no edit and no evidence change reaches the packet unreviewed. It builds the
   packet from `mapping.json` and `resolve()` alone, and lists each unit once per
   clause. The packet contains:
   - a case summary assembled from the reviewed ratings and arguments,
   - one section per section of the level document, in its order, with each item
     as a subsection holding its clause arguments, links and any next steps,
   - stated limits: each period's coverage gaps, ended periods not yet collected,
     and which units are provisional.

   The packet carries no activity statistics.

## Store

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
config.json
recorded.md
periods/<YYYY, YYYY-QN or current>/data.json
units.json
mapping.json
packets/<date>.md
```

The first run asks for:

- the Principal level document,
- the career start year,
- every email address the user has committed under,
- the directories holding the user's repositories.

It sets `quarterly_from` to the current year. Copy anything useful from
`career-context.md` into `recorded.md` by hand. Nothing in the store is committed,
and tests use synthetic people and criteria.

## Steps

1. **work-summary.**
   - Make the four changes, each with tests.
   - Captures record a format version. A saved older-format capture is tested
     through the JSON digest, so stored periods keep working as the collector
     changes.
   - Then time one real calendar-year collection and read its coverage. If it is
     unusable, fix work-summary further; never add a second collector here.
2. **career-advocate.** Build `store.py` and `packet.py` with unit tests on
   synthetic captures, then setup, fold and review.
3. **Removal.**
   - Delete `scripts/collect-*.sh`, `scripts/scan-repos.sh`, `lenses/`,
     `reference/analysis-guide.md` and `tests/TEST-PLAN.md`, and remove the
     `private/` gitignore entry.
   - Drop the `assess`, `meeting` and `scan` modes; work-summary's quarterly,
     weekly and discovery modes cover them.
   - Rewrite SKILL.md's description and argument hint, and update
     `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real career packet privately and compare it with
   the July 2026 draft, which credited a Kubernetes rebase as authored work. It
   passes when:
   - every claim is traceable,
   - nothing is inflated by rebases or volume,
   - every clause is argued, or its gap is named with a next step.
