#!/usr/bin/env bats
# tests/lib.bats — scripts/lib/gerrit-detect.sh + scripts/lib/chain.sh

load helpers

setup() {
  common_setup
  # shellcheck source=scripts/lib/chain.sh
  source "$REPO_ROOT/scripts/lib/chain.sh"
}

# Every GS_* variable must be unset (not merely empty).
assert_gs_unset() {
  local v
  for v in GS_TOPLEVEL GS_REMOTE GS_HOST GS_BRANCH GS_PROJECT GS_BASE \
    GS_HOOK_OK GS_HOOKS_DIR GS_STATE_DIR; do
    if [ -n "${!v+x}" ]; then fail "$v is set to '${!v}'"; fi
  done
}

# ---------------------------------------------------------------- loading

@test "chain.sh sources gerrit-detect.sh and exposes the public API" {
  local f
  for f in gs_detect gs_config gs_trace gs_state_dir gs_chain_commits \
    gs_change_ids_of gs_subject_of gs_is_fixup gs_diffstat_of \
    gs_snapshot_write gs_snapshot_diff gs_session_mark gs_session_marked \
    gs_parse_git_cmd gs_git_args_have; do
    declare -F "$f" >/dev/null || fail "missing function $f"
  done
  # sourcing again is harmless
  source "$REPO_ROOT/scripts/lib/chain.sh"
  source "$REPO_ROOT/scripts/lib/gerrit-detect.sh"
}

# ---------------------------------------------------------------- gs_detect

@test "gs_detect: plain repo returns 1 and exports nothing" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  if gs_detect; then fail "gs_detect succeeded in a plain repo"; fi
  assert_gs_unset
}

@test "gs_detect: not a git repo / missing dir returns 1 silently" {
  cd "$BATS_TEST_TMPDIR"
  run --separate-stderr gs_detect
  assert_eq 1 "$status"
  assert_eq "" "$output"
  assert_eq "" "$stderr"
  run --separate-stderr gs_detect "$BATS_TEST_TMPDIR/does-not-exist"
  assert_eq 1 "$status"
  assert_eq "" "$output"
  assert_eq "" "$stderr"
}

@test "gs_detect: gerrit repo (.gitreview + push refspec) exports exact values" {
  local repo top
  repo=$(make_gerrit_repo)
  top=$(physical_path "$repo")
  cd "$repo"
  gs_detect
  assert_eq "$top" "$GS_TOPLEVEL" GS_TOPLEVEL
  assert_eq origin "$GS_REMOTE" GS_REMOTE
  assert_eq master "$GS_BRANCH" GS_BRANCH
  assert_eq demo "$GS_PROJECT" GS_PROJECT
  assert_eq "" "$GS_HOST" GS_HOST
  assert_eq refs/remotes/origin/master "$GS_BASE" GS_BASE
  assert_eq 1 "$GS_HOOK_OK" GS_HOOK_OK
  assert_eq "$top/.git/hooks" "$GS_HOOKS_DIR" GS_HOOKS_DIR
  assert_eq "$top/.git/gerrit-stack" "$GS_STATE_DIR" GS_STATE_DIR
  # exported, i.e. visible to child processes
  assert_eq "origin master demo" "$(bash -c 'printf "%s %s %s" "$GS_REMOTE" "$GS_BRANCH" "$GS_PROJECT"')"
}

@test "gs_detect: push refspec only (no .gitreview)" {
  local repo top
  repo=$(make_gerrit_repo)
  top=$(physical_path "$repo")
  rm -f "$repo/.gitreview"
  cd "$repo"
  gs_detect
  assert_eq origin "$GS_REMOTE" GS_REMOTE
  assert_eq master "$GS_BRANCH" GS_BRANCH
  assert_eq remote "$GS_PROJECT" GS_PROJECT   # basename of remote.git
  assert_eq "" "$GS_HOST" GS_HOST
  assert_eq refs/remotes/origin/master "$GS_BASE" GS_BASE
  assert_eq 1 "$GS_HOOK_OK" GS_HOOK_OK
  assert_eq "$top/.git/hooks" "$GS_HOOKS_DIR" GS_HOOKS_DIR
}

