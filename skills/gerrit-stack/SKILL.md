---
name: gerrit-stack
description: Default workflow for any code change in a Gerrit-backed repo. Plans a relation chain of small single-concern commits, gets Change-Ids from the commit-msg hook (never hand-written), pushes the chain with `git push <remote> HEAD:refs/for/<branch>` after the user confirms (grouping by hashtag or topic only if the user asks), then iterates on review by amending middle changes and re-pushing the whole chain. Use whenever the user asks to implement a feature or fix, "push to Gerrit", "upload for review", "create a chain", "stack these", "amend change N", "re-push", "rebase the chain", "address review comments", or mentions refs/for, Change-Id, topic, hashtag, patchset, relation chain, or stacked changes. Use proactively before the first commit in any repo whose remote is Gerrit.
---

# gerrit-stack

## Purpose

In a Gerrit-backed repo a change is a **relation chain**: an ordered set of small,
single-concern commits, each with a `Change-Id` trailer that the `commit-msg` hook
adds, uploaded together by one `git push <remote> HEAD:refs/for/<branch>`. This skill
runs that workflow end to end: preflight, plan, build, pre-push review, push, iterate,
land. The plugin's PreToolUse guard denies or asks on the dangerous git commands; the
rest of the discipline is yours.

**Background (do not re-derive it here):** `/gerrit:gerrit-workflow` from the official
`gerrit@gerrit-mcp` plugin explains Change-Id, patch sets and `refs/for`. Read it when
in doubt; this skill only adds the chain discipline on top.

Scripts below live in this plugin. Run them **from inside the target repo** as
`bash "${CLAUDE_PLUGIN_ROOT}/scripts/<name>"`. If that string ever reaches Bash
unexpanded, the skill was not plugin-loaded: say so and stop.

## Non-negotiables

Violating the letter of these rules is violating their spirit. No exceptions for
"just this once", "the user is in a hurry", or "it is a one-line fix".

| Never | Why | Instead |
|---|---|---|
| Type a `Change-Id:` line yourself | The hook owns it; a typed id is unverifiable. Guard denies. | Let the hook add it; check with `chain-status.sh` |
| `git commit --no-verify` / `-n` | Skips the hook, so no Change-Id; the push is denied later anyway | Fix the hook: `install-commit-msg-hook.sh` |
| `git commit --amend -m` | Replaces the whole message, drops the Change-Id, Gerrit opens a **new** change. Guard denies. | `--amend --no-edit`, or `--amend -F <file>` that keeps the line |
| Push to `refs/heads/*` or a bare branch name | Bypasses review. Guard denies. | `HEAD:refs/for/<branch>` as printed by `push-chain.sh` |
| Push without an explicit "yes" **in this turn** | The human confirms every upload; a "yes" from earlier does not carry over | Ask the push question (Phase 3), then push |
| Add `%topic=` unless the user chose `topic` | With `submitWholeTopic` a topic submits atomically; surprises reviewers | Ask the grouping question once; `none` is the default |
| Re-push a subset after editing a middle commit | Descendants were rewritten too | Always re-push the whole chain |
| Mix two concerns in one commit | Reviewers review concerns, not diffs | `stack-planner`; retro-split if it already happened |
| Build a chain deeper than 5 | Rebases multiply, reviewers lose the thread | Two chains; land the first, then push the second |
| Create a branch unless asked | The chain lives on the tracking branch | Commit on the current branch |
| Add `%wip` unprompted | Hides the chain from reviewers | Only on a `wip` answer or `gerrit-stack.default-wip=true` |
| `cd <dir> && git …` | Claude Code's permission layer refuses it | `git -C <dir> …`, or run from the repo root |

Grouping is the user's choice, never assumed. A relation chain in one repo and
branch is already grouped by parent-child; `none` is the recommended answer.

## Quick reference

`<base>` is the `base` that `chain-status.sh` prints (normally `<remote>/<branch>`).
`<tip>` is the change number of the commit at `HEAD`.

