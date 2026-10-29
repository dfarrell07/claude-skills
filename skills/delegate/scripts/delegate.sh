#!/usr/bin/env bash
# Run one prompt on one agent CLI under one account; write the final answer to a file.
# Usage: delegate.sh <tool>:<account> <prompt-file> [out-file]
#   tool:    agy | codex (claude is refused unless DELEGATE_ALLOW_CLAUDE=1)
#   account: work | personal | personal2
# Prints only the output path and size, so callers never pay for the answer
# until they choose to read it. On failure prints FAIL and leaves no out-file.
# Optional env:
#   DELEGATE_MODEL, DELEGATE_EFFORT (low|medium|high|xhigh|max). Unset: per-target defaults
#     below, trying fallback models in order. Setting DELEGATE_MODEL uses only that model.
#   DELEGATE_ALLOW_CLAUDE=1   claude targets are refused unless set: claude orchestrates, delegate is for weaker models
#   DELEGATE_WRITE=1   allow edits (default read-only; agy cannot enforce this)
#   DELEGATE_USAGE=1   print the account's usage/quota from the CLI itself (no model call), then exit; exit 3 if
#                      the account has no such source (quota.sh then probes instead)
#   DELEGATE_PROBE=1   claude only: no tools, settings or default system prompt, for cheap quota probes
#   DELEGATE_TIMEOUT   seconds before the run is killed (default 1800; agy 3000, since high effort takes ~17 min)
set -Eeuo pipefail
umask 077

target=${1:?target: <agy|codex|claude>:<work|personal|personal2>}
prompt_file=${2:?prompt file}
[[ $target == *:* ]] || { echo "target must be <tool>:<account>" >&2; exit 2; }
[[ -r $prompt_file ]] || { echo "cannot read $prompt_file" >&2; exit 2; }
tool=${target%%:*} account=${target#*:}
[[ $tool != claude || ${DELEGATE_ALLOW_CLAUDE:-} == 1 ]] || {
  echo "refusing claude target $target: claude runs delegate, it is not a sub-agent. Set DELEGATE_ALLOW_CLAUDE=1 only if the user named it" >&2; exit 2; }
out=${3:-$(mktemp "${TMPDIR:-/tmp}/delegate-${tool}-${account}-XXXXXX.md")}
err="$out.err"
mkdir -p "$(dirname "$out")"
[[ ! -L $out && ! -L $err ]] || { echo "refusing symlink out-file" >&2; exit 2; }
rm -f "$out" "$err"

prompt=$(<"$prompt_file")
[[ $prompt == *[![:space:]]* ]] || { echo "empty prompt" >&2; exit 2; }
((${#prompt} < 100000)) || { echo "prompt too large; reference file paths instead" >&2; exit 2; }

model=${DELEGATE_MODEL:-} effort=${DELEGATE_EFFORT:-} write=${DELEGATE_WRITE:-}
[[ -z $effort || $effort =~ ^(low|medium|high|xhigh|max)$ ]] || { echo "bad DELEGATE_EFFORT: $effort" >&2; exit 2; }

# Defaults per target (see references/models.md for what each account can use).
case $tool:$account in
  agy:*)       dmodels="gemini-4-argon gemini-3.8-flash" deffort=medium; dtimeout=3000 ;;  # quotas are per model
  codex:work)  dmodels="gpt-6.1-sol gpt-5.6-luna" deffort=high ;;    # sol needs budget; luna is the fallback
  codex:*)     dmodels="gpt-6-luna"               deffort=medium ;;  # personal quota is scarce
  claude:work) dmodels="haiku"                    deffort=high ;;
  claude:*)    dmodels="claude-sonnet-5-5"        deffort=medium ;;
esac
timeout_s=${DELEGATE_TIMEOUT:-${dtimeout:-1800}}
if [[ -n $model ]]; then models=("$model"); else read -ra models <<<"$dmodels"; effort=${effort:-${deffort:-}}; fi
[[ -z ${DELEGATE_LIST:-} ]] || { echo "${models[*]}"; exit 0; }  # the candidates only (quota.sh)
unsupported() { echo "unsupported: $target ($1)" >&2; exit 2; }
need_dir() { [[ -d $1 ]] || unsupported "$1 missing; log in there first"; }

exec 3>&2 # the trap can fire while the tool's own stderr redirect is still active
trap 'echo "FAIL $target: $(tail -c 300 "$err" 2>/dev/null; head -c 300 "$out" 2>/dev/null)" >&3; rm -f "$out"' ERR

run() { timeout "$timeout_s" "$@"; }

