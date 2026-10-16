# Work summaries with incomplete tooling

Start with the capabilities actually available in this session. A missing CLI is
not a reason to stop when another source can provide useful evidence. Do not
install tools, sign in, request new permissions, or fetch source repositories as
a prerequisite to producing the summary. Use already authorized read access.

| Available access | Collect | State the limit |
| --- | --- | --- |
| Python 3.10+, Git 2.37+, authenticated CLIs | Run the collector on the requested roots and window. | Retain each source's recorded gaps. |
| Git and Python, no remote CLI or authentication | Use local commits, refs and worktree evidence. | GitHub/Jira were unavailable; PR state and branch delivery remain unverified. |
| Python and authenticated remote CLIs, no usable Git | Collect GitHub/Jira independently. | Local repositories and backup branches were not searched. |
| GitHub or Jira connector, without a shell | Read native accessible items and discussions through that connector. | Name the accessible sources and the local/remote sources not searched. |
| Browser access only | Read accessible native pages and visible pagination; preserve source links and dates. | Browser visibility/pagination limits prevent an exhaustive inventory. |
| Supplied links, pasted notes or a saved capture only | Summarize the supplied evidence with its original links and dates. | This is a summary of supplied evidence, not a fresh account or repository search. |
| No readable evidence | Say that the session has no accessible work evidence, then ask for a few links, notes or an available source. | Do not manufacture an empty activity history or invented work bullets. |

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
bullets can cite repository path and full commit hash when no verified web link
exists. Keep older backup work outside weekly accomplishments unless exact-window
evidence supports that framing. A missing PR match never proves unpublished work.

Begin or end with a short source note, for example: "Based on accessible Jira
and GitHub records for the last 7 days; local Git and backup branches were not
available." Preserve private/restricted associations only within the private
summary and avoid restricted titles. List a source as empty only after a complete
successful search for the stated scope. Offer the detailed inventory when useful;
do not bury the work list behind setup instructions or thousands of warnings.
