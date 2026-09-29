#!/usr/bin/env bash
# STUB for Phase 0 probes — replaced by WP A1.
set -uo pipefail
input=$(cat)
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null)
if [ -n "${GERRIT_STACK_TRACE:-}" ]; then printf '%s\t%s\t%s\n' "git-post" "$(printf '%s' "$input" | jq -r '.hook_event_name // "?"')" "$cmd" >> "$GERRIT_STACK_TRACE"; fi
exit 0
