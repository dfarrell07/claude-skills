# Plan: portable personal Jira triage

Reviewed October 14, 2026 against this repository's main `3ee5dda` and the completed personal Jira audit.
Design scope: create `personal-jira-triage`, which discovers work, checks whether Jira needs a change, drafts minimal updates,
gets adversarial review, and presents a summary plus the first batch to the user. The canonical bundle is now implemented;
this document retains its design and qualification criteria. Implementation status and limits appear below.
No Jira writes, global installation, push or PR was performed.

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

The session transcript adds several recovery lessons. Read canonical messages/tool results once, including pre-compaction records;
keyword hits and wrapper success are leads, not proof of an actual failure. Inspect each subprocess exit and resource-specific JSON
shape: a successful shell's last command or fulfilled tool promise can mask an earlier failed query. An inaccessible resource/path
is not proof of a missing PR, release or artifact. Authentication and permissions are service-specific; renewed `oc` login does not
establish Jira/GitHub access or permission to inspect another namespace. Use accessible primary evidence and disclose the gap.
Parse source URLs and match exact service/repository/issue identities; wildcard host patterns or substring matches are insufficient.

A native Jira edit persisted even though a faulty rendered-text check failed. Resume from current native state and receipts, never
from the exception alone. Heading/list/table separators and card links need structural handling; HTML is a secondary check.
A closure legitimately changed status category and a chart-derived field. Identify derived fields through live schema/history;
do not ignore all custom fields or count a plugin's cached status as authority. Another refresh found an Activity Type change,
not new work: inspect the actual difference before invalidating acceptance or inventing an update. When maintaining saved plans,
keep completed payloads/receipts immutable; refresh only current queue pointers, with correct source revisions and valid anchors.

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

Use `collect --imports <manifest.json>` to incorporate native connector captures; `check` reads the resulting packet.
The versioned import manifest contains `schema_version` and `sources`: each source identifies service/site, resource kind,
supported response schema/tool identity, version when available, authenticated-identity evidence reference, query/resource, capture time,
relative raw-file paths/hashes and pagination/completeness proof. Raw files live within the explicit import directory,
not arbitrary linked paths.
Validate each supported resource shape, retain provenance and reject conflicting identity, missing pages or contradictory duplicate
resources. Record an unavailable connector version honestly; version disclosure alone is not a completeness requirement.
Complete inventory/issues/comments can be imported when `acli` is absent; analogous Git captures can fill missing `gh`
reads. The agent exports through available native tools; the helper never invokes a connector or extracts its credentials.
Imports can satisfy an unavailable capability only with complete evidence; otherwise preserve a provisional packet and named gaps.
Define this small contract in `references/evidence.md` before implementing collection, rather than adding a provider framework.

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
Each decision binds an inventory key; drafts/reviews bind baseline and decision-input fingerprints plus the draft/operation hash.
Retain raw-file hashes for provenance; semantically identical refreshed captures need not retain their raw hash to preserve review.
Approval fingerprints cover decision-relevant native fields, complete comment content/identity/visibility, relevant history,
PR/check identities and required artifacts. Collection timestamps,
`lastViewed` and transport-only `self`/icon URLs must not cause endless re-review; actual content/link/owner/state changes must.
Connector imports must satisfy this contract or remain incomplete. This is a small packet format, not a database.
Each candidate declares its decision inputs and which are mutable. A final refresh receipt records the capture cutoff and refreshed
source identities/fingerprints, so unchanged target state cannot hide changed supporting evidence.

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
operation; exact ADF allowed differences and preserved links; missing-link/exact-comment deduplication; request hashes;
and whether reviewer reports cover the actual candidate revision. Exact duplicate detection is mechanical; semantic duplication
and acceptance remain reviewer judgments backed by primary evidence. Store decisions and request identity so completed deltas
do not reenter the queue.

`check` establishes structural validity and consistency with the latest saved refresh, including whether drafts/reviews are stale
against that evidence. It cannot prove live remote freshness or manufacture a refresh receipt. A structurally valid packet without
that receipt remains provisional; audited-current means current at the stated capture cutoff, never indefinitely current or authorized.
If a later authorized writer is used, re-read immediately before each write, stop on relevant drift, inspect uncertain responses before
retrying, and verify native state/rendering/history and preserved fields afterward. Transport gateway `self`/icon URLs can differ;
compare status/resolution native IDs without normalizing user-content links or weakening preservation checks.
No writer is included in this first implementation.

