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
control after restore 0 failed. That mutation targeted the PRE-#720 sentence, and both it and
the pin that held it are gone now — see round 5. EVERY round recorded in this docstring has its
OWN selection and its OWN control, which is why they are separate paragraphs and not one table.
This sentence used to open by COUNTING those rounds ("the three rounds..."), and two later cards
appended a round each without touching it — #657 in `d60cdad` and #720 in `ce056ed`, taking the
docstring to five while the tally stayed at three. VMCP-196 (733) reported that; the repair is to
carry no tally at all, since it is one nobody is prompted to update while adding the round that
falsifies it.

The truncation round is the load-bearing one, and NOT because the others are all prose — an
earlier version of this sentence said "every other mutation here damages prose the tests read",
which its own list refutes: making review_task's `report` optional edits a SIGNATURE, and so do
round 4's two below. What makes it load-bearing is that it alone, among the rounds recorded
here, plants the exact defect this card hypothesised — a real size threshold in serialisation
— and the stdio test goes red on it. Without that round the stdio test would be pinning a
property nothing had shown it could measure.

Said plainly, because a sweep that claims more than it did is the failure this repo keeps
catching — and this paragraph WAS the instance. It used to say that two tests here were "NOT
killed by any round, and cannot be by mutating this repo ... which no edit to our source
changes". The first half holds; the second was FALSE, and review disproved it by construction.
Round 4, selection = exactly test_a_dropped_optional_argument_reaches_the_tool_body_as_none
and the misspelling test — which VMCP-192 (720) has since RENAMED to
`test_a_misspelled_parameter_name_is_now_refused_BY_NAME`, because that card closed the class
this round measured. The rounds below are history and are kept at the tree they were taken on;
do not re-read them as describing today's behaviour, where an unknown key is refused at the
boundary rather than dropped. Control 0 failed; `advance`'s
signature in server.py written as `worklog: str = ""` -> 2 failed, BOTH of them; written as
`worklog: object | None` -> 1 failed, the misspelled one; control after restore 0 failed. The
surviving half was re-measured in that round rather than inherited: all EIGHT mutations of
rounds 1-3, replayed against this same two-test selection, are 0 failed.

Round 5 is VMCP-192 (720)'s REWORK, and what it guards is a defect the fix itself introduced:
closing the class made three AGENT-FACING texts false, since all three still said a misspelling
is dropped in silence and should be checked FIRST. Selection = this file plus test_server.py at
72 tests, `__pycache__` deleted and then PYTHONDONTWRITEBYTECODE=1, each round restored from a
byte copy and confirmed sha256-identical: control 0 failed; restore the pre-#720 "CHECK THE
PARAMETER NAME FIRST" sentence into `_LOST_ARGUMENT_HINT` -> 1 failed,
test_the_refusal_RULES_OUT_the_misspelling_cause alone; drop the `sys.stderr is None` guard from
the degradation path -> 1 failed, test_the_forbid_gate_degrades_instead_of_killing_the_server
alone; control after restore 0 failed. Both deaths are on-name. What that does NOT cover is
worth writing down rather than leaving to be discovered: the pin reads `_LOST_ARGUMENT_HINT` and
nothing else, so the same retired advice reinstated in SKILL.md or in `advance`'s docstring dies
to no test here — those two are pinned for QUOTING `arrived as null`, never for what they say
its cause might be. The gap is the reason this card was bounced in the first place, so it is a
known hole, not a fixed one. An independent second pass replayed both rounds in its own clone,
same selection, and got the same numbers: control 0 failed, each mutation 1 failed, each
on-name, control after restore 0 failed. That replicates the ROUNDS and says nothing about the
PROSE — the same pass disproved two claims made around them, and both corrections are written
where the claims were, not here.

