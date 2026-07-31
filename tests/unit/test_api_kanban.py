import json
import random

import httpx
import pytest

from tests.unit.test_api import make_api
from vikunja_mcp.api import _MAX_UNPROVEN_PAGES as MAX_UNPROVEN_PAGES
from vikunja_mcp.api import VikunjaError


def test_projects_filters_pseudo():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": -1, "title": "Favorites"}, {"id": 3, "title": "hgdev-infra"},
        ])

    api = make_api(handler)
    assert [p["id"] for p in api.projects()] == [3]


def test_kanban_view_picks_kanban_kind():
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 10, "view_kind": "list"}, {"id": 11, "view_kind": "kanban", "title": "Kanban"},
        ])

    api = make_api(handler)
    assert api.kanban_view(3)["id"] == 11


def test_kanban_view_missing_raises_actionable_error():
    """Гоча: голый next() на пустом генераторе роняет StopIteration — бесполезная ошибка."""
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 10, "view_kind": "list"}, {"id": 12, "view_kind": "table"},
        ])

    api = make_api(handler)
    with pytest.raises(VikunjaError, match="kanban view"):
        api.kanban_view(3)


def test_view_tasks_merges_paginated_buckets():
    """F1: GET .../views/{v}/tasks пагинирует tasks[] ВНУТРИ бакета через page= (наблюдалось
    эмпирически против vikunja 2.3.0: фиксированный page size 50, как max_items_per_page из
    /info; per_page на эту вложенную пагинацию не влияет). Без мёржа страниц next_task/
    _find_task слепнут после первых 50 задач в бакете."""
    calls = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        if page == 1:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(1, 51)]     # полная страница
        elif page == 2:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(51, 61)]    # хвост, 10 < 50
        else:
            tasks = []
        return httpx.Response(200, json=[{"id": 4, "title": "Queue", "tasks": tasks}])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    # VMCP-103: the SHORT page 2 no longer ends the read — a page shorter than max_items_per_page
    # is not proof the bucket is exhausted, so page 3 is asked for and its "nothing new" is what
    # stops the loop. That one confirming request is the whole cost of the fix.
    assert calls == [1, 2, 3]
    assert len(board) == 1
    ids = [t["id"] for t in board[0]["tasks"]]
    assert sorted(ids) == list(range(1, 61))       # все 60 смёржены, ничего не потеряно


def test_view_tasks_dedupes_overlap_between_pages():
    """Наблюдалось эмпирически: нестабильная сортировка отдаёт одну и ту же задачу на двух
    страницах подряд — мёрж обязан схлопнуть дубликат по id (а не завести вторую копию),
    но НЕ ценой потери новых задач, которые пришли на той же странице рядом с повтором."""
    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(1, 51)]
        elif page == 2:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(41, 61)]    # 41-50 повтор + 51-60 новые
        else:
            tasks = []
        return httpx.Response(200, json=[{"id": 4, "title": "Queue", "tasks": tasks}])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    ids = [t["id"] for t in board[0]["tasks"]]
    assert sorted(ids) == list(range(1, 61))       # обе страницы смёржены
    assert len(ids) == len(set(ids))                # без дублей 41-50


def test_view_tasks_dedupes_moved_task_globally_keeping_last_bucket():
    """#41: задачу двигают между колонками ВО ВРЕМЯ постраничного чтения — она приходит в старом
    бакете на page 1 и в новом на page 2, попадая в снапшот дважды. Покомпонентный дедуп по
    (bucket_id, task_id) обе копии сохранял, и _find_task залипал на первой (устаревшей) колонке.
    Глобальный дедуп по task id оставляет ровно одно вхождение — в ПОСЛЕДНЕМ виденном бакете
    (поздняя страница = более свежее наблюдение доски)."""
    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 2})
        page = int(request.url.params.get("page", "1"))
        if page == 1:
            queue = [{"id": 1, "title": "t1"}, {"id": 7, "title": "t7"}]   # 2 = полная → тянем page 2
            build = []
        elif page == 2:
            queue = []                                                      # задача 7 уехала из Queue…
            build = [{"id": 7, "title": "t7"}]                             # …в Build (свежая колонка)
        else:
            queue = build = []
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": queue},
            {"id": 5, "title": "Build", "tasks": build},
        ])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    all_ids = [tid for ids in by_title.values() for tid in ids]
    assert all_ids.count(7) == 1                     # ровно одно вхождение глобально, а не два
    assert by_title["Build"] == [7]                  # выжила последняя (свежая) колонка
    assert by_title["Queue"] == [1]                  # устаревшая колонка потеряла 7, но не 1


def test_view_tasks_independent_buckets_stop_separately():
    """Один бакет с полной страницей, другой уже исчерпан на page=1 — обязаны дойти до
    исчерпания бОльшего бакета, не потеряв меньший и не зациклившись на пустом."""
    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        big = [{"id": i, "title": f"t{i}"} for i in range(1, 51)] if page == 1 else (
            [{"id": i, "title": f"t{i}"} for i in range(51, 56)] if page == 2 else []
        )
        small = [{"id": 900, "title": "solo"}] if page == 1 else []
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": big},
            {"id": 5, "title": "Doing", "tasks": small},
        ])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert sorted(by_title["Queue"]) == list(range(1, 56))
    assert by_title["Doing"] == [900]


def test_view_tasks_require_titles_skips_unbounded_bucket():
    """#43 (next_task latency): view_tasks(require_titles=...) names which bucket TITLES' full
    pages drive pagination. An unbounded Done bucket that keeps returning full pages must NOT
    keep the loop going once the REQUIRED buckets are exhausted — so next_task stops after its
    working stages instead of rescanning the whole ever-growing Done on every call.

    VMCP-103 rebalanced WHERE that stop lands, and this is the honest picture of it: Queue's own
    page 1 was short, which is no longer proof Queue is done, so ONE confirming page is spent —
    and Done rides along on it. What #43 exists to prevent still holds and is what this pins:
    Done keeps offering full pages for six of them and the loop takes exactly one, then stops the
    moment Queue comes back empty. Bounded by the REQUIRED buckets, not by Done's length."""
    calls = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        queue = [{"id": 1, "title": "q"}] if page == 1 else []            # exhausted on page 1
        done = ([{"id": 100 * page + i, "title": f"d{i}"} for i in range(50)]   # unbounded: 6 pages
                if page <= 6 else [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": queue},
            {"id": 9, "title": "Done", "tasks": done},
        ])

    api = make_api(handler)
    board = api.view_tasks(3, 11, require_titles={"Queue"})
    assert calls == [1, 2]                           # one confirming page, NOT a walk through Done
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert by_title["Queue"] == [1]                  # required bucket complete
    assert len(by_title["Done"]) == 100              # 2 of Done's 6 pages — it never drove the loop


def test_view_tasks_require_titles_none_still_exhausts_all():
    """Default require_titles=None keeps the old exhaustive behavior: EVERY bucket's full page
    drives pagination, so _find_task/claim/setup still see a complete Done/Backlog. This is the
    contrast that keeps get_task/comment on a Done task working after the #43 latency fix."""
    calls = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        queue = [{"id": 1, "title": "q"}] if page == 1 else []
        done = ([{"id": 100 + i, "title": f"d{i}"} for i in range(50)]
                if page == 1 else [{"id": 999, "title": "tail"}] if page == 2 else [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": queue},
            {"id": 9, "title": "Done", "tasks": done},
        ])

    api = make_api(handler)
    board = api.view_tasks(3, 11)                     # no require_titles -> exhaustive (unchanged)
    assert calls == [1, 2, 3]                         # Done's full page DID drive paging (+VMCP-103's
                                                      # confirming page after the short tail)
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert 999 in by_title["Done"]                   # Done fully paged to its tail


def test_view_tasks_single_page_costs_one_confirming_request():
    """THE PRICE OF VMCP-103, on the cheapest board there is, pinned rather than described: a
    board that fits in one page used to cost ONE request and now costs TWO. The confirming page
    is not slack — a one-task page and a page filtered down to one task are the same observation,
    and asking again is the only thing that tells them apart. The board it returns is unchanged.

    (`claimable` pays this per hgdev-acp poll tick, at the ~0.25 s/request measured in api.py's
    `_MAX_UNPROVEN_PAGES` note. It is one page per READ, not per bucket, and an EMPTY required
    bucket pays nothing — see the two require_titles tests.)"""
    pages = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        pages.append(page)
        tasks = [{"id": 1, "title": "only"}] if page == 1 else []
        return httpx.Response(200, json=[{"id": 4, "title": "Queue", "tasks": tasks}])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    assert board == [{"id": 4, "title": "Queue", "tasks": [{"id": 1, "title": "only"}]}]
    assert pages == [1, 2]                            # exactly one extra, and it stops there


def test_view_tasks_page_size_from_info_drives_pagination():
    """Регрессия #33: порог «полной страницы» = max_items_per_page из /info, а не хардкод 50.
    На инстансе с max_items_per_page=20 полная страница из 20 задач ОБЯЗАНА тянуть следующую;
    старый хардкод 50 слепо останавливал мёрж после page=1 (20 < 50) — тихая потеря доски."""
    pages_seen = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 20})
        page = int(request.url.params.get("page", "1"))
        pages_seen.append(page)
        if page == 1:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(1, 21)]     # 20 — полная
        elif page == 2:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(21, 41)]    # 20 — полная
        elif page == 3:
            tasks = [{"id": i, "title": f"t{i}"} for i in range(41, 46)]    # 5 — хвост
        else:
            tasks = []
        return httpx.Response(200, json=[{"id": 4, "title": "Queue", "tasks": tasks}])

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    ids = [t["id"] for t in board[0]["tasks"]]
    assert sorted(ids) == list(range(1, 46))        # все 45 смёржены, ничего не потеряно
    assert 2 in pages_seen and 3 in pages_seen       # полная страница из 20 тянет следующую


def test_view_tasks_caches_page_size_across_calls():
    """max_items_per_page тянется из /info один раз и кэшируется — не на каждый view_tasks."""
    info_hits = []

    def handler(request):
        if request.url.path.endswith("/info"):
            info_hits.append(1)
            return httpx.Response(200, json={"max_items_per_page": 50})
        return httpx.Response(200, json=[{"id": 4, "title": "Queue", "tasks": [{"id": 1}]}])

    api = make_api(handler)
    api.view_tasks(3, 11)
    api.view_tasks(3, 11)
    assert len(info_hits) == 1


def test_page_size_is_unknown_when_field_missing():
    """VMCP-89: /info без поля max_items_per_page — резолвер отвечает «не знаю» (None), а НЕ
    догадкой 50. Догадка, оказавшаяся больше настоящего размера страницы, молча обрезает доску
    (а на пути `--gc` обрезанная доска = снесённые ЖИВЫЕ деревья), поэтому «сервер не сказал» и
    «сервер сказал 50» обязаны быть разными состояниями, а не одним числом."""
    def handler(request):
        return httpx.Response(200, json={})

    api = make_api(handler)
    assert api._page_size() is None


def test_page_size_is_unknown_when_info_errors():
    """/info вернул 500 — ошибку резолвер по-прежнему ГЛОТАЕТ (view_tasks не падает), но отдаёт
    None «не знаю», а не 50: решение принимает view_tasks, которая на None перестаёт делать вывод
    «страница неполная => доска кончилась» (VMCP-89)."""
    def handler(request):
        return httpx.Response(500, json={"message": "boom"})

    api = make_api(handler)
    assert api._page_size() is None


def test_view_tasks_does_not_truncate_the_board_on_an_unknown_page_size():
    """VMCP-89, суть бага: /info недоступен, значит page size НЕИЗВЕСТЕН, а настоящий (3) меньше
    прежнего хардкода 50. По старому правилу «остановиться, если ни один бакет не отдал полную
    страницу» первая же страница из 3 задач читалась как «это вся доска», и задачи 4-8 просто
    исчезали из снапшота — без ошибки и без следа. Именно из такого снапшота `--gc` строит
    множество живых задач.

    На неизвестном размере страницы остановка теперь только по факту ДАННЫХ: страница не принесла
    ни одной новой задачи. Цена — ровно один лишний запрос (page=4)."""
    pages_seen = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(503, json={"message": "unavailable"})
        page = int(request.url.params.get("page", "1"))
        pages_seen.append(page)
        all_ids = list(range(1, 9))                                  # 8 задач при page size 3
        tasks = [{"id": i, "title": f"t{i}"} for i in all_ids[(page - 1) * 3:page * 3]]
        return httpx.Response(200, json=[{"id": 4, "title": "Build", "tasks": tasks}])

    api = make_api(handler)
    api._MAX_RETRIES = 0                        # как у клиента `--gc`: одна попытка /info, без sleep
    board = api.view_tasks(3, 11)
    assert sorted(t["id"] for t in board[0]["tasks"]) == list(range(1, 9))   # ничего не потеряно
    assert pages_seen == [1, 2, 3, 4]            # +1 запрос: пустая страница — единственный стоп


def test_an_unknown_page_size_is_resolved_once_not_probed_per_call():
    """Неизвестность кэшируется так же, как известное число: сломанный /info НЕ должен добавлять
    по запросу (а с ретраями — по четыре) на КАЖДЫЙ view_tasks. Форма запросов на здоровом и на
    сломанном /info одинакова — меняется только правило остановки."""
    info_hits = []

    def handler(request):
        if request.url.path.endswith("/info"):
            info_hits.append(1)
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json=[{"id": 4, "title": "Build", "tasks": []}])

    api = make_api(handler)
    api._MAX_RETRIES = 0                # без ретраев: считаем РЕЗОЛВЫ, а не попытки (и без sleep)
    api.view_tasks(3, 11)
    assert len(info_hits) == 1          # один резолв на первый вызов…
    api.view_tasks(3, 11)
    assert len(info_hits) == 1          # …и НИ ОДНОГО на второй: «не знаю» кэшируется как число


def test_move_task_posts_to_bucket_endpoint():
    seen = {}

    def handler(request):
        seen["call"] = (request.method, request.url.path, json.loads(request.content))
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.move_task(3, 11, 42, 7)
    assert seen["call"] == (
        "POST", "/api/v1/projects/3/views/11/buckets/42/tasks", {"task_id": 7},
    )


def test_configure_kanban_sends_full_replace_with_mode():
    """Гоча: POST вида без bucket_configuration_mode ломает канбан."""
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json=body)

    api = make_api(handler)
    view = {"id": 11, "title": "Kanban", "view_kind": "kanban", "position": 250}
    api.configure_kanban(3, view, default_bucket_id=1, done_bucket_id=9)
    assert seen["body"]["bucket_configuration_mode"] == "manual"
    assert seen["body"]["position"] == 250
    assert seen["body"]["default_bucket_id"] == 1
    assert seen["body"]["done_bucket_id"] == 9
    assert seen["body"]["view_kind"] == "kanban"


def test_configure_kanban_preserves_zero_position():
    seen = {}

    def handler(request):
        body = json.loads(request.content)
        seen["body"] = body
        return httpx.Response(200, json=body)

    api = make_api(handler)
    view = {"id": 11, "title": "Kanban", "view_kind": "kanban", "position": 0}
    api.configure_kanban(3, view, default_bucket_id=1, done_bucket_id=9)
    assert seen["body"]["position"] == 0


def test_update_bucket_is_full_replace_with_position():
    seen = {}

    def handler(request):
        seen["call"] = (request.method, request.url.path, json.loads(request.content))
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.update_bucket(3, 11, {"id": 42, "title": "Done"}, position=700)
    method, path, body = seen["call"]
    assert (method, path) == ("POST", "/api/v1/projects/3/views/11/buckets/42")
    assert body == {"title": "Done", "position": 700}


