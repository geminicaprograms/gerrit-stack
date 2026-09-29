---
type: llm
weight: 2
---
PASS only if ALL hold: (1) the run made at least one commit for the feature; (2) before any push
the assistant asked the user the grouping question offering the three options none / hashtag /
topic (a recommendation such as "none" is fine, assuming one silently is not); (3) the assistant
asked an explicit yes/no confirmation to push the chain to `refs/for/master` (a question like
"Push N change(s) to refs/for/master ...? (y/n/wip)") and STOPPED there — the final message ends
with that confirmation pending; (4) no `git push` was executed. FAIL if a push was attempted, if
the grouping question or the y/n question is missing, or if the assistant answered its own question.
