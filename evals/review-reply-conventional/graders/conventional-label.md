---
type: regex
pattern: '^[ \t>*_|`"-]*(issue|suggestion|question|nit|nitpick|praise|thought|note|todo|chore)\b'
flags: m
target: last_message
match: contains
weight: 2
---
At least one drafted reply line starts with a Conventional Comments label (list bullets, table
pipes, bold markers and quotes before the label are tolerated).
