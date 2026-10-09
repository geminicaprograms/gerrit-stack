# Changelog

All notable changes to this project are documented in this file.

The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.1.0/),
and this project adheres to [Semantic Versioning](https://semver.org/spec/v2.0.0.html).

## [Unreleased]

### Changed

- README opens with the revised thesis (the agent already splits; gerrit-stack
  keeps the chain intact under pressure, in rework and in review) and quotes
  the pass 2 benchmark; the https-rewrite troubleshooting row points at the
  merged upstream fix (change 635805). `demo/recording/` is ignored.

### Added

- Team config file: a committed `.gerrit-stack` at the repo top level
  (git-config format, section `[gerrit-stack]`). `gs_config` now resolves
  per-clone `git config gerrit-stack.<key>` → `.gerrit-stack` → default, and
  only documented keys are read from the committed file. Detection
  (`enabled`, `remote`, `branch`, `host`) uses the same lookup.
- `commit-lint` setting (`auto` | `off`, default `auto`): commit messages are
  checked with the repository's own commitlint config when one exists and
  `commitlint` resolves offline (`PATH`, else `node_modules/.bin`). Feedback
  after `git commit` (rule lines + `git commit --amend -F <file>` recipe),
  deny before a push to `refs/for` when a chain commit fails. Never installs,
  never uses the network, fail-open; a missing tool is only mentioned in the
  SessionStart context. gerrit-stack has no commit message rules of its own.
- `comment-style` setting (`conventional` | `none`, default `none`) and
  `scripts/comment-guard.sh`: with `conventional`, an unlabelled top-level
  comment is denied on the Gerrit MCP tools `post_review_comment` /
  `post_draft_comment` and on `gerrit-rest.py review`; replies are exempt.
- SessionStart context names the active team conventions.
- `demo/seed.sh` commits `.gerrit-stack` (`commit-lint = auto`,
  `comment-style = conventional`) and `commitlint.config.mjs` with the
  skeleton.
- `tests/conventions.bats` plus `gs_config` / commitlint rows in
  `tests/lib.bats`; README "Team conventions" section.

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
  `bench`, `bench-unprompted`, `bench-split`, `bench-rework`, `bench-review`,
  `demo-up`, `demo-seed`, `demo-reset`, `demo-warm`, `demo-down`.
- `.github/workflows/ci.yml`: `claude plugin validate ./ --strict`,
  `shellcheck`, `bats`, `python3 -m py_compile`, and `unittest discover`.
- `README.md` (install, skills, hooks/scripts reference, configuration,
  demo, tests, troubleshooting, design principles) and
  `docs/jj-stretch.md` (deferred jj-mode design note).
- Benchmark redesign (uber environment): four suites with `kind` cases in
  `case.yaml` (`implement` | `rework` | `review`): `evals/bench-unprompted`
  (does it split on its own), `evals/bench-split` (prompted split, concern
  maps), `evals/bench-rework/fix-mid-conflict` (seeded six-change chain,
  conflicting blocking fix, natural and nudged) and
  `evals/bench-review/planted-defects` (reviewer with three planted defects).
  `make bench-unprompted bench-split bench-rework bench-review`.
- `evals/run.py` sandbox (tier 1): env allowlist, `ENABLE_CLAUDEAI_MCP_SERVERS=false`,
  `--setting-sources project,local`, pinned `--model`, per-run `isolation.json`
  (a failed startup check errors the run) and capability counters,
  `conventions.json`, `--variants natural,nudged`, `--push-to` hashtags
  `bench-<case>-<arm>`, `run-<id>`, `var-<variant>`, `rep-<n>`, `-j`.
- `scripts/chain-metrics.sh --concerns <case.yaml>` (purity, completeness,
  tests travel) and a reimplemented `--verify-cmd` (builds alone, per-commit
  clean checkout, `--verify-timeout`).
- `evals/metrics/collect.py` rewritten: Split quality, Rework (seeded chain),
  Reviewer, Conventions, Guardrails and Isolation sections; renders
  `docs/benchmark.md`, which is not committed until the first sandboxed pass.

### Changed

- Size policy: the concern is the unit; size is a reviewer-load warning.
  The 50–150-line target and the 200-line hard cap are withdrawn (they
  misread SmartBear's 200–400 lines per sitting and treated the Google /
  Graphite / Gerrit medians as targets, and counted tests and docs).
  - `diff-budget.sh` counts production lines only (tests, docs and
    lock/generated files are reported separately) and prints
    `prod=<n> test=<t> other=<o> files=<m> warn=<L> hard=<H|none> files-warn=<F|none>`
    (replaces `lines=… files=… budget=…/… hard=…`; `--json` now has
    `prod`/`test`/`other`/`total`, `null` for unset limits, status
    `over-warn` instead of `over-soft`). Defaults: `budget.lines` 400
    (production-line warning), `budget.hard-lines` and `budget.files`
    unset = none. Exit 3 only when a hard cap is set. Clones that set the
    old values keep them.
  - `git-post.sh`: after a commit over the warning (exit 1 or 3) the
    feedback asks for a one-line justification in the commit message,
    says broad mechanical changes stay one change and never asks to split
    mechanically. Silent for a `refactor`/`build`/`chore` commit whose
    body says "mechanical", and for a message-only `--amend`.
  - `stack-planner` skill rewritten around vertical slices that build, are
    tested and make sense alone; an anti-fragmentation rule (no class
    without a caller, no scaffolding only the next change explains, no
    "part 1/2"); a section on broad mechanical changes (one change,
    mechanical first, split by module only when each part stands alone);
    `budget.md` evidence table corrected (SmartBear is the load limit; the
    medians are observations, not targets). Plans list production and test
    lines per step.
  - `gerrit-stack` skill, its commit-message / troubleshooting references
    and the README configuration table use the new keys and wording.
- `gerrit-review` skill: Conventional Comments labels are used only when
  `comment-style` is `conventional`; replies are free-form in both modes.
- `gerrit-stack` skill and `references/commit-message.md`: a commitlint
  config in the repo is the commit convention; the built-in style table is
  the fallback.
- `git-guard.sh` hook timeout raised from 10 s to 30 s (one commitlint run
  per chain commit before a push).
