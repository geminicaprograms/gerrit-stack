---
name: stack-planner
description: Split a feature, refactor, or bugfix into an ordered chain of single-concern changes, each building and tested on its own; how fine to cut is the author's judgement, stated in the plan. Size is a reviewer-load warning on production lines (tests and docs not counted), not a quota; broad mechanical changes (library migration, rename, formatter, codemod) stay one change. Use before writing code for any multi-file change (anything likely to touch more than one file), when asked to "plan", "break this down", "split this", "stack this", "make it reviewable", "plan the chain", or when a commit or dirty worktree mixes several concerns and must be split into reviewable commits (retro-split). VCS-agnostic (Gerrit relation chains, GitHub stacked PRs).
---

# Stack Planner

## Overview

**The concern is the unit.** One step = one change = one concern a reviewer can state in one sentence, build, verify, approve and revert on its own. This skill turns a request into an ordered chain of such steps and **stops at the approved plan**. It writes no code and runs no `git add` / `git commit`.

Size is information, not a target: `diff-budget.sh` counts **production lines** (tests, docs and lock files excluded) and warns above ~400, the amount one reviewer reads well in one sitting. A concern over that line stays one change with a one-line justification; a concern under it is not split further.

Vocabulary is VCS-agnostic: a *step* in the plan becomes a *change* (one commit) in the chain. On Gerrit the chain is a relation chain pushed with `git push <remote> HEAD:refs/for/<branch>` (the `gerrit-stack` skill owns commit and push); on GitHub it is a set of stacked PRs. The planning rules are identical.

## When to use

