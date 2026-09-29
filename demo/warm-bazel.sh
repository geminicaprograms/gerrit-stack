#!/usr/bin/env bash
# gerrit-stack demo: warm the in-tree Bazel build of demo-plugin.
#
# Usage: demo/warm-bazel.sh [gerrit-tree]
# Runs the plugin's tools/verify.sh twice and prints both timings. Exits 1 when
# the second (warm) run takes longer than 20 s, because then the stage should
# fall back to tools/quick-check.sh (git config gerrit-stack.verify-cmd).
set -uo pipefail

demo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
limit=${WARM_LIMIT_SECONDS:-20}

if [ $# -ge 1 ]; then
  GERRIT_TREE=$(cd "$1" 2>/dev/null && pwd -P) || { echo "warm-bazel.sh: no such directory: $1" >&2; exit 2; }
  export GERRIT_TREE
fi
tree=${GERRIT_TREE:-$HOME/workspace/open/gerrit-3.14}

link=$tree/plugins/demo-plugin
if [ ! -d "$link" ]; then
  echo "warm-bazel.sh: $link missing; run: bash $demo_dir/gerrit-tree.sh $tree" >&2
  exit 2
fi
plugin_dir=$(cd "$link" && pwd -P)
verify=$plugin_dir/tools/verify.sh
if [ ! -f "$verify" ]; then
  echo "warm-bazel.sh: $verify not found" >&2
  exit 2
fi

run() {
  local label=$1 rc
  echo "=== warm-bazel: $label run ==="
  SECONDS=0
  bash "$verify"
  rc=$?
  eval "$2=\$SECONDS"
  echo "=== warm-bazel: $label run finished: exit $rc, ${SECONDS}s ==="
  return "$rc"
}

first=0
second=0
run "first (cold)" first || { echo "warm-bazel.sh: first run failed" >&2; exit 1; }
run "second (warm)" second || { echo "warm-bazel.sh: second run failed" >&2; exit 1; }

echo "warm-bazel: first run ${first}s, second run ${second}s (limit ${limit}s)"
if [ "$second" -gt "$limit" ]; then
  echo "warm-bazel: second run exceeded ${limit}s -> use the 5-second fallback on stage:" >&2
  echo "            git config gerrit-stack.verify-cmd 'bash tools/quick-check.sh'" >&2
  exit 1
fi
echo "warm-bazel: OK, Bazel is warm enough for the stage (verify-cmd may use tools/verify.sh)"
