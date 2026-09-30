# Retro-split: turning existing code into a chain

Use after the split plan is approved (see SKILL.md, "Retro-split"). The input is either an uncommitted worktree or one oversized commit; the output is one commit per concern whose combined tree is byte-for-byte the original. Every command starts with `git`; the commit-msg hook adds each `Change-Id` — you never write one.

Ground rules: never `--amend -m` (it replaces the message and the hook then mints a new Change-Id), never `--no-verify` (skips the hook), never `--hard` except the recovery step below.

## Step 0: snapshot the original

**Uncommitted worktree** — snapshot it with `git stash create`, which writes a commit object holding the worktree state without touching HEAD, the index or the files. No wip commit: `git add -A && git commit -m "wip…"` conflicts with gerrit-stack's rules and gets a real Change-Id plus budget feedback from the post-commit hook.

```
git status --porcelain                          # everything listed is part of the split
git add -A                                      # snapshot only: untracked files must be in the index for stash create to see them (do not use -N: stash create rejects intent-to-add entries)
git stash create                                # prints <orig>: a snapshot commit whose tree is the full worktree; nothing else changes
git reset                                       # unstage again; the worktree still holds every change
git tag retro-split/orig <orig>                 # bookmark; deleted at the end
git rev-parse HEAD                              # <base>: the split commits go on top of HEAD
```

The snapshot's parent is `HEAD`, so `git diff retro-split/orig..HEAD` in Step 4 and the `git reset --hard retro-split/orig` recovery work unchanged. Skip Step 1: HEAD is already `<base>` and the changes are already unstaged in the worktree.

(Fallback only if `git stash create` prints nothing: commit everything as `git commit -m "wip: <goal> (to be split)"` and treat it as an oversized commit below. That commit is never pushed and disappears in Step 1; its Change-Id and any post-commit budget feedback are expected noise.)

**Oversized commit** — it is already the snapshot. Bookmark it and note the base (the parent of the first commit being split, usually `HEAD~1`):

```
git log -1 --format='%H %s'                     # <orig>
git tag retro-split/orig                        # bookmark; deleted at the end
git rev-parse HEAD~1                            # <base>
```

## Step 1: move HEAD back, keep every change in the worktree

```
git reset --soft <base>                         # HEAD → base; all changes staged; worktree untouched
git restore --staged .                          # unstage all; the worktree still holds every change
```

(`git reset <base>` does both in one go. Never `git reset --hard` here.)

## Step 2: one commit per concern, in plan order

For each step of the approved plan, from the bottom of the chain up:

```
git add <path>...                               # whole files that belong to this concern
git diff --cached --stat                        # what the commit will contain
git commit -F <msg-file>                        # message per the gerrit-stack commit conventions (or -m for a one-liner; never a bare `git commit`: the editor would hang the session); hook adds the Change-Id
git log -1 --format=%B | grep -c '^Change-Id:'  # must print 1
```

**A file shared between concerns** needs hunk-level staging:

- With a terminal: `git add -p -- <path>` and answer `y`/`n` per hunk (`s` splits a hunk, `e` edits it).
- From an agent session (no interactive prompt): print the hunks with `git diff -- <path>`, copy the hunks that belong to this concern into a patch file (keep the `diff --git` / `---` / `+++` header; `@@` counts may be stale), then stage exactly those hunks:

  ```
  git apply --cached --recount <patch-file>
  git diff --cached -- <path>                   # confirm only this concern's hunks are staged
  ```

  The remaining hunks stay in the worktree for the later concern.

Commit messages: subject `<type>: <what>` in the imperative, one Conventional Commit type, body explains why; footers the project requires (for example `Release-Notes`) — but never `Change-Id`. If a `-m` one-liner is not enough, write the message with `git commit -F <file>` where the file contains no trailer at all; the hook appends the trailer.

## Step 3: prove each commit builds alone

The worktree during Step 2 always contained later concerns, so builds there proved nothing. Run the verify command on every commit in isolation:

```
git -c sequence.editor=true rebase -i --exec '<verify cmd>' <base>
```

It stops at the first failing commit with that commit checked out. Fix the code, then:

```
git add <path>...
git commit --amend --no-edit                    # keeps message and Change-Id
git rebase --continue
```

If the fix belongs to a *different* commit than the one that failed, move it there with `git commit --fixup=<sha>` after the rebase finishes, followed by `git -c sequence.editor=true rebase -i --autosquash <base>`.

## Step 4: prove equivalence

```
git status --porcelain                          # must print nothing: no stray hunks, no untracked files
git diff retro-split/orig..HEAD --stat          # must print nothing: same tree as the original
git log --oneline <base>..HEAD                  # the new chain, oldest at the bottom
```

An empty `git diff` output is the proof; paste it (or state "empty") in the hand-off. Then check sizes and clean up:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"   # sha | Change-Id | +/- | files | subject per commit
git tag -d retro-split/orig
```

When the diff is **not** empty:

- Lines missing from HEAD are still in the worktree (`git status` shows them): `git add <path>...`, `git commit --fixup=<sha of the commit they belong to>`, then `git -c sequence.editor=true rebase -i --autosquash <base>`.
- Content differs (a hand-edited hunk went wrong): `git reset --hard retro-split/orig` restores the original exactly — safe only because the tag holds every change — and Step 1 starts again.

## A commit that was already pushed

Two facts drive the choice. `git commit --amend` keeps the Change-Id, so Gerrit sees a new patchset of the same change. A fresh `git commit` gets a new Change-Id from the hook, so Gerrit sees a new change. `git reset --soft` + fresh commits therefore produces N new changes unless one commit reuses the original message.

**Option A — new chain (default).** Split exactly as above. All commits are new changes. In the body of the commit that carries the original's user-visible behaviour, write `Supersedes change <number or URL>`. After the push, the user abandons the old change (skills never abandon). Review history stays on the old change; link it in a reply.

**Option B — keep the pushed change alive as the tip.** Use it when the original subject and body still describe the slimmer tip and reviewers should keep their comment threads. Commit concerns 1..N-1 fresh, then for the final concern reuse the original message verbatim:

```
git commit -C retro-split/orig                  # git copies the message including its Change-Id trailer
git log -1 --format=%B | grep -c '^Change-Id:'  # must print 1
```

The trailer travels inside git; the hook keeps an existing Change-Id and adds nothing. Gerrit records a new patchset with the smaller diff and the new parents. Explain what moved into the parents in a review reply (via the `gerrit-review` skill), not by rewriting the message; if the message must change, use Option A.

Either way the whole chain is re-pushed with `HEAD:refs/for/<branch>`; the `gerrit-stack` skill composes the command and asks before pushing. For stacked PRs the same split applies; your stacking tool re-creates the branches and PRs from the commits.
