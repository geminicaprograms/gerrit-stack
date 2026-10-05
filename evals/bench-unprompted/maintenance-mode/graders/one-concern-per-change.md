---
type: llm
weight: 3
---

The prompt was a plain product request with NO instructions about commits, chains, Gerrit or pushing.
The feature naturally decomposes into these concerns:
- a `maintenanceMessage` config setting (+ test)
- a per-project maintenance state holder (+ test)
- the `demo-plugin-maintain` global capability declaration
- REST GET/PUT/DELETE views for `demo-plugin~maintenance` with the capability check (+ tests)
- ping (REST and SSH) answering 503 / the message while in maintenance (+ tests)
- the SSH `demo-plugin maintenance` command

Score what the agent did by default (partial credit per criterion):

1. The work ended as several commits, each holding ONE of the concerns above (or a defensible
   merge of two declaration-only concerns, or a declaration-only concern or config setting merged
   into the change that first uses it), not a single commit or no commit. Five or more single-concern commits is
   full marks; two to four is partial; one or zero fails. Fragments (see 6) do not count as
   single-concern commits.
2. Commit subjects are imperative and typed (Conventional Commit style or the project style); no
   wip/fixup! left in the chain.
3. Every commit carries exactly one Change-Id trailer added by the hook (never typed).
4. If a chain deeper than five was needed, the agent either split it into two chains and said so, or
   merged declaration-only steps to stay at five; splitting one concern into halves fails this.
5. If the agent reached the point of pushing, it asked the user first (grouping and/or y/n);
   stopping before any push is fine. Nothing was pushed to refs/heads/*.
6. No over-fragmentation. Each commit is meaningful on its own: it builds, it is tested or
   used, and a reviewer can state its purpose in one sentence. A commit that only makes sense
   with the next one (a class with no caller, scaffolding, "part 1" of a concern) counts against
   the chain exactly like a commit that mixes two concerns. The concerns listed above are
   meaningful units by definition. A broad mechanical change (a migration, a rename) is one
   commit by design. Size is not a criterion: never reward or penalise a commit for its line or
   file count.

PASS if criteria 1, 3, 5 and 6 hold and neither 2 nor 4 is violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
