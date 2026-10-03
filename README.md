# gerrit-stack

A Claude Code plugin that makes Gerrit relation chains — not GitHub-style pull
requests — the coding agent's default output shape. Every stacking skill on
the market today (gh-stack, the Graphite skill, GitButler's `but agent
setup`, thoughtbot's atomic-commits hook, Cursor's `/split-to-prs`, PostHog's
stacking-prs, Codex's change-size guidance) is PR-shaped: branches and pull
requests. Gerrit works differently — one `Change-Id` per concern, chained by
parent/child commits, uploaded with a single `git push … refs/for/<branch>` —
and the official `gerrit@gerrit-mcp` plugin only covers the review side
(`get_related_changes`, `set_topic`, `post_review_comment`, a
`gerrit-workflow` skill) with no planning, splitting, or push step. No
existing skill makes relation chains the agent's default; gerrit-stack fills
that gap. It plans a chain of small, single-concern commits, gets each
`Change-Id` from the real `commit-msg` hook (never hand-written), prints —
never runs — the push command so a human confirms it, and iterates on review
by amending the right commit and re-pushing the whole chain.

## Requirements

- Claude Code ≥ 2.1 (plugin manifest + hooks JSON format used here)
- `git`, `jq`, `bash` (hook scripts are bash-3.2-compatible, macOS default
  `/bin/bash`)
- `python3` (`gerrit-rest.py`, evals runner, metrics collector — stdlib only)
- The official [`gerrit@gerrit-mcp`](https://gerrit.googlesource.com/gerrit-mcp-server)
  plugin, for the MCP tools the skills call first (`get_related_changes`,
  `set_topic`, `post_review_comment`, …); gerrit-stack falls back to
  `gerrit-rest.py` when a tool isn't available
- [`uv`](https://docs.astral.sh/uv/), used by `gerrit@gerrit-mcp` to run its
  MCP server (`uv run --directory ${CLAUDE_PLUGIN_ROOT} gerrit-mcp-server`)

## Install

```
/plugin marketplace add https://gerrit.googlesource.com/gerrit-mcp-server
/plugin install gerrit@gerrit-mcp
/gerrit:setup
/plugin marketplace add geminicaprograms/gerrit-stack
/plugin install gerrit-stack@gerrit-stack
```

If `gerrit.googlesource.com` is unavailable (Gitiles has occasional outages),
add the GitHub mirror instead of the first command:

```
/plugin marketplace add https://github.com/GerritCodeReview/gerrit-mcp-server
```

`/gerrit:setup` is `gerrit@gerrit-mcp`'s own setup skill — it writes the
Gerrit host(s) and credentials both `gerrit@gerrit-mcp` and gerrit-stack's
`gerrit-rest.py` fallback use. Run it before installing gerrit-stack.

### Local development

```
claude --plugin-dir ~/workspace/open/gerrit-stack
```

Skip the marketplace steps for gerrit-stack itself; `gerrit@gerrit-mcp` still
needs to be installed and configured as above.

## Skills

- **`/gerrit-stack:gerrit-stack`** — the default workflow for any code
  change in a Gerrit-backed repo. Runs a preflight (`chain-status.sh
  --preflight`), hands planning to `stack-planner`, builds the chain one
  commit per concern (Change-Id from the hook, verified after each commit),
  asks the grouping question and an explicit push confirmation before
  printing `push-chain.sh`'s command, then iterates on review by amending
  the right commit, re-verifying Change-Ids after rebase, and re-pushing the
  whole chain.
- **`/gerrit-stack:stack-planner`** — VCS-agnostic planning: splits a
  feature, refactor, or bugfix into an ordered chain of small,
  single-concern steps, each independently buildable, reviewable, and
  revertable within a diff budget (default 50–150 lines / 2–8 files per
  step, hard cap 200 lines single-layer). Used before writing code, or to
  retro-split an existing oversized diff.
- **`/gerrit-stack:gerrit-review`** — reads unresolved review threads on a
  change or chain via `gerrit@gerrit-mcp` (falling back to `gerrit-rest.py`),
  drafts replies and comments in Conventional Comments format, and posts
  them only after the user approves the batch. Never votes labels, never
  submits.

## How it works

### Hooks

| Event | Script | Behavior |
|---|---|---|
| `SessionStart` | `session-start.sh` | Emits `additionalContext` beginning `[gerrit-stack] …` (remote/host/branch/project, commit-msg hook status, chain length vs base) only when the repo is Gerrit-backed; silent (no output) otherwise. |
| `PreToolUse` (matcher `Bash`, `if: Bash(git *)`) | `git-guard.sh` | **deny** = stderr message + exit 2; **ask** = stdout JSON `permissionDecision: "ask"`; else exit 0 silently. Dispatches on `git commit`/`git push`; see the guard table below for every row. |
| `PostToolUse` (matcher `Bash`, `if: Bash(git *)`, fires only when the `Bash` call succeeded) | `git-post.sh` | Feedback only — stderr + exit 2, never blocks. After `commit`: checks exactly one Change-Id, diff budget, required footers, refreshes the chain snapshot. After `rebase`: reports `lost:`/`new:` Change-Ids against the snapshot. After `push`: parses the pushed change numbers or explains `no new changes`. |
| `PreToolUse` (matcher `Bash`, `if: Bash(*gerrit-rest.py*)`) | `git-guard.sh` → `comment-guard.sh` | Only with `comment-style = conventional`: **deny** a `gerrit-rest.py review` whose new comment has no Conventional Comments label. |
| `PreToolUse` (matcher `mcp__plugin_gerrit_gerrit__(post_review_comment\|post_draft_comment)`) | `comment-guard.sh` | Only with `comment-style = conventional`: **deny** an unlabelled top-level comment (stderr lists the labels and an example); replies with `in_reply_to` pass. Silent otherwise. |
| `Stop` | `stop-check.sh` | `{"decision":"block","reason":"…"}` only when this session committed (session marker set) and a non-fixup commit in the chain still lacks a Change-Id; exit 0 otherwise, and always when `stop_hook_active` is true. |

`git-guard.sh` (`PreToolUse`) guard rows:

| Condition | Decision | Reason |
|---|---|---|
| `commit` message already contains a `Change-Id:` trailer | deny | the hook adds it; remove the hand-written trailer |
| `commit --no-verify` / `-n` | deny | skips the `commit-msg` hook, so no Change-Id is added |
| `commit --amend -m` | deny | drops the existing Change-Id → Gerrit opens a **new** change; use `--amend --no-edit` or `-F` with a file that keeps the trailer |
| `commit --amend -F` | ask | confirm the file preserves the current Change-Id |
| `commit` and the `commit-msg` hook is missing | deny | run `install-commit-msg-hook.sh` first |
| `push` to `refs/heads/*` or a bare branch, `gerrit-stack.allow-direct-push` ≠ `true` | deny | push the chain to `refs/for/<branch>` instead |
| `push --force*` to `refs/for/*` | deny | force is meaningless against `refs/for` |
| `push` to `refs/for/*` with a `fixup!`/`squash!` commit present | deny | run an autosquash rebase first |
| `push` to `refs/for/*` with a commit missing a Change-Id | deny | lists the offending SHAs and the repair command (`git -c sequence.editor=true rebase -i --exec 'git commit --amend --no-edit' <base>`) |
| `push` to `refs/for/*` with a commit whose message fails the repo's commitlint config (when the commitlint check is active) | deny | lists `sha7 subject — first failing rule` and the `git commit --amend -F <file>` repair |
| `push` to `refs/for/*`, otherwise | **ask** | "Push N change(s) to `refs/for/<b>` [grouping: none / hashtag `<tag>` / topic `<slug>`]: sha7 subject…" — no warning when a topic is simply absent; if `%topic=` is present but `gerrit-stack.grouping` ≠ `topic`, the reason adds a `submitWholeTopic` warning |
| anything else | exit 0 | no output |

### Scripts

| Script | Purpose | Flags |
|---|---|---|
| `chain-status.sh` | Table of the local chain: `sha7 \| Change-Id(short) \| +/- \| files \| subject`, header `chain: N change(s) on <remote>/<branch> (base <sha7>)`. | `[--preflight] [--json] [--snapshot] [--verify-ids]` — `--preflight` prints detection + hook + MCP status and exits 1 if the hook is missing; `--snapshot` records the Change-Id set; `--verify-ids` compares against it and exits 1 on drift. |
| `push-chain.sh` | Validates the chain (non-empty, exactly one Change-Id per commit, no fixup/squash, grouping+name consistent) and **prints** the single `git push` command; never runs it. Exit 3 with reasons on stderr if validation fails. | `[--wip] [--grouping none\|hashtag\|topic] [--name <x>] [--branch <b>] [--remote <r>]` — flags override config for this print only; the script never writes config. |
| `diff-budget.sh` | Reports `lines=<n> files=<m> budget=<L>/<F> hard=<H>` against `gerrit-stack.budget.*`. | `[<rev>\|--worktree\|--estimate <path>...]` — exit 0 within budget, 1 over the soft budget, 3 over the hard cap. |
| `install-commit-msg-hook.sh` | Installs the real Gerrit `commit-msg` hook into the repo's hooks directory, `chmod +x`, self-tests it (Change-Id added; none added for `fixup!`), prints the installed path. | `[--host <url>] [--from <file>]` — source is `--from <file>` or `curl -fsSL <host>/tools/hooks/commit-msg` (`<host>` may be `file:///…`). |
| `gerrit-rest.py` | MCP fallback: talks to the Gerrit REST API directly (`~/.netrc` auth when present, anonymous otherwise; strips the XSSI prefix). | `[--host URL] <cmd> …` — `related <change>`, `comments <change> [--unresolved]`, `review <change> --message M [--comment FILE:LINE:MSG]... [--in-reply-to ID] [--resolved]` (never sets labels), `rebase-chain <change>`, `topic <change> <topic>`, `hashtags <change> --add T...`, `submitted-together <change>`, `detail <change>`, `query <q>`. |

Two libraries, `scripts/lib/gerrit-detect.sh` and `scripts/lib/chain.sh`, are
`source`d by the hooks and tool scripts above and are not invoked directly.

## Configuration

Every key is looked up in this order: per-clone `git config gerrit-stack.<key>`
→ the committed team file `.gerrit-stack` at the repo top level → the default.
The team file uses git-config syntax with a `[gerrit-stack]` section (set a key
with `git config -f .gerrit-stack gerrit-stack.<key> <value>`); only the keys
in this table are read from it, anything else in the file is ignored.

```ini
# .gerrit-stack — committed, shared by the team
[gerrit-stack]
	commit-lint = auto
	comment-style = conventional
```

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Set `false` to disable gerrit-stack in a repo entirely. |
| `remote` | auto | Which remote is the Gerrit remote; auto-detected when unset. |
| `branch` | auto | Upload target branch; falls back through `.gitreview` `defaultbranch` → `remote.<r>.push` refspec → `origin/HEAD`. |
| `host` | auto | Gerrit base URL for `gerrit-rest.py` and `install-commit-msg-hook.sh`; derived from the remote URL (http/https only) when unset. |
| `grouping` | `none` | `none` \| `hashtag` \| `topic` — see below. |
| `group-name` | unset | The hashtag or topic slug, once chosen. |
| `default-wip` | `false` | Push new chains as work-in-progress (`%wip`) by default. |
| `budget.lines` | `150` | Soft per-commit line budget. |
| `budget.files` | `8` | Soft per-commit file-count budget. |
| `budget.hard-lines` | `200` | Hard per-commit line cap (`diff-budget.sh` exits 3 past this). |
| `commit-style` | `conventional` | Expected commit subject style (guidance for the skill; used when the repo has no commitlint config). |
| `commit-lint` | `auto` | `auto` \| `off`. `auto` checks commit messages with the repo's own commitlint config when one exists and the `commitlint` command resolves; see [Team conventions](#team-conventions). |
| `comment-style` | `none` | `conventional` \| `none`. `conventional` makes Conventional Comments labels mandatory for new review comments; see [Team conventions](#team-conventions). |
| `footers` | unset | Comma-separated list of required trailers, e.g. `Release-Notes`. |
| `allow-direct-push` | `false` | Allow `git push` straight to `refs/heads/*` (otherwise denied). Per-clone `git config` only, never read from `.gerrit-stack`. |
| `verify-cmd` | unset | Command run per-commit to check it builds/tests alone. Per-clone `git config` only, never read from `.gerrit-stack`. |

Grouping semantics: **none** (default) — a relation chain in one repo/branch
is already grouped by parent-child, nothing extra is added. **hashtag**
(`%t=<tag>`) — informational only, searchable via a `hashtag:` query, no
submit semantics. **topic** (`%topic=<slug>`) — use when the chain spans
repos or branches, or atomic submission is wanted; with
`change.submitWholeTopic` enabled, all changes in the topic submit together.

## Team conventions

Two conventions are a team's choice, not the plugin's, so both live in the
repository (`.gerrit-stack`, plus the team's own commitlint config) and both
are off until the team opts in.

**Commit messages: delegated to commitlint.** gerrit-stack ships no commit
message rules of its own. A repo that wants a convention already has a
standard way to state it (a commitlint config), CI can enforce the same file,
and a second rule set in a plugin would only drift from it. The check is
active when `commit-lint` is `auto` (default), a commitlint config exists at
the top level (`commitlint.config.{js,cjs,mjs,ts}`, `.commitlintrc`,
`.commitlintrc.{json,yaml,yml,js,cjs,mjs}`, or a `commitlint` key in
`package.json`) and the tool resolves offline (`commitlint` on `PATH`, else
`node_modules/.bin/commitlint`; never `npx`, nothing is installed, no network).

- After `git commit`, `git-post.sh` pipes the message to commitlint; on a
  violation the agent gets commitlint's rule lines plus the repair recipe
  (`git commit --amend -F <file>`, keeping the existing `Change-Id:` line;
  never `--amend -m`). `fixup!`/`squash!`/`amend!` commits are skipped.
- Before a push to `refs/for/*`, `git-guard.sh` denies the push when a chain
  commit fails the lint and lists `sha7 subject — first failing rule`.
- Config present but tool missing: one sentence in the SessionStart context,
  nothing is blocked. A commitlint run that crashes (broken config, missing
  preset) is ignored as well: only named rule violations count.
- Gerrit's `commit-msg` hook slot is untouched; the `Change-Id` footer passes
  `@commitlint/config-conventional`.

**Review comments: Conventional Comments, optional.** With
`comment-style = conventional`, a new top-level comment must start with a
label (`praise`, `nitpick`, `suggestion`, `issue`, `todo`, `question`,
`thought`, `chore`, `note`), optionally decorated (`issue (blocking): …`).
`comment-guard.sh` denies unlabelled comments on the Gerrit MCP tools
`post_review_comment` / `post_draft_comment` and on
`gerrit-rest.py review --comment FILE:LINE:MSG` (a review that carries only
`--message` is checked on that message). Replies (`in_reply_to` /
`--in-reply-to`) stay free-form and `publish_drafts` is not guarded. With
`none` (default) there is no guard and the `gerrit-review` skill drafts plain
comments.

The SessionStart context names whichever of the two is active.

## Demo

Bring up a local docker Gerrit 3.14 and seed it with an admin/reviewer
account, a `demo-plugin` project, and a small Gerrit-plugin skeleton:

```
make demo-up demo-seed
```

The skeleton's Bazel build runs **in-tree** against a real Gerrit
`stable-3.14` checkout (there's no `stable-3.14` branch of
`cookbook-plugin`): `demo/gerrit-tree.sh <path-to-gerrit-3.14-checkout>`
symlinks the skeleton into that checkout's `plugins/` directory and writes a
cache-backed `user.bazelrc`, then `demo/warm-bazel.sh` builds and tests it.
`demo/skeleton/tools/quick-check.sh` is a `javac`-only fallback for a faster
per-commit `verify-cmd`.

See `demo/RUN.md` for the full seeded walkthrough (stage script, timings,
fallback triggers).

## Tests & evals

```
make check       # validate + lint + bats + python unittest
```

`claude plugin eval` (Claude Code ≥ 2.1.269, pass `--trust-plugin` in CI) is
the reference runner. Two reasons `evals/run.py` exists as well: on macOS with
Docker Desktop the official sandbox refuses Bash-granting cases (symlinks under
`~/.docker`), and the benchmark needs a third arm (gerrit-mcp only) that the
official with/without ablation cannot express. `run.py` is a stdlib runner over
the same case format (`prompt.md` frontmatter + `graders/*.md`) that drives
`claude -p --plugin-dir … --output-format stream-json` directly, so the cases
stay 100 % compatible with the official runner:

```
make eval         # python3 evals/run.py --runs 2 --threshold 0.8
make bench         # python3 evals/run.py --bench --ablation --runs 3 (add --push-to http://localhost:8080/a/demo-plugin to review each arm in the demo Gerrit)
```

The benchmark run (arm A vanilla, arm B vanilla + `gerrit@gerrit-mcp`, arm C
+ gerrit-stack) is reported in `docs/benchmark.md`.

A second benchmark extends this past "chain pushed" through a scripted
review round — reviewer comment, rework, re-push, read-back — to measure
how cheaply each arm gets a change re-reviewable and whether the guard holds
under guardrail-pressure nudges; see "Rework + guardrail pipelines" in
`evals/README.md`. No results are published: the first rework run (2026-10-01) was withdrawn, see `docs/plans/PROGRESS.md`.

## Troubleshooting

- **Hook missing / guard denies every commit** — run `bash
  "${CLAUDE_PLUGIN_ROOT}/scripts/install-commit-msg-hook.sh"` (or invoke the
  `gerrit-stack` skill, which runs it for you) to install the real
  `commit-msg` hook, then commit again.
- **`push-chain.sh` prints a command but the push says `no new changes`** —
  the tip commit's Change-Id already matches the change on the server and
  nothing changed since the last patch set; re-check `chain-status.sh` for
  drift, or amend the intended commit first.
- **Lost the Change-Id after `git commit --amend -m "…"`** — the guard
  should have denied this; if it slipped through (e.g. `--no-verify`
  bypass), the amended commit opened a new Gerrit change. Recover the
  original trailer from the previous patch set (or `git reflog`) and
  `git commit --amend -F <file-with-trailer>`.
- **Guard denies a direct push to `refs/heads/*`** — this is intentional;
  push chains to `refs/for/<branch>` instead, or set
  `git config gerrit-stack.allow-direct-push true` if the repo genuinely
  needs direct pushes.
- **MCP not configured** — `gerrit@gerrit-mcp`'s `SessionStart` hook keeps
  nagging until it is; run `/gerrit:setup`.

## Design principles

- Scripts never run `git commit`/`git push` themselves — the agent runs raw
  `git …` so the `PreToolUse` guard fires; `push-chain.sh` only composes and
  prints the command, and the push guard always asks for confirmation even
  when `git push` is allow-listed.
- Grouping is optional and asked, never assumed; the choice is persisted in
  `git config gerrit-stack.grouping`/`gerrit-stack.group-name` and reused on
  re-push.
- Recipes always start with a literal `git` (e.g. `git -c
  sequence.editor=true rebase …`, never `ENV=x git …`) so a single `"if":
  "Bash(git *)"` hook filter matches, including `cd x && git …`.
- One dispatcher script per hook event (`git-guard.sh`, `git-post.sh`),
  routing internally on the parsed git verb.
- Gerrit detection is layered and fails open (`gerrit-stack.enabled=false` →
  off; explicit `remote` config; `.gitreview`; `remote.*.push=HEAD:refs/for/`;
  remote URL pattern; else no-op) — `trap 'exit 0' ERR`, no network access.
- Hook state lives in `.git/gerrit-stack/`: a Change-Id snapshot of the chain
  and a per-session "committed" marker.
- gerrit-stack never duplicates the `gerrit-workflow` skill's Change-Id and
  patchset semantics — it links to it instead.
- Default commit style is Conventional Commits; a `Release-Notes` footer (or
  any other trailer) is opt-in via `git config gerrit-stack.footers`.
- The three skills are symlinked into `~/claude-skills-hub/skills/` for local
  development, not into `~/.claude/skills/` — a plugin install already
  discovers them there, and adding both would register each skill twice.

## License

Apache-2.0 — see [LICENSE](LICENSE).

| Gerrit MCP tools fail with `curl: (35) … tlsv1 alert protocol version` against a plain-HTTP Gerrit (e.g. the local demo) | The official server rewrites `http://` to `https://` for every host | Apply `demo/patch-gerrit-mcp.sh` (upstream fix: [change 635805](https://gerrit-review.googlesource.com/c/gerrit-mcp-server/+/635805)), or let the skills use the REST fallback (`gerrit-rest.py`, which honours `http://`) |
| MCP tools are named `mcp__plugin_gerrit_gerrit__<tool>` | Plugin-scoped naming in Claude Code (`mcp__plugin_<plugin>_<server>__<tool>`) | Grant/allow them with that prefix, e.g. `--allowedTools mcp__plugin_gerrit_gerrit__get_related_changes` |