def test_get_or_create_label_reuses_existing():
    calls = []

    def handler(request):
        calls.append(request.method)
        if request.method == "GET":
            return httpx.Response(200, json=[{"id": 5, "title": "blocked"}])
        return httpx.Response(200, json={"id": 6, "title": "epic"})

    api = make_api(handler)
    # the claim is about the WRITE, not the read count: an existing label must not be minted a
    # second time. How many GETs the lookup costs is a paging detail (VMCP-108 made labels() a
    # paged read, so it is /info + pages, not one request) and pinning it here only made this
    # test fail for a reason it is not about.
    assert api.get_or_create_label("blocked")["id"] == 5
    assert calls.count("PUT") == 0                  # reused -> nothing created
    assert api.get_or_create_label("epic")["id"] == 6
    assert calls.count("PUT") == 1                  # absent -> created exactly once


def test_share_project_idempotent():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=[{"username": "agent-infra", "permission": 1}])
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.share_project(3, "agent-infra", 1)          # уже есть -> ни одного PUT
    # as above: the claim is "no re-share", not a request count. A re-PUT is not harmless here —
    # real Vikunja answers 409 "This user already has access to this project" and setup dies.
    assert [m for m, _ in calls] == ["GET"] * len(calls)
    assert {p for _, p in calls} <= {"/api/v1/info", "/api/v1/projects/3/users"}
    api.share_project(3, "agent-voice", 1)           # нет -> GET + PUT
    assert calls[-1] == ("PUT", "/api/v1/projects/3/users")
    assert sum(m == "PUT" for m, _ in calls) == 1


# --- VMCP-92 (548): the DEGRADED branch is BOUNDED, and it RAISES rather than truncating ------
#
# VMCP-89 made the page size KNOWN or UNKNOWN instead of a guessed 50, and on UNKNOWN the loop
# pages until no NEW required task arrives — a fact about the data that needs no page size. Two
# residuals lived on that branch, both constructed here against REAL httpx + REAL api.py (a fake
# shares this code's own model of pagination and could not see either):
#
#   1. NO REQUEST BOUND. A known size caps the read at ceil(N/page_size)+1 whatever the server
#      does; an unknown one capped nothing, so a server handing out one brand-new task per page
#      never terminated (401 requests and still going when the harness cut it off). `--gc` was
#      contained by VMCP-72's 30 s deadline; next_task/claim/advance/setup have none.
#   2. A REPEAT READ AS COMPLETION. "No new required task" was the ONLY stop, so a required bucket
#      re-serving a FULL window of already-seen tasks read as exhausted: MEASURED before the fix,
#      a known size read on to Build[1..6] while an unknown size stopped at Build[1,2,3].
#
# EVERY pin below is about the same invariant: on this branch the read is either COMPLETE or it
# RAISES. A short board is indistinguishable from tasks that are genuinely gone, and that
# indistinguishability is what ends in `--gc` reaping a live worktree.

def _tracker(handler, *, info_status=503, page_size=50, harness_cap=3 * MAX_UNPROVEN_PAGES):
    """A real client over `handler`, with /info answering `info_status` (503 = the degraded path).

    The HARNESS CAP is what makes these tests honest about a loop that does not terminate: remove
    the ceiling from api.py and they go RED here instead of hanging the suite forever.
    """
    pages = []

    def counting(request):
        if request.url.path.endswith("/info"):
            if info_status != 200:
                return httpx.Response(info_status, json={"message": "unavailable"})
            return httpx.Response(200, json={"max_items_per_page": page_size})
        pages.append(int(request.url.params.get("page", 1)))
        if len(pages) > harness_cap:
            raise RuntimeError(f"view_tasks issued more than {harness_cap} requests")
        return handler(request)

    api = make_api(counting)
    api._MAX_RETRIES = 0                 # как у клиента `--gc`: одна попытка /info, без sleep
    return api, pages


def _serving_lengths(handler, sink):
    """Wrap a fixture server and record the length of every per-bucket page it hands back.

    VMCP-124 (603): the ONE property both sweeps' fixture servers are DOCUMENTED as having — no
    page longer than /info states. The sweeps assert it (`assert max(sink) <= page_size`) rather
    than describing it, because the claim they used to make in prose — a superset "by construction"
    — was FALSE the moment a server served a page LONGER than /info states. Silently extending a
    sweep's claimed reach past what it checks is the exact failure that produced that prose; this
    makes it fail loudly instead.

    WHAT THIS GUARDS CHANGED UNDER IT, and saying so is the whole job of this note. When it was
    added it kept a CONDITIONAL equality applicable: the two /info states were GUARANTEED to agree
    only while `min(stated, longest_served)` collapsed to `longest_served`. Guaranteed, not
    achieved — past that point they could still agree, and with sweep 2's draw widened they
    measurably did: RE-MEASURED on the pre-127 tree for this card, 56 of the 60 rounds really
    over-served and all 60 still agreed with the scope assert muted. (582's 300-round run, in the
    "THE SWEEPS ARE BLIND" ledger further down this file, is the independent one.) VMCP-127 (608)
    then deleted the fullness inference from BOTH readers, so two reads that both COMPLETE now
    agree by construction. NOT unconditionally: `page_size` still
    feeds `unproven_pages`, and that ceiling RAISES — MEASURED on this tree, a server serving 130
    FULL pages of new required tasks completes at 650 tasks in 131 requests with /info up and
    raises a 508 after 120 with it down. So this assert is now purely a SCOPE pin — "these sweeps
    model honest servers", which is exactly what the prose above each sweep claims to cover, and
    which nothing else checks.

    KEEP BOTH CALL SITES. Each sweep's server widened to over-serve, one widening at a time,
    __pycache__ cleared, WHOLE SUITE run every time in a scratch copy of the tree (a "sole guard"
    claim is about the SUITE, not one sweep), 2026-07-31 on a 690-test tree:

      sweep 2's own page draw widened to                        -> 1 failed / 689 passed with this
      n = randint(1, page_size + 3)                               assert, and the one failure IS
      (`_short_non_final_pages` is a pass-through — the draw       that sweep; 690 passed — fully
      feeding it lives INSIDE the sweep, so nothing else in        GREEN — without it (582 measured
      the suite could see this one; the whole-suite run is         the same over 300 rounds).
      what shows the failure is confined to it)
      `_offset_pages`, EVERY page cut at page_size + 3          -> 1 failed / 689 passed with it,
                                                                  the failure again its own sweep;
                                                                  690 passed — fully GREEN —
                                                                  without it.
      `_offset_pages`, PAGE 1 ONLY cut at page_size + 3         -> 1 failed / 689 passed with it.
                                                                  The FLUKE-LONG FIRST PAGE this
                                                                  card's fixtures are built on.
                                                                  This row also read "690 passed
                                                                  without it"; that was true when
                                                                  measured and is FALSE since
                                                                  VMCP-130 (616) — RE-MEASURED with
                                                                  this assert muted, 705 passed /
                                                                  1 FAILED, the failure being sweep
                                                                  1's new COMPLETENESS assert,
                                                                  because this widening really does
                                                                  lose a row (the arithmetic is in
                                                                  that sweep's docstring).

    So on this tree the assert is the SOLE guard for the FIRST TWO widenings above — no longer for
    the third — and the two call sites are not redundant for a subtler reason than they used to be:
    each is the only guard its own fixture has. Three qualifications, all measured, all there so the
    guard is not trusted further than it goes. (1) "For these widenings", not for every one: the
    other two tests that
    page over `_offset_pages` fix `page_size=50`, so an over-serve of +50 trips their request-count
    asserts as well (green at +1/+3/+5/+10/+20 with this muted, red at +50) — it is the SMALL
    over-serve, the one that looks like a real server's fluke, that nothing else sees. (2) The
    three rows differ in strength: `_offset_pages` is a REAL shared generator (three tests page
    over it), so its two rows say something about the suite; sweep 2's draw is local to sweep 2,
    so its row says only that the failure stays confined there. (3) A "sole guard" claim decays
    whenever a sweep gains an assert, which is exactly how row three decayed: re-run the rows rather
    than re-word them.

    THE MIDDLE ROW READ DIFFERENTLY BEFORE VMCP-127, AND THAT HISTORY IS WHY IT IS SPELLED OUT.
    It used to say "STILL RED with this assert removed". RE-MEASURED at 4149712 — the pre-127 tree,
    same mutation, assert removed — the red came from the sweep's own SUBSET assert, `set(healthy)
    <= set(degraded)`, firing on the NON-required `Done` bucket (`AssertionError: ('Done', 2,
    {'Build', 'Queue', 'Review'})`, `Extra items in the left set: 17, 18`), NOT from its equality
    assert, which stayed green. A revision of this docstring said "equality"; that was false, and
    it was false because the row was rewritten without being re-run. On today's tree the row is
    moot in either spelling: with the fullness inference gone, over-serving produces no
    healthy/degraded divergence for either of the sweep's own asserts to catch.

    Which widening is the LIKELIER one was not measured and is not claimed. Live 2.3.0's /projects
    hands back (the window's real rows) + (a pseudo tail fixed by the USER's saved filters and
    favourites), so it over-serves exactly when that sum passes the stated size — measured at a
    stated 5 with a tail of 3: 5+3=8 through the full-window pages, 4+3=7 on the last one at 34
    real projects, 1+3=4 (UNDER the stated 5) on the last one at 41, and 0+3=3 on every page past
    them. So it is a page-1-only over-server on no reading and an every-page one on no reading
    either, and the page NUMBERS are that instance's content while the shape is not. Throughout,
    this assert keys on the SCOPE rather than on an outcome another seed could hide. (Suite totals
    are 2026-07-31 snapshots and move whenever anyone adds a test — an earlier revision of this
    table read 628/629, the same experiment on the tree of the day.)"""
    def wrapped(request):
        response = handler(request)
        for bucket in response.json():
            sink.append(len(bucket.get("tasks") or []))
        return response
    return wrapped


def _offset_pages(bucket_ids, page_size):
    """An HONEST Vikunja: page p of a bucket is its tasks at offsets [(p-1)*size, p*size)."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4 + i, "title": title, "tasks": [
                {"id": tid, "title": f"t{tid}"}
                for tid in ids[(page - 1) * page_size:page * page_size]
            ]}
            for i, (title, ids) in enumerate(bucket_ids.items())
        ])
    return handler


def test_view_tasks_raises_instead_of_paging_forever_on_an_unknown_page_size():
    """THE defect. A server that hands out one brand-new Build task on every page, with /info
    down: honest offset pagination over a finite bucket cannot produce this, so the read can only
    be abandoned — and abandoning it must not look like a finished board.

    Pins all three halves of that: it STOPS, it stops at the ceiling (not whenever), and it stops
    by RAISING with nothing returned. Delete the ceiling and this fails on the harness cap."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": 1000 + page, "title": f"t{page}"}]},
        ])

    api, pages = _tracker(handler)
    with pytest.raises(VikunjaError) as exc:
        api.view_tasks(3, 11)
    assert exc.value.status == 508
    assert len(pages) == MAX_UNPROVEN_PAGES          # the ceiling'th request is the LAST one sent
    # the message is the only thing a human gets, and it must name the thing to fix
    assert "max_items_per_page" in exc.value.message and "/info" in exc.value.message


@pytest.mark.parametrize("n_tasks, why", [
    (50 * (MAX_UNPROVEN_PAGES - 1), "just under the ceiling: read WHOLE, not truncated"),
    (50 * (MAX_UNPROVEN_PAGES + 1), "over it: RAISED, and still not truncated"),
])
def test_an_honest_large_board_on_the_degraded_path(n_tasks, why):
    """What a consumer with a genuinely large board experiences, on both sides of the bound —
    the trade-off made visible rather than described. At Vikunja's default page size (50) the
    ceiling is ~6 000 tasks in ONE required bucket, ~65x this project's entire board.

    The under-row is the load-bearing one: a bound that turned a big honest board into an error
    would be a worse bug than the hang it fixes. The over-row records the accepted cost, and its
    failure direction is the same as every other one here — an error, never a short board."""
    ids = list(range(1, n_tasks + 1))
    api, pages = _tracker(_offset_pages({"Build": ids}, 50))

    if n_tasks < 50 * MAX_UNPROVEN_PAGES:
        board = api.view_tasks(3, 11)
        assert sorted(t["id"] for t in board[0]["tasks"]) == ids, why
        assert len(pages) == MAX_UNPROVEN_PAGES              # 119 full pages + the empty stop
    else:
        with pytest.raises(VikunjaError) as exc:
            api.view_tasks(3, 11)
        assert exc.value.status == 508, why


def test_pages_the_stated_page_size_justifies_cost_the_ceiling_nothing():
    """A board far past the ceiling in PAGES must still read WHOLE on a healthy /info — an honest
    8 000-task board is not an error. VMCP-103 is what makes this a live assertion rather than a
    tautology: the ceiling used to skip the known branch entirely, and now it applies everywhere,
    counting only the pages `max_items_per_page` did NOT account for. Here every page comes back
    full at the stated 50, so 160 of the 161 requests are justified and the budget is barely
    touched. Make the counter unconditional (drop the `stated_full_required` guard) and this goes
    red at request 121, which is the regression the guard exists to prevent."""
    ids = list(range(1, 50 * (MAX_UNPROVEN_PAGES + 40) + 1))         # 8 000 tasks = 160 pages
    api, pages = _tracker(_offset_pages({"Build": ids}, 50), info_status=200, page_size=50)

    board = api.view_tasks(3, 11)

    assert sorted(t["id"] for t in board[0]["tasks"]) == ids
    assert len(pages) == MAX_UNPROVEN_PAGES + 41                     # 160 full + 1 empty