launch() {
local model=$1
rm -f "$out" "$err"
case $tool in
  agy)
    # agy has no telemetry or data-sharing switches.
    opts=(--mode=accept-edits --dangerously-skip-permissions --disable-slash-commands
          --print-timeout="${timeout_s}s")
    [[ -n $model ]] && opts+=(--model "$model")
    [[ -n $effort ]] && opts+=(--effort "$effort")
    case $account in
      personal)  dir=$HOME/.gemini ;;
      # No keyring on a null bus, so credentials live in this dir, not the shared default login.
      personal2) dir=$HOME/.gemini-per2; export DBUS_SESSION_BUS_ADDRESS=unix:path=/dev/null ;;
      *) unsupported "no agy $account login configured" ;;
    esac
    need_dir "$dir"
    if [[ -n ${DELEGATE_USAGE:-} ]]; then run agy --prompt=/usage --gemini_dir="$dir" --mode=plan </dev/null; exit; fi
    run agy --prompt="$prompt" --gemini_dir="$dir" "${opts[@]}" >"$out" 2>"$err" ;;
  codex)
    # No connectors, plugins, browser, memory or analytics (web search stays on).
    opts=(--ephemeral --skip-git-repo-check -c analytics.enabled=false -c feedback.enabled=false)
    for f in apps plugins browser_use computer_use memories; do opts+=(--disable "$f"); done
    [[ -n $write ]] && opts+=(--sandbox=danger-full-access) || opts+=(--sandbox=read-only)
    [[ -n $model ]] && opts+=(-m "$model")
    [[ -n $effort ]] && opts+=(-c "model_reasoning_effort=\"$effort\"")
    case $account in
      work)      dir=$HOME/.codex ;;
      personal)  dir=$HOME/.codex-personal ;;
      personal2) dir=$HOME/.codex-personal2 ;;
      *) unsupported "unknown account" ;;
    esac
    need_dir "$dir"
    if [[ -n ${DELEGATE_USAGE:-} ]]; then CODEX_HOME=$dir run python3 -I "$(dirname "${BASH_SOURCE[0]}")/codex-usage.py"; exit; fi
    CODEX_HOME=$dir run codex exec "${opts[@]}" -o "$out" -- "$prompt" </dev/null >/dev/null 2>"$err" ;;
  claude)
    # No MCP servers/connectors, user skills, memory writes or telemetry (web tools stay on).
    opts=(--no-session-persistence --strict-mcp-config --disable-slash-commands)
    if [[ -n ${DELEGATE_PROBE:-} ]]; then  # ~6.6k tokens down to ~400
      opts+=(--tools "" --system-prompt "Be terse." --setting-sources "")
    elif [[ -n $write ]]; then opts+=(--dangerously-skip-permissions)
    else opts+=(--tools "Read,Grep,Glob,WebFetch,WebSearch" --allowedTools "Read Grep Glob WebFetch WebSearch" --permission-mode dontAsk)
    fi
    [[ -n $model ]] && opts+=(--model "$model")
    [[ -n $effort ]] && opts+=(--effort "$effort")
    # The child must not think it is the parent session or reuse its account.
    unset CLAUDE_CONFIG_DIR "${!CLAUDE_CODE_MESSAGING@}" "${!CLAUDE_CODE_SESSION@}" CLAUDE_CODE_CHILD_SESSION
    export DISABLE_TELEMETRY=1 DISABLE_ERROR_REPORTING=1 CLAUDE_CODE_DISABLE_NONESSENTIAL_TRAFFIC=1 \
           CLAUDE_CODE_DISABLE_AUTO_MEMORY=1 CLAUDE_CODE_DISABLE_FEEDBACK_SURVEY=1
    case $account in
      work)      export CLAUDE_CODE_USE_VERTEX=1 ;;
      personal)  export CLAUDE_CODE_USE_VERTEX=0 ;;
      personal2) export CLAUDE_CODE_USE_VERTEX=0 CLAUDE_CONFIG_DIR=$HOME/.claude-per2 ;;
      *) unsupported "unknown account" ;;
    esac
    if [[ -n ${DELEGATE_USAGE:-} ]]; then
      [[ $account != work ]] || exit 3  # Vertex bills per token; there is no quota to report
      run claude -p /usage --no-session-persistence </dev/null | grep '^Current' || exit 3; exit
    fi
    run claude -p "${opts[@]}" -- "$prompt" </dev/null >"$out" 2>"$err" ;;
  *) unsupported "unknown tool" ;;
esac || return 1
# Some CLIs exit 0 and write API/quota errors to stdout as the "answer".
[[ -s $out ]] && ! head -c 40 "$out" | grep -qE "^(API Error|You've hit your)"
}

# Try each candidate model until one produces an answer; the last failure is reported.
ok=
for m in "${models[@]}"; do
  if launch "$m"; then ok=1; break; fi
done
[[ -n $ok ]] || false
rm -f "$err"
echo "$out $(wc -c <"$out")B"
