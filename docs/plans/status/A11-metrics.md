# A11 — metrics / efficiency benchmark

status: done

Resumed WP: `scripts/chain-metrics.sh` (441 lines) existed from the killed
first run, unverified. This pass verified it, fixed one real bug, and wrote
everything else the WP owns.

## Files written (owned by A11; nothing else touched)
- `scripts/chain-metrics.sh` — verified + 3 edits: (1) **bug**: per-change rows
  were TAB-separated and read back with `IFS=$'\t'`, which collapses
  consecutive tabs, so a commit without a Conventional Commit type (e.g.
  `fixup! …`) shifted every following field and broke `jq --argjson`; the row
  was silently dropped from `changes[]` (aggregates were still right). Fixed
  with a `-` placeholder for the type column. (2) `# shellcheck disable=SC2329`
  on the trap-invoked `cleanup()` (shellcheck 0.11 info). (3) removed a dead
  `cleanup_wt_now` branch.
- `evals/bench/{feature-3-concern,bugfix-1-concern,refactor-then-feature}/`
  `{prompt.md,case.yaml,fixture.sh,graders/{one-concern-per-change,no-push-executed,no-manual-change-id}.md}`
  — official case format (`schema_version: "1.1"`, `tags: [bench]`, `runs: 3`,
  `max_turns: 40`, `timeout_seconds: 900`, `allowed_tools: [Read, Glob, Grep,
  Edit, Write, Bash, Skill]`; `case.yaml` → `context.scaffold_script:
  fixture.sh`). `fixture.sh` is self-contained (does not source
  `evals/fixtures/scaffold-common.sh`): copies `demo/skeleton/` (plugin root
  from `EVAL_PLUGIN_ROOT` or the script's own path), `git init -b master`,
  bare `../remote.git`, `remote.origin.push=HEAD:refs/for/master`,
  `.gitreview`, real `tests/fixtures/commit-msg` into
  `$(git rev-parse --git-path hooks)`, initial commit (Change-Id from the
  hook), `push origin HEAD:refs/heads/master`, `gerrit-stack.verify-cmd 'bash
  tools/quick-check.sh'`. Graders: `llm` rubric (one concern per change /
  plan before code / asked before push / no hand-written Change-Id /
  conventional subjects), `tool_used` Bash `input_match: "git push"`
  `min: 0 max: 0`, `regex` `target: trace` `match: not_contains` pattern
  `"command":"(?:[^"\\]|\\.)*Change-Id:` (JSON-escaping note in the body).
- `evals/metrics/collect.py` (stdlib, 640 lines) — see "collect.py" below.
- `docs/benchmark.md` — rendered placeholder ("No runs yet", arms A/B/C, targets table, `make bench` footer).
- `tests/metrics.bats` (9 tests) — own temp repo with the real hook, hermetic
  git env; does **not** load `tests/helpers.bash`.
- `tests/test_collect.py` (12 tests) — synthetic results dir.
- `docs/plans/status/A11-metrics.md` (this file).

## chain-metrics.sh — JSON keys
`repo, head, base, base_ref, budget{lines,files}, chain_length, changes[]{sha,
subject, lines, files, change_ids, conventional_type, is_fixup, within_budget,
single_concern, builds_alone}, lines_median, lines_p75, lines_max,
files_median, within_budget_pct, one_change_id_pct, conventional_pct,
single_concern_pct, fixups_present, verify_cmd, builds_alone_pct,
verify_error, remote{name,url,local,refs_changes,refs_for,refs_for_commits,
refs_for_pushed}, refs_for_pushed, hook_trace{file,missing,lines,violations,
violations_by_verb,asks,asks_by_verb,feedback}, violations,
violations_by_verb, asks, asks_by_verb, change_id_set[]`.
Percentages are over all chain commits (fixups count as lacking a Change-Id —
the hook skips them by design; `fixups_present` flags that). Optional blocks
(`builds_alone_pct`, `remote`/`refs_for_pushed`, `hook_trace`/`violations`)
are `null` unless the flag is given. `refs_for_pushed` on a plain bare remote
= refs under `refs/changes/` + commits reachable from `refs/for/*` but not
`refs/heads/*` (a bare repo stores a push to `refs/for/master` literally).
`--verify-cmd` runs `git -c sequence.editor=true rebase -i --exec <cmd>
<base>` in a temporary detached `git worktree`, continues past failures so
every change is checked, and removes the worktree; the caller's HEAD, index,
untracked files and worktree list are untouched (tested).

## collect.py
`python3 evals/metrics/collect.py [--results DIR ...] [--out docs/benchmark.md] [--json PATH]`.
Default results = every `evals/results/*` with an `aggregate-result.json` or
`runs/`. Per run (`runs/<case>/<arm>/<n>/`): from `trace.jsonl` (stream-json:
`assistant` → `tool_use`, `user` → `tool_result`, `result` → `total_cost_usd`,
`num_turns`, `duration_ms`, `usage`) → `turns, tool_calls, bash_calls,
git_commit_calls, git_push_calls, cost_usd, wall_s, self_corrections` (a Bash
call whose `tool_result` is an error mentioning hook/denied/blocked, followed
by a successful Bash call with the same git verb); `asks` = hook-trace `ask`
+ `AskUserQuestion` calls; `denies` = hook-trace `deny` (falls back to the
trace-detected count); `score` from the aggregate arm entry (`score` or
`passed`); `chain-metrics.json` merged verbatim. Runs present only in the
aggregate (no run dir) still count, with `costUsd/durationSeconds/numTurns`
if present. Aggregation per case × arm and overall (mean, median), deltas
`with − mcp-only` and `with − without` (of means), targets on arm `with`:
within_budget ≥ 90 %, one_change_id = 100 %, violations = 0, cost overhead
≤ +30 % vs `mcp-only`. Rows with no value in any arm are omitted from the
tables.

## Verification (2026-09-30)
| command | result |
|---|---|
| `shellcheck -x scripts/chain-metrics.sh evals/bench/*/fixture.sh` | clean |
| `/bin/bash -n` (bash 3.2.57) on both; `/bin/bash scripts/chain-metrics.sh --json .` | ok |
| `bats --print-output-on-failure tests/metrics.bats` | 9/9 ok |
| `python3 -m unittest discover -s tests -p 'test_collect.py' -v` | 12/12 OK |
| `python3 -m py_compile evals/metrics/collect.py tests/test_collect.py` | clean |
| `python3 evals/metrics/collect.py --out /tmp/bench.md` (no results) | placeholder rendered; same command without `--out` produced `docs/benchmark.md` |
| each `evals/bench/*/fixture.sh` run in an empty temp dir (with and without `EVAL_PLUGIN_ROOT`, incl. a copy of the script outside the tree) | workspace built, `git log` shows `chore: import demo-plugin skeleton` with a `Change-Id: I…` trailer, `../remote.git` has `refs/heads/master`, `bash tools/quick-check.sh` exit 0 in 1 s, `chain-metrics.sh` → `base_ref: refs/remotes/origin/master, chain_length: 0` |
| grader regex checked with python `re` against synthetic stream-json lines (commit `-m 'Change-Id: …'`, heredoc, `interpret-trailers` → match; hook output in `tool_result`, adjacent tool calls → no match) | ok |
| smoke: 3-commit chain + `fixup!` with `--verify-cmd`, `--hook-trace`, `--remote origin` | table + JSON correct; caller `git status` clean, 1 worktree |
| `git status --porcelain -uall` | only A11 files new/modified (plus other WPs' `evals/fixtures/*`, `tests/helpers.bash`, `scripts/lib/`, `demo/etc/gerrit.config` — untouched by me); no `git add`/`commit`; `claude` never run |

## Open issues / notes for other WPs
- **A9 (`evals/run.py`)**: per-run dir contract assumed exactly as the plan:
  `runs/<case>/<arm>/<n>/{trace.jsonl,hook-trace.log,chain-metrics.json}` with
  arm names `with|mcp-only|without` and `n` a 1-based integer dir; run
  `scripts/chain-metrics.sh --json --hook-trace <log> --remote origin
  --verify-cmd "$(git config gerrit-stack.verify-cmd)" <workspace>` after each
  run. `aggregate-result.json` arms lists are matched to run dirs by index
  (or an explicit `run` int). The `--bench` flag in `make bench` is A9's to
  implement; the bench cases carry `tags: [bench]` for selection. Bench
  `fixture.sh` needs `EVAL_PLUGIN_ROOT` only when the case dir is copied out
  of the tree.
- **A3**: `review-metrics` JSON Lines shape is fine; `collect.py` does not
  consume it (review-side metrics are a separate, live-Gerrit step).
- The official runner passes only `EVAL_*` env; the fixture's git identity
  defaults (`Bench Fixture <bench@example.com>`) cover the no-config case.
- Grader frontmatter follows the Phase 0 findings (`type, weight` + per-type
  keys; `llm` uses `focus` + rubric body). Not validated with `claude plugin
  eval` (gated on this machine; A9/CI to confirm).