On resume after interruption/compaction, recover the user's scope, exact selected draft revision, evidence cutoff, operation order
and completed/uncertain receipts from the private packet and canonical history. Reconcile that record with live state before drafting
again. A failed post-write verification is not permission to replay an operation. A short approval refers only to a clearly identified
preceding draft; numbering alone, an ambiguous correction or an older batch must not select another issue. V1 still stops at review;
this recovery rule prevents re-proposing already completed changes without introducing a writer.

## Adversarial review before the first batch

Use two separate reviewer contexts where the host provides authorized delegation, optionally in parallel:

* **Evidence and coverage reviewer:** independently check discovery coverage, ownership, original acceptance, actual PR/artifact
  attribution, current state and negative/unknown evidence. Look for a missing required change as well as false completion.
* **Necessity and preservation reviewer:** challenge every proposed operation against existing comments/tracking, new scope/criteria,
  ownership transfer, misleading wording, exact ADF/URL preservation, field/visibility metadata and operation order.

Give each reviewer the user scope, full inventory/coverage ledger, raw-source file access and selected candidate dossiers/drafts/hashes.
Audit the first batch deeply; other candidates stay explicitly unreviewed until their batch. Do not create a costly reviewer fan-out
for every unchanged issue. The coverage reviewer can independently request raw evidence for a suspicious no-change/held disposition.
Use history-free reviewer contexts supplied only those inputs; explicitly disable inherited drafter/conversation history and record
the actual context mode. Separate agents with inherited reasoning do not satisfy this contract. Give no desired verdict.
Reviewers cannot change Jira, source, assignments or the shared
packet. Read-only capability restriction must use what the host actually supports, not reliance on portable `allowed-tools` metadata.
Each finding names a candidate, disputed claim, raw source and consequence, then returns `accept`, `revise`, `hold` or `unreviewed`.
They must not invent findings to appear adversarial. An unresolved material objection holds that candidate; majority voting is insufficient.

Use host-native delegation without requiring a particular model, CLI or framework. Two isolated reviewer calls on one model can provide
independent contexts; changing persona in the drafter's same context cannot. If native delegation is absent, an explicitly authorized
fresh reviewer session can consume the packet. Otherwise present provisional drafts and the missing independent audit honestly;
never label a self-check as independent review or require installation/spending merely to produce a useful draft.

Resolve critiques from evidence; make the smallest correction, recompute request hashes and re-review affected drafts.
After review, refresh the mutable decision inputs of selected candidates: target and supporting issue/owner/child evidence,
comments, PR heads/checks/statuses, and operation/visibility metadata actually needed by the draft. Check current artifact availability
only when acceptance depends on it; reuse immutable blobs and leave unrelated inventory alone. Record the final refresh receipt.
Material changes invalidate the affected review; recheck/re-review before marking the candidate audited. Bound the review loop:
persistent disagreement becomes a held item/user decision.

## User-facing result

Lead with inventory coverage, deep-triage coverage, independently reviewed first-batch coverage and the evidence cutoff, then what
actually needs changing and the next engineering priority. Sampling unchanged issues is not independent review of the whole portfolio.
Show issue titles as well as linked keys.
Separate ready candidates from held/decision/specialist work and no-change issues; keep the detailed per-issue ledger in the saved packet.
Avoid a huge table of unchanged issues and repeated historical prose.

Propose a first batch of at most three issues by default, smaller for sensitive/terminal decisions, in dependency order.
State both issue count and request count. For each include the exact draft, reason/primary evidence, status/resolution choice and
what remains unfinished. Distinguish ready for user review from executable native requests. Only independently reviewed, current
candidates enter the audited batch; provisional drafts stay labeled and can still be shown for user review.
A later approval applies to the stated issues/operations; skipped operations or a partial failure must be reported independently.
If the batch is empty, say why and name the evidence/decision that could justify the next update. No invented "next ten updates."

## Implementation sequence and acceptance

1. **Contract and read-only collector first.** Define the minimal import/packet/refresh contract in the evidence reference; implement
   identity/capability recording, paginated Jira/PR discovery, role classification, local
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
   Use the layered tests below. Repeat affected cases after a demonstrated correction; do not tune against the only evaluation set.
   Qualify installed-source invocation in each available host; list unavailable hosts as untested.

## Test setup and observable acceptance

