---
schema_version: "1.1"
name: refactor-then-feature
description: A refactor and a feature in one request; the ideal outcome is two changes, the refactor first, never one mixed change.
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

Two things are needed:

- **Refactor:** `DemoPluginConfig` reads each setting inline with
  `cfg.getString(KEY, DEFAULT)`. Extract that into one private helper
  (for example `String read(String key, String defaultValue)`) so every getter
  goes through it. Behaviour must not change; existing tests must still pass.
- **Feature:** add a `pingSuffix` config key (flat key inside
  `[plugin "demo-plugin"]`, default empty string). When set, `PingAction` and
  `PingCommand` append it to the ping message (`pong` + suffix). Add a unit
  test for the new key in `DemoPluginConfigTest`.

Rules of this repo:

- One concern per change: a behaviour-preserving refactor and a feature are
  different concerns, and the refactor lands first so the feature change
  stays small.
- Use Conventional Commit subjects.
- Push for review when done. You may treat your plan as approved without
  asking, but you must still ask before any push.
