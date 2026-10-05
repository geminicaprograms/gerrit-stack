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
# "refactor ... and ..." commit (+6). Every file is *.txt, so all of it counts
# as other_lines (docs), none as production lines.
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
  echo "$output" | jq -e '.budget == {lines: 400, files: null}' >/dev/null
  echo "$output" | jq -e '.changes | length == 3' >/dev/null
  echo "$output" | jq -e '.changes[0] | .subject == "feat: add a" and .lines == 2 and .files == 1 and .change_ids == 1 and .conventional_type == "feat" and .is_fixup == false and .single_concern == true' >/dev/null
  echo "$output" | jq -e '.changes[1] | .lines == 3 and .files == 2 and .conventional_type == "fix"' >/dev/null
  echo "$output" | jq -e '.changes[2] | .conventional_type == "refactor" and .single_concern == false' >/dev/null
  echo "$output" | jq -e '.lines_median == 3 and .lines_p75 == 6 and .lines_max == 6 and .files_median == 1' >/dev/null
  echo "$output" | jq -e '[.changes[] | [.prod_lines, .test_lines, .other_lines]] == [[0, 0, 2], [0, 0, 3], [0, 0, 6]]' >/dev/null
  echo "$output" | jq -e '.prod_lines_median == 0 and .prod_lines_max == 0 and .test_lines_total == 0' >/dev/null
  echo "$output" | jq -e '.within_budget_pct == 100 and .one_change_id_pct == 100 and .conventional_pct == 100' >/dev/null
  echo "$output" | jq -e '.single_concern_pct == 66.7 and .fixups_present == false' >/dev/null
  echo "$output" | jq -e '.builds_alone_pct == null and .violations == null and .refs_for_pushed == null' >/dev/null
  echo "$output" | jq -e '.change_id_set | length == 3 and all(.[]; test("^I[0-9a-f]{40}$"))' >/dev/null
}

@test "budget override via git config and fixup detection" {
  git -C "$WORK" config gerrit-stack.budget.lines 5
  mkdir -p "$WORK/src"
  printf '1\n2\n3\n4\n5\n6\n' > "$WORK/src/x.sh"
  git -C "$WORK" add -A
  git -C "$WORK" commit -q -m "fixup! feat: add a"
  run bash "$SCRIPT" --json "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.budget.lines == 5 and .chain_length == 4' >/dev/null
  # only the 6 production lines of src/x.sh exceed the warning; the 6-line big.txt does not
  echo "$output" | jq -e '.within_budget_pct == 75 and [.changes[].within_budget] == [true, true, true, false]' >/dev/null
  echo "$output" | jq -e '.changes[3].prod_lines == 6 and .prod_lines_max == 6' >/dev/null
  echo "$output" | jq -e '.fixups_present == true and .changes[3].is_fixup == true and .changes[3].change_ids == 0 and .changes[3].conventional_type == null' >/dev/null
  echo "$output" | jq -e '.one_change_id_pct == 75' >/dev/null
}

@test "budget.lines from the team file .gerrit-stack; git config wins" {
  git config -f "$WORK/.gerrit-stack" gerrit-stack.budget.lines 12
  git config -f "$WORK/.gerrit-stack" gerrit-stack.budget.files 3
  run bash "$SCRIPT" --json "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.budget == {lines: 12, files: 3}' >/dev/null
  git -C "$WORK" config gerrit-stack.budget.lines 30
  run bash "$SCRIPT" --json "$WORK"
  echo "$output" | jq -e '.budget == {lines: 30, files: 3}' >/dev/null
  git config -f "$WORK/.gerrit-stack" gerrit-stack.budget.lines lots
  git config -f "$WORK/.gerrit-stack" --unset gerrit-stack.budget.files
  git -C "$WORK" config --unset gerrit-stack.budget.lines
  run bash "$SCRIPT" --json "$WORK"
  echo "$output" | jq -e '.budget == {lines: 400, files: null}' >/dev/null
}

