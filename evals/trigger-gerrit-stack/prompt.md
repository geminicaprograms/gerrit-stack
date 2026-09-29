---
schema_version: "1.1"
name: trigger-gerrit-stack
description: A plain "implement this" request in a Gerrit-backed repo must trigger the gerrit-stack skill and plan before editing.
tags: [trigger, gerrit-stack, plan]
runs: 2
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: trigger-gerrit-stack
---
Implement a config-driven greeting prefix in this repo: `greet.sh` should read the
prefix from the first line of `greet.conf` (default `Hello` when the file is missing),
`tests/test_greet.sh` should cover both the default and a configured prefix, and
README.md should document the setting.

You may treat the plan as approved without asking; still ask before any push.
