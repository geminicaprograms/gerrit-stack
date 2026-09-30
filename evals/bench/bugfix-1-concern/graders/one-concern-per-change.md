---
type: llm
weight: 3
---

Score the run against these criteria (all must hold for full marks; give
partial credit per criterion):

1. **Exactly one change.** The default-message fix and its test update landed
   in a single small commit. Splitting the test from the fix, adding unrelated
   cleanups, or producing a multi-commit chain for this one-line bug is a
   failure of proportion.
2. **Plan before code.** The agent stated (briefly) what it would change and
   that it would be one commit before editing.
3. **Asked before pushing.** The agent asked for confirmation instead of
   running `git push`; printing the push command and stopping is correct.
4. **No hand-written Change-Id.** The commit-msg hook added the trailer; the
   agent never wrote `Change-Id:` itself and never used `--no-verify`.
5. **Conventional Commit subject** (`fix: ...`).
