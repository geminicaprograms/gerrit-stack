#!/usr/bin/env bash
# scripts/diff-budget.sh — size of a change, as reviewer load.
#
# Usage: diff-budget.sh [--json] [<rev> | --worktree | --cached | --estimate <path>...]
#
#   <rev>            one commit (default HEAD): `git show --numstat <rev>`
#   --worktree       staged + unstaged changes vs HEAD, plus untracked
#                    (non-ignored) files counted as added lines
#   --cached         staged changes only (what the next `git commit` will contain)
#   --estimate P...  sum of the current sizes (lines) of the given paths;
#                    a directory counts every file under it; a path that does
#                    not exist counts as 40 lines (a new file) and is noted on
#                    stderr
#
# Lines = insertions + deletions, split by path into three buckets:
#   test   a path component `test/` or `tests/` (covers `src/test/`), or a file
#          name matching *Test.* · *_test.* · test_*.* · *.spec.*
#   other  docs (*.md, *.rst, *.txt, a `Documentation/` or `docs/` component)
#          and lock/generated files (*.lock, package-lock.json, go.sum)
#   prod   everything else — the only bucket the line warning looks at
# files = every file touched (all buckets).
#
# Output (stdout, one line):
#   prod=<n> test=<t> other=<o> files=<m> warn=<L> hard=<H|none> files-warn=<F|none>
# L = git config gerrit-stack.budget.lines (default 400, a reviewer-load
# warning), H = gerrit-stack.budget.hard-lines (optional, unset = no cap),
# F = gerrit-stack.budget.files (optional, unset = no file warning).
# `--json` prints {"mode","target","prod","test","other","total","files",
# "budget":{"lines","hard_lines","files"},"status"} instead (unset → null;
# status ok | over-warn | over-hard).
#
# Exit: 0 within · 1 over the warning (prod > L, or files > F when F is set) ·
# 3 over the hard cap (prod > H, only when H is set) · 2 usage / not a git
# repo / unknown rev. Works in any git repository (Gerrit detection is only
# used for config). The warning never means "split mechanically": a single
# concern or a broad mechanical change stays one change with a one-line
# justification in its commit message.
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

if [ "$mode" = rev ] && ! git rev-parse -q --verify "$target^{commit}" >/dev/null 2>&1; then
  printf 'diff-budget: unknown revision: %s\n' "$target" >&2
  exit 2
fi

# ---------------------------------------------------------------- thresholds

# budget_value <key> <default> — non-negative integer from config, else the
# default; an empty default means "unset = none" and prints "" when unset.
budget_value() {
  local v
  v=$(gs_config "$1" "$2")
  case "$v" in
    '') ;;
    *[!0-9]*)
      printf 'diff-budget: ignoring non-numeric gerrit-stack.%s=%s (using %s)\n' "$1" "$v" "${2:-none}" >&2
      v=$2 ;;
  esac
  printf '%s' "$v"
}
L=$(budget_value budget.lines 400)
H=$(budget_value budget.hard-lines '')
F=$(budget_value budget.files '')

# ---------------------------------------------------------------- counting

# summarise — reads numstat-like lines "<added>\t<deleted>\t<path>" on stdin,
# prints "<prod> <test> <other> <files>". Renames ("a => b", "d/{a => b}/f")
# are classified by their new path; binary files ("-") count 0 lines.
summarise() {
  awk -F '\t' '
    function newpath(p,   pre, post, mid) {
      if (index(p, " => ") == 0) return p
      if (match(p, /\{[^}]* => [^}]*\}/)) {
        pre = substr(p, 1, RSTART - 1)
        mid = substr(p, RSTART + 1, RLENGTH - 2)
        post = substr(p, RSTART + RLENGTH)
        sub(/^.* => /, "", mid)
        p = pre mid post
        gsub(/\/\/+/, "/", p)
        sub(/^\//, "", p)
        return p
      }
      sub(/^.* => /, "", p)
      return p
    }
    function bucket(p,   n, parts, i, base) {
      gsub(/^"|"$/, "", p)
      n = split(p, parts, "/")
      base = parts[n]
      for (i = 1; i < n; i++)
        if (parts[i] == "test" || parts[i] == "tests") return "test"
      if (base ~ /Test\./ || base ~ /_test\./ || base ~ /^test_.*\./ || base ~ /\.spec\./)
        return "test"
      for (i = 1; i < n; i++)
        if (parts[i] == "Documentation" || parts[i] == "docs") return "other"
      if (base ~ /\.(md|rst|txt|lock)$/ || base == "package-lock.json" || base == "go.sum")
        return "other"
      return "prod"
    }
    BEGIN { nprod = 0; ntest = 0; nother = 0; nfiles = 0 }
    NF >= 3 {
      n = 0
      if ($1 != "-") n += $1
      if ($2 != "-") n += $2
      b = bucket(newpath($3))
      if (b == "test") ntest += n; else if (b == "other") nother += n; else nprod += n
      nfiles++
    }
    END { print nprod, ntest, nother, nfiles }'
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

