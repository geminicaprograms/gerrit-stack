---
name: stack-planner
description: Split a feature, refactor, or bugfix into an ordered chain of single-concern changes, each building and tested on its own. Size is a reviewer-load warning on production lines, not a quota; mechanical changes (rename, migration, formatter, codemod) stay one change. Use before writing code for any multi-file change, when asked to "plan", "break this down", "split this", "stack this", "make it reviewable", "plan the chain", or when a commit or dirty worktree mixes several concerns (retro-split). VCS-agnostic (Gerrit relation chains, GitHub stacked PRs).
---

# Stack Planner

Turn a request into an ordered chain of changes, one concern each, and **stop at
the approved plan**: no edits, no `git add`, no `git commit` before the user
says yes. A step becomes one commit; on Gerrit the chain is a relation chain
(the `gerrit-stack` skill owns commit and push), on GitHub a set of stacked PRs.

Not for a single-concern edit (just make it) or a mechanical change (one change
by design).

Run scripts from inside the target repository. The literal `${CLAUDE_PLUGIN_ROOT}`
is substituted when this skill is loaded from the plugin; if it reaches Bash
unexpanded, the skill was not plugin-loaded — say so and use the plain `git`
commands in `references/budget.md`.

## The Concern Is the Unit

A step belongs in the chain when a reviewer can approve it without reading the
next one:

- one behaviour or one mechanical transformation, stated without "and";
- builds and passes with only the earlier steps present;
- carries its tests **and its docs** (no final "add tests" or "update docs" step);
- is used: no class without a caller, no scaffolding, no "part 1/2";
- revertable alone.

A concern is a vertical slice through the layers it needs — a layer is never a
step. How fine to cut beyond that is a judgement call: follow the repository's
habits (`git log --stat`) and state the choice in the plan. Patterns:
`references/split-patterns.md`.

**Mechanical changes** (rename, migration, API move, formatter, codemod) are one
step however large, before any behaviour change, marked "mechanical" with how
they were produced. Split by module only if each part builds alone.

**Size** is a warning: above ~400 production lines (insertions + deletions;
tests, docs and lock files not counted) a step gets a one-line justification.
Never split a concern to get under it, never merge concerns to shorten the
chain. The team sets other values in `.gerrit-stack` (`budget.lines`,
`budget.hard-lines`, `budget.files`), per-clone `git config gerrit-stack.*`,
or `AGENTS.md` / `CONTRIBUTING` / `CLAUDE.md`. Counting and evidence:
`references/budget.md`.

## Process

1. **Collect inputs**; ask for what is missing: goal and acceptance criteria;
   verify command (`git config gerrit-stack.verify-cmd`, else `AGENTS.md`,
   `CONTRIBUTING`, `CLAUDE.md`, Makefile or CI); subject style
   (`gerrit-stack.commit-style`: `conventional` = `<type>: <subject>`, `gerrit`
   = plain imperative; a commitlint config or documented conventions win, else
   `git log --format=%s -20`); target (Gerrit remote/branch or stacked PRs).
2. **Map** every file to create or modify and its layer (`git grep <symbol>`,
   `git ls-files <dir>`).
3. **Find the concerns** and make each a step. Do not plan a step the target
   cannot do with the information it has (a server-side operation that needs
   local build or conflict state, for example).
4. **Order** infra → data/migration → API → caller; refactors and mechanical
   changes first. When the chain goes to a project you do not maintain, order
   by what can land first: small, self-contained changes at the bottom, the
   ones that need discussion on top.
5. **Estimate** production and test lines per step:
   `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --estimate <path>...`
   (prints `prod= test= other= files= warn= hard=`; exit 1 over the warning,
   3 over a hard cap). A port or move of an existing file is sized at that
   file's current line count, never at a fresh-write guess. Say which figures
   are guesses.
6. **Emit the plan** with `references/plan-template.md`.
7. **Ask and stop.** Continue without asking only if the request explicitly
   pre-approved the plan. "implement X" is not approval of a plan the user has
   not seen.

Findings from a later review, self-review or security pass are folded into the
step that owns the code (`git commit --fixup=<sha>` + autosquash, see the
`gerrit-stack` skill), never appended as "fix review" steps.

## Retro-split

When a commit or worktree already mixes concerns (not merely when it is large):
measure it (`bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --worktree`
or `… HEAD`; plain git: `git diff --numstat HEAD`,
`git show --numstat --format= HEAD`), plan the split as above (hunks per step
where a file is shared), ask, then follow `references/retro-split.md`.

## Pre-flight Checklist

- [ ] Each step: one concern, no "and", the repository's subject style.
- [ ] Each step builds, is tested, carries its docs and is used on its own.
- [ ] No step is a layer; no step is "part 1/2".
- [ ] No blank cells; new files marked `(new)`; guesses stated; ports sized by
  the existing file.
- [ ] Steps over the warning are justified, not split; mechanical steps are
  marked and first.
- [ ] `Chain summary:` line present; the approval question is last.
- [ ] Nothing edited, staged or committed; no `Change-Id:` written.
