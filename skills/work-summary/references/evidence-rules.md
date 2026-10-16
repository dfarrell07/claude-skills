# Evidence and attribution rules

The collector applies these rules automatically; when it runs, you do not need to
repeat them. Read this file when collecting by hand or through connectors (see
[reduced access](reduced-access.md)), when auditing a surprising claim, or when
changing the collector. The short version is in SKILL.md: credit only the user's
own actions with native actor and timestamp proof inside the window, treat roles
and current state as context, and treat unavailable access as unknown, not empty.

## Local Git

Default local roots are home and cwd. Inventory top-level/deep clones, bare
backups, symlinks with cycle detection, and registered worktrees outside roots.
Retain stale/inaccessible registrations and actual exclusions. Skip dependency,
credential/tool-state caches and pytest fixtures. Explicit roots replace defaults.
Unreadable directories are coverage gaps; never bypass their permissions.

An unresolved home must leave explicit roots/cwd usable. Local ownership needs
configured author emails or native account-verified aliases. Names alone are
unverified leads. Keep author and committer dates separately: old changes
recommitted this week are not necessarily new implementation work.

Retain exact refs/object types, immutable tips, sanitized fetch/push remotes,
cached default refs, detached heads, and backup snapshots. Undated tree/blob
refs are context, not authored commits. Deduplicate clones, rebases, backports,
commits, PRs and tickets when describing accomplishments.
For stale worktrees, read the surviving Git administration metadata and HEAD
reflog before declaring evidence missing. Record recovered tips separately from
actual refs. A missing working directory alone does not mean its stored history
was lost. Read-only recovery must leave permissions, refs and registrations intact.
Read worktree-specific `refs/worktree`, `refs/bisect` and `refs/rewritten` in each
registered or retained-admin context; the main checkout's ref listing omits them.
Bind each native namespace response to its actual common directory, keep those
reference labels distinct from shared refs, and preserve any disappeared paths
as changes between observations rather than erasing earlier captured history.

## GitHub activity and comments

GitHub discovery uses the authenticated account on `GH_HOST` (default
`github.com`): authored updates, older open authored/assigned work, review
requests, and recent review/comment/involvement candidates. Search caps and
paging/count drift remain explicit. Candidate searches have no upper updated
cutoff; native actor/event times determine the exact window. Preserve ID-less
cross-reference events using native source identity. Comment edits require
native matching editor proof. For edited comments that could overlap the window,
read bounded native revision history: a later bot/other-author edit can hide an
earlier personal edit. Preserve every revision, distinguish verified latest edits
from unresolved historical personal edits, and retain failed/deleted histories as
specific gaps. Roles and updates alone do not prove personal work. Mention/subscription,
assignment, requested-review and cross-reference events are context unless
separate substantive activity supports the described work; an actor/time match
alone does not make a passive event an accomplishment.
The current comment body is context, even when its creation or edit actor/time
is verified. Other edits can change that body; do not attribute exact wording
to a historical event without native revision-content evidence. Preserve
contradictory body observations and their uncertainty.

## Branches and their PRs

For branch PR associations check all states, saved authored indexes, independent
any-author SHA search, and commit associations. A commit association alone can
miss closed unmerged PRs. Preserve positives even when sibling reads fail.
If a saved remote is inaccessible, local-only or missing, a global SHA search
can recover associations in readable repositories. Retain the original remote
access or provider gap; a global negative cannot prove every private repository
or another provider was searched. Verify
renamed repository IDs and repeat searches under the canonical name: a successful
zero-result search for an old name may omit the current repository entirely.
Distinguish a successful lookup of an explicitly absent commit object in a
verified repository from a failed request, missing repository or malformed
response. Preserve failed/capped reads, unsupported hosts and name-only matches
as unresolved. A completed negative lookup means only no association found by
the supported methods for that exact snapshot; deleted forks and rewritten
history can hide past associations. It does not prove unpublished work.
Preserve distinct native PR observations when heads or bodies change during
collection; an exact-SHA proof must match the particular captured observation
that supplied it, rather than assuming every response observed the same head.

## Historical PR heads

For related PRs with matching branch names or captured native timeline leads,
read paginated force-push history and compare native before/after commit hashes
against all captured local snapshots. Keep the full native event and PR identity
with each historical-head proof. A matching name is only a lead; a native former
head proves an association, not authorship or new work during the requested week.
Follow the filtered connection's cursors to completion: its reported total may
describe the unfiltered timeline and must not be treated as a filtered event count.
When a historical after-commit object is unavailable, read the native paginated
REST timeline: its force-push event can retain the hash. Keep any still-unknown
before head distinct from the recovered after head. If commit objects remain
opaque, try the public native PR timeline, including its hidden-event pagination.
Accept full hash pairs only from native event containers bound to the same REST
event ID, time and after hash, with matching native compare and commit URLs.
Retain the pages and replayable proof; PR prose and abbreviated hashes cannot
prove a former head. A complete alternate read resolves that projection gap;
other failed pages or unavailable records remain separate limits.

