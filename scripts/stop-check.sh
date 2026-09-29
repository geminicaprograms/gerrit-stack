#!/usr/bin/env bash
# scripts/stop-check.sh — Stop hook for gerrit-stack.
#
# Blocks the stop (stdout `{"decision":"block","reason":…}`) only when this
# session committed in the Gerrit repo (session marker exists) and a non-fixup
# commit of the local chain lacks a Change-Id. Everything else exits 0 silently
# (stop_hook_active, no marker, non-Gerrit repo, unexpected errors).
set -uo pipefail
trap 'exit 0' ERR

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0
active=$(printf '%s' "$input" | jq -r '.stop_hook_active // false' 2>/dev/null) || exit 0
[ "$active" != true ] || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cwd" ] || exit 0
[ -d "$cwd" ] || exit 0
cd "$cwd" || exit 0
session=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null) || exit 0
[ -n "$session" ] || exit 0

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

gs_detect || exit 0
if ! gs_session_marked "$session"; then
  gs_trace stop-check stop silent
  exit 0
fi

missing=''
# shellcheck disable=SC2119  # base defaults to $GS_BASE
for sha in $(gs_chain_commits); do
  if gs_is_fixup "$sha"; then continue; fi
  ids=$(gs_change_ids_of "$sha")
  if [ -z "$ids" ]; then
    missing="$missing ${sha:0:7} $(gs_subject_of "$sha");"
  fi
done

if [ -z "$missing" ]; then
  gs_trace stop-check stop silent
  exit 0
fi

reason="gerrit-stack: commit(s) in the local chain (${GS_BASE#refs/remotes/}..HEAD) have no Change-Id and cannot be pushed to Gerrit:${missing} Repair (the commit-msg hook adds the id on each amend): git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' ${GS_BASE} — then verify with: git log --format='%h %s%n%(trailers:key=Change-Id)' ${GS_BASE}..HEAD"
gs_trace stop-check stop block
jq -cn --arg r "$reason" '{decision:"block",reason:$r}'
exit 0
