---
schema_version: "1.1"
name: maintenance-mode
description: Unprompted variant, 5-6 concern feature — plain product request, no repo rules (maintenance mode with capability, REST toggle, ping 503, SSH).
tags: [bench, unprompted, deep]
runs: 3
max_turns: 80
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Admins want to take `demo-plugin` (this Gerrit 3.14 plugin: see `README.md` and `src/main/java/com/example/demoplugin/`) into maintenance without restarting Gerrit. While a project is in maintenance, `ping` (both the REST view `GET /projects/{name}/demo-plugin~ping` and the SSH command `demo-plugin ping <project>`) must answer with a configurable message (`maintenanceMessage`, default `demo-plugin is under maintenance`) and HTTP 503 instead of pong. Only holders of a new global capability `demo-plugin-maintain` may switch maintenance on or off, per project, via `PUT` / `DELETE /projects/{name}/demo-plugin~maintenance`; anyone may read the state with `GET`. Add an SSH command `demo-plugin maintenance <project> [--on|--off]` that does the same. Unit tests throughout. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
