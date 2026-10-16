# Collect through available connectors

Missing tools, expired authentication, or one provider's rate limit must not stop
collection from other available sources. Record which sources and operations
were actually available, then produce the report supported by that evidence.

| Available access | Useful evidence | Leave unresolved |
| --- | --- | --- |
| Local Git only | Verified commits, refs, backup heads and cached containment. | Remote activity and PR associations. |
| GitHub only | Native PRs/issues, actor events and supported associations. | Jira participation and local backups. |
| Jira only | Native tickets, comments and explicit review links. | Unread PR state/activity and local work. |
| Connector/API, no shell | Native records actually exposed. | Missing identities, paging, dates, access or refs. |
| No readable source | Identified user-provided evidence. | Unobserved work and complete-coverage claims. |

Discover the tools exposed in the current host before choosing a connector. Do
not assume a connector, tool name, shell, filesystem, or local checkout exists on
mobile or in a cloud runtime. Use an already available authenticated read-only
API/connector before suggesting installation or login. Continue with the other
sources and name the missing coverage.

For GitHub, establish the configured host and target account before collecting
work. The native [authenticated-user endpoint](https://docs.github.com/en/rest/users/users#get-the-authenticated-user)
can establish the session's user. An explicitly supplied target account is a
different scope: record that choice, and do not mistake an app/service identity
for the person whose work is requested. Preserve native item IDs, canonical
URLs, state, authors, creation/update/merge times, and available title/body/ref
text. Authored, assigned, requested-review, and involvement matches provide
context; verified actor identities and exact-window event times establish
activity. An updated item alone does not establish the person's activity.
If Jira reads are unavailable, retain explicit Jira URLs as unverified context.
If GitHub reads are unavailable, retain Jira-provided PR URLs as unread context. Do not count an unavailable reader as a native attempt.

Follow the provider's native pagination and retain completeness evidence.
[GitHub REST pagination](https://docs.github.com/en/rest/using-the-rest-api/using-pagination-in-the-rest-api)
uses response links; GraphQL connections expose cursors. Search result caps,
unknown totals, unconsumed pages, access restrictions, and failed lookups remain
coverage gaps. Retain earlier pages and successful sibling lookups after a
403, rate limit, missing executable, or expired session. A failed or null commit
lookup cannot establish that a branch has no PR. A repository connector that
exposes only a default branch cannot establish the absence of local branches or
backup refs.

For Jira, establish the authenticated site and account ID through a capability
that actually supplies them, such as the native
[current-user endpoint](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-myself/#api-rest-api-3-myself-get).
Collect accessible all-state role context and explicit referenced tickets;
attribute activity only when native account IDs and timestamps prove it. The
[issue-comments endpoint](https://developer.atlassian.com/cloud/jira/platform/rest/v3/api-group-issue-comments/#api-rest-api-3-issue-issueidorkey-comment-get)
provides paginated comment evidence. Preserve a native suffix page even when its
offset is nonzero, and attempt the missing history. Display-name/body-only
projections can retain issue-bound references with real comment IDs, but cannot
prove who acted or when. Preserve private markings and original reference URLs.

The Python GitHub client accepts a callable returning native REST/GraphQL
responses; its transport need not invoke `gh`. A connector's search excerpt or
normalized tool result is not automatically that response schema. Map only
fields whose native meaning is supplied, preserving their source provenance.
The Jira CLI collector expects actual `acli` output; do not fabricate its auth,
count, or pagination text to make a different connector fit. If a capability
cannot supply the collector's required evidence, produce a source-cited partial
report or keep its raw capture separately. Do not invent collector JSON, actor
IDs, dates, totals, empty bodies, or successful lookups to satisfy validation.
