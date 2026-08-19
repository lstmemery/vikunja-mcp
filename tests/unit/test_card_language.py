"""THE `language` KEY MOVES PROSE AND NOTHING ELSE (#1165).

The key has two halves and only one of them can be tested here. The half this file measures is
the prose the PRODUCT authors — six of `workflow.py`'s twelve `add_comment` sites, each of them
now a marker literal wrapped around a `card_text(self.language, ...)` body. The OTHER half — the
spec, the worklog and the review report, which are the bulk of a card's text — is written by the
AGENT, so all this tool can do is carry the value in `next_task`'s payload and state the rule in
SKILL.md. Only the carrying is pinnable, and it is pinned below; nothing in a unit test can check
that an agent obeyed the rule.

TWO THINGS MUST NOT MOVE, and they fail in opposite directions.

* THE MARKER. `workflow.py` reads comment text in exactly two places — `startswith("[review]")`
  and `startswith("[worklog]")`, both in the review-offering branch — so a per-language spelling
  on EITHER OF THOSE TWO drops every card written under the other setting out of the offering,
  silently. The remaining eight markers are not parsed by anything, and the sweep below records
  that directly: localising `[claim]` fires the flip pin and the no-bracket pin while leaving the
  classification pin GREEN. They are frozen anyway, because the vocabulary is read by eye and by
  grep, and a half-translated vocabulary is worse than either half. This is
  measured TWICE and deliberately so:
  `test_flipping_the_language_moves_the_body_and_never_the_marker` BUILDS both card streams and
  compares them byte-for-byte, and
  `test_a_card_written_in_one_language_is_still_classified_after_the_flip` drives the actual
  consequence through `next_task`'s real offering branch. The first would still pass if the
  markers were identical and the offering logic had been rewritten to key off something else;
  the second is the regression that matters.
* THE VERDICT TOKENS. `[review] APPROVE` / `[review] NEEDS WORK` are not in the table at all,
  and `test_the_verdict_tokens_do_not_translate` says so with a card built under `ru`. SKILL.md
  quotes both spellings to the reviewer, so localising them would make the rulebook false in
  one of the two languages.

WHY THE COMPARISON IS BUILT RATHER THAN READ. The acceptance criterion for this card was
explicit that the invariance be measured and not asserted by reading the diff, and the reason is
visible in the mechanism: a marker can acquire a Cyrillic look-alike (`а`, U+0430) that no diff
reader distinguishes, and the two languages live in one table where a body and its bracket would
sit on the same line if the bracket were ever moved in. So the runs are driven end to end
against `FakeAPI` and the comment streams are compared as bytes.

MUTATION SWEEP. Selection is this file alone (`tests/unit/test_card_language.py`) so no
collateral test can stand in for a pin. Run in a CLONE of the worktree — never in the tree being
edited — with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` before every round, and
`vikunja_mcp.__file__` printed each round, resolving inside the clone every time. Rounds are read
by COUNTING lines beginning `FAILED `, with `ERROR ` counted separately, and `-q` is dropped so
the `collected` line is there to cross-check. Every patcher asserts it replaced exactly ONE
occurrence, so a round that failed to mutate fails loudly instead of coming back green. Each
round is stated beside its own control:

* opening control 0 failed, 0 errors, 12 collected.

* `[claim]` moved INTO the table and localised there (`"[claim] …"` / `"[клейм] …"`), the marker
  drift the whole design is arranged to prevent: control 0 failed, 12 collected; mutation 2
  failed — the flip pin and `test_no_card_text_entry_contains_a_marker_bracket`. NOT the
  classification pin, and that is the useful half of this round: the offering branch parses
  `[worklog]` and `[review]` and nothing else, so a `[claim]` drift is invisible to it. Two
  separate pins are needed precisely because one marker's drift is caught structurally and
  another's behaviourally.

* `Workflow.claim` reverted to a hard-coded English body, i.e. one call site silently ignoring
  `language`: control 0 failed, 12 collected; mutation 1 failed, the flip pin alone — showing
  its body half is live independently of its marker half.

* `card_text` made to ignore its `language` argument and always return the `en` row: control 0
  failed, 12 collected; mutation 2 failed (the flip pin and the `[attach]` units pin). The
  marker and classification pins stay GREEN, correctly — an all-English board classifies fine,
  which is exactly why "cards actually flip language" needs an assertion of its own.

* `config.load_config` made to fall back to `en` on an unknown value instead of raising: control
  0 failed, 12 collected; mutation 1 failed, `test_an_unknown_language_is_refused_by_name`.

* the `result["language"] = self.language` line deleted from `with_wip`: control 0 failed, 12
  collected; mutation 2 failed — the payload pin, and the classification pin THROUGH ITS PAYLOAD
  ASSERT rather than its classification assert. Recorded that way because the distinction is the
  point: the classification itself is unaffected, and this is the half the tool cannot check any
  further, since with the key gone from the payload the agent is simply never told.

* `[worklog]` given a per-language spelling in `advance` — the one marker the offering branch
  parses on the write side: control 0 failed, 12 collected; mutation 2 failed, the flip pin and
  the classification pin. THIS is the round that proves the classification pin measures what its
  name says, and the one the `[claim]` round above could not provide.

* `[review] APPROVE` given a per-language spelling — the other half of the timestamp comparison,
  and simultaneously a verdict token: control 0 failed, 12 collected; mutation 3 failed, adding
  `test_the_verdict_tokens_do_not_translate` to the previous pair.

* closing control 0 failed, 0 errors, 12 collected, every mutated source byte-identical to the
  baseline afterwards (compared as bytes, per round-trip).
"""
import pytest

