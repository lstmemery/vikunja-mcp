import json
import time

import httpx
import pytest

from vikunja_mcp.api import VikunjaAPI, VikunjaError, canonical_base_url


def make_api(handler):
    transport = httpx.MockTransport(handler)
    client = httpx.Client(
        base_url="https://t.example/api/v1",
        headers={"Authorization": "Bearer tk"},
        transport=transport,
    )
    return VikunjaAPI("https://t.example", "tk", client=client)


def test_url_normalization_appends_api_v1():
    api = VikunjaAPI("https://t.example/", "tk")
    assert str(api._client.base_url).rstrip("/") == "https://t.example/api/v1"
    api2 = VikunjaAPI("https://t.example/api/v1", "tk")
    assert str(api2._client.base_url).rstrip("/") == "https://t.example/api/v1"


def test_error_raises_vikunja_error():
    def handler(request):
        return httpx.Response(403, json={"message": "no access"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.get_task(1)
    assert exc.value.status == 403 and "no access" in str(exc.value)


def test_update_task_is_read_modify_write():
    """POST = полная перезапись: update обязан слать ВСЕ поля задачи, не только изменённые."""
    calls = []

    def handler(request):
        calls.append(request)
        if request.method == "GET":
            return httpx.Response(200, json={
                "id": 5, "title": "t", "description": "keep me", "priority": 3, "done": False,
            })
        return httpx.Response(200, json=json.loads(request.content))

    api = make_api(handler)
    result = api.update_task(5, priority=5)
    sent = json.loads(calls[1].content)
    assert calls[1].method == "POST" and calls[1].url.path.endswith("/tasks/5")
    assert sent["description"] == "keep me"      # старое поле не потеряно
    assert sent["priority"] == 5
    assert result["priority"] == 5


def test_create_task_uses_put():
    def handler(request):
        assert request.method == "PUT" and request.url.path.endswith("/projects/3/tasks")
        body = json.loads(request.content)
        return httpx.Response(201, json={"id": 9, **body})

    api = make_api(handler)
    t = api.create_task(3, "new task", description="d", priority=2)
    assert t["id"] == 9 and t["title"] == "new task"


def test_comments_and_assignees_endpoints():
    seen = []

    def handler(request):
        seen.append((request.method, request.url.path))
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.comments(7)
    api.add_comment(7, "note")
    api.add_assignee(7, 2)
    api.remove_assignee(7, 2)
    api.add_relation(7, 1, "parenttask")
    assert seen == [
        # VMCP-108: comments() is PAGED now, and the first paged read of a client resolves
        # max_items_per_page once. This handler answers every GET with `{}`, i.e. not a list,
        # so the page loop takes it as an empty page and stops after one request.
        ("GET", "/api/v1/info"),
        ("GET", "/api/v1/tasks/7/comments"),
        ("PUT", "/api/v1/tasks/7/comments"),
        ("PUT", "/api/v1/tasks/7/assignees"),
        ("DELETE", "/api/v1/tasks/7/assignees/2"),
        ("PUT", "/api/v1/tasks/7/relations"),
    ]


def test_download_attachment_returns_raw_bytes_not_json():
    """#139: the download endpoint streams the file itself, not JSON — download_attachment
    returns the bytes verbatim (r.json() would blow up on a binary body). `attachment_id`
    is the attachment's own id, so the URL is /tasks/{id}/attachments/{attachment_id}."""
    def handler(request):
        assert request.method == "GET"
        assert request.url.path.endswith("/tasks/5/attachments/7")
        return httpx.Response(200, content=b"\x89PNG\r\n\x1a\nrawbytes")

    api = make_api(handler)
    data = api.download_attachment(5, 7)
    assert data == b"\x89PNG\r\n\x1a\nrawbytes"
    assert isinstance(data, bytes)


def test_download_attachment_404_raises_vikunja_error():
    def handler(request):
        return httpx.Response(
            404, json={"code": 4011, "message": "This task attachment does not exist."}
        )

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.download_attachment(5, 999)
    assert exc.value.status == 404


def test_add_comment_sends_html_not_raw_plain_text():
    # #85: the comment field is HTML — add_comment must convert agent plain text to
    # structure-preserving, escaped HTML on the wire, not ship raw newlines/'<'.
    sent = {}

    def handler(request):
        sent["body"] = json.loads(request.content)
        return httpx.Response(200, json={})

    api = make_api(handler)
    api.add_comment(7, "[worklog]\nfixed a < b bug\n\nEvidence: abc")
    comment = sent["body"]["comment"]
    assert comment.startswith("<p>[worklog]")   # marker intact at the front
    assert "<br>" in comment                     # single newline -> line break
    assert comment.count("<p>") == 2             # blank line -> new paragraph
    assert "&lt; b" in comment                   # literal '<' escaped, markup safe
    assert "\n" not in comment                   # no raw newline leaked into the field


# --- транзиентные ретраи (#86) ---------------------------------------------------------


@pytest.fixture
def no_sleep(monkeypatch):
    # backoff -> no real waiting, tests stay instant (api.py imports the module `time`)
    monkeypatch.setattr(time, "sleep", lambda _s: None)


def test_transient_5xx_on_get_is_retried_then_succeeds(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, json={"id": 1, "title": "ok"})

    api = make_api(handler)
    assert api.get_task(1)["title"] == "ok"
    assert calls["n"] == 3   # 2 transient failures retried, 3rd succeeded


def test_transient_retries_are_bounded_then_raise(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(503, json={"message": "down"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.get_task(1)
    assert exc.value.status == 503
    assert calls["n"] == VikunjaAPI._MAX_RETRIES + 1   # bounded, then the last error surfaces


def test_permanent_4xx_is_not_retried(no_sleep):
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(404, json={"message": "nope"})

    api = make_api(handler)
    with pytest.raises(VikunjaError):
        api.get_task(1)
    assert calls["n"] == 1   # a permanent error surfaces immediately, no retry


def test_put_create_is_not_retried_on_5xx(no_sleep):
    # PUT = create: a 5xx may have applied server-side; retrying would duplicate -> no retry.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.create_task(3, "t")
    assert exc.value.status == 502
    assert calls["n"] == 1   # non-idempotent create not retried on an ambiguous 5xx


def test_429_is_retried_even_for_put_create(no_sleep):
    # 429 = rejected before applying -> safe to retry even a create; it lands exactly once.
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(429, json={"message": "slow down"})
        return httpx.Response(201, json={"id": 9, "title": "t"})

    api = make_api(handler)
    assert api.create_task(3, "t")["id"] == 9
    assert calls["n"] == 2   # 429 retried once, then created exactly once


def test_raw_download_inherits_get_retry_on_transient_5xx(no_sleep):
    """#139: the raw download goes through _req(raw=True), so it inherits the #86 GET
    retry/backoff — a transient 5xx is retried, then the bytes come back."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 2:
            return httpx.Response(503, json={"message": "unavailable"})
        return httpx.Response(200, content=b"filebytes")

    api = make_api(handler)
    assert api.download_attachment(1, 1) == b"filebytes"
    assert calls["n"] == 2   # one transient failure retried, then success


def test_upload_attachment_sends_multipart_put_not_json():
    """#137: an upload goes out as multipart/form-data (field `files`) via PUT — api.py's JSON
    body helper doesn't fit, so _req(files=...) is used. Verified on real 2.3.0: PUT (POST->405),
    response {"errors":..., "success":[...]}; the filename and raw bytes ride in the multipart."""
    seen = {}

    def handler(request):
        seen["method"] = request.method
        seen["path"] = request.url.path
        seen["ctype"] = request.headers.get("content-type", "")
        seen["body"] = request.content
        return httpx.Response(
            200, json={"errors": None, "success": [{"id": 3, "file": {"name": "shot.png"}}]}
        )

    api = make_api(handler)
    resp = api.upload_attachment(9, "shot.png", b"\x89PNGdata", mime="image/png")
    assert seen["method"] == "PUT"
    assert seen["path"].endswith("/tasks/9/attachments")
    assert seen["ctype"].startswith("multipart/form-data")   # not application/json
    assert b"shot.png" in seen["body"] and b"\x89PNGdata" in seen["body"]
    assert resp["success"][0]["id"] == 3


def test_upload_attachment_put_is_not_retried_on_5xx(no_sleep):
    """PUT=create: an ambiguous 5xx may have stored the file server-side, so retrying would
    duplicate the attachment -> not retried (same rule as create_task)."""
    calls = {"n": 0}

    def handler(request):
        calls["n"] += 1
        return httpx.Response(502, json={"message": "bad gateway"})

    api = make_api(handler)
    with pytest.raises(VikunjaError) as exc:
        api.upload_attachment(1, "a.png", b"x", mime="image/png")
    assert exc.value.status == 502
    assert calls["n"] == 1   # non-idempotent upload not retried on an ambiguous 5xx


def test_upload_attachment_429_retried_with_body_reencoded(no_sleep):
    """429 = rejected before applying -> safe to retry even a create; because the body is passed as
    BYTES (not a consumed stream), the retry re-encodes the SAME multipart, not an empty one."""
    calls = {"n": 0}
    bodies = []

    def handler(request):
        calls["n"] += 1
        bodies.append(request.content)
        if calls["n"] < 2:
            return httpx.Response(429, json={"message": "slow down"})
        return httpx.Response(200, json={"errors": None, "success": [{"id": 7}]})

    api = make_api(handler)
    out = api.upload_attachment(1, "a.png", b"payload", mime="image/png")
    assert out["success"][0]["id"] == 7
    assert calls["n"] == 2                                    # retried exactly once
    assert b"payload" in bodies[0] and b"payload" in bodies[1]   # same body re-sent, never empty


def test_connection_drop_retried_for_get_not_for_put(no_sleep):
    # "Connection closed mid-response": retry the idempotent GET, never the PUT create.
    calls = {"GET": 0, "PUT": 0}

    def handler(request):
        calls[request.method] += 1
        raise httpx.ReadError("Connection closed mid-response")

    api = make_api(handler)
    with pytest.raises(httpx.TransportError):
        api.get_task(1)
    with pytest.raises(httpx.TransportError):
        api.create_task(3, "t")
    assert calls["GET"] == VikunjaAPI._MAX_RETRIES + 1   # idempotent -> retried to exhaustion
    assert calls["PUT"] == 1                             # create -> raised immediately


# --- tracker #164: what canonical_base_url must NOT fold ----------------------------------------
# The canonicalizer's dangerous direction is PERMISSIVE. Every pair of urls it folds onto one
# string is a pair the #148 repoint guard reads as the SAME endpoint, and #148 exists precisely to
# stop a token rotation from silently repointing an agent at another project's queue. #154 built
# the function and pinned the three things it SHOULD fold (trailing slash, scheme case, host case
# — tests/unit/test_server.py) and, in the same file, that a genuinely different scheme VALUE, host,
# port or path must still refuse. What nothing pinned is CASE where case is meaningful: #154's own
# reviewer ran the mutation "let it lowercase the path too" and no test went red. Re-measured on
# the PRE-card tree on 2026-08-02 with `__pycache__` cleared: control 0 failed; `{path}` ->
# `{path.lower()}` in canonical_base_url -> 0 failed. (On THIS tree that same mutation is 4 failed
# — see the MUTATION-CHECKED records below.) Correct behaviour resting on nobody having touched it.
#
# The second row of the same finding was a real defect rather than a gap: the authority was folded
# WHOLE, so userinfo — case-sensitive per RFC 3986 6.2.2.1, and a credential — collapsed too
# ('https://u:PassWord@h' -> 'https://u:password@h'), which is this function disagreeing with the
# very httpx behaviour its own docstring appeals to. Far from the only such disagreement, as writing
# the second test below turned up: an uppercase scheme with a default port, an IPv6 literal with
# uppercase hex, and a query or fragment before the first `/` all diverge too — all three from
# #154's scheme/host folding rather than from this row, and all three now asserted there rather
# than described. The third of them is a live permissive fold and is filed as its own card.


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("https://t.example/", "https://t.example/api/v1"),
        ("HTTPS://t.example", "https://t.example/api/v1"),
        ("https://T.EXAMPLE", "https://t.example/api/v1"),
        ("https://t.example/Vikunja", "https://t.example/Vikunja/api/v1"),
        ("https://t.example/VIKUNJA/Sub", "https://t.example/VIKUNJA/Sub/api/v1"),
        ("https://User:PassWord@t.example", "https://User:PassWord@t.example/api/v1"),
        ("https://User:PassWord@T.EXAMPLE:8443", "https://User:PassWord@t.example:8443/api/v1"),
        ("https://a@b:PW@T.EXAMPLE", "https://a@b:PW@t.example/api/v1"),
        ("https://[::1]:3456", "https://[::1]:3456/api/v1"),
        ("https://t.example:443", "https://t.example:443/api/v1"),
        # tracker #707 — the zone id is an OS interface name, so its case is MEANINGFUL.
        ("https://[fe80::1%25ETH0]", "https://[fe80::1%25ETH0]/api/v1"),
        ("https://[FE80::1%25ETH0]:3456", "https://[fe80::1%25ETH0]:3456/api/v1"),
        ("https://[fe80::1%ETH0]", "https://[fe80::1%ETH0]/api/v1"),
        ("https://[FE80::1]", "https://[fe80::1]/api/v1"),
        ("https://[fe80::1%25]", "https://[fe80::1%25]/api/v1"),
        ("https://User:PassWord@[FE80::1%25ETH0]", "https://User:PassWord@[fe80::1%25ETH0]/api/v1"),
        ("https://h%25ST.example", "https://h%25st.example/api/v1"),
        ("https://[fe80::1%25ETH%2D0]", "https://[fe80::1%25ETH%2D0]/api/v1"),
    ],
    ids=[
        "trailing-slash-stripped", "scheme-case-folded", "host-case-folded",
        "path-case-KEPT", "path-case-KEPT-multi-segment",
        "userinfo-case-KEPT", "userinfo-KEPT-while-host-folds", "split-on-the-LAST-at",
        "ipv6-literal", "default-port-KEPT",
        "ipv6-zone-id-case-KEPT", "ipv6-hex-folds-WHILE-zone-KEPT", "ipv6-bare-percent-zone-KEPT",
        "ipv6-hex-still-folds-without-a-zone", "ipv6-empty-zone-unchanged",
        "userinfo-AND-zone-both-KEPT-while-hex-folds", "reg-name-with-pct-encoding-still-folds",
        "zone-id-with-its-own-pct-encoding-KEPT",
    ],
)
def test_canonical_base_url_folds_the_case_insensitive_parts_and_nothing_else(raw, expected):
    """Exact output, row by row — the folding half and the KEEPING half in one table.

    The keeping rows carry this card. `https://h/vikunja` and `https://h/Vikunja` are different
    endpoints on a case-sensitive server, and `User:PassWord` vs `user:password` is a different
    CREDENTIAL: fold either and the guard stops being able to tell a genuine repoint from a
    cosmetic edit, in the permissive direction, which is #148's hole re-opened. `:443` is kept for
    the same reason from the strict side — folding it would be safe against httpx but is not what
    this function promises, and a loud refusal beats a quiet repoint (#164 point 3, deliberately
    unchanged). `split-on-the-LAST-at` follows httpx, measured: `httpx.URL('https://a@b:PW@HOST')`
    reads host `host` and userinfo `a%40b:PW`, so an illegal un-encoded `@` in the userinfo makes
    this function fold LESS, never more. `ipv6-literal` is deliberately case-BLIND — `[::1]:3456`
    holds no alphabetic character, so it pins bracket/port handling and cannot detect any folding
    mutation at all; the case question for IPv6 is answered by the uppercase-hex assert in the test
    below, which is where it diverges from httpx.

    The `ipv6-*` rows are #707's, and they exist because "IPv6 literal" named a thing that is not
    homogeneous. The ADDRESS folds (`ipv6-hex-still-folds-without-a-zone`) and the ZONE ID does not
    (`ipv6-zone-id-case-KEPT`), because a zone id is an OS interface name — measured, not read off
    an RFC: `socket.if_nametoindex('eth0')` -> 426 while `'ETH0'` raises OSError on Linux, and
    `IPv6Address('fe80::1%ETH0') != IPv6Address('fe80::1%eth0')` while `FE80::1 == fe80::1`.
    `ipv6-hex-folds-WHILE-zone-KEPT` is the row that pins both halves of one literal going separate
    ways, and it is the only row R1 and R2 below BOTH kill. `ipv6-empty-zone-unchanged` and
    `reg-name-with-pct-encoding-still-folds` pin what #707 must NOT have changed: a `%25` outside
    brackets is an ordinary reg-name octet and stays case-insensitive.

    MUTATION-CHECKED over the whole `tests/unit` selection, `__pycache__` DELETED (not just
    `PYTHONDONTWRITEBYTECODE=1` — that stops writing, not reading), every round restored with
    `git checkout --` and the restore verified by sha256 against the pristine file. Two sweeps run
    on 2026-08-03, each opening with an UNMUTATED CONTROL round on the same selection; all rounds
    collected 921 items. Control round: 0 failed.
      * `{path}` -> `{path.lower()}` in canonical_base_url -> 4 failed, two of them this table's
        `path-case-KEPT` rows (the others are the httpx test below and the guard row in
        test_server.py). On the PRE-#164 tree, control 0 failed, that same mutation was 0 failed —
        which is the gap #164 was filed for
      * fold the authority WHOLE again (`authority.lower()`, the pre-#164 body) -> 13 failed, eight
        of them rows here: `userinfo-case-KEPT`, `userinfo-KEPT-while-host-folds`,
        `split-on-the-LAST-at` and all five zone rows
      * `rpartition("@")` -> `partition("@")` -> 10 failed: `split-on-the-LAST-at` as intended, plus
        `host-case-folded` here, the test below, and `host-case` in test_server.py — because with no
        `@` present `partition` puts the WHOLE authority in the userinfo half and then folds nothing
        at all, so it breaks far more than the `@` split it was aimed at
      * identity canonicalizer (suffix only, fold nothing) -> 14 failed, eight of them rows here, so
        this table pins the FOLDING side. The test below is among the rest, but only via its
        divergence asserts; its equal set alone would not notice
      * (#707) `_fold_host` -> `host.lower()`, the pre-#707 body -> 8 failed, FIVE of them rows here
        (`ipv6-zone-id-case-KEPT`, `ipv6-hex-folds-WHILE-zone-KEPT`, `ipv6-bare-percent-zone-KEPT`,
        `userinfo-AND-zone-both-KEPT-while-hex-folds`, `zone-id-with-its-own-pct-encoding-KEPT`)
        and two the guard rows in test_server.py. Re-run as a PAIRED mutation — helper left in
        place, `zone.lower()` added back instead — the failure set is IDENTICAL, so these rows pin
        the behaviour rather than one spelling of the edit
      * (#707) fold NOTHING inside brackets, and separately `address.lower()` -> `address` -> 7
        failed EACH, with the SAME set both times: the two hex rows, `userinfo-AND-zone-...`, the
        test below, and all three rows of the paired guard test. Honest limit — these tests do not
        DISTINGUISH those two mutations
      * (#707) `literal.partition("%")` -> `rpartition("%")` -> 5 failed, including
        `zone-id-with-its-own-pct-encoding-KEPT` as aimed AND `ipv6-hex-still-folds-without-a-zone`,
        which was not: on a literal with no `%` at all, `rpartition` returns `("", "", literal)`, so
        the address lands in the zone half and stops folding entirely
    The three legacy counts above were RE-MEASURED for #707 rather than copied: adding these rows
    moved two of them (6 -> 13, 4 -> 10) and the identity round (7 -> 14). Only the `path.lower()`
    round was unchanged at 4. A count recorded next to a growing table is stale by construction.
    """
    assert canonical_base_url(raw) == expected


def test_the_canonicalizer_changes_the_client_url_only_in_these_measured_classes():
    """The behavioural half: routing the client through canonical_base_url leaves the request it
    builds byte-identical to the pre-#154 raw `rstrip('/') + /api/v1` path for the urls this
    project's config actually holds — and diverges in three measured classes, each asserted here
    rather than hedged around.

    This is the function's own docstring claim, made checkable instead of asserted, and checking it
    is what found the claim overstated. #154 wrote "httpx folds these the same way ... leaves its
    observable behaviour identical". Measured divergences, httpx 0.28.1:
      1. UPPERCASE SCHEME + EXPLICIT DEFAULT PORT — the default-port drop is case-SENSITIVE about
         the scheme, so `HTTPS://h:443` keeps `:443` on the wire while canonicalized `https://h:443`
         loses it. Same endpoint, so nothing broke.
      2. IPv6 LITERAL WITH UPPERCASE HEX — `[::FFFF:1]` is folded to `[::ffff:1]`, changing the Host
         header. Same address (hex digits are case-insensitive; RFC 5952 prefers lowercase anyway).
      3. QUERY OR FRAGMENT BEFORE THE FIRST `/` — `?Q=A` / `#Frag` land in the authority slice and
         get lowercased. NOT harmless in principle and NOT endorsed: this is the same permissive
         fold #164 removed from userinfo, still open for this shape, filed as its own card. The
         assert below CHARACTERIZES today's behaviour so a fix has to come past it deliberately.
    All three are PRE-EXISTING — they come from #154's scheme/host folding, not from this card,
    which only stopped userinfo being a fourth. None is reachable from a Vikunja base url. They are
    asserted because a sentence wider than its measurement is the defect class this card exists for,
    and because each must neither spread nor quietly vanish under a future edit or an httpx bump.

    Userinfo is the row that was a real defect rather than an overstatement: the pre-#164 body
    folded the case of a CREDENTIAL, so `https://User:PassWord@h` went out as
    `https://user:password@h`. That row is red-first evidence.

    NOT a proof for all urls. The equal set says these ten inputs are unchanged and nothing about an
    eleventh; the 540-url sweep recorded in canonical_base_url's docstring is a grid, and classes 2
    and 3 are precisely shapes that grid could not contain — which is how they were found, and why
    "these measured classes" in the name is not "all classes".

    #707 SHRANK class 2 rather than adding a fourth. The zone id used to ride down with the hex,
    so `https://[fe80::1%25ETH0]` belonged in the divergence list; it now sits in the EQUAL set
    above, because httpx passes a zone id through verbatim (measured, 0.28.1: `httpx.URL` returns
    `[fe80::1%25ETH0]` unchanged and reads `.host` as `fe80::1%25ETH0`). Class 2 is now precisely
    the ADDRESS's hex, and the assert that the two halves of one literal go separate ways is what
    holds that line. Class 3 is untouched and still open — see the SEAM assert at the end, a url
    carrying both cards at once.

    MUTATION-CHECKED, same selection and hygiene as the table above, two sweeps on 2026-08-03, each
    opening with an unmutated control on the same selection. Control round: 0 failed.
      * `{path}` -> `{path.lower()}` -> 4 failed, this test among them
      * fold the authority WHOLE again (the pre-#164 body) -> 13 failed, this test among them, on
        the `https://User:PassWord@t.example` row — which is what made #164's fix a fix and not a
        preference
      * identity canonicalizer (fold nothing) -> 14 failed, this test among them. It is caught by
        the divergence asserts, NOT by the equal set — an identity body satisfies every row of the
        equal set, since httpx folds scheme and host by itself. That is measured per-assert, and it
        corrects this record's first version, which said 6 failed and "this test is NOT among them":
        both were true before the divergence asserts were added and went stale inside #164
      * (#707) `_fold_host` -> `host.lower()` (pre-#707 body) -> 8 failed, this test among them —
        red-first for the two zone urls now in the equal set
      * (#707) `tail.lower()` -> `tail` -> 1 failed, and it is THIS test, via the SEAM assert
        alone. Worth saying plainly rather than rounding up: the whole boundary with #706 rests on
        that single assert. It is a real pin (the mutation reddens it), but there is no redundancy
        behind it
      * of the divergence asserts, only the CANONICAL side of each pair can fail from a change to
        this function; the `pre_154` sides never call it and are httpx-bump tripwires only
    The 6 and 7 in the first version of this record were re-measured for #707 and became 13 and 14;
    they moved because #707 added rows, not because anything regressed.
    """
    def pre_154(url):
        stripped = url.rstrip("/")
        return stripped if stripped.endswith("/api/v1") else stripped + "/api/v1"

    def wire(url):
        return str(httpx.URL(url))

    for raw in [
        "https://t.example", "https://t.example/", "HTTPS://t.example", "https://T.EXAMPLE",
        "https://t.example/Vikunja", "https://t.example/vikunja",
        "https://User:PassWord@t.example", "https://user:password@t.example",
        "https://[::1]:3456", "https://t.example/api/v1",
        # tracker #707 — RED before that card: this used to fold to `[fe80::1%25eth0]` and so
        # belonged to the divergence list below. Now the client builds the same request either
        # way, because httpx passes a zone id through verbatim (measured, 0.28.1).
        "https://[fe80::1%25ETH0]", "https://[fe80::1%25ETH0]:3456",
    ]:
        assert wire(canonical_base_url(raw)) == wire(pre_154(raw)), \
            f"canonicalizing {raw!r} changed the url the client builds"

    # Class 1 — uppercase scheme + explicit DEFAULT port.
    assert wire(pre_154("HTTPS://t.example:443")) == "https://t.example:443/api/v1"
    assert wire(canonical_base_url("HTTPS://t.example:443")) == "https://t.example/api/v1"
    assert wire(canonical_base_url("HTTPS://t.example:8443")) == \
        wire(pre_154("HTTPS://t.example:8443")), \
        "the exception is the DEFAULT port specifically; a non-default port must be unaffected"

    # Class 2 — IPv6 literal written with uppercase hex. The ADDRESS still folds; what #707 took
    # out of this class is the zone id, which used to ride along with it.
    assert wire(pre_154("https://[::FFFF:1]:3456")) == "https://[::FFFF:1]:3456/api/v1"
    assert wire(canonical_base_url("https://[::FFFF:1]:3456")) == "https://[::ffff:1]:3456/api/v1"
    # ... and the two halves of one literal go SEPARATE ways: hex down, zone untouched.
    assert wire(canonical_base_url("https://[FE80::1%25ETH0]")) == "https://[fe80::1%25ETH0]/api/v1"

    # Class 3 — query/fragment BEFORE the first `/`, so it lands in the authority slice and folds.
    # Characterization of a KNOWN-OPEN permissive fold, not an endorsement (see the docstring).
    assert canonical_base_url("https://t.example?Q=A") == "https://t.example?q=a/api/v1"
    assert canonical_base_url("https://t.example#Frag") == "https://t.example#frag/api/v1"
    # ... and the same query AFTER a `/` is in the path slice, so it is correctly left alone.
    assert canonical_base_url("https://t.example/x?Q=A") == "https://t.example/x?Q=A/api/v1"
    # The SEAM between #707 (fixed) and #706 (open), in one url that carries both. #707 cuts the
    # zone at the literal's closing `]`, so everything after it — a port, and until #706 a query or
    # fragment — keeps folding exactly as before. Characterization again, not endorsement: when #706
    # lands, THIS assert is the one it has to come past, and the zone half must survive it intact.
    assert canonical_base_url("https://[fe80::1%25ETH0]?Q=A") == "https://[fe80::1%25ETH0]?q=a/api/v1"
