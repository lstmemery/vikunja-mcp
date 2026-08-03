"""Stages and gates of the agent flow. The rules are baked in here, not in prompts."""
import mimetypes
import os
import shutil
import sys
import tempfile
import time
from collections.abc import Callable
from typing import Any

import httpx

from .api import VikunjaError
from .config import DEFAULT_WIP_LIMIT
from .formatting import html_to_text
from .notify import WebhookNotifier

STAGES = ["Backlog", "Queue", "Design", "Build", "Review", "Your Call", "Done"]
ACTIVE_STAGES = ("Design", "Build")
# The only stages next_task ever inspects (Queue for free/stuck tasks, Design/Build for my
# active ones, Review for bug re-review). It never reads Done/Backlog/Your Call, so its board
# fetch passes these as view_tasks(require_titles=...) — the unboundedly-growing Done is no
# longer paged exhaustively on every next_task, which is the #43 latency fix.
NEXT_TASK_STAGES = frozenset({"Queue", *ACTIVE_STAGES, "Review"})
LABEL_BLOCKED = "blocked"
LABEL_EPIC = "epic"
LABEL_EPIC_READY = "epic-ready"        # маркер: все дети эпика в Review/Done — контейнер собран, ждёт Done человека
LABEL_BUG = "bug"
LABEL_REVIEWED = "reviewed"            # прошёл независимое агентское ревью
LABEL_REVIEW_FAILED = "review-failed"  # отбит на доработку, сейчас переделывается

# Hard sequence gate (option C, epic #94). A predecessor is "ready" — no longer blocks its
# successor — only at Review or Done. The human chose REVIEW (not Done) as the bar so a chain
# can drain autonomously: only a human moves a task to Done, so gating on Done would wedge a
# human between every step. NB: "Your Call" sorts AFTER Review in STAGES yet is NOT ready (a
# parked question), so readiness is explicit set membership, never a positional comparison.
READY_STAGES = frozenset({"Review", "Done"})
# Relation kinds that make the OTHER task a PREDECESSOR of this one. Vikunja auto-inverts:
# "P precedes S" surfaces as "follows: P" on S; "P blocking S" surfaces as "blocked: P" on S.
# The gate keys off THESE kinds only — never parenttask — so old unordered epics whose children
# carry just a parenttask link stay claimable exactly as before (the migration guard).
PREDECESSOR_RELATION_KINDS = ("follows", "blocked")

# advance: to -> (откуда, куда)
AGENT_ADVANCE = {"build": ("Design", "Build"), "review": ("Build", "Review")}

# `_require_mine`'s ownerless-card clause (#705, widened by #734): the sentence that names the
# REAL exit, keyed by stage. Per-stage and not one shared text, because ONE text is measurably
# FALSE in at least one stage — see the sweep recorded in `_require_mine`. A stage absent from
# this map keeps the bare "claim it first": that is QUEUE only, and it is absent BY DESIGN,
# because there the advice is simply correct (measured: claim on an ownerless Queue card
# succeeds and moves it to Design). Design/Build carry #705's wording byte for byte.
#
# Note where the split falls. The shared prefix stops at "claim() works only from Queue"; the
# NEXT clause — "so no call of yours can make it yours" — lives in the tails, because it is FALSE
# in Review and this card's own second pass caught it there. Measured: review_task(needs_work)
# then claim(), two of the agent's own calls, leave an ownerless Review card in Design assigned to
# me. Leaving that clause shared would have had the Review entry contradict its own first half.
_ACTIVE_OWNERLESS_EXIT = (
    ", so no call of yours can make it yours (advance, call_human, return_task and decompose all "
    "refuse it identically — don't work down the list). Only a human can move it back into the "
    "pipeline: say so in your report"
)
_OWNERLESS_EXITS: dict[str, str] = {
    "Design": _ACTIVE_OWNERLESS_EXIT,
    "Build": _ACTIVE_OWNERLESS_EXIT,
    # Backlog is the REACHABLE one: return_task parks a card here AND clears the assignee, so an
    # ownerless Backlog card is the everyday outcome of a tool an agent calls itself — not the
    # rare hand-placement the Design/Build branch guards. So the exit says "this is normal",
    # not "this is broken": there is nothing to report and nothing to fix.
    "Backlog": (
        ", so no call of yours can make it yours. That is not damage and not yours to fix: "
        "Backlog is the human's triage zone, and return_task parks a card here unassigned BY "
        "DESIGN (so do decompose on a parent and file_task) — an ownerless card in Backlog is the "
        "everyday state, not a stranding. A human triages it into Queue; whether it is claimable "
        "from THERE is the ordinary queue's business, not a promise this refusal can make. Leave "
        "it and take the next task"
    ),
    # Review is the ONE non-Queue stage an agent can move this card out of (measured), so the
    # shared "only a human can move it back" would be a LIE here — and the reviewer's own tool
    # never needs ownership in the first place. Reached by `advance` only: call_human,
    # return_task and decompose each refuse from Review with their own stage gate, first.
    "Review": (
        " — but you do not need to OWN a card to review it: review_task(task_id, "
        "verdict='approve'|'needs_work', report=…) takes no ownership. And this is the one stage "
        "where a call of yours CAN make the card yours, in two steps rather than one: needs_work "
        "sends an ownerless card to Queue, and claim() takes it from there — subject to the "
        "ordinary Queue gates, which still refuse an `epic` container, a card with an unfinished "
        "predecessor, and any claim at a full WIP limit. Review is the only non-Queue stage an "
        "agent can move this card out of, so don't report it as stuck"
    ),
    # Your Call is the ANOMALOUS one: call_human KEEPS the assignee, so a parked card is not
    # supposed to be ownerless at all. Nothing for the agent to do — but unlike Backlog it is
    # worth reporting, because the human's answer moves the card back to Design/Build, where an
    # ownerless card is exactly the #705 dead end and next_task offers it to nobody (measured).
    "Your Call": (
        ", so no call of yours can make it yours. Only a human moves a card out of Your Call, so "
        "there is nothing here for you to do — "
        "but DO report it: call_human KEEPS the assignee, so a parked card is not supposed to be "
        "ownerless, and when the human answers and moves this one back to Design/Build it will "
        "still have no owner, where next_task offers it to nobody"
    ),
    # Done is terminal and human-only in BOTH directions — the same answer #626/#649 already
    # give from return_task and decompose, which is why this text points at the same door they
    # do. Reached by `advance` only, for the same reason as Review.
    "Done": (
        ", so no call of yours can make it yours. Done is human-only in BOTH directions, so this "
        "card is not yours to move no matter "
        "who owns it. Work that a Done card revealed is NEW work rather than this card: file_task(…, "
        "related_task_id=<this task>) for a human to triage; a human can also move this card "
        "back themselves"
    ),
}

# --- вложения: временные файлы (download_attachment, #139) ---
# Скачанные вложения кладём в один выделенный temp-каталог, КАЖДОЕ скачивание — в свой
# mkdtemp-подкаталог, чтобы файл сохранял ТОЧНОЕ исходное имя (рендерер образов у агента
# ключуется на расширении .png/.jpg), и два файла с одним именем из разных задач не затирали
# друг друга. Никто не удаляет файл сразу после записи — агент читает его Read-ом секундами
# позже, — поэтому чистка это best-effort TTL-подметание на КАЖДОМ вызове: подкаталоги старше
# _ATTACHMENT_TTL сносятся (только что записанный всегда свежий, под нож не попадёт). Так течь
# ограничена ~одним TTL скачиваний БЕЗ фонового потока и БЕЗ atexit (который на долгоживущем
# stdio-сервере не срабатывает до его остановки). Размер режем ДО скачивания по метаданным.
_ATTACHMENT_ROOT = os.path.join(tempfile.gettempdir(), "vikunja-mcp-attachments")
_ATTACHMENT_TTL = 3600  # сек: подкаталоги скачиваний старше этого best-effort сносятся
_MAX_ATTACHMENT_BYTES = 25 * 1024 * 1024  # 25 МБ: щедро для скринов/доков, отсекает рантаймы
# Байтовый бюджет имени temp-файла: open() кидает OSError ("File name too long") на именах
# ~255+ байт, а сервер-контролируемое имя вложения может быть любой длины -> режем до этого.
_MAX_ATTACHMENT_NAME_BYTES = 200

# #657: what `advance` says when a required report field is unusable. The old refusals ran
# the value through `(x or "").strip()`, which COLLAPSES two different states into one — an
# argument that never arrived (None) and one that arrived blank ("") — and then named BOTH
# fields whatever was actually wrong. Two facts are recoverable here and both were thrown
# away: WHICH field is unusable, and HOW it arrived. Neither proves anything about the cause
# (an agent who simply omits the argument also produces None), but "did not arrive" is the
# reading the old text made unavailable, and it is the one that changes what an agent does
# next. The card that filed this retried the identical ~7 KB call THREE times against a
# message that only ever said "you owe a report".
# `_ARG_STATE_ABSENT` says only what this tool can SEE. An earlier spelling opened with "not
# passed at all", which is literally false for the one client that sends `worklog: null`
# EXPLICITLY: that key IS passed, and (measured) arrives indistinguishable from an omitted one.
# The state is null; the CAUSE is what _LOST_ARGUMENT_HINT refuses to guess.
_ARG_STATE_ABSENT = "arrived as null, not as a string"
_ARG_STATE_BLANK = "passed, but empty or whitespace-only"
# Measured 2026-08-02 on #657, at this repo's mcp 2.0.0 / Python 3.12.13, on ONE machine over
# stdio: Workflow.advance itself carries a 1 MiB worklog byte-exact through FakeAPI, and the
# real MCPServer over the real stdio transport delivers a 4 MiB argument byte-exact — an
# independent re-measure using a raw JSON-RPC client and the REAL Workflow got the same result
# to 8 MiB; the contents tried in THAT re-measure all cross intact (Cyrillic, NUL, CRLF, one
# 8 MiB line with no newline at all), and one that does NOT is named in the fourth limit
# below — so read this as a list, never as "no content fails". A kilobyte-sized report is
# nowhere near anything measured to fail
# below this line, and an identical retry does not address a report that arrived as null.
# FOUR limits on that, the first three of which an earlier draft of this comment overstated:
#  * These are ceilings that were TESTED on one transport, not proof that none exists above.
#  * "advance behaves like review_task" holds only for a PRESENT, non-empty argument. For a
#    MISSING one they are opposite, and that opposition is the whole point below.
#  * The threshold in the ORIGINAL report was never reproduced, so nothing here locates one —
#    and WHICH KIND of failure it is was never established either. "Known to be
#    non-deterministic (three refusals, then a success)" is what this bullet claimed first, and
#    it MISREADS the card: the success came from replacing the ~7 KB worklog with
#    `worklog="probe"`, not from repeating the identical call. Three failures at ~7 KB then a
#    success at 5 characters is what a SIZE-DEPENDENT, deterministic loss looks like. So the
#    successes bound nothing in either direction, which is the honest form of this limit.
#  * "No content fails" is the one an earlier draft actually got WRONG rather than merely
#    overstated. Constructed on this probe server, controls in the SAME run: Cyrillic, NUL and
#    CRLF cross byte-exact, and a LONE SURROGATE (a truncated astral pair) does not — the call
#    raises client-side and never arrives. Precisely: what refuses it is pydantic-core's JSON
#    serializer, not UTF-8 as such — stdlib `json.dumps` escapes the surrogate and encodes
#    fine, measured — and it is loud at SESSION scope: `call_tool` itself surfaces a bare
#    CancelledError and the real cause appears on teardown, taking the stdio session with it.
#    Loud either way, so it is NOT this card's silent symptom — but it is a content that
#    fails, which is what the sentence above had denied.
_LOST_ARGUMENT_HINT = (
    "If you DID pass a long value and still read this, it did not reach this tool, and an "
    "identical retry is NOT the fix — what dropped it was never reproduced (#657), and the "
    "filing card never retried the identical call either: its success came from a SHORT "
    "worklog. So nobody knows whether a retry is futile or merely lucky, and either way it "
    "addresses no cause. CHECK THE PARAMETER NAME FIRST — a "
    "misspelling ('wroklog') is dropped in silence and lands here identically (measured), and "
    "that one is yours to fix. Otherwise: measured (#657), this server takes a 4 MiB argument "
    "byte-exact over its own stdio transport, so a kilobyte-sized report is nowhere near any "
    "limit here, and a value you did pass that arrives as null was dropped ABOVE this server "
    "where this tool cannot see it. (Null does not by itself name a cause: a misspelled name, "
    "a key dropped in transit, an argument you never passed and an EXPLICIT null you did pass "
    "all arrive here as null — measured; the middle two are one and the same on the wire.) "
    "Workaround that is known to work: advance with a SHORT value, then post the full text as "
    "separate comment() calls marked [worklog]."
)


def _unusable_report_fields(*fields: tuple[str, str | None]) -> list[tuple[str, str]]:
    """For each (name, value) that cannot serve as a report field, return (name, state) where
    state says HOW it is unusable — see _ARG_STATE_*.

    An empty list means no field is BLANK BY THIS TEST, which is narrower than "usable" (the
    word this docstring used first). The test is `str.strip()`, i.e. zero NON-whitespace
    characters: measured, 100 NBSP are refused because `\\xa0` is whitespace, while 50 ZWSP —
    or a word joiner, or a BOM — are NOT whitespace, so they pass here and advance a card whose
    report is empty to every reader. Deliberate rather than missed: widening the test to
    "visible characters" is a guess about an open set of code points, and the states this card
    is about (null vs blank) do not depend on it."""
    return [
        (name, _ARG_STATE_ABSENT if value is None else _ARG_STATE_BLANK)
        for name, value in fields
        if not (value or "").strip()
    ]


def _sweep_old_attachments(now: float) -> None:
    """Best-effort: снести подкаталоги скачиваний старше _ATTACHMENT_TTL. Полностью
    защищено — чистка временных файлов не имеет права уронить вызов тулзы."""
    try:
        entries = os.listdir(_ATTACHMENT_ROOT)
    except OSError:
        return
    for entry in entries:
        path = os.path.join(_ATTACHMENT_ROOT, entry)
        try:
            if now - os.path.getmtime(path) > _ATTACHMENT_TTL:
                shutil.rmtree(path, ignore_errors=True)
        except OSError:
            pass


def _truncate_preserving_ext(name: str, max_bytes: int) -> str:
    """Урезать имя файла до max_bytes БАЙТ (не символов), сохранив расширение и НЕ разрубив
    многобайтовый символ пополам. splitext даёт stem+ext; если само расширение уже >= max_bytes
    — это не расширение, а длинный «хвост» с точкой, дропаем его. Иначе бюджет = max_bytes минус
    длина ext в байтах, режем utf-8 stem'а по границе байта, decode(errors='ignore') сносит
    повисший обрубок символа. Кодируем surrogatepass (имя могло прийти с суррогатами), декодим
    ignore (обрубок символа/битый суррогат просто исчезает)."""
    stem, ext = os.path.splitext(name)
    ext_bytes = ext.encode("utf-8", "surrogatepass")
    if len(ext_bytes) >= max_bytes:            # «расширение» само не влезает -> это не расширение
        ext, ext_bytes = "", b""
    budget = max_bytes - len(ext_bytes)
    stem_bytes = stem.encode("utf-8", "surrogatepass")[:budget]
    return stem_bytes.decode("utf-8", "ignore") + ext


