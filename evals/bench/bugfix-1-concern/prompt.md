---
schema_version: "1.1"
name: bugfix-1-concern
description: A one-line bug fix plus its test; the ideal outcome is exactly one small change, not a chain and not a mixed change.
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

Bug report: the default ping message is wrong. Operators expect
`GET /projects/{name}/demo-plugin~ping` and `demo-plugin ping <project>` to
answer `pong!` (with an exclamation mark) when `pingMessage` is not configured,
but the plugin answers `pong`.

Fix the default in `DemoPluginConfig` and update the existing unit test(s) so
they assert the new default. Do not touch anything else.

Rules of this repo:

- One concern per change; this is a single concern, so it must be a single
  small change.
- Use a Conventional Commit subject.
- Push the fix for review when done. You may treat your plan as approved
  without asking, but you must still ask before any push.