from tests.unit.fakes import FakeAPI
# The marker vocabulary, imported from the pin that OWNS it rather than restated here. The
# coupling is the feature: add a marker there and this file's completeness check fails until the
# driver below reaches it, which is what keeps "every marker survives the flip" from quietly
# meaning "every marker this driver happened to write".
from tests.unit.test_card_text_is_ascii import _MARKERS
from vikunja_mcp import cardtext
from vikunja_mcp.config import DEFAULT_LANGUAGE, LANGUAGES, ConfigError, load_config
from vikunja_mcp.workflow import STAGES, Workflow


def _wf(language):
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, language=language)


def _drive_every_comment_site(api, wf, tmp_path):
    """Every path in `workflow.py` that writes a comment, run once, on one board.

    Returns the comment stream of the whole board — GROUPED BY TASK ID, in creation order within
    each task, which is not the same as board-wide creation order (the epic parent has a lower id
    than its child, so its `[epic-ready]` line precedes a `[worklog]` written before it). Any
    stable order does for the pins, which compare two runs of this same function; the ordering is
    named because a later reader would otherwise assume the wrong one. The neighbouring project a
    cross-project `file_task` writes to is included. It is one function rather than a test per site
    because the pins below compare two RUNS of it: what matters is that the two streams line up
    position for position, which only holds if both runs took the same path. Completeness is
    asserted rather than trusted — `test_the_driver_reaches_every_marker` walks the result against
    `_MARKERS`, which `test_card_text_is_ascii.py` in turn holds equal to the set actually written
    in `workflow.py`, so the chain reaches the source without this file re-deriving it.
    """
    tmp_path.mkdir(parents=True, exist_ok=True)
    queued = api.add_task("queued", "Queue")
    wf.claim(queued["id"])                                        # [claim]
    wf.advance(queued["id"], to="build", spec="the approach")     # [spec]
    wf.advance(                                                   # [worklog]
        queued["id"], to="review", worklog="what was done",
        evidence="deadbeef", root_cause="why it happened",
    )
    wf.review_task(queued["id"], verdict="needs_work", report="not yet")   # [review] NEEDS WORK
    # needs_work sends the card back to Build, so the rework has to be re-submitted before the
    # second verdict can be cast — that is the real cycle, and it puts a SECOND [worklog] on the
    # card, which is what the offering branch next door compares timestamps against.
    wf.advance(queued["id"], to="review", worklog="reworked", evidence="c0ffee")
    wf.review_task(queued["id"], verdict="approve", report="good now")     # [review] APPROVE

    parked = api.add_task("parked", "Build", assignee=api.me_user)
    wf.call_human(parked["id"], question="which option?")         # [needs-human]

    blocked = api.add_task("blocked", "Build", assignee=api.me_user)
    wf.return_task(blocked["id"], reason="waiting on infra")      # [blocked]

    big = api.add_task("big", "Build", assignee=api.me_user)
    wf.decompose(big["id"], [{"title": "part one"}, {"title": "part two"}], ordered=True)

    wf.file_task("a finding", description="found it", related_task_id=big["id"])
    wf.file_task("a queued finding", queue=True)
    wf.file_task("a plain finding")
    neighbour = api.add_project("neighbour", buckets=STAGES)
    wf.file_task("a cross-project finding", project_id=neighbour["id"])

    with_attachment = api.add_task("with an attachment", "Build", assignee=api.me_user)
    blob = tmp_path / "blob.bin"
    blob.write_bytes(b"x" * 2048)
    wf.attach_file(with_attachment["id"], str(blob), note="a screenshot")   # [attach]

    # the epic container's assembled notice: one child, taken to Review by the same agent
    epic = api.add_task("epic parent", "Backlog", labels=("epic",))
    child = api.add_task("only child", "Build", assignee=api.me_user)
    api.add_relation(child["id"], epic["id"], "parenttask")
    wf.advance(child["id"], to="review", worklog="child done", evidence="cafe")

    return [
        text
        for task_id in sorted(api.tasks)
        for text in api.comments_text(task_id)
    ]