Where each death LANDS is worth writing down, because two of the three are on-name and one is
not — and "each died on its own assertion, not collaterally" is what this paragraph said first,
which over-covers M3. M2 kills both on-name: `assert not (False or True)` once an explicit null
stops being accepted, and `assert 0 == -1` once a dropped key defaults to "" rather than None.
M3 kills the misspelling test on a DIFFERENT assertion — `assert "worklog" in str(...)`, the
wrong-TYPE half — because `object | None` lets 12345 through to the stub, where `len()` raises
a message naming nothing. Under M3 the misspelling property itself still holds. So M3 is not
collateral either, but what it measures is pydantic's by-name type rejection, not the name in
the test.

So what they characterise is an SDK behaviour AT A SIGNATURE WE WRITE. The SDK is what
discards an unknown key and defaults an absent optional; `advance`'s signature is what says
`worklog` is an optional string in the first place, and the two edits above are what move it
out of that shape. test_advance_keeps_worklog_optional_in_its_schema pins the shape and says
why it is deliberate — it is also where the old claim contradicted itself, since it already
stated that a REQUIRED `worklog` would change this very behaviour.
"""
import asyncio
import io
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


def test_review_refusal_offers_the_workaround_instead_of_an_identical_retry(env):
    """The old text sent agents to retry an identical call — the filing card did it 3x.

    Pinned as "NOT the fix" rather than "will not help", which is what this file said first and
    is stronger than anything measured: the loss was never reproduced, and WHICH KIND of loss it
    is was never established either. The filing card's success came from replacing the ~7 KB
    worklog with `worklog="probe"`, not from repeating the identical call, so its evidence does
    NOT show non-determinism — three failures at ~7 KB then a success at 5 characters is what a
    SIZE-DEPENDENT, deterministic loss looks like. The successes bound nothing in either
    direction, and nobody knows whether a retry would be futile or merely lucky; either way it is
    not the fix, because it addresses no cause and the next long report meets the same wire. That
    weaker claim is the one the measurements support."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError) as exc:
        wf.advance(t["id"], to="review", evidence="a" * 40)
    msg = str(exc.value)
    assert "an identical retry is NOT the fix" in msg
    assert "will not change that" not in msg, "the old over-claim must not creep back"
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
    # 77_824 chars / 147_456 bytes = 144.0 KiB. Counted, not estimated: the repeated unit is 19
    # CHARACTERS and 36 BYTES, because 17 of the 19 are Cyrillic and cost two bytes each — the
    # eyeball figure this comment used to carry (~35 K / ~65 KB) was low by 2.2x and 2.3x.
    worklog = "проверено запуском\n" * 4096
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
# multiple-of-the-failure is claimed here, and the ambiguity survives into the factor rather
# than being quietly resolved: read "~7 KB" as 7168 bytes and these are 70.9x and 72.0x, read
# it as 7000 and they are 72.6x and 73.7x (measured, all four). Written as a factor because
# "two orders of magnitude", the phrase this comment carried first, rounds 1.85 up and was the
# one number here nobody had run.
# BOTH shapes are present because stdio framing is newline-delimited:
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
    deterministic — and whether this one is deterministic was never established: the card's
    success came from `worklog="probe"`, not from repeating the call, so "three refusals, then
    a success" sounds like proof of non-determinism and is not. That leaves (1) withholding the
    ground for a bound rather than refuting one: neither "longer than N always fails" nor "up to
    N is safe" follows from a success.
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


