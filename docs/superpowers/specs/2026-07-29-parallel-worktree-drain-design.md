# Parallel drain: N tasks at once, each in its own git worktree

Date: 2026-07-29
Status: approved (brainstorming) → ready for implementation plan

## Problem

The drain is serial by design and by rule: SKILL.md says *«Дренаж
последовательный, не параллельный»* — claim, dispatch ONE per-task agent, wait
for it to reach Review, only then take the next. The pipeline's throughput is
therefore one task per agent-lifetime, even when the Queue holds several
mutually independent tasks and the machine is idle waiting on a single agent.

The blocker was never the tracker: `next_task` already tolerates 2+ active
tasks (the rework-first ordering at `workflow.py:361` was written for exactly
that), and `enforce_single_wip` already exists as an *opt-in* gate that this
repo deliberately leaves off. The blocker is the **working copy**: two agents
editing one checkout collide on files, on `git add`, and on `HEAD`. That is the
gap this design closes — with `git worktree`, one per in-flight task.

A second, quieter win falls out of the same mechanism: today a per-task agent
that dies mid-task leaves an uncommitted diff in the *shared* checkout, which
the next agent must reason about. With a per-task worktree, a crashed agent's
work sits in its own tree and the resume agent walks straight back into it.

## Goals

- Drain up to `wip_limit` tasks concurrently, each per-task agent isolated in
  its own worktree; reviewers likewise get their own tree.