## Jira

Jira uses the authenticated tenant/account. Retain all-status assigned,
reported, created, watched and formerly assigned inventories plus recent
participation leads, with independent before/after counts. Read every captured
ticket's details/comments. A detail response may contain a nonzero-offset
comment suffix: retain those native comments immediately, read missing history,
and preserve richer native rows when merging projected pages. Count/identity
drift stays incomplete while valid comments and links survive. Projected rows
without native actors/times cannot prove weekly activity.
Request all readable detail fields, including custom fields: a narrow field list
can omit the ticket's current PR/MR field. Bind custom-field links to their native
field ID and observation. When enriching a saved narrow capture, retain the
original responses and add an authenticated full-field observation separately.
Follow moved Jira keys using independent authenticated reads of the requested
and canonical keys with equal native issue IDs. Preserve both native responses
and the original reference URL. A readable canonical issue survives a failed
alias-verification read, but the unresolved alias cannot prove a ticket relation.
Check actual changelog objects and comment fidelity rather than assuming that
an installed CLI exposes every API capability. When a supported authenticated
REST reader or connector is available, use native paginated history and comment
reads to fill those gaps. Otherwise retain the exact missing comment IDs,
required read routes and native failure responses in the private capture.

Assignment, watching, reporting and updatedBy are leads/context. Native comment
actors/timestamps support activity. Day-level queries and current state do not
prove exact field-change history; unavailable changelogs, deleted comments and
missing editor evidence remain explicit limits. Observations are not an atomic
historical snapshot.

## Following references

Start reference discovery from independent account/role inventories, local work
and user-selected evidence, including candidates whose event reads are incomplete.
Resolve their direct ticket/review counterparts, attempting failures once.
A fetched contextual review can resolve its ticket without making that ticket
a new source of unrelated reviews. A reference alone does not create another
work root; independently verified personal activity can promote context and
revisit its deferred references. Preserve unexpanded targets and their origins
separately from failed reads. Do not cap the number of independent work roots.
PR references can reveal tickets absent from inventories. Do not confirm relations from
partial version strings such as `release-4.20` or `release-4-20`.
Lowercase path/branch tokens are discovery leads; an existing same-key ticket
does not corroborate a relation. Confirm pairs through explicit native URLs,
conventional uppercase ticket references, or native Jira references to the PR.
Review the surrounding meaning before listing a pair. Foreign-site Jira URLs
do not identify a same-key ticket on the active site. Keep failed lexical leads
separate from explicit Jira URLs and native issue references: a token such as
`AES-256` plus an ambiguous native error does not establish a missing ticket.
Preserve every attempted key and failure without excluding unknown projects.
Verify GitHub
repository transfers using independent native reads with equal repository IDs;
keep historical source URLs beside canonical relations.
Include native issue/review comments and top-level review bodies in reference
discovery, including other authors and dates outside the weekly window. Preserve the
comment URL as relation evidence; a reference does not credit its author to the
user or make an old discussion a weekly accomplishment. Failed guessed keys
need their exact native failure and source context; a CLI's combined
"does not exist or no permission" response cannot establish either condition alone.

## Sensitivity, GitLab and public history

Preserve sensitivity and native relation provenance. Restricted titles must
stay out of the work list; private links remain private. Native Jira GitLab MR
URLs are contextual counterparts; do not infer their state/ownership or send
them to GitHub APIs. Keep native captures outside public PRs and fixtures.
For configured GitLab remotes, also read advertised MR head refs through existing
native Git access. API authentication and Git SSH access are independent: an empty
SSH agent does not rule out a configured identity. Bind the actual remote route,
retain native ref responses and match full hashes across captured snapshots.
These proofs can supply MR links; they cannot establish MR state or activity.
Run that read with the verified URL from an empty directory, never inside the
checkout: copied checkouts carry config that can run commands or redirect credentials.
Read anonymous MR details only from gitlab.com, configured remote hosts and hosts
named in Jira review fields or your own PR descriptions; a host named only in someone
else's comment stays unread context.
Try native public item and history endpoints independently of authentication
status endpoints. Collect readable GitLab MR details, notes and public role
inventories; match native public email to a previously verified alias before
crediting the numeric account. Read current descriptions and captured discussion
bodies across providers within that work/counterpart scope. Public Jira
field history supplements authenticated details and comments without replacing
their visibility or counts. Retain endpoint-specific failures and scopes.
Follow explicit review URLs in retained Jira history too, keeping the native
field, timestamp, and old/new side. Historical additions and removals are read
targets and context, separate from current ticket/PR pairings; a removed link
must not reappear as a core counterpart in the work list. Process newly discovered
tickets' histories before declaring the eligible counterpart frontier closed;
this does not claim that every transitive third-party reference was collected.
