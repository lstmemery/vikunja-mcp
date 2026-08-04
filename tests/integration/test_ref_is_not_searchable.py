"""#757: the identifier half of a `ref` is NOT a search key — pinned against a real 2.3.0.

WHY THIS IS AN INTEGRATION TEST AND NOT A UNIT ONE. Until #757 the whole justification for
`ref` — in `Workflow._ref`, and copied by #735 into `server.file_task`, `Workflow.file_task`'s
cross-project note and SKILL.md — was "a human searches the tracker by the identifier; the bare
global id is not searchable". Nobody had ever asked a server. Measured, it is false: the
identifier finds NOTHING, in the REST API and in the web UI's quick-action search alike (the UI
is a thin client over this same `?s=` endpoint, so the two agree by construction). The prose
that replaced it asserts that as a property OF THE SERVER, so it is pinned where a real server
can contradict it. A FakeAPI cannot: it would only re-state today's belief.

WHAT WOULD MAKE THIS GO RED, and that is the point rather than a caveat: a Vikunja that starts
resolving identifiers in `s=` (or grows a working `filter=identifier`). That is a WELCOME red —
it means the prose in the four surfaces above has to be re-measured and rewritten, not that this
file needs relaxing. The control assertion is what keeps the red honest: without it, a search
endpoint broken in some unrelated way would satisfy "the identifier finds nothing" and the pin
would pass while measuring nothing at all.
"""
import uuid

import httpx
import pytest

from tests.integration.conftest import BASE, _api

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def seeded(boss_jwt):
    """A project WITH an identifier prefix (the default a reconcile makes has none, and a
    prefix-less project renders its ref as '#<index>' — which would not exercise the claim
    at all), holding one task whose title carries a token unique to this run.

    The PREFIX is unique per run too, and that is not tidiness: measured, a second project
    asking for an identifier this user already owns is rejected `400 Bad Request` at create,
    so a fixed prefix passes on a fresh container and fails on the second run against the
    same one — exactly the shape that reads as "the pin broke" rather than "the pin is
    unrepeatable". Uppercase, since Vikunja upper-cases the identifier it stores."""
    h = {"Authorization": f"Bearer {boss_jwt}"}
    token = uuid.uuid4().hex[:10]
    prefix = f"P{token[:5].upper()}"
    project = httpx.put(
        _api("/projects"), headers=h,
        json={"title": f"ref-search-{token}", "identifier": prefix},
    )
    project.raise_for_status()
    pid = project.json()["id"]
    task = httpx.put(
        _api(f"/projects/{pid}/tasks"), headers=h, json={"title": f"needle {token} haystack"},
    )
    task.raise_for_status()
    body = task.json()
    # The server assigns the identifier; do not construct it here — that is the very habit
    # SKILL.md forbids, and it would make this pin agree with itself rather than with Vikunja.
    assert body["identifier"], "2.3.0 returns `identifier` on task create; the pin needs it"
    return h, token, body


def _search(headers, query):
    r = httpx.get(_api("/tasks"), headers=headers, params={"s": query})
    r.raise_for_status()
    return r.json() or []


def test_search_finds_a_task_by_a_word_from_its_title(seeded):
    """CONTROL, and it runs first for a reason: every other assertion in this file is a
    NEGATIVE one, and a negative is worthless without evidence the endpoint answers at all."""
    headers, token, task = seeded
    hits = _search(headers, token)
    assert [t["id"] for t in hits] == [task["id"]], f"expected exactly the seeded task, got {hits}"


def test_the_identifier_is_not_a_search_key(seeded):
    """The measured claim: neither the full identifier nor its prefix finds the card."""
    headers, _token, task = seeded
    ident = task["identifier"]                      # e.g. 'PIN-1'
    prefix = ident.split("-")[0]
    for query in (ident, ident.lower(), prefix):
        assert _search(headers, query) == [], f"s={query!r} found something; re-measure #757"


def test_filtering_by_identifier_is_rejected_outright(seeded):
    """`filter=identifier` is not merely empty, it is a 400 — the field does not exist for
    filtering. Asserted on the STATUS, not on the message, which is Vikunja's to reword."""
    headers, _token, task = seeded
    r = httpx.get(
        _api("/tasks"), headers=headers,
        params={"filter": f"identifier = '{task['identifier']}'"},
    )
    assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:200]}"
