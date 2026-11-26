#!/usr/bin/env bash
# delegate.sh and budget.py must name CLAUDE_CONFIG_DIR for every Claude account: on macOS an unset one is a
# separate Keychain login from the explicit ~/.claude that cper uses. A stub `claude` records what it was given.
set -Eeuo pipefail
root=$(cd "$(dirname "${BASH_SOURCE[0]}")/../../.." && pwd)
tmp=$(mktemp -d)
trap 'rm -rf "$tmp"' EXIT
mkdir -p "$tmp/bin" "$tmp/home"
cat >"$tmp/bin/claude" <<'STUB'
#!/usr/bin/env bash
echo "${CLAUDE_CONFIG_DIR-UNSET}" >>"$STUB_LOG"
echo "Current week (all models): 8% used · resets Nov 24 at 4am (America/New_York)"
STUB
chmod +x "$tmp/bin/claude"
export STUB_LOG=$tmp/log PATH=$tmp/bin:$PATH HOME=$tmp/home CLAUDE_CONFIG_DIR=/parent/session/dir
echo hi >"$tmp/prompt"
fail=0
expect() {  # <what> <want>; each check reads only the call just made (the log is cleared before it)
  local got; got=$(tail -n1 "$STUB_LOG" 2>/dev/null); got=${got:-NOT CALLED}
  if [[ $got == "$2" ]]; then echo "ok   $1"; else echo "FAIL $1: CLAUDE_CONFIG_DIR=$got, want $2"; fail=1; fi
}
for acct in personal personal2; do
  want=$HOME/.claude; [[ $acct == personal2 ]] && want=$HOME/.claude-per2
  : >"$STUB_LOG"
  DELEGATE_ALLOW_CLAUDE=1 bash "$root/skills/delegate/scripts/delegate.sh" "claude:$acct" "$tmp/prompt" "$tmp/out-$acct" >/dev/null || true
  expect "delegate claude:$acct run" "$want"
  : >"$STUB_LOG"
  python3 -I "$root/skills/delegate/scripts/budget.py" "claude:$acct" >/dev/null
  expect "budget claude:$acct" "$want"
done
# Unknown tools fail fast with `unsupported` (exit 2); a bad multi.sh mode leaves no run directory.
rc=0; err=$(bash "$root/skills/delegate/scripts/delegate.sh" foo:work "$tmp/prompt" "$tmp/out-foo" 2>&1) || rc=$?
if [[ $rc == 2 && $err == "unsupported: foo:work"* ]]; then echo "ok   unknown tool unsupported"; else echo "FAIL unknown tool: rc=$rc $err"; fail=1; fi
mkdir "$tmp/t"; rc=0; TMPDIR=$tmp/t bash "$root/skills/delegate/scripts/multi.sh" bogus "$tmp/prompt" codex:work 2>/dev/null || rc=$?
if [[ $rc == 2 && -z $(ls -A "$tmp/t") ]]; then echo "ok   bad mode leaves no run dir"; else echo "FAIL bad mode: rc=$rc"; fail=1; fi
# A failing tool gives exactly one FAIL line (both fallback models fail), leaves no out-file and terminates.
mkdir -p "$tmp/home/.codex"
printf '#!/bin/sh\necho "line one\nYou have hit your limit" >&2; exit 1\n' >"$tmp/bin/codex"; chmod +x "$tmp/bin/codex"
rc=0; err=$(timeout 30 bash "$root/skills/delegate/scripts/delegate.sh" codex:work "$tmp/prompt" "$tmp/out-codex" 2>&1) || rc=$?
if [[ $rc == 1 && $(grep -c . <<<"$err") == 1 && $err == "FAIL codex:work: "*"hit your limit"* && ! -e $tmp/out-codex ]]; then
  echo "ok   failed run prints one FAIL line"; else echo "FAIL failed run: rc=$rc $err"; fail=1; fi
exit $fail
