#!/usr/bin/env bash
# gerrit-stack demo: in-tree verify command for demo-plugin.
#
# Builds and tests the plugin inside a Gerrit source tree (Bazel), resolved from
#   1. $GERRIT_TREE
#   2. git config gerrit-stack.gerrit-tree   (set by demo/gerrit-tree.sh)
#   3. ~/workspace/open/gerrit-3.14
# The tree's plugins/demo-plugin must be a symlink to this plugin directory
# (demo/gerrit-tree.sh creates it). Exit code = Bazel's; 2 = setup problem.
set -uo pipefail

tools_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
plugin_dir=$(cd "$tools_dir/.." && pwd -P)

tree=${GERRIT_TREE:-}
if [ -z "$tree" ]; then
  tree=$(git -C "$plugin_dir" config --get gerrit-stack.gerrit-tree 2>/dev/null || true)
fi
if [ -z "$tree" ]; then
  tree=$HOME/workspace/open/gerrit-3.14
fi
if [ ! -f "$tree/plugins/BUILD" ]; then
  echo "verify.sh: not a Gerrit tree: $tree (set GERRIT_TREE or run demo/gerrit-tree.sh)" >&2
  exit 2
fi

link=$tree/plugins/demo-plugin
if [ ! -d "$link" ]; then
  echo "verify.sh: $link is missing; run: bash demo/gerrit-tree.sh $tree $plugin_dir" >&2
  exit 2
fi
linked_to=$(cd "$link" && pwd -P)
if [ "$linked_to" != "$plugin_dir" ]; then
  echo "verify.sh: $link points to $linked_to, not to $plugin_dir; run: bash demo/gerrit-tree.sh $tree $plugin_dir" >&2
  exit 2
fi

bazel=bazelisk
command -v bazelisk >/dev/null 2>&1 || bazel=bazel
if ! command -v "$bazel" >/dev/null 2>&1; then
  echo "verify.sh: neither bazelisk nor bazel found on PATH" >&2
  exit 2
fi

echo "verify.sh: $bazel in $tree (plugins/demo-plugin -> $plugin_dir)"
SECONDS=0
(
  cd "$tree" || exit 2
  "$bazel" build plugins/demo-plugin \
    && "$bazel" test plugins/demo-plugin:demo_plugin_tests
)
rc=$?
echo "verify.sh: exit $rc after ${SECONDS}s"
exit "$rc"
