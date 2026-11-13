#!/usr/bin/env bash
# Start, list, stop and clean up background Claude Code sessions: tmux, Remote Control on, clean environment, named
# profile, and in a git repository a worktree and branch of their own.
#   session.sh start <profile> <dir> [--name N] [--branch B] [--base REF] [--here] [--trust] [--prompt T] [--tools R]
#   session.sh start <name> [--trust] [--prompt T]
#   session.sh ls | stop <name> | clean [<name>]
# Exit: 0 ok, 1 failed, 2 usage, 3 needs trust. Design: plans/session-orchestrator.md.
# Runs only tmux, git, the user's shell and claude: never systemctl, loginctl, reboot or tmux kill-server.
set -euo pipefail

PROFILES='^(work|personal|personal2)-'
WAIT=${SESSIONS_WAIT:-30}
STATE=${SESSIONS_STATE:-${XDG_STATE_HOME:-$HOME/.local/state}/claude-sessions}

die() { echo "session: $1" >&2; exit "${2:-1}"; }
usage() { sed -n '4,6s/^#   /usage: /p' "$0" >&2; exit 2; }
# The user's default tmux server, even when called from inside another one.
tm() { env -u TMUX tmux ${SESSIONS_TMUX_SOCKET:+-L "$SESSIONS_TMUX_SOCKET"} "$@"; }
alive() { tm has-session -t "=$1" 2>/dev/null; }
dead() { [[ $(tm display-message -pt "=$1:" '#{pane_dead}' 2>/dev/null) == 1 ]]; }
screen() { tm capture-pane -pt "=$1:" 2>/dev/null | grep -v '^$' || true; }
slug() { local s=${1//\//-}; echo "${s//[^a-zA-Z0-9_-]/_}"; }
g() { git -C "$D_repo" "$@"; }
# One start or clean at a time picks names, writes definitions and adds or removes worktrees.
lock() { mkdir -p "$STATE"; exec 9>"$STATE/.lock"; ! command -v flock >/dev/null || flock 9; }

# A definition is one file of key=value lines in $STATE, named after the session. repo and worktree are empty for a
# session that works in place (--here or no repository); dir is where it starts; created=1 when the launcher made
# the branch, the only kind clean deletes.
FIELDS=(profile repo worktree dir branch base created id tools)
load() { # load <name>: sets D_<field>; fails when undefined
	[[ -f $STATE/$1 ]] || return 1
	local f k v
	for f in "${FIELDS[@]}"; do printf -v "D_$f" ''; done
	while IFS='=' read -r k v; do
		[[ " ${FIELDS[*]} " == *" $k "* ]] && printf -v "D_$k" '%s' "$v"
	done <"$STATE/$1"
}
save() { # save <name>, from D_<field>
	local f v tmp
	tmp=$(mktemp "$STATE/.$1.XXXXXX")
	for f in "${FIELDS[@]}"; do
		v=D_$f
		[[ ${!v} != *$'\n'* ]] || die "a newline in $f"
		printf '%s=%s\n' "$f" "${!v}"
	done >"$tmp"
	mv "$tmp" "$STATE/$1"
}
claimed() { grep -lsxF "worktree=$1" "$STATE"/* | sed 's|.*/||' || true; } # the session that owns a worktree

# Profile -> SETS (applied after the user's shell rc, -u NAME unsets) and ENV_FILE, mirroring cwork, cper and cper2
# in laptop-setup's zshrc.j2.
profile() {
	local subagents=CLAUDE_CODE_MAX_SUBAGENTS_PER_SESSION=2000
	local -a personal=(-u CLAUDE_CODE_USE_VERTEX -u ANTHROPIC_VERTEX_PROJECT_ID -u CLOUD_ML_REGION
		-u GOOGLE_APPLICATION_CREDENTIALS -u GOOGLE_CLOUD_PROJECT -u ANTHROPIC_BASE_URL -u ANTHROPIC_API_KEY
		-u ANTHROPIC_AUTH_TOKEN -u CLAUDE_CODE_OAUTH_TOKEN -u CLAUDE_CODE_USE_BEDROCK -u CLAUDE_CODE_USE_FOUNDRY
		CLAUDE_CODE_USE_VERTEX=0 "$subagents")
	ENV_FILE=''
	case $1 in
	work)
		ENV_FILE=${SESSIONS_WORK_ENV:-$HOME/.config/claude/work-env}
		[[ -f $ENV_FILE ]] || ENV_FILE='' # older setups keep the Vertex variables in the shell rc
		SETS=(CLAUDE_CODE_USE_VERTEX=1 "$subagents")
		;;
	personal) SETS=("${personal[@]}" "CLAUDE_CONFIG_DIR=$HOME/.claude") ;;
	personal2) SETS=("${personal[@]}" "CLAUDE_CONFIG_DIR=$HOME/.claude-per2") ;;
	*) die "unknown profile '$1' (work, personal, personal2)" 2 ;;
	esac
}

