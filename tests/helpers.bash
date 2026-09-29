#!/usr/bin/env bash
# shellcheck shell=bash
# tests/helpers.bash — shared bats helpers for gerrit-stack.
#
# Every bats file starts with `load helpers` and calls `common_setup` from its
# `setup()`. All fixtures live under $BATS_TEST_TMPDIR; nothing is written
# outside it. Change-Id trailers are never hand-written: fixtures install the
# real Gerrit commit-msg hook from tests/fixtures/commit-msg.

REPO_ROOT=$(cd "${BATS_TEST_DIRNAME:?}/.." && pwd -P)
export REPO_ROOT
FIXTURE_HOOK="$REPO_ROOT/tests/fixtures/commit-msg"
export FIXTURE_HOOK

# common_setup — hermetic git environment. Call from every bats `setup()`.
common_setup() {
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_TERMINAL_PROMPT=0
  export GIT_AUTHOR_NAME="Test User" GIT_AUTHOR_EMAIL="test@example.com"
  export GIT_COMMITTER_NAME="Test User" GIT_COMMITTER_EMAIL="test@example.com"
  unset GERRIT_STACK_TRACE GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_COMMON_DIR \
    GIT_OBJECT_DIRECTORY GIT_NAMESPACE XDG_CONFIG_HOME
  # Fresh HOME so ~/.gitconfig, ~/.netrc, ~/.claude never leak into tests.
  HOME="$BATS_TEST_TMPDIR/home"
  export HOME
  mkdir -p "$HOME"
}

# physical_path <dir> — resolve symlinks (macOS $TMPDIR is a symlink; git
# reports physical paths).
physical_path() {
  (cd "$1" && pwd -P)
}

# fail [message...] — print a message to stderr and fail the current test.
fail() {
  printf 'FAIL: %s\n' "$*" >&2
  return 1
}

# assert_eq <expected> <actual> [label]
assert_eq() {
  if [ "$1" != "$2" ]; then
    printf 'assert_eq%s failed:\n  expected: %s\n  actual:   %s\n' \
      "${3:+ ($3)}" "$1" "$2" >&2
    return 1
  fi
}

# count_lines <text> — number of non-empty lines (portable, no `wc` padding).
count_lines() {
  if [ -z "$1" ]; then
    printf '0\n'
  else
    printf '%s\n' "$1" | grep -c '' || true
  fi
}

_helpers_git_identity() {
  git -C "$1" config user.name "Test User"
  git -C "$1" config user.email "test@example.com"
}

# make_plain_repo — non-Gerrit repo with one commit; prints its path.
make_plain_repo() {
  local dir="$BATS_TEST_TMPDIR/plain"
  mkdir -p "$dir"
  git -C "$dir" init -q -b master
  _helpers_git_identity "$dir"
  printf '# plain\n' > "$dir/README.md"
  git -C "$dir" add README.md
  git -C "$dir" commit -q -m "chore: init"
  printf '%s\n' "$dir"
}

# make_gerrit_repo [name] — Gerrit-looking work repo (default name `work`)
# with: local bare remote $BATS_TEST_TMPDIR/remote.git, `origin` pointing at
# it, remote.origin.push=HEAD:refs/for/master, a committed .gitreview, the real
# commit-msg hook installed, README.md committed and pushed so
# refs/remotes/origin/master exists. Prints the work repo path. A second call
# (different name) clones the same remote history instead of re-seeding it.
make_gerrit_repo() {
  local name="${1:-work}"
  local bare="$BATS_TEST_TMPDIR/remote.git"
  local dir="$BATS_TEST_TMPDIR/$name"
  local hooks

  if [ ! -d "$bare" ]; then
    git init -q --bare -b master "$bare"
  fi
  mkdir -p "$dir"
  git -C "$dir" init -q -b master
  _helpers_git_identity "$dir"
  git -C "$dir" remote add origin "$bare"
  git -C "$dir" config remote.origin.push HEAD:refs/for/master

  hooks=$(git -C "$dir" rev-parse --git-path hooks)
  case "$hooks" in /*) ;; *) hooks="$dir/$hooks" ;; esac
  mkdir -p "$hooks"
  cp -f "$FIXTURE_HOOK" "$hooks/commit-msg"
  chmod +x "$hooks/commit-msg"

  if git --git-dir="$bare" rev-parse -q --verify refs/heads/master >/dev/null 2>&1; then
    git -C "$dir" fetch -q origin
    git -C "$dir" reset -q --hard refs/remotes/origin/master
  else
    printf '[gerrit]\nhost=localhost\nproject=demo\ndefaultbranch=master\n' > "$dir/.gitreview"
    printf '# demo\n' > "$dir/README.md"
    git -C "$dir" add .gitreview README.md
    git -C "$dir" commit -q -m "chore: initial commit"
    git -C "$dir" push -q origin HEAD:refs/heads/master
  fi
  printf '%s\n' "$dir"
}

# commit_file <repo> <path> <content> <subject> — write, add, commit; prints sha.
commit_file() {
  local repo="$1" path="$2" content="$3" subject="$4"
  mkdir -p "$(dirname "$repo/$path")"
  printf '%s\n' "$content" > "$repo/$path"
  git -C "$repo" add -- "$path"
  git -C "$repo" commit -q -m "$subject"
  git -C "$repo" rev-parse HEAD
}

# hook_json <event> <cwd> <command> [session_id] [tool_response_json]
# Prints the stdin JSON a Claude Code hook receives for a Bash tool call.
hook_json() {
  local event="$1" cwd="$2" command="$3" session="${4:-test-session}"
  local response="${5:-}"
  if [ -z "$response" ]; then
    response='{"stdout":"","stderr":"","interrupted":false}'
  fi
  jq -cn \
    --arg event "$event" --arg cwd "$cwd" --arg command "$command" \
    --arg session "$session" --argjson response "$response" '
    { hook_event_name: $event, session_id: $session, cwd: $cwd,
      tool_name: "Bash", tool_input: { command: $command } }
    + (if $event == "PostToolUse" then { tool_response: $response } else {} end)
    + (if $event == "Stop" then { stop_hook_active: false } else {} end)
    + (if $event == "SessionStart" then { source: "startup" } else {} end)'
}

# run_hook <script-path> <json> — bats `run` with separated streams:
# afterwards $status, $output (stdout), $stderr and ${lines[@]} are set.
run_hook() {
  local script="$1" json="$2"
  run --separate-stderr bash "$script" <<< "$json"
}
