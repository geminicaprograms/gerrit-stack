# A10-docs — status

status: done

## Files written
- `README.md` — pitch, requirements, install (SPEC line-100 order + GitHub
  mirror note for `gerrit@gerrit-mcp`, local-dev `--plugin-dir`), skills
  (three, one paragraph each), how-it-works (hooks table + full
  `git-guard.sh` row table + scripts/flags table from the interface
  contract), configuration (`git config gerrit-stack.<key>` table with
  defaults + grouping semantics), demo (`make demo-up demo-seed`, in-tree
  Bazel via `demo/gerrit-tree.sh`, `demo/RUN.md` pointer), tests & evals
  (`make check`, `evals/run.py` rationale, `docs/benchmark.md` pointer),
  troubleshooting (5 items as specified), design principles (all 9 bullets
  from SPEC lines 34-42, condensed), license.
- `CHANGELOG.md` — Keep a Changelog format, `## [Unreleased]` / `### Added`
  only, one bullet group per shipped component (manifests, hooks, libs,
  tool scripts, skills, demo, evals + runner, benchmark, tests, Makefile,
  CI, docs).
- `docs/jj-stretch.md` — design note: gate status (jj 0.34 shipped `jj
  gerrit upload`, current release 0.45.1, not installed on this machine →
  deferred, decide after M3), `.jj/` detection → `GS_VCS=jj`, SKILL "jj
  mode" (`jj new`/`jj describe`, `jj squash --into`, `jj gerrit upload -r
  'trunk()..@' --remote <r>`), Change-Id handling via jj's transient
  change-id-derived Change-Id + `[templates] commit_trailers =
  format_gerrit_change_id_trailer(self)` + `gerrit.review-url`, `jj-guard.sh`
  (`if: Bash(jj *)`), same grouping question, 1 bats + 1 eval, ~8 h effort.
- `docs/plans/status/A10-docs.md` — this file.

## Verification
- Read the full interface contract, hook/guard tables, config-key list, and
  tool-script flag signatures from
  `~/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-part1-execution-plan.md`
  and cross-checked every flag/command mentioned in README.md against it —
  no invented flags.
- Read SPEC lines 1-200 (Part 1, lines 31-172) from
  `~/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md`
  for design principles, hook tables, skill descriptions/phases, demo infra,
  evals table, and the jj-stretch line (172).
- Read `docs/plans/PROGRESS.md` Phase 0 findings: confirmed `claude plugin
  eval` is gated ("early access") on this install → A9 ships `evals/run.py`
  (matches what README/CHANGELOG say); confirmed `gerrit@gerrit-mcp` was
  installed from the GitHub mirror because `gerrit.googlesource.com`
  returned a Gitiles 503 → used as the rationale for the mirror note in the
  install section.
- Read `.claude-plugin/plugin.json`, `.claude-plugin/marketplace.json`,
  `hooks/hooks.json`, `Makefile`, `.claude/CLAUDE.md` in the repo root and
  matched `make eval`/`make bench`/`make check` command lines verbatim in
  README.md against the actual `Makefile` targets.
- Verified `jj gerrit upload`'s real documented flags (`-r`/`--revisions`,
  `--remote`, `--remote-branch`, `--dry-run`) and the Change-Id template
  mechanism (`gerrit.review-url`, `[templates] commit_trailers =
  format_gerrit_change_id_trailer(self)`) via WebFetch against
  https://docs.jj-vcs.dev/latest/gerrit/ — this diverged from one detail in
  the work-package brief, see "Open issues" below.
- `grep` over README.md confirmed every `|` character inside a table cell is
  backslash-escaped (`\|`) or was rewritten as `/`, so no table row breaks.
- No files outside this WP's ownership (`README.md`, `CHANGELOG.md`,
  `docs/jj-stretch.md`, `docs/plans/status/A10-docs.md`) were created,
  edited, or deleted. No `git add`/`git commit` run.

## Open issues
- The work-package brief for `docs/jj-stretch.md` specified writing that
  `jj gerrit upload` takes `--topic/--hashtag/--wip` flags. Live
  verification against the jj docs (https://docs.jj-vcs.dev/latest/gerrit/)
  found no such flags documented — only `-r`/`--revisions`, `--remote`,
  `--remote-branch`, `--dry-run`. Since this stretch is gated/deferred and
  cannot be tested locally (`jj` not installed), I wrote the note to state
  this honestly: grouping via `jj gerrit upload` is an **open question**,
  with the likely mechanism (Gerrit magic-branch suffix through
  `--remote-branch`, e.g. `--remote-branch '<branch>%topic=<slug>'`,
  mirroring how `push-chain.sh` encodes grouping for `git push` today)
  flagged as unconfirmed rather than asserted as fact. Flagging this in case
  the brief's flags came from a source I don't have access to and should
  override my web-verified version.
- README's "Demo" and "Tests & evals" sections reference `demo/RUN.md`,
  `docs/benchmark.md`, `demo/gerrit-tree.sh`, `demo/warm-bazel.sh`, and
  `evals/run.py`, which are owned by other work packages (A7, A11, A8, A9)
  and did not all exist in the tree at the time of writing (per the
  contract, A10 was told to work from the interface contract, not from
  files that may not exist yet). Nothing to fix on my end; flagging so the
  main agent's Phase 3 review can confirm the paths/flags I documented
  still match what those WPs actually ship.
