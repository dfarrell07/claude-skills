#!/usr/bin/env bash
# Offline tests for session.sh: a fake claude on PATH, a private tmux server, local bare repositories as remotes.
set -uo pipefail

sc=$(cd "$(dirname "$0")/../scripts" && pwd)/session.sh
tmp=$(mktemp -d "${TMPDIR:-/tmp}/sessions-test.XXXXXX")
tmp=$(cd "$tmp" && pwd -P)
unset XDG_STATE_HOME TMUX TMUX_PANE
export SESSIONS_TMUX_SOCKET=sessions-test-$$ SESSIONS_WAIT=5 LEAK=1 CLAUDECODE=1
export HOME=$tmp/home SHELL=/bin/bash PATH=$tmp/bin:$PATH GIT_CONFIG_NOSYSTEM=1 GIT_CONFIG_GLOBAL=$tmp/gitconfig
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@t GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@t
trap 'tmux -L "$SESSIONS_TMUX_SOCKET" kill-server 2>/dev/null; rm -rf -- "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/plain" "$HOME/.claude/projects/p" "$HOME/.config/claude"
git config --global protocol.file.allow always # local submodules
fails=0

# The fake claude logs env, args and cwd to $HOME/log/<name>.*; asks for trust once per directory tree and takes only
# Down then Enter, like claude; fails when $HOME/fail-<name> exists; runs stop on itself when $HOME/stopself-<name>
# exists.
cat >"$tmp/bin/claude" <<'EOF'
#!/bin/bash
n=; a=("$@"); for ((i = 0; i < ${#a[@]}; i++)); do [[ ${a[i]} == --name ]] && n=${a[i+1]}; done
mkdir -p "$HOME/log"; env | sort >"$HOME/log/$n.env"; printf '%s\n' "$@" >"$HOME/log/$n.args"; pwd >"$HOME/log/$n.pwd"
[[ ! -e $HOME/fail-$n ]] || { echo "fake failure"; exit 1; }
t=; d=$PWD; while :; do grep -qxF "$d" "$HOME/trusted" 2>/dev/null && t=1; [[ $d == / ]] && break; d=$(dirname "$d"); done
if [[ -z $t ]]; then
	printf ' ❯ No, exit\n   Yes, I trust this folder\n'; IFS= read -r l; [[ $l == *$'\e'*B* ]] || exit 1
	echo "$PWD" >>"$HOME/trusted"
fi
if [[ -e $HOME/menu-$n ]]; then # another dialog, with the trust sentence elsewhere on screen
	printf 'quoting: "   Yes, I trust this folder"\n ❯ 1. Keep\n   2. Remove\n'; IFS= read -r l; echo "$l" >"$HOME/log/$n.keys"
fi
if [[ -e $HOME/stopself-$n ]]; then
	SESSIONS_TMUX_SOCKET=$(basename "${TMUX%%,*}") bash "$(cat "$HOME/sc")" stop "$n" >"$HOME/log/$n.stop" 2>&1
fi
echo "❯ Try"; exec sleep 600
EOF
chmod +x "$tmp/bin/claude"
echo "$sc" >"$HOME/sc"
# The shell rc a new terminal gets, with a claude() guard like zshrc.j2's; personal profiles drop its Vertex variables.
cat >"$HOME/.bashrc" <<'EOF'
export RCVAR=1 ANTHROPIC_VERTEX_PROJECT_ID=rc-project
claude() { touch "$HOME/guard"; command claude "$@"; }
EOF
echo 'WORKVAR=1' >"$HOME/.config/claude/work-env"

# origin: main, a branch only the remote has, and a submodule. repo: a clone with a feature branch checked out.
git init -q --bare -b main "$tmp/sub.git"
git clone -q "$tmp/sub.git" "$tmp/subseed" 2>/dev/null
git -C "$tmp/subseed" commit -q --allow-empty -m s && git -C "$tmp/subseed" push -q origin main
git init -q --bare -b main "$tmp/origin.git"
git clone -q "$tmp/origin.git" "$tmp/seed" 2>/dev/null
mkdir -p "$tmp/seed/sub" && echo s >"$tmp/seed/sub/f" && echo '*.log' >"$tmp/seed/.gitignore"
git -C "$tmp/seed" submodule add -q "$tmp/sub.git" sm
git -C "$tmp/seed" submodule add -q "$tmp/sub.git" sm2 # two: grep -q under pipefail once hid them
git -C "$tmp/seed" add -A && git -C "$tmp/seed" commit -qm files && git -C "$tmp/seed" push -q origin main
git -C "$tmp/seed" push -q origin main:remote-only
git -C "$tmp/seed" commit -q --allow-empty -m pr && git -C "$tmp/seed" push -q origin HEAD:pr && git -C "$tmp/seed" reset -q --hard origin/main
git clone -q --recurse-submodules "$tmp/origin.git" "$tmp/repo"
git -C "$tmp/repo" checkout -q -b feature && git -C "$tmp/repo" commit -q --allow-empty -m feature
git -C "$tmp/repo" branch -q topic main
R=$tmp/repo WT=$tmp/repo/.claude/worktrees
echo "$R" >"$HOME/trusted"

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
log() { cat "$HOME/log/$1.$2"; }
id() { sed -n 's/^id=//p' "$HOME/.local/state/claude-sessions/$1"; }
lacks() { ! grep -qE -- "$1" "$2"; } # lacks <regex> <file>
hasnt() { [[ $1 != *"$2"* ]]; } # hasnt <text> <substring>
ls_lacks() { local out; out=$(s ls) && ! grep -qE -- "$1" <<<"$out"; }

echo "-- in place"
check "new dir stops at trust" 3 "needs-trust personal2-plain" s start personal2 "$tmp/plain"
check "rerun with --trust answers it" 0 "ready personal2-plain" s start personal2 "$tmp/plain" --trust
check "rerun is a no-op" 0 "exists personal2-plain" s start personal2 "$tmp/plain"
check "caller env does not leak" 0 "" lacks '^(LEAK|CLAUDECODE)=' "$HOME/log/personal2-plain.env"
check "profile env set" 0 "CLAUDE_CONFIG_DIR=$HOME/.claude-per2" log personal2-plain env
check "shell rc applied" 0 "RCVAR=1" log personal2-plain env
rm -f "$HOME/guard" && s start personal "$tmp/plain" --name g >/dev/null
check "  through its claude() guard" 0 "" test -e "$HOME/guard"
check "personal drops Vertex" 0 "" lacks '^(ANTHROPIC_VERTEX_PROJECT_ID|CLAUDE_CODE_USE_VERTEX=1)' "$HOME/log/personal2-plain.env"
check "new conversation, named, Remote Control" 0 "--session-id
$(id personal2-plain)
--remote-control
personal2-plain
--name
personal2-plain
--dangerously-skip-permissions" log personal2-plain args
check "  no --plugin-dir for a directory that is no plugin" 0 "" lacks plugin-dir "$HOME/log/personal2-plain.args"
mkdir -p "$tmp/plug/.claude-plugin" && echo '{}' >"$tmp/plug/.claude-plugin/plugin.json" && echo "$tmp/plug" >>"$HOME/trusted"
check "a plugin under development is loaded" 0 "--plugin-dir
$tmp/plug" bash -c "bash '$sc' start personal '$tmp/plug' >/dev/null; cat '$HOME/log/personal-plug.args'"
check "--here in a repo works in place" 0 "ready personal-repo" s start personal "$R" --here
check "  no worktree" 0 "$R" log personal-repo pwd
check "work profile" 0 "ready work-plain" s start work "$tmp/plain"
check "  Vertex kept" 0 "ANTHROPIC_VERTEX_PROJECT_ID=rc-project" log work-plain env
check "  Vertex on" 0 "CLAUDE_CODE_USE_VERTEX=1" log work-plain env
check "  work env sourced" 0 "WORKVAR=1" log work-plain env
check "  Remote Control, as cwork" 0 "--remote-control" log work-plain args
check "--allow: an allowlist" 0 "dontAsk
--allowedTools
Read" bash -c "bash '$sc' start personal '$tmp/plain' --name t --allow Read >/dev/null; cat '$HOME/log/personal-t.args'"
check "  instead of skip-permissions" 0 "" lacks dangerously "$HOME/log/personal-t.args"
check "a flag without its value" 2 "usage" s start personal "$tmp/plain" --name
mkdir -p "$HOME/src/org/thing" "$HOME/repos/dup" "$HOME/src/o/dup"
check "a bare name is looked up (~/src/<org>/<name>)" 0 "ready personal-thing" s start personal thing --trust
check "  " 0 "$HOME/src/org/thing" log personal-thing pwd
check "  several matches refused" 2 "several match" s start personal dup
check "  none" 1 "no such directory: nowhere" s start personal nowhere
check "unknown profile" 2 "unknown profile" s start bogus "$tmp/plain"
check "a path is no session name" 2 "not a session name" s clean ../../home/.bashrc
check "  " 2 "not a session name" s start ../x
check "start <session> takes no definition flags" 2 "usage" s start personal2-plain --branch x
check "a base that looks like an option" 2 "bad base" s start personal "$tmp/plain" --base --upload-pack=x
check "a base that is a refspec" 2 "bad base" s start personal "$R" --name rs --base 'origin/+main:refs/heads/topic'
check "an --allow that is an option" 2 "bad --allow" s start personal "$tmp/plain" --name a --allow --dangerously-skip-permissions
mkdir -p "$tmp/x#y"
check "a directory with # (a tmux format)" 2 "path with #" s start personal "$tmp/x#y"
check "a bad branch name" 2 "bad branch name" s start personal "$R" --branch 'a..b'
check "  nor @" 2 "bad branch name" s start personal "$R" --branch @
check "--branch in place refused" 2 "need a worktree" s start personal "$tmp/plain" --branch feat
check "a prompt ending in ;" 0 "--
run it;" bash -c "bash '$sc' start personal '$tmp/plain' --name semi --prompt 'run it;' >/dev/null; cat '$HOME/log/personal-semi.args'"
touch "$HOME/fail-personal-f"
check "a failing claude shows why" 1 "fake failure" s start personal "$tmp/plain" --name f
if command -v zsh >/dev/null; then
	echo 'export RCVAR=zsh' >"$HOME/.zshrc"
	check "zsh as the user's shell" 0 "ready personal-z" env SHELL="$(command -v zsh)" bash "$sc" start personal "$tmp/plain" --name z
	check "  its rc applied" 0 "RCVAR=zsh" log personal-z env
fi
touch "$HOME/menu-personal-menu"
check "a session in another dialog" 0 "waiting personal-menu" s start personal "$tmp/plain" --name menu
check "  gets no keys from --trust" 0 "waiting personal-menu" s start personal "$tmp/plain" --name menu --trust
check "  " 1 "" test -s "$HOME/log/personal-menu.keys"
touch "$HOME/stopself-personal-me"
check "a session that stops itself" 0 "ready personal-me" s start personal "$tmp/plain" --name me
check "  is refused" 0 "refusing to stop my own session" log personal-me stop

echo "-- worktrees"
check "a repo gets a worktree" 0 "worktree $WT/repo-1 on new branch worktree-repo-1 from origin/main" s start personal "$R"
check "  session runs there" 0 "$WT/repo-1" log personal-repo-1 pwd
check "  from origin's main, not the checked-out branch" 0 "$(git -C "$R" rev-parse origin/main)" git -C "$WT/repo-1" rev-parse HEAD
check "  no upstream to push to by accident" 128 "" git -C "$WT/repo-1" rev-parse --abbrev-ref '@{u}'
check "  submodules initialized" 0 "" test -e "$WT/repo-1/sm/.git" -a -e "$WT/repo-1/sm2/.git"
check "  main checkout stays clean" 0 "" test -z "$(git -C "$R" status --porcelain)"
check "a second agent gets the next one" 0 "ready personal-repo-2" s start personal "$R"
check "named, from a subdirectory, with a task" 0 "ready personal-fix-1" s start personal "$R/sub" --name fix-1 --prompt "do it"
check "  starts in the same subdirectory" 0 "$WT/fix-1/sub" log personal-fix-1 pwd
check "  prompt after --" 0 "--
do it" log personal-fix-1 args
check "a remote-only branch" 0 "on new branch remote-only from origin/remote-only" s start personal "$R" --branch remote-only
check "  without tracking it" 128 "" git -C "$WT/remote-only" rev-parse --abbrev-ref '@{u}'
check "an existing local branch" 0 "ready personal-topic" s start personal "$R" --branch topic
check "same name for another repo refused" 1 "already defined for $R" s start personal "$tmp/plain" --name repo-1
check "a worktree another profile owns refused" 1 "belongs to personal-fix-1" s start work "$R" --name fix-1
check "another branch under a used name refused" 1 "is on worktree-fix-1" s start personal "$R" --name fix-1 --branch other
check "--base on an existing session refused" 1 "only to a new session" s start personal "$R" --name fix-1 --base origin/main
mkdir -p "$WT/stray/.claude"
check "a directory already there is never adopted" 1 "already exists" s start personal "$R" --name stray
git -C "$R" worktree add -q "$WT/cw" -b cwt-branch
check "  nor a worktree made outside the launcher" 1 "already exists" s start personal "$R" --name cw --branch wanted
check "a remote branch ahead of the base" 0 "from origin/pr" s start personal "$R" --branch origin/pr
check "  named for the branch, not origin/pr" 0 "" test -z "$(git -C "$R" branch --list 'origin/*')"
git -C "$tmp/seed" commit -q --allow-empty -m fresh && git -C "$tmp/seed" push -q origin HEAD:fresh && git -C "$tmp/seed" reset -q --hard origin/main
check "a branch pushed after the last fetch" 0 "from origin/fresh" s start personal "$R" --branch fresh
check "  is fetched first" 0 "fresh" git -C "$WT/fresh" log -1 --format=%s
check "an in-place name for a worktree session refused" 1 "already defined for $R;" s start personal "$R" --name repo
check "a worktree name for an in-place session refused" 1 "already defined for $R (a worktree)" s start personal "$R" --here --name fix-1
git -C "$R" commit -q --allow-empty -m x && mkdir -p "$R/newdir" && touch "$R/newdir/f" && git -C "$R" add newdir && git -C "$R" commit -qm newdir
check "a subdirectory not on the base starts at the top" 0 "ready personal-nd" s start personal "$R/newdir" --name nd
check "  " 0 "$WT/nd" log personal-nd pwd
git clone -q --template= "$tmp/origin.git" "$tmp/notemplate" && echo "$tmp/notemplate" >>"$HOME/trusted"
check "a clone without .git/info" 0 "ready personal-nt" s start personal "$tmp/notemplate" --name nt
git clone -q --single-branch "$tmp/origin.git" "$tmp/single" && echo "$tmp/single" >>"$HOME/trusted"
git -C "$tmp/seed" commit -q --allow-empty -m off && git -C "$tmp/seed" push -q origin HEAD:off &&
	git -C "$tmp/seed" reset -q --hard origin/main && git -C "$R" fetch -q && mv "$tmp/origin.git" "$tmp/origin.away"
check "offline, --branch takes the remote branch as last fetched" 0 "from origin/off" s start personal "$R" --branch off
check "  not the base" 0 "off" git -C "$WT/off" log -1 --format=%s
mv "$tmp/origin.away" "$tmp/origin.git"
check "a --single-branch clone fetches another base" 0 "from origin/remote-only" s start personal "$tmp/single" --base origin/remote-only
git -C "$R" worktree add -q "$tmp/usb" -b usb && mv "$tmp/usb" "$tmp/usb.away"
check "another worktree's missing directory (unmounted)" 0 "ready personal-um" s start personal "$R" --name um
check "  keeps its registration" 0 "$tmp/usb" git -C "$R" worktree list
mv "$tmp/usb.away" "$tmp/usb"
git clone -q "$tmp/origin.git" "$tmp/my.repo" && echo "$tmp/my.repo" >>"$HOME/trusted"
check "a repository name with a dot" 0 "ready personal-my_repo-1" s start personal "$tmp/my.repo"
git init -q --bare -b devel "$tmp/upstream.git" && git -C "$tmp/seed" push -q "$tmp/upstream.git" main:devel
git clone -q "$tmp/origin.git" "$tmp/fork" && git -C "$tmp/fork" remote add upstream "$tmp/upstream.git"
echo "$tmp/fork" >>"$HOME/trusted"
check "a fork starts from upstream's default branch" 0 "from upstream/devel" s start personal "$tmp/fork"
git -C "$tmp/seed" push -q origin main:rel && git -C "$tmp/seed" commit -q --allow-empty -m fix &&
	git -C "$tmp/seed" push -q "$tmp/upstream.git" HEAD:rel && git -C "$tmp/seed" reset -q --hard origin/main
check "--branch upstream/rel takes upstream's, not the fork's" 0 "from upstream/rel" s start personal "$tmp/fork" --branch upstream/rel
git init -q --separate-git-dir "$tmp/sep.git" "$tmp/sepwt" && git -C "$tmp/sepwt" commit -q --allow-empty -m s
echo "$tmp/sepwt" >>"$HOME/trusted"
check "a checkout whose .git is a file" 0 "ready personal-sepwt-1" s start personal "$tmp/sepwt"
check "ls" 0 "personal-repo-1 running $WT/repo-1" s ls

echo "-- restarts"
check "stop" 0 "stopped personal-repo-1" s stop personal-repo-1
check "ls shows it stopped" 0 "personal-repo-1 stopped $WT/repo-1" s ls
touch "$HOME/.claude/projects/p/$(id personal-repo-1).jsonl" # it has a conversation
check "start <name> continues it in its worktree" 0 "ready personal-repo-1" s start personal-repo-1
check "  the newest conversation there (survives /clear)" 0 "--continue" log personal-repo-1 args
check "  same worktree" 0 "$WT/repo-1" log personal-repo-1 pwd
s stop personal-repo >/dev/null
touch "$HOME/.claude/projects/p/$(id personal-repo).jsonl"
check "an in-place session resumes its own id" 0 "--resume
$(id personal-repo)" bash -c "bash '$sc' start personal-repo >/dev/null; cat '$HOME/log/personal-repo.args'"
s stop personal-repo-2 >/dev/null && git -C "$R" worktree remove --force "$WT/repo-2"
check "a worktree removed by hand comes back" 0 "ready personal-repo-2" s start personal-repo-2
s stop personal-repo-2 >/dev/null && git -C "$R" worktree remove --force "$WT/repo-2" &&
	git -C "$R" branch -qm worktree-repo-2 renamed
check "  but not with its branch gone" 1 "branch worktree-repo-2 is gone" s start personal-repo-2
git -C "$R" branch -qm renamed worktree-repo-2 && s start personal-repo-2 >/dev/null
check "start <unknown>" 1 "no definition" s start personal-nope
s stop personal-fix-1 >/dev/null && git -C "$WT/fix-1" rm -rq sub && git -C "$WT/fix-1" commit -qm "no sub"
touch "$HOME/.claude/projects/p/$(id personal-fix-1).jsonl" # its conversation is in the old directory
check "a restart whose subdirectory is gone starts at the top" 0 "ready personal-fix-1" s start personal-fix-1
check "  " 0 "$WT/fix-1" log personal-fix-1 pwd
check "  in a new conversation (--continue would find none there)" 0 "--session-id" log personal-fix-1 args
check "a definition value ending in =" 0 "ready personal-eq" s start personal "$tmp/plain" --name eq --allow 'Bash(x=)'
s stop personal-eq >/dev/null && s start personal-eq >/dev/null
check "  survives a restart" 0 "Bash(x=)" log personal-eq args
touch "$HOME/.local/state/claude-sessions/personal-x~"
check "a stray file in the state directory" 0 "personal-eq" s ls

echo "-- clean"
for n in repo repo-1 repo-2 fix-1 remote-only topic pr fresh nd nt; do s stop "personal-$n" >/dev/null; done
check "a running session is not cleaned" 1 "running; stop it first" s clean personal2-plain
echo x >"$WT/fix-1/new.txt"
echo w >"$WT/repo-2/w" && git -C "$WT/repo-2" add w && git -C "$WT/repo-2" commit -qm wip
echo y >"$WT/topic/build.log"
out=$(s clean 2>&1)
check "bulk: finished work goes, with its branch" 0 "removed personal-repo-1 and branch worktree-repo-1" echo "$out"
check "  a remote branch with nothing new is finished" 0 "removed personal-remote-only and branch remote-only" echo "$out"
check "  also one ahead of the base" 0 "removed personal-pr and branch pr" echo "$out"
check "  build output (ignored files) goes too, not the user's branch" 0 "removed personal-topic (branch topic kept)" echo "$out"
check "  kept: uncommitted" 0 "kept personal-fix-1: uncommitted changes" echo "$out"
check "  kept: unmerged" 0 "kept personal-repo-2: unmerged work" echo "$out"
check "  in-place sessions stay" 0 "personal-repo stopped" s ls
check "clean <name> with unmerged work keeps the branch" 0 "removed personal-repo-2 (branch worktree-repo-2 kept)" s clean personal-repo-2
check "  its commit is safe" 0 "wip" git -C "$R" log -1 --format=%s worktree-repo-2
check "  and a new session never picks it up by name" 1 "worktree-repo-2 exists" s start personal "$R" --name repo-2
git -C "$tmp/seed" push -q origin main:gone
check "a branch whose remote branch is deleted" 0 "ready personal-gone" s start personal "$R" --branch gone
echo g >"$WT/gone/g" && git -C "$WT/gone" add g && git -C "$WT/gone" commit -qm g && s stop personal-gone >/dev/null
git -C "$tmp/seed" push -q origin :gone && git -C "$R" fetch -q --prune
out=$(s clean 2>&1)
check "  is kept (its base is unknown)" 0 "kept personal-gone: unmerged work" echo "$out"
check "  quietly" 0 "" hasnt "$out" fatal
check "uncommitted work is never removed" 0 "kept personal-fix-1: uncommitted" s clean personal-fix-1
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
check "unpushed submodule commits are never removed" 0 "ready personal-smc" s start personal "$R" --name smc
git -C "$WT/smc/sm" checkout -q -b local && git -C "$WT/smc/sm" commit -q --allow-empty -m local &&
	git -C "$WT/smc/sm" checkout -q --detach HEAD~1 &&
	s stop personal-smc >/dev/null
check "  kept" 0 "kept personal-smc: unpushed submodule commits" s clean personal-smc
check "submodule changes hidden by ignore=dirty are never removed" 0 "ready personal-smd" s start personal "$R" --name smd
git -C "$WT/smd" config submodule.sm.ignore dirty && echo n >"$WT/smd/sm/notes.txt" && s stop personal-smd >/dev/null
check "  kept" 0 "kept personal-smd: uncommitted changes" s clean personal-smd
check "a failing git check keeps the worktree" 0 "ready personal-ro" s start personal "$R" --name ro
echo n >"$WT/ro/notes.txt" && s stop personal-ro >/dev/null && chmod 000 "$(git -C "$WT/ro" rev-parse --git-dir)/index"
check "  " 0 "kept personal-ro: git status failed" s clean personal-ro
chmod 644 "$(git -C "$WT/ro" rev-parse --git-dir)/index"
git -C "$tmp/seed" tag v1 && git -C "$tmp/seed" push -q origin v1 && git -C "$R" fetch -q --tags
check "a branch named like a tag" 0 "ready personal-v1" s start personal "$R" --branch v1
echo v >"$WT/v1/v" && git -C "$WT/v1" add v && git -C "$WT/v1" commit -qm v && s stop personal-v1 >/dev/null
check "  keeps its unmerged commits" 0 "removed personal-v1 (branch v1 kept)" s clean personal-v1
check "a worktree with submodules is removed" 0 "ready personal-sm" s start personal "$R" --name sm
s stop personal-sm >/dev/null
check "  with its branch" 0 "removed personal-sm and branch worktree-sm" s clean personal-sm
check "clean <name> on an in-place session forgets it" 0 "forgot personal-repo" s clean personal-repo

echo "-- stop"
tmux -L "$SESSIONS_TMUX_SOCKET" new-session -d -s mine sleep 600
check "ls hides others" 0 "" ls_lacks '^mine '
check "stop refuses others" 2 "not a session name" s stop mine
check "stop missing" 1 "no session" s stop personal-nope
tmux -L "$SESSIONS_TMUX_SOCKET" new-session -d -s personal-cwlike sleep 600
check "stop a session without a definition (cw), promising no restart" 0 "" test "$(s stop personal-cwlike)" = "stopped personal-cwlike"

if [[ $fails == 0 ]]; then echo "all passed"; else echo "$fails failed"; exit 1; fi