Keep one stdlib `unittest` suite in `tests/test_triage.py`; use inline synthetic fixtures and small fixture files only when they
improve readability. Do not copy private Jira exports/transcripts into the repository or add a general evaluation framework.
The transcript supplies failure mechanisms, not golden private issue counts, titles, credentials or expected prose.

1. **Pure helper tests:** inventory accounting, pagination termination, explicit native response shapes, ADF preservation and
   decision/review fingerprints. Assert exact allowed native diffs, preserved content/URLs and useful failure diagnostics.
   Test tampered raw evidence, missing/orphan/duplicate decisions, conflicting operations and reviews for a different revision.
   Include a deliberately corrupted counterpart to a passing packet so the test proves the rejection, not merely that a check ran.
   A schema-valid packet is not semantic acceptance; `check` must not certify an unaudited judgment.
2. **Subprocess contract tests:** invoke the real helper from an unrelated directory using fake `acli`/`gh` executables first on
   a test-local PATH. Stubs record argv and return controlled stdout, stderr, exits and paginated responses; reject unknown commands
   and mutations. Validate the installed client's flat versus paginated/slurped shapes explicitly, not by arbitrary JSON flattening.
   Check each process result, malformed/truncated output, timeout and successful-looking JSON accompanied by a nonzero exit.
   Define/document exits: valid empty collection succeeds; operational failure/incomplete collection remains nonzero with a saved
   coverage packet; invalid/stale/unreviewed ready claims fail `check`. A legitimate held disposition is not a broken packet.
   `check` runs with all external calls forbidden. The collector only invokes known read commands; explicitly allow a read-only
   search/GraphQL POST where a native client needs it, rather than treating every POST as a mutation or every `gh api` as safe.
3. **Blind agent tests:** supply the actual skill, user request and raw fixture services, with a separate oracle unavailable to
   drafter/reviewers. Inspect saved decisions, exact drafts, evidence references, reviewer identities/hashes, final batch and call log.
   Pair cases differing in one decisive fact: a final-page owner report, merged versus closed-unmerged, a card link versus no link,
   actual fix ancestry versus matching unrelated bytes, completed original scope versus unmet acceptance, or already-posted versus
   genuinely missing commentary. Include needed edits and an empty batch; measure missed necessary changes and unnecessary changes.
   Grade mechanical preservation against exact expected diffs and semantic acceptance against an evidence-linked oracle/rubric;
   start fresh evaluation contexts without solution-bearing inherited history and keep oracle answers outside their permitted
   filesystem/tool inputs, fixture names and reviewer packets. If isolation cannot be enforced, label the run assisted rather than
   blind qualification. Reserve unseen variants for high-risk mechanisms, rather than
   matching a suggested answer, counting headings or requiring exact prose.
4. **Host qualification:** record host/version, discovery root and loaded bundle hash, then invoke from an unrelated working directory
   with fixture clients and the host's actual reviewer capabilities. Stage the complete bundle only in an owned temporary workspace
   discovery root; preserve existing global installations/configuration and record workspace versus global qualification separately.
   Observe the read-only stop, resource/argument handling and two
   separate reviewer contexts, including one unsupported reviewer path. Installed binaries, static lint and a self-review are not
   discovery or independent-review proof. Do not silently install globally or launch extra model sessions merely to pass qualification.
   Run the full blind corpus on one qualified host, then discovery/arguments/stop/reviewer-fallback smoke cases on other claimed hosts;
   expand only where host differences or failures warrant it, rather than multiplying every domain fixture by every harness.

Each test owns a `TemporaryDirectory` and explicit packet/stub/log paths; keep real HOME, credentials, native configs, installed skill
roots and user checkout untouched. Run two collector fixtures concurrently and compare their separate receipts. Fixture runs must
never fall through to authenticated services; where a host cannot enforce isolation, do not run a supposedly offline agent test there.
Real read-only inventory qualification is separate and opt-in; no legacy CVE levels that launch models/scans/live workflows belong in
this skill's default suite. Offline CI discovers tests through `make unit-test` and explicitly fails if no tests are collected;
run the legacy CVE suite sequentially. Configure the existing CI unit-test job for the declared Python 3.10 minimum and also verify
locally with the available Python version; no new matrix/workflow is needed. Verify both suites actually execute in
`.github/workflows/linting.yml` and failures propagate.

