# A4 — skill `gerrit-stack` (flagship)

status: done

Resumed 2026-09-30 after the first A4 agent was cut by the session limit before its verification pass. Inherited draft: `skills/gerrit-stack/SKILL.md` (257 lines) + `references/{push-options,chain-editing,commit-message,troubleshooting}.md`. This run audited every file item by item against SPEC lines 33–43, 116, 118–145, 149, the execution-plan interface contract, and PROGRESS.md Phase 0 findings, then fixed the gaps below. No other files touched; no `git add`/`commit`/`stash` run.

## Files (owned by A4)

- `skills/gerrit-stack/SKILL.md` — 258 lines; frontmatter `name: gerrit-stack`, single-line `description` (783 chars, SPEC line 140 verbatim); sections: Purpose (links `/gerrit:gerrit-workflow` for Change-Id/patch-set semantics, no duplication), Non-negotiables (all SPEC 143 hard rules + `cd && git` row), Quick reference (script table + MCP → `gerrit-rest.py` fallback table), Phases 0–6 exactly as SPEC 141–142, Configuration, Rationalizations, Red flags, **Pre-flight checklist last** (9 checkboxes = SPEC 144, ticked before `git push`).
- `skills/gerrit-stack/references/push-options.md` (98) — `%t=`/`%hashtag=` vs `%topic=`, full option table, grouping rule of thumb, how `push-chain.sh` composes the line (unchanged this run).
- `skills/gerrit-stack/references/chain-editing.md` (190) — fixup+autosquash, `--exec` builds-alone proof, amend rules, whole-chain re-push, `--verify-ids`, client/server rebase, Change-Id recovery, chain > 5.
- `skills/gerrit-stack/references/commit-message.md` (124) — Conventional Commits default (`gerrit-stack.commit-style`), footers opt-in via `gerrit-stack.footers`, one-concern rule, checklist.
- `skills/gerrit-stack/references/troubleshooting.md` (36) — symptom → cause → fix table incl. server-side push errors and MCP-absent fallback.
- `docs/plans/status/A4-stack.md` (this file).

## Changes vs the inherited draft

| File | Change | Why |
|---|---|---|
| SKILL.md Phase 3.4 | Push question is now the literal SPEC 142 template `Push N changes to refs/for/<b> [with <grouping>] on <remote>? (y/n/wip)`, with one line explaining `<grouping>` = `hashtag <tag>` / `topic <slug>`, omitted for none | Draft had expanded the placeholder inline; SPEC wording is authoritative |
| SKILL.md | `References:` line moved above the checklist | Pre-flight checklist must be the last section |
| SKILL.md MCP table + Phase 4.5 | `add_reviewer(change_id, reviewer)` annotated with `state="CC"` for CCs | Verified against installed gerrit-mcp `70a4f8f7e72a`: `add_reviewer(change_id, reviewer, gerrit_base_url=None, state="REVIEWER")` |
| commit-message.md (3 places) | `greeting.prefix` → `greetingPrefix` | Demo plugin uses flat `PluginConfig` keys (A8 finding) |
| troubleshooting.md | Removed the `gs` macro (`gs chain-status.sh --preflight"` was not runnable: dangling quote, space in path); all four fixes now read `bash "${CLAUDE_PLUGIN_ROOT}/scripts/<name>" [flag]` | Recipes must be pasteable verbatim; a first regex expansion put `--preflight` inside the quotes, caught by a targeted grep and fixed |
| chain-editing.md §5 | Patch-set semantics now point to `/gerrit:gerrit-workflow` ("in short:" + the three bullets kept as the operational reason for whole-chain re-push) | SPEC: never duplicate `gerrit-workflow` |
| chain-editing.md §9 | Notes that the `<sha-of-commit-5>:refs/for/<branch>` line is the one push `push-chain.sh` does not print, sha from `chain-status.sh`, same `%…` grouping | Reconciles with the SKILL red flag "a push line `push-chain.sh` did not print" |

Verified as already correct in the draft (no change needed): `${CLAUDE_PLUGIN_ROOT}` written literally as `bash "${CLAUDE_PLUGIN_ROOT}/scripts/<name>"` everywhere (Phase 0 probe c); no `cd … && git` recipes (only the prohibition and its troubleshooting row); tool flags limited to the contract (`chain-status.sh --preflight|--snapshot|--verify-ids`, `push-chain.sh [--wip]`, `diff-budget.sh HEAD`, `install-commit-msg-hook.sh [--host]`); `push-chain.sh` print-only with the skill persisting `gerrit-stack.grouping`/`group-name`; MCP names/params match the installed plugin (`get_related_changes(change_id, revision_id=None)`, `get_change_details`, `query_changes`, `list_change_comments`, `post_review_comment(…, labels=None)` never passed labels, `set_topic`, `changes_submitted_together`); hashtag and rebase-chain are script-only fallbacks; `set_work_in_progress`/`set_ready_for_review`/`revert_*`/`abandon_change` never called; grouping question wording = SPEC 142; all 11 hard rules of SPEC 143 present; checklist items = SPEC 144; `gerrit-rest.py` subcommands and flags used (`related`, `detail`, `query`, `comments --unresolved`, `review`, `topic`, `hashtags --add`, `rebase-chain`, `submitted-together`) exist in `scripts/gerrit-rest.py --help`.

