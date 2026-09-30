#!/usr/bin/env bash
# Push the kept workspaces of one benchmark case (run with --keep) to the demo Gerrit so the
# arms can be compared in the review UI: each arm's commits are rebased onto the demo project's
# master and pushed to refs/for/master with the hashtag bench-<case>-<arm>.
# Usage: bash evals/push-arms.sh <results-dir> <case> [<gerrit-url>] [<run-number>]
set -uo pipefail
results=${1:?results dir}; case_name=${2:?case}; url=${3:-http://localhost:8080/a/demo-plugin}; n=${4:-1}
run_id=$(basename "${results%/}")
for arm in with mcp-only without; do
  ws="$results/runs/$case_name/$arm/$n/workspace/workspace"   # --keep moves the temp dir, repo is one level down
  [ -d "$ws/.git" ] || ws="$results/runs/$case_name/$arm/$n/workspace"
  if [ ! -d "$ws/.git" ]; then echo "$arm: no kept workspace at $ws (run with --keep)"; continue; fi
  tag="bench-$case_name-$arm"
  run_tag="run-$run_id"
  git -C "$ws" remote remove demo >/dev/null 2>&1 || true
  git -C "$ws" remote add demo "$url"
  if ! git -C "$ws" fetch -q demo master; then echo "$arm: fetch from $url failed"; continue; fi
  root=$(git -C "$ws" rev-list --max-parents=0 HEAD | tail -1)
  if ! git -C "$ws" -c sequence.editor=true rebase -q --onto demo/master "$root" HEAD >/dev/null 2>&1; then
    git -C "$ws" rebase --abort >/dev/null 2>&1 || true
    echo "$arm: rebase onto demo/master failed (conflicting skeleton?)"; continue
  fi
  count=$(git -C "$ws" rev-list --count demo/master..HEAD)
  echo "== $arm: $count commit(s), hashtags $tag $run_tag"
  git -C "$ws" push demo "HEAD:refs/for/master%t=$tag,t=$run_tag" 2>&1 | grep -E '/c/|SUCCESS|error|rejected|no new' | sed 's/^ *remote: *//'
done
echo "Open: ${url%/a/*}/q/project:demo-plugin+hashtag:run-$run_id"