@test "human table by default" {
  run bash "$SCRIPT" "$WORK"
  [ "$status" -eq 0 ]
  [[ "$output" == *"chain: 3 change(s)"* ]]
  [[ "$output" == *"feat: add a"* ]]
  [[ "$output" == *"lines prod  test  files"* ]]
  [[ "$output" == *"prod lines: median 0  max 0  |  test lines: total 0  |  prod <= 400: 100% (info)"* ]]
  [[ "$output" != *"within budget"* ]]
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

# ---------------------------------------------------------------------------
# Split quality: --concerns, tests_travel, --verify-cmd per change.
# ---------------------------------------------------------------------------

# java_repo — fresh repo $J (root commit only, real commit-msg hook) and a
# concern map $MAP in the block form the bench cases use.
java_repo() {
  J="$BATS_TEST_TMPDIR/java"
  MAP="$BATS_TEST_TMPDIR/case.yaml"
  git init -q -b master "$J"
  hooks=$(git -C "$J" rev-parse --git-path hooks)
  case "$hooks" in /*) ;; *) hooks="$J/$hooks" ;; esac
  mkdir -p "$hooks"
  cp "$HOOK" "$hooks/commit-msg"
  chmod +x "$hooks/commit-msg"
  echo base > "$J/README.md"
  git -C "$J" add -A
  git -C "$J" commit -q -m "chore: initial import"
  cat > "$MAP" <<'YAML'
context:
  scaffold_script: fixture.sh
kind: implement
concerns:
  # comment inside the list
  - name: setting
    paths:
      - '/DemoPluginConfig(Test|IT)?\.java$'
      - 'Documentation/config\.md$'
  - name: rest
    paths: ['/[A-Za-z]*Maint[a-z]*(Action|View)(Test)?\.java$']
  - name: ssh
    paths:
      - "/[A-Za-z]*Command(Test)?\\.java$"

nudges:
  stage1: "not a concern"
YAML
}

# jcommit <subject> <path>... — touch every path (append a line) and commit.
jcommit() {
  local subject=$1 f
  shift
  for f in "$@"; do
    mkdir -p "$J/$(dirname "$f")"
    echo "$subject" >> "$J/$f"
  done
  git -C "$J" add -A
  git -C "$J" commit -q -m "$subject"
}

M=src/main/java/demo
T=src/test/java/demo

@test "--concerns: pure chain, every concern in one change, tests travel" {
  java_repo
  jcommit "feat: add setting" "$M/DemoPluginConfig.java" "$T/DemoPluginConfigTest.java" src/main/resources/Documentation/config.md
  jcommit "feat: add rest view" "$M/GetMaintenanceView.java" "$T/GetMaintenanceViewTest.java" "$M/Module.java"
  jcommit "feat: add ssh command" "$M/MaintenanceCommand.java" "$T/MaintenanceCommandTest.java"
  jcommit "docs: readme" README.md
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.concerns_defined == ["setting", "rest", "ssh"] and .concerns_seen == ["setting", "rest", "ssh"]' >/dev/null
  echo "$output" | jq -e '[.changes[].concerns] == [["setting"], ["rest"], ["ssh"], []]' >/dev/null
  echo "$output" | jq -e '.purity_pct == 100 and .completeness_pct == 100 and .tests_travel_pct == 100' >/dev/null
  # paths no concern claims are reported and do not count
  echo "$output" | jq -e '.changes[1].unmapped_paths == ["src/main/java/demo/Module.java"]' >/dev/null
  echo "$output" | jq -e '.changes[3].unmapped_paths == ["README.md"] and .changes[3].tests_travel == null' >/dev/null
  echo "$output" | jq -e '[.changes[0:3][].tests_travel] == [true, true, true]' >/dev/null
}

@test "--concerns: a mixed-concern commit lowers purity, not completeness" {
  java_repo
  jcommit "feat: add setting" "$M/DemoPluginConfig.java" "$T/DemoPluginConfigTest.java"
  jcommit "feat: rest view and ssh command" "$M/GetMaintenanceView.java" "$M/MaintenanceCommand.java" "$T/MaintenanceCommandTest.java"
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].concerns] == [["setting"], ["rest", "ssh"]]' >/dev/null
  echo "$output" | jq -e '.purity_pct == 50 and .completeness_pct == 100' >/dev/null
}

@test "--concerns: a concern split over two commits lowers completeness, not purity" {
  java_repo
  jcommit "feat: add setting" "$M/DemoPluginConfig.java" "$T/DemoPluginConfigTest.java"
  jcommit "feat: add rest view" "$M/GetMaintenanceView.java" "$T/GetMaintenanceViewTest.java"
  jcommit "docs: document the setting" src/main/resources/Documentation/config.md
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].concerns] == [["setting"], ["rest"], ["setting"]]' >/dev/null
  echo "$output" | jq -e '.purity_pct == 100 and .completeness_pct == 50' >/dev/null
  echo "$output" | jq -e '.concerns_seen == ["setting", "rest"]' >/dev/null
}

@test "--concerns: only unmapped paths -> null rates; flow form parses too" {
  java_repo
  jcommit "docs: readme" README.md
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.purity_pct == null and .completeness_pct == null and .concerns_seen == []' >/dev/null
  flow="$BATS_TEST_TMPDIR/flow.yaml"
  cat > "$flow" <<'YAML'
concerns:                  # contract example form
  - {name: docs, paths: ['README', 'config\.md$']}   # regexes over changed paths
  - {paths: ['\.java$'], name: "code"}
kind: implement
YAML
  jcommit "feat: code" "$M/A.java"
  run bash "$SCRIPT" --json --concerns "$flow" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.concerns_defined == ["docs", "code"]' >/dev/null
  echo "$output" | jq -e '[.changes[].concerns] == [["docs"], ["code"]] and .purity_pct == 100' >/dev/null
}

@test "--concerns: reads the real bench concern map" {
  real="$REPO_ROOT/evals/bench-unprompted/maintenance-mode/case.yaml"
  grep -q '^concerns:' "$real" 2>/dev/null || skip "no concerns map in $real"
  java_repo
  jcommit "feat: setting" "$M/DemoPluginConfig.java" "$T/DemoPluginConfigTest.java" "$M/Module.java" BUILD
  jcommit "feat: ping" "$M/PingAction.java" "$T/PingActionTest.java"
  run bash "$SCRIPT" --json --concerns "$real" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.concerns_defined | index("setting") != null and index("ping") != null and length >= 2' >/dev/null
  echo "$output" | jq -e '[.changes[].concerns] == [["setting"], ["ping"]]' >/dev/null
  echo "$output" | jq -e '.changes[0].unmapped_paths == ["BUILD", "src/main/java/demo/Module.java"]' >/dev/null
  echo "$output" | jq -e '.purity_pct == 100 and .completeness_pct == 100' >/dev/null
}

@test "tests_travel: code without a test is counted, doc-only changes are not" {
  java_repo
  jcommit "feat: with test" "$M/A.java" "$T/ATest.java"
  jcommit "feat: without test" "$M/B.java"
  jcommit "docs: only docs" docs/x.md
  jcommit "test: only a test" "$T/BTest.java"
  run bash "$SCRIPT" --json "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].tests_travel] == [true, false, null, null]' >/dev/null
  echo "$output" | jq -e '.tests_travel_pct == 50' >/dev/null
  # without --concerns the concern fields stay null
  echo "$output" | jq -e '.purity_pct == null and .concerns_seen == null and .changes[0].concerns == null' >/dev/null
}

@test "--verify-cmd: a commit that does not build is the only one marked" {
  java_repo
  jcommit "feat: fine" "$M/A.java"
  jcommit "feat: breaks the build" broken.flag
  git -C "$J" rm -q broken.flag
  git -C "$J" commit -q -m "fix: repair the build"
  before_head=$(git -C "$J" rev-parse HEAD)
  before_index=$(git -C "$J" ls-files -s | shasum)
  # the command leaves ignored/untracked output behind; it must not leak into the next change
  run bash "$SCRIPT" --json --verify-cmd 'test ! -e broken.flag && test ! -e out.log && touch out.log' "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].builds_alone] == [true, false, true]' >/dev/null
  echo "$output" | jq -e '.builds_alone_pct == 66.7 and .verify_timeouts == 0 and .verify_error == null' >/dev/null
  [ "$(git -C "$J" rev-parse HEAD)" = "$before_head" ]
  [ "$(git -C "$J" ls-files -s | shasum)" = "$before_index" ]
  [ "$(git -C "$J" status --porcelain)" = "" ]
  [ "$(git -C "$J" symbolic-ref HEAD)" = "refs/heads/master" ]
  [ "$(git -C "$J" worktree list | wc -l | tr -d ' ')" = "1" ]
}

@test "--verify-timeout: a hanging command fails the change (timeout(1) and built-in watchdog)" {
  java_repo
  jcommit "feat: slow" slow.flag
  jcommit "feat: fast again" "$M/A.java"
  git -C "$J" rm -q slow.flag
  git -C "$J" commit -q -m "fix: drop slow"
  cmd='if [ -e slow.flag ]; then sleep 30; fi'
  run bash "$SCRIPT" --json --verify-timeout 1 --verify-cmd "$cmd" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].builds_alone] == [false, false, true]' >/dev/null
  echo "$output" | jq -e '.verify_timeouts == 2 and .verify_timeout == 1 and .builds_alone_pct == 33.3' >/dev/null
  CHAIN_METRICS_NO_TIMEOUT_BIN=1 run bash "$SCRIPT" --json --verify-timeout 1 --verify-cmd "$cmd" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].builds_alone] == [false, false, true] and .verify_timeouts == 2' >/dev/null
  CHAIN_METRICS_NO_TIMEOUT_BIN=1 run bash "$SCRIPT" --json --verify-cmd 'test ! -e slow.flag' "$J"
  echo "$output" | jq -e '[.changes[].builds_alone] == [false, false, true] and .verify_timeouts == 0' >/dev/null
  [ "$(git -C "$J" worktree list | wc -l | tr -d ' ')" = "1" ]
}

@test "human table shows the split-quality columns" {
  java_repo
  jcommit "feat: add setting" "$M/DemoPluginConfig.java" "$T/DemoPluginConfigTest.java"
  jcommit "feat: rest view" "$M/GetMaintenanceView.java"
  run bash "$SCRIPT" --concerns "$MAP" --verify-cmd true "$J"
  [ "$status" -eq 0 ]
  [[ "$output" == *"build tests  concerns  subject"* ]]
  [[ "$output" == *"yes    setting  feat: add setting"* ]]
  [[ "$output" == *"no     rest  feat: rest view"* ]]
  [[ "$output" == *"tests travel 50%  |  purity 100%  |  completeness 100%  |  concerns seen 2/3  |  unmapped paths 0"* ]]
  [[ "$output" == *"builds alone: 100%"* ]]
}

@test "--concerns / --verify-timeout usage errors exit 2" {
  run bash "$SCRIPT" --concerns "$BATS_TEST_TMPDIR/nope.yaml" "$WORK"
  [ "$status" -eq 2 ]
  run bash "$SCRIPT" --verify-timeout soon --verify-cmd true "$WORK"
  [ "$status" -eq 2 ]
}

# ---------------------------------------------------------------------------
# Size: production / test / other lines (information only, never a gate).
# ---------------------------------------------------------------------------

# nlines <path> <n> — write <n> fresh lines to $J/<path>.
nlines() {
  mkdir -p "$J/$(dirname "$1")"
  seq 1 "$2" | sed "s/^/line /" > "$J/$1"
}

# size_chain — three commits on java_repo: a mixed first change, a 2-line edit
# of production code (+2 -2), a docs-only change.
size_chain() {
  java_repo
  nlines "$M/A.java" 10                                        # prod
  nlines "$M/Latest.java" 2                                    # prod (lower-case "test." is not *Test.*)
  nlines BUILD 3                                               # prod
  nlines "$T/ATest.java" 7                                     # test: src/test/ and *Test.*
  nlines tests/run.bats 3                                      # test: tests/
  nlines web/app.spec.ts 2                                     # test: *.spec.*
  nlines pkg/app_test.go 2                                     # test: *_test.*
  nlines scripts/test_x.py 2                                   # test: test_*.*
  nlines notes.rst 1                                           # other: *.rst
  nlines docs/guide.html 5                                     # other: docs/
  nlines src/main/resources/Documentation/config.md 1          # other: Documentation/, *.md
  nlines package-lock.json 6                                   # other: lock
  nlines go.sum 2                                              # other: lock
  nlines Cargo.lock 1                                          # other: *.lock
  printf '\000\001\002' > "$J/logo.bin"                         # binary: counts 0
  git -C "$J" add -A
  git -C "$J" commit -q -m "feat: mixed first change"
  sed -i.bak 's/^line 1$/LINE 1/; s/^line 2$/LINE 2/' "$J/$M/A.java"
  rm -f "$J/$M/A.java.bak"
  git -C "$J" commit -q -am "fix: tweak A"
  echo more >> "$J/README.md"
  git -C "$J" commit -q -am "docs: readme"
}

@test "size: prod / test / other lines per change and the top-level fields" {
  size_chain
  run bash "$SCRIPT" --json "$J"
  [ "$status" -eq 0 ]
  # first change: prod 10+2+3, test 7+3+2+2+2, other 1+5+1+6+2+1, binary 0 (counted as a file)
  echo "$output" | jq -e '.changes[0] | .prod_lines == 15 and .test_lines == 16 and .other_lines == 16 and .lines == 47 and .files == 15' >/dev/null
  echo "$output" | jq -e '.changes[1] | .prod_lines == 4 and .test_lines == 0 and .other_lines == 0 and .lines == 4' >/dev/null
  echo "$output" | jq -e '.changes[2] | .prod_lines == 0 and .test_lines == 0 and .other_lines == 1' >/dev/null
  echo "$output" | jq -e '.prod_lines_median == 4 and .prod_lines_max == 15 and .test_lines_total == 16' >/dev/null
  # gross lines stay for compatibility
  echo "$output" | jq -e '.lines_max == 47 and .lines_median == 4' >/dev/null
  echo "$output" | jq -e '.budget == {lines: 400, files: null} and .within_budget_pct == 100' >/dev/null
}

@test "size: within_budget compares production lines with budget.lines, not gross lines" {
  size_chain
  git -C "$J" config gerrit-stack.budget.lines 20
  run bash "$SCRIPT" --json "$J"
  [ "$status" -eq 0 ]
  # 47 gross lines, 15 production lines: within a 20-line warning
  echo "$output" | jq -e '[.changes[].within_budget] == [true, true, true] and .within_budget_pct == 100' >/dev/null
  git -C "$J" config gerrit-stack.budget.lines 10
  run bash "$SCRIPT" --json "$J"
  echo "$output" | jq -e '[.changes[].within_budget] == [false, true, true] and .within_budget_pct == 66.7' >/dev/null
}

@test "size: human table shows prod / test columns and the size summary" {
  size_chain
  run bash "$SCRIPT" "$J"
  [ "$status" -eq 0 ]
  [[ "$output" == *"prod-line warning=400"* ]]
  [[ "$output" == *"lines prod  test  files type"* ]]
  [[ "$output" == *"47    15    16    15    feat"* ]]
  [[ "$output" == *"prod lines: median 4  max 15  |  test lines: total 16  |  prod <= 400: 100% (info)"* ]]
}

@test "size: empty chain gives null size aggregates" {
  run bash "$SCRIPT" --json --base HEAD "$WORK"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '.chain_length == 0 and .prod_lines_median == null and .prod_lines_max == null and .test_lines_total == null' >/dev/null
}

@test "--concerns: a setting marked joins: first-consumer may travel with its first consumer" {
  java_repo
  # mark the map's setting concern as joining its first consumer
  python3 - "$MAP" <<'PYJ'
import sys
p = sys.argv[1]; s = open(p).read()
s = s.replace("  - name: setting\n", "  - name: setting\n    joins: first-consumer   # ships with its first consumer\n", 1)
open(p, "w").write(s)
PYJ
  jcommit "feat: setting with its rest view" "$M/DemoPluginConfig.java" "$M/GetMaintenanceView.java"
  jcommit "feat: ssh command" "$M/MaintenanceCommand.java" "$T/MaintenanceCommandTest.java"
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  [ "$status" -eq 0 ]
  echo "$output" | jq -e '[.changes[].concerns] == [["setting", "rest"], ["ssh"]]' >/dev/null
  echo "$output" | jq -e '.purity_pct == 100 and .completeness_pct == 100' >/dev/null
  # without the marker the same chain is mixed
  sed -i '' '/joins: first-consumer/d' "$MAP"
  run bash "$SCRIPT" --json --concerns "$MAP" "$J"
  echo "$output" | jq -e '.purity_pct == 50' >/dev/null
}
