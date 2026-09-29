#!/usr/bin/env bash
# scripts/git-post.sh — PostToolUse hook (matcher Bash, if "Bash(git *)") for
# gerrit-stack. Fires only after a successful Bash call. Per git invocation in
# a Gerrit repo:
#   commit       mark the session, check exactly one Change-Id on HEAD, run
#                diff-budget.sh HEAD when present (exit 3 → retro-split hint),
#                check required footers (gerrit-stack.footers), refresh the
#                Change-Id snapshot
#   rebase / cherry-pick / reset
#                compare the chain's Change-Id set with the snapshot (lost:/new:)
#   push         to refs/for: report the /c/<proj>/+/<n> change numbers found
#                in the tool response, or explain "no new changes"
# Feedback = message on stderr + exit 2 (never blocks); otherwise exit 0 silent.
# Fail-open: any unexpected condition → exit 0 silent.
set -uo pipefail
trap 'exit 0' ERR

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cwd" ] || exit 0
[ -d "$cwd" ] || exit 0
cd "$cwd" || exit 0
cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
[ -n "$cmd" ] || exit 0
session=$(printf '%s' "$input" | jq -r '.session_id // empty' 2>/dev/null) || session=''
response=$(printf '%s' "$input" | jq -r '
  .tool_response // "" |
  if type == "string" then . else ((.stdout // "") + "\n" + (.stderr // "")) end' 2>/dev/null) || response=''

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

script_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" 2>/dev/null && pwd -P) || script_dir=''
budget_script="$script_dir/diff-budget.sh"

feedback_msg=''
inv_decision=silent

feedback() {
  inv_decision=feedback
  feedback_msg="${feedback_msg}gerrit-stack: $1"$'\n'
}

# ---------------------------------------------------------------- commit

post_commit() {
  local args="$1" ids n out rc footers f body missing=''

  [ -n "$session" ] && gs_session_mark "$session"

  if gs_git_args_have "$args" --dry-run; then return 0; fi

  ids=$(gs_change_ids_of HEAD)
  n=0
  [ -z "$ids" ] || n=$(printf '%s\n' "$ids" | grep -c '' || true)
  if [ "$n" -eq 0 ]; then
    feedback "HEAD ($(git -C "$GS_TOPLEVEL" rev-parse --short=7 HEAD 2>/dev/null)) has no Change-Id trailer — the commit-msg hook did not run (missing hook or --no-verify). Gerrit cannot track it. Repair now: git commit --amend --no-edit (the hook adds the id); if the hook is missing install it first: bash \"${CLAUDE_PLUGIN_ROOT:-$script_dir/..}/scripts/install-commit-msg-hook.sh\"."
  elif [ "$n" -gt 1 ]; then
    feedback "HEAD carries $n Change-Id trailers ($(printf '%s' "$ids" | tr '\n' ' ')); Gerrit rejects commits with more than one. Keep exactly one line: git commit --amend (edit the message)."
  fi

  if [ -n "$script_dir" ] && [ -x "$budget_script" ]; then
    out=$(bash "$budget_script" HEAD 2>/dev/null)
    rc=$?
    if [ "$rc" -eq 3 ]; then
      feedback "this commit exceeds the hard diff budget ($out). Consider a retro-split into smaller changes: invoke the stack-planner skill on the diff of HEAD (git show --stat HEAD) before pushing."
    fi
  fi

  footers=$(gs_config footers "")
  if [ -n "$footers" ]; then
    body=$(git -C "$GS_TOPLEVEL" log -1 --format=%B HEAD 2>/dev/null) || body=''
    for f in $(printf '%s\n' "$footers" | tr ',' ' '); do
      f=$(printf '%s' "$f" | tr -d '[:space:]')
      [ -n "$f" ] || continue
      if ! printf '%s\n' "$body" | grep -qi "^${f}:" 2>/dev/null; then
        missing="${missing:+$missing, }$f"
      fi
    done
    if [ -n "$missing" ]; then
      feedback "required footer(s) missing from the HEAD commit message: $missing (git config gerrit-stack.footers=$footers). Add them as trailers in the last paragraph, e.g. git commit --amend (keep the Change-Id: line)."
    fi
  fi

  gs_snapshot_write
  return 0
}

# ---------------------------------------------------------------- rebase & co

post_rewrite() {
  local verb="$1" diff gitdir
  gitdir=$(git -C "$GS_TOPLEVEL" rev-parse --git-dir 2>/dev/null) || gitdir=''
  case "$gitdir" in /*) ;; *) gitdir="$GS_TOPLEVEL/$gitdir" ;; esac
  # mid-rebase / mid-cherry-pick: the chain is not final yet
  if [ -d "$gitdir/rebase-merge" ] || [ -d "$gitdir/rebase-apply" ] \
    || [ -f "$gitdir/CHERRY_PICK_HEAD" ]; then
    return 0
  fi
  diff=$(gs_snapshot_diff) && return 0
  feedback "after '$verb' the local chain's Change-Id set differs from the snapshot taken at the last commit:
$diff
'lost:' ids no longer exist in ${GS_BASE#refs/remotes/}..HEAD (dropped or rewritten commits — Gerrit will see NEW changes instead of new patchsets); 'new:' ids appeared. If this is unintended, restore from the reflog (git reflog) or re-apply the original Change-Id lines with git commit --amend. Snapshot refreshed to the current chain."
  gs_snapshot_write
  return 0
}

# ---------------------------------------------------------------- push

# grouping_disp — "none" | "hashtag <x>" | "topic <x>" from the command's
# %options (refspec or -o) else config
grouping_disp() {
  local args="$1" w o kind='' name=''
  for w in $args; do
    case "$w" in
      *refs/for/*%*) for o in $(printf '%s\n' "${w#*%}" | tr ',' '\n'); do
          case "$o" in
            topic=*) kind=topic; name=${o#topic=} ;;
            t=*|hashtag=*) [ "$kind" = topic ] || { kind=hashtag; name=${o#*=}; } ;;
          esac
        done ;;
    esac
  done
  if [ -z "$kind" ]; then
    kind=$(gs_config grouping none)
    name=$(gs_config group-name "")
    case "$kind" in hashtag|topic) ;; *) kind=none; name='' ;; esac
  fi
  if [ "$kind" = none ]; then printf 'none\n'; else printf '%s %s\n' "$kind" "$name"; fi
}

post_push() {
  local args="$1" numbers tip grp
  # only pushes aimed at refs/for (explicit refspec or remote.<r>.push default)
  case "$args" in
    *refs/for/*) ;;
    *)
      case "$(git -C "$GS_TOPLEVEL" config --get-all "remote.${GS_REMOTE}.push" 2>/dev/null)" in
        *refs/for/*) ;;
        *) return 0 ;;
      esac
      case "$args" in
        *:*|*refs/heads/*) return 0 ;;
      esac ;;
  esac
  [ -n "$response" ] || return 0
  grp=$(grouping_disp "$args")
  numbers=$(printf '%s\n' "$response" | grep -oE '/c/[^ ]+/\+/[0-9]+' | sed 's#.*/+/##' | awk '!seen[$0]++' | tr '\n' ',')
  numbers=${numbers%,}
  if [ -n "$numbers" ]; then
    tip=${numbers##*,}
    feedback "pushed changes $numbers (grouping: $grp); verify the relation chain with get_related_changes($tip) (Gerrit MCP) or: python3 \"${CLAUDE_PLUGIN_ROOT:-$script_dir/..}/scripts/gerrit-rest.py\" related $tip"
    gs_snapshot_write
  elif printf '%s' "$response" | grep -qi 'no new changes'; then
    feedback "Gerrit answered 'no new changes': every commit in the chain already exists as a patchset on the server (same tree, message and Change-Id). Nothing was uploaded — that is expected after a re-push without edits. To create a new patchset, amend a commit first (git commit --amend --no-edit after a change, or rebase the chain)."
  fi
  return 0
}

# ---------------------------------------------------------------- dispatch

lines=$(gs_parse_git_cmd "$cmd")
[ -n "$lines" ] || exit 0

while IFS=$'\t' read -r dir verb args; do
  [ -n "$verb" ] || continue
  inv_decision=silent
  if ! gs_detect "$dir"; then
    continue
  fi
  case "$verb" in
    commit) post_commit "$args" ;;
    rebase|cherry-pick|reset) post_rewrite "$verb" ;;
    push) post_push "$args" ;;
  esac
  gs_trace git-post "$verb" "$inv_decision"
done <<EOF
$lines
EOF

if [ -n "$feedback_msg" ]; then
  printf '%s' "$feedback_msg" >&2
  exit 2
fi
exit 0
