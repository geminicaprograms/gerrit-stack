---
name: gerrit-review
description: Read and respond to Gerrit review feedback on a change or relation chain via the official Gerrit MCP (REST fallback when it is absent). Lists unresolved threads, sorts each into fix / answer / defer / escalate, drafts the whole batch of replies, and posts them as threaded drafts only after the user approves the batch. Writes Conventional Comments labels only when the team opted in (`comment-style = conventional`). Use when asked to "check review comments", "reply to comments", "address feedback", "resolve threads", or when gerrit-stack reaches its iterate phase. Never votes, never submits.
---

# gerrit-review

The loop is **read → classify → draft the whole batch → user approves → post**.
Votes, submit, WIP/ready, abandon and revert are the user's; never call them,
never pass `labels`. Code fixes go through the `gerrit-stack` skill (Phase 5:
fixup → autosquash → `chain-status.sh --verify-ids` → re-push); Change-Id and
patch-set semantics are the `gerrit-workflow` skill's (from `gerrit@gerrit-mcp`).

**Comment style is a team choice.** Read it before drafting; per-clone config
wins over the committed team file:

```
git config --get gerrit-stack.comment-style || git config -f "$(git rev-parse --show-toplevel)/.gerrit-stack" --get gerrit-stack.comment-style
```

`conventional`: new top-level comments and author notes carry a label from
`references/conventional-comments.md` plus `(blocking)` / `(non-blocking)` when
the label does not say; a PreToolUse guard denies an unlabelled one. Replies
inside a thread stay free-form. `none` or unset (the default): plain sentences.
Reviewers' labels are read the same way in both modes.

## Tools

MCP tools are `mcp__plugin_gerrit_gerrit__<name>`. If they are absent or fail
to authenticate, use the REST fallback (`~/.netrc`; host from `--host` →
`GERRIT_HOST` → `git config gerrit-stack.host` → remote URL) or point the user
to `/gerrit:setup`. `change` = number, Change-Id, or `project~branch~Change-Id`.

| Need | MCP | Fallback |
|---|---|---|
| Whole chain | `get_related_changes(change_id)` | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" related <change>` |
| Threads on a change | `list_change_comments(change_id)` | `… gerrit-rest.py comments <change> --unresolved` (raw JSON with `id`/`in_reply_to`) |
| Reply in a thread | `post_draft_comment(…, in_reply_to=<id>)` then one `publish_drafts(change_id)` | `… gerrit-rest.py review <change> --message "Replies" --comment FILE:LINE:MSG --in-reply-to <id> [--resolved]` |
| Author note (top-level) | `post_draft_comment(…, unresolved=False)` in the same batch | `… gerrit-rest.py review <change> --comment FILE:LINE:MSG` |
| New patch set exists? | `get_change_details(change_id)` | `… gerrit-rest.py detail <change>` |
| Local chain intact? | — | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids` |

Never use `post_review_comment` for a reply: it cannot thread. `publish_drafts`
sends every draft on the change, including the user's own: run
`list_draft_comments` first and say so in the batch preview. Payload shapes:
`references/review-json.md`.

## Read

1. **Target**: a change → that change; a chain → `get_related_changes`, only
   `NEW` entries. Warn if any entry's `revision_number` is behind its
   `current_revision_number` (the local chain may be stale).
2. **Comments** per change. A thread starts at a comment without
   `in_reply_to`; it is unresolved when its last comment is unresolved; replies
   go to that last comment's `id` (the MCP text output lacks ids — use the
   fallback `comments` command before replying).
3. **Table**, oldest change first, unresolved threads only:

```
change | file:line | author | summary
42     | src/.../GreetingRestHandler.java:23 | Rena | issue (blocking): prefix can be null → NPE
43     | README.md:5 | Rena | nitpick: "comand" → "command"
2 unresolved threads on 2 of 3 changes.
```

## Respond

| Bucket | When | Do |
|---|---|---|
| **fix** | a code change you agree with | hand to `gerrit-stack` Phase 5; the fix is folded into the owning change, never a new "fix review" commit; post `Done.` only after `get_change_details` shows the new patch set |
| **answer** | a question, or a request you decline with a reason | reply; no code change |
| **defer** | valid but out of scope | reply naming the follow-up; stays unresolved unless marked non-blocking |
| **escalate** | you already disagreed once, or the decision is not yours | no reply; show the user both positions |

Replies: at most 3 sentences; `Done.` plus what changed, or a direct answer; a
disagreement cites a principle or doc; `unresolved=False` only when fixed and
pushed, or answered with no change asked.

Show the whole batch and stop:

```
Ready to post 2 replies. Nothing sent yet.
# | change | file:line | in_reply_to | unresolved after | reply
1 | 42 | GreetingRestHandler.java:23 | a1b2c3 | no | Done. Falls back to "" when the prefix is unset (PS2).
2 | 43 | README.md:5 | g7h8i9 | no | Done.
Drafts already on 43: 1 (README.md:9), published with these.
Post? (y/n/edit N)
```

Post only after a yes to **this** batch — "just do it" earlier in the
conversation is not approval of a batch the user has not seen. After the yes,
per change: one draft per row, then one `publish_drafts(change_id)`; if a draft
fails, do not publish that change. Then read again and report what remains;
the user votes and submits.

## Pre-flight Checklist

- [ ] Only `NEW` changes of the target; stale patch sets flagged.
- [ ] Every unresolved thread has one bucket; fix replies wait for the new
  patch set and `--verify-ids` says `ok`.
- [ ] `comment-style` read (clone config, else `.gerrit-stack`); labels on new
  top-level comments only when it is `conventional`.
- [ ] Replies ≤ 3 sentences, each with `in_reply_to`; one disagreement round per
  thread.
- [ ] No `labels`, vote, submit, WIP, abandon or revert.
- [ ] Existing drafts listed; the user said yes to this batch.
- [ ] Git recipes use `git -C <dir>`; no hand-written `Change-Id:`.
