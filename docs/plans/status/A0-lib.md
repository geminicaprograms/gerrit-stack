# A0-lib — shared libraries + test helpers

status: done (resumed 2026-09-30 after the 09-29 rate-limit kill; tests/helpers.bash and tests/lib.bats already existed, libraries were missing)

## Files

| File | Change |
|---|---|
| `scripts/lib/gerrit-detect.sh` | new — `gs_detect`, `gs_config`, `gs_state_dir`, `gs_trace` (+ private `_gs_*` helpers) |
| `scripts/lib/chain.sh` | new — sources gerrit-detect.sh; `gs_chain_commits`, `gs_change_ids_of`, `gs_subject_of`, `gs_is_fixup`, `gs_diffstat_of`, `gs_snapshot_write`, `gs_snapshot_diff`, `gs_session_mark`, `gs_session_marked`, `gs_parse_git_cmd`, `gs_git_args_have` |
| `tests/helpers.bash` | one addition: `bats_require_minimum_version 1.5.0` (needed by `run --separate-stderr` in `run_hook`; silences bats BW02 for every WP). Fixture API unchanged. |
| `tests/lib.bats` | unchanged — every existing test matched the contract; nothing had to be rewritten |
| `tests/README.md` | new — fixtures + how to run |

## Verification (run from repo root, bash 3.2.57, bats 1.14, shellcheck 0.11, git 2.55, jq 1.8)

```
$ shellcheck -x scripts/lib/*.sh tests/helpers.bash ; echo rc=$?
rc=0
$ bats --print-output-on-failure tests/lib.bats
1..37
ok 1 … ok 37            (37/37, no BW02 warnings)
```
Test 36 exercises every public function under `set -u -o pipefail` + ERR trap **and** under `set -Eeu -o pipefail` in gerrit / plain / non-repo dirs with zero stderr; test 37 checks the libs change neither shell options nor cwd. Extra manual probes (not in the suite): `echo "$(git rev-parse HEAD)"` is seen; an unbalanced `)` inside Claude Code's `git commit -m "$(cat <<'EOF' … EOF\n)"` shape neither corrupts the args nor hides a following `&& git push`; `(cd sub && git status); git log` keeps `.` for the second call; a 3 KB command parses in well under 100 ms.

## Contract decisions / deviations (all documented in the file headers)

- `gs_detect [dir]` uses `git -C "$dir"` instead of `cd`, so the caller's cwd never changes (test 18 requires it; contract wording "cd's to dir first" is satisfied semantically). It **unsets** all `GS_*` variables before detection, so a failed call never leaves stale exports (test 15).
- `GS_HOST` from `.gitreview` is only used when `.gitreview scheme` is `http`/`https` (test 4 expects `""` for `host=localhost` without a scheme); when the remote URL's host equals the `.gitreview` host the URL wins (`/a/` prefix and `/<project>` suffix stripped). `.gitreview` without any remote is not a usable signal (returns 1).
- `gs_change_ids_of` implements JGit footer semantics (last paragraph, every `Change-Id:` line, in order) rather than `git interpret-trailers`, which ignores `See bug 42\nChange-Id: …` paragraphs (test 25).
- `gs_trace` writes **TAB**-separated `script\tverb\tdecision` (task brief + test 21; the plan prose shows spaces).
- `gs_parse_git_cmd` goes beyond the minimum: recurses into `$(…)`, backticks and `(…)` (subshell `cd` does not leak), skips heredoc bodies and `#` comments, strips `if/then/else/elif/while/until/do/!/{/}` keywords and `env/command/exec/time/sudo/doas/nohup/nice/builtin` (+ their `-x` options) and `VAR=x` prefixes, treats `&`, `;`, `|`, `&&`, `||`, newline as separators (`2>&1`, `&>` kept), and consumes the full current list of git global options (`-C`, `-c`, `--git-dir[=]`, `--work-tree[=]`, `--namespace[=]`, `--super-prefix[=]`, `--config-env`, `--attr-source`, `--shell-path`, `--exec-path=`, `--list-cmds=`, `-p/--paginate`, `-P/--no-pager`, `--no-replace-objects`, `--no-lazy-fetch`, `--no-optional-locks`, `--no-advice`, `--bare`, `--(no-)literal-pathspecs`, `--(no)glob-pathspecs`, `--icase-pathspecs`). Args are the raw words joined by single spaces; newlines inside quotes become spaces.
- `gs_git_args_have`: `--opt` also matches `--opt=value`; bundled short flags only match all-alphanumeric clusters (`-am` has `-a`/`-m`; `--no-verify` never has `-n`); stops at `--`.
- `gs_snapshot_diff` is BSD-awk safe (macOS awk rejects multi-line `-v` values — this was the only red test on the first run).

## Notes for A1/A2 (consumers)

- `source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"` from `scripts/*.sh` (chain.sh sources gerrit-detect.sh itself; sourcing twice is harmless).
- All functions return 0 except: `gs_detect` (1 = not a Gerrit repo), `gs_is_fixup`, `gs_session_marked`, `gs_git_args_have` (1 = no), `gs_snapshot_diff` (1 = drift). Wrap those in `if`.
- `gs_config`, `gs_chain_commits`, `gs_change_ids_of` … act on `$GS_TOPLEVEL` when set, else on cwd; `gs_state_dir` creates the per-worktree dir lazily, `gs_detect` never creates it.

## Open issues

None for this WP. Not run here: `make lint` over other WPs' scripts (`scripts/chain-metrics.sh` is A11's) and `claude plugin validate`.
