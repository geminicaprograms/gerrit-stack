#!/usr/bin/env bats
# tests/hooks.bats — scripts/session-start.sh, git-guard.sh, git-post.sh,
# stop-check.sh (WP A1). One test per hook rule (SPEC 119–138).

load helpers

setup() {
  common_setup
  SS="$REPO_ROOT/scripts/session-start.sh"
  GUARD="$REPO_ROOT/scripts/git-guard.sh"
  POST="$REPO_ROOT/scripts/git-post.sh"
  STOP="$REPO_ROOT/scripts/stop-check.sh"
  export GERRIT_STACK_TRACE="$BATS_TEST_TMPDIR/trace"
}

# assert_contains <haystack> <needle> [label]
assert_contains() {
  case "$1" in
    *"$2"*) ;;
    *) printf 'assert_contains%s failed:\n  needle: %s\n  haystack: %s\n' \
         "${3:+ ($3)}" "$2" "$1" >&2; return 1 ;;
  esac
}

# assert_not_contains <haystack> <needle> [label]
assert_not_contains() {
  case "$1" in
    *"$2"*) printf 'assert_not_contains%s failed:\n  needle: %s\n  haystack: %s\n' \
         "${3:+ ($3)}" "$2" "$1" >&2; return 1 ;;
  esac
}

# assert_silent — status 0, empty stdout and stderr
assert_silent() {
  assert_eq 0 "$status" status
  assert_eq "" "$output" stdout
  assert_eq "" "$stderr" stderr
}

# assert_deny <substring> — status 2, stderr mentions <substring>, no stdout
assert_deny() {
  assert_eq 2 "$status" status
  assert_contains "$stderr" "$1" stderr
  assert_eq "" "$output" stdout
}

# ask_reason — permissionDecisionReason of the JSON in $output (fails unless ask)
ask_reason() {
  assert_eq 0 "$status" status
  assert_eq "" "$stderr" stderr
  assert_eq ask "$(printf '%s' "$output" | jq -r .hookSpecificOutput.permissionDecision)" permissionDecision
  assert_eq PreToolUse "$(printf '%s' "$output" | jq -r .hookSpecificOutput.hookEventName)" hookEventName
  printf '%s' "$output" | jq -r .hookSpecificOutput.permissionDecisionReason
}

trace_has() {
  grep -q "$1" "$GERRIT_STACK_TRACE" || fail "trace lacks '$1': $(cat "$GERRIT_STACK_TRACE" 2>/dev/null)"
}

# ---------------------------------------------------------------- non-Gerrit

@test "all four hooks are silent in a plain repo" {
  local repo
  repo=$(make_plain_repo)
  run_hook "$SS" "$(hook_json SessionStart "$repo" "")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "git commit --no-verify -m x")"
  assert_silent
  run_hook "$POST" "$(hook_json PostToolUse "$repo" "git commit -m x")"
  assert_silent
  run_hook "$STOP" "$(hook_json Stop "$repo" "")"
  assert_silent
}

@test "all four hooks are silent outside any repo and on bad input" {
  run_hook "$SS" "$(hook_json SessionStart "$BATS_TEST_TMPDIR" "")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$BATS_TEST_TMPDIR" "git push origin master")"
  assert_silent
  run_hook "$GUARD" "not json at all"
  assert_silent
  run_hook "$STOP" ""
  assert_silent
  run_hook "$POST" "$(hook_json PostToolUse "$BATS_TEST_TMPDIR/nope" "git commit -m x")"
  assert_silent
}

# ---------------------------------------------------------------- SessionStart

