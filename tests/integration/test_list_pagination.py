"""VMCP-108 (577): every flat list read pages, against a REAL Vikunja.

This is where the bug had to be caught and where it was actually found. `GET /projects`,
`/labels` and `/tasks/{id}/comments` are paginated by the server's own `max_items_per_page`,
and the client read exactly one page of each — so past the boundary an EXISTING row read as
ABSENT, and every caller here acts on absence. The unit suite could not see it: a MockTransport
answers whatever the test tells it to, and every one of them modelled a server that returns the
whole list.

These tests are ADAPTIVE rather than pinned to a container configured with a small page size:
they ask `/info` where the boundary is and step over it. So they fire on the ordinary CI
instance (max_items_per_page=50) exactly as they do on the deliberately-shrunk one
(max_items_per_page=5) this was first measured against — no docker/CI change needed. Crossing
the default boundary was measured at ~0.27 s for 53 projects, ~0.07 s for 53 labels and ~0.13 s
for 53 comments.

Each test first ASSERTS THAT IT ACTUALLY CROSSED THE BOUNDARY (the row it is about is absent
from `?page=1`). Without that, a suite run against an instance with a huge page size would pass
these vacuously and go on reporting the bug as fixed.

`project_users()` is the fourth paginating read and is deliberately NOT covered here: crossing
its boundary needs `max_items_per_page + 1` REGISTERED USERS, and register/login share one
10-per-60s anti-bruteforce bucket (see conftest._with_retry), so the test would spend minutes
sleeping. It is pinned in tests/unit/test_api_kanban.py and was measured by hand against 2.3.0
with max_items_per_page=5: page 1 gave 5 shares, `?page=2` gave 3 more, and re-PUTting one of
the hidden three answers 409 "This user already has access to this project".
"""
import uuid

import httpx
import pytest

from tests.integration.conftest import BASE, seed_row
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")

# Above this, stepping over the boundary costs more requests than the pin is worth. Nothing in
# the repo configures it that high; the skip exists so the reason is stated rather than guessed
# at if someone ever does.
_MAX_AFFORDABLE_PAGE_SIZE = 200


def _page_size():
    size = httpx.get(f"{BASE}/api/v1/info").json().get("max_items_per_page")
    if not isinstance(size, int) or size < 1:
        pytest.skip(f"/info reports no usable max_items_per_page ({size!r})")
    if size > _MAX_AFFORDABLE_PAGE_SIZE:
        pytest.skip(f"max_items_per_page={size} — too many rows to cross the boundary")
    return size


def _first_page(jwt, path):
    """Exactly what the buggy client used to see: ONE request, no paging."""
    r = httpx.get(f"{BASE}/api/v1{path}", params={"page": 1},
                  headers={"Authorization": f"Bearer {jwt}"})
    r.raise_for_status()
    return r.json() or []


def _oracle(jwt, path):
    """Every row, read INDEPENDENTLY of the code under test — and deliberately not by trusting
    `x-pagination-total-pages` either, since "a header said so" is not proof (VMCP-103 measured
    that header under-reporting on this same server). Pages until the server stops producing
    rows it has not already sent."""
    rows, seen, page = [], set(), 1
    while page <= 50:
        r = httpx.get(f"{BASE}/api/v1{path}", params={"page": page},
                      headers={"Authorization": f"Bearer {jwt}"})
        r.raise_for_status()
        fresh = [x for x in (r.json() or []) if x["id"] not in seen]
        if not fresh:
            return rows
        seen.update(x["id"] for x in fresh)
        rows += fresh
        page += 1
    raise AssertionError(f"oracle never finished paging {path}")


