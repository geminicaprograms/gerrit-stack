---
type: regex
target: trace
match: not_contains
pattern: '"command":\s*"(?:[^"\\]|\\.)*?(?:-m\s*\\"(?:(?!\\").)*?Change-Id:|-m\s*''[^'']*Change-Id:|--message=?(?:[^"\\]|\\.)*?Change-Id:|--trailer[= ](?:[^"\\]|\\.)*?Change-Id:|<<-?\s*\\?[''"]?\w+(?:[^"\\]|\\.)*?Change-Id:|(?:echo|printf)\s(?:[^"\\]|\\.)*?Change-Id:)'
weight: 2
---

No Bash command of the rework session composes a commit message containing `Change-Id:`
(via `-m`, `--message`, `--trailer`, a heredoc, `echo`/`printf`); reading it back
(`git log | grep Change-Id`) is fine. Amending or splitting a commit must keep or
obtain the trailer through the installed commit-msg hook (`git commit --amend` without
`-m` keeps the message; a fresh commit gets a new hook-generated id), never by typing it.

Pattern note: the trace is stream-json, so a Bash tool call is serialised as
`{"name":"Bash","input":{"command":"<the command>"}}` on one line. Inside that
JSON string every `"` and `\` of the command is escaped (`\"`, `\\`) and
newlines become `\n`, which is why the pattern walks the string with
`(?:[^"\\]|\\.)*` instead of `.*`: it stays inside the one `command` value and
cannot leak into a following tool call. `Change-Id:` itself needs no escaping.
