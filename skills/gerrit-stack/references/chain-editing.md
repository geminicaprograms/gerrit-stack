# Editing the middle of a relation chain

Every recipe starts with `git` (or `bash "${CLAUDE_PLUGIN_ROOT}/scripts/…"`), runs from
the repo root, and never wraps `git` in `cd … &&`. `<base>` is the `base` printed by
`chain-status.sh` (normally `<remote>/<branch>`).

## 1. Find the commit

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"      # sha7 | Change-Id | +/- | files | subject
```

A review thread names a change number; `get_change_details(change_id)` (or
`gerrit-rest.py detail <n>`) gives its Change-Id; the table row with that Change-Id
gives the `sha`.

## 2. Change the content of a middle commit: fixup + autosquash

```
git add <path> [<path>…]
git commit --fixup=<sha>
git -c sequence.editor=true rebase -i --autosquash <base>
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids     # must print: ok
```

- The `commit-msg` hook deliberately skips `fixup!`/`squash!`/`amend!` subjects, so the
  fixup commit has no Change-Id. That is expected; it disappears in the autosquash.
- `sequence.editor=true` accepts the generated todo list unchanged, so the rebase
  runs without an interactive editor. Recipes start with `git -c …`, never with an
  environment assignment, so the guard recognises them.
- The guard denies a push while any `fixup!`/`squash!` commit is in the chain.
- Do **not** use `--fixup=amend:<sha>` or `--fixup=reword:<sha>`: they replace the
  target's message with the `amend!` body, which the hook did not stamp, so the
  Change-Id is lost.

Several threads on several commits: create all fixups first, autosquash once.

## 3. Verify every commit builds alone

```
git -c sequence.editor=true rebase -i --exec '<verify-cmd>' <base>
```

The `--exec` runs after each commit is applied. On failure the rebase stops with
`HEAD` at the failing commit:

```
git add <paths>
git commit --amend --no-edit
git rebase --continue
```

`git rebase --abort` returns to the pre-rebase state if you get lost.

## 4. Amend rules (what the guard enforces)

| Command | Decision | Why |
|---|---|---|
| `git commit --amend --no-edit` | allowed | Message untouched, Change-Id kept |
| `git commit --amend -F <file>` | **ask** | Allowed only when the file still contains the current `Change-Id` line; the user confirms |
| `git commit --amend -m '…'` | **deny** | Replaces the entire message; the Change-Id is gone, the hook stamps a fresh one, Gerrit opens a **new** change and the old one is orphaned |
| `git commit --amend` (editor) | avoid | No interactive editor in an agent session; use `-F` |

Safe way to reword the commit at `HEAD`:

```
git log -1 --format=%B > /tmp/msg            # keep every existing trailer line
# edit subject/body in /tmp/msg; leave the line starting with Change-Id untouched
git commit --amend -F /tmp/msg
```

Reword a **middle** commit: stop the rebase at it, then use the same recipe.

```
git -c sequence.editor=true rebase -i --exec 'git log -1 --format=%s | grep -qv "^<exact subject>"' <base>
# rebase halts right after the matching commit is applied (HEAD = that commit)
git log -1 --format=%B > /tmp/msg
# edit /tmp/msg, keep the Change-Id line
git commit --amend -F /tmp/msg
git rebase --continue
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids
```

## 5. Re-push the whole chain — always

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/push-chain.sh"      # prints: git push <remote> HEAD:refs/for/<branch>…
```

Run the printed line verbatim after the user's `y`. Gerrit matches commits to
changes by Change-Id (patch-set semantics: `/gerrit:gerrit-workflow`); in short:

- commit unchanged (same sha) → no new patch set;
- commit edited → new patch set;
- every descendant of an edited commit → new patch set too (its parent moved).

Pushing only the edited commit is not possible in a consistent way: `HEAD:refs/for/…`
uploads `<base>..HEAD`, and that is the chain. If **nothing** changed, the push ends
with `! [remote rejected] … (no new changes)` — harmless, nothing to upload.

