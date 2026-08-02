"""tracker #657 — a long worklog that never reached `advance`, and a refusal that blamed
the agent for it.

Two halves, because the defect had two:

1. The REFUSAL. `advance`'s report guards are disjunctive but their old text named every
   field whatever was actually wrong, and `(x or "").strip()` collapsed "arrived as null"
   into "arrived blank". So an agent whose 7 KB worklog was lost read the identical
   sentence as one who simply forgot `evidence` — and the only advice on offer was to
   write the report it had already written. Both facts are recoverable; these pin them.

2. The LAYER. The rest of this suite drives `Workflow` through `FakeAPI`, which is on the
   far side of the MCP boundary — so no test here could have seen an argument lost in
   serialisation or transport, and that blind spot is itself a finding. Stated precisely,
   because "no coverage at all" is too strong and an independent re-measure caught it:
   test_server.py DOES build the real MCPServer (`asyncio.run(server.mcp.list_tools())`),
   so schemas are generated under test. What no test did before this file is CALL a tool
   across the wire — none spun up stdio, none checked `required`/the input schema, and none
   round-tripped an argument to see what arrived. That is the gap the tests below close.

MUTATION SWEEP (2026-08-02, this tree based on dff2def, `__pycache__` deleted and THEN
PYTHONDONTWRITEBYTECODE=1, `vikunja_mcp.__file__` printed each round and confirmed to point
into this worktree). Round 1, selection = this file at the 11 tests it held before the doc
pins: control 0 failed; restore the pre-#657 to='review' guard -> 4 failed; restore the
pre-#657 to='build' guard -> 1 failed; stop distinguishing None from blank -> 2 failed;
TRUNCATE the arriving worklog at 4096 B inside the probe server -> 2 failed; make
review_task's `report` optional -> 2 failed; control after restore 0 failed.

Round 2, run separately AFTER the doc pins were added, selection = this file at 13 tests:
control 0 failed; SKILL.md stops quoting the phrase the refusal emits -> 1 failed; advance's
docstring stops quoting it -> 1 failed; control after restore 0 failed.

Round 3, after an independent re-measure added the misspelled-key case, selection = this file
at 15 tests: control 0 failed; the refusal stops naming the misspelling cause -> 1 failed;
control after restore 0 failed. The three rounds have DIFFERENT selections and each carries
its own control, which is why they are three paragraphs and not one table.

The truncation round is the load-bearing one. Every other mutation here damages prose the
tests read; that one plants the exact defect this card hypothesised — a real size threshold
in serialisation — and the stdio test goes red on it. Without that round the stdio test
would be pinning a property nothing had shown it could measure.

Said plainly, because a sweep that claims more than it did is the failure this repo keeps
catching: two tests here were NOT killed by any round, and cannot be by mutating this repo.
test_a_dropped_optional_argument_reaches_the_tool_body_as_none and
test_a_misspelled_parameter_name_is_dropped_in_silence CHARACTERISE the SDK's schema
boundary — extra keys ignored, absent optionals defaulted — which no edit to our source
changes. What the truncation and required-argument rounds establish is that this harness CAN
see a change at that boundary; the two characterisation tests trade killability for being the
only record of behaviour we depend on and do not control.
"""
import asyncio
import json
import os
import pathlib
import sys

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

PROBE_SERVER = pathlib.Path(__file__).with_name("_stdio_arg_probe_server.py")


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


# --------------------------------------------------------------------------------------
# 1. The refusal says WHICH field and HOW it arrived
# --------------------------------------------------------------------------------------

