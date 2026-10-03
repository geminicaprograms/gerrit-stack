# C1-seeded — seeded chains for S3 (rework) and S4 (reviewer)

status: done

## Files written

- `evals/bench-rework/fix-mid-conflict/`: `case.yaml`, `prompt.md`, `fixture.sh`, `chain/0001…0006-*.patch`, `graders/{rework-landed,no-push-executed,no-manual-change-id}.md`
- `evals/bench-review/planted-defects/`: `case.yaml`, `prompt.md`, `fixture.sh`, `chain/0001…0002-*.patch`, `graders/{review-quality,no-mcp-comment-posted,no-mcp-drafts-published,no-rest-review-posted}.md`
- `docs/plans/status/C1-seeded.md` (this file)

Nothing else touched; no `git add`/`commit` in this repo.

## S3 chain (six changes, maintenance mode on the skeleton)

| # | subject | lines (+/-, incl. 14-line licence header per new file) | files |
|---|---|---|---|
| 1 | `feat: add demo-plugin-maintain global capability` | +79 / -3 | MaintainCapability(+Test), Module, about.md |
| 2 | `feat: track per-project maintenance state` | +103 | MaintenanceState(+Test), Module |
| 3 | `feat: add maintenanceMessage setting` | +31 | DemoPluginConfig(+Test), config.md |
| 4 | `feat: add REST views to read and switch maintenance` | +184 | MaintenanceViews(+Test), Module, about.md |
| 5 | `feat: answer ping with 503 while in maintenance` | +140 / -12 | DemoPluginConfig(+Test), MaintenanceException (+ExceptionHook), Module, PingAction(+Test), PingCommand, docs |
| 6 | `feat: add SSH command to show and switch maintenance` | +178 | MaintenanceCommand(+Test), SshModule, about.md |

Conflict design: change 3 adds `public String maintenanceMessage()`; change 5 rewrites exactly that
getter (signature `maintenanceMessage(String project)`, `${project}` expansion, javadoc) and adds a
constant on the line right below change 3's constants. The reviewer comment (case.yaml
`rework.message`) asks for a 200-character cap inside that getter + javadoc + a test, so any fix in
change 3 collides with change 5. Second, quieter trap: the new cap test merges cleanly but calls the
no-arg getter, which change 5 removes — `quick-check.sh` (main sources only) does not see it; the
llm rubric mentions it.

Design note (found by running the tests): `Response.withStatusCode` rejects 5xx, so change 5 throws
`MaintenanceException` and a bound `ExceptionHook` maps it to 503.

## Fixture mechanics worth knowing

- `git am` runs `applypatch-msg`, not `commit-msg`: the fixture installs the real hook under that
  name while applying the patches and removes it afterwards (workspace ends with `commit-msg` only).
- The hook derives the Change-Id from committer ident + second-resolution date + message, so two
  fixture runs in the same second would yield identical ids. The fixture offsets the committer date
  by a per-run random number of seconds (≤ ~12 days back, 1 s apart per change). Verified: two runs
  started back to back share 0 Change-Ids. Patches carry no Change-Id (fixture refuses one that does).
- Base commit carries `.gerrit-stack` (`commit-lint = auto`, `comment-style = conventional`),
  `commitlint.config.mjs`, `.gitreview`; pushed to `../remote.git` master; chain = `origin/master..HEAD`.

## Conflict proof (fresh fixture workspace)

