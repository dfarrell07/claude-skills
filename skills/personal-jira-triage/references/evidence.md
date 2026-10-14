# Evidence, packets and drafts

The helper uses Python 3.10+ and the standard library. It provides `collect` and offline `check`; there is no writer.
Resolve its absolute path relative to the loaded skill, and pass arguments as arrays. Confirm options with `--help`.

```text
python3 ABSOLUTE_HELPER collect --run-dir ABSOLUTE_PRIVATE_DIRECTORY
  [--imports ABSOLUTE_MANIFEST] [--offline]
  [--issue KEY]... [--pr URL]... [--root PATH]... [--plan-root PATH]...
  [--github-host HOST] [--timeout SECONDS]
python3 ABSOLUTE_HELPER check ABSOLUTE_PACKET_DIRECTORY
```

Use new or empty unique private run paths outside repositories/installations, normally under the user's state directory.
Never store tokens, private Jira exports or internal issue content in the public skill tree. Keep existing checkouts, configurations
and completed receipts intact. On resume, inspect current evidence and prior receipts before proposing the same delta again.

`collect` saves `packet.json` and raw evidence under `raw/`; the agent authors decisions, drafts, reviews and refresh receipts.
`--issue` selects enrichment and adds non-inventory issues as context; it does not authorize another owner's edits.
During online collection with imports, explicit `--issue` keys omit their imported issue/comments and recapture them into the new run.
Without explicit keys, complete imported captures retain their original cutoff; importing an old file does not refresh it.
Incomplete imports may be replaced by a complete online native read; retain a named gap if recapture fails.
Offline selection uses supplied captures and reports missing selected issue/comment sources.
`--pr` fetches explicit PRs during native collection; offline collection reports a gap for each explicit PR lacking a complete
matching import.
Incomplete collection returns exit 2 while retaining named gaps.
Valid empty collection is success. `check` returns 0 for valid packets, including honest held/provisional results; invalid packets or
unsupported audited-ready claims return 2. It checks consistency with saved evidence, not live freshness or semantic acceptance.
Save the actual `check` output beside the packet. An empty `audited_drafts` list means zero validated independently reviewed drafts;
without a successful saved check and actual native returned-report ledger, reviewed-batch coverage remains zero.

## Collect and enrich

Start with authenticated identity and paginated assigned inventory across all accessible projects/statuses; use status category for
active work. Do not cap at a familiar issue count. Discover open authored/assigned/review-requested PRs without creation-date filters.
Additional recent merged/closed searches are leads; always fetch an older directly referenced PR.
Union explicit issue/PR requests, plan references, related tracking, optional registry roots and local worktrees.
Plan regex matches are unverified leads: read the relevant plan, expand compact keys/ranges such as `PROJECT-10/11`, and verify
actual project/key identities. A finding prefix is neither universally Jira nor universally excluded by spelling alone.
Classify other-owner/unassigned issues as context; their relationship alone does not authorize a change.
Complete default discovery requires assigned inventory, active `reporter`, `watcher` and `former-assignee` Jira context queries,
plus open Git `author`, `assignee`, `review-requested` and `involves` searches. Unsupported queries remain named gaps.
Context roles are retained separately; merging relationship roles into one issue must not convert another owner's work into assigned work.
Local discovery records its roots, depth-four boundary, exclusions and every discovered clone/worktree path.
Enumerate registered worktrees for discovered repositories, including worktrees outside the scanned roots;
inaccessible roots or worktrees remain gaps.

The helper's CLI captures are the starting point. The agent must enrich relevant comments, history, review threads, checks/statuses,
field schema and operation metadata through available native reads. An unsupported service/capability is a named gap.
Successful collection validates captured scope; it does not prove uncaptured services, PR roles or relationships were searched.
Do not use an old collector's capped/cache/date-filtered output to claim completeness. Do not extract connector credentials.

Record query, capture time, unique IDs and actual page termination. Reject repeated tokens, missing pages, contradictory duplicates
and failures disguised as empty lists. Split GitHub searches exceeding its cap/incomplete-result signal or disclose incomplete scope.
Different source IDs do not make conflicting captures of one service/site/kind/resource compatible. Keep one assigned snapshot;
reject conflicting copies rather than unioning their membership.
The host re-queries assigned membership before presentation and classifies disappeared/new members by direct reads; equal counts
can hide different issues. On drift, use the latest complete assigned capture in a new packet, never union temporal inventories.
Carry forward only decisions and reviews still justified by matching evidence fingerprints.
The helper invokes each native read once with a timeout; it does not implement rate-header retries.
The agent may make bounded native retries only for clearly transient failures; authentication/permission/schema failures remain gaps.
Inspect every child exit and validate explicit resource shapes.

