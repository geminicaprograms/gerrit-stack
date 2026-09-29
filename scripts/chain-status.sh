#!/usr/bin/env bash
# scripts/chain-status.sh — relation-chain status for gerrit-stack.
#
# Usage: chain-status.sh [--preflight | --json | --snapshot | --verify-ids]
#
#   (default)     table  `sha7 | Change-Id | +/- | files | subject`, oldest
#                 first, under the header
#                 `chain: N change(s) on <remote>/<branch> (base <sha7>)`.
#                 Rows with no / several Change-Id lines show MISSING / DUP(n)
#                 in the Change-Id column; fixup!/squash!/amend! subjects are
#                 suffixed with `[fixup: squash before push]`.
#   --json        {remote,host,branch,project,base,base_sha,hook_ok,
#                  changes:[{sha,change_ids,lines,files,subject,fixup}]}
#   --preflight   detection summary (remote, host, branch, project, base,
#                 commit-msg hook, chain length, gerrit-mcp plugin); exit 1
#                 when the commit-msg hook is missing.
#   --snapshot    write the chain's Change-Id set to <git-dir>/gerrit-stack/chain-ids
#   --verify-ids  `ok` (exit 0) or `lost: I…` / `new: I…` lines (exit 1)
#                 against that snapshot; no snapshot → note, exit 0.
#
# Read-only: never runs `git commit`/`git push`, never touches the network
# (the gerrit-mcp hint only runs the local `claude plugin list`). Outside a
# Gerrit repo: one line on stderr, exit 1. Usage errors: exit 2.
set -uo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

SCRIPT_DIR=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)

usage() {
  printf 'usage: chain-status.sh [--preflight | --json | --snapshot | --verify-ids]\n'
}

mode=table
nmodes=0
while [ $# -gt 0 ]; do
  case "$1" in
    --preflight) mode=preflight; nmodes=$((nmodes+1)) ;;
    --json) mode=json; nmodes=$((nmodes+1)) ;;
    --snapshot) mode=snapshot; nmodes=$((nmodes+1)) ;;
    --verify-ids) mode=verify; nmodes=$((nmodes+1)) ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf 'chain-status: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2 ;;
  esac
  shift
done
if [ "$nmodes" -gt 1 ]; then
  printf 'chain-status: use one of --preflight, --json, --snapshot, --verify-ids at a time\n' >&2
  exit 2
fi

if ! gs_detect; then
  printf 'chain-status: not a Gerrit repository here (no gerrit-stack.remote config, .gitreview, HEAD:refs/for/ push refspec or Gerrit-looking remote URL)\n' >&2
  exit 1
fi

# ---------------------------------------------------------------- helpers

