# Session orchestrator

Status: built as `skills/sessions` (`SKILL.md`, `scripts/session.sh`, `tests/test.sh`). Boot setup belongs to
laptop-setup (below).

## Problem

Background Claude Code sessions are started by hand (`cw`, `ccp`, `cwt` open tmux; `cper`, `cper2`, `cwork` run in
the foreground) or by asking whatever session is idle, which drags its context, environment and `CLAUDE_CODE_*`
markers into the new one (a claude started inside another stops saving its transcript). Several agents in one
repository share a checkout unless someone sets up worktrees by hand.

## Shape

- **One launcher**, `session.sh start|ls|stop|clean`, the only code. A shell, a unit, the orchestrator or any session
  can run it, with the same result: only a short allowlist of the caller's environment reaches the new session.
- **One orchestrator per machine**: a Claude session, itself started by the launcher, reached over Remote Control or
  `tmux attach`. The skill (its operating prompt, and the reference for request handling, output and cleanup) turns
  requests into launcher calls; it does no repo work, so its context stays small.
- **Boot**: a systemd user unit in laptop-setup, which owns the profiles, the shell launchers and the user units.

Not `claude --bg`: Claude's own background sessions have no per-session profile (account), no tmux, and refuse an
untrusted workspace instead of asking.

## Starting a session

The launcher never guesses: the **profile** is required, because accounts are a data boundary, and a bare directory
name is resolved by the orchestrator, which asks when unsure.

**Name**: `<profile>-<N>`, the tmux session, `--name` and the Remote Control name. N is `--name`, else `--branch`,
else `<repo>-<k>` for a new agent in a repository (the first k with no definition, session, worktree or
`worktree-<repo>-k` branch, so it never picks up old work), else the directory's name; reduced to `[A-Za-z0-9_-]`.
The name is the key: a running session is never started twice (`exists`); a name defined for another repository, or
with another `--branch`, is refused. Every name the launcher is given must have that shape, so no argument can name a
path outside its state.

**Profiles**, mirroring `zshrc.j2`:

| Profile | Applied after the shell rc | Like |
| --- | --- | --- |
| `work` | source `~/.config/claude/work-env` if present; `CLAUDE_CODE_USE_VERTEX=1` | `cwork` |
| `personal` | unset Vertex/Bedrock/API-key variables; `CLAUDE_CONFIG_DIR=~/.claude`, `CLAUDE_CODE_USE_VERTEX=0` | `cper` |
| `personal2` | same, with `CLAUDE_CONFIG_DIR=~/.claude-per2` | `cper2` |

All get `--remote-control`, `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=2000` and, unless `--allow` is given,
`--dangerously-skip-permissions`, as the shell launchers do; Remote Control on Vertex (`work`) is unverified, as for
`cwork` (the docs say it needs the Anthropic API). Unlike them, `--plugin-dir` is passed only for a directory with a
plugin manifest: a plugin under development, loaded with the same trust as that repository's own hooks, rather than
every repository loaded as a plugin. work-env is optional because hosts on the older zshrc keep the Vertex variables
in the rc.

