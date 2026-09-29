# A1-hooks — hook dispatchers (SessionStart / PreToolUse / PostToolUse / Stop)

status: done (2026-09-30)

## Files

| File | Change |
|---|---|
| `scripts/session-start.sh` | replaced stub — `additionalContext` per SPEC 119 (remote/host/branch/project, `commit-msg hook: installed|MISSING (run: …)`, `local chain: N ahead of <base>`, "Default workflow (not optional)" paragraph, grouping = user's choice, feedback → gerrit-review, status → get_related_changes). JSON via `jq`. |
| `scripts/git-guard.sh` | replaced stub — every row of the SPEC 121–134 table; parses all git invocations (`gs_parse_git_cmd`), `gs_detect "$dir"` per invocation, strictest decision wins (deny > ask > silent). |
| `scripts/git-post.sh` | replaced stub — commit (session marker, exactly-one-Change-Id, `diff-budget.sh HEAD` when present+executable, `gerrit-stack.footers`, snapshot), rebase/cherry-pick/reset (`gs_snapshot_diff` → `lost:`/`new:`), push refs/for (`/c/<proj>/+/<n>` numbers → `get_related_changes(<tip>)`; `no new changes` explanation). |
| `scripts/stop-check.sh` | replaced stub — `stop_hook_active` / no marker / non-Gerrit → exit 0; non-fixup chain commit without Change-Id → `{"decision":"block","reason":…}` with the SPEC repair command. |
| `tests/hooks.bats` | new — 37 tests, one per rule (+ fail-open, compound commands, `git -C <sub>`, strictest-wins, grouping from `%opts` vs config, force forms incl. `+refspec`, tags silent, budget via stubbed `diff-budget.sh`, snapshot drift, push response parsing, all four Stop paths). |

All four scripts: `set -uo pipefail`, `trap 'exit 0' ERR`, `command -v jq || exit 0`, stdin read once, `cd "$(jq -r .cwd)"` first, no network, `gs_trace <script> <verb> <decision>` on every decision path (`context|deny|ask|feedback|block|silent`), exit 0 silently in non-Gerrit dirs and on any unexpected condition.

## Verification (repo root, bash 3.2.57, bats 1.14, shellcheck 0.11, jq 1.8, git 2.55, Claude Code 2.1.284)

```
$ shellcheck -x scripts/session-start.sh scripts/git-guard.sh scripts/git-post.sh scripts/stop-check.sh; echo rc=$?
rc=0
$ bats tests/hooks.bats            → 1..37, 37 ok
$ bats tests/                      → 1..116, all ok (lib 37 + hooks 37 + metrics 9 + tools.bats from A2 as present at run time)
$ claude plugin validate ./ --strict → "Validation passed"
```

Live smoke (fixture built with `make_gerrit_repo` + 2 `commit_file` commits in the scratchpad; cwd = the work repo):

```
$ GERRIT_STACK_TRACE=$PWD/../trace.log claude -p --plugin-dir /Users/jcentkowski/workspace/open/gerrit-stack \
    --allowedTools 'Bash(git *)' --max-turns 4 "Run exactly: git push origin HEAD:refs/for/master and report what happened"
→ Claude reported the push was blocked pending confirmation and quoted the ask reason
  "Push 2 change(s) to refs/for/master [grouping: none]: 0efa1e7 feat: first; 05db697 feat: second"
trace.log:
  session-start	start	context
  git-guard	push	ask
  stop-check	stop	silent
$ git -C ../remote.git show-ref      → only refs/heads/master (no refs/for/*)
```
Second live run with the fixture's commit-msg hook removed: the SessionStart context contained
`commit-msg hook: MISSING (run: bash "/Users/jcentkowski/workspace/open/gerrit-stack/scripts/install-commit-msg-hook.sh")` — the plugin path resolves correctly in the hook environment.

No `git add`/`git commit` was run in the plugin repo.

## Contract decisions / notes for reviewers

- **`${CLAUDE_PLUGIN_ROOT}` in hook text**: hook *output* is not substituted by Claude Code, so the scripts expand the variable themselves (`${CLAUDE_PLUGIN_ROOT:-<script dir>/..}`) and print the absolute path in the install command (SessionStart MISSING line, guard "hook missing" deny, post "no Change-Id" feedback, post "verify with … gerrit-rest.py" hint). Verified live (see above).
- **`Change-Id:` in a commit command** is checked against the whole Bash command string, not just the parsed args, so heredoc bodies (`git commit -F - <<EOF …`) are covered too. A read-only git command mentioning `Change-Id:` in the same compound command as a `git commit` would also be denied (accepted: conservative).
- **Push parsing** (`parse_push` in git-guard.sh): positional = remote then refspecs; `--repo`, `-o/--push-option` (also `%`-style grouping from `-o topic=`), `--force*`/`-f`/bundled `-f`/`+refspec` → force; `--all/--mirror` → direct; `--tags`, `refs/tags/*`, `refs/*` other than heads/for → silent. No refspec → `remote.<r>.push` (all values); none → current branch (push.default simple) → direct. Remote default: `branch.<b>.pushRemote` → `remote.pushDefault` → `branch.<b>.remote` → `$GS_REMOTE`.
- **Chain for a refs/for push** = `refs/remotes/<remote>/<target-branch>..<src>` when that tracking ref exists (falls back to `$GS_BASE`), `src` from the refspec (`HEAD` when it does not resolve). Duplicate Change-Ids (>1 on one commit) also deny (Gerrit rejects them) — one extra row beyond the SPEC table.
- **Ask reason format**: `Push N change(s) to refs/for/<b> [grouping: none | hashtag <tag> | topic <slug>]: sha7 subject; sha7 subject` (+ ` — topic will be submitted together if submitWholeTopic is on — intended?` only when `%topic=` is in the command and `gerrit-stack.grouping` ≠ topic). No text about a missing topic.
- **git-post rebase/cherry-pick/reset**: skipped while a rebase or cherry-pick is still in progress (`rebase-merge`/`rebase-apply`/`CHERRY_PICK_HEAD`); after reporting drift the snapshot is refreshed so the same drift is reported once. `git commit --dry-run` only marks the session.
- **git-post push**: only for refs/for pushes (explicit refspec, or no refspec with a `remote.<r>.push=…refs/for/…` default). Change numbers are de-duplicated in order of appearance; the tip is the last one Gerrit listed.
- **diff-budget.sh** is run only when `scripts/diff-budget.sh` exists **and is executable** (per the WP brief). At the time of writing A2's file exists but is mode 644 → the budget check is skipped silently. If A2 does not `chmod +x` it, either A2 does so or the `-x` test in `post_commit` should become `-f` (it is invoked via `bash`, so the mode bit is not technically needed). The bats test for exit 3 uses a stubbed copy and does not depend on A2.
- Trace lines are TAB-separated (`gs_trace` contract). Per-invocation trace, so a compound command yields one line per git call.

## Open issues

- `diff-budget.sh` executable bit (see above) — needs A2 or main to decide.
- Not exercised live: PostToolUse feedback against a real Gerrit push (A7 demo / P4 integration); covered by unit tests with a captured Gerrit response shape.
