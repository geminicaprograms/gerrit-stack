# A9-evals — status

status: done

## Files written (all new; nothing outside this list touched)

- `evals/run.py` — stdlib runner over the official case format: case discovery (`--case GLOB`,
  `--bench`), YAML-subset parser (scalars, quoted strings, flow lists/maps, block lists, nested
  maps, `|`/`>` scalars), per case × arm × run temp workspace → `fixture.sh` (env `EVAL_*` +
  `EVAL_PLUGIN_ROOT`) → `claude -p --output-format stream-json --verbose --max-turns N
  --allowedTools … [--model M] [--plugin-dir <repo>] "<prompt>"` with
  `GERRIT_STACK_TRACE=<run>/hook-trace.log`; arms `with` / `mcp-only` / `without`
  (`claude plugin disable gerrit@gerrit-mcp` for the duration, restored in `finally`),
  `--ablation`; trace parsing (init, tool_use/tool_result, result cost/turns/usage/subtype);
  graders `regex` (contains / not_contains / count:N over last_message | trace | files |
  {source: file, path}), `tool_used` (input_match over compact JSON input, min/max),
  `tool_order` (before/after, name or {tool, input_match}), `file_exists`, `llm`
  (`claude -p --model <judge> --max-turns 1`, PASS/FAIL last line, majority of `--judge-votes`),
  `baseline` skipped with a warning; `chain-metrics.sh --json --hook-trace` → `chain-metrics.json`
  (failure ignored); `.stub-pid` killed; workspace deleted unless `--keep`; cost ceiling → partial +
  exit 2; `aggregate-result.json` (schemaVersion 1, official keys), `report.md`, stdout table,
  `--dry-run`, `--json PATH`, `--out-dir`, process-group kill on timeout, run failures never abort.
- `evals/fixtures/scaffold-common.sh` — `make_gerrit_workspace [--chain N] [--project-kind sh|java]`
  (master, bare `../remote.git`, `remote.origin.push=HEAD:refs/for/master`, `.gitreview`, real
  `tests/fixtures/commit-msg` into `$(git rev-parse --git-path hooks)`, sh or `demo/skeleton`
  project, initial commit pushed to `refs/heads/master`; `--chain N` = N hook-Change-Id commits,
  change 2 touches `greet.sh` line 3), `make_dirty_diff <lines>` (3 concerns: `lib/logging.sh`,
  `lib/config.sh`, `docs/USAGE.md` + `tests/test_config.sh`, plus a wiring edit in `greet.sh`).
- `evals/fixtures/gerrit-rest-stub.py` — `http.server` on `127.0.0.1:<free port>`, `)]}'` prefix,
  with/without `/a/`: `related` (chain 1→2→3), `comments` (one unresolved rena `issue (blocking)`
  on `greet.sh:3` of change 2), `detail`, `/changes/?q=`, version, `accounts/self`; `POST …/review`,
  `PUT …/topic`, `POST …/hashtags`, drafts → `review-posts.jsonl`; `.stub-port`/`.stub-pid`;
  `--port` (0 = free), `--dir`, `--change-ids`, `--subjects`, `--project`; responses never contain
  the word `labels`.
- 7 cases `evals/<case>/{prompt.md,case.yaml,fixture.sh,graders/*.md}`:
  `trigger-gerrit-stack` (tool_used Skill `[:"]gerrit-stack"`, llm planned-before-edit),
  `trigger-stack-planner` (tool_used Skill `stack-planner"`, regex `Step 1`, Edit/Write max 0,
  llm plan+approval), `plan-before-code` (tool_order Skill→Edit, Skill→Write, tool_used Skill),
  `push-requires-confirm` (llm grouping + y/n, regex `refs/for/master` over trace, regex
  not_contains `%topic=` scoped to Bash commands, tool_used Bash `git push` max 0),
  `no-manual-change-id` (llm refused/used hook, regex not_contains Change-Id-composing Bash
  command with the JSON-escaping explanation in the body, tool_used `--no-verify` max 0),
  `split-over-budget` (`make_dirty_diff 600`; tool_used stack-planner, llm ≥ 2 commits by
  concern/retro-split, regex not_contains `--no-verify` scoped), `review-reply-conventional`
  (fixture starts the stub + `git config gerrit-stack.host`; regex `^(issue|suggestion|…)` flags m
  over last_message, llm no vote + asked before posting, regex on `review-posts.jsonl`:
  nothing posted, no `labels`).