@test "gs_detect: push refspec branch wins over origin/HEAD and strips %options" {
  local repo
  repo=$(make_gerrit_repo)
  rm -f "$repo/.gitreview"
  cd "$repo"
  git config remote.origin.push 'HEAD:refs/for/release-1.0%topic=x'
  gs_detect
  assert_eq release-1.0 "$GS_BRANCH" GS_BRANCH
  assert_eq "" "$GS_BASE" GS_BASE   # refs/remotes/origin/release-1.0 does not exist, no upstream
}

@test "gs_detect: .gitreview only (no push refspec)" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  git config --unset remote.origin.push
  gs_detect
  assert_eq origin "$GS_REMOTE" GS_REMOTE
  assert_eq master "$GS_BRANCH" GS_BRANCH
  assert_eq demo "$GS_PROJECT" GS_PROJECT
  assert_eq refs/remotes/origin/master "$GS_BASE" GS_BASE
}

@test "gs_detect: .gitreview picks the remote whose URL matches host, else first remote" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  git remote add upstream https://github.com/acme/thing.git
  git remote add mirror https://gerrit.acme.example/a/thing
  printf '[gerrit]\nhost=gerrit.acme.example\nscheme=https\nport=8443\nproject=team/thing.git\ndefaultbranch=main\n' > .gitreview
  gs_detect
  assert_eq mirror "$GS_REMOTE" GS_REMOTE
  assert_eq main "$GS_BRANCH" GS_BRANCH
  assert_eq team/thing "$GS_PROJECT" GS_PROJECT
  # host derives from the http(s) remote URL first (strip /a/ and project path)
  assert_eq https://gerrit.acme.example "$GS_HOST" GS_HOST

  git remote remove mirror
  gs_detect
  assert_eq upstream "$GS_REMOTE" GS_REMOTE   # first (only) remote
  # remote URL is not the Gerrit host: fall back to .gitreview scheme+host+port
  assert_eq https://gerrit.acme.example:8443 "$GS_HOST" GS_HOST
}

@test "gs_detect: remote URL regex (https://review.example.com/x)" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  git remote add origin https://review.example.com/x
  gs_detect
  assert_eq origin "$GS_REMOTE" GS_REMOTE
  assert_eq https://review.example.com "$GS_HOST" GS_HOST
  assert_eq x "$GS_PROJECT" GS_PROJECT
  assert_eq master "$GS_BRANCH" GS_BRANCH   # no refspec, no .gitreview, no origin/HEAD
  assert_eq "" "$GS_BASE" GS_BASE
  assert_eq 0 "$GS_HOOK_OK" GS_HOOK_OK
}

@test "gs_detect: URL regex variants match, github does not" {
  local repo url
  repo=$(make_plain_repo)
  cd "$repo"
  for url in ssh://user@gerrit.example.com:29418/foo/bar \
    https://chromium.googlesource.com/chromium/src \
    https://review.gerrithub.io/a/org/repo \
    ssh://host.example.com:29418/proj \
    https://example.com/gerrit/a/proj; do
    git remote remove origin 2>/dev/null || true
    git remote add origin "$url"
    gs_detect || fail "no detection for $url"
  done
  git remote remove origin
  git remote add origin https://github.com/acme/thing.git
  if gs_detect; then fail "github URL detected as Gerrit"; fi
  assert_gs_unset
}

