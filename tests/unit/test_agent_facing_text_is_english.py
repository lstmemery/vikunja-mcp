"""THE TEXT `workflow.py` HANDS BACK TO AN AGENT IS ENGLISH — the one string population the
`language` key deliberately does not reach (tracker #1166).

THREE POPULATIONS, AND THIS FILE OWNS THE THIRD. `cardtext.py` holds what the tool writes onto a
CARD, keyed by language, and `tests/unit/test_card_text_is_ascii.py` plus
`tests/unit/test_card_language.py` pin it. An agent's own `spec`/`worklog`/`question` is
unconstrained by design. What is left is what a TOOL CALL returns to its caller — a
`WorkflowError` message, and the `message`/`note` keys of a `next_task` payload. #1165 put that
population OUT of the table on purpose, and `cardtext.py`'s own docstring says so in the bullet
naming `WorkflowError` text and the `note`/`message` strings in tool payloads: their audience is
the AGENT, they are effectively prompt content, and they land in logs. So it is ONE language for
every consumer whatever their `language` key says, and that language is the one this repo's
README, CLAUDE.md and SKILL.md are written in.

WHY THIS FILE EXISTS. #1164 translated the card text and left this population alone, on a rule
that reads "Leave it English; do not fold it into any later localization" — an INSTRUCTION, which
this file now enforces. What went wrong is the sentence that grew beside it: that card's own build
note and worklog paraphrase the rule as "leave it English (it already is)", and the parenthetical
was untrue. Two strings said otherwise and shipped: `_cycle_signal`'s five-line
`message`, sitting in the same returned dict as a fully English `note` — one payload, one
field in each language — and `claim`'s epic-container fallback, which rendered `его
подзадачами` at the end of an otherwise English sentence whenever the epic had no subtasks to
name. Neither is card text, so neither ASCII gate could see them — and what the rest of the suite
held was worse than nothing on one of the two: `test_workflow_sequence_gate.py` asserted
`"цикл" in res["message"].lower()`, so TRANSLATING that message was the red test, and two further
pins over the same message read its interpolated values through contiguous literals, one of them
spelling `задач(и)`. The fallback is the mirror case: its branch IS exercised, by
`test_claim_refuses_childless_epic_gracefully` in `test_workflow_epic_skip.py`, which matches on
the word "container" and never looks at the tail of the sentence. That is the shape this file is
against — a green suite that renders the string and does not read it.

THE UNIT IS CYRILLIC, NOT ASCII, AND THAT IS MEASURED RATHER THAN CHOSEN. The card-text gates next
door assert ASCII, which is right for them: a marker is a wire format and a card body is prose
this repo keeps typographically plain. This population is different — it is English prose full of
em dashes and arrows, so an ASCII pin over it is red on arrival. The test below named for that
asserts it as a PROPERTY rather than writing the count down, because the count moves with every
refusal anyone adds. What the Cyrillic unit costs is stated where it is felt: a
lookalike inside a Latin word (`а` for `a`) is caught, a Greek or Hebrew string would not be, and
neither would an English sentence translated into a language written in Latin script. The defect
it is pinned against is the one that happened, twice, in a repo whose other language is Russian.

WHAT THE STATIC SCAN CAN AND CANNOT SEE. It reads every string literal in `workflow.py` that is
not a docstring, which is broader than the two call sites and deliberately so: an agent-facing
string is not marked as one, so the cheap complete rule is "this module's own literals are
English" — the per-language prose lives in `cardtext.py`, which is exactly where a translation
belongs. Docstrings are exempt because #1164 left the Russian code documentation alone on
purpose, and `workflow.py`'s docstrings are read by a human in the source, never shipped to an
agent (`server.py`'s tool docstrings ARE shipped, and are a neighbouring population this file
does not cover — filed separately). What the scan cannot see is text composed elsewhere and
interpolated in, which is `cardtext.py`'s job and pinned there; the two runtime tests below cover
that for the two paths this card is about, by rendering them and reading the result.

MUTATION SWEEP. Selection is this file alone, so no collateral test can stand in for the pin. Run
in a CLONE of the worktree, `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` before every
round, `vikunja_mcp.__file__` printed each round and resolving inside the clone; `-q` dropped so
`collected` prints, and every round read by COUNTING lines beginning `FAILED ` with lines
beginning `ERROR ` counted separately. Each round states its own control:

* the `message` of `_cycle_signal` reverted to its pre-#1166 Russian, byte for byte: control 0
  failed / 0 errors / 4 collected; mutation 2 failed / 0 errors / 4 collected
  (`test_no_cyrillic_string_literal_in_workflow` and
  `test_the_predecessor_cycle_payload_is_all_one_language`).

* `claim`'s epic fallback reverted to its pre-#1166 Russian: control 0 failed / 0 errors / 4
  collected; mutation 2 failed / 0 errors / 4 collected
  (`test_no_cyrillic_string_literal_in_workflow` and
  `test_the_childless_epic_refusal_is_english_to_its_last_word`).

* the fallback replaced by a Cyrillic string that no longer reaches an agent at all — assigned to
  an unused local instead of interpolated — which is what separates the static half from the
  runtime half: control 0 failed / 0 errors / 4 collected; mutation 1 failed / 0 errors / 4
  collected, the static scan alone.

* the mirror of that one: the fallback translated into GREEK, non-ASCII and non-Cyrillic,
  reaching the agent exactly as before. Control 0 failed / 0 errors / 4
  collected; mutation 1 failed / 0 errors / 4 collected — the runtime pin alone, on its
  `its subtasks` literal, with the Cyrillic scan green. That is the blindness this file's unit
  buys, shown rather than asserted.

* closing control 0 failed / 0 errors / 4 collected, with `workflow.py` compared BYTE for byte
  against the pre-sweep baseline after every single restore, not only at the end — a patcher that
  matches nothing is the failure that looks exactly like a blind pin, so it asserts it replaced
  exactly one occurrence and the round is never reached if it did not.
"""
import ast
import pathlib
import re

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

_WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / "src/vikunja_mcp/workflow.py"

# Cyrillic + the Cyrillic Supplement, i.e. the script this repo's other language is written in.
_CYRILLIC = re.compile(r"[Ѐ-ԯ]")


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _non_docstring_literals() -> list[tuple[int, str]]:
    """(line, value) for every string literal in workflow.py that is not a docstring.

    A docstring is the first statement of a module, class or function and nothing else — an
    attribute "docstring" (a bare string after an assignment) is not one, and is read here like
    any other literal.
    """
    tree = ast.parse(_WORKFLOW.read_text(encoding="utf-8"))
    docstrings: set[int] = set()
    for node in ast.walk(tree):
        holders = (ast.Module, ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)
        if not isinstance(node, holders):
            continue
        first = node.body[0] if node.body else None
        if (isinstance(first, ast.Expr) and isinstance(first.value, ast.Constant)
                and isinstance(first.value.value, str)):
            docstrings.add(id(first.value))
    return [
        (node.lineno, node.value)
        for node in ast.walk(tree)
        if isinstance(node, ast.Constant) and isinstance(node.value, str)
        and id(node) not in docstrings
    ]


def test_no_cyrillic_string_literal_in_workflow():
    """workflow.py's own literals are English — the module writes no per-language prose.

    Broader than "the strings that reach an agent", because nothing marks a string as
    agent-facing and the neighbouring literals (a marker, a label, a dict key) are ASCII anyway.
    Translated prose has one home in this package and it is `cardtext.py`, where a language
    column is a structure rather than a literal typed at a call site.
    """
    offenders = [(line, lit) for line, lit in _non_docstring_literals() if _CYRILLIC.search(lit)]
    assert not offenders, (
        f"workflow.py holds {len(offenders)} Cyrillic string literal(s) outside its docstrings: "
        f"{offenders}. What this module returns to a caller — a WorkflowError message, a payload "
        f"`message`/`note` — is agent-facing prompt content: it lands in an orchestrator's log "
        f"and in a per-task agent's context, is NOT reached by the `language` key, and stays "
        f"English for every consumer. Prose that a consumer's `language` should translate goes "
        f"in cardtext.py's table, never in a literal here. Russian code comments and docstrings "
        f"are deliberately untouched and are not read by this scan"
    )


