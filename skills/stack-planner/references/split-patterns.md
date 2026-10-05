# Split patterns

How to cut work into changes that each build, are tested, and make sense alone. The unit is the concern, not a line count: every pattern below yields steps a reviewer can approve without reading the next one, and none of them produces a fragment that only the next step explains. Size is a reviewer-load warning (`budget.md`), never a reason to cut a concern.

## Patterns

### Vertical slice (the default)

One step = one user-visible behaviour across whatever layers it needs. A reviewer verifies it end to end.

```
# Horizontal: nothing is verifiable until step 4
Step 3 — feat: add all REST endpoints (GET, PUT, DELETE)
Step 4 — feat: add settings UI

# Vertical: each step is a complete behaviour with its test
Step 3 — feat: save token (PUT endpoint + save button)
Step 4 — feat: show stored token (GET endpoint + load on open)
Step 5 — feat: delete token (DELETE endpoint + delete button)
```

### Infra-first

Build wiring, module registration, a new dependency, a test harness: a horizontal step by nature, so keep it behaviour-free and only as a step of its own when it builds and is verifiable alone (the build passes, the harness runs an existing test). Wiring that only makes sense with the first feature belongs in that feature's step.

### Broad mechanical change

A library migration, rename, API move, formatter run or codemod is **one change**, even when it is large or touches many files. It goes first; behaviour changes go on top in their own steps. Split it by module only when each module's part builds and is complete on its own (the module is fully migrated, the build stays green). Type `refactor`/`build`/`chore`, and say "mechanical" in the body with how it was produced (tool + command, or the rename rule).

```
# Wrong: arbitrary batches and a behaviour change mixed in
Step 1 — refactor: migrate files A–M to the v5 client
Step 2 — refactor: migrate files N–Z to the v5 client and add retries

# Right: one mechanical change, behaviour on top
Step 1 — build: migrate to the v5 HTTP client      (mechanical: vendor codemod, 2,300 lines / 140 files)
Step 2 — feat: retry idempotent requests on 503
```

### Migration alone

A schema or data migration is its own step, compatible with the code before and after it, ordered before the feature that needs it. It is the step most likely to need an independent revert or a staged rollout, so it must not share a change with behaviour.

### Refactor-before-feature

A refactor the feature needs (extract a method, introduce an interface, move a class) is a `refactor:` step at the bottom of the chain, behaviour-preserving, with existing tests passing unchanged — and it must be meaningful alone: the extracted method has its existing callers, the interface its existing implementation. Mixing it into the feature step hides which lines change behaviour.

### Tests-with-code

Every step carries the tests for what it adds or changes. A separate `test:` step exists only to add coverage to code that already exists. A final "add tests" step means the earlier steps were not reviewable.

### API-then-caller

When both halves are meaningful alone — the API has its own tests and other consumers, or is a public surface reviewed on its own — ship two steps: the API with its tests, then the caller. When the caller is the only consumer, the API alone is a class without a caller: keep them as one cross-layer vertical slice, whatever its size, and justify the size in one line if it passes the warning.

### Feature flag for partial landings

When the chain would exceed depth 5, or steps must land over days, put the new behaviour behind a flag or config key that defaults to off. Each step lands dark but complete (built, tested, reachable with the flag on); the flag flip is the last, tiny step; removing the flag is a follow-up chain. This keeps every intermediate state shippable without fragments.

## Split signals

Split a step when any of these holds:

- The subject needs "and": "add endpoint **and** update UI **and** migrate data".
- It mixes concerns: new feature + migration + cleanup, or a mechanical change + behaviour.
- Its acceptance criteria test different behaviours.
- Reverting it alone would break something unrelated.

Size alone is **not** a split signal. Over the production-line warning (default 400) with one concern: keep it, justify it in one line.

## Merge signals (anti-fragmentation)

Merge a step into its neighbour when:

- It adds a class, function or endpoint with no caller in the same step (tests aside).
- It is scaffolding only the next step explains (an interface with no implementation, a config key nothing reads).
- It is "part 1" of something, or half of one file's change.
- It cannot be verified on its own ("add import", "add constant"), or is under ~10 lines with no standalone meaning.
- A reviewer would ask "why is this separate?" or the reviewer note says "used in the next change".

## Worked example: config-driven greeting

Request: a plugin should greet by project. (1) `greeting.prefix` read from plugin config, (2) REST `GET /projects/{name}/demo-plugin~greeting`, (3) SSH `demo-plugin greet <project>`; unit tests for each. Verify command from `git config gerrit-stack.verify-cmd`, else the project's build/test command.

Map: config layer (`GreetingConfig`, `Module` binding), API layer (REST action and its HTTP module; SSH command and its SSH module), tests next to each. No migration, no refactor needed.

Concerns by behaviour: "greeting is served over REST" · "greeting is served over SSH". The config read on its own would be a class without a caller, so it travels with the first transport that uses it; the second transport reuses it.

| Step N — type: subject | files | est prod / test lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — feat: serve a configurable greeting over REST | `src/main/java/…/GreetingConfig.java` (new), `src/main/java/…/Module.java`, `src/main/java/…/GetGreeting.java` (new), `src/main/java/…/HttpModule.java`, `src/test/java/…/GreetingConfigTest.java` (new), `src/test/java/…/GetGreetingTest.java` (new) | ~90 / ~50 | `bash tools/quick-check.sh` | — | Prefix from plugin config with default `"Hello"`, served as `{"greeting": "<prefix> <project>"}`; check the missing-key path and 404 for an unknown project |
| Step 2 — feat: serve the greeting over SSH | `src/main/java/…/GreetCommand.java` (new), `src/main/java/…/SshModule.java`, `src/test/java/…/GreetCommandTest.java` (new) | ~35 / ~15 | `bash tools/quick-check.sh` | Step 1 | Same `GreetingConfig`; check argument parsing and output format |

Chain summary: 2 changes, ~125 production / ~65 test lines total, largest change ~90 production lines, depth 2 ≤ 5, target Gerrit relation chain on origin/master.

Why not one change: it bundles two behaviours; a reviewer who objects to the SSH argument format would block the REST work too. Why not three (config, REST, SSH): the config step would be a class with no caller, approved on faith (merge signal).
