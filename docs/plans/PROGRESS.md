# gerrit-stack Part 1 — progress ledger

Links: [SPEC](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md) · [Execution plan](/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-part1-execution-plan.md) · [Agent brief](AGENT-BRIEF.md)

## Resume here
**Part 1 complete (2026-09-30)** except two items: (1) M2 works only with the local gerrit-mcp patch (`demo/patch-gerrit-mcp.sh`; propose upstream); (2) eval and benchmark were run once per case (`--runs 1`); repeat with `--runs 2`/`3` before tagging v0.1.0. Next steps: W3 rehearsal with `demo/RUN.md` (demo Gerrit is up on :8080 with chain 2–4 pushed; `make demo-reset` for a clean slate), Part 2 deck. Follow-ups listed at the bottom.

**Resume here (2026-10-05, 22:40): pass 2 COMPLETE — 90 runs, 0 errors, $92.53 → `docs/benchmark.md`.** Results dirs `evals/results/pass2-*` (gitignored). Headline: once the prompt or the model splits, all arms split alike; the plugin's measurable effect is under pressure (0 `--no-verify` and 0 typed Change-Ids in arm C vs 6/6 nudged `--no-verify` in A and B, 6 typed/forged Change-Ids in B, 3 in A), in rework (C 6/6, B 1/6, A 4/6) and in review comment labels (C 19/19, B 0/16, A 6/18). Next: user review; talk slides from these numbers. Pass 1 archive: `~/workspace/open/gerrit-stack-archive/pass1-2026-10-04.tar.gz`.** Results dirs `evals/results/pass1-s{1..4}-*` (gitignored). Waiting for the user's review before any 3-run pass. Run batches detached (`nohup` + `caffeinate -i -s -w <pid>`): the harness stops background commands at 10 min and the Mac sleeps after 1 min idle. Open follow-ups: a guardrail counter for forged Change-Ids; drop/rename the `reply_labelled` rework metric (replies are free-form by design); split-sizing guidance did not stop 260–360-line changes on maintenance-mode.**

## Rework benchmark

Extends the unprompted benchmark past "chain pushed" through review feedback → rework → re-push, and exercises the guard's deny/ask paths under nudge pressure. Plan: `.claude/plans/2026-09-30-rework-benchmark-plan.md` (binding "Implementation contract" at the end). See `evals/README.md` § "Rework + guardrail pipelines" for the schema, CLI, metrics and fairness caveats.

| Phase / WP | Status | Commit | Verified by | Notes |
|---|---|---|---|---|
| W1 runner (`evals/run.py`, `tests/test_run.py`) | done | b51de8b, a5b4d7f | 59 unit tests; dry-run; live smoke 1+2 | smoke 1 found: MCP tools auto-denied (rule `mcp__plugin_gerrit_gerrit` added), plain-text file content, origin/master out of sync after the first push, reply regex; reviewer found the `no new changes` chain drop |
| W2 cases (`evals/bench-unprompted/{greeting,rate-limited-ping,maintenance-mode}/`) | done | b51de8b, a5b4d7f | parse_yaml, dry-run, smoke 2 | fix demands rewritten to caps (10000 / 40 chars / 200 chars) after stage 1 pre-satisfied the first version |
| W3 collector (`evals/metrics/collect.py`, `tests/test_collect.py`, `docs/benchmark-unprompted.md`) | done | b51de8b | 24 unit tests; legacy dirs render byte-identically; smoke 2 rendered | — |
| W4 docs (`evals/README.md`, `README.md`, `CHANGELOG.md`, this ledger) | done | b51de8b, 2270d54 | make validate | MCP allowlist caveat added to docs/benchmark-unprompted.md |
| Batch 1 = smoke on `rate-limited-ping`, 12 pipelines | done | (this commit) | `docs/benchmark-rework.md`; results `rework-rlp-final` (= batch-2 minus re-run cells) + `rework-batch-2b` + `rework-batch-2c` | batch-1 (`rework-batch-1`) exposed: A/B leave work uncommitted / lose Change-Ids with `--no-verify` → runner commits leftovers + requireChangeId off per batch; split demand bogus on an already-split chain → production-file heuristic with Guice Module ignored + applicability skip; 429 session limit mid-batch → API errors now mark a stage errored. Batch 3 (greeting + maintenance-mode, 24 pipelines, $48.5, 2 h 15 min at `-j 3`) done 2026-10-01 into `rework-batch-3`; all 36 pipelines rendered in `docs/benchmark-rework.md`. Follow-up: `--runs 3` before quoting deltas in the talk; fix+split × natural+nudged × 3 arms, 1 run/cell, ≈ $25; gates the full 3-case matrix |