@test "session-start: Gerrit repo prints additionalContext with remote, branch, hook installed, chain" {
  local repo ctx
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: a" >/dev/null
  run_hook "$SS" "$(hook_json SessionStart "$repo" "")"
  assert_eq 0 "$status"
  assert_eq "" "$stderr"
  assert_eq SessionStart "$(printf '%s' "$output" | jq -r .hookSpecificOutput.hookEventName)"
  ctx=$(printf '%s' "$output" | jq -r .hookSpecificOutput.additionalContext)
  assert_contains "$ctx" "[gerrit-stack]"
  assert_contains "$ctx" "remote: origin"
  assert_contains "$ctx" "branch: master"
  assert_contains "$ctx" "project: demo"
  assert_contains "$ctx" "hook: installed"
  assert_contains "$ctx" "1 ahead of origin/master"
  assert_contains "$ctx" "Default workflow (not optional)"
  assert_contains "$ctx" "gerrit-review"
  assert_contains "$ctx" "get_related_changes"
  assert_not_contains "$ctx" "MISSING"
  trace_has $'session-start\tstart\tcontext'
}

@test "session-start: hook MISSING when commit-msg is absent, with install command" {
  local repo ctx
  repo=$(make_gerrit_repo)
  rm -f "$repo/.git/hooks/commit-msg"
  run_hook "$SS" "$(hook_json SessionStart "$repo" "")"
  assert_eq 0 "$status"
  ctx=$(printf '%s' "$output" | jq -r .hookSpecificOutput.additionalContext)
  assert_contains "$ctx" "hook: MISSING"
  assert_contains "$ctx" "install-commit-msg-hook.sh"
  assert_contains "$ctx" "0 ahead of origin/master"
}

# ---------------------------------------------------------------- git-guard: commit

@test "guard: plain commit in a Gerrit repo is silent" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit -m "feat: x"')"
  assert_silent
  trace_has $'git-guard\tcommit\tsilent'
}

@test "guard: non-git and read-only git commands are silent" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git status && git log --oneline -3')"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'echo hi')"
  assert_silent
}

@test "guard: commit with a hand-written Change-Id is denied" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" $'git commit -m "feat: x\n\nChange-Id: I0123456789abcdef0123456789abcdef01234567"')"
  assert_deny "Change-Id"
  trace_has $'git-guard\tcommit\tdeny'
}

@test "guard: commit --no-verify / -n is denied" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --no-verify -m x')"
  assert_deny "no-verify"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit -n -m x')"
  assert_deny "no-verify"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit -anm x')"
  assert_deny "no-verify"
}

@test "guard: commit --amend -m / --message is denied, --amend --no-edit is silent" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend -m "new"')"
  assert_deny "amend"
  assert_contains "$stderr" "--no-edit"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend --message=new')"
  assert_deny "amend"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend --no-edit')"
  assert_silent
}

@test "guard: commit --amend -F with a file that keeps HEAD's Change-Id is silent; without it asks" {
  local repo id
  repo=$(make_gerrit_repo)
  commit_file "$repo" f.txt one "feat: one" >/dev/null
  id=$(git -C "$repo" log -1 --format=%B | sed -n 's/^Change-Id: //p')
  printf 'feat: one\n\nJustification: over budget on purpose.\n\nChange-Id: %s\n' "$id" > "$repo/keep.txt"
  printf 'feat: one\n\nno trailer here\n' > "$repo/drop.txt"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend -F keep.txt')"
  [ "$status" -eq 0 ]
  [ -z "$output" ]
  trace_has $'git-guard\tcommit\tsilent'
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend --file=drop.txt')"
  assert_contains "$(ask_reason)" "Change-Id"
}

@test "guard: commit --amend -F asks" {
  local repo reason
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend -F msg.txt')"
  reason=$(ask_reason)
  assert_contains "$reason" "Change-Id"
  trace_has $'git-guard\tcommit\task'
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend --file=msg.txt')"
  reason=$(ask_reason)
  assert_contains "$reason" "Change-Id"
}

@test "guard: commit with the commit-msg hook missing is denied with the install command" {
  local repo
  repo=$(make_gerrit_repo)
  rm -f "$repo/.git/hooks/commit-msg"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit -m x')"
  assert_deny "install-commit-msg-hook.sh"
}

@test "guard: compound command — git add . && git commit --no-verify is denied" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git add . && git commit --no-verify -m x')"
  assert_deny "no-verify"
}

