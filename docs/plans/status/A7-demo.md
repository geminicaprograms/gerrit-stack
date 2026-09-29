# A7 — demo infra (docker Gerrit 3.14 + seed + stage script)

status: done — container `gerrit-stack-demo` left running (healthy), seeded; throwaway change **1** abandoned.

## Files written (all new; nothing else touched)
- `demo/docker-compose.yml` — project `name: gerrit-stack-demo`, service `gerrit` (`gerritcodereview/gerrit:3.14.4`, container `gerrit-stack-demo`, 8080/29418, `CANONICAL_WEB_URL`, volumes `gerrit-git/index/cache/db`, bind `./etc:/var/gerrit/etc`, curl healthcheck on `/config/server/version`, `restart: unless-stopped`).
- `demo/etc/gerrit.config` — full config per contract (DEVELOPMENT_BECOME_ANY_ACCOUNT, HTTP basic auth, sshd/httpd, LUCENE, sendemail off, allowRemoteAdmin, container user/javaOptions, enableSignedPush off, submitWholeTopic off).
- `demo/seed.sh` (`--reset-netrc`, `-v`), `demo/reset.sh` (`--netrc`), `demo/reviewer-comment.sh <n> [--file] [--line] [--text]` — bash 3.2, `set -uo pipefail`, `shellcheck -x` clean.
- `demo/feature-request.md` (greeting feature, `greetingPrefix`, REST `demo-plugin~greeting`, SSH `demo-plugin greet`), `demo/RUN.md` (7-minute stage script + T-30 checklist + fallback triggers + reset line).
- `docs/plans/status/A7-demo.md` (this file). Task-observer: observation #55 appended to `~/claude-skills-hub/skill-observations/log.md`.

## Verification (2026-09-30, live container)
| step | command | result |
|---|---|---|
| compose | `docker compose -f demo/docker-compose.yml config -q` | ok |
| lint | `shellcheck -x demo/seed.sh demo/reset.sh demo/reviewer-comment.sh`; `bash -n` | clean |
| up | `docker compose -f demo/docker-compose.yml up -d` | first-start init ≈ 90 s to `healthy` (init + reindex + 64 plugins preloaded) |
| seed #1 | `bash demo/seed.sh -v` | **2 s**; admin 1000000 (`username=admin`), rena 1000001, project created, clone + hook, skeleton commit `ecbbb62` pushed to `refs/heads/master`, gerrit-tree wired, MCP config written |
| seed #2 (idempotent) | `bash demo/seed.sh` | **0.5 s**, every step reports exists / present / in sync, exit 0, clone still clean at `ecbbb62` |
| seed `--reset-netrc` | `bash demo/seed.sh --reset-netrc -v` | drops entries, becomes `admin` via `/login/?user_name=admin`, rotates token `demo` (DELETE 204 → PUT 201), rewrites `~/.netrc` (600) + one-time `~/.netrc.gerrit-stack.bak`, `/a/accounts/self` 200 |
| accounts / project | `curl -n …/a/accounts/self`, `/a/accounts/rena`, `/a/projects/demo-plugin` | 200 / 200 / 200 (`Admin admin@example.com`, `Rena Reviewer rena@example.com`, `ACTIVE`) |
| hook + Change-Id | `ls .git/hooks/commit-msg`; `git log -1 --format=%B \| grep Change-Id` | hook 3127 B from `/tools/hooks/commit-msg`; `Change-Id: I97f8f0…` on the seed commit |
| git config | `git -C demo/work/demo-plugin config --get-regexp gerrit-stack` | `verify-cmd=bash tools/quick-check.sh`, `gerrit-tree=~/workspace/open/gerrit-3.14` (written by gerrit-tree.sh); no `grouping` |
| MCP config | `jq . ~/.claude/plugins/cache/gerrit-mcp/gerrit/70a4f8f7e72a/gerrit_mcp_server/gerrit_config.json` | valid; `default_gerrit_base_url` + 1 host `gerrit-stack demo` (`http_basic`, no username/token → netrc) |
| quick-check | `bash demo/work/demo-plugin/tools/quick-check.sh` | exit 0, **0.6 s** wall (5 files) |
| throwaway change | `TEST.md` → commit → `git push origin HEAD:refs/for/master` | change **1** created (`/c/demo-plugin/+/1`), push < 1 s |
| reviewer bot | `bash demo/reviewer-comment.sh 1`; again with `--file TEST.md --line 1 --text 'nit: …'` | both exit 0, print change URL; `GET /a/changes/1/comments` shows 2 unresolved comments by `rena` on `TEST.md:1` (default text = the `issue (blocking)` one); `Code-Review` `rena: -1` |
| cleanup | `POST /a/changes/1/abandon` → 200; `git reset -q --hard origin/master` | clone clean at `ecbbb62`; change 1 `ABANDONED` |
| reset.sh | run on a scratch copy (fake `$HOME`, scratch git repo, separate compose project name) | HEAD-restore path, no-HEAD strip path (`git config -f --unset …`), `--netrc` (keeps other machines/`default`/`macdef`, removes localhost + 127.0.0.1, counts 1/0), "nothing to remove" — all ok. (`compose down` in that sandbox failed only because the fake HOME hides `~/.docker/cli-plugins`; with the real HOME `down -v` on that project returned 0.) Not run against the live demo (container must stay up). |

