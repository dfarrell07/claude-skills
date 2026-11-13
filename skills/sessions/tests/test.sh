#!/usr/bin/env bash
# Offline tests for session.sh: a fake claude on a private tmux server.
set -uo pipefail

here=$(cd "$(dirname "$0")" && pwd)
sc=$here/../scripts/session.sh
tmp=$(mktemp -d "${TMPDIR:-/tmp}/sessions-test.XXXXXX")
export SESSIONS_TMUX_SOCKET=sessions-test-$$ SESSIONS_CLAUDE=$tmp/claude SESSIONS_WAIT=5 LEAK=1 CLAUDECODE=1
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
mkdir -p "$tmp/a/repo" "$tmp/b/repo"

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
check "remote control named" 0 "--remote-control
personal2-repo
--name
personal2-repo" cat "$tmp/a/repo/.args.out"
check "worktree and prompt" 0 "--worktree
fix-1
do it" bash -c "bash '$sc' start personal '$tmp/a/repo' --worktree fix-1 --prompt 'do it' >/dev/null; cat '$tmp/a/repo/.args.out'"
check "unknown profile" 2 "unknown profile" bash "$sc" start bogus "$tmp/a/repo"
check "missing work env" 1 "missing" env SESSIONS_WORK_ENV="$tmp/none" bash "$sc" start work "$tmp/a/repo"
check "ls shows ours" 0 "personal2-repo $tmp/a/repo" bash "$sc" ls
tmux -L "$SESSIONS_TMUX_SOCKET" new-session -d -s mine sleep 600
check "ls hides others" 1 "" bash -c "bash '$sc' ls | grep -q mine"
check "stop refuses others" 2 "one session name" bash "$sc" stop mine
check "stop" 0 "stopped personal2-b" bash "$sc" stop personal2-b
check "stop missing" 1 "no session" bash "$sc" stop personal2-b

[[ $fails == 0 ]] && echo "all passed" || { echo "$fails failed"; exit 1; }
