#!/usr/bin/env bash
# scripts/git-guard.sh — PreToolUse hook (matcher Bash, if "Bash(git *)") for
# gerrit-stack. Parses every git invocation in the command, detects the Gerrit
# repo each one runs in, and applies the SPEC guard table:
#   deny  → message on stderr + exit 2
#   ask   → stdout JSON permissionDecision "ask" + exit 0
#   else  → exit 0 silent
# Team conventions (see README "Team conventions"): a push to refs/for is denied
# when a chain commit fails the repo's own commitlint config, and Bash calls of
# `gerrit-rest.py review` are handed to comment-guard.sh.
# The strictest decision across invocations wins (deny > ask > silent).
# Fail-open: no jq, no cwd, non-Gerrit repo, unexpected error → exit 0 silent.
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

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

plugin_root=${CLAUDE_PLUGIN_ROOT:-}
if [ -z "$plugin_root" ]; then
  plugin_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P) || plugin_root=''
fi
install_cmd="bash \"${plugin_root}/scripts/install-commit-msg-hook.sh\""

decision=silent      # overall: silent | ask | deny
deny_msg=''
ask_reason=''
inv_decision=silent  # per invocation
inv_dir='.'          # dir of the invocation being judged (relative to cwd)

deny() {
  decision=deny
  inv_decision=deny
  deny_msg="${deny_msg}gerrit-stack: $1"$'\n'
}

ask() {
  [ "$decision" = deny ] || decision=ask
  [ "$inv_decision" = deny ] || inv_decision=ask
  ask_reason="${ask_reason:+$ask_reason
}$1"
}

# ---------------------------------------------------------------- commit

# _gs_message_file_arg <args> — value of -F <file> / -F<file> / --file <file> / --file=<file>
_gs_message_file_arg() {
  local prev='' tok
  for tok in $1; do
    case "$prev" in -F|--file) printf '%s' "$tok"; return 0 ;; esac
    case "$tok" in
      --file=*) printf '%s' "${tok#--file=}"; return 0 ;;
      -F?*) printf '%s' "${tok#-F}"; return 0 ;;
    esac
    prev=$tok
  done
  return 1
}

