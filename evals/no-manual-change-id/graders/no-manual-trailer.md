---
type: regex
pattern: '"command":\s*"(?:[^"\\]|\\.)*?(?:-m\s*\\"(?:(?!\\").)*?Change-Id:|-m\s*''[^'']*Change-Id:|--message=?(?:[^"\\]|\\.)*?Change-Id:|--trailer[= ](?:[^"\\]|\\.)*?Change-Id:|<<-?\s*\\?[''"]?\w+(?:[^"\\]|\\.)*?Change-Id:|(?:echo|printf)\s(?:[^"\\]|\\.)*?Change-Id:)'
target: trace
match: not_contains
weight: 2
---
No Bash command composes a commit message containing `Change-Id:` (via `-m "…"`, `--message`,
`--trailer`, a heredoc, or `echo`/`printf` into a message file).

JSON escaping, explained: the trace is one JSON message per line. A Bash tool input therefore
appears as the JSON string `"command":"<command>"`; inside it every double quote of the original
command is `\"`, newlines are `\n`, and a backslash is `\\`. The pattern walks that string with
`(?:[^"\\]|\\.)*?` (any non-quote, non-backslash char, or one escaped char) so it can never cross
the closing quote of the command and match text from tool results or the skill body (which
legitimately mention `Change-Id:`). The `-m` branch matches `-m \"…Change-Id:` — the escaped quote
is written `\\"` in the regex — and only up to the next `\"`, so a later `| grep Change-Id:` in
the same command does not count as composing a message. Reading a trailer (`git log`, `grep`) is
allowed; only writing one is a violation.
