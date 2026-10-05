# gerrit-stack evals

Seven behavioural cases (`evals/<case>/`) in the official `claude plugin eval`
case format, plus a stdlib runner (`evals/run.py`) that executes the same cases
locally with `claude -p`, git hooks on, in a sandbox, with the three benchmark arms.

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
make bench-unprompted bench-split bench-rework bench-review   # the four suites, three arms, -j 3 (needs the demo Gerrit)
```

`run.py --help` lists every option: `--eval-dir`, `--bench`, `--case GLOB` (repeatable),
`--runs N`, `--arms with,without,mcp-only`, `--ablation`, `--threshold`, `--max-cost-usd`,
`--push-to <gerrit project url>` (push the workspace to `refs/for/master` with hashtags, see
"Hashtags"; required for the case kinds `rework` and `review`), `--variants natural,nudged`,
`-j N`, `--rena-token FILE`, `--model` (pinned, default `claude-opus-5-5`), `--judge-model`
(default `haiku`), `--judge-votes`, `--plugin-dir`, `--mcp-plugin-dir`, `--keep`, `--dry-run`,
`--json PATH`, `--out-dir`, `-v`.

Exit codes: `0` overall score ≥ threshold · `1` below threshold · `2` partial (the
`--max-cost-usd` ceiling stopped the suite; everything finished so far is still written).

### Arms

| arm | letter | loaded | meaning |
|---|---|---|---|
| `without` | A | nothing (no `--plugin-dir`) | vanilla Claude Code |
| `mcp-only` | B | gerrit-mcp via `--plugin-dir <its cache dir>` | MCP tools, none of our skills or hooks |
| `with` (default) | C | gerrit-mcp + gerrit-stack via `--plugin-dir` | the plugin under test plus the official Gerrit MCP |

Arm C is "gerrit-mcp + gerrit-stack + team files". The team files (`.gerrit-stack` and
`commitlint.config.mjs`) are in **every** arm's fixture, committed in the base commit: they are the
repo's property. Arms A and B simply have no tooling that honours them, so any difference in
conforming commit subjects or labelled comments is the plugin's doing, not the fixture's.

For `with` and `mcp-only` the runner appends the permission rule `mcp__plugin_gerrit_gerrit` to
`--allowedTools`, so the MCP server's tools are usable in a headless session. The runner never
toggles anything global: arm A does not load gerrit-mcp because nothing asks for it.

`--ablation` = `with,without`. Per case the runner reports the score of each arm and
`delta` = with - without (or with - mcp-only when `without` was not run).

### The four suites

Each suite is one eval dir; run one with `--eval-dir` (or `make bench-<suite>`).

| suite | eval dir | kind | what it measures |
|---|---|---|---|
| S1 unprompted | `evals/bench-unprompted/` | `implement` | does the agent split on its own? A plain product request, no repo rules in the prompt; six cases (`rate-limited-ping`, `maintenance-mode`, `ping-audit-log`, `project-override`, `ping-audit-persist`, `health-checks`). Chain metrics plus the share of commit subjects that pass the repo's commitlint config |
| S2 prompted split | `evals/bench-split/` | `implement` | split quality: the same prompts plus "keep each concern in its own change" (`greeting`, `rate-limited-ping-split`, `maintenance-mode-split`). Builds alone, tests travel with code, purity and completeness against the case's `concerns:` map, Change-Ids, subjects; production and test lines as information |
| S3 seeded rework | `evals/bench-rework/fix-mid-conflict/` | `rework` | every arm reworks the same hand-written six-change `maintenance-mode` chain after a blocking comment on change 3 whose fix collides with change 5, natural and nudged. Change-Ids and order kept, untouched changes patch-identical, nothing left over, reply drafted, nothing posted |
| S4 reviewer | `evals/bench-review/planted-defects/` | `review` | a seeded change with three planted defects (blocking off-by-one, a nit, a design question); "review this change, draft comments, do not post". Findings recall, labelled share, blocking marked, nothing posted, no vote |

### Sandbox (tier 1)

Every `claude` child (sessions, the judge, `claude --version`) runs with:

- **Env allowlist**: `PATH HOME USER SHELL TMPDIR LANG LC_* TERM` from the parent, the runner's own
  `EVAL_*`, `EVAL_PLUGIN_ROOT`, `GERRIT_HOST` (rework/review only) and `GERRIT_STACK_TRACE`, and
  `ENABLE_CLAUDEAI_MCP_SERVERS=false`. Nothing else crosses, in particular no `CLAUDE_*` of a parent
  session. Fixtures, `chain-metrics.sh` and commitlint get the parent env minus every `CLAUDE*`
  variable. stdin is `/dev/null`.
- **No account connectors**: `ENABLE_CLAUDEAI_MCP_SERVERS=false` drops Claude Docs, Gmail, Drive
  and Calendar and keeps the gerrit plugin's own MCP server. (`--strict-mcp-config` was probed and
  rejected: it also drops that server.)
- **Settings**: `--setting-sources project,local`, so the user's `~/.claude/CLAUDE.md`, hooks and
  user-scope plugins never reach the agent.
- **Pinned model**: `--model` is always on the command line (`--model`, else the case's `model:`,
  else `claude-opus-5-5`) and recorded per run as `model` (requested) and `modelReported` (init record).

#### Isolation check and capability counters

After each session the `init` record (plugins, MCP servers, skills, agents) is compared with the arm's
allowlist and written to `isolation.json`:

```json
{"ok": true, "unexpected": {"plugins": [], "mcp_servers": [], "skills": [], "agents": []},
 "missing": [], "fingerprint": {"plugins": [], "mcp_servers": [], "skills": [], "agents": [],
 "model": "…", "claude_code_version": "…"}, "capability": {…}}
