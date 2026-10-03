#!/usr/bin/env bats
# tests/conventions.bats — team conventions: the committed .gerrit-stack file,
# the commitlint delegate (git-post.sh, git-guard.sh, session-start.sh) and the
# Conventional Comments guard (comment-guard.sh, git-guard.sh for gerrit-rest.py).
# commitlint is a stub on PATH (tests/helpers.bash stub_commitlint) except in
# the one integration test, which is skipped when the real tool is absent.

load helpers

setup() {
  common_setup
  SS="$REPO_ROOT/scripts/session-start.sh"
  GUARD="$REPO_ROOT/scripts/git-guard.sh"
  POST="$REPO_ROOT/scripts/git-post.sh"
  CG="$REPO_ROOT/scripts/comment-guard.sh"
  REST="python3 \"$REPO_ROOT/scripts/gerrit-rest.py\""
  export GERRIT_STACK_TRACE="$BATS_TEST_TMPDIR/trace"
}

assert_contains() {
  case "$1" in
    *"$2"*) ;;
    *) printf 'assert_contains%s failed:\n  needle: %s\n  haystack: %s\n' \
         "${3:+ ($3)}" "$2" "$1" >&2; return 1 ;;
  esac
}

assert_not_contains() {
  case "$1" in
    *"$2"*) printf 'assert_not_contains%s failed:\n  needle: %s\n  haystack: %s\n' \
         "${3:+ ($3)}" "$2" "$1" >&2; return 1 ;;
  esac
}

assert_silent() {
  assert_eq 0 "$status" status
  assert_eq "" "$output" stdout
  assert_eq "" "$stderr" stderr
}

assert_deny() {
  assert_eq 2 "$status" status
  assert_contains "$stderr" "$1" stderr
  assert_eq "" "$output" stdout
}

ask_reason() {
  assert_eq 0 "$status" status
  assert_eq "" "$stderr" stderr
  assert_eq ask "$(printf '%s' "$output" | jq -r .hookSpecificOutput.permissionDecision)" permissionDecision
  printf '%s' "$output" | jq -r .hookSpecificOutput.permissionDecisionReason
}

trace_has() {
  grep -q "$1" "$GERRIT_STACK_TRACE" || fail "trace lacks '$1': $(cat "$GERRIT_STACK_TRACE" 2>/dev/null)"
}

session_ctx() {
  run_hook "$SS" "$(hook_json SessionStart "$1" "")"
  assert_eq 0 "$status" status
  printf '%s' "$output" | jq -r .hookSpecificOutput.additionalContext
}

# lint_repo — Gerrit repo with a commitlint config and the stub tool on PATH
lint_repo() {
  local repo
  repo=$(make_gerrit_repo)
  add_commitlint_config "$repo"
  printf '%s\n' "$repo"
}

# ---------------------------------------------------------------- SessionStart

@test "session-start: no team choices → no conventions clause" {
  local repo ctx
  repo=$(make_gerrit_repo)
  PATH=$(path_without commitlint)
  ctx=$(session_ctx "$repo")
  assert_not_contains "$ctx" "Team conventions"
  assert_not_contains "$ctx" "commitlint"
  assert_not_contains "$ctx" "Conventional Comments"
}

@test "session-start: names commitlint and Conventional Comments when both are active" {
  local repo ctx
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  team_config "$repo" comment-style conventional
  ctx=$(session_ctx "$repo")
  assert_contains "$ctx" "Team conventions: commit messages are checked by commitlint"
  assert_contains "$ctx" "review comments use Conventional Comments"
}

@test "session-start: only the active convention is named; commit-lint=off silences commitlint" {
  local repo ctx
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  ctx=$(session_ctx "$repo")
  assert_contains "$ctx" "checked by commitlint"
  assert_not_contains "$ctx" "Conventional Comments"
  team_config "$repo" commit-lint off
  team_config "$repo" comment-style conventional
  ctx=$(session_ctx "$repo")
  assert_not_contains "$ctx" "commitlint"
  assert_contains "$ctx" "Team conventions: review comments use Conventional Comments"
}

