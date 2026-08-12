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
  PRESENCE, so a Review column holding 25 tasks all assigned to the agent — written up at
  the time as done work awaiting a human's Done — read as "work!" forever — ~144 no-op agent
  boots/day ≈ $105/day — while the gates themselves offered nothing. Since #991 what keeps
  FINISHED own work quiet is worklog FRESHNESS, not authorship: an own card still owed
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

## What the 2026-07-14 board carried — and why nothing here claims to know (#1002)

The incident above is the reason this command exists, so its write-up gets repeated in three
places — this file, the module header, and the docstring of
`test_dogfood_review_bucket_of_my_already_reviewed_tasks_is_not_claimable`. All three used to
finish the sentence for the reader: 25 cards in Review, all the agent's, and all of them
already ruled on — spelled **ALL already carrying a verdict** in the two code files and
"all already ruled on" here. Nothing in this tree measures that last part. #991's independent
reviewer flagged it on round 2 as a non-blocking finding, that card's author checked it
against the primary source and confirmed it as reconstruction, and #1002 is the removal.

**The STRING is young; the CLAIM is as old as the command, and keeping those apart is the
lesson.** `git log -S 'ALL already carrying a verdict'` finds two commits that ADDED it (the
same two under `--regexp-ignore-case`; a nonexistent-string control returns none), one per
file: it entered at `8132e2e`, #991's round 1, in the TEST docstring, and `0373de4` — the
round-2 sweep whose stated job was fixing four descriptions of the old behaviour — copied it
into the module header and into this file. Re-run that command AFTER #1002 and it returns
THREE, because `-S` reports every commit where the occurrence count CHANGED, removals
included; a clean demonstration on a neighbour is `git log -S 'you never independently review
your own work'`, which returns `713bcdf` (added) and `0373de4` (removed).

The claim itself is older than any of that, and softer. `713bcdf` — the same day, 11:29 —
already called the 25 "done work awaiting a HUMAN's Done move", which for a normal card means
one carrying a verdict; it simply never said the word. Sixteen minutes
later `e3a45ad` rebuilt the fixture with `[worklog]`s and justified it with a live-board
statement of its own — that every Review card DID carry a worklog, "so the own-work guard was
the ONLY thing between 25 own cards and a `claimable:true`" — and the parenthetical it gave as
grounds is `advance(to='review')` hard-requires a report, i.e. DERIVED from the product, not
observed on the board. So the habit is there from day one, in both directions: the board gets
described from what the code implies. #991's rebuild then had to give the fixture verdicts —
without them it pinned the authorship guard while claiming to pin the incident — and that
shape was written back as history in words, after which a documentation sweep carried it to
two more files. ATTRIBUTION in SKILL.md's sense, with the extra sting that the sweep FIXING
stale copy is what spread it.

**Do not go re-derive it from the tracker.** Vikunja does persist what would answer it —
`review_task` posts a `[review]` comment and sets `reviewed`/`review-failed`, and comments
carry `created`, which is exactly what the freshness guard reads. What is missing is the other
half: no stage history, so WHICH 25 cards stood in Review that day is not recoverable from
today's board (the #1002 card reports 278 in Done; not re-counted here). hgdev-acp's logs for
those 24 h are the obvious external source, and the session transcripts for that day are
another; neither was consulted, because of the paragraph below.

**Nothing rests on the answer, which is why the honest form costs nothing.** Two shapes are
pinned side by side in `test_claimable_cmd`, both over `FakeAPI` and both with
`require_review_independence` at its default of false: 25 own cards WITH verdicts are held
quiet by worklog freshness (`kind='empty'`), and the same 25 WITHOUT verdicts are claimable as
`kind='review'` and run dry after 25 rounds — the second only because the test casts a verdict
each round, which is a rulebook obligation and not a property of the code (its own pin says so
in capitals). Turn the flag ON and the without-verdicts board reads `kind='empty'` again, by
authorship. So what those pins settle is the CODE's answer per shape, not which shape the live
board had: finished work is quiet today, a card still owed a review launches an agent — on
purpose, that being the #991 fix and not the regression returning. Nor are those two the only
shapes a Review card can have (an `epic` container sits there done and unreviewable, a
hand-parked card has no `[worklog]` at all), which is a second reason not to argue history
from them. And what the incident MEASURED survives every answer anyway — 144 boots a day did
zero work, because the hub's guess and the verdict the gates would hand an agent were two
different things. That is the whole point of the command, and it never needed the clause.

**Tool gotcha, paid for here:** `git log -S` matches RAW bytes, so a phrase that a file wraps
across a line break is invisible to it. `all already ruled on` — this file's own spelling of
the clause until #1002 — returned zero commits for exactly that reason, which reads
identically to "was never written"; the one-line fragment `all already` finds `0373de4`
straight away, which is how the wrap was told apart from an absence. Grep the file first to
see where the phrase breaks, then search for a fragment that fits on one line.