def _safe_attachment_name(name: str, fallback: str) -> str:
    """Имя файла от сервера НЕ должно ни уводить запись за пределы temp-каталога (path traversal),
    ни уронить сам open(). Оставляем только basename (нормализовав и обратные слэши — на POSIX
    os.path.basename их не режет); вырезаем управляющие байты (ord < 0x20 или == 0x7F: NUL + C0 +
    DEL — иначе open() кидает ValueError на NUL); пустое или всё из точек ('', '.', '..') ->
    fallback (перепроверяем ПОСЛЕ вырезания: "\\x00" схлопывается в пустоту); режем до
    _MAX_ATTACHMENT_NAME_BYTES байт с сохранением расширения (иначе open() кидает OSError
    'File name too long' на ~255+ байтах). Общий для download_attachment и attach_file."""
    base = os.path.basename((name or "").replace("\\", "/").strip().rstrip("/"))
    base = "".join(ch for ch in base if ord(ch) >= 0x20 and ord(ch) != 0x7F)
    if not base or set(base) <= {"."}:
        return fallback
    return _truncate_preserving_ext(base, _MAX_ATTACHMENT_NAME_BYTES)


def _write_attachment_to_temp(name: str, data: bytes, fallback: str) -> str:
    """Записать байты вложения во СВЕЖИЙ per-download подкаталог под _ATTACHMENT_ROOT,
    сохранив исходное имя, и вернуть путь. Попутно best-effort подметает старые скачивания."""
    os.makedirs(_ATTACHMENT_ROOT, exist_ok=True)
    _sweep_old_attachments(time.time())
    dest_dir = tempfile.mkdtemp(dir=_ATTACHMENT_ROOT)
    path = os.path.join(dest_dir, _safe_attachment_name(name, fallback))
    with open(path, "wb") as fh:
        fh.write(data)
    return path


def _human_size(n: int) -> str:
    """Человекочитаемый размер для журнального коммента [attach] (#184): человек в ленте читает
    «1.4 МБ», а не 1468006. Кап вложений — 25 МБ, поэтому МБ — верхняя единица."""
    if n < 1024:
        return f"{n} Б"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} КБ"
    return f"{n / (1024 * 1024):.1f} МБ"


def _stderr_note_best_effort(prefix: str, exc: Exception) -> None:
    """One guarded line on STDERR for a swallowed best-effort failure — the #134/#135 contract
    factored out for reuse: never stdout (a stray byte corrupts the MCP stdio protocol), the
    exception CLASS is formatted unconditionally, a str(exc) that itself raises degrades to
    '<unprintable>' instead of escaping (the diagnostic survives the pathological case), and
    the print is wrapped so nothing on this logging path can ever propagate into the caller's
    (already succeeded) result."""
    try:
        detail = str(exc)
    except Exception:
        detail = "<unprintable>"
    try:
        print(f"{prefix}: {exc.__class__.__name__}: {detail}", file=sys.stderr)
    except Exception:
        pass


class WorkflowError(Exception):
    """The message is shown to the agent as the tool result."""