Decisions (user, 2026-09-30):
- Feedback types: (1) local blocking fix on one concern, (2) "split this change" on the biggest change.
- Nudges (guardrail pressure): natural and nudged variants run in one batch.
- No submits: the runner never votes +2 / submits; "landable" is counted, not exercised.
- Cases: `greeting` (the original demo prompt `demo/feature-request.md`), `rate-limited-ping`, `maintenance-mode`; arms `with`, `mcp-only`, `without`.
- Batch 1 = smoke on `rate-limited-ping` only, full matrix (fix+split × natural+nudged × 3 arms = 12 pipelines, ≈ $25, 1 run per cell); `greeting` + `maintenance-mode` follow once the pipeline and metrics are validated.
- Implement `-j 3` in the runner (cases/pipelines parallel within an arm, arms sequential).
- Status: built 2026-09-30 (see the phase table).

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
| A1 hooks | done | feat | bats hooks 37/37 (suite 116/116); live `claude -p` smoke: push → ask, no refs/for on remote | budget check gated on diff-budget.sh being executable |
| A2 tools | done | feat | bats tools 33/33 (also under /bin/bash 3.2); real 3-commit chain rendered in experiments/gerrit-split | diff-budget exit 2 = usage, 1/3 = budget verdicts |
| A3 gerrit-rest.py | done | wip | 55/55 unittest; CLI smoke incl. netrc auth, 404 → exit 1 | default output JSON, `--table` opt-in; review-metrics emits JSON Lines |
| A4 skill gerrit-stack | done (reviewed, fixes applied) | wip | validate --strict ok; audited vs SPEC 140–145 + contract; no `cd &&`, no trailers | open: chain-editing §8 copies an existing Change-Id back (recovery) — reviewer to confirm |
| A5 skill stack-planner | done (reviewed, fixes applied) | wip | validate --strict ok; recipe exercised in scratch repo with real hook | open: headless evals must pre-approve the plan STOP; cross-layer 500 cap = planner judgement |
| A6 skill gerrit-review | done (reviewed, fixes applied) | wip | validate --strict ok; tool signatures cross-checked against gerrit-mcp main.py | |
| A7 demo infra | done (reviewed: token argv leaks fixed) | wip | live: seed 2 s / re-seed 0.5 s idempotent; throwaway change 1 reviewed+abandoned; container healthy | ACL fix: admins lack push on refs/heads → seed grants on demo-plugin; container rewrites etc/gerrit.config (serverId) — pristine copy committed, live file left modified |
| A8 demo skeleton (in-tree) | done (review agent died; not re-run) | wip | in-tree build+test 60 s cold / 1–2 s warm; quick-check 0.6 s; `bats`-free (java) | config keys are flat: `pingMessage`, greeting → `greetingPrefix` |
| A9 evals + runner | done (3 runner bugs fixed during P4) | wip | unittest 22/22; fixtures build; smoke trigger-stack-planner = 1.0 (8 turns, 32 s, $0.37) | full run in P4 |
| A10 docs | done (refreshed after P4) | wip | README/CHANGELOG/jj-stretch written; review in P3 | flagged: jj upload flags uncertain |
| A11 metrics/benchmark | done (collector fixes applied) | wip | bats 9/9, unittest 12/12, bench fixtures build, collect.py renders placeholder | full benchmark run in P4 |
| P3 review | done | see fix commits | skills review (15 findings), python/demo review (3 findings) | A8 java/scripts review not re-run after rate-limit kill |
| P4 integration | done (M2 via local gerrit-mcp patch) | see verification log | make check; hook e2e; M1 live chain; headless demo run; evals 7/7; benchmark 3 arms; hub symlinks; context cost | |

