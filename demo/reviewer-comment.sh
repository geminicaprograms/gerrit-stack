#!/usr/bin/env bash
# gerrit-stack demo: the "reviewer" half of the stage script. Posts, as rena,
# a Code-Review -1 with one unresolved inline comment on a change.
#
# Usage: bash demo/reviewer-comment.sh <change-number> [--file <path>] [--line <n>] [--text <t>]
#   --file  path in the change's current revision (default: first .java under src/main)
#   --line  line number (default 1)
#   --text  comment text (default: a Conventional Comments "issue (blocking)")
#
# Auth: rena's token from demo/work/.rena-token (written by seed.sh), i.e. the
# equivalent of  curl -u rena:$(cat demo/work/.rena-token) …  without the token
# showing up in the process list. Needs curl + jq.
set -uo pipefail

GERRIT_URL=${GERRIT_URL:-http://localhost:8080}
GERRIT_URL=${GERRIT_URL%/}
demo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
token_file=$demo_dir/work/.rena-token
default_text='issue (blocking): the config value is read on every request; cache it in the @Singleton (see DemoPluginConfig) and add a unit test for the default.'

usage() { sed -n '2,12p' "$0" >&2; exit 2; }
die() { printf 'reviewer-comment.sh: %s\n' "$*" >&2; exit 1; }

change=
file=
line=1
text=$default_text
while [ $# -gt 0 ]; do
  case $1 in
    --file) [ $# -ge 2 ] || usage; file=$2; shift 2 ;;
    --line) [ $# -ge 2 ] || usage; line=$2; shift 2 ;;
    --text) [ $# -ge 2 ] || usage; text=$2; shift 2 ;;
    -h|--help) usage ;;
    -*) usage ;;
    *) [ -z "$change" ] || usage; change=$1; shift ;;
  esac
done
[ -n "$change" ] || usage
case $change in *[!0-9]*|'') die "change number must be numeric: $change" ;; esac
case $line in *[!0-9]*|'') die "--line must be numeric: $line" ;; esac
for tool in curl jq; do command -v "$tool" >/dev/null 2>&1 || die "$tool not found on PATH"; done
[ -s "$token_file" ] || die "no $token_file — run: bash $demo_dir/seed.sh"

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/reviewer-comment.XXXXXX")
trap 'rm -rf "$tmpdir"' EXIT
curlrc=$tmpdir/curlrc
(umask 077 && printf 'user = "rena:%s"\n' "$(cat "$token_file")" > "$curlrc")

# rest <METHOD> <path> [json] -> REST_CODE, REST_BODY (XSSI prefix stripped)
REST_CODE=000
REST_BODY=
rest() {
  local method=$1 path=$2 body=${3:-}
  local out=$tmpdir/body
  local args=(-sS -K "$curlrc" -o "$out" -w '%{http_code}' -X "$method")
  [ -z "$body" ] || args+=(-H 'Content-Type: application/json' --data "$body")
  : > "$out"
  REST_CODE=$(curl "${args[@]}" "$GERRIT_URL$path" 2>"$tmpdir/curl.err") || REST_CODE=000
  REST_BODY=$(sed "1s/^)]}'\$//" "$out")
}

rest GET "/a/changes/$change"
[ "$REST_CODE" = 200 ] || die "GET /a/changes/$change -> HTTP $REST_CODE (does the change exist? is rena's token valid? re-run seed.sh)"
project=$(printf '%s' "$REST_BODY" | jq -r .project)
subject=$(printf '%s' "$REST_BODY" | jq -r .subject)

if [ -z "$file" ]; then
  rest GET "/a/changes/$change/revisions/current/files"
  [ "$REST_CODE" = 200 ] || die "GET /a/changes/$change/revisions/current/files -> HTTP $REST_CODE"
  file=$(printf '%s' "$REST_BODY" | jq -r '
    keys | map(select(startswith("/") | not)) | sort
    | (map(select(test("^src/main/.*\\.java$"))) + map(select(endswith(".java"))) + .)
    | .[0] // empty')
  [ -n "$file" ] || die "change $change has no files to comment on (pass --file)"
fi

review=$(jq -n --arg file "$file" --argjson line "$line" --arg text "$text" '{
  labels: {"Code-Review": -1},
  message: "Reviewed as rena",
  comments: { ($file): [ { line: $line, unresolved: true, message: $text } ] }
}')
rest POST "/a/changes/$change/revisions/current/review" "$review"
if [ "$REST_CODE" != 200 ]; then
  printf 'reviewer-comment.sh: POST /a/changes/%s/revisions/current/review -> HTTP %s\n%s\n' "$change" "$REST_CODE" "$REST_BODY" >&2
  # shellcheck disable=SC2016  # the $(cat …) is meant for the user's shell
  printf 'manual fallback:\n  curl -u rena:$(cat %s) -X POST -H "Content-Type: application/json" -d %s %s/a/changes/%s/revisions/current/review\n' \
    "$token_file" "'$(printf '%s' "$review" | jq -c .)'" "$GERRIT_URL" "$change" >&2
  exit 1
fi
printf 'rena: Code-Review -1 + unresolved comment on %s:%s of change %s (%s)\n' "$file" "$line" "$change" "$subject"
printf '%s/c/%s/+/%s\n' "$GERRIT_URL" "$project" "$change"
