# Independent review of changes — the push model

> **A reference for SKILL.md, not rules of its own.** Read it **when you are the REVIEWER and are recording a verdict**.
> What is BINDING lives in SKILL.md itself — what is worked out here is the response shapes,
> the measured gotchas and the reasons a rule is written exactly the way it is.

## Independent review of changes — the push model

No change goes to the human without independent review — not just a bug fix.
ANY task brought to Review is reviewed by a separate agent. The ONLY
exception is an epic container (the `epic` label): it has no code of its own (the evidence lies
in the children, each child reviewed on its own move into Review), there is nothing to review.
The exception hangs on the `epic` label, NEVER on the presence of subtasks.

In a solo setup (one token for everything — the orchestrator and its per-task agents travel under
one assignee) review is initiated by the same side that wrote the change — by push:

- **The push comes right after Review.** A per-task agent that has brought a task to Review gets
  `review_needed: True` and `review_kind` back from `advance` and reports that in its summary;
  the orchestrator immediately dispatches a FRESH review subagent **in the background** (the
  reviewer is a sibling of the orchestrator, so author ≠ reviewer).
- **The first thing a reviewer does is establish that it is looking at EXACTLY that code.** A
  verdict on someone else's code is the worst outcome a review can have, and after the fact it is
  indistinguishable from an honest one. You need your own tree at ANY `wip.limit`, not only in a
  parallel drain: `vikunja-mcp workspace <id> --role review --at <sha from evidence>` reads
  neither the board, nor the token, nor the limit. At `limit: 1` you need it all the more: while
  you review in the main checkout, the pump is already ENTITLED to dispatch the next task's agent
  into that very place. And a tree is not yet a guarantee: the same call WITHOUT `--at` silently
  hands you the EXISTING tree, nailed to the OLD sha (`created: false`, the old sha in `head`, not
  a word of refusal). So check it yourself: `git rev-parse HEAD` in your tree = the sha from
  `evidence`. No tree at all (`workspace` refused, no path was given in the brief) — do NOT review
  what you are standing in: that is the main branch's code, not that commit, and on top of that a
  sibling may be working in it right now; read `git show <sha from evidence>` and name in the
  verdict what you looked with.
  And know WHERE everything else about your tree lives — how to release it, the four readings of
  `released: false`, the `dirty` refusal: in the bullet "Having cast a verdict, the reviewer releases its own tree", and that one stands INSIDE the section "Parallel drain (when
  `wip.limit > 1`)". The section heading does not apply to you: that bullet is yours at ANY
  limit — it is just that at `limit: 1` you would otherwise never get to it at all.
