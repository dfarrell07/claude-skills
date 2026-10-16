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

GitLab API authentication and Git SSH access are separate capabilities. An
unauthenticated API response or an empty SSH agent does not establish that an
existing checkout cannot read its remote: SSH may use a configured identity
directly. For a known GitLab project, a bounded, noninteractive native
`git ls-remote --refs REMOTE 'refs/merge-requests/*/head'` can retain advertised
MR numbers and exact head hashes without fetching objects or creating a token.
Bind the remote's native host/project to the checkout, retain the response, and
compare full hashes against captured snapshots. Preserve failures and malformed
rows. A positive proves an advertised head association; it supplies no MR title,
state, discussion history, actor, or activity timestamp. No advertised match
cannot establish that an old, rewritten, deleted, or inaccessible MR never existed.

Mobile or cloud sessions may have connectors but no local filesystem, Bash,
Python or Git. In that case do not offer shell commands as the execution path,
claim a local scan happened, or fabricate collector JSON to imply validation.
Produce the same useful work list directly from connector evidence. When file
tools exist, a saved capture can be read without claiming a fresh native search.

Authentication or permission errors affect the failing operation. Preserve
evidence already collected from other sources and earlier successful pages.
Do not quote credential values, dump authentication configuration, or interpret
an inaccessible repository/ticket as nonexistent. A connector may provide a
working alternative to an unauthenticated CLI; verify its native account rather
than assuming the CLI and connector use the same identity.

Test access per operation. A denied authenticated-user endpoint does not prove
that public item, inventory, comment, or history endpoints are unreadable.
Existing native Git credential helpers can make HTTPS Git reads usable when
SSH fails; invoke them through Git without printing credentials or changing
persisted authentication. If a native TLS error names a missing configured CA
file, a command-local installed OS CA bundle can restore that read while keeping
certificate verification enabled. Retain both attempts; never disable TLS checks.
For explicit GitLab MR URLs, try verified-TLS native public detail reads, native
repository redirects, and paginated notes/discussions. If the numeric and encoded
project REST notes/discussions routes fail, try the native anonymous read-only
GraphQL notes connection independently. Bind its native project and MR IDs, full
note IDs, target URLs, counts and cursor pagination; retain all failed REST
observations alongside readable GraphQL notes. A complete connection can recover
note coverage without claiming private-account visibility. Preserve native
`system` and `internal` values and full note observations; confidentiality fields
that the native schema does not expose remain unknown. Keep native author GIDs
separate from REST numeric IDs and bind their numeric user identity before
crediting personal activity. Current bodies are context, and `updatedAt` alone
does not prove a personal edit: retain distinct native creation and edit times
and any explicit native editor. A native public email
matching an already verified account alias can establish the target's numeric
GitLab identity for public authored, assigned and review inventories. GitLab
requires a public email to be verified; retain the native identity object and
alias basis rather than matching usernames across services. See the
[GitLab Users API](https://docs.gitlab.com/api/users/).

For Jira, separately try public issue identity and paginated field history at
the authenticated site's native endpoint. Verify issue IDs and keys before
combining observations. Anonymous visibility can be narrower than authenticated
visibility: retain public history as a separate scope and preserve authenticated
comments and their counts. An anonymous zero or 404 cannot erase an authenticated
positive or establish that a private item/comment does not exist. Keep exact
endpoint failures; public histories and public MR inventories do not establish
complete private-account coverage.

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
showing the core ticket and implementation links when each helps the reader.
A valid relation alone is not an instruction to display every connected URL. Local-only
bullets can identify the repository briefly when no verified web link exists;
retain full paths and hashes in private evidence or optional details. Keep older
backup work outside weekly accomplishments unless exact-window evidence supports
that framing. A missing PR match never proves unpublished work.

Keep local repositories without a configured GitHub remote in the evidence,
including test fixtures and backup clones. A completed global SHA search with
no match leaves GitHub repository association unsupported; it does not imply
missing account data or prove the work unpublished. Describe this scope as
context. A failed native global search remains partial, and a positive match
retains its native proof regardless of the local repository's path or provider.

Completed snapshot lookups can retain a PR with the same branch name without
proving that the captured commit belonged to that PR. Mark this `related_only`
and keep the name-based candidate as context. It is not a failed native read,
an exact PR association or proof of unpublished work. If any snapshot lookup
failed, retain `partial` even when a name-based candidate exists.
The same applies when a recorded force-push history read for that candidate PR
failed; completed SHA searches do not erase a genuine history coverage gap.

Begin or end with a short source note, for example: "Based on accessible Jira
and GitHub records for the last 7 days; local Git and backup branches were not
available." Preserve private/restricted associations only within the private
summary and avoid restricted titles. List a source as empty only after a complete
successful search for the stated scope. Offer the detailed inventory when useful;
do not bury the work list behind setup instructions or thousands of warnings.
