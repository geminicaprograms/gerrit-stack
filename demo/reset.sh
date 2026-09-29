#!/usr/bin/env bash
# gerrit-stack demo: back to a clean slate (between rehearsals, or when the demo Gerrit is in a bad state).
#
# Usage: bash demo/reset.sh [--netrc]
#   --netrc  also drop the localhost / 127.0.0.1 entries from ~/.netrc
#
# Removes the container + named volumes (docker compose down -v), demo/work/
# (clone + rena token), the files Gerrit's first-start init dropped into
# demo/etc/ (secure.config, ssh host keys, jgit.config, mail/ ...) and the
# keys it appended to demo/etc/gerrit.config. Keeps demo/etc/gerrit.config and
# demo/etc/.gitkeep. Prints what it removed. Afterwards: make demo-up demo-seed
set -uo pipefail

demo_dir=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
repo_dir=$(cd "$demo_dir/.." && pwd -P)
compose=$demo_dir/docker-compose.yml
netrc=$HOME/.netrc
netrc_backup=$HOME/.netrc.gerrit-stack.bak
drop_netrc=0
for arg in "$@"; do
  case $arg in
    --netrc) drop_netrc=1 ;;
    -h|--help) sed -n '2,12p' "$0"; exit 0 ;;
    *) echo "reset.sh: unknown flag: $arg" >&2; exit 2 ;;
  esac
done

removed=()
warn() { printf 'reset.sh: %s\n' "$*" >&2; }

# 1. container + volumes
if command -v docker >/dev/null 2>&1; then
  if docker compose -f "$compose" down -v --remove-orphans >/dev/null 2>&1; then
    removed+=("container gerrit-stack-demo, its network and volumes gerrit-git/index/cache/db (docker compose down -v)")
  else
    warn "docker compose down -v failed (daemon not running?) — run: docker compose -f $compose down -v"
  fi
else
  warn "docker not found; skipping compose down"
fi

# 2. working clone + rena token
if [ -e "$demo_dir/work" ]; then
  rm -rf "$demo_dir/work"
  removed+=("demo/work/ (clone demo-plugin, .rena-token)")
fi

# 3. init artefacts in demo/etc (everything but gerrit.config and .gitkeep)
etc_removed=()
while IFS= read -r entry; do
  [ -n "$entry" ] || continue
  rm -rf "$entry"
  etc_removed+=("$(basename "$entry")")
done < <(find "$demo_dir/etc" -mindepth 1 -maxdepth 1 ! -name gerrit.config ! -name .gitkeep 2>/dev/null)
if [ "${#etc_removed[@]}" -gt 0 ]; then
  removed+=("demo/etc/: ${etc_removed[*]}")
fi

# 4. keys the entrypoint's init appended to demo/etc/gerrit.config
cfg=$demo_dir/etc/gerrit.config
if [ -f "$cfg" ]; then
  pristine=$(mktemp "${TMPDIR:-/tmp}/gerrit.config.XXXXXX")
  if git -C "$repo_dir" show HEAD:demo/etc/gerrit.config > "$pristine" 2>/dev/null && [ -s "$pristine" ]; then
    if ! cmp -s "$pristine" "$cfg"; then
      cp -f "$pristine" "$cfg"
      removed+=("init additions in demo/etc/gerrit.config (restored from git HEAD)")
    fi
  else
    # not committed yet: strip the known init additions in place
    if git config -f "$cfg" --get gerrit.serverId >/dev/null 2>&1 \
        || git config -f "$cfg" --get container.javaHome >/dev/null 2>&1; then
      git config -f "$cfg" --unset gerrit.serverId 2>/dev/null
      git config -f "$cfg" --unset container.javaHome 2>/dev/null
      git config -f "$cfg" --unset sendemail.smtpServer 2>/dev/null
      git config -f "$cfg" --unset-all container.javaOptions 2>/dev/null
      git config -f "$cfg" --add container.javaOptions '-Djava.security.egd=file:/dev/./urandom'
      removed+=("init additions in demo/etc/gerrit.config (serverId, javaHome, smtpServer, extra javaOptions)")
    fi
  fi
  rm -f "$pristine"
fi

# 5. ~/.netrc entries (opt-in)
if [ "$drop_netrc" -eq 1 ] && [ -f "$netrc" ]; then
  n=$(python3 - "$netrc" "$netrc_backup" <<'PY'
import os, re, shutil, sys
path, backup = sys.argv[1:3]
drop = {"localhost", "127.0.0.1"}
with open(path, encoding="utf-8", errors="surrogateescape") as f:
    text = f.read()
if not os.path.exists(backup):
    shutil.copyfile(path, backup)
    os.chmod(backup, 0o600)
tokens = list(re.finditer(r"\S+", text))
starts = []
i = 0
while i < len(tokens):
    t = tokens[i].group(0)
    if t == "macdef":
        end = text.find("\n\n", tokens[i].end())
        end = len(text) if end < 0 else end + 2
        while i < len(tokens) and tokens[i].start() < end:
            i += 1
        continue
    if t == "machine" and i + 1 < len(tokens):
        starts.append((tokens[i].start(), tokens[i + 1].group(0)))
        i += 2
        continue
    if t == "default":
        starts.append((tokens[i].start(), "default"))
    i += 1
removed = 0
if starts:
    head = text[: starts[0][0]]
    kept = head
    for n, (off, name) in enumerate(starts):
        end = starts[n + 1][0] if n + 1 < len(starts) else len(text)
        if name in drop:
            removed += 1
        else:
            kept += text[off:end]
    if kept != text:
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8", errors="surrogateescape") as f:
            f.write(kept)
        os.chmod(path, 0o600)
print(removed)
PY
  )
  case ${n:-} in
    '') warn "could not edit $netrc" ;;
    0) echo "reset.sh: no localhost/127.0.0.1 entries in ~/.netrc" ;;
    *) removed+=("$n localhost/127.0.0.1 entr$([ "$n" = 1 ] && echo y || echo ies) in ~/.netrc (backup once: ~/.netrc.gerrit-stack.bak)") ;;
  esac
fi

if [ "${#removed[@]}" -eq 0 ]; then
  echo "reset.sh: nothing to remove"
else
  echo "reset.sh removed:"
  printf '  - %s\n' "${removed[@]}"
fi
tree=${GERRIT_TREE:-$HOME/workspace/open/gerrit-3.14}
if [ -L "$tree/plugins/demo-plugin" ]; then
  echo "note: $tree/plugins/demo-plugin still points at demo/work/demo-plugin; seed.sh re-links it"
fi
echo "next: docker compose -f $compose up -d && bash $demo_dir/seed.sh   (make demo-up demo-seed)"
