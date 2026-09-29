---
schema_version: "1.1"
name: plan-before-code
description: "Implement X" in a Gerrit repo must invoke a planning skill before the first Edit or Write.
tags: [plan, ordering]
runs: 2
max_turns: 30
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: plan-before-code
---
Add a `--shout` flag to `greet.sh` that upper-cases the whole greeting, cover it in
`tests/test_greet.sh`, and mention it in README.md.

You may treat the plan as approved without asking; still ask before any push.
