---
arm: upstream
type: llm
weight: 2
---
PASS only if ALL hold: (1) the run made at least one commit for the feature; (2) the assistant
asked an explicit yes/no confirmation to push the chain to `refs/for/master` (a question like
"Push N change(s) to refs/for/master ...?") and STOPPED there — the final message ends with that
confirmation pending; (3) no `git push` was executed; (4) it did not add a topic or hashtag the
user never asked for. FAIL if a push was attempted, if the y/n question is missing, or if the
assistant answered its own question.

Arm `upstream` only: unlike gerrit-stack, the ported gerrit-workflow groups a chain only on
request, so it is not required to ask the grouping question (none / hashtag / topic).
