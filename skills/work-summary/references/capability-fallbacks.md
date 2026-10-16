# Work summaries with incomplete tooling

Start with the capabilities actually available in this session. A missing CLI is
not a reason to stop when another source can provide useful evidence. Do not
install tools, sign in, request new permissions, or fetch source repositories as
a prerequisite to producing the summary. Use already authorized read access.

| Available access | Collect | State the limit |
| --- | --- | --- |
| Python, Git, authenticated CLIs | Run the collector. | Retain each source's gaps. |
| Git and Python only | Local commits, refs and worktrees. | Remote activity and PR state are unverified. |
| Git and shell, no supported Python | Read known repos with native Git. | Name inspected repos/refs and identity/date limits. |
| Python and remote CLIs, no usable Git | Collect remote sources independently. | Local repos and backups were not searched. |
| Connector, no shell | Read native accessible records. | Name sources and operations not searched. |
| Browser only | Read native pages and visible pagination. | Visibility and paging may limit the inventory. |
| Supplied links, notes or saved capture | Summarize their original evidence. | This is not a fresh account search. |
| No readable evidence | State the lack of accessible evidence. | Do not invent work or an empty activity history. |

The script requires Python 3.10+ and Git 2.37+ for local collection. Direct Git
reads need no Python: use supported read-only commands, filter native timestamps,
and verify author identities. Do not claim that the Python collector or renderer
ran. Preserve which repositories and refs were inspected.

Mobile or cloud sessions may have connectors but no local filesystem, Bash,
Python or Git. In that case do not offer shell commands as the execution path,
claim a local scan happened, or fabricate collector JSON to imply validation.
Produce the same useful work list directly from connector evidence. When file
tools exist, a saved capture can be read without claiming a fresh native search.

Authentication or permission errors affect the failing source only. Preserve
evidence already collected from other sources and earlier successful pages.
Do not quote credential values, dump authentication configuration, or interpret
an inaccessible repository/ticket as nonexistent. A connector may provide a
working alternative to an unauthenticated CLI; verify its native account rather
than assuming the CLI and connector use the same identity.

For connector collection, resolve the current native user and requested time
window, then read authored changes, accessible participation events, and older
open work as separate evidence. Follow pagination and explicit Jira/PR links when
the available interface supports them. Exact native actors and event timestamps
support personal activity; assignment, watcher status, mentions and current
states provide context. If only search snippets or titles are available, describe
the limited observation and avoid claims about implementation, impact or delivery.

Write a concise ranked bullet list by default: concrete delivered results first,
then substantive investigation/review work, then ongoing work with its current
observed state. Ranking should use supported significance and completion, not
raw commit counts. Combine duplicate commits, PRs and tickets into one work item,
showing both links when native evidence establishes their relation. Local-only
bullets can identify the repository briefly when no verified web link exists;
retain full paths and hashes in private evidence or optional details. Keep older
backup work outside weekly accomplishments unless exact-window evidence supports
that framing. A missing PR match never proves unpublished work.

Begin or end with a short source note, for example: "Based on accessible Jira
and GitHub records for the last 7 days; local Git and backup branches were not
available." Preserve private/restricted associations only within the private
summary and avoid restricted titles. List a source as empty only after a complete
successful search for the stated scope. Offer the detailed inventory when useful;
do not bury the work list behind setup instructions or thousands of warnings.
