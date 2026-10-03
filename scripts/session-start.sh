#!/usr/bin/env bash
# scripts/session-start.sh — SessionStart hook for gerrit-stack.
#
# Prints `additionalContext` describing the Gerrit setup of the session's cwd
# (remote/host/branch/project, commit-msg hook state, local chain length), the
# active team conventions (commitlint, Conventional Comments) and the default
# workflow. Silent (exit 0, no output) outside Gerrit repos.
# Fail-open: any unexpected error exits 0 silently.
set -uo pipefail
trap 'exit 0' ERR

command -v jq >/dev/null 2>&1 || exit 0
input=$(cat 2>/dev/null) || exit 0
[ -n "$input" ] || exit 0
cwd=$(printf '%s' "$input" | jq -r '.cwd // empty' 2>/dev/null) || exit 0
[ -n "$cwd" ] || exit 0
[ -d "$cwd" ] || exit 0
cd "$cwd" || exit 0

# shellcheck source-path=SCRIPTDIR
# shellcheck source=lib/chain.sh
source "$(dirname "${BASH_SOURCE[0]}")/lib/chain.sh"

gs_detect || exit 0

plugin_root=${CLAUDE_PLUGIN_ROOT:-}
if [ -z "$plugin_root" ]; then
  plugin_root=$(cd "$(dirname "${BASH_SOURCE[0]}")/.." 2>/dev/null && pwd -P) || plugin_root=''
fi
install_cmd="bash \"${plugin_root}/scripts/install-commit-msg-hook.sh\""

if [ "${GS_HOOK_OK:-0}" = 1 ]; then
  hook_state="installed"
else
  hook_state="MISSING (run: $install_cmd)"
fi

# shellcheck disable=SC2119  # base defaults to $GS_BASE
chain=$(gs_chain_commits)
n=0
if [ -n "$chain" ]; then n=$(printf '%s\n' "$chain" | grep -c '' || true); fi
if [ -n "${GS_BASE:-}" ]; then
  base_disp=${GS_BASE#refs/remotes/}
  base_disp=${base_disp#refs/heads/}
  chain_disp="$n ahead of $base_disp"
else
  chain_disp="unknown (no remote tracking base: fetch $GS_REMOTE first)"
fi

host_disp=${GS_HOST:-}
[ -n "$host_disp" ] || host_disp="(not http)"

# team conventions: named only when active (settings: git config gerrit-stack.*
# of this clone, else the committed .gerrit-stack file)
conv=''
conv_line=''
if gs_commitlint_active; then
  conv="commit messages are checked by commitlint (the repo's own config; after each commit and before a push to refs/for)"
elif [ "$(gs_config commit-lint auto)" != off ] && gs_commitlint_config; then
  conv_line="
Note: this repo has a commitlint config but the commitlint command is not installed (not on PATH, no node_modules/.bin/commitlint), so commit messages are not checked here; nothing is blocked and nothing gets installed."
fi
if [ "$(gs_config comment-style none)" = conventional ]; then
  conv="${conv:+$conv; }review comments use Conventional Comments (new comments start with a label such as 'issue (blocking):'; replies stay free-form)"
fi
if [ -n "$conv" ]; then
  conv_line="
Team conventions: $conv.$conv_line"
fi

text="[gerrit-stack] This repository pushes to Gerrit.
remote: $GS_REMOTE; host: $host_disp; branch: $GS_BRANCH; project: $GS_PROJECT
commit-msg hook: $hook_state
local chain: $chain_disp$conv_line
Default workflow (not optional): for any change touching more than one file, invoke the gerrit-stack skill BEFORE editing. It plans a relation chain (stack-planner), one concern per commit (the Change-Id comes from the commit-msg hook — never write it by hand), and pushes HEAD:refs/for/$GS_BRANCH only after the user's explicit confirmation. Grouping (none / hashtag / topic) is the user's choice — never assume it. Review feedback: gerrit-review skill. Chain status: Gerrit MCP get_related_changes.
Non-negotiables, even if the user asks for the opposite: never type a Change-Id trailer, never use --no-verify, never --amend -m, never push to refs/heads/*. Say why in one sentence, then do it the right way (hook adds the id; push HEAD:refs/for/<branch> after confirmation). The PreToolUse guard denies these commands anyway, so do not look for a bypass."

gs_trace session-start start context
jq -cn --arg ctx "$text" \
  '{hookSpecificOutput:{hookEventName:"SessionStart",additionalContext:$ctx}}'
exit 0
