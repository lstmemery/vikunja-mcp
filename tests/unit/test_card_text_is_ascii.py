"""EVERY STRING THIS TOOL AUTHORS ONTO A CARD IS ASCII, AND THE MARKER VOCABULARY IS FROZEN THERE.

READ THE TITLE'S VERB. It is what the TOOL authors, not everything a card ends up carrying: an
agent's own `spec`, `worklog`, `question` or `attach_file` note travels through these same calls
and is deliberately NOT constrained — `tests/unit/test_workflow_gates.py` still passes a Russian
note into an `[attach]` comment and asserts it arrives intact, and `advance`'s report tests do
the same for a Russian `worklog` and `root_cause`. Only the literals the PRODUCT contributes are
read below. "Card text is ASCII end to end" would be a false claim, and an earlier draft of this
file made it in three places.

WHY THIS FILE EXISTS (#1164). The product used to write Russian into every consumer's tracker
while its README, CLAUDE.md and SKILL.md were English: a card read `[claim] nova взял задачу в
работу` under an English title, and TWO markers were Cyrillic in their own right — the
parked-question one and the epic-assembled one (`git show "HEAD~:src/vikunja_mcp/workflow.py"`
from the landing commit shows both). The prose was translated in the same commit that added
this file. What this file defends is the state AFTER that, because a translation is a one-off
and the next `add_comment` call site is not: nothing in the repo stopped anyone typing the next
card line in any language.

TWO DIFFERENT THINGS ARE PINNED, and confusing them is how the weaker half gets deleted.

* THE MARKERS ARE A WIRE FORMAT. `workflow.py` does `startswith("[review]")` and
  `startswith("[worklog]")` on rendered comment text, so those brackets are parsed, not read:
  a Review card carrying no `[worklog]` takes the "placed here by hand, not a review candidate"
  branch. A marker is therefore not prose and does not get translated, ever, in any language
  the cards are later written in. ASCII is the floor under that: a marker that is ASCII cannot
  acquire a per-language spelling by accident.
* THE BODY IS PROSE. It is translated here and may be translated again by the `language` key
  this card precedes. ASCII is not a claim that English is the only language a card may ever be
  written in — it is what makes the CURRENT text mechanically checkable, and the pin is expected
  to be re-derived (not deleted) if a language key ever lands.

`WorkflowError` text is out of scope by the card's own rule, and the two tests read it
differently rather than not at all. The `add_comment` scan never reaches it — nothing raises
through that call. The MARKER scan does read it, because it keeps every `[`-leading literal in
the file wherever it sits: constructed, a `WorkflowError` whose message begins `[отказ]` turns
`test_every_comment_marker_is_ascii` red (control 0 failed; that mutation 1 failed). No
`WorkflowError` begins with a bracket today, so the rule and the gate do not collide — but they
would, and a future card localising error text should know it is this test that will say so.

WHAT THE STATIC HALF CANNOT SEE, stated as narrowly as it was measured. The resolver chases
local names TRANSITIVELY inside the enclosing function — an f-string, implicitly concatenated
literals, and a name bound by assignment, `+=` or `list.append`, following each of those into
the names IT mentions. Transitive rather than one-hop is a correction, not a flourish: this
card's independent second pass constructed the counter-example that broke the one-hop version.
`attach_file` builds `journal = f"[attach] {name} ({meta})"`, and `meta` is a SECOND-hop local
carrying its own `", "` separator, so an em dash put there was invisible to the pin while
shipping inside a real `[attach]` line. What the resolver still does not do is cross a FUNCTION
boundary, and `_human_size` is exactly that: it renders the size inside the same `[attach]`
line from another function, and its units were `Б`/`КБ`/`МБ` at HEAD. So it gets its own runtime
assert below, and a new cross-function flow needs the same treatment.

MUTATION SWEEP. Selection is this file alone (`tests/unit/test_card_text_is_ascii.py`) so no
collateral test can stand in for the pin. Run in a CLONE of the worktree — never in the tree the
author is editing — with `__pycache__` deleted and `PYTHONDONTWRITEBYTECODE=1` before every
round; `vikunja_mcp.__file__` was printed before the first round and again after the last, both
resolving inside the clone, and the clone was never re-created in between. Every round is read
by COUNTING lines beginning `FAILED `, never by the first `N failed` a grep finds in pytest's
output — pytest prints a failing test's own docstring inside the traceback, and in this repo
those docstrings say `control 0 failed`. `-q` is dropped so the `collected` line is there to
cross-check. Six rounds, each stated beside its own control:

* opening control 0 failed, 0 errors, 3 collected.

* `[claim]` -> `[клейм]` at the claim call site: control 0 failed, 3 collected; mutation 2 failed
  (`test_every_comment_marker_is_ascii` and `test_no_non_ascii_in_the_text_workflow_comments`).

* `[needs-human]` -> `[needs-humаn]`, one Cyrillic `а` (U+0430) inside an otherwise identical
  marker — the invisible form this pin exists for, since the diff looks like no change at all:
  control 0 failed, 3 collected; mutation 2 failed, the same two.

* the `[worklog]` body prefix `Worklog:` reverted to its pre-#1164 spelling: control 0 failed,
  3 collected; mutation 1 failed, the add_comment scan alone. That is the pair showing the two
  static tests measure DIFFERENT things — a body is not a marker, and only one of them fires.

* `_human_size`'s `KB` reverted to its pre-#1164 spelling: control 0 failed, 3 collected;
  mutation 1 failed, `test_human_size_units_are_ascii` alone. The source scan next door stays
  GREEN through this one, which is the measurement behind the cross-function paragraph above.

* the `[epic-ready]` bracket deleted from the epic-assembled comment, i.e. a marker RETIRED
  rather than misspelled: control 0 failed, 3 collected; mutation 1 failed,
  `test_every_comment_marker_is_ascii` — and by its `_MARKERS` assert, not its derived one,
  which is exactly the hole that list is there to cover.

* the SECOND-HOP one: `meta`'s `", "` separator turned into an em dash, i.e. non-ASCII entering
  a real `[attach]` comment through a local the argument never names: control 0 failed,
  3 collected; mutation 1 failed, the add_comment scan. Against the ONE-HOP resolver the same
  mutation gave 0 failed under a 0-failed control, which is why the resolver is transitive.

* closing control 0 failed, 0 errors, 3 collected, source byte-identical to the baseline
  (`cmp`) after every restore.

TWO ROUNDS WERE DISCARDED, AND THEY ARE RECORDED BECAUSE OF HOW THEY LOOKED. Twice a patcher
matched nothing — once because a shell-quoted argument was mangled, once because the string
being mutated had by then become non-unique in the file — and both times the round that followed
came back entirely GREEN, indistinguishable from a blind pin on a mutation that was never
applied. What caught both was the patcher asserting it had replaced exactly one occurrence, not
anything in the round's own output. A sweep step that fails LOUDLY when it fails to mutate is
worth more than a cleverer mutation.
"""
import ast
import pathlib

