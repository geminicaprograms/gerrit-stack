#!/usr/bin/env bash
# Apply (or check/revert) the local gerrit-mcp patch that keeps an explicit http://
# host instead of rewriting it to https:// — needed for the plain-HTTP demo Gerrit.
# Idempotent. Re-run after `claude plugin update gerrit@gerrit-mcp`. Upstream fix:
# https://gerrit-review.googlesource.com/c/gerrit-mcp-server/+/635805 (drop this once merged).
# Usage: bash demo/patch-gerrit-mcp.sh [--check] [--revert]
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)
patch_file="$here/patches/gerrit-mcp-keep-http.patch"
root=$(find "$HOME/.claude/plugins/cache/gerrit-mcp/gerrit" -mindepth 1 -maxdepth 1 -type d 2>/dev/null | sort | tail -1)
if [ -z "$root" ]; then
  echo "gerrit-mcp not installed (claude plugin install gerrit@gerrit-mcp)"; exit 1
fi
target="$root/gerrit_mcp_server/main.py"
applied=0
if grep -q 'keep an explicit http://' "$target"; then applied=1; fi
case "${1:-apply}" in
  --check)
    if [ "$applied" = 1 ]; then echo "patched: $target"; exit 0; fi
    echo "NOT patched: $target"; exit 1 ;;
  --revert)
    if [ "$applied" = 0 ]; then echo "not patched; nothing to revert"; exit 0; fi
    if (cd "$root" && patch -R -p0 --silent < "$patch_file"); then echo "reverted: $target"; else echo "revert failed"; exit 1; fi ;;
  *)
    if [ "$applied" = 1 ]; then echo "already patched: $target"; exit 0; fi
    if (cd "$root" && patch -p0 --silent --no-backup-if-mismatch < "$patch_file"); then
      echo "patched: $target"
    else
      echo "patch failed — upstream layout changed? see $patch_file"; exit 1
    fi ;;
esac
