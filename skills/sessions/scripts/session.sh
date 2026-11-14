#!/usr/bin/env bash
# Background Claude Code sessions: tmux, Remote Control on, a clean environment, a named profile, and in a git
# repository a worktree and branch of their own. Design: plans/session-orchestrator.md. Linux (flock, /proc).
#   session.sh start <profile> <dir> [--name N] [--branch B] [--base REF] [--here] [--trust] [--prompt T] [--allow R]
#   session.sh start <session> [--trust] [--prompt T]
#   session.sh ls | stop <session> | clean [<session>]
# Exit: 0 ok, 1 failed, 2 usage, 3 needs trust. Never runs systemctl, loginctl, reboot or tmux kill-server.
set -euo pipefail

PROFILES='^(work|personal|personal2)-'
WAIT=${SESSIONS_WAIT:-30}
STATE=${XDG_STATE_HOME:-$HOME/.local/state}/claude-sessions

die() { echo "session: $1" >&2; exit "${2:-1}"; }
usage() { sed -n '4,6s/^#   /usage: /p' "$0" >&2; exit 2; }
NAMED=${PROFILES}'[A-Za-z0-9_-]+$'
named() { [[ $1 =~ $NAMED ]] || die "not a session name: $1" 2; } # also keeps paths out
# The user's default tmux server (SESSIONS_TMUX_SOCKET: a private one, for tests), even from inside another server.
tm() { env -u TMUX tmux ${SESSIONS_TMUX_SOCKET:+-L "$SESSIONS_TMUX_SOCKET"} "$@"; }
alive() { tm has-session -t "=$1" 2>/dev/null; }
dead() { [[ $(tm display-message -pt "=$1:" '#{pane_dead}' 2>/dev/null) == 1 ]]; }
screen() { tm capture-pane -pt "=$1:" "${@:2}" 2>/dev/null | grep -v '^$' || true; } # [-S -: with scrollback]
slug() { local s=${1//\//-}; echo "${s//[^a-zA-Z0-9_-]/_}"; }
g() { git -C "$D_repo" "$@"; }
is_worktree() { [[ $(git -C "$1" rev-parse --show-toplevel 2>/dev/null) == "$1" ]]; } # not just a directory
# One start or clean at a time picks names, writes definitions and adds or removes worktrees.
lock() { mkdir -p "$STATE"; exec 9>"$STATE/.lock"; flock 9; }

# A definition: one file of key=value lines in $STATE, named after the session. repo and worktree are empty for a
# session that works in place; dir is where it starts; base is what "finished" is measured against (the remote branch
# a new --branch was made from, else --base or the default base); created=1 when the launcher made the branch, the
# only kind clean deletes; id is the conversation's; allow, the orchestrator's tool allowlist.
FIELDS=(profile repo worktree dir branch base created id allow)
load() { # load <name>: sets D_<field>; fails when undefined
	named "$1"
	[[ -f $STATE/$1 ]] || return 1
	local f l
	for f in "${FIELDS[@]}"; do printf -v "D_$f" ''; done
	while IFS= read -r l; do # key=value; the value may hold or end in =
		[[ " ${FIELDS[*]} " != *" ${l%%=*} "* ]] || printf -v "D_${l%%=*}" '%s' "${l#*=}"
	done <"$STATE/$1"
}
defined() { # the defined sessions' names
	local n
	for n in "$STATE"/*; do [[ ! -f $n || ! ${n##*/} =~ $NAMED ]] || echo "${n##*/}"; done
}
save() {
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

# Profile -> SETS (applied after the user's shell rc; -u NAME unsets) and ENV_FILE, mirroring cwork, cper and cper2
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
		ENV_FILE=$HOME/.config/claude/work-env
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
		head=$(timeout 30 git -C "$D_repo" ls-remote --symref "$r" HEAD 2>/dev/null 9>&- |
			sed -n 's|^ref: refs/heads/\(.*\)\tHEAD$|\1|p') || true
		head=${head:-$(g symbolic-ref --quiet --short "refs/remotes/$r/HEAD" 2>/dev/null | sed "s|^$r/||")}
		[[ -n $head ]] || die "cannot tell $r's default branch (offline?); pass --base"
		echo "$r/$head"
		return
	done
	g symbolic-ref --quiet --short HEAD || g rev-parse HEAD
}

