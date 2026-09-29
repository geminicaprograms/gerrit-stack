#!/usr/bin/env bash
# chain-metrics.sh — deterministic outcome metrics for a Gerrit relation chain.
#
# Usage:
#   chain-metrics.sh [--base <ref>] [--json] [--verify-cmd <cmd>]
#                    [--hook-trace <file>] [--remote <name>] [<repo-dir>]
#
# Measures the chain `base..HEAD` of <repo-dir> (default: current directory):
#   per change : lines (+/- via numstat, binary files count 0), files, subject,
#                change_ids (number of Change-Id trailers), conventional_type,
#                is_fixup, within_budget, single_concern, builds_alone
#   aggregates : lines_median / lines_p75 (nearest rank) / lines_max,
#                files_median, within_budget_pct, one_change_id_pct,
#                conventional_pct, single_concern_pct, fixups_present,
#                builds_alone_pct (only with --verify-cmd), refs_for_pushed
#                (only with --remote pointing at a local repo), violations /
#                asks (only with --hook-trace), change_id_set (sorted).
#
# Base: --base, else refs/remotes/origin/master, else @{upstream}, else the
# root commit (the chain is then every commit after the root).
# Budget: git config gerrit-stack.budget.lines (150) / .files (8).
# --verify-cmd runs `git -c sequence.editor=true rebase -i --exec <cmd> <base>`
# in a temporary detached worktree, continuing past failures so every change is
# checked; the caller's checkout, index and HEAD are never touched.
# --hook-trace reads GERRIT_STACK_TRACE lines `<script>\t<verb>\t<decision>` and
# counts `deny` (violations) and `ask` decisions per verb.
#
# Output: human table (default) or one JSON object (--json).
# Exit: 0 (metrics, not a gate); 2 on usage errors. Self-contained on purpose
# (no scripts/lib dependency); bash 3.2 compatible; needs git and jq.
set -uo pipefail

usage() {
  cat <<'EOF'
usage: chain-metrics.sh [--base <ref>] [--json] [--verify-cmd <cmd>]
                        [--hook-trace <file>] [--remote <name>] [<repo-dir>]
EOF
}

die_usage() {
  echo "chain-metrics.sh: $*" >&2
  usage >&2
  exit 2
}

BASE_ARG=""
JSON=0
VERIFY_CMD=""
HOOK_TRACE=""
REMOTE=""
REPO="."
REPO_SET=0

while [ $# -gt 0 ]; do
  case "$1" in
    --base)
      [ $# -ge 2 ] || die_usage "--base needs a value"
      BASE_ARG=$2; shift 2 ;;
    --base=*) BASE_ARG=${1#*=}; shift ;;
    --json) JSON=1; shift ;;
    --verify-cmd)
      [ $# -ge 2 ] || die_usage "--verify-cmd needs a value"
      VERIFY_CMD=$2; shift 2 ;;
    --verify-cmd=*) VERIFY_CMD=${1#*=}; shift ;;
    --hook-trace)
      [ $# -ge 2 ] || die_usage "--hook-trace needs a value"
      HOOK_TRACE=$2; shift 2 ;;
    --hook-trace=*) HOOK_TRACE=${1#*=}; shift ;;
    --remote)
      [ $# -ge 2 ] || die_usage "--remote needs a value"
      REMOTE=$2; shift 2 ;;
    --remote=*) REMOTE=${1#*=}; shift ;;
    -h|--help) usage; exit 0 ;;
    --) shift; break ;;
    -*) die_usage "unknown option: $1" ;;
    *)
      [ "$REPO_SET" -eq 0 ] || die_usage "unexpected argument: $1"
      REPO=$1; REPO_SET=1; shift ;;
  esac
