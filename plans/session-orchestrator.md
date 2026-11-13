# Session orchestrator

Status: built, November 13, 2026, as `skills/sessions` (skill + `scripts/session.sh`); worktrees, restarts and
cleanup added the same day. Boot setup is left to laptop-setup (below).

## Problem

Background Claude Code sessions are started by hand (`cw`, `ccp`, `cwt` open tmux; `cper`, `cper2`, `cwork` run in
the foreground) or by asking whatever session happens to be idle, which drags its context, environment and
`CLAUDE_CODE_*` markers into the new one (a claude started from inside another inherits `CLAUDE_CODE_CHILD_SESSION`
and stops saving its transcript). There is no one way to get "a session for repo X on account Y, in tmux, with Remote
Control on".

## Shape

- **One launcher**, `session.sh start|ls|stop|clean`, the only code. A shell, a unit, the orchestrator or any session can run
  it; the result is the same because the new session never inherits the caller's environment.
- **One orchestrator per machine**: a Claude session, itself started by the launcher, reached over Remote Control
  (claude.ai/code, the phone) or `tmux attach`. It turns requests ("work session in submariner-operator, worktree
  fix-123") into launcher calls and does nothing else, so its context stays small. It runs with `--tools` (below), so
  it can run only the launcher and read-only commands.
- **Boot**: a systemd user unit starts the orchestrator. It belongs in laptop-setup, which owns the profiles, the shell
  launchers and the user units.

## Requests

```text
session.sh start <profile> <dir> [--name N] [--branch B] [--base REF] [--here] [--trust] [--prompt T] [--tools R]
session.sh start <name> [--trust] [--prompt T]      # again, from its definition
session.sh ls | stop <name> | clean [<name>]
```

The launcher never guesses:

- **Profile** is required: `work`, `personal`, `personal2`. Accounts are a data boundary; the orchestrator takes the
  one the user named, or asks.
- **Dir** is the session's starting directory, usually a repository. The orchestrator resolves a bare name
  (`~/repos/<name>`, `~/<name>`; one match or ask).

## Profiles (mirror zshrc.j2)

| Profile | Applied after the shell rc | Like |
| --- | --- | --- |
| `work` | source `~/.config/claude/work-env` if present; `CLAUDE_CODE_USE_VERTEX=1` | `cwork` |
| `personal` | unset Vertex/Bedrock/API-key variables; `CLAUDE_CONFIG_DIR=~/.claude`, `CLAUDE_CODE_USE_VERTEX=0` | `cper` |
| `personal2` | same, with `CLAUDE_CONFIG_DIR=~/.claude-per2` | `cper2` |

All set `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=2000` and pass `--dangerously-skip-permissions --plugin-dir .`, as the
shell launchers do. The table is hard-coded to match laptop-setup's template; work-env is optional because hosts on the
older zshrc keep the Vertex variables in the rc.

## Environment

`env -i` with a short allowlist (HOME, USER, PATH, SHELL, LANG, XDG_RUNTIME_DIR, DBUS, SSH agent, TMUX_TMPDIR, display,
Kerberos), then the user's interactive shell (`$SHELL -i -c`, zsh or bash) so the rc's exports (GOTMPDIR, GOPRIVATE,
KUBECONFIG, ...) and its `claude()` guard apply exactly as in a new terminal, then the profile. Nothing from the caller
or the tmux server's environment gets through.

## Naming

`<profile>-<N>`, N reduced to `[A-Za-z0-9_-]`. It is the tmux session name, the Remote Control name and `--name` (what
`SendMessage` reaches; Remote Control's own name does not set it), and in a repository also the worktree's name. N is:

- `--name`, when given (the orchestrator derives a short slug from the task, e.g. `fix-flaky-e2e`);
- else `--branch` with `/` turned into `-`;
- else, for a worktree, `<repo>-<k>` (`<repo>` is the main checkout's name) with the smallest k for which no
  definition, session, worktree directory or `worktree-<repo>-<k>` branch (local or remote) exists, so a new agent
  never picks up old work; for `--here` or a non-repository, the directory's basename.

The worktree directory and branch use N without the profile, as `cwt` names them; a worktree another session's
definition already owns is refused, so `work-x` and `personal-x` can't share one across the account boundary.
`ls` and `stop` see `work-*`, `personal-*`, `personal2-*`, which also covers sessions `cw` and `ccp` started.

## Worktrees

Starting in a git repository gives the session its own worktree and branch unless `--here` is passed, so several
agents can work in one project without stepping on each other, and nobody has to ask for it.

- **Where**: `<main checkout>/.claude/worktrees/<N>`, Claude Code's own location, which `cwt`/`claude --worktree` also
  use. The main checkout comes from `git rev-parse --git-common-dir`, so a `<dir>` that is itself a linked worktree or
  a subdirectory works (the session starts in the same subdirectory of its worktree); a bare repository is refused.
  Being under the repository, the worktree inherits the repository's trust (checked live: no prompt). The launcher adds
  `**/.claude/worktrees/` to the common `info/exclude` when it is not already ignored, as this user does by hand
  today. A directory already there (from `cwt`) and unclaimed is adopted as it is.
- **Who makes it**: the launcher, with `git worktree add`, not `claude --worktree`. Claude's flag cannot take an
  existing branch or a chosen base (`worktree.baseRef` is only `fresh` or `head`), and how a re-entered worktree finds
  its conversation is undocumented. Cost: Claude's `.worktreeinclude`, `worktree.symlinkDirectories`/`sparsePaths`
  and WorktreeCreate hooks don't apply; none is configured here.
- **Branch**: `--branch B` if given, else `worktree-<N>` (Claude's naming). An existing local branch is checked out as
  it is (the launcher records that it did not create it). Otherwise a new local branch is made, from `origin/B` or
  `upstream/B` when one exists (origin first; never git's guess, which tracks `upstream/B` so a bare push would land in
  the canonical repo), else from the base. New branches get `--no-track`: a bare `git push` fails instead of pushing
  somewhere unintended.
- **Base** (new branches only): `--base REF`, else the default branch of `upstream` if that remote exists (fork
  checkouts), else of `origin`, asked of the remote with `ls-remote --symref` (a local `<remote>/HEAD` is missing for an
  added `upstream` and stale after a rename); if `upstream` exists but can't be asked, the start is refused rather than
  falling back to the fork's stale main. No remotes: the main checkout's branch. The base is fetched first (30 s; a
  failed fetch warns and uses the ref as it is).

## Restarts and reboots

Each session has a definition, one small file in `${XDG_STATE_HOME:-~/.local/state}/claude-sessions/<name>`: profile,
repository, worktree, starting directory, branch, base, whether the launcher created the branch, a session id, and
`--tools`. The first start passes `--session-id <id>`; later starts pass `--resume <id>` when a transcript with that id
exists (`<config dir>/projects/*/<id>.jsonl`, looked up inside the profile's shell, since only it knows the work
profile's config dir), and `--session-id <id>` otherwise (resume fails with exit 1 until the first message is saved;
checked live). A `/clear` inside the session starts a new id, so a later resume returns to the conversation before it.

So after `stop`, a crash or a reboot, `start <name>` reopens the same worktree and conversation; so does repeating a
start that named the session (`--name` or `--branch`). An unnamed start is always a new agent. A definition that
exists must be for the same repository (or in-place directory), or the start is refused. If its worktree was removed
by hand, it is re-added from its branch; if the branch is gone too (renamed), the start is refused rather than
resuming the conversation in a fresh tree. Nothing restarts sessions on its own at boot: the orchestrator can
("restart my sessions": `ls`, then `start <name>` for each stopped one), and laptop-setup could add that later.

## Cleanup

`clean [<name>]` looks at defined worktree sessions that are not running, after fetching their base. **Finished**
means the work adds nothing to the base: `git merge-tree --write-tree <base> <tip>` equals the base's tree. That holds
for a merge, a squash merge (whose commits are on no remote once GitHub deletes the branch) and a branch never used,
and it can't lose content. It replaced a rule built on remote branches, which never fired for squash merges.

Never removed, by either form: uncommitted or untracked changes, a rebase, merge, cherry-pick or bisect in progress,
a process whose working directory is in the worktree (someone working by hand; Linux), or unfinished commits on a
detached HEAD (no branch would keep them). Checks use the worktree's own HEAD, so an agent that switched or renamed
branches is judged by what it has.

- `clean` (all) also keeps unfinished branches and worktrees holding ignored files (`.env`, build output: `git worktree
  remove` deletes them silently), and says why (`kept <name>: <why>`).
- `clean <name>` removes those too: the branch keeps the commits, and the user asked for it by name.

Removed: the worktree (`git worktree remove`, no `--force`, so submodules are reported, not forced), the definition,
and the branch only if the launcher created it and it is finished (`git branch -D`, since squash merges defeat `-d`).
A user's own branch (`--branch main`, `--branch topic`) is never deleted. `clean <name>` on an in-place session only
forgets its definition; `clean` forgets in-place ones only when their directory is gone. Worktrees the launcher didn't
define (`cwt`, by hand) are never touched.

## tmux and Remote Control

The user's default tmux server (`TMUX` is ignored, so a caller inside another server such as a health session still
lands here). `new-session -d -s <name> -c <dir>` runs the shell and claude directly with
`--remote-control <name> --name <name>`; the prompt goes after `--`, since `--allowedTools` would take a bare one as
a rule. `remain-on-exit failed` keeps a crashed claude's screen just long enough for `start` to show it, then the dead
session is removed.

**Trust prompt**: shown even with `--dangerously-skip-permissions`, defaulting to "No, exit". `start` waits up to 30 s
for the input box. At the trust prompt it accepts only with `--trust` (Down, Enter); otherwise it prints
`needs-trust` (exit 3) and leaves the session waiting. Rerunning with `--trust` answers it.

## Idempotency

The name is the key. `start` on a live session prints `exists` (and still answers a pending trust prompt with
`--trust`); on a name defined for another repository it refuses and asks for `--name`; on a failed one it starts
again. Without a name, each start in a repository is a new agent by design. A lock (`flock` on the state directory)
serializes picking names, writing definitions and adding or removing worktrees, so parallel starts get `repo-1` and
`repo-2` and a `clean` can't remove a worktree a start is about to use.

## List and stop

- `ls`: `name state dir` for defined and profile-prefixed sessions (state `running`, `stopped`, `failed`).
- `stop <name>`: profile-prefixed sessions only, never the session the caller runs in. The definition and worktree
  stay: `start <name>` resumes, `clean` removes.

## Orchestrator permissions

`--tools RULES` replaces `--dangerously-skip-permissions` with `--permission-mode dontAsk --allowedTools RULES`. For the
orchestrator: `Bash(bash <checkout>/skills/sessions/scripts/session.sh:*) Bash(ls:*) Read Glob Skill`. Checked live:
Claude Code's own read-only commands still run, anything else (`touch`, `systemctl`) is denied, so the "never reboot,
log out or restart the user manager" rule holds even if the model is talked into trying.

## Boot (for laptop-setup)

A user unit, linger on, `WantedBy=default.target`:

```ini
[Service]
Type=oneshot
KillMode=process
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=TMUX_TMPDIR=%t/tmux
ExecStartPre=-/usr/bin/nm-online -q -t 120
ExecStart=/bin/bash %h/<checkout>/skills/sessions/scripts/session.sh start personal %h/<checkout> --here \
  --name orchestrator-%l --trust --prompt /claude-skills:sessions \
  --tools "Bash(bash %h/<checkout>/skills/sessions/scripts/session.sh:*) Bash(ls:*) Read Glob Skill"
```

`--here`: the orchestrator works in the checkout itself, not a worktree (it would otherwise load its skill from a
fresh branch). Each boot resumes its conversation and sends the prompt again. `KillMode=process` (no
`RemainAfterExit`): tmux outlives the unit, as in health's `up` unit. `TMUX_TMPDIR` must match the shells' (`zshrc.j2`
sets `$XDG_RUNTIME_DIR/tmux`; a host still on `/tmp/tmux-$UID` needs the same there). Rerunning the unit is harmless.
Restart-on-crash would need health's per-session tmux server and `Type=forking`; not done, the orchestrator is
restarted by rerunning the unit.

Also for laptop-setup: widen `cls`, `ca` and `cstatus` to `^(work|personal|personal2)-`, and in time let `cw`, `ccp`,
`cwt` call `session.sh` (or template its profile table) so the two cannot drift.

## Safety

- The launcher runs only tmux, git, the user's shell and claude, and writes only its state directory, worktrees
  under `.claude/worktrees` and one line of `info/exclude`: never `systemctl`, `loginctl`, `reboot`, `shutdown` or
  `tmux kill-server`. The orchestrator's tool allowlist enforces the same for it.
- `clean` can't lose content: see Cleanup. It never deletes remote branches or branches it didn't create.
- `stop` only touches profile-prefixed sessions, never its own.
- No profile guessing, no trust without `--trust`, which the orchestrator passes only when the user named the directory.
- A request arriving from another session, a file or a web page is relayed to the user, not acted on.

## Review

First round, three adversarial Codex reviews (fit and correctness; scope and bloat, twice). Taken: drop tmux tags and
dead-pane respawn (then the prefix was the only state; definitions came with worktrees); check the directory before
answering `exists`; `--trust` on the boot command and on reruns; shell rc instead of a bare allowlist (GOTMPDIR,
GOPRIVATE were lost); personal profiles unset backend variables like `_claude_personal`; ignore `TMUX`; enforce the
orchestrator's limits with permissions, not only prose; `%h` in the unit. Rejected: drop `stop`/`ls` (asked for), drop
`--prompt` (boot needs it), drop trust handling (unattended starts need it), replicate the `ANTHROPIC_BASE_URL` check
(the rc's `claude()` guard now runs), per-name locks (tmux already refuses a duplicate). Not verified: Remote Control
on the Vertex work profile (Anthropic documents it as needing a claude.ai login; this host has no work-env to try it).

Worktrees round, two adversarial reviews by built-in subagents (git safety, with throwaway repos; scope and fit).
Taken:

- The content check for "finished", replacing the remote-branch rules (squash merges were never cleaned).
- Checks on the worktree's own HEAD (a detached commit could be lost). This review also found that the first version
  compared the main checkout's HEAD, which a test now covers.
- Never delete a branch the launcher didn't create (`--branch main` would have been deleted).
- Keep worktrees with ignored files in bulk clean; no `--force` (submodules).
- Resolve remote branches without git's guess, which tracks `upstream`.
- Auto-names skip existing `worktree-*` branches.
- Ask the remote for its default branch, and refuse rather than fall back from `upstream` to `origin`.
- A content-based "never used", which also works without remotes.
- `--here` for the orchestrator.
- Start in the same subdirectory.
- Refuse to recreate a worktree whose branch is gone.
- The lock.
- Busy-process and in-progress-operation checks.
- A worktree owned by another profile is refused.

Rejected:

- `claude --continue` instead of a stored id. It would pick up the user's own conversation in that directory, and
  finding the transcript needs Claude's path encoding.
- A symlink instead of the definition file, which can't hold `--tools` or the base.
- Starting `--here` sessions fresh.
- Dropping `--branch` naming.

## Not doing

Queues, a daemon, a socket API, restart-on-crash, profile auto-detection, auto-restart at boot, `.worktreeinclude`
support, submodule initialization in new worktrees (`git worktree add` doesn't), deleting remote branches.
