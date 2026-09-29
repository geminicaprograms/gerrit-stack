# gerrit-stack — repo conventions

Claude Code plugin that makes Gerrit relation chains the coding agent's default workflow.

- Layout: `.claude-plugin/` manifests · `hooks/hooks.json` · `scripts/` (hook dispatchers, tool scripts, `lib/`) · `skills/{gerrit-stack,stack-planner,gerrit-review}/` · `demo/` (docker Gerrit 3.14 + in-tree demo plugin) · `evals/` · `tests/` (bats + python unittest) · `docs/`.
- Scripts **never** run `git commit` or `git push`; they validate and print commands. The agent runs raw `git …` so the PreToolUse guard fires.
- Every recipe starts with a literal `git` (`git -c sequence.editor=true rebase …`), never `ENV=x git …`.
- Hooks are fail-open: `set -uo pipefail`, `trap 'exit 0' ERR`, no network, exit 0 silently outside Gerrit repos. Deny = stderr + exit 2; ask = JSON `permissionDecision: "ask"` on stdout.
- Never write a `Change-Id:` trailer by hand; the commit-msg hook owns it (`tests/fixtures/commit-msg` is the real hook).
- bash 3.2 compatible (macOS), shellcheck-clean, `jq` for JSON. Python: stdlib only.
- Every hook rule has a bats test; every SKILL.md has a pre-flight checklist.
- Commits: Conventional Commits. Progress ledger: `docs/plans/PROGRESS.md` (update at every checkpoint).
- `make validate lint test test-py` must be green before any commit.