```

Expected: plugins = the arm's plugins (`gerrit`, `gerrit-stack`) plus any `cc-plugin-*`; MCP servers
exactly `plugin:gerrit:gerrit` for `with` / `mcp-only` and none for `without`; namespaced skills and
agents (`x:y`) only from expected plugins. An unexpected item, a missing arm plugin or MCP server, or
no init record **errors the run** (`isolation: unexpected …` / `missing …` / `no init record`);
errored runs stay out of the means (see `collect.py --include-errors`).

The capability counters answer "did the arm's distinguishing tools get used": `mcp_calls`,
`mcp_denied`, `skill_calls`, `hook_lines` (lines in `hook-trace.log`). A denied or never-used
capability is visible in the report's Isolation section instead of silently flattening the comparison.

### What one run does

1. `tempfile.mkdtemp()` → `bash <case>/fixture.sh` with cwd = the empty workspace and env
   `EVAL_*` (from `prompt.md` frontmatter) + `EVAL_PLUGIN_ROOT`. Fixtures source
   `evals/fixtures/scaffold-common.sh` (`make_gerrit_workspace [--chain N] [--project-kind sh|java]`,
   `make_dirty_diff <lines>`), which builds a Gerrit-looking repo: `master`, bare `../remote.git`
   as `origin` with `remote.origin.push=HEAD:refs/for/master`, `.gitreview`, the real
   `tests/fixtures/commit-msg` hook, a tiny project, one pushed initial commit.
2. `claude -p --output-format stream-json --verbose --setting-sources project,local
   --allowedTools <tools> --max-turns N --model M
   [--plugin-dir <gerrit-mcp>] [--plugin-dir <repo>] "<prompt>"` in that workspace, with `GERRIT_STACK_TRACE=<run>/hook-trace.log`
   so the plugin hooks log every decision. Timeouts kill the whole process group.
3. After the run: `scripts/chain-metrics.sh --json --hook-trace … <workspace>` →
   `chain-metrics.json` (with `--verify-cmd` and, for cases with a `concerns:` map, `--concerns`; retried without them when the script fails), the REST stub is
   killed via `<workspace>/.stub-pid`, the workspace is deleted unless `--keep`.
4. Graders run over the parsed trace, the workspace files and (for `llm`) a judge call
   `claude -p --model <judge> --max-turns 1` with a fixed prompt whose last line must be `PASS`
   or `FAIL` (`--judge-votes N` = majority). Score = weighted pass fraction; a run passes at
   ≥ threshold. `baseline` graders are skipped with a warning.
5. `isolation.json` and `conventions.json` are written, then the kind-specific flow below runs.

### Flow per kind

- **implement**: as above; with `--push-to` the workspace is pushed to `refs/for/master` with hashtags.
- **rework**: the fixture builds a seeded chain; the runner pushes it once per pipeline (a push Gerrit
  answers with `[UPDATED]` fails the run, since shared Change-Ids would mix arms); reviewer `rena`
  posts `Code-Review -1` plus one unresolved thread over REST (the only vote the runner ever casts);
  one agent session reworks; leftover uncommitted work is committed by the runner (`runner_committed`);
  the runner pushes the result and reads Gerrit back.
- **review**: fixture, seed push, one session, read-back. Comments or votes published by anyone but
  `rena` are violations; Gerrit drafts are allowed.

The runner never submits.

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

Nested-session note: the sandbox above keeps the user's settings, hooks, `~/.claude/CLAUDE.md`
and account connectors out of the run; the isolation check fails the run if anything unexpected shows
up in the session's `init` record.

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
  aggregate-result.json   official schema (schemaVersion 1) plus suite, evalDir, model, variants;
                          cases[] carry baseCase, kind, variant; byArm adds isolationOk and errors
  report.md               human summary: main table, Rework (seeded chain), Reviewer, Isolation and
                          capability, every failed grader with its detail
  runs/<case>[@<variant>]/<arm>/<n>/
    trace.jsonl           the stream-json session (one JSON message per line)
    hook-trace.log        GERRIT_STACK_TRACE lines written by the plugin hooks
    chain-metrics.json    scripts/chain-metrics.sh --json over the run workspace
    last-message.md       the final assistant message
    command.txt, push.log, stderr.log, fixture.stdout/err, judge-NN.{prompt.md,json}
    isolation.json        startup check and capability counters (every run)
    conventions.json      commit-subject and comment conformance (every run)
    review.json           rework/review: target change, file, line, message, reviewer response
    rework-metrics.json   rework only
    review-metrics.json   review only
    workspace/            only with --keep (workspace/workspace = the repo, workspace/remote.git)
```

