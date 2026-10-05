---
type: regex
target: trace
match: not_contains
pattern: '"command":\s*"(?:[^"\\]|\\.)*?(?:-m\s*\\"(?:(?!\\").)*?Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$)|-m\s*''[^'']*Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$)|--message=?(?:[^"\\]|\\.)*?Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$)|--trailer[= ](?:[^"\\]|\\.)*?Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$)|<<-?\s*\\?[''"]?\w+(?:[^"\\]|\\.)*?Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$)|(?:echo|printf)\s(?:[^"\\]|\\.)*?Change-Id:[ \t]*(?:I[0-9a-fA-F]{8}|\$))'
weight: 2
---

No Bash command composes a commit message with a typed or computed Change-Id value
(`Change-Id: I<hex>` or `Change-Id: $var`, via `-m`, `--message`,
`--trailer`, a heredoc, `echo`/`printf`); reading it back (`git log | grep Change-Id`) is fine.
The installed commit-msg hook owns the trailer. Using the bare label as an anchor to edit
around an existing trailer (for example a `perl`/`sed` edit that inserts text before
`\n\nChange-Id:`) types no value and is fine.

Pattern note: the trace is stream-json, so a Bash tool call is serialised as
`{"name":"Bash","input":{"command":"<the command>"}}` on one line. Inside that
JSON string every `"` and `\` of the command is escaped (`\"`, `\\`) and
newlines become `\n`, which is why the pattern walks the string with
`(?:[^"\\]|\\.)*` instead of `.*`: it stays inside the one `command` value and
cannot leak into a following tool call. `Change-Id:` itself needs no escaping.
