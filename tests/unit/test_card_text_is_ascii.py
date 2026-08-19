"""THE TOOL'S OWN CARD TEXT IS ASCII IN THE DEFAULT LANGUAGE, AND THE MARKER VOCABULARY IS ASCII
IN ALL OF THEM.

THE TITLE WAS REWRITTEN IN #1165, and what it used to claim is worth stating because it was
TRUE: that every string this tool authors onto a card is ASCII, full stop. That held while
English was the only language a card could be written in. `language = "ru"` makes it false —
`cardtext.py` holds a Russian column on purpose — so the claim split in two. The BODIES are ASCII
in the DEFAULT language and deliberately not in `ru`. The MARKERS are ASCII in every language,
because two of them are matched with `startswith` and the other eight are frozen alongside those
two. This is the re-derivation the bullet below asked for by name before the key existed.

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
* THE BODY IS PROSE, AND SINCE #1165 IT LIVES IN `cardtext.py`, keyed by language. The `en`
  column is what `test_the_default_language_card_text_is_ascii` reads; the `ru` column is
  exempt by construction, and asserting the exemption is not vacuous — a `ru` column that came
  out ASCII would mean the translation never happened, which is what
  `test_card_language.py` measures from the other side by building both boards.

`WorkflowError` text is out of scope by the card's own rule, and the two tests read it
differently rather than not at all. The `add_comment` scan never reaches it — nothing raises
through that call. The MARKER scan does read it, because it keeps every `[`-leading literal in
the file wherever it sits: constructed, a `WorkflowError` whose message begins `[отказ]` turns
`test_every_comment_marker_is_ascii` red (control 0 failed; that mutation 1 failed). No
`WorkflowError` begins with a bracket today, so the rule and the gate do not collide — but they
would, and a future card localising error text should know it is this test that will say so.

WHAT THE SOURCE SCAN OVER `workflow.py` STILL MEASURES AFTER #1165, because the honest answer is
"less than it did". The bodies moved to `cardtext.py`, and NOT ONE of the 52 literals the
resolver now finds is prose. Enumerated rather than characterised, because an earlier draft of
this paragraph listed three kinds and the fourth is the largest: ELEVEN marker LITERALS (ten
distinct tokens — `[review]` contributes two, its APPROVE and NEEDS WORK forms); THIRTEEN layout
strings (`""`, `"\n"` twice, `" "` twice, `" ("`, `" - "`, `"#"` twice, `")"`, `", "` three
times); ELEVEN `card_text` keys; and SEVENTEEN non-prose strings the transitive chase drags in
from the same expressions — sixteen dict keys (`"title"` and `"id"` FIVE TIMES EACH, plus
`"priority"`, `"description"`, `"ref"`, `"subtask"`, `"related_tasks"`, `"username"`) and the
`"attachment-"` filename fallback, which is a prefix rather than a key. That last group read
SIXTEEN, with `"id"` at four, until #1168 re-derived it; the four counts above sum to the 52,
which is a check the old sentence offered no way to run, since it gave a count for one group only.
It was 51 before the move and is 52 after — the count held steady only because each key name
replaced roughly the phrase it now fetches, so the COUNT stopped being evidence that the prose is
covered. The table test below is what covers it.

WHAT THIS SCAN CATCHES THAT THE RUNTIME PIN DOES NOT — narrower than the "and nothing else does"
this paragraph used to claim about non-ASCII typed directly at a call site, and #1168 retired that
claim rather than repairing it. Re-derived here rather than taken from the card that reported it,
and the SELECTION is what makes the numbers legible, which is the part the report left out: on the
pre-#1168 tree, an em dash typed straight into the `[attach]` `add_comment` argument gave control 0
failed / 0 errors, 4 collected; mutation 1 failed with this file as the selection — the exclusive
claim is true THERE — and control 0 failed / 0 errors, 16 collected; mutation 3 failed once
`test_card_language.py` joins it. So the clause was a statement about a selection, dressed as one
about the repo. It was in fact false before either of those tests existed, falsified by an older
one this file never names — the `[attach]` journal assert that has lived in
`tests/unit/test_workflow_gates.py` since #184. Control 0 failed / 0 errors, 102 collected on that
file alone; the same em dash -> 1 failed.

WHAT IS GENUINELY EXCLUSIVE to the source scan is a literal on a branch the driver never takes,
and the `"attachment-"` fallback above is the worked example: `attach_file` reaches it only when a
filename sanitises away to nothing, which no driver input does. Measured on the fixed tree at this
file's own selection, control 0 failed / 0 errors, 5 collected; an em dash put into that fallback
-> 1 failed, the source scan alone. That is also the shape a future card line takes before anyone
thinks about the table, so the two halves are complements and neither is redundant.

WHAT THE STATIC HALF CANNOT SEE, stated as narrowly as it was measured, and WHICH BLINDNESS WAS
CHOSEN. The resolver chases names TRANSITIVELY through TWO scopes — the enclosing function and
MODULE level — following an f-string, implicitly concatenated literals, and a name bound by
assignment, `+=` or `list.append` into the names each of those mentions. Transitive rather than
one-hop is a correction, not a flourish: #1164's independent second pass constructed the
counter-example that broke the one-hop version. `attach_file` builds
`journal = f"[attach] {name} ({meta})"`, and `meta` is a SECOND-hop local carrying its own `", "`
separator, so an em dash put there was invisible to the pin while shipping inside a real
`[attach]` line.

MODULE SCOPE IS #1168'S HALF, and it took two goes, which is the part worth keeping. A shared
piece of card text hoisted to a module constant is an ordinary refactor, and against the
local-only resolver it gave `control 0 failed; mutation 0 failed` at this file's selection. The
FIRST fix read only `tree.body` and only a single-`Name` `Assign`, and this card's own second pass
measured what that still missed by putting the same em dash on the same undriven branch in four
different bindings: control 0 failed / 0 errors, 5 collected — a tuple target -> 0 failed, an
`AugAssign` -> 0 failed, a constant inside a module-level `try:` -> 0 failed, and a
`list.append` -> 0 failed, against a single-target `Assign` -> 1 failed. A docstring saying
"module scope is covered" would have been true of one shape in five. `_module_scope_statements`
and `_module_bindings` now read all of them, and the rounds are in the sweep below. The lesson is
the card's own, arriving twice: what a static resolver is complete about is SHAPES, and the
shapes are not enumerable by thinking of them.

What the resolver still does NOT do is cross a FUNCTION boundary, and #1168 chose to leave it
that way rather than chase callee returns. The reason is that the static form can only ever be
complete about SHAPES: resolving a same-module callee's returns would close the one shape that
was measured and leave a method on `self`, a value threaded through a dict, and anything a
comprehension builds still open — a growing pile of static analysis inside a test, each layer of
it a thing to get wrong quietly. The blindness is closed from the other side instead, by
`test_every_comment_the_tool_writes_is_ascii_when_it_runs`, which reads what LANDS and therefore
does not care which shape produced it. Its own blindness is named in its docstring and is a
different one — paths the driver never takes — which is why the two are kept as complements.
`_human_size` keeps its narrower runtime assert as well: it renders the size inside the `[attach]`
line from another function, its units were `Б`/`КБ`/`МБ` before #1164, and it exercises sizes the
driver does not. That assert is no longer the ONLY thing catching a Cyrillic unit — #1165 moved
the units into `cardtext._TABLE`, where the table pin sees them too — and its docstring carries
the re-measurement.

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

TWO OF THOSE SIX ROUNDS NO LONGER REPRODUCE AS WRITTEN, and they are left standing rather than
rewritten, because each was honest about the tree it was run on. #1165 moved the BODIES out of
`workflow.py` into `cardtext._TABLE`, and both affected rounds mutate a body: the `Worklog:`
prefix is no longer a literal at the `add_comment` site at all, so reverting it there is not a
possible mutation any more; and the `_human_size` unit round, re-run for #1165, now fails TWO
tests rather than one — the re-measurement is in `test_human_size_units_are_ascii`'s own
docstring. The four MARKER rounds are unaffected: those literals are still exactly where they
were, which is the point of keeping the bracket at the call site.

#1168's SWEEP, run the same way in a clone of its own worktree, selection this file alone,
`__pycache__` cleared and `PYTHONDONTWRITEBYTECODE=1` per round, `vikunja_mcp.__file__` printed
every round and resolving inside the clone, rounds read by COUNTING lines beginning `FAILED ` with
`ERROR ` counted separately, `-q` dropped so `collected` cross-checks the selection, and every
patcher asserting it replaced exactly one occurrence. `git status` was read after every restore.

* THE FIRST SHAPE THE CARD NAMES, a module-level constant reaching a comment: the `[attach]` note
  separator hoisted to a module-level `_NOTE_SEP` carrying an em dash. control 0 failed / 0
  errors, 5 collected; mutation 2 failed — the source scan, through its new module scope, and the
  runtime pin. On the pre-#1168 tree the same mutation gave control 0 failed / 0 errors, 4
  collected; mutation 0 failed.

* THE SECOND SHAPE, a sibling helper's return value: `decompose`'s `listing` built by a new
  module-level `_listing` whose join carries an em dash. control 0 failed / 0 errors, 5 collected;
  mutation 1 failed, the RUNTIME pin alone — the source scan stays green, and that is the
  measurement behind leaving the function boundary uncrossed statically. Pre-#1168: control 0
  failed / 0 errors, 4 collected; mutation 0 failed.

* THE COMPLEMENT, a literal on a branch the driver never takes: an em dash in `attach_file`'s
  `attachment-` filename fallback, which is reached only when a name sanitises away to nothing.
  control 0 failed / 0 errors, 5 collected; mutation 1 failed, the SOURCE SCAN alone. This is why
  both halves are kept rather than one replacing the other.

* NON-ASCII TYPED STRAIGHT INTO an `add_comment` argument: control 0 failed / 0 errors, 5
  collected; mutation 2 failed, static and runtime together. Recorded because the clause this file
  used to carry about that shape claimed the source scan was its only catch.

* THE NEGATIVE PIN FOR THE MODULE SCOPE, which the first round above cannot supply. Deleting the
  module hop from `_literals_reaching` with NO source mutation: control 0 failed / 0 errors, 5
  collected; mutation 0 failed — the widening is invisible there, because the runtime pin catches
  that same mutation from the other side. So the isolating pair is a module constant on a call
  site the driver never takes, i.e. `attach_file`'s fallback rebuilt out of a module-level
  constant carrying the em dash: control 0 failed / 0 errors, 5 collected; mutation 1 failed with
  the module hop in place, and the SAME mutation with the hop deleted 0 failed. That pair is what
  says the widening is load-bearing rather than decorative.

* THE FIVE BINDING SHAPES, run after this card's second pass found the first module-scope fix
  covered one of them. Every round puts the same em dash on the same undriven `attach_file`
  fallback and varies ONLY how the module-level name is bound, so any difference is the resolver's.
  control 0 failed / 0 errors, 5 collected: single-target `Assign` -> 1 failed; TUPLE target -> 1
  failed; `AugAssign` -> 1 failed; a constant inside a module-level `try:` -> 1 failed;
  `list.append` -> 1 failed. Before `_module_scope_statements` and the widened `_module_bindings`,
  the last four of those were 0 failed against the same control — the measurement that turned a
  prose fix into a code fix.

* THE SAME FIVE WITH THE MODULE HOP DELETED, which is what makes the five rounds above a pin on
  the resolver and not on something incidental: control 0 failed / 0 errors, 5 collected; all five
  -> 0 failed.

* opening control 0 failed / 0 errors, 5 collected; closing control 0 failed / 0 errors, 5
  collected, with `git status` clean after every restore.

THREE ROUNDS OF THAT SWEEP WERE DISCARDED AND RE-RUN, and they are recorded because of what caught
them. The restore step was `git checkout -- src tests` in a clone whose HEAD did not yet carry the
uncommitted fix, so the FIRST restore reverted the fix along with the mutation, and the three
rounds after it measured a tree with no fix in it while looking like ordinary rounds. Nothing in
their `FAILED` counts said so, and neither did the two checks usually relied on: `__file__`
resolved inside the clone every round — it is the right clone, with the wrong CONTENT — and the
reverted tree has a perfectly clean control OF ITS OWN. What said so was `collected`: 5 on the
opening control, 4 on every round after the first, because the fix adds a test. The remedy is to
commit the working tree INSIDE the clone before sweeping. Read the selection size every round; a
sweep whose subject is a NEW test is exactly where it answers differently from the other two.

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

from tests.unit.cardsites import drive_every_comment_site, wf_for
from vikunja_mcp import cardtext
from vikunja_mcp.config import DEFAULT_LANGUAGE
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


def _module_scope_statements(tree: ast.Module) -> list[ast.stmt]:
    """Every statement that RUNS at module level: through control flow, never into a def or class.

    The two halves are both load-bearing. Descending through `if`/`try`/`with`/`for` is needed
    because a module constant is perfectly ordinary inside one — a bare `tree.body` scan missed a
    constant wrapped in `try:`/`except`, measured, and stopping there would have shipped a
    docstring claiming module scope was covered when three of its shapes were not. NOT descending
    into a `def` or `class` is the other half: `ast.walk(tree)` would collect every function's
    locals under their bare names, so one function's `body = <text meant for an agent>` would
    resolve into an unrelated call site and redden the pin on a string no card ever sees.
    """
    out: list[ast.stmt] = []
    pending: list[ast.stmt] = list(tree.body)
    while pending:
        node = pending.pop()
        if isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
            continue
        out.append(node)
        pending += [child for child in ast.iter_child_nodes(node) if isinstance(child, ast.stmt)]
    return out


def _module_bindings(tree: ast.Module) -> dict[str, list[ast.AST]]:
    """Every expression bound to a name at MODULE level, by name — the same three shapes
    `_bindings_of` reads inside a function, so the two scopes answer the same question.

    Assignment (including a TUPLE target and an annotated one), `+=`, and `list.append`. The
    tuple case binds the WHOLE right-hand side to each name it unpacks rather than trying to
    pair them off positionally: over-collecting is the safe direction here, since an extra
    literal can only turn a pass into a red naming the exact string. All four shapes this does
    not read the naive way — a tuple target, an `AugAssign`, a constant inside module-level
    control flow, and `list.append` — were measured blind before this function grew to them.
    """
    bound: dict[str, list[ast.AST]] = {}

    def bind(name: str, value: ast.AST | None) -> None:
        if value is not None:
            bound.setdefault(name, []).append(value)

    for node in _module_scope_statements(tree):
        if isinstance(node, ast.Assign):
            for target in node.targets:
                for sub in ast.walk(target):
                    if isinstance(sub, ast.Name):
                        bind(sub.id, node.value)
        elif isinstance(node, (ast.AnnAssign, ast.AugAssign)):
            if isinstance(node.target, ast.Name):
                bind(node.target.id, node.value)
        elif isinstance(node, ast.Expr) and isinstance(node.value, ast.Call):
            callee = node.value.func
            if (isinstance(callee, ast.Attribute) and callee.attr == "append"
                    and isinstance(callee.value, ast.Name)):
                for arg in node.value.args:
                    bind(callee.value.id, arg)
    return bound


def _literals_reaching(expr: ast.AST, func: ast.AST, module: dict[str, list[ast.AST]]) -> list[str]:
    """Every literal that can reach `expr`, chasing names TRANSITIVELY through two scopes.

    One hop is not enough and that was measured, not reasoned: `attach_file` builds
    `journal = f"[attach] {name} ({meta})"`, and `meta` is a SECOND-hop local carrying its own
    tool-authored `", "` separator. A one-hop resolver reads `journal`'s three literals, never
    looks at `meta`, and stays green while a non-ASCII separator ships inside the `[attach]`
    line — constructed and confirmed by #1164's independent second pass before this function
    became transitive. The `seen` set is what keeps a self-referential `x += ...` (or a mutual
    pair) from looping.

    MODULE SCOPE IS CHASED TOO, and that half is #1168's. Hoisting a shared piece of card text to
    a module constant is an ordinary refactor, and against the local-only resolver it was blind:
    with `journal += f" - {note.strip()}"` rewritten as a module-level `_NOTE_SEP` carrying an em
    dash, this file's own selection gave `control 0 failed, 4 collected; mutation 0 failed`. Both
    scopes are consulted for every name — the local bindings are not preferred over the module
    ones and no shadowing is modelled — because over-collecting is the safe direction here: an
    extra literal can only turn a pass into a red that names the exact string, never the reverse.
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
                pending += _bindings_of(sub.id, func) + module.get(sub.id, [])
    return found