from vikunja_mcp.workflow import _human_size

_WORKFLOW = pathlib.Path(__file__).resolve().parents[2] / "src/vikunja_mcp/workflow.py"

# The vocabulary, spelled out so an accidental DELETION of a marker is as red as a bad rename.
# This is the WHOLE set as of #1164, not a chosen subset: the derived scan in the first test
# finds exactly these ten distinct bracket tokens and nothing else. Verdict suffixes
# (`[review] APPROVE`) are not separate markers and live in test_formatting.py's escaping pin.
_MARKERS = (
    "[claim]", "[spec]", "[worklog]", "[review]", "[needs-human]",
    "[blocked]", "[decompose]", "[filed-by-agent]", "[attach]", "[epic-ready]",
)


def _module() -> ast.Module:
    return ast.parse(_WORKFLOW.read_text(encoding="utf-8"))


def _string_literals(node: ast.AST) -> list[str]:
    """Every string literal reachable inside one expression, without leaving it.

    Covers a bare constant, an f-string (`JoinedStr` holds its literal halves as constants),
    implicitly concatenated adjacent literals (the parser has already merged them), and a
    literal buried in a call argument such as `"\\n".join(...)`.
    """
    return [
        sub.value for sub in ast.walk(node)
        if isinstance(sub, ast.Constant) and isinstance(sub.value, str)
    ]


