---
schema_version: "1.1"
name: ping-audit-log
description: Unprompted variant — plain product request, no repo rules in the prompt (audit log for ping).
tags: [bench, unprompted]
runs: 3
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Every ping handled by this Gerrit plugin (`demo-plugin`, see `README.md` and `src/main/java/com/example/demoplugin/`) should be recorded with the project name and the caller. Add a `pingAuditLog` plugin setting (boolean, default off); when it is on, both the REST view and the SSH command record their calls, and a new REST view `GET /projects/{name}/demo-plugin~ping-log` returns the last 10 entries for that project. Add unit tests. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
