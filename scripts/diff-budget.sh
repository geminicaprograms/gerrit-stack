#!/usr/bin/env bash
# scripts/diff-budget.sh — size of a change against the gerrit-stack budget.
#
# Usage: diff-budget.sh [--json] [<rev> | --worktree | --cached | --estimate <path>...]
#
#   <rev>            one commit (default HEAD): `git show --numstat <rev>`
#   --worktree       staged + unstaged changes vs HEAD, plus untracked
#   --cached         staged changes only (what the next `git commit` will contain)
#                    (non-ignored) files counted as added lines
#   --estimate P...  sum of the current sizes (lines) of the given paths;
#                    a directory counts every file under it; a path that does
#                    not exist counts as 40 lines (a new file) and is noted on
#                    stderr
#
# Output (stdout, one line): lines=<n> files=<m> budget=<L>/<F> hard=<H>
# with L/F/H from git config gerrit-stack.budget.lines|files|hard-lines
# (defaults 150/8/200). `--json` prints
# {"mode","target","lines","files","budget":{"lines","files","hard_lines"},"status"}
# instead.
#
# Exit: 0 within budget · 1 over the soft budget (lines > L or files > F) ·
# 3 over the hard cap (lines > H) · 2 usage / not a git repo / unknown rev.
# Works in any git repository (Gerrit detection is only used for config).
set -uo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

usage() {
  printf 'usage: diff-budget.sh [--json] [<rev> | --worktree | --cached | --estimate <path>...]\n'
}

json=0 mode=rev target=HEAD
paths=''
npaths=0
while [ $# -gt 0 ]; do
  case "$1" in
    --json) json=1 ;;
    --worktree) mode=worktree; target=worktree ;;
    --cached|--staged) mode=cached; target=cached ;;
    --estimate)
      mode=estimate; target=estimate
      shift
      while [ $# -gt 0 ]; do
        case "$1" in
          --json) json=1 ;;
          *) paths="$paths$1"$'\n'; npaths=$((npaths+1)) ;;
        esac
        shift
      done
      break ;;
    -h|--help) usage; exit 0 ;;
    --*)
      printf 'diff-budget: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 2 ;;
    *) mode=rev; target=$1 ;;
  esac
  shift
done
if [ "$mode" = estimate ] && [ "$npaths" -eq 0 ]; then
  printf 'diff-budget: --estimate needs at least one path\n' >&2
  exit 2
fi

if ! git rev-parse --show-toplevel >/dev/null 2>&1; then
  printf 'diff-budget: not a git repository\n' >&2
  exit 2
fi
gs_detect >/dev/null 2>&1 || true   # only for gs_config; plain repos are fine

# ---------------------------------------------------------------- budget

# budget_value <key> <default> — positive integer from config or the default
budget_value() {
  local v
  v=$(gs_config "$1" "$2")
  case "$v" in
    ''|*[!0-9]*)
      printf 'diff-budget: ignoring non-numeric gerrit-stack.%s=%s (using %s)\n' "$1" "$v" "$2" >&2
      v=$2 ;;
  esac
  printf '%s' "$v"
}
L=$(budget_value budget.lines 150)
F=$(budget_value budget.files 8)
H=$(budget_value budget.hard-lines 200)

# sum_numstat — reads numstat lines on stdin, prints "lines files"
sum_numstat() {
  awk 'BEGIN { l = 0; f = 0 }
       NF >= 3 { f++; if ($1 != "-") l += $1; if ($2 != "-") l += $2 }
       END { print l, f }'
}

# file_lines <path> — number of lines in a regular file (0 for anything else)
file_lines() {
  local n
  if [ -f "$1" ]; then
    n=$(wc -l < "$1" 2>/dev/null | tr -d ' ') || n=0
    # count an unterminated last line too
    if [ -s "$1" ] && [ "$(tail -c 1 "$1" 2>/dev/null | od -An -c | tr -d ' ')" != '\n' ]; then
      n=$((n+1))
    fi
    printf '%s' "${n:-0}"
  else
    printf '0'
  fi
}

lines=0 files=0
case "$mode" in
  rev)
    if ! git rev-parse -q --verify "$target^{commit}" >/dev/null 2>&1; then
      printf 'diff-budget: unknown revision: %s\n' "$target" >&2
      exit 2
    fi
    read -r lines files <<EOF
$(git show --numstat --format= "$target" -- 2>/dev/null | sum_numstat)
EOF
    ;;

  cached)
    if git rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1; then
      read -r lines files <<EOF
$(git diff --cached --numstat HEAD -- 2>/dev/null | sum_numstat)
EOF
    else
      read -r lines files <<EOF
$(git diff --cached --numstat -- 2>/dev/null | sum_numstat)
EOF
    fi
    ;;

  worktree)
    if git rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1; then
      read -r lines files <<EOF
$(git diff --numstat HEAD -- 2>/dev/null | sum_numstat)
EOF
    fi
    # untracked, non-ignored files count as new
    top=$(git rev-parse --show-toplevel)
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      n=$(file_lines "$top/$p")
      lines=$((lines + n))
      files=$((files + 1))
    done <<EOF
$(git -C "$top" ls-files --others --exclude-standard 2>/dev/null)
EOF
    ;;

  estimate)
    while IFS= read -r p; do
      [ -n "$p" ] || continue
      if [ -d "$p" ]; then
        while IFS= read -r f; do
          [ -n "$f" ] || continue
          n=$(file_lines "$f")
          lines=$((lines + n))
          files=$((files + 1))
        done <<EOF
$(find "$p" -type f 2>/dev/null)
EOF
      elif [ -e "$p" ]; then
        n=$(file_lines "$p")
        lines=$((lines + n))
        files=$((files + 1))
      else
        printf 'diff-budget: note: %s does not exist; counted as a new file of 40 lines\n' "$p" >&2
        lines=$((lines + 40))
        files=$((files + 1))
      fi
    done <<EOF
$paths
EOF
    ;;
esac

# ---------------------------------------------------------------- verdict

status=ok rc=0
if [ "$lines" -gt "$H" ]; then
  status=over-hard; rc=3
elif [ "$lines" -gt "$L" ] || [ "$files" -gt "$F" ]; then
  status=over-soft; rc=1
fi

if [ "$json" = 1 ]; then
  jq -cn --arg mode "$mode" --arg target "$target" --arg status "$status" \
    --argjson lines "$lines" --argjson files "$files" \
    --argjson L "$L" --argjson F "$F" --argjson H "$H" '
    { mode: $mode, target: $target, lines: $lines, files: $files,
      budget: { lines: $L, files: $F, hard_lines: $H }, status: $status }'
else
  printf 'lines=%s files=%s budget=%s/%s hard=%s\n' "$lines" "$files" "$L" "$F" "$H"
fi
case "$status" in
  over-hard) printf 'diff-budget: over the hard cap (%s lines > %s): split this change\n' "$lines" "$H" >&2 ;;
  over-soft) printf 'diff-budget: over the soft budget (%s lines / %s files vs %s/%s): justify or split\n' "$lines" "$files" "$L" "$F" >&2 ;;
esac
gs_trace diff-budget "$mode" "$status"
exit "$rc"