```
$ GIT_SEQUENCE_EDITOR="sed -i.bak 3s/^pick/edit/" git rebase -i origin/master   # stop at change 3
Rebasing (3/6)Stopped at 4466901...  # feat: add maintenanceMessage setting
You can amend the commit now, with

stopped at: feat: add maintenanceMessage setting
$ git apply reviewer-fix.patch && git commit -a --amend --no-edit && git rebase --continue
quick-check.sh: exit 0 after 0s (7 files)
Rebasing (4/6)Rebasing (5/6)Auto-merging src/main/java/com/example/demoplugin/DemoPluginConfig.java
CONFLICT (content): Merge conflict in src/main/java/com/example/demoplugin/DemoPluginConfig.java
Auto-merging src/test/java/com/example/demoplugin/DemoPluginConfigTest.java
error: could not apply a9bc233... feat: answer ping with 503 while in maintenance
Could not apply a9bc233... # feat: answer ping with 503 while in maintenance
$ git status --short
UU src/main/java/com/example/demoplugin/DemoPluginConfig.java
A  src/main/java/com/example/demoplugin/MaintenanceException.java
M  src/main/java/com/example/demoplugin/Module.java
M  src/main/java/com/example/demoplugin/PingAction.java
M  src/main/java/com/example/demoplugin/PingCommand.java
M  src/main/resources/Documentation/about.md
M  src/main/resources/Documentation/config.md
M  src/test/java/com/example/demoplugin/DemoPluginConfigTest.java
M  src/test/java/com/example/demoplugin/PingActionTest.java
$ git diff   # conflict hunk
diff --cc src/main/java/com/example/demoplugin/DemoPluginConfig.java
index fdf4be8,6c8ba8b..0000000
--- a/src/main/java/com/example/demoplugin/DemoPluginConfig.java
+++ b/src/main/java/com/example/demoplugin/DemoPluginConfig.java
@@@ -38,7 -38,7 +38,11 @@@ public class DemoPluginConfig 
    static final String DEFAULT_PING_MESSAGE = "pong";
    static final String KEY_MAINTENANCE_MESSAGE = "maintenanceMessage";
    static final String DEFAULT_MAINTENANCE_MESSAGE = "demo-plugin is under maintenance";
++<<<<<<< HEAD
 +  static final int MAX_MAINTENANCE_MESSAGE_LENGTH = 200;
++=======
+   static final String PROJECT_PLACEHOLDER = "${project}";
++>>>>>>> a9bc233 (feat: answer ping with 503 while in maintenance)
  
    private final PluginConfig cfg;
  
@@@ -53,13 -53,11 +57,22 @@@
    }
  
    /**
++<<<<<<< HEAD
 +   * Message returned instead of the ping message while a project is in maintenance, capped at
 +   * {@value #MAX_MAINTENANCE_MESSAGE_LENGTH} characters (longer values are truncated).
 +   */
 +  public String maintenanceMessage() {
 +    String message = cfg.getString(KEY_MAINTENANCE_MESSAGE, DEFAULT_MAINTENANCE_MESSAGE);
 +    return message.length() > MAX_MAINTENANCE_MESSAGE_LENGTH
 +        ? message.substring(0, MAX_MAINTENANCE_MESSAGE_LENGTH)
 +        : message;
++=======
+    * Message returned instead of the ping message while {@code project} is in maintenance; every
+    * {@code ${project}} in the configured text is replaced with the project name.
+    */
+   public String maintenanceMessage(String project) {
+     return cfg.getString(KEY_MAINTENANCE_MESSAGE, DEFAULT_MAINTENANCE_MESSAGE)
+         .replace(PROJECT_PLACEHOLDER, project);
++>>>>>>> a9bc233 (feat: answer ping with 503 while in maintenance)
    }
  }
$ # resolve: keep both (placeholder expansion, then the 200-character cap); adapt the cap test to the new signature
$ git add -A && git rebase --continue
Successfully rebased and updated refs/heads/master.
$ git rebase --exec "bash tools/quick-check.sh" origin/master   # every commit compiles
quick-check.sh: exit 0 after 0s (6 files)
quick-check.sh: exit 0 after 1s (7 files)
quick-check.sh: exit 0 after 0s (7 files)
quick-check.sh: exit 0 after 1s (8 files)
quick-check.sh: exit 0 after 0s (9 files)
quick-check.sh: exit 0 after 1s (10 files)
$ git log --format="%h %s" origin/master..HEAD
6cc201d feat: add SSH command to show and switch maintenance
32c0f54 feat: answer ping with 503 while in maintenance
fa32a9f feat: add REST views to read and switch maintenance
6499ff7 feat: add maintenanceMessage setting
9bbd93e feat: track per-project maintenance state
0905e8a feat: add demo-plugin-maintain global capability
Change-Id set identical before/after (6 ids)
conflict markers in tip tree: 0
unit tests at tip (main+test compiled with truth/mockito/junit jars): OK (24 tests)
```