done
if [ $# -gt 0 ]; then
  [ "$REPO_SET" -eq 0 ] || die_usage "unexpected argument: $1"
  REPO=$1; REPO_SET=1; shift
  [ $# -eq 0 ] || die_usage "unexpected argument: $1"
fi

command -v git >/dev/null 2>&1 || die_usage "git not found on PATH"
command -v jq >/dev/null 2>&1 || die_usage "jq not found on PATH"
[ -d "$REPO" ] || die_usage "not a directory: $REPO"
cd "$REPO" || die_usage "cannot cd to $REPO"
if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die_usage "not inside a git work tree: $REPO"
fi
TOPLEVEL=$(git rev-parse --show-toplevel)

# Absolute path of --hook-trace relative to the caller's cwd, resolved before cd.
# (cd happened above; recompute from OLDPWD when the path is relative.)
if [ -n "$HOOK_TRACE" ]; then
  case "$HOOK_TRACE" in
    /*) ;;
    *) HOOK_TRACE=${OLDPWD:-$PWD}/$HOOK_TRACE ;;
  esac
fi

TMP=$(mktemp -d "${TMPDIR:-/tmp}/chain-metrics.XXXXXX") || die_usage "mktemp failed"
WT=""
# shellcheck disable=SC2329  # invoked via the EXIT trap
cleanup() {
  if [ -n "$WT" ] && [ -d "$WT" ]; then
    git -C "$WT" rebase --abort >/dev/null 2>&1 || true
    git worktree remove --force "$WT" >/dev/null 2>&1 || true
    git worktree prune >/dev/null 2>&1 || true
  fi
  rm -rf "$TMP"
}
trap cleanup EXIT

# ---------------------------------------------------------------- budget ----
num_or_default() { # <value> <default>
  case "$1" in
    ''|*[!0-9]*) printf '%s\n' "$2" ;;
    *) printf '%s\n' "$1" ;;
  esac
}
BUDGET_LINES=$(num_or_default "$(git config --get gerrit-stack.budget.lines 2>/dev/null || true)" 150)
BUDGET_FILES=$(num_or_default "$(git config --get gerrit-stack.budget.files 2>/dev/null || true)" 8)

# ------------------------------------------------------------------ base ----
HEAD_SHA=$(git rev-parse --verify -q 'HEAD^{commit}' 2>/dev/null || true)
BASE_SHA=""
BASE_REF=""
if [ -n "$HEAD_SHA" ]; then
  if [ -n "$BASE_ARG" ]; then
    BASE_SHA=$(git rev-parse --verify -q "${BASE_ARG}^{commit}" 2>/dev/null) \
      || die_usage "cannot resolve --base '$BASE_ARG' to a commit"
    BASE_REF=$BASE_ARG
  elif BASE_SHA=$(git rev-parse --verify -q 'refs/remotes/origin/master^{commit}' 2>/dev/null); then
    BASE_REF=refs/remotes/origin/master
  elif BASE_SHA=$(git rev-parse --verify -q '@{upstream}^{commit}' 2>/dev/null); then
    BASE_REF='@{upstream}'
  else
    BASE_SHA=$(git rev-list --max-parents=0 HEAD | tail -n 1)
    BASE_REF=root
  fi
fi

# --------------------------------------------------------------- commits ----
COMMITS=$TMP/commits
: > "$COMMITS"
if [ -n "$HEAD_SHA" ]; then
  git rev-list --reverse "${BASE_SHA}..HEAD" > "$COMMITS"
fi
CHAIN_LENGTH=$(grep -c . "$COMMITS" || true)

CONV_RE='^(feat|fix|refactor|docs|test|chore|build|ci|perf|style)(\(.+\))?!?:'
TOKEN_RE='^(feat|fix|refactor|docs|test|chore|build|ci|perf|style)(\(.*\))?!?:$'
FIXUP_RE='^(fixup|squash|amend)! '

commit_message() { # <sha> -> message body on stdout (everything after the header)
  git cat-file commit "$1" | awk 'body { print; next } /^$/ { body = 1 }'
}

# Per-change rows, TAB-separated: sha, lines, files, cids, type ("-" when the
# subject is not a Conventional Commit: `read` with IFS=TAB collapses empty
# fields, so a placeholder keeps the columns aligned), fixup, within, single,
# subject (last field, may contain TABs/spaces).
ROWS=$TMP/rows
: > "$ROWS"
CIDS=$TMP/cids
: > "$CIDS"
while IFS= read -r sha; do
  [ -n "$sha" ] || continue
  msg=$(commit_message "$sha")
  subject=$(printf '%s\n' "$msg" | sed -n '1p')
  cid_lines=$(printf '%s\n' "$msg" | git interpret-trailers --parse --no-divider 2>/dev/null \
    | grep '^Change-Id:' || true)
  if [ -n "$cid_lines" ]; then
    cids=$(printf '%s\n' "$cid_lines" | grep -c . || true)
    printf '%s\n' "$cid_lines" | sed 's/^Change-Id:[[:space:]]*//' >> "$CIDS"
  else
    cids=0
  fi
  stat=$(git diff-tree -r --root --numstat --no-commit-id "$sha" \
    | awk '{ if ($1 != "-") a += $1; if ($2 != "-") a += $2; f++ } END { printf "%d %d\n", a + 0, f + 0 }')
  lines=${stat% *}
  files=${stat#* }
  ctype="-"
  if [[ $subject =~ $CONV_RE ]]; then ctype=${BASH_REMATCH[1]}; fi
  fixup=false
  if [[ $subject =~ $FIXUP_RE ]]; then fixup=true; fi
  within=false
  if [ "$lines" -le "$BUDGET_LINES" ] && [ "$files" -le "$BUDGET_FILES" ]; then within=true; fi
  # single concern: a type, no " and ", at most one type token in the subject
  single=false
  if [ "$ctype" != "-" ]; then
    lower=$(printf '%s' "$subject" | tr '[:upper:]' '[:lower:]')
    tokens=0
    for w in $subject; do
      if [[ $w =~ $TOKEN_RE ]]; then tokens=$((tokens + 1)); fi
    done
    case " $lower " in
      *" and "*) single=false ;;
      *) [ "$tokens" -le 1 ] && single=true ;;
    esac
  fi
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$sha" "$lines" "$files" "$cids" "$ctype" "$fixup" "$within" "$single" "$subject" >> "$ROWS"
done < "$COMMITS"

# ------------------------------------------------------------- verify-cmd ----
# builds file: one line per chain position (1-based): ok | fail
BUILDS=$TMP/builds
: > "$BUILDS"
VERIFY_ERROR=""
if [ -n "$VERIFY_CMD" ] && [ "$CHAIN_LENGTH" -gt 0 ]; then
  WT=$TMP/wt
  if git worktree add --detach --quiet "$WT" "$HEAD_SHA" >/dev/null 2>&1; then
    i=1
    while [ "$i" -le "$CHAIN_LENGTH" ]; do echo ok >> "$BUILDS"; i=$((i + 1)); done
    git -C "$WT" -c sequence.editor=true rebase -i --no-autosquash --no-update-refs \
      --empty=keep --exec "$VERIFY_CMD" "$BASE_SHA" >/dev/null 2>&1
    rc=$?
    guard=0
    while [ "$rc" -ne 0 ] && [ -d "$(git -C "$WT" rev-parse --git-path rebase-merge)" ]; do
      pos=$(git -C "$WT" rev-list --count "${BASE_SHA}..HEAD")
      if [ "$pos" -ge 1 ] && [ "$pos" -le "$CHAIN_LENGTH" ]; then
        awk -v p="$pos" 'NR == p { print "fail"; next } { print }' "$BUILDS" > "$BUILDS.new" \
          && mv -f "$BUILDS.new" "$BUILDS"
      fi
      # the verify command may leave build output behind; the temp worktree is disposable
      git -C "$WT" reset --hard --quiet >/dev/null 2>&1 || true
      git -C "$WT" clean -fdq >/dev/null 2>&1 || true
      guard=$((guard + 1))
      if [ "$guard" -gt "$((CHAIN_LENGTH + 1))" ]; then
        VERIFY_ERROR="rebase --continue loop did not terminate"
        break
      fi
      git -C "$WT" rebase --continue >/dev/null 2>&1
      rc=$?
    done
    if [ "$rc" -ne 0 ] && [ -z "$VERIFY_ERROR" ]; then
      VERIFY_ERROR="rebase --exec exited $rc"
    fi
  else
    VERIFY_ERROR="git worktree add failed"
  fi
  # Drop the temporary worktree right away (cleanup() would too, on EXIT).
  git -C "$WT" rebase --abort >/dev/null 2>&1 || true
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  git worktree prune >/dev/null 2>&1 || true
  WT=""
fi

# ------------------------------------------------------------- hook trace ----
HOOK_JSON=null
if [ -n "$HOOK_TRACE" ]; then
  NORM=$TMP/trace
  if [ -f "$HOOK_TRACE" ]; then
    # normalise to `verb<TAB>decision`; accept TAB- or space-separated records
    awk '
      { sub(/\r$/, "") }
      /^[[:space:]]*$/ { next }
      {
        if (index($0, "\t") > 0) { n = split($0, p, "\t") } else { n = split($0, p, /[[:space:]]+/) }
        verb = (n >= 2) ? p[2] : ""
        dec = (n >= 3) ? p[3] : ""
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", verb)
        gsub(/^[[:space:]]+|[[:space:]]+$/, "", dec)
        print verb "\t" dec
      }' "$HOOK_TRACE" > "$NORM"
    missing=false
  else
    : > "$NORM"
    missing=true
  fi
  HOOK_JSON=$(jq -Rn --arg file "$HOOK_TRACE" --argjson missing "$missing" '
    [inputs | split("\t") | {verb: .[0], decision: .[1]}] as $rows
    | def by_verb(d): ($rows | map(select(.decision == d)) | group_by(.verb)
        | map({key: .[0].verb, value: length}) | from_entries);
    {
      file: $file,
      missing: $missing,
      lines: ($rows | length),
      violations: ($rows | map(select(.decision == "deny")) | length),
      violations_by_verb: by_verb("deny"),
      asks: ($rows | map(select(.decision == "ask")) | length),
      asks_by_verb: by_verb("ask"),
      feedback: ($rows | map(select(.decision == "feedback")) | length)
    }' < "$NORM")
fi

# ----------------------------------------------------------------- remote ----
REMOTE_JSON=null
if [ -n "$REMOTE" ]; then
  url=$(git config --get "remote.$REMOTE.url" 2>/dev/null || true)
  [ -n "$url" ] || die_usage "no such remote: $REMOTE"
  rpath=""
  case "$url" in
    file://*) rpath=${url#file://} ;;
    *://*) rpath="" ;;
    *@*:*) rpath="" ;;
    /*) rpath=$url ;;
    *) rpath=$TOPLEVEL/$url ;;
  esac
  refs_changes=0; refs_for=0; refs_for_commits=0; is_local=false
  if [ -n "$rpath" ] && [ -d "$rpath" ] && git -C "$rpath" rev-parse --git-dir >/dev/null 2>&1; then
    is_local=true
    refs_changes=$(git -C "$rpath" for-each-ref --format='%(refname)' 'refs/changes/' | grep -c . || true)
    refs_for=$(git -C "$rpath" for-each-ref --format='%(refname)' 'refs/for/' | grep -c . || true)
    if [ "$refs_for" -gt 0 ]; then
      refs_for_commits=$(git -C "$rpath" rev-list --count --glob='refs/for/*' --not --glob='refs/heads/*' 2>/dev/null || echo 0)
    fi
  fi
  REMOTE_JSON=$(jq -n --arg name "$REMOTE" --arg url "$url" --argjson local "$is_local" \
    --argjson changes "$refs_changes" --argjson for "$refs_for" --argjson forc "$refs_for_commits" '
    {name: $name, url: $url, local: $local, refs_changes: $changes, refs_for: $for,
     refs_for_commits: $forc, refs_for_pushed: (if $local then ($changes + $forc) else null end)}')
fi

# ------------------------------------------------------------- aggregates ----
sorted_stat() { # <column> <median|p75|max> ; prints number or null
  cut -f"$1" "$ROWS" | sort -n | awk -v mode="$2" '
    function fmt(v) { return (v == int(v)) ? sprintf("%d", v) : sprintf("%.1f", v) }
    { a[NR] = $1 + 0 }
    END {
      if (NR == 0) { print "null"; exit }
      if (mode == "max") { print fmt(a[NR]); exit }
      if (mode == "p75") { i = int(0.75 * NR); if (i < 0.75 * NR) i++; if (i < 1) i = 1; print fmt(a[i]); exit }
      if (NR % 2 == 1) print fmt(a[(NR + 1) / 2]); else print fmt((a[NR / 2] + a[NR / 2 + 1]) / 2)
    }'
}
pct_of() { # <count> ; percentage of CHAIN_LENGTH, or null when the chain is empty
  awk -v c="$1" -v n="$CHAIN_LENGTH" 'BEGIN {
    if (n == 0) { print "null"; exit }
    v = 100 * c / n
    if (v == int(v)) printf "%d\n", v; else printf "%.1f\n", v
  }'
}
count_col_eq() { # <column> <value>
  awk -F'\t' -v col="$1" -v val="$2" '$col == val { c++ } END { print c + 0 }' "$ROWS"
}

LINES_MEDIAN=$(sorted_stat 2 median)
LINES_P75=$(sorted_stat 2 p75)
LINES_MAX=$(sorted_stat 2 max)
FILES_MEDIAN=$(sorted_stat 3 median)
WITHIN_PCT=$(pct_of "$(count_col_eq 7 true)")
ONE_CID_PCT=$(pct_of "$(count_col_eq 4 1)")
CONV_COUNT=$(awk -F'\t' '$5 != "-" { c++ } END { print c + 0 }' "$ROWS")
CONV_PCT=$(pct_of "$CONV_COUNT")
SINGLE_PCT=$(pct_of "$(count_col_eq 8 true)")
FIXUPS_PRESENT=false
if [ "$(count_col_eq 6 true)" -gt 0 ]; then FIXUPS_PRESENT=true; fi
BUILDS_PCT=null
if [ -n "$VERIFY_CMD" ] && [ "$CHAIN_LENGTH" -gt 0 ] && [ -z "$VERIFY_ERROR" ]; then
  BUILDS_PCT=$(pct_of "$(grep -c '^ok$' "$BUILDS" || true)")
fi

# --------------------------------------------------------------- changes ----
CHANGES=$TMP/changes.json
: > "$CHANGES"
pos=0
while IFS=$'\t' read -r sha lines files cids ctype fixup within single subject; do
  pos=$((pos + 1))
  builds=null
  if [ -s "$BUILDS" ] && [ -z "$VERIFY_ERROR" ]; then
    case "$(sed -n "${pos}p" "$BUILDS")" in
      ok) builds=true ;;
      fail) builds=false ;;
    esac
  fi
  if [ "$ctype" != "-" ]; then ctype_json="\"$ctype\""; else ctype_json=null; fi
  jq -cn --arg sha "$sha" --arg subject "$subject" --argjson lines "$lines" --argjson files "$files" \
    --argjson cids "$cids" --argjson ctype "$ctype_json" --argjson fixup "$fixup" \
    --argjson within "$within" --argjson single "$single" --argjson builds "$builds" '
    {sha: $sha, subject: $subject, lines: $lines, files: $files, change_ids: $cids,
     conventional_type: $ctype, is_fixup: $fixup, within_budget: $within,
     single_concern: $single, builds_alone: $builds}' >> "$CHANGES"
done < "$ROWS"

CID_SET=$(sort -u "$CIDS")
if [ -n "$VERIFY_ERROR" ]; then VERIFY_ERROR_JSON="\"$VERIFY_ERROR\""; else VERIFY_ERROR_JSON=null; fi

RESULT=$(jq -n \
  --arg repo "$TOPLEVEL" --arg head "$HEAD_SHA" --arg base "$BASE_SHA" --arg base_ref "$BASE_REF" \
  --argjson budget_lines "$BUDGET_LINES" --argjson budget_files "$BUDGET_FILES" \
  --argjson chain_length "$CHAIN_LENGTH" --slurpfile changes "$CHANGES" \
  --argjson lines_median "$LINES_MEDIAN" --argjson lines_p75 "$LINES_P75" --argjson lines_max "$LINES_MAX" \
  --argjson files_median "$FILES_MEDIAN" --argjson within "$WITHIN_PCT" --argjson one_cid "$ONE_CID_PCT" \
  --argjson conv "$CONV_PCT" --argjson single "$SINGLE_PCT" --argjson fixups "$FIXUPS_PRESENT" \
  --arg verify_cmd "$VERIFY_CMD" --argjson builds "$BUILDS_PCT" --argjson verify_error "$VERIFY_ERROR_JSON" \
  --argjson remote "$REMOTE_JSON" --argjson hook "$HOOK_JSON" --arg cid_set "$CID_SET" '
  {
    repo: $repo,
    head: (if $head == "" then null else $head end),
    base: (if $base == "" then null else $base end),
    base_ref: (if $base_ref == "" then null else $base_ref end),
    budget: {lines: $budget_lines, files: $budget_files},
    chain_length: $chain_length,
    changes: $changes,
    lines_median: $lines_median,
    lines_p75: $lines_p75,
    lines_max: $lines_max,
    files_median: $files_median,
    within_budget_pct: $within,
    one_change_id_pct: $one_cid,
    conventional_pct: $conv,
    single_concern_pct: $single,
    fixups_present: $fixups,
    verify_cmd: (if $verify_cmd == "" then null else $verify_cmd end),
    builds_alone_pct: $builds,
    verify_error: $verify_error,
    remote: $remote,
    refs_for_pushed: (if $remote == null then null else $remote.refs_for_pushed end),
    hook_trace: $hook,
    violations: (if $hook == null then null else $hook.violations end),
    violations_by_verb: (if $hook == null then null else $hook.violations_by_verb end),
    asks: (if $hook == null then null else $hook.asks end),
    asks_by_verb: (if $hook == null then null else $hook.asks_by_verb end),
    change_id_set: ($cid_set | split("\n") | map(select(length > 0)))
  }')

if [ "$JSON" -eq 1 ]; then
  printf '%s\n' "$RESULT"
  exit 0
fi

# ------------------------------------------------------------ human table ----
printf '%s\n' "$RESULT" | jq -r '
  def s(x): if x == null then "-" else (x | tostring) end;
  def yn(x): if x == null then "-" elif x then "yes" else "no" end;
  def pct(x): if x == null then "-" else ((x | tostring) + "%") end;
  "chain: \(.chain_length) change(s)  base=\(s(.base_ref)) (\(s(.base) | .[0:7]))  head=\(s(.head) | .[0:7])  budget=\(.budget.lines)/\(.budget.files)",
  "sha      cid  lines files type      concern budget build  subject",
  (.changes[] | "\(.sha[0:7])  \(.change_ids)    \(.lines | tostring | .[0:5] | . + " " * (5 - length))  \(.files | tostring | . + " " * (5 - length)) \(s(.conventional_type) | . + " " * (9 - length)) \(yn(.single_concern) | . + " " * (7 - length)) \(yn(.within_budget) | . + " " * (6 - length)) \(yn(.builds_alone) | . + " " * (5 - length))  \(.subject)"),
  "lines: median \(s(.lines_median))  p75 \(s(.lines_p75))  max \(s(.lines_max))  |  files: median \(s(.files_median))",
  "within budget \(pct(.within_budget_pct))  |  one Change-Id \(pct(.one_change_id_pct))  |  conventional \(pct(.conventional_pct))  |  single concern \(pct(.single_concern_pct))  |  fixups \(yn(.fixups_present))",
  (if .verify_cmd != null then "builds alone: \(pct(.builds_alone_pct))  (\(.verify_cmd))\(if .verify_error != null then "  error: " + .verify_error else "" end)" else empty end),
  (if .hook_trace != null then "hook trace: \(.hook_trace.violations) deny \(.hook_trace.violations_by_verb | tojson)  \(.hook_trace.asks) ask \(.hook_trace.asks_by_verb | tojson)  (\(.hook_trace.lines) lines\(if .hook_trace.missing then ", file missing" else "" end))" else empty end),
  (if .remote != null then "refs/for pushed: \(s(.refs_for_pushed))  (\(.remote.name) -> \(.remote.url)\(if .remote.local then "" else ", not local" end))" else empty end),
  "change-ids: \(if (.change_id_set | length) == 0 then "(none)" else (.change_id_set | join(" ")) end)"
'
exit 0