**Environment**: `env -i` with a short allowlist (HOME, USER, PATH, SHELL, LANG, XDG_RUNTIME_DIR, D-Bus, SSH agent,
TMUX_TMPDIR, display, Kerberos, and the pane's own TMUX and TMUX_PANE), then the user's interactive shell (`$SHELL -i
-c`, zsh or bash), so the rc's exports (GOTMPDIR, GOPRIVATE, KUBECONFIG, ...) and its `claude()` guard apply as in a
new terminal, then the profile.

**tmux**: the user's default server, whatever server the caller is in. `remain-on-exit failed`: a claude that exits
with an error leaves its pane, so `start` can show its last lines, `ls` shows it as `failed`, and the next `start`
replaces it. A directory with `#` is refused (tmux reads it as a format).

**Trust**: claude asks even with `--dangerously-skip-permissions`, defaulting to "No, exit". `start` waits up to 30 s
for the input box; at the trust prompt it answers yes only with `--trust`, else reports `needs-trust` (exit 3) and
leaves the session waiting. The orchestrator passes `--trust` only for a directory the user named.

## Worktrees

In a git repository each session gets its own worktree and branch unless `--here` is passed, so agents never share a
checkout and nobody has to ask for one.

- **Where**: `<main checkout>/.claude/worktrees/<N>`, Claude's own location (`cwt` and `claude --worktree` use it). The
  main checkout comes from `--git-common-dir`, so `<dir>` may be a linked worktree or a subdirectory (the session
  starts in the same subdirectory, or at the top if the base lacks it); a bare repository is refused. Under the repository, the worktree inherits its
  trust. `**/.claude/worktrees/` goes into `info/exclude` unless already ignored. A new session whose worktree path
  already exists is refused: nothing is adopted (a `cwt` worktree on another branch, or a stray directory in which the
  session would run on the main checkout).
- **Made by the launcher** with `git worktree add`, not `claude --worktree`, which can't take an existing branch or a
  chosen base. Claude's `.worktreeinclude`, worktree settings and WorktreeCreate hooks therefore don't apply; none is
  in use here.
- **Branch**: `--branch B` (`origin/B` means B) or `worktree-<N>`. An existing local branch is checked out as it is
  (and never deleted). A new one is created from `origin/B` or `upstream/B`, fetched first, if one exists (which is
  then its base), else from the base, always with `--no-track`, so a bare `git push` fails rather than landing on the base or in the canonical repository.
- **Base**: `--base REF`, else the default branch of `upstream` if that remote exists (forks), else of `origin`: asked
  of the remote with `ls-remote --symref`, else its local `<remote>/HEAD`; with neither, the start is refused (never a
  fallback from upstream to origin). No remote: the main checkout's branch. The base is fetched first, and must be a
  plain revision, never a refspec.
- **Submodules**: initialized in the new worktree when the main checkout has them initialized.

## Definitions and restarts

Each session has a definition in `${XDG_STATE_HOME:-~/.local/state}/claude-sessions/<session>` (fields: the comment
above `FIELDS` in `session.sh`), so after a stop, a crash or a reboot `start <session>` finds its worktree and
conversation again. A worktree removed by hand is re-added from its branch; if the branch is gone too (renamed), the
start is refused rather than resuming in a fresh tree.

**Conversation**: the first start passes `--session-id <id>`. Once a transcript with that id exists, a worktree session
starts with `--continue`, the newest conversation in its own directory, which only it uses, so a `/clear` inside it
carries over. (`--continue` skips conversations begun with `/loop` and may pick one that `/add-dir`ed the worktree.)
An in-place session (`--here`, the orchestrator), whose directory the user may also work in, resumes `--resume <id>`:
after a `/clear` that is the conversation before it; use `/resume` there.

**Lock**: `flock` on the state directory serializes naming, definitions and adding or removing worktrees, so parallel
starts get `repo-1` and `repo-2`, and `clean` can't remove a worktree a start is about to use.

## List, stop and clean

`ls` also shows `work-*`, `personal-*` and `personal2-*` tmux sessions without a definition (`cw`, `ccp`), which
`stop` accepts. `stop` never stops the session it runs in. What `clean` keeps and removes is in SKILL.md; why:

- **Finished** means the work adds nothing to the base: `git merge-tree --write-tree <base> <tip>` equals the base's
  tree. That holds for a merge, a squash merge (whose commits are on no remote once the branch is deleted) and an
  unused branch, and can't lose content. Checks use the worktree's own HEAD, so commits on a detached HEAD, which no
  branch keeps, are seen.
- Every check fails closed: a git command that errors keeps the worktree.
- `git status` is asked with `--ignore-submodules=none`, so a `submodule.<name>.ignore` setting can't hide changes.
- Ignored files are build output: a new worktree has none, so they don't keep a finished worktree.
- Removal uses `git worktree remove --force`, which submodules need; the checks cover what it overrides, and it still
  refuses a locked worktree. Only a branch the launcher created, and finished, is deleted.

Worktrees without a definition (`cwt`, by hand) are not touched. laptop-setup should make `cwt` call the launcher
(below), so they get definitions; Claude's own subagent worktrees are swept by Claude.

## Orchestrator permissions

`--allow RULES` replaces `--dangerously-skip-permissions` with `--permission-mode dontAsk --allowedTools RULES`. For the
orchestrator: `Bash(bash <checkout>/skills/sessions/scripts/session.sh *) Skill(claude-skills:sessions)`. Checked
live: other Bash (`touch`, `systemctl`) is denied; Claude Code's built-in read-only commands (`ls`, `cat`) still run.
The skill's `allowed-tools` is the same launcher rule, so loading it widens nothing; a bare `Skill` rule would let the
model load another skill whose `allowed-tools` grants all of Bash.

This limits what the orchestrator's own mistakes can do; it is not a security boundary. Its job is to start sessions
with `--dangerously-skip-permissions` and a prompt, and any other session can run the launcher or type into it. So the
skill's rules apply to the tasks it passes on too, and the launcher validates names, directories, branches and bases
so those can't reach beyond it; `--prompt` and `--allow` are passed on as given, by design.

## Boot (for laptop-setup)

A user unit, linger on, `WantedBy=default.target`:

```ini
[Service]
Type=oneshot
KillMode=process
Environment=PATH=%h/.local/bin:/usr/local/bin:/usr/bin:/bin
Environment=TMUX_TMPDIR=%t/tmux
ExecStartPre=-/usr/bin/nm-online -q -t 120
ExecStartPre=-/bin/bash %h/<checkout>/skills/sessions/scripts/session.sh clean personal-orchestrator-%l
ExecStart=/bin/bash %h/<checkout>/skills/sessions/scripts/session.sh start personal %h/<checkout> --here \
  --name orchestrator-%l --trust --prompt /claude-skills:sessions \
  --allow "Bash(bash %h/<checkout>/skills/sessions/scripts/session.sh *) Skill(claude-skills:sessions)"
```

- `<checkout>` must be the physical path (no symlink): the launcher passes `--plugin-dir <checkout>` (it has a plugin
  manifest), so the skill loads from there and `${CLAUDE_SKILL_DIR}` is that path, which the allow rule must match.
- `--here`: the orchestrator works in the checkout, not a worktree.
- The `clean` first forgets its definition, so each boot starts a fresh conversation (it holds nothing worth keeping)
  rather than a resumed one, which after a long break may ask whether to resume.
- `KillMode=process`: tmux outlives the unit. Rerunning the unit is harmless. A tmux server the unit starts lives in
  the unit's cgroup, with every session in it; `systemd-run --user --scope` would give it its own, if memory pressure
  handling (systemd-oomd) ever treats them as one.
- `TMUX_TMPDIR` must match the shells' (`zshrc.j2` sets `$XDG_RUNTIME_DIR/tmux`).

Also for laptop-setup:

- widen `cls`, `ca` and `cstatus` to `^(work|personal|personal2)-`;
- make `cwt <n>` `session.sh start work . --name <n> && tmux attach -t work-<n>`, so its worktrees are defined and
  cleaned (existing ones need a one-time `git worktree remove`);
- in time let `cw` and `ccp` call the launcher too, or template its profile table (and the subagent limit), so the two
  can't drift.

## Assumptions

Documented by Claude Code: transcripts at `<config>/projects/<encoded cwd>/<id>.jsonl` (found with `find`, never
encoded here); `--continue` takes the newest conversation in the directory; `--allowedTools` rule syntax; skills'
`${CLAUDE_SKILL_DIR}`. Undocumented but visible when they break, and covered by tests with
a fake claude: the input box starts with `❯`, the trust menu's text; with Remote Control on, the transcript is written
at startup. Linux only (`flock`, `/proc`).

## Rejected

- `claude --resume <name>`: with two conversations of that name (after a `/clear`), it opens a picker.
- Matching transcripts' `customTitle`: an internal format.
- Cleaning unclaimed `.claude/worktrees` (`cwt`'s): new mechanism for a transitional problem; laptop-setup fixes `cwt`.
- Rules on remote branches for "finished": squash merges never qualify.
- Restart-on-crash, auto-restart at boot, a daemon or queue, profile auto-detection: not asked for.
