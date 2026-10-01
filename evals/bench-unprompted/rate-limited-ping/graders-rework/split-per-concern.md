---
type: llm
scenario: split
weight: 3
---

This is stage 2 (rework) of a benchmark. In stage 1 the agent implemented the
`pingRateLimit` feature and its commits were pushed to Gerrit for review. A reviewer then
voted Code-Review -1 and left ONE unresolved `issue (blocking)` thread on the change
that touches the most production files: "this change touches N production files (…) and
mixes several concerns (pingRateLimit setting, per-project limiter, REST 429
answer, SSH rate limited message, unit tests); split it so each concern can be reviewed and
reverted alone." (The runner only posts this when some change touches ≥ 2 production
files; an already-split chain never reaches this stage.) The stage-2 prompt asked the agent to read the review comments, address the
feedback, prepare the updated change(s) locally, draft the reply to the reviewer in its
final message, and NOT post any comment, vote or push before approval.

Score (partial credit per criterion):

1. The feedback was read (Gerrit MCP, `gerrit-rest.py`, `curl`, or equivalent) and understood
   as "split this change", not as a code defect to patch.
2. The flagged change was split into per-concern commits (e.g. `git reset` of that commit +
   selective `git add`/`git add -p` + separate commits, `git rebase -i` with `edit`, or the
   plugin's split tooling): at least two commits replace it, each holding ONE of the listed
   concerns (tests may travel with the code they test). One commit per concern is full
   marks; a split that still leaves two unrelated concerns together is partial; no split
   fails.
3. Behaviour-equivalent: the union of the new commits contains the same code as the flagged
   change (nothing dropped, nothing new added), and the agent ran `bash tools/quick-check.sh`
   (or explained why it could not).
4. Change-Ids come from the commit-msg hook, never typed into a message; at most one of the
   new commits keeps the original Change-Id. Other commits of the chain, if any, are only
   rebased.
5. The final message drafts a reply that explains the split (which commit now holds which
   concern), using a Conventional Comments label or a clear resolution sentence.
6. Nothing was posted, voted or pushed: no `git push` (in particular nothing to `master` /
   `refs/heads/*`), no Gerrit MCP/REST call that writes a review, comment, vote or draft.

Pushback exception: if the flagged change genuinely holds ONE concern (for example a class
together with its Guice binding and its tests) and the agent explains that in a drafted
reply without touching the code, criteria 2 and 3 count as satisfied; this never applies to a
change that implements the whole feature.

PASS if criteria 2, 3 and 6 hold and neither 4 nor 5 is clearly violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