Every deeply triaged issue needs full native fields and complete accessible comments. Use relevant history to resolve ownership or
scope changes. PR title/blob equality is a lead, not merge attribution. Read native merge state and relevant ancestry; compare the
current target branch separately from an old PR base SHA. Keep combined statuses and individual check failures/skips/missing jobs
distinct. Collect domain artifacts only when original acceptance requires them; an unavailable artifact is not proven absent.

## Connector-only imports

Export native reads through available host tools into a private directory. Keep raw captures unchanged and hash their bytes.
Pass a manifest with `collect --imports ABSOLUTE_MANIFEST --offline --run-dir ABSOLUTE_NEW_RUN_DIRECTORY` to avoid CLI calls.
Complete imported inventory/issues/comments can replace unavailable `acli`; corresponding Git captures can replace missing `gh`.
Missing identity, metadata or page proof remains a gap. The helper invokes no connector and manages no credentials.

The envelope is `{"schema_version": 1, "sources": [...]}`. Each source has:

| Field | Meaning |
| --- | --- |
| `id` | Explicit source identifier when another record refers to it |
| `service`, `site` | `jira`, `git`, or a distinct domain service for artifacts; selected native site/host identity |
| `kind`, `resource` | Supported kind and exact logical identity listed below; retain actual queries in pagination proof |
| `schema` | Nonempty descriptive native schema/tool label; resource `kind` determines validated shape |
| `identity_ref` | Source ID for authenticated native identity; required for account-bound reads |
| `captured_at` | Original ISO capture time with timezone; copying a file does not refresh it |
| `raw_path`, `sha256` | Relative file inside the manifest directory and SHA256 of its exact bytes |
| `complete`, `pagination` | Completeness assertion supported by actual page termination |

Paths cannot escape that directory or follow symlinks. Unknown client version alone is not a gap when the native schema and
authenticated evidence are supported. Collection validates shapes and fingerprints; do not flatten arbitrary nested JSON until it fits.

Supported native capture kinds:

| Kind | Raw response |
| --- | --- |
| `jira_identity` | `/myself` object with `accountId` |
| `jira_inventory` | Complete native search object, flat issue list or explicit paginated lists |
| `jira_context` | Same issue shapes, with contextual relationship roles |
| `jira_issue` | Native issue object, including its full fields |
| `jira_comments` | Complete native comment object, flat list or explicit paginated lists |
| `jira_history` | Complete native changelog object, flat list or explicit paginated lists |
| `jira_metadata` | Object with relevant `editmeta`, `transitions`, `permissions`, `comment_visibility` |
| `git_identity` | Native `/user` object with `login` and `id` |
| `git_search` | Complete native PR search object or explicit captured lists |
| `git_pr` | Native REST PR object |
| `git_checks` | Object containing `check_runs` and `statuses` |
| `artifact_identity` | JSON string preserving the native reader principal, such as actual `oc whoami` output |
| `artifact` | Nonempty unmodified native JSON object, with explicit reader access |

Use `resource: "assigned"` for assigned inventory. Jira contexts use `reporter`, `watcher`, `former-assignee` or `explicit`;
issue/comment/history/metadata resources use the exact issue key. Git search resources use the four role names above;
native PR resources use `owner/repository#number`. Jira sites identify one exact HTTPS host; user-content URLs remain unchanged.
Online CLI evidence binds to imported `/myself` only when the CLI's authenticated email matches its native `emailAddress`;
missing cross-client identity proof leaves CLI evidence provisional, and a mismatch is a gap.

For artifact captures choose a distinct service such as `openshift`, exact HTTPS site and native resource identity.
Use `access: "authenticated"` with a complete same-service/site `artifact_identity` reference, or `access: "anonymous_public"`
for an actual anonymous public read without a fabricated identity. Artifact reader identity never establishes Jira ownership.
Reviewers verify the claimed access; the checker validates integrity/provenance, not whether a service is truly public.
Artifact fingerprints bind native data, service/site/resource/schema/access and identity reference, excluding capture time.

For complete flat inventory/comment/history captures, use `pagination: {"terminated": true}` only after reading every page.
Native paged responses must include all advertised rows. An embedded preview is incomplete. Conflicting CLI/connector accounts
must remain separate; never select whichever identity makes an ownership assertion pass.
For actual concatenated `acli` comment-page stdout, use `schema: "acli-comments-json-stream-v1"` and preserve the exact bytes;
page offsets/limits, totals, termination and unique IDs must reconcile. Imports retain original raw bytes and hashes.
Rendered comment bodies/author strings remain useful accessible evidence with `fidelity: "rendered"` and a provisional gap.
An audited comment baseline needs actual full native ADF, author account IDs and visibility evidence; setting a fidelity flag
cannot upgrade rendered text. A full native embedded comment list is usable only when all advertised rows are present.