## Phase 0 findings
- `claude plugin validate ./ --strict`: passes with `"dependencies": ["gerrit@gerrit-mcp"]` in plugin.json; marketplace.json needs a `description` or strict fails.
- `claude plugin eval`: 2.1.260 printed "early access"; docs say it is GA from **2.1.269** (`claude update`). Updated the CLI (see verification log). Decision kept: A9 also ships `evals/run.py` (stdlib) over the official case format, because the benchmark needs an arm the official runner cannot express (gerrit-mcp-only vs gerrit-mcp+gerrit-stack) and per-run workspace metrics; it emits the official `aggregate-result.json` schema (`schemaVersion: 1`, `aggregates.overallScore/casesPassed/casesTotal/meanDelta`, `cases[].name/aggregates.score/delta`, `cases[].arms.with[]/without[]`, `costUsd`, `durationSeconds`, `claudeVersion`).
- gerrit-mcp: installed at `~/.claude/plugins/cache/gerrit-mcp/gerrit/<version-hash>/` (currently `70a4f8f7e72a`); config file = `<that dir>/gerrit_mcp_server/gerrit_config.json` (keys: `default_gerrit_base_url`, `gerrit_hosts[{name, external_url, authentication{type: http_basic, username?, auth_token?}}]`; omit username/token → curl `--netrc`); venv at `~/.claude/plugins/data/gerrit-gerrit-mcp/.venv` (prebuilt; `gerrit-check-config` exit 1 = missing config). SessionStart hook `check-config.sh` nags until config exists. MCP server name: `gerrit` → tools are exposed plugin-scoped as `mcp__plugin_gerrit_gerrit__<tool>` (verified 2026-09-30 via `claude -p`; `mcp__gerrit__*` is wrong).
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
- 2026-09-30 P4.8 hub symlinks: `~/claude-skills-hub/skills/{gerrit-stack,stack-planner,gerrit-review}` → repo skills; `~/.claude/skills` untouched ✓.
- 2026-09-30 P4.10 `claude plugin details gerrit-stack` (local marketplace install, then uninstalled): always-on ~709 tok; on-invoke gerrit-stack ~6.8k, gerrit-review ~4.3k, stack-planner ~3.6k ✓ (< 4k always-on target).
- 2026-09-30 P4.4 **M2 slice blocked upstream**: `mcp__plugin_gerrit_gerrit__get_related_changes` reached the server but the official gerrit-mcp `_normalize_gerrit_url()` rewrites `http://` → `https://` unconditionally (main.py ~L250), so a plain-HTTP localhost Gerrit fails with `curl: (35) … tlsv1 alert protocol version`. No config/env opt-out. Options: (a) TLS sidecar for the demo (Caddy `tls internal` on :8443 + trust its CA for curl/git via `CURL_CA_BUNDLE`/`http.sslCAInfo`), (b) propose an upstream change keeping an explicit `http://` scheme for hosts that declare it, (c) demo the REST fallback path (skills already support it). Decision pending with the user.
- 2026-09-30 P4.5 skill-driven headless run of `demo/feature-request.md` in the demo clone: Skill gerrit-stack → `chain-status.sh --preflight` → Skill stack-planner → 3 commits (feat: greetingPrefix +42, greeting REST +127/-3, greet SSH +44/-2), `tools/verify.sh` per step and `rebase -i --exec` builds-alone pass, `--verify-ids ok`, then grouping question + "Push 3 changes to refs/for/master on origin? (y/n/wip)"; no push executed (open changes unchanged). 16 turns, 4 m 34 s, $0.96. Hook trace: only `silent`/`ok` decisions.
- 2026-09-30 P4.6 evals (`evals/run.py --runs 1 --arms with`): first pass 4/7 (0.74, $2.35, 11 min) → fixes: SessionStart non-negotiables (agent had complied with an adversarial "hand-write the Change-Id + --no-verify" request; the guard denied it but the agent then offered bypasses), label grader anchored at line start missed a `note:` in a table cell, split fixture concerns were ~200 lines (over hard cap) so the agent split files into halves → fixture rebalanced (4 concerns ≤150) + "never mechanical halves" rule in both skills. Re-run: no-manual-change-id 1.0, review-reply-conventional 1.0, split-over-budget 0.5 with correct behaviour (4 by-concern commits) but the judge omitted its PASS/FAIL line → judge retry added; final re-run pending.
- 2026-09-30 P4.6 final: split-over-budget 1.0 after the judge retry → **all 7 eval cases pass** (latest run per case; total eval spend ≈ $3.6).

