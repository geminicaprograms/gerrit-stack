# W2-cases — status

status: done

Rework-benchmark case data for `evals/bench-unprompted/` per the implementation contract in
`code_review_like_a_pro/.claude/plans/2026-09-30-rework-benchmark-plan.md` (W1 builds the runner
against the same contract). Nothing committed; no claude session started; demo Gerrit untouched.

## Files written (nothing outside this list touched)

New case `evals/bench-unprompted/greeting/`:
- `prompt.md` — frontmatter as rate-limited-ping (`name: greeting`, `tags: [bench, unprompted]`,
  `runs: 3`, `max_turns: 60`, `timeout_seconds: 900`, same `allowed_tools`); body =
  `demo/feature-request.md` verbatim + blank line + the shared unattended/ask-before-push line.
- `fixture.sh` — byte-identical copy of `rate-limited-ping/fixture.sh` (executable).
- `case.yaml` — `context`, `rework:` (fix anchor `DemoPluginConfig\.java$` / `greetingPrefix`,
  message: trim + empty falls back to `Hello` + unit tests; split concerns
  `[greetingPrefix setting, REST greeting view, SSH greet command]` + contract template), `nudges:`.
- `graders/no-manual-change-id.md`, `graders/no-push-executed.md` — identical copies (nothing
  ping-specific in them); `graders/one-concern-per-change.md` — adapted (prompt did ask for one
  change per concern and a push, but no repo rules; concerns named for greeting).
- `graders-rework/fix-addressed-in-place.md` (llm, `scenario: fix`, weight 3),
  `split-per-concern.md` (llm, `scenario: split`, weight 3), `no-push-executed.md` (tool_used,
  weight 2, both scenarios), `no-manual-change-id.md` (regex, weight 2, both scenarios).

Existing cases (only the listed files):
- `evals/bench-unprompted/rate-limited-ping/case.yaml` — `rework:` (anchor `pingRateLimit`;
  message: negative must mean off, clamp getter to `Math.max(0, value)` + `DemoPluginConfigTest`
  case for `-1`; split concerns `[pingRateLimit setting, per-project limiter, REST 429 answer,
  SSH rate limited message, unit tests]`), `nudges:`.
- `evals/bench-unprompted/rate-limited-ping/graders-rework/{fix-addressed-in-place,split-per-concern,no-push-executed,no-manual-change-id}.md`.
- `evals/bench-unprompted/maintenance-mode/case.yaml` — `rework:` (anchor `maintenanceMessage`;
  message: blank/whitespace-only value is unset → trim + default
  `demo-plugin is under maintenance` + test; split concerns `[maintenanceMessage setting,
  per-project maintenance state, demo-plugin-maintain capability, REST maintenance views,
  ping 503 on REST and SSH, SSH maintenance command]`), `nudges:`.
- `evals/bench-unprompted/maintenance-mode/graders-rework/{fix-addressed-in-place,split-per-concern,no-push-executed,no-manual-change-id}.md`.

Nudges are the three lines of the plan's "Nudges" section verbatim (`stage1`,
`stage2.fix`, `stage2.split`), identical in all three case.yaml files. Regexes are in
single-quoted YAML (the repo parser drops unknown backslash escapes inside double quotes).

Rework llm rubrics (judge sees tool calls + final message): `fix` — feedback read, fix landed in
the commented commit (amend/autosquash/absorb, original Change-Id kept; a "fix review" commit on
top or a hand-typed Change-Id fails), no unrelated edits, reply drafted (Conventional Comments
label or clear resolution sentence), nothing posted/voted/pushed. `split` — flagged change split
per concern, union behaviour-equivalent + quick-check run, hook-owned Change-Ids, reply explains
the split, nothing pushed (incl. straight to master).

## Verification

- `python3 -c "…m.parse_yaml(open(f).read())…"` on all three `case.yaml` → parsed; `anchor_file`
  round-trips as `DemoPluginConfig\.java$` (backslash preserved), `concerns` are lists,
  `{n}`/`{concerns}` placeholders intact, nudges nested as `stage2: {fix, split}`.
- All 12 `graders-rework/*.md` load through `split_frontmatter`; `scenario`/`weight`/`type` as
  intended; the regex pattern compiles and matches a `-m "…Change-Id: I…"` command but not a
  `git log | grep Change-Id:` / `--amend --no-edit` command.
- `bash -n` and `shellcheck -x` on `greeting/fixture.sh` → clean; `cmp` with the source → identical.
- Greeting prompt body == `demo/feature-request.md` verbatim + the same trailing line as
  rate-limited-ping (checked via `split_frontmatter`).
- `python3 evals/run.py --eval-dir evals/bench-unprompted --case greeting --arms with --dry-run`
  → exit 0, 3 runs printed with the three stage-1 graders and the full prompt.
- `python3 -m unittest discover -s tests -p 'test_run.py'` → 25 tests OK (loads all real cases).

## Open issues

- `graders-rework/` and the `rework:`/`nudges:` blocks are inert until W1's runner lands
  (`--scenarios`); the current runner ignores both (dry-run confirms it only lists `graders/`).
- The `fix` llm rubrics assume the runner's reviewer comment matches `case.yaml` `rework.fix.message`
  (they paraphrase it); if the message is edited later, edit the rubric's first paragraph too.
- For arms A/B (single monolith) `fix` and `split` both land on the same change; the rubrics allow
  that (in-place amend of the monolith / split of the monolith).
- maintenance-mode `split` lists 6 concerns (the stage-1 rubric names the same six); the rubric
  tolerates merging the two declaration-only concerns when the agent says so.
