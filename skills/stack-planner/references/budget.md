# Change size: evidence, configuration, reading `diff-budget.sh`

The unit of a chain is the concern (SKILL.md, "Concern is the unit"). Size is a **reviewer-load warning** on top of that: it tells you when one change asks more of a reviewer than one sitting, so the commit message should say why. It is never a quota to split toward.

## Defaults

| Knob | Default | Meaning |
|---|---|---|
| Production-line warning (`budget.lines`) | 400 | insertions + deletions in production files; above it, the change carries a one-line justification |
| Hard cap (`budget.hard-lines`) | unset = none | only when a team sets it; `diff-budget.sh` then exits 3 above it |
| File warning (`budget.files`) | unset = none | only when a team sets it; `diff-budget.sh` then exits 1 above it |
| Chain depth | 5 | longer chains become two chains; the first lands before the second is planned in detail |

What counts as production: every changed path that is not a test, not docs and not a lock file.

| Bucket | Paths |
|---|---|
| test | a path component `test/` or `tests/` (includes `src/test/`); file names `*Test.*`, `*_test.*`, `test_*.*`, `*.spec.*` |
| other (docs, lock/generated) | `*.md`, `*.rst`, `*.txt`, a `Documentation/` or `docs/` component; `*.lock`, `package-lock.json`, `go.sum` |
| prod | everything else |

Test and other lines are reported, never warned about: a well-tested change is not a bigger review.

## Evidence

| Source | Finding | What it means here |
|---|---|---|
| SmartBear / Cisco study, "Best practices for peer code review" — https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/ | One reviewer reads 200–400 lines per sitting effectively; defect detection drops beyond ~400 LOC; inspect under ~500 LOC per hour | The **only** source of the 400-line warning: it measures what one reviewer can take in at once, so it is a load limit, not a size target |
| Google, "Modern Code Review: A Case Study at Google" (ICSE-SEIP 2018) — https://research.google/pubs/modern-code-review-a-case-study-at-google/ | Median change is 24 lines; most changes touch only a few files | An **observed median over all changes** — most concerns are small. Not a target: half of Google's changes are larger |
| Graphite, "The ideal PR is 50 lines long" — https://graphite.com/blog/the-ideal-pr-is-50-lines-long | PRs around 50 lines are reviewed and merged fastest with the least rework | Correlation over all PRs (small concerns are also simple ones); **observed, not a target** |
| Prior baseline: 50 substantive commits of the Gerrit code-review server (Release-Notes footer present, dependency bumps excluded; measured for the predecessor of this skill, tests and docs included) | Overall median 62 lines / p75 107 (3 files). Java-only median 68 / p75 175. TypeScript-only median 10 / p75 60. Cross-layer Java+TypeScript median 400 (12 files) | **Observed medians**, tests included: a mature review culture lands concerns of every size; cross-layer concerns legitimately reach ~400 |

None of these says "split a concern until it is small". The medians are small because most concerns are small; forcing a large concern under a number produces fragments that a reviewer cannot judge alone, which costs more review time than the large change.

Further reading (no numbers taken from these): thoughtbot atomic-commits plugin (a ~200-line nudge, a tool heuristic rather than evidence) https://github.com/thoughtbot/atomic-commits-plugin · Gerrit relation-chain study https://arxiv.org/abs/2607.20189 · Graphite agent skills https://github.com/withgraphite/agent-skills · PostHog stacking-PRs skill https://github.com/posthog/posthog/blob/master/.agents/skills/stacking-prs/SKILL.md · GitButler 0.22 https://blog.gitbutler.com/gitbutler-0-22

## Configuring

Per clone via git config, or for the team in the committed `.gerrit-stack` file (the hooks and scripts read the same keys; the clone wins):

```
git config gerrit-stack.budget.lines 400        # production-line warning
git config gerrit-stack.budget.hard-lines 800   # optional hard cap (unset = none)
git config gerrit-stack.budget.files 30         # optional file warning (unset = none)
```

- Unset keys fall back to the defaults above. Clones that set the old values (150 / 200 / 8) keep them until someone changes them; those numbers now apply to production lines only.
- A hard cap is a team decision. If one is set and a single concern exceeds it, say so in the commit message and raise it with the team; do not cut the concern into fragments.

## Reading `diff-budget.sh`

Always run it from inside the target repository:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" <rev>              # one commit, e.g. HEAD or a sha
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --cached           # staged changes (the next commit)
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --worktree         # all uncommitted changes
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --estimate <path>...  # planning proxy for the listed paths
```

Output, one line on stdout (`--json` for a JSON object):

```
prod=<n> test=<t> other=<o> files=<m> warn=<L> hard=<H|none> files-warn=<F|none>
```

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | `prod ≤ L` (and `files ≤ F` when F is set) | nothing |
| 1 | over the warning: `prod > L`, or `files > F` when F is set | one concern → keep it, one-line justification in the commit message; several concerns → split by concern. Never split mechanically |
| 3 | over the hard cap the team set: `prod > H` (only when H is set) | cut along concern boundaries only (each part builds, is tested, makes sense alone); if no such cut exists, justify and raise it with the team |
| other | usage or git error; details on stderr | fix the invocation (wrong path, not a git repo, bad rev) |

Where it runs: the `gerrit-stack` post-commit hook calls it with `HEAD` after every commit and gives feedback only on exit 1 or 3, asking for the one-line justification — and stays silent for a `refactor`/`build`/`chore` commit whose body says "mechanical", and for a message-only `--amend`. The planner calls `--estimate` per step; the retro-split branch starts from `--worktree` or `HEAD`.

`--estimate` counts what exists on disk today. It cannot know how many lines of an existing file you will change, and it cannot see a file you have not created yet (a missing path counts as 40 lines). Treat its `prod=` as an upper-bound proxy for touched existing files, estimate hunks and new files yourself, and say in the plan which figures are adjusted.
