status: done

# S2 — benchmark size policy (user decision 2026-10-05)

## Files written
- `scripts/chain-metrics.sh` — per change `prod_lines` / `test_lines` / `other_lines` (path rules from the
  plan: tests = `src/test/`, `test/`, `tests/`, `*Test.*`, `*_test.*`, `test_*.*`, `*.spec.*`; other = `*.md`,
  `*.rst`, `*.txt`, `Documentation/`, `docs/`, `*.lock`, `package-lock.json`, `go.sum`; prod = the rest;
  binary = 0). Top-level `prod_lines_median`, `prod_lines_max`, `test_lines_total` (null on an empty chain).
  `within_budget` = `prod_lines <= budget.lines` (git config, else team file `.gerrit-stack`, default 400),
  information only; `budget.files` = null unless set. `lines` (gross) kept. Counted in-script (numstat -z),
  no call to diff-budget.sh. Human table: `lines prod test files …` columns, summary
  `prod lines: median M  max X  |  test lines: total T  |  prod <= 400: P% (info)`; "within budget" text removed.
- `tests/metrics.bats` — updated 3 existing tests; new: team-file budget lookup + git-config precedence,
  prod/test/other classification (every rule incl. binary, `Latest.java` not a test), top-level fields,
  within_budget on prod not gross lines, human table, empty chain nulls. 24 tests.
- `evals/metrics/collect.py` — `within_budget_pct` removed from CHAIN_METRICS (Overall/Per case + deltas),
  SPLIT_METRICS and TARGETS ("budget compliance" gone; targets = one Change-Id, violations, cost overhead).
  New info rows (Overall, Per case, Split quality): production lines median / largest change, test lines
  whole chain; always rendered, `–` for old result dirs. Reading-the-numbers bullet + Split-quality text
  say size is information only; footer target list and intro sentence ("smaller") updated.
- `tests/test_collect.py` — budget target/row removal asserted, `–` size rows for S1 fixture, S2 fixture
  carries prod/test lines; new `test_size_rows_are_information_only`. 33 tests.
- Graders: all 9 `one-concern-per-change.md` (S1 ×6, S2 ×3) gain criterion 6 "No over-fragmentation"
  (meaningful alone: builds, tested or used, one-sentence purpose; class with no caller / scaffolding /
  "part 1" counts like a mixed commit; mechanical migration/rename = one commit; size never rewarded or
  penalised); PASS rule now `1, 3, 5 and 6 hold`. Detailed rubrics also allow merging a declaration-only
  concern into its first user and say fragments do not count as single-concern commits.
  `rework-landed.md` criterion 1: a fix split off from change 3 counts as fragmentation; change-3 growth
  is not a criterion. No grader had an explicit 150/200-line or "within budget" criterion to remove.
  PASS/FAIL last-line rules unchanged.
- `evals/README.md` — S2 suite row, per-run metric keys (prod/test/other, top-level size, budget/within as
  info), Collector table (targets list, Split quality contents), new "Size policy" paragraph.

## Verification
- `shellcheck -x scripts/chain-metrics.sh` — clean; `bash -n` ok.
- `caffeinate -i bats tests/metrics.bats` — 24/24 ok (~60 s).
- `caffeinate -i python3 -m unittest tests.test_collect` — 33 tests OK.
- `python3 -m py_compile evals/metrics/collect.py tests/test_collect.py` — ok.
- `python3 evals/metrics/collect.py --results evals/results/pass1-s1-unprompted … pass1-s4-review --out /tmp/pass1-newsize.md`
  — 30 runs, 0 errors, renders; size rows show `–`; 0 occurrences of "budget"; Targets table has 3 rows
  (all PASS for bench-unprompted). `docs/benchmark.md` not touched.
- Smoke: `chain-metrics.sh --base HEAD~3 .` on this repo prints the new columns.

## Follow-up (after S1's note on stack-planner)
- All 9 `one-concern-per-change.md`, criterion 1: a config setting may travel with its first consumer
  (setting + first view in one commit) or stand alone; neither shape counts as mixing concerns. Matches
  S1's rewritten stack-planner (config read lands in the same commit as the first transport).

## Open issues
- **Purity vs. setting-with-first-consumer**: the `concerns:` maps in the case.yaml files (not mine) list
  `setting` as its own concern, so a setting + REST commit counts as 2 concerns and lowers `purity_pct`
  although the rubric accepts it. Owner of case.yaml: drop `setting` from the maps (unmapped like wiring
  files) or fold its regexes into the first consumer's concern.
- `evals/fixtures/scaffold-common.sh` (not mine) still says "under the 150-line soft budget" in comments
  (lines ~203/207); the `split-over-budget` case name/description in README line 14 is not a metric section
  and was left as is.
- docs/benchmark.md must be re-rendered by the owner after the next pass (old runs show `–` for size).