| Need | Run |
|---|---|
| Preflight (detection, hook, MCP hint) | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --preflight` |
| Chain table (`sha7 \| Change-Id \| +/- \| files \| subject`) | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"` |
| Snapshot / verify Change-Id set | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --snapshot` / `--verify-ids` |
| Size of the last commit | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" HEAD` |
| Install the commit-msg hook | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-msg-hook.sh"` |
| Compose the push command | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/push-chain.sh" [--wip]` |
| REST fallback when MCP is unavailable | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" <cmd> …` |

MCP tools come from the `gerrit` server (`mcp__gerrit__*`). When the server is not
configured (`/gerrit:setup`) or unreachable, use the fallback column:

| Need | MCP | Fallback (`gerrit-rest.py`) |
|---|---|---|
| Chain as Gerrit sees it | `get_related_changes(change_id=<tip>)` | `related <tip>` |
| Change detail (Change-Id, status) | `get_change_details(change_id)` | `detail <n>` |
| Search | `query_changes(query)` | `query <q>` |
| Review threads | `list_change_comments(change_id)` | `comments <n> --unresolved` |
| Reply / comment | `post_review_comment(...)` | `review <n> …` |
| Reviewer | `add_reviewer(change_id, reviewer)` (`state="CC"` for a CC) | (none; ask the user to add in the UI) |
| Topic after the fact | `set_topic(change_id, topic)` | `topic <n> <slug>` |
| Hashtag after the fact | (no MCP tool) | `hashtags <n> --add <tag>` |
| Rebase chain server-side | (no MCP tool) | `rebase-chain <tip>` |
| What submits together | `changes_submitted_together(change_id=<tip>)` | `submitted-together <tip>` |

Never call `set_work_in_progress`, `set_ready_for_review`, `revert_*`,
`abandon_change`, and never pass `labels`: votes and submits are the user's.

## Phase 0 — Preflight

1. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --preflight`
   Prints remote, host, branch, project, hook status, MCP hint, chain length.
2. Hook `MISSING` (exit 1): `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-msg-hook.sh"`,
   then re-run step 1. Do not commit until it reports installed.
3. MCP: if `mcp__gerrit__*` tools are absent or a call fails, use the fallback column
   for the rest of the session and tell the user once (`/gerrit:setup` fixes it).
4. Existing chain (N > 0)? Show the table and ask whether the work continues it.

## Phase 1 — Plan

1. Invoke `/gerrit-stack:stack-planner` with the request. It returns an ordered plan:
   `Step N — <type>: <subject> | files | est ±lines | verify cmd | depends on`.
2. Show the plan and **wait for the user's OK**. No file edits before that. A
   single-file fix still gets a one-step plan (one line is enough).
3. More than 5 steps: split into two chains in the plan; only chain 1 is built now.

## Phase 2 — Build the chain (repeat per step)

1. Implement exactly the files in the step (tests travel with the code).
2. `git config --get gerrit-stack.verify-cmd` — if it prints a command, run that
   command as its own Bash call; fix failures before committing.
3. `git add <path> [<path>…]` with the step's explicit paths. Never `git add -A` or `.`.
4. Write the message per [references/commit-message.md](references/commit-message.md)
   to a temp file (no `Change-Id` line, ever), then `git commit -F <file>`
   (or `git commit -m '<subject>' -m '<body>'` for short ones).
5. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"` — the new row must show
   exactly one Change-Id. Missing → hook problem, see troubleshooting; do not continue.
6. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" HEAD`
   - exit 0: within budget
   - exit 1: over the soft budget — justify in one line or split
   - exit 3: over the hard cap — retro-split now via `/gerrit-stack:stack-planner`
     (its `retro-split.md` recipe), then re-check every resulting commit

## Phase 3 — Pre-push review

1. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"` — N ≤ 5, every row has one
   Change-Id, no `fixup!`/`squash!` rows, subjects read as a story oldest → newest.
2. Optional, recommended for chains ≥ 3: prove each commit builds alone
   `git -c sequence.editor=true rebase -i --exec '<verify-cmd>' <base>`
   It stops at the first failing commit: fix → `git add <paths>` →
   `git commit --amend --no-edit` → `git rebase --continue`.
