#!/usr/bin/env bash
# Bench fixture (scaffold_script): builds a Gerrit-looking workspace in $PWD.
#
# Self-contained on purpose: the benchmark cases must not depend on
# evals/fixtures/scaffold-common.sh (owned by the main eval cases), because a
# bench case may be copied out of the plugin tree by the eval runner.
#
# Result (all inside the caller's $PWD, which the runner makes the run's
# workspace):
#   .               git repo on `master`, demo/skeleton copied in, one initial
#                   commit (Conventional Commit subject, carrying a Change-Id
#                   from the real hook)
#   team files      .gerrit-stack (commit-lint = auto, comment-style =
#                   conventional) and commitlint.config.mjs
#                   (config-conventional), committed in that base commit.
#                   They are the repo's property and exist for every arm;
#                   only arms with tooling that honours them act on them.
#   ../remote.git   local bare "Gerrit" remote; `master` already pushed
#   origin          remote.origin.push = HEAD:refs/for/master, .gitreview,
#                   real commit-msg hook installed, verify-cmd configured
#
# Plugin root: $EVAL_PLUGIN_ROOT when set (the official runner passes only
# EVAL_* variables through), else derived from this script's own location
# (<plugin>/evals/<bench-dir>/<case>/fixture.sh).
set -uo pipefail

here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
plugin_root=${EVAL_PLUGIN_ROOT:-$(cd "$here/../../.." && pwd -P)}
skeleton=$plugin_root/demo/skeleton
hook_src=$plugin_root/tests/fixtures/commit-msg

if [ ! -d "$skeleton" ] || [ ! -f "$hook_src" ]; then
  echo "fixture.sh: plugin root '$plugin_root' has no demo/skeleton or tests/fixtures/commit-msg" >&2
  echo "fixture.sh: set EVAL_PLUGIN_ROOT to the gerrit-stack checkout" >&2
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

cat > .gitreview <<'GR'
[gerrit]
host=localhost
port=29418
project=demo-plugin
defaultbranch=master
GR

# 3. the real Gerrit commit-msg hook (adds Change-Id trailers)
hooks_dir=$(git rev-parse --git-path hooks)
mkdir -p "$hooks_dir"
cp -f "$hook_src" "$hooks_dir/commit-msg"
chmod +x "$hooks_dir/commit-msg"

# 4. team files: the committed team config read by gerrit-stack and the
#    commitlint config the commit convention is delegated to.
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

# 5. initial commit (its subject must itself pass the commitlint config),
#    pushed straight to master on the bare remote so that
#    refs/remotes/origin/master exists as the chain base.
git add -A
git commit -q -m "chore: import demo-plugin skeleton" || exit 1
git push -q origin HEAD:refs/heads/master || exit 1

if ! git cat-file commit HEAD | grep -q '^Change-Id: I'; then
  echo "fixture.sh: commit-msg hook did not add a Change-Id" >&2
  exit 1
fi
for team_file in .gerrit-stack commitlint.config.mjs; do
  if ! git cat-file -e "HEAD:$team_file" 2>/dev/null; then
    echo "fixture.sh: team file $team_file is not in the base commit" >&2
    exit 1
  fi
done
echo "fixture.sh: workspace ready at $workspace (remote ../remote.git, base origin/master $(git rev-parse --short HEAD))"
