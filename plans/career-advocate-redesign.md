# Plan: career-advocate on top of work-summary

Status: proposed, November 10, 2026. No skill changes yet.

Goal: a Senior → Principal Software Engineer promotion case that a calibration
committee can check claim by claim. Rebuild `career-advocate` as a thin layer over
`work-summary`: work-summary finds and verifies what the user did; career-advocate
keeps that evidence across quarters and maps it to the level criteria.

## Why change

- **Weaker collector.** `collect-contributions.sh` credits commits by author email
  with no rebase detection. Most of its searches cap silently, and failures become
  `[]`. The July 2026 draft counted a Kubernetes rebase as authored work and quoted
  PR and review totals that its own collection log does not contain.
- **Volume stands in for level.** Line counts, commit counts and "N/18 categories"
  show nothing about scope or level.
- **Private data is unreachable when installed.** `private/` lives in the skill
  directory, so the plugin's installed copy has no level documents.
- **Evidence does not accumulate.** Each run re-collects 180 days. Mentoring,
  feedback and talks, which Principal weighs most, sat in free-text sections that
  stayed empty.

## Layers

Each layer reads only the one below it.

### 1. Collection (work-summary, unchanged)

Per calendar quarter, run work-summary's explicit-window collection and candidate
digest as `references/quarterly-reviews.md` shows. Resolve scripts at
`$SKILL_DIR/../work-summary/scripts/`: both skills ship together in the plugin.
Find `SKILL_DIR` as work-summary does, not with `BASH_SOURCE`, which is `.` in an
inline block. A closed quarter is collected once. The current quarter is refreshed
on each run. First runs collect several quarters in the background, closed
quarters first.

### 2. Evidence (career-advocate)

Per quarter, `quarters/2026-Q3/` holds:

- **`analysis.json`**: accomplishments chosen the way work-summary's quarterly
  briefing chooses them, in its analysis schema, so `render --analysis` proves
  every citation exists in the capture.
- **`coverage.md`**: the digest's source-status section. It is saved so the layer
  above can state collection gaps without opening a capture.
- **`data.json.gz`**: the capture, kept for re-validation and re-selection.

Each theme's `name` is unique within its quarter, which makes the reference
`2026-Q3/<name>`. An optional `effort` slug joins one initiative's themes across
quarters, because Principal criteria judge whole initiatives, not quarterly slices.
work-summary's validator ignores fields it does not know (`work_summary.py`
`check_theme`).

Evidence no collector can see goes in `recorded.md`: mentoring, feedback quotes,
talks, design documents, adoption by other teams, decisions influenced. Each entry
is a `## YYYY-MM-DD title` heading (also its reference) with its source: a link, a
named person, or "own account". Ask the user for these entries; never infer them
from commits.

### 3. Mapping (career-advocate)

`mapping.json` rates every numbered item in the target-level document, keyed by
section (`Responsibilities/4`, `Skills/5`), because Collaboration and Business
Impact appear in both lists. Each item gets cited references and one rating:

- **at target**: the evidence shows target-level scope, such as across teams,
  setting direction or others adopting the work,
- **at current**: real evidence at Senior scope,
- **no evidence**.

`scripts/check.py` (about 50 lines) fails on a missing or extra category or on a
reference that resolves to nothing. It also flags categories supported only by
"own account" entries.

A reviewer subagent, given each category's target-level text and only the cited
evidence, downgrades any claim it cannot trace. This replaces the four lens
subagents and targets July's failure, overclaiming.

The packet is rendered from the mapping: a manager briefing, evidence per category
with links, then gaps, each with the concrete step that would close it. Numbers
come only from cited sources or `coverage.md`; the packet carries no activity
statistics.

## Store and interface

The store is `~/.local/share/career-advocate/`, mode 700, outside any checkout or
plugin cache:

```text
levels/current.md, levels/target.md
recorded.md
quarters/<YYYY-QN>/{analysis.json, coverage.md, data.json.gz}
mapping.json
packets/<date>.md
```

Copy the existing `private/` level documents and career context into the store by
hand once. Nothing in the store is committed. Tests use synthetic people and
criteria.

`/career-advocate [period]` refreshes the evidence, then writes the mapping,
review, packet and gaps. The period defaults to the last four completed quarters
plus the current one. `assess`, `meeting` and `scan` are removed: work-summary's
quarterly, weekly and discovery modes cover them.

## Steps

1. **Evidence.** Per-quarter collection, `analysis.json`, `coverage.md`, the
   `effort` slug and `recorded.md`. Add a contract test that runs work-summary's
   renderer on `skills/work-summary/tests/fixtures/quarterly-review.json` with a
   theme carrying `effort`, so a change to work-summary that breaks this skill
   fails CI.
2. **Mapping.** `check.py` with unit tests on a synthetic ladder and evidence, then
   the mapping pass, reviewer pass and packet template.
3. **Removal.** Delete `scripts/`, `lenses/`, `reference/analysis-guide.md`,
   `tests/TEST-PLAN.md` and the `private/` gitignore entry. Rewrite SKILL.md's
   description and argument hint. Update `.claude/SKILLS.md` and the README.
4. **Qualification.** Build the real four-quarter packet privately and compare it
   with the July draft: every claim traceable, no rebase or volume inflation, gaps
   still named.

## Open questions

- Does Red Hat's nomination use a fixed form? If so, it sets the packet's sections
  and how the 18 categories are grouped; answer this before step 2.
- Is four quarters the right default window?