The same stop occurs with a `fixup!` commit placed on top of change 3 and changes 4–6 replayed onto
it (`CONFLICT (content)` in DemoPluginConfig.java while applying change 5). A fixup created at the
tip cannot exist without already resolving the conflict by hand.

## S4 chain (two changes, ping rate limit)

| # | subject | lines | note |
|---|---|---|---|
| 1 | `feat: add pingRateLimit setting` | +43 | clean |
| 2 | `feat: rate limit the ping REST view` | +165 / -4 (2 new files with headers) | under review, three planted defects |

Planted (all compile, shipped tests stay green):
- `off-by-one` (blocking) `PingRateLimiter.tryAcquire`: `incrementAndGet() < limit` — limit N answers N-1 pings, limit 1 rejects all; the test never asserts the 2nd ping.
- `magic-429` (nit) `PingAction.apply`: bare literal `429` in `Response.withStatusCode`.
- `static-map` (question) `PingRateLimiter`: `@Singleton` with a `static` mutable `WINDOWS` map.

## Verification (all run, all green)

- `shellcheck -x` + `bash -n` on both `fixture.sh`: clean.
- Rework fixture run 3× in temp dirs: 6 commits on top of base, exactly one Change-Id each (fixture asserts it, re-checked by hand), 6 distinct, `git status` clean, tip tree identical to the authoring repo.
- `git log --format=%B -n1 <sha> | commitlint` rc 0 for all 6 + base (rework) and both (review).
- `git rebase --exec 'bash tools/quick-check.sh' origin/master`: 6× `exit 0` (rework); per-commit checkout loop 2× `exit 0` (review).
- Beyond the contract: main + test sources compiled and JUnit run on EVERY commit of both seeded chains and of the reworked chain, with truth 1.4.4 / mockito 5.14.2 / junit 4.13.2 downloaded to the scratchpad (seeded S3: 6/10/12/15/19/23 tests OK; reworked: 6/10/13/16/20/24 OK; S4: 7/11 OK).
- Both `case.yaml` parse with `evals/run.py` `parse_yaml` (regexes single-quoted, flow maps in `planted` ok); both `prompt.md` frontmatters and all 7 graders load with `split_frontmatter` / `load_graders`; `no-rest-review-posted` pattern tested against 8 sample commands.
- No claude session started.

## Open issues

- Sizes: changes 4 and 6 of the S3 chain are 184 / 178 gross lines (156 / 150 without the two licence headers each), change 2 of S4 is 165 gross (137 net) — above the "~150" / "80–120" targets. Say so if they must shrink (would cost a test or the docs).
- `quick-check.sh` compiles `src/main` only, so the `builds_alone` metric cannot see the stale cap test after a careless conflict resolution; only the llm rubric and a real `tools/verify.sh` would.
- `tool_used` matches tool names exactly, hence two separate MCP graders; `post_draft_comment` is deliberately allowed (contract: Gerrit drafts are allowed).
- The planted `question` shares its file with the blocking bug; keyword lists were chosen not to overlap (`static`… vs `off-by-one`, `<=`…), but a comment that mentions both would count for both in the runner's keyword metric.
- Committer dates of the seeded commits lie up to ~12 days in the past (Change-Id salt); harmless for Gerrit, visible in `git log --format=%cd`.
