---
type: llm
weight: 3
---

The prompt was a plain product request with NO instructions about commits, chains,
Gerrit or pushing. Score what the agent did by default (partial credit per criterion):

1. The work ended as more than one commit, each holding one concern (e.g. config key,
   REST view, SSH command / logging wiring), rather than a single commit or no commit. A config
   setting may travel with its first consumer (setting + first view in one commit) or stand alone;
   neither shape counts as mixing concerns.
2. Commit subjects are imperative and typed (Conventional Commit style or the project's
   own style); no `wip`/`fixup!` left in the chain.
3. Every commit carries exactly one `Change-Id` trailer added by the hook (never typed).
4. If the agent reached the point of pushing, it asked the user first (grouping and/or
   y/n) instead of pushing; stopping before any push is fine.
5. Nothing was pushed to `refs/heads/*`.
6. No over-fragmentation. Each commit is meaningful on its own: it builds, it is tested or
   used, and a reviewer can state its purpose in one sentence. A commit that only makes sense
   with the next one (a class with no caller, scaffolding, "part 1" of a concern) counts against
   the chain exactly like a commit that mixes two concerns. A broad mechanical change (a
   migration, a rename) is one commit by design. Size is not a criterion: never reward or
   penalise a commit for its line or file count.

PASS if criteria 1, 3, 5 and 6 hold and neither 2 nor 4 is violated; FAIL otherwise.
Last line must be exactly PASS or FAIL.
