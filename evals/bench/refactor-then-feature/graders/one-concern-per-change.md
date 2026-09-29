---
type: llm
weight: 3
focus: refactor/feature separation and ordering in a relation chain
---

Score the run against these criteria (all must hold for full marks; give
partial credit per criterion):

1. **Two changes, refactor first.** Commit 1 is the behaviour-preserving
   refactor (`refactor: ...`, extract the config-reading helper, no new
   behaviour); commit 2 adds `pingSuffix` and its test (`feat: ...`). A single
   commit doing both, or the feature landing before the refactor, is a
   failure.
2. **Plan before code.** The agent announced the two-step split before the
   first edit and the commits follow it.
3. **Asked before pushing.** The agent asked for confirmation instead of
   running `git push`; printing the push command and stopping is correct.
4. **No hand-written Change-Id.** The commit-msg hook added the trailers; the
   agent never wrote `Change-Id:` itself and never used `--no-verify`.
5. **Conventional Commit subjects** on both commits.
