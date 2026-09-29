#!/usr/bin/env bash
# gerrit-stack demo: 5-second fallback verify command for demo-plugin.
#
# Compiles src/main/java with javac against gerrit-plugin-api-3.14.4.jar from
# Maven Central (downloaded once into ~/.cache/gerrit-stack-demo/).
# Exit code = javac's; 2 = setup problem (no javac / download failed).
set -uo pipefail

API_VERSION=3.14.4
CACHE_DIR=${GERRIT_STACK_DEMO_CACHE:-$HOME/.cache/gerrit-stack-demo}
JAR=$CACHE_DIR/gerrit-plugin-api-$API_VERSION.jar
URL="https://repo1.maven.org/maven2/com/google/gerrit/gerrit-plugin-api/$API_VERSION/gerrit-plugin-api-$API_VERSION.jar"

tools_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
plugin_dir=$(cd "$tools_dir/.." && pwd -P)

SECONDS=0
if ! command -v javac >/dev/null 2>&1; then
  echo "quick-check.sh: javac not found on PATH (JDK 21+ required)" >&2
  exit 2
fi

mkdir -p "$CACHE_DIR"
if [ ! -s "$JAR" ]; then
  echo "quick-check.sh: downloading $URL"
  if ! curl -fsSL --retry 2 -o "$JAR.tmp" "$URL"; then
    rm -f "$JAR.tmp"
    echo "quick-check.sh: download failed" >&2
    exit 2
  fi
  mv "$JAR.tmp" "$JAR"
fi

out=$(mktemp -d "${TMPDIR:-/tmp}/demo-plugin-quick-check.XXXXXX")
trap 'rm -rf "$out"' EXIT

find "$plugin_dir/src/main/java" -name '*.java' > "$out/sources.txt"
if [ ! -s "$out/sources.txt" ]; then
  echo "quick-check.sh: no sources under src/main/java" >&2
  exit 2
fi

javac --release 21 -Xlint:-options -proc:none -d "$out/classes" -cp "$JAR" @"$out/sources.txt"
rc=$?
echo "quick-check.sh: exit $rc after ${SECONDS}s ($(wc -l < "$out/sources.txt" | tr -d ' ') files)"
exit "$rc"
