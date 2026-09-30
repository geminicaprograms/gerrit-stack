---
schema_version: "1.1"
name: rate-limited-ping
description: Unprompted variant — plain product request, no repo rules in the prompt (rate limit on ping).
tags: [bench, unprompted]
runs: 3
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Operators want `ping` in this Gerrit plugin (`demo-plugin`, see `README.md` and `src/main/java/com/example/demoplugin/`) to refuse more than N calls per minute per project. Make N configurable as a plugin setting `pingRateLimit` (0 = off, the default). When the limit is exceeded, the REST view `GET /projects/{name}/demo-plugin~ping` should answer HTTP 429 and the SSH command `demo-plugin ping <project>` should print `rate limited`. Add unit tests for the setting and the limiter. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
