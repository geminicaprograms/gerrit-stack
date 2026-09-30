# W3 — collector (rework + guardrail blocks)

status: done

## Files written
- `evals/metrics/collect.py` — pipeline-aware loading, rework / guardrail / cost-per-stage aggregates and sections (+530 / −8 lines).
- `tests/test_collect.py` — `PipelineResults` (11 tests) and `LegacyRegression` (golden) on top of the 13 existing tests.
- `docs/plans/status/W3-collector.md` — this file.

Not touched: `docs/benchmark-unprompted.md`, `docs/benchmark.md` (rendered pages; see "Verification").

## What the collector does now (coded against the implementation contract, not W1's files)
- **Loading.** An aggregate entry is a pipeline when its name parses as `<base>@<scenario>-<variant>`
  (`parse_pipeline_key`), or the case carries `baseCase`/`scenario`/`variant`, or the run record has
  `stage2`/`rework`/`guardrails`, or the run dir has `stage2/` or `rework-metrics.json`. Each pipeline run
  yields three rows (`stage` = `1`, `2`, `"pipeline"`) with `base_case`, `scenario`, `variant`:
  - stage 1 = `load_run(run_dir)` exactly as today;
  - stage 2 = `load_run(run_dir/stage2)` (same parsers over `stage2/trace.jsonl`, `stage2/hook-trace.log`,
    `stage2/chain-metrics.json`; score/turns/cost fallback from the run record's `stage2` block);
  - pipeline = rework metrics from `rework-metrics.json` (fallback: run record `rework`), `review.json`
    summary, `guardrails.stage1|stage2` (runner block wins; gaps filled from the stage rows: hook-trace
    asks/denies, trace self-corrections, and trace-command heuristics for `no_verify_used`, `amend_m_used`,
    `force_push_attempted`, `topic_used_unasked`, `refs_heads_push_attempted`; `commit_without_change_id`,
    `refs_heads_moved`, `gerrit_master_moved` are runner-only → `None` when absent), `cost_usd` =
    `pipelineCostUsd` (else stage sum), `wall_s` = `pipelineDurationSeconds` (else sum), `turns` = sum,
    `hashtags` (union of both stages' `hashtags`), `pushed_stage1` / `pushed_stage2`.
  - Legacy entries: scenario/variant `None`, stage 1, one row — unchanged.
- **Aggregation.** The classic blocks (Overall, Targets, Per case) use stage-1 rows only, so a pipeline's
  implement stage appears under its pipeline key (e.g. `### rate-limited-ping@fix-natural`) next to legacy
  cases. `report["pipelines"]` adds: `rework` (per base case × scenario × variant → arm → metric; booleans as
  rates `{true, n, rate_pct}`, counts as mean/median; deltas C−B and C−A on means, percentage points for
  rates), `guardrails` (per variant × stage → arm), `cost_per_stage` (per variant × {1, 2, pipeline} → arm),
  `hashtags` (`run-…`, `scn-…` seen in the aggregate).
- **Rendering** (only when at least one pipeline row exists, inserted between `## Per case` and `## Reproduce`):
  `## Rework` (intro, "Where to look in Gerrit" line with `hashtag:run-<id>` / `hashtag:scn-…`, one table per
  pipeline with a per-table `hashtag:scn-…`, then `### Reading the rework numbers`), `## Guardrails`
  (`### <variant> — stage N` tables: asks, denies, self-corrections, every bad-outcome counter), `## Cost per
  stage` (variant × stage rows, `USD · turns · wall` per arm, Δ cost C−B / C−A). Rows no arm has are omitted
  (so `split_*` rows only show for split pipelines).
- **JSON** (`--json`) gains `pipelines` with the same aggregates; every row carries the new fields.
- CLI unchanged (`--results`, `--include-errors`, `--out`, `--json`); the summary line appends
  `(N pipeline run(s))` only when pipelines are present.

## Verification
```
python3 -m py_compile evals/metrics/collect.py tests/test_collect.py     # clean
python3 -m unittest tests.test_collect -v                                 # Ran 24 tests … OK
```
Regression on the real result dirs (rendered to `/tmp/x.md`, not into `docs/`):
```
python3 evals/metrics/collect.py --results evals/results/20260930-083458 evals/results/20260930-091703 evals/results/20260930-100850 --out /tmp/x.md --json /tmp/x.json
python3 evals/metrics/collect.py --results evals/results/20260930-064551 evals/results/20260930-072437 --out /tmp/y2.md
python3 evals/metrics/collect.py --out <scratch>/all.md          # default: all 11 evals/results/* dirs, 40 runs
```
- All three renders are **byte-identical** (modulo the `*Generated …*` timestamp line) to the same commands run
  with the committed collector (`git show HEAD:evals/metrics/collect.py`), and the JSON row count is unchanged.
- vs the committed `docs/benchmark-unprompted.md`: the only differences are hand edits made after generation —
  the title suffix "— unprompted variant" and the blockquote summarising the 2026-09-30 runs / `hashtag:run-20260930-100850`.
- vs the committed `docs/benchmark.md`: only the hand-added "Caveat for the 2026-09-30 run" blockquote
  (the page matches the default re-render, i.e. crashed runs skipped).
- The `LegacyRegression` test pins the synthetic legacy fixture to a golden captured from the committed collector.

## Open issues / notes for W1, W4
- Row `case` for pipelines is the pipeline key, so nudged stage-1 runs are pooled into `## Overall` alongside
  natural and legacy runs (they are the same implement task plus one nudge line). If that is unwanted, filter
  `variant == "nudged"` out of `aggregate()`'s classic block — one line.
- `## Guardrails` pools cases and scenarios per variant × stage (as the contract says); per-case guardrail
  tables are available in `--json` (`pipelines.rework[*]` has no guardrails; per-run rows do) but not rendered.
- `new_changes_opened` is accepted as a number, bool or list (list → count); `stage1_changes`/`stage2_changes`
  are rendered as counts (`changes after stage 1 / 2`).
- Bad-outcome trace heuristics are a fallback only; the runner's `guardrails` block always wins when present.
  `refs_heads_push_attempted` also flags `git push [-u] <remote> master|main` / `HEAD:master` (no `refs/for/`).
- Run-record field for hashtags is `hashtags` (what `run.py --push-to` already writes); the stage-2 block is
  read the same way. If W1 stores them elsewhere, `load_run` / `load_pipeline` need a one-line change.
- Pre-existing style: the file has many lines > 79 columns; no linter config in the repo, so left as is.
