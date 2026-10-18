# Work summaries with reduced access

Use the capabilities this session actually has. A missing CLI, expired login or
one provider's rate limit affects only that source: keep collecting from the
others and name what was not searched. Do not install tools, sign in, request new
permissions, fetch repositories or extract credentials as a prerequisite.

| Available access | Collect | State the limit |
| --- | --- | --- |
| Python 3.10+, Git, authenticated `gh`/`acli` | Run the collector (SKILL.md). | Its coverage warnings. |
| Python and Git only | Run the collector; it skips remote sources. | Remote activity and PR state are unverified. |
| Git and shell, no supported Python | [Native Git](#native-git-without-python) for known repos. | Repos/refs inspected; identity and date limits. |
| Python and remote CLIs, no usable Git | Run the collector; it skips local Git. | Local repositories and backups were not searched. |
| GitHub/Jira connectors, no shell | [Connector quick path](#connector-quick-path). | Searches and pages not available or not read. |
| Browser only | Native pages and their visible pagination. | Visibility and paging may limit the inventory. |
| Supplied notes, links or a saved capture | Summarize that evidence with its own dates. | This is not a fresh account search. |
| Nothing readable | Say no personal work could be verified. | Never invent work or call the history empty. |

Never claim the collector, renderer or validation ran when it did not, and never
fabricate collector JSON to make other evidence pass validation. Without a shell,
write the same ranked bullet list directly. [Evidence rules](evidence-rules.md)
has the full attribution detail when collecting by hand.

## Connector quick path

Mobile and cloud sessions may have GitHub or Jira connectors but no filesystem,
shell, Python or Git. Do not offer shell commands as the path forward there.

1. Establish identities through the connector: the GitHub authenticated user
   (`GET /user`) and the Jira current user (`/rest/api/3/myself`), plus the GitHub
   host and Jira site. If a connector and a CLI use different accounts, keep them
   separate. An explicitly requested other person is a different scope.
2. Compute the exact window: the last N×24 hours for weekly mode, or the
   confirmed calendar interval for quarterly mode (see quarterly-reviews.md).
3. Run these GitHub searches with `updated:>=START` (date precision is fine; the
   exact window is applied to events later), following every page:
   `is:pr author:@me`, `is:pr reviewed-by:@me`, `is:pr commenter:@me`,
   `is:pr involves:@me`, `is:issue involves:@me`. Also list open work without a
   date bound for context: `is:open is:pr author:@me`, `is:open assignee:@me`,
   `is:open is:pr review-requested:@me`. Searches return at most 1,000 results;
   narrow by `created:` ranges when a total exceeds that.
4. Run this JQL, following pagination:
   `(assignee = currentUser() OR reporter = currentUser() OR creator = currentUser()
   OR watcher = currentUser() OR assignee WAS currentUser()) AND updated >= "START"`.
   Add `issuekey IN updatedBy("ACCOUNT", "START", "END")` when supported.
5. For the most significant hits, read the item itself: PR merge time and author,
   your reviews and comments with their timestamps, Jira comments with their
   author account IDs and times, and explicit PR/Jira links in bodies and fields.
6. Write the ranked list from SKILL.md. Add one source sentence when a missing
   source could hide work, e.g. "From GitHub and Jira connectors; local Git and
   backup branches were not available."

Search snippets or titles alone support only a limited claim; avoid statements
about implementation, impact or delivery that the read records do not show.

## Native Git without Python

Use known work repositories and the exact window. This is a bounded manual
inventory, not the collector's recursive discovery. For each repository set
`REPO` to its path and use a read-only helper:

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
```

Deduplicate repositories by their common Git directory. The ref inventory
includes backup refs, remote-tracking refs and tags, not only local branches;
record non-commit objects as context and use tags peeled to commits. Registered
worktrees supply detached HEADs; keep stale registrations as gaps.

Put the full commit hashes of those tips, one per line, in a private file named
by `TIPS`, then read the history from those immutable tips:

```bash
git_read log --stdin --no-use-mailmap --no-show-signature --no-color \
    --encoding=UTF-8 \
    --format='%H%ncommitter %ct %cI%nauthor %at %aI%nidentity %an <%ae>%nsubject %s%n' \
    < "$TIPS"
```

Filter committer timestamps to the window and keep author timestamps separately:
an old change recommitted this week is not necessarily new work. Do not stop at
the first old commit, because timestamps can move backward along history. With
Git 2.37+, `--since-as-filter` reduces output without pruning traversal; with
older Git omit date filters and filter the output. Truncated output, a timeout or
a max-count makes coverage incomplete, not empty.

Credit commits only to configured or account-verified author emails; names and
committer identity are leads, not proof. Do not fetch, repair worktrees, rewrite
refs or scan unreachable objects.

## Provider notes

- **GitHub:** search caps, unknown totals, unread pages, access errors and failed
  lookups are coverage gaps. Keep earlier pages after a 403, rate limit or expired
  session. A failed commit lookup cannot show that a branch has no PR, and a
  connector that exposes only default branches cannot show that local or backup
  branches do not exist.
- **Jira:** credit activity only from native comment author account IDs and
  timestamps. Display names, bodies or projected rows without actors cannot prove
  who acted or when. Keep restricted titles private. If Jira is unavailable,
  explicit Jira URLs in PR bodies remain unverified context. Likewise PR URLs from
  Jira when GitHub is unavailable.
- **GitLab:** API authentication and Git SSH access are independent. For a known
  project, a bounded `git ls-remote --refs REMOTE 'refs/merge-requests/*/head'`
  can match MR heads to local commits without fetching. Run it with the remote's
  URL from an empty directory rather than inside the checkout, so the checkout's
  own config (credential helpers, proxies, SSH commands) cannot take part. A match
  supplies an MR link, not its state, author or activity.
  Anonymous public MR detail reads can work without login, but only for hosts
  the user already works with.
- **Per-operation access:** a denied user endpoint does not make public item,
  comment or history endpoints unreadable, and an anonymous 404 cannot erase an
  authenticated positive. Never disable TLS verification; a command-local OS CA
  bundle is acceptable when a configured CA file is missing.

The Python GitHub client accepts any callable returning native REST/GraphQL
responses, so a connector that returns native responses could feed it. Map only
fields whose native meaning is supplied; a normalized search excerpt is not that
schema, and `acli` output cannot be imitated to make another Jira reader fit.