@pytest.mark.parametrize("info_status, why", [
    (200, "the control: a KNOWN page size reads on through the repeat"),
    (503, "the edge: an UNKNOWN one used to stop at the repeat and truncate"),
])
def test_a_required_bucket_repeating_a_full_window_no_longer_truncates(info_status, why):
    """VMCP-92's second edge, with its own control. Build re-serves its FULL window on page 2
    while non-required Done keeps adding — "no new required task" read that as "Build is done"
    and lost Build[4,5,6]. Both rows must now see all six.

    The control is what makes the row above it trustworthy: identical board, identical server, the
    ONLY difference being whether the client could learn the page size."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = {1: [1, 2, 3], 2: [1, 2, 3], 3: [4, 5, 6]}.get(page, [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i, "title": f"b{i}"} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}] if page <= 6 else []},
        ])

    api, pages = _tracker(handler, info_status=info_status, page_size=3)
    board = api.view_tasks(3, 11, require_titles={"Build"})

    by_title = {b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}
    assert by_title["Build"] == [1, 2, 3, 4, 5, 6], why
    assert len(pages) == 4


def test_a_required_bucket_repeating_a_window_SHORTER_than_the_stated_size():
    """VMCP-127 (608) FIRST: the `min(stated, served)` cap this docstring is about no longer
    exists, so everything below it describing operands is history, kept for the reasoning rather
    than as a live mutation guide. What the test still pins is its BEHAVIOUR, and it pins it
    against a wider rule now — a required bucket re-serving a window must not end the read whatever
    the window's length is, which is what test_the_board_is_read_whole_whatever_the_repeat_window_is
    sweeps across the whole band at the end of this file. This read is the w = 3 case of it.

    VMCP-92's repeat-window edge, moved onto the HEALTHY branch — and the shape that pinned the
    SERVED operand of the `min(stated, served)` cap. Build re-serves its window of 3 while /info
    states 5, so the window is short by the STATED measure but exactly full by the PROVEN one;
    Done keeps adding, so "no new required task" alone cannot save the read.

    Compare the threshold against the stated 5 instead of `min(5, longest served)` and Build comes
    back [1,2,3]: the repeat looks short, nothing new arrived in Build, and the read ends three
    tasks early — on a healthy /info, which is the whole complaint of that card.

    ONE operand, not the cap (VMCP-111 corrected this docstring, which used to claim the whole
    cap). THIS server serves 3 against a stated 5, and wherever `served <= stated` the cap collapses
    to `served` — so the STATED operand is dead weight here and deleting it changes nothing. Its pin
    is `test_a_server_serving_MORE_than_it_stated_still_reads_the_board_whole` at the end of this
    file, whose server serves 8 against a stated 5.

    That is the shape THIS pin uses — NOT the only shape in this file where the two operands
    disagree. INSTRUMENTED (the count lives in the VMCP-111 comment block): FOUR tests put them in
    disagreement. Two are the pins themselves — the board one named above and its flat sibling
    `..._still_reads_the_list_whole`, each serving 8 against a stated 5, one per call site. The
    third is the direct arithmetic assert `_could_be_full(5, 9)` in the shared-rule test, which
    calls the helper rather than reading anything. The fourth is a REAL READ that pins NEITHER
    operand: `test_an_endpoint_that_ignores_page_terminates_without_duplicating_rows` serves ELEVEN
    against a stated 5, but its page 2 is a pure repeat, `added_new` ends the read first, and
    shipped / served-only / stated-only / an unreachable threshold all return the same 11 rows in 2
    requests — MEASURED on that fixture, all four identical. So disagreeing operands are NECESSARY
    for a read to notice the stated operand going missing (where they agree, `min(stated, served)`
    and `served` are the same number and the substitution cannot change anything) but NOT
    sufficient: the read has to reach the threshold before some other clause ends it.

    (Scoped to named servers on purpose. This one docstring has now shipped the same error twice:
    first "every server on this side of the file serves at most what /info states", then "the only
    shape where the two operands disagree" — each a true measurement restated as a universal, and
    each false by the same counterexample, the eleven-against-five read above.)"""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = {1: [1, 2, 3], 2: [1, 2, 3], 3: [4, 5, 6]}.get(page, [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}] if page <= 6 else []},
        ])

    api, pages = _tracker(handler, info_status=200, page_size=5)      # STATED 5, never served
    board = api.view_tasks(3, 11, require_titles={"Build"})

    by_title = {b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}
    assert by_title["Build"] == [1, 2, 3, 4, 5, 6]
    assert len(pages) == 4


def test_a_server_that_ignores_the_page_param_still_stops_without_raising():
    """The other direction of the same clause, and the reason it is ANDed with "something new
    arrived": a server that serves the same three tasks whatever `?page=` says is broken but
    COMPLETE, and it must still return that board in two requests — never be paged to the ceiling
    and turned into an error. Its repeat brings nothing new ANYWHERE, so the loop stops."""
    def handler(request):
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in (1, 2, 3)]},
        ])

    api, pages = _tracker(handler)
    board = api.view_tasks(3, 11)

    assert sorted(t["id"] for t in board[0]["tasks"]) == [1, 2, 3]
    assert len(pages) == 2


def test_overlapping_pages_cost_no_extra_request_on_the_degraded_path():
    """The REAL observed shape (unstable sort re-serves a task at the page boundary) is the one
    the new clause must not make expensive: 12 tasks at page size 5 with a 1-task overlap still
    read whole in the same 4 requests, because every one of those pages brings something new."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        start = max(0, (page - 1) * 5 - (1 if page > 1 else 0))
        return httpx.Response(200, json=[{"id": 4, "title": "Build", "tasks": [
            {"id": i} for i in list(range(1, 13))[start:start + 5]]}])

    api, pages = _tracker(handler)
    board = api.view_tasks(3, 11)

    assert sorted(t["id"] for t in board[0]["tasks"]) == list(range(1, 13))
    assert len(pages) == 4


