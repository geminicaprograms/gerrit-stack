# E-docs — docs for the benchmark redesign

status: done

## Files written
- `evals/README.md` — rewritten where stale: four suites, arms A/B/C (team files in every fixture), tier-1 sandbox, isolation.json + capability counters, flow per kind, case.yaml schema per kind, results layout, hashtags (`bench-/run-/var-/rep-`), per-run metric keys (chain, conventions, rework, review, guardrails), collector sections, cost estimate, reproduce steps. Removed: `--scenarios`, `graders-rework/`, `scn-` hashtags, push-arms.sh, two-stage pipelines, greeting under bench-unprompted.
- `README.md` — Tests & evals benchmark paragraphs and file lists; docs/benchmark.md described as generated, not committed until the first sandboxed pass.
- `CHANGELOG.md` — stale rework-benchmark entry replaced by three accurate entries; A-plugin entries kept; Makefile target list updated.
- `Makefile` — `bench-unprompted`, `bench-split`, `bench-rework`, `bench-review` (shared `BENCH_ARGS`: arms, `--push-to`, `-j 3`; rework adds `--variants natural,nudged`). `check` and `bench` untouched.
- `demo/RUN.md` — checklist item for team files + commitlint, highlight after the commits (feat:/test: subjects, commitlint feedback on a bad subject), Conventional Comments tie-back sentence, highlight on labelled comments (replies are exempt by design).

## Verification
- `make -n bench-unprompted bench-split bench-rework bench-review` — four sane commands.
- `make validate` — passed.
- grep for `--scenarios|graders-rework|scn-|push-arms|benchmark-unprompted|benchmark-rework|withdrawn` over the owned docs — no hits.

## Open issues
- S1 has six cases on disk (C2), the cost table keeps the plan's three-case figure; noted in the README.
- Metric key definitions follow B-runner/D-metrics status files and run.py; not checked against live output.
- README "Makefile targets" line in the Repository list may not mention the new targets if it was worded differently.
