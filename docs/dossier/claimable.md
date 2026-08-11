# claimable_cmd.py — контракт с hgdev-acp и stderr-трейл

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → claimable_cmd.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/claimable_cmd.py` — `vikunja-mcp claimable`: the sibling-EXPORTED
  claimable verdict (ONE JSON line `{"claimable","kind","task_id"}`, exit 0 = the check
  ran / 1 = it failed) that hgdev-acp's repo-agent loop spawns (`uvx …@stable vikunja-mcp
  claimable`) as its pre-launch idle check, instead of re-implementing next_task's gates
  hub-side. It runs the REAL `Workflow.next_task()` — zero gate drift by construction —
  which is therefore **READ-ONLY BY CONTRACT** (comment on `next_task` + a no-writes unit
  test): the hub polls it per loop tick, so a side effect there becomes a per-poll tracker
  mutation. Born from a dogfood regression: the hub used to guess from kanban BUCKET
  PRESENCE, so a Review column holding 25 tasks all assigned to the agent and all already
  ruled on (done work awaiting a human's Done) read as "work!" forever — ~144 no-op agent
  boots/day ≈ $105/day — while `next_task` rightly offered nothing. Since #991 the guard
  that keeps that board quiet is worklog FRESHNESS, not authorship: an own card still owed
  a review is claimable on purpose, and the lane empties as verdicts land. The JSON
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
