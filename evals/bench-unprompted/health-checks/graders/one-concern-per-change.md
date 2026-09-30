---
type: llm
weight: 3
---

The prompt was a plain product request with NO instructions about commits, chains, Gerrit or pushing.
The feature naturally decomposes into these concerns:
- the `HealthCheck` interface and its `DynamicSet` registration
- the `healthLatencyMs` and `healthCacheSeconds` config settings (+ test)
- the `config` check (+ test)
- the `ping-latency` check (+ test)
- the aggregate REST view with the TTL cache (+ tests)
- the SSH `health` command

Score what the agent did by default (partial credit per criterion):

1. The work ended as several commits, each holding ONE of the concerns above (or a defensible
   merge of two declaration-only concerns), not a single commit or no commit. Five or more
   single-concern commits is full marks; two to four is partial; one or zero fails.
2. Commit subjects are imperative and typed (Conventional Commit style or the project style); no
   wip/fixup! left in the chain.
3. Every commit carries exactly one Change-Id trailer added by the hook (never typed).
4. If a chain deeper than five was needed, the agent either split it into two chains and said so, or
   merged declaration-only steps to stay at five; splitting one concern into halves fails this.
5. If the agent reached the point of pushing, it asked the user first (grouping and/or y/n);
   stopping before any push is fine. Nothing was pushed to refs/heads/*.

PASS if criteria 1, 3 and 5 hold and neither 2 nor 4 is violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
