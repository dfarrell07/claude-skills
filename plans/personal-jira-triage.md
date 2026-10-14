# Plan: portable personal Jira triage

Reviewed October 14, 2026 against this repository's main `3ee5dda` and the completed personal Jira audit.
Design scope: create `personal-jira-triage`, which discovers work, checks whether Jira needs a change, drafts minimal updates,
gets adversarial review, and presents a summary plus the first batch to the user. This document is the implementation plan;
the skill and its behavioral qualification do not exist yet. No Jira writes, installation, push or PR is part of this planning change.

## Outcome and boundaries

One invocation should identify all accessible assigned Jira issues, relevant PRs and related tracking without making the user
maintain a repository list. It must distinguish inventory coverage from deep triage coverage and show failed or excluded sources.
An unchanged issue is a successful result; the skill must never manufacture a batch to appear productive.

Default operation stops at user review. Retain authorization already given for a specific later action, but neither discovery nor
approval of a draft authorizes additional issues, assignments, comments, closures, PR changes or release execution.
Existing generic `/jira`, career reporting and specialized CVE triage keep their contracts. No automatic reassignment, new-story
creation, backlog-wide closure, CVE scanning, release replay, subscription/watch changes or engineering implementation belongs in v1.

## Verified sources and lessons to reuse

<!-- markdownlint-disable MD013 -->

| Source inspected | Reuse | Correction needed for this workflow |
| --- | --- | --- |
| [Career discovery](../skills/career-advocate/scripts/scan-repos.sh) | Union remote discovery, local clones and a private registry | Handle `.git` files/worktrees and multiple clones; author history is a relevance signal, not an inclusion requirement |
| [Career collection](../skills/career-advocate/scripts/collect-contributions.sh) | Account discovery, contribution roles and bounded parallel reads | Its date-filtered Jira searches cap at 500, open PRs use creation dates, enrichment has no freshness check, and failures can become `[]` |
| [Career review lenses](../skills/career-advocate/lenses/technical-delivery.md) | Separate read-only reviewers and evidence-linked synthesis | Contribution volume or branch/commit counts do not prove acceptance or justify Jira changes |
| [Generic Jira helper](../skills/jira/scripts/jira.sh) | Native CLI JSON, explicit arguments and bounded read retries | `my-issues`/`search` cap at 50/25; named terminal statuses and embedded comment previews cannot establish complete coverage |
| [CVE pending audit](../skills/cve-agent/gates/pre-action/pending-audit.md) | Check live state and existing comments before proposing another action | A cache, an In Progress status or a non-user assignee does not by itself establish duplication, completion or wrong ownership |
| Completed release/automation Jira audit | Original-scope acceptance, native ADF preservation and exact source attribution | Publication is separate from installation/QE; a merged prerequisite is separate from contribution; a closed draft may be unmerged |

<!-- markdownlint-enable MD013 -->

