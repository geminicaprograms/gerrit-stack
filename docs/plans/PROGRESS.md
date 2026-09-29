# gerrit-stack Part 1 — progress ledger

Links: [SPEC](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md) · [Execution plan](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-part1-execution-plan.md) · [Agent brief](AGENT-BRIEF.md)

## Resume here
Fan-out in progress (2026-09-29 evening): agents A0, A3, A4, A5, A6, A7, A8, A11 running; A10 done. Probe (b) (official eval + git hooks) running. Next: when A0 finishes → launch A1 + A2; when probe (b) result known → launch A9; then Phase 3 reviews, Phase 4 integration. Uncommitted agent output = files present in the tree without a `wip(<WP>)` commit; `ls docs/plans/status/` shows who finished.

## Phase status
| Phase / WP | Status | Commit | Verified by | Notes |
|---|---|---|---|---|
| P0.1 brew deps | done | — | `bats 1.14.0`, `shellcheck 0.11.0`, `uv 0.12.17` | |
| P0.2 Docker + image | done | — | `docker pull gerritcodereview/gerrit:3.14.4` ok (1.56 GB) | daemon running |
| P0.3 gerrit-mcp install | done | — | `claude plugin list` → `gerrit@gerrit-mcp` 70a4f8f7e72a enabled; venv prebuilt | googlesource Gitiles 503 → installed from GitHub mirror |
| P0.4 repo init | done | — | `git init -b main`, identity set | |
| P0.5 Gerrit stable-3.14 checkout | done | — | `~/workspace/open/gerrit-3.14` head 0e3db2f7fd, Bazel 8.6.0, 14 submodules | |
| P0.6a validate --strict + dependencies | done | — | exit 0 on dir and on plugin.json | marketplace needs `description` |
| P0.6b eval hook-disable probe | blocked (documented) | — | official runner refuses Bash-granting runs here: `~/.docker/cli-plugins/*` symlinks (Docker Desktop); `DOCKER_CONFIG` does not bypass | → `evals/run.py` is the local runner; official `claude plugin eval --trust-plugin` in CI |
| P0.6c CLAUDE_PLUGIN_ROOT in SKILL.md | done | — | `claude -p --plugin-dir . /gerrit-stack:probe` → `PLUGIN_ROOT=/Users/jcentkowski/workspace/open/gerrit-stack` | substituted inline in SKILL.md body |
| P0.6d `if: Bash(git *)` compound | done | — | hook trace shows PreToolUse fired for `cd sub && git status` and `git -C sub status`, not for `echo hi` | single `if` entry suffices |
| P1 foundation | in-progress | see git log | manifests, hooks.json, stubs, Makefile, CI, ledger committed; lib (A0) pending | |
| A0 lib + test helpers | todo | | | |
| A1 hooks | todo | | | |
| A2 tools | todo | | | |
| A3 gerrit-rest.py | todo | | | |
| A4 skill gerrit-stack | todo | | | |
| A5 skill stack-planner | todo | | | |
| A6 skill gerrit-review | todo | | | |
| A7 demo infra | todo | | | |
| A8 demo skeleton (in-tree) | done (unreviewed) | wip | in-tree build+test 60 s cold / 1–2 s warm; quick-check 0.6 s; `bats`-free (java) | config keys are flat: `pingMessage`, greeting → `greetingPrefix` |
| A9 evals + runner | todo | | | |
| A10 docs | done (unreviewed) | wip | README/CHANGELOG/jj-stretch written; review in P3 | flagged: jj upload flags uncertain |
| A11 metrics/benchmark | todo | | | |
| P3 review | todo | | | |
| P4 integration | todo | | | |

## Phase 0 findings
- `claude plugin validate ./ --strict`: passes with `"dependencies": ["gerrit@gerrit-mcp"]` in plugin.json; marketplace.json needs a `description` or strict fails.
- `claude plugin eval`: 2.1.260 printed "early access"; docs say it is GA from **2.1.269** (`claude update`). Updated the CLI (see verification log). Decision kept: A9 also ships `evals/run.py` (stdlib) over the official case format, because the benchmark needs an arm the official runner cannot express (gerrit-mcp-only vs gerrit-mcp+gerrit-stack) and per-run workspace metrics; it emits the official `aggregate-result.json` schema (`schemaVersion: 1`, `aggregates.overallScore/casesPassed/casesTotal/meanDelta`, `cases[].name/aggregates.score/delta`, `cases[].arms.with[]/without[]`, `costUsd`, `durationSeconds`, `claudeVersion`).
- gerrit-mcp: installed at `~/.claude/plugins/cache/gerrit-mcp/gerrit/<version-hash>/` (currently `70a4f8f7e72a`); config file = `<that dir>/gerrit_mcp_server/gerrit_config.json` (keys: `default_gerrit_base_url`, `gerrit_hosts[{name, external_url, authentication{type: http_basic, username?, auth_token?}}]`; omit username/token → curl `--netrc`); venv at `~/.claude/plugins/data/gerrit-gerrit-mcp/.venv` (prebuilt; `gerrit-check-config` exit 1 = missing config). SessionStart hook `check-config.sh` nags until config exists. MCP server name: `gerrit` → tools `mcp__gerrit__<tool>`.
- Docker: `gerritcodereview/gerrit:3.14.4` pulled (arm64).
- Gerrit tree for in-tree demo build: `~/workspace/open/gerrit-3.14` (stable-3.14, Bazel 8.6.0 via bazelisk).
- Probe b (official eval): `claude plugin eval` 2.1.284 runs (needs `--trust-plugin` non-interactively) but any case granting `Bash` aborts with "the Docker (~/.docker, DOCKER_CONFIG) credential store on this machine holds a symbolic link inside it, so the Bash sandbox cannot reliably exclude it" — cause: Docker Desktop's `~/.docker/cli-plugins/*` symlinks; setting `DOCKER_CONFIG` to a plain dir did not help. Docs confirm official runs disable git hooks/credential helpers via `GIT_CONFIG_COUNT` env config and withhold the shell env (only `EVAL_*` pass). Case layout confirmed: `case.yaml` → `context.scaffold_script` (+ `context.add_dirs`, `context.history_file`); mocks register only for servers declared in the plugin's MCP config. Decision: local runs use `evals/run.py` (own env: hooks on, `GERRIT_STACK_TRACE`, arms with/without/mcp-only); CI (Linux) can use the official runner.
- Probe c: `${CLAUDE_PLUGIN_ROOT}` **is** substituted inside a plugin SKILL.md body under `--plugin-dir` (printed the absolute plugin path). Skills write the literal `bash "${CLAUDE_PLUGIN_ROOT}/scripts/x.sh"`; no fallback.
- Probe d: `if: "Bash(git *)"` fires the PreToolUse hook for `cd sub && git status` AND `git -C sub status`, and not for `echo hi`; the hook receives the whole command string. PostToolUse fired only for the command that actually ran. **Also learned:** Claude Code's own permission layer refused `cd sub && git status` under an allowlist of `Bash(git *)` ("changes directory before running git, which can execute untrusted hooks") → skill recipes use `git -C <dir> …`, never `cd <dir> && git …`. SessionStart and Stop hooks from the plugin ran in `-p` mode.

## Open issues
- (none blocking)

## Verification log
- 2026-09-29 `claude plugin validate ./ --strict` → "Validation passed".
- 2026-09-29 probe c/d via `claude -p --plugin-dir` with stub hooks + `GERRIT_STACK_TRACE` → see Phase 0 findings.