3. **Grouping question** — ask **once per chain**, and only when
   `git config --get gerrit-stack.grouping` prints nothing. Ask exactly:

   > All N changes target `<repo>/<branch>`. Group them?
   > **none** (recommended — relation chain already links them) /
   > **hashtag `<tag>`** (informational, `hashtag:` search) /
   > **topic `<slug>`** (use if the work spans repos or branches; submits together
   > when submitWholeTopic is on)

   Persist the answer so re-pushes reuse it without asking again:
   `git config gerrit-stack.grouping <none|hashtag|topic>` and, unless none,
   `git config gerrit-stack.group-name <tag-or-slug>`.
   A user who already named a topic or hashtag in the request has answered; persist
   it and mention the atomic-submit caveat in one line. Semantics:
   [references/push-options.md](references/push-options.md).
4. **Push question** — ask exactly:

   > Push N changes to refs/for/<b> [with <grouping>] on <remote>? (y/n/wip)

   `[with <grouping>]` reads `with hashtag <tag>` or `with topic <slug>` and is
   omitted for `none`. `y` → Phase 4. `wip` → Phase 4 with `--wip`. `n` → stop and
   report the chain. The answer is valid for this turn only.

## Phase 4 — Push

1. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/push-chain.sh" [--wip]` prints exactly one
   line, e.g. `git push origin HEAD:refs/for/master` (plus `%t=…`/`%topic=…`/`,wip`
   from config and flags). Exit 3 = validation failed; read stderr, fix, back to Phase 3.
2. Run the printed line **verbatim as its own Bash call**. The guard answers `ask`
   with the chain summary — that is expected; it is the human's confirmation dialog.
   A `deny` means a rule was broken: read the reason, fix, do not retry blindly.
3. Parse the push output for `/c/<project>/+/<n>` lines → change numbers, oldest
   first. `remote rejected … (no new changes)` on a re-push means nothing changed.
4. Verify the chain server-side: `get_related_changes(change_id=<tip>)`
   (fallback `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" related <tip>`).
   All N changes must appear.
5. Only if the user named reviewers: `add_reviewer(change_id, reviewer)` for each
   change in the chain (reviewers are per change; `state="CC"` for a CC).
6. Only if grouping is `topic` and the pushed command lacked `%topic=`:
   `set_topic(change_id, topic)` per change (fallback `gerrit-rest.py topic <n> <slug>`).
7. Report a table `change | subject | url` and the grouping used.

## Phase 5 — Iterate on review

1. Invoke `/gerrit-stack:gerrit-review` to read the threads (its table:
   `change | file:line | author | unresolved | summary`). It never votes or submits.
2. Map each thread to a commit: change number → `get_change_details` gives the
   Change-Id → the `chain-status.sh` row with that Change-Id gives the `sha`.
3. For every commit that needs a code fix: edit → `git add <paths>` →
   `git commit --fixup=<sha>`. The hook skips `fixup!` commits; no Change-Id there
   is expected. Never `--fixup=amend:`/`reword:` (they rewrite the message and lose
   the Change-Id).
4. `git -c sequence.editor=true rebase -i --autosquash <base>`
5. `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids` must print
   `ok`. `lost:`/`new:` lines → stop, recover per
   [references/chain-editing.md](references/chain-editing.md); do not push.
6. Re-run the verify command on touched commits (Phase 3 step 2) when cheap.
7. Ask the push question again (grouping comes from config; no second grouping
   question) → Phase 4. Unchanged commits get no new patch set; edited ones and their
   descendants do.
8. Reply to the threads through `gerrit-review` after the user approves the batch
   (`Done.` plus what changed, `unresolved: false` only when fixed).

## Phase 6 — Land

1. Parent merged or branch moved on: `git fetch <remote>` → `git rebase <base>` →
   `chain-status.sh --verify-ids` → push question → Phase 4. Server-side alternative
   when no further local edits are planned:
   `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" rebase-chain <tip>`
   (there is no MCP tool for it; local commits are stale afterwards, see chain-editing).