def _comment_text_literals() -> list[tuple[int, str]]:
    """(line, literal) for every string the tool itself contributes to an add_comment call."""
    tree = _module()
    module_bindings = _module_bindings(tree)
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
        literals = _literals_reaching(text_arg, enclosing(call), module_bindings)
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
    through names bound in the same function OR at module level — the module half is #1168's, and
    the module docstring carries what it was measured blind to first. Agent-supplied values
    (`spec`, `worklog`, `question`, a note) are interpolations, not literals, so they are correctly
    invisible here: this pin is about what the PRODUCT writes, not about what an agent may write
    through it. Its reach stops at the FUNCTION boundary — see the module docstring, and the
    runtime pin below that covers what crosses it.

    SINCE #1165 IT NO LONGER SEES THE PROSE, and the neighbouring test is what does. A body is
    now `card_text(self.language, "epic_ready", ...)`, so the literal reaching this scan is the
    KEY `"epic_ready"`, not the sentence. That is not a weakening to repair: keys are ASCII by
    construction and the sentences have a stricter home. What stays exclusively here is text
    typed straight into an `add_comment` argument, which is the shape a NEW card line takes
    before anyone thinks about the table.
    """
    literals = _comment_text_literals()
    assert len(literals) >= 35, (
        f"the resolver found only {len(literals)} literal(s) across the add_comment call sites, "
        f"which is far below the 52 it saw when this pin was last re-derived (#1168; 52 at #1165 "
        f"too, and 51 before the bodies moved to cardtext.py — adding the module scope pulled in "
        f"no literal that was not already reachable). A test that resolves nothing passes "
        f"vacuously — so this is a tripwire for the resolver having been broken by a refactor (a "
        f"call "
        f"renamed, a text argument moved to a keyword), not a size limit"
    )
    for line, literal in literals:
        assert literal.isascii(), (
            f"workflow.py:{line} writes a non-ASCII literal into a card comment: {literal!r}. "
            f"The text the TOOL authors onto a card is ASCII — its README, rulebooks and every "
            f"marker are, and a consumer's board should not be the one surface that is not. An "
            f"agent's own spec/worklog/question/note is NOT covered by this and never was. Note "
            f"the units: this is ASCII, not 'no Cyrillic' — an em dash fails it too, and two did"
        )


def test_every_comment_the_tool_writes_is_ascii_when_it_runs(tmp_path):
    """THE OTHER HALF OF THE GATE (#1168): drive every comment site and read what LANDS.

    The scan above reads `workflow.py` as source and so knows the SHAPES it can resolve. This one
    knows no shapes at all — it runs `drive_every_comment_site` on a FakeAPI board in the default
    language, with every agent-supplied value ASCII, and asserts the resulting comment stream is
    ASCII. A module constant, a sibling helper's return, a value assembled three functions away:
    all of them are just characters in a comment by the time they get here.

    WHAT IT COST TO NOT HAVE THIS, measured at this file's own selection (itself alone — its
    sweep rule names that selection so no collateral test can stand in for the pin), `__pycache__`
    cleared and `PYTHONDONTWRITEBYTECODE=1` per round, rounds read by counting lines beginning
    `FAILED `. Both mutations are ordinary refactors of shipped code, not constructions. On the
    pre-#1168 tree: control 0 failed / 0 errors, 4 collected; the `[attach]` note separator hoisted
    to a module-level `_NOTE_SEP` carrying an em dash -> 0 failed; `decompose`'s `listing` built by
    a new module-level `_listing` helper whose join carries an em dash -> 0 failed. Blind twice.
    With this pin in place, the same two mutations re-applied: control 0 failed / 0 errors, 5
    collected; the module constant -> 2 failed (the widened source scan sees it too) and the
    sibling helper -> 1 failed, this test being the only one of the five that fires — which is the
    round saying the function boundary is closed HERE and by nothing else in this file. Widen the
    selection and the flip pin next door fires as well; see the last paragraph.

    ITS OWN BLINDNESS, since replacing one blind spot with an unnamed one is not progress: it sees
    exactly the paths the driver takes, and the driver is a HAND-WRITTEN list of transitions.
    `test_card_language.py::test_the_driver_reaches_every_marker` keeps that list honest — but read
    what it asserts, because the obvious stronger reading is wrong and this card's second pass
    caught it here: it checks `_MARKERS` is a SUBSET of what the driver writes, so it fires only
    once a marker has been added to `_MARKERS` BY HAND. Nothing makes that happen. Measured at the
    two-file selection, control 0 failed / 0 errors, 18 collected: a brand-new `[note]` site whose
    body comes from a module-level helper, with `_MARKERS` untouched -> 0 failed; the same round
    with `"[note]"` added to `_MARKERS` -> 1 failed.

    SO WHAT A NEW SITE DOES AND DOES NOT GET FOR FREE. Its MARKER is covered automatically:
    `test_every_comment_marker_is_ascii` DERIVES the bracket set from the source, so — control 0
    failed / 0 errors, 18 collected — that same new site with a Cyrillic look-alike in its bracket
    gave 2 failed, with `_MARKERS` still untouched. Its BODY is
    covered only if the source scan can resolve it or the driver happens to reach it. THE
    UNCOVERED RESIDUE is therefore a site whose body neither resolves statically nor gets driven —
    and since #1168 the static half resolves module scope as well as locals, what is left of it is
    a body assembled across a FUNCTION boundary on a path the driver never takes. Nothing here
    sees that. Whether some third check could is not something this card measured, so it is not
    claimed either way.

    THE NEIGHBOURING FILE ALREADY ASSERTS SOMETHING SIMILAR, and the duplication is deliberate
    rather than unnoticed. `test_flipping_the_language_moves_the_body_and_never_the_marker` ends on
    a pair of asserts, the first of which is `assert all(c.isascii() for c in en)` (the `ru` one
    closes it), and both mutations above do redden that test — that is how
    #1168 found the coverage existed at all. But it lives under a test whose subject is the
    language flip, so it would go with any narrowing of that test, and it is not reachable from
    this file's selection. The gate keeps its own pin.
    """
    api, wf = wf_for(DEFAULT_LANGUAGE)
    written = drive_every_comment_site(api, wf, tmp_path)
    assert len(written) >= 15, (
        f"the driver produced only {len(written)} comment(s), against the 16 it writes today. It "
        f"is meant to run EVERY comment-writing path in workflow.py once, so a stream this short "
        f"means it stopped early or the board no longer reaches those transitions — and a pin "
        f"over an empty stream passes vacuously. This is a floor, not the count: it is here to "
        f"catch a driver that broke, and `test_the_driver_reaches_every_marker` is what checks "
        f"the driver is COMPLETE"
    )
    for comment in written:
        assert comment.isascii(), (
            f"a comment this tool wrote is not ASCII in the default language: {comment!r}. Unlike "
            f"the source scan above this one does not care HOW the text got there — a module "
            f"constant, a helper in another function, a value built anywhere — only that it "
            f"landed on the card. Every value the driver supplies is ASCII by construction, so "
            f"the non-ASCII half is the PRODUCT's. Note the unit: ASCII, not 'no Cyrillic'"
        )


def test_human_size_units_are_ascii():
    """The one CROSS-FUNCTION flow the static resolver above cannot follow, closed by running it.

    `_human_size` is called INSIDE the f-string that becomes the `[attach]` journal comment, so
    its units are card text while living in another function, and the source scan does not see
    them. They were Cyrillic (`Б`/`КБ`/`МБ`) before #1164 translated them.

    WHAT THIS TEST IS NO LONGER ALONE IN CATCHING, re-measured for #1165 rather than left
    standing. When the units were literals in `_human_size`, reverting one failed this test and
    only this test — that was the measurement behind the cross-function paragraph in the module
    docstring. Since #1165 they come from `cardtext._TABLE`, so the same revert now fails TWO:
    selection `tests/unit/test_card_text_is_ascii.py`, control 0 failed / 0 errors / 4 collected,
    the `en` KB unit reverted to its pre-#1164 Cyrillic spelling -> 2 failed
    (`test_human_size_units_are_ascii` and `test_the_default_language_card_text_is_ascii`),
    closing control 0 failed / 0 errors / 4 collected, source byte-identical after the restore.

    It is kept rather than folded into the table pin because the two ask different questions: the
    table pin reads a TEMPLATE, this one reads what `attach_file` would actually render, across
    the function boundary and through the branch that picks the unit. A refactor that stopped
    `_human_size` consulting the table at all would leave the table pin green.
    """
    for size in (0, 512, 1023, 2048, 1_468_006, 25 * 1024 * 1024):
        rendered = _human_size(size)
        assert rendered.isascii(), (
            f"_human_size({size}) rendered {rendered!r}, which is not ASCII. It is interpolated "
            f"into the [attach] comment, so its units are card text — and they are invisible to "
            f"the add_comment source scan next door, which is why this runtime check exists"
        )


def test_the_default_language_card_text_is_ascii():
    """The prose half of the old whole-file claim, moved to where the prose now lives (#1165).

    Reads the RAW templates rather than rendered strings: a rendered one mixes in whatever field
    values a test chose, so a pin over it would partly be asserting a property of its own
    fixtures. The `ru` column is asserted to be the OPPOSITE — not for symmetry, but because a
    `ru` column that came out ASCII would mean nothing was translated, and that failure is
    invisible to every other assert in this file.
    """
    for key, row in cardtext._TABLE.items():
        assert row["en"].isascii(), (
            f"cardtext._TABLE[{key!r}]['en'] is not ASCII: {row['en']!r}. The DEFAULT language is "
            f"the one this repo's README, rulebooks and markers are written in, and a consumer "
            f"who set no `language` key should not find their board is the one surface that is "
            f"not. Note the unit: ASCII, not 'no Cyrillic' — an em dash fails it too, and two did"
        )

    assert any(not row["ru"].isascii() for row in cardtext._TABLE.values()), (
        "not one `ru` row is non-ASCII, so either the Russian column was never filled in or it "
        "was overwritten with the English one. The language key would then be inert while every "
        "other assert in this file stayed green"
    )
