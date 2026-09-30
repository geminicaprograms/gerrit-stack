# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Added

- Plugin manifests: `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`
  (self-hosted marketplace for `gerrit-stack@gerrit-stack`), declaring
  `gerrit@gerrit-mcp` as a dependency.
- Hooks (`hooks/hooks.json`): `session-start.sh` (SessionStart context for
  Gerrit-backed repos), `git-guard.sh` (PreToolUse guard on `git commit` /
  `git push`), `git-post.sh` (PostToolUse feedback after commit/rebase/push),
  `stop-check.sh` (Stop check for commits missing a Change-Id).
- Shared libraries `scripts/lib/gerrit-detect.sh` (layered, fail-open Gerrit
  detection) and `scripts/lib/chain.sh` (chain enumeration, Change-Id
  extraction, diffstat, snapshot/verify).
- Tool scripts: `chain-status.sh`, `push-chain.sh` (prints, never runs, the
  `git push … refs/for/<branch>` command), `diff-budget.sh`,
  `install-commit-msg-hook.sh`, and `gerrit-rest.py` (Gerrit REST fallback
  for the official MCP tools: `related`, `comments`, `review`,
  `rebase-chain`, `topic`, `hashtags`, `submitted-together`, `detail`,
  `query`).
- Skills: `gerrit-stack` (default relation-chain workflow: plan, build,
  pre-push review, push, iterate, land), `stack-planner` (VCS-agnostic diff
  splitting and budget planning), `gerrit-review` (reads and replies to
  review threads via `gerrit@gerrit-mcp`, falling back to `gerrit-rest.py`;
  never votes or submits), each with a `references/` directory and a
  pre-flight checklist.
- Demo infrastructure (`demo/`): `docker-compose.yml` for a local Gerrit
  3.14.4, `seed.sh` (admin + reviewer accounts, `demo-plugin` project,
  `~/.netrc` credential, `gerrit@gerrit-mcp` config), `reset.sh`,
  `reviewer-comment.sh`, `feature-request.md` stage prompt, `RUN.md` demo
  script, and an in-tree Gerrit plugin skeleton (`demo/skeleton/`,
  `demo/gerrit-tree.sh`, `demo/warm-bazel.sh`,
  `demo/skeleton/tools/quick-check.sh`) built against a `stable-3.14` Gerrit
  checkout.
- Evals: 7 cases under `evals/` (trigger-gerrit-stack, trigger-stack-planner,
  plan-before-code, push-requires-confirm, no-manual-change-id,
  split-over-budget, review-reply-conventional) plus
  `evals/fixtures/gerrit-rest-stub.py`, and a stdlib `evals/run.py` runner
  compatible with the official case format (needed where the official
  sandbox refuses Bash-granting cases and for the three-arm benchmark).
- Benchmark: `scripts/chain-metrics.sh`, three `evals/bench/` cases,
  `evals/metrics/collect.py`, and `docs/benchmark.md`, measuring
  gerrit-stack against vanilla Claude Code and vanilla + `gerrit@gerrit-mcp`.
- Tests: `tests/hooks.bats`, `tests/lib.bats`, `tests/tools.bats`,
  `tests/metrics.bats` (bats), `tests/test_gerrit_rest.py`,
  `tests/test_collect.py` (Python `unittest`), and shared fixtures
  (`tests/helpers.bash`, `tests/fixtures/commit-msg`).
- `Makefile` targets: `validate`, `lint`, `test`, `test-py`, `check`, `eval`,
  `bench`, `demo-up`, `demo-seed`, `demo-reset`, `demo-warm`, `demo-down`.
- `.github/workflows/ci.yml`: `claude plugin validate ./ --strict`,
  `shellcheck`, `bats`, `python3 -m py_compile`, and `unittest discover`.
- `README.md` (install, skills, hooks/scripts reference, configuration,
  demo, tests, troubleshooting, design principles) and
  `docs/jj-stretch.md` (deferred jj-mode design note).
- Rework + guardrail benchmark (in progress, per
  `.claude/plans/2026-09-30-rework-benchmark-plan.md`): extends
  `evals/run.py` with `--scenarios fix,split`, `--variants natural,nudged`
  and `-j/--jobs N` to drive a second stage — reviewer feedback (posted as
  `rena`), rework, re-push, Gerrit read-back — after the existing
  implement-and-push stage, plus `rework:`/`nudges:` blocks in
  `case.yaml`, `graders-rework/`, rework-correctness and guardrail-counter
  metrics, and a `## Rework`/`## Guardrails` section in
  `evals/metrics/collect.py`'s output. Documented in `evals/README.md`
  ("Rework + guardrail pipelines"); batch 1 is a 12-pipeline smoke on
  `rate-limited-ping` alone before the full 3-case matrix.
