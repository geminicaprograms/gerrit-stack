#!/usr/bin/env bash
# chain-metrics.sh — deterministic outcome metrics for a Gerrit relation chain.
#
# Usage:
#   chain-metrics.sh [--base <ref>] [--json] [--verify-cmd <cmd>]
#                    [--verify-timeout <seconds>] [--concerns <case.yaml>]
#                    [--hook-trace <file>] [--remote <name>] [<repo-dir>]
#
# Measures the chain `base..HEAD` of <repo-dir> (default: current directory):
#   per change : lines (+/- via numstat, binary files count 0), files, subject,
#                change_ids (number of Change-Id trailers), conventional_type,
#                is_fixup, within_budget, single_concern, builds_alone,
#                tests_travel, concerns, unmapped_paths
#   aggregates : lines_median / lines_p75 (nearest rank) / lines_max,
#                files_median, within_budget_pct, one_change_id_pct,
#                conventional_pct, single_concern_pct, fixups_present,
#                tests_travel_pct, builds_alone_pct (only with --verify-cmd),
#                purity_pct / completeness_pct / concerns_seen (only with
#                --concerns), refs_for_pushed (only with --remote pointing at
#                a local repo), violations / asks (only with --hook-trace),
#                change_id_set (sorted).
#
# Split quality:
#   tests_travel  : a change touching src/main/**/*.java also touches a file
#                   under src/test/ (null for changes without main Java code);
#                   tests_travel_pct is the share among the changes that do.
#   --concerns    : reads the `concerns:` list of a case.yaml (items
#                   `{name, paths: [regex, ...]}`, Python regexes searched in
#                   the changed paths; needs python3). Per change: `concerns`
#                   (names with at least one matching path) and
#                   `unmapped_paths` (paths no concern claims; they do not
#                   count). purity_pct = changes with exactly one concern among
#                   changes with at least one mapped path; completeness_pct =
#                   concerns living in exactly one change among the concerns
#                   that appear at all; concerns_seen = those concerns.
#
# Base: --base, else refs/remotes/origin/master, else @{upstream}, else the
# root commit (the chain is then every commit after the root).
# Budget: git config gerrit-stack.budget.lines (150) / .files (8).
# --verify-cmd checks out every change of the chain, one after another, in a
# temporary detached worktree (cleaned with `git clean -fdx` between changes so
# nothing built for one change helps the next) and runs <cmd> there with
# `bash -c`; exit 0 = builds alone. Each run is limited to --verify-timeout
# seconds (default 300; a timeout counts as a failure and in verify_timeouts).
# The worktree is removed afterwards; the caller's checkout, index and HEAD are
# never touched.
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
                        [--verify-timeout <seconds>] [--concerns <case.yaml>]
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
VERIFY_TIMEOUT=300
CONCERNS=""
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
    --verify-timeout)
      [ $# -ge 2 ] || die_usage "--verify-timeout needs a value"
      VERIFY_TIMEOUT=$2; shift 2 ;;
    --verify-timeout=*) VERIFY_TIMEOUT=${1#*=}; shift ;;
    --concerns)
      [ $# -ge 2 ] || die_usage "--concerns needs a value"
      CONCERNS=$2; shift 2 ;;
    --concerns=*) CONCERNS=${1#*=}; shift ;;
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
case "$VERIFY_TIMEOUT" in
  ''|*[!0-9]*|0) die_usage "--verify-timeout needs a positive number of seconds" ;;