@test "gs_detect: host/project derivation from URL shapes" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  git remote add origin https://user@review.example.com/a/foo/bar.git
  gs_detect
  assert_eq https://review.example.com "$GS_HOST" GS_HOST
  assert_eq foo/bar "$GS_PROJECT" GS_PROJECT

  git remote set-url origin ssh://user@gerrit.example.com:29418/foo/bar
  gs_detect
  assert_eq "" "$GS_HOST" GS_HOST          # ssh: no http base known
  assert_eq foo/bar "$GS_PROJECT" GS_PROJECT

  git remote set-url origin gerrit.example.com:foo/bar.git   # scp-like
  gs_detect
  assert_eq "" "$GS_HOST" GS_HOST
  assert_eq foo/bar "$GS_PROJECT" GS_PROJECT

  # sub-path Gerrit with .gitreview project known → base keeps the prefix
  git remote set-url origin https://example.com/gerrit/a/foo/bar
  printf '[gerrit]\nhost=example.com\nproject=foo/bar\n' > .gitreview
  gs_detect
  assert_eq https://example.com/gerrit "$GS_HOST" GS_HOST
  assert_eq foo/bar "$GS_PROJECT" GS_PROJECT
}

@test "gs_detect: gerrit-stack.remote/branch/host config override everything" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  git remote add origin https://review.example.com/x
  git remote add upstream https://github.com/acme/thing.git
  git config gerrit-stack.remote upstream
  git config gerrit-stack.branch develop
  git config gerrit-stack.host https://gerrit.internal.example/r/
  gs_detect
  assert_eq upstream "$GS_REMOTE" GS_REMOTE
  assert_eq develop "$GS_BRANCH" GS_BRANCH
  assert_eq https://gerrit.internal.example/r "$GS_HOST" GS_HOST
  assert_eq acme/thing "$GS_PROJECT" GS_PROJECT
}

@test "gs_detect: branch falls back to refs/remotes/<remote>/HEAD symref" {
  local repo
  repo=$(make_plain_repo)
  cd "$repo"
  git remote add origin https://review.example.com/x
  git update-ref refs/remotes/origin/main HEAD
  git symbolic-ref refs/remotes/origin/HEAD refs/remotes/origin/main
  gs_detect
  assert_eq main "$GS_BRANCH" GS_BRANCH
  assert_eq refs/remotes/origin/main "$GS_BASE" GS_BASE
}

@test "gs_detect: GS_BASE falls back to @{upstream} when remote branch ref is missing" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  git config gerrit-stack.branch develop       # refs/remotes/origin/develop does not exist
  git branch -q --set-upstream-to=origin/master
  gs_detect
  assert_eq develop "$GS_BRANCH" GS_BRANCH
  assert_eq refs/remotes/origin/master "$GS_BASE" GS_BASE
}

@test "gs_detect: gerrit-stack.enabled=false disables and clears stale exports" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  assert_eq origin "$GS_REMOTE"
  git config gerrit-stack.enabled false
  if gs_detect; then fail "enabled=false still detected"; fi
  assert_gs_unset
  git config gerrit-stack.enabled 0
  if gs_detect; then fail "enabled=0 still detected"; fi
  git config gerrit-stack.enabled true
  gs_detect
}

@test "gs_detect: hook missing, not executable, or not the Gerrit hook → GS_HOOK_OK=0" {
  local repo hook
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  hook="$GS_HOOKS_DIR/commit-msg"
  chmod -x "$hook"
  gs_detect
  assert_eq 0 "$GS_HOOK_OK" "not executable"
  chmod +x "$hook"
  printf '#!/bin/sh\nexit 0\n' > "$hook"
  gs_detect
  assert_eq 0 "$GS_HOOK_OK" "no Change-Id in hook"
  rm -f "$hook"
  gs_detect
  assert_eq 0 "$GS_HOOK_OK" "missing"
  assert_eq origin "$GS_REMOTE"   # detection itself still succeeds
}

@test "gs_detect: honours core.hooksPath for GS_HOOKS_DIR" {
  local repo top
  repo=$(make_gerrit_repo)
  top=$(physical_path "$repo")
  cd "$repo"
  mkdir -p "$repo/.githooks"
  git config core.hooksPath .githooks
  gs_detect
  assert_eq "$top/.githooks" "$GS_HOOKS_DIR" GS_HOOKS_DIR
  assert_eq 0 "$GS_HOOK_OK" GS_HOOK_OK
}

