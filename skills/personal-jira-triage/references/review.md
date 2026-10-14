# Independent review

Review the first selected batch, not every candidate in parallel. Use two history-free contexts with different lenses.
Supply only the user scope, full inventory/coverage ledger, raw-source access and selected decisions/drafts/identities/hashes.
Do not supply drafter reasoning, a suggested verdict or oracle answers. Separate calls on one model can qualify; inherited-history
agents and same-context persona switches cannot. Record the actual context mode and reviewer identities.
Count a review only after the native delegation returns an actual report and a nonempty execution/context ID distinct from the other lens.
Save the returned report and native proof; record its `execution_id` in the review. Waiting, planned calls, personas and empty agent
results do not establish review. No returned independent reports means explicitly zero reviews and provisional drafts.
Initialize validated independently reviewed draft coverage to zero. Derive it only from successful saved `check` output
`audited_drafts` and the actual native returned-report ledger; absent packet/check/report ledger means zero validated drafts.
Do not substitute a semantic-review count from personas or waits. Verify execution IDs/results before saying delegation started
or completed. Returned findings with unvalidated provenance can inform a provisional draft without establishing a qualified audit.

Use host-native delegation only when authorized. Constrain reviewer tools to reading evidence and reporting findings with the
host's actual capabilities; portable frontmatter is not enforcement. Reviewers must not mutate Jira, PRs, source files or the
shared packet. Return findings to the drafter to save. An unavailable independent-review capability produces provisional drafts,
not fabricated approval or a requirement to install software, launch nested model CLIs or spend on another service.

## Evidence and coverage prompt

> Independently audit the selected drafts against raw evidence. You are read-only and must not change the shared packet or services.
> Check stated discovery coverage, authenticated identity and ownership, original acceptance, exact source/PR/artifact attribution,
> current state, missing pages and negative/unknown evidence. Look for both unjustified proposed changes and necessary changes the
> ledger missed; request raw evidence for suspicious no-change or held items rather than accepting a summary as proof.
> Keep source changes, publication, installation and QE acceptance separate. A matching title/blob or merged prerequisite does not
> establish original-task completion. Do not impose extra acceptance criteria or rerun engineering workflows.
> For each selected draft return accept, revise, hold or unreviewed, bound to its exact hash and decision-input fingerprints.
> Each finding identifies the draft/issue, disputed claim, primary source and consequence. Missing evidence is a limitation, not
> proof of absence. Do not invent findings to appear adversarial. State precisely what you did and did not examine.

## Necessity and preservation prompt

> Independently challenge every selected operation. You are read-only and must not change the shared packet or services.
> Does it fix a useful current contradiction or missing delta? Check complete existing comments, related tracking and receipts for
> duplication. Reject metric churn, stale-warning cleanup, retrospective stories, ownership transfer, speculative milestones,
> newly invented acceptance criteria and bulk closure of test/dry-run children. Verify that user-selected scope includes other-owned
> or unassigned changes.
> Missing commentary alone is not necessity. Require misleading current tracking, an explicit useful request or an actionable
> handoff; leave accurate open work/uncertain acceptance in the summary or held ledger and unused batch capacity empty.
> Check exact old/new wording, native ADF nodes/marks/unknown attributes, links/card URLs and preserved fields. Check live operation,
> permission/visibility and transition/resolution metadata, dependent request order and remaining unfinished work. HTML rendering
> is secondary; it must not reconstruct the source. Hold ambiguous node matches or unavailable sensitive-operation evidence.
> For each selected draft return accept, revise, hold or unreviewed, bound to its exact hash and decision-input fingerprints.
> Each finding identifies the draft/issue, disputed claim, primary source and consequence. Do not manufacture objections or imply
> that a successful schema check proves acceptance. State what remains uncertain.

## Record and resolve

Save each returned verdict in `reviews.json` using the exact format in [the evidence reference](evidence.md).
Use distinct reviewer IDs, observed native execution IDs and both `evidence` and `preservation` lenses.
Record actual returned findings rather than reconstructing imaginary approvals from your own analysis.
Review the current draft hash and every declared decision
input fingerprint. A concrete unresolved material objection holds the candidate; majority voting cannot remove it.
Fix supported findings from evidence, recompute the changed draft hash and re-review affected drafts.

After review, refresh mutable decision inputs: target and supporting issue/owner/child evidence, comments, relevant PR heads/checks
and operation/visibility metadata. Refresh artifact availability only when acceptance depends on it. Reuse immutable source blobs.
Store the actual refresh receipt and cutoff; materially changed input fingerprints invalidate affected reviews even when the target
issue itself did not change. A receipt cannot make stale captures fresh. Bound the correction loop and hold persistent disagreement.

Run the offline checker, then report inventory, deep-triage and independently reviewed first-batch coverage separately.
Save its actual output beside the packet and verify the reviewed-batch IDs against both returned reviewer reports before presenting.
Sampling unchanged issues does not independently audit the whole portfolio. Unsupported review remains explicit; useful provisional
drafts may still be presented for user review, with a read-only stop.
