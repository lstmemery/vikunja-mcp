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


# VMCP-92: the request ceiling of a board read the server's own stated page size does not justify
# — see the long note above `_page_size`. DERIVED, not picked: `workspace_cmd._READ_DEADLINE_
# SECONDS` (30 s) is the budget a human already decided a WHOLE tracker read may take, and the
# VMCP-72 comment MEASURED the healthy read against the real tracker at four requests in
# 0.89-1.10 s, i.e. ~0.25 s/request. 30 / 0.25 = 120, so the callers with NO deadline
# (next_task/claim/advance/setup) get, in requests, the containment `--gc` already has in seconds —
# and machine-independently, since it comes from a configured budget and a measured rate rather
# than from a loopback page count.
#
# VMCP-103 renamed it from `_UNKNOWN_PAGE_SIZE_MAX_PAGES`: it is no longer the DEGRADED branch's
# ceiling, it is every read's, counted over the pages `max_items_per_page` did NOT account for. An
# unknown page size justifies nothing, so a degraded read spends the budget page by page exactly
# as before; a healthy read spends it only on pages where no required bucket came back full, which
# is why an honest 8 000-task board still reads whole in 161 requests.
_MAX_UNPROVEN_PAGES = 120


def _could_be_full(stated: int | None, longest_served: int) -> int:
    """"Could a page of this length still be full?" — the threshold, and the ONE expression this
    repo has now got wrong three times (VMCP-89, VMCP-92, VMCP-103, each time in a branch the
    previous card had not touched). It lives here, module level, called from BOTH paginating
    readers — `view_tasks` (nested board) and `_paged_list` (flat lists) — so the next correction
    lands in both by construction rather than by whoever edits remembering the other one exists.
    That drift is not hypothetical: VMCP-103 was entirely the story of the DEGRADED branch being
    fixed twice while the HEALTHY branch kept the deleted rule and silently truncated.

    `stated` = max_items_per_page as reported by /info, or None when the server never told us.
    `longest_served` = the longest page this read has actually seen, which is a PROVEN lower bound
    on the server's real page size (it served that many at once) — evidence, never a guess.

    A page counts as possibly-full if it is full by EITHER measure, which is what makes this one
    rule a superset of the two it replaced. Crucially `longest_served` starts at 0, so the FIRST
    page of any read is always inconclusive: a page short of the stated size proves nothing
    (MEASURED in VMCP-103 — page1=[1,2] with max_items_per_page=5 and 3..9 still behind it), so
    "the first page was short" may never end a read. That is where the one unavoidable extra
    request per read comes from, and it is the price of not truncating."""
    return longest_served if stated is None else min(stated, longest_served)


