---
schema_version: "1.1"
name: feature-3-concern
description: The demo feature request (config key + REST view + SSH command); ideal outcome is a 3-change relation chain, one concern per change.
tags: [bench]
runs: 3
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

You are working in `demo-plugin`, a small Gerrit 3.14 plugin (see `README.md`
and `src/main/java/com/example/demoplugin/`). The repo is reviewed on Gerrit:
`.gitreview` points at project `demo-plugin`, `origin` pushes to
`refs/for/master`, and the commit-msg hook is installed.

Implement the **greeting feature**:

1. A new plugin config key `greetingPrefix` in `DemoPluginConfig` (flat key
   inside `[plugin "demo-plugin"]`, default `Hello`), with a unit test in
   `DemoPluginConfigTest`.
2. A REST endpoint `GET /projects/{name}/demo-plugin~greeting` that answers
   `{"plugin":"demo-plugin","project":"<name>","message":"<prefix> <name>"}`,
   registered in `Module` the same way `ping` is, with a unit test mirroring
   `PingActionTest`.
3. An SSH command `demo-plugin greet <project>` printing `<prefix> <project>`,
   registered in `SshModule` the same way `ping` is.

Rules of this repo:

- Each concern is its own change (its own commit) in a relation chain; keep
  every change small and buildable on its own (`bash tools/quick-check.sh`).
- Use Conventional Commit subjects.
- When the chain is complete, push it for review. You may treat your plan as
  approved without asking, but you must still ask before any push.