@test "session-start: commitlint config present but tool missing → one sentence, not 'checked'" {
  local repo ctx
  repo=$(lint_repo)
  PATH=$(path_without commitlint)
  ctx=$(session_ctx "$repo")
  assert_contains "$ctx" "commitlint command is not installed"
  assert_contains "$ctx" "nothing is blocked"
  assert_not_contains "$ctx" "checked by commitlint"
}

# ---------------------------------------------------------------- commitlint: after commit

@test "post: bad subject with commitlint active → feedback with rule lines and the -F repair recipe" {
  local repo id
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  commit_file "$repo" a.txt a "Added rate limit" >/dev/null
  id=$(git -C "$repo" log -1 --format=%B | sed -n 's/^Change-Id: //p')
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "Added rate limit"')"
  assert_eq 2 "$status"
  assert_eq "" "$output"
  assert_contains "$stderr" "commitlint"
  assert_contains "$stderr" "Added rate limit"
  assert_contains "$stderr" "[subject-empty]"
  assert_contains "$stderr" "[type-empty]"
  assert_contains "$stderr" "git commit --amend -F <file>"
  assert_contains "$stderr" "Change-Id: $id"
  assert_contains "$stderr" "never 'git commit --amend -m'"
  trace_has $'git-post\tcommit\tfeedback'
}

@test "post: conforming subject with a Change-Id footer → silent" {
  local repo
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  commit_file "$repo" a.txt a "feat(ping): add rate limit" >/dev/null
  assert_eq 1 "$(git -C "$repo" log -1 --format=%B | grep -c '^Change-Id:')" "hook added the id"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat(ping): add rate limit"')"
  assert_silent
}

@test "post: bad subject is not linted without a config, with commit-lint=off, or without the tool" {
  local repo
  repo=$(make_gerrit_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  commit_file "$repo" a.txt a "Added rate limit" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m x')"
  assert_silent                                   # no commitlint config
  add_commitlint_config "$repo"
  git -C "$repo" config gerrit-stack.commit-lint off
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m x')"
  assert_silent                                   # switched off in this clone
  git -C "$repo" config --unset gerrit-stack.commit-lint
  rm -f "$BATS_TEST_TMPDIR/stub-bin/commitlint"
  PATH=$(path_without commitlint)
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m x')"
  assert_silent                                   # config, no tool: fail-open
}

@test "post: fixup! commits are not linted" {
  local repo sha
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  sha=$(commit_file "$repo" a.txt a "Added rate limit")
  printf 'a2\n' > "$repo/a.txt"
  git -C "$repo" add a.txt
  git -C "$repo" commit -q --fixup="$sha"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" "git commit --fixup=$sha")"
  assert_silent
}

@test "post: a crashing commitlint (broken config) is not reported as a lint failure" {
  local repo
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  printf '#!/usr/bin/env bash\necho "ReferenceError: x is not defined" >&2\nexit 1\n' \
    > "$BATS_TEST_TMPDIR/stub-bin/commitlint"
  commit_file "$repo" a.txt a "Added rate limit" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m x')"
  assert_silent
}

@test "post: real commitlint + config-conventional (integration; skipped when not installed)" {
  local repo
  command -v commitlint >/dev/null 2>&1 || skip "commitlint not installed"
  repo=$(lint_repo)
  commit_file "$repo" a.txt a "feat: add rate limit" >/dev/null
  (cd "$repo" && git log -1 --format=%B | commitlint >/dev/null 2>&1) \
    || skip "@commitlint/config-conventional does not resolve here"
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "feat: add rate limit"')"
  assert_silent
  commit_file "$repo" b.txt b "Added rate limit" >/dev/null
  run_hook "$POST" "$(hook_json PostToolUse "$repo" 'git commit -m "Added rate limit"')"
  assert_eq 2 "$status"
  assert_contains "$stderr" "[type-empty]"
  assert_contains "$stderr" "git commit --amend -F <file>"
}

# ---------------------------------------------------------------- commitlint: before push

@test "guard: push to refs/for is denied when a chain commit fails the lint (sha7, subject, first rule)" {
  local repo bad good
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  good=$(commit_file "$repo" a.txt a "feat: first")
  bad=$(commit_file "$repo" b.txt b "Added rate limit")
  commit_file "$repo" c.txt c "fix: third" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_deny "commitlint"
  assert_contains "$stderr" "${bad:0:7} Added rate limit — subject may not be empty [subject-empty]"
  assert_not_contains "$stderr" "${good:0:7}"
  assert_not_contains "$stderr" "[type-empty]"
  assert_contains "$stderr" "git commit --amend -F <file>"
  trace_has $'git-guard\tpush\tdeny'
}

@test "guard: push to refs/for still asks when every chain commit passes the lint" {
  local repo reason
  repo=$(lint_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  commit_file "$repo" a.txt a "feat: first" >/dev/null
  commit_file "$repo" b.txt b "fix(ping): second" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  reason=$(ask_reason)
  assert_contains "$reason" "Push 2 change(s) to refs/for/master"
}

@test "guard: a bad subject does not block the push without a config, with commit-lint=off, or without the tool" {
  local repo
  repo=$(make_gerrit_repo)
  stub_commitlint >/dev/null; PATH="$BATS_TEST_TMPDIR/stub-bin:$PATH"
  commit_file "$repo" a.txt a "Added rate limit" >/dev/null
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_contains "$(ask_reason)" "Push 1 change(s)"
  add_commitlint_config "$repo"
  team_config "$repo" commit-lint off
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_contains "$(ask_reason)" "Push 1 change(s)"
  team_config "$repo" commit-lint auto
  rm -f "$BATS_TEST_TMPDIR/stub-bin/commitlint"
  PATH=$(path_without commitlint)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" 'git push origin HEAD:refs/for/master')"
  assert_contains "$(ask_reason)" "Push 1 change(s)"
}

# ---------------------------------------------------------------- comment guard: MCP

@test "comment-guard: off by default → silent, no trace" {
  local repo
  repo=$(make_gerrit_repo)
  run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "this can be null")"
  assert_silent
  run_hook "$CG" "$(mcp_hook_json "$repo" post_draft_comment "this can be null")"
  assert_silent
  team_config "$repo" comment-style none
  run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "this can be null")"
  assert_silent
  [ ! -e "$GERRIT_STACK_TRACE" ] || fail "trace written while the convention is off"
}