class Workflow:
    def __init__(
        self, api: Any, project_id: int, enforce_single_wip: bool = False,
        notifier: WebhookNotifier | None = None, wip_limit: int | None = None,
    ):
        self.api = api
        self.project_id = project_id
        # optional WIP gate: when true, claim() refuses a new task while you already
        # have an active one. Off by default -> the gate does zero extra work.
        self.enforce_single_wip = enforce_single_wip
        # optional Your-Call webhook ping (#252): built from VIKUNJA_NOTIFY_WEBHOOK by the
        # server; None (default, URL unset) -> call_human behaves bit-for-bit as before.
        # Called strictly best-effort — see call_human.
        self.notifier = notifier
        # parallel drain: how many tasks may be active (Design/Build) at once. None means the
        # repo toml set no wip_limit — NOT "no gate": the fallback is the legacy flag (1) or
        # DEFAULT_WIP_LIMIT. See _effective_wip_limit for the precedence.
        self.wip_limit = wip_limit
        self._me_cache: dict | None = None
        self._view_cache: dict | None = None
        self._buckets_cache: dict[str, dict] | None = None

    # --- кэшируемые справочники ---
    def _me(self) -> dict:
        if self._me_cache is None:
            self._me_cache = self.api.me()
        return self._me_cache

    def _view(self) -> dict:
        if self._view_cache is None:
            self._view_cache = self.api.kanban_view(self.project_id)
        return self._view_cache

    def _bucket(self, title: str) -> dict:
        if self._buckets_cache is None:
            found = self.api.buckets(self.project_id, self._view()["id"])
            self._buckets_cache = {b["title"]: b for b in found}
            missing = [s for s in STAGES if s not in self._buckets_cache]
            if missing:
                raise WorkflowError(
                    f"the project board has no columns {missing} — run `vikunja-mcp setup`"
                )
        return self._buckets_cache[title]

    # --- поиск и проверки ---
    def _board(self, require_titles: set[str] | None = None) -> list[dict]:
        # require_titles is forwarded to view_tasks: None (default) = full exhaustive board
        # (for _find_task/claim which must see every bucket incl. Done); next_task passes
        # NEXT_TASK_STAGES to skip exhaustively paging the unbounded Done (#43 latency fix).
        return self.api.view_tasks(
            self.project_id, self._view()["id"], require_titles=require_titles
        )

    def _my_active_tasks(self, board: list[dict] | None = None) -> list[tuple[str, dict]]:
        """(stage, task) for tasks in an ACTIVE stage (Design/Build) assigned to the
        caller — the 'one task at a time' set. Shared by next_task's resume branch and
        claim's optional WIP gate. Pass a pre-fetched board (the raw _board() list) to
        skip a second fetch; a stuck claim still sitting in Queue is deliberately NOT
        active (finishing it isn't starting a second task)."""
        raw = self._board() if board is None else board
        by_stage = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]
        return [
            (stage, t)
            for stage in ACTIVE_STAGES
            for t in by_stage.get(stage, [])
            if my_id in self._assignee_ids(t)
        ]

    def liveness_board(self) -> list[dict]:
        """The one board read that covers every set `workspace --gc` needs.

        Review finding (Important 4): active_task_ids/review_task_ids used to each call _board
        separately — two exhaustive-adjacent fetches per gc sweep, on a path that runs on
        EVERY orchestrator tick, more often than next_task's own #43-fixed single fetch. Pass
        this board's result into both accessors (their `board` param) so one call to
        view_tasks serves the whole sweep, same discipline as next_task's raw/resolve_full.

        "Your Call" is in the required titles for `parked_task_ids` (VMCP-68) — NOT for liveness:
        a parked card's tree is dead by design, and that is the whole point. It has to be
        EXHAUSTIVE and not just "whatever the first page happened to carry", because a parked id
        that pagination truncated away reads as not-parked, i.e. gc grades a routine refusal as
        an alarm — the exact never-empty-signal failure this set exists to fix. The cost is one
        extra page fetch only on a board whose Your Call is itself full (page_size cards a human
        has yet to answer), unlike the unbounded Done/Backlog #43 deliberately leaves out."""
        return self._board(require_titles=frozenset({*ACTIVE_STAGES, "Review", "Your Call"}))

    def active_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of tasks in an ACTIVE stage (Design/Build) assigned to me — the live BUILD set.

        Public on purpose: `vikunja-mcp workspace --gc` needs it to tell a crashed agent's
        orphaned worktree from a live one, and that boundary deserves a real interface rather
        than a CLI reaching into _my_active_tasks. Pass a pre-fetched board (`liveness_board()`)
        to share one fetch with `review_task_ids`; omit it to fetch on its own."""
        raw = self.liveness_board() if board is None else board
        return [t["id"] for _stage, t in self._my_active_tasks(raw)]

    def review_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of every task sitting in Review — the live REVIEW set.

        Deliberately NOT filtered by assignee: a reviewer works on someone ELSE's card, so
        ownership would reap the tree out from under a running review. Pass a pre-fetched
        board (`liveness_board()`) to share one fetch with `active_task_ids`; omit it to fetch
        on its own."""
        raw = self.liveness_board() if board is None else board
        return [
            t["id"] for bucket in raw if bucket["title"] == "Review"
            for t in (bucket.get("tasks") or [])
        ]

    def parked_task_ids(self, board: list[dict] | None = None) -> list[int]:
        """Ids of every task parked in Your Call. NOT a liveness set — the opposite of one.

        `workspace --gc` reads it to GRADE its own report (VMCP-68), never to spare a tree: a
        dead build tree that still holds uncommitted or unpushed work is the routine, no-action
        state while its card waits for a human (call_human parks the card and keeps the assignee,
        so the human already has the signal, and it clears when they answer), and the very same
        refusal on a card that is anywhere else is work nobody is coming back for. One refusal,
        two meanings, and only the board can tell them apart.

        Deliberately NOT filtered by assignee, like review_task_ids: a `task-<id>` worktree only
        ever exists for a task we worked on, so ownership would buy no precision and cost a
        `_me()` fetch. Pass a pre-fetched board (`liveness_board()`) to share its one fetch."""
        raw = self.liveness_board() if board is None else board
        return [
            t["id"] for bucket in raw if bucket["title"] == "Your Call"
            for t in (bucket.get("tasks") or [])
        ]

    def _wip_limit_with_origin(self) -> tuple[int, str]:
        """The slot count AND the breadcrumb saying which knob produced it, resolved by ONE
        branch structure so the two can never disagree (tracker #517).

        Precedence: an explicit wip_limit is the truth; otherwise the legacy #38 flag means
        exactly 1; otherwise DEFAULT_WIP_LIMIT. Keeping both keys alive means an existing
        consumer that committed enforce_single_wip = true needs no edit.

        There is deliberately no "unlimited" (tracker #524): an unset key used to return None
        = no gate, which contradicted the rulebook's «unset ⇒ SERIAL drain» and let a pump
        claim the whole Queue. Returning int, not int | None, is what makes that structural —
        callers cannot reintroduce an unbounded branch by forgetting a None check.

        The origin string exists because the refusal message lost its breadcrumb when #38's
        `enforce_single_wip` stopped being the only knob: an agent hitting a surprising "WIP
        limit reached" could no longer tell whether a human committed the number, whether the
        legacy flag was still on, or whether nothing was configured at all — three different
        next actions, and the third is not even a toml edit. Computed HERE rather than in a
        sibling helper on purpose: a second copy of this if/elif is a lie waiting to happen,
        and a message that names the wrong knob is worse than one that names none."""
        if self.wip_limit is not None:
            return self.wip_limit, "the `wip_limit` key in the repo's .vikunja-mcp.toml"
        if self.enforce_single_wip:
            return 1, "`enforce_single_wip = true` in the repo's .vikunja-mcp.toml"
        return DEFAULT_WIP_LIMIT, (
            "the built-in default — the repo's .vikunja-mcp.toml sets no `wip_limit`"
        )

    def _effective_wip_limit(self) -> int:
        """How many active tasks this token may hold. ALWAYS a number — the gate is never off.
        Thin view over _wip_limit_with_origin, which owns the precedence."""
        return self._wip_limit_with_origin()[0]

    def _find_task(self, task_id: int, board: list[dict] | None = None) -> tuple[dict, str]:
        for bucket in (board if board is not None else self._board()):
            for task in bucket.get("tasks") or []:
                if task["id"] == task_id:
                    return task, bucket["title"]
        raise WorkflowError(f"task {task_id} not found on the board of project {self.project_id}")

    def _unfinished_predecessors(
        self, task_id: int, board: list[dict] | None = None,
        resolve_full: Callable[[], list[dict]] | None = None,
    ) -> list[dict]:
        """Predecessors of `task_id` that are NOT yet ready (still below Review) and so must
        reach Review/Done before this task may be started. A predecessor is any task linked from
        this one by a `follows` (this follows P) or `blocked` (this blocked-by P) relation;
        parenttask is deliberately excluded, so an old epic whose children carry only a parenttask
        link yields [] and stays claimable (the migration guard). Each entry: {id, ref, title,
        stage}, deduped by id. A task with no follows/blocked relation returns [] without arming
        the gate. Pass a pre-fetched board (raw _board()) to reuse one snapshot for stages.

        resolve_full (#126): a memoised getter for the EXHAUSTIVE board, supplied by next_task,
        which resolves stages against its LIGHT board (require_titles=NEXT_TASK_STAGES — Backlog/
        Your Call/Done are not exhaustively paged, #43). On that light board a predecessor that is
        simply absent is NOT provably deleted: it may sit in an unpaged Backlog/Your Call/Done
        bucket. So before ruling "gone -> not a blocker" we consult resolve_full() — the same full
        board claim/advance read — and treat the predecessor as gone only if it is missing there
        too. resolve_full is memoised by the caller, so the full board is fetched AT MOST ONCE per
        next_task (a 1->2 view_tasks escalation, and only when a predecessor is genuinely off the
        light board — never per candidate); the common no-off-board-predecessor path never calls
        it, preserving the #43/#105 single fetch. claim/advance pass the full board and OMIT
        resolve_full, so their verdict is unchanged — this makes next_task agree with them by
        construction instead of by keeping three bucket-sets in sync by hand."""
        base = self._board() if board is None else board
        stage_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in base for t in (bucket.get("tasks") or [])
        }
        full_stage_by_id: dict[int, tuple[dict, str]] | None = None
        related = self.api.get_task(task_id).get("related_tasks") or {}
        unfinished: list[dict] = []
        seen: set[int] = set()
        for kind in PREDECESSOR_RELATION_KINDS:
            for pred in related.get(kind) or []:
                pid = pred["id"]
                if pid in seen:
                    continue
                seen.add(pid)
                found = stage_by_id.get(pid)
                if found is None and resolve_full is not None:
                    # light-board absence is NOT deletion — disambiguate against the exhaustive
                    # board (fetched at most once via the memoised resolve_full) before ruling gone
                    if full_stage_by_id is None:
                        full_stage_by_id = {
                            t["id"]: (t, bucket["title"])
                            for bucket in resolve_full() for t in (bucket.get("tasks") or [])
                        }
                    found = full_stage_by_id.get(pid)
                if found is None or found[1] in READY_STAGES:
                    continue  # genuinely gone (absent even from the full board) or already ready
                pred_task, pred_stage = found
                unfinished.append({
                    "id": pid, "ref": self._ref(pred_task),
                    "title": pred_task["title"], "stage": pred_stage,
                })
        return unfinished

    @staticmethod
    def _assignee_ids(task: dict) -> list[int]:
        return [a["id"] for a in task.get("assignees") or []]

    @staticmethod
    def _has_label(task: dict, title: str) -> bool:
        return any(lb.get("title") == title for lb in task.get("labels") or [])

    def _add_label(self, task_id: int, title: str) -> None:
        label = self.api.get_or_create_label(title)
        self.api.add_label(task_id, label["id"])

    def _remove_label(self, task: dict, title: str) -> None:
        # снимаем только реально висящую на снапшоте метку — иначе DELETE по
        # несуществующей связи вернул бы 404
        lb = next((x for x in task.get("labels") or [] if x.get("title") == title), None)
        if lb:
            self.api.remove_label(task["id"], lb["id"])

    def _clear_verdict_labels(self, task: dict) -> None:
        """Снять ОБЕ взаимоисключающие вердикт-метки (`reviewed` / `review-failed`). Задача,
        (пере)входящая в активный пайплайн — агент начинает (пере)сборку или ресабмитит в
        Review, — НЕ несёт действующего вердикта: любой прошлый инвалидируется в момент
        возобновления работы. #119: когда человек РУКАМИ вытаскивает одобренную карточку из
        Review на доработку, ни одна тулза не срабатывает, поэтому `reviewed` переживает
        возврат; снятие здесь, на следующем forward-переходе агента, не даёт несвежему APPROVE
        уехать обратно в свежий Review (ложь на доске). Оффер ревью в next_task при этом
        цепляется за свежесть коммента [worklog]/[review], а НЕ за эту метку, так что стале-
        `reviewed` не подавлял бы re-ревью — но ложный бейдж всё равно не должен оставаться.
        Идемпотентно по каждой метке — _remove_label шлёт DELETE только по реально висящей на
        снапшоте связи, поэтому на задаче без вердикт-меток (свежий клейм) это no-op.
        #673 добавил ТРЕТИЙ вызов, и он про обратное направление: `decompose` зовёт это не на
        входе в пайплайн, а на ВЫХОДЕ из него — карточка перестаёт быть работой и становится
        эпиком-контейнером, чья работа переезжает в детей. Общее у всех трёх — не стадия, а то,
        что прошлая оценка перестала описывать карточку; у эпика она вдобавок НЕПРИМЕНИМА —
        обе точки, где карточку предлагают на независимое ревью (push-нудж в advance и pull-ветка
        в next_task), эпик пропускают, так что штатный поток к этому вердикту уже не вернётся."""
        self._remove_label(task, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)

    def _require_mine(self, task: dict, stage: str | None = None) -> None:
        assignees = self._assignee_ids(task)
        if self._me()["id"] in assignees:
            return
        msg = f"task {task['id']} is not assigned to you — claim it first"
        # #705, residual half: "claim it first" is UNFOLLOWABLE for an OWNERLESS card outside
        # Queue — claim only works from Queue, so the advice names the one call that is guaranteed
        # to refuse. Both conditions carry weight: with an assignee (someone else's card) "not
        # assigned to you" is the accurate diagnosis and the right action — leave it alone — is
        # unchanged, while in Queue "claim it first" is simply correct (measured: claim from Queue
        # on such a card SUCCEEDS). Those two are the refusals that stay byte for byte what they
        # were, and both are pinned that way. `stage` is optional so a caller without one in hand
        # keeps the plain message rather than guessing.
        #
        # #734 widened it from Design/Build to EVERY stage claim refuses from — that is all six
        # non-Queue stages — but NOT with one shared text, because one text is measurably false.
        # The table below is the BEFORE picture — measured at 7121dcf, the tip this work forked
        # from, with #705 already in it — on the real Workflow over FakeAPI: ownerless card, all
        # 7 stages x the 5 ownership-gated forms (advance x2, call_human, return_task, decompose),
        # plus a control round on a card owned by SOMEBODY ELSE and a mover round over all 8
        # card-moving calls. It is what MOTIVATES the map, not what the code does now, and saying
        # so is this card's own second-pass finding: read undated under a "#734 widened it"
        # heading, 12 of its 35 cells contradict the module they sit in. AFTER the change every
        # `bare` below outside the Queue row reads `clause`; nothing else moves.
        #
        #   Backlog    bare bare STAGE-GATE bare bare      claim REFUSED  movable-by-agent: none
        #   Queue      bare bare STAGE-GATE bare bare      claim OK       claim -> Design
        #   Design     clause x5                           claim REFUSED  none
        #   Build      clause x5                           claim REFUSED  none
        #   Review     bare bare STAGE-GATE x3             claim REFUSED  review_task(needs_work)
        #                                                                  -> Queue
        #   Your Call  bare bare STAGE-GATE bare bare      claim REFUSED  none
        #   Done       bare bare STAGE-GATE x3             claim REFUSED  none
        #
        # Three things in that table decide the wording, and each kills a tempting shortcut:
        # (1) #705's own clause CANNOT be copied outward — it says "advance, call_human,
        # return_task and decompose all refuse it identically", which is true ONLY in
        # Design/Build; elsewhere call_human (and in Review/Done also return_task and decompose)
        # answers with its own stage gate instead. (2) "Only a human can move it back" is true in
        # Backlog/Design/Build/Your Call/Done and FALSE in Review, the one non-Queue stage an
        # agent moves an ownerless card out of. (3) The three stages this card is titled for are
        # not one case: Backlog is NORMAL (return_task produces it every day), Your Call is
        # ANOMALOUS (call_human keeps the assignee, so a parked card should have one) and Done is
        # TERMINAL (human-only both ways, same door #626/#649 already point at). So the exit
        # sentence is per stage — `_OWNERLESS_EXITS`, above.
        #
        # What is NOT claimed: that unfollowable advice is now impossible. The clause keys off
        # "no assignee at all", so somebody ELSE's card keeps the bare message in every stage —
        # deliberately, since "not assigned to you" is then the accurate diagnosis and "leave it
        # alone" the unchanged right action, even though `claim` would refuse from Backlog/Your
        # Call/Done just the same. And the table is today's tool set, not a law (#649/#662).
        #
        # REACHABILITY is not uniform across the six, which is why Backlog's exit reads "this is
        # normal" and Design/Build's reads "tell a human". Reaching the DESIGN/BUILD branch takes
        # a human hand-placing an unassigned card there: review_task(needs_work) used to be the
        # tool that produced it and now bounces such a card to Queue — routing on a re-read, so
        # the mid-call window is closed too — and claim's vanish-window guard refuses before its
        # own move. SWEPT rather than reasoned (#705): all 12 registered tools (14 forms) run from
        # each of the 7 stages with the card assigned and unassigned, and no landing leaves a card
        # ownerless in Design/Build. BACKLOG is the opposite: `return_task` parks a card there and
        # clears the assignee in the same call, so an agent produces that state itself, daily —
        # driven through the real tool in the test, not argued from the source. Your Call and Done
        # are hand-placements like Design/Build; Review takes the same human hand, and clearing
        # the assignee on a card under review is the route #705's own race test already exercises.
        # None of that was re-swept per stage here. All of it is a measurement of today's set, not a
        # law: a future tool that moves a card without checking assignees produces the state
        # again, and nothing here would catch it (the open-class caveat #649 records, #662).
        exit_advice = _OWNERLESS_EXITS.get(stage or "")
        if exit_advice and not assignees:
            msg += (
                f" — except that is UNFOLLOWABLE here: this card has NO assignee at all and is "
                f"already in {stage}, and claim() works only from Queue"
            ) + exit_advice
        raise WorkflowError(msg)

    @staticmethod
    def _ref(task: dict) -> str:
        """Human-searchable task reference for agents to echo: the Vikunja identifier
        (project prefix + per-project index, e.g. 'VMCP-27') plus the global id in
        parens -> 'VMCP-27 (82)'. A human searches the tracker by the identifier; the
        bare global id (#82) is not searchable. Vikunja already returns `identifier` on
        every task read (a project with no prefix yields '#<index>', which we keep);
        falls back to '#<id>' only if it's absent."""
        ident = (task.get("identifier") or "").strip()
        return f"{ident} ({task['id']})" if ident else f"#{task['id']}"

    @staticmethod
    def _summary(task: dict) -> dict:
        return {
            "id": task["id"],
            "ref": Workflow._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": (task.get("description") or "")[:500],
        }

    # --- тулзы ---
    def next_task(self, exclude: list[int] | None = None) -> dict:
        # READ-ONLY BY CONTRACT: next_task never writes (no comments, moves, labels, assigns) —
        # its whole call inventory is GETs: me / kanban_view / view_tasks (which itself probes
        # GET /info once, cached, for the page size) / get_task / comments — pinned by
        # test_claimable_cmd.test_the_check_makes_no_writes. The hgdev-acp hub polls it
        # per loop tick via `vikunja-mcp claimable` (see claimable_cmd.py) as its pre-launch idle
        # check, so a side effect added here becomes a per-poll tracker mutation on every repo in
        # the fleet. If one is ever genuinely needed, decouple the claimable verdict first.
        #
        # light board: only the stages next_task reads need be complete — don't page the
        # unbounded Done exhaustively on every call (#43). _my_active_tasks(raw) reuses this
        # same fetch (Design/Build are in NEXT_TASK_STAGES, so they're complete).
        raw = self._board(require_titles=NEXT_TASK_STAGES)
        board = {b["title"]: (b.get("tasks") or []) for b in raw}
        my_id = self._me()["id"]

        mine = self._my_active_tasks(raw)
        # parallel drain: `exclude` names the tasks the CALLER already has a live
        # agent on. The tracker cannot know sub-agent liveness — that is a fact of the harness,
        # not of the board — so the pump states it. It is consulted by the three branches that
        # can offer an ALREADY-ASSIGNED task (resume / stuck-in-Queue / review offer); the
        # free-queue branch never reads it and does not need to (see the note there: an excluded
        # id is assigned to the caller, so its assignee filter already drops it). So `exclude` is
        # NOT a queue filter — it never narrows WHICH free work is offered, and a caller learns
        # nothing about the rest of the queue from passing it. An excluded id still OCCUPIES its
        # slot, though: it is real work in progress. On a fresh tick after a killed turn the set
        # is empty and the abandoned task correctly resurfaces as resume (the crash-recovery path).
        excluded = set(exclude or [])
        limit = self._effective_wip_limit()
        wip = {
            "active": len(mine),
            "limit": limit,
            "free": max(0, limit - len(mine)),
        }

        def with_wip(result: dict) -> dict:
            result["wip"] = wip
            return result

        offerable = [st for st in mine if st[1]["id"] not in excluded]
        if offerable:
            # rework-first ordering (option C, epic #94, mechanism 3): when I hold TWO+ active
            # tasks from one chain, hand back the one that is a PREDECESSOR of another of my
            # active tasks BEFORE its successor — even when the successor outranks it by priority
            # — so I finish the unblocking rework, not the shinier successor (whose advance→review
            # is latched anyway, mechanism 2). Both tasks being active ⇒ both below Review ⇒ the
            # predecessor surfaces in _unfinished_predecessors; keys off follows/blocked only,
            # never parenttask. Computed only for 2+ active tasks — the common 0/1-active path
            # keeps a plain -priority sort and makes zero extra get_task calls. active_ids is
            # built from ALL of `mine`, not `offerable`: if I hold both a predecessor (excluded —
            # another agent is live on it) and its successor, the successor must still rank as
            # rework-first-blocked-by-that-predecessor; filtering active_ids to offerable would
            # silently lose that ordering.
            rework_first: set[int] = set()
            if len(mine) > 1:
                active_ids = {t["id"] for _s, t in mine}
                for _s, t in mine:
                    for pred in self._unfinished_predecessors(t["id"], board=raw):
                        if pred["id"] in active_ids:
                            rework_first.add(pred["id"])
            offerable.sort(key=lambda st: (
                0 if st[1]["id"] in rework_first else 1, -st[1].get("priority", 0)
            ))
            stage, task = offerable[0]
            note = (
                "this is your active task — don't claim a new one. First reconcile "
                "the actual state: read the dossier (get_task) and check the "
                "code/repo — the work may already be done in full or in part. "
                "Done — verify it and advance(to='review') with honest evidence; "
                "not — continue from where it left off"
            )
            # over-budget disclosure (tracker #529). The WIP limit gates claim(), it is not an
            # invariant on the active count: review_task(verdict='needs_work') moves a card
            # Review->Build, and a human moves one out of Your Call (or hand-places an assigned
            # card), both WITHOUT passing the gate — deliberately, since rework must be
            # receivable at the limit or reviewed work strands. So active > limit is a correct
            # state, and this branch is exactly where it surfaces: the card being handed back is
            # typically the rework that caused it. `free` is max(0, limit - active) and so cannot
            # show it — "exactly full" and "over budget by two" are both free: 0 — while the
            # rulebook teaches the pump to branch on `free`. Appended ONLY when active > limit,
            # so the common case is byte-for-byte the old note (no noise), mirroring the
            # wip_saturated message, which already puts both numbers side by side in prose.
            # Pure string building: next_task stays READ-ONLY BY CONTRACT (see the top of this
            # method) — nothing here touches the tracker.
            if wip["active"] > limit:
                note += (
                    f". NOTE — you hold {wip['active']} active tasks against a limit of "
                    f"{limit}: that is legitimate, NOT board corruption. The limit gates "
                    f"claim(); a card bounced back by review_task(verdict='needs_work') or "
                    f"moved out of Your Call by a human re-enters Build without passing it, "
                    f"and rework outranks a fresh claim. Drain the rework — the overshoot "
                    f"clears when it reaches Review. Don't 'fix' the board and don't "
                    f"call_human about it"
                )
            if wip["free"] == 0:
                # #527: THIS branch returns before the free == 0 slot guard below, so a caller
                # whose `exclude` misses even one in-flight task gets a resume here and never
                # sees wip_saturated — the same board in the same minute answers
                # wip_saturated:true to a complete exclude and "your active task" at free:0 to an
                # incomplete one. That order is DELIBERATE and stays: `vikunja-mcp claimable`
                # calls next_task with an EMPTY exclude, and free == 0 implies
                # len(mine) >= limit >= 1, so the guard is structurally unreachable there — which
                # is exactly what keeps the hub's CLOSED seven-kind enum whole (see
                # claimable_cmd.classify_next: a saturated payload would classify as "empty" and
                # idle every hub loop on a board that still has resumable work). So the fix is
                # not to move the guard but to say HERE what the pump is looking at — the payload
                # is what it reads at the moment of confusion, and a rule in a file it loaded
                # hours ago is weaker. Conditional on free == 0 so the common resume (a free slot,
                # nothing surprising) keeps a byte-identical note.
                note += (
                    ". NOTE: wip.free == 0 AND a resume, with no wip_saturated — saturation is "
                    "only reported once `exclude` names every task you already have a live agent "
                    "on, because your active tasks are offered BEFORE the slot check. So check "
                    "your exclude, not the board: if an agent IS live on this task your exclude "
                    "is incomplete — add this id and call next_task again (that is how the "
                    "saturation signal appears), and do NOT dispatch a second agent onto it. If "
                    "no agent is live on it, this is the ordinary crash-recovery resume"
                )
            return with_wip({
                "resume": True, "stage": stage, "task": self._summary(task),
                "note": note,
            })

        # skip an epic here too: an epic container assigned to me in Queue (only ever a human's
        # doing — decompose parks epics in Backlog with the assignee cleared) is NOT claimable
        # (claim refuses epics below), and this stuck branch outranks the free queue, so handing
        # it back as a "call claim to finish" instruction would LIVELOCK the pump on an
        # unclaimable card and starve real work. Keys off the epic LABEL, never subtask structure;
        # this is not a false-skip of "really my active work" — an epic container is never one.
        stuck = [
            t for t in board.get("Queue", [])
            if my_id in self._assignee_ids(t)
            and not self._has_label(t, LABEL_EPIC)
            and t["id"] not in excluded
        ]
        if stuck:
            stuck.sort(key=lambda t: -t.get("priority", 0))
            note = (
                "this task in Queue is assigned to you (by a human or an unfinished "
                "claim) — call claim(task_id) to finish moving it into Design"
            )
            # #571: this branch, like the resume above, returns BEFORE the free == 0 slot guard,
            # so at zero free slots it hands back an instruction the pump cannot carry out —
            # claim() is exactly what the WIP gate refuses ("WIP limit reached"). Deliberately a
            # VARIANT of #527's clause and not that text: reaching this branch PROVES `offerable`
            # was empty, i.e. every active task of the caller is ALREADY named in `exclude`, so
            # "your exclude may be incomplete" — the ambiguity #527 answers on the resume branch —
            # cannot arise here. The useful fact is the other one: the instruction above is
            # un-followable right now, no wip_saturated came with it (this branch outranks the slot
            # check), and the way to surface saturation is to exclude THIS id for the rest of the
            # tick and ask again — the same "claim ОТКАЗАЛ — id в exclude до конца тика" move
            # SKILL.md already teaches. Nothing is claimed, so the card must NOT be dispatched onto:
            # it stays claimable once a slot frees. The branch ORDER is again NOT what gets fixed,
            # for #527's reason — `vikunja-mcp claimable` calls next_task with an EMPTY exclude and
            # its closed kind enum depends on this order. The over-budget clause stays on the resume
            # branch (#529's slice): the card here is not the rework that caused an overshoot.
            # Pure string building — next_task stays READ-ONLY BY CONTRACT (see the top of this
            # method). Conditional on free == 0 so the ordinary stuck claim keeps a byte-identical
            # note.
            if wip["free"] == 0:
                note += (
                    ". NOTE: wip.free == 0, so claim(task_id) will be REFUSED right now (\"WIP "
                    "limit reached\") — the slot gate stands between this instruction and Design. "
                    "And no wip_saturated is reported because this branch is offered BEFORE the "
                    "slot check, so the state is read from your own set, not the board: put this "
                    "id in `exclude` for the rest of the tick and call next_task again — that is "
                    "how the saturation signal appears. Do NOT dispatch an agent onto it: nothing "
                    "has been claimed, and the card stays claimable once a slot frees"
                )
            return with_wip({
                "resume": True, "stage": "Queue", "task": self._summary(stuck[0]),
                "note": note,
            })

        # independent-review pull path (#117): offer ANY task in Review awaiting review —
        # not just bug fixes — EXCEPT an epic container (label epic), whose code lives in its
        # children (each reviewed on its own advance), so there is nothing to review here. The
        # epic skip keys off the LABEL, never the presence of subtasks (same migration-guard
        # principle as the sequence gate). Two guards keep the pump safe: skip a task assigned
        # to the caller (never review your own work) and skip one whose verdict is fresher than
        # its last report (else an already-reviewed card is handed back forever and the queue
        # never advances — the freshness check just below).
        for t in sorted(board.get("Review", []), key=lambda t: -t.get("priority", 0)):
            if t["id"] in excluded:
                continue
            if self._has_label(t, LABEL_EPIC) or my_id in self._assignee_ids(t):
                continue
            # вердикт актуален, только если он свежее последнего отчёта: после цикла
            # needs_work -> доработка -> Review задача должна снова попасть к ревьюеру
            comments = self.api.comments(t["id"])
            # comments are stored as HTML (#85); render back to plain text before matching
            # the leading marker, else "[review]" hides behind a "<p>" wrapper.
            last_review = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[review]")),
                default=None,
            )
            last_worklog = max(
                (c.get("created") or "" for c in comments
                 if html_to_text(c.get("comment") or "").startswith("[worklog]")),
                default="",
            )
            # nothing to review until a work report exists: advance→review always posts a
            # [worklog], so a Review card WITHOUT one was placed there by hand — not a review
            # candidate. This also keeps the sequence gate's bare "predecessor ready at Review"
            # tasks (and any hand-parked card) out of the widened #117 net.
            if not last_worklog:
                continue
            if last_review is not None and last_review >= last_worklog:
                continue
            review_kind = "bug" if self._has_label(t, LABEL_BUG) else "change"
            return with_wip({
                # "stage" on every task-bearing result (see the free-queue branch below): the
                # stage the task was FOUND in, which for a review offer is always Review.
                # classify_next checks `review` BEFORE `resume`/`stage`, so this stays kind
                # "review" — pinned in test_claimable_cmd.
                "review": True, "review_kind": review_kind, "stage": "Review",
                "task": self._summary(t),
                "note": (
                    "this task is waiting for independent review — run it and cast a verdict "
                    "via review_task(task_id, verdict=..., report=...). review_kind='bug': "
                    "reproduce it and confirm the fix closes the CAUSE (not the symptom); "
                    "review_kind='change' (feat/chore/docs/refactor): confirm it does what "
                    "the spec/description said, the tests are real, it stayed in its slice, "
                    "and look for obvious regressions nearby. Do NOT review it if you wrote "
                    "this code in this session"
                ),
            })

        # no free slot -> do not even look at the free queue. This is NOT an empty queue: the
        # pump must WAIT for a dispatched agent to return, not idle the tick. Reported alone —
        # `starving` describes a chain that cannot start, which is not the actionable fact when
        # there is nowhere to put a task anyway (and computing it can cost a board escalation).
        if wip["free"] == 0:
            return with_wip({
                "task": None,
                "wip_saturated": True,
                "message": (
                    f"all {limit} WIP slot(s) are busy ({wip['active']} active) — "
                    f"nothing can be claimed until one finishes"
                ),
                "note": (
                    "NOT an empty queue: wait for a dispatched agent to return, then call "
                    "next_task again. Do NOT claim, and do NOT end the tick / ScheduleWakeup "
                    "as if there were no work"
                ),
            })

        # #126: exhaustive-board escalation for the sequence gate, memoised to AT MOST ONE fetch
        # per next_task. The board above is LIGHT (NEXT_TASK_STAGES omits Backlog/Your Call/Done,
        # #43), so a predecessor absent from it is not provably gone — it may sit in an unpaged
        # bucket. resolve_full lets _unfinished_predecessors consult the full board (the same one
        # claim/advance read) before ruling "not a blocker", so next_task's verdict matches claim's
        # BY CONSTRUCTION, not by keeping bucket-sets in sync by hand. Fetched lazily: when every
        # predecessor is already on the light board (the common case — a ready head sits at Review,
        # which IS in NEXT_TASK_STAGES) it is never called, so next_task still issues exactly one
        # view_tasks (the #43 latency win and the #105 single-fetch measurement both hold).
        full_board: dict[str, list[dict]] = {}

        def resolve_full() -> list[dict]:
            if "board" not in full_board:
                full_board["board"] = self._board()  # exhaustive: all buckets, incl Backlog/YC/Done
            return full_board["board"]

        # Queue-контракт: свободные берём, назначенные на другого НЕ трогаем — это «для людей».
        # epic-контейнер тоже пропускаем (по аналогии с blocked): родитель с меткой epic и живыми
        # детьми — это контейнер, а не работа, клеймить его бессмысленно (ровно баг из #94, где
        # next_task предложил epic-родителя как свободную задачу Queue). Скип цепляется за метку
        # epic, НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности): у обычной задачи тоже может быть подзадача, и она обязана остаться
        # клеймабельной.
        # No `excluded` check needed here (unlike the resume/stuck/review branches above): this
        # filter already requires `not self._assignee_ids(t)`, and an excluded id is by
        # definition a task the caller already holds — i.e. assigned. An excluded task therefore
        # can never pass the assignee filter and reach this list. If that filter is ever loosened
        # to admit assigned tasks, this reasoning breaks and `excluded` would need to be added here.
        queue = [
            t for t in board.get("Queue", [])
            if not self._assignee_ids(t)
            and not self._has_label(t, LABEL_BLOCKED)
            and not self._has_label(t, LABEL_EPIC)
        ]
        queue.sort(key=lambda t: -t.get("priority", 0))
        # hard sequence gate (option C, epic #94) — free-queue half: a free task whose
        # predecessor is still unfinished (below Review) is NOT yet claimable; skip it and offer
        # the next one. Keys off follows/blocked only (never parenttask), so an old unordered
        # epic's child stays offered (migration guard, C1). Reuse the ONE board snapshot (raw)
        # already fetched above — never refetch it per candidate (the board fetch isn't cheap).
        # A head returned to Backlog sits on the light board's page-1, so it's seen here; claim's
        # full-board gate backstops the rare Backlog-beyond-page-1 case (never a silent pass).
        gated: list[tuple[dict, list[dict]]] = []
        for t in queue:
            blockers = self._unfinished_predecessors(t["id"], board=raw, resolve_full=resolve_full)
            if not blockers:
                return with_wip({
                    # "stage" is on EVERY task-bearing result (see the review offer below and
                    # the two resume branches above), because SKILL.md's tick branches on it:
                    # "stage == Queue -> claim; Design/Build -> it's already yours". A free
                    # queue task used to omit it, so the rulebook's discriminator was ABSENT on
                    # the most common branch of all and the pump had to infer Queue-ness from
                    # resume:false — which is exactly how the rule got written wrong twice.
                    # classify_next (claimable_cmd, a cross-repo contract) only reads `stage`
                    # inside its resume-truthy branch, so resume:False still classifies as
                    # kind "queue" here — pinned in test_claimable_cmd.
                    "resume": False, "stage": "Queue", "task": self._summary(t),
                    "note": (
                        "a free task from the queue — call claim(task_id) (it moves it into "
                        "Design), then dispatch a per-task agent for the whole task. "
                        "resume:false here means 'take a new one', not 'nothing to do' "
                        "(empty is only task:null). A human picked this task into Queue, so "
                        "taking it is your mandate, NOT unbidden initiative: don't defer it "
                        "and don't stop the /loop under the generic autonomous-loop default "
                        "'steward, not initiator: don't start fresh work without a go-ahead' "
                        "— it does not apply to draining the tracker queue"
                    ),
                })
            gated.append((t, blockers))
        # Queue non-empty but EVERY free candidate gated -> starving tail. This MUST be
        # distinguishable from the empty queue below (the pump idles on task:null), else a
        # stalled chain sleeps forever unseen.
        if gated:
            # cycle safety valve (option C, epic #94, C5/#105): before reporting a generic
            # starving tail, DFS the unfinished-predecessor edges from these gated candidates.
            # A back-edge = a predecessor CYCLE (only ever hand-created in the web UI: A follows
            # B, B follows A) in which nothing is claimable AND which can't self-unblock, so it
            # earns its own distinct signal instead of masquerading as an ordinary stalled tail.
            # Reuse the ONE board snapshot (raw); the walk is bounded and provably terminating
            # (see _find_predecessor_cycle). A cycle anywhere on the board can NOT suppress a
            # genuinely claimable free task — the loop above already RETURNED it before here.
            cycle = self._find_predecessor_cycle(gated, raw, resolve_full=resolve_full)
            if cycle is not None:
                return with_wip(self._cycle_signal(cycle, full_board.get("board", raw)))
            return with_wip(self._starving_tail(gated))
        return with_wip({"task": None, "message": "the queue is empty — no work for the agent"})

    def _starving_tail(self, gated: list[tuple[dict, list[dict]]]) -> dict:
        """The distinguishable "everything is blocked" signal — NOT the empty queue.

        Returned only when the free Queue is NON-empty yet EVERY candidate is gated by an
        unfinished predecessor. It must NOT look like the empty-queue result ({task:None +
        "the queue is empty"}): the pump's /loop treats a bare empty queue as "ScheduleWakeup
        and idle", so a starved tail reported as empty would sleep forever and nobody would
        learn the chain stalled. `task` stays None (nothing to claim), but the additive
        discriminators — starving/waiting_count/needs_retriage — let a caller BRANCH, and
        `waiting` names each blocked task with the predecessor holding it. Special case: a
        predecessor sitting in Backlog is a chain HEAD sent back by return_task (label blocked,
        assignee cleared); its whole tail stalls until a human re-triages it — flagged
        needs_retriage and spelled out in the message, never left a mystery. (A predecessor
        CYCLE among these same gated candidates is caught earlier, by _find_predecessor_cycle
        (C5/#105), which returns its own distinct signal — so reaching here means the gate is
        acyclic: an honest starving tail, not a loop.)"""
        waiting = [
            {
                "task": self._summary(task),
                "blocked_by": blockers,
                "needs_retriage": any(b["stage"] == "Backlog" for b in blockers),
            }
            for task, blockers in gated
        ]
        retriage = [w for w in waiting if w["needs_retriage"]]
        lines = [
            f"{w['task']['ref']} ← "
            + "; ".join(
                f"{b['ref']} in '{b['stage']}'"
                + (" [sent back to Backlog via return_task — needs human re-triage]"
                   if b["stage"] == "Backlog" else "")
                for b in w["blocked_by"]
            )
            for w in waiting
        ]
        message = (
            f"{len(waiting)} queued task(s) can't be claimed — each waits on an unfinished "
            f"predecessor (a predecessor is 'ready' only at Review or Done). This is NOT an "
            f"empty queue. Waiting: " + " | ".join(lines)
        )
        if retriage:
            message += (
                f". {len(retriage)} of these are stalled behind a chain HEAD returned to "
                f"Backlog (return_task) — a human must re-triage the head before the tail "
                f"can resume."
            )
        return {
            "task": None,
            "starving": True,
            "waiting_count": len(waiting),
            "needs_retriage": bool(retriage),
            "waiting": waiting,
            "message": message,
            "note": (
                "NOT an empty queue: the free Queue is non-empty but every task is gated by an "
                "unfinished predecessor, so nothing is claimable right now. Do NOT treat this "
                "as 'nothing to do' — surface it so a human sees the stalled chain, then "
                "ScheduleWakeup and re-check later. When needs_retriage is set, a chain head "
                "was returned to Backlog and a human must re-triage it before the tail resumes."
            ),
        }

    def _find_predecessor_cycle(
        self, gated: list[tuple[dict, list[dict]]], board: list[dict],
        resolve_full: Callable[[], list[dict]] | None = None,
    ) -> list[int] | None:
        """DFS over UNFINISHED-predecessor edges from the gated Queue candidates; return the ids
        on the first cycle found (a back-edge into the current path), else None. A cycle can only
        be introduced by a human hand-editing follows/blocked relations in the web UI (an ordered
        decompose builds a linear, acyclic chain), and when it happens every task in the loop has
        an unfinished predecessor, so nothing is claimable — otherwise indistinguishable from a
        plain starving tail. This runs inside next_task, the pump's own tool, on every idle tick,
        so it MUST terminate and MUST NOT hang: the walk is ITERATIVE (no recursion limit) and
        each node enters the path at most once (guarded by `visited`/`on_path`), so it is bounded
        by the reachable unfinished subgraph. A malformed self-referential relation (A follows A)
        surfaces the node as its own predecessor and is reported as a 1-cycle, never an infinite
        loop. `visited` and `on_path` are SEPARATE sets — a node re-reached off the current path
        (a diamond/converging DAG) is pruned, NOT mistaken for a cycle (the false-positive guard).
        Bounded to unfinished (below-Review) predecessors — the exact edges the gate reads, never
        the whole board. The blockers next_task already computed for the roots seed the edge
        cache, so their get_task calls aren't repeated; deeper nodes are fetched lazily and
        memoized (each expanded at most once). Reuses the ONE board snapshot passed in."""
        preds_cache: dict[int, list[int]] = {
            t["id"]: [b["id"] for b in blockers] for t, blockers in gated
        }

        def preds(tid: int) -> list[int]:
            if tid not in preds_cache:
                preds_cache[tid] = [
                    p["id"] for p in self._unfinished_predecessors(
                        tid, board=board, resolve_full=resolve_full
                    )
                ]
            return preds_cache[tid]

        visited: set[int] = set()  # fully explored, proven not to reach a cycle -> never re-walked
        for root, _blockers in gated:
            if root["id"] in visited:
                continue
            path: list[int] = []       # the CURRENT dfs path, in order
            on_path: set[int] = set()  # its membership -> a hit here is a back-edge (a cycle)
            # explicit stack of (node, iterator-over-its-unfinished-predecessors)
            stack: list[tuple[int, Any]] = [(root["id"], iter(preds(root["id"])))]
            path.append(root["id"])
            on_path.add(root["id"])
            while stack:
                node, it = stack[-1]
                descended = False
                for child in it:
                    if child in on_path:
                        return path[path.index(child):]  # back-edge -> the loop is this slice
                    if child in visited:
                        continue  # already proven cycle-free -> prune, do NOT flag (diamond guard)
                    stack.append((child, iter(preds(child))))
                    path.append(child)
                    on_path.add(child)
                    descended = True
                    break
                if not descended:  # node's predecessors exhausted with no back-edge -> finish it
                    stack.pop()
                    path.pop()
                    on_path.discard(node)
                    visited.add(node)
        return None

    def _cycle_signal(self, cycle_ids: list[int], board: list[dict]) -> dict:
        """The distinguishable "a predecessor CYCLE makes everything unclaimable" signal — a THIRD
        state beside the empty queue and the plain starving tail. A cycle (A follows B, B follows
        A — only ever hand-created in the web UI) can't self-unblock: every task in it waits on
        another, so unlike a starving tail (which clears once a head reaches Review) ONLY a human
        can break it, by removing one follows/blocked link. `task` stays None (nothing to claim);
        `cycle`/`cycle_tasks` are the additive discriminators; the message and note NAME the
        looping tasks and tell the caller to surface it to a human, NOT to read it as 'nothing to
        do' and just sleep. Reuses the passed board snapshot to resolve each id to ref/title/stage
        (a member gone from the board falls back to '#<id>', never crashing)."""
        task_by_id = {
            t["id"]: (t, bucket["title"])
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        nodes: list[dict] = []
        for tid in cycle_ids:
            found = task_by_id.get(tid)
            if found is None:
                nodes.append({"id": tid, "ref": f"#{tid}", "title": "?", "stage": "?"})
            else:
                task, stage = found
                nodes.append(
                    {"id": tid, "ref": self._ref(task), "title": task["title"], "stage": stage}
                )
        # render the loop CLOSED (A → B → A) so a 2-cycle and a self-loop both read unambiguously
        loop = " → ".join([n["ref"] for n in nodes] + [nodes[0]["ref"]])
        detail = "; ".join(f"{n['ref']} in '{n['stage']}'" for n in nodes)
        message = (
            f"ЦИКЛ предшественников — {loop}: {len(nodes)} задач(и) взаимно ждут друг друга "
            f"(follows/blocked-связи образуют петлю), поэтому НИЧЕГО в цикле не клеймабельно и "
            f"цепочка НЕ разблокируется сама. Это НЕ пустая очередь и НЕ обычное голодание "
            f"хвоста: разорвать цикл может только человек, убрав одну follows/blocked-связь в "
            f"вебе. Задачи в цикле: {detail}"
        )
        return {
            "task": None,
            "cycle": True,
            "cycle_tasks": nodes,
            "message": message,
            "note": (
                "a predecessor CYCLE (hand-edited follows/blocked relations form a loop) makes "
                "every task in it unclaimable and it can NOT self-unblock — distinct from a plain "
                "starving tail. Do NOT treat this as 'nothing to do' and just ScheduleWakeup: "
                "surface it to a human (call_human) to break the cycle by removing one "
                "follows/blocked link in the web UI. Nothing in the loop moves until they do."
            ),
        }

    def claim(self, task_id: int) -> dict:
        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        if stage != "Queue":
            raise WorkflowError(f"task is in '{stage}', you can only claim from Queue")
        # epic containers are not claimable (epic #94 / #118): a card labelled epic is a
        # CONTAINER, not a unit of work — its evidence lives in its children, each claimed and
        # reviewed on its own. Refuse it here (next_task already skips it, but claim must gate too:
        # it otherwise checks only stage==Queue and would take an epic handed in directly), and
        # point the agent at the children. Keys off the epic LABEL, never the presence of subtasks
        # — an ordinary task may have subtasks and MUST stay claimable (the migration guard, same
        # principle as the sequence gate).
        if self._has_label(task, LABEL_EPIC):
            related = self.api.get_task(task_id).get("related_tasks") or {}
            subtasks = related.get("subtask") or []
            kids = ", ".join(self._ref(s) for s in subtasks) or "его подзадачами"
            raise WorkflowError(
                f"{self._ref(task)} is an epic CONTAINER (label epic), not a unit of work — "
                f"there is nothing to claim on the container itself. Its code/evidence lives in "
                f"its children, each claimed and reviewed on its own; work on those instead: "
                f"{kids}"
            )
        # hard sequence gate (option C, epic #94): refuse to START a successor while any of its
        # predecessors is unfinished (below Review). claim otherwise checks only stage==Queue, so
        # without this the gate is trivially bypassed by claiming a successor directly. Keys off
        # follows/blocked only (never parenttask) — old epics stay claimable. Reuses the snapshot.
        blockers = self._unfinished_predecessors(task_id, board=board)
        if blockers:
            joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
            raise WorkflowError(
                f"can't claim {self._ref(task)} yet — it's waiting on an unfinished "
                f"predecessor: {joined}. A predecessor becomes ready only at Review or Done; "
                f"finish that one first"
            )
        # WIP slot gate (generalises the #38 single-WIP flag): refuse a claim that would put this
        # token over its allowed number of simultaneously active tasks — always enforced, since
        # _effective_wip_limit always yields a number (an unset wip_limit means DEFAULT_WIP_LIMIT,
        # tracker #524). Reuse the board snapshot claim already fetched — the old code called
        # _my_active_tasks() with no board and paid for a SECOND full board fetch per gated claim.
        limit, limit_origin = self._wip_limit_with_origin()
        active = self._my_active_tasks(board=board)
        if len(active) >= limit:
            names = ", ".join(f"#{t['id']}" for _stage, t in active)
            # Name the KNOB, not just the number (tracker #517): a surprising refusal is the one
            # moment an agent needs to find where the limit is set, and the origin sentence sits
            # AFTER the "(n/m)" parens so the pins matching on that prefix keep working.
            raise WorkflowError(
                f"WIP limit reached ({len(active)}/{limit}) — you already hold {names}. "
                f"That number comes from {limit_origin}. "
                f"Finish one (advance to Review) or return_task it before claiming another"
            )
        existing = task.get("assignees") or []
        me = self._me()
        # self-heal: партиальный клейм (assign прошёл, move — нет) или человек руками
        # вернул заклеймленную задачу в Queue — я тут единственный assignee, долечиваем
        # вместо отказа. Кто-то ДРУГОЙ среди assignees (один или вместе со мной) — отказ как раньше.
        self_heal = len(existing) == 1 and existing[0].get("id") == me["id"]
        if existing and not self_heal:
            names = ", ".join(a.get("username", "?") for a in existing)
            raise WorkflowError(f"already taken ({names}) — grab the next one via next_task")

        if not self_heal:
            self.api.add_assignee(task_id, me["id"])
        fresh = self.api.get_task(task_id)
        fresh_ids = self._assignee_ids(fresh)
        others = [aid for aid in fresh_ids if aid != me["id"]]
        if others:
            self.api.remove_assignee(task_id, me["id"])
            raise WorkflowError("lost the race for this task — grab the next one via next_task")
        # vanish-window: человек мог снять моё назначение в окно между assign и re-read.
        # others пуст — но без меня в assignees move уведёт задачу в Design «ничьей»
        # (невидимо для next_task и незаклеймимо из Queue). Отказ до move закрывает окно
        # и в обычном, и в self-heal пути (там add_assignee не звался — окно то же).
        if me["id"] not in fresh_ids:
            raise WorkflowError(
                "the assignment vanished during the claim (a human removed it) — retry next_task"
            )

        # #693: entering the active pipeline invalidates any prior verdict, exactly as `advance`
        # has done since #119 — a human can hand-place a verdict-carrying card back in Queue with
        # the assignee cleared, and claiming it then walked `reviewed` into Design. The window is
        # narrower than `return_task`'s (the very next `advance(to='build')` clears it anyway), so
        # this is the same rule applied one step earlier rather than a second mechanism. `fresh`,
        # not the board snapshot: `_clear_verdict_labels` only DELETEs links present on the
        # snapshot it is handed, and the board copy is one read older than the labels being removed.
        self._clear_verdict_labels(fresh)
        view = self._view()
        self.api.move_task(self.project_id, view["id"], self._bucket("Design")["id"], task_id)
        self.api.add_comment(task_id, f"[claim] {me['username']} взял задачу в работу")
        return {
            "claimed": True, "task": self._summary(fresh),
            "next": "describe your approach and call advance(to='build', spec=...)",
        }

    def _move(self, task_id: int, stage: str) -> None:
        self.api.move_task(
            self.project_id, self._view()["id"], self._bucket(stage)["id"], task_id
        )

    def _target_backlog(self, project_id: int) -> tuple[int, int]:
        """(view_id, bucket_id) колонки Backlog на ЧУЖОЙ доске — кросс-проектная половина
        file_task. Сознательно ОТДЕЛЬНА от _view/_bucket/_move: те (и их кэши) привязаны к
        self.project_id и питают каждый горячий гейт, а кросс-файлинг — редкое событие
        координации, поэтому здесь свежий kanban_view+buckets на каждый вызов (без кэша ->
        без новой поверхности устаревания). Резолв происходит ДО создания карточки
        (fail-fast): кривой id, недоступный токену проект или не-трекерная доска отказывают,
        НИЧЕГО не осиротив в дефолт-бакете цели. 403/404 заворачиваются в actionable
        WorkflowError с именем цели — граница безопасности ЗДЕСЬ сам скоуп-токен (решает
        Vikunja, мы только внятно показываем отказ). 401 НЕ заворачиваем намеренно: он
        должен дойти до server._tool как VikunjaError, чтобы сработал reload-and-retry
        ротации токена (#140)."""
        try:
            view = self.api.kanban_view(project_id)
            found = self.api.buckets(project_id, view["id"])
        except VikunjaError as exc:
            if exc.status in (403, 404):
                raise WorkflowError(
                    f"can't file into project {project_id}: Vikunja said {exc.status} "
                    f"({exc.message}). Either the token's user has no access to that "
                    f"project (the scoped API token is the security boundary — a human "
                    f"must share the target project with this agent), the project id is "
                    f"wrong, or the project has no kanban board. Nothing was created."
                ) from exc
            raise
        backlog = next((b for b in found if b["title"] == "Backlog"), None)
        if backlog is None:
            raise WorkflowError(
                f"can't file into project {project_id}: its board has no 'Backlog' "
                f"column — not a tracker-managed board (run `vikunja-mcp setup` for it "
                f"first). Nothing was created."
            )
        return view["id"], backlog["id"]

    def _mark_epic_if_children_complete(self, child: dict, board: list[dict]) -> None:
        """Best-effort epic-complete marker (#118 Part 2). When THIS child's advance→review makes
        EVERY child of an epic parent ready (Review or Done — READY_STAGES, the same readiness the
        sequence gate uses; NOT a second definition), leave a VISIBLE marker on the EPIC so the
        human sees the container is assembled and can close the set: the LABEL_EPIC_READY label
        (at-a-glance on the board) plus an explanatory comment. It does NOT move the epic — agents
        can't and mustn't (Part 1 made epics unclaimable; only a human moves anything to Done). This
        is deliberately the ADDITIVE form of the cross-task write #103 rejected in its STRUCTURAL
        form: it reaches out of the child's transition to touch a DIFFERENT card, but adds only a
        label + comment — no stage move, no lost work, no gate effect. It MUST therefore be called
        strictly best-effort (the caller swallows every exception): a cosmetic marker on someone
        else's card must never strand the child's own advance, and it adds nothing to the child's
        result. Idempotent — skips if the epic already carries LABEL_EPIC_READY, so a bounced-and-
        re-advanced child never double-marks. Keys off the epic LABEL and the parenttask relation,
        never structure alone. `board` is the full snapshot advance already fetched; the current
        child moved to Review AFTER it was taken, so the child is scored as Review explicitly while
        every other sibling is read from the snapshot."""
        child_id = child["id"]
        related = self.api.get_task(child_id).get("related_tasks") or {}
        parents = related.get("parenttask") or []
        if not parents:
            return  # not a subtask of anything — nothing to mark
        stage_by_id = {
            t["id"]: bucket["title"]
            for bucket in board for t in (bucket.get("tasks") or [])
        }
        for parent in parents:
            # `parent` here is a related_tasks SUB-DICT, and the real server HOLLOWS those — labels/
            # assignees/nested related_tasks come back as None even when the task carries them (only
            # scalars survive; verified on real 2.3.0, #118 rework). So its labels can NOT be read
            # here — doing so silently no-op'd the marker in production while the too-generous fake
            # stayed green (#125). Re-fetch the FULL parent and read labels (both epic and the
            # idempotency marker) off IT. This is the same get_task the sibling read already needs,
            # so it is ZERO extra calls in the epic case (one hoisted, not added); for a non-epic
            # parent it costs +1 get_task, which is fine (best-effort, off next_task's hot path).
            full_parent = self.api.get_task(parent["id"])
            if not self._has_label(full_parent, LABEL_EPIC):
                continue  # parent isn't an epic container — not ours to mark
            if self._has_label(full_parent, LABEL_EPIC_READY):
                continue  # already marked — idempotent (a bounced+re-advanced child won't re-fire)
            siblings = (full_parent.get("related_tasks") or {}).get("subtask") or []
            if not siblings:
                continue
            all_ready = all(
                ("Review" if s["id"] == child_id else stage_by_id.get(s["id"])) in READY_STAGES
                for s in siblings
            )
            if not all_ready:
                continue
            # label FIRST (the idempotency key AND the board marker), THEN the comment: a partial
            # failure (label lands, comment doesn't) still leaves the epic consistently "marked", so
            # a later advance won't double-fire.
            self._add_label(parent["id"], LABEL_EPIC_READY)
            self.api.add_comment(
                parent["id"],
                f"[эпик собран] все {len(siblings)} дет(и) эпика достигли Review-или-Done — "
                f"контейнер собран и готов к твоему Done (в Done двигает только человек). Если "
                f"позже отобьёшь ребёнка из Review — увидишь его в Build и придержишь закрытие."
            )

    def advance(
        self, task_id: int, to: str,
        spec: str | None = None, worklog: str | None = None, evidence: str | None = None,
        root_cause: str | None = None,
    ) -> dict:
        to = (to or "").strip().lower()
        if to == "done":
            raise WorkflowError("only a human moves a task to Done after review — not you")
        if to not in AGENT_ADVANCE:
            raise WorkflowError(f"invalid transition '{to}'; available: build, review")
        from_stage, to_stage = AGENT_ADVANCE[to]

        board = self._board()
        task, stage = self._find_task(task_id, board=board)
        self._require_mine(task, stage)
        if stage != from_stage:
            raise WorkflowError(
                f"moving to {to_stage} is only possible from {from_stage}; task is now in {stage}"
            )

        if to == "build":
            unusable = _unusable_report_fields(("spec", spec))
            if unusable:
                raise WorkflowError(
                    f"a spec is required — this call's spec was {unusable[0][1]}: describe "
                    f"your approach before implementing. {_LOST_ARGUMENT_HINT}"
                )
            self.api.add_comment(task_id, f"[spec]\n{spec.strip()}")
            # (пере)сборка тоже инвалидирует любой прошлый вердикт: человек мог руками
            # вернуть одобренную/отбитую карточку сюда (#119). На свежем клейме меток нет —
            # это no-op; needs_work-цикл идёт через Build (не Design), сюда не заходит.
            self._clear_verdict_labels(task)
        else:
            # hard sequence gate (option C, epic #94, mechanism 2): the advance→review LATCH on
            # an in-flight successor — the case the human asked about. Refuse to land THIS task in
            # Review while any of its predecessors is below Review: a predecessor P that had
            # reached Review (so this successor got claimed) but was then bounced Review→Build
            # must be reworked back to Review before this one may advance. Applies ONLY to
            # to='review' (to='build' and every other transition are untouched); keys off
            # follows/blocked only, never parenttask (migration guard); reuses the full board
            # already fetched (must be full, not light — a predecessor may sit in Your Call/Done).
            # Known residual gap accepted by design: if THIS task was ALREADY in Review when P
            # bounced, the latch doesn't apply retroactively — the human-only Done move backstops.
            blockers = self._unfinished_predecessors(task_id, board=board)
            if blockers:
                joined = "; ".join(f"{b['ref']} in '{b['stage']}'" for b in blockers)
                raise WorkflowError(
                    f"can't move {self._ref(task)} to Review yet — its predecessor is being "
                    f"reworked below Review: {joined}. Finish that predecessor's rework and get "
                    f"it back to Review first, then advance this one (a predecessor is 'ready' "
                    f"only at Review or Done)."
                )
            # #657: DISJUNCTIVE guard, so it must name WHICH field failed it. The old text
            # listed both whatever was actually wrong, which made two very different states
            # read identically: an agent who wrote a full worklog and merely forgot evidence
            # got the same sentence as an agent whose 7 KB worklog never arrived. See
            # _LOST_ARGUMENT_HINT for what is and is not provable about the second one.
            # #718: `root_cause` joins that guard for a BUG, and only for a bug. Until this card
            # the field was a silent no-op — measured, a card labelled `bug` advanced to Review
            # with no cause at all and the tool answered `review_kind: 'bug'` in the SAME payload,
            # i.e. it knew. Meanwhile `advance`'s own docstring called the field MANDATORY and
            # SKILL.md called it ОБЯЗАТЕЛЕН, so the rules promised a gate that did not exist and
            # the reviewer's 'bug' rubric ("confirm the fix closes the CAUSE from the report")
            # could be handed a report with no cause in it. Asked with the same expression that
            # computes `review_kind` below, deliberately: a second definition of "what counts as
            # a bug" is how the two would drift. Folded into the SAME call rather than a separate
            # `if`, so an agent missing two fields of three is told all three at once — that
            # disjunctive shape is #657's, and splitting it would undo it.
            # The epic container is exempt for the reason the push-nudge below exempts it: its
            # code lives in its children, no reviewer is ever offered it, so a cause demanded here
            # would have no consumer.
            fields = [("worklog", worklog), ("evidence", evidence)]
            if self._has_label(task, LABEL_BUG) and not self._has_label(task, LABEL_EPIC):
                fields.append(("root_cause", root_cause))
            unusable = _unusable_report_fields(*fields)
            if unusable:
                named = "; ".join(f"{name} — {state}" for name, state in unusable)
                raise WorkflowError(
                    f"Review needs a report. Unusable in this call: {named}. worklog = what "
                    f"was done and how it was VERIFIED (by running it, not by reading the "
                    f"code); evidence = the commit sha / PR link / verification output; for a "
                    f"bug fix root_cause too — the cause of the bug, not the symptom. "
                    f"{_LOST_ARGUMENT_HINT}"
                )
            report = ["[worklog]"]
            if (root_cause or "").strip():
                report.append(f"Причина: {root_cause.strip()}")
            report.append(f"Сделано: {worklog.strip()}")
            report.append(f"\nEvidence: {evidence.strip()}")
            self.api.add_comment(task_id, "\n".join(report))
            # resubmit-reset: ресабмит инвалидирует ЛЮБОЙ прошлый вердикт — снимаем ОБЕ
            # вердикт-метки, и review-failed, и reviewed (#119: человек мог руками вытащить
            # одобренную карточку из Review на доработку — reviewed не должен уехать на новое
            # ревью). No-op на первом сабмите (меток ещё нет).
            self._clear_verdict_labels(task)
        self._move(task_id, to_stage)
        result = {"moved_to": to_stage, "task_id": task_id}
        if to == "review":
            # best-effort epic-complete marker (#118 Part 2): if THIS child was the LAST of an epic
            # parent to reach Review-or-Done, mark the epic (label + comment) so the human sees it's
            # ready to close. It writes to a DIFFERENT card, so it is wrapped so NOTHING it does can
            # fail the child's advance or change this result's shape (it adds no keys) — see the
            # helper's docstring. Any exception (epic lookup, comment, or label) is swallowed after a
            # one-line stderr note (#134).
            try:
                self._mark_epic_if_children_complete(task, board)
            except Exception as exc:
                # strictly best-effort — a marker on another card never fails the child's advance, so
                # the exception is still swallowed; but NO LONGER silently (#134). A bare
                # `except Exception: pass` hid a marker broken by a refactor: `except Exception`
                # catches TypeError/AttributeError (programmer errors), not just network blips, and
                # the marker IS the human's visibility mechanism for an assembled epic, so a
                # silently-dead indicator is worse than none. Leave one line on STDERR only (never
                # stdout — a stray byte corrupts the MCP stdio protocol), naming the advancing child
                # and the exception class so the failure is actionable (the epic's own id isn't
                # reliably known here — the helper can raise before resolving a parent — and the
                # helper is out of this card's slice; the child is one get_task from the epic). Same
                # best-effort-with-a-stderr-trace contract as sync_installed_artifacts (#88).
                #
                # #135: the LOG path must be as guarded as the marker it reports on. `{exc}`
                # calls str(exc) INSIDE this handler, so an exception whose __str__ itself
                # raises would escape advance(). By now the child has ALREADY reached Review
                # and written its [worklog], so a leaked exception makes advance raise for work
                # that genuinely succeeded — a state/report divergence, worse than a lost log.
                # So format the always-safe parts (exception CLASS + child id) unconditionally,
                # fall back to "<unprintable>" when str(exc) blows up so the diagnostic survives
                # the pathological case (a silent swallow would undo #134), then wrap the write
                # itself so nothing on this best-effort path can propagate. For ordinary
                # exceptions detail == str(exc), so the line is byte-for-byte the #134 one.
                try:
                    detail = str(exc)
                except Exception:
                    detail = "<unprintable>"
                try:
                    print(
                        f"vikunja-mcp: epic-complete marker skipped for child #{task_id}: "
                        f"{exc.__class__.__name__}: {detail}",
                        file=sys.stderr,
                    )
                except Exception:
                    pass
        # push-нудж (#117): ЛЮБАЯ задача, доведённая до Review, требует независимого ревью —
        # не только багфикс. Исключение — epic-контейнер (label epic): его код лежит в детях
        # (каждый отревьюен на своём advance), ревьюить нечего. Скип цепляется за метку epic,
        # НИКОГДА за наличие подзадач (тот же миграционный принцип, что у гейта
        # последовательности). Пер-таск-агент вернёт review_needed оркестратору, тот задиспатчит
        # свежего ревьюера (author != reviewer); review_kind задаёт рубрику: 'bug' —
        # воспроизвести и закрыть причину; 'change' — соответствие spec, реальные тесты, слайс.
        if to == "review" and not self._has_label(task, LABEL_EPIC):
            result["review_needed"] = True
            result["review_kind"] = "bug" if self._has_label(task, LABEL_BUG) else "change"
            result["note"] = (
                "this task needs independent review — return the review_needed flag to the "
                "orchestrator in your result: it will dispatch a fresh reviewer in the "
                "background (author ≠ reviewer). review_kind tells it the rubric: 'bug' — "
                "reproduce and confirm the cause is closed; 'change' — conforms to spec, real "
                "tests, stayed in slice, obvious regressions nearby"
            )
        return result

    def review_task(self, task_id: int, verdict: str, report: str) -> dict:
        verdict = (verdict or "").strip().lower()
        if verdict not in ("approve", "needs_work"):
            raise WorkflowError("verdict must be 'approve' or 'needs_work'")
        if not (report or "").strip():
            raise WorkflowError(
                "report required: what you reproduced/verified by running and why this verdict"
            )
        task, stage = self._find_task(task_id)
        if stage != "Review":
            raise WorkflowError(f"only tasks in Review can be reviewed; this one is in {stage}")

        if verdict == "approve":
            self.api.add_comment(task_id, f"[review] APPROVE\n{report.strip()}")
            self._add_label(task_id, LABEL_REVIEWED)
            self._remove_label(task, LABEL_REVIEW_FAILED)
            return {
                "verdict": "approve", "task_id": task_id,
                "note": "verdict recorded; a human moves the task to Done",
            }
        self.api.add_comment(task_id, f"[review] NEEDS WORK\n{report.strip()}")
        self._add_label(task_id, LABEL_REVIEW_FAILED)
        self._remove_label(task, LABEL_REVIEWED)
        # An OWNERLESS card bounces to QUEUE, not Build (#705). Build means "someone is working
        # on this"; with no assignee there is no implementer to hand it back TO, and the card
        # measured UNREACHABLE there. Precisely: it can still be READ and commented on
        # (get_task/comment/attach_file need no ownership) — what no agent tool could do is MOVE
        # it or make it anyone's. Measured at 3a0ee77 by sweeping all 12 registered tools (14
        # forms) against such a card in Design AND in Build: ZERO movers from either, against an
        # assigned control that yields four. Individually: call_human/advance/return_task/
        # decompose all answer "not assigned to you — claim it first", claim refuses ("you can
        # only claim from Queue") so that advice cannot be followed, and next_task offers
        # nothing — no branch of it can, since resume keys off assignees, the stuck-claim and
        # free-queue branches off stage == Queue, and the review offer off stage == Review.
        # The reviewer's question then dies on a card nobody comes back for.
        # This is the SAME state claim's vanish-window guard already refuses to create ("без меня
        # в assignees move уведёт задачу в Design «ничьей» (невидимо для next_task и
        # незаклеймимо из Queue)") — so it is fixed the same way: by not producing it, rather
        # than by teaching the other tools to live with it. Same way means same WINDOW, too, and
        # that half was missing from this card's first draft: routing off `task` — the board
        # snapshot _find_task took at the top of this method — decides ownership up to four API
        # calls before the move (measured sequence: view_tasks -> add_comment ->
        # get_or_create_label -> add_label -> buckets -> move_task), so a human clearing the
        # assignee in the web UI mid-call put the card in Build ownerless and reproduced #705
        # through this very method. claim pays for the same guarantee with TWO get_task re-reads
        # before ITS move; this pays one, here, and routes on the FRESH read. The price is one
        # extra GET on the needs_work path and one more place this method can fail after the
        # verdict comment has landed — the shape move_task itself already has, and cheaper than
        # a window that recreates the bug the method exists to close.
        #
        # Queue and not Build/Design: an ownerless card that needs work IS free work, so the
        # ordinary path reopens — next_task offers it (priority-sorted, like any queue item),
        # claim takes it, and the four routes a bounce can need — advance, call_human,
        # return_task, decompose — are open to the new owner, who reads the [review] comment
        # from the dossier and can forward the question with call_human.
        # It does NOT weaken "the WIP limit gates claim(), it is not an invariant on active"
        # nor "rework outranks a fresh claim": both are about a card WITH an owner, which
        # re-enters THEIR active set without passing the gate. This card was in nobody's active
        # set, so routing it through claim strands nothing — at a saturated board it waits in
        # Queue, visible and offered the moment a slot frees, exactly like the fresh work beside
        # it. The ASSIGNED path is untouched, byte for byte, note included: a card assigned to
        # someone ELSE still goes back to Build for THAT implementer and never becomes claimable
        # by whoever reviewed it ("assigned to another" keeps meaning "not yours"), and the split
        # asks for NO assignee AT ALL, so a card I merely CO-own is an assigned card too.
        #
        # "Reopens the ordinary path" is measured, not assumed, and it is not universal — two
        # label sub-cases keep the card out of next_task's free-queue offer, both by PRE-EXISTING
        # filters and neither made worse by landing in Queue (in Build nothing could see it at
        # all). Measured: `epic` — not offered AND claim refuses it as a container, so an
        # ownerless epic ends up parked in Queue for a human rather than in Build for nobody;
        # `blocked` — not offered, though claim still takes it by id (that asymmetry between the
        # two is older than this change). An unfinished predecessor behaves as designed: claim
        # refuses by the sequence gate and the card becomes claimable once the head is ready.
        if self._assignee_ids(self.api.get_task(task_id)):
            self._move(task_id, "Build")
            return {
                "verdict": "needs_work", "task_id": task_id, "moved_to": "Build",
                "note": "the task went back to the implementer — they'll see it in next_task",
            }
        self._move(task_id, "Queue")
        return {
            "verdict": "needs_work", "task_id": task_id, "moved_to": "Queue",
            "note": (
                "this card had NO assignee, so there is no implementer to hand it back to — it "
                "went to the QUEUE as free work instead of Build, where an ownerless card can "
                "be read but no agent tool can move it or make it anyone's. Whoever claims it "
                "next reads your report in the dossier; if it was a question for the human, "
                "they forward it with call_human from Design/Build"
            ),
        }

    def call_human(self, task_id: int, question: str) -> dict:
        if not (question or "").strip():
            raise WorkflowError(
                "state your question: what you need from the human and which options you weighed"
            )
        task, stage = self._find_task(task_id)
        # Stage BEFORE ownership (#590). The refused SET is unchanged — both checks are
        # conjunctive — but the ORDER decides which refusal a REVIEWER reads, and a reviewer's
        # card is in Review and (multi-identity) assigned to the implementer, so the old order
        # answered "claim it first": advice that is actively wrong here. You never claim work
        # you are reviewing. The stage message below tells them where the question really goes.
        if stage not in ACTIVE_STAGES:
            msg = f"call_human works only from Design/Build; task is in {stage}"
            if stage == "Review":
                # Measured (#590): parking from Review is not merely disallowed, it is lossy —
                # this method's body would _move the card to Your Call, and from Your Call
                # review_task refuses BOTH verdicts, so the verdict dies with the question.
                msg += (
                    " — a reviewer's question goes in review_task(task_id, verdict='needs_work', "
                    "report=<the question>): the card returns to its implementer in Build, who can "
                    "call_human from there. Parking it from here would move it OUT of Review, and "
                    "review_task then refuses — your verdict would die with your question."
                )
            raise WorkflowError(msg)
        self._require_mine(task, stage)
        self.api.add_comment(task_id, f"[нужен человек] {question.strip()}")
        self._move(task_id, "Your Call")
        result = {
            "moved_to": "Your Call", "task_id": task_id,
            "note": "assignee kept; the human replies and moves the task back to Design/Build",
        }
        # Slack-webhook ping (#252): the human used to discover a YC card only by looking at
        # the board — when VIKUNJA_NOTIFY_WEBHOOK is configured, tell them. Fires only AFTER
        # the park fully succeeded (comment + move) — never about a card that isn't actually
        # in Your Call — and is STRICTLY BEST-EFFORT (same contract as the epic marker,
        # #134/#135): the notifier raises on any failure, and this single boundary swallows
        # it with one guarded stderr line, so a down/misconfigured gateway costs the ping,
        # never the parked question. notified=true/false surfaces delivery honestly (the
        # attach_file journal_comment pattern) so the agent's report can say "check the
        # board" when the ping was lost; the key is absent entirely when no webhook is
        # configured (zero result-shape change for the feature-off default).
        if self.notifier is not None:
            try:
                self.notifier.your_call(
                    ref=self._ref(task), title=task["title"],
                    question=question.strip(), task_id=task_id,
                )
                result["notified"] = True
            except Exception as exc:
                _stderr_note_best_effort(
                    f"vikunja-mcp: Your Call webhook ping skipped for #{task_id}", exc
                )
                result["notified"] = False
        return result

    def return_task(self, task_id: int, reason: str) -> dict:
        if not (reason or "").strip():
            raise WorkflowError("give the reason for the block — it'll be posted as a comment")
        task, stage = self._find_task(task_id)
        # TWO stages are shut, both BEFORE _require_mine so solo and multi-identity read the same,
        # correct refusal (in multi-identity a reviewer's card is the implementer's, and
        # "claim it first" would send them the wrong way). The five OTHER stages stay open on
        # purpose — Backlog/Queue/Design/Build/Your Call: returning a half-claimed or in-flight
        # card is a defensible "externally blocked", which is what this tool is for. Your Call is
        # deliberately among them: that card is still the agent's OWN work in flight (call_human
        # keeps the assignee), the [нужен человек] question survives in the append-only journal,
        # and a block that appears while waiting for an answer is the same defensible case as from
        # Design/Build. That choice is not free, and the price is named rather than hidden: the
        # webhook ping (if configured) has already gone out and points at a card no longer in the
        # column the human looks at, and `parked_task_ids` stops covering it, so a dead tree's
        # unpushed work regrades from `--gc`'s `expected` to `kept` and wakes someone. Both are
        # noise, neither destroys work — unlike Done, where the card is not the agent's to move.
        # Review (#590): measured — without this gate the tool passed from Review and silently
        # walked reviewed work to Backlog, unassigned + `blocked`.
        if stage == "Review":
            raise WorkflowError(
                "return_task is not available from Review: it would unassign the card and send "
                "work that is under review (or already approved) back to Backlog for re-triage. "
                "A reviewer who needs a human decision puts it in review_task(task_id, "
                "verdict='needs_work', report=<the question>) — the card goes back to its "
                "implementer in Build, who owns it and can call_human from there; a finding "
                "outside the card's slice goes to file_task. Anything else genuinely blocked in "
                "Review takes that same door back to Build first."
            )
        # Done (#626): the Done transition is human-only BY DESIGN, and an invariant that only
        # holds one way is not an invariant. Measured on a card driven the normal way (Queue ->
        # claim -> Design -> Build -> Review -> approve -> a human moves it to Done): return_task
        # did not refuse, and left the card in Backlog with NO assignee, carrying `reviewed` AND
        # `blocked` at once — the same "approved and blocked" board state #590 documented for
        # Review. The gate belongs HERE rather than in one shared stage rule because human-only
        # Done is not expressed anywhere as a rule: every tool re-derives it from its own source
        # stage (advance from_stage, claim Queue, review_task Review, call_human Design/Build),
        # so a tool that moves a card without a stage check reproduces the hole. `decompose` was
        # exactly that tool — measured on the same card, it walked the parent to Backlog with
        # `epic` — hence #626 did NOT claim to be the last one; that sibling was gated by #649,
        # which closed the last instance known then WITHOUT closing the class: the rule is still
        # nowhere written once, so the next mutating tool reopens it and nothing catches that.
        # Ownership cannot stand in for the check either: a human moving a card
        # into Done does not unassign it, so `_require_mine` passes on the very card that must be
        # untouchable.
        if stage == "Done":
            raise WorkflowError(
                "return_task is not available from Done: a human accepted this card, and walking "
                "accepted work back out to Backlog is the human's call too — the Done transition "
                "is human-only in BOTH directions. It would also unassign the card and stack "
                "`blocked` on top of `reviewed`, so the board would claim 'approved' and 'blocked' "
                "at once. If Done work needs redoing, file_task a follow-up card "
                "(related_task_id=<this task>) for a human to triage — call_human refuses from "
                "Done as well; a human can also move this card back themselves."
            )
        self._require_mine(task, stage)
        self.api.add_comment(task_id, f"[blocked] {reason.strip()}")
        # #693: the card LEAVES the pipeline unassigned, so any prior verdict has stopped
        # describing it — same reason `decompose` clears on its way out (#673). Measured before
        # the call was added: approve -> a human hand-drags the approved card back to Build ->
        # return_task left Backlog holding `['blocked', 'reviewed']` at once. That pair is the
        # end state the Done refusal three blocks up REFUSES in so many words ("the board would
        # claim 'approved' and 'blocked' at once") — reachable here from an OPEN stage, where
        # `return_task` is legitimate and there is nothing to gate. `review-failed` + `blocked`
        # is the weaker form of the same shape and goes with it: both are stale once the card is
        # ownerless in Backlog awaiting a human's re-triage.
        self._clear_verdict_labels(task)
        label = self.api.get_or_create_label(LABEL_BLOCKED)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        return {"moved_to": "Backlog", "task_id": task_id, "labeled": LABEL_BLOCKED}

    def decompose(self, task_id: int, subtasks: list[dict], ordered: bool = False) -> dict:
        if not subtasks or len(subtasks) < 2:
            raise WorkflowError("decomposition means at least 2 subtasks")
        if any(not (st.get("title") or "").strip() for st in subtasks):
            raise WorkflowError("every subtask must have a title")
        task, stage = self._find_task(task_id)
        # Review (#663): the shape #590 gated for `return_task`, still open on the sibling tool —
        # #649 shut Done here and said so in this very block. Measured through the real `Workflow`
        # over a FakeAPI board, on a card driven the NORMAL way (Queue -> claim -> Design -> Build
        # -> Review): decompose did not refuse and left the parent in Backlog with NO assignee and
        # `epic`, two children in Queue and a `[decompose]` comment — work under review pulled out
        # of the pipeline and re-declared an unfinished container before anyone ruled on it. On an
        # APPROVED card still waiting for a human's Done the same run produced `reviewed` AND
        # `epic` at once: the Done block's own end state, one stage early. Per-tool for the reason
        # spelled out below, and the PLACEMENT is measured, not chosen by taste: a guard inside
        # `_move` fires LAST — both children already on the board, assignee off, comment posted —
        # so its refusal would LIE to the caller. It also runs BEFORE `_require_mine`, because in
        # multi-identity the card under review is the IMPLEMENTER's: measured, the ungated tool
        # answered "not assigned to you — claim it first", the one answer a reviewer must never be
        # given (you never claim work you are reviewing).
        if stage == "Review":
            raise WorkflowError(
                "decompose is not available from Review: it would unassign the card, stack `epic` "
                "on top of the verdict label and drop fresh children into Queue, so work that is "
                "under review (or already approved) would be pulled out of the pipeline and "
                "re-declared an unfinished container before anyone ruled on it. Deciding that work "
                "needs splitting is a Build-time call, so the card has to come back to Build "
                "first: a reviewer sends it there with review_task(task_id, verdict='needs_work', "
                "report=<why it should be split>), and its implementer, who owns it in Build, "
                "decomposes from there; a human can also move it back themselves. A finding "
                "outside this card's slice goes to file_task instead."
            )
        # Done (#649): the second half of the same bypass #626 closed for `return_task`, and the
        # LAST measured instance of it. Measured on a card driven the normal way (Queue -> claim
        # -> Design -> Build -> Review -> approve -> a human moves it to Done): decompose did not
        # refuse and walked the parent to Backlog with NO assignee, carrying `reviewed` AND `epic`
        # at once, with two fresh children in Queue — the board claiming a card a human accepted
        # is now an unfinished container. Not a regression: at 51ab50d^ (the parent of #590's
        # commit) decompose reads the same `_find_task` -> `_require_mine` with no stage check at
        # all. The gate is per-tool for the same reason #626's is: human-only Done is nowhere
        # expressed as ONE rule, and the only chokepoint every card-touching tool shares is
        # `_find_task`, which also serves the READ paths (get_task/comment/download_attachment/
        # attach_file) — shutting Done there would make an accepted card unreadable, a worse
        # regression than the hole. A guard inside `_move` would fire only AFTER the children
        # exist and `epic`/unassign have landed, which is not a guard. So the CLASS stays open by
        # construction — the next mutating tool that moves a card without checking its stage
        # reopens it, and nothing catches that — and is filed for a human's ruling as #662; what
        # is closed here is the last instance known TODAY, and that was SWEPT, not assumed: all 12
        # registered tools were run against one such card and NONE walks it out — the five that
        # refuse (advance, claim, call_human, review_task, return_task) plus this one, and the six
        # that never move it (next_task, get_task, comment, file_task, attach_file,
        # download_attachment). The count is spelled out because #626 shipped this same claim off
        # a sweep of 5 of 12. Review WAS a different question, left open here and filed as #663;
        # the gate directly above shut it, so this block is now the SECOND of decompose's two
        # stage gates rather than its only one, and five stages stay open, not six.
        # Ownership cannot stand in: a human moving a card into Done does not unassign it, so
        # `_require_mine` passes on the very card that must be untouchable — and it runs SECOND
        # here so that a Done card belonging to someone else reads the stage refusal instead of
        # "claim it first", which for an accepted card is the one answer that can never be right.
        if stage == "Done":
            raise WorkflowError(
                "decompose is not available from Done: a human accepted this card, and splitting "
                "accepted work back out into Backlog is the human's call too — the Done "
                "transition is human-only in BOTH directions. It would also unassign the card, "
                "stack `epic` on top of `reviewed` and drop fresh children into Queue, so the "
                "board would claim work a human accepted is an unfinished container. Work that a "
                "Done card revealed is NEW work, not a split of this one: file_task the "
                "follow-ups (related_task_id=<this task>) for a human to triage — call_human "
                "refuses from Done as well; a human can also move this card back themselves."
            )
        self._require_mine(task, stage)

        created: list[dict] = []
        try:
            for st in subtasks:
                child = self.api.create_task(
                    self.project_id, st["title"].strip(),
                    description=st.get("description", ""), priority=int(st.get("priority", 0)),
                )
                # record the child the instant it exists on the board — BEFORE add_relation
                # /_move — so a failure anywhere below still reports it. This is the retry-
                # duplication boundary: once create_task returned, a naive re-run doubles it.
                # `ref` alongside the id (#749), the same fix #735 made in `file_task`:
                # `child` IS the create_task response and already carries `identifier`
                # (measured on live 2.3.0 — a project with no prefix yields `#<index>`,
                # byte-identical to a read-back), so this value was on hand and thrown
                # away. SKILL.md forbids an agent to BUILD a ref: the per-project index
                # follows from nothing about the global id, so a composed one does not
                # look broken — it points at an unrelated LIVE card. Without this key the
                # rulebook's own advice was a `get_task` per child, or a guess.
                created.append(
                    {"id": child["id"], "ref": self._ref(child), "title": child["title"]}
                )
                self.api.add_relation(child["id"], task_id, "parenttask")
                self._move(child["id"], "Queue")
            # ordered chain (option C, epic #94): link adjacent children so each precedes the
            # next, in ARRAY ORDER — child[i] `precedes` child[i+1]. Vikunja auto-creates the
            # inverse `follows` on the SUCCESSOR (empirically verified on real 2.3.0), which is
            # exactly the kind the sequence gate reads (PREDECESSOR_RELATION_KINDS). So the head
            # keeps only an outgoing `precedes` (no follows -> claimable now) while every later
            # child gains `follows`→its predecessor the instant the chain is built (gated until
            # that predecessor reaches Review). The direction is load-bearing: a flipped chain
            # would gate the head and free the tail — the exact silent corruption to prevent.
            # Kept INSIDE the try so a chaining failure (children already exist) is surfaced by
            # the same partial-failure handler, never blind-retried. range(len(created) - 1) is a
            # no-op for 0/1 children. No cycle detection — a linear chain is acyclic by
            # construction (that's #105, deliberately out of scope).
            if ordered:
                for i in range(len(created) - 1):
                    self.api.add_relation(created[i]["id"], created[i + 1]["id"], "precedes")
        except (VikunjaError, httpx.HTTPError) as exc:
            if not created:
                raise  # nothing landed on the board yet — the bare error is safe to retry
            listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
            raise WorkflowError(
                f"decompose failed after creating {len(created)} of {len(subtasks)} "
                f"subtask(s) ({exc}). Already on the board: {listing}. Do NOT blindly "
                f"retry — you would duplicate these; delete them first, or re-run "
                f"decompose for the remaining subtasks only."
            ) from exc

        listing = ", ".join(f"#{c['id']} {c['title']}" for c in created)
        comment = f"[decompose] создано: {listing}"
        if ordered:
            comment += " (упорядочено: цепочка precedes — клеймабельна только голова)"
        self.api.add_comment(task_id, comment)
        # #673: a card that BECOMES A CONTAINER carries no verdict. `advance` already clears both
        # mutually-exclusive verdict labels on both of its forms — #119's ruling, in ITS OWN
        # words, is that "a resubmission into the active pipeline invalidates any prior verdict"
        # — and decompose is the same kind of resumption, the work simply
        # moves into the children; it just cleared nothing, so the parent kept whatever label it
        # arrived with. Measured through the real Workflow over a FakeAPI board, along the exact
        # route #663's refusal recommends (a reviewer is refused from Review -> review_task(
        # verdict='needs_work') -> the owner decomposes from Build): the parent landed in Backlog
        # carrying `epic` AND `review-failed` at once. The other verdict reaches it too — an
        # APPROVED card a human hand-pulled back to Build gave `epic` AND `reviewed` — which is why
        # both go, via the SAME helper `advance` uses rather than a second spelling of the rule.
        # And the label here is not merely stale, it is INAPPLICABLE. A card is offered for
        # independent review in exactly two places — the push nudge at the end of `advance` and
        # `next_task`'s pull path — and LABEL_EPIC is skipped by BOTH, so nothing in the pipeline
        # ever routes a reviewer to a container and the normal flow can never refresh that
        # verdict. (Not "can never be refreshed at all": `review_task` gates on stage alone, so a
        # reviewer handed the id by hand still lands a verdict on an epic. Measured, not assumed.)
        # PARENT only. FOUR calls above touch a child — create_task, the `parenttask` relation,
        # the move to Queue, and the `ordered` `precedes` chain — and not one of them takes a
        # label argument, so no decompose path can put a verdict on a child and there is nothing
        # on them to clear. Placed with the
        # other PARENT mutations instead of before the children: they are grouped here, and
        # clearing earlier would invent a half-applied state (verdict gone, never became an epic).
        self._clear_verdict_labels(task)
        label = self.api.get_or_create_label(LABEL_EPIC)
        self.api.add_label(task_id, label["id"])
        self.api.remove_assignee(task_id, self._me()["id"])
        self._move(task_id, "Backlog")
        result = {
            "created": created,
            "parent": {"id": task_id, "moved_to": "Backlog", "labeled": LABEL_EPIC},
        }
        if ordered:
            result["ordered"] = True
            result["note"] = (
                "children are chained head→tail (precedes/follows); only the head is claimable "
                "now — each successor unlocks when its predecessor reaches Review"
            )
        return result

    def file_task(
        self, title: str, description: str = "", priority: int = 0,
        related_task_id: int | None = None, project_id: int | None = None,
        queue: bool = False,
    ) -> dict:
        """File a finding (a bug/tech-debt OUTSIDE the current task) into Backlog for
        human triage — NOT into Queue (a human prioritizes). Optionally: a 'related'
        relation to the task it was found during. No ownership required — this is a new
        card, not an edit of your task (unlike decompose). project_id (agent-to-agent
        coordination): file into ANOTHER project's Backlog; the target board is resolved
        BEFORE the card is created (fail-fast — no orphan in its default bucket), the
        token's access to the target is Vikunja's call (403 -> clear refusal), and the
        marker names the SOURCE project so the target's humans see provenance. None (or
        the own project id) keeps today's behavior bit-for-bit. queue=True (#249) is the
        explicit human-asked opt-in: the card lands in the OWN project's Queue instead —
        unassigned, so immediately claimable (next_task / the hub's `claimable` poll see
        it) — because the human's instruction to create the work IS the triage. It is
        deliberately OWN-PROJECT-ONLY: injecting ready-for-pickup work into ANOTHER
        project's Queue would bypass that project's human (and wake their fleet loop
        with work nobody there sanctioned), so queue+cross is refused before anything
        is created. The result's filed.ref (#735) is the card's human-searchable name —
        echo it VERBATIM, never reconstruct one from the id."""
        if not (title or "").strip():
            raise WorkflowError("a non-empty title is required for the new task")
        target = self.project_id if project_id is None else int(project_id)
        cross = target != self.project_id
        if queue and cross:
            raise WorkflowError(
                "queue=True can't be combined with a cross-project project_id: filing "
                "into ANOTHER project is Backlog-only — that project's human triages "
                "their own board, an agent must not inject ready-for-pickup work into "
                "someone else's Queue. Drop queue to file into their Backlog, or ask "
                "via call_human. Nothing was created."
            )
        if cross and target <= 0:
            raise WorkflowError(
                f"project_id must be a positive Vikunja project id, got {target} "
                f"(negative ids are Vikunja pseudo-projects like favorites)"
            )
        # кросс: резолвим доску ЦЕЛИ до create_task (fail-fast, см. _target_backlog);
        # свой проект: порядок сегодняшний (create -> _move), байт-в-байт.
        coords = self._target_backlog(target) if cross else None
        created = self.api.create_task(
            target, title.strip(),
            description=(description or "").strip(), priority=int(priority or 0),
        )
        new_id = created["id"]
        # явно в Backlog/Queue: не полагаемся на то, что default-бакет проекта == Backlog
        stage = "Queue" if queue else "Backlog"
        if cross:
            view_id, bucket_id = coords
            self.api.move_task(target, view_id, bucket_id, new_id)
        else:
            self._move(new_id, stage)
        if related_task_id is not None:
            self.api.add_relation(new_id, related_task_id, "related")
        if cross:
            # provenance: люди ЦЕЛЕВОГО проекта должны видеть, откуда пришла карточка
            marker = (
                f"[filed-by-agent] заведено агентом из проекта id={self.project_id} "
                f"для триажа человеком"
            )
        elif queue:
            # честный провенанс: триаж Backlog пропущен — по явной просьбе человека
            marker = "[filed-by-agent] заведено агентом сразу в Queue (минуя триаж в Backlog)"
        else:
            marker = "[filed-by-agent] заведено агентом для триажа человеком"
        if related_task_id is not None:
            marker += f" (по ходу работы над #{related_task_id})"
        self.api.add_comment(new_id, marker)
        # `ref` (#735): the human-searchable name of the card THIS tool just created. The tools
        # that HAND BACK a task already carry one (_summary for next_task/claim, get_task), so an
        # agent told by SKILL.md to echo a ref, having only `filed.id`, had to invent the half no
        # tool gave it — and #660 shipped exactly that: "Filed as VMCP-181 (732)", where 732 is
        # really VMCP-195 and VMCP-181 is a LIVE unrelated card (id 706). A fabricated identifier
        # resolves to plausibly the WRONG card, which is worse than a broken link: it takes the
        # reader somewhere. Note the scope: this closes file_task, NOT the class — `decompose`
        # creates cards too and still records its children as {id, title}, measured by running it
        # and by reading every historical version of the line that records a child (introduced by
        # f6508ac, unchanged since; no `git log -S` spelling is quoted for it, because a command
        # written INTO the file it interrogates changes its own answer — this comment would be a
        # new match). So #735's own description was wrong to list decompose among the tools that
        # already return a ref; that half is filed as VMCP-206 (749), and until it lands a child's
        # ref costs a get_task, which SKILL.md says rather than implying otherwise.
        # It costs ZERO extra requests, and that is measured twice, not assumed: real 2.3.0
        # returns `identifier` in the PUT /projects/{id}/tasks response itself ('PRB-1'; '#1' for
        # a project with no prefix — byte-identical to the read-back), and a hooked call
        # inventory of a live file_task shows NO GET of the new card in either branch. So _ref is
        # a pure format over the dict `create_task` already returned — which is also why "the
        # token may not see the card it just filed" cannot arise here: nothing is re-read.
        # CROSS-PROJECT: the identifier is computed by the TARGET's board, so the ref carries
        # THEIR prefix (measured live: 'TGT-1 (5)' filed from a project prefixed OWN) — that
        # prefix is what makes the card findable on the board it actually lives on, and the note
        # says so out loud.
        # What `ref` still does NOT cover, pre-existing and deliberately not widened here: the
        # result is assembled LAST, so a failure between create and here (a scope gap on the
        # move, the relation or the marker) raises with the card already on the board and hands
        # back neither id nor ref. `decompose` takes the other choice a few hundred lines up —
        # it records each child the instant it exists, BEFORE its relation and move — and the
        # asymmetry is worth knowing about rather than assuming away.
        result = {
            "filed": {
                "id": new_id, "ref": self._ref(created),
                "title": created["title"], "stage": stage,
            },
            "note": (
                "in Queue, unassigned — immediately claimable (Backlog triage bypassed; "
                "queue=True is only for tasks a human explicitly asked to file as work)"
                if queue
                else "in Backlog for human triage (not Queue — a human prioritizes)"
            ),
        }
        if cross:
            result["filed"]["project_id"] = target
            result["note"] = (
                f"filed into project {target}'s Backlog for THAT project's human to "
                f"triage (not Queue — a human prioritizes). The card lives on the TARGET "
                f"board: your other tools (get_task/comment/next_task) are bound to your "
                f"own project and won't see it — the 'related' link is the cross-reference. "
                f"`ref` carries the TARGET project's identifier prefix, not yours: that is "
                f"the name their humans search by, so echo it verbatim"
            )
        if related_task_id is not None:
            result["related_to"] = related_task_id
        return result

    def comment(self, task_id: int, text: str) -> dict:
        if not (text or "").strip():
            raise WorkflowError("an empty comment is not needed")
        self._find_task(task_id)
        self.api.add_comment(task_id, text.strip())
        return {"commented": task_id}

    def get_task(self, task_id: int) -> dict:
        """Full dossier: unlike _summary (next_task/claim), the description is NOT
        truncated and related is added — a compact dict {relation_kind: [{"id", "title"}, ...]}.
        attachments lists each file's METADATA only ({id, name, mime, size}) — no bytes, so a
        card that is nothing but a screenshot is SEEN, not guessed at; fetch the bytes with
        download_attachment(task_id, attachment_id) using the `id` here."""
        _, stage = self._find_task(task_id)
        task = self.api.get_task(task_id)
        raw_comments = self.api.comments(task_id)
        related_raw = task.get("related_tasks") or {}
        related = {
            kind: [{"id": rt["id"], "title": rt["title"]} for rt in items]
            for kind, items in related_raw.items()
        }
        # attachments come INSIDE the task JSON (tasks:read_one, no extra scope), each
        # {id, task_id, file:{name,mime,size}}; the server sends None (not []) when there are
        # none. Surface METADATA ONLY — the bytes would bloat every dossier (the point is the
        # agent SEES "shot.png (image/png)" and chooses whether to download_attachment it). `id`
        # is the attachment id download_attachment keys off (NOT file.id), so it is load-bearing.
        attachments = [
            {
                "id": a.get("id"),
                "name": (a.get("file") or {}).get("name"),
                "mime": (a.get("file") or {}).get("mime"),
                "size": (a.get("file") or {}).get("size"),
            }
            for a in task.get("attachments") or []
        ]
        return {
            "id": task["id"],
            "ref": self._ref(task),
            "title": task["title"],
            "priority": task.get("priority", 0),
            "description": task.get("description") or "",
            "stage": stage,
            "assignees": [a.get("username", "?") for a in task.get("assignees") or []],
            "labels": [lb.get("title") for lb in task.get("labels") or []],
            "related": related,
            "attachments": attachments,
            # comments are stored as HTML (#85); render back to plain text so the agent
            # reads clean multiline text (the human reads the formatted HTML in the UI).
            "comments": [
                {"author": c.get("author", {}).get("username", "?"),
                 "text": html_to_text(c.get("comment", ""))}
                for c in raw_comments
            ],
        }

    def download_attachment(self, task_id: int, attachment_id: int) -> dict:
        """Download a task attachment's bytes to a TEMP FILE and return its path (an agent then
        Reads the path — a PNG/JPG renders visually — instead of a base64 blob that bloats the
        context). `attachment_id` is the id from get_task's attachments[] (NOT the filename).
        Fails in agent-actionable ways: a wrong/absent id lists the task's real attachments; an
        oversized file (metadata size > cap) is refused BEFORE downloading, naming the size."""
        self._find_task(task_id)  # same board membership check as get_task/comment
        task = self.api.get_task(task_id)
        attachments = task.get("attachments") or []
        match = next((a for a in attachments if a.get("id") == attachment_id), None)
        if match is None:
            available = ", ".join(
                f"#{a.get('id')} {(a.get('file') or {}).get('name')}" for a in attachments
            ) or "none"
            raise WorkflowError(
                f"task {task_id} has no attachment #{attachment_id} — its attachments are: "
                f"{available}. Use the `id` from get_task's attachments[]"
            )
        file_meta = match.get("file") or {}
        name = file_meta.get("name") or f"attachment-{attachment_id}"
        # size cap read from METADATA, BEFORE downloading — so a runaway file fails fast and
        # actionably instead of pulling GBs into a temp file / the agent's context.
        size = file_meta.get("size")
        if isinstance(size, int) and size > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"attachment #{attachment_id} ({name}) is {size} bytes — over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte download cap. Fetch it directly from the tracker "
                f"UI instead of pulling it into the agent context"
            )
        data = self.api.download_attachment(task_id, attachment_id)
        # Second-line cap: the metadata pre-check above is cheap but can under-report (or be
        # missing/0 — a legit 0-byte file is fine, so we do NOT refuse on that). len(data) is the
        # real bound, so re-check the bytes we actually pulled before writing them to a temp file.
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"attachment #{attachment_id} ({name}) downloaded as {len(data)} bytes — over the "
                f"{_MAX_ATTACHMENT_BYTES}-byte cap; its metadata under-reported the size. Fetch it "
                f"directly from the tracker UI instead of pulling it into the agent context"
            )
        path = _write_attachment_to_temp(name, data, fallback=f"attachment-{attachment_id}")
        return {
            "path": path,
            "name": name,
            "mime": file_meta.get("mime"),
            "size": len(data),
            "note": (
                "Read this path to view the file — an image (PNG/JPG) renders visually, a "
                "text/PDF opens as text. It sits in a temp dir and is cleaned up automatically; "
                "Read it now rather than saving the path for later"
            ),
        }

    def attach_file(self, task_id: int, path: str, note: str | None = None) -> dict:
        """Upload a LOCAL file — typically a SCREENSHOT of finished, visually-verifiable work — as
        an attachment on the task, so a human and the independent reviewer SEE the result instead
        of taking 'done' on faith. The UPLOAD twin of download_attachment; deliberately a STANDALONE
        tool, NOT an argument to advance: a failed upload is its own actionable error, never a
        half-finished stage transition (the #118/#134/#135 lesson — keep cross-cutting side effects
        out of advance), and both the implementer (own task) and the reviewer (a task in Review)
        can attach. No ownership is required (same as download_attachment) — only board membership.

        Every successful upload also JOURNALS itself (#184): an `[attach] <name> (<mime>, <size>)`
        comment — plus the optional `note` (one line on WHAT the file shows, e.g. «доска после
        reconcile») — lands in the task's comment stream through the same add_comment chokepoint
        as every other marker, so a human reading the comments sees «вот здесь бот приложил четыре
        скрина» in the story itself, not just rows in the attachments widget. Deliberately a plain-
        text marker, no deep-link/embed: comment bodies are HTML-ESCAPED (text_to_html, #85), so an
        <img>/<a> would render as literal text — the filename is the honest reference. The journal
        comment is posted AFTER the upload landed, and its own failure never fails the tool: an
        {"error": ...} result would read as 'the attach failed' and provoke a blind re-upload (a
        duplicate attachment); instead the result carries journal_comment=False plus an actionable
        note (don't re-upload; post a comment() manually if the trace matters).

        Validated BEFORE any bytes hit the wire: `path` must resolve (realpath, so a symlink to a
        real file is followed) to an existing REGULAR file — a symlink to a dir/socket, a missing
        path, or a directory is refused with an actionable message — within the _MAX_ATTACHMENT_BYTES
        cap (checked via getsize, so a runaway file fails fast, never loaded). The path is NOT
        confined to the workspace: screenshots routinely land in a temp/Downloads dir outside the
        repo (a browser tool, an OS screenshot), so confining it would break the primary use case;
        the size cap + regular-file check are the guardrails. The basename becomes the attachment
        name (never the full path) and the MIME is guessed from the extension. Needs the
        tasks_attachments:create token scope — a 401 means the token is read-only for attachments
        and a human must add the `create` op (verified on real 2.3.0: create governs the upload)."""
        self._find_task(task_id)  # same board-membership check as comment/download_attachment
        try:
            real = os.path.realpath(path)
        except ValueError as exc:
            raise WorkflowError(
                f"can't attach {path!r}: invalid path ({exc}) — a path can't contain a NUL byte; "
                f"pass a real local screenshot/render path"
            ) from exc
        if not os.path.isfile(real):
            raise WorkflowError(
                f"no file to attach at {path!r} — it doesn't exist or isn't a regular file. "
                f"Pass the path to a screenshot/render you already produced while verifying the "
                f"work (a directory, a broken symlink, or a missing path is refused here)"
            )
        # getsize→open is a TOCTOU window: the file can be removed/replaced/made unreadable after
        # the isfile guard, so an OSError from either becomes an actionable WorkflowError, never a
        # raw traceback. The oversized WorkflowError raised BETWEEN them is not an OSError, so it
        # propagates cleanly past this handler.
        try:
            size = os.path.getsize(real)
            if size > _MAX_ATTACHMENT_BYTES:
                raise WorkflowError(
                    f"{path} is {size} bytes — over the {_MAX_ATTACHMENT_BYTES}-byte upload cap. "
                    f"Attach a screenshot/thumbnail, not a large asset or a runtime artifact"
                )
            with open(real, "rb") as fh:
                data = fh.read()
        except OSError as exc:
            raise WorkflowError(
                f"the file at {path!r} could not be read ({exc}) — it may have been removed, "
                f"replaced, or made unreadable after the size check; re-produce it and retry"
            ) from exc
        # Second-line cap mirroring download_attachment: getsize is a cheap pre-check but can lie
        # (the file grew between stat and read), so len(data) is the real bound and the honest
        # uploaded length reported below.
        if len(data) > _MAX_ATTACHMENT_BYTES:
            raise WorkflowError(
                f"{path} read as {len(data)} bytes — over the {_MAX_ATTACHMENT_BYTES}-byte upload "
                f"cap (its size grew after the pre-check). Attach a screenshot/thumbnail, not a "
                f"large asset or a runtime artifact"
            )
        name = _safe_attachment_name(os.path.basename(real), fallback=f"attachment-{task_id}")
        mime, _ = mimetypes.guess_type(name)
        resp = self.api.upload_attachment(task_id, name, data, mime=mime)
        created = (resp or {}).get("success") or []
        new_id = created[0].get("id") if created and isinstance(created[0], dict) else None
        # журнальный след аплоада (#184): человек листает ЛЕНТУ КОММЕНТОВ, а не виджет файлов —
        # без следа «бот приложил скрин» в истории задачи невидимо. mime может быть None
        # (неизвестное расширение) — тогда в скобках только размер.
        meta = f"{mime}, {_human_size(len(data))}" if mime else _human_size(len(data))
        journal = f"[attach] {name} ({meta})"
        if (note or "").strip():
            journal += f" — {note.strip()}"
        journal_failure: str | None = None
        try:
            self.api.add_comment(task_id, journal)
        except (VikunjaError, httpx.HTTPError) as exc:
            # файл УЖЕ на карточке — ошибка коммента не имеет права выглядеть как ошибка
            # загрузки (слепой повтор = дубль вложения); деградируем в journal_comment=False
            # с подсказкой, что делать. Ловим только API/сетевые ошибки — программные пусть падают.
            journal_failure = str(exc)
        return {
            "attached": True,
            "task_id": task_id,
            "attachment_id": new_id,
            "name": name,
            "mime": mime,
            "size": len(data),
            "journal_comment": journal_failure is None,
            "note": (
                "the file is on the card and journaled as an [attach] comment in the task's "
                "comment stream — don't post a separate comment about the upload. For a "
                "visually-verifiable change, cite it in your advance(to='review') worklog as "
                "evidence alongside the commit sha"
            ) if journal_failure is None else (
                f"the file IS on the card, but posting the [attach] journal comment failed "
                f"({journal_failure}) — do NOT re-upload (that would duplicate the attachment); "
                f"if the journal trace matters, post a brief comment() naming the file"
            ),
        }
