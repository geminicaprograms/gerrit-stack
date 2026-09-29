# A2-tools — chain-status / push-chain / diff-budget / install-commit-msg-hook

status: done

## Files written (owned by A2; nothing else touched)

| File | Change |
|---|---|
| `scripts/chain-status.sh` | new — default table, `--json`, `--preflight`, `--snapshot`, `--verify-ids` |
| `scripts/push-chain.sh` | new — validates the chain, prints exactly one `git push … HEAD:refs/for/…` line, never writes config |
| `scripts/diff-budget.sh` | new — `<rev>` (default `HEAD`) / `--worktree` / `--estimate <path>...`, optional `--json`; works in any git repo |
| `scripts/install-commit-msg-hook.sh` | new — `--host` / `--from` / `--force`, self-test, prints the hook path; the only script that uses the network (one `curl`) |
| `tests/tools.bats` | new — 33 tests (9 chain-status, 10 push-chain, 7 diff-budget, 7 install-hook) |
| `docs/plans/status/A2-tools.md` | this file |

All four scripts: `#!/usr/bin/env bash`, `set -uo pipefail`, source `lib/chain.sh` via `$(dirname "${BASH_SOURCE[0]}")`, bash 3.2 clean (suite also run with `/bin/bash` 3.2.57 first on `PATH`), `jq` for JSON, never run `git commit`/`git push`. `gs_trace` lines: `chain-status preflight|verify-ids`, `push-chain validate reject|compose <grouping>`, `diff-budget <mode> <status>`, `install-commit-msg-hook install ok`.

## Verification (repo root, bats 1.14, shellcheck 0.11, jq 1.8, git 2.55)

```
$ shellcheck -x scripts/chain-status.sh scripts/push-chain.sh scripts/install-commit-msg-hook.sh scripts/diff-budget.sh ; echo rc=$?
rc=0
$ bats --print-output-on-failure tests/tools.bats
1..33  — 33/33 ok
$ PATH=<dir with bash -> /bin/bash>:$PATH bats tests/tools.bats      # macOS bash 3.2.57
1..33  — 33/33 ok
$ bats --print-output-on-failure tests/
1..116 — 116/116 ok  (lib.bats 37 + metrics.bats + tools.bats 33 + others present in the tree)
$ make lint            # shellcheck over every scripts/**/*.sh + demo + py_compile
clean
$ claude plugin validate ./ --strict
✔ Validation passed
```

Real chain worktree `/Users/jcentkowski/workspace/open/experiments/gerrit-split` (read-only; `--snapshot` deliberately not run there):

```
$ bash scripts/chain-status.sh
chain: 3 change(s) on origin/master (base 7f236d8)
sha7    | Change-Id  | +/-         | files | subject
5559ac7 | I2efcd2ba9 | +0/-296     |     5 | Revert canAiReview field on ChangeInfo REST response
af709a0 | I6f8401426 | +70/-75     |     7 | Wire AI review button to revision actions endpoint
4dbf71e | I119ef4897 | +337/-1     |     5 | Emit aiReview action with permission gate
rc=0

$ bash scripts/chain-status.sh --preflight
remote: origin
host: https://gerrit-review.googlesource.com
branch: master
project: gerrit
base: origin/master (7f236d8)
commit-msg hook: installed (/Users/jcentkowski/workspace/open/experiments/gerrit/.git/hooks/commit-msg)
chain: 3
gerrit-mcp: installed
rc=0

$ bash scripts/chain-status.sh --json | jq -c '{remote,branch,project,base,hook_ok,n:(.changes|length)}'
{"remote":"origin","branch":"master","project":"gerrit","base":"origin/master","hook_ok":1,"n":3}
$ bash scripts/push-chain.sh
git push origin HEAD:refs/for/master                      # rc=0
$ bash scripts/diff-budget.sh HEAD
lines=338 files=5 budget=150/8 hard=200                   # rc=3 (+ stderr "over the hard cap")
$ bash scripts/chain-status.sh --verify-ids
no snapshot at …/.git/worktrees/gerrit-split/gerrit-stack/chain-ids (…); nothing to verify   # rc=0
```
(The hooks dir resolves to the main repo's `.git/hooks`, the state dir to the worktree's `gerrit-stack/` — `--git-path` semantics preserved from A0.)

## Contract decisions (documented in each script header)

- **chain-status table**: 5 columns exactly as the contract (`sha7 | Change-Id | +/- | files | subject`), a column-header row under the `chain: N …` header, `+/-` shown as `+ins/-del`. Marks live inside the existing columns: Change-Id column = `MISSING` / `DUP(n)`, subject suffixed with ` [fixup: squash before push]`. `--json` adds `base_sha` next to `base` (`base` is the short ref `origin/master`, matching the `<base>` the skills paste into rebase commands). `hook_ok` is the number 1/0. Modes are exclusive (two flags → exit 2); unknown option → exit 2.
- **preflight**: `commit-msg hook: installed (<path>)` or `MISSING (run bash <abs scripts dir>/install-commit-msg-hook.sh)`; `host:` prints a hint when the remote is not http(s). `gerrit-mcp:` greps `claude plugin list` for `gerrit-mcp` (≈0.2 s), `unknown` when `claude` is absent or the command fails.
- **verify-ids**: "no snapshot" is detected via `$GS_STATE_DIR/chain-ids` (the lib's `gs_snapshot_diff` returns 0 in that case too, so the script checks the file itself).
- **push-chain**: `--remote`/`--branch` override takes `refs/remotes/<r>/<b>` as base when that ref exists; otherwise the chain is validated against the detected base and a `note:` goes to stderr (a `--branch stable-1` without a tracking ref still prints a usable line instead of failing). `default-wip` read with `git config --type=bool`. Reasons on stderr, one per line: missing ids list the sha7s **and** the SPEC-132 repair line `git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' <base>`; fixups get the `--autosquash` line; duplicate ids are listed with their count. Invalid `--grouping` value is a validation failure (exit 3), not a usage error.
- **diff-budget**: no argument = `HEAD`. `--worktree` = `git diff HEAD --numstat` **plus untracked non-ignored files** (counted as added lines / one file each) so a retro-split measurement sees new files — a slight extension of "staged+unstaged vs HEAD", noted in the header. `--estimate` accepts directories (every file under them). Binary files count as a file with 0 lines. Over-budget verdicts add a one-line hint on stderr; stdout stays exactly `lines=<n> files=<m> budget=<L>/<F> hard=<H>`. Non-numeric config values fall back to defaults with a stderr note. Unknown rev / not a repo → exit 2 (so A1's `git-post.sh` can treat 1/3 as budget verdicts only).
- **install-commit-msg-hook**: `--host` trailing slashes stripped; downloaded/`--from` content must contain `Change-Id` before it is written; identical existing hook is accepted without `--force`; the self-test runs the hook from the repo top (it needs `git var`/`hash-object`), and the `fixup!` assertion is skipped when `gerrit.createChangeId=always`. Failed download never touches the destination. Prints the physical (`pwd -P`) hook path.

## Open issues

- None blocking. `scripts/git-post.sh` (A1) is still the Phase-0 stub in the tree, so the `diff-budget.sh HEAD` integration was exercised only through this WP's tests and the real-repo run above.
- `docs/plans/PROGRESS.md` not updated (not owned by this WP).