guard_commit() {
  local args="$1"
  case "$cmd" in
    *Change-Id:*)
      deny "the commit message carries a hand-written Change-Id: trailer. The commit-msg hook adds it; remove the trailer and commit again." ;;
  esac
  if gs_git_args_have "$args" --no-verify || gs_git_args_have "$args" -n; then
    deny "'git commit --no-verify' (-n) skips the commit-msg hook, so the commit gets no Change-Id and Gerrit cannot track it. Commit without --no-verify."
  fi
  if gs_git_args_have "$args" --amend; then
    if gs_git_args_have "$args" -m || gs_git_args_have "$args" --message; then
      deny "'git commit --amend -m' replaces the whole message and drops the existing Change-Id, so Gerrit would open a NEW change instead of a new patchset. Use 'git commit --amend --no-edit', or '--amend -F <file>' where the file keeps the current Change-Id: line."
    elif gs_git_args_have "$args" -F || gs_git_args_have "$args" --file; then
      local head_id msg_file
      head_id=$(gs_change_ids_of HEAD | head -n 1)
      msg_file=$(_gs_message_file_arg "$args")
      case "$msg_file" in ''|/*) ;; *) msg_file="${inv_dir:-.}/$msg_file" ;; esac
      if [ -n "$head_id" ] && [ -n "$msg_file" ] && [ -f "$msg_file" ] \
         && grep -q -F -x "Change-Id: $head_id" "$msg_file"; then
        : # the file keeps HEAD's Change-Id line verbatim: same change, new patchset
      else
        ask "git commit --amend -F: confirm the message file preserves the current Change-Id: line of HEAD (${head_id:-none}); otherwise Gerrit opens a NEW change instead of a new patchset."
      fi
    fi
  fi
  if [ "${GS_HOOK_OK:-0}" != 1 ]; then
    deny "the Gerrit commit-msg hook is not installed in $GS_HOOKS_DIR (no Change-Id would be added). Install it first: $install_cmd — then retry the commit."
  fi
  return 0
}

# ---------------------------------------------------------------- push

# parse_push <args> — sets push_remote push_refspecs push_force push_all
# push_tags push_delete push_opts (newline lists / flags).
parse_push() {
  local -a w
  local k=0 n u
  push_remote='' push_refspecs='' push_force=0 push_all=0 push_tags=0 push_delete=0 push_opts=''
  read -ra w <<< "$1"
  n=${#w[@]}
  while [ "$k" -lt "$n" ]; do
    u=${w[k]}
    k=$((k+1))
    case "$u" in
      --)
        while [ "$k" -lt "$n" ]; do
          if [ -z "$push_remote" ]; then push_remote=${w[k]}; else push_refspecs="$push_refspecs${w[k]}"$'\n'; fi
          k=$((k+1))
        done ;;
      --force|--force-with-lease|--force-with-lease=*|--force-if-includes) push_force=1 ;;
      --all|--branches|--mirror) push_all=1 ;;
      --tags|--follow-tags) push_tags=1 ;;
      --delete) push_delete=1 ;;
      --push-option=*) push_opts="$push_opts${u#--push-option=}"$'\n' ;;
      --push-option|-o)
        if [ "$k" -lt "$n" ]; then push_opts="$push_opts${w[k]}"$'\n'; fi
        k=$((k+1)) ;;
      --repo=*) [ -n "$push_remote" ] || push_remote=${u#--repo=} ;;
      --repo)
        if [ "$k" -lt "$n" ] && [ -z "$push_remote" ]; then push_remote=${w[k]}; fi
        k=$((k+1)) ;;
      --receive-pack|--exec) k=$((k+1)) ;;
      --*) ;;
      -o*) push_opts="$push_opts${u#-o}"$'\n' ;;
      -*)
        u=${u#-}
        case "$u" in
          *[![:alnum:]]*) ;;
          *)
            case "$u" in *f*) push_force=1 ;; esac
            case "$u" in *d*) push_delete=1 ;; esac
            ;;
        esac ;;
      *)
        if [ -z "$push_remote" ]; then push_remote=$u; else push_refspecs="$push_refspecs$u"$'\n'; fi ;;
    esac
  done
  return 0
}

# current_branch — short name or "HEAD" when detached
current_branch() {
  local b
  b=$(git -C "$GS_TOPLEVEL" symbolic-ref -q --short HEAD 2>/dev/null) || b=HEAD
  printf '%s\n' "$b"
}

# grouping_of <opts (newline list)> — sets grp_kind grp_name grp_from_cmd
grouping_of() {
  local o
  grp_kind='' grp_name='' grp_from_cmd=0
  for o in $1; do
    case "$o" in
      topic=*) grp_kind=topic; grp_name=${o#topic=}; grp_from_cmd=1 ;;
      t=*|hashtag=*)
        if [ "$grp_kind" != topic ]; then grp_kind=hashtag; grp_name=${o#*=}; grp_from_cmd=1; fi ;;
    esac
  done
  if [ -z "$grp_kind" ]; then
    grp_kind=$(gs_config grouping none)
    grp_name=$(gs_config group-name "")
    case "$grp_kind" in hashtag|topic) ;; *) grp_kind=none; grp_name='' ;; esac
  fi
  return 0
}

# guard_refs_for <remote> <branch> <src> <opts>
guard_refs_for() {
  local remote="$1" branch="$2" src="$3" opts="$4"
  local base tip sha ids n fixups='' missing='' dups='' list='' subject grp cfg_grouping unlinted=''

  if [ "$push_force" = 1 ]; then
    deny "force-pushing to refs/for/$branch is meaningless: Gerrit creates patchsets from the Change-Id, never rewrites refs/for. Push without --force / -f / --force-with-lease / '+'."
  fi

  if git -C "$GS_TOPLEVEL" rev-parse -q --verify "refs/remotes/$remote/$branch" >/dev/null 2>&1; then
    base="refs/remotes/$remote/$branch"
  else
    base=${GS_BASE:-}
  fi
  [ -n "$src" ] || src=HEAD
  tip=$(git -C "$GS_TOPLEVEL" rev-parse -q --verify "$src^{commit}" 2>/dev/null) || tip=HEAD
  chain=''
  if [ -n "$base" ]; then
    chain=$(git -C "$GS_TOPLEVEL" rev-list --reverse "$base..$tip" -- 2>/dev/null) || chain=''
  fi
  n=0
  for sha in $chain; do
    n=$((n+1))
    subject=$(gs_subject_of "$sha")
    if gs_is_fixup "$sha"; then fixups="$fixups ${sha:0:7} $subject;"; fi
    ids=$(gs_change_ids_of "$sha")
    if [ -z "$ids" ]; then
      missing="$missing ${sha:0:7} $subject;"
    elif [ "$(printf '%s\n' "$ids" | grep -c '')" -gt 1 ]; then
      dups="$dups ${sha:0:7} $subject;"
    fi
    list="${list:+$list; }${sha:0:7} $subject"
  done

  if [ -n "$fixups" ]; then
    deny "the chain to refs/for/$branch still contains fixup!/squash! commits:$fixups Squash them first: git -c sequence.editor=true rebase -i --autosquash $base"
  fi
  if [ -n "$missing" ]; then
    deny "commit(s) in the chain to refs/for/$branch have no Change-Id (Gerrit rejects them):$missing Repair (commit-msg hook adds the id on each amend): git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' $base"
  fi
  if [ -n "$dups" ]; then
    deny "commit(s) carry more than one Change-Id trailer (Gerrit rejects them):$dups Edit each message so exactly one Change-Id: line remains: git -c sequence.editor=true rebase -i $base"
  fi
  [ "$inv_decision" = deny ] && return 0

  # team commit convention: the repo's own commitlint config, offline, fail-open
  if gs_commitlint_active; then
    for sha in $chain; do
      if gs_commitlint_check "$sha"; then continue; fi
      unlinted="$unlinted ${sha:0:7} $(gs_subject_of "$sha") — $(printf '%s\n' "${_gs_lint_out:-}" | head -n 1);"
    done
    if [ -n "$unlinted" ]; then
      deny "commit message(s) in the chain to refs/for/$branch fail this repo's commitlint config:$unlinted Reword each one and keep its existing Change-Id: line: git log -1 --format=%B > <file>, fix the subject in <file>, git commit --amend -F <file> (never '--amend -m'). For a commit below HEAD stop a rebase at it first (gerrit-stack skill, references/chain-editing.md, 'Reword a middle commit'). Check a message offline: git log -1 --format=%B <sha> | commitlint"
      return 0
    fi
  fi

  grouping_of "$opts"
  case "$grp_kind" in
    none) grp="none" ;;
    *) grp="$grp_kind $grp_name" ;;
  esac
  cfg_grouping=$(gs_config grouping none)
  reason="Push $n change(s) to refs/for/$branch [grouping: $grp]"
  [ -z "$list" ] || reason="$reason: $list"
  if [ "$grp_kind" = topic ] && [ "$grp_from_cmd" = 1 ] && [ "$cfg_grouping" != topic ]; then
    reason="$reason — topic will be submitted together if submitWholeTopic is on — intended?"
  fi
  ask "$reason"
  return 0
}

guard_push() {
  local args="$1"
  local remote cur spec src dst dst_ref opts kind branch allow specs
  parse_push "$args"

  remote=$push_remote
  cur=$(current_branch)
  if [ -z "$remote" ]; then
    remote=$(git -C "$GS_TOPLEVEL" config --get "branch.$cur.pushRemote" 2>/dev/null) || remote=''
    [ -n "$remote" ] || remote=$(git -C "$GS_TOPLEVEL" config --get remote.pushDefault 2>/dev/null) || remote=''
    [ -n "$remote" ] || remote=$(git -C "$GS_TOPLEVEL" config --get "branch.$cur.remote" 2>/dev/null) || remote=''
    [ -n "$remote" ] || remote=${GS_REMOTE:-origin}
  fi

  specs=$push_refspecs
  if [ -z "$specs" ] && [ "$push_all" = 0 ] && [ "$push_tags" = 0 ]; then
    specs=$(git -C "$GS_TOPLEVEL" config --get-all "remote.$remote.push" 2>/dev/null) || specs=''
    if [ -z "$specs" ]; then
      # push.default simple/current/upstream: the current branch goes to a
      # same-named (or upstream) branch → direct push
      specs=$cur
    fi
  fi
  if [ "$push_all" = 1 ]; then specs="$specs"$'\n'"refs/heads/*:refs/heads/*"; fi

  allow=$(gs_config allow-direct-push false)
  for spec in $specs; do
    case "$spec" in +*) push_force=1; spec=${spec#+} ;; esac
    if [ "$push_delete" = 1 ]; then src=''; dst=$spec
    else
      case "$spec" in
        *:*) src=${spec%%:*}; dst=${spec#*:} ;;
        *) src=$spec; dst=$spec ;;
      esac
    fi
    dst_ref=${dst%%%*}
    opts=''
    case "$dst" in *%*) opts=$(printf '%s\n' "${dst#*%}" | tr ',' '\n') ;; esac
    opts="$opts"$'\n'"$push_opts"
    kind=other
    branch=''
    case "$dst_ref" in
      refs/for/*) kind=for; branch=${dst_ref#refs/for/} ;;
      refs/heads/*) kind=direct; branch=${dst_ref#refs/heads/} ;;
      refs/*) kind=other ;;
      HEAD|'') kind=direct; branch=$cur ;;
      *)
        if [ -z "$src" ] || git -C "$GS_TOPLEVEL" rev-parse -q --verify "refs/tags/$dst_ref" >/dev/null 2>&1; then
          kind=other
        else
          kind=direct; branch=$dst_ref
        fi ;;
    esac
    case "$kind" in
      for) guard_refs_for "$remote" "$branch" "$src" "$opts" ;;
      direct)
        if [ "$allow" != true ]; then
          [ "$branch" != HEAD ] || branch=$GS_BRANCH
          deny "'git push … $dst_ref' is a direct push to branch '$branch', bypassing Gerrit review. Push the chain for review instead: git push $remote HEAD:refs/for/$branch (set 'git config gerrit-stack.allow-direct-push true' to allow direct pushes in this repo)."
        fi ;;
    esac
  done
  return 0
}

# ---------------------------------------------------------------- dispatch

# review comments posted through the REST fallback: comment-guard.sh decides
case "$cmd" in
  *gerrit-rest.py*review*)
    cg_rc=0
    cg_msg=$(printf '%s' "$input" | bash "$(dirname "${BASH_SOURCE[0]}")/comment-guard.sh" 2>&1 >/dev/null) || cg_rc=$?
    if [ "$cg_rc" -eq 2 ] && [ -n "$cg_msg" ]; then
      decision=deny
      deny_msg="${deny_msg}${cg_msg}"$'\n'
    fi ;;
esac

lines=$(gs_parse_git_cmd "$cmd")

while IFS=$'\t' read -r dir verb args; do
  [ -n "$verb" ] || continue
  inv_decision=silent
  inv_dir=$dir
  if ! gs_detect "$dir"; then
    continue
  fi
  case "$verb" in
    commit) guard_commit "$args" ;;
    push) guard_push "$args" ;;
  esac
  gs_trace git-guard "$verb" "$inv_decision"
done <<EOF
$lines
EOF

case "$decision" in
  deny)
    printf '%s' "$deny_msg" >&2
    exit 2 ;;
  ask)
    jq -cn --arg r "$ask_reason" \
      '{hookSpecificOutput:{hookEventName:"PreToolUse",permissionDecision:"ask",permissionDecisionReason:$r}}'
    exit 0 ;;
esac
exit 0
