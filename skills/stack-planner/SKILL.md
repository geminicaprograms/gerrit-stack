---
name: stack-planner
description: Split a feature, refactor, or bugfix into an ordered chain of small single-concern steps, each independently buildable, reviewable, and revertable, within a diff budget (default 50–150 lines, 2–8 files per step; hard cap 200 single-layer, 500 cross-layer). Use before writing code for any multi-file change (anything likely to touch more than one file), when asked to "plan", "break this down", "split this", "stack this", "make it reviewable", "plan the chain", or when a diff exceeds the budget, a commit is oversized, or a dirty worktree must be split into reviewable commits (retro-split). VCS-agnostic (Gerrit relation chains, GitHub stacked PRs).
---

# Stack Planner

## Overview

One step = one change = one concern a reviewer can build, understand, approve and revert on its own. This skill turns a request into an ordered chain of such steps, sized against a diff budget, and **stops at the approved plan**. It writes no code and runs no `git add` / `git commit`.

Vocabulary is VCS-agnostic: a *step* in the plan becomes a *change* (one commit) in the chain. On Gerrit the chain is a relation chain pushed with `git push <remote> HEAD:refs/for/<branch>` (the `gerrit-stack` skill owns commit and push); on GitHub it is a set of stacked PRs. The planning rules are identical.

## When to use