def _attach_line(wf, api, tmp_path, size):
    """The one comment whose body crosses a function boundary: `_human_size`'s units render
    inside the `[attach]` line from another function, so it needs its own driver."""
    target = api.add_task("with an attachment", "Build", assignee=api.me_user)
    blob = tmp_path / f"blob-{size}.bin"
    blob.write_bytes(b"x" * size)
    wf.attach_file(target["id"], str(blob), note="a screenshot")
    return [c for c in api.comments_text(target["id"]) if c.startswith("[attach]")][0]


def _marker(comment):
    """The bracket at the head of a comment, `[` through `]` inclusive."""
    assert comment.startswith("["), f"comment does not open with a marker: {comment!r}"
    return comment[: comment.index("]") + 1]


# --- the acceptance pins -------------------------------------------------------------------

def test_the_driver_reaches_every_marker(tmp_path):
    """The completeness check under the flip pin: a marker the driver never writes is a marker
    the flip pin never compares, and it would go untested while looking covered."""
    api, wf = _wf("en")
    written = {_marker(c) for c in _drive_every_comment_site(api, wf, tmp_path)}
    missing = sorted(set(_MARKERS) - written)
    assert not missing, (
        f"_drive_every_comment_site never writes {missing}, so the invariance pin below silently "
        f"stops covering it. Add the transition that emits it, or — if the marker was genuinely "
        f"retired — drop it from _MARKERS in test_card_text_is_ascii.py, which owns that list"
    )