## Gotchas / findings
- **Gerrit 3.14 default ACL has no `push` on `refs/heads/*` for Administrators** (only create/submit/forge/revert/editTopicName; `push` exists only under `refs/for/*`). The seed's direct push was rejected on the first run. seed.sh now grants `push` on `refs/heads/*` to the Administrators group **in `demo-plugin` only** (`POST /a/projects/demo-plugin/access`, idempotent via `GET /a/access/?project=`); the demo's own pushes stay on `refs/for/master`.
- **`/login/?user_name=admin` under DEVELOPMENT_BECOME_ANY_ACCOUNT creates the account when it does not exist** (with username already set → `PUT /accounts/self/username` answers 405, tolerated). seed.sh tries it first, so re-bootstrap after a token loss re-uses account 1000000; `?action=create_account` is only the fallback. The first account is in Administrators (rena/project creation worked). Gerrit also auto-adds an `initialToken` next to our `demo` token.
- **The tracked `demo/etc/gerrit.config` is rewritten by the container's first start** (entrypoint: `init --batch` adds `gerrit.serverId`, `container.javaHome`, `sendemail.smtpServer`, flogger `javaOptions`; then `git config --add` appends `-Djava.security.egd` again + two `--add-opens`). It shows as modified in `git status` after `demo-up`; that is by design (directory bind mount, see the compose file comment). `reset.sh` restores it from `git show HEAD:demo/etc/gerrit.config` (or strips the known keys when not committed). Keep `serverId` while volumes live — reset always does `down -v` first.
- The image runs as uid 1000 `gerrit` and has `curl` (healthcheck) but no `wget`. Bundled plugins live in the container FS (`/var/gerrit/plugins`, not a volume): a `down` (no `-v`) + `up` recreates the container without them — harmless for the demo; `reset.sh` + re-init brings them back.
- `~/.netrc` gets both `machine localhost` and `machine 127.0.0.1`; rewriting keeps other entries/comments/macdefs verbatim (token-boundary parser in python). rena's token is only in `demo/work/.rena-token` (600) and `reviewer-comment.sh` feeds it to curl through a `-K` config file, so it never appears in `ps` or output.
- `seed.sh` leaves the clone alone (warns) when it has uncommitted changes or unpushed commits other than its own `chore: seed demo-plugin skeleton` — a rehearsal in progress survives a re-seed; `reset.sh` is the clean slate. A failed seed push is retried on the next run (the seed commit is recognised by subject).
- Machine quirks hit: heredoc inside a python heredoc needs distinct delimiters (an inner `PY` terminator ended the outer one); the Bash tool's zsh + `cp -i` aliases as documented in AGENT-BRIEF.

## Open issues
- None blocking. `make demo-warm` (in-tree Bazel) was not re-run here (A8 verified it; `verify.sh` reads `gerrit-stack.gerrit-tree`, which seed.sh sets through `gerrit-tree.sh`).
- `demo/recording/` does not exist yet (RUN.md points to it; recorded at rehearsal #2 per SPEC).