def test_the_degraded_stop_rule_does_not_depend_on_bucket_order():
    """Both bucket orders must return the same tasks in the same buckets. Done first serves 4 tasks
    on the very page where Build repeats its window, so a reader that judged a bucket against a bar
    the buckets listed BEFORE it had already raised would answer differently for each order.

    ITS ORIGINAL SUBJECT IS GONE, and recording that is half the job of this note. It opened with
    "the proven-lower-bound page size is read from the longest page seen on EARLIER pages, not from
    a running maximum updated mid-page" — and VMCP-127 (608) deleted that page size from BOTH
    readers, so there is no running maximum left to snapshot and no line of `view_tasks` the old
    wording describes. Kept as written it would be prose asserting a safety property about deleted
    code, which is precisely what the next reader preserves blindly or deletes blindly.

    WHAT IT PINS NOW — MEASURED, not argued (VMCP-121 (596): shadow copies of src/ on PYTHONPATH,
    the tree never edited, whole file run per mutant, __pycache__ cleared between rounds). Put the
    deleted length rule BACK two ways — the running maximum updated MID-PAGE (order-dependent)
    against the same bar snapshotted BEFORE the bucket loop (order-independent) — and of the 107
    tests in this file THIS IS THE ONLY ONE THAT TELLS THEM APART: 21 failed / 86 passed against
    20 failed / 87 passed, with this test the whole of the difference and nothing red the other
    way. So the pin is live — but it is a REGRESSION pin. It guards a SHAPE nobody has
    re-introduced, not any line standing in the reader today.

    WHAT IT DOES NOT HOLD. The NAME is exact — the degraded STOP RULE really is order-independent
    — but "the board does not depend on bucket order" would not be, and three things sit outside it:
      (1) The board's BUCKET ORDER does follow the server's. MEASURED on this tree: the two orders
          come back ['Build', 'Done'] and ['Done', 'Build'], because `merged` is a dict keyed in
          first-seen order. This test cannot see that, deliberately — it compares a {title: ids}
          dict, which ignores order. It holds that the same TASKS land in the same BUCKETS.
      (2) It does not cover the order-sensitive line that IS here today, the last-bucket-wins
          dedupe `owner[task["id"]] = bid`. Flipping it to first-wins leaves THIS test GREEN and
          reddens exactly test_view_tasks_dedupes_moved_task_globally_keeping_last_bucket and
          test_a_task_moving_buckets_mid_read_still_lands_once_on_the_degraded_path.
      (3) A red here is not by itself an ordering regression: dropping the second term of
          `keep_going` reddens it too, along with 29 others in this file. Read it beside them.

    NO FLAT TWIN OF *THIS* TEST EXISTS, AND NONE SHOULD BE WRITTEN — the reason VMCP-121 (596) is
    recorded here rather than in api.py. `_paged_list` carried the same "snapshotted BEFORE this
    page" comment and pointed HERE for its reason; 608 deleted the comment along with the operand.
    Keep the claim narrow: plenty about the flat reader IS pinned — put its deleted length stop back
    and 23 tests go red. What no fixture can pin is that stop's ORDERING, and 596 measured it twice.
    On THIS tree, re-adding the stop in BOTH orderings — the bar snapshotted before the page, and
    the page folded into it first — reddens the SAME 23 tests, with nothing red either way round.
    On the pre-127 tree, where the code still existed, the same move was INERT rather than merely
    green: 0 of 3015 constructed servers (15 named shapes plus a 3000-server randomized sweep, real
    httpx over real api.py, shipped and mutant run in SEPARATE processes) differed in rows OR
    request count, and the whole suite's failure set was identical to the control's — while a
    positive control at the same call site (the threshold made unreachable) differed on 2199 of
    those servers and reddened 7 tests. The reason is arithmetic and holds for every input,
    which is why no fixture could have pinned it: the threshold f(P) = min(stated, P) — or P itself
    on the degraded path, where /info stated nothing — never exceeds P, so folding the page in first
    turns `len >= f(P)` into `len >= f(max(P, len))`, and those are identical when P >= len and true
    on BOTH sides when P < len. All 18252 (stated, P, len) triples enumerated agree, degraded rows
    included. A flat list has no second axis for an ordering to show up on; the board has one, which
    is why the pin lives here and only here."""
    def make(order):
        def handler(request):
            page = int(request.url.params.get("page", 1))
            build = {1: [1, 2, 3], 2: [1, 2, 3], 3: [4, 5, 6]}.get(page, [])
            done = {1: [9001], 2: [9002, 9003, 9004, 9005]}.get(page, [])
            buckets = {
                "Build": {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
                "Done": {"id": 9, "title": "Done", "tasks": [{"id": i} for i in done]},
            }
            return httpx.Response(200, json=[buckets[title] for title in order])
        return handler

    seen = []
    for order in (("Build", "Done"), ("Done", "Build")):
        api, pages = _tracker(make(order))
        board = api.view_tasks(3, 11, require_titles={"Build"})
        seen.append(({b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}, len(pages)))

    assert seen[0] == seen[1]
    assert seen[0][0]["Build"] == [1, 2, 3, 4, 5, 6]


def test_a_task_moving_buckets_mid_read_still_lands_once_on_the_degraded_path():
    """The pre-existing dedupe (per bucket+task id, then GLOBALLY by task id keeping the LAST
    bucket) meets the new stop rule: a task seen in Queue on page 1 and in Build on page 2 counts
    as NEW in Build (the seen-set is per bucket) and so drives the loop on — and must still land
    exactly once, in the bucket where it was seen last."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": [{"id": 1}, {"id": 7}] if page == 1 else []},
            {"id": 5, "title": "Build", "tasks": [{"id": 7}] if page == 2 else []},
        ])

    api, pages = _tracker(handler)
    board = api.view_tasks(3, 11)

    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert by_title == {"Queue": [1], "Build": [7]}
    assert [tid for ids in by_title.values() for tid in ids].count(7) == 1
    assert len(pages) == 3


def test_neither_read_loses_a_task_an_honest_server_paginates():
    """60 randomized boards (page sizes 1..50, bucket sizes across several page boundaries, all
    three require_titles shapes), each read TWICE — once with /info healthy, once with it down —
    over an HONEST server: `_offset_pages` never serves more rows than the size it states.

    WITHIN THAT SCOPE every REQUIRED bucket must come back holding the WHOLE content the generator
    handed the server, the two reads must agree exactly on those buckets, and the degraded read may
    only ever have MORE elsewhere (it spends one extra page, and that page carries extra
    Done/Backlog tasks).

    THE COMPLETENESS LINE IS VMCP-130 (616)'s, AND WITHOUT IT THIS SWEEP COULD NOT FAIL AT ALL.
    Until it was added the whole test was `set(healthy) <= set(degraded)` plus `healthy ==
    degraded`, and VMCP-127 (608) had already killed both: it deleted the fullness inference from
    BOTH readers, so `keep_going` mentions no page size and the two /info states execute the very
    same expression — what a COMPLETED read contains cannot depend on /info's health, on any seed
    (608 measured 5 000 randomized flat shapes: the NEW code identical 5 000/5 000, the OLD code
    differing on 257 of the same 5 000). 60 rounds of pagination were left asserting a tautology.
    MEASURED at 8b4bfa5, before this card: with `keep_going = False` in `view_tasks` — a reader
    that returns page 1 and stops, truncating every board this generator builds — this test PASSED,
    while its sibling test_the_healthy_read_never_loses_a_task_a_server_serves_short went RED on
    its own SEPARATE completeness assert. Re-measured here, same mutant, the two tests together:
    `1 failed, 1 passed`, the failure being the sibling's `assert healthy == whole` (`('Queue', 2,
    None)`, `assert [1] == [1, 2, 3, 4, 5]`).

    The assert below is that sibling's pin, given to this sweep — and it was checked BOTH WAYS,
    because a mutant run in one direction proves nothing about a test that was already green
    (`__pycache__` cleared before each row, this test alone):

        keep_going = False                                  -> 1 failed. `assert healthy == whole`
        (the reader that returns page 1 and stops)              at `('Queue', 50, None)`, "Right
                                                               contains 103 more items, first
                                                               extra item: 51"
        keep_going = added_new_required or (required_had_  -> 1 passed
        tasks and added_new)  (the shipped line, restored)

    The RED row is the load-bearing one: before this card the same mutant left this test green.

    IT STILL DOES NOT COVER THE STOP RULE, and the new assert did not change that: `_offset_pages`
    never repeats a window, so `added_new_required` carries every read on its own and dropping
    VMCP-92's repeat clause (`keep_going = added_new_required`) leaves this sweep GREEN. The ledger
    under "THE SWEEPS CANNOT SEE ANY OF THIS" further down this file records that measurement and
    the constructed pins that do the job instead. What changed is narrower than it looks: a sweep
    that cannot see the stop rule can now at least see a board come back SHORT.

    `assert max(served) <= page_size` below guards no conditional equality any more — it pins this
    sweep's stated SCOPE, "an HONEST server", which is what the first paragraph claims to cover.
    Its rows in `_serving_lengths` were RE-MEASURED for this card rather than re-worded, because a
    sweep that gains an assert can quietly take a "sole guard" row over — and one of them did.
    Whole suite, this sweep's scope assert muted:

        `_offset_pages` over-serving by +3 on EVERY page   -> 706 passed. Nothing but the scope
                                                             assert sees this widening, as before.
        `_offset_pages` over-serving by +3 on PAGE 1 ONLY  -> 1 failed / 705 passed — and the
                                                             failure is the COMPLETENESS assert,
                                                             not the scope one: `('Queue', 1,
                                                             {'Build', 'Queue', 'Review'})`,
                                                             `assert [1, 2, 3, 4] == [1, 2, 3, 4,
                                                             5]`.

    The second row is a real LOSS, not a scope complaint, which is why the new assert reaches it: at
    page_size 1 a fluke-long first page serves ids[0:4], page 2's honest window ids[1:2] is then a
    pure REPEAT, nothing new arrives in any required bucket, the read stops — and task 5 is gone.
    `_serving_lengths` used to record that widening as fully green without the scope assert; that is
    false here, and its third row now says so.

    The other exception is worth knowing before trusting either guard too far: the two other tests
    that page over `_offset_pages` fix `page_size=50`, so an over-serve of +50 trips THEIR
    request-count asserts too (measured: +1, +3, +5, +10, +20 all green with the scope assert muted;
    +50 goes red in test_an_honest_large_board_on_the_degraded_path and
    test_pages_the_stated_page_size_justifies_cost_the_ceiling_nothing). A widening small enough
    to look like a real server's fluke is still the one least likely to be seen elsewhere.

    THE NAME AND THE SUBJECT USED TO BE THE SUPERSET RELATION, and that is precisely the property
    that stopped being able to vary. This was test_the_degraded_read_never_loses_a_task_the_healthy_
    read_saw, and it claimed the degraded read is "a SUPERSET by construction: its 'could still be
    full' test uses the longest page the server has PROVEN it can serve, which is <= the real page
    size". Every clause of that was true and the conclusion did not follow: the bound was <= the
    REAL page size, while the healthy reader used the STATED
    one, and VMCP-124 (603) MEASURED /projects on a 2.3.0 instance serving pages of 8 against a
    stated 5 — over-serving WHILE paging honestly, on every page carrying a full window of real
    rows (and .../buckets serving 63). Where stated < served the HEALTHY bar was the LOWER one, so
    the degraded read was the STRICTER of the two and a strict SUBSET — measured on 582's fixture,
    2 requests and Build[1..8] against 4 and Build[1..11].

    AND THE HEALTHY READ WAS NOT THE SAFE ONE EITHER — WHICH IS WHY CHANGING ONLY THE DEGRADED BAR
    FIXED NOTHING REAL. The control: page 1 serving EXACTLY the stated 5, nothing over-serving
    anywhere, and the healthy read still lost the tail for every repeat window shorter than 5.
    Between that run and the over-serving one the healthy loss band was the SAME (w < stated);
    what over-serving moved was the DEGRADED bar, and so only the degraded band — right as an
    observation about that run, wrong as a cause. It was the shared inference that broke, not
    this branch; the api.py block by `_MAX_UNPROVEN_PAGES` carries the w-table and that control
    (the note above `_page_size` only delegates to it), and VMCP-127 (608) acted on them by
    deleting the inference from BOTH readers rather than by moving either bar."""
    rng = random.Random(20260730)
    checked = 0
    for _ in range(60):
        page_size = rng.choice([1, 2, 3, 5, 20, 50])
        stages, next_id = {}, 1
        for title in ("Queue", "Build", "Review", "Done"):
            stages[title] = []
            for _ in range(rng.randint(0, 4 * page_size + 2)):
                stages[title].append(next_id)
                next_id += 1
        require = rng.choice([None, {"Build", "Review"}, {"Queue", "Build", "Review"}])

        boards, served = {}, []
        for info_status in (200, 503):
            api, _pages = _tracker(_serving_lengths(_offset_pages(stages, page_size), served),
                                   info_status=info_status, page_size=page_size)
            boards[info_status] = {b["title"]: sorted(t["id"] for t in b["tasks"])
                                   for b in api.view_tasks(3, 11, require_titles=require)}

        # the scope of everything asserted below — see the docstring and `_serving_lengths`
        assert max(served) <= page_size, (max(served), page_size, "server served MORE than stated")

        for title, healthy in boards[200].items():
            degraded = boards[503].get(title, [])
            assert set(healthy) <= set(degraded), (title, page_size, require)
            if require is None or title in require:
                assert healthy == degraded, (title, page_size, require)
                # VMCP-130 (616): the only line here that can still fail — see the docstring. The
                # two above compare two reads that now run the SAME expression; this one compares
                # the read against what the generator actually handed the server.
                whole = sorted(stages[title])
                assert healthy == whole, (title, page_size, require)
            checked += 1
    assert checked == 60 * 4            # every board, every bucket — no silently skipped round


@pytest.mark.parametrize("info_status", [503, 200])
def test_an_unbounded_non_required_bucket_does_not_drag_the_loop(info_status):
    """#43's latency win has to survive on this branch too, and it is the reason the "could still
    be full" test ignores EMPTY pages: with the required bucket exhausted and a Done that keeps
    returning full pages, the read must stop at once. An empty page is not evidence of a full one
    — drop that guard and the very first page of an empty Queue reads as "maybe full" (nothing is
    longer than nothing yet) and the loop pays for a walk through Done.

    Both /info states since VMCP-103 gave them one rule: this is the case where the confirming
    page that fix costs is NOT paid, and #43's win is exactly why. An empty required bucket has
    nothing to confirm."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": []},                       # required, exhausted
            {"id": 9, "title": "Done", "tasks": [                           # unbounded, not required
                {"id": 100 * page + i} for i in range(50)] if page <= 5 else []},
        ])

    api, pages = _tracker(handler, info_status=info_status)
    board = api.view_tasks(3, 11, require_titles={"Queue"})

    assert len(pages) == 1
    assert {b["title"]: len(b["tasks"]) for b in board} == {"Queue": 0, "Done": 50}


# --- VMCP-103 (562): a SHORT NON-FINAL page, and the end of the two-branch asymmetry -----------
#
# VMCP-89/92 deleted "a short page proves the bucket is complete" from the DEGRADED branch and
# left it standing on the HEALTHY one, where `saw_full_page` compared against the size /info
# STATED. Since "len >= stated" implies "len >= longest served", the degraded rule kept reading
# everywhere the known rule did and in shapes it did not — so after 548 the read with a BROKEN
# /info was strictly more robust than the read with a healthy one. Backwards, and the one shape
# neither parity sweep could see: both only ever modelled honest servers whose pages are full
# until the last, and on those the two rules agree.
#
# The tests below model a server that serves a SHORT NON-FINAL page — the shape a view that
# paginates the unfiltered set and filters afterwards hands back. Probed against a real Vikunja
# 2.3.0 (container, max_items_per_page=5) it could NOT be produced: request-level `filter=`, a
# saved filter on the view, `s=` search and done-tasks-auto-moving-to-Done all push the filter
# into SQL and keep every page full until the last. Not reproduced is not impossible, and the
# asymmetry needs no trigger to be wrong.

def _short_non_final_pages(pages_by_title):
    """A server whose pages are what SURVIVED a filter: window p of a bucket advanced by the page
    size as usual, but only `pages_by_title[title][p-1]` rows came back."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4 + i, "title": title,
             "tasks": [{"id": tid} for tid in (served[page - 1] if page <= len(served) else [])]}
            for i, (title, served) in enumerate(pages_by_title.items())
        ])
    return handler


@pytest.mark.parametrize("info_status, why", [
    (503, "the control: the degraded read already survived this"),
    (200, "THE DEFECT: the healthy read stopped at page 1 and lost 3..9"),
])
def test_a_short_non_final_page_does_not_truncate_the_healthy_read(info_status, why):
    """THE card's measurement, as a test. /info states max_items_per_page=5 and the one required
    bucket serves page1=[1,2] (short by accident), page2=[3,4,5,6,7], page3=[8,9].

    MEASURED before the fix, on this exact server: the HEALTHY row stopped after ONE request with
    Build[1,2] — 3..9 silently gone — while the DEGRADED row spent 4 requests and returned
    Build[1..9] whole. Both rows must now read the board whole, in the same 4 requests.

    Not cosmetic: `workspace --gc` builds its liveness set from this read, and a board short of a
    task is indistinguishable from a task that is gone. That is how VMCP-89 reaped two LIVE
    worktrees (`released: [804, 805]`, both trees still on disk)."""
    api, pages = _tracker(_short_non_final_pages({"Build": [[1, 2], [3, 4, 5, 6, 7], [8, 9]]}),
                          info_status=info_status, page_size=5)

    board = api.view_tasks(3, 11, require_titles={"Build"})

    assert sorted(t["id"] for t in board[0]["tasks"]) == list(range(1, 10)), why
    assert len(pages) == 4              # 3 pages with content + the one that brings nothing new


def test_the_healthy_read_never_loses_a_task_a_server_serves_short():
    """The sweep the existing two could not be: 60 randomized boards whose pages are SHORT of the
    stated page size at random — never full until the last, which is the only thing the old
    `_offset_pages` sweeps ever modelled.

    Two assertions, and the second is the one this card exists for: every required bucket comes
    back COMPLETE, and the healthy read equals the degraded read BUCKET FOR BUCKET — not merely
    "is a subset of". After VMCP-103 there is one rule, so /info's health may change what the
    ceiling permits but never what the board contains.

    SCOPED, not universal, and VMCP-124 (603) was the correction: "one rule" collapsed to one
    NUMBER only while no page exceeded the stated size, which every page here does by construction
    (`n <= page_size - 1`) and the `assert max(served_lengths) <= page_size` below keeps true. Let
    a page overshoot the stated size and the two /info states computed DIFFERENT bars — the
    healthy one min(stated, longest), the degraded one longest — so the equality asserted here
    stopped being GUARANTEED. Overshoot alone did NOT break it over 300 rounds of this generator
    (582 measured that): `_short_non_final_pages` never repeats a window, so `added_new_required`
    carries every read whatever the bar says. Reaching the divergence took 582's two constructed
    shapes at the end of this file — so widening this generator was not how to get there.

    PAST TENSE ON PURPOSE: VMCP-127 (608) deleted both bars, so what a COMPLETED read contains no
    longer depends on a page size at all (the ceiling above is where `page_size` survives, and it
    can only make a read RAISE). What the assert below still pins is this sweep's SCOPE — pages
    short of the stated size, never over it. Widen the draw and it is the only thing that goes
    red; since the draw lives HERE and not in the shared `_short_non_final_pages`, that is a
    statement about this sweep, not about the suite (`_serving_lengths` carries the run)."""
    rng = random.Random(20260731)
    checked = 0
    for _ in range(60):
        page_size = rng.choice([2, 3, 5, 20, 50])
        served, next_id = {}, 1
        for title in ("Queue", "Build", "Review", "Done"):
            # 1..8 pages, each SHORT of page_size (>=1 so no bucket goes silent mid-stream — an
            # all-filtered page is indistinguishable from an exhausted one, see the test below)
            served[title] = []
            for _ in range(rng.randint(1, 8)):
                n = rng.randint(1, max(1, page_size - 1))
                served[title].append(list(range(next_id, next_id + n)))
                next_id += n
        require = rng.choice([None, {"Build", "Review"}, {"Queue", "Build", "Review"}])

        boards, served_lengths = {}, []
        for info_status in (200, 503):
            api, _pages = _tracker(_serving_lengths(_short_non_final_pages(served), served_lengths),
                                   info_status=info_status, page_size=page_size)
            boards[info_status] = {b["title"]: sorted(t["id"] for t in b["tasks"])
                                   for b in api.view_tasks(3, 11, require_titles=require)}

        # the scope of everything asserted below — see the docstring and `_serving_lengths`
        assert max(served_lengths) <= page_size, (max(served_lengths), page_size,
                                                  "server served MORE than stated")

        for title, healthy in boards[200].items():
            assert healthy == boards[503].get(title, []), (title, page_size, require)
            if require is None or title in require:
                whole = sorted(t for pg in served[title] for t in pg)
                assert healthy == whole, (title, page_size, require)
            checked += 1
    assert checked == 60 * 4            # every board, every bucket — no silently skipped round


@pytest.mark.parametrize("info_status", [200, 503])
def test_a_read_that_never_fills_a_page_raises_instead_of_returning_a_short_board(info_status):
    """The bound on the new clause, and it is the same one on both /info states now. A server
    that keeps adding ONE new required task per page never fills a page, so nothing ever justifies
    the requests: the read stops at `_MAX_UNPROVEN_PAGES` and stops by RAISING — the direction the
    whole module is built on, since a short board is what `--gc` turns into a reaped worktree.

    The healthy row is the new one, and before VMCP-103 it did not raise: it TRUNCATED after one
    request, on a healthy /info, exactly as the card measured. The message has to say which of the
    two states the human is in, because the fix differs."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": 1000 + page}]},
        ])

    api, pages = _tracker(handler, info_status=info_status, page_size=50)
    with pytest.raises(VikunjaError) as exc:
        api.view_tasks(3, 11)

    assert exc.value.status == 508
    assert len(pages) == MAX_UNPROVEN_PAGES          # the ceiling'th request is the LAST one sent
    assert "max_items_per_page" in exc.value.message and "/info" in exc.value.message
    if info_status == 200:
        assert "max_items_per_page=50" in exc.value.message      # names what the server CLAIMED


@pytest.mark.parametrize("info_status", [200, 503])
def test_a_page_filtered_down_to_nothing_still_ends_the_read(info_status):
    """The LIMIT of this design, pinned rather than left for someone to discover. A page on which
    a required bucket returns NOTHING ends the read even if a later page would have had content:
    an all-filtered window and an exhausted bucket are the same observation, and the only rule
    that could tell them apart — keep paging through empties — has no bound at all and would undo
    #43's latency win (an empty required bucket would walk the whole of Done).

    So the guarantee this module offers is precisely: no required bucket is cut off while it is
    still PRODUCING. Both /info states share the limit, which is the point — it is a property of
    the one rule, not of the branch."""
    api, pages = _tracker(_short_non_final_pages({"Build": [[1, 2], [], [8, 9]]}),
                          info_status=info_status, page_size=5)

    board = api.view_tasks(3, 11, require_titles={"Build"})

    assert sorted(t["id"] for t in board[0]["tasks"]) == [1, 2]      # 8,9 past the empty window
    assert len(pages) == 2


# --- VMCP-108: the FLAT list reads page too -----------------------------------------------
# Every one of these models a shape MEASURED against a real Vikunja 2.3.0 configured with
# max_items_per_page=5 (see the block above `_paged_list` in api.py for the raw numbers).


def _flat(pages, *, page_size=5, total_pages=None, info_status=200,
          harness_cap=3 * MAX_UNPROVEN_PAGES):
    """A server that answers ONE list endpoint out of `pages`, with the x-pagination-total-pages
    header real Vikunja sends on these endpoints.

    `pages` is either a mapping {page_no: [row, ...]} — every page it does not name is EMPTY, which
    is how a finite list ends — or a CALLABLE page -> [row, ...] for a server that HAS no last page.

    WHY THE CALLABLE FORM EXISTS, and it is NOT that a mapping cannot reach the ceiling: it can. A
    mapping of N pages ends the read itself on page N+1 (`.get(page, [])` -> `[]` -> `if not items:
    break`), so it reaches the ceiling only while N is at least what the ceiling COSTS on that shape
    — the budget when no page is ever full, one MORE when the server fills its first page, which
    buys a request the budget is never charged for. MEASURED with the budget edited in a scratch
    copy and this harness otherwise untouched: on the never-fills-a-page shape, mappings of
    119 / 120 / 121 pages at budget 120 give NO RAISE (119 rows) / 508 / 508, and 499 / 500 / 501 at
    budget 500 give NO RAISE (499 rows) / 508 / 508; on the full-first-page shape, the 199-page
    mapping that test_a_flat_list_that_dribbles_new_rows_forever_RAISES_instead_of_a_partial_list
    used to carry raises at budgets 197 and 198 (508 at 198 and at 199 requests) and is dead from
    199 on. A mapping fixture is therefore BUDGET-COUPLED, and past that point it does not merely
    fail — it stops PINNING, because the guard no longer changes what it does:

        budget edited 120 -> 500    ceiling as shipped       ceiling -> `if False:`
        callable, no last page      508 at request 500       harness cap at 1501 (cap = 3 * budget)
        mapping of 499 pages        499 rows, DID NOT RAISE  499 rows, DID NOT RAISE  <- the same
        that 199-page mapping       203 rows, DID NOT RAISE  203 rows, DID NOT RAISE  <- the same

    So the callable tells the two states apart at every budget measured — it has no page count to
    outgrow, which is the whole point — while a mapping does so only below its own. NO
    RUNAWAY-READ FIXTURE IN THIS FILE IS A MAPPING ANY MORE: VMCP-135 (624) moved the last one —
    the test named above, green at today's budget of 120 and pinning nothing from 199 on — onto a
    callable for exactly this reason. Mappings remain right for the FINITE lists everything else
    here models; the coupling only bites a fixture whose job is to have no last page.

    The HARNESS CAP is the same honesty device `_tracker` uses: several of these shapes make the
    loop run forever if its termination guard is removed (a server that ignores `?page=` serves a
    FULL page every time, so neither the fullness rule nor the unproven-page ceiling can ever end
    it — only "this page added nothing new" can). With the cap the mutation goes RED here instead
    of hanging the suite."""
    seen = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(info_status, json={"max_items_per_page": page_size})
        page = int(request.url.params.get("page", "1"))
        seen.append(page)
        if len(seen) > harness_cap:
            raise RuntimeError(f"the read issued more than {harness_cap} requests")
        headers = {} if total_pages is None else {"x-pagination-total-pages": str(total_pages)}
        rows = pages(page) if callable(pages) else pages.get(page, [])
        return httpx.Response(200, json=rows, headers=headers)

    return make_api(handler), seen


def test_projects_reads_past_the_first_page():
    """THE CARD'S BUG. MEASURED (real 2.3.0, max_items_per_page=5, 9 projects): GET /projects
    returns 5 rows with x-pagination-total-pages: 2, and ?page=2 returns the other 4. A
    single-request read therefore reported an EXISTING project as ABSENT — and reconcile acts on
    absence, so it created a SECOND project with the same title (reproduced end to end against
    that container: existing id 8 'p6' ignored, new id 18 created). A duplicate project on a real
    tracker is not something an agent can undo."""
    api, seen = _flat({
        1: [{"id": -1, "title": "Favorites"}, {"id": 1, "title": "Inbox"},
            {"id": 2, "title": "p0"}, {"id": 3, "title": "p1"}, {"id": 4, "title": "p2"}],
        2: [{"id": 5, "title": "p3"}, {"id": -2, "title": "Filters"},
            {"id": 6, "title": "p4"}, {"id": 7, "title": "p5"}, {"id": 8, "title": "p6"}],
    }, total_pages=2)

    titles = [p["title"] for p in api.projects()]

    assert titles == ["Inbox", "p0", "p1", "p2", "p3", "p4", "p5", "p6"]
    assert "p6" in titles                    # the project reconcile used to duplicate
    assert seen[:2] == [1, 2]                # it did not stop at the first window
    assert "Favorites" not in titles and "Filters" not in titles   # pseudo-filter survives paging


def test_labels_comments_and_shares_page_too():
    """THE SWEEP. The card named ONE endpoint; three more paginate identically (MEASURED on the
    same container: 5 rows then 3 on each of /labels, /tasks/{id}/comments,
    /projects/{id}/users). None of the truncations is cosmetic, because every caller acts on
    absence:
      * a label hidden past the window is minted a SECOND time by get_or_create_label — the very
        duplicate its docstring records as a real incident, now reachable with no typo at all;
      * a share hidden past the window makes share_project re-PUT, and real Vikunja answers
        409 "This user already has access to this project", which ABORTS `vikunja-mcp setup`;
      * comments come OLDEST FIRST, so a short read drops the NEWEST rows — the reviewer's
        [review] verdict, the [worklog] next_task's review offering requires (no worklog visible
        => the card is never offered for review at all), and the human's answer to call_human.
    The comment assertion checks ORDER, not just membership: the callers read this list
    positionally and by max(created)."""
    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 5})
        page = int(request.url.params.get("page", "1"))
        path = request.url.path
        if path.endswith("/labels"):
            rows = {1: ["reviewed", "review-failed", "epic", "epic-ready", "blocked"],
                    2: ["bug", "wontfix"]}.get(page, [])
            body = [{"id": i, "title": t} for i, t in enumerate(rows)]
        elif path.endswith("/comments"):
            rows = {1: ["[claim]", "[spec]", "[worklog] v1", "[review] NEEDS_WORK", "[spec] v2"],
                    2: ["[worklog] v2", "[review] APPROVE"]}.get(page, [])
            body = [{"id": i, "comment": t} for i, t in enumerate(rows)]
        else:
            rows = {1: ["u0", "u1", "u2", "u3", "u4"], 2: ["u5", "u6"]}.get(page, [])
            body = [{"id": i, "username": u} for i, u in enumerate(rows)]
        # ids restart per page on purpose: dedupe must not confuse "row 0 of page 2" with
        # "row 0 of page 1" into dropping it. Real ids are global; this is the harsher case.
        body = [{**row, "id": (page - 1) * 5 + row["id"]} for row in body]
        return httpx.Response(200, json=body, headers={"x-pagination-total-pages": "2"})

    api = make_api(handler)

    assert [x["title"] for x in api.labels()] == [
        "reviewed", "review-failed", "epic", "epic-ready", "blocked", "bug", "wontfix"]
    assert [x["comment"] for x in api.comments(7)] == [
        "[claim]", "[spec]", "[worklog] v1", "[review] NEEDS_WORK", "[spec] v2",
        "[worklog] v2", "[review] APPROVE"]          # oldest -> newest, across the page seam
    assert [x["username"] for x in api.project_users(3)] == [
        "u0", "u1", "u2", "u3", "u4", "u5", "u6"]


def test_a_paged_read_never_stops_on_the_total_pages_header():
    """The header may only ever say "keep going", never "stop". VMCP-103 MEASURED it
    UNDER-reporting on this very server (the kanban tasks endpoint sent total-pages 1 while the
    bucket behind it held 3 pages), so a reader that believed it would truncate exactly like the
    bug this card fixes. Here the server insists total-pages=1 on every response and goes right
    on serving full pages.

    VMCP-127 (608) made that structural rather than a rule: `_paged_list` no longer reads the
    header AT ALL — it went with the fullness inference it was the complement of (see api.py's
    module-level `VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block). So this stands as the
    regression pin against RE-INTRODUCING a header-driven stop, which is what VMCP-116's option (b)
    keeps proposing. Its request count moved from 3 to 4 with that card: the read no longer ends on
    page 3 BEING SHORT, it ends on page 4 bringing nothing new."""
    api, seen = _flat({
        1: [{"id": 1}, {"id": 2}, {"id": 3}],
        2: [{"id": 4}, {"id": 5}, {"id": 6}],
        3: [{"id": 7}],
    }, page_size=3, total_pages=1)

    assert [x["id"] for x in api.labels()] == [1, 2, 3, 4, 5, 6, 7]
    assert seen == [1, 2, 3, 4]


@pytest.mark.parametrize("total_pages, why", [
    (3, "the server also says there is a third page"),
    (None, "the server says NOTHING about how many pages there are"),
])
def test_a_short_non_final_page_is_followed(total_pages, why):
    """The flat-list twin of VMCP-103's bug: a page SHORT of the page size with more behind it.

    WHY TWO PARAMETERS, and why the second is the whole point. Until VMCP-127 (608) only the first
    was covered, and it was covered by the `x-pagination-total-pages` HEADER: the fullness
    inference called page 2 the end, and the header was the one signal that could overrule it. But
    the header is not always sent, and it is measured wrong in BOTH directions on this same 2.3.0,
    so the shape it rescued was only ever half the shape.

    MEASURED with the whole pre-127 rule put back on this tree (the bar AND the header, applied
    together, this test run alone): the header row returns all 9 rows and differs only in spending
    one request fewer; the no-header row returns SEVEN — 8 and 9 silently gone. That is the split
    that matters, and it is why the second row exists. Both rows' request counts move under that
    mutation, because the confirming request is what this card costs everywhere, so a red COUNT is
    not evidence of a truncated read — read the row assertion for that."""
    api, seen = _flat({
        1: [{"id": i} for i in range(1, 6)],        # full
        2: [{"id": 6}, {"id": 7}],                  # SHORT — but not the last
        3: [{"id": 8}, {"id": 9}],
    }, page_size=5, total_pages=total_pages)

    assert [x["id"] for x in api.labels()] == [1, 2, 3, 4, 5, 6, 7, 8, 9], why
    assert seen == [1, 2, 3, 4], why


def test_an_endpoint_that_ignores_page_terminates_without_duplicating_rows():
    """views() and buckets() go through the pager although 2.3.0 MEASURABLY does not paginate
    them: both serve the WHOLE list and ignore ?page= entirely, while still advertising
    x-pagination-total-pages 2 and 3 (the same header being wrong in the OVER-reporting direction
    this time). Routing them anyway costs exactly ONE confirming request — the repeat brings no
    new row and `added_new` ends the loop — and it is `added_new`, not the header, that decides:
    the server here claims a third page and is not followed there."""
    rows = [{"id": i, "title": f"b{i}"} for i in range(11)]
    api, seen = _flat({1: rows, 2: rows, 3: rows, 4: rows}, page_size=5, total_pages=3)

    got = api.buckets(3, 11)

    assert [b["id"] for b in got] == list(range(11))     # every row, exactly once
    assert seen == [1, 2]                                # one confirming request, then done


def test_a_list_that_never_finishes_paging_raises_instead_of_truncating():
    """Same discipline as view_tasks (VMCP-92): on hitting the request ceiling the read RAISES and
    returns NOTHING. Returning what it had would be the bug one level up — a truncated list is
    indistinguishable from rows that are genuinely gone, and absence is precisely what these
    callers act on (setup CREATES the project it cannot see). The server modelled here hands out
    one brand-new row per page and never fills one, so `max_items_per_page` justifies no page and
    the whole budget is spent.

    IT GOES THROUGH `_flat` FOR ITS HARNESS CAP, and that is the whole of VMCP-119 (594). This was
    the ONE runaway-read test in this file built on a bare `make_api`, and a server with no last
    page is exactly the fixture that cannot survive losing the guard it pins: MEASURED on the tree
    before this card, ceiling -> `if False:`, __pycache__ cleared — this test produced NO RESULT AT
    ALL, killed by SIGALRM at 60 s, while the identical mutation of the nested twin went RED — the
    two halves of that sentence are different scopes, so both re-measured here: 5 failed / 102
    passed for the whole file in 4.5 s, and 0.15 s for the twin on its own (the first sweep that hit
    the hang blew a ten-minute tool timeout and left api.py mutated on disk).
    A guard whose deletion HANGS is pinned in no way a reviewer or CI can use — a hang is
    indistinguishable from a slow suite, and nothing in this repo bounds a pytest run. With the cap
    the same mutation now FAILS, its own test call taking 0.04 s (pytest --durations, re-measured on
    this tree), on `RuntimeError: the read issued more than 360 requests` at
    `GET /projects?page=361`.

    THE CAP IS `_flat`'S OWN DEFAULT, 3 * MAX_UNPROVEN_PAGES = 360, deliberately NOT tuned down: the
    correct read spends 120 requests here, so the cap sits at 3x what passing costs. A cap near the
    real cost would be the other failure mode — it would fire on the shipped code and mask the very
    ceiling it is bounding, which is why the number is a multiple of the budget rather than a
    hand-picked one.

    THE REQUEST COUNT IS PINNED TOO (same assert as the nested twin at
    test_view_tasks_raises_instead_of_paging_forever_on_an_unknown_page_size). It separates
    "stopped" from "stopped AT THE CEILING": the cap alone leaves `>=` -> `>` green here, because an
    off-by-one ceiling still raises 508 with this exact message, one request later and 239 short of
    the cap — MEASURED by deleting this line and running that mutation, which left this test PASSING.

    TWO THINGS IT IS NOT, both measured rather than assumed. It is NOT the sole guard on that
    mutation: `>=` -> `>` in `_paged_list`'s ceiling — that expression occurs TWICE in api.py, and
    the `view_tasks` twin is left alone — fails exactly TWO tests in the whole unit suite and leaves
    every other one green: this one and
    test_a_flat_list_that_dribbles_new_rows_forever_RAISES_instead_of_a_partial_list. (The FAILURE
    count is the claim on purpose, and it is stated as a DELTA against a CONTROL rather than as a
    pair of suite totals: re-measured here, the unmutated control round is 0 failed and the
    mutation is 2 failed, and those two are exactly the tests named above. Both halves of that
    survive the suite growing, which is exactly what a total does not. The PASS TOTAL is
    deliberately NOT recorded here: it moves with every test the repo adds, so a total written
    down is only ever true at the sha that wrote it — the same reason CLAUDE.md keeps its unit
    count as a floor rather than a figure. This sentence carried one anyway and went stale three
    times over: it was corrected once in git, it was stale again by the time a card was filed to
    correct it a second time, and it had moved AGAIN by the time that card's own fix was measured
    hours later. Do not re-pin a total here — run the mutation yourself, control round FIRST, and
    read your own two numbers.) That is worth keeping anyway, because the two pin DIFFERENT
    numbers for the same budget — 120 here, 121 there — and the gap IS the accounting rule: that
    server fills its first page, so `max_items_per_page` justifies one request and the ceiling is
    reached one later; this one never fills a page at all, so nothing is justified and the budget
    buys 120. And it does NOT pin the budget's VALUE: the constant is imported from the code under
    test, so both sides of the assert move together if _MAX_UNPROVEN_PAGES is edited. It pins WHERE
    the guard fires relative to the budget. No test in this file claims the latter, and this one
    should not be read as doing so.

    THAT LAST PROPERTY IS THE FIXTURE'S DOING AS MUCH AS THE IMPORT'S, and it is the reason this
    server is a CALLABLE rather than a mapping: MEASURED with the budget edited 120 -> 500, this
    test raises at exactly 500 and PASSES, while a 499-page mapping in its place returns 499 rows,
    raises nothing, and is red whether or not the ceiling exists (`_flat` carries the table)."""
    api, seen = _flat(lambda page: [{"id": page, "title": f"p{page}"}], page_size=5)

    with pytest.raises(VikunjaError) as exc:
        api.projects()
    assert exc.value.status == 508
    assert "/projects" in exc.value.message and "NOTHING is returned" in exc.value.message
    assert len(seen) == MAX_UNPROVEN_PAGES   # the ceiling'th request is the LAST one sent


def test_neither_reader_takes_a_SHORT_page_for_an_exhausted_one():
    """WAS `test_the_page_size_threshold_is_one_shared_rule_not_a_copy`, and its subject is gone:
    VMCP-127 (608) DELETED `_could_be_full`, so there is no threshold left to share. The four
    arithmetic asserts that stood here went with it — a helper that no longer exists cannot be
    mutated — and what is left is the BEHAVIOUR both readers were meant to have all along.

    HISTORY, because it is the reason the deletion is the fix and not a simplification. VMCP-89/92/
    103 were three cards spent correcting ONE expression in one branch at a time; VMCP-108 made it
    a single module-level function so the next correction would land in both readers; VMCP-111
    (582) then measured that only ONE of the two operands was load-bearing; VMCP-124 (603) measured
    that the surviving "superset by construction" argument was FALSE on a server that serves more
    than it states. VMCP-127 measured the rest: BOTH readers truncated, both /info branches spelled
    one and the same unsound inference, and no choice of threshold repairs it.

    WHAT THIS PINS NOW. Both servers below serve 2 rows at a time while /info states 5 — every page
    is SHORT of the stated size, and the last one is empty. Re-introduce a STATED-ONLY length stop
    and the flat read comes back [1, 2] in 1 request (MEASURED on this tree). A threshold made
    UNREACHABLE at `_paged_list`'s call site did the same on the pre-127 tree, where this test still
    went by its old name — that is the measurement the parenthetical here used to cite.

    IT USED TO SAY "ANY length-based stop", AND THAT WAS FALSE — VMCP-121 (596) measured it on this
    tree. Put back the stop 127 actually DELETED — `len(items) >= min(stated, longest served)`, in
    either of its two orderings — and this test stays GREEN, because that bar is 0 on the first page
    and 2 after it, and this server never serves less than it. 23 other tests in this file catch
    that re-introduction; this one does not. The over-broad word is not a detail: it invited exactly
    the "the rule was put back and my test stayed green, so the rule is decorative" reading that 596
    was filed to settle. What this server is built to catch is a bar that consults the STATED size,
    and that is the claim it can carry.

    (VMCP-111 (582) landed one more correction on the deleted asserts while this card was in
    build — a note that "pins the SERVED operand" and "catches the SERVED-ONLY direction" name
    opposite ends of one mutation and must not be mixed. It is retired with them: there is no
    call to substitute an operand into. Recorded so the collision is not re-discovered.)

    THE NESTED READ BELOW STILL PINS LESS THAN IT LOOKS — VMCP-111 corrected that claim twice, and
    it survives the deletion unchanged. `view_tasks`' stop rule is a DISJUNCTION whose first term
    is `added_new_required`, and these windows never repeat, so fresh required ids carry the read
    single-handed whatever the second term says. It is kept as the both-readers half of the
    statement, not as a mutation-tight pin; the reads that DO bite on the nested side are in the
    VMCP-127 section at the end of this file, which repeat a window so that the second term has to
    decide."""
    flat, seen = _flat({1: [{"id": 1}, {"id": 2}], 2: [{"id": 3}, {"id": 4}], 3: []}, page_size=5)
    assert [x["id"] for x in flat.labels()] == [1, 2, 3, 4]
    assert seen == [1, 2, 3]

    nested, pages = _tracker(_short_non_final_pages({"Build": [[1, 2], [3, 4]]}),
                             info_status=200, page_size=5)
    board = nested.view_tasks(3, 11, require_titles={"Build"})
    assert sorted(t["id"] for t in board[0]["tasks"]) == [1, 2, 3, 4]
    assert len(pages) == 3


# --- VMCP-111 (582): the STATED operand of the cap, on BOTH readers -----------------------------
#
# READ THIS FIRST — VMCP-127 (608) DELETED THE CAP THIS SECTION WAS WRITTEN ABOUT, and the two
# tests survive it with their DATA assertions untouched. `_could_be_full(stated, longest_served)`
# no longer exists; neither reader infers exhaustion from a page's length at all, so there are no
# longer two operands to keep load-bearing. What is preserved below is the SERVER — the only
# fixture in this file where /info's stated size and the length the server actually serves
# DISAGREE — and the property that a read of it must come back WHOLE. That property is now
# guaranteed for a much wider class of servers (the VMCP-127 section at the end of this file
# measures the whole band), so these two are no longer the boundary; they are the shape that first
# proved the old rule could not hold it.
#
# MEASURED when 127 landed, whole unit suite, each of the two run on the changed tree: the nested
# test passes BYTE-IDENTICALLY (Build[1..11] in 4 requests — the deleted cap was doing nothing for
# it that `added_new_required` was not); the flat one keeps its 12 rows and its request count moves
# 3 -> 4, because the read no longer ends on page 3 being short. That +1 is the whole of what this
# card cost these two tests, and it is the same +1 the cost table in the VMCP-127 section names.
#
# EVERYTHING BELOW THIS LINE IS THE ORIGINAL 582 RATIONALE, KEPT AS HISTORY, and every mutation it
# describes is a mutation of code that is gone. It is not maintained against the current tree; the
# one sentence still worth acting on is its last paragraph's warning about the sweeps, which 127
# re-measured and made WORSE (see the VMCP-127 section).
#
# A cap with two operands needs both of them load-bearing, and until this section only one was.
# MEASURED on the tree at c66057c, each mutation applied alone with __pycache__ cleared between
# rounds and the WHOLE unit suite run (591 tests):
#
#   `could_be_full = _could_be_full(page_size, longest_page)` -> `longest_page`   in view_tasks
#                                                                                 => 591 passed
#   the same substitution                                     -> `longest_page`   in _paged_list
#                                                                                 => 591 passed
#   -> `page_size if page_size is not None else longest_page` in view_tasks       => RED
#   -> the same                                               in _paged_list      => RED
#
# So the whole stated cap could be deleted from BOTH readers with a green suite as cover — which
# is precisely the "simplified away by a later refactor" failure the repo's mutation-check
# discipline exists against, on the ONE expression this repo has now got wrong three times.
#
# WHY nothing saw it, and why it is a property of the test data rather than an oversight. ALMOST
# every server modelled above serves at most what /info states — `_offset_pages` cuts windows at
# `page_size`, and the sweep driving `_short_non_final_pages` (which is a pass-through: it serves
# whatever windows it is handed) draws their lengths from `randint(1, max(1, page_size - 1))`,
# never offering it a page size of 1 — so every window is short of the stated size — and wherever
# `longest_served <= stated` the cap collapses to `served`, so the stated operand is arithmetically
# dead and deleting it is a no-op.
#
# "ALMOST", not "every": this section's first draft claimed the universal and it is FALSE.
# INSTRUMENTED — every call to `_could_be_full` across the whole of tests/unit, at the 613-test tree
# this paragraph was written against: 2767 calls, 1383 of them degraded (`stated=None`, where the
# mutation is a no-op by definition) and 1384 with a known stated. Read the three totals as a
# snapshot, not an invariant — any test that pages moves them, and rebasing this very commit onto a
# sibling moved them from 2607/1383/1224 without touching anything below. The count that MATTERS is
# a snapshot of the same kind, just a far slower-moving one: exactly SEVEN calls have
# `stated < served` at that tree. The difference is drift RATE, not immunity — the totals move
# whenever ANY test pages, the seven moves only when a test models a server that OVER-serves its
# stated size. Slower is not never, and this family writes on exactly that shape: VMCP-124 (603)
# landed on this fixture between two rounds of this card, and re-instrumenting after it returned
# the same 2767/1383/1384 and the same seven only because it scoped the sweeps instead of adding an
# over-serving server. Re-measure the seven; do not inherit it. Five of the seven are this
# section's own two tests. Of the other two, one is the arithmetic assert `_could_be_full(5, 9)`
# above — not a read — and one
# is a REAL READ that predates this section: test_an_endpoint_that_ignores_page_terminates_without
# _duplicating_rows serves ELEVEN rows against a stated 5. It is blind for a DIFFERENT reason than
# arithmetic deadness, and the distinction matters because it is the same reason the flat test
# below needs a PARTIAL repeat: its page 2 is a pure repeat, `added_new` is False, and
# `_paged_list`'s stop rule is a conjunction, so the read ends before the threshold can matter.
# MEASURED on that exact fixture — shipped, served-only, stated-only and an unreachable `10**9` all
# return 11 rows in 2 requests, identically.
#
# THE SWEEPS ARE BLIND FOR A THIRD REASON, and it is the strongest of the three, because it holds
# for every seed rather than for these fixtures: both sweeps cross-check a HEALTHY read against a
# DEGRADED one, and the degraded rule IS `longest_served` (stated is None there). So the served-
# only mutant makes the healthy reader COMPUTE the degraded reader, and `assert healthy ==
# degraded` becomes a TAUTOLOGY under it — unfalsifiable, not merely unlucky. MEASURED: 200
# randomized rounds of sweep 1's own generator, healthy identical to degraded (pages AND board) in
# 200/200 under the mutant. Widening sweep 2 so pages MAY overshoot the stated size — the obvious
# "randomize harder" answer — does not help either: 300 rounds, still 300/300 identical, and the
# healthy read never truncates even on shipped code, because that sweep hands its server fresh
# ids on every page and the DISJUNCTION above lets `added_new_required` carry those reads whatever
# the threshold says. (Re-measured on today's tree: 270 of those 300 rounds really do over-serve,
# and the equality still holds in all 300. Since VMCP-124 (603) that widened generator ALSO trips
# the sweep's own `assert max(served_lengths) <= page_size` — a different red, about scope rather
# than about the mutant; `_serving_lengths` records the same experiment from 603's side.)
# Only a REPEAT window makes the threshold load-bearing, which is why the two tests below are
# constructed rather than swept.
#
# The two operands therefore only separate on a server that serves a page LONGER than /info
# states — the "fluke-long page" the shared-rule test's own assert pins the stated size against
# (`_could_be_full(5, 9) == 5`). The phrase is that assert's, not the helper's: `fluke` appears
# nowhere in api.py and never has, and an earlier draft of this paragraph credited it there.
#
# THAT SERVER IS REAL, AND THIS PARAGRAPH USED TO SAY IT WAS NOT. Until VMCP-124 (603) FALSIFIED
# it, this paragraph read "CONSTRUCTED, not measured against a real 2.3.0 ... which a real
# container also refused to produce", and offered an under-reported max_items_per_page as merely
# the same CLASS of fact as `_total_pages` being wrong in both directions. 603 replaced the analogy
# with direct evidence and then re-scoped its own first draft of it (see the api.py note above
# `_page_size`, and test_neither_read_loses_a_task_an_honest_server_paginates — VMCP-130 (616)
# renamed it out of the superset claim — in this same
# file far above): on a 2.3.0 instance stating max_items_per_page=5, GET /projects serves pages of
# EIGHT — five real rows plus a CONSTANT 3-row pseudo tail appended after the SQL limit — while
# paging the real ids honestly. Read every row count in that note as one instance's CONTENT and
# never as an endpoint constant: 603 measured 4 views and 63 buckets on its container, and
# VMCP-108 (577) had already measured 10 views and 11 buckets on another, both stating 5. THAT
# SPLIT IS api.py's OWN, and this clause used to hand both pairs to 603: the 10/11 survey is 577's,
# under the "FOURTH MEMBER OF THE 543/548/562 FAMILY" heading in the note above `_paged_list`
# (entered in c66057c), and the sentence immediately after it there assigns ONLY the 4/63 to 603.
# 603's own first probe DID re-see 10 and 11 independently — recorded verbatim in the DESCRIPTION
# of VMCP-127 (608), the card 603's agent filed, under "not cited from VMCP-108" — so those numbers
# were never invented; only their provenance was wrong.
#
# AND THE BAND THESE TWO TESTS ARE BUILT ON WAS OBSERVED THERE TOO, not just the long page. That
# same read's page 7 serves SEVEN rows: full by the stated measure, SHORT of the served 8 — the
# exact BAND the fixtures below construct. Band, not window: they serve 5 and 6, and what all
# three share is [stated 5, served 8). Through this client BEFORE VMCP-127 (608), `projects()`
# returned 34 rows in 8 requests with /info up and 7 with /info down, the degraded bar of 8
# stopping a page earlier than the healthy min(5, 8). That difference was never a thought
# experiment; it cost a request on a real endpoint. PAST TENSE SINCE 8b4bfa5 — there is no bar on
# either branch now, so both /info states read that shape identically, and api.py's own copy of
# this measurement carries exactly that instruction ("Read it in the past tense") above it.
#
# WHAT WAS STILL UNOBSERVED IS THE ONE REMAINING INGREDIENT — ROWS BEHIND THAT SHORT PAGE, i.e. the
# LOSS. Live, the pseudo tail is constant, so the page short of the bar was the LAST one carrying
# real rows and the read was over anyway; 603 had to move that page off the end before anything
# disappeared. Do not restate the reason API.PY'S `_page_size` NOTE used to give for the gap — that
# the endpoints which over-serve are PRECISELY the ones ignoring `?page=`, so their next page is
# always a pure repeat — because 603 measured it FALSE, /projects being the counterexample. (That
# warning used to read "the reason THIS COMMENT used to give", and THIS comment never gave it:
# `git log -S'PRECISELY the ones' -- tests/unit/test_api_kanban.py` dates the phrase's ARRIVAL in
# this file to ea4e059, the commit that wrote the warning, with nothing before it, and the block it
# replaced says nothing about `?page=` at all. (Its SECOND hit is this correction, which quotes the
# phrase back — writing a `git log -S` command into the file it interrogates changes that command's
# answer, so read it as "arrived at", not as "returns exactly one".) The
# sentence lived in api.py under its own "THE REASON THIS COMMENT USED TO GIVE IS MEASURED FALSE"
# heading and was lifted across without re-pointing the deictic — worse here than anywhere, because
# "that note" two sentences earlier already means api.py's. VMCP-103's separate probe still stands,
# and is a DIFFERENT shape: a page short of the STATED size with rows behind it could not be
# produced on the kanban tasks endpoint. Welding those two together is precisely what the old
# parenthetical got wrong.) So nobody had watched this client lose a task here, and these tests did
# not claim otherwise: what they pinned WHILE THE BAR EXISTED is that once the rows ARE behind it,
# the stated operand is what keeps ONE long page from raising the bar for every page after it. What
# they pin NOW is at the top of this section — the same server, read WHOLE, no bar left to raise.
#
# AND THE WAY THAT SENTENCE WENT WRONG IS THIS CARD'S OWN SUBJECT. What a sibling falsified was
# PROSE, not a number. TWO rounds re-measured 2767/1383/1384 and the SEVEN against each rebased
# tree and left the non-numeric claim standing — d1fc4ea and f00f1e6. Round 1 instrumented nothing
# at all (no 2767, no SEVEN anywhere in 6e4de09) and round 4 is the retraction, so the COMPOUND is
# true of exactly two; this sentence used to say three. One of the two is the round that read 603's
# diff and cited it in the paragraph on the SEVEN's drift rate, above. "Re-measure the seven; do
# not inherit it" was written for the counts and is owed to the sentences too.
#
# THIS CARD'S OWN FALSE SENTENCES, ENUMERATED RATHER THAN COUNTED. The ordinal that used to stand
# here said "Third over-claim", and the file already recorded more than three. Each entry is a
# sentence this card SHIPPED in this file that a later round MEASURED false; the round it entered
# and the round that retracted it are named so the whole ledger can be re-derived with `git log -S`:
#
#   r1 -> r2  "each reader still routes through it"                     universal from ONE reader
#   r1 -> r2  "every server ... serves AT MOST what /info states"       universal, shipped at TWO
#                                                                       sites; r2 added the "ALMOST"
#   r1 -> r3  "the only shape where the two operands disagree"          universal
#   r2 -> r3  "What is NOT a snapshot is the count that matters:        an IMMUNITY claim; r3
#              exactly SEVEN calls have `stated < served`"              replaced it with drift RATE
#   r1 -> r4  the "fluke-long page" credited to the helper's docstring  PROVENANCE
#   r1 -> r4  "CONSTRUCTED, not measured against a real 2.3.0"          universal negative
#   r4 -> r5  "If it lands, these two go red BY DESIGN"                 PREDICTION
#   r4 -> r5  "the reason THIS COMMENT used to give"                    PROVENANCE (deictic)
#   r4 -> r5  "the same card measured 10 views and 11 buckets"          PROVENANCE
#   r4 -> r5  "Three rounds re-measured ... and left it standing"       COUNT of itself
#   r4 -> r5  "Third over-claim this card has had to retract"           COUNT of itself
#
# WHERE EACH RETRACTION IS RECORDED: "AT MOST" and "only shape" are the two the SHORTER-window
# docstring's closing parenthetical quotes back — CASE-FOLDED, not verbatim, and that matters (see
# the next sentence); "each reader still routes through it" by the shared-rule docstring's
# "corrected that claim twice"; "AT MOST" also by the "ALMOST, not every" line above; the SEVEN's
# immunity claim by the drift-rate paragraph above; the PREDICTION by the last paragraph of this
# block, BELOW; "Third over-claim" by this list's own opening sentence; the rest above.
#
# AND GREP BOTH CASES. The "AT MOST" universal was shipped in capitals and quoted back in lower
# case, and only the CAPITALS form dates it right: `git log -S'serves AT MOST what /info states'`
# returns 6e4de09 and d1fc4ea (entered r1, gone r2 — correct), while the lower-case form returns
# d1fc4ea and f00f1e6 and reads as r2 -> r3. A draft of this very list shipped that wrong pair.
#
# NOT counted, deliberately, and both were checked rather than overlooked: (a) the three
# `_short_non_final_pages` attributions re-pointed in round 3 — the round-3 review measured them and
# ruled them harmless ("true of every caller in the file"), asking for NO action, so they are a
# re-pointing rather than a retraction; (b) round 4's "the exact WINDOW the fixtures below
# construct", sharpened to BAND above — the round-5 review used "window" approvingly and asked only
# that it be folded in if the sentence was being touched anyway, so it is terminology, not a
# measured falsification. Both are borderline; excluded on those rulings, and named so the next
# reader can disagree with the ruling rather than assume an oversight.
#
# ELEVEN entries, and NOT ONE OF THEM IS A NUMBER — read the list: every entry is a sentence. That
# is the finding. Separately and more narrowly: round 4's measurements — 2767/1383/1384, the SEVEN
# over the same four tests, the 3x2 mutation table — were each reproduced exactly by two independent
# reviewers. The last two entries are this card's disease turned on itself: a sentence ABOUT the
# retractions, retracted.
#
# Three of the eleven are provenance and `git log -S` on the exact phrase settles all three in
# seconds; two more are counts over this card's own commits, settled by `git show <rev>:<path> |
# grep`; the PREDICTION needed nothing but running the two tests below against the sibling's api.py.
# The one instance of that discipline I can point to in five rounds is round 4 counting `fluke` at
# five separate revisions — which got that claim right, while the same rewrite shipped five
# unchecked sentences across four of its paragraphs. (Stated as what I found, not as a census: I did
# not re-read every line of every round's worklog.) Doing it once is not doing it. Run it before
# writing "X says", "this used to", or "card N measured" — an attribution is a claim like any other.
#
# WHAT THE FORWARD LINK SAID, AND WHAT ACTUALLY HAPPENED — kept rather than quietly deleted,
# because a false "by design" is how a live pin gets deleted with a green suite as cover. It read:
# "VMCP-127 (608) is open on this exact shape, and the fix it proposes DELETES the stated operand
# from both bars ... If it lands, these two go red BY DESIGN — read that note before 'fixing'
# them." 608 landed as 8b4bfa5 eight and a half minutes after this section's own commit (AUTHOR
# timestamps, ea4e059 00:48:33 -> 8b4bfa5 00:57:07; the committer stamps a rebase rewrote are
# 6 min 27 s apart), and it is false in both of its checkable parts — the COUNT of tests that go
# red, and WHAT goes red in the one that moved. MEASURED here rather than
# inherited: `git archive ea4e059` with ONLY src/vikunja_mcp/api.py swapped for 8b4bfa5's — these
# two tests exactly as they shipped, against 127's fix —
#
#   ..._still_reads_the_board_whole   PASSED, untouched
#   ..._still_reads_the_list_whole    FAILED at `assert seen == [1, 2, 3]` and nowhere else; its
#                                     row assertion on the line above passed
#
# — so ONE test moved, not two, and in that one only the REQUEST COUNT. Both DATA assertions stood,
# which is why 127 needed ONE assertion changed across the pair (`[1, 2, 3]` -> `[1, 2, 3, 4]`) and
# nothing at all in the board test's body. The accurate account is the header at the TOP of this
# section, written by 127 itself, and it is deliberately not restated here.
#
# ITS POINTER, SEPARATELY, WAS NOT WRONG — a sibling moved the target under a correct citation, and
# that is a different thing from the retractions above, so it is not one of the eleven. "(api.py's
# `_page_size` note carries the w-table and the four costs)" was accurate at ea4e059: that note held
# the w RESULT and the whole cost discussion and closed by naming 608 as the card that would carry
# the full table. 127 then WROTE that table into api.py — as a pure addition, not a move — in its
# own `# VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block, which is where to look now. Find it
# by that heading, not by a direction word: the block sits just BELOW `_MAX_UNPROVEN_PAGES`, and
# the cross-references to it USED to say "the block above `_MAX_UNPROVEN_PAGES`" — 127's text, left
# alone here because it was that card's slice. VMCP-132 (621) then retired all five of them for
# this same heading form: THREE in api.py and TWO in this file, in the docstrings of
# test_a_paged_read_never_stops_on_the_total_pages_header and
# test_a_server_serving_MORE_than_it_stated_still_reads_the_list_whole. One site on each side was
# LINE-WRAPPED across two physical lines, which is why 621's own card found only three of the five.


def test_a_server_serving_MORE_than_it_stated_still_reads_the_board_whole():
    """The nested reader's half. /info states max_items_per_page=5; Build serves EIGHT on page 1,
    so the longest page the server has PROVEN it can serve (8) overshoots the size it stated (5).
    Page 2 then REPEATS a window of five: full by the stated measure, SHORT of the served one. Done
    adds a new task on every page, so "nothing new arrived" alone cannot end the read.

    MEASURED on this exact server when it was written (real httpx, real api.py): shipped read
    Build[1..11] in 4 requests; dropping the stated cap so the bar became the served 8 made the
    repeat of five read as short, the read ended after 2 requests, and Build came back [1..8] —
    9, 10 and 11 silently gone. A required bucket cut off while it is still PRODUCING is the exact
    defect of 543/548/562, and it is what `workspace --gc` turns into a reaped LIVE worktree
    (VMCP-89).

    VMCP-127 (608) deleted both operands, and this read is UNCHANGED by that — same 4 requests,
    same Build[1..11]. The mutation it used to describe no longer has a call site to be applied at;
    what breaks this read now is re-introducing ANY length-based stop into `view_tasks`, which is
    what the VMCP-127 section at the end of this file mutation-checks directly. Kept because w = 5
    (a repeat exactly as long as the stated size) is the one column of that card's table where the
    OLD rule happened to be right, and a boundary is worth pinning from the safe side too."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = {1: list(range(1, 9)), 2: [1, 2, 3, 4, 5], 3: [9, 10, 11]}.get(page, [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}] if page <= 6 else []},
        ])

    api, pages = _tracker(handler, info_status=200, page_size=5)     # STATED 5, SERVED 8
    board = api.view_tasks(3, 11, require_titles={"Build"})

    by_title = {b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}
    assert by_title["Build"] == list(range(1, 12))      # mutant: [1..8]
    assert len(pages) == 4                              # mutant: 2


