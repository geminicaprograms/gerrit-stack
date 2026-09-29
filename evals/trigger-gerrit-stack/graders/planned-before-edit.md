---
type: llm
weight: 1
---
The run must PLAN before it CODES. PASS only if, before the first `Edit` or `Write` tool call,
the assistant either invoked a planning skill (`gerrit-stack` / `stack-planner`, a `Skill` tool
call) or produced an explicit chain plan (ordered steps / commits, one concern each, with the
files per step). FAIL if the first file modification happens with no plan or skill invocation
before it, or if the run edits nothing and produces no plan at all.
