# gerrit-stack — 7-minute stage script

One terminal, one browser, one prompt. The plugin does the work; you narrate the
Gerrit concepts as they appear (Change-Id from the hook, relation chain, grouping,
`refs/for/`, fixup + autosquash, re-push of the whole chain).

Paths below are relative to the repo root (`/Users/jcentkowski/workspace/open/gerrit-stack`).

## T-30 — checklist (every rehearsal, every show)

```
make demo-up demo-seed demo-warm` and `bash demo/patch-gerrit-mcp.sh` (gerrit-mcp must keep http:// for localhost)        # Gerrit 3.14 on :8080 (init ~90 s the first time), admin + rena,
                                        # project demo-plugin with the skeleton, ~/.netrc, gerrit-mcp config,
                                        # in-tree Bazel build warmed (2nd run < 20 s)
bash demo/work/demo-plugin/tools/quick-check.sh   # < 1 s, exit 0
```

- [ ] `docker compose -f demo/docker-compose.yml ps` → `healthy`.
- [ ] Browser tab 1: `http://localhost:8080/q/status:open+project:demo-plugin` (empty list; you are `admin`
      — if the UI shows you logged out, open `http://localhost:8080/login/?user_name=admin`).
      Tab 2 stays free for change 2 (opened after the push).
- [ ] Terminal tab **A** (the demo): `cd demo/work/demo-plugin && claude --plugin-dir /Users/jcentkowski/workspace/open/gerrit-stack`
      then `/mcp` → `gerrit` is **green**; the session-start line `[gerrit-stack] …` was printed. Leave it at the prompt.
- [ ] Terminal tab **B** (the reviewer): shell at the repo root, `cat demo/feature-request.md` ready to copy,
      `bash demo/reviewer-comment.sh` ready to type.
- [ ] Dry-run once on the day: run the whole script below, then reset (last section). During that dry run answer
      "yes, don't ask again" to the permission prompts for `git …` and `bash tools/quick-check.sh`, so the show
      runs without prompts (settings land in `demo/work/demo-plugin/.claude/settings.local.json`; a reset wipes them).
- [ ] The demo project carries the team files from the seed commit: `git -C demo/work/demo-plugin show --stat HEAD`
      lists `.gerrit-stack` (`commit-lint = auto`, `comment-style = conventional`) and `commitlint.config.mjs`;
      `commitlint --version` works in tab A (installed globally: `npm i -g @commitlint/cli @commitlint/config-conventional`).
      The session-start line names both team conventions. Without commitlint the commit highlight below is silent.
- [ ] Font size 18+, `Ctrl-L` in both tabs, notifications off, `demo/recording/` open in Finder as the fallback.

## 0:00 — the prompt

Paste the content of `demo/feature-request.md` into tab A (three concerns: `greetingPrefix` setting, REST
`demo-plugin~greeting`, SSH `demo-plugin greet`; unit tests; "each concern its own change"; push for review).

Narrate: *"No mention of Gerrit specifics. The plugin's skill triggers on the repo, not on the prompt."*

## 0:30 — plan → yes

The `stack-planner` skill prints a 3-step plan (`Step 1 — feat: greetingPrefix …`, `Step 2 — feat: REST …`,
`Step 3 — feat: SSH …`, each with files, ±lines, verify command). Nothing has been edited yet.

Answer **yes**. Narrate: *"One concern per commit, each ≤ ~150 lines, each buildable alone — that is what makes a
relation chain reviewable. The plan is the contract; code comes after."*

## 1:00 — three commits

Watch for, per step: edit → `bash tools/quick-check.sh` (< 1 s) → `git add <paths>` → `git commit` → the plugin
asserts exactly one `Change-Id:` and prints the diff size.

Narrate on the first commit: *"The Change-Id trailer came from Gerrit's commit-msg hook, never typed by the agent —
a hook rule denies any hand-written one. It is the identity that survives rebases and amends."*
Point at the sizes: three commits around 60 / 80 / 50 lines.

**Highlight — the team files.** Show the repo's `.gerrit-stack` and `commitlint.config.mjs` (committed in the demo
project, so every clone has them) and then the chain subjects: `git log --oneline origin/master..HEAD` reads
`feat: …` for the code changes and `test: …` where tests travel on their own — conforming without the prompt
saying a word about commit style. Narrate: *"The plugin ships no commit rules of its own. It runs the repo's own
commitlint config on every commit and before the push; the team decides, the agent follows."*

If a subject slips (or you nudge one on purpose, e.g. a rehearsal commit `Added greeting`), the post-commit
feedback shows commitlint's own rule lines (`subject-case`, `type-empty`) and the agent amends with
`git commit --amend -F <file>`, keeping the Change-Id line. Narrate: *"That is commitlint talking, not the
plugin — and the Change-Id survives the amend."* A push of a chain with a failing subject is denied, listing the shas.

## 3:00 — the grouping question → **none**