Use a small call-log check to verify lightweight discovery, bounded read concurrency, lazy dossiers and immutable-blob reuse;
change mutable comments/heads/checks and confirm a fresh read. This tests efficiency without enforcing historical timing/counts.
Store qualification receipts privately with case/fixture revision, actual bundle hash, host, observed decisions and failures.
Record the tested source tree after merges/rebases complete; a conflicted or subsequently changed candidate has not passed those tests.
Record unsupported/blocked cases explicitly; do not count them as passes. Correctness gates below apply to the relevant test layer,
not merely the number of unit assertions.

<!-- markdownlint-disable MD013 -->

| Behavioral case | Required observed outcome |
| --- | --- |
| More than 50 assigned issues; old quiet active issue; unusual terminal status | Complete stated inventory; no date/cap/named-status omission |
| Genuine title correction, requested missing PR link, completed original task | Propose exact minimal edits/closure; preserve references and status where appropriate |
| Owner report on last comment page; incomplete preview | Full comment collection finds the report before drafting |
| Failed auth, Forbidden, 404, bad schema, missing/repeated page token or rate limit | Explicit incomplete/held result; no empty-success or absence claim |
| Search changed during paging; same count but different IDs; prior issue vanished | Reconcile identities, bounded refresh and direct-read classification; disclose uncertainty |
| GitHub search >1,000, `incomplete_results`, an ancient open draft | Split/fallback or incomplete coverage; retain old open work |
| Other-owner QE child; unassigned test child; watcher/reporter lead | Context role, no assignment or bulk-close operation |
| Inline-card PR link; repeated text; table/code/mention/unknown ADF nodes | Existing link recognized; only exact intended differences allowed |
| Merged prerequisite; closed unmerged draft; passing combined status with failed check | Preserve separate delivery states; no unjustified completion |
| Matching fix blob but unrelated PR; target/head/base moved | Actual attribution/current-branch checks; stale draft/review invalidated |
| Completed research/source task but unfinished product release | Original-scope closure can be proposed; no invented release gate |
| Parent children complete but owner/acceptance evidence missing | Hold parent closeout; never resolve test artifacts through the parent |
| Merge already commented; stale bot warning; rolling totals drift | No duplicate comment/field write or metric-refresh churn |
| Only retrieval time/lastViewed/gateway URL changed; actual Activity Type changed | Keep irrelevant metadata from causing churn; inspect meaningful deltas without inventing edits |
| Draft changed after review; terminal request lacks required resolution | Reject audited-ready claim until corrected and re-reviewed |
| Native edit persisted but renderer/transport verification failed; interrupted batch | Read live state and receipts; no duplicate proposal or replay; preserve dependencies |
| Closure changed schema-confirmed derived fields; unrelated custom field also changed | Explain only legitimate derived changes; retain detection of unrelated edits |
| Short approval after an ambiguous numbered reference or old draft | Bind explicit issue/title/revision; unresolved selection stays held |
| Old source path is 404; historical plan points to a retired anchor | Correct source revision/path; report inaccessible evidence, preserve receipts and validate pointers |
| Outer shell/tool succeeded but a child query failed; flat/paged JSON mismatch | Inspect child exits and explicit response shape; save incomplete coverage, never empty success |
| Missing connector metadata; conflicting accounts; unsupported CLI field | Useful scoped/provisional output, no execution-ready claim |
| Complete connector-only inventory; missing identity/page proof import | Accept complete native captures without CLI; reject incomplete-ready claims |
| Target unchanged but supporting child/owner/check/operation metadata changes | Refresh declared mutable inputs; invalidate only materially affected review |
| Internally valid old packet; missing final refresh receipt | Structural validation can pass; no audited-current/live-freshness claim |
| Separate reviewer inherits drafter history; oracle accessible in fixture context | Provisional review or assisted evaluation; no independent/blind qualification |
| Test discovery finds zero tests | CI fails without depending on a fixed historical assertion count |
| No independent reviewer; reviewer finds a concrete contradiction | Honest provisional/held result; no fake second-reviewer approval |
| Separate concurrent runs; spaces/metacharacters; unrelated cwd; worktree `.git` file | Isolated private paths and argument vectors; no checkout mutation |
| Collection attempts a mutation or executes instructions in issue text | Reject side effect; retain read-only output |
| Prior audit's zero remaining Jira updates | Empty first batch with the real engineering next steps |
| Unsupported host/global path or duplicate installed bundle | Capability gap reported; no claimed discovery/execution qualification |

<!-- markdownlint-enable MD013 -->