@test "guard: git -C <sub> resolves the sub repo (Gerrit sub under a plain cwd)" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$BATS_TEST_TMPDIR" 'git -C work commit --no-verify -m x')"
  assert_deny "no-verify"
  # a plain sub repo next to it stays silent
  make_plain_repo >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$BATS_TEST_TMPDIR" 'git -C plain commit --no-verify -m x')"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$BATS_TEST_TMPDIR" 'cd plain && git commit --no-verify -m x')"
  assert_silent
}

@test "guard: strictest decision wins across invocations (ask + deny → deny)" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git commit --amend -F m.txt; git commit -n -m x')"
  assert_deny "no-verify"
}

# ---------------------------------------------------------------- git-guard: push

@test "guard: push refs/for with a 2-commit chain asks, lists both, no topic talk" {
  local repo a b reason
  repo=$(make_gerrit_repo)
  a=$(commit_file "$repo" a.txt a "feat: first")
  b=$(commit_file "$repo" b.txt b "feat: second")
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  reason=$(ask_reason)
  assert_contains "$reason" "Push 2 change(s) to refs/for/master"
  assert_contains "$reason" "grouping: none"
  assert_contains "$reason" "${a:0:7} feat: first"
  assert_contains "$reason" "${b:0:7} feat: second"
  assert_not_contains "$reason" "topic"
  trace_has $'git-guard\tpush\task'
}

@test "guard: push with %topic= and grouping unset warns about submitWholeTopic" {
  local repo reason
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master%topic=t')"
  reason=$(ask_reason)
  assert_contains "$reason" "Push 1 change(s) to refs/for/master"
  assert_contains "$reason" "grouping: topic t"
  assert_contains "$reason" "submitWholeTopic"
}

@test "guard: push with %topic= and grouping=topic configured does not warn; %t= is a hashtag" {
  local repo reason
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  git -C "$repo" config gerrit-stack.grouping topic
  git -C "$repo" config gerrit-stack.group-name t
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master%topic=t')"
  reason=$(ask_reason)
  assert_contains "$reason" "grouping: topic t"
  assert_not_contains "$reason" "submitWholeTopic"
  git -C "$repo" config --unset gerrit-stack.grouping
  git -C "$repo" config --unset gerrit-stack.group-name
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master%t=my-tag,wip')"
  reason=$(ask_reason)
  assert_contains "$reason" "grouping: hashtag my-tag"
  assert_not_contains "$reason" "submitWholeTopic"
}

@test "guard: push grouping falls back to config when the command has no %options" {
  local repo reason
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  git -C "$repo" config gerrit-stack.grouping hashtag
  git -C "$repo" config gerrit-stack.group-name feat-x
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  reason=$(ask_reason)
  assert_contains "$reason" "grouping: hashtag feat-x"
}

@test "guard: bare push with no refspec uses remote.<r>.push (refs/for → ask)" {
  local repo reason
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push')"
  reason=$(ask_reason)
  assert_contains "$reason" "Push 1 change(s) to refs/for/master"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin')"
  reason=$(ask_reason)
  assert_contains "$reason" "Push 1 change(s) to refs/for/master"
}

@test "guard: direct push (bare branch, refs/heads, no refspec without refs/for) is denied" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin master')"
  assert_deny "refs/for/master"
  trace_has $'git-guard\tpush\tdeny'
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/heads/master')"
  assert_deny "refs/for/master"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push -u origin master')"
  assert_deny "refs/for/master"
  git -C "$repo" config --unset remote.origin.push
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push')"
  assert_deny "refs/for/master"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin')"
  assert_deny "refs/for/master"
}

@test "guard: direct push is silent when allow-direct-push=true" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  git -C "$repo" config gerrit-stack.allow-direct-push true
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin master')"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/heads/master')"
  assert_silent
}

@test "guard: force push to refs/for is denied" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push --force origin HEAD:refs/for/master')"
  assert_deny "force"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push -f origin HEAD:refs/for/master')"
  assert_deny "force"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push --force-with-lease origin HEAD:refs/for/master')"
  assert_deny "force"
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin +HEAD:refs/for/master')"
  assert_deny "force"
}

@test "guard: push refs/for with a fixup!/squash! commit in the chain is denied" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  commit_file "$repo" a.txt a2 "fixup! feat: first" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_deny "autosquash"
  assert_contains "$stderr" "fixup!"
}

