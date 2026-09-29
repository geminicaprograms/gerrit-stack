---
type: llm
weight: 2
---
The worktree held ~600 uncommitted lines across three concerns: logging helpers
(`lib/logging.sh`), config parsing (`lib/config.sh`), and docs + tests (`docs/USAGE.md`,
`tests/test_config.sh`), plus a one-line wiring edit in `greet.sh`. PASS only if the run created
at least 2 commits, each covering one concern (staging by path or `git add -p`), rather than one
commit with everything — a retro-split (commit then `git reset --soft` and re-commit per concern)
also PASSES as long as the end state is ≥ 2 single-concern commits. FAIL if a single commit holds
the whole diff, if the run stops without committing anything, or if it drops part of the change.
