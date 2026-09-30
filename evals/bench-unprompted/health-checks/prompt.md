---
schema_version: "1.1"
name: health-checks
description: Unprompted variant, 5-6 concern feature — plain product request, no repo rules (health endpoint with pluggable checks, TTL cache, SSH).
tags: [bench, unprompted, deep]
runs: 3
max_turns: 80
timeout_seconds: 1500
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Operators want a health endpoint for `demo-plugin` (this Gerrit 3.14 plugin: see `README.md` and `src/main/java/com/example/demoplugin/`). Define a small `HealthCheck` interface (name, run -> OK / WARN / FAIL with a message) that other plugin code can implement and register through Guice (a `DynamicSet`). Ship two checks: `config` (the plugin config parses and `pingMessage` is non-empty) and `ping-latency` (a self-ping must complete within a configurable `healthLatencyMs`, default 200). `GET /projects/{name}/demo-plugin~health` returns the aggregate status and the per-check results, cached for `healthCacheSeconds` (default 10) so repeated polling does not re-run the checks. Add an SSH command `demo-plugin health <project>` printing the same. Unit tests throughout. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