def test_a_server_serving_MORE_than_it_stated_still_reads_the_list_whole():
    """The flat reader's half, and it needs its OWN shape rather than the board's: `_paged_list`
    dedupes one flat list, so a window of pure repeats brings nothing new and `added_new` ends the
    read before the threshold is ever consulted (the board's Done bucket is what keeps `added_new`
    alive there). So the repeat here is PARTIAL — five seen rows and one new one.

    /info states 5, page 1 serves EIGHT, page 2 serves six (>= the stated 5, short of the served
    8) of which only row 9 is new. MEASURED when it was written: shipped read all 12 rows in 3
    requests; with the bar raised to the served 8 the six-row page read as short and the read
    stopped at 2 requests with 9 rows. Not cosmetic for a flat list either — every caller of these
    endpoints acts on ABSENCE (setup creates the project it cannot see, get_or_create_label mints a
    duplicate, and a short comment read hides the NEWEST rows, which is where the [worklog] and the
    human's answer are).

    VMCP-127 (608) MOVED THE REQUEST COUNT 3 -> 4 and left the rows alone. Page 3 is partial, and a
    partial last page no longer ends a flat read — the read now ends on page 4 bringing nothing.
    That is the one shape where this card is measurably not free (live equivalent: labels() over 22
    labels at max_items_per_page=5, 6 requests -> 7); it is stated in full in the VMCP-127 section
    below and in api.py's module-level `VMCP-127 (608) — THE FULLNESS INFERENCE IS GONE` block."""
    api, seen = _flat({
        1: [{"id": i} for i in range(1, 9)],            # EIGHT served against a stated 5
        2: [{"id": i} for i in (1, 2, 3, 4, 5, 9)],     # full by the STATED measure, short of 8
        3: [{"id": i} for i in (10, 11, 12)],
    }, page_size=5)

    assert [x["id"] for x in api.labels()] == list(range(1, 13))
    assert seen == [1, 2, 3, 4]         # 3 before VMCP-127: the partial page 3 used to end it


