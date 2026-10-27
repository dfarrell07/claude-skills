#!/usr/bin/env bash
# Run one task on several agents. Results land in one fresh run directory.
# Usage: multi.sh parallel|chain <prompt-file> <tool:account>...
#   parallel: every agent gets the prompt independently -> <dir>/1.md, 2.md, ...
#   chain:    agent 1 gets the prompt; agent k gets the task plus result k-1 and
#             verifies/improves it. Stops at the first failure.
# Prints one delegate.sh line per step (path and size, or FAIL). Repeat a target
# to run it several times. Targets must be all work or all personal/personal2. Env vars are passed through to delegate.sh.
set -Eeuo pipefail

mode=${1:?usage: multi.sh parallel|chain <prompt-file> <tool:account>...}
prompt=$(realpath "${2:?prompt file}")
shift 2
(($#)) || { echo "need at least one target" >&2; exit 2; }
# A run is one data group: all work, or all personal/personal2. Never both.
work=0 other=0
for t; do [[ ${t#*:} == work ]] && work=1 || other=1; done
((work + other < 2)) || { echo "refusing to mix work and personal targets in one run" >&2; exit 2; }
delegate=$(dirname "${BASH_SOURCE[0]}")/delegate.sh
dir=$(mktemp -d "${TMPDIR:-/tmp}/delegate-run-XXXXXX")

case $mode in
  parallel)
    pids=() rc=0
    i=0; for t; do
      i=$((i + 1)); bash "$delegate" "$t" "$prompt" "$dir/$i.md" & pids+=($!)
    done
    for p in "${pids[@]}"; do wait "$p" || rc=1; done
    exit $rc ;;
  chain)
    step=$prompt i=0
    for t; do
      i=$((i + 1))
      if ((i > 1)); then
        step=$dir/step$i.prompt.md
        cat >"$step" <<TEMPLATE
Task (read this file first): $prompt
Previous agent's result: $dir/$((i - 1)).md

The previous result is unverified and may contain errors. Check its claims against
the actual sources, fix mistakes, fill gaps, and output the complete improved
result in the same format as the task requires (including any first-line VERDICT).
TEMPLATE
      fi
      bash "$delegate" "$t" "$step" "$dir/$i.md" || { echo "chain stopped at step $i" >&2; exit 1; }
    done ;;
  *) echo "mode must be parallel or chain" >&2; exit 2 ;;
esac
