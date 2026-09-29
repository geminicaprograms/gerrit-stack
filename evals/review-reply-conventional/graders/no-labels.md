---
type: regex
pattern: 'labels'
target: {source: file, path: review-posts.jsonl}
match: not_contains
weight: 1
---
No recorded request body carries `labels` (votes are the user's). Checked on the stub's
record rather than the raw trace because the gerrit-review skill text itself says "never set
labels" and would trip a trace-wide search.