- Before writing code for anything likely to touch more than one file (the `gerrit-stack` skill invokes this in its Plan phase).
- The user says "plan", "break this down", "split this", "stack this", "make it reviewable", "plan the chain".
- A commit or dirty worktree mixes several concerns (feature + refactor, two behaviours) → [Retro-split](#retro-split-code-already-exists). A size warning alone is not a reason to split.
- A plan someone else wrote contains a step like "implement the feature" — or the opposite, steps like "part 1/2".

**Do not use** for a single-concern edit (just make it, whatever its size), or for a broad mechanical change (see [Broad mechanical changes](#broad-mechanical-changes)) — that is one change by design. Never use it to skip the approval: the plan is the deliverable.

## Inputs (collect before planning; ask for what is missing)

| Input | Where it comes from |
|---|---|
| Goal + acceptance criteria | the request: one sentence plus what a user or caller can observe |
| Layers in play | repo layout: infra/build, config, data/migration, API, caller/UI, docs; where tests live |
| Verify command | `git config gerrit-stack.verify-cmd`; else the project's build/test command (Makefile target, `npm test`, `bazel test …`); ask if unknown |
| Size warning | `git config gerrit-stack.budget.lines` (production-line warning, default 400); `.hard-lines` / `.files` only if the team set them (default none) — `references/budget.md` |
| Existing code? | uncommitted diff or a commit mixing concerns → Retro-split |
| Target | Gerrit remote/branch, or stacked PRs |

## Process

Run scripts from inside the target repository. The literal `${CLAUDE_PLUGIN_ROOT}` is substituted when this skill is loaded from the plugin; if it ever reaches Bash unexpanded, the skill was not plugin-loaded — say so and stop.

1. **Map.** List every file you expect to create or modify and tag each with its layer. Find callers with `git grep <symbol>`; list neighbours with `git ls-files <dir>`. Output: a file → layer table (it feeds the plan's *files* column).
2. **Find the concerns.** Each concern is one behaviour or one mechanical transformation a reviewer can state in one sentence ("prefix is read from config with a default"; "all callers move to the v5 client"). How fine to cut is a judgement call, not a rule of this skill: follow the codebase's and the team's habits (look at recent history with `git log --stat`), and state your reasoning in the plan so the user can push back. Patterns to choose from: `references/split-patterns.md`.
3. **Make every step stand on its own.** It builds and carries its tests. Avoid both extremes: a step that mixes unrelated work, and a step that means nothing alone (an import, a constant, "part 1"). Everything in between is the author's call.
4. **Order by dependency.** infra → data/migration → API → caller. Tests travel with the code they test. A mechanical change or refactor the feature needs comes first, behaviour on top. Each step must build and pass with only the earlier steps present.
5. **Estimate each step.** `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --estimate <path>...` prints `prod=<n> test=<t> other=<o> files=<m> warn=<L> hard=<H|none> files-warn=<F|none>` and exits 0 (within), 1 (over the warning), 3 (over a hard cap the team set). The figures are a proxy: for a small edit in a large file estimate the hunk; for a new file estimate its size. Record production and test lines per step and say which figures you adjusted.
6. **Check against the warning, not toward it.** A step over the warning gets a one-line justification in its reviewer note (it becomes the commit message line). Split it only if it actually holds two concerns. Chain length follows the concerns: never merge two concerns to shorten the chain; land the bottom changes as they are approved.
7. **Emit the plan** with `references/plan-template.md`: exact headings, the step table, the `Chain summary:` line, the approval question last.
8. **Ask and STOP.** Edit, stage or commit nothing until the user answers. If the user's request explicitly pre-approves the plan (e.g. "treat the plan as approved"), record that and continue without the question; otherwise ask and stop. "implement X" or "commit this" is not approval of a plan the user has not seen.

## Output format

One row per step, in chain order:

| Step N — `<type>`: `<subject>` | files | est prod / test lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|

- `<type>`: Conventional Commit type (`feat`, `fix`, `refactor`, `build`, `test`, `docs`, `chore`); one type per step; no "and" in the subject.
- *files*: every path the step touches, `(new)` marked.
- *est prod / test lines*: insertions + deletions in production files / in test files, from step 5 (docs and lock files are not listed).
- *verify cmd*: the command that must pass with only this and earlier steps present.
- *depends on*: earlier step numbers, or `—`.
- *reviewer note*: what to look at; for a step over the warning, the one-line justification; for a mechanical step, the word "mechanical" and how it was produced.

Then `Chain summary: N changes, ~P production / ~T test lines total, largest change ~M production lines, depth N, target <Gerrit relation chain on <remote>/<branch> | stacked PRs>` and the approval question.

## Concern is the unit

A change belongs in the chain when a reviewer can say "yes" or "no" to it without reading the next one:

- **one concern**: statable in one sentence without "and";
- **builds and passes alone** with only earlier steps present;
- **tested**: the tests for what it adds travel with it;
- **tested or used**: the new code has its own tests, a caller or an entry point;
- **revertable** without breaking its neighbours.

### Granularity is a judgement call

This skill does not prescribe how fine to cut. Splitting by component (a helper class with its tests, then each surface that uses it) and by user-visible behaviour (each behaviour with the code it needs) are both fine; so is keeping a config setting in its own step or folding it into its first user. Pick what fits the codebase and the team, say why in the plan, and let the user change it. Only the two extremes are wrong: a step mixing unrelated work, and a step that means nothing on its own.

## Size: a reviewer-load warning, not a quota

| | Value | Meaning |
|---|---|---|
| Production-line warning | ~400 (`budget.lines`) | insertions + deletions in production files; tests, docs (`*.md`, `*.rst`, `*.txt`, `docs/`, `Documentation/`) and lock files are reported but never counted |
| Hard cap | none (`budget.hard-lines` only if the team sets one) | |
| File warning | none (`budget.files` only if the team sets one) | |
| Chain length | follows the concerns | no cap: one concern per change even for a long chain; land the bottom changes early; two chains only when the work has two natural parts |

Evidence: SmartBear/Cisco found one reviewer reads 200–400 lines per sitting well and detection drops beyond that — that is where 400 comes from. Google (median 24 lines), Graphite (~50) and the Gerrit baseline (62) are **observed medians over all changes**, not targets; most changes are small because most concerns are small. Details: `references/budget.md`.

Over the warning: keep the change if it is one concern, and put a one-line justification in the reviewer note and later in the commit message ("one parser; splitting it leaves half a grammar"). Never split to get under the number.

## Broad mechanical changes

A library migration, rename, API move, formatter run or codemod is **one concern by design**:

- **One change**, even when it is large or touches many files. Reviewers check the recipe and spot-check the result; splitting it multiplies that work and leaves the tree half-migrated.
- **Mechanical first, behaviour on top.** Never mix a behaviour change into the mechanical change; the behaviour change is its own step after it.
- **Split by module only** when each part builds and is meaningful alone (each module migrates completely and the build stays green between parts). Otherwise keep it whole.
- **Mark it**: a `refactor`, `build` or `chore` type, and the word "mechanical" in the body with how it was produced (tool + command, or the rename rule). `diff-budget.sh` still reports the numbers, but the post-commit size note stays silent: the marker is the justification.

## Retro-split: code already exists

When a commit or the worktree mixes several concerns (not merely because it is large):

1. Measure: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --worktree` (uncommitted) or `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" HEAD` (last commit), and list the concerns you find.
2. Plan the split with the same process: the *files* column lists paths (or hunks) per concern, ordered so each commit builds on its own. One concern found → no split; keep it and justify the size. Ask for approval.
3. After approval follow `references/retro-split.md`: `git reset --soft <base>`, stage per concern (`git add <paths>`, `git add -p`, or a curated patch via `git apply --cached`), one `git commit` per concern (the commit-msg hook supplies the Change-Id — never write it), then prove equivalence with an empty `git diff <orig>..HEAD --stat`. A commit that was already pushed is covered there too.

## Anti-patterns

| Anti-pattern | Fix |
|---|---|
| "Step N: implement the feature" | Slice by concern / user-visible behaviour |
| "Part 1 / part 2", a step that means nothing alone (an import, a constant) | Merge it into its neighbour |
| Splitting one concern to get under ~400 lines | Keep it one change; justify the size in one line |
| Splitting a migration/rename/formatter run into arbitrary batches | One mechanical change; by module only when each part builds and means something alone |
| Final "add tests" step | Tests travel with the code in every step |
| Behaviour change inside a mechanical or refactor step | Mechanical/refactor first, behaviour on top |
| Concerns merged to keep the chain short | One concern per change; length is not a defect |
| Estimates by gut feel | Run `diff-budget.sh --estimate`; state adjustments |
| Editing files before approval | Stop; the plan is the deliverable |
| Writing `Change-Id:` yourself "to help" | Never; the commit-msg hook owns it |

## Pre-flight checklist

Re-read before delivering the plan; fix anything unchecked.

- [ ] Every step has one concern, one Conventional Commit type, no "and" in the subject.
- [ ] Every step builds, is tested and is used on its own: no class without a caller, no scaffolding only the next step explains, no "part 1/2".
- [ ] Every row has files (`(new)` marked), est prod / test lines, verify cmd, depends on, reviewer note — no blank cells.
- [ ] Estimates come from `diff-budget.sh --estimate`; adjusted figures say so.
- [ ] A step over the production-line warning (default 400) carries a one-line justification; no step was split only to get under it.
- [ ] Broad mechanical changes are one step (or one per module that builds alone), typed `refactor`/`build`/`chore`, "mechanical" in the reviewer note, before any behaviour change.
- [ ] Order is infra → data/migration → API → caller; each step builds and passes with only earlier steps present.
- [ ] Tests travel with the code; there is no "add tests" step.
- [ ] No step holds two concerns to shorten the chain; chain length follows the concerns.
- [ ] `Chain summary:` line present; the approval question is the last line.
- [ ] Nothing edited, staged or committed; no `Change-Id:` written anywhere.
- [ ] Wording is VCS-agnostic and the target (Gerrit relation chain / stacked PRs) is named.
