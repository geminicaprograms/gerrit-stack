---
type: llm
focus: last_message
weight: 1
---
PASS only if the final message (a) contains an ordered plan of at least 2 and at most 5 steps
that splits the three prefix sources (config file, environment variable, command-line flag) into
separate single-concern steps with tests travelling with the code, (b) gives per-step files and a
size estimate or verify command, and (c) ends by asking the user to approve the plan (or name a
step to change) rather than starting to implement. FAIL if the message implements code, merges
all three concerns into one step, or does not ask for approval.