# The base for new branches: upstream's default branch when that remote exists (fork checkouts), else origin's, asked
# of the remote itself (a local <remote>/HEAD is often missing or stale); else the main checkout's branch.
default_base() {
	local r head
	for r in upstream origin; do
		g remote get-url "$r" >/dev/null 2>&1 || continue
		head=$(timeout 30 git -C "$D_repo" ls-remote --symref "$r" HEAD 2>/dev/null |
			sed -n 's|^ref: refs/heads/\(.*\)\tHEAD$|\1|p') || true
		head=${head:-$(g symbolic-ref --quiet --short "refs/remotes/$r/HEAD" 2>/dev/null | sed "s|^$r/||")}
		[[ -n $head ]] || die "cannot tell $r's default branch (offline?); pass --base"
		echo "$r/$head"
		return
	done
	g symbolic-ref --quiet --short HEAD || g rev-parse HEAD
}

fetch() { # fetch <remote>/<branch>, best effort: a stale base beats no session
	local r=${1%%/*}
	[[ $1 == */* ]] && g remote get-url "$r" >/dev/null 2>&1 || return 0
	timeout 30 git -C "$D_repo" fetch --quiet "$r" "${1#*/}" 2>/dev/null || echo "warning: could not fetch $1; using it as it is" >&2
}

# Add the worktree of the loaded definition. new=1: its branch may be created; otherwise it must still exist.
add_worktree() {
	local new=$1 common r
	common=$(g rev-parse --path-format=absolute --git-common-dir)
	g worktree prune
	if ! g check-ignore -q .claude/worktrees/x; then
		mkdir -p "$common/info"
		echo '**/.claude/worktrees/' >>"$common/info/exclude"
	fi
	mkdir -p "${D_worktree%/*}"
	if g show-ref --verify --quiet "refs/heads/$D_branch"; then
		[[ -n $new ]] && D_created=0
		g worktree add --quiet "$D_worktree" "$D_branch" || die "could not check out $D_branch"
		return
	fi
	[[ -n $new ]] || die "branch $D_branch is gone (renamed?): restore it, or clean $NAME and start again"
	D_created=1
	for r in origin upstream; do # an existing remote branch: a local copy that pushes nowhere until told to
		if g show-ref --verify --quiet "refs/remotes/$r/$D_branch"; then
			g worktree add --quiet --no-track -b "$D_branch" "$D_worktree" "$r/$D_branch" ||
				die "could not check out $r/$D_branch"
			echo "worktree $D_worktree on $D_branch from $r/$D_branch"
			return
		fi
	done
	fetch "$D_base"
	g rev-parse --verify --quiet "$D_base^{commit}" >/dev/null || die "no such base: $D_base"
	g worktree add --quiet --no-track -b "$D_branch" "$D_worktree" "$D_base" ||
		die "could not create $D_branch from $D_base"
	echo "worktree $D_worktree on new branch $D_branch from $D_base"
}