@test "guard: push refs/for with a commit lacking a Change-Id is denied with shas and the repair command" {
  local repo bad
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  printf 'b\n' > "$repo/b.txt"
  git -C "$repo" add b.txt
  git -C "$repo" commit -q --no-verify -m "feat: no id"
  bad=$(git -C "$repo" rev-parse --short=7 HEAD)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_deny "Change-Id"
  assert_contains "$stderr" "$bad"
  assert_contains "$stderr" "git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' refs/remotes/origin/master"
}

@test "guard: push of a non-branch refspec (tags) is silent" {
  local repo
  repo=$(make_gerrit_repo)
  git -C "$repo" tag v1
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin v1:refs/tags/v1')"
  assert_silent
}

# ---------------------------------------------------------------- git-post

@test "post: good commit → silent, session marker written, snapshot refreshed" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"' sess-1)"
  assert_silent
  [ -f "$repo/.git/gerrit-stack/session-sess-1" ] || fail "session marker missing"
  [ -s "$repo/.git/gerrit-stack/chain-ids" ] || fail "snapshot missing"
  trace_has $'git-post\tcommit\tsilent'
}

@test "post: commit made with --no-verify → feedback mentions Change-Id" {
  local repo
  repo=$(make_gerrit_repo)
  printf 'b\n' > "$repo/b.txt"
  git -C "$repo" add b.txt
  git -C "$repo" commit -q --no-verify -m "feat: no id"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit --no-verify -m "feat: no id"' sess-2)"
  assert_eq 2 "$status"
  assert_contains "$stderr" "Change-Id"
  assert_eq "" "$output"
  [ -f "$repo/.git/gerrit-stack/session-sess-2" ] || fail "session marker missing"
  trace_has $'git-post\tcommit\tfeedback'
}

@test "post: commit missing a required footer → feedback names it" {
  local repo
  repo=$(make_gerrit_repo)
  git -C "$repo" config gerrit-stack.footers "Release-Notes,Bug"
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "Release-Notes"
  assert_contains "$stderr" "Bug"
  git -C "$repo" commit -q --amend -m "$(printf 'feat: first\n\nRelease-Notes: yes\nBug: 1')"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit --amend --no-edit')"
  assert_silent
}

# big_file <path> <n> — a file with n lines (production code unless the path
# says test/docs).
big_file() {
  mkdir -p "$(dirname "$1")"
  awk -v n="$2" 'BEGIN { for (i = 1; i <= n; i++) printf "line %d\n", i }' > "$1"
}

@test "post: commit over the 400-line production warning → asks for a one-line justification" {
  local repo
  repo=$(make_gerrit_repo)
  big_file "$repo/src/main/Feature.java" 450
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: add feature"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: add feature"')"
  assert_eq 2 "$status"
  assert_eq "" "$output"
  assert_contains "$stderr" "prod=450"
  assert_contains "$stderr" "one-line justification"
  assert_contains "$stderr" "mechanical"
  assert_contains "$stderr" "stay one change"
  assert_contains "$stderr" "Do not split a concern"
  assert_not_contains "$stderr" "retro-split"
  assert_not_contains "$stderr" "smaller changes"
}

@test "post: tests do not count — 900 test lines + 300 production lines are silent" {
  local repo
  repo=$(make_gerrit_repo)
  big_file "$repo/src/main/Feature.java" 300
  big_file "$repo/src/test/FeatureTest.java" 900
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: add feature"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: add feature"')"
  assert_silent
}

@test "post: a refactor/build/chore commit marked mechanical gets no size feedback" {
  local repo
  repo=$(make_gerrit_repo)
  big_file "$repo/src/main/Client.java" 1200
  git -C "$repo" add .
  git -C "$repo" commit -q -m "refactor: migrate HTTP client to v5" \
    -m "Mechanical migration produced by the vendor codemod; no behaviour change."
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -F msg.txt')"
  assert_silent
  # the same size without the "mechanical" marker gets the note
  big_file "$repo/src/main/Other.java" 1200
  git -C "$repo" add .
  git -C "$repo" commit -q -m "refactor: migrate the other client" -m "Same as before."
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -F msg.txt')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "one-line justification"
  # "mechanical" in the body of a feat commit does not exempt it
  big_file "$repo/src/main/Third.java" 1200
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: third" -m "Not mechanical at all."
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -F msg.txt')"
  assert_eq 2 "$status"
}

