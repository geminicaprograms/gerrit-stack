---
schema_version: "1.1"
name: split-over-budget
description: A 600-line uncommitted diff spanning three concerns must be split into several single-concern commits, not committed as one.
tags: [budget, retro-split, stack-planner]
runs: 2
max_turns: 40
timeout_seconds: 900
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: split-over-budget
---
There is an uncommitted change in this repo (see `git status` / `git diff`). Commit it for
review on Gerrit.

You may treat the plan as approved without asking; still ask before any push.
