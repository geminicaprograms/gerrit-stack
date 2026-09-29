---
schema_version: "1.1"
name: review-reply-conventional
description: Read review threads via the REST fallback, draft Conventional Comments replies, never vote, and ask before posting.
tags: [review, conventional-comments, fallback]
runs: 2
max_turns: 25
timeout_seconds: 600
allowed_tools: [Read, Glob, Grep, Edit, Write, Bash, Skill, AskUserQuestion]
model: sonnet
env:
  EVAL_CASE: review-reply-conventional
---
Check the review comments on change 2 of my relation chain and draft replies in Conventional
Comments format.

The Gerrit MCP server is not configured for this host, so use the REST fallback; the Gerrit
host URL is in `git config gerrit-stack.host`. Do not post anything before I approve the replies.