# Resolve start's arguments into NAME and a loaded or new definition D_*.
define() {
	local prof=$1 dir sub='' main='' n k
	profile "$prof"
	dir=$(cd "$2" 2>/dev/null && pwd -P) || die "no such directory: $2"
	if [[ -z $here ]] && git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		main=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir)
		[[ $main == */.git ]] || die "$dir: no main checkout to hold worktrees (bare repository?); use --here"
		main=${main%/.git}
		sub=$(git -C "$dir" rev-parse --show-prefix) # start in the same subdirectory of the worktree
	fi
	D_repo=$main
	if [[ -n $name || -n $branch || -z $D_repo ]]; then
		n=$(slug "${name:-${branch:-${dir##*/}}}")
	else # a new agent: <repo>-<k>, the first k nothing uses yet
		k=1
		while n=${D_repo##*/}-$k; [[ -e $STATE/$prof-$n || -e $D_repo/.claude/worktrees/$n ]] || alive "$prof-$n" ||
			[[ -n $(g for-each-ref --format=x "refs/heads/worktree-$n" "refs/remotes/*/worktree-$n") ]]; do
			k=$((k + 1))
		done
	fi
	NAME=$prof-$n
	if load "$NAME"; then # an existing session: its definition wins, but it must be the same place
		[[ ${D_repo:-$D_dir} == "${main:-$dir}" ]] ||
			die "$NAME is already defined for ${D_repo:-$D_dir}; choose another --name"
		D_tools=${tools:-$D_tools}
		return
	fi
	D_profile=$prof D_repo=$main D_worktree='' D_dir=$dir D_branch='' D_base='' D_created='' D_tools=$tools
	D_id=$(cat /proc/sys/kernel/random/uuid 2>/dev/null || uuidgen | tr '[:upper:]' '[:lower:]')
	[[ -n $D_repo ]] || return 0
	D_worktree=$D_repo/.claude/worktrees/$n # the name without its profile, as cwt and claude --worktree name it
	local owner
	owner=$(claimed "$D_worktree")
	[[ -z $owner ]] || die "$D_worktree belongs to $owner; choose another --name"
	D_dir=$D_worktree${sub:+/${sub%/}}
	D_branch=${branch:-worktree-$n}
	D_base=${base:-$(default_base)}
	NEW=1
}

