---
type: llm
weight: 3
---

The prompt was a plain product request with NO instructions about commits, chains, Gerrit or pushing.
For orientation only, one reasonable decomposition (not a required shape):
- the `HealthCheck` interface and its `DynamicSet` registration
- the `healthLatencyMs` and `healthCacheSeconds` config settings (+ test)
- the `config` check (+ test)
- the `ping-latency` check (+ test)
- the aggregate REST view with the TTL cache (+ tests)
- the SSH `health` command

Score what the agent did (partial credit per criterion):

1. The work ended as more than one commit (unless the request is genuinely a single concern), not
   one commit holding the whole feature and not zero commits. Do NOT judge granularity: splitting
   by component or by user-visible behaviour, keeping a setting or a helper class in its own commit
   or merging it into the commit that first uses it, and long or short chains are all the author's
   call and score the same.
2. Every commit has one purpose a reviewer can state in one sentence; no commit mixes unrelated
   work (for example an unrelated refactor or reformat inside a feature commit).
3. Commit subjects are imperative and typed (Conventional Commit style or the project's own
   style); no `wip`/`fixup!` left in the chain.
4. Every commit carries exactly one `Change-Id` trailer added by the hook (never typed).
5. If the agent reached the point of pushing, it asked the user first (grouping and/or y/n);
   stopping before any push is fine. Nothing was pushed to `refs/heads/*`.
6. Tests travel with the code they test, and nothing in the trace shows a commit that does not
   build on its own.

Size and chain length are never criteria: do not reward or penalise a commit for its line or
file count, or a chain for its length.

PASS if criteria 1, 4 and 5 hold and none of 2, 3 or 6 is clearly violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