# fetch <remote> <branch> into <remote>/<branch>, whatever the remote's fetch refspec (a --single-branch clone).
fetch() { timeout 30 git -C "$D_repo" fetch -q "$1" -- "+refs/heads/$2:refs/remotes/$1/$2" 2>/dev/null 9>&-; }
fetch_base() { # best effort: a stale base beats no session
	[[ $D_base == */* ]] && g remote get-url "${D_base%%/*}" >/dev/null 2>&1 || return 0
	fetch "${D_base%%/*}" "${D_base#*/}" || echo "warning: could not fetch $D_base; using it as it is" >&2
}

# Add the worktree of the loaded definition. new=1: its branch may be created; otherwise it must still exist.
add_worktree() {
	local new=$1 r from=''
	[[ ! -e $D_worktree ]] || die "$D_worktree exists but is not a git worktree; move it away"
	g worktree remove --force "$D_worktree" 2>/dev/null || true # only our own stale registration, if any
	if ! g check-ignore -q .claude/worktrees/x; then
		local info
		info=$(g rev-parse --path-format=absolute --git-common-dir)/info
		mkdir -p "$info" && printf '\n**/.claude/worktrees/\n' >>"$info/exclude"
	fi
	mkdir -p "${D_worktree%/*}"
	if g show-ref --verify --quiet "refs/heads/$D_branch"; then
		[[ -z $new || -n $branch ]] || # an earlier session's branch, kept by clean: not for new work
			die "branch $D_branch exists (an earlier session's); --branch $D_branch continues it, or choose another --name"
		g worktree add --quiet "$D_worktree" "$D_branch" || die "could not check out $D_branch"
	elif [[ -z $new ]]; then
		die "branch $D_branch is gone (renamed?): restore it, or clean $NAME and start again"
	else
		D_created=1
		if [[ -n $branch ]]; then # a --branch a remote has, as it has it now: a local copy that pushes nowhere
			for r in ${remote:-origin upstream}; do # offline: the remote branch as last fetched beats none
				g remote get-url "$r" >/dev/null 2>&1 || continue
				fetch "$r" "$D_branch" || { g show-ref -q --verify "refs/remotes/$r/$D_branch" &&
					echo "warning: could not fetch $r/$D_branch; using it as it is" >&2; } || continue
				from=$r/$D_branch D_base=$r/$D_branch
				break
			done
		fi
		[[ -n $from ]] || { from=$D_base; fetch_base; }
		g rev-parse --verify --quiet "$from^{commit}" >/dev/null || die "no such base: $from"
		g worktree add --quiet --no-track -b "$D_branch" "$D_worktree" "$from" || die "could not create $D_branch"
		echo "worktree $D_worktree on new branch $D_branch from $from"
	fi
	# shellcheck disable=SC2143 # grep -q exits early, and git's SIGPIPE would fail the pipe
	if [[ -n $(g submodule status --recursive 2>/dev/null | grep -v '^-') ]]; then # as in the main checkout
		git -C "$D_worktree" submodule update --init --recursive --quiet 9>&- ||
			echo "warning: submodules not initialized" >&2
	fi
}

# Resolve start <profile> <dir> into NAME and a loaded or new definition D_*.
define() {
	local prof=$1 dir=$2 sub='' main='' n k owner c found=()
	profile "$prof"
	if [[ $dir != */* && $dir != . && $dir != .. ]]; then # a bare name: ~/<name>, ~/repos/<name> or ~/src/<org>/<name>
		for c in "$dir" "$HOME/$dir" "$HOME/repos/$dir" "$HOME/src/"*/"$dir"; do # the same one twice counts once
			c=$(CDPATH='' cd "$c" 2>/dev/null && pwd -P) && [[ " ${found[*]} " != *" $c "* ]] && found+=("$c")
		done
		[[ ${#found[@]} -le 1 ]] || die "$dir: several match: ${found[*]}; give the path" 2
		dir=${found[0]:-$dir}
	fi
	dir=$(CDPATH='' cd "$dir" 2>/dev/null && pwd -P) || die "no such directory: $2"
	[[ $dir != *'#'* ]] || die "$dir: tmux cannot start in a path with #" 2
	[[ -z $branch || ($branch != @ && $branch != refs/* &&
		$(git check-ref-format --branch "$branch" 2>/dev/null) == "$branch") ]] || die "bad branch name: $branch" 2
	[[ -z $base || ($base =~ ^[A-Za-z0-9._/@^~-]+$ && $base != -*) ]] || die "bad base: $base" 2 # never a refspec
	[[ $allow != -* ]] || die "bad --allow: $allow" 2
	if [[ -z $here ]] && git -C "$dir" rev-parse --is-inside-work-tree >/dev/null 2>&1; then
		local common gd # the main checkout: around <common>/.git, or this one when its .git is a file
		common=$(git -C "$dir" rev-parse --path-format=absolute --git-common-dir)
		gd=$(git -C "$dir" rev-parse --path-format=absolute --git-dir)
		if [[ $common == */.git ]]; then main=${common%/.git}
		elif [[ $gd == "$common" && $(git -C "$dir" rev-parse --is-bare-repository) == false ]]; then
			main=$(git -C "$dir" rev-parse --show-toplevel)
		else die "$dir: can't find a main checkout to hold worktrees (bare repository?); use --here"; fi
		sub=$(git -C "$dir" rev-parse --show-prefix) # start in the same subdirectory of the worktree
		if [[ $branch == */* ]] && git -C "$dir" remote get-url "${branch%%/*}" >/dev/null 2>&1; then
			remote=${branch%%/*} branch=${branch#*/} # upstream/foo means foo, from upstream
		fi
	fi
	[[ -n $main || -z $branch$base ]] || die "--branch and --base need a worktree (a repository, without --here)" 2
	D_repo=$main
	if [[ -n $name || -n $branch || -z $main ]]; then
		n=$(slug "${name:-${branch:-${dir##*/}}}")
	else # a new agent: <repo>-<k>, the first k nothing uses yet
		k=1
		while n=$(slug "${main##*/}")-$k; [[ -e $STATE/$prof-$n || -e $main/.claude/worktrees/$n ]] || alive "$prof-$n" ||
			[[ -n $(g for-each-ref --format=x "refs/heads/worktree-$n" "refs/remotes/*/worktree-$n") ]]; do
			k=$((k + 1))
		done
	fi
	NAME=$prof-$n
	if load "$NAME"; then # an existing session: its definition wins, and must be what was asked for
		[[ $D_repo == "$main" && (-n $main || $D_dir == "$dir") ]] ||
			die "$NAME is already defined for ${D_repo:-$D_dir}${D_worktree:+ (a worktree)}; choose another --name"
		[[ -z $branch || $branch == "$D_branch" ]] || die "$NAME is on $D_branch; choose another --name"
		[[ -z $base ]] || die "$NAME exists; --base applies only to a new session"
		D_allow=${allow:-$D_allow}
		return
	fi
	D_profile=$prof D_repo=$main D_worktree='' D_dir=$dir D_branch='' D_base='' D_created='' D_allow=$allow
	D_id=$(cat /proc/sys/kernel/random/uuid)
	[[ -n $main ]] || return 0
	D_worktree=$main/.claude/worktrees/$n # the name without its profile, as cwt and claude --worktree name it
	owner=$(claimed "$D_worktree")
	[[ -z $owner ]] || die "$D_worktree belongs to $owner; choose another --name"
	[[ ! -e $D_worktree ]] || die "$D_worktree already exists (cwt, or by hand); choose another --name"
	D_dir=$D_worktree${sub:+/${sub%/}}
	D_branch=${branch:-worktree-$n}
	D_base=${base:-$(default_base)}
	NEW=1
}

start() {
	local pos=() name='' branch='' base='' here='' trust='' prompt='' allow='' remote=''
	while [[ $# -gt 0 ]]; do
		case $1 in
		--here | --trust) printf -v "${1#--}" 1; shift ;;
		--name | --branch | --base | --prompt | --allow)
			[[ $# -ge 2 ]] || usage
			printf -v "${1#--}" '%s' "$2"
			shift 2
			;;
		-*) usage ;;
		*) pos+=("$1"); shift ;;
		esac
	done
	NEW=''
	lock
	case ${#pos[@]} in
	1)
		[[ -z $name$branch$base$here$allow ]] || usage # a defined session keeps its definition
		NAME=${pos[0]}
		load "$NAME" || die "no definition for $NAME (session.sh ls)"
		;;
	2) define "${pos[@]}" ;;
	*) usage ;;
	esac
	profile "$D_profile"

	if alive "$NAME" && dead "$NAME"; then
		tm kill-session -t "=$NAME" # claude failed earlier
	fi
	if alive "$NAME"; then # the name is the key: never a second claude
		local was
		was=$(tm display-message -pt "=$NAME:" '#{session_path}')
		[[ $was == "$D_dir" ]] || die "$NAME already runs in $was; choose another --name"
		flock -u 9
		echo "exists $NAME"
		ready "$NAME" "$trust"
		return
	fi
	if [[ -n $D_worktree ]] && ! is_worktree "$D_worktree"; then add_worktree "$NEW"; fi
	if [[ ! -d $D_dir && -n $D_worktree ]]; then # the subdirectory isn't on the branch (any more)
		echo "warning: ${D_dir#"$D_worktree"/} is not on $D_branch; starting at the worktree's top, in a new conversation" >&2
		D_dir=$D_worktree D_id=$(cat /proc/sys/kernel/random/uuid) # --continue looks only in the starting directory
	fi
	[[ -d $D_dir ]] || die "$D_dir is gone"
	save "$NAME"

	# A fresh environment (tmux's TMUX and TMUX_PANE kept, so the session knows where it runs), then the user's
	# interactive shell rc, as a new terminal gets, then the profile: nothing from the caller or the tmux server
	# (CLAUDE_CODE_* markers, another account's variables) reaches the session.
	local v vars=("PATH=$PATH" TERM=tmux-256color)
	for v in HOME USER LOGNAME SHELL LANG XDG_RUNTIME_DIR DBUS_SESSION_BUS_ADDRESS SSH_AUTH_SOCK TMUX_TMPDIR \
		DISPLAY WAYLAND_DISPLAY KRB5CCNAME; do
		[[ -z ${!v:-} ]] || vars+=("$v=${!v}")
	done
	local sh=${SHELL:-/bin/bash}
	[[ ${sh##*/} == @(zsh|bash) ]] || sh=/bin/bash
	# Inside: source the env file, apply the profile, then run claude (the rc's claude() guard, if it has one). Once
	# the session has a conversation, a worktree session continues the newest one in its own directory (so a /clear
	# inside it carries over); an in-place session, whose directory the user shares, resumes its id.
	# shellcheck disable=SC2016 # expanded by the inner shell
	local run=("$sh" -i -c 'f=$1 id=$2 wt=$3; shift 3; if [ -n "$f" ]; then set -a; . "$f" || exit 1; set +a; fi
		while [ "$1" != -- ]; do if [ "$1" = -u ]; then unset "$2"; shift; else export "$1"; fi; shift; done
		shift; c=$1; shift
		if ! find "${CLAUDE_CONFIG_DIR:-$HOME/.claude}/projects" -mindepth 2 -maxdepth 2 -name "$id.jsonl" 2>/dev/null |
			grep -q .; then "$c" --session-id "$id" "$@"
		elif [ -n "$wt" ]; then "$c" --continue "$@"
		else "$c" --resume "$id" "$@"; fi' session "$ENV_FILE" "$D_id" "$D_worktree" "${SETS[@]}" --)
	local cmd=(claude --remote-control "$NAME" --name "$NAME") root=${D_worktree:-$D_dir}
	if [[ -n $D_allow ]]; then # these rules (plus the user's and checkout's settings allow rules); the rest is denied
		cmd+=(--permission-mode dontAsk --allowedTools "$D_allow")
	else
		cmd+=(--dangerously-skip-permissions)
	fi
	[[ ! -f $root/.claude-plugin/plugin.json ]] || cmd+=(--plugin-dir "$root") # a plugin under development
	[[ -z $prompt ]] || cmd+=(-- "$prompt") # --allowedTools would take a bare prompt as a rule
	# remain-on-exit failed: a claude that fails keeps its screen for ready() to show. The client runs in the clean
	# environment too, since a tmux server it starts keeps it for every later session; 9>&-: nor the lock.
	# shellcheck disable=SC2016 # expanded by sh in the pane
	local pane=(sh -c 'exec env -i TMUX="$TMUX" TMUX_PANE="$TMUX_PANE" "$@"' sh "${vars[@]}" "${run[@]}" "${cmd[@]}")
	pane=("${pane[@]/%;/\\;}") # tmux would take a trailing ; as the end of its command
	env -i "${vars[@]}" tmux ${SESSIONS_TMUX_SOCKET:+-L "$SESSIONS_TMUX_SOCKET"} new-session -d -s "$NAME" -c "$D_dir" \
		-x 200 -y 50 "${pane[@]}" \; set-option -t "=$NAME:" remain-on-exit failed 9>&-
	flock -u 9
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
			screen "$name" -S - | tail -5 >&2 # a dead pane's last output is only in its scrollback
			tm kill-session -t "=$name"
			die "$name failed before it was ready"
		elif grep -q '^❯' <<<"$s"; then # the input box; the trust menu's cursor is indented
			echo "ready $name"
			return 0
		elif [[ -z $sent ]] && grep -qE '^ *❯ *([0-9]+\. *)?No, exit *$' <<<"$s" &&
			grep -qE '^ +([0-9]+\. *)?Yes, I trust this folder *$' <<<"$s"; then # the dialog, cursor on No: Down, Enter
			[[ -n $trust ]] || { echo "needs-trust $name (tmux attach -t $name, or: start $name --trust)"; exit 3; }
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
		for n in $(defined); do
			alive "$n" || { load "$n" && echo "$n stopped $D_dir"; }
		done
	} | sort
}

stop() {
	[[ $# -eq 1 ]] || usage
	named "$1"
	alive "$1" || die "no session $1"
	if [[ -n ${TMUX_PANE:-} ]]; then # not the one this runs in
		local me
		me=$(tmux display-message -pt "$TMUX_PANE" '#{socket_path} #S' 2>/dev/null || true)
		[[ $me != "$(tm display-message -pt "=$1:" '#{socket_path} #S')" ]] || die "refusing to stop my own session"
	fi
	tm kill-session -t "=$1"
	if [[ -f $STATE/$1 ]]; then echo "stopped $1 (start $1 resumes it; clean $1 removes it)"; else echo "stopped $1"; fi
}

# Adds nothing to the base: merged (also by squash or rebase), or never used.
# shellcheck disable=SC2143 # grep -q exits early, and under pipefail that fails the pipe
finished() { # an unknown base is never finished
	local t mb p
	t=$(g rev-parse --verify -q "$D_base^{tree}") || return 1
	[[ $(g merge-tree --write-tree "$D_base" "$1" 2>/dev/null) != "$t" ]] || return 0 # merged, or nothing new
	# Merged as other commits, with the base changing the same lines since: rebased (each commit's patch is on the
	# base) or squashed (the whole branch's patch is). No grep -q: under pipefail its early exit fails the pipe.
	mb=$(g merge-base "$D_base" "$1") || return 1
	[[ -n $(g cherry "$D_base" "$1") && -z $(g cherry "$D_base" "$1" | grep '^+') ]] && return 0
	p=$(g diff "$mb" "$1" | git patch-id --stable) && [[ -n $p ]] || return 1
	[[ -n $(g log -p --format='commit %H' "$mb..$D_base" | git patch-id --stable | grep "^${p%% *} ") ]]
}

# Why a stopped session's worktree must be kept, or nothing. all=1 (clean with no name) also keeps unfinished work;
# clean <name> removes that (a branch keeps the commits). Ignored files are build output: the worktree started with
# none.
keep() {
	local all=$1 wt=$D_worktree out tip p # every check fails closed: an error keeps the worktree
	out=$(git -C "$wt" status --porcelain --untracked-files=all --ignore-submodules=none) ||
		{ echo "git status failed"; return; }
	[[ -z $out ]] || { echo "uncommitted changes"; return; }
	for p in /proc/[0-9]*/cwd; do # someone working there by hand
		[[ $(readlink "$p" 2>/dev/null)/ != "$wt"/* ]] || { echo "in use by process ${p//[^0-9]/}"; return; }
	done
	out=$(git -C "$wt" submodule foreach -q --recursive 'git log --oneline -1 HEAD --branches --not --remotes') ||
		{ echo "git submodule failed"; return; }
	[[ -z $out ]] || { echo "unpushed submodule commits"; return; }
	tip=$(git -C "$wt" rev-parse HEAD) || { echo "no HEAD"; return; } # the worktree's own, not the main checkout's
	! finished "$tip" || return 0
	git -C "$wt" symbolic-ref --quiet HEAD >/dev/null || { echo "commits on a detached HEAD"; return; }
	[[ -z $all ]] || echo "unmerged work"
}

clean() {
	[[ $# -le 1 ]] || usage
	local names=("$@") all='' why
	lock
	[[ $# == 1 ]] || { all=1; mapfile -t names < <(defined); }
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
		if is_worktree "$D_worktree"; then
			fetch_base
			why=$(keep "$all")
			[[ -z $why ]] || { echo "kept $NAME: $why"; continue; }
			# --force: keep() checked what it overrides (submodules); a single one still refuses a locked worktree.
			g worktree remove --force "$D_worktree" || { echo "kept $NAME: git worktree remove failed"; continue; }
		fi
		if ! g show-ref --verify --quiet "refs/heads/$D_branch"; then
			echo "removed $NAME"
		elif [[ $D_created == 1 ]] && finished "refs/heads/$D_branch" && g branch -q -D "$D_branch"; then
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