- The limit is **configured and machine-enforced**, not remembered by a model.
- ~~Ships **inert**: a consumer that sets nothing behaves byte-for-byte as
  today.~~ **This goal was REVERSED by a human decision on 2026-07-30 (tracker
  #524): an unset `wip_limit` now means 3.** Kept here rather than deleted,
  because it explains the shape of everything below. Why it was dropped: an
  absent key meant two contradictory things — *no gate at all* in the code, a
  *serial* drain in the rulebook — so every consumer ran one task at a time by
  discipline while `claim` silently permitted any number. The humans want three
  parallel per-task agents as the default everywhere, and adding the key by hand
  to each project's toml was the friction that prompted the change. What was
  traded away: inertness bought a safe rollout of an unproven mode; once the
  mode existed, the human took the blast radius knowingly — `stable`
  re-resolves on every MCP server start, so `claim` begins refusing a 4th
  active task in projects that configured nothing. "No limit" is no longer
  expressible at all (`wip_limit = 0` remains a `ConfigError`, NOT the unbounded
  spelling); if unbounded is ever wanted it gets its own explicit spelling in
  its own task.
- `main` stays the integration branch — one task, one commit, pushed at
  `advance(to='review')` time, CI auto-release untouched.
- Worktree hygiene (release, GC of crashed agents' trees) is mechanical, and
  **never** destroys unpushed work.
- The `vikunja-mcp claimable` cross-repo contract with hgdev-acp is untouched.

## Non-goals

- Feature branches, PRs, or a merge queue. Rejected: they move the release
  pipeline (`stable` auto-patches off green `main`) and stall the drain.
- Multi-identity / multi-token parallelism. Out of scope; the dormant pull path
  in `next_task` already covers it if a second token ever appears.
- Moving worktree provisioning into hgdev-acp. Its mirror+worktree machinery is
  per-*run*; the local terminal `/loop` — which is what actually drains this
  repo — would get nothing.
- Predicting file-level conflicts between unrelated tasks from the tracker.
  See "Undeclared overlap" below: we detect at integration time instead.

## Decisions taken

| Fork | Decision |
|---|---|
| Unit of concurrency | Per-task **build** agents *and* **review** agents, each in a worktree |
| Path into `main` | Commit on a throwaway `task/<id>` branch, then `git push origin HEAD:main` with rebase-retry |
| Where the WIP limit lives | A number in the repo toml + a hard gate in `claim()` |
| Who owns the worktree | vikunja-mcp, via a new **CLI subcommand** (sibling of `claimable`) — not a new MCP tool |

`git worktree add` refuses to check out a branch that is already checked out
elsewhere, so `main` can live in at most one tree. Per-task branches are not a
preference — they are forced by that constraint. `git push origin HEAD:main`
keeps the "one task = one commit on `main`" rule intact regardless.

## Architecture

### 1. Tracker side (`config.py`, `workflow.py`, `server.py`)

No git here. This layer only counts slots.

```toml
[tracker]
wip_limit = 3                                # absent = 3 as well (#524); 1 = enforce_single_wip
                                             # (< 1 is a ConfigError, see below — not "no limit")
worktree_root = "../vikunja-mcp.worktrees"   # optional
```

`wip_limit` is committed team policy of the same class as `enforce_single_wip`:
read **only** from the repo toml, never from env, never a secret. Precedence:
`wip_limit` set → it is the truth; unset → `enforce_single_wip = true` ⇒ limit 1,
otherwise `DEFAULT_WIP_LIMIT` = **3** (tracker #524, superseding the reversed
"ships inert" goal above — it used to be "no limit"). `enforce_single_wip` keeps
working; docs mark `wip_limit = 1` as the modern spelling. `wip_limit < 1` raises
`ConfigError` at load rather than silently meaning "no slots". The number is
resolved in `workflow._effective_wip_limit`, which returns `int` and never
`None` — the gate is unconditional, and `Config.wip_limit is None` says only
that the key is absent, which is what keeps the `enforce_single_wip` step of
that precedence reachable.

`worktree_root` is machine-local, so unlike `wip_limit` it *does* take an env
override (`VIKUNJA_WORKTREE_ROOT`). Relative paths resolve against the git root.

**`claim()`** — the existing single-WIP block generalises to `len(active) >=
limit`. It also stops re-fetching: today the gate calls `_my_active_tasks()`
with no board and pays for a second full board fetch although `claim` already
holds the snapshot in `board`. Pass it through.

**`next_task(exclude: list[int] | None = None)`** — two additions.

1. `exclude` carries the task ids the caller *currently has a live agent on*.
   The tracker cannot know sub-agent liveness — that is a fact of the harness,
   not of the board — so the caller states it. An excluded id is **never offered
   by any branch** (resume, stuck Queue, or review) but **still counts as an
   occupied slot** — one rule, no per-branch exceptions. On a fresh tick
   after a killed turn the set is empty, and `next_task` correctly hands the
   active tasks back as "pick up the abandoned work", exactly as today.
2. Every result carries `wip: {"active": K, "limit": N, "free": …}` — both
   numbers always, never `null`, since #524 removed the unlimited case. The
   free-queue branch is offered only while `free > 0`; otherwise the result is
   `{"task": null, "wip_saturated": true, "wip": {...}}` — "wait for an agent to
   return; do not claim, do not sleep".

Branch order is unchanged: `mine` → stuck Queue → review offer → free queue.
A review offer does **not** consume a slot — background review is already "not
your active task" per SKILL.md; this just makes the accounting say so.

**Rollout safety.** `exclude` defaults to empty, so a standalone `vikunja-mcp
claimable` sees exactly what it sees today, and `wip_saturated` never reaches it
(that state is only visible from inside an already-running orchestrator). No new
`kind` enters the hub's closed enum → **no inverted rollout order needed**.

### 2. Dependencies between tasks

Already handled, and no new code is needed for the *declared* case.

The hard sequence gate stands in both places — `next_task` skips a gated
candidate, `claim` refuses outright (`workflow.py:731`) — keyed on
`follows`/`blocked` only, never `parenttask`. A predecessor counts as ready at
`READY_STAGES = {Review, Done}` (`workflow.py:36`).

The load-bearing coincidence: a predecessor becomes ready exactly at
`advance(to='review')`, which by this repo's rule is exactly when its commit is
pushed to `main`. A successor therefore cannot start before its predecessor's
code is in `origin/main`, and its worktree is cut from an `origin/main` that
already contains it. Transitivity is implicit — a middle link sits below Review,
so its own successors stay gated. `next_task`'s rework-first ordering
(`workflow.py:361`) already hands back the predecessor first when one agent
holds two tasks of one chain.

**So the parallel drain draws from the same gated pool as the serial one.**
Related tasks cannot be in flight simultaneously by construction.

**Undeclared overlap** is the genuinely new risk: two tasks with no declared
relation may still edit one file. The worst case is not a conflict (loud) but a
clean auto-merge of two individually-correct changes that are wrong together.
This cannot be predicted from the tracker, so we detect it at integration time,
in three layers:

1. **Push = rebase + re-verify.** `fetch` → `rebase origin/main` → **run the
   tests/lint again on the rebased tree** → only then `push origin HEAD:main`.
   Rejected again → repeat, bounded. This is the step that catches a semantic
   conflict the merge swallowed; the serial drain never needed it because
   nothing moved underneath.
2. **`decompose(ordered=…)` stops being cosmetic.** Under parallelism
   `ordered=False` is an assertion that the children are safe to build
   *simultaneously*. SKILL.md rule: unsure whether the subtasks touch the same
   code → `ordered=True`. We deliberately do **not** gate unordered epic
   siblings — that would gut `decompose` and leave no way to say "yes, really
   parallel".
3. **`wip_limit` as blast radius.** 2–3, not "as many as fit".

Accepted residual risk, named so it is a decision and not a surprise: task A is
at Review, B (`follows` A) is already claimed and building on top, and A's review
returns `needs_work`. This is **not new** — the gate opened at Review in the
serial drain too — but parallelism raises its likelihood. The existing machinery
handles it: A returns to Build and rework-first offers it ahead of B.

### 3. `workspace` CLI (`src/vikunja_mcp/workspace_cmd.py`)

Sibling of `claimable_cmd.py`, wired in `server.main` next to `claimable`.

```
vikunja-mcp workspace <task_id> [--role build|review] [--at <ref>]
vikunja-mcp workspace --release <task_id> [--role build|review]
vikunja-mcp workspace --gc
```

One JSON line on stdout, exit 0 = the command ran / 1 = it failed — the same
discipline as `claimable`. Unlike `claimable` this is **not** a cross-repo
contract: its only consumer is SKILL.md, which ships in the same wheel.

**Layout.** Default `<parent-of-repo>/<repo>.worktrees/task-<id>`, a sibling of
the repo — deliberately not inside it, where pytest collection, ruff and
`git add -A` would sweep them up. The git root comes from `git rev-parse
--show-toplevel`, not from the toml walk-up; only the former is correct for git.

**Create is idempotent**, and the step order matters:

1. `git worktree prune` — drop records of hand-deleted directories;
2. `git fetch origin` — the base is always a fresh `origin/main`, never local;
3. a worktree for `task/<id>` already exists → print its path, `created: false`
   (this is the resume-after-crash path: the agent walks back into its own tree
   with its unfinished work);
4. branch exists, worktree does not → `worktree add <path> task/<id>` — do
   **not** recreate the branch, it may carry commits;
5. neither exists → `worktree add -b task/<id> <path> origin/main`;
6. the path exists but is not a worktree → refuse, touch nothing.

**`--role review`** → `worktree add --detach <root>/review-<id> <ref>`, with
`--at` defaulting to `origin/main` (under this design the reviewed code is
already on `main`) though a reviewer normally passes the `evidence` sha from the
dossier. Detached is mandatory: `task/<id>` is checked out by the builder.

**`--release` — hgdev-acp's policy verbatim: push OK → remove, push FAIL →
keep.** A dirty tree (`status --porcelain`) or unpushed commits (`log
origin/main..HEAD`) → leave it alone and return `{"released": false, "reason":
...}` with exit 0 (the command ran; the verdict is negative). Otherwise
`worktree remove` + `branch -D`. Losing an agent's work to housekeeping is not
an acceptable failure mode. `--role` selects which of a task's two possible
trees to release (default `build`); a review tree is detached and carries no
branch, so only the `worktree remove` half applies to it.

**`--gc` is the reason this lives in vikunja-mcp at all.** Enumerate worktrees
under the root → parse the task id out of **both** naming shapes (`task-<id>`
and `review-<id>`) → ask the tracker which tasks are still alive → release
everything else under the same guards. Nothing but the tracker knows that, so a
crashed agent's orphan tree can only be reaped here. Read-only against the
tracker, same class as `claimable`.

Liveness differs by role and must not be conflated: a **build** tree is alive
while its task sits in Design/Build assigned to me; a **review** tree is alive
while its task sits in Review. `Workflow` grows one thin public accessor for
this (`active_task_ids()` / `review_task_ids()`) rather than the CLI reaching
into `_my_active_tasks()` — the boundary stays a real interface.

It follows that **create and release never touch the tracker** — no token, no
network, instant. Only `--gc` needs config.

**Concurrency.** Two `worktree add` calls on one repo race. An `flock` on
`<git-dir>/vikunja-mcp-worktree.lock` wraps create/release/gc — the same shape
as hgdev-acp's per-mirror mutex.

**Git surface isolation.** All git `subprocess` work lives **only** in
`workspace_cmd.py`; `server.py`, `workflow.py` and `api.py` stay git-free. This
goes into CLAUDE.md as a rule, not as an accident.

### 4. Process rules (`skills/tracker/SKILL.md`)

SKILL.md ships to every consumer, most of whom set no `wip_limit`. ~~The serial
flow therefore stays the documented default~~ — **inverted by #524**: an unset
key now yields `limit: 3`, so the consumers who set nothing are precisely the
ones running the PARALLEL flow, and the serial flow is what a project opts into
with `wip_limit = 1` (or the legacy `enforce_single_wip = true`). The rule
therefore keys on the NUMBER in `next_task`'s `wip` payload (`1` ⇒ serial,
`> 1` ⇒ parallel) — `limit: null` can no longer reach an agent at all, so the
old "`null` or `1` ⇒ serial" wording had to go. The agent still learns the limit
from the payload; the default is named in prose only as *what an unset key
means*, never as a number to hardcode into a tick.

**Orchestrator tick (parallel mode):**

1. `vikunja-mcp workspace --gc` — reap orphans from previous ticks;
2. while `wip.free > 0`: `next_task(exclude=[in-flight ids])` → `claim` →
   `workspace <id>` → dispatch a **background** per-task agent, its worktree path
   in the brief;
3. an agent returns → dispatch its reviewer in the background
   (`workspace <id> --role review --at <sha>`) → the slot frees → back to 2;
4. `wip_saturated` → wait for an agent to return; do not claim, do not sleep;
5. `next_task` empty *and* nothing in flight → yield the turn (unchanged).

The in-flight set lives in the orchestrator's tick. A killed turn loses it —
and that is fine: on the next tick an empty `exclude` surfaces the active tasks
as resume candidates, and the existing "the per-task agent died → dispatch a
fresh resume agent" rule picks them back up.

**Per-task agent:** works **only** inside its worktree; never `checkout`/`switch`
in the main checkout. Integration recipe replaces today's plain push:

```sh
git add <files of this task>
git commit -m "type(scope): … (tracker #N)"
git fetch origin && git rebase origin/main
<re-run this task's done criteria>    # catches what the merge swallowed
git push origin HEAD:main             # rejected → repeat the block, max 3 rounds
git rev-parse HEAD                    # evidence sha — read AFTER a successful push
```

"Done criteria" are the ones the orchestrator put in the dispatch brief (here:
`uv run pytest tests/unit -q` + `uv run ruff check .`). SKILL.md ships to every
consumer, so it names the *concept*, never a repo's specific commands.

A rebase conflict is resolved by the agent itself (it holds the task's context);
if it cannot, `call_human` — and the worktree survives, because `--release`
refuses to delete unpushed work. After `advance(to='review')` the agent calls
`workspace --release <id>`.

**Reviewer:** its own detached tree via `--role review --at <evidence sha>`,
`--release` after the verdict.

## Error handling and degradation

| Failure | Behavior |
|---|---|
| Not a git repo / no `origin` / git missing | `{"error": …}`, exit 1. The orchestrator does **not** kill the loop — it falls back to one slot in the main checkout, i.e. exactly today's behavior. |
| `--release` refuses (dirty/unpushed) | The worktree stays; `--gc` reports it on the next tick so a human can see it. |
| Rebase conflict | The agent resolves it, or `call_human`. Never force-push, never `--skip`. |
| Push rejected repeatedly (>3 rounds) | `call_human`; the card parks in **Your Call**, so the worktree reads DEAD to `--gc` — what keeps it is the unpushed work in it, not the stage. Cleaned up before asking (`rebase --abort`), it is clean and pushed and will be reaped mid-question; the agent re-runs `workspace <id>` after the human answers. |
| Per-task agent crashes | The task stays active, its worktree keeps the work, and `workspace <id>` returns that same tree to the resume agent. Strictly better than today, where the diff sat in the shared checkout. |
| No free slots *and* the free queue is gated | `wip_saturated` wins and is reported alone. `starving` describes a chain that cannot start; with zero slots that is not the actionable fact, and computing it would cost a board escalation for nothing. |

## Testing

- `tests/unit/` on `FakeAPI`: the `claim` gate at N; `next_task`'s `exclude`,
  `wip` payload, `wip_saturated`, branch order, and that a review offer does not
  consume a slot.
- `tests/unit/test_workspace_cmd.py` against **real git** in `tmp_path` (a bare
  origin plus a clone; no network): create is idempotent, an existing branch is
  reused rather than recreated, review trees are detached, `--release` refuses a
  dirty tree and refuses unpushed commits, `--gc` reaps only inactive tasks.
- The `--release` guards get **negative pins** per the house rule: delete the
  guard and the test must **fail**, otherwise the test is fictional.
- A pin that `claimable`'s verdict is unchanged with `wip_limit > 1` and an
  empty `exclude` — this protects the cross-repo contract.
- `config`: `wip_limit < 1` → `ConfigError`; `enforce_single_wip = true` with no
  `wip_limit` → limit 1; neither key set → `DEFAULT_WIP_LIMIT` (#524: the
  gate fires on the 4th claim, and the `< 1` refusal names the default instead
  of offering "no limit").
- One integration test for `--gc` against the real container (it reads the
  board); the rest of the git surface never talks to Vikunja.

## Rollout order

Each step is green and shippable on its own; the feature only becomes live at
step 4.

1. Config + `claim` gate + `next_task` (`wip_limit` absent → behavior
   byte-for-byte as today). Inert, ships immediately. *(Historical: that
   inertness is what #524 later reversed — an absent key means 3 now, so this
   step no longer describes the shipped code, only the order it landed in.)*
2. The `workspace` CLI. A new subcommand nobody calls yet.
3. SKILL.md rules — active only when `wip.limit > 1`.
4. Set `wip_limit = 2` in this repo's own `.vikunja-mcp.toml` and dogfood it.

Step 4 is the one that matters: per the house lesson, tests share the
implementation's model of the world and a dogfood run is what finds the bug they
all agreed on.

## Deferred follow-ups (recorded at landing, 2026-07-30)

Everything below was found by review, triaged as safe to carry, and deliberately
NOT fixed before landing. Kept here because the SDD ledger that held them is
scratch and does not survive; the git history alone would not preserve them.

**Highest value first:**

1. **The live parallel drain has never run.** The plan named this the step that
   matters; the tracker MCP tools would not connect in the implementing session,
   so the first real parallel tick is still pending. Watch the first one.
2. **Mechanical guard for the `--gc` race.** `--gc` at tick start sees a build
   tree as dead the instant its task reaches Review, while its agent may still
   be standing in it — nothing is destroyed (only clean, pushed trees go) but a
   cwd can vanish mid-turn. Shape: skip, silently and in neither list, a dead
   tree whose dir/index mtime is younger than N minutes. **Rejected** (recorded
   so it is not re-proposed): treating a build tree as alive while its card sits
   in Review would suspend the reaper indefinitely, since a card waits there for
   a human's Done.
3. **A killed local git call can manufacture an unrecoverable worktree.**
   `_GIT_TIMEOUT = 600` makes the `locked "initializing"` half-checkout reachable
   without an external killer, and nothing detects it: `_find` hands it back as
   `created: false` and it is dirty-forever, so release and gc keep and report it
   every tick. Hardening is one line — `list_worktrees` already parses the
   porcelain and currently ignores the `locked` key.
4. **The gc hold is bounded per request, not in total.** The tracker read sits
   inside the repo-wide flock by design (moving it out reopens a race where a
   tree created between the read and the reap is destroyed under a
   just-dispatched agent). It now uses a 10s/no-retry client, but total hold
   still scales with page count.

**Known bounds, accepted:**

5. The dirty guard has no ignore-awareness. A gitignored per-worktree secret or
   notes file is destroyed by a successful release; conversely a consumer whose
   build byproducts are *not* gitignored gets a refusal after a clean push.
   `--ignored` is strictly worse — `.venv`/`__pycache__` would block every release.
6. Both release guards inspect only `HEAD`, so work moved off HEAD inside a tree
   (`reset --hard HEAD~1`) is lost. Symmetric on the branch path; a bound of the
   "HEAD is the work" model, not a gap in one branch.
7. Two holes fail toward KEEP (safe, but the tree is unreapable): a sha reachable
   only from *another* worktree's detached HEAD, and a review tree whose only ref
   is later deleted or rebased away.
8. `kept` is routinely non-empty in two expected states — a Your Call card with
   an unpushed commit (every tick until the human answers) and an unreleasable
   review tree (forever). Documented; a `reason`-based severity split would help.
9. Dormant in a single-identity setup: a saturated pump never sees pending review
   offers, and the pull-path review recipe needs a `get_task` to find the
   evidence sha (the offer payload carries none).

**Test hygiene:**

10. One test mutates `os.environ` directly and `del`s it in `finally` (it would
    delete an ambient value) and asserts `GIT_TERMINAL_PROMPT == "0"`, so it
    passes spuriously on a machine exporting that variable. Two other pins cannot
    fail for the property they name (both honestly labelled), and one
    `pytest.raises` matcher would accept any exception containing "expected".
11. The sweep's `parent != wt_root` guard is **not** load-bearing — deleting it
    left all workspace tests green, because `_release_locked` re-derives the
    canonical path. Commented in place; a refactor that lets `_release_locked`
    trust the enumerated path would silently lose the protection.

**Cosmetic:** this plan's fenced SKILL.md excerpt still quotes the pre-fix
`call_human` wording with the correction outside the fence (an agent
re-executing that step would copy the fence); SKILL.md over-lists
«эпик-контейнер» among refusals a tick can meet, which `next_task` never offers;
a comment line-wrap garble in `workspace_cmd.py`; `worktree_root` recomputation;
a `kept` entry can name an already-removed tree; the WIP refusal message no
longer says which knob set the limit.

## First live parallel drain (2026-07-30)

Follow-up 1 above — *"the live parallel drain has never run"* — is now closed.
This section is the record, written by the per-task agent of VMCP-70 (518) from
**inside** the drain being measured: `task-518` was one of the three slots, so
everything below was captured live rather than reconstructed afterwards.

**Method, so the evidence can be weighed.** A read-only poller (`git worktree
list`, `git for-each-ref refs/heads/task/`, `git ls-remote origin main`, `ls` on
the worktree root; every ~20–45 s, appended to a timestamped log) ran for the
whole window. It never wrote: no `fetch`, no HEAD moves, and no `--gc` or
`--release` against sibling trees. Sibling worktrees were inspected only through
`git -C <path> status/diff/log`. All times below are UTC.

Three cards drained through the window: **524** and **522** landed and reached
Review, **515** and later **519** were claimed and started. Two reviewers ran
concurrently.

### Timeline (observed)

| Time | Observation |
|---|---|
| 12:47:41 | `task-518@29e8847`, `task-522@29e8847`, `task-524@ba771b8`; `origin/main = ba771b8` |
| 12:48:21 | `task-524` tree **and** branch `task/524` both gone — release is real |
| 12:48:34 | `origin/main = 6e5d7f4` (CI `chore: v0.2.51` on top of 524's commit) |
| 12:50:20 | `review-524  ba771b8 (detached HEAD)` appears; `task-515  6e5d7f4 [task/515]` appears |
| 12:51:47 | `task-522` flips to `6e5d7f4 (detached HEAD)` — its rebase has started |
| 12:52:35 | `both modified: SKILL.md` (conflict), `modified: workflow.py` staged clean |
| 12:53:47 | still unmerged — the conflict took >2 min of agent time |
| 12:54:09 | rebase done, `HEAD=509d707` on `task/522`, tree clean; `origin/main = 509d707` — pushed |
| 12:54:54 | `task-522` tree + branch gone; `origin/main = 0ba7780` (CI `v0.2.52`) |
| ~12:55:17 | `review-522  509d707 (detached HEAD)` appears |
| 12:56:31 | `task-519  0ba7780 [task/519]` appears — next slot refilled from the newest `main` |

### The checklist, item by item

**Worktrees appear on `task/<id>` branches cut from a fresh `origin/main` —
CONFIRMED, and the "fresh" part is not incidental.** `task-518`/`task-522` were
cut at `29e8847`, `task-515` at `6e5d7f4`, `task-519` at `0ba7780` — in each case
the tip of `origin/main` at creation time, three different bases within nine
minutes. Step 2 of the create sequence (`git fetch origin` before `worktree add`)
is doing what §3 says it does.

**Releases actually remove the tree and delete the branch — CONFIRMED, twice.**
`task/524` and `task/522` each vanished from both `git worktree list` and
`for-each-ref refs/heads/task/` within ~45 s of their push. The remaining
directory listing after each was clean; no `prune`-able stragglers appeared.

**Each task lands as its own commit on `main` — CONFIRMED.**

```
$ git log --oneline -20 origin/main | grep -o 'tracker #[0-9]*' | sort | uniq -c
   1 tracker #514
   1 tracker #522
   1 tracker #524
```

Exactly one commit per task, and each is single-parent (`git rev-list --parents
-1` yields two fields, so no merge commits entered `main`). `git push origin
HEAD:main` from a throwaway branch preserved the one-task-one-commit rule as §
"Decisions taken" claimed, and CI auto-released per landed task (`v0.2.51` after
524, `v0.2.52` after 522) — the release pipeline was untouched by parallelism.

**The push race happened; the recipe absorbed it — but no push was ever
*rejected*, and that distinction matters.** `main` moved two commits under 522
while it worked (`29e8847` → `ba771b8` → `6e5d7f4`), so a bare `git push origin
HEAD:main` would have been refused. It never got the chance: the agent ran
`fetch` → `rebase` → re-verify → push in that order, so the race was *absorbed
in advance* rather than survived after a rejection. **The `>3 rounds` retry path
in "Error handling" therefore remains untested by this run.** What was tested,
and passed, is the harder half: a real semantic collision.

**The rebase conflict — the most valuable thing in this window.** 522 and 524
both edited `SKILL.md` and `workflow.py` with no declared relation between them:
the exact "undeclared overlap" of §2. The outcome split cleanly along hunk
distance, and both halves are instructive.

- `SKILL.md` — 524 rewrote old-side lines 95–104, 522 edited 104–105. Overlapping
  → **hard conflict**, `both modified`, resolved by the agent by hand (it kept
  524's newer semantics and re-applied its own edit on top, per its worklog).
- `workflow.py` — 524 touched old line 418, 522 touched 409–410. Eight lines
  apart → **clean auto-merge, staged without comment.** This is precisely the
  failure mode §2 names as worse than a conflict: two individually-correct
  changes merged silently. Nothing but re-running the criteria could have caught
  it, and the agent did re-run them (470 passed) *after* resolution and *before*
  pushing. The recipe's re-verify step is load-bearing, not ceremony.

I verified independently that the resolution lost nothing:

```
$ git diff --numstat ba771b8 origin/main -- src/vikunja_mcp/skills/tracker/SKILL.md
39      6       src/vikunja_mcp/skills/tracker/SKILL.md
```

39 added / 6 removed between 524's commit and post-522 `main` — identical to
522's own diffstat, i.e. the only change to that file since 524 is 522's, and
the six removals are 522's deliberate rewrites in three separate hunks. 524's
`НЕ задан — дефолт **3**` rule and its `DEFAULT_WIP_LIMIT` import both survive at
`origin/main`.

**`wip_saturated` appeared and the pump respected it — CONFIRMED, with a caveat
that is itself a finding.** With a fully populated `exclude`:

```
next_task(exclude=[515, 518, 522]) ->
  {"task": null, "wip_saturated": true,
   "message": "all 3 WIP slot(s) are busy (3 active) — nothing can be claimed until one finishes",
   "wip": {"active": 3, "limit": 3, "free": 0}}
```

**A review offer does not consume a slot — CONFIRMED empirically**, not just in
the accounting: with `review-522` *and* `review-524` both live on disk,
`next_task(exclude=[515,518])` still reported `{"active": 2, "limit": 3, "free":
1}` and offered a free-queue card. Two concurrent reviewers narrowed the drain by
nothing, as §1 promised.

**`--gc` left the unrelated harness worktree alone — CONFIRMED.**
`.claude/worktrees/agent-a76c799de4858312f` and its branch
`worktree-agent-a76c799de4858312f` were present in **13 of 13** poller snapshots
spanning the entire window — the same window in which two build trees were
reaped and three created. The "not ours" guard holds on a real board.

### What contradicted the card, SKILL.md, or this spec

**1. Verifying a reported `evidence` sha with `git rev-parse` does not work.**
Card 518's own checklist says *"each `evidence` sha actually resolves (`git
rev-parse`; a subagent can report a sha that never landed)"*. Tested directly:

```
$ git rev-parse 0123456789012345678901234567890123456789
0123456789012345678901234567890123456789
exit=0
$ git rev-parse --verify 0123456789012345678901234567890123456789
0123456789012345678901234567890123456789
exit=0
$ git cat-file -e 0123456789012345678901234567890123456789^{commit}
fatal: Not a valid object name …^{commit}
exit=128
```

A full 40-char sha is echoed back with exit 0 by `rev-parse` **and** by
`rev-parse --verify`, whether or not the object exists — so the one check the
card names is the one check that cannot detect the failure it is guarding
against. Only `git cat-file -e <sha>^{commit}` fails.

And existence is still not enough, which this window proved live: 522's
pre-rebase commit `e3d5be6` **still resolves** (`cat-file -e` → 0, object not yet
gc'd after its branch was deleted) yet `git merge-base --is-ancestor e3d5be6
origin/main` → **NO**. Had the agent captured `evidence` before the rebase, the
sha would have passed both `rev-parse` and `cat-file` while naming a commit that
is not on `main`. The recipe's "read the sha AFTER a successful push" is what
prevents this, and 522's agent additionally self-invented the right check (`git
branch -r --contains HEAD`). Neither SKILL.md nor this spec's recipe names a
post-push verification step; they should. Filed as **VMCP-77 (526)**.

*(Honest note on method: my own first pass mistyped an abbreviated sha and got
`fatal: Not a valid object name` — a wrong guess at an abbreviation is
indistinguishable from a missing object. Verify against full shas only.)*

**2. `wip_saturated` is invisible to a caller whose `exclude` is incomplete —
even when `free` is 0 in the very same payload.** Tested at 12:58:53 with three
build trees live (`task-515`, `task-518`, `task-519`), first with the in-flight
set named and then without it:

```
next_task(exclude=[515, 518, 522])
  -> {"task": null, "wip_saturated": true, "wip": {"active": 3, "limit": 3, "free": 0}}

next_task()                       # same moment, empty exclude
  -> {"task": {"id": 518, …}, "resume": true, "stage": "Build",
      "wip": {"active": 3, "limit": 3, "free": 0}}
```

Same `free: 0`, and the second result carries no `wip_saturated` key at all: the
resume branch returns before the `free == 0` check, so the saturation signal is
structurally unreachable unless the caller names its live agents. SKILL.md
already requires the orchestrator to maintain `exclude` to avoid double-dispatch;
what it does not say is that the *saturation signal itself* depends on it. It
also independently confirms, by observation rather than by code-tracing, 524's
claim that `claimable`'s verdict is unaffected — with an empty `exclude` the
saturated state cannot be reached at all. Filed as **VMCP-78 (527)**.

*(The `[worklog]` comment on card 518 labels these two findings VMCP-76 and
VMCP-77 — both off by one. The refs above are the correct ones, read back from
`get_task(526)` → `VMCP-77 (526)` and `get_task(527)` → `VMCP-78 (527)`; the
tracker comment cannot be edited, so the correction lives here.)*

**3. The double-dispatch hazard is real, and I walked into it.** An earlier
`next_task()` at 12:48:34, again with no `exclude`, handed me **task 522** as
"your active task" (`stage: "Design"`, `resume: true`) — a card that at that
moment had a live sibling agent standing in `task-522` with uncommitted edits to
`SKILL.md` and `workflow.py`. An orchestrator that had lost its in-flight set
would have dispatched a second agent straight onto that dirty tree. SKILL.md
warns about this in prose; this is the observed instance.

### What I could not observe, and why

Stated plainly rather than filled in:

- **No agent's cwd vanished under it — but the `--gc` race was not disproved.**
  522's window between `advance(to='review')` and its tree's removal was ~45 s,
  and I cannot tell from the outside whether the removal was the agent's own
  `--release` or the orchestrator's `--gc`; the two are indistinguishable in
  `git worktree list`. No agent reported a lost directory. Follow-up 2 and card
  VMCP-71 (519) stand unresolved, neither confirmed nor cleared.
- **No review tree was observed being released.** Both `review-522` and
  `review-524` were still on disk when this record was written; their verdicts
  had not landed. The `--release <id> --role review` half of the lifecycle, and
  follow-up 8's "unreleasable review tree", remain unobserved.
- **No rejected push, and no `>3 rounds` `call_human` escalation** (see above).
- **The queue was never surveyed, and cannot be.** `next_task` offers one card at
  a time and `exclude` does not filter the free-queue branch
  (`workflow.py:580-584`), so no agent in this window — including me — was in a
  position to say what the queue held. This is the same limitation that produced
  card 522; it is a property of the tool, not a gap in this run.
- **`kept` was never non-empty** in any `--gc` output I had sight of, so
  follow-ups 5–8 (the release guards' bounds) got no live exercise.

### Inferred, not observed

Labelled separately on purpose:

- That a `--gc` ran during my window at all is inferred from SKILL.md's tick
  order plus the orchestrator's own recorded `{"released": [], "kept": []}` from
  the start of this tick. I did not see the invocation.
- That `task-524`'s and `task-522`'s removals were their agents' own `--release`
  calls (rather than `--gc`) is inferred from the ~45 s gap after each push and
  from SKILL.md's instruction to the per-task agent.
- The hunk-distance explanation of why `SKILL.md` conflicted while `workflow.py`
  auto-merged is my reading of the two diffs, not a statement from git.

### This card's own integration

Recorded because the instruction asked for it. This card edits the very file 524
rewrote and pushed mid-window, so **518 is itself an instance of undeclared
overlap.** I rebased onto the live `origin/main` *before* writing, so the text
above is appended to 524's current version rather than to the `29e8847` copy my
tree was cut from; that first rebase was a pure fast-forward (`Successfully
rebased and updated refs/heads/task/518`, no commits of my own to replay), which
left the work sitting on `6e5d7f4` (`git log -1 --format=%p 9ec979f` → `6e5d7f4`).
Between the base this tree was cut from and the base its commit finally landed on,
`main` advanced six commits (`git rev-list --count 29e8847..6651eb7` → `6`).

The **push-time rebase was real, not a no-op**: the commit was replayed from base
`6e5d7f4` onto `6651eb7`, which changed its sha (`9ec979f` → `16821e9`, and see
the amend below). **Four** commits had landed underneath in the meantime, not two
(`git log --oneline --reverse 6e5d7f4..6651eb7`):

```
509d707 docs(skill): fill the free WIP slots — overlap is detected at integration, not predicted (tracker #522)
0ba7780 chore: v0.2.52 [skip ci]
f7f0eaf test(cli): make four workspace-CLI pins able to fail (tracker #515)
6651eb7 chore: v0.2.53 [skip ci]
```

It replayed **without conflict** — and the first commit it replayed cleanly over
is `509d707`, task 522's own landing: the very commit this section's centrepiece
is about. `git show --numstat 509d707` gives `39 6` on `SKILL.md` and `8 4` on
`workflow.py`, neither of which 518 touches (`git show --name-only 1c295cb` lists
this design spec and nothing else); 515's commit likewise touched only
`tests/unit/test_workspace_cmd.py`, and `git log 6e5d7f4..6651eb7 -- <this file>`
is empty, so nothing in the replayed range held the file this change appends to.
That makes this the cleanest available demonstration of the point: **522's diff
collided head-on with 524's and passed under 518's without a mark** — same
repository, same window, the same total absence of any declared relation. The
conflict there was not bad luck but hunk overlap, and its absence here is not
virtue but distance.

The shipped commit is a pure append of **+269/-0** (`git show --numstat 1c295cb`),
and its history holds three objects rather than two:

```
$ git log -1 --format='%h %p %ci' 9ec979f   # 9ec979f 6e5d7f4  16:01:11  written,  +247/-0
$ git log -1 --format='%h %p %ci' 16821e9   # 16821e9 6651eb7  16:01:13  rebased,  +247/-0
$ git log -1 --format='%h %p %ci' 1c295cb   # 1c295cb 6651eb7  16:01:43  amended,  +269/-0
$ git merge-base --is-ancestor 16821e9 1c295cb; echo $?   # 1 — not a child, an amend
$ git diff --numstat 16821e9 1c295cb        # 23 1 — this section, added after the rebase
```

One caveat on that middle object, stated so a later reader is not misled: `16821e9`
is reachable from no ref at all (`git branch -a --contains 16821e9` → empty, and
`git rev-list --all` does not list it). It is an amend orphan, alive only as a loose
object, and a `git gc --prune` will eventually remove it — after which the two
commands above stop resolving. It is quoted as it was verified, and it is also an
accidental second instance of VMCP-77 (526)'s point: the sha satisfies
`git cat-file -e 16821e9^{commit}` today while belonging to no branch whatsoever,
so existence and ancestry really are separate questions.

So the real chain is `9ec979f` → rebase → `16821e9` → **amend** → `1c295cb`: this
section was written *after* the rebase it describes and folded in with `--amend`,
which is why the pushed diff is 22 lines larger than the one the rebase replayed.
Nothing about the rebase changes, but a record that documents its own commit has
to disclose that the commit was rewritten after the events it reports.

By the end of the window `main` carried exactly four task commits from this
drain, one each:

```
$ git log --oneline -24 origin/main | grep -o 'tracker #[0-9]*' | sort | uniq -c
   1 tracker #514
   1 tracker #515
   1 tracker #522
   1 tracker #524
```

### Correction to the section above (recorded, not overwritten)

The paragraphs under "This card's own integration" are a correction. As first
committed in `1c295cb`, that one section — alone in this record — was written from
recollection instead of from the poller log and `git`, and the independent review
of this card caught it by re-running the cheapest check in the whole document.
What it claimed, against what git says:

| Claimed in `1c295cb` | Actual | Check that settles it |
| --- | --- | --- |
| replayed from base `0ba7780` | `6e5d7f4` | `git log -1 --format=%p 9ec979f` |
| "two commits landed underneath" | four | `git rev-list --count 6e5d7f4..6651eb7` |
| "a pure append (+247/-0)" | `+269/-0` | `git show --numstat 1c295cb` |
| `9ec979f` → `1c295cb` | `9ec979f` → rebase → `16821e9` → amend → `1c295cb` | `git log -1 --format='%h %p' 16821e9` |
| `main` "advanced five commits" | six, cut base to landing base | `git rev-list --count 29e8847..6651eb7` |

Not one of these changes a conclusion. The push-time rebase was real and it
replayed over *more* than was claimed, including the single commit that makes the
paragraph's own point best. That is exactly why the errors are recorded here
instead of being quietly overwritten. This document's entire value is that it was
captured rather than reconstructed; the one paragraph that was reconstructed is
the one that failed review, and deleting the evidence of that would delete the
most transferable warning the run produced: **in an evidence document, the section
about yourself is the section you are likeliest to write from memory rather than
from the record, and it is also the cheapest one for a reader to falsify.** Five
wrong figures in twenty lines, none of them load-bearing, were enough to put the
unfalsifiable 95% of the record — the poller snapshots, the mid-rebase `git
status`, the `next_task` payloads — under suspicion. Keeping the correction
visible also keeps the fix itself falsifiable: every figure above now carries the
command that produced it, so the next reader can re-derive the correction as
cheaply as the review re-derived the error.

**And one "could not observe" item is closed by this correction's own push.** The
list above says *"No rejected push"*. The first push attempt of this rework commit
was rejected, and its cause is worth the record:

```
$ git push origin HEAD:main
error: failed to push some refs to 'github.com:ufna/vikunja-mcp.git'
hint: Updates were rejected because a pushed branch tip is behind its remote
hint: counterpart.
$ git fetch origin && git log --oneline --reverse fe18b4a..origin/main
43f3df9 chore: v0.2.56 [skip ci]
```

The commit that beat it to `main` was not a sibling task at all — it was **CI's own
version bump**, the auto-release for the task that had landed moments before,
racing an agent who had just rebased onto the very commit that triggered it. So the
race the retry loop exists for is not merely agent-versus-agent: in this repo every
landing spawns a second, machine push about a minute later, which makes a fresh
rebase stale almost as soon as it succeeds, and makes a rejected push the expected
outcome for anyone who rebases immediately after a sibling lands rather than an
edge case. Round two — rebase onto `43f3df9`, re-run the checks, push — carried it
in. The 3-round ceiling and the `call_human` escalation past it are still untested.

That paragraph was written after this commit was first created and folded into it
with `git commit --amend` — the same operation the table above faults the original
record for hiding. Amending to keep a record true is fine; the defect was never the
amend, it was the silence about it.

### Verdict

The mechanism works. Isolation, per-task commits, release, the `wip` accounting
and the "not ours" guard all behaved as designed on a real board, and the one
genuinely dangerous case — a silent clean auto-merge alongside a loud conflict,
between two tasks the tracker had no way to know were related — was caught by the
re-verify step exactly where §2 said it would be. What this run did **not**
exercise is the failure edges: a rejected push, the retry ceiling, review-tree
release, and the `--gc` grace race. Those are still theory.