@test "post: a message-only --amend after the size note stays silent" {
  local repo msg
  repo=$(make_gerrit_repo)
  big_file "$repo/src/main/Feature.java" 450
  git -C "$repo" add .
  git -C "$repo" commit -q -m "feat: add feature"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: add feature"')"
  assert_eq 2 "$status"
  msg="$BATS_TEST_TMPDIR/msg"
  git -C "$repo" log -1 --format=%B | awk 'NR == 1 { print; print ""; print "Size: one parser; splitting it would leave half a grammar."; next } { print }' > "$msg"
  git -C "$repo" commit -q --amend -F "$msg"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" "git commit --amend -F $msg")"
  assert_silent
}

@test "post: diff-budget.sh exit 3 (team hard cap) → cut by concern, never fragments (stubbed)" {
  local repo tmp
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  # copy the hook into a scratch scripts dir with a stub diff-budget.sh
  tmp="$BATS_TEST_TMPDIR/scripts"
  mkdir -p "$tmp/lib"
  cat "$REPO_ROOT/scripts/lib/gerrit-detect.sh" > "$tmp/lib/gerrit-detect.sh"
  cat "$REPO_ROOT/scripts/lib/chain.sh" > "$tmp/lib/chain.sh"
  cat "$POST" > "$tmp/git-post.sh"
  printf '#!/usr/bin/env bash\necho "prod=999 test=0 other=0 files=20 warn=400 hard=800 files-warn=none"\nexit 3\n' > "$tmp/diff-budget.sh"
  chmod +x "$tmp/diff-budget.sh"
  run_hook "$tmp/git-post.sh" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "prod=999"
  assert_contains "$stderr" "hard cap"
  assert_contains "$stderr" "concern boundaries"
  assert_contains "$stderr" "one-line justification"
}

@test "post: rebase that drops a Change-Id → feedback with lost:" {
  local repo id2
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  commit_file "$repo" b.txt b "feat: second" >/dev/null
  id2=$(git -C "$repo" log -1 --format=%B | sed -n 's/^Change-Id: //p')
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: second"')"
  assert_silent
  git -C "$repo" reset -q --hard HEAD~1
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git -c sequence.editor=true rebase -i HEAD~2')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "lost: $id2"
  trace_has $'git-post\trebase\tfeedback'
  # the snapshot is NOT refreshed on drift: --verify-ids still reports the loss
  cd "$repo"
  run --separate-stderr bash "$REPO_ROOT/scripts/chain-status.sh" --verify-ids
  assert_eq 1 "$status" "verify-ids status"
  assert_contains "$output" "lost: $id2" "verify-ids output"
  # ... and so does the next rewrite hook, until --snapshot is run explicitly
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git rebase origin/master')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "lost: $id2"
  run --separate-stderr bash "$REPO_ROOT/scripts/chain-status.sh" --snapshot
  assert_eq 0 "$status" "snapshot status"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git rebase origin/master')"
  assert_silent
}

@test "post: rebase without drift refreshes the snapshot and stays silent" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"')"
  assert_silent
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git rebase origin/master')"
  assert_silent
  [ -s "$repo/.git/gerrit-stack/chain-ids" ] || fail "snapshot missing"
  trace_has $'git-post\trebase\tsilent'
}

