# Review JSON shapes used by gerrit-review

Field definitions from Gerrit `Documentation/rest-api-changes.txt` (3.14). MCP tool behaviour from
`gerrit@gerrit-mcp` `gerrit_mcp_server/main.py`. Every REST response starts with the XSSI prefix
`)]}'` on its own line; the MCP server and `gerrit-rest.py` strip it.

## 1. Identifying a change

| You have | Use as `change_id` / `<change>` | Notes |
|---|---|---|
| URL `https://host/c/<project>/+/<n>` or `/c/<project>/+/<n>/<ps>` | `<n>` (or `<project>~<n>`, the form Gerrit recommends) | `<ps>` is a patch-set number, not part of the id |
| Push output `remote: https://host/c/<project>/+/<n> <subject> [NEW]` | `<n>` | one line per change in the chain |
| Change-Id `I8473b95934b5732ac55d26311a706c9c2bde9940` | the Change-Id | ambiguous if the same Change-Id exists on several branches |
| Triplet `<project>~<branch>~<Change-Id>` | as is | unambiguous; `refs/heads/` may be omitted from `<branch>` |
| Local commit | `git -C <dir> log -1 --format=%B <sha> \| grep '^Change-Id:'` | then look it up as above |

Reverse mapping (thread → local commit): `git -C <dir> log --format='%h %s' --grep='Change-Id: <Change-Id>' <base>..HEAD`.

## 2. `list_change_comments` — reading threads

REST: `GET /changes/{change}/comments` → map of file path → list of `CommentInfo`, files sorted by
path, comments per file sorted by patch set. `patch_set` and `author` are always set here.

`CommentInfo` fields the skill uses:

| Field | Meaning |
|---|---|
| `id` | URL-encoded UUID of the comment; the value to pass as `in_reply_to` |
| `path` | file path (absent when the comment sits inside the path-keyed map) |
| `line` | 1-based line; absent (or `0` on input) = file-level comment; equals `range.end_line` when `range` is set |
| `range` | `{start_line, start_character, end_line, end_character}`; start inclusive, end exclusive |
| `in_reply_to` | `id` of the parent comment; absent on a thread root |
| `message` | the comment text |
| `author` | `{_account_id, name, email, username}` |
| `unresolved` | whether the comment must be addressed; **the thread's state is the value on its chronologically last comment** |
| `patch_set` | patch set the comment was made on |
| `updated` | timestamp `YYYY-MM-DD HH:MM:SS.nnnnnnnnn` (UTC) |

Raw example — one thread of two comments, still unresolved because the last comment says so:

```json
{
  "src/main/java/com/example/GreetingRestHandler.java": [
    {
      "patch_set": 1,
      "id": "a1b2c3",
      "line": 23,
      "message": "issue (blocking): `prefix` can be null when the config key is unset; this NPEs on first request.",
      "updated": "2026-09-29 10:01:12.000000000",
      "author": {"_account_id": 1000001, "name": "Rena Reviewer", "email": "rena@example.com"},
      "unresolved": true
    },
    {
      "patch_set": 1,
      "id": "b2c3d4",
      "line": 23,
      "in_reply_to": "a1b2c3",
      "message": "question: is an empty string an acceptable fallback, or should the greeting be skipped?",
      "updated": "2026-09-29 10:04:40.000000000",
      "author": {"_account_id": 1000002, "name": "Demo Admin", "email": "admin@example.com"},
      "unresolved": true
    }
  ]
}
```

Threading algorithm on raw JSON:

1. root = comment without `in_reply_to`; children = comments whose `in_reply_to` points into the thread (follow transitively).
2. Sort the thread by `updated`; `thread.unresolved = last.unresolved`; `thread.reply_to = last.id`.
3. `thread.summary` = first line of `root.message`; `thread.author` = `root.author.name`.

**MCP output is text, not JSON.** `mcp__plugin_gerrit_gerrit__list_change_comments` renders each comment as
`L<line>: [<author name>] (<updated>) - UNRESOLVED|RESOLVED id=<id>` followed by the indented message,
grouped under `File: <path>` headers, and **omits `in_reply_to`, `patch_set` and `range`**. Approximate
threads by `(path, line)` in listed order and use the last entry's status; when a line carries
several threads or you need the exact `id` to reply to, use
`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" comments <change> --unresolved` for the raw JSON.

## 3. `ReviewInput` / `CommentInput` — what a reply sends

REST: `POST /changes/{change}/revisions/current/review` with a `ReviewInput`.

`ReviewInput` fields the skill may send: `message` (change-level message), `comments` (map path →
list of `CommentInput`), `drafts` (`PUBLISH`, `PUBLISH_ALL_REVISIONS`, `KEEP`; default `KEEP`).
Fields the skill **never** sends: `labels` (a vote), `ready`, `work_in_progress`, `reviewers`,
`on_behalf_of`.

`CommentInput` fields: `path` (optional inside the map), `line` (`0` = file comment; ignored when
`range` is set), `range`, `in_reply_to`, `message`, `unresolved`. Note the default: `unresolved`
"will default to false if the comment is an orphan, or the value of the `in_reply_to` comment if it
is supplied" — so a reply that omits `unresolved` inherits the reviewer's `true`. Always set it.

