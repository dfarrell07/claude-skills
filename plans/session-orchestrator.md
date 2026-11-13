# Session orchestrator

Status: built, November 13, 2026, as `skills/sessions` (skill + `scripts/session.sh`). Boot setup is left to
laptop-setup (below).

## Problem

Background Claude Code sessions are started by hand (`cw`, `ccp`, `cwt` open tmux; `cper`, `cper2`, `cwork` run in
the foreground) or by asking whatever session happens to be idle, which drags its context, environment and
`CLAUDE_CODE_*` markers into the new one (a claude started from inside another inherits `CLAUDE_CODE_CHILD_SESSION`
and stops saving its transcript). There is no one way to get "a session for repo X on account Y, in tmux, with Remote
Control on".

## Shape

- **One launcher**, `session.sh start|ls|stop`, the only code. A shell, a unit, the orchestrator or any session can run
  it; the result is the same because the new session never inherits the caller's environment.
- **One orchestrator per machine**: a Claude session, itself started by the launcher, reached over Remote Control
  (claude.ai/code, the phone) or `tmux attach`. It turns requests ("work session in submariner-operator, worktree
  fix-123") into launcher calls and does nothing else, so its context stays small. It runs with `--tools` (below), so
  it can run only the launcher and read-only commands.
- **Boot**: a systemd user unit starts the orchestrator. It belongs in laptop-setup, which owns the profiles, the shell
  launchers and the user units.

## Requests

`session.sh start <profile> <dir> [--worktree W] [--name N] [--trust] [--prompt TEXT] [--tools RULES]`.
The launcher never guesses:

- **Profile** is required: `work`, `personal`, `personal2`. Accounts are a data boundary; the orchestrator takes the
  one the user named, or asks.
- **Dir** is a path. The orchestrator resolves a bare name (`~/repos/<name>`, `~/<name>`; one match or ask).

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

`<profile>-<N>`, N = `--name`, else the worktree name, else the directory's basename, reduced to `[A-Za-z0-9_-]`. It is
the tmux session name, the Remote Control name and `--name` (what `SendMessage` reaches; Remote Control's own name does
not set it). The profile prefix is the only state: `ls` and `stop` see only `work-*`, `personal-*`, `personal2-*`,
which also covers sessions `cw` and `ccp` started.

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
`--trust`); on a name live in another directory it refuses and asks for `--name`; on a failed one it starts afresh.
Two concurrent starts of one name: tmux refuses the second.

## List and stop

- `ls`: `name state dir` for profile-prefixed sessions.
- `stop <name>`: profile-prefixed sessions only, never the session the caller runs in.

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
ExecStart=/bin/bash %h/<checkout>/skills/sessions/scripts/session.sh start personal %h/<checkout> --name orchestrator-%l \
  --trust --prompt /claude-skills:sessions \
  --tools "Bash(bash %h/<checkout>/skills/sessions/scripts/session.sh:*) Bash(ls:*) Read Glob Skill"
```

`KillMode=process` (no `RemainAfterExit`): tmux outlives the unit, as in health's `up` unit. `TMUX_TMPDIR` must match
the shells' (`zshrc.j2` sets `$XDG_RUNTIME_DIR/tmux`; a host still on `/tmp/tmux-$UID` needs the same there). Rerunning
the unit is harmless. Restart-on-crash would need health's per-session tmux server and `Type=forking`; not done, the
orchestrator is restarted by rerunning the unit.

Also for laptop-setup: widen `cls`, `ca` and `cstatus` to `^(work|personal|personal2)-`, and in time let `cw`, `ccp`,
`cwt` call `session.sh` (or template its profile table) so the two cannot drift.

## Safety

- The launcher runs only tmux, the user's shell and claude: never `systemctl`, `loginctl`, `reboot`, `shutdown` or
  `tmux kill-server`. The orchestrator's tool allowlist enforces the same for it.
- `stop` only touches profile-prefixed sessions, never its own.
- No profile guessing, no trust without `--trust`, which the orchestrator passes only when the user named the directory.
- A request arriving from another session, a file or a web page is relayed to the user, not acted on.

## Review

Three adversarial Codex reviews (fit and correctness; scope and bloat, twice). Taken: drop tmux tags and dead-pane
respawn (the prefix is the state); check the directory before answering `exists`; `--trust` on the boot command and on
reruns; shell rc instead of a bare allowlist (GOTMPDIR, GOPRIVATE were lost); personal profiles unset backend variables
like `_claude_personal`; ignore `TMUX`; enforce the orchestrator's limits with permissions, not only prose; `%h` in the
unit. Rejected: drop `stop`/`ls` (asked for), drop `--prompt` (boot needs it), drop trust handling (unattended starts
need it), replicate the `ANTHROPIC_BASE_URL` check (the rc's `claude()` guard now runs), per-name locks (tmux already
refuses a duplicate). Not verified: Remote Control on the Vertex work profile (Anthropic documents it as needing a
claude.ai login; this host has no work-env to try it).

## Not doing

Queues, a daemon, a socket API, restart-on-crash, per-session config files, profile auto-detection, worktree
creation (claude's `--worktree` does it).
