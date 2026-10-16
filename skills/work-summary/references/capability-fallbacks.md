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

## Native Git without Python

Use known work repositories and the requested exact UTC window. This is a
bounded manual inventory, not the collector's recursive discovery. For each
repository, set `REPO` to its actual path and use a read-only helper:

```bash
git_read() (
    unset GIT_DIR GIT_WORK_TREE GIT_COMMON_DIR GIT_INDEX_FILE
    unset GIT_OBJECT_DIRECTORY GIT_ALTERNATE_OBJECT_DIRECTORIES
    export GIT_TERMINAL_PROMPT=0 GIT_NO_LAZY_FETCH=1 GIT_ALLOW_PROTOCOL=
    git --no-pager --no-optional-locks -C "$REPO" "$@"
)
git_read rev-parse --git-common-dir
git_read config --get user.email
git_read for-each-ref \
    --format='%(refname) %(objecttype) %(objectname) %(*objecttype) %(*objectname)'
git_read worktree list --porcelain
git_read rev-parse --verify HEAD
```

Retain the native outputs privately. Deduplicate repositories by their canonical
common Git directory, resolving relative paths against the inspected repository.
The [ref inventory](https://git-scm.com/docs/git-for-each-ref) includes custom
backup refs, remote-tracking refs and tags, not just local branches. Record
noncommit objects as context. Use native commit tips and tags peeled to commits;
do not pass tree/blob objects as commit tips. The
[worktree inventory](https://git-scm.com/docs/git-worktree) supplies registered
HEADs, including detached heads; retain inaccessible/stale registrations as gaps.
An unborn or inaccessible HEAD does not invalidate the other readable refs.

Copy the captured full commit hashes, including readable worktree HEADs, into a
private file with one hash per line. Set `TIPS` to that file's actual path. Read
those immutable tips rather than moving branch names:

```bash
git_read log --stdin --no-use-mailmap --no-show-signature --no-color \
    --encoding=UTF-8 \
    --format='%H%ncommitter %ct %cI%nauthor %at %aI%nidentity %an <%ae>%nsubject %s%n' \
    < "$TIPS"
```

Read the complete reachable output and filter the actual committer timestamps
to the inclusive requested window. Keep author timestamps separately. Do not
stop at the first old-looking commit: timestamps can move backward along a
history. Ordinary `--since` can prune traversal and omit newer ancestors. With
Git 2.37+, [`--since-as-filter`](https://git-scm.com/docs/git-log) can reduce output
while still traversing the history; with older Git, omit traversal date filters
and apply the exact window to the emitted records. A max-count, timeout, truncated
tool response or unread page makes coverage incomplete, not empty.

Attribute work only to exact configured or independently account-verified author
emails from each relevant worktree; names and committer identity are not author
proof. Retain other plausible aliases as unverified leads. Without usable identity
evidence, describe observed repository changes without crediting them to the user.
Keep hashes and ref associations for deduplication; backup aliases, rebases and
old authored changes recommitted recently are not separate weekly accomplishments.
Do not fetch, repair worktrees, rewrite refs or scan unreachable objects as part
of this fallback. Missing objects, unsupported commands and unknown PR delivery
remain explicit limits. Manual evidence is not Python-validated evidence.

## Connectors and other partial evidence

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