Concrete body for the batch row "Done." on line 23, threaded, resolving — no `labels` anywhere:

```json
{
  "message": "Replies to patch set 1 review",
  "comments": {
    "src/main/java/com/example/GreetingRestHandler.java": [
      {
        "line": 23,
        "in_reply_to": "b2c3d4",
        "message": "Done. Falls back to \"\" when greeting.prefix is unset (PS2).",
        "unresolved": false
      }
    ]
  }
}
```

Response is a `ReviewResult` (e.g. `{}` or `{"labels": {...}}` when labels were sent — ours never are).

How each tool maps onto this:

| Tool | Sends |
|---|---|
| `gerrit-rest.py review <change> --message M --comment FILE:LINE:MSG --in-reply-to ID [--resolved]` | exactly the body above; `--resolved` → `unresolved: false`, otherwise `true`; no label flag exists |
| `post_review_comment(change_id, file_path, line_number, message, unresolved=True, labels=None)` | `{"comments": {file_path: [{line, message, unresolved}]}}` — **no `in_reply_to`**, so only for new threads / author notes; `labels` must stay `None` |
| `post_draft_comment(change_id, file_path, line_number, message, unresolved=True, in_reply_to=ID)` | `PUT /changes/{change}/revisions/current/drafts` with `{path, line, message, unresolved, in_reply_to}` — visible to nobody until published |
| `publish_drafts(change_id, message=None, labels=None)` | `{"drafts": "PUBLISH_ALL_REVISIONS", "message": ...}` — publishes **all** drafts on the change, including any the user left in the UI; `labels` must stay `None` |

## 4. `get_related_changes` — the chain

REST: `GET /changes/{change}/revisions/current/related` → `RelatedChangesInfo`:

```json
{
  "changes": [
    {"project": "demo-plugin", "change_id": "I3e…", "_change_number": 43, "_revision_number": 1, "_current_revision_number": 1,
     "status": "NEW", "commit": {"commit": "9c1f…", "subject": "feat(ssh): add greet command", "parents": [{"commit": "5b2a…"}]}},
    {"project": "demo-plugin", "change_id": "I7f…", "_change_number": 42, "_revision_number": 1, "_current_revision_number": 2,
     "status": "NEW", "commit": {"commit": "5b2a…", "subject": "feat(rest): add greeting endpoint", "parents": [{"commit": "e0d4…"}]}},
    {"project": "demo-plugin", "change_id": "Ia1…", "_change_number": 41, "_revision_number": 1, "_current_revision_number": 1,
     "status": "MERGED", "commit": {"commit": "e0d4…", "subject": "feat(config): read greeting.prefix", "parents": [{"commit": "77aa…"}]}}
  ]
}
```

- Order is git commit order, **newest first** (descendants, then the queried change, then ancestors). Reverse it for the threads table.
- `status` ∈ `NEW`, `MERGED`, `ABANDONED`; only `NEW` changes take replies.
- `_revision_number < _current_revision_number` (change 42 above) means the chain member you are looking at is not the latest patch set — the local chain is probably stale; fetch and rebase (gerrit-stack Phase 6) before any fixup.
- `changes` empty = the change has no relation chain (solo change).

The MCP tool returns the same data flattened: `{change_id, revision_id, related_changes: [{change_number, change_id, project, subject, commit_sha, parents, status, revision_number, current_revision_number}], note}`; `note` is set only when the list is empty.

## 5. `get_change_details` / `detail` — confirming a new patch set

`GET /changes/{change}/detail?o=CURRENT_REVISION&o=CURRENT_COMMIT&o=DETAILED_LABELS` (the MCP always adds these three options). Fields used:
`_number`, `change_id`, `status`, `subject`, `current_revision` (sha), `revisions[<sha>]._number` (patch-set number), `has_unresolved_comments` may be absent — count threads yourself.
A "fix" reply is allowed once `revisions[current_revision]._number` is greater than the patch set the comment was made on.

## 6. `gerrit-rest.py` subcommands used here

`python3 "${CLAUDE_PLUGIN_ROOT}/scripts/gerrit-rest.py" [--host URL] <cmd> …` — JSON to stdout; exit 1 on network/HTTP error, 2 on usage error.

| Command | REST call | Used in |
|---|---|---|
| `related <change>` | `GET /changes/{c}/revisions/current/related` | Process A step 1 |
| `comments <change> [--unresolved]` | `GET /changes/{c}/comments`, filtered to unresolved comments with `--unresolved` | Process A step 2 (raw `id`/`in_reply_to`) |
| `review <change> --message M [--comment FILE:LINE:MSG]... [--in-reply-to ID] [--resolved]` | `POST /changes/{c}/revisions/current/review`; never sets `labels` | Process B post, Process C |
| `detail <change>` | `GET /changes/{c}/detail` | confirming the new patch set |
| `query "<q>"` | `GET /changes/?q=<q>` | finding own changes with `has:unresolved` |

`--in-reply-to` and `--resolved` apply to the whole call, so a threaded reply is one call per thread; several new (non-threaded) comments may share one call.