def _bindings_of(name: str, func: ast.AST) -> list[ast.AST]:
    """Every expression that can bind `name` inside `func`, by the three shapes in use here.

    `comment = f"..."` then `comment += "..."`; `marker` assigned in three branches of an
    if/elif/else and then appended to; `report = ["[worklog]"]` grown by `report.append(...)`
    and handed to `"\\n".join`.
    """
    bound: list[ast.AST] = []
    for node in ast.walk(func):
        if isinstance(node, ast.Assign):
            if any(isinstance(t, ast.Name) and t.id == name for t in node.targets):
                bound.append(node.value)
        elif isinstance(node, ast.AugAssign):
            if isinstance(node.target, ast.Name) and node.target.id == name:
                bound.append(node.value)
        elif isinstance(node, ast.Call):
            callee = node.func
            if (isinstance(callee, ast.Attribute) and callee.attr == "append"
                    and isinstance(callee.value, ast.Name) and callee.value.id == name):
                bound += list(node.args)
    return bound


def _literals_reaching(expr: ast.AST, func: ast.AST) -> list[str]:
    """Every literal that can reach `expr`, chasing local names TRANSITIVELY inside `func`.

    One hop is not enough and that was measured, not reasoned: `attach_file` builds
    `journal = f"[attach] {name} ({meta})"`, and `meta` is a SECOND-hop local carrying its own
    tool-authored `", "` separator. A one-hop resolver reads `journal`'s three literals, never
    looks at `meta`, and stays green while a non-ASCII separator ships inside the `[attach]`
    line — constructed and confirmed by this card's independent second pass before this
    function became transitive. The `seen` set is what keeps a self-referential `x += ...` (or
    a mutual pair) from looping.

    Over-collecting is the safe direction: an extra literal can only turn a pass into a red
    that names the exact string, never the reverse.
    """
    found: list[str] = []
    pending = [expr]
    seen: set[str] = set()
    while pending:
        node = pending.pop()
        found += _string_literals(node)
        for sub in ast.walk(node):
            if isinstance(sub, ast.Name) and sub.id not in seen:
                seen.add(sub.id)
                pending += _bindings_of(sub.id, func)
    return found


def _comment_text_literals() -> list[tuple[int, str]]:
    """(line, literal) for every string the tool itself contributes to an add_comment call."""
    tree = _module()
    functions = [
        n for n in ast.walk(tree)
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef))
    ]

    def enclosing(node: ast.AST) -> ast.AST:
        # innermost by definition: the LAST function whose span contains the node
        return max(
            (f for f in functions if f.lineno <= node.lineno <= (f.end_lineno or f.lineno)),
            key=lambda f: f.lineno,
        )

    out: list[tuple[int, str]] = []
    for call in ast.walk(tree):
        if not (isinstance(call, ast.Call)
                and isinstance(call.func, ast.Attribute)
                and call.func.attr == "add_comment"):
            continue
        text_arg = call.args[1]
        literals = _literals_reaching(text_arg, enclosing(call))
        out += [(call.lineno, lit) for lit in literals]
    return out


