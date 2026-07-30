import json

import httpx
import pytest

from tests.unit.test_api import make_api
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
    assert calls == [1, 2]                        # остановились по "меньше page size", без page=3
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
    working stages instead of rescanning the whole ever-growing Done on every call."""
    calls = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        page = int(request.url.params.get("page", "1"))
        calls.append(page)
        queue = [{"id": 1, "title": "q"}] if page == 1 else []            # exhausted on page 1
        done = ([{"id": 100 + i, "title": f"d{i}"} for i in range(50)]    # full page (would page on)
                if page == 1 else [{"id": 999, "title": "tail"}] if page == 2 else [])
        return httpx.Response(200, json=[
            {"id": 4, "title": "Queue", "tasks": queue},
            {"id": 9, "title": "Done", "tasks": done},
        ])

    api = make_api(handler)
    board = api.view_tasks(3, 11, require_titles={"Queue"})
    assert calls == [1]                              # stopped: only Done had a full page, not required
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert by_title["Queue"] == [1]                  # required bucket complete
    assert 999 not in by_title["Done"]               # Done NOT exhaustively paged (page-2 tail skipped)
    assert len(by_title["Done"]) == 50               # only Done's first page came along — harmless


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
    assert calls == [1, 2]                            # Done's full page DID drive paging
    by_title = {b["title"]: [t["id"] for t in b["tasks"]] for b in board}
    assert 999 in by_title["Done"]                   # Done fully paged to its tail


def test_view_tasks_single_page_unchanged():
    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 50})
        assert request.url.params.get("page") == "1"
        return httpx.Response(
            200, json=[{"id": 4, "title": "Queue", "tasks": [{"id": 1, "title": "only"}]}]
        )

    api = make_api(handler)
    board = api.view_tasks(3, 11)
    assert board == [{"id": 4, "title": "Queue", "tasks": [{"id": 1, "title": "only"}]}]


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
