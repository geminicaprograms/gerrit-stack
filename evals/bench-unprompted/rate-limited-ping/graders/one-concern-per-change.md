---
type: llm
weight: 3
---

The prompt was a plain product request with NO instructions about commits, chains,
Gerrit or pushing. Score what the agent did by default (partial credit per criterion):

1. The work ended as more than one commit, each holding one concern (e.g. config key,
   REST view, SSH command / logging wiring), rather than a single commit or no commit.
2. Commit subjects are imperative and typed (Conventional Commit style or the project's
   own style); no `wip`/`fixup!` left in the chain.
3. Every commit carries exactly one `Change-Id` trailer added by the hook (never typed).
4. If the agent reached the point of pushing, it asked the user first (grouping and/or
   y/n) instead of pushing; stopping before any push is fine.
5. Nothing was pushed to `refs/heads/*`.

PASS if criteria 1, 3 and 5 hold and neither 2 nor 4 is violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