@test "comment-guard: conventional → unlabelled top-level MCP comment is denied with labels and an example" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "this can be null")"
  assert_deny "Conventional Comments"
  assert_contains "$stderr" "src/Main.java:7"
  assert_contains "$stderr" "praise nitpick suggestion issue todo question thought chore note"
  assert_contains "$stderr" "issue (blocking):"
  trace_has $'comment-guard\tpost_review_comment\tdeny'
  run_hook "$CG" "$(mcp_hook_json "$repo" post_draft_comment "Issue: capitalised label is not a label")"
  assert_deny "Conventional Comments"
  run_hook "$CG" "$(mcp_hook_json "$repo" post_draft_comment "issues everywhere: not a label either")"
  assert_deny "Conventional Comments"
  trace_has $'comment-guard\tpost_draft_comment\tdeny'
}

@test "comment-guard: conventional → labelled comments are allowed (plain, decorated, multi-line)" {
  local repo msg
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  for msg in "issue (blocking): prefix can be null" "nitpick: typo" \
    "suggestion (non-blocking, test): add a case for 0" "praise: nice split" \
    $'question: why not the factory?\n\nIt caches per plugin load.' \
    "todo: rename" "thought: maybe later" "chore: run the formatter" "note (non-blocking): moved only"; do
    run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "$msg")"
    assert_silent
  done
  trace_has $'comment-guard\tpost_review_comment\tallow'
}

@test "comment-guard: conventional → a reply (in_reply_to) stays free-form" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$CG" "$(mcp_hook_json "$repo" post_draft_comment "Done. Falls back to an empty prefix." a1b2c3)"
  assert_silent
  trace_has $'comment-guard\tpost_draft_comment\tallow'
}

@test "comment-guard: per-clone config overrides the team file both ways" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  git -C "$repo" config gerrit-stack.comment-style none
  run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "this can be null")"
  assert_silent
  team_config "$repo" comment-style none
  git -C "$repo" config gerrit-stack.comment-style conventional
  run_hook "$CG" "$(mcp_hook_json "$repo" post_review_comment "this can be null")"
  assert_deny "Conventional Comments"
}

