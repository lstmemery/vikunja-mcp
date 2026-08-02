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
uv run ruff check .                       # lint — wrap at 100, RED above 120 (see below)
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

**Line length is TWO numbers, and only one of them is a gate (tracker #669).** Wrap at **100** —
that is `line-length`, the formatter's target and what this repo wraps to by hand — not perfectly,
see the 76-line band below, but everywhere it matters. CI goes red at **121**: `E501` is selected
with `[tool.ruff.lint.pycodestyle] max-line-length = 120`. The gap
between them is honest slack, not an oversight; the reasoning with its counts lives in
`pyproject.toml` beside the settings and is pinned in `tests/unit/test_line_length_gate.py`. Three
things follow for anyone writing prose here, which is most tasks. **The band 101-120 is convention
with nothing behind it** — a 103-character line ships green, so keep measuring your own additions
rather than reading a green `ruff check` as "wrapped correctly". **Measure in CHARACTERS, never
bytes**: ruff does, the shell reflex (`awk '{print length($0)}'`, `wc -c`) does not, and this prose
is full of em-dashes (3 bytes) and Cyrillic (2 bytes each) — at `3db8ef9`, 1015 lines sat at or
under 100 characters while a byte counter would call them violations, and a separate 413 did the
same at 120 (separate, not a subset: a line over 100 characters cannot be in the first set). Both
figures count prose, so they move with every landing — re-measure rather than quote them. VMCP-132
(621)'s worklog records the mistake in those words — "an awk byte-count had falsely flagged one
line because of the em-dash". In python it is `len(line)`, not `len(line.encode())`. And **"red at
121" has one measured exception**: E501 does not fire on a line
whose overlong part contains no whitespace — a long URL, one unbroken token — since ruff will not
demand a break where none is possible (a 136-character comment ending in a URL passes at 120). The
pin has no such exemption and flags that shape; that disagreement is deliberate.

Before #669 there was no gate at all: `line-length` was set, `E501` was **not** selected (it is
absent from ruff's default `E4,E7,E9,F`), so `line-length` drove only the formatter — which this
repo does not run — and `ruff check .` was green on a 140-character comment. **And running the
formatter would not have saved it:** measured, `ruff format` on the pre-fix `api.py` reformats the
file and leaves the 140-character line at 140, because it re-wraps code and does not reflow comment
or string content — 36 of the 77 overlong lines. That is how the defect #669 fixed got in: a hand
re-wrap in which one line absorbed the start of the next sentence instead of breaking, invisible to
every tool, to the card that shipped it and to that card's reviewer.
**The 120 is a ratchet, not a preference** — it is the smallest round number above every line the
repo still held once #669's own defect was reflowed (the longest is now 116, in `workflow.py`),
chosen so the gate could go on that day rather than after a 76-line cosmetic diff through 18 files,
nine of them under active concurrent edit. Lowering it is the intended direction, the remaining
band is card #711, and the decision point is the `_HARD_LIMIT` assertion in that test.

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
  **STDERR is the opposite kind of channel — a breadcrumb trail, explicitly NOT a contract**
  (tracker #536). Deferring the SDK import took `logging.basicConfig(INFO)` out of this
  process, so the httpx line-per-call that a check leaving no other trace used to emit went
  with it. That costs nothing on the lanes the hub reads (it DISCARDS stderr on success and
  reads only stdout's `error` on the failure lane) and everything on the one it can't: a
  WEDGED check, SIGKILLed on the hub's own ctx bound, whose stderr is then the only thing the
  child ever said. So the trail is back by DESIGN — ONE line, one token per tracker request,
  written BEFORE the request (httpx logged AFTER the response, so a hung request showed only
  as an absence) and flushed per token, opened by `cfg/<project>` (no token at all ⇒ it hung
  before this code, in uvx/import) and terminated by `end/<n>@<elapsed>` **plus the newline** —
  an unterminated line is precisely "killed on this token":
  `[claimable] cfg/10 info views:1 :2 tasks:1 :2 :3 :4 user tasks/628 /164 /547 /536 end/12@2.4s`
  **The terseness is forced by a measurement in the CONSUMER, not a preference here:**
  hgdev-acp puts the child's stderr on a run row via `detail()` → `snippet()`, capped at
  `snippetCap = 200` BYTES and keeping the **HEAD** (`internal/hub/vikunja/vikunja.go`, read
  2026-08-02). The first shape of this feature — a verbose line per request — cost 727 B on
  the live board, i.e. the
  hub would have shown four lines and cut off exactly the tail, the only part that says where
  it hung. A trail that overflows that cap is worse than none, because it looks like a
  diagnosis. The compact form costs 94 B for 12 requests — 31 B of frame + 5.25 B/step. And
  the cap is NOT ours alone: `detail()` is stderr+stdout, and `uvx`'s own stderr is written
  FIRST (27 B measured; 32 B in the hub's own test), so it is never the part cut — budget
  against ~170 B, which leaves ~14 more steps, not the 20 an earlier draft got by spending
  uv's share. The other half of that sharing is STDOUT's, and naming it is the difference
  between a trade and a free win: since `detail()` writes stderr FIRST and the cut keeps the
  head, every byte of trail displaces one byte of stdout on the lanes where stdout IS the
  evidence — chiefly `bad verdict json`, where `detail()` is the row's only CHILD-derived
  content; measured, 84 B of trail leaves an offending stdout 115 B of the 200 instead of all
  200, and 88 B once uv's own 27 B goes in front of it. The wedge and spawn lanes — the ones
  this exists for — leave stdout EMPTY, so there it costs nothing.
  5.25 B is a MEAN over one mix (3 B for an abbreviated page, 10 B for a task
  fetch), so TASK FETCHES eat it fastest — NOT a Review-heavy board, whose extra cards repeat
  one endpoint and so cost 3 B each after the first (measured): that one grows the line slowly
  and without bound, which is the harder failure to see coming. Headroom, not a promise — measured against
  a board that never stops paging, one line reached 545 B over 123 requests. ON BY DEFAULT
  with a
  `VIKUNJA_MCP_NO_TRACE=1` opt-out, and on-by-default is settled rather than weighed: a wedge
  is not reproducible on demand, so a flag set IN ADVANCE is only ever set by someone who
  already knows — and the hub could not set it anyway, because it hands its child an
  ALLOWLISTED env that deliberately DROPS every inherited `VIKUNJA_*` name (`checkerEnv`,
  same file, same read). Off-by-default would be off in the one process that needs it; the
  opt-out is for humans and other callers. A diagnostic must also never break its own check —
  it runs inside an httpx event hook, so every stderr touch and the token derivation are
  guarded, a write failure disables the trail rather than failing the verdict closed, and
  `sys.stderr is None` (fd 2 closed at exec) is checked explicitly because `print(file=None)`
  goes to **stdout** and would splice the trail into the verdict line — no exception, so no
  guard catches it. stdout is byte-for-byte identical with the trail on and off, in both
  lanes, and the exit-code split did not move; #521 pinned that IDENTITY, never the sizes (54
  B/140 B are just what this board and this server said that day). Do not let it grow a consumer — its shape may change in any release, and a hub
  that parsed it would need the rollout dance the JSON keys need.
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
  Housekeeping is never how an agent's work disappears. **Only ONE of the two refusal channels is
  coded, and the split is deliberate — do not restate it as "every refusal".** A `--release`/`--gc`
  refusal is exit 0 + `released: false` + a machine-readable `code` beside the prose `reason` ("the
  tool RAN and is protecting your work"). The invariant is over `released: false`, NOT over the word
  "refusal" (#631): `--release` can still RAISE, and a raise is the create channel's shape by
  construction — `{"error"}` + exit 1, no code — because it goes through the same catch-all over the
  same open set (a non-git cwd, a malformed toml, a directory git cannot delete). That sentence is
  false before AND after #631; what #631 removed is the instance that mattered, not the class — a
  tree a HUMAN pinned with `git worktree lock`, codeable precisely because git's own porcelain NAMES
  it, so the guard recognises it before touching the tree. It is now `locked`, one guard covering
  all four spellings (with a reason, reasonless, on a review tree, and a locked entry whose
  directory is gone — that last only because the guard sits BEFORE the first git call with cwd
  inside the tree, where it used to raise a bare `FileNotFoundError`). `--gc` GRADES those codes
  into two lists
  (`_keep_is_expected`): `kept` = a human should
  look, `expected` = the two routine states that used to keep `kept` permanently non-empty — a
  parked Your Call card's unsaved work (hence `Workflow.parked_task_ids`, off the same board
  fetch) and a review tree's in-tree commit. Routine is a property of the guard AND the board AND
  the ROLE, and **BOTH rows turn on the role — a claim that was true of one of them for two
  rounds** (#547): `unreachable-head` is routine only in a REVIEW tree (the conjunct stays as a
  backstop even though #540 stopped build trees from reaching it), and `dirty`/`unpushed` only in
  a BUILD tree, because every word of the parked-card justification is about the build agent's
  own conflict while the `dirty` guard is role-agnostic — so a reviewer's stranded draft used to
  be laundered by a parked card it merely shared a task id with. The two rows now SHARE the role
  conjunct and differ in the other one — the build pair additionally needs the card parked — so
  they are near-mirrors rather than one rule, which is why "we checked the branch we were looking
  at" kept reading as "we checked it": the whole grid — every code × role (build, review, AND a
  role-less entry) × parked — is now written out above `_keep_is_expected` and pinned as a grid,
  and a new `CODE_*` fails that pin until it is graded deliberately. A BUILD tree that is not on
  its own `task/<id>` branch — what an interrupted `git rebase origin/main` leaves: CLEAN, yet
  DETACHED — is refused by BOTH `ensure` (loudly, so a resume agent is never handed a tree whose
  HEAD is not where it is told) and `--release` (`detached-build`, because the unpushed-commits
  guard cannot run on a tree that is off its branch), each naming `git rebase --continue`/
  `--abort` for the AGENT to choose: the tool never picks, since `--abort` discards replayed
  work. An unknown code lands in `kept`: noisy beats quiet. A `released`
  entry can still need action — #517's `branch_deleted: false` + `warning` (the tree went, the
  branch leaked), which is why the rulebook says read `kept` AND scan `released`.
  A CREATE refusal is the OTHER channel and carries no `code` at all — `{"error": …}` + exit 1,
  "the tool could NOT do the work" — measured over every one of them (half-created, detached-build,
  the review `--at` pin, an occupied path, each argument-combination refusal). That is a design
  decision, not an oversight to tidy up (#580 weighed making it uniform and rejected it). A `code`
  exists to feed a GRADER, and `_keep_is_expected` is the only grader there is; on create every
  refusal has the same answer — SKILL.md's «Не завелось — цикл НЕ роняем»: degrade to one slot,
  never stop the loop — so a create-side code would be a public value, spelled in SKILL.md and
  pinned by tests, with no consumer. Nor could "every" be made true there: the `{"error"}` line is
  rendered by a catch-all over an OPEN set (a non-repo, a malformed toml, a git timeout, an
  OSError), so a code could only ever be present-SOMETIMES — worse to parse than absent-always,
  since `payload["code"]` would then pass every test and `KeyError` in production. On create the
  EXIT CODE is the whole machine-readable verdict, and SKILL.md tells agents to branch on that
  split, so blurring it costs more than the uniformity buys.
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
is itself worth noticing. Where a figure genuinely needs precision, name the
SHA it was measured at, because **a DATE does not name a TREE** — card 688
shipped FOUR counts in one commit that were true when it measured them at
`6dd2803` and already false 80 minutes later at `bba4fed`, the commit that
carried them, because a sibling landed in between. Three were labelled with the
day and one with "on this tree", and neither label names a tree; the two shas
share a date. Date as well by all means (the release section's landings-per-day
snapshot is genuinely a fact about a day), but a count over a tree belongs to
the tree. Better still, where a reader will ACT on the figure, assert the
property instead of writing the number — that is the one form that cannot go
stale, and `tests/unit/test_mutation_sweep_contract.py` carries the worked
example.

**And the sweep that HUNTS stale figures must not be LINE-FED — but do not
"fix" that by writing a cleverer grep: which lever reaches a wrapped figure
depends on WHICH grep, and the two on this machine need OPPOSITE ones.** Test
prose here is hand-wrapped near 100 columns, and that is the repo's wrap
TARGET (`line-length`) rather than a checked limit — since #669 the enforced
ceiling is `max-line-length = 120`, so where a line actually breaks is a
convention, and a reflow can push a figure across a break without touching a
digit. Measured on `e86b2c9^`, where `test_api_kanban.py` carried a real one
at :1473-1474 ("… 5 failed / 102" ending one line, "passed for the whole
file" opening the next): read PER LINE, both greps return the SAME 15 hits
and miss it — with 665's literal space and with a `[[:space:]]\+` class alike
— so loosening the regex by itself recovers nothing. What DOES reach it
splits by implementation, and neither half transfers to the other. BSD grep
2.6.0-FreeBSD needs the FLAG *and* the class TOGETHER — and needs `-o` to
count at all: `grep -zo` with `[[:space:]]\+` yields 16 MATCHES and finds it,
against 15 for 665's literal space. Count matches, not lines: a bare `-z`
makes the file ONE record, so `-zc` answers 1 for either pattern, and
`-zo | wc -l` answers 17, because the wrapped match itself spans two lines.
`grep -zn` then numbers every match line **1**, trading the blind spot for an
inability to say WHERE. ugrep 7.5.0 is the mirror image: there `-z` is
`--decompress`, and its real null-data (`-00`) does not recover the figure
either — 15 matches, still blind. What works there is the PATTERN, and
specifically an explicit `\n` inside it:
`ugrep -n -o '[0-9]{2,}\n\s+passed'` prints `1473:102` and `1474|    passed`
on the default matcher, with `-E` or `-P` alike — while that same `-P` with
`\s+` in place of the `\n` falls back to 15 and misses it. And this BSD grep
has no `-P` at all. So the portable move
is to stop using grep as the READER. Read each file WHOLE, collapse every
whitespace run to one space, then match — and report the
DIFF against the per-line hits, never the raw list: the raw one is dominated
by what the old sweep already found. Price it, since a pattern loose enough to
cross a wrap also catches `<number> failed` in prose: with
`\d+ (?:passed|failed)`, `e86b2c9^` had THREE spanning-only hits — one
genuine, :1473, and two false, a docker port (`Bind for 0.0.0.0:3456 failed`)
and an illustrative `-> 7 failed` in the contract test named below — and from
`94bae3d` on only those two false ones remain, while the narrower
`[0-9]\{2,\} passed` finds no wrapped hit at all today. That WRAPPED count is
the durable half and the total is not: over `94bae3d` → `aadde71` →
`7718e6c` the total ran 245 → 319 → 330 while wrapped stayed 2 — and since
the sweep counts the file it is written in, the pin below moved that total
itself. A small footprint is the argument for fixing the METHOD rather than
the sites: what a sweep is FOR is its NEGATIVE answer, and 665's sweep
reported `test_api_kanban.py` clean at the exact site 668 was later filed
against. Coda, because it cuts the other way — 668's implementer and its
reviewer both re-measured that figure RIGHT, so what the sweep could not see
there was a missing ATTRIBUTION, not a stale total.

**A mutation sweep opens with an UNMUTATED CONTROL round on the SAME selection,
and every round count is a DELTA against it.** Sweeps here are hand-run — edit
the source, `pytest`, read the summary line, restore — and that summary line is
where the arithmetic goes wrong: `N failed` is a kill count only if the same
selection failed ZERO times before a single mutation was applied, and nothing in
a `-q` summary says whether it did. Not hypothetical: card 594 swept in a tree
where 30 tests failed constantly for an unrelated reason, so every row of a
six-row table came out inflated by exactly 30 and its headline was wrong by a
factor of 16 (true kill count 2). Constant failures survive a before/after
comparison intact and read as signal; a control round is the cheapest thing that
tells them apart. So run it FIRST and WRITE ITS FAILED COUNT beside the round's:
`control 0 failed; mutation 2 failed` still means something a month later,
whereas `control PASS` is a sentence that can be true and useless at the same
time. Record the FAILED count, never the pass total — the total moves with every
test the repo adds (the floor above), the failed count does not.
`tests/unit/test_mutation_sweep_contract.py` enforces that shape on every record
written from here on, and names the pre-existing ones it cannot fix. **"Beside"
is enforced IN THE SAME PARAGRAPH** (card 688): the scanner's unit is the
paragraph, not the whole docstring, so a control declared once at the top of a
long section stops vouching for the rounds below the next blank line — repeat it
there, or leave no blank line between the header and its rounds. It used to read
whole records, and then one clause about an unrelated mutation immunised every
other count in the docstring — which is not a hypothetical either: that is how
the record card 668 was filed against passed.

**And inflation is the friendlier half.** That stand was rebuilt on 2026-08-02:
the same pre-622 sha exported twice, once with `.git` and once without, one
mutation (drop `.playwright-mcp/` from `.gitignore`), one selection
(`tests/unit/test_repo_browser_isolation.py`). The healthy tree read `control 0
failed` → `mutation 1 failed`; the corrupt one read `30 failed` BOTH times,
because the very test that mutation kills was already one of the 30. Read as an
absolute, that round overstates the kill 30×; read as a delta, it calls the
mutation UNCAUGHT. The same round lies in both directions at once, and the
control is what tells you so before you write either number down.

**A clean control does not mean the round MEASURED anything.** It is the cheapest
detector, not a complete one, and three forms met in one day bound it. CAUGHT: a
constant background failure (594/622 above), and stale bytecode — though that one
is narrower than card 624's summary of it, and its remedy weaker. Re-measured
2026-08-02 by reading the `.pyc` header: cache validity is the pair (source mtime
in SECONDS, source size), so a same-length rewrite replays the PREVIOUS budget
only when the mtime ALSO fails to advance a whole second — a scripted sweep's
hazard, not a hand edit's. And `PYTHONDONTWRITEBYTECODE=1` stops Python WRITING
bytecode, not READING it: with a stale `.pyc` already on disk, the same round
replayed the old value under that variable, and only deleting `__pycache__` moved
it. So do both — delete the caches, then set the variable so new ones do not
appear. NOT caught: a mutation that never reached the
interpreter — a tree copied with `cp -R` drags `.venv` along, which puts the
ORIGINAL `src` earlier on `sys.path`, after which control and rounds are all
green and four false greens in a row read as "nothing kills this mutation" (card
646). Copy a tree with `git archive` or `rsync -a --exclude .venv`, and print
`vikunja_mcp.__file__` in every round — that, and not the control, is what
catches this one.

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
sha (`gh run list --commit "$(git rev-parse HEAD)"` — the FULL 40-char sha; an
abbreviated one returns `[]` and exit 0, which reads exactly like "no run" and
raises a false marker alarm) — "no run" and "green run" look identical from git.

**A run that EXISTS is not a run that PASSED, and that gap silently cost seven
landings in one night** (tracker #614). Measured 2026-07-31 on this repo: 7 of 15
consecutive runs on `main` were red, every one of them `lint-and-unit` success +
`integration` failure + `release` **skipped**, so `stable` never moved — while
every agent had truthfully reported "a run exists". Seven is a FLOOR: that window
ended on its own last red, and the same night held at least one more (`d6195e1`).
The count is also read-at-the-time — `gh run list` reports a run's CURRENT verdict
and `gh run rerun` rewrites it in place, so `8b4bfa5`, one of the seven, reads
`success` today. The two checks are two because their DEADLINES differ: existence
asks about a fact that does not ripen — the run is created or it never will be —
while the outcome does. (How fast GitHub *creates* the run was NOT measured here,
so the rulebook says to ask a second time before raising the marker alarm on a
push that is seconds old, rather than assert a number it does not have.) Measured
over 40 runs timed on their FIRST attempt (two were later re-run by hand, and a
re-run's `updatedAt` carries the HUMAN's delay — 31 min and 3 h 26 min — not CI's;
the runner queue itself was 0 s on 35 of 38 and never above 80 s), a run concludes
42–120 s after it appears, median 60 s. So the outcome is read ONCE and LAST —
after `advance(to='review')` and `workspace --release`, which cost about that long
anyway — and never by waiting: `gh run view <id> --json status,conclusion,jobs`,
branching on `status` FIRST, because `conclusion` is meaningful only at
`status == "completed"` — an in-flight run renders it as the EMPTY STRING (caught
live: `{"conclusion":"","status":"in_progress"}`), which is not `null`, so a jq
`// "unknown"` fallback does not fire either. A still-running run is therefore
reported as UNKNOWN, never as green, and the card's independent reviewer is the
backstop — late by construction. The bias helps but does not SEPARATE: red runs
are 42–55 s (median 46) against 53–120 s for green (median 65), so the bands
overlap at 53–55 s. And the reason is not that `integration` fails early — per-job
timing says it is never the critical path (16–29 s against `lint-and-unit`'s
38–46 s); a run's length is set by `lint-and-unit`, and a GREEN run additionally
runs `release` (8–15 s), which a red one skips. Urgency is bounded but not zero: a later green
landing moves `stable` with the red commit already included (verified — red
`8fc53f8` is an ancestor of today's `stable`; that night the catch-up took
1–48 min), so what actually costs is the LAST landing of a session, which nothing
later heals and nobody can identify in advance.

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

**Committed `.claude/settings.json` sets `PLAYWRIGHT_MCP_ISOLATED=true`** (tracker #558)
— do not delete it as stray local state; `.gitignore` deliberately re-includes that one
file (`.claude/*` + `!.claude/settings.json`). `@playwright/mcp` derives its on-disk
browser profile as `mcp-<channel>-<sha256(first MCP root)[:7]>`, i.e. PER WORKSPACE ROOT,
so different repos never collide — but two `claude` sessions on the SAME repo (a human's
plus the hgdev-acp repo-agent, the normal case here) resolve to one profile, and the
second browser refuses to start at all: `Browser is already in use … use --isolated`,
after ~7 s of lock polling. The env var is the documented equivalent of `--isolated`
(in-memory profile), and project-scope settings DO reach a spawned MCP server's
environment — both measured. It is deliberately NOT a `.mcp.json` entry: a project
`.mcp.json` does not shadow a plugin-provided server (`claude mcp list` then shows BOTH
`plugin:playwright:playwright` and the new one), so that route adds a second browser
instead of fixing the first. Cost: the profile lives in memory, so browser logins do not
persist between sessions.

**`PLAYWRIGHT_MCP_STORAGE_STATE` does NOT buy that cost back, and is deliberately set
NOWHERE here** (tracker #585, measured on the 0.0.78 this machine actually runs). Upstream
documents it as `--isolated`'s complement and it is one — but only for LOADING. Measured:
with isolation the file IS read (cookie + localStorage restored, confirmed server-side by
the `Cookie:` header the browser then sent) and zero profiles hit disk, so the two really
do compose; WITHOUT isolation the same variable is silently ignored. It is never WRITTEN:
after a login, `browser_close` and a clean client shutdown the file stayed byte-identical
(md5, size, mtime_ns) — SIGTERM too — and session 2 read back the seed, not the login. So
it converts "log in every session" into "hand-maintain a seed file", not into persistent
logins. Two further measurements make a committed value actively harmful: a path whose
file does not exist yet makes EVERY `browser_*` call fail (`Error reading storage state …
ENOENT`), which is worse than the status quo for anyone who clones; and the value is a
machine-local path to LIVE SESSION COOKIES — a secret of the same class as
`.vikunja-mcp.env` and `VIKUNJA_NOTIFY_WEBHOOK`, which this repo keeps out of committed
files on principle. The only writer is the `browser_storage_state` tool (hidden behind
`PLAYWRIGHT_MCP_CAPS=storage`, which also exposes 16 other cookie/storage tools). BY
DEFAULT it refuses paths outside the MCP client's roots — the server's cwd, i.e. this
checkout, plus its `.playwright-mcp/` output dir — but that confinement is a default, not a
law: with `PLAYWRIGHT_MCP_ALLOW_UNRESTRICTED_FILE_ACCESS=true` (the same flag SKILL.md
offers agents for `file://`) the identical call wrote a working seed straight outside the
repo, and it restored correctly next session. So a machine-local opt-in that never touches
this repository IS constructible; it is still not worth shipping, for the reason above
rather than for a safety reason — what it yields is a hand-maintained seed that never
updates itself, which is not what the card asked for.

**The `.gitignore` guard reduces that accident; it does not make it impossible.** Claiming
otherwise was this card's own first defect — review disproved it by constructing the leak
under a name the rule missed, and a guard oversold is worse than one honestly described.
`browser_storage_state` takes ANY filename anywhere under its root, so a name-based rule can
only ever cover a LIST: here that is the tool's default
(`.playwright-mcp/storage-state-<timestamp>.json` — the output dir, NOT the repo root, and
`.playwright-mcp/` is ignored wholesale, which also settles #607's page snapshots), all three
lower-case spellings of `storage-state`, `state*.json`, `auth.json`/`cookies.json`/
`session.json`, and `.auth/` for Playwright's documented `playwright/.auth/user.json`. It does
NOT cover `tracker-login.json`, and no pattern that would is safe to write here. Two measured
qualifications on that list, both of which read as universal until you check them: the
`state*.json` glob also hides ordinary files (`states.json`, `src/data/state-defaults.json` —
any basename starting with `state`, at any depth), and the whole list is CASE-DEPENDENT — git
folds case only where `core.ignorecase` is true, which it takes from the filesystem at clone
time, so `storageState.json` (Playwright's own `context.storageState({path})` spelling) is
covered on a macOS checkout and NOT on Linux, where CI runs. The guarantee that
does not depend on the name is a unit test: it asks git what `git add -A` would publish and
fails on any file of storage-state SHAPE (`{"cookies": […], "origins": […]}`) under any name,
tracked or untracked, and at any SIZE — a candidate too big to read is reported rather than
skipped. The format holds a localStorage array PER ORIGIN, filled from every origin the context
visited, so an export has no fixed upper size and "too large to classify" is exactly what a fat
credential looks like. That part was itself a bounce: the first version capped the scan at 1 MiB
on the reasoning that "a credential export that big is not a thing", and a correctly-shaped
4,194,662-byte export then walked past it with the suite green. That is a GATE — red in the pre-push `pytest` run the
integration recipe already requires, and red in CI — not a lock on `git commit`. Nothing here
is a lock, and the candidates were built rather than argued about: a `.git/hooks` pre-commit
hook does not reach a clone at all, and committing the hooks with `core.hooksPath` pointed at
them does not either — constructed, the DIRECTORY clones and `core.hooksPath` does not (it is
local config, not content), so the clone committed unblocked. A pre-commit framework installs
into `.git/hooks` from a per-clone step and fails the same way. Every stronger option reduces
to "works on whichever machine ran an installer". All of it — coverage, the names deliberately
left uncovered, the collateral, the case split — is pinned in
`tests/unit/test_repo_browser_isolation.py`.

**The same two layers now also cover the browser's OTHER output, because `.playwright-mcp/`
covers less than its name suggests** (tracker #629). That directory holds what the browser names
ITSELF; a `filename` argument is resolved against the SERVER's cwd — the main checkout — so it
lands in the repo ROOT. Measured on the same 0.0.78:
`browser_take_screenshot(filename="x.png")` wrote `./x.png` there, unignored, and SKILL.md
prescribes exactly that call. Layer one is four extension rules (`*.png`, `*.jpg`, `*.jpeg`,
`*.pdf`), affordable because this repo tracks no image or PDF and never has — measured 2026-08-02
with `git log --all --name-only`, which asks about ANY commit touching such a path on any ref,
not just additions. That is the standing `*.html` already had. **But extensions are the wrong axis
and the honest bound is sharper than "a list can't be complete": the name does not decide the
content at all.** Measured, a screenshot asked for as `shot.bin`, or with NO extension, is still
PNG, because the format comes from the `type` argument (png|jpeg). So layer two reads the leading
MAGIC BYTES of what `git add -A` could publish and fails on PNG/JPEG/PDF under any name, needing
no size ceiling because a magic number cannot be hidden by growing the file. **It is complete
about NAMES and about the three formats it names — NOT about formats.** That distinction was the
card's own first defect: an earlier draft called those three "the entire binary surface", and the
second pass disproved it by construction. `browser_network_request` — in the DEFAULT capability
set — takes `part: "response-body"` plus a `filename` and drops the RAW body of any request the
page made into the same root, in whatever format the server sent; measured, a GIF and a ZIP landed
as `.bin`, caught by no rule and no signature. One more binary format is a single capability away,
and the CAP NAME is the anchor that survives, not a tool count: `browser_start_video` is absent
from the default set and present with `PLAYWRIGHT_MCP_CAPS=devtools`. Naming and writing are
different calls there, which is worth stating precisely because the earlier draft did not: it is
`browser_start_video` that takes the `filename`, and it answers only "Video recording started."
with the cwd still empty; the WebM (`1a45dfa3…`) appears in the root of the server's cwd when
`browser_stop_video` answers `- [Video](./vmcp629-video.webm)`.
**A tool total for "every capability on" is deliberately NOT the anchor for those**, and that is
the correction this card was bounced for: in 690d648 `.gitignore` hung that label on a tool total
of 53, and 53 is not that set. That round carried the same label in two more files, on
acceptor/writer counts rather than on a total — enumerated in
`tests/unit/test_repo_browser_isolation.py`, with why a `git log -S` commit count is not a count
of occurrences. Nor does 53 name a
set at all: measured, three different cap combinations reach 53, with 10 or 11 `filename`
acceptors depending which. Every capability on is 69. Cap names survive; tool counts belong to an
npm package pulled at `@latest`, and the full measurement is in
`tests/unit/test_repo_browser_isolation.py`. What NEITHER layer reaches, measured rather than
assumed: `tools/list` shows SEVEN tools taking a `filename` on the default capability set — the
one the shared session server runs, which its tool ROSTER says and the absence of a `--caps` flag
does not, since `PLAYWRIGHT_MCP_CAPS` and `--config` carry capabilities too — six of which write,
and `browser_snapshot`, `browser_console_messages`, `browser_network_requests` and
`browser_evaluate` drop the page's own
TEXT and its request query strings in the same root as plain text — no listable extension, no
signature, indistinguishable from a legitimate file here. A marker planted on a probe page came
back in three of those files, and a token placed in a request's query string in two. Writing
`filename` INTO the already-ignored `.playwright-mcp/` is accepted by the tools and sidesteps all
of it — but that is a change to what SKILL.md tells agents to do, so it is filed as #703 rather
than assumed here. Escaping the checkout entirely is refused by default (`File access denied …
outside allowed roots`, the roots being the server's cwd and its `.playwright-mcp/`), so the
spill is confined to exactly the directory git can see.

## Live instance notes

- Tracker: `https://tracker.zz.hgdev.com` (public) / `tracker.vpn.hgdev.com`
  (overlay). Board reconcile of a human-owned project 403s on the view
  config — admin share or agent-owned projects only (details in
  hgdev-infra `docs/vikunja-mcp-usage.md`).
- Scoped tokens REQUIRE permission groups `other:user` and
  `projects:views_buckets` (401 on all tools otherwise); minting lives in
  hgdev-infra `roles/vikunja/files/vikunja-bootstrap.py`.