def test_a_misspelled_parameter_name_is_now_refused_BY_NAME():
    """VMCP-192 (720) closed the class this test used to CHARACTERIZE, and the inversion is the
    deliverable — so the history stays rather than being tidied away.

    WHAT IT PINNED BEFORE: an unknown key was discarded without a word, so `wroklog=<7 KB>`
    produced the exact refusal a lost argument does — byte-identical, measured, to omitting the
    key and to passing `worklog=None`. The agent was told to write a report it had already
    written. A wrong TYPE, by contrast, was always rejected loudly and by name, which is what
    made the silence look like a policy rather than a gap.

    WHAT IT PINS NOW: `_forbid_unknown_tool_arguments` turns every one of the 12 tools' argument
    models to `extra="forbid"` after registration, so the same call is refused at the boundary
    and the refusal NAMES `wroklog`. The wrong-TYPE row stays, unchanged, as the control it
    always was — if both rows ever passed again the gate would be off.

    Why this is worth doing at all when a LOST argument arrives the same way: #657 measured that
    the loss is not ours (nothing truncates below 4-8 MiB in this server or its transport), while
    the typo is entirely ours. Closing it shrinks an ambiguous class to the half nobody here can
    control, which is the most a gate can do about it.

    MUTATION-CHECKED for #720, selection `tests/unit/test_advance_report_arguments.py` plus
    `tests/unit/test_server.py`, `__pycache__` deleted and then PYTHONDONTWRITEBYTECODE=1, each
    round restored from a byte copy and the file confirmed sha256-identical; the script refuses
    unless its target matches exactly once. Control round: 0 failed.
      * drop the `_forbid_unknown_tool_arguments` call from `_server()` -> 2 failed, this test
        and the all-12 sibling
      * keep the call but drop the `tool.parameters = …` line -> 1 failed, the sibling's
        ADVERTISED half alone. That is what says the two halves are separate facts rather than
        one written twice
      * keep the call but drop `model_rebuild(force=True)` -> 1 failed, THIS test alone: the
        config key is set (so the sibling's `extra` half is green) while validation still lets
        the misspelling through. A pin on `model_config` alone would have called that a pass
    Three rounds, three different failure sets — the shape that says each assertion is load-
    bearing on its own.
    """
    typo, wrong_type = _drive_probe_server([
        ("advance", {"task_id": 657, "to": "review", "wroklog": "ц" * 7000,
                     "evidence": "a" * 40}),
        ("advance", {"task_id": 657, "to": "review", "worklog": 12345,
                     "evidence": "a" * 40}),
    ])
    assert typo[0], "an unknown key is accepted again — the extra='forbid' gate is off"
    assert "wroklog" in str(typo[1]), (
        f"the refusal must name the offending key, or it is no better than the old silence: "
        f"{typo[1]}"
    )
    assert wrong_type[0], "a wrong TYPE, the control, is refused at the boundary as it always was"
    assert "worklog" in str(wrong_type[1])


def test_every_tool_forbids_an_unknown_argument_and_publishes_that():
    """The gate is global on purpose: `advance` is where it was measured, but a rule that held
    for one tool would be forgotten by the thirteenth. Both halves are asserted, because they
    are separate facts — `extra="forbid"` is what the server ENFORCES, `additionalProperties:
    false` is what it ADVERTISES, and the published schema is frozen at registration, so
    setting only the first would refuse calls the advertised schema still calls legal."""
    from vikunja_mcp import server

    tools = server._server()._tool_manager._tools
    assert len(tools) == 12, f"the tool surface moved: {sorted(tools)}"
    unforbidden = sorted(
        name for name, tool in tools.items()
        if tool.fn_metadata.arg_model.model_config.get("extra") != "forbid"
    )
    assert not unforbidden, f"these tools still accept unknown arguments silently: {unforbidden}"
    unadvertised = sorted(
        name for name, tool in tools.items()
        if tool.parameters.get("additionalProperties") is not False
    )
    assert not unadvertised, (
        f"these tools enforce the gate but do not publish it: {unadvertised} — a client reading "
        "the schema would still believe an extra key is allowed"
    )