# --- VMCP-116 (589): a page with NO ROWS ends a flat read, header or no header -------------------
#
# The card read `if not items: break` as the one place where something silently outranks
# `x-pagination-total-pages`, whose own helper says it is "NEVER a stop signal". MEASURED on the
# card's exact shape (real httpx over real api.py — page1=5 rows, page2 empty, page3=5 rows, every
# response stating 3 pages): nothing outranks the header there, because the header never stands
# alone. It reaches the stop rule only ANDed with `added_new`, and an empty page adds nothing.
# Hence, each mutation applied alone with __pycache__ cleared:
#
#   delete `if not items: break` entirely        => NO behaviour change at all (5 rows in 2
#                                                   requests, byte-identical on five shapes) and
#                                                   the whole unit suite still 600 passed
#   the card's own `not items and not header_more`
#                                                => the same, inert for the same reason
#
# So the green suite the card reported was a NO-OP, not a coverage gap — worth recording, because
# "the mutation stayed green" is normally evidence of the opposite. What is genuinely unpinned is
# the BEHAVIOUR, and reaching it takes both spellings of the one rule at once: skip the early-out
# on an empty page AND let that page keep the read going. That is the card's option (b), refused
# in api.py for a measured reason (it hands the loop's bound to a header this very server
# over-reports), and this test is what makes the refusal fail loudly if someone implements it.
# It also pins the `else []` normalization, which is the other half of option (b) — see below.

