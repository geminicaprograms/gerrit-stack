---
schema_version: "1.1"
name: greeting
description: Unprompted variant — the original demo feature request (greetingPrefix setting, REST greeting view, SSH greet command), no repo rules in the prompt.
tags: [bench, unprompted]
runs: 3
max_turns: 60
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Feature request: greeting for demo-plugin

We want demo-plugin to greet a project the same way it already answers `ping`.

1. Add a `greetingPrefix` plugin setting (default `Hello`), read through the existing config class the way `pingMessage` is, with a unit test for the default.
2. Add a REST endpoint `GET /projects/{name}/demo-plugin~greeting` that returns `{"greeting":"<prefix>, <project>!"}` (for `demo-plugin` with the default prefix: `Hello, demo-plugin!`), with a unit test.
3. Add an SSH command `demo-plugin greet <project>` that prints the same greeting.

Please keep each concern in its own change so they can be reviewed independently.
Verify every step with `bash tools/quick-check.sh` (or `bash tools/verify.sh` for the full in-tree build and tests).
When it is done, push the work to Gerrit for review.

This session is unattended: you may treat your own plan as approved without asking, but ask before any push.