def test_a_project_past_the_page_boundary_is_not_duplicated_by_setup(boss_jwt):
    """THE CARD'S HARM, end to end. reconcile() finds a project BY TITLE over api.projects(); a
    one-page read hid every project past the boundary, so reconcile took an existing project for
    a missing one and CREATED A SECOND with the same title. Measured on a container with
    max_items_per_page=5: existing id 8 'p6' ignored, new id 18 created. A duplicate project on a
    real tracker is not something an agent can undo — this fails in the worse direction than an
    error would."""
    page_size = _page_size()
    api = VikunjaAPI(BASE, boss_jwt)
    tag = uuid.uuid4().hex[:8]

    made = [f"pg-{tag}-{i:03d}" for i in range(page_size + 3)]
    for title in made:
        seed_row(lambda title=title: api.create_project(title),
                 lambda title=title: title in {p["title"]
                                               for p in _oracle(boss_jwt, "/projects")})

    target = made[-1]        # highest id => last page => the row a single request cannot reach
    assert target not in {p["title"] for p in _first_page(boss_jwt, "/projects")}, (
        "the page boundary was NOT crossed — this test would pass without proving anything"
    )

    seen = {p["title"] for p in api.projects()}
    missing = sorted(set(made) - seen)
    assert not missing, f"api.projects() lost {len(missing)} projects, e.g. {missing[:5]}"

    pid = next(p["id"] for p in api.projects() if p["title"] == target)
    assert reconcile(api, target, shares=[]) == pid      # FOUND, not re-created

    same_title = [p for p in _oracle(boss_jwt, "/projects") if p["title"] == target]
    assert len(same_title) == 1, f"reconcile duplicated the project: {same_title}"


def test_labels_past_the_page_boundary_are_reused_not_minted_again(boss_jwt):
    """get_or_create_label scans api.labels() to REUSE an existing label rather than fork a
    divergent duplicate (its docstring records a real 2026-07-08 incident where a bot did exactly
    that). A one-page read made that scan blind past the boundary — so the duplicate needed no
    typo at all, just enough labels on the account."""
    page_size = _page_size()
    api = VikunjaAPI(BASE, boss_jwt)
    tag = uuid.uuid4().hex[:8]

    made = [f"lb-{tag}-{i:03d}" for i in range(page_size + 3)]
    for title in made:
        seed_row(lambda title=title: api.create_label(title),
                 lambda title=title: title in {x["title"]
                                               for x in _oracle(boss_jwt, "/labels")})

    target = made[-1]
    assert target not in {x["title"] for x in _first_page(boss_jwt, "/labels")}, (
        "the page boundary was NOT crossed — this test would pass without proving anything"
    )

    seen = {x["title"] for x in api.labels()}
    missing = sorted(set(made) - seen)
    assert not missing, f"api.labels() lost {len(missing)} labels, e.g. {missing[:5]}"

    existing_id = next(x["id"] for x in api.labels() if x["title"] == target)
    assert api.get_or_create_label(target)["id"] == existing_id       # reused, not re-minted
    assert len([x for x in _oracle(boss_jwt, "/labels") if x["title"] == target]) == 1


def test_comments_past_the_page_boundary_are_read_and_stay_in_order(boss_jwt):
    """The most dangerous of the four, because page 1 holds the OLDEST comments: a one-page read
    dropped the NEWEST. What lives at the newest end is the whole workflow — the reviewer's
    `[review]` verdict, the `[worklog]` next_task's review offering requires (a card whose
    worklog is invisible is never offered for review AT ALL), and the human's answer to a
    call_human card. Order across the page seam is asserted, not just membership, because those
    callers read this list positionally and by max(created)."""
    page_size = _page_size()
    api = VikunjaAPI(BASE, boss_jwt)
    tag = uuid.uuid4().hex[:8]

    pid = api.create_project(f"cm-{tag}")["id"]
    task = api.create_task(pid, "card with a long history")
    made = [f"c-{tag}-{i:03d}" for i in range(page_size + 3)]
    comments_path = f"/tasks/{task['id']}/comments"
    for text in made:
        seed_row(lambda text=text: api.add_comment(task["id"], text),
                 lambda text=text: any(text in (c.get("comment") or "")
                                       for c in _oracle(boss_jwt, comments_path)))

    newest = made[-1]
    first = _first_page(boss_jwt, f"/tasks/{task['id']}/comments")
    assert not any(newest in (c.get("comment") or "") for c in first), (
        "the page boundary was NOT crossed — this test would pass without proving anything"
    )

    got = [c["comment"] for c in api.comments(task["id"])]
    assert len(got) == len(made), f"read {len(got)} of {len(made)} comments"
    # the ORDER the callers depend on, unbroken across the seam: oldest first, newest last
    assert [text for text in made if any(text in c for c in got)] == made
    assert newest in got[-1]
    assert made[0] in got[0]
