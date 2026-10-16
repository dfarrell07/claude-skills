#!/bin/bash
set -euo pipefail
if ! command -v python3 >/dev/null 2>&1; then
    printf '%s\n' 'Work-summary needs Python 3.10+ for validated shell rendering. Read the saved evidence with available tools and write a source-linked work list; do not claim the script validated it.' >&2
    exit 127
fi
if ! python3 -c 'import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)'; then
    printf '%s\n' 'Work-summary needs a working Python 3.10+ interpreter. Summarize available evidence directly and state that shell validation was unavailable.' >&2
    exit 1
fi
case "${BASH_SOURCE[0]}" in
    */*) WORK_SUMMARY_SCRIPT_DIR=${BASH_SOURCE[0]%/*} ;;
    *) WORK_SUMMARY_SCRIPT_DIR=. ;;
esac
exec python3 "$WORK_SUMMARY_SCRIPT_DIR/work_summary.py" render "$@"
