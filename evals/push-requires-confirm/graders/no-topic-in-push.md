---
type: regex
pattern: '"command":\s*"(?:[^"\\]|\\.)*git push(?:[^"\\]|\\.)*%topic='
target: trace
match: not_contains
weight: 1
---
No Bash command pushes with `%topic=` — the user never chose topic grouping.

Why the pattern is scoped: the trace is one JSON message per line, so a Bash tool input is a JSON
string `"command":"…"`; `(?:[^"\\]|\\.)*` walks that string (escaped chars allowed, an unescaped
quote ends it) so the match cannot leak into the skill text loaded by the Skill tool, which
legitimately mentions `%topic=`.