@test "gs_detect <dir>: works from elsewhere, from a subdirectory, and keeps cwd" {
  local repo top
  repo=$(make_gerrit_repo)
  top=$(physical_path "$repo")
  mkdir -p "$repo/sub/deeper"
  cd "$BATS_TEST_TMPDIR"
  gs_detect "$repo/sub/deeper"
  assert_eq "$top" "$GS_TOPLEVEL" GS_TOPLEVEL
  assert_eq "$top/.git/hooks" "$GS_HOOKS_DIR" GS_HOOKS_DIR
  assert_eq "$(physical_path "$BATS_TEST_TMPDIR")" "$(pwd -P)" "cwd unchanged"
  cd "$repo"
  gs_detect sub
  assert_eq "$top" "$GS_TOPLEVEL"
  assert_eq "$top" "$(pwd -P)" "cwd unchanged"
}

@test "gs_detect: linked worktree gets a per-worktree state dir" {
  local repo top wt
  repo=$(make_gerrit_repo)
  top=$(physical_path "$repo")
  wt="$BATS_TEST_TMPDIR/wt"
  git -C "$repo" worktree add -q "$wt" -b feature
  gs_detect "$wt"
  assert_eq "$(physical_path "$wt")" "$GS_TOPLEVEL" GS_TOPLEVEL
  assert_eq "$top/.git/hooks" "$GS_HOOKS_DIR" GS_HOOKS_DIR      # shared hooks
  assert_eq "$top/.git/worktrees/wt/gerrit-stack" "$GS_STATE_DIR" GS_STATE_DIR
  assert_eq 1 "$GS_HOOK_OK"
}

# ---------------------------------------------------------------- gs_config / gs_trace / gs_state_dir

@test "gs_config: value or default" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  assert_eq 150 "$(gs_config budget.lines 150)"
  assert_eq "" "$(gs_config budget.lines)"
  git config gerrit-stack.budget.lines 42
  assert_eq 42 "$(gs_config budget.lines 150)"
  gs_detect
  cd "$BATS_TEST_TMPDIR"
  assert_eq 42 "$(gs_config budget.lines)"   # reads the detected repo, not cwd
}

@test "gs_trace: no-op without GERRIT_STACK_TRACE, TAB-separated append with it" {
  local trace="$BATS_TEST_TMPDIR/trace"
  gs_trace git-guard push ask
  [ ! -e "$trace" ]
  GERRIT_STACK_TRACE="$trace" gs_trace git-guard push ask
  GERRIT_STACK_TRACE="$trace" gs_trace git-post commit silent
  assert_eq $'git-guard\tpush\task\ngit-post\tcommit\tsilent' "$(cat "$trace")"
}

@test "gs_state_dir: creates the directory lazily; empty outside a repo" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  [ ! -d "$GS_STATE_DIR" ]
  assert_eq "$GS_STATE_DIR" "$(gs_state_dir)"
  [ -d "$GS_STATE_DIR" ]
  unset GS_STATE_DIR GS_TOPLEVEL
  assert_eq "$(physical_path "$repo")/.git/gerrit-stack" "$(gs_state_dir)"   # derived from cwd
  cd "$BATS_TEST_TMPDIR"
  unset GS_STATE_DIR GS_TOPLEVEL
  assert_eq "" "$(gs_state_dir)"
}

# ---------------------------------------------------------------- chain queries

@test "gs_chain_commits: oldest→newest for base..HEAD; nothing when base empty" {
  local repo a b c
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  a=$(commit_file "$repo" a.txt "a" "feat: a")
  b=$(commit_file "$repo" b.txt "b" "feat: b")
  c=$(commit_file "$repo" c.txt "c" "feat: c")
  assert_eq "$a
$b
$c" "$(gs_chain_commits)"
  assert_eq "$b
$c" "$(gs_chain_commits "$a")"
  assert_eq "" "$(gs_chain_commits "")"
  assert_eq "" "$(GS_BASE= gs_chain_commits)"
  assert_eq "" "$(gs_chain_commits HEAD)"
}

