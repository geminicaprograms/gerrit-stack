# D-metrics — split-quality metrics + collector rewrite

status: done

## Files written
- `scripts/chain-metrics.sh` — new flags `--concerns <case.yaml>`, `--verify-timeout <seconds>`; `--verify-cmd` reimplemented.
- `tests/metrics.bats` — 10 new tests (19 total).
- `evals/metrics/collect.py` — rewritten around one row per run (`suite`, `kind`, `variant`); old two-stage pipeline code removed.
- `tests/test_collect.py` — rewritten (32 tests) around synthetic result dirs for S1–S4.
- `docs/plans/status/D-metrics.md` — this file.

## chain-metrics.sh
- `--concerns <case.yaml>`: parses the `concerns:` list with a small embedded python3 snippet (written to the temp dir, then run; bash 3.2 cannot parse a here-document containing parentheses inside `$(...)`). Handles both the **block form** that is on disk (`- name: x` / `paths:` + list items, or `paths: [...]`) and the contract's flow form (`- {name: x, paths: ['re', …]}`); regexes are searched with Python `re` over the changed paths. Tested against the real `evals/bench-unprompted/maintenance-mode/case.yaml`.
- Output per change: `concerns` (names, in map order), `unmapped_paths` (paths no concern claims — reported, never counted), `tests_travel` (true/false, null when the change has no `src/main/**/*.java`), `builds_alone`.
- Top level: `purity_pct` (changes with exactly one concern / changes with ≥ 1 mapped path), `completeness_pct` (concerns in exactly one change / concerns that appear), `concerns_seen`, `concerns_defined`, `concerns_file`, `tests_travel_pct` (always computed, no flag needed), `builds_alone_pct`, `verify_timeout`, `verify_timeouts`. Rates are `null` when the denominator is empty; all concern fields are `null` without `--concerns`.
- `--verify-cmd`: the old `rebase -i --exec` loop is replaced by: one temporary detached worktree, `checkout --detach` of each chain commit, `git clean -fdx`, `bash -c <cmd>` with a per-commit timeout (`--verify-timeout`, default 300 s; `timeout`/`gtimeout` when on PATH, else a built-in watchdog; `CHAIN_METRICS_NO_TIMEOUT_BIN=1` forces the watchdog for tests). A timeout counts as not building. Worktree removed afterwards; the caller's HEAD, index and checkout are untouched (asserted in bats).
- Human table: new columns `tests` and `concerns`, new summary line `tests travel … | purity … | completeness … | concerns seen n/m | unmapped paths k`.
- Deviation from the contract sentence on `tests_travel_pct`: the task text wins — the denominator is changes touching `src/main/**/*.java`; pure doc/config changes are `null` and outside the rate (not counted as passing).

## collect.py
- Row per run: `suite` (case `suite` → basename of the parent of case `dir` → aggregate `suite`/`evalDir`/`evalsDir` → from kind: `bench-rework` / `bench-review` / `bench`), `kind` (record → case → presence of `rework-metrics.json` / `review-metrics.json` → implement), `case`, `variant` (record → case → `<case>@<variant>` → `var-` hashtag → natural), `case_key`, process + chain metrics, `rework`, `review`, `conventions`, `guardrails` (flat), `isolation`, `capability`, `model`, `claude_version`, `error`, `errored`.
- Per-run files win over the run record blocks (`isolation`, `conventions`, `rework`, `reviewMetrics`); `capability` comes from the record (or a `capability` key in isolation.json, or capability.json).
- Sections: Arms, Reading the numbers, Sources, per implement suite `## Suite …` with `### Overall` / `### Targets` / `### Per case` (Overall and Targets use the natural variant; nudged implement runs show as `<case>@nudged` under Per case and in Guardrails), `## Split quality`, `## Rework (seeded chain)`, `## Reviewer`, `## Conventions` (with the commitlint-fallback note), `## Guardrails` (per variant × arm, pooled over suites; unknown `bad_outcomes` keys are shown as extra rows), `## Isolation`, `## Reproduce`. Sections without data are omitted; Isolation is always rendered.
- Errored runs = run record `error` or `isolation.ok == false`. Out of the means by default, in with `--include-errors`; always in Isolation and in each section's `Runs / errors — <arm>: <runs> / <errors>` line.
- `--json` = `sources`, `runs` + every section block (`suites`, `split_quality`, `rework`, `reviewer`, `conventions`, `guardrails`, `isolation`) and the totals. CLI unchanged; default `--out` is `docs/benchmark.md`.
- Public API change for callers: `aggregate(rows, include_errors=False)` / `build_report(rows, include_errors)` replace the module global `INCLUDE_ERRORS`.

## Verification
- `python3 -m py_compile evals/metrics/collect.py` — ok
- `python3 -m unittest tests.test_collect -v` — 32 tests, OK
- `bats tests/metrics.bats` — 19/19 ok
- `shellcheck -x scripts/chain-metrics.sh` — clean

## Open issues / notes for other packages
- **B-runner**: pass `--concerns <case.yaml>` and `--verify-cmd` to chain-metrics; `run_chain_metrics` currently uses a 180 s subprocess timeout, which a verified 6-change chain can exceed (clean checkout + build per change). A message with the collector's input expectations was sent to B-runner; nothing in the collector was verified against the rewritten runner's real output — only against the contract and synthetic dirs.
- `## Reproduce` lists `python3 evals/run.py --eval-dir evals/bench-… --arms … [--variants …] [--push-to …]`; adjust the text in `_footer()` if the runner's flags end up different. `make bench` in the Makefile still points at `--bench` (not my file).
- `blocking_marked_correct` is rendered as a mean count: review-metrics.json has no denominator for planted blocking defects.
- The bats suite takes about 35 s (the timeout test alone about 10 s) and longer on a loaded machine.
- `git clean -fdx` between changes drops build caches inside the temp worktree, so each change builds from scratch; that is the point of "builds alone" but makes verification slower than the old rebase loop.
- `docs/benchmark.md` was not regenerated (no results on disk; not my file).
