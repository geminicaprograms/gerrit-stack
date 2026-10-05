# Plan template

Emit exactly this structure. Replace every `<…>`; keep the headings, the column order and the `Chain summary:` line; the approval question is the last line of the message. The first column of each row starts with `Step N — ` so a reader (or a grader) can find `Step 1` verbatim.

```markdown
## Plan: <goal in one line>

**Base:** <remote>/<branch> at <sha7> · **Target:** <Gerrit relation chain | stacked PRs> · **Verify:** `<verify cmd>` · **Size warning:** <L> production lines<; hard cap <H> if the team set one>

| Step N — <type>: <subject> | files | est prod / test lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — <type>: <subject> | `<path>` (new), `<path>` | ~<p> / ~<t> | `<cmd>` | — | <what to look at; one-line size justification if over the warning; "mechanical: <how>" for a mechanical step> |
| Step 2 — <type>: <subject> | `<path>` | ~<p> / ~<t> | `<cmd>` | Step 1 | <…> |

Chain summary: <N> changes, ~<P> production / ~<T> test lines total, largest change ~<M> production lines, depth <N>, target <Gerrit relation chain on <remote>/<branch> | stacked PRs>.

Estimates: <from `diff-budget.sh --estimate`; list any adjusted figures and why, or "none adjusted">

Out of scope / follow-ups: <bullets, or "none">

Approve this <N>-step plan? Reply **yes** to start Step 1, or name the step to change. No file is edited until you answer.
```

Rules for filling it in:

- `<type>` is a Conventional Commit type (`feat`, `fix`, `refactor`, `build`, `test`, `docs`, `chore`); one per step; the subject has no "and".
- *files* lists every path the step touches; mark new files `(new)`. For a retro-split, list hunks as `path (hunks: 2, 4)` when a file is shared between concerns.
- *est prod / test lines* is insertions + deletions in production files / in test files (docs and lock files are left out).
- *verify cmd* must pass with only this and earlier steps present.
- *depends on* names earlier steps or `—`.
- Every step builds, is tested and is used on its own: no class without a caller, no scaffolding that only a later step explains, no "part 1/2".
- A step over the production-line warning (default 400) carries a one-line justification in *reviewer note*; it is not split for size alone. A broad mechanical change (migration, rename, formatter, codemod) is one step typed `refactor`/`build`/`chore`, noted "mechanical", before any behaviour change.

## Filled example

```markdown
## Plan: config-driven greeting over REST and SSH

**Base:** origin/master at 3f2a9c1 · **Target:** Gerrit relation chain · **Verify:** `bash tools/quick-check.sh` · **Size warning:** 400 production lines

| Step N — <type>: <subject> | files | est prod / test lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — feat: serve a configurable greeting over REST | `src/main/java/com/example/demo/GreetingConfig.java` (new), `src/main/java/com/example/demo/Module.java`, `src/main/java/com/example/demo/GetGreeting.java` (new), `src/main/java/com/example/demo/HttpModule.java`, `src/test/java/com/example/demo/GreetingConfigTest.java` (new), `src/test/java/com/example/demo/GetGreetingTest.java` (new) | ~90 / ~50 | `bash tools/quick-check.sh` | — | Prefix from plugin config, default `"Hello"`; `GET /projects/{name}/demo-plugin~greeting` returns `{"greeting": "<prefix> <project>"}`. Check the missing-key path and the 404. |
| Step 2 — feat: serve the greeting over SSH | `src/main/java/com/example/demo/GreetCommand.java` (new), `src/main/java/com/example/demo/SshModule.java`, `src/test/java/com/example/demo/GreetCommandTest.java` (new) | ~35 / ~15 | `bash tools/quick-check.sh` | Step 1 | `demo-plugin greet <project>`; same config as REST; check argument parsing. |

Chain summary: 2 changes, ~125 production / ~65 test lines total, largest change ~90 production lines, depth 2, target Gerrit relation chain on origin/master.

Estimates: `--estimate` on the three existing module files gave prod=58; new files estimated by hand (config ~35, REST ~45, SSH ~30, tests ~15–30 each).

Out of scope / follow-ups: none

Approve this 2-step plan? Reply **yes** to start Step 1, or name the step to change. No file is edited until you answer.
```
