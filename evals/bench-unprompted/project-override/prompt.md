---
schema_version: "1.1"
name: project-override
description: Unprompted variant — plain product request, no repo rules in the prompt (per-project pingMessage override).
tags: [bench, unprompted]
runs: 3
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Projects should be able to override `pingMessage` for this Gerrit plugin (`demo-plugin`, see `README.md` and `src/main/java/com/example/demoplugin/`) in their own `project.config` (`[plugin "demo-plugin"] pingMessage = …`), falling back to the global plugin setting when the project does not set one. Both the REST view and the SSH command must honour the override. Add unit tests. `bash tools/quick-check.sh` compiles the plugin.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
