"""Vikunja REST client. Gotchas baked in: PUT=create, POST=full-replace update -> RMW."""
import time
from typing import Any

import httpx

from .formatting import text_to_html


class VikunjaError(Exception):
    def __init__(self, status: int, message: str):
        self.status = status
        self.message = message
        super().__init__(f"Vikunja API {status}: {message}")


# VMCP-92: the request ceiling of the DEGRADED (unknown page size) read — see the long note above
# `_page_size`. DERIVED, not picked: `workspace_cmd._READ_DEADLINE_SECONDS` (30 s) is the budget a
# human already decided a WHOLE tracker read may take, and the VMCP-72 comment MEASURED the healthy
# read against the real tracker at four requests in 0.89-1.10 s, i.e. ~0.25 s/request. 30 / 0.25 =
# 120, so the callers with NO deadline (next_task/claim/advance/setup) get, in requests, the
# containment `--gc` already has in seconds — and machine-independently, since it comes from a
# configured budget and a measured rate rather than from a loopback page count.
_UNKNOWN_PAGE_SIZE_MAX_PAGES = 120


def canonical_base_url(base_url: str) -> str:
    """Canonicalize a Vikunja base URL — the SINGLE normalization shared by the client (which builds
    requests from it) and the 401 repoint guard in server.py (which compares a reloaded config's url
    against the running session's). Kept as one function so the two can NEVER drift apart (tracker
    #154: they had — the guard compared the RAW url while the client normalized it, so a cosmetic-only
    difference read as a mid-session host change and refused a healthy token rotation, inverting #148).

    Folds ONLY what is the same endpoint by definition, and keeps every genuine change:
      * strips a trailing slash — cosmetic;
      * lowercases the scheme and the authority (host[:port]) — the RFC-3986 case-insensitive parts;
        httpx folds these the same way when it builds a request, so routing the client through this
        leaves its observable behaviour identical (the existing api tests pass untouched);
      * ensures the `/api/v1` suffix.
    It deliberately does NOT touch the scheme VALUE (http vs https — a plaintext downgrade is REAL),
    the host, the port, or the path (all case-sensitive): a rotation moving any of those is a genuine
    repoint the guard must still refuse."""
    prefix, sep, rest = base_url.partition("://")
    if sep:
        authority, slash, path = rest.partition("/")
        base = f"{prefix.lower()}{sep}{authority.lower()}{slash}{path}"
    else:
        base = base_url
    base = base.rstrip("/")
    if not base.endswith("/api/v1"):
        base += "/api/v1"
    return base


