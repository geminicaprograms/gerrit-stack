# Commit messages for a relation chain

One commit = one concern = one Gerrit change. The message is what the reviewer reads
first, and the subject becomes the change's title. The `Change-Id` trailer is added by
the `commit-msg` hook; you never type it.

## Structure

```
<subject>                      ← one line, ≤ 72 chars (aim for ≤ 50), imperative, no trailing period

<body>                         ← why, not what; wrap at 72; blank line after subject

<footers>                      ← only the ones the repo requires (see Footers)
                               ← Change-Id is appended here BY THE HOOK
```

## Styles

**A commitlint config in the repo is the convention.** When the repo has one
(`commitlint.config.*`, `.commitlintrc*`, or a `commitlint` key in `package.json`) and the
`commitlint` command is available, gerrit-stack checks every message against *that* config
(setting `commit-lint`, default `auto`) and has no rules of its own: follow the repo's config
and treat the table below as the fallback for repos without one. Check a draft offline with
`commitlint < <file>` (run from the repo top level); the `Change-Id` footer the hook adds
does not disturb the lint.

Without a commitlint config, select with `git config gerrit-stack.commit-style <conventional|gerrit>` (default
`conventional`). Both share the body and footer rules.

| | `conventional` (default) | `gerrit` |
|---|---|---|
| Subject | `type(scope): subject` — Conventional Commits | `Subsystem: Imperative subject` — optional capitalised prefix, capitalised subject |
| Types | `feat` `fix` `refactor` `test` `docs` `build` `ci` `perf` `chore` | none; the verb carries the type |
| Case | lowercase type and scope; subject lowercase unless it starts with a name | Capitalise the first word after the prefix |
| Breaking | `type(scope)!: subject` + `BREAKING CHANGE:` footer | Say so in the body's first paragraph |
| Examples | `feat(config): read greetingPrefix from plugin config` | `Read greetingPrefix from plugin config` |
| | `fix(rest): return 404 when project is unknown` | `REST: Return 404 when the project is unknown` |
| | `test(ssh): cover greet command with no args` | `Cover the greet command with no arguments` |

Subject rules for both: imperative present tense ("Add", "Fix", "Remove", not
"Added"/"Adds"), starts with a verb after the prefix, no trailing period, ≤ 72
characters, reads as the change's title in a list of five.

## Body

- Blank line after the subject.
- Explain **why**: the problem or current behaviour, then what the change does about
  it, then anything non-obvious (trade-off, alternative rejected, follow-up).
- Wrap at 72 characters. `*` or `-` bullets for several points.
- For a chain step, one sentence on where it sits: "Second of three: the REST endpoint
  that exposes the prefix read in the previous change."
- Over the soft diff budget? Say why in one line ("generated fixture, 90 lines").

```
# good
Before this change the greeting was hard-coded, so every deployment showed the
same text. Read `greetingPrefix` from the plugin config and fall back to the
previous constant when it is unset.

First of three: the REST endpoint and SSH command follow.

# bad
Changed GreetingConfig to read config.        ← what, not why; no context
```

## Footers

Footers are trailers after the body, separated by a blank line. They are **opt-in**:
`git config gerrit-stack.footers Release-Notes,Bug` makes the post-commit check
require those trailers on every commit; without the config, none are required.

| Footer | When | Form |
|---|---|---|
| `Bug: Issue NNN` / `Feature: Issue NNN` | Tracker reference | Before `Release-Notes` |
| `Release-Notes: <text>` or `Release-Notes: skip` | Projects that build release notes from trailers (Gerrit core does) | `skip` for refactors, tests, docs, build; otherwise one user-facing sentence, not the subject verbatim |
| `BREAKING CHANGE: <text>` | Conventional style, incompatible change | After the body |
| `Change-Id` | **Always**, on every commit | **Added by the hook. Never typed, never copied to a different change** (re-using the same change's id when re-creating it is fine — see stack-planner retro-split, Option B) |

Footer order: tracker references → `Release-Notes` → the hook's trailer last.

## Writing the message with git

Multi-line messages go through a file so the body keeps its wrapping:

```
git commit -F /tmp/msg                     # /tmp/msg = subject + blank + body + footers, NO Change-Id line
git commit -m '<subject>' -m '<body>'      # fine for short messages
```

Then confirm the hook did its job:

```
bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh"      # the new row shows one Change-Id
```

Never `-m` together with `--amend` (drops the Change-Id; guard denies). To change a
message after the fact, see `chain-editing.md` section 4.

## One concern per commit

A commit belongs in the chain when a reviewer can say "yes" or "no" to it on its own:

- one layer or one behaviour (config, then endpoint, then command), tests included;
- builds and passes alone (the `--exec` rebase in the skill proves it);
- revertable without touching its neighbours;
- within the diff budget (`diff-budget.sh HEAD`), or justified.

Signs it should be two commits: "and" in the subject, two types fit, a refactor
mixed with a behaviour change, unrelated files.

## Checklist

- [ ] Subject: imperative, verb-first (after prefix), ≤ 72 chars, no trailing period
- [ ] Subject matches the repo's commitlint config when there is one, else the configured style (`conventional` or `gerrit`)
- [ ] Blank line after the subject; body wrapped at 72
- [ ] Body says why, and where the step sits in the chain
- [ ] Only the footers `gerrit-stack.footers` requires, in order
- [ ] No `Change-Id` typed; one present after commit (`chain-status.sh`)
- [ ] One concern; over-budget explained

## Common mistakes

| Mistake | Fix |
|---|---|
| Past tense ("Added option") | Imperative ("Add option") |
| Subject describes the file ("Update GreetingConfig.java") | Describe the behaviour ("Read greetingPrefix from config") |
| Body repeats the diff | Lead with the problem, then the decision |
| `Release-Notes` repeats the subject | Summarise the user impact, or `skip` |
| Footer required by config missing | Post-commit check reports it; `git commit --amend -F` with the trailer added (keep the Change-Id line) |
| Typed a `Change-Id` line | Remove it, commit again; the hook adds the real one |
| Two Change-Id lines after a squash | Keep one, `--amend -F` (guard asks), `chain-status.sh --verify-ids` |
