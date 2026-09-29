# Plan template

Emit exactly this structure. Replace every `<…>`; keep the headings, the column order and the `Chain summary:` line; the approval question is the last line of the message. The first column of each row starts with `Step N — ` so a reader (or a grader) can find `Step 1` verbatim.

```markdown
## Plan: <goal in one line>

**Base:** <remote>/<branch> at <sha7> · **Target:** <Gerrit relation chain | stacked PRs> · **Verify:** `<verify cmd>` · **Budget:** <L> lines / <F> files (hard <H>)

| Step N — <type>: <subject> | files | est ±lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — <type>: <subject> | `<path>` (new), `<path>` | ~<n> | `<cmd>` | — | <what to look at; budget justification if over> |
| Step 2 — <type>: <subject> | `<path>` | ~<n> | `<cmd>` | Step 1 | <…> |

Chain summary: <N> changes, ~<T> lines total, max step ~<M> lines / <F> files, depth <N> ≤ 5, target <Gerrit relation chain on <remote>/<branch> | stacked PRs>.

Estimates: <from `diff-budget.sh --estimate`; list any adjusted figures and why, or "none adjusted">

Out of scope / follow-ups: <bullets, or "none">

Approve this <N>-step plan? Reply **yes** to start Step 1, or name the step to change. No file is edited until you answer.
```

Rules for filling it in:

- `<type>` is a Conventional Commit type (`feat`, `fix`, `refactor`, `build`, `test`, `docs`, `chore`); one per step; the subject has no "and".
- *files* lists every path the step touches; mark new files `(new)`. For a retro-split, list hunks as `path (hunks: 2, 4)` when a file is shared between concerns.
- *est ±lines* is insertions + deletions including tests.
- *verify cmd* must pass with only this and earlier steps present.
- *depends on* names earlier steps or `—`.
- A step over 150 lines or 8 files carries its justification in *reviewer note*; nothing is over 200 lines single-layer or 500 cross-layer.

## Filled example

```markdown
## Plan: config-driven greeting over REST and SSH

**Base:** origin/master at 3f2a9c1 · **Target:** Gerrit relation chain · **Verify:** `bash tools/quick-check.sh` · **Budget:** 150 lines / 8 files (hard 200)

| Step N — <type>: <subject> | files | est ±lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — feat: read greeting prefix from plugin config | `src/main/java/com/example/demo/GreetingConfig.java` (new), `src/main/java/com/example/demo/Module.java`, `src/test/java/com/example/demo/GreetingConfigTest.java` (new) | ~60 | `bash tools/quick-check.sh` | — | Config read with default `"Hello"`; no callers yet. Check the missing-key path. |
| Step 2 — feat: serve greeting over REST | `src/main/java/com/example/demo/GetGreeting.java` (new), `src/main/java/com/example/demo/HttpModule.java`, `src/test/java/com/example/demo/GetGreetingTest.java` (new) | ~80 | `bash tools/quick-check.sh` | Step 1 | `GET /projects/{name}/demo-plugin~greeting` returns `{"greeting": "<prefix> <project>"}`; check the 404 path. |
| Step 3 — feat: serve greeting over SSH | `src/main/java/com/example/demo/GreetCommand.java` (new), `src/main/java/com/example/demo/SshModule.java`, `src/test/java/com/example/demo/GreetCommandTest.java` (new) | ~50 | `bash tools/quick-check.sh` | Step 1 | `demo-plugin greet <project>`; same config as REST; check argument parsing. |

Chain summary: 3 changes, ~190 lines total, max step ~80 lines / 3 files, depth 3 ≤ 5, target Gerrit relation chain on origin/master.

Estimates: `--estimate` on the two existing module files gave lines=41 files=2; new files estimated by hand (config ~35, REST ~55, SSH ~30, tests ~20 each).

Out of scope / follow-ups: none

Approve this 3-step plan? Reply **yes** to start Step 1, or name the step to change. No file is edited until you answer.
```