- **Having checked the sha, look at the OUTCOME of the CI run on it — you are the only one who by
  construction is LATE.** The implementer must look at the outcome as the last action of its turn,
  but for them that is ONE look without waiting, and on an unfinished run their honest answer is
  "did not wait" (see "After the push there are TWO checks" in "Commit+push is part of the transition to Review"; the commands, the requirement of the FULL sha and the analysis of
  `status`/`conclusion` are there too). You do not have that problem: you start later and work in
  minutes, while a run completes in at most 120 s (40 measurements on the first attempt; the
  runner queue was 0 s for 35 of 38 and at most 80 s) — so by the time of your verdict
  `conclusion` usually already exists. There is exactly one caveat: a run RESTARTED by hand waits
  for a human, not for a runner (measured 31 min and 3 h 26 min from creation to the second
  attempt), so "120 s" is about an ordinary push run, not about any run.
  So: `gh run list --commit <FULL sha from evidence> --json databaseId,status,conclusion`
  — and name the outcome in `[review]` beside what you looked at the code with. This is not a new
  duty out of nowhere: both the implementer and the reviewer of VMCP-129 (615) checked CI exactly
  this way on their own initiative, the rule merely stops hoping for initiative. If it has not
  finished for you either — do NOT hold the verdict back because of it ("record the verdict
  IMMEDIATELY" is stronger): write in `[review]` that at the moment of the verdict the run was
  still going, and name its id.
  A red run BY ITSELF is not yet `needs_work` — first look at
  `jobs`: `lint-and-unit` failed (the same `ruff`/`pytest` as in the readiness criteria) —
  the main branch is broken by this commit, and that is a verdict; a lone `integration` failed —
  an environment refusal, and that is a finding for the report, not a reason to drive the card
  back for rework. If the run did not start at all on the full sha — here there is first ONE step
  of diagnosis, not `needs_work` straight away: a run is started on the TIP of a push, so a
  non-tip commit is left without a run and without a check-suite even though the work arrived
  (measured — 1 of 21 task commits, ~5 %; the analysis and the commands are in "After the push
  there are TWO checks"). `git log --oneline <sha from evidence>..origin/main`: there is a commit
  with a run above — the marker has nothing to do with it, this is a finding for `[review]`
  (nobody ran the tree at exactly this sha), not a bounce. Empty above OR the descendant has no
  run either — then `needs_work` without discussion: a swallowed ci-skip marker, the work did not
  reach the consumers.
- **`review_kind` sets the reviewer's rubric.** The general brief: read the dossier (`get_task` —
  description, spec, worklog, and on a second round the previous `[review]` as well: the card came
  back from Review for a reason), verify BY RUNNING (not by reading the code), look for obvious
  regressions nearby and record the verdict `review_task(task_id, verdict='approve'|
  'needs_work', report=...)`. **Record the `review_task` verdict IMMEDIATELY, as soon as you are
  sure** — do not put it off to the very end after optional extra checks: a turn killed by a
  limit or an error BEFORE the call loses the verdict entirely and the review has to be
  repeated from scratch. **And your report is the same kind of text with measurable claims as the
  implementer's prose: the second independent pass is mandatory over IT too** — the section
  "A second independent pass over YOUR OWN text" is written for both roles (measured: the
  reviewer's first pass was heading for approve, and the decisive finding came from the second
  agent). The one does not argue with the other: the pass is
  started EARLY and IN PARALLEL, and if it has not come back by the moment you are sure — you
  record the verdict anyway, the findings you add in a separate comment. Then, by the rubric:
  - `review_kind: 'bug'` — reproduce the bug (or explain why that is impossible) and
    make sure the fix closes the CAUSE from the report (root_cause), not the symptom.
  - `review_kind: 'change'` (feat/chore/docs/refactor/…) — make sure exactly what is in the
    spec/description was done; that the tests are REAL (they check behaviour, not a
    tautology); that the change stayed in its slice (no scope creep and no stray edits).
    root_cause is NOT required here — it is mandatory only for bugs.
- **Reviewer ≠ implementer.** These are different subagents with unmixed contexts;
  whoever wrote the change in this session does not review it.
- **In parallel, without blocking.** Having dispatched the review in the background, do NOT wait
  for it — go straight to `next_task`/`claim` for the next task. A background review does not
  count as your active task (see "Queue discipline").
- **Verdict → a label on the board.** approve hangs `reviewed` (the human will see
  `[review] APPROVE` and take the decision about Done); needs_work hangs `review-failed`
  and returns the task to the implementer in Build with a report — and a card WITHOUT an
  assignee has no implementer, so it leaves for **Queue** as free work (#705; see "After Review",
  where what to do with it once claimed is worked out). The labels are mutually exclusive; a
  resubmit into the active pipeline through `advance` (to='build' or to='review') itself
  removes ANY previous verdict — both `review-failed` and `reviewed`: resuming
  work invalidates the old assessment. This also closes the case where a human pulled an
  approved card out of Review for rework BY HAND (no tool fired, and the
  `reviewed` label would otherwise have travelled with the task into a new review).
- **The needs_work cycle — and NOT every outcome of it leads back into Review.** The ordinary
  one: the task is reworked and goes to Review again through `advance` (and will return
  `review_needed` again) — push a fresh reviewer once more. But `needs_work` is the ONLY way to
  return a card to its owner (no agent tool takes it out of Review any more), so a reviewer uses
  it to file what is not "rework" at all, and from the shape of the bounce that is NOT VISIBLE —
  you have to read the text of the report:
  - **A QUESTION FOR THE HUMAN** — the reviewer has no door of its own to the human on this card
    (`call_human` from Review refuses, see "Stuck? The way out depends on your ROLE"). The
    implementer forwards the question through `call_human` from Build → the card leaves for
    **Your Call**.
  - **"IT HAS TO BE SPLIT"** — `decompose` from Review refuses too (gate #663), so it is split by
    the owner from Build → the parent leaves for **Backlog** with the `epic` label, the children
    go into Queue (measured; the details are in the `decompose` bullet of the section
    "Decomposition and filing findings").
  - **"IT LOST ITS POINT" / an external block** — `return_task` from Review refuses by the same
    gate (#590), so it is returned by the owner from Build → the card leaves for **Backlog** with
    the `blocked` label and WITHOUT an assignee, for the human to re-triage (measured).
  In any of these branches there is NOTHING to push a reviewer at — the card is not in Review —
  and no `advance` will follow the bounce either. Do not wait for either. And do not read the list
  as closed: the full analysis, together with the "nothing fitted" branch, is in "After Review".
- **Multi-identity (for the future).** If a second free agent with a DIFFERENT token appears in
  the setup, it will be able to pick a task up for review by itself — through
  `next_task` (branch 3: any non-epic task in Review without a fresh verdict, not its
  own; the dormant pull path stays alive). In solo there is no second one, so the mechanism is push.
