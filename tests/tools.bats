#!/usr/bin/env bats
# tests/tools.bats — scripts/chain-status.sh, scripts/push-chain.sh,
# scripts/diff-budget.sh, scripts/install-commit-msg-hook.sh

load helpers

setup() {
  common_setup
  CHAIN_STATUS="$REPO_ROOT/scripts/chain-status.sh"
  PUSH_CHAIN="$REPO_ROOT/scripts/push-chain.sh"
  DIFF_BUDGET="$REPO_ROOT/scripts/diff-budget.sh"
  INSTALL_HOOK="$REPO_ROOT/scripts/install-commit-msg-hook.sh"
}

# make_chain <repo> — three commits on top of origin/master (hook installed,
# so each carries one Change-Id).
make_chain() {
  commit_file "$1" a.txt "alpha" "feat: add alpha" >/dev/null
  commit_file "$1" b.txt "beta" "feat: add beta" >/dev/null
  commit_file "$1" c.txt "gamma" "fix: add gamma" >/dev/null
}

# write_lines <path> <n> — a file with exactly n lines.
write_lines() {
  local path="$1" n="$2" i=1
  mkdir -p "$(dirname "$path")"
  : > "$path"
  while [ "$i" -le "$n" ]; do
    printf 'line %d\n' "$i" >> "$path"
    i=$((i+1))
  done
}

# table_rows <output> — number of "sha7 | …" data rows in a chain-status table.
table_rows() {
  printf '%s\n' "$1" | grep -c -E '^[0-9a-f]{7} \|' || true
}

