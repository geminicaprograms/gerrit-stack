# A3 — `gerrit-rest.py` (MCP fallback REST client)

status: done

Resumed work package: `scripts/gerrit-rest.py` (865 lines) and
`tests/test_gerrit_rest.py` (756 lines) already existed from a prior run of
this WP, killed by a rate limit before verification. This pass verified,
found and fixed one real cross-WP contract deviation, and re-verified green.

## Files written (owned by A3; nothing else touched)

- `scripts/gerrit-rest.py` — unchanged in structure; two edits this pass:
  - `emit()` flipped to default to raw JSON, `--table` now opt-in (was the
    reverse). See "Deviation found and fixed" below.
  - top docstring and `--json`/`--table` `--help` text updated to match.
- `tests/test_gerrit_rest.py` — 13 existing tests updated to pass `--table`
  explicitly where they assert on the human-readable rendering (their intent
  was always to exercise `render_*`, not the default), plus one new test
  (`test_default_output_is_json`) locking in that a bare invocation returns
  parseable JSON identical to the server payload.
- `docs/plans/status/A3-rest.md` (this file).

## What was already there (verified correct, no changes needed)

All required subcommands present with the exact REST calls from the
contract: `related` (`GET revisions/current/related`), `comments
[--unresolved]` (`GET comments`), `review --message M [--comment
FILE:LINE:MSG]... [--in-reply-to ID] [--resolved]` (`POST
revisions/current/review`, confirmed never emits `labels`), `rebase-chain
[--base X]` (`POST rebase:chain`), `topic` (`PUT topic`), `hashtags --add
T... [--remove T...]` (`POST hashtags`), `submitted-together` (`GET
submitted_together?o=NON_VISIBLE_CHANGES`), `detail` (`GET
detail?o=CURRENT_REVISION&o=CURRENT_COMMIT`), `query [--limit N]` (`GET
/changes/?q=...&n=N&o=CURRENT_REVISION`), and `review-metrics --owner U
--since YYYY-MM-DD [--project P] [--limit N]` (per-change rows via one
`/changes/` search + per-change `comments`/`related` calls, medians summary
split all/chain/solo).

`<change>` ref parsing (number / full Change-Id / `project~branch~Change-Id`
with per-segment URL-encoding / Gerrit URL incl. legacy `#/c/<n>/`), host
resolution precedence (`--host` → `GERRIT_HOST` → `git config
gerrit-stack.host` → http(s)-only `remote.origin.url` → exit 2), `~/.netrc`
auth (`NETRC` env honoured, hostname-scoped, malformed file tolerated) with
`/a/` prefix + Basic header when present else anonymous, `--anonymous`
override, XSSI-prefix stripping, `-v` request logging, and exit codes
(0/1/2, HTTP/network errors on stderr with status + body truncated to 300
chars) were all already correctly implemented and are stdlib-only.

## Deviation found and fixed: default output format

The interface contract (execution plan, "source of truth for all agents")
says plainly: *"XSSI strip. **JSON to stdout**; exit 1 network/HTTP, 2
usage."* The as-written script instead defaulted to a `--table`
human-readable rendering and required an explicit `--json` flag to get raw
JSON — the reverse of the contract.

This wasn't just a docstring nit: every downstream doc from other WPs that
this script feeds already assumes JSON-by-default. None of them pass
`--json`:
- `skills/gerrit-review/references/review-json.md`: *"`gerrit-rest.py
  comments <change> --unresolved` for the raw JSON"* and *"JSON to stdout"*
  in its own subcommand table.
- `skills/gerrit-stack/SKILL.md`, `references/chain-editing.md`,
  `references/push-options.md`, `references/troubleshooting.md`: every
  fallback invocation is bare (`gerrit-rest.py related <tip>`,
  `... detail <n>`, `... topic <n> <slug>`, etc.), consistent only with a
  JSON default.

Left as-is, this would have silently handed those consumers a table instead
of the JSON structure their own docs promise. Fixed by flipping `emit()` so
the default (no flag) is JSON and `--table` becomes the explicit opt-in for
a human-friendly rendering; `--json` still works (now a no-op vs. default,
kept for explicitness / muscle memory). `review-metrics`'s default (JSON
Lines rows + a summary line) was already contract-compliant and untouched.
Updated the script's docstring, `--help` text for `--json`/`--table`, and
the 13 tests whose assertions depended on the old default (added `--table`
to preserve their original intent of exercising the render functions) plus
added `test_default_output_is_json` as a regression lock.

## Verification run

| Command | Result |
|---|---|
| `python3 -m py_compile scripts/gerrit-rest.py tests/test_gerrit_rest.py` | clean |
| `python3 -m unittest discover -s tests -p 'test_gerrit_rest.py' -v` (repo root, no `tests/__init__.py`) | **55 tests, OK** (54 pre-existing + 1 new) |
| `./scripts/gerrit-rest.py --help` / `review --help` | usage renders correctly, exit 0; documents new JSON-default |
| `./scripts/gerrit-rest.py` (no args) | usage error to stderr, exit 2 |
| `./scripts/gerrit-rest.py --host <url> related not-a-change` | `unrecognised change reference` to stderr, exit 2 |
| `env -i ... ./scripts/gerrit-rest.py related 1` (no host anywhere) | `no Gerrit host: ...`, exit 2 |
| Standalone `http.server`-based stub (own script, not the unittest harness), executable invoked via subprocess | authenticated (`~/.netrc` match) request hit `/a/...` with correct `Authorization: Basic ...`; anonymous and `--anonymous` both hit the path with no `/a/` prefix and no auth header; `GERRIT_HOST` env resolution worked; explicit 404 and default table→JSON output both verified live, matching unit-test expectations |
| `git status --porcelain` | only `scripts/gerrit-rest.py` and `tests/test_gerrit_rest.py` modified; no `git add`/`commit` run |

## Open issues / notes for main and other WPs

1. **README.md / other WPs' docs never show a `--json` flag** — this is now
   correct by construction (default is JSON), no doc changes needed on
   their side because of this fix. Flagging only so A10-docs/A6-review know
   the default-output question is resolved and doesn't need its own pass.
2. **`review-metrics` output shape**: default is JSON Lines (one compact
   JSON object per change, then one `{"summary": ...}` line) — not a single
   JSON document — because A11's `collect.py` / `chain-metrics.sh` likely
   want to stream/aggregate per-change rows. `--json` on `review-metrics`
   gives a single `{"query", "changes", "summary"}` document instead if a
   consumer prefers that. Worth a one-line confirmation from A11 that JSON
   Lines is the shape it expects; trivial to swap the default if not.
3. Live-Gerrit end-to-end exercise (M1 in the verification summary —
   3-change chain, `related`/`review`/comments against real docker Gerrit
   3.14) was not run from this WP; that's Phase 4 (main agent) territory
   per the execution plan, and doesn't require any further gerrit-rest.py
   changes based on this verification.
