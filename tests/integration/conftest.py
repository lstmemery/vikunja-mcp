"""Интеграция против реальной Vikunja (docker). Скип без VIKUNJA_TEST_URL."""
import os
import time

import httpx
import pytest

from vikunja_mcp.api import VikunjaError

BASE = os.environ.get("VIKUNJA_TEST_URL", "").rstrip("/")
PASSWORD = "integr4tion-Pass!"

# срез боевых прав агента (roles/vikunja/files/vikunja-bootstrap.py), но с 2 добавками
# найденными интеграционными тестами против реальной 2.3.0 (тот скрипт несёт тот же
# пробел — см. отчёт T10):
# - "other": ["user"] — без него GET /api/v1/user 401-ит для скоуп-токенов, и
#   Workflow._me() не может себя идентифицировать (next_task/claim/advance/... сломаны).
#   routes["other"]["user"] это GET /api/v1/user — не путать с несуществующей
#   верхнеуровневой группой "user" (PUT /tokens 400 The permission of group user is invalid).
# - "projects": добавлен "views_buckets" (GET .../buckets) — без него api.buckets()
#   (список колонок канбана) 401-ит; "views_buckets_tasks" (move) уже был в списке,
#   но одного move недостаточно — Workflow._bucket() сначала читает список.
# - "tasks_attachments": ["read_one", "create"] (#139, #137) — скачивание вложения это
#   GET /tasks/:task/attachments/:attachment (op `read_one`; НЕ `read`; `read_all` — это листинг
#   GET .../attachments), а ЗАГРУЗКА (#137) — PUT /tasks/:task/attachments, и в /routes этот
#   эндпоинт висит на op `create` (проверено на реальной 2.3.0: тот же токен без `create`, только
#   `read_one`, 401-ит на загрузке; POST -> 405, метод строго PUT). Без `read_one` 401-ит
#   Workflow.download_attachment, без `create` — Workflow.attach_file. Человек добавил `read_one`
#   боевым токенам по #139; `create` (#137) он должен до-минтить отдельно (карточка через
#   call_human), а тест минтит свой токен сам, поэтому здесь оба op держат тест в синхроне с
#   ПОЛНЫМ набором прав, к которому прод придёт.
AGENT_PERMS = {
    "tasks": ["read_all", "read_one", "create", "update", "position"],
    "tasks_assignees": ["create", "delete", "read_all"],
    "tasks_comments": ["create", "read_all", "read_one"],
    "tasks_labels": ["create", "read_all"],
    "tasks_relations": ["create", "delete"],
    "tasks_attachments": ["read_one", "create"],
    "projects": ["read_all", "read_one", "views_buckets", "views_buckets_tasks"],
    "projects_views": ["read_all", "read_one"],
    "projects_views_tasks": ["read_all"],
    "labels": ["read_all", "create"],
    "other": ["user"],
}

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


def _api(path):
    return f"{BASE}/api/v1{path}"


def _with_retry(request):
    """login/register делят один anti-bruteforce лимит (наблюдалось: 10/60s,
    заголовки X-Ratelimit-*), который несколько локальных прогонов подряд легко
    выбивают за пределами обычного одного `pytest tests/integration` (см. отчёт T10).
    Ждём до X-Ratelimit-Reset (с фолбэком на экспоненциальный бэкофф) и повторяем."""
    r = request()
    for _ in range(5):
        if r.status_code != 429:
            return r
        reset_at = r.headers.get("X-Ratelimit-Reset")
        wait = max(float(reset_at) - time.time(), 1.0) if reset_at else 2.0
        time.sleep(min(wait, 30.0) + 0.5)
        r = request()
    return r


def seed_row(create, landed, *, attempts=4, backoff=0.25):
    """Create ONE seed row, absorbing the transient 500 a SQLite-backed Vikunja returns under an
    unpaced write burst — VMCP-129 (615).

    WHY IT IS NOT A FLAKE-SILENCER. The 500 lands in a test's SEEDING loop, before a single
    assertion runs: `test_comments_past_the_page_boundary_are_read_and_stay_in_order` never got
    as far as the thing it is about. Measured 2026-07-31 over full-suite runs against FRESH
    containers: 5 of 9 failed, always the same signature. The server side cannot be configured
    out of it — Vikunja 2.3.0 already puts `_busy_timeout=5000` on its SQLite DSN (it appends
    that string itself; feed a second one through VIKUNJA_DATABASE_PATH and the driver reports
    `Invalid _journal: WAL?_busy_timeout=5000`), and yet every captured 500 came back in
    0.4-1.9 ms, nowhere near 5 s. The busy handler was never invoked, which is SQLite's
    documented behaviour when a DEFERRED transaction that has already read tries to PROMOTE its
    lock to a write while another connection holds RESERVED: waiting could only deadlock, so
    SQLITE_BUSY is returned at once and no timeout can absorb it. Pinning the pool to a single
    connection (`VIKUNJA_DATABASE_MAXOPENCONNECTIONS=1`) was measured too: 2 of 10 still failed.
    So the write burst has to be survived by the writer.

    WHY IT DOES NOT BLIND-RETRY. PUT=create here, and a write that APPLIED and then failed on
    the way back would be minted twice — which is exactly what these tests assert against
    (`len(got) == len(made)`, `len(same_title) == 1`). So `landed()` — an INDEPENDENT read, not
    the client under test — is consulted first, and the create is re-issued only when the row is
    genuinely absent. This is deliberately NOT a retry in `api.py`: the client's rule that a PUT
    is never retried on 5xx is correct and stays.

    Re-issue happens IN PLACE, on the spot, rather than in a repair pass after the loop, because
    the comment test asserts ORDER across the page seam — a row rebuilt at the end would land
    newest and break it.

    A non-5xx failure is raised untouched (a 4xx is a real refusal, not contention), and the last
    attempt raises rather than swallowing, so a server that is genuinely broken still fails the
    suite loudly."""
    for attempt in range(attempts):
        try:
            return create()
        except VikunjaError as err:
            if err.status < 500 or attempt == attempts - 1:
                raise
            if landed():
                return None          # the write DID apply — re-issuing it would duplicate it
            time.sleep(backoff * (attempt + 1))
    raise AssertionError("unreachable: the last attempt either returns or raises")


def register_and_login(username: str) -> str:
    _with_retry(lambda: httpx.post(_api("/register"), json={
        "username": username, "email": f"{username}@test.local", "password": PASSWORD,
    }))  # 400 если уже есть — ок
    r = _with_retry(lambda: httpx.post(_api("/login"), json={
        "username": username, "password": PASSWORD,
    }))
    r.raise_for_status()
    return r.json()["token"]


def mint_scoped_token(jwt: str) -> str:
    headers = {"Authorization": f"Bearer {jwt}"}
    routes = _with_retry(lambda: httpx.get(_api("/routes"), headers=headers)).json()
    perms = {
        grp: [op for op in ops if op in routes.get(grp, [])]
        for grp, ops in AGENT_PERMS.items()
        if grp in routes
    }
    r = _with_retry(lambda: httpx.put(_api("/tokens"), headers=headers, json={
        "title": "scoped", "permissions": perms, "expires_at": "2099-01-01T00:00:00Z",
    }))
    r.raise_for_status()
    return r.json()["token"]


@pytest.fixture(scope="session")
def boss_jwt():
    return register_and_login("boss")


@pytest.fixture(scope="session")
def agent_jwts():
    return register_and_login("agent1"), register_and_login("agent2")