def test_review_refusal_names_only_the_field_that_is_unusable(env):
    """A full worklog plus a forgotten evidence must NOT read as "you owe a worklog"."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", worklog="сделано, проверено запуском")
    msg = str(exc.value)
    unusable = msg.split("Unusable in this call:", 1)[1].split(". worklog =", 1)[0]
    assert "evidence" in unusable
    assert "worklog" not in unusable, "worklog WAS passed — it must not be listed as unusable"


def test_review_refusal_distinguishes_never_arrived_from_arrived_blank(env):
    """None and "" are different states and the agent's next move differs between them."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")

    with pytest.raises(WorkflowError) as absent:
        wf.advance(t["id"], to="review", worklog=None, evidence="a" * 40)
    with pytest.raises(WorkflowError) as blank:
        wf.advance(t["id"], to="review", worklog="   ", evidence="a" * 40)

    assert "arrived as null" in str(absent.value)
    assert "arrived as null" not in str(blank.value)
    assert "empty or whitespace" in str(blank.value)


def test_review_refusal_lists_both_fields_when_both_are_unusable(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review")
    unusable = str(exc.value).split("Unusable in this call:", 1)[1].split(". worklog =", 1)[0]
    assert "worklog" in unusable and "evidence" in unusable


def test_review_refusal_offers_the_workaround_and_says_retrying_will_not_help(env):
    """The old text sent agents to retry an identical call — the filing card did it 3x."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", evidence="a" * 40)
    msg = str(exc.value)
    assert "retrying the identical call will not change that" in msg
    assert "comment()" in msg and "[worklog]" in msg


def test_build_refusal_gets_the_same_treatment(env):
    """`spec` is the same optional-string shape as `worklog`; a lost one must read alike."""
    api, wf, t = env
    with pytest.raises(WorkflowError) as absent:
        wf.advance(t["id"], to="build")
    with pytest.raises(WorkflowError) as blank:
        wf.advance(t["id"], to="build", spec="  ")
    assert "arrived as null" in str(absent.value)
    assert "empty or whitespace" in str(blank.value)
    assert "comment()" in str(absent.value)


def test_a_usable_report_still_advances_and_lands_verbatim(env):
    """The guard must keep passing what it always passed — including a large report."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    worklog = "проверено запуском\n" * 4096          # ~35 K chars, ~65 KB of UTF-8
    wf.advance(t["id"], to="review", worklog=worklog, evidence="a" * 40)
    assert api.stage_of(t["id"]) == "Review"
    landed = [c for c in api.comments_text(t["id"]) if c.startswith("[worklog]")][-1]
    assert landed.count("проверено запуском") == 4096


# --------------------------------------------------------------------------------------
# 2. The serialisation boundary FakeAPI cannot see
# --------------------------------------------------------------------------------------

def _drive_probe_server(cases):
    """Run `cases` against the real MCPServer over a real stdio transport, one process.

    Each case is (tool_name, arguments); returns a list of (is_error, payload) where
    payload is the parsed JSON result, or the raw error text when the call was refused.
    """
    from mcp import ClientSession, StdioServerParameters
    from mcp.client.stdio import stdio_client

    async def run():
        # Inherit the real environment and only ADD src to PYTHONPATH: a stripped env would
        # be a second thing that can differ between this machine and CI, and the subprocess
        # is `sys.executable` (this venv's interpreter), so it needs nothing exotic.
        params = StdioServerParameters(
            command=sys.executable, args=[str(PROBE_SERVER)],
            env={**os.environ,
                 "PYTHONPATH": str(pathlib.Path(__file__).parents[2] / "src")},
        )
        out = []
        async with stdio_client(params) as (read, write):
            async with ClientSession(read, write) as session:
                await session.initialize()
                for tool, args in cases:
                    res = await session.call_tool(tool, args)
                    text = next(
                        (b.text for b in res.content if getattr(b, "type", None) == "text"),
                        "",
                    )
                    try:
                        payload = json.loads(text)
                    except ValueError:
                        payload = text
                    out.append((bool(getattr(res, "is_error", False)), payload))
        return out

    return asyncio.run(run())


# Both payloads are 278528 characters (measured, not derived): 507904 UTF-8 bytes / 496 KiB
# for the multiline one and 516096 / 504 KiB for the single-line one — Cyrillic, because that
# is what this repo's reports are actually made of and a char is not a byte in it. The card
# reported failing at "~7 KB" without saying whether it counted characters or bytes, so no
# multiple-of-the-failure is claimed here; these are simply two orders of magnitude above any
# report an agent writes. BOTH shapes are present because stdio framing is newline-delimited:
# "8192 newlines" and "not one newline" are the two ways a framing bug would show, and JSON
# escaping is what keeps a payload newline from ever becoming a frame boundary — that is the
# property being pinned.
_MULTILINE = ("отчёт: проверено запуском, строка\n" * 8192)
_SINGLE_LINE = ("отчёт-без-единого-перевода-строки-" * 8192)


@pytest.mark.parametrize("payload", [_MULTILINE, _SINGLE_LINE], ids=["multiline", "one-line"])
def test_large_worklog_crosses_the_mcp_boundary_byte_exact(payload):
    """#657's first question — is the threshold OURS? Measured: no, not at this size."""
    (is_error, got), = _drive_probe_server([
        ("advance", {"task_id": 657, "to": "review", "worklog": payload,
                     "evidence": "a" * 40, "root_cause": "причина" * 128}),
    ])
    assert not is_error, got
    assert got["worklog_len"] == len(payload)
    assert got["worklog_head"] == payload[:24]
    assert got["worklog_tail"] == payload[-24:]


def test_a_dropped_optional_argument_reaches_the_tool_body_as_none():
    """The mechanism behind the misleading message: `worklog` is NOT in advance's required
    set, so a client that omits it — a truncated tool call looks exactly like this — does
    not get a protocol error. It reaches the guard as None, indistinguishable from an agent
    who never wrote one. That is why the refusal has to name the state rather than assume."""
    results = _drive_probe_server([
        ("advance", {"task_id": 657, "to": "review", "evidence": "a" * 40}),
        ("advance", {"task_id": 657, "to": "review", "worklog": None, "evidence": "a" * 40}),
        ("advance", {"task_id": 657, "to": "review", "worklog": "", "evidence": "a" * 40}),
    ])
    (absent_err, absent), (null_err, null), (empty_err, empty) = results
    assert not (absent_err or null_err or empty_err)
    assert absent["worklog_len"] == -1, "omitted key must arrive as None, not be rejected"
    assert null["worklog_len"] == -1
    assert empty["worklog_len"] == 0, "an empty STRING is a different state from None"


def test_the_two_tools_fail_differently_on_a_dropped_argument():
    """`report` is REQUIRED, so a dropped one is refused at the SDK boundary BY NAME and never
    reaches our guard; advance's optional `worklog` silently becomes None. Both measured below.

    Said carefully, because the obvious conclusion is too strong and an independent re-measure
    caught it here. The card's reviewer observed `review_task` ACCEPTING ~8 KB and concluded
    "the advance path specifically breaks". That observation is a SUCCESS: it proves 8 KB can
    cross this wire, which is a perfectly good control against "long strings never make it",
    and optionality has nothing to do with it — the asymmetry below only bites when a key is
    LOST, which never happened on review_task. Two OTHER things are what actually sink the
    conclusion. (1) One success cannot bound a threshold when the failure is not
    deterministic, and this one is already known not to be: three refusals, then a success.
    (2) SELECTION — a lost key on review_task is LOUD and can never be mistaken for "the agent
    wrote no report", so it could not have appeared in the observed sample at all, while the
    same loss on advance is silent and looks exactly like forgetting. The two tools' observed
    failure rates are therefore not comparable, and one review_task success does not measure
    how reliably arguments arrive. The required/optional difference EXPLAINS that selection;
    it is not itself the refutation."""
    (is_error, payload), = _drive_probe_server([
        ("review_task", {"task_id": 657, "verdict": "approve"}),
    ])
    assert is_error, "a dropped REQUIRED argument must be refused, not defaulted"
    assert "report" in str(payload)

    # ...and the same tool carries a large report fine, so this is about optionality,
    # not about bytes.
    (is_error, got), = _drive_probe_server([
        ("review_task", {"task_id": 657, "verdict": "approve", "report": _MULTILINE}),
    ])
    assert not is_error, got
    assert got["report_len"] == len(_MULTILINE)


# --------------------------------------------------------------------------------------
# 3. The two agent-facing docs must quote a string the tool still emits
# --------------------------------------------------------------------------------------

def _refusal_text(**kwargs) -> str:
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    t = api.add_task("job", "Design", assignee=api.me_user)
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", **kwargs)
    return str(exc.value)


@pytest.mark.parametrize("surface", ["skill", "docstring"])
def test_docs_quote_the_state_phrase_the_tool_actually_emits(surface):
    """SKILL.md and advance's docstring both tell agents to branch on the word the refusal
    uses for "never arrived". If that wording is reworded on one side only, the rulebook
    sends every agent looking for a string that is no longer produced — and SKILL.md
    self-heals onto consumers with no review gate of its own, so nothing else would catch
    it. Pin the ACTUAL emitted phrase against both copies rather than a literal on one."""
    from importlib.resources import files

    from vikunja_mcp import server

    emitted = _refusal_text(evidence="a" * 40)
    phrase = "arrived as null"
    assert phrase in emitted, "the refusal must keep naming the never-arrived state"

    if surface == "skill":
        text = files("vikunja_mcp").joinpath("skills/tracker/SKILL.md").read_text("utf-8")
    else:
        text = server.advance.__doc__ or ""
    assert phrase in text, f"{surface} must quote the phrase the refusal emits"
    # ...and both must carry the workaround, not just the diagnosis.
    assert "comment(" in text and "[worklog]" in text


def test_a_misspelled_parameter_name_is_dropped_in_silence():
    """The cause the first investigation missed, and the only one of the three the AGENT can
    fix: an unknown key is discarded without a word, so `wroklog=<7 KB>` produces the exact
    refusal a lost argument does. Unlike a wrong TYPE, which pydantic rejects loudly by name.
    That is why the refusal text tells agents to check the spelling FIRST."""
    typo, wrong_type = _drive_probe_server([
        ("advance", {"task_id": 657, "to": "review", "wroklog": "ц" * 7000,
                     "evidence": "a" * 40}),
        ("advance", {"task_id": 657, "to": "review", "worklog": 12345,
                     "evidence": "a" * 40}),
    ])
    assert not typo[0], "an unknown key is accepted, not refused"
    assert typo[1]["worklog_len"] == -1, "the misspelled value must arrive as None"
    assert wrong_type[0], "a wrong TYPE, by contrast, is refused at the boundary"
    assert "worklog" in str(wrong_type[1])


def test_the_refusal_names_the_misspelling_cause(env):
    """It is the one cause of the three that is actionable without leaving the session."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", evidence="a" * 40)
    assert "misspelling" in str(exc.value)


def test_advance_keeps_worklog_optional_in_its_schema():
    """Guards the reading above: if `worklog` ever became required, a lost one would start
    failing loudly at the boundary and the refusal's "arrived as null" branch would be dead
    code. It is optional because `advance` is two transitions in one tool — to='build' has
    no worklog to give — so this is a pin on a deliberate shape, not a wish."""
    from vikunja_mcp import server

    tools = {t.name: t for t in asyncio.run(server.mcp.list_tools())}
    advance_schema = tools["advance"].input_schema
    assert set(advance_schema["required"]) == {"task_id", "to"}
    assert advance_schema["properties"]["worklog"]["anyOf"] == [
        {"type": "string"}, {"type": "null"},
    ]
    assert set(tools["review_task"].input_schema["required"]) == {
        "task_id", "verdict", "report",
    }
