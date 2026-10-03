---
type: tool_used
tool: Bash
input_match: 'gerrit-rest\.py[\\"'']*(?:\s+--host(?:=|\s+)\S+|\s+-\S+)*\s+review(?![\w-])|/revisions/[^/\s]+/review(?![\w-])'
min: 0
max: 0
weight: 2
---

No Bash call may publish a review over REST: neither the plugin's fallback
client (`gerrit-rest.py … review …`, the subcommand that POSTs comments and
votes) nor a hand-rolled request to `…/revisions/<rev>/review` (curl or
otherwise). Reading (`gerrit-rest.py comments|detail|related`, `GET` requests)
is fine and does not match.

Pattern note: `input_match` runs over the JSON-encoded tool input. The first
alternative is the `review` subcommand right after the script name and its
global options (`--host URL`, `--json`, `--table`, `--anonymous`, `-v`), so
`gerrit-rest.py review-metrics …` or a later `| grep review` do not match.