def test_every_comment_marker_is_ascii():
    """A marker is parsed, not read — so it may not acquire a non-ASCII character.

    The list is DERIVED from the source rather than compared against `_MARKERS`, so a brand-new
    marker is covered the moment it is written: every `[`-leading string literal in workflow.py
    is a marker today (measured — the extraction below finds the write sites AND the two
    `startswith` read sites, and nothing else), because nothing else in that file opens a string
    with a bracket. `_MARKERS` is then asserted on top, which catches the opposite failure: a
    marker silently DELETED leaves the derived set smaller and the derived check green.
    """
    emitted: set[str] = set()
    for node in ast.walk(_module()):
        head = None
        if isinstance(node, ast.Constant) and isinstance(node.value, str):
            head = node.value
        elif isinstance(node, ast.JoinedStr) and node.values:
            first = node.values[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str):
                head = first.value
        if head and head.startswith("[") and "]" in head:
            emitted.add(head[: head.index("]") + 1])

    for marker in sorted(emitted):
        assert marker.isascii(), (
            f"comment marker {marker!r} in workflow.py is not ASCII. Markers are a WIRE FORMAT, "
            f"not prose: workflow.py matches rendered comment text with startswith(), so a "
            f"marker that changes spelling — including gaining one Cyrillic letter that looks "
            f"Latin — silently re-routes the review offering. Translate the BODY of a comment "
            f"if you must; never the bracket"
        )

    missing = [m for m in _MARKERS if m not in emitted]
    assert not missing, (
        f"marker(s) {missing} are no longer written or matched anywhere in workflow.py. If one "
        f"was deliberately retired, drop it from _MARKERS in the same commit — the derived "
        f"ASCII check above cannot see a marker that has ceased to exist, so this list is what "
        f"keeps a deletion from reading as a pass"
    )


def test_no_non_ascii_in_the_text_workflow_comments():
    """The card text this tool AUTHORS is ASCII — asserted over the source, not by eye.

    Reads every `api.add_comment` call site in workflow.py and every literal that can reach it
    through local names inside the same function. Agent-supplied values (`spec`, `worklog`,
    `question`, a note) are interpolations, not literals, so they are correctly invisible here:
    this pin is about what the PRODUCT writes, not about what an agent may write through it.
    Its reach stops at the function boundary — see the module docstring, and the runtime assert
    below that covers the one flow crossing it.
    """
    literals = _comment_text_literals()
    assert len(literals) >= 35, (
        f"the resolver found only {len(literals)} literal(s) across the add_comment call sites, "
        f"which is far below the 51 it saw when this pin was written. A test that resolves "
        f"nothing passes vacuously — so this is a tripwire for the resolver having been broken "
        f"by a refactor (a call renamed, a text argument moved to a keyword), not a size limit"
    )
    for line, literal in literals:
        assert literal.isascii(), (
            f"workflow.py:{line} writes a non-ASCII literal into a card comment: {literal!r}. "
            f"The text the TOOL authors onto a card is ASCII — its README, rulebooks and every "
            f"marker are, and a consumer's board should not be the one surface that is not. An "
            f"agent's own spec/worklog/question/note is NOT covered by this and never was. Note "
            f"the units: this is ASCII, not 'no Cyrillic' — an em dash fails it too, and two did"
        )


def test_human_size_units_are_ascii():
    """The one CROSS-FUNCTION flow the static resolver above cannot follow, closed by running it.

    `_human_size` is called INSIDE the f-string that becomes the `[attach]` journal comment, so
    its units are card text while living in another function. They were Cyrillic (`Б`/`КБ`/`МБ`)
    at HEAD, and the source scan does not see them: swept, reverting them fails THIS test alone
    while the scan next door stays green (control 0 failed; that mutation 1 failed).
    """
    for size in (0, 512, 1023, 2048, 1_468_006, 25 * 1024 * 1024):
        rendered = _human_size(size)
        assert rendered.isascii(), (
            f"_human_size({size}) rendered {rendered!r}, which is not ASCII. It is interpolated "
            f"into the [attach] comment, so its units are card text — and they are invisible to "
            f"the add_comment source scan next door, which is why this runtime check exists"
        )