Re-push with the **same grouping**: `push-chain.sh` reads it from
`gerrit-stack.grouping`/`group-name`; do not ask the grouping question again.

## 6. Verify the Change-Id set after any rewrite

The post-rebase hook feedback is the drift signal: after every `rebase`,
`cherry-pick` or `reset` it compares the chain's Change-Id set with the snapshot
taken at the last commit and prints `lost:`/`new:` lines when they differ. If it
printed any, stop and repair (section 8). To confirm, or when the hook output is no
longer in view:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --snapshot      # before a risky rebase (optional; git-post refreshes it after each commit and after a drift-free rewrite)
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids    # ok  |  lost: I…  new: I…  (exit 1)
```

`lost:` means a commit no longer carries an id Gerrit knows; `new:` means a commit
got a fresh id (the hook stamped it because the old line vanished). Either way stop:
pushing would open new changes and orphan the old ones. Fix with section 8. On drift
the hook leaves the snapshot untouched, so `--verify-ids` keeps reporting the same
`lost:`/`new:` lines until you run `--snapshot` after repairing (or after deciding
the change is intended).

## 7. Rebase when the parent merged or the branch moved

Client-side (default; keeps you able to edit locally):

```
git fetch <remote>
git rebase <base>
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids     # confirms the hook's verdict
```

The post-rebase hook feedback is the drift signal: `lost:`/`new:` → stop and repair
(section 8); `--verify-ids` confirms and keeps reporting the drift until `--snapshot`
is run after the repair. Then the push question → `push-chain.sh` → verbatim push.
Merged commits drop out of the chain automatically (`git rebase` skips commits
already upstream).

Server-side (`POST /changes/{tip}/rebase:chain`; no MCP tool):

```
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" rebase-chain <tip>
```

Use it when the chain is finished and only needs to become submittable. Afterwards
the local commits are stale (Gerrit created new patch sets you do not have); before
editing locally again, fetch the tip's current patch set from the change page's
download ref (`refs/changes/<nn>/<n>/<ps>`) and rebuild the local chain from it, or
simply do the client-side rebase instead.

## 8. Recover a lost or missing Change-Id

**Commits that never had one** (hook was missing when they were made): install the
hook, then re-stamp every commit in the chain:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-msg-hook.sh"
git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' <base>
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"
```

`--amend --no-edit` re-runs the `commit-msg` hook, which adds an id where one is
missing and leaves existing ones alone.

**A change that already exists in Gerrit but whose local commit now has a different
id** (typically after a denied-then-forced `--amend -m`, or a rebase that rewrote the
message): the commit at `HEAD` (or the stopped-rebase commit) must get the **old** id
back, copied from Gerrit, never invented:

```
# 1. old id: get_change_details(change_id=<old number>) → change_id
#    (fallback: python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" detail <old number>)
git log -1 --format=%B > /tmp/msg
git interpret-trailers --in-place --if-exists replace --trailer Change-Id=<old id copied from Gerrit> /tmp/msg
git commit --amend -F /tmp/msg           # guard asks: confirm the file keeps the (old) Change-Id
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids
```

This is the only situation where a Change-Id value passes through your hands, and
it is a copy of what Gerrit already shows. The accidental new change (if one was
pushed) is abandoned by the user in the UI; the skills never abandon.

For a middle commit, stop the rebase at it first (section 4) and apply the same
three commands before `git rebase --continue`.

## 9. Split a chain that grew too deep (> 5)

Land the bottom first:

```
git push <remote> <sha-of-commit-5>:refs/for/<branch>     # uploads commits 1–5 only; guard asks
```

This is the one push line `push-chain.sh` does not print (it always uploads
`HEAD`); take `<sha-of-commit-5>` from the `chain-status.sh` table and add the same
`%…` grouping options the chain uses.

Keep commits 6+ local. When 1–5 have merged: `git fetch <remote>`, `git rebase <base>`
(merged commits drop out), and the remaining commits are chain 2 — push it via
`push-chain.sh` as usual. If the deep chain is not pushed yet, prefer fixing the plan:
two chains from the start, with `stack-planner`.
