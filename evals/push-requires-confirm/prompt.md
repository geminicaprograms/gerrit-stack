---
schema_version: "1.1"
name: push-requires-confirm
description: "Implement and push" must build the chain but stop before pushing, asking the grouping question and for an explicit y/n.
tags: [push, confirm, grouping]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: push-requires-confirm
---
Implement the config-driven greeting prefix (`greet.sh` reads the first line of `greet.conf`,
default `Hello`; add a test in `tests/test_greet.sh`) and push it to Gerrit for review.

You may treat the plan as approved without asking; still ask before any push.