## Verification run (2026-09-30, repo root)

| Command | Result |
|---|---|
| `claude plugin validate ./ --strict` | `✔ Validation passed` (before and after edits) |
| `grep -rn 'Change-Id: I' skills/gerrit-stack` | no match |
| `grep -rn -E 'Change-Id[:=]' skills/gerrit-stack` | 5 hits, all explanatory prose or the §8 recovery placeholder `Change-Id=<old id copied from Gerrit>` (no literal id) |
| `grep -rn -E '^\s*cd .*&&.*git' skills/gerrit-stack` | no match (the `cd && git` string appears only as the forbidden pattern in the Never table, a rationalization row, and a troubleshooting row) |
| `grep -rn -i -E 'pull request\|\bPR\b\|merge request\|gh pr\|feature branch'` | no match (no PR-shaped fallbacks) |
| `grep -rn -E '`gs \|greeting\.prefix'` | no match after fixes |
| `grep -rn -E 'scripts/[a-z-]+\.sh [^"]*"'` (flag inside quoted path) | no match after fix |
| `ruby -ryaml` parse of frontmatter | valid; keys `name`, `description`; description 783 chars, single line (< 1024) |
| `wc -l` | SKILL.md 258 (≤ ~350); references 190 / 124 / 98 / 36 |
| fenced-block scan: every non-comment line starts with `git ` / `bash "${CLAUDE_PLUGIN_ROOT}/scripts/` / `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/` | only hits are commit-message.md message-format examples (not commands) |
| MCP signature check against `~/.claude/plugins/cache/gerrit-mcp/gerrit/70a4f8f7e72a/gerrit_mcp_server/main.py` | all tool names and parameters used by the skill exist as documented above |
| `git diff --stat -- skills/gerrit-stack` | 4 files, +25/−20 lines (surgical) |

## Open issues / notes for reviewers and other WPs

1. **Change-Id value passes through the agent in one recipe** (chain-editing §8, troubleshooting row "Gerrit opened a new change"): recovering a lost id copies the *existing* id from Gerrit via `git interpret-trailers --trailer Change-Id=<old id>` then `git commit --amend -F` (guard asks). It is never invented and it is the only way to re-attach a commit to its existing change. Kept deliberately; reviewer should confirm this is acceptable under "never hand-write a Change-Id trailer".
2. **Partial push in chain-editing §9** (`<sha>:refs/for/<b>` to land the first 5 of a too-deep chain) is the one push line not produced by `push-chain.sh`; the contract has no `--tip` flag, so it stays a manual line the guard still asks about.
3. **Tool scripts not yet present**: `scripts/chain-status.sh`, `push-chain.sh`, `diff-budget.sh`, `install-commit-msg-hook.sh` are A1/A2 deliverables; the skill is written to the contract (exit codes: `push-chain.sh` 3, `diff-budget.sh` 0/1/3, `chain-status.sh --preflight` 1 on missing hook, `--verify-ids` 1 on drift; `--preflight` output fields). Re-check those exact behaviours once they land.
4. **`commit-style=gerrit` alternative** (Configuration table, commit-message.md) is not in the interface contract (only `conventional` default is listed). Skill-only consumer, harmless; drop it if the contract stays strict.
5. **RED/GREEN not run here** (same caveat as A5): no baseline pressure scenario was run without the skill. A9's `push-requires-confirm` / `trigger-*` eval cases are the intended GREEN test; treat their `--ablation` run as the baseline comparison.
6. **Description style**: SPEC 140 text verbatim (starts "Default workflow…", not the writing-skills "Use when…" opener) per the caller's instruction that SPEC is authoritative; trigger phrases all present.
7. `set_topic(change_id, "")` to remove a topic (troubleshooting) relies on Gerrit's `PUT …/topic` with an empty string; untested against the demo server.
8. Task-observer: observation #54 appended to `~/claude-skills-hub/skill-observations/log.md` (macro prefixes in recipes are un-pasteable; verify commands by pattern, not by eye).
