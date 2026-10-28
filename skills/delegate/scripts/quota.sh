#!/usr/bin/env bash
# What each target can run now.
# Usage: quota.sh <tool>:<account>...
# First asks the CLI for its own usage numbers (agy, claude subscriptions, codex ChatGPT accounts): no model call,
# one line per limit as `<target> usage <limit>`. Targets with no such source (claude:work, codex:work) get a minimal
# probe prompt per model instead: `<target> <model> ok` or `<target> <model> FAIL <reset or error>`. A usage figure
# says how much quota is left, not whether a model is allowed; a probe also proves that. MODELS="a b" forces probes
# of those models. Probes spend a little quota (about 400 tokens on claude; codex cannot be slimmed, ~12k cold, less once cached).
set -Euo pipefail

(($#)) || { echo "usage: quota.sh <tool>:<account>..." >&2; exit 2; }
delegate=$(dirname "${BASH_SOURCE[0]}")/delegate.sh
probe=$(mktemp "${TMPDIR:-/tmp}/delegate-probe-XXXXXX")
echo "Reply with exactly: ok" >"$probe"

check() {
  local target=$1 usage rc models m r why l
  if [[ -z ${MODELS:-} ]]; then
    usage=$(DELEGATE_USAGE=1 DELEGATE_TIMEOUT=60 bash "$delegate" "$target" "$probe" 2>&1) rc=$?
    if ((rc == 0)) && [[ -n $usage ]]; then while IFS= read -r l; do echo "$target usage $l"; done <<<"$usage"; return; fi
    if ((rc == 2)); then echo "$target ${usage:-unsupported}"; return; fi  # not set up on this machine
  fi
  models=${MODELS:-$(DELEGATE_LIST=1 bash "$delegate" "$target" "$probe")}
  for m in $models; do
    r=$(DELEGATE_PROBE=1 DELEGATE_MODEL=$m DELEGATE_TIMEOUT=120 bash "$delegate" "$target" "$probe" 2>&1)
    case $r in
      FAIL*) why=$(grep -oiE 'resets? (in|at)? ?[0-9][^".,]*' <<<"$r" | head -1)
             [[ -n $why ]] || why=$(grep -m1 -E 'ERROR|rror' <<<"$r" | cut -c1-120)
             echo "$target $m FAIL ${why:-${r#FAIL }}" ;;
      unsupported*) echo "$target $r"; break ;;
      *) echo "$target $m ok"; rm -f "${r% *}" ;;
    esac
  done
}

# Targets run concurrently; output stays in argument order.
dir=$(mktemp -d "${TMPDIR:-/tmp}/delegate-quota-XXXXXX")
trap 'rm -rf "$probe" "$dir"' EXIT
i=0; for target; do i=$((i + 1)); check "$target" >"$dir/$i" 2>&1 & done
wait
for ((n = 1; n <= i; n++)); do cat "$dir/$n"; done
