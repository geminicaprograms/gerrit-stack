#!/usr/bin/env bash
# gerrit-stack demo: wire a Gerrit source tree up for in-tree builds of demo-plugin.
#
# Usage: demo/gerrit-tree.sh <gerrit-tree> [plugin-dir]
#   plugin-dir defaults to demo/work/demo-plugin (if it exists) else demo/skeleton.
#
# Idempotent. Only touches <gerrit-tree>/plugins/demo-plugin (symlink),
# <gerrit-tree>/user.bazelrc (a managed block) and ~/.cache/gerrit-stack-demo/.
set -uo pipefail

demo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
cache_dir=$HOME/.cache/gerrit-stack-demo
begin_marker='# >>> gerrit-stack demo (managed by demo/gerrit-tree.sh) >>>'
end_marker='# <<< gerrit-stack demo <<<'

if [ $# -lt 1 ] || [ $# -gt 2 ]; then
  echo "usage: $0 <gerrit-tree> [plugin-dir]" >&2
  exit 2
fi

tree=$(cd "$1" 2>/dev/null && pwd -P) || { echo "gerrit-tree.sh: no such directory: $1" >&2; exit 2; }
if [ ! -f "$tree/plugins/BUILD" ] || [ ! -f "$tree/.bazelversion" ]; then
  echo "gerrit-tree.sh: $tree does not look like a Gerrit source tree (plugins/BUILD, .bazelversion)" >&2
  exit 2
fi

if [ $# -eq 2 ]; then
  plugin_dir=$(cd "$2" 2>/dev/null && pwd -P) || { echo "gerrit-tree.sh: no such directory: $2" >&2; exit 2; }
elif [ -d "$demo_dir/work/demo-plugin" ]; then
  plugin_dir=$demo_dir/work/demo-plugin
else
  plugin_dir=$demo_dir/skeleton
fi
if [ ! -f "$plugin_dir/BUILD" ]; then
  echo "gerrit-tree.sh: $plugin_dir has no BUILD file" >&2
  exit 2
fi

# 1. plugins/demo-plugin symlink
link=$tree/plugins/demo-plugin
if [ -L "$link" ]; then
  current=$(cd "$link" 2>/dev/null && pwd -P) || current=""
  if [ "$current" = "$plugin_dir" ]; then
    echo "symlink  : $link -> $plugin_dir (unchanged)"
  else
    rm -f "$link" && ln -s "$plugin_dir" "$link"
    echo "symlink  : $link -> $plugin_dir (was: ${current:-dangling})"
  fi
elif [ -e "$link" ]; then
  echo "gerrit-tree.sh: $link exists and is not a symlink; remove it first" >&2
  exit 1
else
  ln -s "$plugin_dir" "$link"
  echo "symlink  : $link -> $plugin_dir (created)"
fi

# 2. caches under ~/.cache/gerrit-stack-demo (repo cache seeded from an existing Gerrit cache)
mkdir -p "$cache_dir/bazel-disk"
gerrit_repo_cache=$HOME/.gerritcodereview/bazel-cache/repository
if [ ! -d "$cache_dir/bazel-repo" ] && [ -d "$gerrit_repo_cache" ]; then
  if cp -Rc "$gerrit_repo_cache" "$cache_dir/bazel-repo" 2>/dev/null \
      || cp -R "$gerrit_repo_cache" "$cache_dir/bazel-repo"; then
    echo "repocache: seeded $cache_dir/bazel-repo from $gerrit_repo_cache"
  else
    rm -rf "$cache_dir/bazel-repo"
    echo "repocache: seeding failed; starting empty" >&2
  fi
fi
mkdir -p "$cache_dir/bazel-repo"

# 3. user.bazelrc managed block (Gerrit's .bazelrc has try-import %workspace%/user.bazelrc)
rc=$tree/user.bazelrc
tmp=$(mktemp "$tree/user.bazelrc.XXXXXX")
trap 'rm -f "$tmp"' EXIT
if [ -f "$rc" ]; then
  awk -v b="$begin_marker" -v e="$end_marker" '
    $0 == b { skip = 1; next }
    $0 == e { skip = 0; next }
    !skip { print }
  ' "$rc" > "$tmp"
fi
{
  echo "$begin_marker"
  echo "build --disk_cache=~/.cache/gerrit-stack-demo/bazel-disk"
  echo "build --repository_cache=~/.cache/gerrit-stack-demo/bazel-repo"
  echo "$end_marker"
} >> "$tmp"
if [ -f "$rc" ] && cmp -s "$rc" "$tmp"; then
  rm -f "$tmp"
  echo "bazelrc  : $rc (unchanged)"
else
  mv "$tmp" "$rc"
  echo "bazelrc  : $rc (disk_cache + repository_cache under $cache_dir)"
fi

# 4. remember the tree in the plugin clone's git config (only when plugin-dir is its own repo)
toplevel=$(git -C "$plugin_dir" rev-parse --show-toplevel 2>/dev/null || true)
if [ -n "$toplevel" ] && [ "$(cd "$toplevel" && pwd -P)" = "$plugin_dir" ]; then
  git -C "$plugin_dir" config gerrit-stack.gerrit-tree "$tree"
  echo "gitconfig: gerrit-stack.gerrit-tree=$tree in $plugin_dir"
else
  echo "gitconfig: skipped ($plugin_dir is not the top level of a git repo; verify.sh falls back to GERRIT_TREE or ~/workspace/open/gerrit-3.14)"
fi

echo "next     : bash $plugin_dir/tools/verify.sh   (or: bash $demo_dir/warm-bazel.sh $tree)"
