#!/usr/bin/env bash
# Bench fixture (scaffold_script), kind: review. Builds a Gerrit-looking
# workspace in $PWD that already carries a short seeded chain; its second
# change is the one under review and holds three planted defects (see
# case.yaml, `review.planted`).
#
# Self-contained on purpose (a bench case may be copied out of the plugin
# tree by the eval runner); only demo/skeleton and tests/fixtures/commit-msg
# are taken from the plugin root.
#
# Result (all inside the caller's $PWD, the run's workspace):
#   .               git repo on `master`: demo/skeleton + the team files
#                   (.gerrit-stack, commitlint.config.mjs) in one base commit,
#                   then the two hand-written ping-rate-limit changes from
#                   chain/*.patch (0001 clean, 0002 with the planted defects),
#                   one commit each, every one with a fresh Change-Id from the
#                   real commit-msg hook
#   ../remote.git   local bare "Gerrit" remote; `master` = the base commit
#   origin          remote.origin.push = HEAD:refs/for/master, .gitreview,
#                   real commit-msg hook installed, verify-cmd configured
#
# Plugin root: $EVAL_PLUGIN_ROOT when set (the official runner passes only
# EVAL_* variables through), else derived from this script's own location
# (<plugin>/evals/<suite>/<case>/fixture.sh).
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
plugin_root=${EVAL_PLUGIN_ROOT:-$(cd "$here/../../.." && pwd -P)}
skeleton=$plugin_root/demo/skeleton
hook_src=$plugin_root/tests/fixtures/commit-msg
chain_dir=$here/chain
expected_changes=2

if [ ! -d "$skeleton" ] || [ ! -f "$hook_src" ]; then
  echo "fixture.sh: plugin root '$plugin_root' has no demo/skeleton or tests/fixtures/commit-msg" >&2
  echo "fixture.sh: set EVAL_PLUGIN_ROOT to the gerrit-stack checkout" >&2
  exit 1
fi
if [ ! -d "$chain_dir" ]; then
  echo "fixture.sh: no seeded chain at '$chain_dir'" >&2
  exit 1
fi

# The scaffold runs with whatever git identity the runner leaves us; make sure
# commits can be created without a global config.
export GIT_AUTHOR_NAME=${GIT_AUTHOR_NAME:-Bench Fixture}
export GIT_AUTHOR_EMAIL=${GIT_AUTHOR_EMAIL:-bench@example.com}
export GIT_COMMITTER_NAME=${GIT_COMMITTER_NAME:-$GIT_AUTHOR_NAME}
export GIT_COMMITTER_EMAIL=${GIT_COMMITTER_EMAIL:-$GIT_AUTHOR_EMAIL}

workspace=$PWD

# 1. the plugin sources
cp -R "$skeleton/." "$workspace/" || exit 1
rm -rf "$workspace/bazel-bin" "$workspace/bazel-out" "$workspace/bazel-testlogs" 2>/dev/null

# 2. repo + local bare remote
git init -q -b master . || exit 1
git init -q --bare ../remote.git || exit 1
git remote add origin ../remote.git
git config remote.origin.push HEAD:refs/for/master
git config gerrit-stack.verify-cmd 'bash tools/quick-check.sh'
git config user.name "$GIT_AUTHOR_NAME"
git config user.email "$GIT_AUTHOR_EMAIL"
git config commit.gpgsign false

cat > .gitreview <<'GR'
[gerrit]
host=localhost
port=29418
project=demo-plugin
defaultbranch=master
GR

# 3. the team files: the repo's own conventions, committed for every arm
cat > .gerrit-stack <<'GS'
[gerrit-stack]
	commit-lint = auto
	comment-style = conventional
GS
cat > commitlint.config.mjs <<'CL'
// Conventional Commits, with git's usual 72-column subject and body wrap
// (config-conventional alone allows 100). Footers keep 100: a trailer cannot wrap.
export default {
  extends: ['@commitlint/config-conventional'],
  rules: {
    'header-max-length': [2, 'always', 72],
    'body-max-line-length': [2, 'always', 72],
  },
};
CL

# 4. the real Gerrit commit-msg hook (adds Change-Id trailers)
hooks_dir=$(git rev-parse --git-path hooks)
mkdir -p "$hooks_dir"
cp -f "$hook_src" "$hooks_dir/commit-msg"
chmod +x "$hooks_dir/commit-msg"

# 5. base commit, pushed straight to master on the bare remote so that
#    refs/remotes/origin/master exists as the chain base.
git add -A
git commit -q -m "chore: import demo-plugin skeleton" || exit 1
git push -q origin HEAD:refs/heads/master || exit 1
git fetch -q origin || exit 1
base=$(git rev-parse HEAD)

# 6. the seeded chain: one commit per patch, each with its own fresh
#    Change-Id from the real hook. `git am` runs `applypatch-msg`, not
#    `commit-msg`, so the same hook is installed under that name while the
#    chain is applied and removed afterwards (the workspace ends up with the
#    commit-msg hook only, like every other bench fixture).
#
#    The hook derives the Change-Id from the committer ident (name, email,
#    second-resolution date) and the message. Two fixture runs started in the
#    same second (parallel pipelines) would therefore produce the SAME ids and
#    the arms would share changes in Gerrit. A per-run random offset on the
#    committer date (up to ~12 days back, one second apart per change) keeps
#    the ids distinct between runs without touching the identity.
salt=$(( (RANDOM * 32768 + RANDOM) % 1000000 ))
stamp=$(( $(date +%s) - salt - expected_changes ))
cp -f "$hook_src" "$hooks_dir/applypatch-msg"
chmod +x "$hooks_dir/applypatch-msg"
applied=0
for patch in "$chain_dir"/*.patch; do
  [ -f "$patch" ] || continue
  if grep -q '^Change-Id:' "$patch"; then
    echo "fixture.sh: $(basename "$patch") carries a Change-Id; the hook must add it" >&2
    exit 1
  fi
  stamp=$((stamp + 1))
  if ! GIT_COMMITTER_DATE="@$stamp +0000" git am -q --keep-cr "$patch"; then
    echo "fixture.sh: git am failed on $(basename "$patch")" >&2
    git am --abort 2>/dev/null
    rm -f "$hooks_dir/applypatch-msg"
    exit 1
  fi
  applied=$((applied + 1))
done
rm -f "$hooks_dir/applypatch-msg"

count=$(git rev-list --count "$base..HEAD")
if [ "$applied" -ne "$expected_changes" ] || [ "$count" -ne "$expected_changes" ]; then
  echo "fixture.sh: expected $expected_changes seeded changes, applied $applied, chain has $count" >&2
  exit 1
fi

# 7. exactly one Change-Id per commit (base included), all distinct
for sha in $(git rev-list "$base~0" "$base..HEAD" | sort -u); do
  n=$(git cat-file commit "$sha" | grep -c '^Change-Id: I[0-9a-f]\{40\}$')
  if [ "$n" -ne 1 ]; then
    echo "fixture.sh: commit $sha has $n Change-Id trailers, expected exactly 1" >&2
    exit 1
  fi
done
distinct=$(git log --format=%B "$base..HEAD" | grep '^Change-Id: I' | sort -u | wc -l | tr -d ' ')
if [ "$distinct" -ne "$expected_changes" ]; then
  echo "fixture.sh: expected $expected_changes distinct Change-Ids, got $distinct" >&2
  exit 1
fi

echo "fixture.sh: workspace ready at $workspace (remote ../remote.git, base origin/master $(git rev-parse --short "$base"), $count seeded changes)"
