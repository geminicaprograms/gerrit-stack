# S1-plugin-size — size policy change (2026-10-05) in the plugin

status: done

## Files written

| File | Change |
|---|---|
| `scripts/diff-budget.sh` | Counts insertions + deletions per bucket: **prod** (everything else), **test** (`test/` / `tests/` component incl. `src/test/`; `*Test.*`, `*_test.*`, `test_*.*`, `*.spec.*`), **other** (`*.md`, `*.rst`, `*.txt`, `Documentation/`, `docs/`; `*.lock`, `package-lock.json`, `go.sum`). Renames are classified by their new path (`a => b`, `d/{a => b}/f`); binary = 0 lines. New stdout line `prod=<n> test=<t> other=<o> files=<m> warn=<L> hard=<H\|none> files-warn=<F\|none>` (files = all files touched). Defaults: `budget.lines` 400, `budget.hard-lines` / `budget.files` unset = none (non-numeric → ignored with a note). Exit 0 / 1 (prod > L, or files > F when set) / 3 (prod > H, only when set) / 2 usage. Modes unchanged: `<rev>`, `--cached`/`--staged`, `--worktree`, `--estimate`. `--json`: `{mode,target,prod,test,other,total,files,budget:{lines,hard_lines,files},status}`, unset = `null`, status `ok\|over-warn\|over-hard` (was `over-soft`). Stderr wording asks for a one-line justification and says "Do not split mechanically". |
| `scripts/git-post.sh` (budget part) | Feedback on exit 1 or 3 only. Exit 1: asks for a one-line justification in the commit message (amend recipe keeping the Change-Id), says broad mechanical changes stay one change (refactor/build/chore + "mechanical"), "Do not split a concern into parts to get under the number". Exit 3: cut only along concern boundaries, never fragments/"part 1/2", or justify and raise the cap. New `budget_exempt`: silent for subject type `refactor\|build\|chore` (optional scope/`!`) whose body contains "mechanical" (case-insensitive), and for a message-only `--amend` (`HEAD@{1}^{tree}` == `HEAD^{tree}`), so that adding the justification doesn't trigger the note again. |
| `skills/stack-planner/SKILL.md` | Rewritten around "concern is the unit": description without numbers; vertical slices that build, are tested, are used; anti-fragmentation rule; size = reviewer-load warning (~400 production lines, SmartBear evidence, medians are observations); "Broad mechanical changes" section; retro-split triggered by mixed concerns (not by size); `est prod / test lines` column; checklist matches. |
| `skills/stack-planner/references/budget.md` | New defaults/bucket tables, corrected evidence table (SmartBear = the load limit; Google/Graphite/Gerrit = observed medians, not targets; thoughtbot moved to further reading), config incl. team file, new output and exit-code table, post-commit behaviour. |
| `skills/stack-planner/references/split-patterns.md` | New "Broad mechanical change" pattern, merge signals = anti-fragmentation, size isn't a split signal; worked example now 2 steps (config travels with its first consumer, REST). |
| `skills/stack-planner/references/plan-template.md` | Size-warning header, `est prod / test lines` column, new rules, example matches split-patterns. |
| `skills/stack-planner/references/retro-split.md` | "oversized commit" → "commit that mixes concerns"; "budget feedback" → "size feedback". |
| `skills/gerrit-stack/SKILL.md` | Quick-reference row (also fixes a broken table row), Phase-2 step 6, config table rows, pre-push checklist line. |
| `skills/gerrit-stack/references/commit-message.md` | Size justification line, mechanical marker, one-concern list (used on its own; within the production-line warning); the example "config, then endpoint, then command" replaced because it contradicted anti-fragmentation. |
| `skills/gerrit-stack/references/troubleshooting.md` | Rows for exit 1 / size note and exit 3 (team cap). |
| `README.md` | stack-planner bullet, PostToolUse row, `diff-budget.sh` row, config rows `budget.lines` 400 / `budget.hard-lines` unset / `budget.files` unset. |
| `CHANGELOG.md` | `[Unreleased] → Changed`: size policy entry. |
| `tests/tools.bats` | diff-budget section replaced (10 tests): bucket counting incl. lock files; warn only above 400 prod with 900 test lines not counted; no hard cap unless set (+ non-numeric); no file warning unless set + custom warn; team file + clone override; rename classified by new path; `--worktree`; `--cached`/`--staged` (old trailing `--cached` test folded in); `--estimate` incl. directory; plain repo + `--json`. |
| `tests/hooks.bats` | Stub test replaced/added (5 tests): 450-line prod commit → justification wording, no "retro-split"/"smaller changes"; 300 prod + 900 test → silent; refactor + "mechanical" body → silent (no marker / feat with "mechanical" → feedback); message-only `--amend` → silent; stubbed exit 3 → concern-boundary wording. |

## Verification

- `caffeinate -i bats tests/tools.bats` → 36/36 ok (35 s)
- `caffeinate -i bats tests/hooks.bats` → 44/44 ok (37 s)
- Budget tests (`-f 'diff-budget|post: '`, 24 tests) again with `/bin/bash` 3.2.57 first on PATH → 24/24 ok
- `shellcheck -x scripts/diff-budget.sh scripts/git-post.sh` → clean
- `claude plugin validate ./ --strict` → ✔ Validation passed

## Callers/references of the old format or numbers outside my files (not edited)

Nothing outside my files parses `diff-budget.sh` stdout or `--json`. These still carry the old numbers or wording:

- `scripts/chain-metrics.sh:39,169-170` — own budget defaults 150 / 8 from `gerrit-stack.budget.lines|files` (D-metrics / not mine).
- `tests/metrics.bats:71-77` — `budget.lines 5` for chain-metrics (not mine).
- `tests/lib.bats:352-373` — uses `150` only as a `gs_config` default/value in examples; no behaviour depends on it (no change needed).
- `evals/fixtures/scaffold-common.sh:203,207` — comments "under the 150-line soft budget".
- `evals/split-over-budget/graders/split-by-concern.md:5` — "each under the 150-line budget". `evals/README.md:14` names the case `split-over-budget`; the case (600 lines, 3 concerns → ≥ 2 commits) still matches the new policy because it splits by concern, but the name and wording are about size.
- `evals/metrics/collect.py:85,108,170,1283` — `within_budget_pct` / "budget compliance ≥ 90 %" target (the plan drops this from scoring; S2/D's job).
- `evals/bench-*/graders/one-concern-per-change.md` (S2, in progress): criterion 1 lists the config setting + test as its own concern; the skill now puts a config read with its first consumer (no class without a caller). Sent to S2-bench-size; S2 updated criterion 1 in all 9 rubrics (setting may travel with its first consumer). Remaining gap (S2 flagged it for main): the `concerns:` maps in case.yaml list `setting` as its own concern, so `purity_pct` counts a setting+REST commit as 2 concerns.
- `demo/RUN.md:47` — narration "each ≤ ~150 lines".
- Historical docs (`docs/plans/status/A1/A2/A5*.md`, `docs/plans/PROGRESS.md`) and `evals/results/**` show the old format; left as-is (records).

## Open issues / decisions to review

- `files=` counts every file touched (all buckets), so `budget.files` applies to the whole change, not production files only.
- `*.txt` counts as docs by policy, so `CMakeLists.txt` / `requirements.txt` aren't counted as production lines.
- The "mechanical" exemption is a case-insensitive substring match in the body ("not mechanical" in a refactor body would also exempt it); it applies only with a refactor/build/chore subject type, so Gerrit-style (untyped) subjects never get it.
- The message-only `--amend` silence is my addition (it stops the note repeating after the justification is added). The hooks test covers it.