- `evals/README.md` — local runs, arms, why the standalone runner exists (Docker symlink refusal
  quoted), CI command with the official runner, results layout, adding a case, cost notes.
- `tests/test_run.py` — 22 unittest cases: YAML subset, frontmatter, trace parsing, every grader
  type on synthetic traces (incl. the real `no-manual-trailer` pattern against 5 violating and 2
  clean commands), weighted score, case loading/discovery, aggregation + schema keys, report,
  command builder per arm, arg parsing, and that all 7 real cases load and their regexes compile.
  No `claude` spawned.

## Verification

- `python3 -m py_compile evals/run.py evals/fixtures/gerrit-rest-stub.py` → ok.
- `python3 -m unittest discover -s tests -p 'test_run.py' -v` → 22 tests OK
  (`-p 'test_*.py'` also OK together with the other WPs' python tests).
- `shellcheck -x evals/fixtures/scaffold-common.sh evals/*/fixture.sh` → clean
  (SC2016 disabled file-wide in scaffold-common.sh: generated snippets are single-quoted on purpose;
  fixtures carry `source-path=SCRIPTDIR`).
- Every `fixture.sh` run in a temp dir: rc 0, `git log` shows hook-generated Change-Ids on every
  commit; `split-over-budget` leaves ` M greet.sh`, `?? docs/ lib/ tests/test_config.sh`
  (≈ 600 lines); `review-reply-conventional` starts the stub, `curl …/changes/2/detail` answers,
  `git config gerrit-stack.host` set, stub files excluded via `.git/info/exclude`.
- Stub: start with `--port 0`, `curl` of `/changes/2/revisions/current/related`,
  `/a/changes/2/comments`, `POST …/review`, `PUT …/topic` recorded in `review-posts.jsonl`;
  `scripts/gerrit-rest.py --table comments 2 --unresolved` and `related 2` work against it
  through `git config gerrit-stack.host`; SIGTERM stops it.
- `python3 evals/run.py --dry-run --case '*'` → prints fixture + claude command for all 7 cases.
- **Smoke run** `python3 evals/run.py --case trigger-stack-planner --runs 1 --arms with
  --max-cost-usd 2 --keep -v --out-dir evals/results/smoke-A9` → exit 0, score **1.0**
  (all 5 graders PASS: Skill input was `{"skill":"gerrit-stack:stack-planner"}`, `Step 1` in the
  final message, no Edit/Write, haiku judge PASS), 8 turns, 32 s, $0.267 run + $0.100 judge,
  model `claude-sonnet-5-5`, `claudeVersion 2.1.285`; `chain-metrics.json`, `trace.jsonl`,
  `last-message.md`, `report.md`, `aggregate-result.json` written under `evals/results/smoke-A9/`
  (gitignored; kept for inspection).
- `make lint` currently fails on **A1's in-progress hook scripts** (`scripts/git-guard.sh`,
  `git-post.sh`, `session-start.sh`, `stop-check.sh`: SC2046/SC2086/SC1091), not on A9 files;
  `shellcheck -x` on all A9 shell files and `py_compile` on `evals/*.py` are clean.

## Open issues / notes for integration

- Runs inherit the user's Claude Code settings: user-level plugins/hooks and `~/.claude/CLAUDE.md`
  are active inside the eval (the smoke trace shows the personal `task-observer` skill being
  invoked). Graders only look for the plugin's own skills, so it is noise; a clean CI account
  avoids it.
- The stack-planner run noted `diff-budget.sh is missing` at the plugin path (A4 had not landed it
  yet at run time) and estimated by hand — harmless for the graders, but rerun after A4 merges.
- Judge cost: one haiku vote reported `total_cost_usd ≈ 0.10` (large cached system context);
  budget accordingly or point `--judge-model` at a cheaper/cleaner profile.
- Trace-scoped regex graders (`%topic=`, `--no-verify`, `Change-Id:`) match
  `"command":\s*"…"` strings only because the Skill tool loads SKILL.md text (which mentions
  those tokens) into the trace; `labels` is checked on the stub's `review-posts.jsonl` for the
  same reason. Documented in the grader bodies and README.
- `baseline` graders are skipped by `run.py` (official runner only).
- Official-runner caveat (README): hooks disabled + env withheld there; fixtures resolve the plugin
  root via `EVAL_PLUGIN_ROOT` or their own location, and the review chain is fixture-built.
