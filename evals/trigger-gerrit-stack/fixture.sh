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
make_gerrit_workspace --project-kind sh
