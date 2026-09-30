# gerrit-stack Part 1 — progress ledger

Links: [SPEC](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md) · [Execution plan](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-part1-execution-plan.md) · [Agent brief](AGENT-BRIEF.md)

## Resume here
2026-09-30: the 2026-09-29 fan-out was cut by the account session limit (HTTP 429) — agents A0, A3, A4, A6, A7, A9, A11 and two reviewers died mid-work; their partial files were committed as `wip(partial)`. Relaunch order: wave 1 = A0-lib (resume: tests exist, libs missing), A3-rest (verify/finish), A4-stack (verify/finish), A7-demo (fresh); wave 2 = A6-review (verify), A9-evals (fresh), A11-metrics (resume), then A1 + A2 (after A0), then Phase 3 reviews and Phase 4. Keep ≤ 4 agents concurrent to stay under the session limit.

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
| P1 foundation | done | see git log | manifests, hooks.json, Makefile, CI, ledger, libs + test helpers | |
| A0 lib + test helpers | done | feat | `bats tests/lib.bats` 37/37; shellcheck clean; helpers frozen | gs_detect never cd's; trailers via last-paragraph footer semantics |
| A1 hooks | done (unreviewed) | feat | bats hooks 37/37 (suite 116/116); live `claude -p` smoke: push → ask, no refs/for on remote | budget check gated on diff-budget.sh being executable |
| A2 tools | done (unreviewed) | feat | bats tools 33/33 (also under /bin/bash 3.2); real 3-commit chain rendered in experiments/gerrit-split | diff-budget exit 2 = usage, 1/3 = budget verdicts |
| A3 gerrit-rest.py | done (unreviewed) | wip | 55/55 unittest; CLI smoke incl. netrc auth, 404 → exit 1 | default output JSON, `--table` opt-in; review-metrics emits JSON Lines |
| A4 skill gerrit-stack | done (unreviewed) | wip | validate --strict ok; audited vs SPEC 140–145 + contract; no `cd &&`, no trailers | open: chain-editing §8 copies an existing Change-Id back (recovery) — reviewer to confirm |
| A5 skill stack-planner | done (unreviewed) | wip | validate --strict ok; recipe exercised in scratch repo with real hook | open: headless evals must pre-approve the plan STOP; cross-layer 500 cap = planner judgement |
| A6 skill gerrit-review | done (unreviewed) | wip | validate --strict ok; tool signatures cross-checked against gerrit-mcp main.py | |
| A7 demo infra | done (unreviewed) | wip | live: seed 2 s / re-seed 0.5 s idempotent; throwaway change 1 reviewed+abandoned; container healthy | ACL fix: admins lack push on refs/heads → seed grants on demo-plugin; container rewrites etc/gerrit.config (serverId) — pristine copy committed, live file left modified |
| A8 demo skeleton (in-tree) | done (unreviewed) | wip | in-tree build+test 60 s cold / 1–2 s warm; quick-check 0.6 s; `bats`-free (java) | config keys are flat: `pingMessage`, greeting → `greetingPrefix` |
| A9 evals + runner | done (unreviewed) | wip | unittest 22/22; fixtures build; smoke trigger-stack-planner = 1.0 (8 turns, 32 s, $0.37) | full run in P4 |
| A10 docs | done (unreviewed) | wip | README/CHANGELOG/jj-stretch written; review in P3 | flagged: jj upload flags uncertain |
| A11 metrics/benchmark | done (unreviewed) | wip | bats 9/9, unittest 12/12, bench fixtures build, collect.py renders placeholder | full benchmark run in P4 |
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
- 2026-09-30 P4.2(a) plain repo + `claude -p --plugin-dir`: no hook trace, no `[gerrit-stack]` context → silent ✓. P4.2(b) `experiments/gerrit`: SessionStart context injected (remote/host/branch/project, hook installed, chain 1 ahead) ✓; finding: branch resolved to `.gitreview defaultbranch` (master) while checkout is stable-3.12 → fix: prefer HEAD's upstream branch.
- 2026-09-30 `make check` after A1+A2: validate --strict ok, shellcheck clean, bats 116/116, python 89 tests OK.
- 2026-09-30 P4.3 **M1** on live demo: `chain-status.sh --preflight` rc=0; 3-commit chain → `push-chain.sh` printed `git push origin HEAD:refs/for/master` → changes 2,3,4 created; `gerrit-rest.py related 4` lists all 3; `reviewer-comment.sh 3` posted CR-1 + unresolved `issue (blocking)`; `comments 3` lists it. Found+fixed: demo clone was not detected as Gerrit (no .gitreview/refspec; URL regex) → seed sets remote/refspec, regex learns `/a/`.
- 2026-09-30 P3 fixes committed; `make check`: bats 122, python 90, validate --strict, shellcheck all green.
