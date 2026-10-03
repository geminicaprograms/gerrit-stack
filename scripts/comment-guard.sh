#!/usr/bin/env bash
# scripts/comment-guard.sh — PreToolUse hook for gerrit-stack: optional team
# convention "review comments use Conventional Comments".
#
# Wired for the Gerrit MCP tools post_review_comment / post_draft_comment
# (matcher mcp__plugin_gerrit_gerrit__(post_review_comment|post_draft_comment))
# and called by git-guard.sh for Bash commands that run `gerrit-rest.py review`.
#
# Silent (exit 0, no output) unless the session's cwd is a Gerrit repo whose
# setting comment-style (git config gerrit-stack.comment-style, else the
# committed .gerrit-stack file) is `conventional`. Then a NEW top-level comment
# must start with a Conventional Comments label:
#   deny  → message on stderr + exit 2
#   allow → exit 0 silent
# Replies are exempt (MCP: tool_input.in_reply_to; REST: --in-reply-to), and so
# is the cover message of a review that carries inline --comment values.
# Fail-open: no jq, no cwd, unparsable input, unexpected error → exit 0 silent.
set -uo pipefail
trap 'exit 0' ERR

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cwd" ] || exit 0
[ -d "$cwd" ] || exit 0
cd "$cwd" || exit 0
tool=$(printf '%s' "$input" | jq -r '.tool_name // empty' 2>/dev/null) || exit 0
[ -n "$tool" ] || exit 0

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

gs_detect || exit 0
[ "$(gs_config comment-style none)" = conventional ] || exit 0

LABELS='praise|nitpick|suggestion|issue|todo|question|thought|chore|note'
LABEL_RE="^[[:space:]]*($LABELS)( \\([^)]+\\))?:"

bad=''   # newline list of offending comments (shortened)

# check_message <where> <message> — record the comment when it has no label
check_message() {
  local where="$1" msg="$2" first
  if [[ $msg =~ $LABEL_RE ]]; then return 0; fi
  # text the shell would still expand ($(…), $VAR, `…`) cannot be judged here
  case "$msg" in '$'*|'`'*) return 0 ;; esac
  first=${msg%%$'\n'*}
  [ "${#first}" -le 60 ] || first="${first:0:60}…"
  bad="${bad}  ${where}: \"${first}\""$'\n'
  return 0
}

# ---------------------------------------------------------------- MCP tools

guard_mcp() {
  local reply msg where
  reply=$(printf '%s' "$input" | jq -r '
    .tool_input // {} | (.in_reply_to // .inReplyTo // .reply_to // "") | tostring' 2>/dev/null) || reply=''
  case "$reply" in ''|null) ;; *) return 0 ;; esac
  msg=$(printf '%s' "$input" | jq -r '.tool_input.message // empty' 2>/dev/null) || msg=''
  [ -n "$msg" ] || return 0
  where=$(printf '%s' "$input" | jq -r '
    .tool_input | "\(.file_path // "?"):\(.line_number // 0)"' 2>/dev/null) || where='?'
  check_message "$where" "$msg"
  return 0
}

# ---------------------------------------------------------------- gerrit-rest.py review

# check_spec <FILE:LINE:MSG> — judge the MSG part; other shapes are let through
check_spec() {
  local spec="$1" rest
  rest=${spec#*:}
  if [ "$rest" != "$spec" ] && [ "${rest#*:}" != "$rest" ]; then
    check_message "${spec%%:*}:${rest%%:*}" "${rest#*:}"
  fi
  return 0
}

# guard_rest <command> — inspects every `gerrit-rest.py … review …` call in a
# Bash command.
guard_rest() {
  local cmd="$1" k=0 u in_call=0 is_review=0 reply=0 cover='' ncomments=0 pending='' seg_bad=''
  local saved
  _gs_split_words "$cmd"
  # a trailing separator closes the last call
  _gs_words[_gs_nwords]=';'
  _gs_nwords=$((_gs_nwords+1))
  saved=$bad
  while [ "$k" -lt "$_gs_nwords" ]; do
    _gs_unquote "${_gs_words[k]}"; u=$_gs_uq
    k=$((k+1))
    if [ "$in_call" = 0 ]; then
      case "$u" in
        *gerrit-rest.py) in_call=1; is_review=0; reply=0; cover=''; ncomments=0; pending=''; bad='' ;;
      esac
      continue
    fi
    if [ -n "$pending" ]; then
      case "$pending" in
        comment)
          ncomments=$((ncomments+1))
          check_spec "$u" ;;
        message) cover=$u ;;
        reply) reply=1 ;;
      esac
      pending=''
      continue
    fi
    case "$u" in
      ';'|'&&'|'||'|'|'|'&'|*';')
        if [ "$is_review" = 1 ] && [ "$reply" = 0 ]; then
          if [ "$ncomments" -eq 0 ] && [ -n "$cover" ]; then
            check_message "review message" "$cover"
          fi
          seg_bad="$seg_bad$bad"
        fi
        in_call=0; bad='' ;;
      review) is_review=1 ;;
      --comment) pending=comment ;;
      --comment=*)
        ncomments=$((ncomments+1))
        check_spec "${u#--comment=}" ;;
      -m|--message) pending=message ;;
      --message=*) cover=${u#--message=} ;;
      --in-reply-to) pending=reply ;;
      --in-reply-to=*) reply=1 ;;
    esac
  done
  bad="$saved$seg_bad"
  return 0
}

# ---------------------------------------------------------------- dispatch

case "$tool" in
  *post_review_comment|*post_draft_comment)
    short=${tool##*__}
    guard_mcp ;;
  Bash)
    short=gerrit-rest-review
    cmd=$(printf '%s' "$input" | jq -r '.tool_input.command // empty' 2>/dev/null) || exit 0
    case "$cmd" in
      *gerrit-rest.py*review*) guard_rest "$cmd" ;;
      *) exit 0 ;;
    esac ;;
  *) exit 0 ;;
esac

if [ -n "$bad" ]; then
  gs_trace comment-guard "$short" deny
  {
    printf 'gerrit-stack: this repository uses Conventional Comments (gerrit-stack.comment-style=conventional). A new review comment must start with a label; not labelled:\n'
    printf '%s' "$bad"
    printf 'Labels: %s — optional decoration in parentheses, then a colon. Example: "issue (blocking): prefix can be null here, which throws on the first request." Replies in a thread stay free-form (pass in_reply_to / --in-reply-to).\n' "$(printf '%s' "$LABELS" | tr '|' ' ')"
  } >&2
  exit 2
fi
gs_trace comment-guard "$short" allow
exit 0