2. Show what lands together: `changes_submitted_together(change_id=<tip>)`
   (fallback `submitted-together <tip>`). With grouping `none` this is the chain
   below and including that change; with `topic` and `submitWholeTopic` it is the
   whole topic.
3. Submit is the user's action in the Gerrit UI. Never vote, never submit.
4. After merge, `git fetch <remote>` + `git rebase <base>`; `chain-status.sh` shows
   `0 change(s)` when everything landed. Then build chain 2, if the plan had one.

## Configuration

`git config gerrit-stack.<key>`; full table with defaults in the plugin README.

| Key | Used here for |
|---|---|
| `remote`, `branch`, `host` | Target of `refs/for/<branch>`; REST host for the fallback |
| `grouping`, `group-name` | Persisted grouping answer (Phase 3); read by `push-chain.sh` |
| `default-wip` | Adds `%wip` without the `wip` answer |
| `budget.lines`, `budget.files`, `budget.hard-lines` | `diff-budget.sh` thresholds (150 / 8 / 200) |
| `commit-style`, `footers` | `conventional` (default) or `gerrit`; required trailers such as `Release-Notes` |
| `verify-cmd` | Per-step and per-commit verification command |
| `allow-direct-push` | Leave `false`; the guard denies `refs/heads` pushes |

## Rationalizations to refuse

| Excuse | Reality |
|---|---|
| "The user said skip the hook, just push" | Without a Change-Id the push is denied; installing the hook takes seconds |
| "It is a one-line fix, no plan needed" | A one-step plan is one line; still show it and get the OK |
| "The user named a topic, so `%topic=` is fine" | Yes — that is a choice. Persist it, state the atomic-submit caveat, move on |
| "`--amend -m` is just for a typo in the subject" | It replaces the whole message; the Change-Id goes with it |
| "Only commit B changed, push only B" | C's parent changed too; only a whole-chain push is consistent |
| "They are in a hurry, I will push and tell them after" | The confirmation is the product; the guard asks anyway |
| "I will commit everything now and split before pushing" | Retro-split costs more than planning; build one concern at a time |
| "MCP is down, skip the verification" | `gerrit-rest.py related <tip>` exists for exactly this |
| "`cd repo && git …` is the same thing" | The permission layer refuses it; use `git -C` |
| "The user said yes last turn" | Every push needs a yes in the turn it happens |

## Red flags — stop and re-read the rules

- You are about to type `Change-Id` anywhere
- `--no-verify`, `-n`, or `-m` next to `--amend` in a command you are composing
- `refs/heads` in a push, or a push line that `push-chain.sh` did not print
- `%topic=` in a command with `gerrit-stack.grouping` unset or `none`
- Editing files before the plan was approved
- `git add .` / `git add -A`
- A chain table with 6+ rows, or `fixup!` rows before a push
- `chain-status.sh --verify-ids` printed `lost:` and you are still heading to push

References: [push-options](references/push-options.md) ·
[chain-editing](references/chain-editing.md) ·
[commit-message](references/commit-message.md) ·
[troubleshooting](references/troubleshooting.md)

## Pre-flight checklist (tick every box before `git push`)

- [ ] `commit-msg` hook installed (`chain-status.sh --preflight` exit 0)
- [ ] Exactly one Change-Id per commit (`chain-status.sh` table)
- [ ] No `fixup!`/`squash!` commits left (autosquash done)
- [ ] Each commit within budget, or the excess justified in its message
- [ ] Grouping choice confirmed by the user (none / hashtag / topic) and reflected in the push options
- [ ] Refspec is `HEAD:refs/for/<branch>` as printed by `push-chain.sh`
- [ ] The user answered `y` (or `wip`) to the push question **in this turn**
- [ ] Change-Id set unchanged after any rebase (`chain-status.sh --verify-ids` → `ok`)
- [ ] After the push: chain verified via `get_related_changes` (or `gerrit-rest.py related`)
