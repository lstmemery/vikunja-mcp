# The parallel drain: slots, `exclude`, returns, `workspace` refusals

> **A reference for SKILL.md, not rules of its own.** Read it **when `wip.limit > 1` and you are running several agents at once**.
> What is binding lives in SKILL.md itself — what is here is the shapes of the replies,
> the measured gotchas and the reasons a rule is written exactly the way it is.

- **Free slots GET FILLED; overlap is caught at integration, not predicted from the board.**
  While `wip.free > 0` and `next_task` hands back a task — claim and dispatch, up to the limit.
  Holding a slot back because two cards LOOK like they touch the same module ("I'll serialise
  them to be safe") is FORBIDDEN: that substitutes your guess for the project's mechanism. The
  scheme does not even try to predict an undeclared file overlap in advance — it DETECTS it at
  the moment of integration: every per-task agent runs `git fetch origin && git rebase
  origin/main` before pushing, re-runs the acceptance criteria AFRESH on the rebased tree and
  only then pushes (`git push origin HEAD:main`; rejected — it first looks at WHO won the race,
  then takes another round, up to `2 × max(wip.limit, wip.active)`), resolves the conflict
  itself, and when that fails — `call_human` (see "Commit+push is part of the transition to Review").
  The price of a real overlap is one rebase for a sibling; the price of "I'd rather serialise"
  is idle slots on EVERY task, always. The only legitimate narrowing of the drain is a technical
  one: `workspace` could not create a tree (see "It didn't start — do NOT drop the loop"),
  because two agents in one directory is something we never keep. A hunch that "these tasks look
  like they'll overlap" is NOT such a ground.
- **And the main thing: you do NOT SEE the queue — don't reason as if you do.** `next_task` hands
  back EXACTLY ONE card per call and never a listing of the free queue: how many tasks are in it
  and what they are is unknown from its reply (the one list there is, `waiting` under
  `starving:true`, enumerates precisely the UNclaimable, gated tasks). `exclude` is not a queue
  filter either: it strikes a card out of the "your active task / partial claim / review
  offering" branches and does not narrow the free queue at all — it is not needed there, because
  free means unassigned, and what you exclude is what you already hold, i.e. what is assigned to
  you. So "I looked at the queue and decided these tasks conflict" is an illusion: there can be
  more cards invisible to you than visible ones, and among them independent ones that your guess
  simply never started. And do not take the cards you filed yourself (`decompose`,
  `file_task(queue=True)`) for the queue: that is only YOUR contribution to it — beside them
  lies what the human and other agents put there.
- **Declared dependencies are a different matter, and the tools hold them, not you.** Everything
  above is ONLY about UNdeclared overlap. `follows`/`blocked` are gated hard and without your
  involvement: `claim` refuses, and `next_task` does not even offer such a card until the
  predecessor has reached Review. And saying "these run strictly in order" is the FILER's right,
  through `decompose(ordered=True)` (see "Decomposition and filing findings"), not the pump's by
  holding a slot back.
- **YOU maintain `exclude`, and only within the tick.** The tracker does not know whether your
  subagent is alive — that is a fact of the harness, not of the board, so it is the pump that
  must name the busy tasks. Forget, and `next_task` will honestly hand back the task your agent
  is already working on as "your active task", and you will dispatch a second one onto it. A
  killed turn loses that set — and that is FINE: on the next tick an empty `exclude` returns the
  task as "your active task", and the ordinary rule kicks in, "the agent died → dispatch a fresh
  resume agent" (see "Who does the work"). That one comes back to THE SAME tree and its
  unfinished work: `workspace <id>` on an existing tree creates nothing and hands that same tree
  back (`created: false`). But that holds for exactly THIS return path (the agent died, the task
  stayed in Design/Build) — see the next item.
- **A complete `exclude` is also the VISIBILITY of signals, not just protection from a double
  dispatch.** The branch order in `next_task` is rigid: your active tasks → a partial claim in
  Queue → a review offering → the slot check (`wip_saturated`) → the free queue. The slot check
  sits AFTER the first three, so an active task you failed to name is handed back to you as
  "your active task" BEFORE the turn ever reaches that check. The same board in the same minute
  answers `wip_saturated: true` to a complete `exclude` — and "your active task" with
  `wip.free: 0`, with no `wip_saturated` field at all, to an incomplete one. Which means
  `wip_saturated` is a signal you get ONLY if `exclude` is complete.
  - **Saw a resume at `wip.free == 0` — check YOUR `exclude`, not the board.** If an agent of
    yours is ALREADY live on that task, the set is incomplete: add the id to `exclude` and call
    `next_task` again (that is when `wip_saturated` arrives), and do NOT dispatch a second agent
    onto it — that is exactly what `exclude` prevents. No live agent on it — this is the
    ordinary resume after a dead agent, dispatch a fresh one. `next_task`'s reply at
    `wip.free == 0` reminds you of this in its `note` — but only you know the set itself, so
    only you can check it.
  - **What you go blind to is not only saturation: review offerings get overridden too.** The
    review branch sits ABOVE the slot check and takes no slot, so a saturated pump with a
    COMPLETE `exclude` still gets its reviews; with an incomplete one a resume overrides them.
    (Since #991 that holds in a SOLO setup too, and it did not before: the branch skipped cards
    assigned to you, and there every card is yours, so a review never came at all. Now it does —
    and a complete `exclude` starts deciding here as well, because the only thing that takes a
    card off the offering is a verdict.)
  - **This is NOT a bug, and we do NOT touch the branch order.** `vikunja-mcp claimable` rests
    on it — the outward-EXPORTED check "is there work for this token?", by which an external
    supervisor decides whether to boot an agent at all: it calls `next_task` with an EMPTY
    `exclude`, and so NEVER reaches the slot check and answers "there is work: resume". Move the
    slot check higher and a saturated board holding an abandoned, perfectly resumable task
    starts answering "no work", and nobody will send a resume agent for it any more.
- **Two returns, two trees.** There are TWO different ways to come back to a task, and their
  trees differ; the rule above describes one of them. An agent that expects its own tree ALWAYS
  will hunt in it for unfinished work that was never there. And what decides here is not WHY the
  card came back, but one fact: whether the tree was torn down through `--release`. And that
  removes the directory and the `task/<id>` branch ONLY when the tree is clean and everything is
  pushed — so "there is no tree" and "the work is already on the main branch" are one statement,
  not two.
  - **The agent died** (the task still stands in Design/Build behind you, nobody called
    `--release`): `workspace <id>` hands back THE SAME tree (`created: false`) — commits and
    UNcommitted work both in place. Two exceptions: a tree taken off its branch by an
    interrupted rebase comes back as a REFUSAL ("build worktree … DETACHED", see "A separate
    case") — that gets fixed first; and a directory removed CRUDELY (around `--release`) is cut
    AFRESH (`created: true`) and reattached to the surviving `task/<id>` branch — the commits
    come back, the uncommitted work does not.
  - **The card was returned from Review** (`needs_work` from a reviewer, or a human by hand)
    AFTER a successful push and `--release`: neither directory nor branch — there is nothing to
    reattach to. `workspace <id>` cuts a FRESH tree from the CURRENT `origin/<main branch>`
    (`created: true`), which has moved ahead by the siblings' commits. That is neither a loss
    nor a regression: the push succeeded, so the predecessor's change is ALREADY on the main
    branch, and a fresh base is the best one there is. Read what was done from the `[review]`
    comment and the pushed diff (`git show <sha from evidence>`), NOT from the working
    directory.
  - **A return WITHOUT a successful push looks like the first case, not the second** (typically
    the orchestrator refusing an unverifiable evidence sha, step 3 of the tick): `--release`
    refuses there ("unpushed commits"), and the tree with the work stays where it is.
  - **So check, do not assume.** Two commands in the tree: `git status --porcelain` and
    `git log --oneline origin/<main branch>..HEAD`. Both empty — there is NO unfinished work
    here and nothing to look for; non-empty — there it is. The answer is honest on every path
    above, including the rare "the branch leaked" (`branch_deleted: false`): there the tree is
    reattached to it and the base will be older than the current main branch, which the ordinary
    `git fetch origin && git rebase origin/main` before the push straightens out.
  - **Deliberately NOT done:** letting the `task/<id>` branch live on after `--release` so that
    a return could reattach to it. That would break the release's own protection and would pile
    up one branch per EVERY finished task; a fresh tree from the current main branch is the
    better behaviour — it just had to be said out loud.
- **A review takes no slot.** `wip.active` counts only Design/Build assigned to you; a card in
  Review is not your active task (see "Queue discipline"), so background reviews do not narrow
  the drain, however many of them there are.
- **Having cast a verdict, the reviewer releases its own tree:**
  `vikunja-mcp workspace --release <id> --role review`. `--role review` is MANDATORY here — by
  default `--release` takes down the build tree, i.e. somebody else's. Fail to release it and
  the tree lives until the card leaves Review (who moves it out of there — see below), and
  `--gc` will not take it before that: a review tree's liveness is counted BY ROLE, not by
  freshness, so while the card is in Review the sweep does not touch it, however long it has
  stood without a single write — the grace window never even reaches it. **And do NOT commit
  INSIDE a review tree** (notes, a draft verdict): it is detached, a commit in it is reachable
  from no branch — `--release` will refuse to remove it, `--gc` too, and the tree stays forever.
  A second round of review on that task runs into it:
  `workspace <id> --role review --at <new sha>` REFUSES ("pinned at …"), and that is right —
  otherwise you would silently get a tree with the OLD code and cast a verdict on that. The
  verdict goes as a comment to the tracker (`review_task`), not as a commit into the tree.
  - **It is not only the human who moves a card out of Review — YOUR verdict moves it too, and
    the tree dies along with it.** `approve` does NOT move the card (only the labels), so after
    it the tree lives until a human takes the card away. But `review_task(verdict='needs_work')`
    sends the card to Build — and from that second on your tree is DEAD to `--gc`, exactly like
    a build tree after `advance(to='review')`. After that only the grace window holds it, and
    that is counted from the last WRITE in the tree: a purely reading review (Read, `git log`,
    `git show`) has no writes at all, so the window ticks from the tree's CREATION and not from
    the verdict, and a review longer than the window falls outside it before you have even cast
    the verdict. Verified: the very next sweep hands a quiesced tree back in `released`, and the
    directory is gone. No work is lost by this (the tree is detached and clean), the cost is
    bounded by a vanished cwd — but the rule is therefore exactly the build side's after
    `advance`/`call_human` (see "Check-point early" and "Commit+push is part of the transition to
    Review"): **once you have cast the verdict, do not assume you are still standing in your own
    tree**; needed the directory — call `workspace <id> --role review --at <sha>` again rather
    than walking into the old path. Do NOT hold the verdict back for that: "record the verdict
    at once" is the stronger rule (a lost verdict is a whole review again from scratch, a
    vanished directory is one `workspace` call); simply do whatever needs THIS directory BEFORE
    the verdict.
  - **`released: false` has FOUR readings, and your ordinary one is the last.**
    `--release --role review` over an already-removed tree returns exit code 0 and
    `code: "no-worktree"`: that is not a refusal of the PROTECTION ("unsaved work is left"), not
    an interrupted rebase and not a human's lock, but a success after the fact — there is
    NOTHING to do and repeating it is pointless. The fourth reading was filed by #631:
    `code: "locked"` — a HUMAN locked the tree (`git worktree lock`), the work is intact and
    nothing was deleted, but taking the lock
    off is not yours to do (`git worktree unlock` belongs to whoever set it); name the path and
    the lock in your verdict and do not touch the directory. `released: false` is never, ever a
    tool failure: a failure has exit code 1 and an `error` field. `--release`'s breakdown of the
    codes is one for both roles and lives on the build side ("Worked in your own worktree", in
    "Traces of the work") — read it there rather than growing a second one of your own.
    **But "one for both roles" holds in the other direction too: `dirty` does NOT TELL the roles
    apart.** One file left in the tree — a probe, a draft, exactly what "whatever can live in
    your tree, let it live there" calls for — gives `{"released": false, "code": "dirty"}`. And
    the CURE written there is the build one, "take it through to a push and repeat", and it is
    FORBIDDEN to you: the tree is detached, there is no branch, and a commit into it is an
    `unreachable-head` forever — and QUIETLY at that: such an entry grades into `expected`, i.e.
    into the "do not look" list, and nobody will see it. Your cure is one: take the file out of
    the tree (need it later — carry it outside, with the task id in the name) and repeat
    `--release`. Fail to, and when the card leaves Review the entry lands in `kept` (`dirty`
    reaches `expected` only for a BUILD tree and only under a parked card; nothing whitens YOUR
    tree), and the human will see it on every sweep — from the moment the tree has stood without
    a single write for longer than the grace window — without knowing whose it is.
    **And it is exactly the other way round with an IGNORED file: it does not give `dirty` at
    all.** Took a `shot-<id>.png` in your own review tree (the `*.png` rule) or put the
    browser's output into `.playwright-mcp/` — the guard does not see it, the tree goes with an
    ordinary `released: true`, and the files go with it. One trace is left: `removed_ignored` in
    that same entry (the breakdown is in the same place, "Worked in your own worktree"). So
    everything you need AFTER the verdict, carry outside BEFORE it.
- **It didn't start — do NOT drop the loop.** When `workspace` could not do the work (not a git
  repo, no `origin`, the path taken by a foreign directory, no permissions, `--gc` could not
  reach the tracker, a tree half-created by a killed `worktree add` — "HALF-CREATED", which a
  human fixes with the two commands from the error text), it prints `{"error": ...}` and returns
  exit code 1. That is NOT a reason to stop and NOT a reason to declare the queue empty: you
  work in ONE slot in the main checkout — exactly as in sequential mode — and keep draining. A
  per-task agent with no path in its brief is a normal case too: it works where it stands. The
  error repeats tick after tick — file a `file_task` so a human sees it, rather than degrading
  silently forever. **Do not confuse this with `--release`'s `released: false`:** that one comes
  with exit code 0, and is NEVER EVER a tool failure — but it does not carry one single meaning
  either: `dirty`/`unpushed` mean "unsaved work is left", while `no-worktree` means "the tree is
  already gone, and that is fine". Read the `code`, not the bare fact of `false` (the breakdown
  is in "Traces of the work").
- **A separate case: `workspace <id>` refused with the words "build worktree … DETACHED".** This
  is NOT a broken tool and NOT a reason to narrow the drain: the tree is alive, the work is
  intact on the `task/<id>` branch, but the tree is not standing on it — typically an
  interrupted `git rebase origin/main` (a killed turn breaks it off exactly like that and leaves
  the tree CLEAN, so nothing shows from the outside). `ensure` used to hand such a tree back
  silently as an ordinary one, and the resume agent committed into a detached HEAD, while its
  `git push origin HEAD:main` pushed the replayed commit instead of the branch's work. Action:
  dispatch the resume agent AS USUAL (the path is in the error text) and hand it the whole
  diagnostic — the first thing it does in that tree is `git rebase --continue` or
  `git rebase --abort` (which one is its call, knowing its own work; `--abort` throws away what
  was replayed), after which `workspace <id>` hands the tree back normally again. The tool itself
  does not make that choice and fixes nothing silently.