def test_the_forbid_gate_degrades_instead_of_killing_the_server():
    """`_tool_manager` is private and `mcp` is pinned `>=2,<3` while the `stable` channel
    re-resolves dependencies and ignores the lock — so a minor SDK release can move this handle.
    When it does, the stdio server must still start: the old ambiguity is bad, a server that
    refuses to boot for every consumer is worse. One line to stderr, never stdout (a byte there
    is where the framing lives), and no exception.

    The second half is the `sys.stderr is None` case (fd 2 closed at exec), added when #720 was
    bounced. It is checked EXPLICITLY in the source rather than caught, and that is not a style
    choice: measured, `print(x, file=None)` writes to STDOUT and raises nothing, so the
    `except` around it can never see it — the same trap CLAUDE.md records for `claimable_cmd`.
    Without the check the degradation path splices a non-JSON-RPC line into the protocol stream.
    Priced, also by running, and the price SPLITS on what lands there: a real mcp 2.0 client
    survives a complete LINE — it logs `Failed to parse JSONRPC message from server`, then
    initialize and a tool call both succeed — but a single BYTE with no newline HANGS it,
    initialize never returning. `print` appends the newline, so this path can only ever emit the
    survivable one. The source docstring carries the same split, because an earlier draft of it
    retired "a byte on stdout corrupts the protocol" as an overstatement when that sentence is
    true of a byte; and the parse-error log is not the discriminator, appearing in both."""
    from vikunja_mcp import server

    class Broken:
        @property
        def _tool_manager(self):
            raise AttributeError("the SDK moved this")

    captured = io.StringIO()
    stderr, sys.stderr = sys.stderr, captured
    try:
        server._forbid_unknown_tool_arguments(Broken())      # must not raise
    finally:
        sys.stderr = stderr
    assert "could not forbid unknown tool arguments" in captured.getvalue()
    assert "dropped silently" in captured.getvalue()

    # ...and with no stderr at all, the line must be DROPPED rather than land on stdout.
    on_stdout = io.StringIO()
    stdout, sys.stdout = sys.stdout, on_stdout
    stderr, sys.stderr = sys.stderr, None
    try:
        server._forbid_unknown_tool_arguments(Broken())      # must not raise, must not print
    finally:
        sys.stderr, sys.stdout = stderr, stdout
    assert on_stdout.getvalue() == "", (
        f"the degradation notice reached STDOUT, where the JSON-RPC framing lives: "
        f"{on_stdout.getvalue()!r}"
    )


def test_the_refusal_RULES_OUT_the_misspelling_cause():
    """The mirror of the sibling above, on the agent-facing side, and it was INVERTED with it.

    Until #720 this test was `test_the_refusal_names_the_misspelling_cause`, and its entire body
    was `assert "misspelling" in str(exc.value)`. An earlier draft of this docstring said that
    pin "would have gone on HOLDING the retired sentence in place", and that is measurably FALSE
    — the independent second pass disproved it by running the verbatim old assertion against the
    CORRECTED text, where it PASSES, because the new sentence contains the word too ("so a
    misspelling cannot reach this text"). The old pin was not a holder; it was BLIND, green
    whichever direction shipped, and unable to tell "check the name first" from "the name is the
    one thing ruled out". That is the better argument for retargeting, and it is the true one.

    WHY THE REFUSAL CAN SAY THIS AT ALL. Re-measured over real stdio on this tree, not inherited:
    `advance(to='review', wroklog=<7000 chars>, evidence=<40>)` comes back `isError=True` with
    "wroklog … Extra inputs are not permitted" and the tool body never runs, while the same call
    with the key OMITTED reaches the body with `worklog_len == -1`. So a typo cannot produce this
    text, and an agent who reads it has already ruled that cause out by reading it.

    The negative assertion is the load-bearing one: the positive phrasing could be reworded
    honestly by a later card, but the pre-#720 imperative reappearing means the prose regressed.
    """
    msg = _refusal_text(evidence="a" * 40)
    assert "CHECK THE PARAMETER NAME FIRST" not in msg, (
        "the pre-#720 advice is back in an agent-facing refusal: a misspelling is now refused "
        f"at the boundary, so this sends agents to check the one cause reading it excludes: {msg}"
    )
    assert "misspelling cannot reach this text" in msg, (
        f"the refusal must say WHY the name is not worth checking, not merely omit it: {msg}"
    )
    assert "THREE and no longer four" in msg, (
        f"the count moved with the gate — three causes still arrive as null, not four: {msg}"
    )


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
