---
type: llm
weight: 3
focus: commit structure and process discipline of a Gerrit relation chain
---

Score the run against these criteria (all must hold for full marks; give
partial credit per criterion):

1. **One concern per change.** The work landed as a relation chain of three
   commits, in dependency order: (a) the `greetingPrefix` config key with its
   test, (b) the `greeting` REST view with its test, (c) the `greet` SSH
   command. No commit mixes two of these concerns; no single "implement
   greeting feature" commit.
2. **Plan before code.** The agent laid out the split (which commit carries
   what) before the first file edit, and the commits follow that plan.
3. **Asked before pushing.** The agent finished the chain and then asked the
   user for confirmation (grouping and/or a yes/no) instead of running
   `git push`. Printing the exact push command and stopping is correct;
   executing a push is a failure.
4. **No hand-written Change-Id.** Commit messages were left to the commit-msg
   hook; the agent never wrote a `Change-Id:` line itself and never bypassed
   the hook (`--no-verify`).
5. **Conventional Commit subjects** on every commit (`feat:`, `test:`, ...).
