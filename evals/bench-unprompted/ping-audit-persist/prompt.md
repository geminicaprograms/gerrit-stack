---
schema_version: "1.1"
name: ping-audit-persist
description: Unprompted variant, 5-6 concern feature — plain product request, no repo rules (ping audit log with persistence, read views, owner-only clear).
tags: [bench, unprompted, deep]
runs: 3
max_turns: 60
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Every ping handled by `demo-plugin` (this Gerrit 3.14 plugin: see `README.md` and `src/main/java/com/example/demoplugin/`) should be recorded with timestamp, project and caller, and survive a Gerrit restart. Add a `pingAuditSize` setting (default 0 = off, otherwise the number of entries kept per project). Entries are kept in memory and written to a file under the plugin data directory (`@PluginData`) so they are reloaded on start. Expose the entries at `GET /projects/{name}/demo-plugin~ping-log` and with an SSH command `demo-plugin ping-log <project> [--limit N]`. Project owners may clear a project log with `DELETE /projects/{name}/demo-plugin~ping-log`. Unit tests throughout. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
