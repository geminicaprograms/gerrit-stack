# gerrit-stack evals

Seven behavioural cases (`evals/<case>/`) in the official `claude plugin eval`
case format, plus a stdlib runner (`evals/run.py`) that executes the same cases
locally with `claude -p`, git hooks on, and the three benchmark arms.

| case | what it checks |
|---|---|
| `trigger-gerrit-stack` | a plain "implement this" in a Gerrit repo invokes `gerrit-stack` and plans before editing |
| `trigger-stack-planner` | "break this down" invokes `stack-planner`, emits `Step 1…N`, edits nothing, asks for approval |
| `plan-before-code` | a `Skill` call precedes the first `Edit` / `Write` |
| `push-requires-confirm` | chain is built, grouping (none/hashtag/topic) and y/n are asked, no `git push` is executed, no `%topic=` |
| `no-manual-change-id` | adversarial "write the Change-Id yourself" is refused; no Bash command composes a `Change-Id:` trailer |
| `split-over-budget` | a 600-line, 3-concern dirty worktree becomes ≥ 2 single-concern commits, never `--no-verify` |
| `review-reply-conventional` | threads are read through the REST fallback (canned stub), replies are drafted in Conventional Comments, nothing is posted or voted before approval |

## Running locally

```sh
make eval                                            # all cases, 2 runs, threshold 0.8, arm "with"
python3 evals/run.py --case 'trigger-*' --runs 1     # a subset, one run each
python3 evals/run.py --case push-requires-confirm --keep -v   # keep the workspace, verbose
python3 evals/run.py --dry-run --case '*'            # print the claude commands only
make bench                                           # A11 bench cases, --ablation (with vs without), 3 runs
```

`run.py --help` lists every option: `--eval-dir`, `--bench`, `--case GLOB` (repeatable),
`--runs N`, `--arms with,without,mcp-only`, `--ablation`, `--threshold`, `--max-cost-usd`,
`--model`, `--judge-model` (default `haiku`), `--judge-votes`, `--plugin-dir`, `--mcp-plugin`,
`--keep`, `--dry-run`, `--json PATH`, `--out-dir`, `-v`.

Exit codes: `0` overall score ≥ threshold · `1` below threshold · `2` partial (the
`--max-cost-usd` ceiling stopped the suite; everything finished so far is still written).

### Arms

| arm | `--plugin-dir` | gerrit-mcp | meaning |
|---|---|---|---|
| `with` (default) | yes | loaded via `--plugin-dir` | the plugin under test + the official Gerrit MCP |
| `mcp-only` | no | loaded via `--plugin-dir` | MCP tools but none of our skills/hooks |
| `without` | no | not loaded | vanilla Claude Code |

`--ablation` = `with,without`. Per case the runner reports the score of each arm and
`delta` = with − without (or with − mcp-only when `without` was not run).

Every run passes `--setting-sources project,local`, so the user's own `~/.claude/CLAUDE.md`,
hooks and user-scope plugins never reach the agent under test; the official gerrit-mcp plugin is
loaded explicitly with `--plugin-dir <its cache dir>` (`--mcp-plugin-dir` overrides the
auto-detected `~/.claude/plugins/cache/gerrit-mcp/gerrit/<hash>`). Nothing global is toggled.

### What one run does

1. `tempfile.mkdtemp()` → `bash <case>/fixture.sh` with cwd = the empty workspace and env
   `EVAL_*` (from `prompt.md` frontmatter) + `EVAL_PLUGIN_ROOT`. Fixtures source
   `evals/fixtures/scaffold-common.sh` (`make_gerrit_workspace [--chain N] [--project-kind sh|java]`,
   `make_dirty_diff <lines>`), which builds a Gerrit-looking repo: `master`, bare `../remote.git`
   as `origin` with `remote.origin.push=HEAD:refs/for/master`, `.gitreview`, the real
   `tests/fixtures/commit-msg` hook, a tiny project, one pushed initial commit.
2. `claude -p --output-format stream-json --verbose --max-turns N --allowedTools <tools> [--model M]
   [--plugin-dir <repo>] "<prompt>"` in that workspace, with `GERRIT_STACK_TRACE=<run>/hook-trace.log`
   so the plugin hooks log every decision. Timeouts kill the whole process group.
3. After the run: `scripts/chain-metrics.sh --json --hook-trace … <workspace>` →
   `chain-metrics.json` (skipped silently if the script is missing or fails), the REST stub is
   killed via `<workspace>/.stub-pid`, the workspace is deleted unless `--keep`.
4. Graders run over the parsed trace, the workspace files and (for `llm`) a judge call
   `claude -p --model <judge> --max-turns 1` with a fixed prompt whose last line must be `PASS`
   or `FAIL` (`--judge-votes N` = majority). Score = weighted pass fraction; a run passes at
   ≥ threshold. `baseline` graders are skipped with a warning.

## Why a standalone runner

`claude plugin eval` is the reference runner and the case files stay 100 % compatible with it,
but on this machine (Phase 0, probe b) any case that grants `Bash` aborts:

> the Docker (~/.docker, DOCKER_CONFIG) credential store on this machine holds a symbolic link
> inside it, so the Bash sandbox cannot reliably exclude it

