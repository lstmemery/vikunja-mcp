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
    assert api.get_or_create_label("blocked")["id"] == 5
    assert calls == ["GET"]
    assert api.get_or_create_label("epic")["id"] == 6
    assert calls == ["GET", "GET", "PUT"]


def test_share_project_idempotent():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        if request.method == "GET":
            return httpx.Response(200, json=[{"username": "agent-infra", "permission": 1}])
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.share_project(3, "agent-infra", 1)          # уже есть -> только GET
    assert calls == [("GET", "/api/v1/projects/3/users")]
    api.share_project(3, "agent-voice", 1)           # нет -> GET + PUT
    assert calls[-1] == ("PUT", "/api/v1/projects/3/users")


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
    """VMCP-92's repeat-window edge, moved onto the HEALTHY branch — and the one shape that pins
    the `min(stated, served)` cap rather than the rule around it. Build re-serves its window of 3
    while /info states 5, so the window is short by the STATED measure but exactly full by the
    PROVEN one; Done keeps adding, so "no new required task" alone cannot save the read.

    Compare the threshold against the stated 5 instead of `min(5, longest served)` and Build comes
    back [1,2,3]: the repeat looks short, nothing new arrived in Build, and the read ends three
    tasks early — on a healthy /info, which is the whole complaint of this card."""
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
    """The proven-lower-bound page size is read from the longest page seen on EARLIER pages, not
    from a running maximum updated mid-page — otherwise the same board read differently depending
    on the order the server happened to list its buckets in, which no consumer can control. Here
    Done first serves 4 tasks on the very page where Build repeats its window: both orders must
    return the same board."""
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


def test_the_degraded_read_never_loses_a_task_the_healthy_read_saw():
    """THE invariant of the whole branch, swept rather than argued: 60 randomized boards (page
    sizes 1..50, bucket sizes across several page boundaries, all three require_titles shapes),
    each read TWICE — once with /info healthy, once with it down.

    The degraded read is a SUPERSET by construction: its "could still be full" test uses the
    longest page the server has PROVEN it can serve, which is <= the real page size, so every page
    the known rule would fetch the unknown rule fetches too. On the buckets that drive paging the
    two must therefore agree EXACTLY; elsewhere the degraded read may only ever have MORE (it
    spends one extra page, and that page carries extra Done/Backlog tasks)."""
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

        boards = {}
        for info_status in (200, 503):
            api, _pages = _tracker(_offset_pages(stages, page_size),
                                   info_status=info_status, page_size=page_size)
            boards[info_status] = {b["title"]: sorted(t["id"] for t in b["tasks"])
                                   for b in api.view_tasks(3, 11, require_titles=require)}

        for title, healthy in boards[200].items():
            degraded = boards[503].get(title, [])
            assert set(healthy) <= set(degraded), (title, page_size, require)
            if require is None or title in require:
                assert healthy == degraded, (title, page_size, require)
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
    ceiling permits but never what the board contains."""
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

        boards = {}
        for info_status in (200, 503):
            api, _pages = _tracker(_short_non_final_pages(served),
                                   info_status=info_status, page_size=page_size)
            boards[info_status] = {b["title"]: sorted(t["id"] for t in b["tasks"])
                                   for b in api.view_tasks(3, 11, require_titles=require)}

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