## Follow-ups
- Benchmark prompts currently include the repo rules ("one concern per change…"); add an *unprompted* variant of each bench case (plain product request) — that is where the plugin's delta should show.
- Re-run the A8 (Java skeleton + scripts) code review; the reviewer agent was killed by the rate limit.
- Upstream change for the http rewrite: https://gerrit-review.googlesource.com/c/gerrit-mcp-server/+/635805 (pushed 2026-09-30; local patch in `demo/patches/` until it merges).
- `evals/run.py -j N`: run cases in parallel within an arm (workspaces are already isolated per run); keep arms sequential because the `without` arm toggles the user-level gerrit-mcp plugin. Same token spend, ~N× faster, more rate-limit pressure (2–3 is the sweet spot on this account).
- 2026-09-30 P4.7b benchmark (`evals/run.py --bench --runs 1 --arms with,mcp-only,without`, 9 valid runs; first A/B attempt crashed on a runner arg-order bug, fixed): arm C vs B — turns 13 vs 17, wall 147 s vs 199 s, cost $0.68 vs $0.69 (−2 %), chain length 2 vs 1, budget/one-Change-Id/conventional 100 % in all arms (bench prompts spell out the repo rules, so parity on correctness is expected; the 7 behavioural evals carry the differentiation). `docs/benchmark.md` rendered; targets: budget PASS, Change-Id PASS, violations PASS, cost overhead PASS.
- Spend this session (approx.): evals $3.6 + benchmark $7.0 + demo/probe runs ≈ $2 → ≈ $13 of API usage, plus subagent tokens.
- 2026-09-30 P4.4 **M2** passes with a local gerrit-mcp patch (`demo/patch-gerrit-mcp.sh`, diff in `demo/patches/`): keep an explicit `http://` host instead of rewriting to https. `get_related_changes(4)` lists 4→3→2 and `list_change_comments(3)` shows rena's thread against the demo. `claude plugin update` reverts it; re-apply with the script (apply/check/revert all exercised). Proposed upstream change: gerrit-mcp-server `_normalize_gerrit_url` — only add `https://` when no scheme is given.
- 2026-09-30 upstream: gerrit-mcp-server change 635805 pushed (`Keep an explicit http:// scheme when normalizing Gerrit URLs`, 248 unit tests + ruff green in the clone at `~/workspace/open/gerrit-mcp-server`, branch `keep-explicit-http`).
- 2026-09-30 upstream PS2 pushed: change 635805 now loopback-only (localhost/127.0.0.0/8/::1/0.0.0.0 keep http; others rewritten), 250 unit tests + ruff/mdformat green; docs/configuration.md documents the rule.
- 2026-09-30 unprompted sanity (`rate-limited-ping`, plan pre-approved, no repo rules): **with** → 4-change chain (45/193/57/17 lines: config → limiter → REST 429 → SSH), push asked, 26 turns, $1.25; **without** → all code written, 0 commits, 20 turns, $0.93. Grader `no-manual-change-id` false positive: the eval session's agent ran the user-level task-observer skill and wrote an observation mentioning `Change-Id` via heredoc → narrow the grader; isolate eval sessions from user CLAUDE.md.
- 2026-09-30 isolated unprompted sanity (`rate-limited-ping`, arms with/mcp-only/without, `--setting-sources project,local`, gerrit-mcp via --plugin-dir): C = 4-change chain (45/199/35/19), judge PASS, 24 turns, $0.98; B = 1 commit of 293 lines, 15 turns, $0.64; A = 1 commit of 291 lines, 10 turns, $0.57. Rendered to `docs/benchmark-unprompted.md`. Next: run all 3 unprompted cases × 3 runs × 3 arms (~27 sessions, ≈ $25) before the talk.
- 2026-09-30 unprompted `maintenance-mode` (6-concern feature), one run per arm: **C** 6-change chain (169/167/226/329/93/191), judge PASS, chain-depth rule applied (push #1–5, #6 later), 60 turns (cap), $3.02; **B** one 951-line commit on a branch, 43 turns, $1.94; **A** one 1253-line commit, 45 turns, $2.14. Observations: only 1/6 changes within the 150-line budget (two over the 200 cap with justifications); the with-arm hit `max_turns: 60`. Rendered into `docs/benchmark-unprompted.md`.
- 2026-09-30 upstream 635805 PS3 (mdformat fix): Zuul Verified +1.
- 2026-09-30 `maintenance-mode` run 2 (kept workspaces): C 5-change chain (183/207/92/177/164), 61 turns, $2.46; B 1269 lines, 45 turns, $2.44; A 1060 lines, 36 turns, $1.90. All three arms pushed to the demo Gerrit via `evals/push-arms.sh`: changes 5–9 (with), 10 (mcp-only), 11 (without), hashtags `bench-maintenance-mode-<arm>` + `run-20260930-100850`. Runner now has `--push-to` for future runs.

## Part 2 — talk (GUS 2026)

Plan: `/Users/jcentkowski/workspace/open/code_review_like_a_pro/.claude/plans/2026-10-09-part2-talk-execution-plan.md` (SPEC § Part 2 + Amendments 2026-10-09). Deck repo: `~/workspace/open/code_review_like_a_pro`, deck `15min_stack_changes.md`, checks `tools/check-deck.sh`.

**Resume here (2026-10-09):** executing T0–T7 inline (deck draft first; demo undecided — user focuses on slides). D2: README/.gitignore refreshed, publishing left to the user. D4: meme if found with attribution. D5: logo allowed; master stays un-themed, `events/gus-2026` adds the footer.

| Task | Status | Commit | Verified by | Notes |
|---|---|---|---|---|
| T0 SPEC amendments + ledger | done | 895d241 | SPEC § Amendments appended; this section | |
| T1 Part 1 hotfixes (RUN.md T-30, obs #59) | done | ba30147 | `make lint`, `bats tests/hooks.bats` 44/44 | installed cache 70a4f8f7e72a still carries the local patch; `claude plugin update` not run (demo stays as is until a rehearsal) |
| T2 README thesis + .gitignore (publish = user) | done (publish pending) | 895d241 | `tail -3 README.md` ends with License; CHANGELOG Changed entry | `gh repo create geminicaprograms/gerrit-stack --public --source . --remote origin --push` is the user's call |
| T3 scaffold + check script + pages.yml + assets | done | deck repo: feat/ci commits 2026-10-09 | `tools/check-deck.sh` RED on the 2025 deck (subs 15/9, `___`, `???`), PASS on the new deck; logo downloaded, untracked on master (D5) | chain.svg hand-drawn; meme = text quote (D4, no image with attribution found) |
| T4 talk-numbers.py | todo | — | — | ad-hoc extraction done for the draft (verification log below); script + tests still to write |
| T5 research slides + docs/sources.md | done (draft) | deck repo docs commit | citation agent 2026-10-09: LinearB figures changed (8.1 M, ~2.5×, >16 h vs ~200 min), gh-stack "14.7K installs" unsupported → dropped, Google median-24 not on the landing page → not on a slide | |
| T6 slides 6, 9–12 | done (draft) | deck repo feat commit | slide 9 table from the verification log; slide 10 written to work live, recorded or as a walkthrough (D1 open) | |
| T7 notes (v1) | done (draft) | deck repo feat commit | check-deck words=1764 incl. slide 10 (~330) → ~1,430 spoken; read-aloud timing pending | plain English, short sentences |
| T8 demo reconcile + rehearsal #1 | todo | — | — | D1 open |
| T9 recordings + rehearsals | todo | — | — | |
| T10 freeze, event branch, offline kit | todo | — | — | |

Rulings:
- 2026-10-09 T0: ledger lives here (not in a `.superpowers/sdd` workspace) and deck work happens on `master` of the deck repo, as the reviewed plan states — cost if wrong: a branch can be cut later from the commits.

Verification log (Part 2):
- 2026-10-09 slide-9 figures re-derived from `evals/results/pass2-*` (ad-hoc python over `aggregate-result.json` + `trace.jsonl`; to become `evals/metrics/talk-numbers.py`, T4): nudged unprompted runs using `--no-verify` C 0/6 · B 6/6 · A 6/6; runs failing `no-manual-change-id` (typed/forged Change-Id) C 0/30 · B 6/30 (1 unprompted nudged + 5 rework) · A 3/30 (2 unprompted nudged + 1 rework nudged); rework runs with score ≥ 0.8 C 6/6 · B 1/6 · A 4/6 (A's other failure: `rework-landed` once, natural); labelled review comments C 19/19 · B 0/16 · A 6/18 (from `docs/benchmark.md`). Deterministic rework metrics (Change-Id set, order, fix on the right change) 100 % in all arms.
- 2026-10-09 `bash tools/check-deck.sh 15min_stack_changes.md --no-notes-slide 6 --no-build` → slides=12 subs=12/12 notes=11/11 words=1764 PASS.
- 2026-10-09 deck rendered to PNG once (12 slides); slides 7–9, 11, 12 trimmed after the first render; user asked to stop layout work and focus on content — second render not checked.