@test "gs_change_ids_of: 1 with the real hook, 0 with --no-verify, 2 with two trailers" {
  local repo a b ids ta tb
  repo=$(make_gerrit_repo)
  cd "$repo"
  a=$(commit_file "$repo" a.txt "a" "feat: a")
  ids=$(gs_change_ids_of "$a")
  assert_eq 1 "$(count_lines "$ids")"
  case "$ids" in I[0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]*) ;; *) fail "not a Change-Id: $ids" ;; esac
  assert_eq 41 "${#ids}"

  printf 'nv\n' > nv.txt && git add nv.txt && git commit -q --no-verify -m "feat: no hook"
  assert_eq 0 "$(count_lines "$(gs_change_ids_of HEAD)")"

  # Two footer trailers (as after a bad squash): both trailer lines come from
  # hook-generated commits, nothing is hand-written.
  b=$(commit_file "$repo" b.txt "b" "feat: b")
  ta=$(git log -1 --format=%B "$a" | git interpret-trailers --parse --no-divider)
  tb=$(git log -1 --format=%B "$b" | git interpret-trailers --parse --no-divider)
  printf 'feat: squashed\n\nbody text\n\n%s\n%s\n' "$ta" "$tb" | git commit -q --amend --no-verify -F -
  ids=$(gs_change_ids_of HEAD)
  assert_eq 2 "$(count_lines "$ids")"
  assert_eq "$(gs_change_ids_of "$a")
$(gs_change_ids_of "$b")" "$ids"
}

@test "gs_change_ids_of: only the footer paragraph counts (JGit semantics)" {
  local repo a t
  repo=$(make_gerrit_repo)
  cd "$repo"
  a=$(commit_file "$repo" a.txt "a" "feat: a")
  t=$(git log -1 --format=%B "$a" | git interpret-trailers --parse --no-divider)
  # trailer-looking line in the body, footer without it → 0
  printf 'feat: body only\n\n%s\n\nSigned-off-by: T <t@example.com>\n' "$t" | git commit -q --amend --no-verify -F -
  assert_eq 0 "$(count_lines "$(gs_change_ids_of HEAD)")"
  # footer mixes prose and trailers → still found
  printf 'feat: mixed\n\nSee bug 42\n%s\n' "$t" | git commit -q --amend --no-verify -F -
  assert_eq 1 "$(count_lines "$(gs_change_ids_of HEAD)")"
  # unknown sha → nothing, exit 0
  assert_eq "" "$(gs_change_ids_of 0000000000000000000000000000000000000000 2>&1)"
}

@test "gs_subject_of / gs_is_fixup" {
  local repo a f s m
  repo=$(make_gerrit_repo)
  cd "$repo"
  a=$(commit_file "$repo" a.txt "a" "feat: a")
  assert_eq "feat: a" "$(gs_subject_of "$a")"
  git commit -q --allow-empty -m "fixup! feat: a"; f=$(git rev-parse HEAD)
  git commit -q --allow-empty -m "squash! feat: a"; s=$(git rev-parse HEAD)
  git commit -q --allow-empty -m "amend! feat: a"; m=$(git rev-parse HEAD)
  if gs_is_fixup "$a"; then fail "normal commit reported as fixup"; fi
  gs_is_fixup "$f"
  gs_is_fixup "$s"
  gs_is_fixup "$m"
  # the real hook adds no Change-Id to fixup commits
  assert_eq 0 "$(count_lines "$(gs_change_ids_of "$f")")"
}

