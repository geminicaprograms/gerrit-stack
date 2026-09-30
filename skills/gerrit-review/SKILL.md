---
name: gerrit-review
description: Read and respond to Gerrit review feedback on a change or relation chain via the official Gerrit MCP. Lists unresolved threads, drafts replies and comments in Conventional Comments format, posts them after the user approves the batch, hands back to the user for any vote or submit. Use when asked to "check review comments", "reply to comments", "address feedback", "resolve threads", or when gerrit-stack reaches its iterate phase. Never votes, never submits.
---

# gerrit-review

You are the author's hands on a Gerrit review, not the author's vote. The loop is always
**read → classify → draft the whole batch → user approves → post**. Votes, submit, WIP/ready,
abandon and revert belong to the human; this skill never touches them.

**REQUIRED BACKGROUND:** the `gerrit-stack` skill (Phase 5, Iterate) owns every code change
that a review asks for; the `gerrit-workflow` skill (from `gerrit@gerrit-mcp`) owns
Change-Id and patch-set semantics. Do not restate either here — hand off.

## Tool map

MCP tools are exposed as `mcp__plugin_gerrit_gerrit__<name>`. If they are absent, use the fallback script
(auth via `~/.netrc`, host from `--host` → `GERRIT_HOST` → `git config gerrit-stack.host` → remote URL).
`change` = change number, Change-Id, or `project~branch~Change-Id`; from a URL `/c/<proj>/+/<n>` use `<n>`.

| Need | MCP tool | Fallback |
|---|---|---|
| Whole chain of a change | `get_related_changes(change_id)` | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" related <change>` |
| Comments on one change | `list_change_comments(change_id)` (text; drops `in_reply_to`/`patch_set`) | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" comments <change> --unresolved` (raw JSON) |
| Reply inside a thread | `post_draft_comment(change_id, file_path, line_number, message, unresolved, in_reply_to=<id>)` then one `publish_drafts(change_id, message=None, labels=None)` per change | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" review <change> --message "Replies to review" --comment FILE:LINE:MSG --in-reply-to <id> [--resolved]` (one call per reply) |
| New comment / author note | `post_review_comment(change_id, file_path, line_number, message, unresolved, labels=None)` (one call per comment) | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" review <change> --message "Author notes" --comment FILE:LINE:MSG` |
| Confirm the new patch set exists | `get_change_details(change_id)` | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" detail <change>` |
| Find own changes needing attention | `query_changes("is:open owner:self has:unresolved", limit=20)` | `python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" query "is:open owner:self has:unresolved"` |
| Verify local chain before re-push | — | `bash "${CLAUDE_PLUGIN_ROOT}/scripts/chain-status.sh" --verify-ids` |

`post_review_comment` has no `in_reply_to`, so it can never answer a thread — a reply posted with it
becomes an orphan thread on the same line. Threaded replies go through drafts or the fallback.
`publish_drafts` publishes **every** draft on the change, including drafts the user left in the web UI;
say so in the batch preview. Shapes of every payload: `references/review-json.md`.

## Process A — Read

1. Resolve the target. A change number/URL/Change-Id → that change; "the chain", "all my changes",
   or a hand-off from `gerrit-stack` → `get_related_changes(change_id)` and keep only entries with
   `status == "NEW"` (skip `MERGED`/`ABANDONED`). Empty `related_changes` + `note` = solo change.
   If any entry has `revision_number < current_revision_number`, warn that the local chain may be
   stale before any fixup.
2. For each change, `list_change_comments(change_id)`.
3. Group comments into threads. Raw JSON: root = comment without `in_reply_to`, follow `in_reply_to`
   links; the thread is unresolved iff its chronologically **last** comment has `unresolved: true`.
   MCP text output lacks `in_reply_to`: group by `file:line` in listed order and treat the last
   entry's status as the thread status; if one line carries several threads, or you are about to
   reply, switch to the fallback `comments` command for the raw `id`/`in_reply_to` values.
4. Print the threads table, chain order (oldest change first), **unresolved only** by default;
   include resolved ones only when asked ("show all threads").

```
change | file:line | author | unresolved | summary
-------|-----------|--------|------------|----------------------------------------------
42     | src/main/java/com/example/GreetingRestHandler.java:23 | Rena | yes | issue (blocking): prefix can be null → NPE
42     | src/main/java/com/example/GreetingRestHandler.java:80 | Rena | yes | question: why not PluginConfigFactory?
43     | README.md:5 | Rena | yes | nitpick: "comand" → "command"
3 unresolved threads on 2 of 3 changes (41 has none).
```

## Process B — Respond

Classify every unresolved thread into exactly one bucket; write the bucket next to it.

| Bucket | When | What you do |
|---|---|---|
| **fix** | the comment asks for a code change you agree with (`issue`, `todo`, `chore`, agreed `suggestion`/`nitpick`) | map the change to its local commit (`git log --format='%h %s' --grep='Change-Id: <Change-Id>' <base>..HEAD`), add it to the fix list, hand the list to `gerrit-stack` Phase 5 (fixup → autosquash → `chain-status.sh --verify-ids` → re-push with confirmation, same grouping). Draft the reply now, post it **only after** `get_change_details` shows the new patch set |
| **answer** | `question`, `thought`, `note`, `praise`, or a request you decline with a reason | draft a reply; no code change |
| **defer** | valid but out of scope for this chain | draft a reply naming the follow-up Change-Id or ticket; keep `unresolved: true` unless the reviewer marked it `(non-blocking)` |
| **escalate** | you already disagreed once in this thread, or the reviewer asks for a decision that is not yours | draft nothing; list the thread for the human with both positions |

