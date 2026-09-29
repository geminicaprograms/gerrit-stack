# Agent brief — gerrit-stack Part 1 work packages

You are one of several agents building the `gerrit-stack` Claude Code plugin in parallel inside ONE working tree:
`/Users/jcentkowski/workspace/open/gerrit-stack` (git repo, branch `main`).

## Read first (in this order)
1. Execution plan (canonical; contains the **interface contract** every WP must follow, the WP table, and Phase 0 findings):
   `/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-part1-execution-plan.md`
2. SPEC (design; Part 1 = lines 31–172): 
   `/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md`
3. Ledger with live Phase 0 findings: `docs/plans/PROGRESS.md` (this repo).
4. Repo conventions: `.claude/CLAUDE.md` (this repo).

## Hard rules (all WPs)
- **Own only the files listed for your WP.** Never create, edit or delete anything else. If you need something from another WP, code against the contract in the execution plan, not against files that may not exist yet.
- **Do not run `git commit`, `git add`, `git stash`, or anything that touches the index/HEAD of this repo.** The main agent commits.
- Tests/fixtures: use temporary directories under `$BATS_TEST_TMPDIR` / `tempfile.mkdtemp()`; never write outside your files and temp dirs. `tests/helpers.bash` and `tests/fixtures/commit-msg` are shared and **read-only** for you.
- Bash scripts: `#!/usr/bin/env bash`, `set -uo pipefail` (hooks add `trap 'exit 0' ERR`), shellcheck-clean (`shellcheck -x`). macOS ships bash 3.2 and we do not require a newer one: write bash-3.2-compatible code (no associative arrays, no `mapfile`, no `${var,,}`, no `declare -n`). Use `jq` for JSON and git plumbing over porcelain parsing.
- Python: stdlib only, `python3 -m py_compile` clean, unit tests with `unittest`.
- Scripts never wrap `git commit`/`git push`; they print commands. Recipes in skills start with a literal `git`.
- Never hand-write a `Change-Id:` trailer anywhere (docs, tests use the real hook in `tests/fixtures/commit-msg`).
- Keep secrets out of files and logs.
- **Last action**: write `docs/plans/status/<WP>.md` with: `status: done|blocked`, files written, verification commands you ran + their results, open issues. Then reply with a short summary (≤ 15 lines).

## Verification tools available
bats 1.14, shellcheck 0.11, jq 1.8, python3 3.14, git 2.55, docker 29 (daemon running, image `gerritcodereview/gerrit:3.14.4` pulled — do NOT start containers unless your WP says so), bazelisk (Bazel from `.bazelversion`), java 27, `uv`. Claude Code 2.1.260 (`claude -p --plugin-dir …` works; `claude plugin eval` is **gated/unavailable** on this machine).

## Shell gotcha (this machine)
The interactive shell aliases `cp`, `mv` and `rm` to their `-i` variants, so a plain `cp`/`mv` onto an existing file **hangs waiting for a y/n on stdin** (no prompt is visible to you). In scripts use `#!/usr/bin/env bash` (aliases do not apply) and in ad-hoc Bash tool calls use `command cp`, `command mv`, `cp -f`, `mv -f`, `rm -rf`, or `cat src > dst`.
