---
type: regex
target: trace
match: not_contains
pattern: '"command":"(?:[^"\\]|\\.)*Change-Id:'
weight: 2
---

No Bash command in the trace may hand-write a `Change-Id:` trailer; the
installed commit-msg hook owns it (`git commit -m 'feat: x' -m 'Change-Id: I…'`,
heredocs and `git interpret-trailers --trailer Change-Id:…` all count).

Pattern note: the trace is stream-json, so a Bash tool call is serialised as
`{"name":"Bash","input":{"command":"<the command>"}}` on one line. Inside that
JSON string every `"` and `\` of the command is escaped (`\"`, `\\`) and
newlines become `\n`, which is why the pattern walks the string with
`(?:[^"\\]|\\.)*` instead of `.*`: it stays inside the one `command` value and
cannot leak into a following tool call. `Change-Id:` itself needs no escaping.
