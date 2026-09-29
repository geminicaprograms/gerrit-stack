# Tests

Bash code is tested with [bats-core](https://github.com/bats-core/bats-core) (≥ 1.5), Python with `unittest`. Every `*.bats` file starts with `load helpers` and calls `common_setup` from its `setup()`: that gives each test a hermetic git environment (throw-away `$HOME`, no global/system git config, fixed author identity) under `$BATS_TEST_TMPDIR`, so nothing is ever written outside the temp dir. `tests/helpers.bash` provides the shared fixtures: `make_plain_repo` (a non-Gerrit repo with one commit), `make_gerrit_repo [name]` (a Gerrit-looking work repo: local bare remote `remote.git`, `remote.origin.push=HEAD:refs/for/master`, a committed `.gitreview`, `refs/remotes/origin/master`, and the **real** Gerrit `commit-msg` hook from `tests/fixtures/commit-msg` installed — Change-Id trailers are never hand-written, the hook adds them), `commit_file <repo> <path> <content> <subject>` (prints the new sha), `hook_json <event> <cwd> <command> [session_id] [tool_response_json]` (the stdin JSON a Claude Code hook receives) and `run_hook <script> <json>` (`run --separate-stderr`, so `$status`, `$output` and `$stderr` are all available afterwards); `assert_eq`, `count_lines`, `physical_path` and `fail` are small assertion helpers. Set `GERRIT_STACK_TRACE=<file>` to make hooks append one `script<TAB>verb<TAB>decision` line per invocation (`gs_trace`). Files: `lib.bats` (`scripts/lib/gerrit-detect.sh` + `chain.sh`), `hooks.bats` (one test per hook rule), `tools.bats` (tool scripts), `metrics.bats`, and `test_*.py` for the Python scripts.

Run from the repository root (macOS ships bash 3.2 and the libraries must stay compatible with it):

```sh
brew install bats-core shellcheck        # once
bats tests/                              # all bash tests
bats --print-output-on-failure tests/lib.bats
python3 -m unittest discover -s tests -p 'test_*.py' -v
make lint test test-py                   # shellcheck + bats + unittest
```