@test "gs_diffstat_of: insertions+deletions and file count; binary counts 0 lines" {
  local repo a b c
  repo=$(make_gerrit_repo)
  cd "$repo"
  a=$(commit_file "$repo" a.txt $'1\n2\n3' "feat: a")
  assert_eq "3 1" "$(gs_diffstat_of "$a")"
  printf '1\n2\nX\n' > a.txt
  printf 'y\ny\n' > b.txt
  git add a.txt b.txt && git commit -q -m "feat: b"; b=$(git rev-parse HEAD)
  assert_eq "4 2" "$(gs_diffstat_of "$b")"     # -1 +1 in a.txt, +2 in b.txt
  printf '\000\001\002' > bin.dat
  git add bin.dat && git commit -q -m "feat: bin"; c=$(git rev-parse HEAD)
  assert_eq "0 1" "$(gs_diffstat_of "$c")"
  git commit -q --allow-empty -m "chore: empty"
  assert_eq "0 0" "$(gs_diffstat_of HEAD)"
}

# ---------------------------------------------------------------- snapshot

@test "gs_snapshot_write / gs_snapshot_diff: identical, lost after amend --no-verify, new after commit" {
  local repo a b ida idb idc
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  a=$(commit_file "$repo" a.txt "a" "feat: a")
  b=$(commit_file "$repo" b.txt "b" "feat: b")
  ida=$(gs_change_ids_of "$a"); idb=$(gs_change_ids_of "$b")

  run gs_snapshot_diff
  assert_eq 0 "$status" "no snapshot yet"
  assert_eq "" "$output"

  gs_snapshot_write
  assert_eq "$(printf '%s\n%s\n' "$ida" "$idb" | LC_ALL=C sort)" "$(cat "$GS_STATE_DIR/chain-ids")"
  run gs_snapshot_diff
  assert_eq 0 "$status" "identical"
  assert_eq "" "$output"

  git commit -q --amend --no-verify -m "x"       # drops the Change-Id of b
  run gs_snapshot_diff
  assert_eq 1 "$status" "lost"
  assert_eq "lost: $idb" "$output"

  git commit -q --allow-empty -m "feat: c"       # hook adds a new Change-Id
  idc=$(gs_change_ids_of HEAD)
  run gs_snapshot_diff
  assert_eq 1 "$status" "lost + new"
  assert_eq "lost: $idb
new: $idc" "$output"

  gs_snapshot_write
  run gs_snapshot_diff
  assert_eq 0 "$status" "re-snapshotted"
}

@test "gs_snapshot_write: empty chain writes an empty set" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  gs_snapshot_write
  [ -f "$GS_STATE_DIR/chain-ids" ]
  assert_eq "" "$(cat "$GS_STATE_DIR/chain-ids")"
}

# ---------------------------------------------------------------- session marker

@test "gs_session_mark / gs_session_marked with sanitised ids" {
  local repo
  repo=$(make_gerrit_repo)
  cd "$repo"
  gs_detect
  if gs_session_marked abc-123; then fail "marked before mark"; fi
  gs_session_mark abc-123
  [ -f "$GS_STATE_DIR/session-abc-123" ]
  gs_session_marked abc-123
  if gs_session_marked other; then fail "other session marked"; fi
  gs_session_mark 'a/b c$(x)'
  [ -f "$GS_STATE_DIR/session-a_b_c__x_" ]
  gs_session_marked 'a/b c$(x)'
  gs_session_mark ""
  [ -f "$GS_STATE_DIR/session-unknown" ]
}

# ---------------------------------------------------------------- gs_parse_git_cmd

# parse_case <command> <expected-output>
parse_case() {
  local out
  out=$(gs_parse_git_cmd "$1")
  assert_eq "$2" "$out" "gs_parse_git_cmd '$1'"
}

@test "gs_parse_git_cmd: contract table" {
  parse_case 'git status' $'.\tstatus\t'
  parse_case 'cd sub && git commit -m x' $'sub\tcommit\t-m x'
  parse_case 'git -C a/b push origin HEAD:refs/for/master%topic=t' $'a/b\tpush\torigin HEAD:refs/for/master%topic=t'
  parse_case 'git add . && git commit --no-verify -am "x"' $'.\tadd\t.\n.\tcommit\t--no-verify -am "x"'
  parse_case 'FOO=1 git -c core.x=y rebase -i --autosquash HEAD~3' $'.\trebase\t-i --autosquash HEAD~3'
  parse_case 'echo hi' ''
  parse_case 'cd a && cd b && git status' $'a/b\tstatus\t'
  parse_case 'git log | grep x' $'.\tlog\t'
}