start() {
	local pos=() name='' branch='' base='' here='' trust='' prompt='' tools=''
	while [[ $# -gt 0 ]]; do
		case $1 in
		--name) name=${2:?}; shift 2 ;;
		--branch) branch=${2:?}; shift 2 ;;
		--base) base=${2:?}; shift 2 ;;
		--here) here=1; shift ;;
		--trust) trust=1; shift ;;
		--prompt) prompt=${2:?}; shift 2 ;;
		--tools) tools=${2:?}; shift 2 ;;
		-*) usage ;;
		*) pos+=("$1"); shift ;;
		esac
	done
	NEW=''
	lock
	case ${#pos[@]} in
	1) NAME=${pos[0]}; load "$NAME" || die "no definition for $NAME (session.sh ls)" ;;
	2) define "${pos[@]}" ;;
	*) usage ;;
	esac
	profile "$D_profile"

	if alive "$NAME" && dead "$NAME"; then
		tm kill-session -t "=$NAME" # claude failed earlier; its last screen was shown then
	fi
	if alive "$NAME"; then # the name is the key: never a second claude
		local was
		was=$(tm display-message -pt "=$NAME:" '#{session_path}')
		[[ $was == "$D_dir" ]] || die "$NAME already runs in $was; choose another --name"
		echo "exists $NAME"
		ready "$NAME" "$trust"
		return
	fi
	if [[ -n $D_worktree && ! -d $D_worktree ]]; then add_worktree "$NEW"; fi
	[[ -d $D_dir ]] || die "$D_dir is gone"
	save "$NAME"

	local path=$PATH v
	[[ :$path: == *":$HOME/.local/bin:"* ]] || path=$HOME/.local/bin:$path
	# Fresh environment, then the user's interactive shell rc, as a new terminal gets: nothing from the caller or
	# the tmux server (CLAUDE_CODE_* markers, another account's variables) reaches the session.
	local env=(env -i "PATH=$path" "TERM=${SESSIONS_TERM:-tmux-256color}")
	for v in HOME USER LOGNAME SHELL LANG XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK TMUX_TMPDIR \
		DISPLAY WAYLAND_DISPLAY KRB5CCNAME; do
		[[ -n ${!v:-} ]] && env+=("$v=${!v}")
	done
	local sh=${SESSIONS_SHELL:-${SHELL:-/bin/bash}}
	[[ ${sh##*/} == @(zsh|bash) ]] || sh=/bin/bash
	# Inside: source the env file, apply the profile, then run claude (the rc's claude() guard, if it has one). The
	# session's conversation is resumed once it has one (only the profile knows the config dir; --resume fails until
	# the first message is saved).
	# shellcheck disable=SC2016 # expanded by the inner shell
	local run=("$sh" -i -c 'f=$1 id=$2; shift 2; if [ -n "$f" ]; then set -a; . "$f" || exit 1; set +a; fi
		while [ "$1" != -- ]; do if [ "$1" = -u ]; then unset "$2"; shift; else export "$1"; fi; shift; done
		shift; c=$1; shift; r=--session-id
		for t in "${CLAUDE_CONFIG_DIR:-$HOME/.claude}"/projects/*/"$id".jsonl; do [ -f "$t" ] && r=--resume; done
		"$c" "$r" "$id" "$@"' session "$ENV_FILE" "$D_id" "${SETS[@]}" --)
	local cmd=("${SESSIONS_CLAUDE:-claude}" --remote-control "$NAME" --name "$NAME")
	if [[ -n $D_tools ]]; then # only these tools, everything else denied
		cmd+=(--permission-mode dontAsk --allowedTools "$D_tools")
	else
		cmd+=(--dangerously-skip-permissions)
	fi
	cmd+=(--plugin-dir .)
	[[ -n $prompt ]] && cmd+=(-- "$prompt") # --allowedTools would take a bare prompt as a rule
	# remain-on-exit failed: a claude that fails keeps its screen for ready() to show.
	tm new-session -d -s "$NAME" -c "$D_dir" -x 200 -y 50 "${env[@]}" "${run[@]}" "${cmd[@]}" \; \
		set-option -t "=$NAME:" remain-on-exit failed
	flock -u 9 2>/dev/null || true
	echo "started $NAME in $D_dir"
	ready "$NAME" "$trust"
}

# Wait for claude's input box; answer the trust prompt only with --trust.
ready() {
	local name=$1 trust=$2 sent='' s='' i
	for ((i = 0; i < WAIT; i++)); do
		alive "$name" || die "$name exited before it was ready"
		s=$(screen "$name")
		if dead "$name"; then
			tail -5 <<<"$s" >&2
			tm kill-session -t "=$name"
			die "$name failed before it was ready"
		elif grep -q '^❯' <<<"$s"; then # the input box; the trust menu's cursor is indented
			echo "ready $name"
			return 0
		elif [[ -z $sent && $s == *"Yes, I trust this folder"* ]]; then
			[[ -n $trust ]] || { echo "needs-trust $name (tmux attach -t $name, or rerun with --trust)"; exit 3; }
			tm send-keys -t "=$name:" Down Enter
			sent=1
		fi
		sleep 1
	done
	tail -5 <<<"$s" >&2
	echo "waiting $name (not ready after ${WAIT}s: tmux attach -t $name)"
}

list() {
	local n
	{
		tm list-sessions -F '#{session_name} #{?pane_dead,failed,running} #{session_path}' 2>/dev/null |
			grep -E "$PROFILES" || true
		for n in "$STATE"/*; do
			[[ -f $n ]] || continue
			n=${n##*/}
			alive "$n" || { load "$n" && echo "$n stopped $D_dir"; }
		done
	} | sort
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
	echo "stopped $1 (start $1 resumes it; clean $1 removes it)"
}

# Adds nothing to the base: merged (also by squash), or never used. git merge-tree needs git 2.38.
finished() { [[ $(g merge-tree --write-tree "$D_base" "$1" 2>/dev/null) == "$(g rev-parse "$D_base^{tree}")" ]]; }

# Why a stopped worktree session must be kept, or nothing. all=1 (clean with no name) keeps unfinished work too;
# clean <name> removes a worktree whose commits are all on a branch, since the branch keeps them.
keep() {
	local all=$1 wt=$D_worktree head tip p gd
	[[ -d $wt ]] || return 0
	[[ -z $(git -C "$wt" status --porcelain --untracked-files=all) ]] || { echo "uncommitted changes"; return; }
	gd=$(git -C "$wt" rev-parse --absolute-git-dir)
	for p in rebase-merge rebase-apply MERGE_HEAD CHERRY_PICK_HEAD REVERT_HEAD BISECT_LOG; do
		[[ ! -e $gd/$p ]] || { echo "a git operation in progress ($p)"; return; }
	done
	for p in /proc/[0-9]*/cwd; do # someone working there by hand (Linux)
		[[ $(readlink "$p" 2>/dev/null)/ != "$wt"/* ]] || { echo "in use by process ${p//[^0-9]/}"; return; }
	done
	tip=$(git -C "$wt" rev-parse HEAD) # the worktree's own HEAD, not the main checkout's
	if ! head=$(git -C "$wt" symbolic-ref --quiet --short HEAD); then
		finished "$tip" || { echo "commits on a detached HEAD"; return; }
	fi
	[[ -n $all ]] || return 0
	finished "$tip" || { echo "unmerged work on ${head:-HEAD}"; return; }
	[[ $head == "$D_branch" ]] || ! g rev-parse --verify --quiet "$D_branch^{commit}" >/dev/null ||
		finished "$D_branch" || { echo "unmerged work on $D_branch"; return; }
	[[ -z $(git -C "$wt" status --porcelain --ignored=matching | grep '^!!') ]] ||
		{ echo "ignored files (build output, .env...): clean $NAME removes them"; return; }
}

clean() {
	[[ $# -le 1 ]] || usage
	local names=("$@") all='' why r
	[[ $# == 1 ]] || { all=1; for r in "$STATE"/*; do [[ -f $r ]] && names+=("${r##*/}"); done; }
	lock
	for NAME in "${names[@]}"; do
		load "$NAME" || die "no definition for $NAME"
		if alive "$NAME"; then
			[[ -n $all ]] || die "$NAME is running; stop it first"
			continue
		fi
		if [[ -z $D_worktree || ! -d $D_repo ]]; then # works in place, or its repository is gone
			[[ -z $all || ! -d ${D_repo:-$D_dir} ]] || continue
			rm -f -- "${STATE:?}/$NAME"
			echo "forgot $NAME"
			continue
		fi
		[[ $D_base != */* ]] || fetch "$D_base"
		why=$(keep "$all")
		if [[ -n $why ]]; then
			echo "kept $NAME: $why"
			continue
		fi
		if [[ -d $D_worktree ]] && ! g worktree remove "$D_worktree"; then
			echo "kept $NAME: git could not remove the worktree (submodules?)"
			continue
		fi
		if [[ $D_created == 1 ]] && g show-ref --verify --quiet "refs/heads/$D_branch" && finished "$D_branch"; then
			g branch -q -D "$D_branch"
			echo "removed $NAME and branch $D_branch"
		else
			echo "removed $NAME (branch $D_branch kept)"
		fi
		rm -f -- "${STATE:?}/$NAME"
	done
}

case ${1:-} in
start) shift; start "$@" ;;
ls) list ;;
stop) shift; stop "$@" ;;
clean) shift; clean "$@" ;;
*) usage ;;
esac