(cause: Docker Desktop's `~/.docker/cli-plugins/*` symlinks; pointing `DOCKER_CONFIG` at a plain
directory did not help). Two more reasons: the official sandbox disables git hooks
(`GIT_CONFIG_COUNT` override of `core.hooksPath`), which defeats a plugin whose whole point is the
commit-msg hook, and it cannot express the `mcp-only` arm or collect per-run workspace metrics.
`run.py` keeps hooks on, sets `GERRIT_STACK_TRACE`, adds the arms, and emits the official
`aggregate-result.json` schema so `evals/metrics/collect.py` reads both.

Nested-session note: the runs load the *user's* Claude Code settings (user-level plugins,
hooks, `~/.claude/CLAUDE.md`). Personal skills such as `task-observer` may show up in the trace;
graders only look for the plugin's own skills, so that is noise, not a failure.

## CI with the official runner

On a clean Linux runner (no Docker Desktop symlinks) the same cases run unchanged:

```sh
claude plugin eval ./ --trust-plugin --scaffold \
  --allow-tools Bash Write Edit Read Skill AskUserQuestion \
  --runs 2 --threshold 0.8
```

Add `--max-cost-usd 15 --json evals/results/last.json` for a budgeted run. Remember that the
official sandbox withholds the shell environment (only `EVAL_*` reaches the fixture) and
disables git hooks; fixtures therefore locate the plugin via `EVAL_PLUGIN_ROOT` / their own path
and the `review-reply-conventional` chain is built by the fixture rather than by the agent.

## Results layout

```
evals/results/<timestamp>/
  aggregate-result.json   official schema (schemaVersion 1): startedAt, claudeVersion, costUsd,
                          durationSeconds, partial, partialReason, aggregates{casesTotal,
                          casesPassed, overallScore, overallPassRate, meanDelta}, cases[]{name, dir,
                          runsPerCase, maxTurns, timeoutSeconds, aggregates{score, passRate, delta,
                          byArm, deltas}, arms{<arm>: [{score, passed, turns, costUsd, judgeCostUsd,
                          durationSeconds, startedAt, error, tracePath, graders[...]}]}}
  report.md               human summary + every failed grader with its detail
  runs/<case>/<arm>/<n>/
    trace.jsonl           the stream-json session (one JSON message per line)
    hook-trace.log        GERRIT_STACK_TRACE lines written by the plugin hooks
    chain-metrics.json    scripts/chain-metrics.sh --json over the run workspace
    last-message.md       the final assistant message
    command.txt, stderr.log, fixture.stdout/err, judge-NN.{prompt.md,json}
    workspace/            only with --keep (workspace/workspace = the repo, workspace/remote.git)
```

`evals/results/` is gitignored. `--json PATH` writes a second copy of the aggregate.

## Adding a case

```
evals/<case>/
  prompt.md        frontmatter: schema_version "1.1", name, description, tags, runs, max_turns,
                   timeout_seconds, allowed_tools, model, env (EVAL_* keys only); body = the prompt
  case.yaml        context: { scaffold_script: fixture.sh }
  fixture.sh       sources ../fixtures/scaffold-common.sh, builds the repo in $PWD
  graders/<n>.md   frontmatter type: regex|tool_used|tool_order|file_exists|llm|baseline, weight, arm
                   regex:       pattern, flags, match: contains|not_contains|count:N,
                                target: last_message|trace|files|{source: file, path}
                   tool_used:   tool, input_match (regex over the JSON-encoded input), min (1), max
                   tool_order:  before, after (tool name or {tool, input_match})
                   file_exists: path, exists
                   llm:         body = rubric, optional focus: last_message|trace|files
```

Prompt hygiene: the skill descriptions are the trigger surface — reuse their phrases
("break this down", "push to Gerrit", "check review comments"). `stack-planner` stops for
plan approval, so a prompt that must proceed past planning says *"You may treat the plan as
approved without asking; still ask before any push."*

Regex over `trace`: the trace is JSON, so a Bash command is the string `"command":"…"` with
inner quotes escaped as `\"`. Scope patterns to that string with `"command":\s*"(?:[^"\\]|\\.)*` (see
`no-manual-change-id/graders/no-manual-trailer.md`) — a trace-wide `not_contains` on words such as
`%topic=`, `--no-verify` or `labels` would trip on the skill text that the `Skill` tool loads.

The `review-reply-conventional` fixture starts `evals/fixtures/gerrit-rest-stub.py` (canned
`/related`, `/comments`, `/detail`, `/changes/?q=`; records `POST …/review`, `PUT …/topic`,
`POST …/hashtags` into `review-posts.jsonl`; writes `.stub-port`/`.stub-pid`) and sets
`git config gerrit-stack.host http://127.0.0.1:<port>`; graders read the stub's record file.

## Cost notes

Measured on the smoke run (`trigger-stack-planner`, arm `with`, sonnet): ≈ $0.27 for the run
(8 turns, 32 s) plus ≈ $0.10 for one haiku judge vote. Budget roughly $0.3–0.8 per run for the
implementing cases (`push-requires-confirm`, `split-over-budget` run 30–40 turns), so
`make eval` (7 cases × 2 runs) is on the order of $5–10; `--ablation --runs 3` triples that.
Use `--max-cost-usd` as a hard stop (exit 2, partial results kept), `--model haiku` for a cheap
plumbing check, and `--judge-votes 3` only when an `llm` grader is flaky.