@test "gs_parse_git_cmd: separators, quoting, cd forms" {
  parse_case 'git commit -m "a && b; c | d"' $'.\tcommit\t-m "a && b; c | d"'
  parse_case "git commit -m 'it; works'" $'.\tcommit\t-m \'it; works\''
  parse_case 'git fetch; git rebase origin/master || git rebase --abort' $'.\tfetch\t\n.\trebase\torigin/master\n.\trebase\t--abort'
  parse_case $'git fetch\ngit rebase origin/master' $'.\tfetch\t\n.\trebase\torigin/master'
  parse_case 'cd /abs/path; git -C sub status' $'/abs/path/sub\tstatus\t'
  parse_case 'cd a/ && git -C ./b/ status' $'a/b\tstatus\t'
  parse_case 'cd "a b" && git status' $'a b\tstatus\t'
  parse_case 'git -C "a b" -C c status' $'a b/c\tstatus\t'
  parse_case 'cd /x && cd /y && git status' $'/y\tstatus\t'
  parse_case 'cd ~/proj && git status' "$HOME/proj"$'\tstatus\t'
  parse_case '(cd sub && git status)' $'sub\tstatus\t'
  parse_case 'git status & git log' $'.\tstatus\t\n.\tlog\t'
}

@test "gs_parse_git_cmd: prefixes and git global options" {
  parse_case 'env GIT_EDITOR=true command git --no-pager --git-dir=.git rebase --continue' $'.\trebase\t--continue'
  parse_case 'A=1 B="x y" exec git -P -c a=b -c c=d --work-tree /w --literal-pathspecs push' $'.\tpush\t'
  parse_case 'time git --bare --no-optional-locks --no-replace-objects status -sb' $'.\tstatus\t-sb'
  parse_case '/usr/bin/git status' $'.\tstatus\t'
  parse_case '\git status' $'.\tstatus\t'
  parse_case 'sudo git status' $'.\tstatus\t'
  parse_case 'if git diff --quiet; then echo clean; fi' $'.\tdiff\t--quiet'
  parse_case '! git diff --quiet' $'.\tdiff\t--quiet'
  parse_case 'git --version' $'.\t--version\t'
  parse_case 'gitk' ''
  parse_case 'echo git status' ''
  parse_case 'mygit status' ''
}

@test "gs_parse_git_cmd: command substitution, heredocs, redirections" {
  parse_case 'echo $(git rev-parse HEAD)' $'.\trev-parse\tHEAD'
  parse_case 'x=`git rev-parse HEAD`' $'.\trev-parse\tHEAD'
  parse_case 'git push origin HEAD:refs/for/master 2>&1 | tee out.log' $'.\tpush\torigin HEAD:refs/for/master 2>&1'
  parse_case 'git log >/dev/null 2>&1 && git status' $'.\tlog\t>/dev/null 2>&1\n.\tstatus\t'
  # heredoc body lines are not commands
  parse_case $'git commit -F - <<EOF\nfeat: x\n\ngit push is documented here\nEOF\ngit status' $'.\tcommit\t-F - <<EOF\n.\tstatus\t'
  parse_case $'git commit -F - <<-\'EOF\'\n\tgit push\n\tEOF\ngit status' $'.\tcommit\t-F - <<-\'EOF\'\n.\tstatus\t'
  # Claude Code's default commit shape: newlines inside the quoted arg are flattened
  parse_case $'git commit -m "$(cat <<\'EOF\'\nfeat: subject\n\nbody (with parens) and "quotes"\nEOF\n)"' $'.\tcommit\t-m "$(cat <<\'EOF\' feat: subject  body (with parens) and "quotes" EOF )"'
  parse_case 'git -C ${DIR} status' $'${DIR}\tstatus\t'
}

