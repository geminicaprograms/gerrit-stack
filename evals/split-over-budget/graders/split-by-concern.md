---
type: llm
weight: 2
---
The worktree held ~500 uncommitted lines across four concerns, each small enough to review on its own:
logging helpers (`lib/logging.sh`), config parsing (`lib/config.sh`), config tests
(`tests/test_config.sh`) and docs (`docs/USAGE.md`), plus a small wiring edit in `greet.sh`. PASS only if the run created
at least 2 commits, each covering one concern (staging by path or `git add -p`), rather than one
commit with everything — a retro-split (commit then `git reset --soft` and re-commit per concern)
also PASSES as long as the end state is ≥ 2 single-concern commits. FAIL if a single commit holds
the whole diff, if the run stops without committing anything, if it drops part of the change, or if
it splits a single file into mechanical halves ("first half / second half") instead of by concern.
