# Split patterns

How to cut a change into steps that each build, review and revert alone. Every pattern below yields steps a reviewer can approve without reading the next one. Sizes refer to `budget.md`.

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

Build wiring, module registration, a new dependency, a test harness: a horizontal step by nature, so keep it small (typically under 50 lines) and behaviour-free. It goes first so every later step can rely on it. Never let it carry the first feature slice.

### Migration alone

A schema or data migration is its own step, compatible with the code before and after it, ordered before the feature that needs it. It is the step most likely to need an independent revert or a staged rollout, so it must not share a change with behaviour.

### Refactor-before-feature

A refactor the feature needs (extract a method, introduce an interface, move a class) is a `refactor:` step at the bottom of the chain, behaviour-preserving, with existing tests passing unchanged. Mixing it into the feature step hides which lines change behaviour; the reviewer cannot tell intent from mechanics.

### Tests-with-code

Every step carries the tests for what it adds or changes. A separate `test:` step exists only to add coverage to code that already exists. A final "add tests" step means the earlier steps were not reviewable.

### API-then-caller

When both halves are meaningful alone — the API has its own tests and could have other consumers — ship two steps: the API with its tests, then the caller. When the caller is the only consumer and the API cannot be verified without it, the two are one cross-layer vertical slice; justify the size in the reviewer note if it passes 200 lines.

### Feature flag for partial landings

When the chain would exceed depth 5, or steps must land over days, put the new behaviour behind a flag or config key that defaults to off. Each step lands dark; the flag flip is the last, tiny step; removing the flag is a follow-up chain. This keeps every intermediate state shippable.

## Split signals

Split a step when any of these holds:

- The subject needs "and": "add endpoint **and** update UI **and** migrate data".
- It mixes concerns: new feature + migration + cleanup.
- It exceeds 8 files or 200 lines single-layer, 15 files or 500 lines cross-layer.
- Its acceptance criteria test different behaviours.
- Reverting it alone would break something unrelated.

## Merge signals

Merge a step into its neighbour when:

- It cannot be verified on its own ("add import", "add constant").
- It is under 10 lines and has no standalone meaning.
- It is boilerplate that only makes sense next to the following step.
- A reviewer would ask "why is this separate?".

## Worked example: config-driven greeting

Request: a plugin should greet by project. (1) `greeting.prefix` read from plugin config, (2) REST `GET /projects/{name}/demo-plugin~greeting`, (3) SSH `demo-plugin greet <project>`; unit tests for each. Verify command from `git config gerrit-stack.verify-cmd`, else the project's build/test command.

Map: config layer (`GreetingConfig`, `Module` binding), API layer (REST action and its HTTP module; SSH command and its SSH module), tests next to each. No migration, no refactor needed.

Slices by behaviour: "prefix is read with a default" · "greeting is served over REST" · "greeting is served over SSH". The two transports are independent consumers of the same config, so API-then-caller does not apply: each transport is its own vertical slice on top of the config step.

| Step N — type: subject | files | est ±lines | verify cmd | depends on | reviewer note |
|---|---|---|---|---|---|
| Step 1 — feat: read greeting prefix from plugin config | `src/main/java/…/GreetingConfig.java` (new), `src/main/java/…/Module.java`, `src/test/java/…/GreetingConfigTest.java` (new) | ~60 | `bash tools/quick-check.sh` | — | Pure config read with default `"Hello"`; no callers yet — check the default and the missing-key path |
| Step 2 — feat: serve greeting over REST | `src/main/java/…/GetGreeting.java` (new), `src/main/java/…/HttpModule.java`, `src/test/java/…/GetGreetingTest.java` (new) | ~80 | `bash tools/quick-check.sh` | Step 1 | Response is `{"greeting": "<prefix> <project>"}`; check the binding path and 404 for unknown project |
| Step 3 — feat: serve greeting over SSH | `src/main/java/…/GreetCommand.java` (new), `src/main/java/…/SshModule.java`, `src/test/java/…/GreetCommandTest.java` (new) | ~50 | `bash tools/quick-check.sh` | Step 1 | Same `GreetingConfig`; independent of Step 2 but kept in one linear chain — check argument parsing and output format |

Chain summary: 3 changes, ~190 lines total, max step ~80 lines / 3 files, depth 3 ≤ 5, target Gerrit relation chain on origin/master.

Why not one change: ~190 lines and 9 files is within the cross-layer cap but bundles three behaviours; a reviewer who objects to the SSH argument format would block the config and REST work too. Why not five: splitting the module bindings from the classes they bind produces steps that cannot be verified alone (merge signal).