def _total_pages(headers: Any) -> int | None:
    """`x-pagination-total-pages` as an int, or None when the server did not send a usable one.

    NEVER a stop signal — only ever an extra reason to KEEP READING (see `_paged_list`). Measured
    on one and the same Vikunja 2.3.0 it is wrong in BOTH directions: VMCP-103 measured it
    UNDER-reporting on the kanban tasks endpoint (result-count 3 / total-pages 1 while the bucket
    behind it held 3 pages of tasks), and VMCP-108 measured it OVER-reporting on
    /projects/{id}/views and .../buckets (total-pages 2 and 3 while every page served the whole
    list). Used only to keep going, an over-report costs a request and an under-report costs
    nothing; used to stop, an under-report costs DATA."""
    try:
        n = int(headers.get("x-pagination-total-pages", ""))
    except (AttributeError, TypeError, ValueError):
        return None
    return n if n > 0 else None


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
        raw: bool = False, files: Any = None, with_headers: bool = False,
    ) -> Any:
        # with_headers (VMCP-108): return `(body, response.headers)` instead of just the body.
        # ONE caller — `_paged_list`, which needs `x-pagination-total-pages` and must not be a
        # second request path to get it: the retry/backoff rules above (429 retries any method,
        # 5xx retries only idempotent ones) are exactly the kind of thing that stops matching
        # when it exists twice, which is the drift this card is the fourth instance of.
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
            body = r.content if raw else (r.json() if r.content else None)
            return (body, r.headers) if with_headers else body
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

    # --- postранично читаемые ПЛОСКИЕ списки (VMCP-108) ---
    #
    # THE FOURTH MEMBER OF THE 543/548/562 FAMILY: A TRUNCATED READ TAKEN AS COMPLETENESS. Every
    # list GET in this client except `view_tasks` used to be a SINGLE request, and the server
    # paginates them. MEASURED against a real Vikunja 2.3.0 with max_items_per_page=5, 8-11 rows
    # behind each endpoint:
    #     GET /projects                      -> 5 rows, ?page=2 -> 4 MORE      PAGINATES
    #     GET /labels                        -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /tasks/{id}/comments           -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /projects/{id}/users           -> 5 rows, ?page=2 -> 3 MORE      PAGINATES
    #     GET /projects/{id}/views           -> ALL 10 rows, ?page= IGNORED
    #     GET /projects/{id}/views/{v}/buckets -> ALL 11 rows, ?page= IGNORED
    #
    # The four that paginate all fail in the WORSE direction — absence, which every caller acts
    # on. MEASURED end to end: `setup_cmd.reconcile` looks a project up by title over
    # `projects()`, so an EXISTING project past the window reads as missing and reconcile CREATES
    # A DUPLICATE PROJECT (existing id 8 'p6' ignored, new id 18 created — the card's harm, and
    # not something an agent can undo on a real tracker). The other three are the same shape:
    # `get_or_create_label` mints a SECOND label beside the canonical one (the incident its own
    # docstring records, now reachable without anyone typing "Bug " at all); `share_project`
    # re-PUTs a user who is already shared and gets 409 "This user already has access", which
    # ABORTS `setup`; and `comments()` is the worst of them because page 1 holds the OLDEST rows,
    # so a short read drops the NEWEST — the reviewer's `[review]` verdict, the `[worklog]`
    # next_task's review offering keys off (no worklog visible => the card is never offered for
    # review at all), and the human's answer on a card coming back from Your Call.
    #
    # `views()`/`buckets()` are routed through here TOO, though 2.3.0 demonstrably serves them
    # whole. "This endpoint does not paginate in the version I measured" is precisely the
    # assumption that rots — and it rots silently, into duplicate BUCKETS out of the same
    # reconcile and a "project has no kanban view" on a project that has one. The cost is one
    # bounded extra request (they answer ?page=2 with the same rows, so the read stops on
    # `added_new` after exactly 2), on two reads that `Workflow` caches per instance anyway. The
    # uniform rule — every list GET in this client pages — is worth more than the request: it is
    # what stops the next reader having to re-derive which endpoints are safe.
    #
    # THE STOP RULE IS VMCP-103's, REUSED RATHER THAN RE-DERIVED (`_could_be_full` above is
    # literally the same function `view_tasks` calls). Keep reading while the page ADDED SOMETHING
    # NEW **and** (it could still be full by min(size STATED by /info, longest page SERVED) **or**
    # `x-pagination-total-pages` says another page exists). Each conjunct earns its place:
    #   * `added_new` is what TERMINATES the loop, and it is the only thing that can: the row set
    #     is finite, so a read that must add a row per page cannot outrun it. It is also what
    #     makes a `?page=`-ignoring endpoint cost 2 requests instead of looping on its own repeats
    #     (measured: views/buckets stop there, with no duplicate rows).
    #   * the fullness half is the inference rule, and it is what survives the header being WRONG
    #     — which VMCP-103 measured it being, in the UNDER-reporting direction, on this same
    #     server.
    #   * the header is a KEEP-GOING signal and NEVER a stop. It is the one thing this endpoint
    #     family offers that the kanban tasks endpoint did not, and it catches exactly the shape
    #     the fullness rule cannot: a SHORT non-final page (562's bug) with more behind it. Used
    #     this way an over-reported total-pages costs a request and an under-reported one costs
    #     nothing; used as a stop, an under-report would cost DATA. It is never trusted, only
    #     believed when it says "there is more".
    #
    # NOT chosen: paging authoritatively by `x-pagination-total-pages` (what the card suggested,
    # on the strength of the header being meaningful for /projects). It is meaningful for
    # /projects — and on the SAME server it over-reports for views/buckets and under-reports for
    # the kanban tasks endpoint, so "this header is authoritative" is a per-endpoint fact that
    # nothing in the client can check. A stop rule that is right on the endpoints someone measured
    # and silently lossy on the rest is the bug this card is about, one level up.
    #
    # And as in `view_tasks`, hitting the ceiling RAISES rather than returning what it has. A
    # truncated list is indistinguishable from rows that are genuinely gone, and absence is what
    # the callers act on.
    def _paged_list(self, path: str, params: dict | None = None) -> list:
        page_size = self._page_size()   # None = the server never told us; see VMCP-89
        merged: list = []
        seen: set = set()
        longest_page = 0                # PROVEN lower bound on the page size — see _could_be_full
        unproven_pages = 0              # pages `max_items_per_page` did NOT account for
        page = 1
        while True:
            if unproven_pages >= _MAX_UNPROVEN_PAGES:
                stated = (
                    "This client could not read max_items_per_page from /info, so it cannot tell "
                    "a full page from a short one at all."
                    if page_size is None else
                    f"/info states max_items_per_page={page_size}, but no page ever reached it, "
                    f"and a page SHORT of the stated size is no proof the list is exhausted "
                    f"(VMCP-103) — so the stated size bounds nothing here."
                )
                raise VikunjaError(508, (
                    f"the list at {path} never finished paging: {_MAX_UNPROVEN_PAGES} requests "
                    f"that the server's own page size did not account for, and it was STILL "
                    f"adding rows. {stated} NOTHING is returned rather than a partial list: a "
                    f"truncated list is indistinguishable from rows that are genuinely gone, and "
                    f"this client's callers act on ABSENCE — `setup` creates a duplicate project "
                    f"it could not see, get_or_create_label mints a duplicate label, and a short "
                    f"comment read hides the newest report on a card (VMCP-108). Fix /info so it "
                    f"reports max_items_per_page — pages that FILL it cost this budget nothing — "
                    f"or fix the endpoint's `?page=` if it never converges."
                ))
            body, headers = self._req(
                "GET", path, params={**(params or {}), "page": page}, with_headers=True
            )
            # A 200 whose body is not a list is read as NO ROWS, not as an error, because that is
            # how an empty list actually arrives: `_req` returns None for an empty body, and a Go
            # nil slice marshals to `null` (`view_tasks` normalizes the same way, `... or []`).
            # The normalization is load-bearing, not defensive — MEASURED with `items = body`: a
            # page `{"message": ...}` is truthy, so the loop below walks the dict's KEYS and merges
            # the string "message" into the result as a row (VMCP-116).
            items = body if isinstance(body, list) else []
            # AN EARLY-OUT, AND ONLY THAT — it does not outrank `x-pagination-total-pages`, and
            # nothing at this line could: the header reaches the stop rule at the bottom only ANDed
            # with `added_new`, so a page that brings no NEW row ends the read whatever the header
            # claims, and an empty page brings none. MEASURED (VMCP-116, real httpx over the card's
            # exact shape — page1=5 rows, page2=[], page3=5 rows, every response stating 3 pages):
            # delete these two lines and the answer is unchanged, 5 rows in 2 requests, whole unit
            # suite green. The card's own mutation (`not items and not header_more`) is inert for
            # that same reason. The rule this is a fast path for is broader than "empty" anyway —
            # a page of pure REPEATS adds nothing either, and that is what stops a `?page=`-
            # ignoring endpoint after 2 requests.
            #
            # KEPT DELIBERATELY, as the flat twin of view_tasks' choice (VMCP-103's
            # test_a_page_filtered_down_to_nothing_still_ends_the_read): an all-filtered window and
            # an exhausted list are the same observation, and offset pagination over a stable list
            # makes the empty page the NORMAL terminating shape. ONE half of the board's reasoning
            # does NOT carry over — "keep paging through empties has no bound at all" — since here
            # the header would bound it. What replaces it is worse than an absent bound: that bound
            # would belong to the SERVER, and this is the header `_total_pages` documents as
            # measured wrong in BOTH directions on this very version. `added_new` is the only stop
            # that comes from the DATA (the row set is finite, so a read that must add a row per
            # page cannot outrun it). MEASURED on the views/buckets shape 2.3.0 really serves —
            # whole list every page, `?page=` ignored, total-pages OVER-reported — a header allowed
            # to carry the read past a page that added nothing runs to the server's own page count:
            # 41+ requests where this reader spends 2, with the unproven-page ceiling never firing
            # because every page is FULL. That is VMCP-116's option (b), and it is refused as a
            # design: it would mean relaxing THAT conjunct and charging empty pages to
            # `unproven_pages` before this break — never a change to this line.
            if not items:
                break
            # snapshotted BEFORE this page is folded into `longest_page`: a page may not be used
            # as evidence that it is itself too short to continue from (same ordering as
            # view_tasks, where it also keeps the answer independent of bucket order).
            could_be_full = _could_be_full(page_size, longest_page)
            added_new = False
            for item in items:
                # every list endpoint here returns objects with an `id`; the repr fallback only
                # has to make a REPEATED row compare equal to itself, so that a `?page=`-ignoring
                # server still terminates on `added_new` rather than looping on its own echo.
                key = item["id"] if isinstance(item, dict) and "id" in item else repr(item)
                if key in seen:
                    continue
                seen.add(key)
                merged.append(item)     # first-seen order kept: comments are read positionally
                added_new = True
            maybe_full = len(items) >= could_be_full
            longest_page = max(longest_page, len(items))
            if page_size is None or len(items) < page_size:
                unproven_pages += 1     # not justified by the rate the server advertised
            total_pages = _total_pages(headers)
            header_more = total_pages is not None and page < total_pages
            if not (added_new and (maybe_full or header_more)):
                break
            page += 1
        return merged

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
        # PAGED (VMCP-108): page 1 holds the OLDEST comments, so a single-request read dropped
        # the NEWEST — the [review] verdict, the [worklog] next_task's review offering requires,
        # and a human's answer to call_human. Order across pages is preserved.
        return self._paged_list(f"/tasks/{task_id}/comments")

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
        # PAGED (VMCP-108): the card's own endpoint. A single request stopped at
        # max_items_per_page, so an EXISTING project past the window read as absent and
        # setup_cmd.reconcile created a DUPLICATE.
        return [p for p in self._paged_list("/projects") if p.get("id", 0) > 0]

    def create_project(self, title: str) -> dict:
        return self._req("PUT", "/projects", json={"title": title})

    def project_users(self, project_id: int) -> list[dict]:
        # PAGED (VMCP-108): share_project's "is this user already shared?" check reads this, and
        # a share hidden past page 1 makes it re-PUT — Vikunja answers 409 "This user already
        # has access to this project", which aborts `setup` outright.
        return self._paged_list(f"/projects/{project_id}/users")

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
        # PAGED (VMCP-108) although 2.3.0 serves this whole and ignores ?page= — see _paged_list
        # for why measured-not-to-paginate is not a licence to single-shot it. A short read here
        # would surface as `kanban_view` raising "project has no kanban view" on a project that
        # has one, i.e. as a board that cannot be reconciled.
        return self._paged_list(f"/projects/{project_id}/views")

    def kanban_view(self, project_id: int) -> dict:
        for v in self.views(project_id):
            if v["view_kind"] == "kanban":
                return v
        raise VikunjaError(404, "project has no kanban view — run `vikunja-mcp setup`")

    def buckets(self, project_id: int, view_id: int) -> list[dict]:
        # PAGED (VMCP-108) although 2.3.0 serves this whole and ignores ?page= — same reasoning
        # as views(). A short read here is the duplicate-project harm one level down: reconcile
        # builds {title: bucket} from this and CREATES every canonical column it cannot see.
        return self._paged_list(f"/projects/{project_id}/views/{view_id}/buckets")

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
    # So this branch now (a) issues at most `_MAX_UNPROVEN_PAGES` requests and (b) on
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
    #
    # VMCP-103 — AND THAT WAS ONLY HALF THE JOB: THE DEGRADED READ WAS LEFT STRICTLY MORE ROBUST
    # THAN THE HEALTHY ONE. The two rules were never symmetric — "len >= S" IMPLIES "len >= L", so
    # the degraded rule kept reading everywhere the known rule did AND in shapes it did not.
    # VMCP-89/92 spent two cards deleting "a short page proves the bucket is complete" from the
    # degraded branch and left it standing on the healthy one, where `saw_full_page` took a page
    # SHORT of the STATED size as proof of exhaustion. MEASURED (real httpx, real api.py): /info
    # stating max_items_per_page=5 and one required Build serving page1=[1,2] (short by accident),
    # page2=[3,4,5,6,7], page3=[8,9] — the HEALTHY read stopped after ONE request with Build[1,2],
    # silently losing 3..9, while the DEGRADED read spent 4 requests and returned Build[1..9]
    # whole. Backwards, and invisible to both parity sweeps, which only ever modelled honest
    # servers whose pages are full until the last one.
    #
    # So there is ONE stop rule now and the branch is gone. "Could this page still be full?" is
    # answered against `min(size STATED, longest page SERVED)` — full by EITHER measure — which
    # makes the surviving rule a superset of both old ones by construction. That it had to be the
    # DEGRADED rule is forced, not preferred: to stop being the weaker of the two, the known rule
    # has to IMPLY the degraded one, and the weakest rule implying it is that rule itself. Every
    # cheaper variant leaves some shape where /info being DOWN reads more of the board than /info
    # being healthy. (Measured on the same server: keeping only the fullness half, without "a new
    # required task arrived", returns Build[1..7] — still short by two.)
    #
    # THE COST IS REAL AND IS NOT HIDDEN: one extra request per `view_tasks` call whenever a
    # required bucket brought a NEW task on the last page that had content — the smallest board
    # goes from 1 request to 2 (~0.25 s at the rate measured below). It is unavoidable rather than
    # sloppy: a short page and a filtered page are the same observation, and only asking for one
    # more page tells them apart. It is bounded and FLAT — one page per READ, not per bucket — and
    # it is not paid at all when the required buckets come back EMPTY, which is what keeps #43's
    # require_titles win intact (an exhausted Queue beside an unbounded Done still stops at page 1).
    #
    # AND THE STATED PAGE SIZE STILL EARNS ITS KEEP — AS A BUDGET, NOT AS AN ORACLE. Nothing
    # bounds `added_new_required` on its own, so the ceiling VMCP-92 gave the degraded read now
    # covers both, counted over the pages the STATED size did NOT justify: a page on which some
    # required bucket came back full at `max_items_per_page` is the server delivering at the rate
    # it advertised, and it costs nothing. An honest 8 000-task board therefore still reads whole
    # in 161 requests (161 justified pages), while a server that never fills a page is cut off at
    # `_MAX_UNPROVEN_PAGES` — and cut off by RAISING, never by returning the short board.
    #
    # NOT chosen: (a) `x-pagination-total-pages` as a free stop signal — MEASURED against a real
    # Vikunja 2.3.0: on this endpoint those headers describe the BUCKET list (result-count 3,
    # total-pages 1) while the bucket behind them held 3 pages of tasks, so the header is not
    # merely useless here, it is WRONG. (b) A `strict=` flag so that only `--gc` pays the extra
    # page: that moves the asymmetry from "/info up vs down" to "caller remembered vs forgot", and
    # `_find_task`/claim read a truncated board as "no such task" on a card that is right there.
    # (c) The flat ceiling on both branches: an honest 8 000-task board would become an error, and
    # `require_titles=None` (claim/setup) makes an ever-growing Done a required bucket. (d) A
    # progress bound (pages <= ceil(seen/page_size)+K): it would break exactly the
    # filter-after-paginate shape it exists to survive, filtering being slow delivery relative to
    # the window.
    #
    # HONEST ABOUT THE TRIGGER: a short non-final page could NOT be produced against Vikunja 2.3.0.
    # Request-level `filter=`, a saved filter on the view, `s=` search, and done tasks auto-moving
    # into the Done bucket all push the filter into SQL, so every page comes back full until the
    # last (probed on a container with max_items_per_page=5). The mechanism the card suspected —
    # paginate the unfiltered set, filter afterwards — is NOT reproduced here, which is not the
    # same as proven impossible (a proxy, a later version, or a Typesense-backed search hydrating
    # ids from the DB would all have that shape). What does not depend on settling that is the
    # asymmetry itself: the healthy path must not be the fragile one.
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
                                            # real size. VMCP-103: used on EVERY read, capped by the
                                            # stated size when there is one.
        unproven_pages = 0                  # VMCP-103: pages already spent that `max_items_per_page`
                                            # did NOT account for — the only ones the ceiling counts.
        page = 1
        while True:
            if unproven_pages >= _MAX_UNPROVEN_PAGES:
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
                stated = (
                    "This client could not read max_items_per_page from /info, so it cannot tell "
                    "a full page from a short one at all."
                    if page_size is None else
                    f"/info states max_items_per_page={page_size}, but no page of a required "
                    f"bucket ever reached it, and a page SHORT of the stated size is no proof "
                    f"that bucket is exhausted (VMCP-103) — so the stated size bounds nothing "
                    f"here."
                )
                raise VikunjaError(508, (
                    f"the board never finished paging: {_MAX_UNPROVEN_PAGES} requests to "
                    f"/projects/{project_id}/views/{view_id}/tasks that the server's own page "
                    f"size did not account for, and it was STILL adding tasks. {stated} NOTHING "
                    f"is returned rather than a partial board: a truncated board is "
                    f"indistinguishable from tasks that are genuinely gone, and `workspace --gc` "
                    f"reaps worktrees from exactly this read (VMCP-89). Fix /info so it reports "
                    f"max_items_per_page — pages that FILL it cost this budget nothing — or fix "
                    f"the view's pagination if it is `?page=` that never converges."
                ))
            buckets = self._req(
                "GET", f"/projects/{project_id}/views/{view_id}/tasks", params={"page": page}
            ) or []
            if not buckets:
                break
            stated_full_required = False
            maybe_full_required = False
            added_new = False
            added_new_required = False
            # The "could this page still be full?" threshold, snapshotted BEFORE the page so the
            # answer cannot depend on the ORDER the server listed its buckets in. It is the longest
            # page the server has PROVEN it can serve, capped by the size it STATED — i.e. a page
            # counts as possibly-full if it is full by EITHER measure, which is what makes this one
            # rule a superset of the two it replaced (VMCP-103).
            # VMCP-108: the expression moved to module level and is now SHARED with `_paged_list`
            # — the rule that drifted across VMCP-89/92/103 exists once, not once per reader.
            could_be_full = _could_be_full(page_size, longest_page)
            for bucket in buckets:
                bid = bucket["id"]
                dest = merged.setdefault(bid, {**bucket, "tasks": []})
                ids = seen.setdefault(bid, set())
                tasks = bucket.get("tasks") or []
                required = require_titles is None or bucket.get("title") in require_titles
                if required and tasks:
                    if len(tasks) >= could_be_full:
                        maybe_full_required = True  # can't rule out a full page -> can't call it done
                    if page_size is not None and len(tasks) >= page_size:
                        stated_full_required = True     # the server delivered at its OWN stated rate
                longest_page = max(longest_page, len(tasks))
                for task in tasks:
                    owner[task["id"]] = bid          # последнее вхождение выигрывает (см. дедуп ниже)
                    if task["id"] not in ids:
                        ids.add(task["id"])
                        dest["tasks"].append(task)
                        added_new = True
                        added_new_required = added_new_required or required
            # A short page proves NOTHING about a bucket being exhausted — whatever /info said —
            # so the loop stops on facts about the DATA: it keeps going while a page brought a NEW
            # task to a REQUIRED bucket (required-only on purpose: counting any bucket would let an
            # unbounded Done/Backlog drag the loop through itself, the very cost #43's
            # require_titles exists to avoid), and while a required bucket returned a page that
            # could still be full (VMCP-92's repeat-window edge, which "nothing new" alone misses).
            # ONE rule for both branches since VMCP-103 — see the long note above `_page_size` for
            # why the healthy path had to adopt the degraded one rather than the other way round.
            keep_going = added_new_required or (maybe_full_required and added_new)
            if not stated_full_required:
                unproven_pages += 1         # this page was not justified by max_items_per_page
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
        # PAGED (VMCP-108): get_or_create_label scans this to REUSE an existing label. A label
        # hidden past page 1 reads as absent and gets minted a second time — the duplicate its
        # docstring below records as a real incident, reachable here without any typo at all.
        return self._paged_list("/labels")

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