For example, two imported Jira sources identify the current account and its complete assigned inventory:

```json
{
  "schema_version": 1,
  "sources": [
    {
      "id": "jira-me", "service": "jira", "site": "https://example.atlassian.net",
      "kind": "jira_identity", "resource": "/rest/api/3/myself", "schema": "jira-rest-v3",
      "captured_at": "2026-10-14T12:00:00Z", "raw_path": "me.json",
      "sha256": "REPLACE_WITH_FILE_SHA256", "complete": true, "pagination": {}
    },
    {
      "id": "assigned", "service": "jira", "site": "https://example.atlassian.net",
      "kind": "jira_inventory", "resource": "assigned", "schema": "jira-rest-v3",
      "identity_ref": "jira-me", "captured_at": "2026-10-14T12:00:00Z", "raw_path": "assigned.json",
      "sha256": "REPLACE_WITH_FILE_SHA256", "complete": true,
      "pagination": {"terminated": true, "query": "assignee = currentUser() ORDER BY key"}
    }
  ]
}
```

Replace hashes with actual file hashes; the files contain native responses, not this example's explanation or a model summary.
Use the real selected site/account. The example is synthetic, and collecting only these two sources does not deeply triage issues.
It returns named discovery gaps for absent contextual/Git captures; add those captures to establish complete default discovery.

## Packet and authored records

`packet.json` contains `schema_version`, `collected_at`, `identities`, `sources`, `inventory`, `gaps`, `leads`, `local` and `scope`.
Each source includes its stable ID, service/site/kind/resource, capture time/schema, identity reference, raw path/hash,
decision `fingerprint`, completeness and pagination. Inventory rows contain `key`, `role`, `active` and available issue/site identity;
roles are `assigned`, `context` or `specialist`. `scope.selected_context_keys` records explicitly selected other-owned context.
Before drafting, record the actual user request in `scope.user_request`; audited drafts require a nonempty string.
Do not leave the collector's default scope as proof of intent or replace the request with inferred authorization.
Retain raw hashes for provenance; fingerprints omit known transport metadata but conservatively retain full native fields and content.
They also bind service/site/resource/schema, reader reference, access, completeness, pagination and fidelity proof.
Artifact fingerprints additionally bind the captured byte hash; ordinary JSON formatting changes do not invalidate a review.
A real incidental field change may require re-review; it is not a reason to invent an update or add an arbitrary field-ignore mask.

The agent writes three sibling JSON lists. IDs refer to saved sources; do not invent an unsaved evidence ID.

* `decisions.json`: one row per active assigned issue, with `key`, `disposition`, `reason`, `evidence` source IDs and `draft_ids`.
  Dispositions are `no_change`, `candidate`, `held`, `needs_decision` and `specialist`.
* `drafts.json`: `id`, `key`, `kind`, exact native `request`, `baseline`, `evidence`, `decision_inputs`, `preserved_fields`,
  `scope_hash`, `ready`, `hash`.
  `baseline` binds issue, comments and relevant metadata source IDs. `decision_inputs` lists every supporting input, including child,
  owner and PR/check evidence, including the baseline IDs and each referenced native identity source ID.
  Each draft must include all evidence declared by its candidate decision in `decision_inputs`; review those sources and refresh
  their mutable evidence.
  This reader-input closure prevents an unchanged identity reference from hiding a changed principal. `ready` defaults to `provisional`;
  `audited` requests validation of an audited claim, not proof of it.
* `reviews.json`: `reviewer_id`, observed native `execution_id`, `lens`, `context_mode`, `draft_id`, `draft_hash`,
  `evidence_fingerprints`, `verdict`.
  Lenses are `evidence` and `preservation`; audited drafts require distinct reviewers with `history_free` contexts and `accept` verdicts.
  Save actual returned reports/native execution proof. A nonempty claimed ID alone cannot prove a delegation happened;
  actual host calls/results must support it. Empty waits or self-checks leave drafts provisional.

The draft hash covers its entire canonical JSON object except `hash`. Set the requested `ready` value before hashing and submitting
to reviewers; changing it afterward changes the reviewed revision. Only a successful check with both current reviews and a real
refresh establishes the audited claim. Bind reviews to that exact hash and every decision-input fingerprint.
Do not reuse a review after changing wording, operation order, ownership or supporting evidence.

Compute the hash with this exact Python serialization, where `draft` is the draft object:

```python
import hashlib
import json

value = {key: item for key, item in draft.items() if key != "hash"}
encoded = json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
draft["hash"] = hashlib.sha256(encoded).hexdigest()
```

For audited drafts first set `scope_hash` to the same canonical hash algorithm applied to `packet["scope"]`, then hash the full draft.
This binds the saved user request and selected context keys; changing scope invalidates the old draft/reviews.
Provisional drafts may omit it, but a supplied scope hash must match. This is evidence binding, not authorization to write.

Supported draft request shapes are:

```json
{"kind": "fields", "request": {"fields": {"summary": "Corrected title"}}}
{"kind": "comment", "request": {"body": {"type": "doc", "version": 1, "content": []}}}
{"kind": "transition", "request": {"transition": {"id": "LIVE_ID"}, "fields": {"resolution": {"id": "LIVE_ID"}}}}
```

These illustrate request shapes, not complete draft records or useful empty comment bodies. Field edits support `summary`,
`description` and editable custom fields with existing native ADF or an explicitly supported native textarea schema.
Direct assignee/status/resolution field changes are disallowed.
Comments use the explicitly selected visibility at creation. Transitions require live metadata and required resolution values.
The helper validates mechanics; semantic necessity, original acceptance, audience suitability and authorization remain agent/reviewer work.

ADF field drafts also declare `adf_changes`, mapping each changed field to exact patches. A replace patch contains `op: "replace"`,
an existing text node's terminal `text` path, exact `old` string and new `value` string. An append patch uses `op: "append"`, an
existing native `content` list path and a whole new node as `value`. Replacing subtrees, marks, attributes or unknown nodes is
unsupported; hold such edits. The checker reconstructs the full document and rejects undeclared differences. For example:

```json
{
  "adf_changes": {
    "description": [{"op": "replace", "path": ["content", 0, "content", 0, "text"], "old": "Old text", "value": "New text"}]
  }
}
```

A null/empty built-in description may start from an empty native document and use `append` at `["content"]`.
An empty custom field needs live editable textarea metadata for that initialization. Existing documents are never rebuilt;
their marks, attributes and unknown nodes remain intact, and adding an already-present link is rejected.

This footprint is part of the draft hash; semantic necessity and meaning still need independent review.
When native baseline content is unavailable, keep the human draft provisional in the summary;
do not fabricate an exact native request or baseline to satisfy the checker.

For each human draft also provide issue key/title, current status/owner, exact old/new wording, reason/primary evidence,
dependency order, status/resolution choice including `keep`, preserved fields and unfinished work.
Comment absence alone is not a useful delta. Accurate open tracking or uncertain acceptance normally belongs in the summary/held
ledger, unless misleading current tracking, an explicit request or an actionable handoff establishes a needed update.
Preserve native ADF unknown nodes/marks/attributes and URLs; detect `inlineCard`/`blockCard`, mentions and structural separators.
Only identified node edits/missing exact link additions are appropriate. Ambiguous matches stay held. HTML is secondary evidence.
Combine compatible description changes into one request. A dependent transition must follow verified link updates with a fresh
post-link baseline; a draft batch does not authorize execution or promise an old baseline will remain valid.

## Final refresh and limits

Refresh selected mutable decision inputs through real native reads after review. Store `packet.refresh` with `captured_at`,
`source_ids` and `fingerprints` mapping each refreshed source ID to its saved decision fingerprint.
For CLI target refresh, run `collect --imports BASE_MANIFEST --issue KEY --run-dir NEW_PRIVATE_RUN` without `--offline`;
this recaptures selected issue/comments while retaining unrelated imported cutoffs. For connector-only refresh, export genuinely
fresh native captures and use `collect --offline --imports FRESH_MANIFEST --issue KEY --run-dir NEW_PRIVATE_RUN`.
Neither route fabricates a final receipt; supporting metadata/child/PR evidence needs its own fresh captures.
Cover target state/comments, relevant supporting owner/child evidence, PR heads/checks/statuses and needed operation/visibility metadata.
Reuse immutable blobs; do not refresh unrelated inventory indefinitely. Changed material inputs invalidate affected reviews.
The receipt cutoff cannot precede the referenced captures. Current material reviewer objections hold the draft;
historical objections bound to older payload/evidence revisions remain history rather than permanently blocking a corrected draft.

An offline check cannot create a genuine live refresh. Valid old or incomplete packets remain useful provisional records;
audited-current describes the stated capture cutoff, never permanent freshness or permission to write.
For an interrupted prior write, reconcile receipts with current native state before redrafting. Derived status/chart changes need
schema/history evidence; transport `self`/icon URLs can differ without changing native IDs. Never normalize user-content links to
make preservation pass or replay an operation merely because its renderer failed.
