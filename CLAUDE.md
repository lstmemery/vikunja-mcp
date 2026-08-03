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
  Housekeeping is never how an agent's work disappears — **except for IGNORED files, and that
  exception is real, measured, and deliberately NOT closed (#710).** The hole is in the FIRST of
  those three guards only — the other two ask about commits — and it is that `dirty` is
  `git status --porcelain`, which does not report ignored paths at all,
  so a tree where everything is committed and pushed but `shot-<id>.png` or `.playwright-mcp/<id>/`
  sits on disk reads CLEAN and is destroyed with them. Untracked-but-NOT-ignored (`??`) the guard
  does see and does hold on, and since #766 that no longer depends on anyone's config. It used to:
  `status.showUntrackedFiles = no` at ANY config level made the SAME command emit neither `??` nor
  `!!` (measured on a real bare origin plus a real worktree — a tree holding an untracked
  `REAL-WORK.txt` plus an ignored `shot-766.png` returned the empty string, `release_workspace`
  answered `released: true` with no `code` and no `removed_ignored`, and the file was gone), so one
  performance knob switched the whole guard off. `_inspect_status` now forces
  `-c status.showUntrackedFiles=normal` on that single call. **That is a restoration, not the
  widening question below** — the guard already claimed `??`, and measured at the default setting
  the prefix changes no verdict, no entry count and no `removed_ignored`; a CLEAN tree still
  releases under the knob, so nobody who set it deliberately is paralysed. Otherwise the hole is exactly the ignored ones — and it is
  this repo's own
  rulebook that puts them there, since SKILL.md's browser recipes write both INTO the agent's
  worktree. Closing it by widening the guard to `--porcelain --ignored` was measured and rejected:
  the mandated gate (`uv run pytest`) creates `.venv` on its first invocation, so a build tree that
  ran the gates holds ignored paths from then on — sampled 2026-08-03, 3 of 3 live build trees did
  (7, 6 and 2 entries, every one of them `.venv/`/`__pycache__/`/a tool cache; the one review tree,
  which had run nothing, held 0) — and `--gc` would stop reaping ANYTHING: trees pile up, disk
  leaks, and the next human turns the guard off outright. A destroy-only-with-a-flag variant is
  rejected by argument rather than by measurement, and the argument is that it has only two
  settings: unset it reaps nothing, always-set it is today's behaviour with a longer argv. So the
  removal stands and the SILENCE is what was fixed: `released` entries now carry
  `removed_ignored: [paths]`, filtered against a small set of by-construction-regenerable names
  (`.venv/`, `__pycache__/`, tool caches, `*.pyc`, `node_modules/`) so that the ABSENCE of the key
  keeps meaning something — a field present on every entry is the never-read signal #516 had to
  split `kept` in two to cure. That filter is a list and a list rots, which is why it decides only
  what is REPORTED: out of date it costs one noisy line, never a stopped reaper. The dangerous
  direction is ADDING to it — and that direction is guarded by a PARAGRAPH, not by the suite: an
  independent pass measured that adding `.playwright-mcp` fails 2 tests while adding `dist`,
  `build`, `out`, `artifacts`, `screenshots` fails none. **Naming
  a loss is not preventing one, and the key reads in ONE direction only:** present ⇒ something
  unrecognised was destroyed; absent ⇒ NOT a proof that nothing was, because `--ignored` collapses
  an ignored DIRECTORY into one entry, so a file left inside `.venv/` dies unnamed (measured).
  Whether the guard should also HOLD is a product question left to
  a human, not guessed at in code (#764). **Only ONE of the two refusal channels is
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

**That anchor is now CHECKED — but only as a LABEL** (card 699).
`tests/unit/test_measured_figure_anchors.py` resolves every figure written as
the preposition `at` plus a backticked 7-to-40-character sha, across this repo's
`.py` and `.md` prose, and fails unless that commit both EXISTS and is an
ancestor of `HEAD`. It never re-derives the figure: a wrong number under a real
anchor still ships, and what cannot ship is a number whose tree nobody can open.
**It is PROPHYLACTIC, and that is measured rather than glossed:** all 486 commits
reachable at `8d2734c` were scanned, asking of each whether its own tree carried
an anchor failing resolve-or-ancestor at that commit, and the answer is ZERO — the
idiom first appears at `1c295cb` and has never once shipped broken. So it has
caught nothing historically; it caught its own author, twice, on the day it
landed. Do not read the neighbouring `1761` story as a catch either: that figure
was measured in an uncommitted tree and shipped with NO anchor at all, which this
gate cannot see. What it does close is the step after — the moment you DO commit
and DO anchor, since `git rebase origin/main` before pushing is mandatory here and
it orphans a sha anchored to your own un-pushed HEAD. Even there the check only
ever LOOSENS as history moves: a sha that is not an ancestor yet goes green once
it is merged, prose unchanged, so a red is a prompt to re-measure and not a latch.
It is deliberately NOT a general
stale-count detector, and that is measured rather than conceded: three wider
triggers were run over the tree and every one is red on arrival against prose
that is perfectly correct — spelled-out numbers beside a counting noun (36 hits,
almost none of them measurements), digits beside one (which matches a CARD
number), and every backticked hex token (which matches PNG/JPEG/PDF magic bytes,
and commits a design doc quotes precisely BECAUSE a rebase orphaned them). The
anchor idiom separates those by itself, with no exclusion list. Deriving the
count instead was priced, not waved off: walking all 123 commits of one window,
the count in question moved at six of them — one real landing in ten — so that
shape turns an unrelated card's docstring edit into a red suite in a hot file.
The gate needs real history, so `lint-and-unit` checks out with `fetch-depth: 0`;
on a depth-1 clone not one anchor in this repo resolves, and a second test reads
the workflow as TEXT — no git — so a shallow checkout cannot silence both.

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
detector, not a complete one, and four forms bound it. CAUGHT: a
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
catches this one. Re-measured 2026-08-02 on card 702, the `cp -R` failure is
RUNNER-dependent rather than constant, which is worse: the copied editable
`.pth` holds an ABSOLUTE path to the original `src`, so a bare
`<copy>/.venv/bin/python` imports the original and the mutation is invisible,
while `uv run` in that same copy re-syncs, rewrites the path, and the mutation
lands. HALF-CAUGHT, the fourth: a CONCURRENT WRITER — the second independent
pass mutating the same files in the same tree at the same time (card 667, rebuilt
on 702). Its foreign mutant landing under YOUR round is caught, and loudly, which
is how 667 found it; YOUR restore landing under ITS round is NOT — that one
silently reverts the mutant, the round reads green, and the auditor concludes the
pin is blind. Neither shows up in the per-script sha256 restore checks or in `git
status`, both of which stayed clean, because a per-script guard sees only its own
writes. The remedy is a separate tree, not a stronger control: SKILL.md's «ГДЕ он
работает» gives the auditor its own `git clone --no-hardlinks` plus a `git diff
HEAD`/`git apply` pair, since a clone carries only COMMITTED work and the text
under audit is usually uncommitted.

**A prose claim that quotes a string as being IN this repository is checked now,
in a small NAMED set of spellings — and the naming is the part you have to act
on.** Writing the tree from memory is how `889befd`, a commit titled for
correcting six measured claims, shipped a seventh: two example phrases asserted
to be here "each in test_api_kanban.py", one of which occurred nowhere in the
checkout. Nothing caught it — not CI, not review, and not the sweep scanner
whose own pattern is defined thirteen lines below that comment. Measured before the gate existed, whole `tests/unit` in an isolated
clone with the caches cleared: control 0 failed; a fabricated repo-content
quotation planted in a COMMENT 0 failed; the same planted in a DOCSTRING
0 failed. `tests/unit/test_repo_quotation_claims.py` closes the part a scanner
can close. It reads the SENTENCE around one of the assertive idioms its
`_CLAIM_TRIGGERS` names — read the SYMBOL, since the prose beside it only
paraphrases the list — and requires
every phrase quoted in that sentence to occur, whitespace-flattened, somewhere
in what `git ls-files` carries OUTSIDE THE FILE making the claim. That unit is
the sharp part and the obvious one is wrong: excluding only the claiming
PARAGRAPH lets the founding defect through, because at `889befd` the fabricated
phrase sat twice in one file — at line 88 in the sentence asserting it, and at
line 336 as a constructed row of a test — so the phantom vouched for itself. A
file arguing about a phrase quotes the phrase. Two consequences for whoever
writes such a sentence. **Use one of those idioms when you mean it**: the gate
is exactly as wide as the vocabulary it names, so a fabrication phrased "the
phrase X appears in Y.py" is invisible, and that spelling is outside the list
because including that spelling costs TWO false reds on this repo's real prose,
and BOTH are self-inflicted — this sentence and the one in the file saying the
same thing. **And when the quotation is NOT meant to be a repo string** —
another repository, a card description, a tool's output, a wording quoted
BECAUSE it was retracted — expect to name it in that file's ratchet with your
reason beside it; three entries are there already, one per class. The naive rule
was measured before it was rejected, not argued away: "every quoted string in
prose must be found in the tree" is 3,068 violations out of 11,596 quotations
against the 14 the shipped rule asks about (2,993 of 11,352 three card
landings earlier, at `3937b45`), and the first two of those move with every landing — so
the file asserts the RATIO and says how to re-run the digits.
This paragraph shipped that triple wrong once, which is the point of the rule
above and not a footnote to it: the digits were read off a working tree while
the code was still moving, and NO committed tree in this repository reproduces
them. Measure last, after the last change, or write an assert instead. An independent adversarial pass then built sixteen fabrications the gate
shipped green; the trigger, the scan and the delimiter set were widened to close
TEN of them at a measured cost of ZERO false reds, and the six still open are
named in the file rather than left for the next audit to find.
What the gate does NOT reach is written where it lives rather than promised here
— it checks PRESENCE, never meaning and never location; a bare pointer
(`:1473`, a card ref, a sha) is not a quotation at all; and the corpus is the
working tree, so a commit message or a card description is outside it. This
paragraph's own author committed the class while writing the guard, misquoting
that commit subject by one letter, and the file records it.

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
`vX.Y.Z`, and moves `stable` onto that bump commit — the commit/tag/push
half lives in `scripts/release.sh`, in a FILE rather than a `run: |` block
precisely so it can be RUN on a stand instead of reasoned about. The job holds
`permissions: contents: write` (least-privilege, that job only) and a `release`
concurrency group, which serializes the release JOBS **and nothing else**: it
does not move a queued job onto a newer base, so both jobs of two close landings
still compute the same next patch (see the tip guard below).

**The channel moves FORWARD ONLY, and that is a property of the script now, not of
ci.yml** (tracker #737). Until then the last action was an unconditional `git push
-f … stable`, and the window between a job DECIDING to release and that push was
closed by the concurrency group alone: a stand that runs a sibling's whole release
inside the window rolled `stable` BACK onto the earlier bump — job GREEN, both tags
present, channel a patch behind its own newest tag — identically on the guarded
path, the ordinary path and the pre-guard inline block, i.e. the property was
PRE-EXISTING rather than introduced by #716. The fix is the missing `-f`: a plain
push is fast-forward-only, so git itself refuses to point `stable` at a commit that
does not contain the channel's current head, and the refusal is then GRADED by the
same two questions the `main` push already asks — channel equals my bump (it landed,
the client lied) or channel CONTAINS my bump (a newer release already carried me):
green with a notice; **anything else, including "the channel could not be read", is
RED**. Both green branches need a proof that the channel carries my code, so
"channel not moved AND my code not in it" is green on no branch OF THIS STEP. **Wider
than this step that sentence is FALSE, and an attack pass built the counterexample
three ways**: the PRE-TAG gate still returns a green skip with the channel unmoved —
that is its four documented swallowings, the most ordinary being a runner killed
between pushes followed by a `gh run rerun`. This fix removes the channel ROLLBACK, not
the whole class "green job, channel behind" (#723, #740), which it neither introduced
nor closes. A BARE `--force-with-lease` (no argument — it leases against
`refs/remotes/origin/stable`) was measured and rejected, and the FIRST of those
measurements runs the other way, so it goes first. **That ref IS created on the
runner**: read out of the `Run actions/checkout@v4` step of the very job that does the
pushing (job 91562691267 of run 30772730104 — the release of `ad41397`, this card's
first landing), where `fetch-depth: 0` makes checkout run `git -c protocol.version=2
fetch --prune --no-recurse-submodules origin +refs/heads/*:refs/remotes/origin/*
+refs/tags/*:refs/tags/*` and print `* [new branch] stable -> origin/stable`. Not one
run: the same line is in job 91515383221 of run 30754732335, which covers the SECOND
configuration, tip ≠ trigger sha — `origin/main` was `dff2def0` at fetch time while the
job's TRIGGER sha was `0664256f` (that job released nothing at all: it died on `fatal:
tag 'v0.2.171' already exists` before its first push — it is #716's own case), so
checkout runs a SECOND, targeted fetch, `git … fetch --no-tags --prune
--no-recurse-submodules origin +0664256f…:refs/remotes/origin/main`, which force-updates
`origin/main` ALONE and leaves the `origin/stable` the first fetch created — `--prune`
deleted no ref in that job (zero `[deleted]` lines). So on the runner the lease would
have a value and would have refused this very race, and the sentence that used to stand
here — "whether checkout creates that ref was NOT measured" — is false as of those runs.
Honest bound on the second witness: it adds a second CONFIGURATION, not a second action
VERSION — both runs pulled the same `actions/checkout@v4`,
`SHA:11d5960a326750d5838078e36cf38b85af677262`, on the same day, and versions are exactly
what the next sentence is about. It is still rejected, for two reasons the fact does not
touch. First, the refspec is the ACTION's, not ours, and `ci.yml` subscribes to the
FLOATING `@v4`: it can change with any move of that tag, and the one way to pin it —
nailing the action to a sha — is not what this repo does. The cost of it changing is not
"a race slips through" but "EVERY release is red": WITHOUT the ref a bare lease rejects
even a perfectly normal push, with no race in sight (`! [rejected] … (stale info)`, rc=1
on the stand). Second, WITH the ref a plain `git fetch origin` before the push resets the
lease and lets the rollback through (measured, rc=0), because a fetch updates exactly the
value the lease compares; today no such fetch precedes the channel push in `release.sh`
(the two earlier ones both read `refs/heads/main`, and the only `refs/heads/stable` read
sits AFTER the refusal), but that is a property of the current text, not a guarantee. The
EXPLICIT form (`--force-with-lease=refs/heads/stable:<sha>`) depends on no tracking ref
and neither reason reaches it; it is rejected by an argument rather than a stand — a lease
asks "has anything changed since I looked?" where the property wanted is "does the new
value contain the current one?", and those coincide only if you looked BEFORE the sibling.
Look after it and you lease an already-advanced channel, the lease agrees, and the force
performs exactly the rollback. A plain push asks the wanted question always: the comparison
is the server's, and the stand gives the same refusal with and without the tracking ref.
What this does NOT fix, measured on the same stand: a HUMAN's documented rollback
(`git branch -f stable vX.Y.Z && git push -f origin stable`) performed inside a live
release job's window is still silently undone, because the old tag is an ANCESTOR of
the new bump, so the job's push is an honest fast-forward. The same measurement is
what shows the rollback procedure itself still works — the release after a rollback
moves the channel forward again, rc=0. The group survives as defence in depth and is
now PINNED by a test, because after this fix its removal has no reliable
symptom: the common outcomes of the races it prevents are GREEN (the supersession
skip, and this section's notice). Not "never red" — the branch where my bump is on
`main` with something newer on top is red, and dropping the group makes exactly that
more frequent too; what is gone is any DEPENDABLE signal, which is what a pin is for.
The same fix has one named cost: a channel pointed by HAND at a commit off `main` is
no longer silently overwritten, it reddens every release until a human fixes it. The bump commit is
pushed with `GITHUB_TOKEN`, which by design does NOT re-trigger CI (plus
`[skip ci]` as a second belt). So `stable` always tracks the latest green `main`,
patch-bumped, hands-off.

**The bump and its tag are ONE server transaction, and the flag is what makes that
true** (tracker #723). They used to be two pushes in a row, so a refusal on the
SECOND (network, 5xx, ref protection) left the bump on `main` with no tag — measured
on the stand with a hook refusing only `refs/tags/*`: `tags = []` under
`__version__ = "0.2.171"` at the tip, exit 1. That state does not heal. A re-run of
the same job reads its own orphaned bump as "superseded" and exits GREEN having fixed
nothing, and the next landing bumps to the patch AFTER it, so the skipped version
never exists and `git branch -f stable vX.Y.Z` can never name it again. Both halves
are now one `git push --atomic origin HEAD:refs/heads/main refs/tags/vX.Y.Z:…`, and
the same input leaves the remote untouched. **"One command" and `--atomic` are not
the same guard, and which one does the work depends on the SHAPE of the server's
refusal** — measured on both hook kinds. A `pre-receive` is one hook per PUSH, so
exiting non-zero refuses the whole batch with or without the flag, and bundling alone
covers that input. An `update` hook is per REF — the shape a host's ref-protection
takes — and a non-atomic batch then takes what it can, in BOTH directions. Refuse the
TAG: `HEAD -> main` lands beside `! [remote rejected] v1 -> v1 (hook declined)`, i.e.
this card's own "bump without tag" is reachable from a SINGLE command too, and only
the flag stops it (same input, with it: `! [remote rejected] HEAD -> main (atomic push
failure)`, `main` untouched). So "bump without tag needs two separate pushes" would be
FALSE — it holds only for a whole-push refusal. **That input also came within one
measurement of being SILENT, and closing it added the second layer**: run whole, the
client reported failure, the recheck asked its first question, saw its own bump on
`main`, printed `finishing the release`, moved the channel and exited **0** — bump and
channel present, tag absent, job green, where even the pre-#723 shape went red under
`set -eu`. So the recheck's one GREEN branch now also asks the remote where the tag is,
instead of trusting that `--atomic` was honoured; the same input is red today
(`the push was accepted NON-atomically`). Two layers, and they are not the same thing:
`--atomic` stops the state from EXISTING, the tag check stops it from passing QUIETLY.
An attack pass built the case that needs the second — a server that ADVERTISES atomic,
takes the branch, drops the tag and lies — and before the check it went green with a
tagless release, a hole the #723 fix would itself have opened by removing the separate
tag push that used to re-establish it. Refuse `main` instead (non-ff while the
tag name is free) and the non-atomic push
creates the tag and rejects `main`. That orphan tag is strictly worse than the hole
it replaces: the version at `main`'s tip never advanced, so every later job computes
the SAME version and dies on `fatal: tag … already exists` — two consecutive
landings run, both rc 128 (the pin keeps one), and the mechanism says every later one
is the same — and the job that
created it is GREEN, because the recheck honestly sees a supersession. **Atomicity is
a SERVER capability, not a client flag**, and that dependency fails safe rather than
silently: with `receive.advertiseAtomic=false` git refuses to push anything at all
(`fatal: the receiving end does not support --atomic push`, rc 128, remote clean),
which lands in the recheck's red branch — there is no quiet downgrade to a
non-atomic push. GitHub advertises it, read two ways: the capability line at
`https://github.com/<repo>.git/info/refs?service=git-receive-pack` (the same HTTPS
`actions/checkout` pushes over) and a live `git push --atomic --dry-run` that cleared
the client-side capability check. That second read went over SSH, so together they say
"advertised on HTTPS" and "the client accepts it" — NOT "a full atomic push over HTTPS
was exercised", which no stand for this repo can do. **`stable`
is deliberately NOT in that bundle.** A third refspec is syntactically fine (same
remote, no force anywhere since #737), but with the channel pointed by HAND off
`main` — #737's named cost — a three-ref atomic push refuses EVERYTHING: `main` stays
put and no tag is cut, so one channel anomaly would freeze versions entirely and on
every following release. The residual half-state it buys instead — bump and tag
landed, channel not moved — heals only in ONE of its two forms, and the two are worth
keeping apart. When the channel is merely BEHIND (absent, or an ancestor of my bump)
the next landing catches it up: re-measured on both forms, the next job exits 0, `git
merge-base --is-ancestor <my bump> stable` returns 0 and BOTH tags are on the remote,
so no version is skipped. When the channel was pointed by HAND off `main`, nothing
heals it — ff-only refuses the next release too, and every one after that, until a
human fixes it; that is #737's named cost, not a new one. Either way the refusal is
loud and carries the fix command.

**Atomicity also removed an ACCIDENTAL ESCAPE, and that trade is deliberate rather than
overlooked.** If a version tag name is squatted on the remote by a foreign tag that appears
AFTER the job's checkout — so the job's own clone does not have it and `git tag -a` succeeds
locally — the separate pushes used to land the bump anyway, which advanced the version at
`main`'s tip past the squatter, so the next landing computed the NEXT patch and the wedge
healed after one red job. Measured on the stand, pre-#723 code: first job rc 1, then three
consecutive landings all rc 0, version reaching 0.2.174, tags v0.2.171..174, channel moved.
With the atomic push NOTHING lands, so the tip's version never advances, every later job
computes the SAME taken version and dies at `git tag -a`: the same three landings give rc
128, 128, 128, the version stays at 0.2.170 and the channel never moves again. The invariant
holds — every one of those runs is RED, there is no silent green here — but "one red, then it
heals itself" became "the channel stands until a human deletes the foreign tag". The trade
was taken knowingly: the half-state it replaces was QUIET and unrecoverable (the skipped
version never exists), while this is loud and one human command away (#769).

What this does not
touch: the pre-tag gate's four swallows (#740), and the local `git branch -f stable
HEAD`, which is not a push at all — no remote sees it, and it can only fail locally
(the stand got it two ways: an unwritable ref, and `stable` checked out in some
worktree, which git refuses before writing anything — rc 128, loud under `set -eu`).

**The release belongs to the TIP of `main`; a superseded landing skips, green**
(tracker #716). `actions/checkout@v4` holds each job at its OWN trigger sha, so
two landings close enough together leave BOTH checkouts on the same version base
and both compute the same next patch. Measured on 2026-08-02, all times UTC that
day: run 30754732335 on `0664256f`
died on `fatal: tag 'v0.2.171' already exists`, and the tag's actual owner was
the run for `75a1e520` (`git rev-list -n1 v0.2.171` → `dff2def0`, whose parent is
`75a1e520`) — a run created 3 m 13 s LATER, 15:39:41Z against 15:36:28Z, which
released FIRST. Release order follows when each run's `needs` finish, not when
the run was created: the loser's `integration` job sat unstarted for five minutes
(started 15:41:31Z). And the concurrency group had nothing to serialize here —
the two `release` jobs never overlapped at all, the winner's running
15:40:34–15:40:44Z and the loser's 15:41:58–15:42:04Z, which is why the loser saw
the tag already on the remote at checkout. So `scripts/release.sh` asks, before
`git tag` and again after that rejected push (`git push --atomic origin
HEAD:refs/heads/main refs/tags/vX.Y.Z:…` since #723): is `main`'s tip
a DIFFERENT commit that CONTAINS `$GITHUB_SHA`? If yes, a newer landing is already
on top, so the job prints a notice and exits 0 — and the notice says only that,
never who will release the tip. Round 1's notice promised "releasing it is that
newer tip's job", which is false in THREE of the four swallows below: in two of them
the tip is a bump commit, and bump commits get no runs at all — by construction
(`GITHUB_TOKEN` does not re-trigger CI, plus the ci-skip marker) and re-measured on 60
consecutive bump shas, every one of which returns `[]` from `gh run list --commit
<full sha>`; in the third the tip has a run and that run releases nothing.

**After a rejected push that question is asked SECOND, and the order is
load-bearing** — the same order this file already prescribes to agents above
("First *did it land anyway?*"). Round 1 of #716 asked only the second question,
and lost whole releases to exactly the failure that rule exists for: a server can
take the ref update and still leave the client reporting failure, and then the tip
that "supersedes" the job is its OWN landed bump — a different commit that contains
`$GITHUB_SHA`, a perfect match for the condition. The job went GREEN having cut no
tag and moved no `stable`, with no second actor, no human and no re-run involved.
Constructed on the stand with a shim that performs the push and then reports the
hangup: round 1 gave `rc=0 tags=[] stable=none`, round 2 gives `rc=0 tag=v0.2.171
stable=<the bump>`. So the recheck now asks in three steps. My HEAD IS the tip →
the push landed, and since #723 the tag landed WITH it, so all that is left is
`stable`. My HEAD is ON `main` but something NEWER sits on top → LOUD, exit 1 —
and the REASON has been rewritten twice, each time because it went false. #737
killed the first ("a force-push would roll the channel back": there is no `-f` on
the channel any more), #723 killed the second ("the tag never reached the remote":
under `--atomic` it did, and the log line that said `tag … NOT pushed` was lying).
What survives is the repo's standing rule — sound beats silence: the channel is
unmoved, so the release is not fully assembled. Finishing it from there (while the channel has not passed my bump, fast-forwarding
`stable` onto it is a legal FORWARD move even with a newer tip on `main`; once it has,
the channel's own gradation says so) is deliberately NOT done: it would turn red into green on a state where
the script cannot know who will release the tip, which is the very prediction the
skip notice refuses to make. Only then, my HEAD is not on
`main` at all → the supersession question, whose "no" is exit 1.

Everything else proceeds exactly as it did before the guard — with qualifications
that sentence must not be read to cover, all checked rather than assumed. A `main`
force-pushed BACKWARDS is not superseded at all: the rollback tip is an ANCESTOR of
`$GITHUB_SHA`, so the bump is a fast-forward and the job pushes straight over the
rollback — measured on the stand, byte-for-byte the same outcome with the guard and
without it, so this is pre-existing behaviour rather than anything this card
introduced or fixed. And the guard's own cost is FOUR swallows, a number RECOUNTED
TWICE rather than inherited: round 1 said two, round 2 fixed one (the landed push
above) and its second pass built two more, and the rework's own second pass then
built a fourth — which also DISPROVED the sentence (2) used to close on. All four
are constructed, and they do not sit at the same gate.

**(1) The post-push recheck** still never asks WHY git refused, so a non-race
refusal (permissions, branch protection) that COINCIDES with a sibling landing exits
green where it used to be red. The mitigation is real but weaker than round 1's "the
TIP has no sibling by definition": measured with a standing push denial and a
landing inside every job's window, a series of five gave FOUR green swallows and ONE
red — the last. So the surviving signal is one red per SERIES, not one per job.
**(2) The pre-tag gate, as a CLASS**: any half-assembled state plus a RE-RUN is a
green skip. A job that left its own bump as the tip — killed between the atomic push
and the channel push, or between that and the local `git branch -f`, which can fail
too — re-runs, reads the tip as "superseded" by its own orphan,
and goes green. THAT job's first run is loud and only a hand `gh run rerun` silences
it — but the qualifier rests on the tip being the job's OWN bump, and must not be
read as "a half-state is always loud first": a DIFFERENT landing under the same
half-state is swallowed on its first run, which is (4). Round
1 described the class narrower than it is ("without ever cutting the tag"), and #723
narrowed the class itself rather than the description: with bump and tag indivisible,
the ONLY shape this script can leave is "bump AND tag on the remote, `stable` not
moved" — "bump without tag" is no longer constructible from it, as long as the server
honours `atomic`.
The landed question does not rescue a re-run, and should not — a re-run commits its
OWN bump (fresh committer date, different sha), so "did MY push land?" is honestly
no. Before the guard the re-run was red (`fatal: tag … already exists`), which fixed
nothing but was visible. Class tracked as #723.
**(3) The pre-tag gate again**: the tip that supersedes me will not release EITHER —
its run is red, its ci-skip marker was swallowed, or its job was cancelled. The skip
is then literally true and still leaves `stable` where it was. This is the stand's
CASE C, the "tag name still free" row of the card's own table: the same input was
`! [rejected] … (non-fast-forward)`, exit 1, before the guard. The script cannot
check it from here, which is exactly why the notice promises nothing — and it is not
rare: seven of fifteen consecutive `main` runs were red on the night of 31.07.
**(4) The pre-tag gate a third time, and it is neither (2) nor (3)**: the tip is
ANOTHER job's orphaned bump, and what gets swallowed is a DIFFERENT, EARLIER landing
on its FIRST and only run — no re-run, no second actor inside its own path. Unlike
(2), the hangup happened in someone else's run, so "the first run is loud" never
applies; unlike (3), the tip is a BUMP commit, which gets no run at all (re-measured:
60 of 60 consecutive bump shas return `[]`), so "its run is red" is not even a
question that can be asked — the job that owed that tip a release already ran and
died. One hangup swallows as many landings as it buried: two, measured on three
commits. And this one the guard INTRODUCED rather than inherited — on identical
input the pre-716 inline gives `! [rejected] … (non-fast-forward)` and exit 1, round
1 gives rc=0 and round 2 still gives rc=0. Pinned by
`test_a_foreign_orphan_bump_swallows_an_earlier_landing`, tracked as #740. #723
rebuilt that pin's CONSTRUCTION without touching the property: the orphan used to be
made by refusing the TAG push, which no longer leaves a bump behind at all, so it is
now made by refusing the CHANNEL push. Fewer routes reach a foreign orphaned bump;
the class is not closed — a refused (or unreadable) channel, a failed local `git branch
-f`, and a runner killed between the two pushes all still get there.

What that does NOT change is what reaches consumers ON THE SUPERSEDED PATH — and
the scope of that sentence matters, because on the landed-but-reported-failure path
above the fix deliberately pushes MORE than the pre-fix step did. Measured on a
bare-repo stand, the pre-fix superseded job dies at `git tag` when the sibling
already took the name and at `! [rejected] HEAD -> main (non-fast-forward)` when it
did not — and `git tag` sits BEFORE every push, so in both cases the remote's
`main`, tags and `stable` are unchanged before and after; there, and only there,
just the job's CONCLUSION moves, from a false red to a green no-op. Two readings
follow. N rapid landings can share ONE patch bump (already true before the fix),
and a green `release` job therefore no longer implies a new tag exists — the log
line `release skipped: …` is what tells the two apart. And what the guard
guarantees for the LAST landing of a session is that it is never SUPERSEDED, NOT
that it releases: nothing lands after it and an earlier job's bump can only be
pushed onto ITS OWN sha, so it is still the tip when its job runs — but that job
can fail to start at all (a swallowed ci-skip marker), go red, or be killed
mid-way, and each of those leaves the last landing unreleased with nothing later to
heal it. "Always DOES release" was the overclaim; "is never superseded" is the
measured part.

Two directions that look equivalent here and are not, both refuted by measurement
rather than argument. Recomputing the VERSION — from `origin/main`, from a retry,
or by treating a taken tag as "recompute" — fixes only the NAME: the bump still
sits on top of a non-tip, so the job dies at the main push instead, which the
stand reproduces as that same `non-fast-forward` rejection. And
`git describe --tags --abbrev=0` does not even fix the name: from `0664256f` the
nearest REACHABLE tag is `v0.2.170`, so it computes the same, taken `0.2.171`.
The stand is `tests/unit/test_release_script.py` — a real bare repo, real clones,
real pushes, and a `pre-receive` hook to land a sibling mid-push — because the
failure it guards against is a race between two jobs, and neither a fake nor a
reading of the diff can produce one.

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
skipped. **"Tracked" became true of the LOCAL run only at #630**, and the gap is worth knowing
because nothing about it was visible: the candidate LIST always came from git, but the BYTES
came from the worktree, so a shaped file that was staged and then deleted from disk, or whose
worktree copy was overwritten with `{}`, or that was committed and then removed locally without
committing the removal, read as clean — three states, all built, all `1 passed`, while
`git cat-file -p :<path>` still handed out the cookie. CI never saw any of it (a fresh checkout
has no divergence to have), which is why this was a hardening and not a leak. The scan now reads
tracked candidates out of the INDEX, which is what `git add -A` would publish. The same change
reads BYTES rather than utf-8 text, closing three encodings that used to be skipped in silence —
`UnicodeDecodeError` is a `ValueError`, so a BOM'd or UTF-16 export fell into the same `continue`
as "not JSON at all". Not an encoding cure-all: a genuinely invalid byte is still refused, and
correctly, since it is not JSON in any encoding. The format holds a localStorage array PER ORIGIN, filled from every origin the context
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
back in three of those files, and a token placed in a request's query string in two — that second
count is CONDITIONAL, re-measured on #703 rather than carried over: the tokened URL is in the
network log always and in the CONSOLE log only when the request errors, which is what #629's probe
happened to do. Escaping the
checkout entirely is refused by default (`File access denied … outside allowed roots`, the roots
being the server's cwd and its `.playwright-mcp/`), so the spill is confined to exactly the
directory git can see — but NOT to its root: measured on #703, a `filename` carrying a
subdirectory (`src/vikunja_mcp/…md`) is accepted and lands beside the sources, so a root-anchored
rule would have missed it too.

**#703 closed that residual at the WRITE SITE, the only one of the three axes compared (extension,
leading bytes, directory) that reaches text.** SKILL.md no longer prints a bare name: it prescribes
`filename` under `.playwright-mcp/`, the one directory `.gitignore` already covers wholesale,
independently of name and format. Measured — all four text writers and the screenshot accept the
prefix and land there. Two things came with it, both of which read as details and are not. The
directory must EXIST first: a caller-chosen `filename` goes through `workspaceFile()`, which does
NOT mkdir, whereas the auto-named artifacts go through `outputFile()`, which does — measured
`ENOENT` on a snapshot, a screenshot and a nested path, all three resolved by that one function, so
SKILL.md carries the `mkdir -p`. And `--output-dir` is NOT the fix it looks like: it feeds
`outputFile()` only, so pointed OUTSIDE the repo it moved the auto-named files while the explicit
one still landed in the checkout root. Nor is there another knob to reach for, and the reason is
structural rather than a survey of env vars: the base of that resolve is the SERVER'S WORKSPACE —
`clientInfo.cwd = firstRootPath(clientRoots)`, i.e. the first root the MCP CLIENT declares, falling
back to the server's cwd only when a client declares none — so it is set by the client, not by
anything this repository can commit. (That is the same `cwd` whose hash names the browser profile
in #558's note above.) For a `claude` session both are the main checkout, which is why the
artifacts land there; "the checkout" is a property of that setup, not of the tool. What the fix
does NOT do: it is a rule for agents, not a lock, and it protects only where that directory is
ignored (here, yes; a consumer's repo is its own question). The mechanism under the rule is one
cross-file pin — `test_every_filename_skill_md_prescribes_is_excluded_by_this_repos_gitignore`
asks git whether this repo would publish each `filename` SKILL.md prints, so it goes red both if
the rulebook drifts back to a bare name and if `.gitignore` drops the directory rule. Its own
bound is pinned in its docstring: it reads PROSE, so it sees only the spellings its pattern
matches — an independent attack pass got a leaking value past the first version of it by writing
the prescription in JSON, which is what an MCP argument actually is.

**`--output-dir` was the SECOND door into that same directory, and #703 did not close it**
(tracker #736). The `filename` fix reaches the caller-named artifacts; the AUTO-named ones are
resolved by a different function, and where they go is set by the flag in SKILL.md's own recipe
for launching an agent's OWN browser, which said `--output-dir <каталог с id задачи>` — a
placeholder that reads equally as a subdirectory of the worktree and as one of the scratchpad.
Only the second reading was safe. Measured before fixing: with `--output-dir 736-out`, git
reported `?? 736-out/` and `git add -A --dry-run` STAGED both `page-<ts>.yml` — the ARIA
SNAPSHOT, i.e. the page's own text, a link's `?token=` query string included — and
`console-<ts>.log`; only the screenshot was covered, by the `*.png` rule above. The recipe now
says `--output-dir .playwright-mcp/<id>`, the same directory the `filename` rule already names,
and the pin above grew a second half that asks git the same question about each `--output-dir`
value the rulebook prints. Two measurements shape what is claimed for it. Pointing the flag
OUTSIDE the repo WORKS — no `File access denied … outside allowed roots`, because that refusal
belongs to the `filename` resolver and `--output-dir` defines a root rather than escaping one —
so "outside" stays a legitimate answer that SKILL.md names and no pin here can check, having no
path to put to git. And the directory is created by the first `browser_navigate`, not at server
start, which is why a `filename` under the same prefix then needs no manual `mkdir -p`: an
ordering fact, not a guarantee. The sweep also moved the fix: deleting `--output-dir` from the
FENCED recipe while leaving the prose mentions measured control 0 failed / mutation 0 failed,
because a prose restatement is still a value — so the flag's presence in the runnable line is
pinned next door, where `--isolated` and `--channel=chrome` already are.

**Two things that pin does NOT mean, both established by an attack pass that BUILT them rather
than argued them.** First, **the flag's absence is not a leak**: run with no `--output-dir` at
all, the DEFAULT output dir is `<cwd>/.playwright-mcp/` — the ignored one — with `git status`
empty and `git add -A --dry-run` staging nothing (the tool prints it too: `Allowed roots:
<cwd>/.playwright-mcp, <cwd>`, and #585's note recorded the same default long before). The card's
hazard is the FREE CHOICE the old placeholder invited, not the flag's absence, so that assertion
guards the naming rule and the first version of it claimed a leak it had borrowed from a round
where the flag was present and pointed somewhere bad. Second, **a pattern over prose is only as
wide as the spellings it was written from**: `--output-dir=VALUE` is accepted by the server and
spills identically, and against a `\s+`-only pattern the recipe rewritten to `--output-dir=554-out`
measured control 0 failed / mutation 0 failed — fully green. That is the SAME defect this file's
`filename` pattern was already widened for once; the pattern now takes `(?:\s+|=)`. A directory
that is merely *some* ignored directory is not the point either — eight rules here exclude
regardless of filename (`dist/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`,
`.superpowers/`, `.auth/` and this one); `.playwright-mcp/` is the one that is ALSO where the
`filename` prescription sends things, which is what keeps it one directory to reason about.

## Live instance notes

- Tracker: `https://tracker.zz.hgdev.com` (public) / `tracker.vpn.hgdev.com`
  (overlay). Board reconcile of a human-owned project 403s on the view
  config — admin share or agent-owned projects only (details in
  hgdev-infra `docs/vikunja-mcp-usage.md`).
- Scoped tokens REQUIRE permission groups `other:user` and
  `projects:views_buckets` (401 on all tools otherwise); minting lives in
  hgdev-infra `roles/vikunja/files/vikunja-bootstrap.py`.
