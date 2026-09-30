---
type: regex
pattern: '\b(issue|suggestion|question|nit|nitpick|praise|thought|note|todo|chore)(\s*\([^)]*\))?:'
target: last_message
match: contains
weight: 2
---
At least one drafted reply carries a Conventional Comments label (`label:` or `label (decoration):`),
anywhere in the message — replies are usually shown inside a table cell, so no line anchor.
