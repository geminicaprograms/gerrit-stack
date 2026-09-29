# A5 — skill `stack-planner`

status: done

## Files written (owned by A5; nothing else touched)

- `skills/stack-planner/SKILL.md` (113 lines; frontmatter `name: stack-planner`, single-line `description` 652 chars from SPEC line 147 plus trigger phrases; ends with a pre-flight checklist per cross-cutting principle #1)
- `skills/stack-planner/references/budget.md` (evidence table with SmartBear / Google ICSE-SEIP 2018 / Graphite / thoughtbot / prior Gerrit baseline rows and source URLs; `git config gerrit-stack.budget.lines|files|hard-lines`; `diff-budget.sh` output line + exit codes 0/1/3)
- `skills/stack-planner/references/split-patterns.md` (vertical slice, infra-first, migration-alone, refactor-before-feature, tests-with-code, API-then-caller, feature flag; split + merge signals; worked example config → REST → SSH at ~60/80/50 lines)
- `skills/stack-planner/references/plan-template.md` (exact template with `Step N — <type>: <subject> | files | est ±lines | verify cmd | depends on | reviewer note`, `Chain summary:` line, approval question; filled example)
- `skills/stack-planner/references/retro-split.md` (snapshot → `git reset --soft <base>` → stage per concern via `git add <paths>` / `git add -p` / `git apply --cached --recount` → one `git commit` per concern → `rebase -i --exec` builds-alone proof → empty `git diff <orig>..HEAD --stat` proof; pushed-commit handling: Option A new chain, Option B `git commit -C <orig>` keeps the Change-Id)
- `docs/plans/status/A5-planner.md` (this file)

## Verification run

| Command | Result |
|---|---|
| `ruby -ryaml` parse of SKILL.md frontmatter | valid YAML; keys `name`, `description`; description 652 chars, single line |
| `wc -l` SKILL.md / references | 113 / 64 + 81 + 53 + 116 (SKILL.md ≤ 250 as required) |
| `grep -nE 'Change-Id: *I[0-9a-fA-F]{6,}'` over all five files | no match (no hand-written trailer) |
| awk scan of fenced blocks in SKILL.md, budget.md, retro-split.md | every command line starts with `git` or `bash "${CLAUDE_PLUGIN_ROOT}/scripts/…"` |
| `claude plugin validate ./ --strict` (repo root) | "Validation passed" |
| Sandbox run of `retro-split.md` in a scratch repo with the real `tests/fixtures/commit-msg` hook (dirty worktree with two concerns sharing one file + one new file) | wip snapshot commit got 1 Change-Id; `git apply --cached --recount` staged a hand-cut hunk with stale `@@` counts; each concern commit got exactly 1 Change-Id (2 distinct ids in chain); `git -c sequence.editor=true rebase -i --exec` ran the verify command on both commits; `git status --porcelain` and `git diff retro-split/orig..HEAD --stat` both empty; Option B `git commit -C retro-split/orig` kept the original Change-Id (count 1) with an empty diff; `git add -A` + `git stash create` snapshot contains the new files |
| Re-read against the caller's rule list and the writing-skills checklist | all required sections present: purpose, when/when-not, inputs, 7-stage process ending in ask-and-STOP, output table, budget defaults incl. chain depth ≤ 5, VCS-agnostic wording naming Gerrit relation chains and GitHub stacked PRs, retro-split branch, anti-patterns table, pre-flight checklist last |

Scratch repo lives under the session scratchpad (`retro-split-test/`), nothing written elsewhere. No `git add`/`commit`/`stash` run in the gerrit-stack repo.

## Open issues / notes for main and other WPs

1. **Non-interactive evals vs. the approval STOP (A9).** The skill ends with an approval question and edits nothing before the answer. Under `claude -p` nobody answers, so `trigger-stack-planner` and especially `split-over-budget` (grader expects ≥ 2 commits) need either a prompt that explicitly pre-approves ("split it as you see fit, no need to confirm" — the skill honours an explicit no-confirmation request) or a grader that accepts plan + question as success.
2. **Cross-layer cap has no script knob.** Contract exposes `budget.hard-lines` only (default 200); the 500-line cross-layer ceiling is planner judgement. `budget.md` tells the user to either raise `hard-lines` per clone or expect (and answer) the post-commit budget feedback on a justified step. A2/A1 may want the post-commit message to mention "justified in plan?" rather than only "retro-split".
3. **`--estimate` semantics (A2).** The skill treats `lines=` from `--estimate` as an upper-bound proxy (counts existing files; cannot see new files or partial edits) and tells the agent to adjust and say so. If A2 implements something else (for example printing a warning for missing paths), only `budget.md` "Reading `diff-budget.sh`" needs a one-line update.
4. **writing-skills RED/GREEN not run here.** No baseline pressure scenario was run without the skill; verification was rule re-read, static checks and the sandbox recipe run. The eval cases `trigger-stack-planner` and `split-over-budget` (A9) are the intended GREEN test; treat their first run as the baseline comparison (`--ablation`).
5. **Guard assumption in retro-split.** Step 0 uses `git commit -m "wip: …"` for the worktree snapshot; this assumes the PreToolUse guard denies only `--amend -m` / `--no-verify` / direct branch pushes (SPEC hard rules), not plain `-m`. If A1 adds a deny for `-m`, switch the recipe to the documented `git stash create` alternative.
6. **Description style.** Per the caller, the description is the SPEC text nearly verbatim (starts "Split a feature…", 652 chars) rather than the writing-skills "Use when…" opener; trigger phrases are all present.
