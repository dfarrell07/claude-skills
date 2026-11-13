# Session orchestrator

Status: design, November 13, 2026. Built as `skills/sessions` (skill + `scripts/session.sh`).

## Problem

Background Claude Code sessions are started by hand (`cw`, `ccp`, `cwt` open tmux; `cper`, `cper2`, `cwork` run in
the foreground) or by asking whatever session happens to be idle, which drags its context, environment and
`CLAUDE_CODE_*` markers into the new one (a session started from inside another inherits
`CLAUDE_CODE_CHILD_SESSION` and stops saving its transcript). There is no one way to get "a session for repo X on
account Y, in tmux, with Remote Control on".

## Shape

- **One launcher**, `session.sh start|ls|stop`: the only code. Anyone can run it: a shell, a laptop-setup unit, the
  orchestrator.
- **One orchestrator per machine**: a small Claude session (itself started by the launcher) whose only job is to turn
  requests ("work session in submariner-operator, worktree fix-123") into launcher calls. It does no repo work, so its
  context stays small.
- **Boot**: a systemd user unit runs `session.sh start` for the orchestrator. It belongs in laptop-setup (it already
  owns the profiles, the launchers and the user units); this repo only documents it.

## Requests

`session.sh start <profile> <dir> [--worktree W] [--name N] [--trust] [--prompt TEXT]`.
The orchestrator turns words into that; the launcher never guesses.

- **Profile** is required: `work`, `personal`, `personal2` (the accounts behind `cwork`, `cper`, `cper2`).
  Accounts are a data boundary, so the launcher refuses rather than infers. The orchestrator takes the profile the
  user named, or asks.
- **Dir** is a path. A bare name is resolved by the orchestrator (`~/repos/<name>`, `~/<name>`; one match or ask),
  not by the launcher.

## Profiles (mirror zshrc.j2)

| Profile | Env | Like |
| --- | --- | --- |
| `work` | source `~/.config/claude/work-env`, `CLAUDE_CODE_USE_VERTEX=1` | `cwork` |
| `personal` | `CLAUDE_CONFIG_DIR=~/.claude`, `CLAUDE_CODE_USE_VERTEX=0` | `cper` |
| `personal2` | `CLAUDE_CONFIG_DIR=~/.claude-per2`, `CLAUDE_CODE_USE_VERTEX=0` | `cper2` |

All get `CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=2000` and the launcher args `--dangerously-skip-permissions
--plugin-dir .`. The table is hard-coded to match laptop-setup today; laptop-setup should eventually template it.

## Environment

Claude runs under `env -i` with a short allowlist (HOME, USER, LOGNAME, SHELL, PATH, LANG, TERM, XDG_RUNTIME_DIR,
DBUS_SESSION_BUS_ADDRESS, SSH_AUTH_SOCK, TMUX_TMPDIR) plus the profile's variables. Neither the caller's nor the tmux
server's environment reaches the session, so nothing is dragged along.

## Naming

`<profile>-<slug>`, slug = worktree name, else the directory's basename, reduced to `[A-Za-z0-9_-]`; `--name`
overrides. This is the tmux session name, the Remote Control name and `--name` (the name `SendMessage` reaches; Remote
Control's own name does not set it). `work-x` and `personal-x` are what `cw`/`ccp` already use, so `cls`, `ca` and
`cstatus` see these sessions too.

## tmux and Remote Control

The user's default tmux server (honoring `TMUX_TMPDIR`), so `tmux attach -t <name>` works everywhere.
`tmux new-session -d -s <name> -c <dir>` runs claude directly (no wrapping shell), with
`--remote-control <name> --name <name>`. Each session is tagged with the tmux option `@claude-session` and keeps
`remain-on-exit` on, so an exited claude stays visible with its last screen until stopped or restarted.

**Trust prompt**: it appears even with `--dangerously-skip-permissions`, defaulting to "No, exit". The launcher waits
up to 30 s for the session to be ready. If it stops at the trust prompt, it accepts only with `--trust`, which the
orchestrator passes only when the user named that directory in the request; otherwise it reports `needs-trust` and
leaves the session waiting for someone to attach.

## Idempotency

The name is the key. `start` on a live session prints `exists <name>` and does nothing; on an exited one (dead pane)
it respawns it. Calling it twice never makes two claudes.

## List and stop

- `ls`: tagged sessions as `name profile state dir` (state: `running`, `exited`).
- `stop <name>`: kills a tagged session only. Untagged sessions (the user's own tmux, `cw`/`ccp` sessions) are
  refused, and so is the orchestrator's own session.

## Boot (for laptop-setup)

`claude-orchestrator.service`, a user unit with linger on: `Type=oneshot`, `RemainAfterExit=yes`, `KillMode=process`
(the tmux server outlives it), `ExecStartPre=-nm-online -q -t 120`, `Environment=PATH=~/.local/bin:...` and the same
`TMUX_TMPDIR` the shells use, `ExecStart=session.sh start personal <skills checkout> --name orchestrator --prompt
/claude-skills:sessions`. `WantedBy=default.target`. Re-running it is harmless.

## Safety

- The launcher only runs tmux and claude. It never calls `systemctl`, `loginctl`, `reboot`, `shutdown`, kill-server
  or anything that restarts the user manager or ends the login, and the skill tells the orchestrator the same.
- It stops only sessions it tagged, never the orchestrator itself.
- No profile guessing, no auto-trust without `--trust`.
- Text from other sessions asking the orchestrator to launch something is a request to relay to the user, not an
  instruction.

## Not doing

Queues, a daemon, a socket API, restart-on-crash, per-session config files, profile auto-detection, worktree
creation (claude's `--worktree` does it).