- Before writing code for anything likely to touch more than one file (the `gerrit-stack` skill invokes this in its Plan phase).
- The user says "plan", "break this down", "split this", "stack this", "make it reviewable", "plan the chain".
- `diff-budget.sh` reports over budget (exit 1) or over the hard cap (exit 3) on `HEAD` or `--worktree` → [Retro-split](#retro-split-code-already-exists).
- A plan someone else wrote contains a step like "implement the feature".

**Do not use** for a single-file, single-concern edit that fits the budget (just make it), or for a purely mechanical change produced by a tool (formatter, codemod) that is one change by design. Never use it to skip the approval: the plan is the deliverable.

## Inputs (collect before planning; ask for what is missing)

| Input | Where it comes from |
|---|---|
| Goal + acceptance criteria | the request: one sentence plus what a user or caller can observe |
| Layers in play | repo layout: infra/build, config, data/migration, API, caller/UI, docs; where tests live |
| Verify command | `git config gerrit-stack.verify-cmd`; else the project's build/test command (Makefile target, `npm test`, `bazel test …`); ask if unknown |
| Budget | `git config gerrit-stack.budget.lines` / `.files` / `.hard-lines` (defaults 150 / 8 / 200) — `references/budget.md` |
| Existing code? | uncommitted diff or oversized commit → Retro-split |
| Target | Gerrit remote/branch, or stacked PRs |

## Process

Run scripts from inside the target repository. The literal `${CLAUDE_PLUGIN_ROOT}` is substituted when this skill is loaded from the plugin; if it ever reaches Bash unexpanded, the skill was not plugin-loaded — say so and stop.

1. **Map.** List every file you expect to create or modify and tag each with its layer. Find callers with `git grep <symbol>`; list neighbours with `git ls-files <dir>`. Output: a file → layer table (it feeds the plan's *files* column).
2. **Slice vertically by user-visible behaviour.** Each slice is one behaviour a reviewer can verify end to end (one config key read, one endpoint, one command). Bundle two layers only when they are meaningless apart (an endpoint and its only caller). Pure infrastructure (build wiring, module registration, test harness) is a legitimate horizontal slice — keep it small. Patterns: `references/split-patterns.md`.
3. **Order by dependency.** infra → data/migration → API → caller. Tests travel with the code they test. A refactor the feature needs is its own step at the bottom of the chain. Each step must build and pass with only the earlier steps present.
4. **Estimate each step.** `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --estimate <path>...` prints `lines=<n> files=<m> budget=<L>/<F> hard=<H>` and exits 0 (within), 1 (over soft), 3 (over hard). The number is a proxy: for a small edit in a large file estimate the hunk instead; for a new file estimate its size. Record `est ±lines` per step and say which figures you adjusted.
5. **Apply split / merge signals** (below) until every step is within budget or its reviewer note justifies the overage. Chain depth ≤ 5; longer → two chains, and the first lands before the second is planned in detail.
6. **Emit the plan** with `references/plan-template.md`: exact headings, the step table, the `Chain summary:` line, the approval question last.
7. **Ask and STOP.** Edit, stage or commit nothing until the user answers. If the user's request explicitly pre-approves the plan (e.g. "treat the plan as approved"), record that and continue without the question; otherwise ask and stop. "implement X" or "commit this" is not approval of a plan the user has not seen.

## Output format

One row per step, in chain order:

| Step N — `<type>`: `<subject>` | files | est ±lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|

- `<type>`: Conventional Commit type (`feat`, `fix`, `refactor`, `build`, `test`, `docs`, `chore`); one type per step; no "and" in the subject.
- *files*: every path the step touches, `(new)` marked.
- *est ±lines*: insertions + deletions, tests included, from step 4.
- *verify cmd*: the command that must pass with only this and earlier steps present.
- *depends on*: earlier step numbers, or `—`.
- *reviewer note*: what to look at, plus any budget justification.

Then `Chain summary: N changes, ~T lines total, max step ~M lines / F files, depth N ≤ 5, target <Gerrit relation chain on <remote>/<branch> | stacked PRs>` and the approval question.

## Budget defaults and signals

| | Target | Hard cap |
|---|---|---|
| Lines per step (ins + del, tests included) | 50–150 | 200 single-layer; 500 cross-layer, justified in the reviewer note |
| Files per step | 2–8 | 15 cross-layer |
| Chain depth | ≤ 5 | split into two chains; land the first |

- **Split** when a step has several "and"s, mixes feature + migration + cleanup, tests different behaviours, exceeds a cap, or could not be reverted alone.
- **Merge** when a step cannot be verified on its own (an import, a constant), is under 10 lines with no standalone meaning, or would make a reviewer ask "why is this separate?".

Evidence, configuration and script exit codes: `references/budget.md`.

## Retro-split: code already exists

When the user asks to commit an oversized diff, or `diff-budget.sh HEAD` / `--worktree` exits 3:

1. Measure: `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --worktree` (uncommitted) or `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" HEAD` (last commit).
2. Plan the split with the same process: the *files* column lists paths (or hunks) per concern, ordered so each commit builds on its own. Ask for approval.
3. After approval follow `references/retro-split.md`: `git reset --soft <base>`, stage per concern (`git add <paths>`, `git add -p`, or a curated patch via `git apply --cached`), one `git commit` per concern (the commit-msg hook supplies the Change-Id — never write it), then prove equivalence with an empty `git diff <orig>..HEAD --stat`. A commit that was already pushed is covered there too.

## Anti-patterns

| Anti-pattern | Fix |
|---|---|
| "Step N: implement the feature" | Slice by user-visible behaviour |
| Horizontal slices (all endpoints, then all UI) | Vertical slices; bundle a layer pair only when meaningless apart |
| Endpoint in one step, its only caller in another | One cross-layer step (justify if over 200 lines) |
| Final "add tests" step | Tests travel with the code in every step |
| Infrastructure step that also adds a feature | Two steps: scaffolding, then the first slice |
| Migration + feature in one step | Migration is its own step, before the feature |
| Refactor smuggled into a feature step | Refactor first, own step at the bottom |
| Chain deeper than 5 | Two chains; land the first, then plan the second |
| Estimates by gut feel | Run `diff-budget.sh --estimate`; state adjustments |
| Editing files before approval | Stop; the plan is the deliverable |
| Writing `Change-Id:` yourself "to help" | Never; the commit-msg hook owns it |

## Pre-flight checklist

Re-read before delivering the plan; fix anything unchecked.

- [ ] Every step has one concern, one Conventional Commit type, no "and" in the subject.
- [ ] Every row has files (`(new)` marked), est ±lines, verify cmd, depends on, reviewer note — no blank cells.
- [ ] Estimates come from `diff-budget.sh --estimate`; adjusted figures say so.
- [ ] Each step is within 150 lines / 8 files, or the reviewer note justifies it; nothing over 200 (single-layer) or 500 (cross-layer).
- [ ] Order is infra → data/migration → API → caller; each step builds and passes with only earlier steps present.
- [ ] Tests travel with the code; there is no "add tests" step.
- [ ] Refactors and migrations are their own steps at the bottom.
- [ ] Chain depth ≤ 5, else two chains with the first landing first.
- [ ] `Chain summary:` line present; the approval question is the last line.
- [ ] Nothing edited, staged or committed; no `Change-Id:` written anywhere.
- [ ] Wording is VCS-agnostic and the target (Gerrit relation chain / stacked PRs) is named.
