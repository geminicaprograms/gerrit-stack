# A-plugin — team conventions (plan Part A: A1–A4)

status: done

## Files
- `scripts/lib/gerrit-detect.sh` — `gs_config` layered lookup (clone `git config` > committed `<toplevel>/.gerrit-stack` > default, whitelist `_GS_TEAM_KEYS`); `gs_detect` reads `enabled/remote/branch/host` the same way; new `gs_commitlint_config`, `gs_commitlint_cmd`, `gs_commitlint_active`, `gs_commitlint_check <rev>` (rule lines in `_gs_lint_out`).
- `scripts/session-start.sh` — "Team conventions: …" clause only when active; one sentence when a commitlint config exists but the tool is missing.
- `scripts/git-post.sh` — after `git commit`: commitlint feedback (rule lines + `git commit --amend -F <file>` recipe naming HEAD's Change-Id); fixups skipped.
- `scripts/git-guard.sh` — push to `refs/for` denied when a chain commit fails the lint (`sha7 subject — first rule`); delegates `gerrit-rest.py review` commands to `comment-guard.sh`.
- `scripts/comment-guard.sh` (new) — Conventional Comments guard for MCP `post_review_comment` / `post_draft_comment` and Bash `gerrit-rest.py review`.
- `hooks/hooks.json` — MCP matcher entry → `comment-guard.sh`; second Bash entry `if: Bash(*gerrit-rest.py*)` → `git-guard.sh`; git-guard timeout 10 → 30 s.
- `skills/gerrit-review/SKILL.md`, `references/conventional-comments.md`; `skills/gerrit-stack/SKILL.md`, `references/commit-message.md` — text only.
- `tests/conventions.bats` (new, 24 tests), `tests/lib.bats` (+7 tests), `tests/helpers.bash` (appended `team_config`, `add_commitlint_config`, `stub_commitlint`, `path_without`, `mcp_hook_json`). `tests/hooks.bats` unchanged.
- `demo/seed.sh` — writes `.gerrit-stack` + `commitlint.config.mjs` into the clone after the skeleton rsync (same seed commit).
- `README.md` (config lookup order, two keys, hook + guard rows, "Team conventions"), `CHANGELOG.md` (Unreleased Added/Changed).

## Verification
- `make lint test` → shellcheck + py_compile clean; bats `1..165`, 0 failures (the real-commitlint integration test ran, not skipped; commitlint 21.2.3).
- `claude plugin validate ./ --strict` → Validation passed.
- Manual smoke in a scratch repo with real commitlint: post-commit feedback on "Added rate limit", SessionStart clause, MCP comment deny — as expected.

## Behaviour decisions worth knowing
- A commitlint run counts as a failure only when its output has rule lines (`… [rule-name]`); a crash (broken config, unresolvable preset) is fail-open.
- `gerrit-rest.py review`: every `--comment FILE:LINE:MSG` is checked; `--message` is checked only when the call has no `--comment` (a cover message next to labelled inline comments stays free). `--in-reply-to` exempts the whole call. Text starting with `$`/backtick (shell expansion) cannot be judged and passes.
- Comment guard traces `comment-guard <post_review_comment|post_draft_comment|gerrit-rest-review> <allow|deny>` only when the convention is on; nothing when off.
- MCP reply field verified in gerrit-mcp source (`main.py`): `post_draft_comment(..., in_reply_to)`; `post_review_comment` has none.

## Open issues
- `if: "Bash(*gerrit-rest.py*)"` passes `plugin validate` but was not exercised in a live session; if the pattern does not fire, the REST path is only guarded for commands that also match `Bash(git *)`. The MCP matcher was verified earlier (P2).
- `verify-cmd` and `allow-direct-push` are now readable from the committed file (all documented keys are whitelisted as specified). A cloned repo can therefore supply a command the skill runs and switch off the direct-push deny; consider dropping these two from `_GS_TEAM_KEYS`.
- Not switched (not one-liners / not mine): `scripts/chain-metrics.sh` reads `gerrit-stack.budget.*` with plain `git config`; `scripts/gerrit-rest.py` reads `gerrit-stack.host` with plain `git config`. `push-chain.sh`, `chain-status.sh`, `diff-budget.sh`, `stop-check.sh` already go through `gs_config`/`gs_detect` and pick up the team file with no change.
- `demo/RUN.md` highlight moments (plan A4) are outside my file list; not written.
- The full bats suite takes about 2.5 min on this machine, so `make lint test` exceeds a 120 s tool timeout.
