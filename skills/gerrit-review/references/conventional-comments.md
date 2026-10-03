# Conventional Comments — condensed for authors replying on Gerrit

Source: https://conventionalcomments.org (labels and decorations quoted from the spec).
The format makes the *intent* of a review comment machine- and human-parseable, so an author
can tell at a glance what is blocking, what is optional, and what needs no action at all.

**When this applies to what you write:** only when the team chose it — `gerrit-stack.comment-style`
is `conventional` (per-clone `git config`, else the committed `.gerrit-stack` file; default `none`).
Then every **new top-level** comment or author note starts with a label (a guard denies unlabelled
ones) and states `(blocking)` / `(non-blocking)` when the label alone leaves it open. **Replies are
free-form in both modes.** With `none`, write plain concise sentences and use this file only to
read reviewers' labels.

## Format

```
<label> [decorations]: <subject>

[discussion]
```

- **label** — one word that says what kind of comment this is.
- **decorations** — optional, in parentheses, comma-separated: `(non-blocking)`, `(blocking)`, `(if-minor)`; free-form tags such as `(security)`, `(test)`, `(ux)` are allowed too.
- **subject** — the message itself, one line.
- **discussion** — optional paragraph(s) with the reasoning, context and next steps.

## Labels

| Label | Spec meaning | Blocking by default? |
|---|---|---|
| `praise` | "highlight something positive"; look for something to sincerely praise | no |
| `nitpick` | "trivial preference-based requests"; non-blocking by nature | no |
| `suggestion` | "propose improvements to the current subject"; be explicit about what and why | depends — reviewer decorates |
| `issue` | "highlight specific problems with the subject under review"; ideally paired with a suggestion | yes unless `(non-blocking)` |
| `todo` | "small, trivial, but necessary changes" | yes |
| `question` | "a potential concern but not quite sure if it's relevant" | no, but needs an answer |
| `thought` | "an idea that popped up from reviewing"; non-blocking by nature | no |
| `chore` | "simple tasks that must be done before the subject can be 'officially' accepted" (run a job, update a doc) | yes |
| `note` | "always non-blocking and simply highlight something the reader should take note of" | no |

## Decorations

| Decoration | Spec meaning |
|---|---|
| `(non-blocking)` | "should not prevent the subject under review from being accepted" |
| `(blocking)` | "should prevent the subject under review from being accepted, until it is resolved" |
| `(if-minor)` | resolve "only if the changes end up being minor or trivial" |

## Reviewer label → expected author action

| Reviewer wrote | Author does | Thread ends `unresolved` = |
|---|---|---|
| `praise` | nothing, or a one-line thanks | `false` (usually already resolved) |
| `nitpick` | fix if trivial; otherwise reply why not | `false` after fix; `true` if declined and reviewer did not mark non-blocking |
| `suggestion` | fix, or reply with the reason you keep the current form | `false` after fix; `true` if declined |
| `suggestion (if-minor)` | fix only if the change is small; say which way you went | `false` either way once you replied |
| `issue` / `issue (blocking)` | fix in a new patch set, or explain with a cited principle why it is not an issue | `false` only after the fix is pushed |
| `issue (non-blocking)` | fix now or defer to a follow-up change; name it | `false` when fixed or deferred with a reference |
| `todo` | do it in the new patch set | `false` after fix |
| `question` | answer it; if it uncovers a real problem, treat as `issue` | `false` when answered |
| `thought` | optional reply; no code change expected | `false` when answered (or leave as is) |
| `chore` | do the task (run the job, update the doc) and say so | `false` after done |
| `note` | read it; no reply needed | unchanged |

## Author replies — examples

All replies are ≤ 3 sentences, threaded with `in_reply_to`, and use "we"/"could" over "you"/"should".
Labels in replies are optional: the examples below show them, and the same sentences without the
leading label are equally fine.

**Fixed** (the only unlabelled reply; Gerrit's own convention)
```
Done. Falls back to "" when greeting.prefix is unset (PS2).
```

**Fixed in a different way than suggested**
```
Done. Went with Optional.ofNullable instead of a null check so the call site reads the same as ConfigReader (PS2).
```

**Disagree with a reason** — cite a principle, ADR, or doc link; leave `unresolved: true`
```
thought: reading the file directly keeps greeting.prefix hot-reloadable, which ADR-007 requires; PluginConfigFactory caches per plugin load. Happy to switch if reload is not a requirement here.
```

**Answer a question** — `unresolved: false` once answered
```
note: the handler is registered per project so the map is keyed by project name, not by request; see docs/design/greeting.md#scoping.
```

**Defer to a follow-up** — name the Change-Id or ticket; `unresolved` stays `true` unless the reviewer marked non-blocking
```
note: agreed, but it touches the SSH command too; tracked in I7f3c2a91e4b5d6c7 (next change in the chain) so this one stays a pure REST change.
```

**Ask for clarification** — `unresolved: true`
```
question: do we want the fallback to be "" or to skip the greeting entirely when the prefix is unset?
```

**Author note on your own change** (Process C; a new top-level comment — labelled when `comment-style=conventional`)
```
note (non-blocking): reviewer note: this change only moves code; behaviour is unchanged. The rename lands in the next change.
```

## What not to write

- More than one round of disagreement in a thread — escalate to the human instead.
- `Done.` before the patch set with the fix is on the server.
- A reply that resolves a thread you declined — the reviewer closes those.
- With `comment-style=conventional`: a new top-level comment without a label; a reader should know from the first word whether action is needed.
- With `comment-style=none`: labels and decorations the team did not ask for.
