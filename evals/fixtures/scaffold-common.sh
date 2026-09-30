#!/usr/bin/env bash
# scaffold-common.sh — shared helpers for eval fixtures (`evals/<case>/fixture.sh`).
#
# Sourced, never executed. Every fixture runs with cwd = the empty run workspace
# (both under `evals/run.py` and the official `claude plugin eval --scaffold`).
#
#   source "$(dirname "$0")/../fixtures/scaffold-common.sh"
#   make_gerrit_workspace [--chain N] [--project-kind sh|java]
#   make_dirty_diff <lines>
#
# PLUGIN_ROOT resolves from this file's location (evals/fixtures/../..) or from
# $EVAL_PLUGIN_ROOT when set (the official runner passes only EVAL_* variables).
# bash 3.2 compatible; shellcheck-clean; never writes outside $PWD and ../remote.git.
# shellcheck disable=SC2016  # generated shell snippets are written in single quotes on purpose
set -uo pipefail

if [ -n "${EVAL_PLUGIN_ROOT:-}" ] && [ -d "${EVAL_PLUGIN_ROOT}" ]; then
  PLUGIN_ROOT=$(cd "${EVAL_PLUGIN_ROOT}" && pwd -P)
else
  PLUGIN_ROOT=$(cd "$(dirname "${BASH_SOURCE[0]}")/../.." && pwd -P)
fi
export PLUGIN_ROOT

# Git identity + quiet defaults for every git call made by fixtures. Fixture
# commits are made with the real Gerrit commit-msg hook so every commit carries
# a hook-generated Change-Id (never hand-written).
export GIT_AUTHOR_NAME="Eval Author" GIT_AUTHOR_EMAIL="eval@example.com"
export GIT_COMMITTER_NAME="Eval Author" GIT_COMMITTER_EMAIL="eval@example.com"
export GIT_TERMINAL_PROMPT=0

_scaffold_die() { echo "scaffold: $*" >&2; exit 1; }

# _write_sh_project — tiny shell project: greet.sh + README.md + tests/test_greet.sh
_write_sh_project() {
  cat > greet.sh <<'EOF'
#!/usr/bin/env bash
# greet.sh — print a greeting for a name (default: world)
name="${1:-world}"
echo "Hello, ${name}!"
EOF
  chmod +x greet.sh
  mkdir -p tests
  cat > tests/test_greet.sh <<'EOF'
#!/usr/bin/env bash
# Minimal test runner: every check prints ok/FAIL; exit 1 on any failure.
set -uo pipefail
cd "$(dirname "$0")/.." || exit 1
fail=0
check() { if [ "$2" = "$3" ]; then echo "ok   $1"; else echo "FAIL $1: got '$2' want '$3'"; fail=1; fi; }
check "default name" "$(bash greet.sh)" "Hello, world!"
check "given name" "$(bash greet.sh Rena)" "Hello, Rena!"
exit $fail
EOF
  chmod +x tests/test_greet.sh
  cat > README.md <<'EOF'
# greet

A tiny greeting tool used as an eval fixture.

    bash greet.sh [name]

Run the tests with `bash tests/test_greet.sh`.
Pushes go to Gerrit as a relation chain (see `.gitreview`).
EOF
}

# _write_java_project — copies the demo plugin skeleton (Bazel + javac fallback)
_write_java_project() {
  [ -d "$PLUGIN_ROOT/demo/skeleton" ] || _scaffold_die "missing $PLUGIN_ROOT/demo/skeleton"
  # cp -R of the directory contents; trailing /. copies dotfiles too.
  cp -R "$PLUGIN_ROOT/demo/skeleton/." .
}

# _chain_commit <subject> — stage everything and commit through the commit-msg hook
_chain_commit() {
  git add -A
  git commit -q -m "$1" || _scaffold_die "commit failed: $1"
}

# _add_chain_sh N — N meaningful commits on the sh project (the review stub's
# canned thread points at greet.sh line 3 of change 2).
_add_chain_sh() {
  local n=$1 i=1
  while [ "$i" -le "$n" ]; do
    case $i in
      1)
        printf 'Hello\n' > greet.conf
        printf '\n## Config\n\n`greet.conf` holds the greeting prefix (first line).\n' >> README.md
        _chain_commit "feat: add greet.conf with the greeting prefix"
        ;;
      2)
        cat > greet.sh <<'EOF'
#!/usr/bin/env bash
# greet.sh — print a greeting for a name (default: world)
prefix=$(head -n 1 greet.conf 2>/dev/null || echo Hello)
name="${1:-world}"
echo "${prefix}, ${name}!"
EOF
        _chain_commit "feat: read the greeting prefix from greet.conf"
        ;;
      3)
        printf 'check "prefix from config" "$(printf "Hi\\n" > greet.conf; bash greet.sh; printf "Hello\\n" > greet.conf)" "Hi, world!"\n' >> tests/test_greet.sh
        # keep the exit line last
        sed -i.bak '/^exit \$fail$/d' tests/test_greet.sh && rm -f tests/test_greet.sh.bak
        printf 'exit $fail\n' >> tests/test_greet.sh
        _chain_commit "test: cover a configured greeting prefix"
        ;;
      *)
        printf '\nStep %s note.\n' "$i" >> README.md
        _chain_commit "docs: chain step $i"
        ;;
    esac
    i=$((i + 1))
  done
}