base_ref=${GS_BASE#refs/remotes/}
base_sha=''
base_sha7='-------'
if [ -n "$GS_BASE" ]; then
  base_sha=$(git -C "$GS_TOPLEVEL" rev-parse --verify -q "$GS_BASE^{commit}" 2>/dev/null) || base_sha=''
  [ -n "$base_sha" ] && base_sha7=${base_sha:0:7}
else
  base_ref="$GS_REMOTE/$GS_BRANCH"
fi

# stats_of <sha> — sets ins del files
stats_of() {
  local out
  out=$(git -C "$GS_TOPLEVEL" show --numstat --format= "$1" -- 2>/dev/null | awk '
    BEGIN { i = 0; d = 0; f = 0 }
    NF >= 3 { f++; if ($1 != "-") i += $1; if ($2 != "-") d += $2 }
    END { print i, d, f }')
  read -r ins del files <<EOF
$out
EOF
  ins=${ins:-0}; del=${del:-0}; files=${files:-0}
  return 0
}

# id_count <ids-text> — number of non-empty lines
id_count() {
  if [ -z "$1" ]; then printf '0'; else printf '%s\n' "$1" | grep -c '' || true; fi
}

# shellcheck disable=SC2119  # base defaults to $GS_BASE
commits=$(gs_chain_commits)
n=$(id_count "$commits")

# ---------------------------------------------------------------- modes

case "$mode" in
  table)
    printf 'chain: %s change(s) on %s (base %s)\n' "$n" "$base_ref" "$base_sha7"
    [ "$n" -gt 0 ] || exit 0
    printf '%-7s | %-10s | %-11s | %-5s | %s\n' sha7 Change-Id '+/-' files subject
    for sha in $commits; do
      ids=$(gs_change_ids_of "$sha")
      nids=$(id_count "$ids")
      case "$nids" in
        0) idcol='MISSING' ;;
        1) idcol=${ids:0:10} ;;
        *) idcol="DUP($nids)" ;;
      esac
      subject=$(gs_subject_of "$sha")
      if gs_is_fixup "$sha"; then subject="$subject [fixup: squash before push]"; fi
      stats_of "$sha"
      printf '%-7s | %-10s | %-11s | %5s | %s\n' \
        "${sha:0:7}" "$idcol" "+$ins/-$del" "$files" "$subject"
    done
    exit 0 ;;

  json)
    rows=''
    for sha in $commits; do
      ids=$(gs_change_ids_of "$sha")
      subject=$(gs_subject_of "$sha")
      fixup=false
      if gs_is_fixup "$sha"; then fixup=true; fi
      stats_of "$sha"
      row=$(jq -cn --arg sha "$sha" --arg ids "$ids" --arg subject "$subject" \
        --argjson lines "$((ins + del))" --argjson files "$files" --argjson fixup "$fixup" '
        { sha: $sha,
          change_ids: ($ids | split("\n") | map(select(length > 0))),
          lines: $lines, files: $files, subject: $subject, fixup: $fixup }')
      rows="$rows$row"$'\n'
    done
    printf '%s' "$rows" | jq -s \
      --arg remote "$GS_REMOTE" --arg host "$GS_HOST" --arg branch "$GS_BRANCH" \
      --arg project "$GS_PROJECT" --arg base "$base_ref" --arg base_sha "$base_sha" \
      --argjson hook_ok "$GS_HOOK_OK" '
      { remote: $remote, host: $host, branch: $branch, project: $project,
        base: $base, base_sha: $base_sha, hook_ok: $hook_ok, changes: . }'
    exit 0 ;;

  preflight)
    rc=0
    printf 'remote: %s\n' "$GS_REMOTE"
    if [ -n "$GS_HOST" ]; then
      printf 'host: %s\n' "$GS_HOST"
    else
      printf 'host: (unknown — remote is not http(s); set git config gerrit-stack.host <url>)\n'
    fi
    printf 'branch: %s\n' "$GS_BRANCH"
    printf 'project: %s\n' "$GS_PROJECT"
    if [ -n "$base_sha" ]; then
      printf 'base: %s (%s)\n' "$base_ref" "$base_sha7"
    else
      printf 'base: none — run git fetch %s (chain length is unknown until then)\n' "$GS_REMOTE"
    fi
    if [ "$GS_HOOK_OK" = 1 ]; then
      printf 'commit-msg hook: installed (%s/commit-msg)\n' "$GS_HOOKS_DIR"
    else
      printf 'commit-msg hook: MISSING (run bash %s/install-commit-msg-hook.sh)\n' "$SCRIPT_DIR"
      rc=1
    fi
    printf 'chain: %s\n' "$n"
    mcp=unknown
    if command -v claude >/dev/null 2>&1; then
      if plugins=$(claude plugin list 2>/dev/null); then
        if printf '%s\n' "$plugins" | grep -q 'gerrit-mcp'; then
          mcp=installed
        else
          mcp='not installed'
        fi
      fi
    fi
    printf 'gerrit-mcp: %s\n' "$mcp"
    gs_trace chain-status preflight "hook_ok=$GS_HOOK_OK"
    exit "$rc" ;;

  snapshot)
    gs_snapshot_write
    file="$GS_STATE_DIR/chain-ids"
    if [ -f "$file" ]; then
      count=$(grep -c '' "$file" 2>/dev/null || true)
      printf 'snapshot: %s change-id(s) written to %s\n' "${count:-0}" "$file"
      exit 0
    fi
    printf 'chain-status: could not write %s\n' "$file" >&2
    exit 1 ;;

  verify)
    file="$GS_STATE_DIR/chain-ids"
    if [ ! -f "$file" ]; then
      printf 'no snapshot at %s (run chain-status.sh --snapshot before the rebase); nothing to verify\n' "$file"
      exit 0
    fi
    if gs_snapshot_diff; then
      printf 'ok\n'
      gs_trace chain-status verify-ids ok
      exit 0
    fi
    gs_trace chain-status verify-ids drift
    exit 1 ;;
esac