def test_an_ascii_unit_would_be_red_on_arrival():
    """WHY this file's unit is Cyrillic while the card-text gates next door assert ASCII.

    Asserted as a property rather than recorded as a count: the number of em dashes in this
    module's refusals moves with every refusal anyone writes, and a stale figure in a docstring
    would be the argument for "just use ASCII here too" the next time someone reads it.
    """
    plain_but_not_ascii = [
        lit for _line, lit in _non_docstring_literals()
        if not lit.isascii() and not _CYRILLIC.search(lit)
    ]
    assert len(plain_but_not_ascii) >= 40, (
        f"only {len(plain_but_not_ascii)} non-ASCII, non-Cyrillic literal(s) left in workflow.py "
        f"— if that has genuinely fallen to zero, the ASCII unit the card-text gates use becomes "
        f"available here too and this file should adopt it. Until then it would fail on the em "
        f"dashes and arrows this module's English prose is written with"
    )


def test_the_predecessor_cycle_payload_is_all_one_language(env):
    """The defect exactly as filed: a Russian `message` beside an English `note`, one dict.

    Renders the real payload rather than reading the source, so a message assembled from a helper
    or a module constant is covered on this path whatever its literals look like. Those are the
    two shapes VMCP-294 (1168) measured the neighbouring card-text resolver blind to; it landed
    just under this commit and closed them there, one statically and one by a runtime driver.
    Here the runtime form is the whole answer for this path, and the static scan next door is
    what reaches the sites no test drives.
    """
    api, wf = env
    a = api.add_task("A", "Queue")
    b = api.add_task("B", "Queue")
    api.add_relation(a["id"], b["id"], "follows")
    api.add_relation(b["id"], a["id"], "follows")

    res = wf.next_task()
    assert res["cycle"] is True
    for key in ("message", "note"):
        assert not _CYRILLIC.search(res[key]), (
            f"next_task's cycle payload renders a Cyrillic `{key}`: {res[key]!r}. Both keys of "
            f"this dict are read by the same orchestrator in the same breath — they are one "
            f"population and must be one language"
        )
    # Both sides absolute: the lead-in is spelled here, not imported, so the pin and the code can
    # genuinely disagree. It is also the phrase a human greps for when a chain has stalled.
    assert "PREDECESSOR CYCLE" in res["message"], res["message"]
    assert "Tasks in the cycle: " in res["message"], res["message"]


def test_the_childless_epic_refusal_is_english_to_its_last_word(env):
    """The tail of a sentence is where a translation gets forgotten, and nothing read this one.

    `test_workflow_epic_skip.py::test_claim_refuses_childless_epic_gracefully` drives this exact
    branch and matches on "container", so `его подзадачами` rode the last four words of an
    English refusal through a green suite for as long as it existed.
    """
    api, wf = env
    epic = api.add_task("empty epic", "Queue", labels=("epic",))
    with pytest.raises(WorkflowError) as exc:
        wf.claim(epic["id"])

    msg = str(exc.value)
    assert not _CYRILLIC.search(msg), (
        f"claim's epic-container refusal renders Cyrillic: {msg!r}. This is the fallback taken "
        f"when the epic has no subtasks to name, so it is the branch a reader reaches least "
        f"often and the one a translation misses first"
    )
    assert msg.endswith("work on those instead: its subtasks"), msg
