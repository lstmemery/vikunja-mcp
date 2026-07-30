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
- Ships **inert**: a consumer that sets nothing behaves byte-for-byte as today.
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
wip_limit = 3                                # absent = today's behavior; 1 = enforce_single_wip
                                             # (< 1 is a ConfigError, see below — not "no limit")
worktree_root = "../vikunja-mcp.worktrees"   # optional
```

`wip_limit` is committed team policy of the same class as `enforce_single_wip`:
read **only** from the repo toml, never from env, never a secret. Precedence:
`wip_limit` set → it is the truth; unset → today's behavior (`enforce_single_wip
= true` ⇒ limit 1, `false` ⇒ no limit). `enforce_single_wip` keeps working;
docs mark `wip_limit = 1` as the modern spelling. `wip_limit < 1` raises
`ConfigError` at load rather than silently meaning "no slots".

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
2. Every result carries `wip: {"active": K, "limit": N|null, "free": …}`. The
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

SKILL.md ships to every consumer, most of whom set no `wip_limit`. The serial
flow therefore stays the documented default, and the parallel flow is described
as what happens **when `wip.limit > 1`**. The agent learns the limit from
`next_task`'s `wip` payload; it is never hardcoded in prose.

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
  `wip_limit` → limit 1.
- One integration test for `--gc` against the real container (it reads the
  board); the rest of the git surface never talks to Vikunja.

## Rollout order

Each step is green and shippable on its own; the feature only becomes live at
step 4.

1. Config + `claim` gate + `next_task` (`wip_limit` absent → behavior
   byte-for-byte as today). Inert, ships immediately.
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
