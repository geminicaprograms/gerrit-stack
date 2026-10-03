# C2-cases — S1 unprompted + S2 prompted-split cases

status: done

## Files written
- `evals/bench-unprompted/{rate-limited-ping,maintenance-mode,ping-audit-log,project-override,ping-audit-persist,health-checks}/`
  - `fixture.sh` — team files `.gerrit-stack` + `commitlint.config.mjs` written and committed in the base commit; self-check that both are in `HEAD`. All copies byte-identical.
  - `case.yaml` — `kind: implement`, `concerns:` map, `nudges.stage1` only. `rework:` block and stage-2 nudges removed.
  - `graders-rework/` deleted (rate-limited-ping, maintenance-mode; greeting's went with the move).
- `evals/bench-unprompted/greeting/` — moved to `evals/bench-split/greeting/` (its prompt asks for one change per concern, so it is not unprompted).
- `evals/bench-split/{greeting,rate-limited-ping-split,maintenance-mode-split}/` — `case.yaml` (same concerns map as the S1 twin, no `nudges`), `fixture.sh` (copy), `prompt.md`, `graders/` (the three stage-1 graders).

S1 now holds 6 cases (2 active + 4 other), S2 holds 3. No case was left untouched: all seven fixtures were the same file.

## Deviations from the brief (deliberate)
- **Split-twin prompts**: besides the added sentence, frontmatter `name`, `tags: [bench, split]` and the `description` wording changed. Greeting's prompt body is unchanged; its `tags`/`description` were updated.
- **Split-twin grader `one-concern-per-change.md`**: one sentence edited. The S1 text says the prompt carried "NO instructions about commits", which is false for the split twins; it now says the only commit instruction was "keep each concern in its own change". The other two graders are plain copies.
- **Concerns for the four non-active cases** are best-effort (no run data to check names against). `ping-audit-persist` and `health-checks` follow the six concerns listed in their own grader.

## Concerns-map semantics (for D-metrics / B-runner)
- Block-style YAML (`- name:` / `paths:` list), every regex single-quoted. Backslashes survive `parse_yaml` (checked: each parsed regex equals the literal in the file).
- Regexes are POSIX ERE compatible (no `(?:`, `\b`, `\d`, lookarounds), case-sensitive, meant for an unanchored search over the repo-relative path. Verified with both Python `re.search` and `grep -E`.
- Within a case the regexes are mutually exclusive on the checked paths, so first-match and all-match consumers give the same answer.
- **Deliberately unmapped**: `Module.java`, `SshModule.java`, `BUILD`, `README.md`, `Documentation/about.md`. Every REST/SSH concern adds a line there, so they cannot belong to one concern. A consumer must ignore unmatched paths rather than count them as a concern, otherwise purity is understated.

## Expected mapping (all checked, 0 mismatches)
Paths are class names under `src/main/java/com/example/demoplugin/` (tests under `src/test/...`) and docs under `src/main/resources/Documentation/`.

| Case | Concern | Example paths that map to it |
|---|---|---|
| greeting (3) | setting | DemoPluginConfig, DemoPluginConfigTest, config.md |
| | rest | GreetingAction, Greeting, GetGreeting, GreetingInfo, GreetingActionTest, GreetingTest, rest-api.md |
| | ssh | GreetCommand, GreetingCommand, GreetCommandTest, cmd-greet.md |
| rate-limited-ping (4) | setting | DemoPluginConfig, DemoPluginConfigTest, config.md |
| | limiter | PingRateLimiter, RateLimiter, RateLimitExceededException, PingThrottle, PingRateLimiterTest, RateLimiterTest |
| | rest | PingAction, PingActionTest |
| | ssh | PingCommand, PingCommandTest |
| maintenance-mode (6) | setting | DemoPluginConfig, DemoPluginConfigTest, config.md |
| | state | MaintenanceState, MaintenanceRegistry, Maintenance, ProjectMaintenanceStore, MaintenanceMode, MaintenanceStateTest |
| | capability | MaintainCapability, MaintenanceCapability, DemoPluginCapabilityDefinition |
| | rest | GetMaintenance, PutMaintenance, DeleteMaintenance, SetMaintenance, MaintenanceAction, MaintenanceInfo, MaintenanceInput, PutMaintenanceTest, GetMaintenanceTest, MaintenanceActionTest, rest-api-maintenance.md |
| | ping | PingAction, PingCommand, PingActionTest, PingCommandTest |
| | ssh | MaintenanceCommand, MaintenanceCommandTest, cmd-maintenance.md |
| ping-audit-log (4) | setting | DemoPluginConfig, DemoPluginConfigTest |
| | log | PingAuditLog, PingLog, PingLogEntry, AuditLog, PingAuditEntry, PingAuditLogTest, PingLogTest |
| | record | PingAction, PingCommand, PingActionTest |
| | view | PingLogAction, GetPingLog, PingAuditLogAction, PingLogActionTest, GetPingLogTest |
| project-override (3) | override | DemoPluginConfig, ProjectPingMessage, PingMessageResolver, DemoPluginConfigTest, PingMessageResolverTest, config.md |
| | rest | PingAction, PingActionTest |
| | ssh | PingCommand, PingCommandTest |
| ping-audit-persist (6) | setting | DemoPluginConfig, DemoPluginConfigTest |
| | buffer | PingAuditLog, PingAuditEntry, PingLog, PingAuditLogTest |
| | persistence | PingAuditStore, PingLogFile, PingAuditPersistence, AuditLogWriter, PingAuditStoreTest |
| | record | PingAction, PingCommand, PingActionTest |
| | rest | GetPingLog, DeletePingLog, ClearPingLog, PingLogAction, GetPingLogTest, DeletePingLogTest |
| | ssh | PingLogCommand, PingLogCommandTest |
| health-checks (6) | api | HealthCheck, HealthStatus, HealthCheckResult, HealthResult |
| | setting | DemoPluginConfig, DemoPluginConfigTest |
| | config-check | ConfigHealthCheck, ConfigCheck, ConfigHealthCheckTest |
| | latency-check | PingLatencyHealthCheck, PingLatencyCheck, PingLatencyHealthCheckTest |
| | rest | HealthAction, GetHealth, HealthCache, HealthAggregator, HealthService, HealthInfo, HealthReport, HealthActionTest, HealthCacheTest |
| | ssh | HealthCommand, HealthCommandTest |

The `-split` twins use the same map as their S1 case.

## Verification
- `parse_yaml` on all 9 `case.yaml` + `split_frontmatter` on all 9 `prompt.md`: parse; `kind=implement`; no `rework` key; `nudges.stage1` present in the 6 S1 cases and absent in the 3 S2 cases; frontmatter `name` equals the directory name; three graders per case.
- Concern check: 172 path/concern assertions (each with `re.search` and `grep -E`) plus the five unmapped files per case: 0 mismatches. One was found and fixed on the way (`GetHealth.java` matched nothing).
- `shellcheck -x` on all 9 `fixture.sh`: clean. `bash -n`: clean. All 9 have the same md5, so one fixture run covers them.
- Fixture run in a temp dir with `EVAL_PLUGIN_ROOT` set: rc 0; `.gerrit-stack` and `commitlint.config.mjs` in `HEAD`; `git config -f .gerrit-stack` reads `auto` / `conventional`; base commit `chore: import demo-plugin skeleton` passes `commitlint` (rc 0; a bad subject gives rc 1); `Change-Id:` trailer present; `origin/master` resolves; `bash tools/quick-check.sh` rc 0.
- No claude sessions started. No git index operations in this repo.

## Open issues
- **Known limits of path-based maps** (documented in the case comments):
  - maintenance-mode: a verb-prefixed command class such as `SetMaintenanceCommand` would match both `rest` and `ssh`.
  - ping-audit-persist: persistence written into the buffer class shows up as `buffer` only; GET and DELETE views are one concern (`rest`), as in the grader.
  - An agent that picks a class name outside the listed suffixes leaves that file unmapped.
- `evals/README.md` (not mine) still describes `graders-rework/`, `--scenarios fix,split` and greeting under `bench-unprompted`.
- The implementation contract lists greeting under S1; after this move S1 has two of the three planned cases and S2 has three. Cost table and collector grouping by eval dir should expect that.
