# B-runner — runner rebuilt around case `kind`

status: done

## Files
- `evals/run.py` — rewritten (kinds implement | rework | review, sandbox, isolation, capability, conventions, new metrics, report).
- `tests/test_run.py` — rewritten for the new runner (102 tests; tests of removed features deleted).
- `evals/push-arms.sh` — deleted (obsolete: the runner pushes with `--push-to`).
- `docs/plans/status/B-runner.md` — this file.

## What changed
- Removed: `--scenarios`, `--mcp-plugin`, `graders-rework/` loading, split applicability / production-file logic, the two-stage pipeline, keys `<case>@<scenario>-<variant>`, `stage2` records, `pipelineCostUsd`.
- Kept and reused: `GerritRest`, reviewer step (rena, the only vote is Code-Review -1, never submit), `push_workspace_for_review_ex`, hashtag read-back, sha→Change-Id mapping, `commit_leftovers`, `require_change_id_off` (now for any batch with `--push-to`), `-j`, API-error handling, `sync_origin_with_review`, `MCP_TOOL_RULE`.
- Sandbox: `sandbox_env()` = `PATH HOME USER SHELL TMPDIR LANG TERM` + `LC_*` from the parent, plus runner-set `EVAL_*`, `EVAL_PLUGIN_ROOT`, `GERRIT_HOST`, `GERRIT_STACK_TRACE`, plus `ENABLE_CLAUDEAI_MCP_SERVERS=false`. Used for sessions, the judge and `claude --version`. Fixtures, chain-metrics and commitlint get the parent env minus every `CLAUDE*` variable. stdin is `/dev/null`.
- Model: `--model` > the case's `model:` > `claude-opus-5-5`; always on the command line; recorded as `model` (requested) and `modelReported` (init record).
- Isolation: `isolation.json` per run (`ok`, `unexpected`, `fingerprint`, plus `missing` and a copy of `capability`). A failed check sets the run error `isolation: unexpected …` (or `isolation: missing …` / `isolation: no init record`).
- Variants: `--variants natural,nudged`. Key `<case>` when the case has no nudge line, else `<case>@<variant>`; depends on the case only, not on the flag. Hashtags `bench-<case>-<arm>`, `run-<id>`, `var-<variant>`, `rep-<n>`.
- Kind flows per the contract; one session per run; files land directly in the run dir (no `stage2/`).
- `run_chain_metrics` passes `--verify-cmd` and `--concerns`; a non-zero exit is retried without `--concerns`, then without both. Timeout raised to 1800 s.
- Aggregate: case entries carry `baseCase`, `kind`, `variant`; top level carries `suite`, `evalDir`, `model`, `variants`; `byArm` adds `isolationOk` and `errors`. Report sections: main table (kind / variant / isolation ok / key numbers), `Rework (seeded chain)`, `Reviewer`, `Isolation and capability`.

## Decisions where the contract left room
- `GERRIT_HOST` of the parent shell is not inherited; only the runner sets it (rework / review).
- Isolation also fails when an arm's own plugin or MCP server is missing (the contract says "exactly").
- `change_id_set_preserved` is set equality (seeded == final); `lost_change_ids` and `new_changes_opened` tell the two directions apart.
- A seed push that Gerrit answers with `[UPDATED]` fails the run (Change-Ids shared between runs would mix arms).
- Planted defects in the last message are matched per comment block: a block starts at a line naming one of the target change's files and runs to the next such line or heading. This covers the "line mentioning the basename" rule and also a heading followed by the comment text.
- A last-message line that only repeats a Gerrit draft is not counted twice in `comments_total`.
- `commitlint_available` is true only when the tool is on PATH and the workspace has a commitlint config; otherwise the regex is used.
- implement runs record 0 drafted comments (commit subjects such as `chore: …` in a summary would otherwise count as labelled comments).
- rework: when the runner commits leftovers, chain-metrics is re-run so `builds_alone_pct` covers the pushed chain.

## Verification
- `python3 -m py_compile evals/run.py` — ok.
- `python3 -m unittest tests.test_run -v` — Ran 102 tests, OK (about 32 s).
- `python3 evals/run.py --eval-dir evals/bench-unprompted --case rate-limited-ping --arms with,mcp-only,without --dry-run` — rc 0, 9 commands, every one with `--model claude-opus-5-5`.
- `--eval-dir evals/bench-rework --arms with,mcp-only,without --variants natural,nudged --push-to http://localhost:8080/a/demo-plugin --dry-run --runs 1` — rc 0, 6 plans (natural + nudged × 3 arms).
- `--eval-dir evals/bench-review … --dry-run --runs 1` — rc 0, 3 plans (nudged skipped, key `planted-defects`).
- `--eval-dir evals/bench-split --arms with --dry-run` — rc 0, chain-metrics shown with `--verify-cmd --concerns`.
- Real fixtures of `fix-mid-conflict` and `planted-defects` run locally through `make_workspace` (no Gerrit, no session): 6 and 2 seeded changes, target selection picks change 3 at `DemoPluginConfig.java:55`, `may_change` resolves to the ping-503 change, commitlint passes all subjects.
- No claude session was started and the demo Gerrit was not touched.

## Open issues
- Not verified against a live session: the exact shape of the init record (`plugins[].name`, `skills`, `agents`). The parser accepts strings or `{name: …}` objects and strips `@marketplace`. Run one probe session per arm before a batch and read `isolation.json`.
- Not verified against a live Gerrit: seed push, rena's post, `/changes/<n>/drafts` with the admin netrc credentials. Covered only with fakes.
- `bench-unprompted` cases that still carry `nudges.stage1` get the key `<case>@natural` (e.g. `rate-limited-ping@natural`). Drop the nudge from those case.yaml files if S1 should keep plain keys.
- `evals/README.md` and `CHANGELOG.md` still describe `--scenarios`, `scn-…` hashtags, `graders-rework/` and `push-arms.sh` (not my files).
- Several review keywords are common words (`static`, `constant`, `literal`); a block about the right file that uses one for another reason counts as found.
- Cases without graders score 0.0 as before, so a rework/review case needs at least one grader to pass the threshold.