def test_flipping_the_language_moves_the_body_and_never_the_marker(tmp_path):
    """BUILT AND MEASURED: two full boards, one per language, compared position for position.

    The two runs drive the same sequence of transitions, so the streams line up. Two things are
    then asserted, and they fail in opposite directions. Every pair's MARKER must be byte-
    identical — a marker moving with the language means the key did too much. And the set of
    comments left UNCHANGED must be exactly the four markers whose whole body is agent-supplied —
    a comment joining that set means a call site stopped consulting the key, i.e. it did too
    little. The unchanged SET is asserted rather than a count of changed ones, because it is the
    sharper statement: it says which comments are the tool's own prose and which are not.
    """
    en_api, en_wf = _wf("en")
    ru_api, ru_wf = _wf("ru")
    en = _drive_every_comment_site(en_api, en_wf, tmp_path / "en")
    ru = _drive_every_comment_site(ru_api, ru_wf, tmp_path / "ru")

    assert len(en) == len(ru) > 10, (
        f"the two runs produced {len(en)} and {len(ru)} comments; they must take the same path "
        f"for a positional comparison to mean anything, and there must be enough of them to be "
        f"worth comparing"
    )
    for en_comment, ru_comment in zip(en, ru):
        assert _marker(en_comment) == _marker(ru_comment), (
            f"the marker moved with the language: {_marker(en_comment)!r} under en against "
            f"{_marker(ru_comment)!r} under ru. Markers are a WIRE FORMAT — workflow.py matches "
            f"rendered comment text with startswith() — so a per-language spelling silently "
            f"re-routes the review offering on every card written under the other setting. "
            f"Translate the BODY; never the bracket"
        )

    # WHICH comments moved is a sharper statement than how many, and it is the second half of
    # the feature stated as a pin: a comment survives the flip UNCHANGED exactly when its body is
    # the agent's own text, which this tool never rewrites in any language. `[worklog]` is on the
    # changed side because the tool contributes the `Root cause:`/`Worklog:` prefixes around the
    # agent's words; `[review]` is on the unchanged side because the verdict token is not prose.
    unchanged = sorted({_marker(a) for a, b in zip(en, ru) if a == b})
    assert unchanged == ["[blocked]", "[needs-human]", "[review]", "[spec]"], (
        f"the flip left {unchanged} unchanged. The tool translates the prose IT authors and "
        f"nothing else, so the unchanged set is exactly the markers whose whole body is the "
        f"agent's own text. A marker leaving that list means a call site stopped consulting "
        f"`language`; a marker joining it means agent text started being rewritten"
    )
    # the ASCII half, measured here rather than reasoned: en stays ASCII, ru does not, and that
    # asymmetry is exactly what #1164's pin had to be re-derived for.
    assert all(c.isascii() for c in en), "the en board is no longer ASCII"
    assert any(not c.isascii() for c in ru), "the ru board came out ASCII — nothing translated"


def test_a_card_written_in_one_language_is_still_classified_after_the_flip():
    """THE REGRESSION THAT MATTERS: the review offering reads a card the OTHER setting wrote.

    Not a restatement of the marker pin next door. That one compares bytes; this one drives the
    consequence — `next_task`'s real offering branch, which decides whether a Review card is
    handed to a reviewer by comparing the timestamps of the last comment starting `[worklog]`
    against the last starting `[review]`. A localized marker passes neither, but a localized
    marker is not the only way to break this: anything that made the offering key off body text
    would fail here and pass there.

    Driven in BOTH directions, because the two are not symmetric in the code: `en` writing and
    `ru` reading exercises a reader whose own table is Russian, and the reverse exercises a
    Russian card read by an English reader — the shape a project gets the day its human edits
    the toml.
    """
    for writer_language, reader_language in (("en", "ru"), ("ru", "en")):
        api = FakeAPI(buckets=STAGES)
        writer = Workflow(api, project_id=3, language=writer_language)
        task = api.add_task("a card", "Queue")
        writer.claim(task["id"])
        writer.advance(task["id"], to="build", spec="the approach")
        writer.advance(task["id"], to="review", worklog="what was done", evidence="deadbeef")

        # the human flips .vikunja-mcp.toml; the next session's Workflow reads the OTHER language
        reader = Workflow(api, project_id=3, language=reader_language)
        offer = reader.next_task()
        assert offer.get("review") is True and offer["task"]["id"] == task["id"], (
            f"a card whose [worklog] was written under {writer_language!r} is no longer offered "
            f"for review by a workflow reading {reader_language!r}: got {offer!r}. The offering "
            f"branch matches the RENDERED marker with startswith(), so this is what a localized "
            f"marker costs — every card written before the flip stops being reviewable"
        )
        assert offer["language"] == reader_language, (
            "the payload must report the CURRENT setting, not the one the card was written under"
        )

        # ...and the verdict still takes it back off the offering, from the other side of the flip
        reader.review_task(task["id"], verdict="approve", report="fine")
        assert reader.next_task().get("review") is not True, (
            "a verdict written under the reader's language no longer suppresses the offering, so "
            "the same card would be dispatched to a reviewer on every tick"
        )


