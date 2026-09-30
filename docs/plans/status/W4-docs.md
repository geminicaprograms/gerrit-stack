# W4-docs — status

status: done

## Files written (all in my ownership list; nothing outside it touched)

- `evals/README.md` — new "Rework + guardrail pipelines" section: what a
  pipeline is (stage 1 → reviewer step as rena → stage 2 → second push →
  read-back), `case.yaml` `rework:`/`nudges:` schema, `graders-rework/`
  (`scenario:` frontmatter), the CLI (`--scenarios`, `--variants`, `-j`,
  `--push-to`, `--rena-token`), the results layout
  (`runs/<case>@<scenario>-<variant>/<arm>/<n>/` + `stage2/` + `review.json`
  + `rework-metrics.json` + `gerrit-after.json`), the four hashtags and
  `hashtag:run-<id>` in the demo Gerrit UI, a one-line definition for every
  `rework-metrics.json` field and every guardrail counter, the verbatim
  nudge lines, the fairness caveats, the cost-estimate table (full matrix,
  reduced matrix, Batch 1), and a reproduce recipe.
- `README.md` — one pointer paragraph after the existing benchmark
  paragraph, linking to `evals/README.md` § "Rework + guardrail pipelines"
  and the plan.
- `CHANGELOG.md` — `[Unreleased]` → `Added`: one entry describing the
  in-progress rework + guardrail benchmark and pointing at the docs.
- `docs/plans/PROGRESS.md` — new "## Rework benchmark" section (own table,
  existing "Phase status" table rows untouched): W1–W4 rows `in-progress` /
  2026-09-30, `Batch 1 = smoke on rate-limited-ping, 12 pipelines` row
  `todo`, the plan's "Decisions (user, 2026-09-30)" list verbatim. "Resume
  here" got one added paragraph pointing at this phase; original text kept.
- `docs/plans/status/W4-docs.md` — this file.

## What I could not verify

W1 (runner), W2 (cases), W3 (collector) build the actual code in parallel;
none of their output existed to inspect at write time (checked: `rate-limited-ping/case.yaml`
still has no `rework:`/`nudges:` block, no `graders-rework/` dir yet). Per the
brief, `evals/README.md` documents the binding "Implementation contract" in
`.claude/plans/2026-09-30-rework-benchmark-plan.md`, not code — the whole new
section is contract, not observed behavior; I added one disclaimer at the
section's top plus "(per plan)" on each subsection header that describes
CLI/schema/layout rather than something I ran myself.

## Verification

- `grep -n '^|' evals/README.md` / `README.md` / `docs/plans/PROGRESS.md` —
  tables render (pipe counts consistent per table; a naive column-count
  script flagged 4 lines in `README.md`, all pre-existing content I did not
  touch, false positives from escaped `\|` inside cell text).
- `make validate` → `✔ Validation passed` (docs don't affect it; ran once
  per instructions).
- Did not run `make check` (bats/python untouched by this WP and other
  agents may be mid-edit on those files); `make validate` was the requested
  check.

## Open issues

- None blocking. Once W1/W2/W3 land, worth a follow-up pass to swap the
  "(per plan)" markers for confirmed behavior and fix any drift between the
  contract and what actually shipped (flag names, file names).