# ---------------------------------------------------------------- gs_git_args_have

@test "gs_git_args_have: standalone, bundled short, prefix, quoted, after --" {
  gs_git_args_have "-an" "-n"
  gs_git_args_have "--no-verify -am x" "--no-verify"
  gs_git_args_have "-am x" "-a"
  gs_git_args_have "-am x" "-m"
  gs_git_args_have "--message=x" "--message="
  gs_git_args_have "--amend --no-edit" "--amend"
  if gs_git_args_have "-m x" "-n"; then fail "-n found in '-m x'"; fi
  if gs_git_args_have "--no-verify" "-n"; then fail "-n found in --no-verify"; fi
  if gs_git_args_have '-m "x -n y"' "-n"; then fail "-n found inside quotes"; fi
  if gs_git_args_have "-- -n" "-n"; then fail "-n found after --"; fi
  if gs_git_args_have "" "-n"; then fail "found in empty args"; fi
  if gs_git_args_have "-n" ""; then fail "empty flag matched"; fi
}

# ---------------------------------------------------------------- hook-safety

@test "libs are safe under hook conditions (set -uo pipefail + ERR trap, and set -Eeuo pipefail)" {
  local repo plain script opts
  repo=$(make_gerrit_repo)
  plain=$(make_plain_repo)
  script="$BATS_TEST_TMPDIR/hookish.sh"
  cat > "$script" <<'EOF'
#!/usr/bin/env bash
set "$1"; set -o pipefail
trap 'echo "ERR trap fired at line $LINENO"; exit 0' ERR
source "$2"
cd "$3"
if gs_detect; then :; fi
gs_chain_commits >/dev/null
gs_change_ids_of HEAD >/dev/null
gs_change_ids_of nope >/dev/null
gs_subject_of HEAD >/dev/null
gs_subject_of nope >/dev/null
if gs_is_fixup HEAD; then :; fi
gs_diffstat_of HEAD >/dev/null
gs_snapshot_write
if gs_snapshot_diff >/dev/null; then :; fi
gs_session_mark s
if gs_session_marked s; then :; fi
if gs_session_marked t; then :; fi
gs_parse_git_cmd "git status" >/dev/null
gs_parse_git_cmd "" >/dev/null
if gs_git_args_have "-a" "-n"; then :; fi
gs_config nope >/dev/null
gs_config nope default >/dev/null
gs_state_dir >/dev/null
gs_trace a b c
if gs_detect /nonexistent; then :; fi
gs_chain_commits >/dev/null
gs_snapshot_write
if gs_snapshot_diff >/dev/null; then :; fi
echo REACHED_END
EOF
  for opts in -u -Eeu; do
    run --separate-stderr bash "$script" "$opts" "$REPO_ROOT/scripts/lib/chain.sh" "$repo"
    assert_eq REACHED_END "$output" "gerrit repo, set $opts"
    assert_eq "" "$stderr" "gerrit repo, set $opts"
    run --separate-stderr bash "$script" "$opts" "$REPO_ROOT/scripts/lib/chain.sh" "$plain"
    assert_eq REACHED_END "$output" "plain repo, set $opts"
    assert_eq "" "$stderr" "plain repo, set $opts"
    run --separate-stderr bash "$script" "$opts" "$REPO_ROOT/scripts/lib/chain.sh" "$BATS_TEST_TMPDIR"
    assert_eq REACHED_END "$output" "no repo, set $opts"
    assert_eq "" "$stderr" "no repo, set $opts"
  done
}

@test "libs do not change shell options or cwd of the caller" {
  local repo before after
  repo=$(make_gerrit_repo)
  cd "$repo"
  before="$(set +o) $(pwd -P)"
  gs_detect
  gs_chain_commits >/dev/null
  gs_parse_git_cmd "cd sub && git status" >/dev/null
  gs_snapshot_write
  after="$(set +o) $(pwd -P)"
  assert_eq "$before" "$after"
}
