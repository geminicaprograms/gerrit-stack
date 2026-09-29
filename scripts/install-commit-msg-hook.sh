#!/usr/bin/env bash
# scripts/install-commit-msg-hook.sh — install Gerrit's commit-msg hook.
#
# Usage: install-commit-msg-hook.sh [--host <url>] [--from <file>] [--force]
#
# Destination: $(git rev-parse --git-path hooks)/commit-msg of the current
# repository (worktree- and core.hooksPath-safe; the directory is created).
# Source: the `--from` file, else `curl -fsSL <host>/tools/hooks/commit-msg`
# where <host> is `--host`, else `git config gerrit-stack.host`, else the
# http(s) host derived from the Gerrit remote URL (`file:///…` hosts work too).
#
# An existing, different hook is left alone unless `--force`. After
# installing (`chmod +x`) the hook is self-tested on temporary message files:
# `subject\n\nbody\n` must gain a `Change-Id:` line and `fixup! x` must not.
# On success the absolute hook path is printed; every failure prints its
# reason on stderr and exits 1.
#
# The only script in this plugin allowed to touch the network (that one curl).
set -uo pipefail

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

usage() {
  printf 'usage: install-commit-msg-hook.sh [--host <url>] [--from <file>] [--force]\n'
}

die() {
  printf 'install-commit-msg-hook: %s\n' "$*" >&2
  exit 1
}

opt_host='' opt_from='' force=0
while [ $# -gt 0 ]; do
  case "$1" in
    --host|--from)
      if [ $# -lt 2 ] || [ -z "$2" ]; then
        printf 'install-commit-msg-hook: %s needs a value\n' "$1" >&2
        usage >&2
        exit 1
      fi
      case "$1" in
        --host) opt_host=$2 ;;
        --from) opt_from=$2 ;;
      esac
      shift ;;
    --host=*) opt_host=${1#*=} ;;
    --from=*) opt_from=${1#*=} ;;
    --force) force=1 ;;
    -h|--help) usage; exit 0 ;;
    *)
      printf 'install-commit-msg-hook: unknown option: %s\n' "$1" >&2
      usage >&2
      exit 1 ;;
  esac
  shift
done

top=$(git rev-parse --show-toplevel 2>/dev/null) || die "not inside a git repository"
[ -n "$top" ] || die "not inside a git repository"

hooks=$(git rev-parse --git-path hooks 2>/dev/null) || die "cannot resolve the hooks directory"
case "$hooks" in /*) ;; *) hooks="$PWD/$hooks" ;; esac
mkdir -p "$hooks" 2>/dev/null || die "cannot create $hooks"
hooks=$(cd "$hooks" && pwd -P) || die "cannot enter $hooks"
dest="$hooks/commit-msg"

tmpdir=$(mktemp -d "${TMPDIR:-/tmp}/gerrit-stack-hook.XXXXXX") || die "cannot create a temp dir"
trap 'rm -rf "$tmpdir"' EXIT

# ---------------------------------------------------------------- source

src=''
if [ -n "$opt_from" ]; then
  [ -f "$opt_from" ] || die "--from file not found: $opt_from"
  src=$opt_from
else
  host=$opt_host
  if [ -z "$host" ]; then
    if gs_detect; then host=$GS_HOST; fi
  fi
  while [ -n "$host" ] && [ "${host%/}" != "$host" ]; do host=${host%/}; done
  [ -n "$host" ] || die "no Gerrit host URL known: pass --host <http(s)://gerrit> (or --from <file>), or set git config gerrit-stack.host <url>"
  command -v curl >/dev/null 2>&1 || die "curl not found; download $host/tools/hooks/commit-msg yourself and pass --from <file>"
  src="$tmpdir/commit-msg"
  if ! curl -fsSL "$host/tools/hooks/commit-msg" -o "$src" 2>"$tmpdir/curl.err"; then
    die "download failed: $host/tools/hooks/commit-msg ($(tr '\n' ' ' < "$tmpdir/curl.err"))"
  fi
fi
[ -s "$src" ] || die "source hook is empty: $src"
grep -q 'Change-Id' "$src" 2>/dev/null || die "source does not look like Gerrit's commit-msg hook (no Change-Id in $src)"

# ---------------------------------------------------------------- install

if [ -e "$dest" ] && ! cmp -s "$src" "$dest"; then
  if [ "$force" != 1 ]; then
    die "refusing to overwrite the existing, different hook $dest (re-run with --force to replace it)"
  fi
fi
if [ ! -e "$dest" ] || ! cmp -s "$src" "$dest"; then
  cat "$src" > "$dest" 2>/dev/null || die "cannot write $dest"
fi
chmod +x "$dest" 2>/dev/null || die "cannot chmod +x $dest"

# ---------------------------------------------------------------- self-test

msg="$tmpdir/msg"
printf 'subject\n\nbody\n' > "$msg"
if ! (cd "$top" && "$dest" "$msg") >"$tmpdir/hook.out" 2>&1; then
  die "self-test failed: the hook exited non-zero on a plain message ($(tr '\n' ' ' < "$tmpdir/hook.out"))"
fi
grep -q '^Change-Id: I[0-9a-f]\{40\}$' "$msg" || die "self-test failed: the hook did not add a Change-Id line to a plain message"

create=$(git -C "$top" config --get gerrit.createChangeId 2>/dev/null) || create=''
if [ "$create" != always ]; then
  fix="$tmpdir/fixup"
  printf 'fixup! x\n\nbody\n' > "$fix"
  (cd "$top" && "$dest" "$fix") >/dev/null 2>&1 || true
  if grep -q '^Change-Id:' "$fix"; then
    die "self-test failed: the hook added a Change-Id to a fixup! message"
  fi
fi

printf '%s\n' "$dest"
gs_trace install-commit-msg-hook install ok
exit 0
