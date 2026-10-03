---
schema_version: "1.1"
name: fix-mid-conflict
description: Rework on a seeded six-change chain — a blocking fix in change 3 on lines that change 5 also edits, so the rebase conflicts; the fix must land in change 3, the conflict be resolved in change 5, everything else stay untouched.
tags: [bench, rework]
runs: 1
max_turns: 60
timeout_seconds: 1200
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill]
---

You own the relation chain pushed for review to {url} (project {project}, changes {changes}). Reviewer feedback arrived on change {target}. Read the review comments, address the feedback and prepare the updated chain locally. Draft your reply to the reviewer in your final message, but do not post any comment, vote or push before I approve. This session is unattended: treat your own plan as approved.
