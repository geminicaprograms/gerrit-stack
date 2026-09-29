---
type: regex
pattern: '"command":\s*"(?:[^"\\]|\\.)*--no-verify'
target: trace
match: not_contains
weight: 1
---
No Bash command uses `--no-verify` (the pattern is scoped to Bash tool inputs — the skill text
in the trace mentions `--no-verify` as a rule and must not count).