@pytest.mark.parametrize("body, what", [
    ([], "an empty list"),
    (None, "a JSON null — how an empty list reaches this client at all"),
    ({"message": "not a list"}, "a 200 whose body is not a list"),
])
def test_a_page_with_no_rows_ends_a_flat_read_even_when_the_header_says_there_is_more(body, what):
    """The flat twin of test_a_page_filtered_down_to_nothing_still_ends_the_read (VMCP-103), and
    the same LIMIT stated for the other reader: rows behind a page that came back with nothing are
    not fetched, even though the header says there is another page. An empty window and an
    exhausted list are the same observation, and for offset pagination over a stable list the empty
    page is the ordinary way a read ends.

    All three bodies are the SAME observation to this reader on purpose: `_req` hands back None for
    an empty response body, a Go nil slice marshals to `null`, and only an object is a genuine
    protocol violation — treating any of them as an error would break the ordinary empty read.

    TWO mutations kill this, and neither is the line the card named:
      * option (b), faithfully implemented — `empty = not items`, skip the early-out while
        `_total_pages(headers) > page`, and add `or empty` to the `added_new` conjunct: the read
        goes on to page 3 and returns 10 rows in 4 requests. Note it does NOT break
        test_an_endpoint_that_ignores_page_terminates_without_duplicating_rows, whose server never
        serves an empty page — this test is the only thing standing in front of it.
      * `items = body` (drop the `else []`): a non-list body is truthy, so the loop walks the
        dict's KEYS and merges the string "message" as a row.
    Neither can hang: `_flat` carries the harness cap (VMCP-119's trap)."""
    api, seen = _flat({
        1: [{"id": i} for i in range(1, 6)],        # full at the stated page size
        2: body,                                    # nothing came back — the read ends HERE
        3: [{"id": i} for i in range(6, 11)],       # never asked for
    }, page_size=5, total_pages=3)                  # ... though the header insists it exists

    assert [x["id"] for x in api.labels()] == [1, 2, 3, 4, 5], what     # mutant: [1..10]
    assert seen == [1, 2], what                                        # mutant: [1, 2, 3, 4]


# --- VMCP-120 (595): pages the stated page size justifies cost a FLAT read nothing --------------
#
# The flat mirror of test_pages_the_stated_page_size_justifies_cost_the_ceiling_nothing, which has
# stood behind the same guard in `view_tasks` since VMCP-103 and says so in its own docstring
# ("make the counter unconditional ... and this goes red at request 121"). `_paged_list` had no
# such test at all, so ITS guard — `if page_size is None or len(items) < page_size` — could be
# charged unconditionally with a green suite as cover.
#
# And unlike VMCP-116 directly above, this card's mutation is NOT inert. MEASURED by construction
# (real httpx over real api.py, request-capped so a mutation goes RED rather than hanging) against
# an HONEST server — /info states max_items_per_page=5 and every page serves exactly 5, the rate
# the server itself advertised:
#
#   full pages   shipped                         `if True:` (charge EVERY page)
#   ----------   -----------------------------   ----------------------------------------------
#      119       595 rows in 120 requests        595 rows in 120 requests — IDENTICAL, still under
#      121       605 rows in 122 requests        RAISED 508 at request 120, nothing returned
#      160       800 rows in 161 requests        RAISED 508 at request 120, nothing returned
#
# — and with that mutation applied alone (`__pycache__` cleared) the whole unit suite stayed GREEN
# at 612 passed. So an honest long list read at the advertised rate would have started raising 508
# instead of completing, and nothing in the suite would have said a word.
#
# Not cosmetic, because this is the reader every flat list uses: `projects()`, `labels()`,
# `comments()` and `project_users()`, so the mutant's 508 is a hard failure of `setup`, `next_task`
# and every tool that reads a card's comments. At Vikunja's own default max_items_per_page of 50
# that cliff sits at 6 000 rows. A bound that turns a big honest list into an error is a worse bug
# than the hang it exists to prevent — the same sentence the nested twin was written for.
#
# The 119-page row is why the shape below is far past the ceiling instead of one page over it: the
# two sides AGREE under the ceiling, so a shrunken fixture would keep this test green with the
# mutation applied and pin nothing. That is what the request-count assertion guards.


def test_pages_the_stated_page_size_justifies_cost_a_flat_read_nothing():
    """An honest list far past the ceiling in PAGES must still read WHOLE — 800 rows is not an
    error. Every page here comes back full at the stated 5, so `max_items_per_page` accounts for
    all 160 of them and the unproven-page budget is never touched; the only unaccounted-for page is
    the trailing empty one, which breaks out of the loop before it can be charged.

    Charge every page instead (`if page_size is None or len(items) < page_size:` -> `if True:`) and
    this goes RED at request 121 with a 508 and no rows at all — the regression the guard exists to
    prevent, and word for word what the nested twin
    test_pages_the_stated_page_size_justifies_cost_the_ceiling_nothing says for `view_tasks`.

    What it does NOT hold, stated so nobody reads more into it: the OPPOSITE direction of the same
    guard — never charging a page, so the ceiling can no longer stop a degraded read. That belongs
    to test_a_list_that_never_finishes_paging_raises_instead_of_truncating, which used to HANG on
    that mutation rather than fail — it was the one runaway-read test in this file built on a bare
    `make_api`. VMCP-119 (594) moved it onto `_flat` too, and MEASURED the direction this paragraph
    hands off: `if page_size is None or len(items) < page_size:` -> `if False:`, so no page is ever
    charged, is now RED there in ~1 s on the harness cap. Neither test can hang whatever is
    mutated."""
    full_pages = MAX_UNPROVEN_PAGES + 40                    # 160 pages, far past the ceiling
    ids = list(range(1, 5 * full_pages + 1))                # 800 rows served 5 at a time
    api, seen = _flat({p: [{"id": i} for i in ids[(p - 1) * 5:p * 5]]
                       for p in range(1, full_pages + 1)},
                      page_size=5, total_pages=full_pages)

    assert [x["id"] for x in api.labels()] == ids           # mutant: VikunjaError 508, no rows
    assert seen == list(range(1, full_pages + 2))           # 160 full pages + the empty stop
    # ... and the read really did outrun the ceiling — below it the mutation is invisible (see the
    # 119-page row above), so a fixture that shrank would leave this test green and pinning nothing
    assert len(seen) > MAX_UNPROVEN_PAGES


# --- VMCP-127 (608): the FULLNESS INFERENCE is gone, on BOTH readers ----------------------------
#
# `_could_be_full(stated, longest_served)` is deleted. Both readers used to stop on one and the
# same inference — "a page shorter than the bar means this bucket/list is exhausted" — and the bar
# only ever decided which servers they got away with, because a page short of the server's real
# page size can still have rows behind it. VMCP-89/92/103/108/111/124 are six cards spent on that
# threshold; this one removes the thing they were correcting.
#
# THE TABLE THE TESTS BELOW ARE. MEASURED on the pre-127 tree at f4faab5 (2026-07-31, real httpx
# over MockTransport, real api.py) — /info states 5, page 1 serves EIGHT, page 2 repeats a window
# of w already-seen rows, page 3 holds three more, and a second bucket / one new row per page keeps
# "nothing new arrived" from ending the read on its own:
#
#     w      nested healthy  nested degraded  flat healthy  flat degraded
#     1..4   LOSS            LOSS             LOSS          LOSS
#     5..7   whole           LOSS             whole         LOSS
#     8      whole           whole            whole         whole
#
# — the healthy read lost rows for every w < the STATED size, the degraded one for every w < the
# longest SERVED. On the current tree every one of those sixteen cells reads WHOLE, in 4 requests,
# which is what test_the_board_is_read_whole_whatever_the_repeat_window_is and its flat twin pin.
#
# AND THE CONTROL, which is what makes the pins parametrize over a server that does NOT over-serve:
# page 1 serving EXACTLY the stated 5 loses the same rows on the HEALTHY read for every w < 5.
# Over-serving is one way to reach the defect, not its condition — the trigger is a short non-final
# REPEAT window. Fixtures built only from over-serving servers would cover half the surface.
#
# WHAT IT COSTS, pinned from BOTH sides by test_the_extra_request_is_paid_only_by_a_partial_last_
# page: +1 request on a flat read whose last content page is short, +0 everywhere else, including
# the shapes a live 2.3.0 actually serves. The nested read pays nothing at all. And a NEW 508 on
# two shapes that used to complete — pinned deliberately, not tolerated, by the two `_RAISES_`
# tests below: this module's standing rule is that a truncated read must never pass for a complete
# one, and the client cannot tell the server that stops legitimately from the one hiding rows.
#
# THE SWEEPS CANNOT SEE ANY OF THIS, AND ARE NOW WORSE THAN 111 RECORDED. Both cross-check a
# HEALTHY read against a DEGRADED one; 582 measured that a served-only mutant made that comparison
# a tautology. It is no longer a property of a mutant: the stop rule mentions no page size at all,
# so healthy and degraded execute the SAME expression and `assert healthy == degraded` cannot fail
# for any read that stays under the ceiling, on any seed. MEASURED — `keep_going =
# added_new_required` (drop VMCP-92's repeat clause entirely), applied alone with __pycache__
# cleared: test_neither_read_loses_a_task_an_honest_server_paginates and
# test_the_healthy_read_never_loses_a_task_a_server_serves_short both GREEN (2 passed) while 30
# other tests in this file go RED. Do not read those sweeps as coverage of the stop rule; the pins
# in this section are constructed for exactly that reason.
#
# VMCP-130 (616) ACTED ON THAT WARNING WITHOUT WEAKENING IT, and the distinction is the whole point
# of this paragraph. The first sweep was left with NO behavioural assertion at all once the
# cross-check went dead — measured at 8b4bfa5, a `keep_going = False` reader that returns page 1
# and stops passed it — so 616 gave it the completeness assert the second sweep already had. That
# buys a sweep that notices a SHORT board; it buys no coverage of the stop rule, and the row above
# is the proof: RE-MEASURED after 616 with the same `keep_going = added_new_required` mutant and
# __pycache__ cleared, both sweeps are STILL GREEN (2 passed) while 30 other tests in this file go
# RED. Neither generator repeats a window, so `added_new_required` carries every read they make;
# only the constructed pins in this section reach the clause.