# _add_chain_generic N — N small README commits (java kind or N > 3 on sh)
_add_chain_generic() {
  local n=$1 i=1
  while [ "$i" -le "$n" ]; do
    printf '\nChain step %s: reviewable on its own.\n' "$i" >> README.md
    _chain_commit "docs: chain step $i of $n"
    i=$((i + 1))
  done
}

# make_gerrit_workspace [--chain N] [--project-kind sh|java]
# Builds a Gerrit-looking repo in $PWD: master branch, bare ../remote.git as
# origin with push refspec HEAD:refs/for/master, .gitreview, the real commit-msg
# hook, a tiny project, one initial commit pushed to refs/heads/master, and
# optionally N chain commits (each with a hook-generated Change-Id) on top.
make_gerrit_workspace() {
  local chain=0 kind=sh
  while [ $# -gt 0 ]; do
    case $1 in
      --chain) chain=$2; shift 2 ;;
      --project-kind) kind=$2; shift 2 ;;
      *) _scaffold_die "make_gerrit_workspace: unknown option $1" ;;
    esac
  done
  [ -f "$PLUGIN_ROOT/tests/fixtures/commit-msg" ] || _scaffold_die "missing $PLUGIN_ROOT/tests/fixtures/commit-msg"

  git init -q -b master . || _scaffold_die "git init failed"
  git config user.name "Eval Author"
  git config user.email "eval@example.com"
  git config commit.gpgsign false
  git config core.hooksPath "$(git rev-parse --git-path hooks)"

  git init -q --bare -b master ../remote.git || _scaffold_die "bare remote init failed"
  git remote add origin ../remote.git
  git config remote.origin.push HEAD:refs/for/master

  cat > .gitreview <<'EOF'
[gerrit]
host=review.example.com
port=29418
project=demo
defaultbranch=master
EOF

  local hooks_dir
  hooks_dir=$(git rev-parse --git-path hooks)
  mkdir -p "$hooks_dir"
  cat "$PLUGIN_ROOT/tests/fixtures/commit-msg" > "$hooks_dir/commit-msg"
  chmod +x "$hooks_dir/commit-msg"

  case $kind in
    sh) _write_sh_project ;;
    java) _write_java_project ;;
    *) _scaffold_die "unknown --project-kind $kind" ;;
  esac

  git add -A
  git commit -q -m "chore: initial import" || _scaffold_die "initial commit failed"
  git push -q origin HEAD:refs/heads/master || _scaffold_die "push to remote master failed"
  git fetch -q origin master
  git branch -q --set-upstream-to=origin/master master 2>/dev/null || true

  if [ "$chain" -gt 0 ]; then
    if [ "$kind" = sh ]; then _add_chain_sh "$chain"; else _add_chain_generic "$chain"; fi
  fi
}

# _gen_lines <file> <count> <fn-prefix> <comment>
# Appends <count> lines of plausible shell to <file>: one helper function per 5 lines.
_gen_lines() {
  local file=$1 count=$2 prefix=$3 comment=$4 i=0 n=1
  while [ "$i" -lt "$count" ]; do
    {
      printf '# %s helper %s\n' "$comment" "$n"
      printf '%s_%s() {\n' "$prefix" "$n"
      printf '  local value="${1:-}"\n'
      printf '  printf "%%s\\n" "%s ${value}"\n' "$prefix"
      printf '}\n'
    } >> "$file"
    i=$((i + 5))
    n=$((n + 1))
  done
}

# make_dirty_diff <lines> — an uncommitted change of about <lines> lines spread
# over four independent concerns (logging, config parsing, config tests, docs), each
# under the 150-line soft budget, plus a
# one-line wiring edit in greet.sh. Nothing is staged.
make_dirty_diff() {
  local total=${1:-600} third
  # each concern stays within the soft budget (<=150 lines) so a by-concern split is possible
  third=$((total / 4))
  mkdir -p lib docs tests
  printf '#!/usr/bin/env bash\n# lib/logging.sh — structured logging helpers (concern: logging)\n' > lib/logging.sh
  _gen_lines lib/logging.sh "$((third - 6))" log "logging"
  printf '#!/usr/bin/env bash\n# lib/config.sh — key=value config parsing (concern: configuration)\n' > lib/config.sh
  _gen_lines lib/config.sh "$((third - 6))" cfg "config"
  printf '#!/usr/bin/env bash\n# tests/test_config.sh — tests for lib/config.sh (concern: docs + tests)\n' > tests/test_config.sh
  _gen_lines tests/test_config.sh "$((third * 2 / 3 - 2))" test_cfg "config test"
  {
    printf '# Usage guide\n\n'
    local i=1
    while [ "$i" -le $((third / 2)) ]; do
      printf 'Line %s: `greet.sh` reads its settings from `greet.conf`; see lib/config.sh.\n' "$i"
      i=$((i + 1))
    done
  } > docs/USAGE.md
  # wiring edit in an existing tracked file (appended; sourcing is best-effort)
  printf '\n# wiring: load helpers\nsource ./lib/logging.sh 2>/dev/null\nsource ./lib/config.sh 2>/dev/null\n' >> greet.sh
}