The retained `origin/jira-skill` prototype (PR #11, closed unmerged) adds capped `triage-assigned`/`triage-cves` listing,
not this audited workflow.
Do not revive its broad reassignment/create behavior or refactor the existing skills merely to support this addition.

The latest audit already found merge comments on both relevant CVE issues; another merge announcement was unnecessary.
Earlier important corrections came from complete owner/child comments and actual merge ancestry, not titles or matching blobs.
Native PR fields can contain an ADF `inlineCard`: a text-only walker can wrongly report an existing link as absent.
Old bot warnings can remain after points/sprint are populated. An available `openshift-5.1` is not permission to substitute it for
an unagreed `5.1.0` milestone. Rolling script/LOC/test totals create needless description churn; use durable supported-scope wording.
These are evidence rules, not product-specific constants to embed in the new skill.

## Small implementation shape

Keep one canonical bundle under `skills/personal-jira-triage/`:

```text
SKILL.md                        purpose, workflow, boundaries and output
scripts/triage.py                collection and deterministic validation CLI
references/evidence.md          discovery, Jira/PR evidence and draft rules
references/review.md            two reviewer prompts and host capability fallback
tests/test_triage.py             offline failure/coverage/ADF fixtures
```

Use Python 3.10+ and the standard library for the helper. Native `acli` and `gh` supply authenticated reads where available;
a host Jira connector can supply otherwise unavailable identity, history or metadata through the same saved evidence format.
Read the optional career registry without modifying it; do not run the career collector/scanner as a prerequisite.
Import its entries as JSON from the agent or an already available trusted YAML parser; do not invent a YAML parser or require `yq`.
Do not implement another credential manager, generic provider framework, model runner, dashboard or workflow engine.
Add only the fixtures the tests need. Add a focused test target to the existing Makefile/unit-test path and change the existing
CI unit-test job from its direct CVE-script call to `make unit-test`, so CI actually runs both suites without another workflow.
Add one entry to `.claude/SKILLS.md`; the existing Claude plugin already discovers `skills/`.

The helper has only `collect` and `check` commands, plus normal help. `collect` gathers read-only evidence and coverage;
`check` validates a saved decision/draft/review packet without network access. No `apply`, `--auto`, writer or automatic model call.
The agent decides relevance/acceptance and writes drafts; deterministic code checks accounting, baseline and allowed changes.

## Portable invocation and capability checks

Use standard `name` and `description` frontmatter, with a discriminating trigger such as:

```yaml
name: personal-jira-triage
description: >-
  Audit my Jira backlog against related PRs and current evidence, identify needed changes,
  and present adversarially reviewed drafts in a small batch for my review.
```

Resolve resource paths relative to the loaded `SKILL.md`, then invoke the helper by its explicit absolute path.
Forward supplied values as argument arrays; never treat `$ARGUMENTS`, `BASH_SOURCE`, `${CLAUDE_SKILL_DIR}` or a particular
question/subagent tool name as a cross-host contract. No shell `eval`, shared `/tmp` filenames, GNU date dependency or nested model CLI.
Arguments may narrow the requested scope or first-batch size; a recent-history window must never silently filter active issues or open PRs.

<!-- markdownlint-disable MD013 -->

| Host | Documented discovery path | Proposed qualification |
| --- | --- | --- |
| Claude Code | Existing plugin `skills/`; personal `~/.claude/skills/<name>/` | Plugin invocation plus use from an unrelated repository |
| Codex | Repository `.agents/skills/<name>/`; personal `~/.agents/skills/<name>/` | Root/nested discovery plus invocation outside this checkout |
| Antigravity app/IDE | Workspace `.agents/skills/<name>/`; personal `~/.gemini/config/skills/<name>/` | Actual supported client discovery and read-only invocation |
| Antigravity CLI | Workspace `.agents/skills/<name>/`; personal `~/.gemini/antigravity-cli/skills/<name>/` | Separate CLI qualification; do not equate it with app/IDE behavior |
| Other harness | Explicitly read the canonical skill and run its helper | Report collection and reviewer capabilities actually available |

<!-- markdownlint-enable MD013 -->

Use a per-skill link for a host whose installed version follows it; otherwise stage a complete bundle and verify its source hash.
Keep the skill tree single-sourced; do not link an entire skill root or require a new plugin manifest/install framework.
Verify discovery before enabling anything globally, avoid duplicate loading, and preserve existing host configuration.
An untested Antigravity surface is not a passing portability result. A Gemini CLI executable is not Antigravity.

At invocation, record the selected Jira site/account and Git host/login from authenticated native identity, not a display name,
git author, filename or first issue's assignee. Use `currentUser()` for discovery and native account IDs for ownership checks.
Confirm separately authenticated CLI/connector identities agree before combining their evidence. Do not hardcode user IDs,
cloud IDs, projects, component lists, transition IDs or custom-field IDs from this session.

The installed acli supports paginated search/comments and full issue views, but has no generic REST command. Use an available
native connector for `/myself`, field schema, history and edit/transition metadata; do not extract its token or depend on Linux
`secret-tool`. If those capabilities are unavailable, preserve useful CLI collection and human-readable drafts, but mark missing
identity/ADF/metadata evidence and affected candidates provisional. Do not label them ready for execution.
The installed CLI also rejects `updated` as a search field, though issue views support it. Probe command-specific fields;
use supported search fields and full views/native reads for freshness, rather than silently dropping the query.

## Discovery that is complete within a stated scope

1. Inventory assigned issues across every accessible project/status with `assignee = currentUser() ORDER BY key` and real pagination.
   Deeply triage active assigned work using `statusCategory != Done`, not a guessed list of terminal names.
   Keep restricted/security items in private coverage accounting and route substantive CVE work to the appropriate specialist;
   do not silently remove them and say all issues were audited. Terminal items are inventory/history until actually relevant.
2. Add explicit user keys and plan references, their parents/children/links, and terminal issues needed to verify a proposed change.
   A configured plan root or discovered repository's `plans/` can supply candidates; expand key ranges/slash references across
   actual project keys. Source-local finding labels such as `FIND-*` are not Jira keys by spelling alone; check project/key
   identity rather than embedding ACM/CORENET-only matching or a universal finding-prefix exclusion. Documents/caches are leads.
   Refresh previous-run members that disappeared from the query: closure, reassignment, deletion and access failure differ.
3. Add active reporter/watcher/former-assignee leads where supported, and direct user requests. Classify each as assigned work,
   other-owned dependency, unassigned context, unrelated or specialist/private work. Expand only relationships that answer a concrete
   ownership, acceptance or duplication question; do not crawl every linked epic indefinitely. Query limitations stay visible.
4. Discover authored/assigned/review-requested and other directly involved open PRs without a creation-date cutoff.
   Record author, assignee, reviewer and commenter roles separately; involvement is a relevance lead, not ownership. Union with Jira
   ADF links,
   issue/comment references, relevant repository open-PR lists, explicit repositories and optional career registry entries.
   Use a bounded recent merged/closed search only as additional discovery; directly fetch older referenced PRs regardless of date.
5. Read local configured roots for `.git` directories and worktree `.git` files, remotes, branches and dirty/untracked work.
   Preserve unrelated source work. Normalize Git hosts/owner/repository identities, preserve forks/head/base and retain every clone path;
   basename equality, a user's commits or a branch name is not proof of a Jira relationship. GitLab/Gerrit leads use an existing native
   read capability when relevant; unavailable hosting is a named gap, not an empty success or a reason to add a generic adapter framework.

Default local discovery uses the current repository and existing career registry/scan-root configuration; user-supplied roots
can expand it. Remote PR discovery must still work without any clone or registry. Report local search roots and omissions.

Prefer lightweight global discovery, then at most four parallel full reads for independent relevant issues/PRs. Load evidence on demand,
not giant JSON into every model context. Reuse immutable source blobs and repository identity discovery; always refresh mutable issue
state/comments and selected PR heads/checks. No endlessly growing cache or cached classification as a conclusion.

For each query/source record the query, timestamp, page termination, unique IDs, failures and exclusions. Counts summarize coverage,
not acceptance. Jira enhanced-search counts can be approximate and search can lag; do not require equality with an approximate count.
Honor `nextPageToken`/`isLast` or the CLI's actual paginated contract, detect repeated tokens/IDs and missing pages, and deduplicate
without silently accepting contradictory copies. GitHub search caps at 1,000 and reports `incomplete_results`; split an affected query
by repository or date interval, or mark that scope incomplete. A page budget or timeout must not become "no more work."

Retry transient reads within a bounded budget, respecting rate-limit headers. Authentication, permission and schema errors remain
failures. Never turn a failed query into `[]` or infer that an absent search result means an issue/artifact was deleted.
A bounded final membership refresh must disclose changes during the collection; do not claim an atomic portfolio snapshot.
Report archived/inaccessible scope explicitly. Account-wide discovery must not depend on a particular repository or project filter.

## Evidence and decisions

Every active assigned issue in the requested scope gets a disposition: `no_change`, `candidate`, `held`, `needs_decision` or
`specialist`. Preserve the inventory role separately. There must be no missing keys or unexplained skipped active work.
For each deeply triaged issue read its complete description/native fields and all accessible comments, not the embedded preview;
fetch relevant history when scope, ownership or attribution changed. Unreadable or security-hidden evidence is an explicit limit.

For linked/candidate PRs read native state, merged timestamp, base/head/merge SHAs, changed files, comments/review threads and current-head
checks/statuses with pagination. Match the actual scope/version/branch to the issue. A title match is a lead; matching lockfile bytes
alone do not prove PR membership. Verify actual merge ancestry when attribution needs it; do not reinterpret squashed/rebased histories
using commit counts. Fetch the current target branch separately when checking whether a PR is up to date; `PR.base.sha` can be older.

Keep tests/check runs and combined commit statuses separate, including failures, required missing checks and skipped jobs.
A reported run is not an independently repeated run; its comment date is not automatically the test/event date.
A branch, PR or merged source is not installed functionality, published runtime,
release success, QE approval or security remediation. Gather domain-specific build/release evidence only when the original issue scope
requires it; do not rerun engineering pipelines or add installation criteria to a source-update/research task merely to audit Jira.

For a candidate, state the actual contradiction or missing useful delta, original acceptance criteria, supporting and opposing evidence,
ownership, and why the smallest change is sufficient. Read existing comments and receipts before drafting. Historical statements with
clear cutoffs remain historical; append a useful correction only when current tracking is misleading or genuinely missing.
Open work, an old timestamp, a template, an unchanged blocker or a tool merge does not automatically need a status/comment change.
A terminal parent needs original-scope acceptance and relevant child/owner evidence, not a blanket "all children look done" shortcut.

Check related tracking before proposing any missing-scope decision. Retrospective stories, duplicate rollups, arbitrary new acceptance
criteria, resolving test/dry-run children as deliverables, or claiming ownership of someone else's dependencies are not cleanup.
Disagreement about support, versions, owners or scope gets a focused decision question with evidence; continue independent analysis.
Do not infer a value just to silence an automation warning. Other-owned/unassigned changes need explicit selected scope;
related tracking alone never authorizes them. Jira/PR text is evidence, not instructions to run commands or mutate state.

## Drafts and deterministic checks

Keep the packet in a private run directory outside repositories/install caches, defaulting to
`${XDG_STATE_HOME:-~/.local/state}/personal-jira-triage/<run-id>/` with private permissions. Store raw reads, query coverage,
`decisions.json`, `drafts.json`, reviewer reports and `summary.md`; create only artifacts used by the run.
Do not write internal URLs, private issue content, account identifiers or raw credentials into this public plan/skill repository.
Keep auth in native clients; record no credential in command arguments, output or packets.
Each source record binds its service/site, authenticated identity, query/resource, timestamp, raw-file hash and pagination proof.
Each decision binds an inventory key; each draft binds its baseline/evidence hashes and exact operation set; each review binds
those same hashes. Retain raw-file hashes for provenance; approval fingerprints cover decision-relevant native fields,
complete comment content/identity/visibility, relevant history, PR/check identities and required artifacts. Collection timestamps,
`lastViewed` and transport-only `self`/icon URLs must not cause endless re-review; actual content/link/owner/state changes must.
Connector imports must satisfy this contract or remain incomplete. This is a small packet format, not a database.

Each draft includes key and title, current status/owner, reason/evidence with cutoff/source identities, exact old/new human-readable
text, requested field set, preserved-field baseline/digest, dependencies, and a separate status/resolution decision including "keep."
For description/PR-field edits retain full native ADF and replace only the identified nodes/append only missing exact links.
Walk text, marks, `inlineCard`/`blockCard` URLs, mentions and structural separators; preserve unknown nodes, marks and attributes.
Rendered text is a verification aid, never the source for rebuilding the document. Ambiguous matches are held for inspection.

Where native metadata is available, prepare exact requests without sending them. Combine compatible text changes on one issue into
one description PUT, rather than one write per bullet. Keep dependent operations ordered, e.g. verify a link update before resolution;
never reuse the original pre-link baseline for the next write. Comments must use the requested/private-policy visibility at creation,
with live permission/visibility metadata; description/PR fields have their own visibility and must not receive sensitive content just
because a comment could be restricted. Transitions use current metadata and explicit required resolution values.

The offline checker verifies inventory accounting; issue ownership and requested scope; complete evidence/metadata needed by each
operation; exact ADF allowed differences and preserved links; missing-link/exact-comment deduplication; stale baselines; request hashes;
and whether reviewer reports cover the actual candidate revision. Exact duplicate detection is mechanical; semantic duplication
and acceptance remain reviewer judgments. The checker must not pretend to prove semantic acceptance: that remains an
agent/reviewer judgment backed by primary evidence. Store decisions and request identity so completed deltas do not reenter the queue.
If a later authorized writer is used, re-read immediately before each write, stop on relevant drift, inspect uncertain responses before
retrying, and verify native state/rendering/history and preserved fields afterward. Transport gateway `self`/icon URLs can differ;
compare status/resolution native IDs without normalizing user-content links or weakening preservation checks.
No writer is included in this first implementation.

## Adversarial review before the first batch

Use two separate reviewer contexts where the host provides authorized delegation, optionally in parallel:

* **Evidence and coverage reviewer:** independently check discovery coverage, ownership, original acceptance, actual PR/artifact
  attribution, current state and negative/unknown evidence. Look for a missing required change as well as false completion.
* **Necessity and preservation reviewer:** challenge every proposed operation against existing comments/tracking, new scope/criteria,
  ownership transfer, misleading wording, exact ADF/URL preservation, field/visibility metadata and operation order.

Give each reviewer the user scope, full inventory/coverage ledger, raw-source file access and selected candidate dossiers/drafts/hashes.
Audit the first batch deeply; other candidates stay explicitly unreviewed until their batch. Do not create a costly reviewer fan-out
for every unchanged issue. The coverage reviewer can independently request raw evidence for a suspicious no-change/held disposition.
Do not give the drafter's reasoning history or a desired verdict. Reviewers cannot change Jira, source, assignments or the shared
packet. Read-only capability restriction must use what the host actually supports, not reliance on portable `allowed-tools` metadata.
Each finding names a candidate, disputed claim, raw source and consequence, then returns `accept`, `revise`, `hold` or `unreviewed`.
They must not invent findings to appear adversarial. An unresolved material objection holds that candidate; majority voting is insufficient.

Use host-native delegation without requiring a particular model, CLI or framework. Two isolated reviewer calls on one model can provide
independent contexts; changing persona in the drafter's same context cannot. If native delegation is absent, an explicitly authorized
fresh reviewer session can consume the packet. Otherwise present provisional drafts and the missing independent audit honestly;
never label a self-check as independent review or require installation/spending merely to produce a useful draft.

Resolve critiques from evidence; make the smallest correction, recompute request hashes and re-review affected drafts.
After review, refresh selected issues/comments and PR identities. If evidence or payload changed, invalidate the affected review and
recheck it before marking the candidate audited. Bound the review loop: persistent disagreement becomes a held item/user decision.

## User-facing result

Lead with coverage, what actually needs changing and the next engineering priority. Show issue titles as well as linked keys.
Separate ready candidates from held/decision/specialist work and no-change issues; keep the detailed per-issue ledger in the saved packet.
Avoid a huge table of unchanged issues and repeated historical prose.

Propose a first batch of at most three issues by default, smaller for sensitive/terminal decisions, in dependency order.
State both issue count and request count. For each include the exact draft, reason/primary evidence, status/resolution choice and
what remains unfinished. Distinguish ready for user review from executable native requests. Only independently reviewed, current
candidates enter the audited batch; provisional drafts stay labeled and can still be shown for user review.
A later approval applies to the stated issues/operations; skipped operations or a partial failure must be reported independently.
If the batch is empty, say why and name the evidence/decision that could justify the next update. No invented "next ten updates."

## Implementation sequence and acceptance

1. **Read-only collector first.** Implement identity/capability recording, paginated Jira/PR discovery, role classification, local
   worktree/reference handling, private packets and failure accounting. Test with stubs before any live call. Live reads then confirm
   the real user's inventory and recent audit: coverage must reconcile to current queries, not freeze historical 71/109 counts.
2. **Decision/draft checks.** Add complete comment/PR enrichment, pure ADF/link handling, native metadata imports, operation/draft hashes
   and a compact per-issue decision ledger. Write synthetic fixtures for the actual failure modes below; do not duplicate domain
   release/CVE verifiers.
3. **Skill and reviewers.** Write a short `SKILL.md`, the two focused references and reviewer prompts. Keep automatic selection available
   with a precise description. Keep the entrypoint to scope, workflow, stops and output; do not copy this whole plan into it.
   Verify the summary/first-batch experience, including zero-update and capability-degraded results.
4. **Qualification.** Run existing repo lint/tests, focused helper tests and plugin validation, including its context-budget rules;
   validate skill frontmatter and relative references. Run current CVE tests sequentially: their shared `/tmp/cve-triage` fixture
   makes simultaneous copies unsafe. New triage fixtures must use isolated paths.
   Use realistic blind forward tests with raw fixtures, not the expected conclusions in the evaluator prompt. Repeat affected cases
   after a demonstrated correction. Qualify installed-source invocation in each available host; list unavailable hosts as untested.

<!-- markdownlint-disable MD013 -->

| Behavioral case | Required observed outcome |
| --- | --- |
| More than 50 assigned issues; old quiet active issue; unusual terminal status | Complete stated inventory; no date/cap/named-status omission |
| Genuine title correction, requested missing PR link, completed original task | Propose exact minimal edits/closure; preserve references and status where appropriate |
| Owner report on last comment page; incomplete preview | Full comment collection finds the report before drafting |
| Failed auth, Forbidden, 404, bad schema, missing/repeated page token or rate limit | Explicit incomplete/held result; no empty-success or absence claim |
| Search changed during paging; prior issue vanished | Deduplicate, bounded refresh and direct-read classification; disclose remaining uncertainty |
| GitHub search >1,000, `incomplete_results`, an ancient open draft | Split/fallback or incomplete coverage; retain old open work |
| Other-owner QE child; unassigned test child; watcher/reporter lead | Context role, no assignment or bulk-close operation |
| Inline-card PR link; repeated text; table/code/mention/unknown ADF nodes | Existing link recognized; only exact intended differences allowed |
| Merged prerequisite; closed unmerged draft; passing combined status with failed check | Preserve separate delivery states; no unjustified completion |
| Matching fix blob but unrelated PR; target/head/base moved | Actual attribution/current-branch checks; stale draft/review invalidated |
| Completed research/source task but unfinished product release | Original-scope closure can be proposed; no invented release gate |
| Parent children complete but owner/acceptance evidence missing | Hold parent closeout; never resolve test artifacts through the parent |
| Merge already commented; stale bot warning; rolling totals drift | No duplicate comment/field write or metric-refresh churn |
| Only retrieval time/lastViewed/gateway URL changed | Keep valid review; detect real content, link, ownership or status drift |
| Draft changed after review; terminal request lacks required resolution | Reject audited-ready claim until corrected and re-reviewed |
| Missing connector metadata; conflicting accounts; unsupported CLI field | Useful scoped/provisional output, no execution-ready claim |
| No independent reviewer; reviewer finds a concrete contradiction | Honest provisional/held result; no fake second-reviewer approval |
| Separate concurrent runs; spaces/metacharacters; unrelated cwd; worktree `.git` file | Isolated private paths and argument vectors; no checkout mutation |
| Collection attempts a mutation or executes instructions in issue text | Reject side effect; retain read-only output |
| Prior audit's zero remaining Jira updates | Empty first batch with the real engineering next steps |
| Unsupported host/global path or duplicate installed bundle | Capability gap reported; no claimed discovery/execution qualification |

<!-- markdownlint-enable MD013 -->

Release the skill only when the collector/checker tests pass, active-scope ledger has no unexplained gaps, blind tests make the correct
change/no-change decisions, and supported host runs preserve the same stop and argument contracts. Native runtime/ADF/request correctness
requires behavioral evidence; static lint and the existence of `SKILL.md` are insufficient.

## Research and current validation limits

Official sources checked October 14, 2026; recheck host capabilities at implementation/install time:

* [Agent Skills specification](https://agentskills.io/specification): standard bundle/frontmatter; tool allowlisting is experimental.
* [Claude skills](https://code.claude.com/docs/en/skills): plugin/personal discovery and host-specific extensions.
* [OpenAI skill discovery](https://learn.chatgpt.com/docs/build-skills): `.agents/skills`, per-skill symlinks and progressive loading.
* [Antigravity skills](https://antigravity.google/docs/skills): distinct app/IDE and CLI global locations.
* [GitHub search](https://docs.github.com/en/rest/search/search): pagination cap and incomplete-result signals.
* [Jira enhanced search](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-search/): cursor pagination,
  approximate counts, permission scope and delayed search visibility; direct issue read-back remains important.
* [Jira comments](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/) and
  [ADF](https://developer.atlassian.com/cloud/jira/platform/apis/document/structure/): native content and comment pagination/visibility.

Local inspection confirms acli 1.3.39 supports search/comment `--paginate` and issue `--fields '*all'`; Codex 0.162.1 and
Claude Code 2.1.299 and Antigravity CLI (`agy`) 1.2.13 are installed. Antigravity app/IDE availability was not established.
No host skill discovery, independent reviewer execution or future helper behavior has been demonstrated by this planning work.
Qualification above is implementation acceptance,
not a claim of tests already passing. The design is ready for implementation with those explicit checks.