The skill asks once per chain: *All 3 changes target demo-plugin/master. Group them? none / hashtag / topic.*

Answer **none**. Narrate: *"Same repo, same branch — the relation chain already links them, that is what
'Related Changes' will show. A hashtag is only a search handle. A topic is for work that spans repos or branches,
or when you want atomic submit with submitWholeTopic — which this server has off. Grouping is a choice, not a
default; the plugin never adds `%topic=` unless you asked."*

## 3:30 — push → yes

*Push 3 changes to refs/for/master on origin? (y/n/wip)* → **yes**. The agent runs the printed
`git push origin HEAD:refs/for/master` verbatim; Gerrit answers with three change URLs; the plugin confirms the
chain through the MCP (`get_related_changes`).

Narrate: *"`refs/for/master`, never `refs/heads/master` — a hook denies direct branch pushes and asks before every
review push. One push, three changes, one chain."*

Browser tab 1: reload — 3 open changes. Open change 2 in tab 2 → **Related Changes** shows 1 above, 1 below.
Note change 2's number (`<n2>`) for the next step.

## 4:15 — the reviewer strikes (tab B)

```
bash demo/reviewer-comment.sh <n2>
```

Prints the change URL. Reload tab 2: `Code-Review -1` from *Rena Reviewer* and an unresolved thread on the first
`.java` under `src/main`: *issue (blocking): the config value is read on every request; cache it in the @Singleton …*

Narrate: *"Conventional Comments — the label says what kind of feedback this is and whether it blocks."*

Tie-back, one sentence: *"This is the call to action from last year's talk — Conventional Comments — and with
`comment-style = conventional` in the team file the plugin holds the agent to it."*

## 4:45 — address it (tab A)

Type: **Check review comments on the chain and address them**

The `gerrit-review` skill lists the threads (`change | file:line | author | unresolved | summary`) — one row,
change 2. Then `gerrit-stack` phase 5: fix → `git commit --fixup=<sha of change 2>` →
`git -c sequence.editor=true rebase -i --autosquash <base>` → `chain-status.sh --verify-ids` prints `ok`
(all three Change-Ids unchanged) → *Push 3 changes to refs/for/master on origin?* → **yes** → reply drafted
(`Done. …`, ≤ 3 sentences, `unresolved: false`) → *post 1 reply?* → **yes**.

**Highlight — labelled review replies.** With `comment-style = conventional` any new top-level comment the agent
drafts must carry a label (`issue (blocking):`, `suggestion (non-blocking):`, `praise:`); an unlabelled one is
denied with the label list. Replies stay free-form (`Done. …` plus why), so the reply to rena goes through
unlabelled. If you want the guard on screen, ask the agent to add a top-level note on change 3 and watch the
label request.

Narrate over the rebase: *"The fix is squashed into the middle change, not appended as a fourth. The Change-Ids
are verified after the rebase, so Gerrit sees new patchsets, not new changes. The whole chain is re-pushed because
change 3 now sits on a new parent."*

## 6:15 — the result (browser)

Tab 2 (change 2): **Patchset 2**, the thread is resolved with rena's comment answered. Tab 1: change 3 also has
Patchset 2 (rebased, no diff of its own — "Related Changes" still shows the chain), change 1 untouched at
Patchset 1. Rena's `-1` is gone with the new patchset.

Close: *"Three reviewable changes, one review round, no Gerrit vocabulary in the prompt. That is the default the
plugin gives every agent in a Gerrit repo."*

## Fallback triggers → `demo/recording/`

Switch to the recording without apology when any of these hit:

| trigger | do |
|---|---|
| any single step silent > 60 s (plan, a commit, the push) | `demo/recording/full-7min.mp4`, resume narration from the matching chapter |
| `/mcp` not green at T-30, or the push step cannot confirm the chain | run the demo to the push (it works without MCP), then play `short-3min.mp4` for the review round |
| verify step > 30 s (Bazel picked over quick-check) | `git -C demo/work/demo-plugin config gerrit-stack.verify-cmd 'bash tools/quick-check.sh'` and tell the agent to re-run verification |
| Gerrit down (`docker compose ps` not healthy) | `docker compose -f demo/docker-compose.yml restart` costs ~40 s; otherwise `full-7min.mp4` |
| `reviewer-comment.sh` fails | it prints the equivalent `curl` — run it; or post the comment in the UI as rena (`http://localhost:8080/login/?user_name=rena`) |

Recordings are made at rehearsal #2 and re-cut at #3 (`full-7min.mp4`, `short-3min.mp4`).

## Reset between rehearsals

```
bash demo/reset.sh && make demo-up demo-seed demo-warm      # ~2 min; add --netrc to reset.sh to also drop the ~/.netrc entries
```

`reset.sh` prints what it removed (container + volumes, `demo/work/`, init artefacts in `demo/etc/`, the keys
init appended to `demo/etc/gerrit.config`). `seed.sh` is idempotent — run it alone to re-check a live instance.
