#!/usr/bin/env bats
# tests/metrics.bats — scripts/chain-metrics.sh (WP A11).
#
# Self-contained: builds its own temp repos with the real commit-msg hook from
# tests/fixtures/commit-msg (never hand-writes a Change-Id) under
# $BATS_TEST_TMPDIR, hermetic git config. Does not load tests/helpers.bash on
# purpose (owned by A0, may still change).

setup() {
  REPO_ROOT=$(cd "$BATS_TEST_DIRNAME/.." && pwd -P)
  SCRIPT="$REPO_ROOT/scripts/chain-metrics.sh"
  HOOK="$REPO_ROOT/tests/fixtures/commit-msg"
  export GIT_CONFIG_GLOBAL=/dev/null
  export GIT_CONFIG_NOSYSTEM=1
  export GIT_TERMINAL_PROMPT=0
  export GIT_AUTHOR_NAME="Test User" GIT_AUTHOR_EMAIL="test@example.com"
  export GIT_COMMITTER_NAME="Test User" GIT_COMMITTER_EMAIL="test@example.com"
  unset GERRIT_STACK_TRACE GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE
  HOME="$BATS_TEST_TMPDIR/home"; export HOME; mkdir -p "$HOME"
  WORK="$BATS_TEST_TMPDIR/work"
  REMOTE="$BATS_TEST_TMPDIR/remote.git"
  make_chain_repo
}

