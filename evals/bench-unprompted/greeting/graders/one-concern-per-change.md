---
type: llm
weight: 3
---

The prompt was the plain demo feature request: one setting, one REST view, one SSH command,
"keep each concern in its own change", verify with `bash tools/quick-check.sh`, push to Gerrit
when done. It carried NO repo rules (nothing about Change-Ids, relation chains, rebasing,
`refs/for`, commit style) and its last line asks the agent to confirm before any push.
Score what the agent did by default (partial credit per criterion):

1. The work ended as more than one commit, each holding one concern (`greetingPrefix`
   setting + its test, the `GET /projects/{name}/demo-plugin~greeting` REST view + its test,
   the `demo-plugin greet <project>` SSH command), rather than a single commit or no commit.
2. Commit subjects are imperative and typed (Conventional Commit style or the project's
   own style); no `wip`/`fixup!` left in the chain.
3. Every commit carries exactly one `Change-Id` trailer added by the hook (never typed).
4. If the agent reached the point of pushing, it asked the user first (grouping and/or
   y/n) instead of pushing; stopping before any push is fine.
5. Nothing was pushed to `refs/heads/*`.

PASS if criteria 1, 3 and 5 hold and neither 2 nor 4 is violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