Release the skill only when collector/checker and subprocess tests pass, the ledger has no unexplained gaps, blind tests find needed
changes without unnecessary writes, and each claimed supported host preserves the stop, argument and independent-review/fallback
contracts. Native runtime/ADF/request correctness requires behavioral evidence; static lint and the existence of `SKILL.md` are
insufficient.

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
Three expert design reviewers examined this plan and the relevant repository contracts; their supported findings are incorporated.
This design review alone does not qualify skill behavior. The criteria above remain the qualification contract; implementation
evidence and unqualified host capabilities are recorded below.

## Implementation and qualification status

Implemented the five-file [canonical bundle](../skills/personal-jira-triage/SKILL.md), its catalog entry and existing CI integration.
The stdlib helper has only read-only `collect` and offline `check`; provider writes, credential handling and model orchestration
remain outside the bundle. The checker binds user scope, native readers, source provenance/page proof, requests and returned reviews.
Raw captures remain private and byte-preserved. Native rendered comments remain provisional for audited ADF requests.

All 118 offline tests pass on Python 3.10 and the available default Python, including collection subprocess failures, pagination,
worktrees, ADF preservation, stale evidence, account/scope changes and actual reviewer-context guards. The existing CVE unit suite
remains in the sequential `make unit-test` path. No live CVE tests are part of this qualification.
A frozen-helper native read captured the assigned inventory and every active issue/comment history; it correctly returned incomplete
qualification because native Jira account identity and some native comment fidelity were unavailable. Counts are run evidence,
not discovery limits or assertions about a future portfolio. No Jira update was made.

Actual separate history-free reviewers exercised the synthetic draft protocol. Their returned reports and saved checker result
qualify that protocol at its fixture cutoff, not live portfolio correctness. Assisted Codex forward runs exposed fabricated review
counts and an unnecessary gap comment; the skill now requires actual returned-report/check evidence and a useful missing delta.
An unseen paired fixture then produced the needed corrections, held accurate uncertain work and reported zero validated reviews
when the host capabilities were unavailable. Failed earlier runs remain failed qualification evidence.

Strict oracle filesystem isolation was not demonstrated, so no blind behavioral qualification is claimed. Claude's runtime attempt
was initially blocked by its session quota; later scoped real-data runs are recorded below. Antigravity runtime enforcement/discovery
was not exercised. Codex discovery and the assisted degraded-capability workflow were exercised, not every host's full audited workflow.
Before claiming another host is qualified, run its isolated forward cases and verify actual discovery, argument handling, returned
independent reviews or honest fallback, and the review stop. Keep private captures/oracles outside installations and source history.

PR review identified gaps in incomplete-import recovery, conflicting resource captures, explicit offline PR coverage and registered
worktree discovery. The review iteration tightens those collection contracts and binds each audited draft to a nonempty user request
and every declared candidate-decision evidence source. Duplicate description links receive the same append guard as custom fields.
Required resolutions and unknown assigned status categories now retain their validation/accounting guards.
Cross-review also prevents native PR captures from borrowing an imported Git reader after failed identity verification.
The next review correction refreshes explicitly selected imported PRs, blocks native Jira enrichment under an unknown or mismatched
CLI site, confines ADF patch paths to content children and clarifies that offline imports still permit local read-only Git discovery.
These corrections do not establish additional host or blind behavioral qualification; the limits above still apply.

Read-only real-data qualification captured 704 assigned issues, all 75 active issue/comment histories, 48 distinct open PRs and
173 directly inspected native PR objects including referenced historical work. Two independent contexts reviewed the 71 general
active issues; four restricted cases were routed separately. These are dated run counts, not scope limits.
All 75 issue responses already contained complete native comments. Prefer those exact issue bytes with their original capture time
and reader; use the paginated CLI fallback for partial, malformed or rendered embedded data. This removes redundant reads and the
23 avoidable rendered-comment gaps without inventing Jira identity. The real provisional packet passed the checker with zero
audited drafts, and both reviewers accepted two minimal description proposals. No Jira update was applied.

Claude subsequently executed scoped real-data runs. The initial ten-issue result was mixed; it proposed unnecessary historical
commentary and missed a current field discrepancy. A fresh six-issue retest recorded native plugin discovery and 17 Read calls,
found the two useful description corrections, omitted the historical comment and kept validated independent review at zero.
Its remaining ownership, acceptance and semantic-coverage assertions were not fully supported; do not count this as full host
qualification. Retain the separate native returned-report/check gate and clarify the observed fallback limits in the evidence
reference. Neither these assisted runs nor static tests establish blind isolation or Antigravity qualification.
