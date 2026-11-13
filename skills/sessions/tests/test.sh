#!/usr/bin/env bash
# Offline tests for session.sh: a fake claude on a private tmux server, a local bare repository as origin.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
sc=$here/../scripts/session.sh
tmp=$(mktemp -d "${TMPDIR:-/tmp}/sessions-test.XXXXXX")
tmp=$(cd "$tmp" && pwd -P)
export SESSIONS_TMUX_SOCKET=sessions-test-$$ SESSIONS_CLAUDE=$tmp/claude SESSIONS_WAIT=5 LEAK=1 CLAUDECODE=1
export HOME=$tmp/home SHELL=/bin/bash SESSIONS_WORK_ENV=$tmp/work-env SESSIONS_STATE=$tmp/state
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t GIT_CONFIG_NOSYSTEM=1
trap 'tmux -L "$SESSIONS_TMUX_SOCKET" kill-server 2>/dev/null; rm -rf -- "$tmp"' EXIT
fails=0

# Logs its env, args and cwd under $HOME/log/<name>; asks for trust once per directory tree, like claude.
cat >"$tmp/claude" <<'EOF'
#!/bin/bash
n=; a=("$@"); for ((i = 0; i < ${#a[@]}; i++)); do [[ ${a[i]} == --name ]] && n=${a[i+1]}; done
mkdir -p "$HOME/log"; env | sort >"$HOME/log/$n.env"; printf '%s\n' "$@" >"$HOME/log/$n.args"; pwd >"$HOME/log/$n.pwd"
t=; d=$PWD; while :; do grep -qxF "$d" "$HOME/trusted" 2>/dev/null && t=1; [[ $d == / ]] && break; d=$(dirname "$d"); done
if [[ -z $t ]]; then printf ' ❯ No, exit\n   Yes, I trust this folder\n'; read -r; echo "$PWD" >>"$HOME/trusted"; fi
echo "❯ Try"; exec sleep 600
EOF
chmod +x "$tmp/claude"
mkdir -p "$tmp/plain" "$tmp/home/.claude/projects/p"
# The shell rc a new terminal gets; personal profiles must drop its Vertex variables.
echo 'export RCVAR=1 ANTHROPIC_VERTEX_PROJECT_ID=rc-project' >"$tmp/home/.bashrc"
echo 'WORKVAR=1' >"$tmp/work-env"

# origin: main, plus a branch only the remote has. repo: a clone with a feature branch checked out.
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/seed" 2>/dev/null
git -C "$tmp/seed" commit -q --allow-empty -m init
mkdir -p "$tmp/seed/sub" && echo s >"$tmp/seed/sub/f" && echo '*.log' >"$tmp/seed/.gitignore"
git -C "$tmp/seed" add -A && git -C "$tmp/seed" commit -qm files && git -C "$tmp/seed" push -q origin main
git -C "$tmp/seed" push -q origin main:remote-only
git clone -q "$tmp/origin.git" "$tmp/repo"
git -C "$tmp/repo" checkout -q -b feature && git -C "$tmp/repo" commit -q --allow-empty -m feature
git -C "$tmp/repo" branch -q topic main
echo "$tmp/repo" >"$tmp/home/trusted"
R=$tmp/repo WT=$tmp/repo/.claude/worktrees

check() { # check <name> <expected exit> <expected output substring> <command...>
	local what=$1 code=$2 want=$3 out rc
	shift 3
	out=$("$@" 2>&1)
	rc=$?
	if [[ $rc == "$code" && $out == *"$want"* ]]; then echo "ok   $what"; else
		echo "FAIL $what: exit $rc, output: $out"
		fails=$((fails + 1))
	fi
}
s() { bash "$sc" "$@"; }
log() { cat "$tmp/home/log/$1.$2"; }
id() { sed -n 's/^id=//p' "$tmp/state/$1"; }

echo "-- in place"
check "new dir stops at trust" 3 "needs-trust personal2-plain" s start personal2 "$tmp/plain"
check "rerun with --trust accepts" 0 "ready personal2-plain" s start personal2 "$tmp/plain" --trust
check "rerun is a no-op" 0 "exists personal2-plain" s start personal2 "$tmp/plain"
check "caller env does not leak" 1 "" grep -E '^(LEAK|CLAUDECODE)=' "$tmp/home/log/personal2-plain.env"
check "profile env set" 0 "CLAUDE_CONFIG_DIR=$HOME/.claude-per2" log personal2-plain env
check "shell rc applied" 0 "RCVAR=1" log personal2-plain env
check "personal drops Vertex" 1 "" grep -E '^(ANTHROPIC_VERTEX_PROJECT_ID|CLAUDE_CODE_USE_VERTEX=1)' "$tmp/home/log/personal2-plain.env"
check "remote control named, new conversation" 0 "--session-id
$(id personal2-plain)
--remote-control
personal2-plain
--name
personal2-plain" log personal2-plain args
check "--here in a repo works in place" 0 "ready personal-repo" s start personal "$R" --here
check "  no worktree" 0 "$R" log personal-repo pwd
check "work profile" 0 "ready work-plain" s start work "$tmp/plain"
check "  work env sourced, Vertex kept" 0 "ANTHROPIC_VERTEX_PROJECT_ID=rc-project
CLAUDE_CODE_USE_VERTEX=1" bash -c "grep -E '^(ANTHROPIC_VERTEX|CLAUDE_CODE_USE_VERTEX)' '$tmp/home/log/work-plain.env'"
check "  work env file" 0 "WORKVAR=1" log work-plain env
check "--tools replaces skip-permissions" 0 "dontAsk
--allowedTools
Read" bash -c "bash '$sc' start personal '$tmp/plain' --name t --tools Read >/dev/null; cat '$tmp/home/log/personal-t.args'"
check "unknown profile" 2 "unknown profile" s start bogus "$tmp/plain"
check "failing claude shows why" 1 "failed before it was ready" env SESSIONS_CLAUDE=/bin/false bash "$sc" start personal "$tmp/plain" --name f

echo "-- worktrees"
check "a repo gets a worktree" 0 "worktree $WT/repo-1 on new branch worktree-repo-1 from origin/main" s start personal "$R"
check "  session runs there" 0 "$WT/repo-1" log personal-repo-1 pwd
check "  from origin's main, not the checked-out branch" 0 "$(git -C "$R" rev-parse origin/main)" git -C "$WT/repo-1" rev-parse HEAD
check "  no upstream to push to by accident" 128 "" git -C "$WT/repo-1" rev-parse --abbrev-ref '@{u}'
check "  main checkout stays clean" 0 "" bash -c "[[ -z \$(git -C '$R' status --porcelain) ]]"
check "a second agent gets the next one" 0 "ready personal-repo-2" s start personal "$R"
check "named, from a subdirectory, with a task" 0 "ready personal-fix-1" s start personal "$R/sub" --name fix-1 --prompt "do it"
check "  starts in the same subdirectory" 0 "$WT/fix-1/sub" log personal-fix-1 pwd
check "  prompt after --" 0 "--
do it" log personal-fix-1 args
check "a remote-only branch" 0 "on remote-only from origin/remote-only" s start personal "$R" --branch remote-only
check "  without tracking it" 128 "" git -C "$WT/remote-only" rev-parse --abbrev-ref '@{u}'
check "an existing local branch" 0 "ready personal-topic" s start personal "$R" --branch topic
check "same name for another repo refused" 1 "already defined for $R" s start personal "$tmp/plain" --name repo-1
check "a worktree another profile owns refused" 1 "belongs to personal-fix-1" s start work "$R" --name fix-1
check "ls" 0 "personal-repo-1 running $WT/repo-1" s ls

echo "-- restarts"
check "stop" 0 "stopped personal-repo-1" s stop personal-repo-1
check "ls shows it stopped" 0 "personal-repo-1 stopped $WT/repo-1" s ls
touch "$tmp/home/.claude/projects/p/$(id personal-repo-1).jsonl" # its first message was saved
check "start <name> resumes it" 0 "ready personal-repo-1" s start personal-repo-1
check "  same conversation" 0 "--resume
$(id personal-repo-1)" log personal-repo-1 args
check "  same worktree" 0 "$WT/repo-1" log personal-repo-1 pwd
check "start <unknown>" 1 "no definition" s start personal-nope

echo "-- clean"
for n in repo repo-1 repo-2 fix-1 remote-only topic; do s stop "personal-$n" >/dev/null; done
check "running sessions are not cleaned" 1 "running; stop it first" s clean personal2-plain
echo x >"$WT/fix-1/new.txt"
echo w >"$WT/repo-2/w" && git -C "$WT/repo-2" add w && git -C "$WT/repo-2" commit -qm wip
echo y >"$WT/topic/build.log"
check "bulk clean" 0 "removed personal-repo-1 and branch worktree-repo-1" s clean
check "  kept: uncommitted" 0 "personal-fix-1 stopped" s ls
check "  kept: unmerged work" 0 "personal-repo-2 stopped" s ls
check "  kept: ignored files" 0 "personal-topic stopped" s ls
check "  remote-only branch with nothing new is finished" 1 "" bash -c "bash '$sc' ls | grep -q remote-only"
check "  working-in-place definitions stay" 0 "personal-repo stopped" s ls
check "uncommitted work is never removed" 0 "kept personal-fix-1: uncommitted" s clean personal-fix-1
check "clean <name> takes ignored files" 0 "removed personal-topic (branch topic kept)" s clean personal-topic
check "  the user's own branch survives" 0 "topic" git -C "$R" branch --list topic
check "clean <name> with unmerged work keeps the branch" 0 "removed personal-repo-2 (branch worktree-repo-2 kept)" s clean personal-repo-2
check "  its commit is safe" 0 "wip" git -C "$R" log -1 --format=%s worktree-repo-2
check "squash-merged work counts as finished" 0 "ready personal-sq" s start personal "$R" --name sq
echo z >"$WT/sq/z" && git -C "$WT/sq" add z && git -C "$WT/sq" commit -qm z
git -C "$tmp/seed" pull -q && echo z >"$tmp/seed/z" && git -C "$tmp/seed" add z && git -C "$tmp/seed" commit -qm squashed &&
	git -C "$tmp/seed" push -q origin main
s stop personal-sq >/dev/null
check "  removed with its branch" 0 "removed personal-sq and branch worktree-sq" s clean
check "detached commits are never removed" 0 "ready personal-det" s start personal "$R" --name det
git -C "$WT/det" checkout -q --detach && echo l >"$WT/det/l" && git -C "$WT/det" add l && git -C "$WT/det" commit -qm lost
s stop personal-det >/dev/null
check "  kept" 0 "kept personal-det: commits on a detached HEAD" s clean personal-det
check "clean <name> on an in-place session forgets it" 0 "forgot personal-repo" s clean personal-repo

echo "-- stop"
tmux -L "$SESSIONS_TMUX_SOCKET" new-session -d -s mine sleep 600
check "ls hides others" 1 "" bash -c "bash '$sc' ls | grep -q mine"
check "stop refuses others" 2 "one session name" s stop mine
check "stop missing" 1 "no session" s stop personal-nope

[[ $fails == 0 ]] && echo "all passed" || { echo "$fails failed"; exit 1; }
