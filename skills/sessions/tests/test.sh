#!/usr/bin/env bash
# Offline tests for session.sh: a fake claude on a private tmux server.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
sc=$here/../scripts/session.sh
tmp=$(mktemp -d "${TMPDIR:-/tmp}/sessions-test.XXXXXX")
export SESSIONS_TMUX_SOCKET=sessions-test-$$ SESSIONS_CLAUDE=$tmp/claude SESSIONS_WAIT=5 LEAK=1 CLAUDECODE=1
export HOME=$tmp/home SHELL=/bin/bash SESSIONS_WORK_ENV=$tmp/work-env
trap 'tmux -L "$SESSIONS_TMUX_SOCKET" kill-server 2>/dev/null; rm -rf -- "$tmp"' EXIT
fails=0

# Records its env and args; asks for trust once per directory, like claude.
cat >"$tmp/claude" <<'EOF'
#!/bin/bash
env | sort >.env.out; printf '%s\n' "$@" >.args.out
if [[ ! -e .trusted ]]; then printf ' ❯ No, exit\n   Yes, I trust this folder\n'; read -r; touch .trusted; fi
echo "❯ Try"; exec sleep 600
EOF
chmod +x "$tmp/claude"
mkdir -p "$tmp/a/repo" "$tmp/b/repo" "$tmp/c/repo" "$tmp/home"
# The shell rc a new terminal gets; personal profiles must drop its Vertex variables.
echo 'export RCVAR=1 ANTHROPIC_VERTEX_PROJECT_ID=rc-project' >"$tmp/home/.bashrc"
echo 'WORKVAR=1' >"$tmp/work-env"

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

check "new dir stops at trust" 3 "needs-trust personal2-repo" bash "$sc" start personal2 "$tmp/a/repo"
check "rerun with --trust accepts" 0 "ready personal2-repo" bash "$sc" start personal2 "$tmp/a/repo" --trust
check "rerun is a no-op" 0 "exists personal2-repo" bash "$sc" start personal2 "$tmp/a/repo"
check "same name, other dir refused" 1 "already runs in" bash "$sc" start personal2 "$tmp/b/repo"
check "--name disambiguates" 0 "ready personal2-b" bash "$sc" start personal2 "$tmp/b/repo" --name b --trust
check "caller env does not leak" 1 "" grep -E '^(LEAK|CLAUDECODE)=' "$tmp/a/repo/.env.out"
check "profile env set" 0 "CLAUDE_CONFIG_DIR=$HOME/.claude-per2" cat "$tmp/a/repo/.env.out"
check "shell rc applied" 0 "RCVAR=1" cat "$tmp/a/repo/.env.out"
check "personal drops Vertex" 1 "" grep -E '^(ANTHROPIC_VERTEX_PROJECT_ID|CLAUDE_CODE_USE_VERTEX=1)' "$tmp/a/repo/.env.out"
check "remote control named" 0 "--remote-control
personal2-repo
--name
personal2-repo" cat "$tmp/a/repo/.args.out"
check "worktree and prompt" 0 "--worktree
fix-1
--
do it" bash -c "bash '$sc' start personal '$tmp/a/repo' --worktree fix-1 --prompt 'do it' >/dev/null; cat '$tmp/a/repo/.args.out'"
check "unknown profile" 2 "unknown profile" bash "$sc" start bogus "$tmp/a/repo"
check "work profile" 0 "ready work-repo" bash "$sc" start work "$tmp/c/repo" --trust
check "work env sourced, Vertex kept" 0 "ANTHROPIC_VERTEX_PROJECT_ID=rc-project
CLAUDE_CODE_USE_VERTEX=1" bash -c "grep -E '^(ANTHROPIC_VERTEX|CLAUDE_CODE_USE_VERTEX|WORKVAR)' '$tmp/c/repo/.env.out'"
check "work env file" 0 "WORKVAR=1" cat "$tmp/c/repo/.env.out"
check "--tools replaces skip-permissions" 0 "dontAsk
--allowedTools
Read" bash -c "bash '$sc' start personal '$tmp/c/repo' --name t --tools Read >/dev/null; cat '$tmp/c/repo/.args.out'"
check "failing claude shows why" 1 "failed before it was ready" env SESSIONS_CLAUDE=/bin/false bash "$sc" start personal "$tmp/b/repo" --name f
check "failed session cleaned up" 1 "" bash -c "bash '$sc' ls | grep -q '^personal-f '"
check "ls shows ours" 0 "personal2-repo running $tmp/a/repo" bash "$sc" ls
tmux -L "$SESSIONS_TMUX_SOCKET" new-session -d -s mine sleep 600
check "ls hides others" 1 "" bash -c "bash '$sc' ls | grep -q mine"
check "stop refuses others" 2 "one session name" bash "$sc" stop mine
check "stop" 0 "stopped personal2-b" bash "$sc" stop personal2-b
check "stop missing" 1 "no session" bash "$sc" stop personal2-b

[[ $fails == 0 ]] && echo "all passed" || { echo "$fails failed"; exit 1; }