esac
# Absolute path of --concerns relative to the caller's cwd, resolved before cd.
if [ -n "$CONCERNS" ]; then
  case "$CONCERNS" in
    /*) ;;
    *) CONCERNS=$PWD/$CONCERNS ;;
  esac
  [ -f "$CONCERNS" ] || die_usage "no such concerns file: $CONCERNS"
  command -v python3 >/dev/null 2>&1 || die_usage "--concerns needs python3 on PATH"
fi
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
# tests_travel (true | false | null), subject (last field, may contain
# TABs/spaces). PATHS holds `position<TAB>path` for every path a change touches.
ROWS=$TMP/rows
: > "$ROWS"
PATHS=$TMP/paths
: > "$PATHS"
MAIN_JAVA_RE='(^|/)src/main/.*\.java$'
TEST_RE='(^|/)src/test/'
cpos=0
CIDS=$TMP/cids
: > "$CIDS"
while IFS= read -r sha; do
  [ -n "$sha" ] || continue
  cpos=$((cpos + 1))
  changed=$(git -c core.quotepath=false diff-tree -r --root --name-only --no-commit-id "$sha")
  travel=null
  if [ -n "$changed" ]; then
    printf '%s\n' "$changed" | awk -v p="$cpos" '{ print p "\t" $0 }' >> "$PATHS"
    if printf '%s\n' "$changed" | grep -Eq "$MAIN_JAVA_RE"; then
      travel=false
      if printf '%s\n' "$changed" | grep -Eq "$TEST_RE"; then travel=true; fi
    fi
  fi
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
  printf '%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\t%s\n' \
    "$sha" "$lines" "$files" "$cids" "$ctype" "$fixup" "$within" "$single" "$travel" "$subject" >> "$ROWS"
done < "$COMMITS"

# ------------------------------------------------------------- verify-cmd ----
# builds file: one line per chain position (1-based): ok | fail | timeout
BUILDS=$TMP/builds
: > "$BUILDS"
VERIFY_ERROR=""
VERIFY_TIMEOUTS=0
TIMEOUT_BIN=""
# CHAIN_METRICS_NO_TIMEOUT_BIN=1 forces the built-in watchdog (used by the tests)
if [ -n "${CHAIN_METRICS_NO_TIMEOUT_BIN:-}" ]; then
  :
elif command -v timeout >/dev/null 2>&1; then
  TIMEOUT_BIN=timeout
elif command -v gtimeout >/dev/null 2>&1; then
  TIMEOUT_BIN=gtimeout
fi

# run_verify <dir> — runs VERIFY_CMD in <dir>, at most VERIFY_TIMEOUT seconds.
# Returns the command's status, or 124 when it was stopped for running too long.
run_verify() {
  local dir=$1 rc pid wd marker=$TMP/timed-out
  rm -f "$marker"
  if [ -n "$TIMEOUT_BIN" ]; then
    (cd "$dir" && "$TIMEOUT_BIN" -k 5 "$VERIFY_TIMEOUT" bash -c "$VERIFY_CMD") </dev/null >/dev/null 2>&1
    rc=$?
    # 124 = stopped by TERM, 137 = needed the follow-up KILL
    if [ "$rc" -eq 137 ]; then rc=124; fi
    return "$rc"
  fi
  # no timeout(1) (stock macOS): background the command and watch it
  (cd "$dir" && exec bash -c "$VERIFY_CMD") </dev/null >/dev/null 2>&1 &
  pid=$!
  (
    waited=0
    while kill -0 "$pid" 2>/dev/null; do
      if [ "$waited" -ge "$VERIFY_TIMEOUT" ]; then
        : > "$marker"
        kill -TERM "$pid" 2>/dev/null
        sleep 2
        kill -KILL "$pid" 2>/dev/null
        break
      fi
      sleep 1
      waited=$((waited + 1))
    done
  ) </dev/null >/dev/null 2>&1 &
  wd=$!
  wait "$pid" 2>/dev/null
  rc=$?
  if [ -e "$marker" ]; then rc=124; else kill "$wd" 2>/dev/null; fi
  wait "$wd" 2>/dev/null
  return "$rc"
}

if [ -n "$VERIFY_CMD" ] && [ "$CHAIN_LENGTH" -gt 0 ]; then
  WT=$TMP/wt
  if git worktree add --detach --quiet "$WT" "$BASE_SHA" >/dev/null 2>&1; then
    while IFS= read -r sha; do
      [ -n "$sha" ] || continue
      if ! git -C "$WT" checkout --quiet --force --detach "$sha" >/dev/null 2>&1; then
        VERIFY_ERROR="cannot check out $sha in the temporary worktree"
        break
      fi
      # whatever the previous change built must not help this one
      git -C "$WT" clean -fdxq >/dev/null 2>&1 || true
      run_verify "$WT"
      case $? in
        0) echo ok >> "$BUILDS" ;;
        124) echo timeout >> "$BUILDS"; VERIFY_TIMEOUTS=$((VERIFY_TIMEOUTS + 1)) ;;
        *) echo fail >> "$BUILDS" ;;
      esac
    done < "$COMMITS"
  else
    VERIFY_ERROR="git worktree add failed"
  fi
  # Drop the temporary worktree right away (cleanup() would too, on EXIT).
  git worktree remove --force "$WT" >/dev/null 2>&1 || true
  rm -rf "$WT"
  git worktree prune >/dev/null 2>&1 || true
  WT=""
fi

# --------------------------------------------------------------- concerns ----
# One python3 call: parse the `concerns:` list of the case.yaml (flow items
# `- {name: x, paths: ['re', ...]}` or the block form) and map every changed
# path. Prints one JSON object; CONCERNS_JSON stays null without --concerns.
CONCERNS_JSON=null
if [ -n "$CONCERNS" ]; then
  # (written to a file first: bash 3.2 cannot parse a here-document with
  # parentheses inside a command substitution)
  cat > "$TMP/concerns.py" <<'PY'
import json
import re
import sys

SCALAR = r"""'((?:[^']|'')*)'|"((?:[^"\\]|\\.)*)"|([^,\s][^,]*)"""


def scalar(match):
    single, double, bare = match.group(1), match.group(2), match.group(3)
    if single is not None:
        return single.replace("''", "'")
    if double is not None:
        try:
            return json.loads('"' + double + '"')
        except ValueError:
            return double
    return re.sub(r"\s+#.*$", "", bare).strip()


def one(text):
    m = re.match(r"\s*(?:%s)" % SCALAR, text)
    return scalar(m) if m else ""


def many(text):
    return [v for v in (scalar(m) for m in re.finditer(SCALAR, text)) if v != ""]


def flow(body):
    item = {"name": "", "paths": []}
    pm = re.search(r"(?:^|[{,\s])paths\s*:\s*\[", body)
    rest = body
    if pm:
        end = body.rfind("]")
        if end >= pm.end():
            item["paths"] = many(body[pm.end():end])
            rest = body[:pm.start()] + body[end + 1:]
    nm = re.search(r"(?:^|[{,\s])name\s*:\s*(.*)", rest)
    if nm:
        item["name"] = one(re.sub(r"[},\s]+$", "", nm.group(1)))
    return item


def parse(path):
    items, cur, inside, in_paths = [], None, False, False
    with open(path, encoding="utf-8", errors="replace") as fh:
        for raw in fh:
            line = raw.rstrip("\r\n")
            text = line.strip()
            if not text or text.startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            if not inside:
                m = re.match(r"concerns\s*:(.*)$", line)
                if m:
                    inside = True
                    rest = m.group(1).strip()
                    if rest.startswith("["):
                        for part in re.findall(r"\{.*?\]\s*\}", rest):
                            items.append(flow(part))
                        break
                continue
            if indent == 0 and not text.startswith("-"):
                break
            if text.startswith("-"):
                body = text[1:].strip()
                if in_paths and cur is not None and indent > cur["indent"]:
                    cur["paths"].extend(many(body))
                    continue
                in_paths = False
                if body.startswith("{"):
                    end = body.rfind("}")
                    cur = flow(body[: end + 1] if end > 0 else body)
                    cur["indent"] = indent
                    items.append(cur)
                    continue
                cur = {"name": "", "paths": [], "indent": indent}
                items.append(cur)
                text = body
            if cur is None:
                continue
            m = re.match(r"(name|paths)\s*:(.*)$", text)
            if not m:
                continue
            key, value = m.group(1), m.group(2).strip()
            if key == "name":
                cur["name"] = one(value)
                in_paths = False
            elif value.startswith("["):
                end = value.rfind("]")
                cur["paths"] = many(value[1:end] if end > 0 else value[1:])
                in_paths = False
            else:
                in_paths = True
    return [i for i in items if i["name"]]


def pct(count, total):
    if not total:
        return None
    value = round(100.0 * count / total, 1)
    return int(value) if value == int(value) else value


concerns, invalid = [], []
for item in parse(sys.argv[1]):
    compiled = []
    for pattern in item["paths"]:
        try:
            compiled.append(re.compile(pattern))
        except re.error:
            invalid.append(pattern)
    concerns.append((item["name"], compiled))
order = [name for name, _ in concerns]

length = int(sys.argv[3])
paths = {}
with open(sys.argv[2], encoding="utf-8", errors="replace") as fh:
    for raw in fh:
        pos, _, path = raw.rstrip("\n").partition("\t")
        if path:
            paths.setdefault(int(pos), []).append(path)

changes, holders = {}, {}
for pos in range(1, length + 1):
    names, unmapped = [], []
    for path in paths.get(pos, []):
        hit = [name for name, regexes in concerns if any(r.search(path) for r in regexes)]
        if not hit:
            unmapped.append(path)
        for name in hit:
            if name not in names:
                names.append(name)
    names.sort(key=order.index)
    for name in names:
        holders[name] = holders.get(name, 0) + 1
    changes[str(pos)] = {"concerns": names, "unmapped_paths": unmapped, "paths": sorted(paths.get(pos, []))}

mapped = [c for c in changes.values() if c["concerns"]]
seen = [name for name in order if name in holders]
print(json.dumps({
    "file": sys.argv[1],
    "defined": order,
    "invalid_patterns": invalid,
    "changes": changes,
    "concerns_seen": seen,
    "purity_pct": pct(sum(1 for c in mapped if len(c["concerns"]) == 1), len(mapped)),
    "completeness_pct": pct(sum(1 for name in seen if holders[name] == 1), len(seen)),
}))
PY
  CONCERNS_JSON=$(python3 "$TMP/concerns.py" "$CONCERNS" "$PATHS" "$CHAIN_LENGTH") || CONCERNS_JSON=""
  if [ -z "$CONCERNS_JSON" ] || ! printf '%s' "$CONCERNS_JSON" | jq -e . >/dev/null 2>&1; then
    die_usage "cannot read the concerns of $CONCERNS"
  fi
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
TRAVEL_PCT=$(awk -F'\t' '$9 == "true" { t++ } $9 == "true" || $9 == "false" { n++ } END {
  if (n == 0) { print "null"; exit }
  v = 100 * t / n
  if (v == int(v)) printf "%d\n", v; else printf "%.1f\n", v
}' "$ROWS")
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
while IFS=$'\t' read -r sha lines files cids ctype fixup within single travel subject; do
  pos=$((pos + 1))
  builds=null
  if [ -s "$BUILDS" ] && [ -z "$VERIFY_ERROR" ]; then
    case "$(sed -n "${pos}p" "$BUILDS")" in
      ok) builds=true ;;
      fail|timeout) builds=false ;;
    esac
  fi
  if [ "$ctype" != "-" ]; then ctype_json="\"$ctype\""; else ctype_json=null; fi
  jq -cn --arg sha "$sha" --arg subject "$subject" --argjson lines "$lines" --argjson files "$files" \
    --argjson cids "$cids" --argjson ctype "$ctype_json" --argjson fixup "$fixup" \
    --argjson within "$within" --argjson single "$single" --argjson builds "$builds" \
    --argjson travel "$travel" '
    {sha: $sha, subject: $subject, lines: $lines, files: $files, change_ids: $cids,
     conventional_type: $ctype, is_fixup: $fixup, within_budget: $within,
     single_concern: $single, builds_alone: $builds, tests_travel: $travel}' >> "$CHANGES"
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
  --argjson verify_timeout "$VERIFY_TIMEOUT" --argjson verify_timeouts "$VERIFY_TIMEOUTS" \
  --argjson travel "$TRAVEL_PCT" --argjson conc "$CONCERNS_JSON" \
  --argjson remote "$REMOTE_JSON" --argjson hook "$HOOK_JSON" --arg cid_set "$CID_SET" '
  {
    repo: $repo,
    head: (if $head == "" then null else $head end),
    base: (if $base == "" then null else $base end),
    base_ref: (if $base_ref == "" then null else $base_ref end),
    budget: {lines: $budget_lines, files: $budget_files},
    chain_length: $chain_length,
    changes: [$changes | to_entries[] | .value + (
      if $conc == null then {concerns: null, unmapped_paths: null, paths: null}
      else ($conc.changes[(.key + 1) | tostring] // {concerns: [], unmapped_paths: [], paths: []}) end)],
    lines_median: $lines_median,
    lines_p75: $lines_p75,
    lines_max: $lines_max,
    files_median: $files_median,
    within_budget_pct: $within,
    one_change_id_pct: $one_cid,
    conventional_pct: $conv,
    single_concern_pct: $single,
    fixups_present: $fixups,
    tests_travel_pct: $travel,
    concerns_file: (if $conc == null then null else $conc.file end),
    concerns_defined: (if $conc == null then null else $conc.defined end),
    concerns_seen: (if $conc == null then null else $conc.concerns_seen end),
    purity_pct: (if $conc == null then null else $conc.purity_pct end),
    completeness_pct: (if $conc == null then null else $conc.completeness_pct end),
    verify_cmd: (if $verify_cmd == "" then null else $verify_cmd end),
    verify_timeout: (if $verify_cmd == "" then null else $verify_timeout end),
    verify_timeouts: (if $verify_cmd == "" then null else $verify_timeouts end),
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
  "sha      cid  lines files type      concern budget build tests  concerns  subject",
  (.changes[] | "\(.sha[0:7])  \(.change_ids)    \(.lines | tostring | .[0:5] | . + " " * (5 - length))  \(.files | tostring | . + " " * (5 - length)) \(s(.conventional_type) | . + " " * (9 - length)) \(yn(.single_concern) | . + " " * (7 - length)) \(yn(.within_budget) | . + " " * (6 - length)) \(yn(.builds_alone) | . + " " * (5 - length)) \(yn(.tests_travel) | . + " " * (5 - length))  \(if .concerns == null then "-" elif (.concerns | length) == 0 then "(none)" else (.concerns | join(",")) end)  \(.subject)"),
  "lines: median \(s(.lines_median))  p75 \(s(.lines_p75))  max \(s(.lines_max))  |  files: median \(s(.files_median))",
  "within budget \(pct(.within_budget_pct))  |  one Change-Id \(pct(.one_change_id_pct))  |  conventional \(pct(.conventional_pct))  |  single concern \(pct(.single_concern_pct))  |  fixups \(yn(.fixups_present))",
  "tests travel \(pct(.tests_travel_pct))\(if .concerns_seen != null then "  |  purity \(pct(.purity_pct))  |  completeness \(pct(.completeness_pct))  |  concerns seen \(.concerns_seen | length)/\(.concerns_defined | length)  |  unmapped paths \([.changes[].unmapped_paths | length] | add // 0)" else "" end)",
  (if .verify_cmd != null then "builds alone: \(pct(.builds_alone_pct))  (\(.verify_cmd))\(if .verify_timeouts > 0 then "  timeouts: \(.verify_timeouts)" else "" end)\(if .verify_error != null then "  error: " + .verify_error else "" end)" else empty end),
  (if .hook_trace != null then "hook trace: \(.hook_trace.violations) deny \(.hook_trace.violations_by_verb | tojson)  \(.hook_trace.asks) ask \(.hook_trace.asks_by_verb | tojson)  (\(.hook_trace.lines) lines\(if .hook_trace.missing then ", file missing" else "" end))" else empty end),
  (if .remote != null then "refs/for pushed: \(s(.refs_for_pushed))  (\(.remote.name) -> \(.remote.url)\(if .remote.local then "" else ", not local" end))" else empty end),
  "change-ids: \(if (.change_id_set | length) == 0 then "(none)" else (.change_id_set | join(" ")) end)"
'
exit 0