@test "comment-guard: silent in a plain repo, outside any repo, for other tools and on bad input" {
  local plain repo
  plain=$(make_plain_repo)
  git config -f "$plain/.gerrit-stack" gerrit-stack.comment-style conventional
  run_hook "$CG" "$(mcp_hook_json "$plain" post_review_comment "this can be null")"
  assert_silent
  printf '[gerrit-stack]\n\tcomment-style = conventional\n' > "$BATS_TEST_TMPDIR/.gerrit-stack"
  run_hook "$CG" "$(mcp_hook_json "$BATS_TEST_TMPDIR" post_review_comment "this can be null")"
  assert_silent
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$CG" "$(mcp_hook_json "$repo" publish_drafts "unlabelled cover message")"
  assert_silent
  run_hook "$CG" "not json"
  assert_silent
  run_hook "$CG" ""
  assert_silent
}

# ---------------------------------------------------------------- comment guard: gerrit-rest.py

@test "guard: gerrit-rest.py review --comment without a label is denied when conventional" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'Review' --comment 'src/Main.java:7:this can be null'")"
  assert_deny "Conventional Comments"
  assert_contains "$stderr" "src/Main.java:7"
  trace_has $'comment-guard\tgerrit-rest-review\tdeny'
  # one bad comment among good ones is enough
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 -m Review --comment 'a.py:1:nitpick: typo' --comment=\"b.py:2:looks wrong\"")"
  assert_deny "b.py:2"
  assert_not_contains "$stderr" "a.py:1"
  # a review that is only a cover message is a top-level comment too
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'looks wrong to me'")"
  assert_deny "review message"
}

@test "guard: gerrit-rest.py review — labelled comments, replies and other subcommands pass" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'Author notes' --comment 'src/Main.java:7:note (non-blocking): moved only'")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'Replies to review' --comment 'src/Main.java:7:Done. Renamed.' --in-reply-to a1b2c3 --resolved")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'question: is the cache needed?'")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST comments 42 --unresolved")"
  assert_silent
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST query 'is:open review'")"
  assert_silent
  # the comment guard is reachable directly for Bash input too
  run_hook "$CG" "$(hook_json PreToolUse "$repo" "$REST review 42 -m x --comment 'a.py:1:looks wrong'")"
  assert_deny "a.py:1"
}

@test "guard: gerrit-rest.py review is not guarded by default or in a plain repo" {
  local repo plain
  repo=$(make_gerrit_repo)
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "$REST review 42 --message 'Review' --comment 'a.py:1:looks wrong'")"
  assert_silent
  plain=$(make_plain_repo)
  git config -f "$plain/.gerrit-stack" gerrit-stack.comment-style conventional
  run_hook "$GUARD" "$(hook_json PreToolUse "$plain" "$REST review 42 --message 'Review' --comment 'a.py:1:looks wrong'")"
  assert_silent
}

@test "guard: an unlabelled REST comment and a git deny in one command are both reported" {
  local repo
  repo=$(make_gerrit_repo)
  team_config "$repo" comment-style conventional
  run_hook "$GUARD" "$(hook_json PreToolUse "$repo" "git commit -n -m x && $REST review 42 -m x --comment 'a.py:1:looks wrong'")"
  assert_deny "no-verify"
  assert_contains "$stderr" "Conventional Comments"
}

# ---------------------------------------------------------------- wiring

@test "hooks.json wires comment-guard.sh for the two MCP comment tools and git-guard.sh for gerrit-rest.py" {
  local f="$REPO_ROOT/hooks/hooks.json"
  assert_eq 'mcp__plugin_gerrit_gerrit__(post_review_comment|post_draft_comment)' \
    "$(jq -r '.hooks.PreToolUse[] | select(.hooks[0].command | test("comment-guard")) | .matcher' "$f")"
  assert_eq 'Bash(*gerrit-rest.py*)' \
    "$(jq -r '.hooks.PreToolUse[] | select(.hooks[0].if == "Bash(*gerrit-rest.py*)") | .hooks[0].if' "$f")"
  jq -e '.hooks.PreToolUse[] | select(.hooks[0].if == "Bash(*gerrit-rest.py*)") | .hooks[0].command | test("git-guard.sh")' "$f" >/dev/null
  [ -x "$REPO_ROOT/scripts/comment-guard.sh" ] || fail "comment-guard.sh is not executable"
}
