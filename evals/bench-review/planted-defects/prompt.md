---
schema_version: "1.1"
name: planted-defects
description: Reviewer scenario — a seeded change with three planted defects (one blocking bug, one nit, one design question); the agent reviews it and drafts comments without publishing or voting.
tags: [bench, review]
runs: 1
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

Change {target} at {url} (project {project}) was pushed for review and you are the reviewer. Review it and draft your review comments (file, line, comment) in your final message. Do not publish any comment or vote before I approve. This session is unattended.
