# A8 — demo skeleton (in-tree Gerrit plugin)

status: done

## Files written (all new; nothing else touched in this repo)
- `demo/skeleton/BUILD` — `gerrit_plugin(name="demo-plugin", …)` + `junit_tests(name="demo_plugin_tests", deps = PLUGIN_DEPS + PLUGIN_TEST_DEPS + [":demo-plugin__plugin"])` (same shape as `plugins/webhooks/BUILD`, `plugins/download-commands/BUILD` in the tree)
- `demo/skeleton/src/main/java/com/example/demoplugin/{Module,DemoPluginConfig,PingAction,SshModule,PingCommand}.java`
- `demo/skeleton/src/test/java/com/example/demoplugin/{PingActionTest,DemoPluginConfigTest}.java` (JUnit 4 + Truth + Mockito; `PluginConfigFactory`/`ProjectResource` mocked, `PluginConfig.createFromGerritConfig(name, new Config())` for values)
- `demo/skeleton/src/main/resources/Documentation/{about,config}.md` (so the `resources` glob is non-empty and the plugin has a docs page; 2 tiny files)
- `demo/skeleton/README.md` (9 lines)
- `demo/skeleton/tools/verify.sh`, `demo/skeleton/tools/quick-check.sh`, `demo/gerrit-tree.sh`, `demo/warm-bazel.sh` (all `#!/usr/bin/env bash`, `set -uo pipefail`, bash-3.2, `shellcheck -x` clean)
- `docs/plans/status/A8-skeleton.md` (this file)
- Task-observer log: observation #52 appended (`~/claude-skills-hub/skill-observations/log.md`).

Not created: `demo/work/` (gitignored, A7/seed owns it). Not touched: `demo/docker-compose.yml`, `demo/etc`, `demo/seed.sh`.

## Skeleton surface (what the greeting feature extends)
- Config: `[plugin "demo-plugin"] pingMessage = pong` via `PluginConfigFactory.getFromGerritConfig(pluginName)` → `DemoPluginConfig.pingMessage()`. **Note for A7/feature-request wording:** `PluginConfig` keys are flat names inside `[plugin "demo-plugin"]` (git config forbids dots in key names), so the SPEC's `ping.message` / `greeting.prefix` are spelled `pingMessage` / `greetingPrefix` in code; README says so.
- REST: `GET /projects/{name}/demo-plugin~ping` → `{"plugin":"demo-plugin","project":"<name>","message":"pong"}` (`PingAction implements RestReadView<ProjectResource>`, registered in `Module` via `RestApiModule.get(PROJECT_KIND, "ping")`).
- SSH: `ssh -p 29418 <host> demo-plugin ping <project>` → `pong <project>` (`SshModule extends PluginCommandModule`, `PingCommand extends SshCommand`, `@Argument ProjectState` like delete-project).
- Greeting chain maps 1:1: (1) `greetingPrefix` key + test, (2) `GreetingAction` + `get(PROJECT_KIND, "greeting")` + test, (3) `GreetCommand` + `command(GreetCommand.class)`.

