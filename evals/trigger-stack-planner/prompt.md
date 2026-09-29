---
schema_version: "1.1"
name: trigger-stack-planner
description: "Break this down" for a 3-concern feature must trigger stack-planner, emit a Step 1..N plan and stop for approval without coding.
tags: [trigger, stack-planner, plan]
runs: 2
max_turns: 20
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: trigger-stack-planner
---
Break this feature down into an ordered chain of small, independently reviewable steps.
Do not write any code yet — I only want the plan.

The feature: `greet.sh` gets a greeting prefix from three sources with increasing priority:
(1) the first line of a `greet.conf` file, (2) a `GREET_PREFIX` environment variable that
overrides the file, and (3) a `--prefix <text>` command-line flag that overrides both.
Each source needs tests in `tests/test_greet.sh` and a README entry.
