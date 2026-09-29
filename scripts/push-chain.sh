#!/usr/bin/env bash
# scripts/push-chain.sh — compose (never run) the Gerrit push command.
#
# Usage: push-chain.sh [--wip] [--grouping none|hashtag|topic] [--name <x>]
#                      [--branch <b>] [--remote <r>]
#
# Validates the relation chain <base>..HEAD (non-empty, exactly one Change-Id
# per commit, no fixup!/squash!/amend! commits, grouping + name consistent)
# and prints exactly one line on stdout:
#
#   git push <remote> HEAD:refs/for/<branch>[%t=<tag> | %topic=<slug>][,wip]
#
# Defaults come from git config: gerrit-stack.remote, .branch, .grouping,
# .group-name, .default-wip. Flags override them for this print only; the
# script NEVER writes config (the gerrit-stack skill persists the user's
# grouping answer). Names are slugified: whitespace → "-", every character
# outside [A-Za-z0-9._/-] dropped.
#
# Exit codes: 0 printed · 1 not a Gerrit repo · 2 usage · 3 validation failed
# (reasons on stderr, one per line, including the repair command for missing
# Change-Ids). Never runs `git push`.
set -uo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

usage() {
  printf 'usage: push-chain.sh [--wip] [--grouping none|hashtag|topic] [--name <x>] [--branch <b>] [--remote <r>]\n'
}

opt_wip=0 opt_grouping='' opt_name='' opt_branch='' opt_remote=''
while [ $# -gt 0 ]; do
  case "$1" in
    --wip) opt_wip=1 ;;
    --grouping|--name|--branch|--remote)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'push-chain: %s needs a value\n' "$1" >&2
        usage >&2
        exit 2
      fi
      case "$1" in
        --grouping) opt_grouping=$2 ;;
        --name) opt_name=$2 ;;
        --branch) opt_branch=$2 ;;
        --remote) opt_remote=$2 ;;
      esac
      shift ;;
    --grouping=*) opt_grouping=${1#*=} ;;
    --name=*) opt_name=${1#*=} ;;
    --branch=*) opt_branch=${1#*=} ;;
    --remote=*) opt_remote=${1#*=} ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf 'push-chain: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2 ;;
  esac
  shift
done

if ! gs_detect; then
  printf 'push-chain: not a Gerrit repository here (no gerrit-stack.remote config, .gitreview, HEAD:refs/for/ push refspec or Gerrit-looking remote URL)\n' >&2
  exit 1
fi

# ---------------------------------------------------------------- inputs

remote=${opt_remote:-$GS_REMOTE}
branch=${opt_branch:-$GS_BRANCH}
grouping=${opt_grouping:-$(gs_config grouping none)}
name=${opt_name:-$(gs_config group-name)}
wip=$opt_wip
if [ "$wip" = 0 ]; then
  v=$(git -C "$GS_TOPLEVEL" config --type=bool gerrit-stack.default-wip 2>/dev/null) || v=false
  [ "$v" = true ] && wip=1
fi

# base: refs/remotes/<remote>/<branch> when it exists, else the detected base
# (an override of --remote/--branch whose tracking ref is absent is validated
# against the detected chain and noted on stderr).
base=''
if git -C "$GS_TOPLEVEL" rev-parse -q --verify "refs/remotes/$remote/$branch^{commit}" >/dev/null 2>&1; then
  base="refs/remotes/$remote/$branch"
else
  base=$GS_BASE
  if [ -n "$base" ] && { [ "$remote" != "$GS_REMOTE" ] || [ "$branch" != "$GS_BRANCH" ]; }; then
    printf 'push-chain: note: refs/remotes/%s/%s not found; chain validated against %s\n' \
      "$remote" "$branch" "${base#refs/remotes/}" >&2
  fi
fi
base_disp=${base#refs/remotes/}
[ -n "$base_disp" ] || base_disp="$remote/$branch"

# slugify <name> — whitespace → "-", keep [A-Za-z0-9._/-] only
slugify() {
  printf '%s' "$1" | tr '[:space:]' '-' | tr -cd 'A-Za-z0-9._/-'
}

# ---------------------------------------------------------------- validation

reasons=''
add_reason() { reasons="$reasons$1"$'\n'; }

commits=''
if [ -z "$base" ]; then
  add_reason "no base ref for $remote/$branch: run git fetch $remote first"
else
  commits=$(gs_chain_commits "$base")
  [ -n "$commits" ] || add_reason "chain is empty: no commits between $base_disp and HEAD"
fi

missing='' dups='' fixups=''
for sha in $commits; do
  nids=$(gs_change_ids_of "$sha" | grep -c '' || true)
  short=${sha:0:7}
  if gs_is_fixup "$sha"; then
    fixups="$fixups $short"
  fi
  case "$nids" in
    0) missing="$missing $short" ;;
    1) ;;
    *) dups="$dups $short($nids)" ;;
  esac
done
if [ -n "$missing" ]; then
  add_reason "commit(s) without a Change-Id:$missing"
  add_reason "repair (with the commit-msg hook installed): git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' $base_disp"
fi
if [ -n "$dups" ]; then
  add_reason "commit(s) with several Change-Id lines:$dups — keep exactly one (git rebase -i, reword)"
fi
if [ -n "$fixups" ]; then
  add_reason "fixup!/squash! commit(s) still in the chain:$fixups — squash first: git -c sequence.editor=true rebase -i --autosquash $base_disp"
fi

slug=''
case "$grouping" in
  none) ;;
  hashtag|topic)
    if [ -z "$name" ]; then
      add_reason "grouping '$grouping' needs a name: pass --name <x> or set git config gerrit-stack.group-name <x>"
    else
      slug=$(slugify "$name")
      [ -n "$slug" ] || add_reason "grouping name '$name' has no usable characters ([A-Za-z0-9._/-])"
    fi ;;
  *) add_reason "grouping must be one of none|hashtag|topic (got '$grouping')" ;;
esac

if [ -n "$reasons" ]; then
  printf '%s' "$reasons" >&2
  gs_trace push-chain validate reject
  exit 3
fi

# ---------------------------------------------------------------- output

opts=''
case "$grouping" in
  hashtag) opts="t=$slug" ;;
  topic) opts="topic=$slug" ;;
esac
if [ "$wip" = 1 ]; then
  if [ -n "$opts" ]; then opts="$opts,wip"; else opts=wip; fi
fi
line="git push $remote HEAD:refs/for/$branch"
[ -z "$opts" ] || line="$line%$opts"
printf '%s\n' "$line"
gs_trace push-chain compose "$grouping${wip:+ wip=$wip}"
exit 0