Reply rules (checked in pre-flight): grammar from `references/conventional-comments.md`; `Done.` + one
line saying what changed when fixed; otherwise `<label> [decorations]: <subject>` with `note`,
`question`, `thought` or `suggestion`; **≤ 3 sentences**; when disagreeing, cite a principle, ADR,
or doc link; `in_reply_to` = id of the thread's last comment; `unresolved: false` only when the fix is
in the pushed patch set or the thread asked for no change and your reply answers it.

Then show the **whole batch** in one message and stop. Nothing is posted until the user answers yes
in this turn; "just do it" earlier in the conversation is not approval of a batch they have not seen.

```
Ready to post 3 replies (0 new comments). Nothing sent yet.
# | change | file:line | in_reply_to | unresolved after | reply
1 | 42 | GreetingRestHandler.java:23 | a1b2c3 | no  | Done. Falls back to "" when greeting.prefix is unset (PS2).
2 | 42 | GreetingRestHandler.java:80 | d4e5f6 | yes | thought: reading the file keeps config hot-reloadable per ADR-007; PluginConfigFactory caches. Happy to switch if reload is not a requirement here.
3 | 43 | README.md:5               | g7h8i9 | no  | Done.
publish_drafts on 42 and 43 will also publish any drafts you left in the UI. Post? (y/n/edit N)
```

After approval: post exactly the approved rows, one tool call per row (`labels=None` always, or no
`--label` flag), then re-run Process A and print the table with `0 unresolved` or what remains.
The user votes and submits themselves — tell them the chain is ready for their vote.

## Process C — Author notes

Before or right after a push, pre-empt questions on your own change with `note (non-blocking): …`
on the exact line, e.g. `note (non-blocking): reviewer note: this change only moves code; behaviour
is unchanged.` Post with `post_review_comment(..., unresolved=False, labels=None)`; `line_number=0`
makes it a file-level note. Author notes join the same batch preview and wait for the same yes.

## Hard rules

- **Never** set `labels` (always `labels=None`; fallback has no label flag), never `set_ready_for_review`,
  `set_work_in_progress`, `abandon_change`, `revert_*`, never submit. Voting is the human's identity.
- Nothing is posted before the user approves the batch shown in this turn. Not one comment.
- `Done.` is only true after the patch set containing the fix is on the server; never resolve first and fix later.
- Replies ≤ 3 sentences. Disagreement cites a principle/ADR/doc link. One round of disagreement per
  thread, then escalate to the human.
- `unresolved: false` only when the concern is actually addressed; the reviewer closes what you disagreed on.
- Only touch changes returned by `get_related_changes` for the target; never `MERGED`/`ABANDONED` ones.
- Git recipes use `git -C <dir> …`, never `cd <dir> && git …`. Never write a `Change-Id:` trailer.

| Rationalization | Reality |
|---|---|
| "The user said 'just do it', so the batch preview is bureaucracy" | The preview is one message; a wrong reply on a review is public and permanent. Show it. |
| "A +1 would make the chain look ready for the demo" | A vote in the user's name is not yours to give. Say the chain is ready for their vote. |
| "I'll resolve the thread now and push the fix in a minute" | A resolved thread disappears from the reviewer's list. Fix, push, then `Done.`. |
| "One more explanation will convince the reviewer" | Second round = escalate. The human decides. |
| "`post_review_comment` is the tool the spec names, so use it for replies" | It cannot thread. Drafts + `publish_drafts` or the fallback `--in-reply-to`. |

## References

- `references/conventional-comments.md` — labels, decorations, reviewer label → author action, reply examples.
- `references/review-json.md` — `CommentInfo`, `ReviewInput`/`CommentInput`, `RelatedChangesInfo`, identifiers, `gerrit-rest.py` subcommands.

## Pre-flight checklist (re-read before the batch preview and again before posting)

- [ ] Target resolved via `get_related_changes`; only `NEW` changes in scope; stale patch sets flagged.
- [ ] Every unresolved thread has one bucket: fix / answer / defer / escalate.
- [ ] Every **fix** reply waits for a drift-free rewrite (the post-rebase hook feedback is the drift signal: `lost:`/`new:` → stop and repair; `chain-status.sh --verify-ids` confirms, and keeps reporting drift until `--snapshot` is run after the repair) and a new patch set confirmed via `get_change_details`.
- [ ] Every reply ≤ 3 sentences; `Done.` + why, or `<label> [decorations]: <subject>`.
- [ ] Every reply carries `in_reply_to` = last comment id of its thread (draft tool or `--in-reply-to`).
- [ ] `unresolved: false` only on fixed-and-pushed or answered-no-change threads.
- [ ] Every disagreement cites a principle/ADR/doc link; no thread has two rounds from me.
- [ ] No `labels`, no vote, no submit, no WIP/ready/abandon/revert call anywhere in the batch.
- [ ] The full batch (replies + author notes, with the `publish_drafts` warning) was shown and the user said yes this turn.
- [ ] Recipes use `git -C`, not `cd … && git …`; no hand-written `Change-Id:`.
