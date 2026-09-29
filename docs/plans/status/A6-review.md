# A6-review — status

status: done

## Context

Resumed WP: a prior A6-review agent wrote `skills/gerrit-review/SKILL.md`
(138 lines) and `references/{conventional-comments,review-json}.md`, then was
killed by a rate limit before verifying. This pass re-read the sources of
truth and verified the inherited draft item by item rather than rewriting it.

## Changes vs the inherited draft

Only one substantive fix was needed:

- **`skills/gerrit-review/SKILL.md`** — moved `## Pre-flight checklist` to be
  the last section (it was previously followed by `## References`). Now
  matches the sibling `gerrit-stack` skill's structure and the brief's
  explicit "Pre-flight checklist as the last section" requirement. No other
  content changed; line count is unchanged (138).
- `references/conventional-comments.md` and `references/review-json.md`:
  no changes — both verified accurate as written (see below).
- `docs/plans/status/A6-review.md` — this file (new).

## Verification

- **Description vs SPEC line 149**: extracted the `**gerrit-review**`
  description sentence from
  `~/workspace/open/code_review_like_a_pro/.claude/plans/2026-09-29-stack-changes-talk-and-gerrit-stack-plugin.md`
  line 149 and diffed it programmatically against the `description:` field in
  `SKILL.md` — byte-for-byte match (480 chars, well under the 1024-char
  frontmatter limit). Frontmatter is a plain two-key (`name`, `description`)
  block, single-line description, no tabs — valid YAML.
- **Tool map / fallback contract**: cross-checked every fallback cell against
  the execution plan's "Interface contract" (line 151, `gerrit-rest.py`
  subcommand list) and the actual `scripts/gerrit-rest.py` (`COMMANDS` dict +
  argparse subparsers): `related`, `comments --unresolved`, `review --message
  --comment FILE:LINE:MSG --in-reply-to --resolved`, `detail`, `query` are
  all used correctly and no invented subcommand or flag appears. The MCP
  column was verified against the real
  `~/.claude/plugins/cache/gerrit-mcp/gerrit/70a4f8f7e72a/gerrit_mcp_server/main.py`
  source (not just the brief's summary):
  - `post_review_comment` has no `in_reply_to` parameter — confirmed in
    source, so the skill's claim that it "can never answer a thread" and
    routes threaded replies through `post_draft_comment`/`publish_drafts`
    instead is correct, not a deviation from spec.
  - `publish_drafts(change_id, message=None, labels=None)` sends
    `{"drafts": "PUBLISH_ALL_REVISIONS", ...}` (all drafts, not just ours) —
    confirmed in source; the skill's "publishes every draft" warning is
    accurate.
  - `list_change_comments`'s text rendering (`L<line>: [<author>] (<updated>)
    - UNRESOLVED|RESOLVED id=<id>`, grouped under `File: <path>`, omitting
    `in_reply_to`/`patch_set`/`range`) matches the source's f-string output
    exactly.
  - `get_related_changes`'s flattened return shape
    (`{change_id, revision_id, related_changes: [...], note}` with `note`
    set only when the list is empty) matches the source exactly.
  - `query_changes(query, limit=...)` and `get_change_details`'s hard-coded
    `CURRENT_REVISION`/`CURRENT_COMMIT`/`DETAILED_LABELS` options both
    confirmed in source.
- **Process A**: chain resolution via `get_related_changes`, threads table
  columns (`change | file:line | author | unresolved | summary`), grouping
  by `in_reply_to` with the text-mode fallback caveat, and "unresolved only
  by default" are all present and correct.
- **Process B**: code-change threads are hand back to `gerrit-stack` Phase 5
  and the reply is posted only after `get_change_details` confirms the new
  patch set; answer-only threads use Conventional Comments with `Done.` +
  reason when fixed, `in_reply_to`, and `unresolved:false` only when
  addressed. The whole batch is shown and nothing posts before approval.
  Posting uses `post_review_comment`/the fallback with `labels=None` always.
- **Process C**: author notes use `note (non-blocking): …`, posted via
  `post_review_comment(..., unresolved=False, labels=None)`.
- **Hard rules**: no labels/votes, no submit, no
  `set_ready_for_review`/`set_work_in_progress`/`abandon_change`/`revert_*`,
  batch approval required first, replies ≤ 3 sentences, disagreement cites a
  principle/ADR/doc, one round then escalate — all present. No
  `cd <dir> && git …` anywhere; all git recipes use `git -C <dir> …` or a
  plain `git log`/`git commit` without a leading `cd`.
- **References accuracy**: `conventional-comments.md` has exactly the nine
  spec labels (`praise`, `nitpick`, `suggestion`, `issue`, `todo`,
  `question`, `thought`, `chore`, `note`) and the three decorations
  (`(non-blocking)`, `(blocking)`, `(if-minor)`). `review-json.md`'s
  `CommentInfo`/`ReviewInput`/`CommentInput`/`RelatedChangesInfo` shapes and
  its concrete `ReviewInput` JSON example (the "Done." reply body) carry no
  `labels` key.
- **Pre-flight checklist**: now the last section in `SKILL.md`; re-checked
  every bullet against the Hard rules section it summarizes.
- Ran `claude plugin validate ./ --strict` from the repo root:
  `✔ Validation passed`.
- Confirmed no files outside this WP's ownership
  (`skills/gerrit-review/SKILL.md`,
  `skills/gerrit-review/references/conventional-comments.md`,
  `skills/gerrit-review/references/review-json.md`,
  `docs/plans/status/A6-review.md`) were created, edited, or deleted. No
  `git add`/`git commit` run.

## Open issues

- None found. The inherited draft was already source-verified against the
  live `gerrit-mcp` plugin code, not just the brief's summary of it; the
  only correction needed was the Pre-flight-checklist section ordering.
