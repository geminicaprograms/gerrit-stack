# Diff budget: evidence, configuration, reading `diff-budget.sh`

## Defaults

| Knob | Default | Meaning |
|---|---|---|
| Soft lines | 150 | ceiling per step (insertions + deletions, tests included); sweet spot 50–150 |
| Soft files | 8 | ceiling per step; sweet spot 2–8 |
| Hard lines | 200 | a single-layer step over this is split, not justified |
| Cross-layer ceiling | 500 lines / 15 files | planner judgement, not a script knob: allowed only when two layers are meaningless apart, justified in the reviewer note |
| Chain depth | 5 | longer chains become two chains; the first lands before the second is planned in detail |

## Evidence

| Source | Finding | What it means for the budget |
|---|---|---|
| SmartBear / Cisco study, "Best practices for peer code review" — https://smartbear.com/learn/code-review/best-practices-for-peer-code-review/ | Review fewer than 200–400 lines at a time; defect detection drops beyond ~400 LOC; inspect under ~500 LOC per hour | The 200-line hard cap keeps a single-layer review inside one sitting; 400–500 is the outer edge, reserved for coupled cross-layer steps |
| Google, "Modern Code Review: A Case Study at Google" (ICSE-SEIP 2018) — https://research.google/pubs/modern-code-review-a-case-study-at-google/ | Median change is 24 lines; most changes touch only a few files | Small changes are the norm at scale; 50–150 is generous, not stingy |
| Graphite, "The ideal PR is 50 lines long" — https://graphite.com/blog/the-ideal-pr-is-50-lines-long | PRs around 50 lines are reviewed and merged fastest with the least rework | Aim for the low end of the range when a slice allows it |
| thoughtbot atomic-commits plugin — https://github.com/thoughtbot/atomic-commits-plugin | Nudges the agent to split when a commit approaches ~200 lines | An independent tool converging on the same hard cap |
| Prior baseline: 50 substantive commits of the Gerrit code-review server (Release-Notes footer present, dependency bumps excluded; measured for the predecessor of this skill) | Overall median 62 lines / p75 107 (3 files). Java-only median 68 / p75 175 (4 files). TypeScript-only median 10 / p75 60 (3 files). Cross-layer Java+TypeScript median 400 (12 files) | Human-authored changes in a mature review culture sit inside 50–150; cross-layer steps legitimately reach ~400 when backend and frontend are meaningless apart |

Further reading (no numbers taken from these): Gerrit relation-chain study https://arxiv.org/abs/2607.20189 · Graphite agent skills https://github.com/withgraphite/agent-skills · PostHog stacking-PRs skill https://github.com/posthog/posthog/blob/master/.agents/skills/stacking-prs/SKILL.md · GitButler 0.22 https://blog.gitbutler.com/gitbutler-0-22

## Configuring the budget

Per clone, via git config (the `gerrit-stack` hooks and scripts read the same keys):

```
git config gerrit-stack.budget.lines 150
git config gerrit-stack.budget.files 8
git config gerrit-stack.budget.hard-lines 200
```

- Unset keys fall back to the defaults above.
- There is no separate cross-layer knob. For a repository where coupled backend + frontend steps are the norm, either raise `hard-lines` for that clone (for example to 400) or keep 200 and expect the post-commit budget feedback on a justified step; answer it with the justification from the plan's reviewer note rather than splitting again.
- To share values with a team, document them in the project's contributor guide; git config is not versioned.

## Reading `diff-budget.sh`

Always run it from inside the target repository:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" <rev>              # one commit, e.g. HEAD or a sha
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --worktree         # all uncommitted changes
bash "${CLAUDE_PLUGIN_ROOT}/scripts/diff-budget.sh" --estimate <path>...  # planning proxy for the listed paths
```

Output, one line on stdout:

```
lines=<n> files=<m> budget=<L>/<F> hard=<H>
```

| Exit code | Meaning | What to do |
|---|---|---|
| 0 | `n ≤ L` and `m ≤ F` | within budget |
| 1 | over the soft budget (`n > L` or `m > F`) but `n ≤ H` | split, or justify in the reviewer note |
| 3 | over the hard cap (`n > H`) | split; for an existing commit or worktree use `retro-split.md` |
| other | usage or git error; details on stderr | fix the invocation (wrong path, not a git repo, bad rev) |

Where it runs: the `gerrit-stack` post-commit hook calls it with `HEAD` after every commit (feedback only, never blocking); the planner calls `--estimate` per step; the retro-split branch starts from `--worktree` or `HEAD`.

`--estimate` counts what exists on disk today. It cannot know how many lines of an existing file you will change, and it cannot see a file you have not created yet. Treat its `lines=` as an upper-bound proxy for touched existing files, estimate hunks and new files yourself, and say in the plan which figures are adjusted.
