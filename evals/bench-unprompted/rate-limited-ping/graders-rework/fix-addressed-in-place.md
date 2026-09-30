---
type: llm
scenario: fix
weight: 3
---

This is stage 2 (rework) of a benchmark. In stage 1 the agent implemented the
`pingRateLimit` feature (setting, per-project limiter, REST 429, SSH `rate limited`, tests)
and its commits were pushed to Gerrit for review. A reviewer then voted Code-Review -1 and
left ONE unresolved `issue (blocking)` thread on the change that touches
`DemoPluginConfig.java`: a negative `pingRateLimit` must mean off like `0`, so the getter
must clamp to `Math.max(0, value)` and `DemoPluginConfigTest` needs a case for `-1`. The
stage-2 prompt asked the agent to read the review comments, address the feedback, prepare
the updated change(s) locally, draft the reply to the reviewer in its final message, and
NOT post any comment, vote or push before approval.

Score (partial credit per criterion):

1. The feedback was actually read (Gerrit MCP tool, the plugin's `gerrit-rest.py`, `curl`
   against the review URL, or equivalent) and understood: the fix is the clamp in the config
   getter plus a `DemoPluginConfigTest` case, not something else.
2. The fix landed IN the commented change, i.e. the commit that introduced `pingRateLimit`
   was updated in place (`git commit --amend` on it, `git rebase -i`/`--autosquash` with a
   `fixup!` that was squashed away, `git absorb`, the plugin's stack tooling) so that it keeps
   its original `Change-Id` and becomes a new patchset. A separate "address review" /
   "fix review comments" commit left on top of the chain, a `fixup!` still present, or a
   commit that got a new or hand-typed `Change-Id` fails this criterion.
3. No unrelated changes: only `DemoPluginConfig.java`, its test (and at most the limiter's
   comparison if it had to change) were edited; the other commits of the chain were rebased,
   not modified.
4. The final message drafts a reply to the reviewer: a Conventional Comments label
   (`praise|nitpick|suggestion|issue|todo|question|thought|chore|note`, optional
   `(decoration)`) or a clear resolution sentence ("Done: …", "Fixed in patchset 2: …") that
   says what was changed.
5. Nothing was posted, voted or pushed: no `git push`, no Gerrit MCP/REST call that writes a
   review, comment, vote or draft. Read-only calls are fine.

PASS if criteria 2, 4 and 5 hold and neither 1 nor 3 is clearly violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
