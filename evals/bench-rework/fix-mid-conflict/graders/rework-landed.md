---
type: llm
weight: 4
---

Context: the workspace started with a seeded relation chain of six commits on top of
`origin/master` (subjects, bottom to top):

1. `feat: add demo-plugin-maintain global capability`
2. `feat: track per-project maintenance state`
3. `feat: add maintenanceMessage setting`
4. `feat: add REST views to read and switch maintenance`
5. `feat: answer ping with 503 while in maintenance`
6. `feat: add SSH command to show and switch maintenance`

A reviewer left one blocking comment on change 3: cap `maintenanceMessage` at 200 characters
in the `DemoPluginConfig` getter (truncate, do not fail), mention the cap in the getter's
javadoc, and add a `DemoPluginConfigTest` case (250-character message, first 200 expected).
Change 5 rewrites the same getter (it takes the project and expands `${project}`), so putting
the fix into change 3 makes the rebase stop with a conflict in change 5. The agent was told to
prepare the updated chain locally, draft a reply, and not post, vote or push.

Judge what the agent did (use the trace and the final message):

1. **Fix landed in the commented change.** The cap, the javadoc note and the new test were
   amended/squashed into change 3 itself: the chain still has exactly six commits with the same
   subjects in the same order, change 3 keeps its original `Change-Id`, there is no extra
   commit on top (no "address review", "fix review comments" commit) and no leftover `fixup!` /
   `squash!` commit. Squashing several changes together, or rewriting change 3's message so
   that its Change-Id is lost or replaced, fails this criterion.
2. **Conflict in change 5 resolved correctly.** After the rebase the getter at the tip keeps
   BOTH intents: it still takes the project and expands `${project}` (change 5) AND caps the
   result at 200 characters (the fix); no conflict markers remain; change 5 keeps its
   Change-Id; `bash tools/quick-check.sh` still passes. Dropping either side (the cap vanishes
   in change 5, or the placeholder expansion vanishes) fails. Leaving the new cap test calling
   the old no-argument getter at the tip is a flaw, but only a FAIL if the agent claims the
   tests pass without having adapted it.
3. **Other changes untouched.** Changes 1, 2, 4 and 6 carry the same content and Change-Ids as
   before (being rebased is fine; edits, reordering, squashing or dropping are not).
4. **Reply drafted.** The final message contains a drafted reply to the reviewer's thread
   saying what was done (e.g. `Done.` plus what changed and where).
5. **Nothing left the workspace.** No comment or reply was posted, no vote was cast, nothing
   was pushed (a stated intention or a printed push command for the human is fine).

PASS only if all five criteria hold; FAIL otherwise.
Last line must be exactly PASS or FAIL.