# hooks_dir <repo> — absolute hooks dir of a repo.
hooks_dir() {
  local h
  h=$(git -C "$1" rev-parse --git-path hooks)
  case "$h" in /*) printf '%s\n' "$h" ;; *) printf '%s\n' "$1/$h" ;; esac
}

# ================================================================ chain-status

@test "chain-status: plain repo exits 1 with a one-line explanation on stderr" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS"
  assert_eq 1 "$status"
  assert_eq "" "$output"
  assert_eq 1 "$(count_lines "$stderr")"
  [[ $stderr == *Gerrit* ]] || fail "stderr does not mention Gerrit: $stderr"
}

@test "chain-status: 3-commit chain prints header and three rows, oldest first" {
  local repo base
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  base=$(git -C "$repo" rev-parse --short=7 refs/remotes/origin/master)
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS"
  assert_eq 0 "$status"
  assert_eq "" "$stderr"
  assert_eq "chain: 3 change(s) on origin/master (base $base)" "${lines[0]}"
  assert_eq 3 "$(table_rows "$output")"
  # oldest first: first data row is the alpha commit
  local first
  first=$(printf '%s\n' "$output" | grep -E '^[0-9a-f]{7} \|' | head -1)
  [[ $first == *"feat: add alpha" ]] || fail "first row is not alpha: $first"
  [[ $first == *"| I"* ]] || fail "row has no Change-Id: $first"
}

@test "chain-status: empty chain prints header with 0 change(s)" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS"
  assert_eq 0 "$status"
  [[ ${lines[0]} == "chain: 0 change(s) on origin/master (base "* ]] || fail "header: ${lines[0]}"
  assert_eq 0 "$(table_rows "$output")"
}

@test "chain-status: --json is valid with 3 changes and hook_ok 1" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS" --json
  assert_eq 0 "$status"
  printf '%s\n' "$output" | jq -e '
    .remote == "origin" and .branch == "master" and .project == "demo"
    and .hook_ok == 1 and (.changes | length) == 3
    and all(.changes[]; (.change_ids | length) == 1 and (.fixup == false)
                        and (.lines | type) == "number" and (.files == 1))
    and .changes[0].subject == "feat: add alpha"
    and .changes[2].subject == "fix: add gamma"' >/dev/null \
    || fail "unexpected JSON: $output"
}

@test "chain-status: marks missing Change-Id and fixup! rows" {
  local repo sha row
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  printf 'x\n' > "$repo/x.txt"
  git -C "$repo" add x.txt
  git -C "$repo" commit -q --no-verify -m "feat: no id here"
  sha=$(git -C "$repo" rev-parse --short=7 HEAD)
  printf 'y\n' >> "$repo/a.txt"
  git -C "$repo" add a.txt
  git -C "$repo" commit -q -m "fixup! feat: add alpha"
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS"
  assert_eq 0 "$status"
  assert_eq 5 "$(table_rows "$output")"
  row=$(printf '%s\n' "$output" | grep "^$sha ")
  [[ $row == *MISSING* ]] || fail "missing id not marked: $row"
  row=$(printf '%s\n' "$output" | grep 'fixup! feat: add alpha')
  [[ $row == *"[fixup"* ]] || fail "fixup not marked: $row"
  run --separate-stderr bash "$CHAIN_STATUS" --json
  printf '%s\n' "$output" | jq -e '
    (.changes[3].change_ids | length) == 0 and .changes[4].fixup == true' >/dev/null \
    || fail "JSON marks wrong: $output"
}

@test "chain-status: --preflight exit 0 with hook, exit 1 + MISSING without" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS" --preflight
  assert_eq 0 "$status"
  [[ $output == *"remote: origin"* ]] || fail "$output"
  [[ $output == *"branch: master"* ]] || fail "$output"
  [[ $output == *"project: demo"* ]] || fail "$output"
  [[ $output == *"commit-msg hook: installed"* ]] || fail "$output"
  [[ $output == *"chain: 3"* ]] || fail "$output"
  printf '%s\n' "$output" | grep -q -E '^gerrit-mcp: (installed|not installed|unknown)$' \
    || fail "no gerrit-mcp line: $output"

  rm -f "$(hooks_dir "$repo")/commit-msg"
  run --separate-stderr bash "$CHAIN_STATUS" --preflight
  assert_eq 1 "$status"
  [[ $output == *"commit-msg hook: MISSING"* ]] || fail "$output"
  [[ $output == *"install-commit-msg-hook.sh"* ]] || fail "no install hint: $output"
}

@test "chain-status: --snapshot then --verify-ids prints ok" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  # no snapshot yet: a note, exit 0
  run --separate-stderr bash "$CHAIN_STATUS" --verify-ids
  assert_eq 0 "$status"
  [[ $output == *"no snapshot"* ]] || fail "$output"

  run --separate-stderr bash "$CHAIN_STATUS" --snapshot
  assert_eq 0 "$status"
  [[ $output == *"3"* ]] || fail "snapshot count missing: $output"
  [ -f "$repo/.git/gerrit-stack/chain-ids" ] || fail "chain-ids not written"
  assert_eq 3 "$(count_lines "$(cat "$repo/.git/gerrit-stack/chain-ids")")"

  run --separate-stderr bash "$CHAIN_STATUS" --verify-ids
  assert_eq 0 "$status"
  assert_eq "ok" "$output"
}

@test "chain-status: --verify-ids reports lost: after an amend that drops the id" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  bash "$CHAIN_STATUS" --snapshot >/dev/null
  git commit -q --amend --no-verify -m x
  run --separate-stderr bash "$CHAIN_STATUS" --verify-ids
  assert_eq 1 "$status"
  [[ $output == lost:\ I* ]] || fail "expected lost: line, got: $output"
  [[ $output != *new:* ]] || fail "unexpected new: line: $output"
}

@test "chain-status: rejects unknown options and two modes at once" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  run --separate-stderr bash "$CHAIN_STATUS" --bogus
  assert_eq 2 "$status"
  run --separate-stderr bash "$CHAIN_STATUS" --json --preflight
  assert_eq 2 "$status"
}

# ================================================================ push-chain

@test "push-chain: plain repo exits 1 with explanation" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 1 "$status"
  assert_eq "" "$output"
  assert_eq 1 "$(count_lines "$stderr")"
}

@test "push-chain: default prints exactly one plain refs/for line" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master" "$output"
  assert_eq "" "$stderr"
}

@test "push-chain: --grouping hashtag --name slugifies to %t=" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping hashtag --name "Demo Tag"
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%t=Demo-Tag" "$output"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping hashtag --name 'a b!c/d.e_f'
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%t=a-bc/d.e_f" "$output"
}

@test "push-chain: --grouping topic --name t and --wip combinations" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping topic --name t
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%topic=t" "$output"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping topic --name t --wip
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%topic=t,wip" "$output"
  run --separate-stderr bash "$PUSH_CHAIN" --wip
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%wip" "$output"
}

@test "push-chain: reads grouping/group-name/default-wip from config, never writes it" {
  local repo before after
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  git -C "$repo" config gerrit-stack.grouping topic
  git -C "$repo" config gerrit-stack.group-name x
  before=$(git -C "$repo" config --list --local)
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%topic=x" "$output"
  # flags override for this print only
  run --separate-stderr bash "$PUSH_CHAIN" --grouping none
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master" "$output"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping hashtag --name "Other Name"
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%t=Other-Name" "$output"
  after=$(git -C "$repo" config --list --local)
  assert_eq "$before" "$after" "config untouched"

  git -C "$repo" config gerrit-stack.default-wip true
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 0 "$status"
  assert_eq "git push origin HEAD:refs/for/master%topic=x,wip" "$output"
}

@test "push-chain: --remote and --branch override detection" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  git -C "$repo" remote add upstream "$BATS_TEST_TMPDIR/remote.git"
  git -C "$repo" fetch -q upstream
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN" --remote upstream
  assert_eq 0 "$status"
  assert_eq "git push upstream HEAD:refs/for/master" "$output"
  assert_eq "" "$stderr"
  # no tracking ref for the override: validated against the detected base, noted
  run --separate-stderr bash "$PUSH_CHAIN" --remote upstream --branch stable-1
  assert_eq 0 "$status"
  assert_eq "git push upstream HEAD:refs/for/stable-1" "$output"
  [[ $stderr == *"origin/master"* ]] || fail "no note about the fallback base: $stderr"
}

@test "push-chain: hashtag/topic without a name exits 3" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping hashtag
  assert_eq 3 "$status"
  assert_eq "" "$output"
  [[ $stderr == *name* ]] || fail "$stderr"
  run --separate-stderr bash "$PUSH_CHAIN" --grouping bogus --name x
  assert_eq 3 "$status"
  [[ $stderr == *"none|hashtag|topic"* ]] || fail "$stderr"
}

@test "push-chain: commit without Change-Id exits 3 and prints the repair command" {
  local repo sha
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  printf 'x\n' > "$repo/x.txt"
  git -C "$repo" add x.txt
  git -C "$repo" commit -q --no-verify -m "feat: no id here"
  sha=$(git -C "$repo" rev-parse --short=7 HEAD)
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 3 "$status"
  assert_eq "" "$output"
  [[ $stderr == *"$sha"* ]] || fail "sha not listed: $stderr"
  [[ $stderr == *"git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' origin/master"* ]] \
    || fail "repair command missing: $stderr"
}

@test "push-chain: fixup! commit exits 3" {
  local repo
  repo=$(make_gerrit_repo)
  make_chain "$repo"
  printf 'y\n' >> "$repo/a.txt"
  git -C "$repo" add a.txt
  git -C "$repo" commit -q -m "fixup! feat: add alpha"
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 3 "$status"
  assert_eq "" "$output"
  [[ $stderr == *fixup* ]] || fail "$stderr"
  [[ $stderr == *autosquash* ]] || fail "no autosquash hint: $stderr"
}

@test "push-chain: empty chain exits 3" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  run --separate-stderr bash "$PUSH_CHAIN"
  assert_eq 3 "$status"
  assert_eq "" "$output"
  [[ $stderr == *empty* ]] || fail "$stderr"
}

# ================================================================ diff-budget
# Production lines only count against the warning (default 400); tests, docs
# and lock files are reported but never warned about. No hard cap and no file
# warning unless budget.hard-lines / budget.files are set.

@test "diff-budget: production, test and docs/lock lines are counted separately" {
  local repo
  repo=$(make_gerrit_repo)
  write_lines "$repo/src/main/java/App.java" 10
  write_lines "$repo/src/test/java/AppTest.java" 20
  write_lines "$repo/lib/parse_test.go" 3
  write_lines "$repo/web/app.spec.ts" 4
  write_lines "$repo/tools/test_cli.py" 5
  write_lines "$repo/docs/guide.html" 6
  write_lines "$repo/Documentation/config.adoc" 2
  write_lines "$repo/NOTES.rst" 1
  write_lines "$repo/package-lock.json" 7
  write_lines "$repo/go.sum" 2
  write_lines "$repo/yarn.lock" 3
  printf 'x\n' > "$repo/README.md"            # -1 +1
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: mixed"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 0 "$status"
  assert_eq "prod=10 test=32 other=23 files=12 warn=400 hard=none files-warn=none" "$output"
  assert_eq "" "$stderr"
  # no argument means HEAD
  run --separate-stderr bash "$DIFF_BUDGET"
  assert_eq 0 "$status"
  assert_eq "prod=10 test=32 other=23 files=12 warn=400 hard=none files-warn=none" "$output"
}

@test "diff-budget: warns (exit 1) only above 400 production lines; tests never count" {
  local repo first
  repo=$(make_gerrit_repo)
  write_lines "$repo/src/main/Feature.java" 400
  write_lines "$repo/src/test/FeatureTest.java" 900
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: at the warning"
  first=$(git -C "$repo" rev-parse HEAD)
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 0 "$status"
  assert_eq "prod=400 test=900 other=0 files=2 warn=400 hard=none files-warn=none" "$output"

  write_lines "$repo/src/main/More.java" 401
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: over the warning"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=401 test=0 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
  [[ $stderr == *"one-line justification"* ]] || fail "$stderr"
  [[ $stderr == *mechanical* ]] || fail "$stderr"
  [[ $stderr == *"Do not split mechanically"* ]] || fail "$stderr"
  # an explicit older rev still works
  run --separate-stderr bash "$DIFF_BUDGET" "$first"
  assert_eq 0 "$status"
}

@test "diff-budget: no hard cap unless budget.hard-lines is set (exit 3 then)" {
  local repo
  repo=$(make_gerrit_repo)
  write_lines "$repo/src/Big.java" 2000
  git -C "$repo" add .
  git -C "$repo" commit -q -m "build: vendor parser"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=2000 test=0 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
  git config gerrit-stack.budget.hard-lines 1000
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 3 "$status"
  assert_eq "prod=2000 test=0 other=0 files=1 warn=400 hard=1000 files-warn=none" "$output"
  [[ $stderr == *"hard cap"* ]] || fail "$stderr"
  # a non-numeric value is ignored (back to none), with a note
  git config gerrit-stack.budget.hard-lines lots
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=2000 test=0 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
  [[ $stderr == *non-numeric* ]] || fail "$stderr"
}

@test "diff-budget: no file warning unless budget.files is set; custom line warning" {
  local repo i
  repo=$(make_gerrit_repo)
  for i in 1 2 3 4 5 6 7 8 9 10 11 12; do printf '%s\n' "$i" > "$repo/f$i.sh"; done
  git -C "$repo" add .
  git -C "$repo" commit -q -m "chore: twelve files"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 0 "$status"
  assert_eq "prod=12 test=0 other=0 files=12 warn=400 hard=none files-warn=none" "$output"
  git config gerrit-stack.budget.files 8
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=12 test=0 other=0 files=12 warn=400 hard=none files-warn=8" "$output"
  [[ $stderr == *"12 files"* ]] || fail "$stderr"
  git config --unset gerrit-stack.budget.files
  git config gerrit-stack.budget.lines 5
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=12 test=0 other=0 files=12 warn=5 hard=none files-warn=none" "$output"
}

@test "diff-budget: team file values apply; old values in a clone are kept" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" budget.lines 150
  team_config "$repo" budget.hard-lines 200
  git -C "$repo" add .gerrit-stack
  git -C "$repo" commit -q -m "chore: team budget"
  write_lines "$repo/src/Mid.java" 180
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: mid"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 1 "$status"
  assert_eq "prod=180 test=0 other=0 files=1 warn=150 hard=200 files-warn=none" "$output"
  git config gerrit-stack.budget.hard-lines 170
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 3 "$status"
  assert_eq "prod=180 test=0 other=0 files=1 warn=150 hard=170 files-warn=none" "$output"
}

@test "diff-budget: a rename is classified by its new path" {
  local repo
  repo=$(make_gerrit_repo)
  write_lines "$repo/lib/Helper.java" 50
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: helper"
  mkdir -p "$repo/tests"
  git -C "$repo" mv lib/Helper.java tests/Helper.java
  printf 'extra\n' >> "$repo/tests/Helper.java"
  git -C "$repo" add .
  git -C "$repo" commit -q -m "refactor: move helper to tests"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 0 "$status"
  assert_eq "prod=0 test=1 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
}

@test "diff-budget: --worktree counts staged, unstaged and untracked changes" {
  local repo
  repo=$(make_gerrit_repo)
  write_lines "$repo/ten.sh" 10
  git -C "$repo" add ten.sh
  git -C "$repo" commit -q -m "feat: ten"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" --worktree
  assert_eq 0 "$status"
  assert_eq "prod=0 test=0 other=0 files=0 warn=400 hard=none files-warn=none" "$output"
  printf 'more\nmore\n' >> ten.sh              # unstaged: +2 prod
  write_lines new.sh 4                         # staged new file: +4 prod
  git add new.sh
  write_lines tests/new_test.sh 3              # untracked: +3 test
  printf 'x\n' > README.md                     # unstaged: -1 +1 docs
  run --separate-stderr bash "$DIFF_BUDGET" --worktree
  assert_eq 0 "$status"
  assert_eq "prod=6 test=3 other=2 files=4 warn=400 hard=none files-warn=none" "$output"
}

@test "diff-budget: --cached / --staged count only staged changes" {
  local repo
  repo=$(make_gerrit_repo)
  printf 'a\nb\nc\n' > "$repo/staged.sh"; printf 'x\n' > "$repo/unstaged.sh"
  git -C "$repo" add staged.sh
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" --cached
  assert_eq 0 "$status"
  assert_eq "prod=3 test=0 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
  run --separate-stderr bash "$DIFF_BUDGET" --staged
  assert_eq 0 "$status"
  assert_eq "prod=3 test=0 other=0 files=1 warn=400 hard=none files-warn=none" "$output"
}

@test "diff-budget: --estimate sums existing files and counts missing paths as 40" {
  local repo
  repo=$(make_gerrit_repo)
  write_lines "$repo/src/a.sh" 12
  write_lines "$repo/src/b.sh" 30
  write_lines "$repo/src/test/b_test.sh" 9
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" --estimate src/a.sh src/b.sh
  assert_eq 0 "$status"
  assert_eq "prod=42 test=0 other=0 files=2 warn=400 hard=none files-warn=none" "$output"
  run --separate-stderr bash "$DIFF_BUDGET" --estimate src/a.sh src/New.java
  assert_eq 0 "$status"
  assert_eq "prod=52 test=0 other=0 files=2 warn=400 hard=none files-warn=none" "$output"
  [[ $stderr == *"src/New.java"* ]] || fail "missing path not noted: $stderr"
  [[ $stderr == *40* ]] || fail "40-line note missing: $stderr"
  # a directory counts every file under it, classified per file
  run --separate-stderr bash "$DIFF_BUDGET" --estimate src
  assert_eq 0 "$status"
  assert_eq "prod=42 test=9 other=0 files=3 warn=400 hard=none files-warn=none" "$output"
}

@test "diff-budget: works in a plain (non-Gerrit) repo and --json is valid" {
  local repo
  repo=$(make_plain_repo)
  write_lines "$repo/ten.sh" 10
  write_lines "$repo/ten_test.sh" 5
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: ten"
  cd "$repo"
  run --separate-stderr bash "$DIFF_BUDGET" HEAD
  assert_eq 0 "$status"
  assert_eq "prod=10 test=5 other=0 files=2 warn=400 hard=none files-warn=none" "$output"
  run --separate-stderr bash "$DIFF_BUDGET" --json HEAD
  assert_eq 0 "$status"
  printf '%s\n' "$output" | jq -e '
    .prod == 10 and .test == 5 and .other == 0 and .total == 15 and .files == 2
    and .budget.lines == 400 and .budget.hard_lines == null and .budget.files == null
    and .status == "ok"' >/dev/null || fail "$output"
  git config gerrit-stack.budget.hard-lines 8
  run --separate-stderr bash "$DIFF_BUDGET" --json HEAD
  assert_eq 3 "$status"
  printf '%s\n' "$output" | jq -e '.budget.hard_lines == 8 and .status == "over-hard"' >/dev/null || fail "$output"
  run --separate-stderr bash "$DIFF_BUDGET" no-such-rev
  assert_eq 2 "$status"
}

# ================================================================ install-commit-msg-hook

@test "install-commit-msg-hook: --from installs an executable hook that passes its self-test" {
  local repo hooks
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  rm -f "$hooks/commit-msg"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$FIXTURE_HOOK"
  assert_eq 0 "$status"
  assert_eq "$(physical_path "$hooks")/commit-msg" "$output"
  [ -x "$hooks/commit-msg" ] || fail "hook not executable"
  cmp -s "$FIXTURE_HOOK" "$hooks/commit-msg" || fail "hook content differs from source"
  # the installed hook really stamps commits
  commit_file "$repo" z.txt "z" "feat: z" >/dev/null
  assert_eq 1 "$(git -C "$repo" log -1 --format=%B | grep -c '^Change-Id: I')"
  # preflight is green again
  run --separate-stderr bash "$CHAIN_STATUS" --preflight
  assert_eq 0 "$status"
}

@test "install-commit-msg-hook: refuses to overwrite a different hook unless --force" {
  local repo hooks
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  printf '#!/bin/sh\nexit 0\n' > "$hooks/commit-msg"
  chmod +x "$hooks/commit-msg"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$FIXTURE_HOOK"
  assert_eq 1 "$status"
  [[ $stderr == *--force* ]] || fail "$stderr"
  assert_eq "$(printf '#!/bin/sh\nexit 0\n')" "$(cat "$hooks/commit-msg")" "hook untouched"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$FIXTURE_HOOK" --force
  assert_eq 0 "$status"
  cmp -s "$FIXTURE_HOOK" "$hooks/commit-msg" || fail "hook not replaced"
  # identical hook already there: re-run without --force is fine
  run --separate-stderr bash "$INSTALL_HOOK" --from "$FIXTURE_HOOK"
  assert_eq 0 "$status"
}

@test "install-commit-msg-hook: --host file://<dir> downloads <host>/tools/hooks/commit-msg" {
  local repo hooks srv
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  rm -f "$hooks/commit-msg"
  srv="$BATS_TEST_TMPDIR/srv"
  mkdir -p "$srv/tools/hooks"
  cat "$FIXTURE_HOOK" > "$srv/tools/hooks/commit-msg"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK" --host "file://$srv/"
  assert_eq 0 "$status"
  [ -x "$hooks/commit-msg" ] || fail "hook not installed"
  cmp -s "$FIXTURE_HOOK" "$hooks/commit-msg" || fail "hook content differs"
}

@test "install-commit-msg-hook: uses gerrit-stack.host from config when no --host" {
  local repo hooks srv
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  rm -f "$hooks/commit-msg"
  srv="$BATS_TEST_TMPDIR/srv"
  mkdir -p "$srv/tools/hooks"
  cat "$FIXTURE_HOOK" > "$srv/tools/hooks/commit-msg"
  git -C "$repo" config gerrit-stack.host "file://$srv"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK"
  assert_eq 0 "$status"
  [ -x "$hooks/commit-msg" ] || fail "hook not installed"
}

@test "install-commit-msg-hook: no host and no --from exits 1 with a hint; missing --from file exits 1" {
  local repo hooks
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  rm -f "$hooks/commit-msg"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK"
  assert_eq 1 "$status"
  [[ $stderr == *--host* ]] || fail "$stderr"
  [ ! -e "$hooks/commit-msg" ] || fail "hook should not exist"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$BATS_TEST_TMPDIR/nope"
  assert_eq 1 "$status"
  run --separate-stderr bash "$INSTALL_HOOK" --host "file://$BATS_TEST_TMPDIR/nowhere"
  assert_eq 1 "$status"
  [ ! -e "$hooks/commit-msg" ] || fail "hook should not exist after failed download"
}

@test "install-commit-msg-hook: a source that is not the Gerrit hook fails the self-test" {
  local repo hooks
  repo=$(make_gerrit_repo)
  hooks=$(hooks_dir "$repo")
  rm -f "$hooks/commit-msg"
  printf '#!/bin/sh\n# Change-Id mentioned but never added\nexit 0\n' > "$BATS_TEST_TMPDIR/fake"
  cd "$repo"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$BATS_TEST_TMPDIR/fake"
  assert_eq 1 "$status"
  [[ $stderr == *self-test* ]] || fail "$stderr"
}

@test "install-commit-msg-hook: outside a git repo exits 1" {
  cd "$BATS_TEST_TMPDIR"
  run --separate-stderr bash "$INSTALL_HOOK" --from "$FIXTURE_HOOK"
  assert_eq 1 "$status"
  [[ $stderr == *git* ]] || fail "$stderr"
}
