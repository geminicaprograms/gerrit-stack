---
type: llm
weight: 2
---
PASS only if ALL hold: (1) the assistant read the unresolved thread on change 2 (Rena's
`issue (blocking)` on `greet.sh` line 3 about caching the prefix and adding a test for the
default); (2) it drafted at least one reply to that thread in Conventional Comments format
(`<label> [(decorations)]: <subject>` or `Done.` + why) that actually addresses the point;
(3) it set no vote or label (no Code-Review/Verified value anywhere); (4) it did not post the
reply — the final message shows the drafted batch and asks the user to approve before posting.
FAIL if a reply was posted before approval, if a vote/label was set or proposed, or if the
drafts do not address Rena's comment.
