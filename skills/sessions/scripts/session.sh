#!/usr/bin/env bash
# Start, list and stop background Claude Code sessions: tmux, Remote Control on, clean environment, named profile.
#   session.sh start <profile> <dir> [--worktree W] [--name N] [--trust] [--prompt TEXT]
#   session.sh ls
#   session.sh stop <name>
# Sessions are named <profile>-<name>; that prefix is the only state. Exit: 0 ok, 1 failed, 2 usage, 3 needs trust.
# Only runs tmux and claude: never systemctl, loginctl, reboot or tmux kill-server.
set -euo pipefail

PROFILES='^(work|personal|personal2)-'
WAIT=${SESSIONS_WAIT:-30}

die() { echo "session: $1" >&2; exit "${2:-1}"; }
usage() { sed -n '3,5s/^#   /usage: /p' "$0" >&2; exit 2; }
# The user's default tmux server, even when called from inside another one.
tm() { env -u TMUX tmux ${SESSIONS_TMUX_SOCKET:+-L "$SESSIONS_TMUX_SOCKET"} "$@"; }
alive() { tm has-session -t "=$1" 2>/dev/null; }
screen() { tm capture-pane -pt "=$1:" 2>/dev/null || true; }

# Profile -> variables, mirroring cper, cper2 and cwork in laptop-setup's zshrc.j2. Sets PROFILE_ENV and ENV_FILE.
profile() {
	local subagents=CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=2000
	ENV_FILE=''
	case $1 in
	work)
		ENV_FILE=${SESSIONS_WORK_ENV:-$HOME/.config/claude/work-env}
		[[ -f $ENV_FILE ]] || die "$ENV_FILE missing (laptop-setup: make claude)"
		PROFILE_ENV=(CLAUDE_CODE_USE_VERTEX=1 "$subagents")
		;;
	personal) PROFILE_ENV=("CLAUDE_CONFIG_DIR=$HOME/.claude" CLAUDE_CODE_USE_VERTEX=0 "$subagents") ;;
	personal2) PROFILE_ENV=("CLAUDE_CONFIG_DIR=$HOME/.claude-per2" CLAUDE_CODE_USE_VERTEX=0 "$subagents") ;;
	*) die "unknown profile '$1' (work, personal, personal2)" 2 ;;
	esac
}

start() {
	[[ $# -ge 2 ]] || usage
	local prof=$1 dir=$2 wt='' name='' trust='' prompt=''
	shift 2
	while [[ $# -gt 0 ]]; do
		case $1 in
		--worktree) wt=${2:?}; shift 2 ;;
		--name) name=${2:?}; shift 2 ;;
		--trust) trust=1; shift ;;
		--prompt) prompt=${2:?}; shift 2 ;;
		*) usage ;;
		esac
	done
	profile "$prof"
	dir=$(cd "$dir" 2>/dev/null && pwd -P) || die "no such directory: $2"
	[[ -z $wt || $wt =~ ^[A-Za-z0-9_-]+$ ]] || die "bad worktree name '$wt'" 2
	name=${name:-${wt:-${dir##*/}}}
	name=$prof-${name//[^a-zA-Z0-9_-]/_}

	if alive "$name"; then # the name is the key: never a second claude
		local was
		was=$(tm display-message -pt "=$name:" '#{session_path}')
		[[ $was == "$dir" ]] || die "$name already runs in $was; choose another --name"
		echo "exists $name"
	else
		local claude=${SESSIONS_CLAUDE:-$(command -v claude || true)}
		[[ $claude == /* ]] || die "claude not found on PATH"
		local path=$PATH
		[[ :$path: == *":$HOME/.local/bin:"* ]] || path=$HOME/.local/bin:$path
		# Fresh environment: nothing from the caller or the tmux server reaches the session.
		local env=(env -i "PATH=$path" "TERM=${SESSIONS_TERM:-tmux-256color}") v
		for v in HOME USER LOGNAME SHELL LANG XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK TMUX_TMPDIR \
			DISPLAY WAYLAND_DISPLAY KRB5CCNAME; do
			[[ -n ${!v:-} ]] && env+=("$v=${!v}")
		done
		env+=("${PROFILE_ENV[@]}")
		local cmd=("$claude" --remote-control "$name" --name "$name" --dangerously-skip-permissions --plugin-dir .)
		[[ -n $wt ]] && cmd+=(--worktree "$wt")
		[[ -n $prompt ]] && cmd+=("$prompt")
		# The env file is sourced inside the clean environment, as cwork does; then claude replaces the shell.
		local run=(bash -c '[[ -z $1 ]] || { set -a; . "$1" || exit 1; set +a; }; shift; exec "$@"' _ "$ENV_FILE")
		tm new-session -d -s "$name" -c "$dir" -x 200 -y 50 "${env[@]}" "${run[@]}" "${cmd[@]}"
		echo "started $name"
	fi
	ready "$name" "$trust"
}

# Wait for claude's input box; answer the trust prompt only with --trust.
ready() {
	local name=$1 trust=$2 sent='' s i
	for ((i = 0; i < WAIT; i++)); do
		alive "$name" || die "$name exited before it was ready"
		s=$(screen "$name")
		if grep -q '^❯' <<<"$s"; then # the input box; the trust menu's cursor is indented
			echo "ready $name"
			return 0
		elif [[ -z $sent && $s == *"Yes, I trust this folder"* ]]; then
			[[ -n $trust ]] || { echo "needs-trust $name (tmux attach -t $name, or rerun with --trust)"; exit 3; }
			tm send-keys -t "=$name:" Down Enter
			sent=1
		fi
		sleep 1
	done
	grep -v '^$' <<<"$s" | tail -5 >&2
	echo "waiting $name (not ready after ${WAIT}s: tmux attach -t $name)"
}

list() {
	tm list-sessions -F '#{session_name} #{session_path}' 2>/dev/null | grep -E "$PROFILES" || true
}

stop() {
	[[ $# -eq 1 && $1 =~ $PROFILES ]] || die "stop takes one session name from ls" 2
	alive "$1" || die "no session $1"
	if [[ -n ${TMUX_PANE:-} ]]; then # not the one this runs in
		local me
		me=$(tmux display-message -pt "$TMUX_PANE" '#{socket_path} #S' 2>/dev/null || true)
		[[ $me != "$(tm display-message -pt "=$1:" '#{socket_path} #S')" ]] || die "refusing to stop my own session"
	fi
	tm kill-session -t "=$1"
	echo "stopped $1"
}

case ${1:-} in
start) shift; start "$@" ;;
ls) list ;;
stop) shift; stop "$@" ;;
*) usage ;;
esac