class VikunjaAPI:
    def __init__(
        self, base_url: str, token: str, client: httpx.Client | None = None,
        *, timeout: float = 30, max_retries: int | None = None,
        event_hooks: dict | None = None,
    ):
        """`timeout`/`max_retries`/`event_hooks` exist for ONE caller: `workspace --gc`, whose
        board read happens while it holds the repo-wide worktree flock (see
        workspace_cmd._build_workflow). Everything else keeps the defaults. All three are ignored
        when `client` is supplied — the caller then owns the whole client (tests pass a
        MockTransport one, and a test that wants a hook builds it into its own client).

        `event_hooks` is httpx's own {"request": [...], "response": [...]} mapping. It is here
        rather than assembled by the caller so that gc's client is still built by THIS
        constructor: duplicating the base-url canonicalisation and the Authorization header at a
        second call site is how one of them silently stops matching the other."""
        self._client = client or httpx.Client(
            base_url=canonical_base_url(base_url),
            headers={"Authorization": f"Bearer {token}"},
            timeout=timeout,
            event_hooks=event_hooks,
        )
        if max_retries is not None:
            # an INSTANCE attribute shadowing the class default below, for this client only
            self._MAX_RETRIES = max_retries
        # resolved ONCE per client, and the flag is separate from the value because "unknown" is
        # now a real answer (None) rather than a guess — see _page_size / VMCP-89.
        self._page_size_cache: int | None = None
        self._page_size_resolved = False

    # --- транзиентные ретраи (#86 «восстановление работы на ошибках апи») ---
    # Раньше _req падал с ПЕРВОЙ же 429/5xx/обрыва связи, и работа агента вставала на
    # ровном месте. Ретраим с backoff, но безопасно к семантике PUT=create/POST=replace:
    #   - 429: сервер ОТКЛОНИЛ запрос ДО применения -> ретраим ЛЮБОЙ метод (чтим Retry-After);
    #   - 5xx и обрыв/таймаут связи: исход неоднозначен (могло примениться) -> ретраим только
    #     идемпотентные GET и POST (POST = полная перезапись, повтор даёт то же состояние).
    #     PUT (create) и DELETE на этих ошибках НЕ ретраим — иначе дубль или ложная 404.
    # Постоянные ошибки (4xx кроме 429) поднимаются сразу, как и прежде.
    _MAX_RETRIES = 3
    _RETRY_STATUSES = frozenset({500, 502, 503, 504})
    _IDEMPOTENT_METHODS = frozenset({"GET", "POST"})
    _BACKOFF_BASE = 0.5
    _BACKOFF_CAP = 8.0

    def _req(
        self, method: str, path: str, json: Any = None, params: dict | None = None,
        raw: bool = False, files: Any = None,
    ) -> Any:
        # files (#137): a MULTIPART form upload (e.g. attach a screenshot) — httpx encodes it as
        # multipart/form-data instead of a JSON body, so it and `json` are mutually exclusive (the
        # upload path always passes json=None). Callers pass file CONTENT as bytes, not a file
        # handle, so a 429 retry below re-encodes the SAME body cleanly (a consumed stream would
        # re-send empty). Only PUT uploads use it, and PUT=create is not retried on 5xx (no dup).
        method = method.upper()
        for attempt in range(self._MAX_RETRIES + 1):
            final = attempt == self._MAX_RETRIES
            try:
                r = self._client.request(method, path, json=json, params=params, files=files)
            except httpx.TransportError:
                # обрыв/таймаут: могло примениться -> ретраим только идемпотентные методы
                if final or method not in self._IDEMPOTENT_METHODS:
                    raise
                time.sleep(self._backoff(attempt))
                continue
            if not final and self._should_retry(method, r.status_code):
                time.sleep(self._backoff(attempt, r.headers.get("Retry-After")))
                continue
            if r.status_code >= 400:
                raise VikunjaError(r.status_code, r.text[:300])
            # raw=True: тело — НЕ JSON (эндпоинт скачивания вложения отдаёт сырые байты
            # файла с content-type/content-disposition), поэтому возвращаем r.content как
            # есть, минуя r.json() (который бы упал на бинарнике). См. download_attachment.
            if raw:
                return r.content
            return r.json() if r.content else None
        raise AssertionError("unreachable: последняя попытка всегда вернёт или поднимет")

    def _should_retry(self, method: str, status: int) -> bool:
        if status == 429:
            return True  # отклонён до применения — безопасно ретраить любой метод
        return status in self._RETRY_STATUSES and method in self._IDEMPOTENT_METHODS

    def _backoff(self, attempt: int, retry_after: str | None = None) -> float:
        if retry_after:
            try:
                return min(float(retry_after), self._BACKOFF_CAP)
            except ValueError:
                pass
        return min(self._BACKOFF_BASE * (2**attempt), self._BACKOFF_CAP)

    # --- identity ---
    def me(self) -> dict:
        return self._req("GET", "/user")

    # --- tasks ---
    def get_task(self, task_id: int) -> dict:
        return self._req("GET", f"/tasks/{task_id}")

    def update_task(self, task_id: int, **fields: Any) -> dict:
        current = self.get_task(task_id)
        current.update(fields)
        return self._req("POST", f"/tasks/{task_id}", json=current)

    def create_task(
        self, project_id: int, title: str, description: str = "", priority: int = 0
    ) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/tasks",
            json={"title": title, "description": description, "priority": priority},
        )

    # --- attachments ---
    # Task attachments arrive INSIDE the task JSON under the existing tasks:read_one scope
    # (task["attachments"] = [{id, task_id, file:{id,name,mime,size}, ...}], or None when the
    # task has none — verified on real 2.3.0), so listing metadata needs no extra call. Only
    # DOWNLOADING the bytes hits a separate endpoint and needs the tasks_attachments:read scope.
    def download_attachment(self, task_id: int, attachment_id: int) -> bytes:
        """Raw bytes of a task attachment. `attachment_id` is the attachment's OWN id
        (task["attachments"][].id, surfaced by workflow.get_task), NOT the nested file.id.
        GET /tasks/{id}/attachments/{attachment_id} streams the file itself, not JSON, so it
        goes through _req(raw=True) — same GET retry/backoff, but the body is returned
        verbatim. Needs the tasks_attachments:read token scope; a wrong task or attachment id
        surfaces as VikunjaError(404)."""
        return self._req("GET", f"/tasks/{task_id}/attachments/{attachment_id}", raw=True)

    def upload_attachment(
        self, task_id: int, filename: str, data: bytes, mime: str | None = None
    ) -> dict:
        """Upload bytes as a task attachment (e.g. a screenshot of finished work). The endpoint is
        PUT /tasks/{id}/attachments and takes a MULTIPART form — file field `files` — NOT a JSON
        body, so it goes through _req(files=...): the upload-side twin of download_attachment's
        raw=True on the response side (api.py's JSON helpers don't fit either end). Verified on
        real 2.3.0: the governing scope is `tasks_attachments:create` (401 without it), the method
        is PUT (POST -> 405), and the response is
        {"errors": ..., "success": [{id, task_id, file:{id,name,mime,size,...}, ...}]}. `data` is
        bytes (not a stream) so a 429 retry re-encodes the same body; PUT=create is not retried on
        5xx, so an ambiguous failure can't duplicate the upload."""
        file_part = (filename, data, mime) if mime else (filename, data)
        return self._req("PUT", f"/tasks/{task_id}/attachments", files={"files": file_part})

    # --- comments ---
    def comments(self, task_id: int) -> list[dict]:
        return self._req("GET", f"/tasks/{task_id}/comments") or []

    def add_comment(self, task_id: int, text: str) -> dict:
        # Vikunja's comment field is HTML (#85): agents author plain text with newlines,
        # so convert to structure-preserving, HTML-escaped HTML at this single chokepoint
        # — every agent comment body (comment/spec/worklog/review/call_human/claim/...)
        # passes through here.
        return self._req(
            "PUT", f"/tasks/{task_id}/comments", json={"comment": text_to_html(text)}
        )

    # --- assignees ---
    def add_assignee(self, task_id: int, user_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/assignees", json={"user_id": user_id})

    def remove_assignee(self, task_id: int, user_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/assignees/{user_id}")

    # --- relations ---
    def add_relation(self, task_id: int, other_task_id: int, kind: str) -> None:
        self._req(
            "PUT", f"/tasks/{task_id}/relations",
            json={"other_task_id": other_task_id, "relation_kind": kind},
        )

    # --- projects ---
    def projects(self) -> list[dict]:
        return [p for p in (self._req("GET", "/projects") or []) if p.get("id", 0) > 0]

    def create_project(self, title: str) -> dict:
        return self._req("PUT", "/projects", json={"title": title})

    def project_users(self, project_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/users") or []

    def share_project(self, project_id: int, username: str, permission: int) -> None:
        for share in self.project_users(project_id):
            if share.get("username") == username:
                return
        self._req(
            "PUT", f"/projects/{project_id}/users",
            json={"username": username, "permission": permission},
        )

    # --- views & buckets ---
    def views(self, project_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/views") or []

    def kanban_view(self, project_id: int) -> dict:
        for v in self.views(project_id):
            if v["view_kind"] == "kanban":
                return v
        raise VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")

    def buckets(self, project_id: int, view_id: int) -> list[dict]:
        return self._req("GET", f"/projects/{project_id}/views/{view_id}/buckets") or []

    def create_bucket(self, project_id: int, view_id: int, title: str) -> dict:
        return self._req(
            "PUT", f"/projects/{project_id}/views/{view_id}/buckets", json={"title": title}
        )

    def delete_bucket(self, project_id: int, view_id: int, bucket_id: int) -> None:
        self._req("DELETE", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}")

    def update_bucket(
        self, project_id: int, view_id: int, bucket: dict, position: float
    ) -> dict:
        # full-replace бакета: шлём title + position, порядок колонок = position
        return self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket['id']}",
            json={"title": bucket["title"], "position": position},
        )

    # эмпирически против vikunja 2.3.0 (см. отчёт F1): GET .../views/{v}/tasks пагинирует
    # tasks[] ВНУТРИ каждого бакета независимо через params={"page": n} с фиксированным
    # page size = max_items_per_page сервера (per_page на эту вложенную пагинацию не влияет).
    # Порог «полной страницы» читаем из /info (_page_size, кэш на клиенте).
    # Страницы могут перекрываться на 1-2 задачи из-за нестабильной сортировки при равных
    # ключах (без ORDER BY тайбрейкера) — наблюдался дубль, ни разу не пропуск. Мёржим по
    # (bucket_id, task_id), останавливаемся когда ни один бакет не отдал полную страницу
    # (значит дальше для всех пусто) ИЛИ страница не принесла ни одной новой задачи (защита
    # от зацикливания на нестабильной сортировке).
    #
    # VMCP-89 — THE PAGE SIZE IS KNOWN OR UNKNOWN, NEVER GUESSED, and that is a data-loss fix.
    # `_fetch_page_size` used to swallow an unreachable or silent `/info` and return a hardcoded
    # 50. On an instance whose real max_items_per_page is SMALLER, no bucket ever returns 50
    # tasks, so "no bucket gave a full page" read as "that is the whole board" and this loop
    # TRUNCATED after page 1 — no exception, no marker, just fewer tasks. `workspace --gc` builds
    # its liveness set from exactly this board and destroys the worktree of every task missing
    # from it. CONSTRUCTED (real git worktrees, real httpx, real api.py, max_items_per_page=3, a
    # 500 on /info, five live Build tasks): `released=[804, 805]` — two LIVE trees removed and
    # their `task/*` branches deleted, in a sweep that reported success. gc is the caller that
    # loses work, but it is not the only victim: claim/_find_task read a truncated board as "no
    # such task" on a card that is right there.
    #
    # So the guess is gone. `_page_size()` answers None when the server never told us — EITHER
    # way it can fail to (the request errored, or the payload carries no usable
    # max_items_per_page) — and an UNKNOWN size simply forbids concluding a bucket is complete
    # from a SHORT page: the loop then keeps going until a page brings no NEW task in the required
    # buckets, which is a fact about the DATA and needs no page size at all. It costs exactly one
    # extra request, only while /info is broken. A KNOWN size keeps the cheap fullness rule
    # unchanged, so the healthy path pays nothing (this includes #43's require_titles win). Both
    # outcomes are resolved ONCE per client (`_page_size_resolved`), so a broken /info does not
    # add a probe per call either.
    #
    # NOT chosen: making the failure fail-CLOSED (propagate, so gc abandons the sweep — the shape
    # VMCP-72 used for its read deadline). It keeps gc at KEEP, but it leaves the identical
    # truncation live for next_task/claim/setup, and it would disable housekeeping FOREVER on a
    # deployment whose /info simply does not report the field. Not guessing is strictly stronger
    # than refusing to act on a guess: the read stays CORRECT instead of merely being abandoned.
    #
    # VMCP-92 — THE DEGRADED BRANCH IS BOUNDED, AND IT RAISES RATHER THAN RETURNING A SHORT BOARD.
    # VMCP-89 left one residual on this branch only. A KNOWN page size bounds the request count at
    # ceil(N/page_size)+1 whatever the server does; an UNKNOWN one had NO bound — the loop ran for
    # as long as any page brought a new required task. CONSTRUCTED (real httpx, real api.py): a
    # server handing out one brand-new Build task per page never terminated (401 requests and
    # still going when the harness cut it off). The contained caller was fine — `--gc` abandons
    # this read on VMCP-72's 30 s deadline, and `ReadDeadlineExceeded` is a WorkspaceError so
    # `_fetch_page_size` cannot swallow it — but next_task/claim/advance/setup have no deadline
    # and would hang forever.
    #
    # So this branch now (a) issues at most `_UNKNOWN_PAGE_SIZE_MAX_PAGES` requests and (b) on
    # hitting that ceiling RAISES, returning nothing. Raising is the whole point, not a detail: a
    # truncated board is indistinguishable from one whose tasks are genuinely gone, and that
    # indistinguishability is what ends in `--gc` reaping a LIVE worktree. Every caller's failure
    # direction here is KEEP or no-op — gc propagates it out of `_read_liveness` before the reap
    # loop is ever entered, `server._tool` turns it into `{"error": ...}` instead of a hung tool
    # call, `setup` refuses a reconcile it cannot base on a complete board.
    #
    # AND THE STOP RULE STOPPED CONCLUDING COMPLETENESS FROM A REPEAT (the second half of VMCP-92).
    # "A page brought no new required task" was the ONLY stop here, which is strictly weaker than
    # the known-size rule in one shape: a required bucket that re-serves a FULL window of
    # already-seen tasks while some other bucket still adds new ones. MEASURED on this exact
    # server: a known size read on and got Build[1..6], an unknown size stopped at Build[1,2,3] —
    # the same silent truncation VMCP-89 exists to remove. The loop now also continues while a
    # required bucket returns a page AT LEAST AS LONG AS THE LONGEST PAGE THE SERVER HAS SERVED,
    # which is the known-size fullness rule with the page size the server STATED replaced by the
    # longest page it actually SERVED. That length is a PROVEN lower bound on the page size (the
    # server served it, so its page size is at least that) — evidence, never a guess, and the
    # difference from the hardcoded 50 VMCP-89 deleted. Because that bound L <= the real page size
    # S, "len >= S" implies "len >= L": everything the KNOWN branch would keep reading the UNKNOWN
    # branch keeps reading too, so the degraded read can never truncate where the healthy one does
    # not. Verified over 60 randomized boards (required buckets identical, non-required a superset
    # — the degraded read costs one extra page and that page carries extra Done/Backlog tasks).
    # A server that ignores `?page=` entirely still stops after two requests, exactly as before:
    # its repeat brings nothing new ANYWHERE, and this clause needs a new task somewhere to run on.
    def _page_size(self) -> int | None:
        if not self._page_size_resolved:
            self._page_size_cache = self._fetch_page_size()
            self._page_size_resolved = True
        return self._page_size_cache

    def _fetch_page_size(self) -> int | None:
        # /info — публичный, неаутентифицированный эндпоинт; Bearer на нём безвреден.
        # Ошибку по-прежнему ГЛОТАЕМ (у этого резолвера есть вызыватели, которым падение
        # из-за /info было бы хуже деградации) — но отдаём None «не знаю», а не число-догадку:
        # решает не молчание, а то, что с None делает view_tasks (см. комментарий выше).
        try:
            info = self._req("GET", "/info")
        except (VikunjaError, httpx.HTTPError):
            return None
        size = info.get("max_items_per_page") if isinstance(info, dict) else None
        return size if isinstance(size, int) and size > 0 else None

    def view_tasks(
        self, project_id: int, view_id: int, require_titles: set[str] | None = None
    ) -> list[dict]:
        # require_titles (#43): the set of bucket TITLES whose "full page" should keep the
        # pagination loop going. None (default) = every bucket counts -> exhaustive read, kept
        # for _find_task/claim/setup which must see the complete board (incl. a Done task).
        # When given, only those buckets drive paging: an unbounded Done/Backlog that still
        # returns full pages no longer forces extra fetches once the required buckets are
        # exhausted. next_task passes its working stages here so it stops after them instead of
        # rescanning the ever-growing Done on every call (the named next_task-latency fix).
        page_size = self._page_size()       # None = the server never told us; see VMCP-89 above
        merged: dict[int, dict] = {}
        seen: dict[int, set] = {}
        owner: dict[int, int] = {}          # task_id -> последний бакет, где её видели (см. дедуп ниже)
        longest_page = 0                    # VMCP-92: the longest page ANY bucket has actually
                                            # returned = a PROVEN lower bound on the server's page
                                            # size (it served that many at once). Over ALL buckets
                                            # because the page size is one server-wide setting, and
                                            # a HIGHER proven bound is a TIGHTER "could still be
                                            # full" test — never a looser one, since it stays <= the
                                            # real size. Only used when that size is unknown.
        page = 1
        while True:
            if page_size is None and page > _UNKNOWN_PAGE_SIZE_MAX_PAGES:
                # VMCP-92: NOT sent, and NOTHING returned — see the note above `_page_size`. The
                # message has to be self-explaining: it is the only thing a human gets, and the
                # thing it names (/info) is the thing that actually needs fixing.
                #
                # A plain VikunjaError, not a new class: it is what every caller ALREADY handles
                # (`server._tool` -> `{"error": ...}`, the workspace CLI's error line, claimable's
                # exit 1), and a new class is one more thing for an `except` site to miss. The 508
                # (Loop Detected — the server's own paging is what fails to converge) is
                # synthesized the way `kanban_view` synthesizes its 404; it collides with nothing,
                # since the only status-sensitive sites are 403/404 in file_task and 401's token
                # reload, and being raised HERE rather than by `_req` it is never retried.
                raise VikunjaError(508, (
                    f"the board never finished paging: "
                    f"{_UNKNOWN_PAGE_SIZE_MAX_PAGES} requests to /projects/{project_id}/views/"
                    f"{view_id}/tasks and the server was STILL adding tasks. This client could "
                    f"not read max_items_per_page from /info, so it cannot tell a full page from "
                    f"a short one and has no page-count bound of its own. NOTHING is returned "
                    f"rather than a partial board: a truncated board is indistinguishable from "
                    f"tasks that are genuinely gone, and `workspace --gc` reaps worktrees from "
                    f"exactly this read (VMCP-89). Fix /info so it reports max_items_per_page — "
                    f"then this read is bounded by the page size again — or fix the view's "
                    f"pagination if /info is healthy and it is `?page=` that never converges."
                ))
            buckets = self._req(
                "GET", f"/projects/{project_id}/views/{view_id}/tasks", params={"page": page}
            ) or []
            if not buckets:
                break
            saw_full_page = False
            maybe_full_required = False
            added_new = False
            added_new_required = False
            proven_size = longest_page      # snapshot: the could-be-full comparison must not
                                            # depend on the ORDER the server listed its buckets in
            for bucket in buckets:
                bid = bucket["id"]
                dest = merged.setdefault(bid, {**bucket, "tasks": []})
                ids = seen.setdefault(bid, set())
                tasks = bucket.get("tasks") or []
                required = require_titles is None or bucket.get("title") in require_titles
                if page_size is not None and len(tasks) >= page_size and required:
                    saw_full_page = True
                if page_size is None and required and tasks and len(tasks) >= proven_size:
                    maybe_full_required = True   # can't rule out a full page -> can't call it done
                longest_page = max(longest_page, len(tasks))
                for task in tasks:
                    owner[task["id"]] = bid          # последнее вхождение выигрывает (см. дедуп ниже)
                    if task["id"] not in ids:
                        ids.add(task["id"])
                        dest["tasks"].append(task)
                        added_new = True
                        added_new_required = added_new_required or required
            # UNKNOWN page size: a short page proves nothing, so a page that brought a NEW task to
            # a REQUIRED bucket keeps the loop going (required-only on purpose — counting any
            # bucket would let an unbounded Done/Backlog drag the loop through itself, the very
            # cost #43's require_titles exists to avoid), and so does the known-size fullness rule
            # read against the longest page the server has PROVEN it can serve (VMCP-92).
            if page_size is None:
                keep_going = added_new_required or (maybe_full_required and added_new)
            else:
                keep_going = saw_full_page and added_new
            if not keep_going:
                break
            page += 1
        # #41 глобальный дедуп по task id: задачу, переезжающую между колонками ВО ВРЕМЯ
        # постраничного чтения, мы видим в старом бакете на ранней странице и в новом — на поздней,
        # т.е. дважды. Покомпонентный (bucket_id, task_id) merge выше оба вхождения сохранял, и
        # _find_task (берёт первое) залипал на устаревшей колонке. Оставляем задачу ТОЛЬКО в её
        # последнем бакете: страницы читаются последовательно во времени, поздняя = более свежее
        # наблюдение доски, куда бы задачу ни двигали. После этого прохода каждый task id встречается
        # ровно один раз, поэтому дедуп и _find_task (первое вхождение) согласованы по определению.
        for bid, dest in merged.items():
            dest["tasks"] = [t for t in dest["tasks"] if owner.get(t["id"]) == bid]
        return list(merged.values())

    def move_task(self, project_id: int, view_id: int, bucket_id: int, task_id: int) -> None:
        self._req(
            "POST", f"/projects/{project_id}/views/{view_id}/buckets/{bucket_id}/tasks",
            json={"task_id": task_id},
        )

    def configure_kanban(
        self, project_id: int, view: dict, default_bucket_id: int, done_bucket_id: int
    ) -> dict:
        # full-replace: без mode+position канбан теряет колонки
        return self._req(
            "POST", f"/projects/{project_id}/views/{view['id']}",
            json={
                "title": view["title"],
                "view_kind": "kanban",
                "bucket_configuration_mode": "manual",
                "position": view["position"] if view.get("position") is not None else 400,
                "default_bucket_id": default_bucket_id,
                "done_bucket_id": done_bucket_id,
            },
        )

    # --- labels ---
    def labels(self) -> list[dict]:
        return self._req("GET", "/labels") or []

    def create_label(self, title: str) -> dict:
        return self._req("PUT", "/labels", json={"title": title})

    def add_label(self, task_id: int, label_id: int) -> None:
        self._req("PUT", f"/tasks/{task_id}/labels", json={"label_id": label_id})

    def remove_label(self, task_id: int, label_id: int) -> None:
        self._req("DELETE", f"/tasks/{task_id}/labels/{label_id}")

    def get_or_create_label(self, title: str) -> dict:
        # Vikunja labels are owned per-user; GET /labels surfaces every label used on a
        # task the caller can read (not just its own), so match case- and whitespace-
        # insensitively to REUSE an existing label instead of minting a divergent
        # duplicate. Without this an agent typing "Bug"/"bug " forks a second, colorless
        # label beside the canonical one (real incident 2026-07-08: a bot did exactly that).
        want = title.strip().casefold()
        for label in self.labels():
            if (label.get("title") or "").strip().casefold() == want:
                return label
        return self.create_label(title)
