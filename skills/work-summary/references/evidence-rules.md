# Evidence and attribution rules

The collector applies these rules; when it runs you do not need to repeat them.
Read this file when collecting by hand or through connectors (see
[reduced access](reduced-access.md)), when auditing a surprising claim, or when
changing the collector. SKILL.md's credit rules are the short version.

## Local Git

- Scan home and the current directory by default; explicit roots replace them.
  Find ordinary clones, bare backups and registered worktrees, following symlinks
  without loops. Skip dependency and tool-state caches, test fixtures and
  directories owned by other accounts. A directory the user owns but cannot read is
  a stated gap; never bypass its permissions.
- Credit commits only to configured or account-verified author emails. Names are
  leads. Committer time places a commit in the window; an older author time marks
  a rebase or cherry-pick, not new implementation.
- Keep branch tips, backup refs and detached HEADs as snapshots. For a stale
  worktree, read its surviving administration files and HEAD reflog before
  calling its history lost. Never fetch, repair worktrees or change refs.
- Clones, scratch copies and generated test repositories repeat or fabricate
  history; deduplicate and check purpose before turning them into accomplishments.

## GitHub

- Use the authenticated account on `GH_HOST`. Discover candidates with authored,
  reviewed, commented, involved, assigned and review-requested searches. Search
  dates only select candidates; native event times decide the window.
- Credit only the user's own events with actor and time inside the window:
  opening, merging (`merged_at`), reviews and review comments, issue comments,
  force-pushes and state changes. Plain pushes and comment edits are not
  credited. Mentions, subscriptions, assignments, review requests and
  cross-references are context.
- Events are read for authored, reviewed and commented PRs and for all issues.
  PRs found only through involvement, assignment or review requests are context;
  their timelines are not read.
- Keep other people's comments and review bodies as reference evidence, never as
  the user's work. A current comment body may have been edited since; do not quote
  it as what was said at a particular time.
- Search caps, failed pages and count drift are coverage gaps, not empty results.

## Jira

- Use the authenticated site and account. Confirm the account through the
  assigned inventory. `updatedBy()` and a role search limited to tickets updated
  in the window (assignee, reporter, creator, watcher, former assignee) find
  active tickets; all-time inventories other than assignment are not searched.
- Credit comments only from native author account IDs and timestamps. Restricted
  comments without them, and day-level `updatedBy()` hits, are leads whose exact
  change is unverified. acli exposes no changelog; public history for publicly
  visible tickets can confirm individual field changes.
- Keep restricted titles out of the update. A "does not exist or no permission"
  response proves neither.

## Links between work items

- Pair a PR and a ticket only through an explicit Jira URL on the active site, a
  conventional uppercase key (`ACM-123`) in the PR title, body or a comment, or
  the ticket's own PR fields. Lowercase tokens and version strings
  such as `release-4.20` are not keys, and text such as `SHA-256` that matches no
  tenant project is not a ticket.
- Follow references from the user's own work: the tickets its PRs name and the
  PRs and MRs those tickets list. A reference never credits its author to the
  user or turns old discussion into new work.
- A removed link in ticket history is context, not a current pairing.

## GitLab

- Match advertised MR head refs to local commits with `git ls-remote` run from an
  empty directory against the verified remote URL, so the checkout's own config
  (credential helpers, proxies, SSH commands) cannot take part. A match supplies
  an MR link, not its state or activity.
- Read MR details anonymously only from gitlab.com, configured remote
  hosts and hosts named in Jira review fields or the user's own PR descriptions.
  Credit a GitLab account only when its public email matches an already verified
  alias.