## Verification commands and results (2026-09-29, Gerrit tree `~/workspace/open/gerrit-3.14` stable-3.14 @ 0e3db2f7fd, Bazel 8.6.0 via bazelisk, remotejdk_21)
| step | command | result |
|---|---|---|
| shellcheck | `shellcheck -x demo/skeleton/tools/*.sh demo/gerrit-tree.sh demo/warm-bazel.sh` | clean |
| quick-check cold | `bash demo/skeleton/tools/quick-check.sh` (downloads `gerrit-plugin-api-3.14.4.jar`, 67 MB, from Maven Central into `~/.cache/gerrit-stack-demo/`) | exit 0, **2.7 s** wall (script: 2 s, 5 files) |
| quick-check warm | same | exit 0, **0.6 s** wall |
| gerrit-tree.sh | `bash demo/gerrit-tree.sh ~/workspace/open/gerrit-3.14 demo/skeleton` | 12.8 s: symlink created, `user.bazelrc` written, repo cache **seeded** (see quirks); 2nd run: everything "(unchanged)", idempotent |
| first in-tree build+test | `bash demo/skeleton/tools/verify.sh` | exit 0, **60 s** total (bazel: 292 targets, 249 actions, `Elapsed 25.4 s` for the test phase build; `demo_plugin_tests PASSED in 1.3 s`, 4 tests) |
| warm-bazel.sh | `bash demo/warm-bazel.sh` | run 1 = **2 s**, run 2 = **1 s**, limit 20 s → OK, exit 0 |
| incremental (edit → verify) | append a comment to `PingAction.java`, `verify.sh` | exit 0, **6 s** (plugin recompiled; test result cached because class bytes unchanged — a real code edit adds ~1.3 s test) |
| incremental (revert) | remove the comment, `verify.sh` | exit 0, **1 s** (disk-cache hit) |
| error paths | `GERRIT_TREE=/nonexistent verify.sh`; verify.sh from a copy whose symlink points elsewhere; `gerrit-tree.sh` without args / bad dir | all exit 2 with a hint naming the `gerrit-tree.sh` command to run |
| jar | `unzip -p bazel-bin/plugins/demo-plugin/demo-plugin.jar META-INF/MANIFEST.MF` | `Gerrit-PluginName/Module/SshModule` present, `Gerrit-ApiVersion: 3.14.5-SNAPSHOT`, 7 KB, contains `Documentation/*.md` |

Stage decision: in-tree `tools/verify.sh` is well under the 20 s bar (1–6 s warm); `tools/quick-check.sh` (0.6 s) remains the fallback A7 sets as `gerrit-stack.verify-cmd`. Either is fine for RUN.md.

## Gerrit-tree quirks / findings
- `plugins/demo-plugin` is **already gitignored** by the tree (`.gitignore:39 /plugins/*`), so the symlink never shows in `git status`. `user.bazelrc` is **not** ignored → shows as `?? user.bazelrc` in the tree (expected; the tree's `.bazelrc` ends with `try-import %workspace%/user.bazelrc`, and later flags override its `--disk_cache=~/.gerritcodereview/bazel-cache/cas` / `--repository_cache=…/repository`).
- **Warm cache found and reused:** `~/.gerritcodereview/bazel-cache/repository` (4.1 GB) and `…/cas` (5.7 GB) existed from an earlier Gerrit build on this machine. `gerrit-tree.sh` seeds `~/.cache/gerrit-stack-demo/bazel-repo` from the repository cache once (`cp -Rc` = APFS clonefile, ~12 s, no extra disk; falls back to `cp -R`), which is why the "10–40 min" first build took 60 s. The disk cache (`bazel-disk`) starts empty on purpose so warm-run numbers are honest.
- The tree's head is past the 3.14.4 tag: in-tree plugin API reports `3.14.5-SNAPSHOT`, while `quick-check.sh` compiles against the released `3.14.4` jar and the Docker image is 3.14.4. No API difference for anything the skeleton uses (both compile), but keep it in mind if a greeting change uses a newer server API.
- `PluginConfig` keys cannot contain dots (see above) — `pingMessage`, not `ping.message`.
- `gerrit-tree.sh` writes `git config gerrit-stack.gerrit-tree` **only when the plugin dir is the top level of its own git repo** (i.e. the `demo/work/demo-plugin` clone). For `demo/skeleton` (inside this repo) it skips, so this repo's `.git/config` is untouched; `verify.sh` then falls back to `$GERRIT_TREE` → `~/workspace/open/gerrit-3.14`.
- Machine quirks hit while working: the Bash tool's shell is zsh (`${PIPESTATUS[0]}` is empty; use `$pipestatus`), and interactive `cp/mv/rm` are aliased to `-i` (scripts with a bash shebang are unaffected).

## Open issues
- None blocking. Optional: A7's `feature-request.md` may phrase the config key as `greeting.prefix`; the skeleton README explains the flat-key mapping, so an agent will land on `greetingPrefix`.
- `Implementation-Version: unknown` in the manifest (bazlets reads the plugin's git describe; the symlinked dir is inside a repo without tags) — cosmetic.
