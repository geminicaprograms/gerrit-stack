# Gerrit push options — hashtag vs topic, and the rest

Reference for the `%…` suffix on `refs/for/<branch>`. Source of truth:
[user-upload.html](https://gerrit-review.googlesource.com/Documentation/user-upload.html#push_options).

## Syntax

```
git push <remote> HEAD:refs/for/<branch>%<opt1>,<opt2>,<opt3>
git push -o <opt1> -o <opt2> <remote> HEAD:refs/for/<branch>     # equivalent
```

- One `%` after the branch; further options are **comma-joined**, not `%`-joined.
- `-o` is the alternative form; `push-chain.sh` always prints the `%` form so the
  guard's `%topic=` check sees it. Keep it that way.
- Values with spaces: `_` or `+` for a space, percent-encoding for anything else
  (doc example: `%m=This_is_a_rebase_on_master%21`).

## Option table

| Option | Syntax | Meaning |
|---|---|---|
| Topic | `%topic=<slug>` | Groups the pushed changes under a topic (see below) |
| Hashtag | `%t=<tag>` or `%hashtag=<tag>`; repeat for several: `%t=a,t=b` | Adds an informational hashtag to every pushed change |
| Work in progress | `%wip` | Uploads as WIP: reviewers are not notified, no review requested |
| Ready | `%ready` | Clears WIP (also overrides a WIP-by-default preference) |
| Private | `%private` / `%remove-private` | Only owner and reviewers see the change |
| Reviewer | `%r=<email>` (repeatable; `:silent` suffix suppresses mail) | Adds a reviewer to every pushed change |
| CC | `%cc=<email>` (repeatable) | Adds a CC |
| Patch set message | `%m=<text>` (`_`/`+` for spaces) | Description shown on the patch set |
| Base | `%base=<sha>` (repeatable) | Overrides the merge base; forces new changes for commits already in a branch |
| Label | `%l=<Label>+<n>` e.g. `%l=Verified+1` | Votes on push — **never used by this plugin's skills** |
| Notify | `%notify=NONE\|OWNER\|OWNER_REVIEWERS\|ALL` | Email scope for this push |

## Hashtag vs topic — the decision that matters

| | Hashtag `%t=` | Topic `%topic=` |
|---|---|---|
| Purpose | Informational search handle | Grouping with review and submit semantics |
| Search | `hashtag:<tag>` | `topic:<slug>` |
| UI | Chip on each change | "Same Topic" section on every change in the topic |
| Submit | No effect | With `change.submitWholeTopic = true` the whole topic submits **atomically**; one unready change blocks all |
| Cross-repo | Not enough: nothing links the repos | **Required** to relate changes across repos or branches |
| Many per change | Yes | Exactly one |
| Changed later | `POST /changes/{id}/hashtags` (`gerrit-rest.py hashtags <n> --add <tag>`) | `PUT /changes/{id}/topic` (MCP `set_topic`, or `gerrit-rest.py topic <n> <slug>`) |

Docs: [intro-user.html#topics](https://gerrit-review.googlesource.com/Documentation/intro-user.html#topics),
[intro-user.html#hashtags](https://gerrit-review.googlesource.com/Documentation/intro-user.html#hashtags),
[cross-repository-changes.html](https://gerrit-review.googlesource.com/Documentation/cross-repository-changes.html),
[config-gerrit.html#change.submitWholeTopic](https://gerrit-review.googlesource.com/Documentation/config-gerrit.html#change.submitWholeTopic).

**Rule of thumb used by the grouping question:**

- Same repo, same branch → **none**. The relation chain (parent–child) already
  groups the changes: Gerrit shows "Relation chain" on each of them and
  `get_related_changes` lists them.
- You want a search handle for a batch of chains, dashboards, or a release note
  → **hashtag**. Zero side effects.
- The work spans repositories or branches, or the user explicitly wants "all or
  nothing" submission → **topic**. Mention `submitWholeTopic` when you choose it;
  ask an admin if you do not know the server's setting (`GET /config/server/info`
  → `change.submit_whole_topic`).

## Examples

```
# plain chain, no grouping (the default answer)
git push origin HEAD:refs/for/master

# hashtag as a search handle
git push origin HEAD:refs/for/master%t=greeting

# topic because the work spans two repositories
git push origin HEAD:refs/for/master%topic=greeting-api

# work in progress, plus a hashtag
git push origin HEAD:refs/for/master%t=greeting,wip

# reviewer and cc requested by the user
git push origin HEAD:refs/for/master%r=rena@example.com,cc=lead@example.com
```

## How `push-chain.sh` composes the line

`push-chain.sh [--wip] [--grouping none|hashtag|topic] [--name <x>] [--branch <b>] [--remote <r>]`
reads `gerrit-stack.remote`, `.branch`, `.grouping`, `.group-name`, `.default-wip`,
lets the flags override for this print only, validates the chain (non-empty, one
Change-Id per commit, no `fixup!`/`squash!`, grouping and name consistent) and prints
**one** line:

```
git push <remote> HEAD:refs/for/<branch>[%t=<tag> | %topic=<slug>][,wip]
```

It never runs the push and never writes config. Run the printed line verbatim as
its own Bash call; the guard then asks the user for confirmation. Reviewer/cc
options are not composed by the script: add reviewers afterwards with
`add_reviewer` when the user names them.