# numstat — numstat-like lines for the selected mode on stdout
numstat() {
  local top p f
  case "$mode" in
    rev)
      git -c core.quotepath=off show --numstat --format= "$target" -- 2>/dev/null ;;
    cached)
      if git rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1; then
        git -c core.quotepath=off diff --cached --numstat HEAD -- 2>/dev/null
      else
        git -c core.quotepath=off diff --cached --numstat -- 2>/dev/null
      fi ;;
    worktree)
      if git rev-parse -q --verify 'HEAD^{commit}' >/dev/null 2>&1; then
        git -c core.quotepath=off diff --numstat HEAD -- 2>/dev/null
      fi
      # untracked, non-ignored files count as new
      top=$(git rev-parse --show-toplevel)
      while IFS= read -r p; do
        [ -n "$p" ] || continue
        printf '%s\t0\t%s\n' "$(file_lines "$top/$p")" "$p"
      done <<EOF
$(git -C "$top" -c core.quotepath=off ls-files --others --exclude-standard 2>/dev/null)
EOF
      ;;
    estimate)
      while IFS= read -r p; do
        [ -n "$p" ] || continue
        if [ -d "$p" ]; then
          while IFS= read -r f; do
            [ -n "$f" ] || continue
            printf '%s\t0\t%s\n' "$(file_lines "$f")" "$f"
          done <<EOF
$(find "$p" -type f 2>/dev/null)
EOF
        elif [ -e "$p" ]; then
          printf '%s\t0\t%s\n' "$(file_lines "$p")" "$p"
        else
          printf 'diff-budget: note: %s does not exist; counted as a new file of 40 lines\n' "$p" >&2
          printf '40\t0\t%s\n' "$p"
        fi
      done <<EOF
$paths
EOF
      ;;
  esac
}

prod=0 tst=0 other=0 files=0
read -r prod tst other files <<EOF
$(numstat | summarise)
EOF

# ---------------------------------------------------------------- verdict

status=ok rc=0
if [ -n "$H" ] && [ "$prod" -gt "$H" ]; then
  status=over-hard; rc=3
elif [ "$prod" -gt "$L" ] || { [ -n "$F" ] && [ "$files" -gt "$F" ]; }; then
  status=over-warn; rc=1
fi

if [ "$json" = 1 ]; then
  jq -cn --arg mode "$mode" --arg target "$target" --arg status "$status" \
    --argjson prod "$prod" --argjson test "$tst" --argjson other "$other" \
    --argjson files "$files" --argjson L "$L" --arg H "$H" --arg F "$F" '
    { mode: $mode, target: $target, prod: $prod, test: $test, other: $other,
      total: ($prod + $test + $other), files: $files,
      budget: { lines: $L,
                hard_lines: (if $H == "" then null else ($H | tonumber) end),
                files: (if $F == "" then null else ($F | tonumber) end) },
      status: $status }'
else
  printf 'prod=%s test=%s other=%s files=%s warn=%s hard=%s files-warn=%s\n' \
    "$prod" "$tst" "$other" "$files" "$L" "${H:-none}" "${F:-none}"
fi
case "$status" in
  over-hard)
    printf 'diff-budget: %s production lines exceed the hard cap this repository set (gerrit-stack.budget.hard-lines=%s). Cut only along concern boundaries (each part builds, is tested and makes sense alone), never into fragments; if no such cut exists, say so in the commit message and raise the cap with the team.\n' "$prod" "$H" >&2 ;;
  over-warn)
    printf 'diff-budget:' >&2
    if [ "$prod" -gt "$L" ]; then
      printf ' %s production lines, above the reviewer-load warning of %s (tests and docs are not counted).' "$prod" "$L" >&2
    fi
    if [ -n "$F" ] && [ "$files" -gt "$F" ]; then
      printf ' %s files, above the file warning of %s (gerrit-stack.budget.files).' "$files" "$F" >&2
    fi
    printf ' If this is one concern, keep it one change and add a one-line justification to the commit message; broad mechanical changes (migration, rename, formatter) stay one change. Do not split mechanically.\n' >&2 ;;
esac
gs_trace diff-budget "$mode" "$status"
exit "$rc"