# make_chain_repo — bare remote + work repo with a pushed initial commit and a
# 3-commit chain on top: feat (1 file, +2), fix (2 files, +3), and a mixed
# "refactor ... and ..." commit that is over a 5-line test budget.
make_chain_repo() {
  git init -q --bare "$REMOTE"
  git init -q -b master "$WORK"
  git -C "$WORK" remote add origin "$REMOTE"
  git -C "$WORK" config remote.origin.push HEAD:refs/for/master
  hooks=$(git -C "$WORK" rev-parse --git-path hooks)
  case "$hooks" in /*) ;; *) hooks="$WORK/$hooks" ;; esac
  mkdir -p "$hooks"
  cp "$HOOK" "$hooks/commit-msg"
  chmod +x "$hooks/commit-msg"
  echo base > "$WORK/base.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "chore: initial import"
  git -C "$WORK" push -q origin HEAD:refs/heads/master
  printf 'a\nb\n' > "$WORK/a.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "feat: add a"
  printf 'c\nd\n' > "$WORK/c.txt"; echo more >> "$WORK/a.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "fix(a): tweak a"
  printf '1\n2\n3\n4\n5\n6\n' > "$WORK/big.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "refactor: extract helper and add feature"
}

@test "3-commit chain: --json per-change fields and aggregates" {
  run bash "$SCRIPT" --json "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.chain_length == 3' >/dev/null
  echo "$output" | jq -e '.base_ref == "refs/remotes/origin/master"' >/dev/null
  echo "$output" | jq -e '.budget == {lines: 150, files: 8}' >/dev/null
  echo "$output" | jq -e '.changes | length == 3' >/dev/null
  echo "$output" | jq -e '.changes[0] | .subject == "feat: add a" and .lines == 2 and .files == 1 and .change_ids == 1 and .conventional_type == "feat" and .is_fixup == false and .single_concern == true' >/dev/null
  echo "$output" | jq -e '.changes[1] | .lines == 3 and .files == 2 and .conventional_type == "fix"' >/dev/null
  echo "$output" | jq -e '.changes[2] | .conventional_type == "refactor" and .single_concern == false' >/dev/null
  echo "$output" | jq -e '.lines_median == 3 and .lines_p75 == 6 and .lines_max == 6 and .files_median == 1' >/dev/null
  echo "$output" | jq -e '.within_budget_pct == 100 and .one_change_id_pct == 100 and .conventional_pct == 100' >/dev/null
  echo "$output" | jq -e '.single_concern_pct == 66.7 and .fixups_present == false' >/dev/null
  echo "$output" | jq -e '.builds_alone_pct == null and .violations == null and .refs_for_pushed == null' >/dev/null
  echo "$output" | jq -e '.change_id_set | length == 3 and all(.[]; test("^I[0-9a-f]{40}$"))' >/dev/null
}

@test "budget override via git config and fixup detection" {
  git -C "$WORK" config gerrit-stack.budget.lines 5
  echo x > "$WORK/x.txt"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "fixup! feat: add a"
  run bash "$SCRIPT" --json "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.budget.lines == 5 and .chain_length == 4' >/dev/null
  echo "$output" | jq -e '.within_budget_pct == 75' >/dev/null
  echo "$output" | jq -e '.fixups_present == true and .changes[3].is_fixup == true and .changes[3].change_ids == 0 and .changes[3].conventional_type == null' >/dev/null
  echo "$output" | jq -e '.one_change_id_pct == 75' >/dev/null
}

@test "human table by default" {
  run bash "$SCRIPT" "$WORK"
  [ "$status" -eq 0 ]
  [[ "$output" == *"chain: 3 change(s)"* ]]
  [[ "$output" == *"feat: add a"* ]]
  [[ "$output" == *"within budget 100%"* ]]
}

@test "--hook-trace counts deny/ask per verb" {
  trace="$BATS_TEST_TMPDIR/hook-trace.log"
  printf 'git-guard.sh\tpush\tdeny\ngit-guard.sh\tpush\task\ngit-guard.sh\tcommit\tdeny\ngit-post.sh\tcommit\tfeedback\n\n' > "$trace"
  run bash "$SCRIPT" --json --hook-trace "$trace" "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.violations == 2 and .violations_by_verb == {push: 1, commit: 1}' >/dev/null
  echo "$output" | jq -e '.asks == 1 and .asks_by_verb == {push: 1}' >/dev/null
  echo "$output" | jq -e '.hook_trace.lines == 4 and .hook_trace.missing == false' >/dev/null
  run bash "$SCRIPT" --json --hook-trace "$BATS_TEST_TMPDIR/nope.log" "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.violations == 0 and .hook_trace.missing == true' >/dev/null
}

@test "--verify-cmd true: builds_alone_pct 100, caller checkout untouched" {
  echo dirty > "$WORK/untracked.txt"
  before_head=$(git -C "$WORK" rev-parse HEAD)
  run bash "$SCRIPT" --json --verify-cmd true "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.builds_alone_pct == 100 and .verify_error == null' >/dev/null
  echo "$output" | jq -e '[.changes[].builds_alone] == [true, true, true]' >/dev/null
  [ "$(git -C "$WORK" rev-parse HEAD)" = "$before_head" ]
  [ -f "$WORK/untracked.txt" ]
  [ "$(git -C "$WORK" status --porcelain)" = "?? untracked.txt" ]
  [ "$(git -C "$WORK" worktree list | wc -l | tr -d ' ')" = "1" ]
  [ ! -d "$(git -C "$WORK" rev-parse --git-path rebase-merge)" ]
}

@test "--verify-cmd failing on one change: partial builds_alone" {
  run bash "$SCRIPT" --json --verify-cmd 'test ! -e c.txt' "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.builds_alone_pct == 33.3' >/dev/null
  echo "$output" | jq -e '[.changes[].builds_alone] == [true, false, false]' >/dev/null
  [ "$(git -C "$WORK" status --porcelain)" = "" ]
}

@test "--remote counts pushed refs on a local bare remote" {
  run bash "$SCRIPT" --json --remote origin "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.refs_for_pushed == 0 and .remote.local == true' >/dev/null
  git -C "$WORK" push -q origin HEAD:refs/for/master
  run bash "$SCRIPT" --json --remote origin "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.refs_for_pushed == 3 and .remote.refs_for == 1' >/dev/null
  git -C "$REMOTE" update-ref refs/changes/01/1/1 "$(git -C "$WORK" rev-parse HEAD~2)"
  run bash "$SCRIPT" --json --remote origin "$WORK"
  echo "$output" | jq -e '.remote.refs_changes == 1 and .refs_for_pushed == 4' >/dev/null
}

@test "--base and root fallback" {
  run bash "$SCRIPT" --json --base HEAD~1 "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.chain_length == 1 and .base_ref == "HEAD~1"' >/dev/null
  git -C "$WORK" remote remove origin
  run bash "$SCRIPT" --json "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.chain_length == 3 and .base_ref == "root"' >/dev/null
}

@test "usage errors exit 2" {
  run bash "$SCRIPT" --bogus "$WORK"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --base does-not-exist "$WORK"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" "$BATS_TEST_TMPDIR/not-a-repo"
  [ "$status" -eq 2 ]
}