def test_the_default_is_en_with_no_toml_key(tmp_path):
    """No `language` in the toml -> `en`, and that is resolved in `load_config`, not guessed by a
    reader. Asserted through a real file rather than by constructing a Config, because the
    default belongs to the READ: a Config built by hand would pass on the dataclass default even
    if `load_config` had stopped consulting the toml at all."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\n', encoding="utf-8"
    )
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "t"})
    assert cfg.language == "en" == DEFAULT_LANGUAGE

    # and it reaches a card: an unconfigured Workflow writes the English body
    api, wf = _wf(cfg.language)
    task = api.add_task("a card", "Queue")
    wf.claim(task["id"])
    assert api.comments_text(task["id"])[0].startswith("[claim] agent-infra claimed this task")


def test_an_unknown_language_is_refused_by_name(tmp_path):
    """An un-honourable option is un-expressible LOUDLY — the `wip_limit = 0` precedent.

    Falling back to the default would be the worst outcome available: the key's larger half is
    an INSTRUCTION to the agent, so a silent fallback tells it to write in the wrong language
    and leaves no signal anywhere. The refusal names the accepted set, since the whole point is
    that the reader can fix it without opening the source.
    """
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\nlanguage = "de"\n',
        encoding="utf-8",
    )
    with pytest.raises(ConfigError) as excinfo:
        load_config(cwd=tmp_path, environ={"VIKUNJA_TOKEN": "t"})
    message = str(excinfo.value)
    assert "de" in message
    for known in LANGUAGES:
        assert known in message, f"the refusal does not name {known!r} as an accepted value"


def test_language_is_toml_only_and_never_read_from_the_environment(tmp_path):
    """Committed TEAM POLICY, exactly like `wip_limit` and `require_review_independence`.

    Which language a project's cards are written in is a property of the PROJECT, reviewed by
    the whole team in a committed file — not of the machine that happens to be running an agent.
    So the env layers are not consulted at all. The check sets the value in BOTH env layers a test
    can reach — the process environment and the repo-local `.vikunja-mcp.env` beside the toml —
    under a `VIKUNJA_`-prefixed and a bare spelling each, so whichever name a future reader might
    join in is already there to lose. The third env layer, `~/.config/vikunja-mcp/env`, is a real
    machine path and is deliberately left alone rather than pretended at; "all three at once" was
    written here and is not what this test does.
    """
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "https://tracker.example"\nproject_id = 10\nlanguage = "ru"\n',
        encoding="utf-8",
    )
    (tmp_path / ".vikunja-mcp.env").write_text(
        "VIKUNJA_TOKEN=t\nVIKUNJA_LANGUAGE=en\nlanguage=en\n", encoding="utf-8"
    )
    cfg = load_config(
        cwd=tmp_path,
        environ={"VIKUNJA_TOKEN": "t", "VIKUNJA_LANGUAGE": "en", "language": "en"},
    )
    assert cfg.language == "ru", (
        "an env layer overrode the committed toml. `language` is team policy on wip_limit's side "
        "of the config split, not machine-local state like worktree_root"
    )


def test_next_task_carries_the_language_beside_wip():
    """The half of the feature this tool cannot enforce: the agent has to be TOLD.

    The spec, worklog and review report are the bulk of a card's text and `workflow.py` does not
    write a character of them, so the key reaches that text only as an instruction — the payload
    below, plus the SKILL.md rule itself, which
    `test_skill_contract.py::test_the_language_rule_names_a_key_the_code_actually_emits_and_reads`
    holds against the very line asserted here. Checked on the empty-queue
    signal as well as on a task-bearing one because `with_wip` wraps both, and a payload the
    agent reads only when there is work would miss the tick where it reads the rules.
    """
    for language in LANGUAGES:
        api, wf = _wf(language)
        assert wf.next_task()["language"] == language          # empty queue
        api.add_task("free work", "Queue")
        offered = wf.next_task()
        assert offered["task"] is not None
        assert offered["language"] == language
        assert set(offered["wip"]) == {"active", "limit", "free"}, (
            "language must ride BESIDE wip, not inside it — the hub and the rulebook both read "
            "`wip` as a fixed three-key shape"
        )


def test_the_verdict_tokens_do_not_translate():
    """`[review] APPROVE` / `[review] NEEDS WORK` are tokens, not prose, and stay out of the table.

    SKILL.md tells a reviewer that the verdict is on the first line in exactly those spellings
    (its "record the verdict IMMEDIATELY" bullet), so localising either would make the rulebook
    false for one of the two languages, and the same spellings are what anyone scanning a card by
    eye reads. It does NOT ask the orchestrator to grep for them — that claim was written here and
    withdrawn: SKILL.md routes the orchestrator's verdict signal through `review_task`'s result and
    the `reviewed`/`review-failed` labels, and the only other occurrences of `APPROVE` in that file
    are narrative about past cards. So a `ru` board still carries the English tokens — verified by
    building one, since the table is where a body would otherwise be tempted to absorb them.
    """
    api, wf = _wf("ru")
    task = api.add_task("a card", "Queue")
    wf.claim(task["id"])
    wf.advance(task["id"], to="build", spec="подход")
    wf.advance(task["id"], to="review", worklog="сделано", evidence="deadbeef")
    wf.review_task(task["id"], verdict="needs_work", report="ещё нет")
    wf.advance(task["id"], to="review", worklog="переделано", evidence="c0ffee")
    wf.review_task(task["id"], verdict="approve", report="теперь хорошо")
    verdicts = [c for c in api.comments_text(task["id"]) if c.startswith("[review]")]
    assert [v.split("\n")[0] for v in verdicts] == ["[review] NEEDS WORK", "[review] APPROVE"]


# --- the table's own shape ------------------------------------------------------------------

def test_every_key_carries_every_language():
    """A half-filled row is the failure mode a two-column table has: `card_text` falls back to the
    default when a row is missing the requested language, which is the right runtime behaviour
    (a card comment is the wrong place to discover a typo) and exactly why the gap has to be
    caught here instead."""
    for key, row in cardtext._TABLE.items():
        assert set(row) == set(LANGUAGES), (
            f"card text {key!r} carries {sorted(row)} but the accepted set is {list(LANGUAGES)}. "
            f"card_text falls back to {DEFAULT_LANGUAGE!r} on a missing row, so a gap here ships "
            f"as one untranslated line on an otherwise translated board rather than as an error"
        )


def test_no_card_text_entry_contains_a_marker_bracket():
    """The structural half of "the marker never translates".

    The behavioural half is the flip pin above; this one removes the way the bracket could get
    in. Every value here is a BODY — the marker is a literal at its own `add_comment` call site
    in `workflow.py`, where #1164's derived scan and its `_MARKERS` list can both see it. A
    bracket appearing in this table would move a marker out of that scan's reach in the same
    edit that gave it a per-language spelling.
    """
    for key, row in cardtext._TABLE.items():
        for language, template in row.items():
            assert "[" not in template, (
                f"card text {key!r} ({language}) contains a '[': {template!r}. Markers stay as "
                f"literals at their add_comment call site in workflow.py — that is what keeps "
                f"them inside test_card_text_is_ascii.py's derived scan and its _MARKERS list, "
                f"and what keeps them from acquiring a per-language spelling here"
            )


def test_card_text_refuses_an_unknown_key_and_tolerates_an_unknown_language():
    """The two failure modes are deliberately opposite, so both are pinned.

    An unknown KEY is a programming error with no runtime input behind it — raise. An unknown
    LANGUAGE cannot originate from a config file (`load_config` refuses it by name) so it means
    a hand-built `Workflow`, and `card_text` is called mid-transition, after the board has
    already been moved: raising there would leave a card moved with no journal entry.
    """
    with pytest.raises(KeyError):
        cardtext.card_text("en", "no_such_key")
    assert cardtext.card_text("de", "filed_backlog") == cardtext.card_text("en", "filed_backlog")


def test_human_size_units_follow_the_language(tmp_path):
    """`_human_size` renders INSIDE the `[attach]` line from another function, so its units are
    card text and follow the key like every other body. Driven through `attach_file` rather than
    called directly: what makes the units card text is that they reach a comment, and a direct
    call would not measure that."""
    en_api, en_wf = _wf("en")
    ru_api, ru_wf = _wf("ru")
    for size in (512, 2048, 5 * 1024 * 1024):
        en_line = _attach_line(en_wf, en_api, tmp_path, size)
        ru_line = _attach_line(ru_wf, ru_api, tmp_path, size)
        assert _marker(en_line) == _marker(ru_line) == "[attach]"
        assert en_line.isascii() and not ru_line.isascii(), (
            f"the [attach] size units did not follow the language at {size} bytes: "
            f"{en_line!r} against {ru_line!r}"
        )
