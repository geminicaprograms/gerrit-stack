# jj stretch: design note (not implemented)

gerrit-stack targets Git. [Jujutsu (`jj`)](https://jj-vcs.github.io/jj/latest/)
is a separate VCS that can also colocate with a Git repo and talk to Gerrit
directly. Native `jj` support was scoped out of Part 1 as an optional
stretch (~8 h) gated on `jj gerrit upload` being usable. This note records
the design so it can be picked up later without re-deriving it; it ships no
code.

## Gate status

`jj gerrit upload` shipped in `jj` 0.34 (2025-10) as an experimental — but
not flag-gated — command. The current `jj` release is 0.45.1. `jj` itself
**is not installed** on the machine this plugin was built on, so the gate
(`jj gerrit upload --help` exits 0) could not be checked during this build.
**Decision: deferred.** Install `jj` (`brew install jj`), re-run the gate,
and decide whether to build this after M3.

## What would change

- **VCS detection**: `scripts/lib/gerrit-detect.sh` would gain a `.jj/`
  check (colocated or native repo) and export `GS_VCS=jj` alongside the
  existing exports; everything downstream (skills, `chain.sh` queries)
  would branch on `GS_VCS` rather than assuming Git commits.
- **SKILL "jj mode"** (in `gerrit-stack`, additive — the Git path is
  unchanged): one `jj new` + `jj describe` per plan step instead of `git add`
  + `git commit`; `jj squash --into <rev>` to fold a fix into a middle change
  instead of `git commit --fixup` + autosquash rebase; upload with
  `jj gerrit upload -r 'trunk()..@' --remote <r>` (revset scoped to the
  chain, mirroring `gs_chain_commits`'s `base..HEAD`).
- **Change-Id**: `jj` does not write a `Change-Id:` trailer by default. Per
  the [jj Gerrit guide](https://docs.jj-vcs.dev/latest/gerrit/), a transient
  Change-Id derived from the `jj` change ID is used for the upload itself
  (stable across `jj new`/rebase as long as the change ID doesn't change),
  and a `[templates] commit_trailers` entry
  (`format_gerrit_change_id_trailer(self)`) can materialize it into the
  commit description on demand; `gerrit.review-url` switches Gerrit's
  Change-Id trailer for a `Link:` trailer instead. The design keeps the same
  rule as the Git path — gerrit-stack never hand-writes the trailer, it only
  reads whatever `jj`/the template produced.
- **Grouping**: the same grouping question (none / hashtag / topic) the
  `gerrit-stack` skill asks today, stored the same way
  (`git config gerrit-stack.grouping` / `gerrit-stack.group-name` — `jj`
  repos colocated with Git still have a `.git` config to read). **Open
  question, not yet resolved by upstream docs**: `jj gerrit upload`'s
  documented flags are `-r`/`--revisions`, `--remote`, `--remote-branch`,
  and `--dry-run` — no `--topic`/`--hashtag`/`--wip` flags are documented.
  The likely mechanism, matching how `push-chain.sh` encodes grouping today
  (`refs/for/<branch>[%t=<tag>|%topic=<slug>][,wip]`), is passing the magic
  branch suffix through `--remote-branch`, e.g.
  `--remote-branch '<branch>%topic=<slug>'` — this needs to be confirmed
  against a real `jj` + Gerrit pair once the gate is open, not assumed.
- **`jj-guard.sh`**: a new hook script, `if: "Bash(jj *)"`, mirroring
  `git-guard.sh`'s deny/ask rows for the `jj` verbs that matter (`jj gerrit
  upload` without a plan, missing/duplicate Change-Id equivalent, direct
  push equivalents) — registered as an additional `PreToolUse` entry in
  `hooks/hooks.json` alongside the existing `Bash(git *)` one, not a
  replacement for it.
- **Tests**: 1 `bats` file exercising `jj-guard.sh` against a `.jj/` fixture,
  1 eval case exercising the jj-mode upload path end to end.

## Effort

~8 hours, per the original SPEC estimate: `.jj/` detection + `GS_VCS`
plumbing (~1 h), `jj-guard.sh` + bats (~2 h), SKILL "jj mode" phases +
reference doc (~2 h), upload/grouping design confirmation against a real
`jj`+Gerrit pair (~2 h), eval case (~1 h).