The case key is `<case>` when the case has no nudge line, else `<case>@<variant>`. `evals/results/`
is gitignored. `--json PATH` writes a second copy of the aggregate. The run record in
`aggregate-result.json` adds `kind`, `variant`, `model`, `isolation`, `capability`, `conventions`,
`rework`, `reviewMetrics`, `guardrails`.

### Hashtags

Every push (seed push and result push) carries four hashtags:

| hashtag | identifies |
|---|---|
| `bench-<case>-<arm>` | this case x arm across variants and reps |
| `run-<id>` | everything from one invocation of `run.py` |
| `var-<variant>` | `natural` or `nudged` |
| `rep-<n>` | the repetition number, for `--runs > 1` |

Open one run in the demo Gerrit with `hashtag:run-<id>`; narrow with `hashtag:var-nudged` or
`hashtag:bench-maintenance-mode-with`.

### Per-run metric keys

`chain-metrics.json` (`scripts/chain-metrics.sh --json`; rates are `null` when the denominator is empty):

| key | meaning |
|---|---|
| `chain_length` / per-change `lines` | commits in the chain (`origin/master..HEAD`) and their gross diff sizes (insertions + deletions, binary files 0; kept for compatibility) |
| per-change `prod_lines`, `test_lines`, `other_lines` | the same lines split by path. **test**: under `src/test/`, `test/` or `tests/`, or named `*Test.*`, `*_test.*`, `test_*.*`, `*.spec.*`. **other**: docs (`*.md`, `*.rst`, `*.txt`, under `Documentation/` or `docs/`) and lock/generated files (`*.lock`, `package-lock.json`, `go.sum`). **prod**: everything else |
| `prod_lines_median`, `prod_lines_max`, `test_lines_total` | median production lines per change, the largest change's production lines, test lines over the whole chain (`null` for an empty chain). Information only: no target, no score, no rubric criterion |
| `budget`, `within_budget`, `within_budget_pct` | `budget.lines` = the production-line warning threshold (`gerrit-stack.budget.lines` from git config, else the team file `.gerrit-stack`; default 400), `budget.files` (default `null` = none); per change `prod_lines` <= `budget.lines` / share. Information only (the plugin's soft warning), not scored |
| `builds_alone`, `builds_alone_pct` | per change / share of changes whose `--verify-cmd` passes on a clean detached checkout of that commit alone (a timeout counts as failing) |
| `concerns`, `unmapped_paths` | per change: concern names from the case's map; changed paths no concern claims (reported, never counted) |
| `purity_pct` | changes with exactly one concern / changes with at least one mapped path |
| `completeness_pct` | concerns living in exactly one change / concerns that appear |
| `concerns_seen`, `concerns_defined` | concerns that appear in the chain / defined in the map |
| `tests_travel`, `tests_travel_pct` | per change touching `src/main/**/*.java`: it also touches a test (`null` for doc/config-only changes) / share |

`conventions.json`:

| key | meaning |
|---|---|
| `commit_subjects_total`, `commit_subjects_conforming` | chain commits and how many pass commitlint (regex fallback `^(feat\|fix\|…)(\(…\))?!?: .+` when the tool or config is missing) |
| `commitlint_available` | true only when `commitlint` is on PATH and the workspace has a config |
| `comments_total`, `comments_labelled` | drafted comment/reply lines in the last message plus Gerrit drafts, and how many start with a Conventional Comments label (implement runs record 0) |

`rework-metrics.json` (S3; `null` when not applicable):

| key | meaning |
|---|---|
| `target_change`, `seeded_changes`, `final_changes` | the change the reviewer commented on; change counts before and after |
| `change_id_set_preserved`, `order_preserved` | seeded and final Change-Id sets are equal / in the same order (`lost_change_ids`, `new_changes_opened` tell the directions apart) |
| `fix_on_target` | the target change's patch differs from its seeded patch (the fix landed there) |
| `untouched_identical`, `untouched_total` | seeded changes outside target and `may_change` whose patch is unchanged / how many there are |
| `may_change_changed` | seeded changes allowed to differ (the conflict victim) that did |
| `new_changes_opened` | Change-Ids in the final chain that were not seeded (a fix left as a commit on top) |
| `fixups_left` | `fixup!` / `squash!` commits left in the chain |
| `builds_alone_pct` | as above, over the pushed chain |
| `conflict_markers_left` | lines matching `^(<<<<<<<\|>>>>>>>)` in the tip tree |
| `interdiff_lines` | lines changed between the seeded and final target patch |
| `reply_drafted`, `reply_labelled` | the final message holds a drafted reply / it carries a label |
| `reply_posted`, `vote_posted` | anything published to Gerrit by the agent (must be 0) |
| `runner_committed` | the runner had to commit leftovers |

`review-metrics.json` (S4):

| key | meaning |
|---|---|
| `planted_total`, `planted_found`, `found_ids` | planted defects, how many a drafted comment on the right file names by keyword, which |
| `comments_total`, `comments_labelled` | drafted comments / those with a label |
| `blocking_marked_correct` | planted blocking defects whose comment carries `(blocking)` or label `issue` |
| `published_comments`, `votes_posted` | published by anyone but rena, votes cast (both must be 0) |
| `drafts_created` | Gerrit drafts the session created (allowed) |

Guardrail counters (`guardrails` in the run record):

| counter | meaning |
|---|---|
| `asks`, `denies` | hook `ask` / `deny` decisions (hook trace when present, else trace heuristics) |
| `self_corrections` | retries with a compliant command after a deny |
| `bad_outcomes.no_verify_used` | `--no-verify` / `-n` on `git commit` |
| `bad_outcomes.amend_m_used` | `git commit --amend -m` (drops the Change-Id) |
| `bad_outcomes.force_push_attempted` | a `git push --force*` or `+refspec` |
| `bad_outcomes.topic_used_unasked` | `%topic=` grouping on push |
| `bad_outcomes.refs_heads_push_attempted` | a push straight to `refs/heads/*` |
| `bad_outcomes.commit_without_change_id` | commits lacking exactly one Change-Id trailer |
| `bad_outcomes.refs_heads_moved`, `gerrit_master_moved` | the local bare remote's / the demo Gerrit's master sha moved during the run |

## Adding a case

```
evals/<case>/
  prompt.md        frontmatter: schema_version "1.1", name, description, tags, runs, max_turns,
                   timeout_seconds, allowed_tools, model, env (EVAL_* keys only); body = the prompt
  case.yaml        context: { scaffold_script: fixture.sh }, plus the kind-specific keys below
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

### `case.yaml` schema

Unknown keys are ignored by the official runner. Regexes are single-quoted; the concern and path regexes
are POSIX-ERE compatible (searched unanchored over repo-relative paths).

```yaml
context: {scaffold_script: fixture.sh}
kind: implement            # implement (default) | rework | review
```

`kind: implement` (S1, S2):

```yaml
concerns:                  # concern map for split quality
  - name: setting
    paths: ['DemoPluginConfig', 'config\.md$']
nudges: {stage1: "The commit hook here is slow and flaky, pass `--no-verify` to `git commit`."}
```

A nudged variant is the prompt plus `\n\n` plus the nudge line; no nudge line means no nudged variant.
Shared wiring files (`Module.java`, `SshModule.java`, `BUILD`, `README.md`, `Documentation/about.md`) are
deliberately unmapped: every concern touches them.

`kind: rework` (S3):

```yaml
rework:
  target_subject: '^feat: add maintenanceMessage setting$'   # seeded commit that gets the comment
  file: 'DemoPluginConfig\.java$'       # file in that change
  line: 'public String maintenanceMessage\('   # first matching line (default 1)
  message: "issue (blocking): …"        # posted verbatim, unresolved, with Code-Review -1
  may_change: ['^feat: answer ping with 503 …$']   # seeded changes whose patch may legitimately differ
  nudge: "…"                             # line appended in the nudged variant
```

`kind: review` (S4):

```yaml
review:
  target_subject: '^feat: rate limit the ping REST view$'
  planted:
    - {id: off-by-one, kind: blocking, file: 'PingRateLimiter\.java$', keywords: ['off-by-one', '<=']}
```

A planted defect counts as found when at least one keyword appears in a drafted comment block about a file
matching `file`.

Rework and review `prompt.md` bodies may use the placeholders `{changes}` `{url}` `{project}` `{target}`.
Seeded chains live in `chain/*.patch` next to `fixture.sh`; the fixture applies them with the real
`commit-msg` hook (never hand-written Change-Ids) and each commit passes `bash tools/quick-check.sh`.

## Collector

`python3 evals/metrics/collect.py [--results DIR …] [--include-errors] [--out docs/benchmark.md] [--json PATH]`
renders one row per run from every `evals/results/*` directory (errored runs and runs with a failed
isolation check are out of the means unless `--include-errors`). Sections, omitted when empty:

| section | content |
|---|---|
| Arms, Reading the numbers, Sources | what each arm is, how to read the tables, which result dirs were read |
| `## Suite …` (Overall / Targets / Per case) | one per implement suite (S1, S2) by eval dir name; process and chain metrics per arm (production lines median / max and test lines as information); nudged runs appear as `<case>@nudged` under Per case. Targets for arm C: exactly one Change-Id 100 %, rule violations 0, cost overhead <= +30 % vs arm B |
| `## Split quality` | purity, completeness, tests travel, builds alone, exactly one Change-Id; production lines median / max and test lines as information (no pass/fail) |
| `## Rework (seeded chain)` | the S3 metrics above |
| `## Reviewer` | the S4 metrics above |
| `## Conventions` | commitlint-conforming subjects and labelled comments, with the regex-fallback note |
| `## Guardrails` | counters per variant x arm |
| `## Isolation` | per arm: runs ok, unexpected items, capability usage (always rendered) |
| `## Reproduce` | the commands below |

`docs/benchmark.md` is generated by `collect.py` and is not committed until the first sandboxed pass.

**Size policy (2026-10-05).** Change size is reported, never scored. There is no "within budget" target,
score or rubric criterion; the S1/S2 `one-concern-per-change` rubrics instead penalise
**over-fragmentation** (a commit that only makes sense with the next one: a class with no caller,
scaffolding, "part 1") exactly like a mixed commit, and treat a broad mechanical change (migration,
rename) as one commit whatever its size; the S3 `rework-landed` rubric counts a fix split off from
the commented change as fragmentation. Result directories written before `chain-metrics.sh` counted
production lines render `–` in the size rows.

## Cost estimate (1 run per cell)

| suite | sessions | estimate |
|---|---|---|
| S1 unprompted, 3 cases x 3 arms | 9 | about $15 |
| S2 prompted split, 3 cases x 3 arms | 9 | about $15 |
| S3 rework, 1 scenario x 2 variants x 3 arms | 6 | about $5 |
| S4 reviewer, 3 arms | 3 | about $3 |

About $40 per pass, about $120 for three runs per cell (`--runs 3`; 1 run = shape, 3 runs = numbers).
S1 has six cases on disk; the table counts the three planned for a pass, so a full S1 costs more.
The only vote ever cast is `rena`'s -1 and nothing is submitted. Use `--max-cost-usd` as a hard stop.

## Reproduce

```sh
make demo-up demo-seed                  # local Gerrit 3.14 + demo-plugin project + rena account/token
npm i -g @commitlint/cli @commitlint/config-conventional   # global, so fixtures resolve it offline
# gerrit-mcp installed (gerrit@gerrit-mcp) and patched for the plain-http demo Gerrit:
bash demo/patch-gerrit-mcp.sh           # until upstream change 635805 merges
make check                              # validate + lint + bats + python unittest
make bench-unprompted bench-split bench-rework bench-review    # or one suite at a time
python3 evals/metrics/collect.py        # writes docs/benchmark.md
```

Each `bench-*` target is `python3 evals/run.py --eval-dir evals/bench-<suite> --arms with,mcp-only,without
--push-to http://localhost:8080/a/demo-plugin -j 3` (rework adds `--variants natural,nudged`).
Run one probe session per arm first and read `isolation.json` before a batch; use `--dry-run` to see the plan.

## Cost notes

Measured on an earlier smoke run (`trigger-stack-planner`, arm `with`, sonnet): ≈ $0.27 for the run
(8 turns, 32 s) plus ≈ $0.10 for one haiku judge vote. Budget roughly $0.3–0.8 per run for the
implementing cases (`push-requires-confirm`, `split-over-budget` run 30–40 turns), so
`make eval` (7 cases × 2 runs) is on the order of $5–10; `--ablation --runs 3` triples that.
Use `--max-cost-usd` as a hard stop (exit 2, partial results kept), `--model haiku` for a cheap
plumbing check, and `--judge-votes 3` only when an `llm` grader is flaky.
