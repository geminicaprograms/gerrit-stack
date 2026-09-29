#!/usr/bin/env bash
# Eval fixture — runs with cwd = the empty run workspace (evals/run.py and
# `claude plugin eval --scaffold` alike). Builds the Gerrit-looking repo.
set -uo pipefail
here=$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)
common="$here/../fixtures/scaffold-common.sh"
[ -f "$common" ] || common="${EVAL_PLUGIN_ROOT:-}/evals/fixtures/scaffold-common.sh"
# shellcheck source-path=SCRIPTDIR
# shellcheck source=../fixtures/scaffold-common.sh
source "$common"
make_gerrit_workspace --project-kind sh --chain 3

# Canned Gerrit REST stub: change numbers 1..3 map to the chain just built.
ids=$(git log --reverse --format='%(trailers:key=Change-Id,valueonly)' origin/master..HEAD | grep '^I' | paste -sd, -)
subjects=$(git log --reverse --format='%s' origin/master..HEAD | paste -sd'|' -)
rm -f .stub-port .stub-pid
nohup python3 "$PLUGIN_ROOT/evals/fixtures/gerrit-rest-stub.py" --port 0 --dir "$PWD" \
  --change-ids "$ids" --subjects "$subjects" --project demo > .stub.log 2>&1 &
i=0
while [ ! -s .stub-port ] && [ "$i" -lt 100 ]; do sleep 0.1; i=$((i + 1)); done
[ -s .stub-port ] || { echo "fixture: stub did not start" >&2; cat .stub.log >&2; exit 1; }
port=$(cat .stub-port)
git config gerrit-stack.host "http://127.0.0.1:$port"
printf '.stub-port\n.stub-pid\n.stub.log\nreview-posts.jsonl\n' >> "$(git rev-parse --git-path info)/exclude"
echo "stub on http://127.0.0.1:$port (pid $(cat .stub-pid))"