@test "post: git commit --fixup=<sha> (no Change-Id by design) → silent, marker + snapshot written" {
  local repo sha
  repo=$(make_gerrit_repo)
  sha=$(commit_file "$repo" a.txt a "feat: first")
  printf 'a2\n' > "$repo/a.txt"
  git -C "$repo" add a.txt
  git -C "$repo" commit -q --fixup="$sha"
  assert_eq "fixup! feat: first" "$(git -C "$repo" log -1 --format=%s)" subject
  assert_eq 0 "$(git -C "$repo" log -1 --format=%B | grep -c '^Change-Id:')" "fixup has no id"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" "git commit --fixup=$sha" sess-fx)"
  assert_silent
  [ -f "$repo/.git/gerrit-stack/session-sess-fx" ] || fail "session marker missing"
  [ -s "$repo/.git/gerrit-stack/chain-ids" ] || fail "snapshot missing"
  trace_has $'git-post\tcommit\tsilent'
}

@test "post: push response with /c/demo/+/12 → feedback lists 12 and get_related_changes" {
  local repo resp
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  commit_file "$repo" b.txt b "feat: second" >/dev/null
  resp=$(jq -cn '{stdout:"", stderr:"remote: \nremote: Processing changes: new: 2, done\nremote: \nremote: SUCCESS\nremote: \nremote:   http://localhost:8080/c/demo/+/11 feat: first [NEW]\nremote:   http://localhost:8080/c/demo/+/12 feat: second [NEW]\nremote: \nTo ../remote.git\n * [new reference]   HEAD -> refs/for/master\n", interrupted:false}')
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git push origin HEAD:refs/for/master' sess "$resp")"
  assert_eq 2 "$status"
  assert_contains "$stderr" "pushed changes 11,12"
  assert_contains "$stderr" "get_related_changes(12)"
  assert_contains "$stderr" "grouping: none"
  trace_has $'git-post\tpush\tfeedback'
}

@test "post: push response 'no new changes' → feedback explains it" {
  local repo resp
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  resp=$(jq -cn '{stdout:"", stderr:"To ../remote.git\n ! [remote rejected] HEAD -> refs/for/master (no new changes)\nerror: failed to push some refs\n", interrupted:false}')
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git push origin HEAD:refs/for/master' sess "$resp")"
  assert_eq 2 "$status"
  assert_contains "$stderr" "no new changes"
}

@test "post: push with an empty response and non-commit verbs are silent" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_silent
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git status')"
  assert_silent
}

# ---------------------------------------------------------------- stop-check

@test "stop: stop_hook_active=true → silent" {
  local repo json
  repo=$(make_gerrit_repo)
  json=$(hook_json Stop "$repo" "" sess-a | jq -c '.stop_hook_active = true')
  mkdir -p "$repo/.git/gerrit-stack"
  : > "$repo/.git/gerrit-stack/session-sess-a"
  git -C "$repo" commit -q --allow-empty --no-verify -m "feat: no id"
  run_hook "$STOP" "$json"
  assert_silent
}

@test "stop: no session marker → silent even with a bad chain" {
  local repo
  repo=$(make_gerrit_repo)
  git -C "$repo" commit -q --allow-empty --no-verify -m "feat: no id"
  run_hook "$STOP" "$(hook_json Stop "$repo" "" sess-b)"
  assert_silent
  trace_has $'stop-check\tstop\tsilent'
}

@test "stop: marker + commit without Change-Id → JSON block with repair command" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"' sess-c)"
  git -C "$repo" commit -q --allow-empty --no-verify -m "feat: no id"
  run_hook "$STOP" "$(hook_json Stop "$repo" "" sess-c)"
  assert_eq 0 "$status"
  assert_eq "" "$stderr"
  assert_eq block "$(printf '%s' "$output" | jq -r .decision)"
  assert_contains "$(printf '%s' "$output" | jq -r .reason)" "rebase -i --exec 'git commit --amend --no-edit'"
  assert_contains "$(printf '%s' "$output" | jq -r .reason)" "$(git -C "$repo" rev-parse --short=7 HEAD)"
  trace_has $'stop-check\tstop\tblock'
}

@test "stop: marker + clean chain (fixup! without id tolerated) → silent" {
  local repo
  repo=$(make_gerrit_repo)
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: first"' sess-d)"
  commit_file "$repo" a.txt a2 "fixup! feat: first" >/dev/null
  run_hook "$STOP" "$(hook_json Stop "$repo" "" sess-d)"
  assert_silent
}
