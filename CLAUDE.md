# CLAUDE.md

Guidance for Claude Code when working in this repository.

## What This Is

Workflow-level MCP server for a self-hosted [Vikunja](https://vikunja.io)
tracker — NOT a CRUD wrapper. The pipeline and its gates ARE the product:

```
Backlog → Queue → Design → Build → Review → [human] → Done
                     ↕        ↕
                  Your Call         (+ independent review of EVERY task in Review)
```

12 agent tools (`next_task`, `claim`, `get_task`, `comment`, `advance`,
`call_human`, `return_task`, `decompose`, `file_task`, `review_task`,
`attach_file`, `download_attachment`); agents
can never move a task to Done — that transition is human-only by design. Gates are
guardrails for agents; the real security boundary is the scoped API token.

## Commands

```bash
uv sync                                   # env (Python 3.11+, uv)
uv run pytest tests/unit -q               # 500+ unit tests (FakeAPI, MockTransport)
uv run ruff check .                       # lint (line-length 100)
uv run vikunja-mcp --version              # smoke
uv run vikunja-mcp claimable              # one JSON line: is there claimable work for this
                                          # token? (hgdev-acp hub's pre-launch idle check)

# integration — real Vikunja 2.3.0 in docker (skipped without VIKUNJA_TEST_URL):
docker run -d --name vikunja-test -p 3456:3456 \
  -e VIKUNJA_DATABASE_TYPE=sqlite -e VIKUNJA_DATABASE_PATH=/tmp/vikunja.db \
  -e VIKUNJA_FILES_BASEPATH=/tmp/files -e VIKUNJA_SERVICE_JWTSECRET=integration-test-secret \
  -e VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456/ -e VIKUNJA_SERVICE_ENABLEREGISTRATION=true \
  vikunja/vikunja:2.3.0
until curl -sf http://localhost:3456/api/v1/info >/dev/null; do sleep 1; done
VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration -q
docker rm -f vikunja-test
```

## Architecture

- `src/vikunja_mcp/config.py` — 4-layer config: env (`VIKUNJA_URL/TOKEN/PROJECT_ID`)
  > repo-local `.vikunja-mcp.env` (same dir as the toml, found by the same walk-up,
  gitignored) > repo `.vikunja-mcp.toml` (walk-up from cwd) > `~/.config/vikunja-mcp/env`.
  Token is NEVER read from the repo toml (so it can't be committed and used); optional
  `VIKUNJA_NOTIFY_WEBHOOK` (`notify.py` — best-effort Slack-shaped ping when `call_human`
  parks a card in Your Call) is a secret of the same class: env layers only, never the toml.
  Two parallel-drain keys sit on opposite sides of that split: `wip_limit` (how many
  Design/Build tasks one token may hold at once; generalises `enforce_single_wip`, which is
  exactly 1) is committed TEAM POLICY — repo toml ONLY, never env. **Unset means
  `DEFAULT_WIP_LIMIT` = 3, not "no gate"** (human decision, tracker #524 — the gate is always
  on, so every project drains 3-wide without a toml edit); precedence is explicit `wip_limit` →
  else 1 when `enforce_single_wip = true` → else 3, resolved in `workflow._effective_wip_limit`
  (which returns `int`, never `None`) while `Config.wip_limit is None` keeps meaning only "the
  key is absent". `wip_limit = 0` is a `ConfigError`, NOT the unbounded spelling: "no limit" is
  deliberately not expressible any more. **It is a gate on ONE transition (`claim`), not an
  invariant on the active count** (tracker #529): a card re-enters Build without passing it —
  `review_task(verdict='needs_work')` bounces it Review→Build, a human moves it out of Your Call
  or hand-places an assigned card, or the toml lowers the number while work is in flight — so
  `wip.active` legitimately EXCEEDS `wip.limit` (4/3 observed live), and that is correct, because
  rework must be receivable at the limit. `next_task`'s `free` is `max(0, limit - active)`, so the
  overshoot is invisible there and readable only from `active`/`limit`; `claim` keeps refusing and
  reports the true count. Making it impossible, or gating the second path, is deliberately NOT
  done — both would strand reviewed work. `worktree_root` /
  `VIKUNJA_WORKTREE_ROOT` (where per-task worktrees materialise, default a `<repo>.worktrees`
  sibling) is MACHINE-local, so unlike `wip_limit` the env layers DO win over the toml.
- `src/vikunja_mcp/api.py` — REST client. **Vikunja gotchas are codified here:
  PUT = create, POST = FULL-REPLACE update** → every update is
  read-modify-write; kanban view updates must always send
  `bucket_configuration_mode="manual"` + `position` + `title` + `view_kind`
  or the board loses its columns; board fetch paginates per bucket
  (page size read from `/info`'s `max_items_per_page`; when the server never says, the
  size is UNKNOWN — **never guessed** — and the loop pages until no NEW task arrives in
  the required buckets; dedupe page overlap by bucket+task id, then GLOBALLY by
  task id keeping the last-seen bucket so a task moved mid-pagination lands once).
  There is deliberately no fallback constant: a guessed size (the old
  `_PAGE_SIZE_FALLBACK` = 50) silently TRUNCATED the board on an instance whose real
  limit was smaller, and a truncated board told `--gc` a live task was gone — so it
  reaped a live worktree (tracker #543). "Unknown" must stay unknown. That branch is
  also BOUNDED — `_UNKNOWN_PAGE_SIZE_MAX_PAGES` requests, and hitting it RAISES rather
  than returning a short board (tracker #548): a truncated board is indistinguishable
  from tasks that are genuinely gone, so a read that cannot finish must fail LOUDLY.
- `src/vikunja_mcp/workflow.py` — the product rules: stages, gates,
  assign-then-verify claim (with self-heal), review offering (verdict vs
  worklog timestamps), comment markers `[claim] [spec] [worklog] [нужен
  человек] [blocked] [decompose] [review] [attach]` plus mutually-exclusive verdict
  labels `reviewed`/`review-failed` (push-review of EVERY task, not just bug fixes —
  tracker #117: `advance(to='review')` nudges `review_needed` + `review_kind`
  (`'bug'`|`'change'`, the reviewer's rubric) for any card WITHOUT the `epic` label,
  and resets a stale `reviewed`/`review-failed`). An epic container is the lone
  exception: its code lives in its children, each reviewed on its own advance.
  Behavior changes belong here, with a unit test per gate.
- `src/vikunja_mcp/server.py` — thin `MCPServer` wiring (the mcp 2.0 SDK; FastMCP is
  gone — `version=` is passed explicitly because `MCPServer` defaults it to `""`, where
  FastMCP used to report the SDK's own); `_tool` decorator
  converts `WorkflowError/ConfigError/VikunjaError/httpx.HTTPError` into
  `{"error": ...}` tool results (never crashes the stdio server). Tool
  docstrings are agent-facing rules — treat them as UX copy, keep them
  prescriptive (when to call, not just what it does). **The MCP SDK is imported
  LAZILY** (`_server()`, the lone import site; `@_mcp_tool` only collects the 12
  tools, `_server()` registers them when the stdio server is actually built) —
  never move it back to module scope: `claimable`/`workspace`/`setup`/
  `install-skill`/`--version` don't speak MCP and would pay ~0.43s of SDK import
  each, worst on `claimable`, which hgdev-acp spawns per poll tick.
- `src/vikunja_mcp/setup_cmd.py` — `vikunja-mcp setup` (idempotent board
  reconcile: canonical buckets + ORDER via positions, `Todo→Queue` /
  `Doing→Build` migration, shares) and `install-skill` (copies the packaged
  SKILL.md for Claude Code + opencode AND auto-provisions a conditional
  `SessionStart` hook — a dependency-free POSIX-`sh` `~/.claude/hooks/…sh`
  registered in `~/.claude/settings.json` that, ONLY inside a tracker project
  (`.vikunja-mcp.toml` walk-up), injects the orchestrator standing-context so a
  bare `/loop` drains the Queue instead of the generic autonomous default;
  idempotent merge — no matcher = fires on startup/resume/clear/compact).
  `sync_installed_artifacts` self-heals those on **MCP server start**
  (called from `server.main`, so a moving-`stable` rollout refreshes the
  installed SKILL.md + hook as automatically as the code): refresh-only
  (rewrites an installed copy only when it exists and differs — never
  provisions `~/.claude`), best-effort (never raises → never crashes the
  stdio server, never writes stdout), opt out with `VIKUNJA_MCP_NO_SKILL_SYNC`.
- `src/vikunja_mcp/claimable_cmd.py` — `vikunja-mcp claimable`: the sibling-EXPORTED
  claimable verdict (ONE JSON line `{"claimable","kind","task_id"}`, exit 0 = the check
  ran / 1 = it failed) that hgdev-acp's repo-agent loop spawns (`uvx …@stable vikunja-mcp
  claimable`) as its pre-launch idle check, instead of re-implementing next_task's gates
  hub-side. It runs the REAL `Workflow.next_task()` — zero gate drift by construction —
  which is therefore **READ-ONLY BY CONTRACT** (comment on `next_task` + a no-writes unit
  test): the hub polls it per loop tick, so a side effect there becomes a per-poll tracker
  mutation. Born from a dogfood regression: the hub used to guess from kanban BUCKET
  PRESENCE, so a Review column holding 25 tasks all assigned to the agent (done work
  awaiting a human's Done) read as "work!" forever — ~144 no-op agent boots/day ≈ $105/day
  — while `next_task` rightly offered nothing (you never review your own work). The JSON
  keys and the exit-code split are a public cross-repo contract; changing them breaks the
  hub's check (fail-closed: its loops go red until both sides move together).
- `src/vikunja_mcp/workspace_cmd.py` — `vikunja-mcp workspace`: per-task git worktrees for
  the parallel drain (`wip_limit > 1`). **The ONLY module in the package that runs git** —
  `server.py`/`workflow.py`/`api.py` stay git-free by rule, not by accident (a subprocess in
  the stdio server's path is a new class of crash). `git worktree add` refuses a branch that
  is already checked out, so each agent gets its own throwaway `task/<id>` branch and pushes
  with `git push origin HEAD:main` — "one task = one commit on main" and the CI auto-release
  survive untouched. Create (`<id>`, `--role review --at <sha>` for a detached review tree)
  and `--release <id>` need neither the tracker nor a token (create is not offline, though —
  it runs `git fetch origin`); only `--gc` reads the tracker, because
  only the board can say whether the task behind an orphaned tree is still alive (build tree
  ⇔ Design/Build assigned to me via `Workflow.active_task_ids`, review tree ⇔ card in Review
  via `review_task_ids`, one shared `liveness_board()` fetch, read-only like `claimable`).
  Every entry point canonicalises to the MAIN worktree first (`_main_worktree`), so create /
  release / gc agree on paths and config even when invoked from INSIDE a linked tree — the
  normal place for a per-task agent, and where the gitignored `.vikunja-mcp.env` does not
  exist. Safety invariant taken from hgdev-acp's reaper: push OK → remove, push FAIL → KEEP
  (dirty, unpushed, or reachable-from-no-ref ⇒ reported, never destroyed).
  Housekeeping is never how an agent's work disappears. Every refusal carries a machine-readable
  `code`, and `--gc` GRADES them into two lists (`_keep_is_expected`): `kept` = a human should
  look, `expected` = the two routine states that used to keep `kept` permanently non-empty — a
  parked Your Call card's unsaved work (hence `Workflow.parked_task_ids`, off the same board
  fetch) and a review tree's in-tree commit. Routine is a property of the guard AND the board AND
  the ROLE — `unreachable-head` is routine only in a REVIEW tree (the conjunct stays as a
  backstop even though #540 stopped build trees from reaching it). A BUILD tree that is not on
  its own `task/<id>` branch — what an interrupted `git rebase origin/main` leaves: CLEAN, yet
  DETACHED — is refused by BOTH `ensure` (loudly, so a resume agent is never handed a tree whose
  HEAD is not where it is told) and `--release` (`detached-build`, because the unpushed-commits
  guard cannot run on a tree that is off its branch), each naming `git rebase --continue`/
  `--abort` for the AGENT to choose: the tool never picks, since `--abort` discards replayed
  work. An unknown code lands in `kept`: noisy beats quiet. A `released`
  entry can still need action — #517's `branch_deleted: false` + `warning` (the tree went, the
  branch leaked), which is why the rulebook says read `kept` AND scan `released`.
- `src/vikunja_mcp/skills/tracker/SKILL.md` — process rules for agents
  (queue discipline, orchestrator-dispatches-subagents, report format,
  independent review of EVERY task and not just bugs («Независимое ревью
  изменений»), and — when `wip.limit > 1` — the parallel drain:
  `exclude`/`wip_saturated`, one worktree per task, rebase-then-recheck-then-push).
  Ships inside the wheel; root `skills` is a symlink. **THIS file is the authoritative
  copy** — `sync_installed_artifacts` refreshes the installed `~/.claude/skills/tracker/SKILL.md`
  once, at MCP server start, and a session's server starts once, so the text the `tracker`
  skill serves is frozen at session start while this one moves with every landing. Working
  here, read it from the worktree; a task whose deliverable IS a SKILL.md edit therefore
  cannot verify itself by invoking the skill (it gets the pre-session text back and reads as
  "my edit did not take") — `grep`/`diff` the worktree file and say so in the `[worklog]`.
  The rule is stated for agents in the skill's own «Какую копию этих правил ты читаешь».

## Testing Philosophy

TDD. Unit tests drive `Workflow` through `tests/unit/fakes.py::FakeAPI` —
an in-memory mirror of the real client's full surface (keep it 1:1 when you
extend `VikunjaAPI`; it seeds Vikunja's auto To-Do/Doing/Done buckets on
create_project, enforces delete-only-empty buckets, monotonic comment
`created`). Integration tests hit a real container and exist to catch what
the fake can't: permission scopes, pagination shape, relation shapes,
`/login` rate limit (10/60s — conftest retries 429).

**The unit count above is a FLOOR (`500+`), and must stay one — never re-pin it
to an exact figure.** Its only job is a tripwire: a mistyped path makes `pytest`
select NOTHING and print "no tests ran", which looks very much like a pass. A
floor catches that and survives every landing; an exact count is stale by
construction here (at `wip_limit = 3` up to three worktrees land tests
concurrently — the pinned number was wrong twice in one day, 69 → 520 → 528, and
had drifted again to 529 by the time card 555 removed it). It is also an
attractive nuisance in a repo that verifies by running: **capture your own count
from your own run — a figure read out of this file was only ever true at the sha
that wrote it.** Touch the floor only if the suite ever shrinks below it, which
is itself worth noticing. Where a figure genuinely needs precision, DATE it
instead — as the release section does with its landings-per-day snapshot.

## Releases: the `stable` channel

Consumers' `.mcp.json` subscribes to the moving `stable` branch with
`--refresh-package` → every session start re-resolves it (auto-rollout,
no per-consumer bumps). Immutable `vX.Y.Z` tags = history + rollback.

**Patch releases are automatic** during active development. Every green push
to `main` fires the `release` job in `.github/workflows/ci.yml`
(`needs: [lint-and-unit, integration]`): it runs `scripts/bump_version.py`
(bumps the patch in ALL THREE version files — `pyproject.toml`,
`src/vikunja_mcp/__init__.py` and `uv.lock`'s self-entry; the lock is easy to
forget and it is a *dependency-resolution* file, so "version-only" does not mean
"touches nothing that matters"), commits `chore: vX.Y.Z [skip ci]`, tags
`vX.Y.Z`, and force-moves `stable` onto that bump commit. The job holds
`permissions: contents: write` (least-privilege, that job only) and a `release`
concurrency group (serializes racing pushes); the bump commit is pushed with
`GITHUB_TOKEN`, which by design does NOT re-trigger CI (plus `[skip ci]` as a
second belt). So `stable` always tracks the latest green `main`, patch-bumped,
hands-off.

**That bump commit is also a racer, and sizing the drain's retry loop is its
job.** Because it lands 37 s–2 m 55 s after the task commit that triggered it
(median 1 m 41 s; on 2026-07-30 **17 of the 46 commits that reached `main` were
this bot's**), a per-task agent's freshly-completed rebase goes stale within
about two minutes of *any* landing — so under a parallel drain a rejected `git
push origin HEAD:main` is the expected outcome, not an anomaly. The
`GITHUB_TOKEN`/`[skip ci]` property above is what BOUNDS it: the release never
triggers itself, so it never pushes twice in a row and can cost an agent at most
one round. That bound is what sizes SKILL.md's integration ceiling, and the
ceiling is a FORMULA, not a constant. Two steps, and the second is the one that
kept getting dropped: the worst purely MECHANICAL run at `wip_limit = N` is
2·(N−1) + 1 rounds — **5** at the default 3 — and the ceiling must sit STRICTLY
ABOVE that (otherwise it fires on arithmetic), i.e. one more round. So the
ceiling is **`2 × wip_limit`**: 2 at limit 1, **6** at this repo's default 3, 8
at 4, 10 at 5. The worst run and the ceiling are DIFFERENT numbers — quoting the
first where the second belongs is what card 556 caught in this very paragraph.
The rulebook self-heals onto every consumer and `wip_limit` is per-project, so a
pinned constant would call a human onto pure arithmetic in any project running a
wider drain (card 550) — and an agent whose brief omits the limit does not guess
it either: `wip_limit` is repo-toml-only, the toml is committed and therefore
present even in a linked worktree, so it READS it, and the bare 6 survives only
for "there is no toml at all", which is exactly the state that means the default
(card 559). And the count is only the budget: what decides whether a round was
owed at all is asked in two steps, in this order. First *did it land anyway?* — a
server can take the ref update and still leave the client reporting failure, so
`git merge-base --is-ancestor HEAD origin/main` (after a fetch) comes first, and
exit 0 means the work is already on `main`: verify the sha and move on, never
wake anyone. Only exit 1 reaches the second question, *what won the race* (`git
log --oneline HEAD..origin/main` — empty means it was never a race, so retrying
is futile and the agent escalates without spending the budget). That order is
load-bearing rather than tidy: a landed push with a sibling on top shows a
NON-empty range, and the retry it invites rebases the already-upstream commit
away, after which `git rev-parse HEAD` names the SIBLING's commit as evidence and
both landing checks pass on it. See "Откуда потолок" there.

**Never let the literal ci-skip marker into a commit MESSAGE — quoting counts.**
Writing *about* the release is the trap: the marker is matched anywhere in the
message, body and code spans included, so a commit that merely quotes the bump
commit's subject cancels its own CI run — and does so silently. It is a family,
not one spelling: GitHub also honours `[ci skip]`, `[no ci]`, `[skip actions]`,
`[actions skip]` and a `skip-checks: true` trailer. The push
succeeds, both evidence-sha checks pass, and the task looks landed, but there is
no run, no auto-release, and the change never reaches `stable`, i.e. never
reaches consumers. Name the marker descriptively in messages (in a *file* the
literal is harmless), and after pushing confirm a run actually EXISTS for your
sha (`gh run list`) — "no run" and "green run" look identical from git.

Manual procedure remains for:
- **Rollback**: `git branch -f stable vX.Y.Z && git push -f origin stable`
  onto an older, known-good tag. `stable` moves ONLY to tagged, CI-green commits.
- **Minor / major bumps**: hand-edit `version`/`__version__` to `X.(Y+1).0`
  or `(X+1).0.0` in a commit; CI resumes auto-patching from the new baseline.

## Dogfood: this repo's own tasks

This project tracks itself in the same tracker (project `vikunja-mcp`,
id 10 — see `.vikunja-mcp.toml`). Follow the tracker flow for real work
here: the orchestrator is a thin pump — `next_task` → claim → dispatch ONE fresh
per-task agent for the WHOLE task → drain next. That agent owns the whole
lifecycle (`get_task` → spec/`advance(to='build')` → implement, possibly spawning
its own sub-agents → commit+push → `advance(to='review')`); the orchestrator does
no task content itself. EVERY task reaching Review gets independent agent review, not
just bugs (orchestrator dispatches a sibling reviewer; only an `epic` container is
exempt). Whenever the effective limit exceeds 1 — this repo's `.vikunja-mcp.toml`
says `wip_limit = 3` explicitly, and a project that says nothing gets the same 3 by default
(tracker #524) — the same pump keeps several per-task agents in flight at once, up to the
limit `next_task` reports in its `wip` payload, each in its OWN worktree from
`vikunja-mcp workspace <id>`, and the pump
passes `exclude=[ids it has a live agent on]` so `next_task` doesn't re-offer them. Any
`workspace` failure degrades to one slot in this checkout, never a stopped loop. Rules
for agents live in SKILL.md («Параллельный дренаж»).
Run it under `/loop` for continuous operation — the agent drains the queue and,
instead of stopping when idle, waits for its next tick. Pick the mode by
supervision: self-paced (`/loop`, no interval) is fine WHEN SUPERVISED, but for
UNATTENDED / overnight runs use an INTERVAL-backed loop (`/loop 10m`). Why: a
self-paced loop arms its next tick only via an end-of-turn `ScheduleWakeup`, so a
turn killed before that call (session limit, API error, crash) arms nothing and the
loop silently dies forever — whereas an interval loop stores its cadence as a
persistent session cron that the harness daemon fires BETWEEN turns, surviving a
killed turn. Honest limit: neither mode survives the session PROCESS exiting — that
needs a human `claude --resume` (restores session crons within 7 days) or an
external supervisor (sibling project hgdev-acp), not anything vikunja-mcp can ship;
the SessionStart hook only FRAMES a running loop, it cannot restart a dead one. This
loop deliberately OVERRIDES the generic autonomous-`/loop` default ("steward, not
initiator: don't start fresh work without a human go-ahead, stop when idle") — the
Queue is human-triaged work, so claiming a fresh Queue task and dispatching IS the
mandate, not unbidden initiation; an empty queue means yield-to-next-tick, never a
stop. When the orchestrator needs a human answer, it asks via `call_human` (a card)
— never a console prompt (`AskUserQuestion`/`ExitPlanMode`/plain text), since the
human isn't at the console; after asking it keeps draining, and the human answers
and moves the card back so the loop resumes.
Each task lands as its own commit on `main`, pushed at `advance(to='review')`
time (`… (tracker #N)`, `evidence` = the sha) — a completed task commits and
pushes itself, and that green push auto-releases a patch (CI bumps ALL THREE version
files — `pyproject.toml`, `src/vikunja_mcp/__init__.py`, `uv.lock`'s self-entry —
tags `vX.Y.Z`, and moves `stable` — no separate release task for patches;
see the Releases section). The repo
is PUBLIC — this repo's own token is supplied via the repo-local
`.vikunja-mcp.env` (sits next to `.vikunja-mcp.toml`, gitignored), never
committed.

## Live instance notes

- Tracker: `https://tracker.zz.hgdev.com` (public) / `tracker.vpn.hgdev.com`
  (overlay). Board reconcile of a human-owned project 403s on the view
  config — admin share or agent-owned projects only (details in
  hgdev-infra `docs/vikunja-mcp-usage.md`).
- Scoped tokens REQUIRE permission groups `other:user` and
  `projects:views_buckets` (401 on all tools otherwise); minting lives in
  hgdev-infra `roles/vikunja/files/vikunja-bootstrap.py`.
