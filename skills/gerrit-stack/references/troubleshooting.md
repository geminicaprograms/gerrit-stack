# Troubleshooting — symptom → cause → fix

All fixes run from the repo root; `<base>` is the `base` printed by `chain-status.sh`.
Plugin scripts are always `bash "${CLAUDE_PLUGIN_ROOT}/scripts/<name>"`.

| Symptom | Cause | Fix |
|---|---|---|
| `chain-status.sh --preflight` says hook `MISSING`; guard denies `git commit` with "hook missing" | No executable `commit-msg` hook in `$(git rev-parse --git-path hooks)` (worktrees and `core.hooksPath` count) | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-msg-hook.sh"` (add `--host <url>` if the remote is not http(s)); re-run preflight |
| A chain row has no Change-Id; stop hook blocks with "commit without Change-Id"; push denied listing shas | Committed while the hook was missing, or with `--no-verify` | Install the hook, then `git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' <base>`; check `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"` |
| Guard denies `git commit --amend -m` | `-m` replaces the whole message, dropping the Change-Id | `git commit --amend --no-edit`, or `git log -1 --format=%B > /tmp/msg`, edit, `git commit --amend -F /tmp/msg` |
| Gerrit opened a **new** change after a re-push; old change has no new patch set; `--verify-ids` prints `lost:`/`new:` | A rewrite lost the Change-Id (`--amend -m` done outside the guard, a squash that kept the wrong id, a rebase that dropped the trailer) | Copy the old id from Gerrit (`get_change_details` / `gerrit-rest.py detail <n>` → `change_id`), `git interpret-trailers --in-place --if-exists replace --trailer Change-Id=<old id> /tmp/msg` on the saved message, `git commit --amend -F /tmp/msg` (guard asks), re-push the chain; the user abandons the accidental change in the UI. Details: chain-editing.md §8 |
| `! [remote rejected] HEAD -> refs/for/<b> (no new changes)` | Every commit in the chain is already the current patch set of its change | Nothing to do. If you expected a new patch set, the edit did not land in the commit (`git log -1 --stat`), or you pushed before the autosquash |
| Guard denies `git push … refs/heads/<b>` or `git push <remote> <b>` | Direct pushes bypass review; `gerrit-stack.allow-direct-push` is `false` | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/push-chain.sh"` and run the printed `HEAD:refs/for/<b>` line |
| Guard denies `git push --force …refs/for/…` | Force is meaningless for `refs/for`; Gerrit matches by Change-Id | Drop `--force`, push the chain normally |
| Guard denies push: `fixup!`/`squash!` present | Autosquash not run yet | `git -c sequence.editor=true rebase -i --autosquash <base>`, then `--verify-ids` |
| Guard asks "topic will be submitted together if submitWholeTopic is on — intended?" | `%topic=` in the command while `gerrit-stack.grouping` is not `topic` | Answer `n`, remove the option (or set `git config gerrit-stack.grouping topic` + `group-name` if the user really chose topic), re-run `push-chain.sh` |
| Single-repo chain, user surprised that submitting one change submitted all | Topic used on a same-repo chain with `change.submitWholeTopic = true` | Use `none` (or hashtag) next time; for the current chain `set_topic(change_id, "")` per change removes the topic (fallback `gerrit-rest.py topic <n> ""`) |
| Server: `Missing Change-Id in commit message footer` / `missing Change-Id` | Some pushed commit has no Change-Id (the guard only checks `<base>..HEAD`; a wrong `<base>` hides commits) | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --preflight` to confirm remote/branch, then the re-stamp rebase above |
| Server: `not Signed-off-by author/committer/uploader` | Project requires a `Signed-off-by` trailer | `git commit --amend --no-edit --signoff` per commit (`rebase -i --exec 'git commit --amend --no-edit --signoff' <base>`); consider `git config gerrit-stack.footers Signed-off-by` |
| Server: `prohibited by Gerrit: not permitted: create change on refs/for/<b>` / `not permitted: push` | Account lacks `Push` on `refs/for/<b>` (or the branch does not exist) | Check the branch name (`git config gerrit-stack.branch`), then ask a project owner for access; nothing to fix client-side |
| Server: `prohibited by Gerrit: not permitted: create` on `refs/heads/…` | Direct branch push, no `Create reference` permission | Push to `refs/for/<b>` instead |
| Server: `change … closed` / `cannot replace a closed change` | The Change-Id belongs to a merged or abandoned change | New work needs a fresh id: `git log -1 --format=%B`, delete the stale trailer line, `git commit --amend -F` (guard asks), the hook stamps a new one |
| `mcp__plugin_gerrit_gerrit__*` tools absent; SessionStart nags about config | `gerrit@gerrit-mcp` not configured | Run `/gerrit:setup`; until then use `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py"` (auth via `~/.netrc`, host from `gerrit-stack.host` or the remote URL) |
| MCP call fails (timeout, 401, connection refused) | Server down or credentials stale | Same fallback; `gerrit-rest.py --host <url> detail <n>` isolates auth from tool problems; exit 1 = network/HTTP |
| Claude Code refuses `cd <dir> && git …` ("changes directory before running git") | Permission layer under a `Bash(git *)` allowlist | `git -C <dir> …`, or run from the repo root; recipes never `cd` |
| `${CLAUDE_PLUGIN_ROOT}` appears literally in a Bash error | Skill not loaded from the plugin (copied file, or run outside Claude Code) | Load via the plugin (`--plugin-dir` or marketplace install); do not guess the path |
| `push-chain.sh` exits 3 | Validation: empty chain, missing/duplicate Change-Id, fixup present, or grouping without name | Read stderr, fix that item, re-run; it never partially pushes |
| `diff-budget.sh HEAD` exits 1 / post-commit size note | Last commit above the production-line warning (default 400; tests and docs not counted) | One concern: keep it, add a one-line justification to the message (`--amend -F` with a file that keeps the Change-Id line). Mechanical change: `refactor`/`build`/`chore` + "mechanical" in the body. Several concerns: split by concern (`stack-planner`). Never split mechanically |
| `diff-budget.sh HEAD` exits 3 | Over the hard cap the team set (`budget.hard-lines`; unset by default) | Cut along concern boundaries only (`stack-planner` → `retro-split.md`); if none exists, justify and raise the cap with the team |
| `chain-status.sh` shows `0 change(s)` but you just committed | `<base>` is wrong (wrong remote/branch detected) | `git config gerrit-stack.remote <r>` / `gerrit-stack.branch <b>`, `git fetch <r>`, re-run |
| Post-commit feedback: required footer missing | `gerrit-stack.footers` lists a trailer the message lacks | `git log -1 --format=%B > /tmp/msg`, add the trailer above the Change-Id line, `git commit --amend -F /tmp/msg` |

## When the guard says `ask`

That is not an error. It is the confirmation dialog for a push (or an `--amend -F`).
Read the reason it prints — chain length, target ref, grouping — check the pre-flight
checklist, and answer for the user only if they already said `y` in this turn.