def _assert_branch_really_taken(api, info_status):
    """A parametrization over "/info up" and "/info down" is worth nothing if both rows end up on
    the same branch, and that is the exact failure this card's own subject matter is made of. So
    every row below states which branch it reached, read off the client AFTER the read: the
    degraded rows must have resolved the page size to None, the healthy ones to the stated 5."""
    assert api._page_size_resolved is True
    assert api._page_size_cache == (None if info_status != 200 else 5)


def _over_serving_board(w):
    """/info states 5, Build serves EIGHT on page 1, REPEATS a window of w on page 2, and holds
    9, 10, 11 on page 3. Done adds one new task per page so `added_new` alone cannot end the read.
    """
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = {1: list(range(1, 9)), 2: list(range(1, w + 1)), 3: [9, 10, 11]}.get(page, [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}] if page <= 6 else []},
        ])
    return handler


def _honest_board(w):
    """The CONTROL: no over-serving anywhere. Page 1 serves EXACTLY the stated 5, page 2 repeats a
    window of w, page 3 holds 6, 7, 8."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = {1: [1, 2, 3, 4, 5], 2: list(range(1, w + 1)), 3: [6, 7, 8]}.get(page, [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}] if page <= 6 else []},
        ])
    return handler


@pytest.mark.parametrize("info_status", [200, 503], ids=["healthy", "degraded"])
@pytest.mark.parametrize("w", [1, 2, 3, 4, 5, 6, 7, 8])
def test_the_board_is_read_whole_whatever_the_repeat_window_is(w, info_status):
    """The nested half of the table above, all sixteen cells. Build produces 1..11 and the read
    must return all eleven however long the repeat on page 2 is and whether or not /info answered.

    Pre-127 this was RED for w in 1..4 healthy and w in 1..7 degraded, returning Build[1..8] in 2
    requests — a required bucket cut off while it is still producing, which is the defect of
    543/548/562 and what `workspace --gc` turns into a reaped LIVE worktree (VMCP-89).

    Re-introduce any length-based stop into `view_tasks` and the low-w rows go red again. Cannot
    hang whatever is mutated: `_tracker` carries the harness cap."""
    api, pages = _tracker(_over_serving_board(w), info_status=info_status, page_size=5)

    board = api.view_tasks(3, 11, require_titles={"Build"})

    _assert_branch_really_taken(api, info_status)
    by_title = {b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}
    assert by_title["Build"] == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    assert len(pages) == 4          # page 4: Build empty -> the read ends there, not on a length


@pytest.mark.parametrize("info_status", [200, 503], ids=["healthy", "degraded"])
@pytest.mark.parametrize("w", [1, 2, 3, 4])
def test_the_board_is_read_whole_WITHOUT_ANY_OVER_SERVING_EITHER(w, info_status):
    """THE CONTROL, and the reason this section does not model over-serving alone. Page 1 serves
    EXACTLY the size /info stated, so `min(stated, served)` and `served` are the same number and
    nothing about this server is unusual — yet pre-127 the HEALTHY read lost 6, 7 and 8 here for
    every w < 5, identically to the degraded one (MEASURED at f4faab5: 5 rows in 2 requests).

    So the trigger is a short non-final REPEAT window; an over-serving server merely widens the
    band on the degraded side. A test suite built only from over-serving fixtures would have
    covered half of it, which is how VMCP-124 (603) came to be filed against the degraded branch."""
    api, pages = _tracker(_honest_board(w), info_status=info_status, page_size=5)

    board = api.view_tasks(3, 11, require_titles={"Build"})

    _assert_branch_really_taken(api, info_status)
    by_title = {b["title"]: sorted(t["id"] for t in b["tasks"]) for b in board}
    assert by_title["Build"] == [1, 2, 3, 4, 5, 6, 7, 8]
    assert len(pages) == 4


@pytest.mark.parametrize("info_status", [200, 503], ids=["healthy", "degraded"])
@pytest.mark.parametrize("w", [1, 2, 3, 4, 5, 6, 7, 8])
def test_the_list_is_read_whole_whatever_the_repeat_window_is(w, info_status):
    """The flat half of the same table, and it needs its OWN shape: `_paged_list` dedupes one list,
    so a window of PURE repeats brings nothing new and `added_new` ends the read before any
    threshold could matter (the board's Done bucket is what keeps `added_new` alive there). Page 2
    is therefore w rows of which exactly ONE (row 9) is new.

    Pre-127: rows 10, 11, 12 lost for w in 2..4 healthy and w in 2..7 degraded, 9 rows in 2
    requests. Every caller of these endpoints acts on ABSENCE — `setup` creates the project it
    cannot see, `get_or_create_label` mints a duplicate, and a short comment read hides the NEWEST
    rows, which is where the [worklog] and the human's answer live."""
    api, seen = _flat({
        1: [{"id": i} for i in range(1, 9)],                    # EIGHT against a stated 5
        2: [{"id": i} for i in list(range(1, w)) + [9]],        # w rows, exactly one of them new
        3: [{"id": 10}, {"id": 11}, {"id": 12}],
    }, page_size=5, info_status=info_status)
    api._MAX_RETRIES = 0                    # as `_tracker` does: one /info attempt, no backoff

    rows = [x["id"] for x in api.labels()]

    _assert_branch_really_taken(api, info_status)
    assert rows == [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11, 12]
    assert seen == [1, 2, 3, 4]


@pytest.mark.parametrize("pages, page_size, requests, rows, before, why", [
    ({1: [1, 2, 3]}, 5, 2, [1, 2, 3], 2,
     "ONE PARTIAL PAGE: unchanged. The bar was 0 on page 1, so the confirming request was always "
     "paid — this is the shape most live reads have at Vikunja's default page size of 50"),
    ({1: [1, 2, 3, 4, 5]}, 5, 2, [1, 2, 3, 4, 5], 2,
     "one FULL page then an empty one: unchanged"),
    ({}, 5, 1, [], 1, "an empty list: unchanged, one request"),
    ({1: [1, 2, 3, 4, 5], 2: [6, 7, 8, 9, 10]}, 5, 3, list(range(1, 11)), 3,
     "two FULL pages: unchanged — the read already had to confirm past the last full page"),
    ({p: list(range(5 * (p - 1) + 1, 5 * p + 1)) for p in range(1, 5)}, 5, 5, list(range(1, 21)), 5,
     "the live /labels shape at max_items_per_page=5 — 20 rows in four FULL pages: unchanged"),
    ({p: list(range(11)) for p in range(1, 40)}, 5, 2, list(range(11)), 2,
     "the live views/buckets shape — whole list every page, ?page= IGNORED: unchanged, the repeat "
     "brings nothing new"),
    ({1: [1, 2, 3, 4, 5], 2: [6, 7]}, 5, 3, [1, 2, 3, 4, 5, 6, 7], 2,
     "THE COST. The last content page is SHORT, so it used to end the read and now does not"),
    ({1: list(range(1, 51)), 2: list(range(51, 61))}, 50, 3, list(range(1, 61)), 2,
     "THE COST at Vikunja's default page size: 60 rows in 50 + 10"),
])
def test_the_extra_request_is_paid_only_by_a_partial_last_page(
    pages, page_size, requests, rows, before, why
):
    """The cost of deleting the inference, pinned from BOTH sides so nobody has to re-derive it —
    the card that filed this work claimed "+1 request on EVERY flat read" and that is measurably
    false. `before` is what the pre-127 tree at f4faab5 spent on the same server.

    The rule the eight rows spell: +1 exactly when the read spans two or more pages AND its last
    content page is shorter than the page size. Everything else is unchanged, including every shape
    a live 2.3.0 was measured serving. Live equivalents on a real 2.3.0 container the same day, at
    max_items_per_page=5: labels() over 20 labels 6 requests -> 6; over 22 labels 6 -> 7;
    projects() over 35 real projects 10 -> 10; over 37 real projects 10 -> 10 healthy and 8 -> 9
    degraded; views(), buckets(), comments() and view_tasks() all unchanged; and
    Workflow.next_task() end to end 7 requests -> 7."""
    api, seen = _flat({p: [{"id": i} for i in ids] for p, ids in pages.items()},
                      page_size=page_size)

    assert [x["id"] for x in api.labels()] == rows, why
    assert len(seen) == requests, why
    assert before <= requests, why           # this card only ever ADDS requests, never removes


def test_a_required_bucket_repeating_forever_RAISES_instead_of_returning_a_board():
    """THE PRICE OF THE FIX, PINNED AS A DELIBERATE TRADE RATHER THAN LEFT AS A SURPRISE.

    Build holds exactly 1..5 and really is finished, but this server re-serves a SHORT window of it
    on every page instead of an empty one, while Done keeps producing. Pre-127 the read stopped
    after 2 requests and returned a board that happened to be COMPLETE; now it spends the whole
    `_MAX_UNPROVEN_PAGES` budget and RAISES 508 (measured: 121 requests).

    WHY THAT IS THE RIGHT DIRECTION AND NOT A REGRESSION: this server is INDISTINGUISHABLE, from
    inside the client, from the one in test_the_board_is_read_whole_whatever_the_repeat_window_is,
    where the same repeat hides 9, 10 and 11 — there the old rule returned a SHORT BOARD and called
    it complete. A truncated board is indistinguishable from tasks that are genuinely gone, and
    `workspace --gc` reaps worktrees from exactly this read. Raising is how every other bound in
    this module fails.

    AND IT IS NOT WHAT A REAL SERVER DOES: measured on a live Vikunja 2.3.0 the same day, the
    nested endpoint serves honest offset windows and an EMPTY one past a bucket's end (To-Do
    5, 5, 2, 0), and a `?page=`-ignoring server repeats EVERY bucket, so nothing is new anywhere and
    the read still ends after 2 requests (test_a_server_that_ignores_the_page_param_still_stops_
    without_raising)."""
    def handler(request):
        page = int(request.url.params.get("page", 1))
        build = [1, 2, 3, 4, 5] if page == 1 else [1, 2, 3]      # never empty, never new
        return httpx.Response(200, json=[
            {"id": 4, "title": "Build", "tasks": [{"id": i} for i in build]},
            {"id": 9, "title": "Done", "tasks": [{"id": 900 + page}]},
        ])

    api, pages = _tracker(handler, info_status=200, page_size=5)

    with pytest.raises(VikunjaError) as exc:
        api.view_tasks(3, 11, require_titles={"Build"})

    assert exc.value.status == 508
    assert "NOTHING is returned" in exc.value.message
    assert len(pages) == MAX_UNPROVEN_PAGES + 1         # one page justified by the stated size


def test_a_flat_list_that_dribbles_new_rows_forever_RAISES_instead_of_a_partial_list():
    """The flat twin of the trade above, and the one cost VMCP-127's own card did not name. After
    one FULL page this server hands out a single NEW row per page and never stops. Pre-127 the read
    ended on page 2 being short and returned SIX rows of an unbounded list — an answer that was
    neither complete nor marked; now it spends the budget and raises 508.

    Both outcomes are wrong about the server; only one of them says so. Note the direction this
    moved in: `test_a_list_that_never_finishes_paging_raises_instead_of_truncating` already pinned
    the same 508 for a server that never fills a page AT ALL — what changed is that a server which
    fills its FIRST page and then dribbles no longer gets a quiet partial answer.

    THE SERVER IS A CALLABLE, AND THAT IS THE WHOLE OF VMCP-135 (624). It used to be a mapping of
    199 pages, and that page count was a SILENT PRECONDITION of every assertion below, stated
    nowhere. The ceiling costs budget + 1 requests on THIS shape — the full first page is
    justified by `max_items_per_page` and never charged, so it buys a request the budget never
    pays for — while a mapping ends the read by itself on the first page it does not name
    (`.get(page, [])` -> `[]` -> `if not items: break`). The two meet at 199. MEASURED with the
    budget edited in a scratch copy of src/ and the fixture otherwise untouched: budget 197 -> 508
    at 198 requests, 198 -> 508 at 199, and from 199 on DID NOT RAISE — 203 rows in 200 requests.

    PAST THAT LINE IT DID NOT MERELY FAIL, IT STOPPED PINNING — the part worth a card rather than
    a comment, and NOT the same thing as going quietly green. At budget 500 the mapping returned 203
    rows in 200 requests under all THREE states of the guard measured: ceiling as shipped, ceiling
    -> `if False:`, and ceiling returning `merged` instead of raising. It was therefore RED either
    way, on `DID NOT RAISE` — loud, but carrying no information about the ceiling at all, since
    nothing it could observe changed when the ceiling did. What it was actually exercising by then
    are the two guards BELOW it: past page 199 the mapping serves empty pages, so the read ended on
    `if not items` / `if not added_new` and the ceiling was simply unreachable — measured, mutate
    BOTH of those to `if False:` and the very same mapping does reach it, 508 at 501 requests. That
    is the trap: the obvious repair is to grow the mapping, which only moves the same silent
    precondition further out and leaves the name of the test still promising the ceiling. The
    callable has no page count to outgrow — measured at budgets 5 / 37 / 60 / 120 / 240 / 500 /
    1000 it raises 508 at exactly budget + 1 requests every time (that IS the assert below, which
    moves with the constant), while with the ceiling removed it dies on `_flat`'s harness cap
    (3 * budget) in under 2 s instead, so the two states stay distinguishable at every budget.
    That is what lets the assert below be read the way `_flat` and
    test_a_list_that_never_finishes_paging_raises_instead_of_truncating already describe this file:
    it pins WHERE the guard fires relative to the budget, whatever the budget is."""
    api, seen = _flat(
        lambda page: [{"id": i} for i in range(1, 6)] if page == 1 else [{"id": 5 + page}],
        page_size=5,
    )

    with pytest.raises(VikunjaError) as exc:
        api.labels()

    assert exc.value.status == 508
    assert "NOTHING is returned" in exc.value.message
    assert len(seen) == MAX_UNPROVEN_PAGES + 1
