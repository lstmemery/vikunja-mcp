"""The control-round rule for mutation sweeps, made executable — tracker #656.

WHAT IS BROKEN WITHOUT THIS FILE. Mutation sweeps in this repo are hand-run: an agent edits a
source line, runs `pytest`, reads the summary line, restores, and writes the result into a
docstring. The summary line is where the arithmetic goes wrong, because `N failed` is a KILL
COUNT only if that same selection failed ZERO times before any mutation was applied — and a `-q`
summary never says whether it did. VMCP-119 (594) swept in a tree where 30 tests failed
constantly for a reason unrelated to any mutation: every row of a six-row table came out inflated
by exactly 30 and its headline conclusion was wrong by a factor of 16 (the true kill count was
2). Constant failures survive a before/after comparison INTACT, so they read as signal. An
unmutated control round costs one `pytest` invocation and turns that from undetectable into
obvious. VMCP-133 (622) removed the particular 30-failure source; this file is the other half its
description asked for — the METHODOLOGY, which no single test file's fix can close.

WHAT THIS FILE ENFORCES. One shape, stated in CLAUDE.md's Testing Philosophy and pinned below: a
sweep record that quotes a round as a NUMBER of failures must state the control round's number of
failures too. Precision has to be symmetric. `-> FAIL` is answered by `control PASS`; `-> 7
failed` is answered by `control 0 failed` and by nothing weaker, because "control PASS" in a tree
where 30 tests fail constantly is a sentence that is TRUE and USELESS at the same time — that is
exactly the sentence 594 could have written. The count that goes in the record is the FAILED
count, never the pass total: a pass total moves with every test the repo adds (CLAUDE.md keeps
its unit count as a floor for the same reason, and test_api_kanban has watched one total go stale
three times), while `control 0 failed` stays true as the suite grows.

WHAT IT CANNOT ENFORCE, said plainly because this repo prices its guards rather than rounding
them up.
  * It reads PROSE. It sees the SHAPE of a claim, never whether a control round was actually run
    — a record that says `control 0 failed` without running one satisfies it. Nothing in a repo
    of hand-run sweeps can check that; what a scanner CAN do is make the omission impossible to
    ship silently, which is the difference between this and a rule kept in prose alone.
  * One control vouches for its whole PARAGRAPH, not for each number in it. That is the residual
    after VMCP-167 (688) moved the unit down from the whole RECORD, and the two are not the same
    limit: at record granularity one sentence immunised every count in a docstring, which was not
    a constructed worry but the live reason a human filed VMCP-161 (668) — that docstring's
    uncontrolled whole-file count passed on the strength of a control clause 27 lines below it
    about a DIFFERENT mutation, and deleting that one unrelated clause was what turned it red.
    Re-measured here 2026-08-02, `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1, on that
    exact pre-image rather than on a construction: control 0 failed, the pre-image installed under
    the OLD record unit 0 failed — blind — and under the paragraph unit 1 failed, naming the
    paragraph. What still passes is one PARAGRAPH reading "Round A: control 0 failed; drop guard A
    -> 2 failed. Round B, never baselined: drop guard B -> 9 failed. Round C, likewise never
    baselined: drop guard C -> 41 failed." Per-number pairing is not recoverable from prose —
    nothing in the text says which control a number belongs to — so the rule is enforced per
    PARAGRAPH and asked of the author per ROUND. Put a blank line before Round B and the last two
    are flagged.
  * The paragraph unit is AUTHOR-CONTROLLED, which is the honest way to say that a record whose
    author left no separator in it is still one chunk (a blank line, or in a comment run a bare
    `#`). This very block is the example: `WHAT IT CANNOT
    ENFORCE` runs its bullets with no blank line between them, so ALL of them are one paragraph
    and any control in any of them vouches for the rest. That is PINNED rather than dated, in the
    paragraph test below, and the reason is a mistake worth recording: it was first written with
    the measured pair "four bullets, 47 lines", and each of the next three edits to this very
    block falsified it again — 51, then 54, then 56. A number describing the text it is written
    in cannot be kept true by care, which is the same self-reference the scope comment below
    flags for `git log -S`. So the property is asserted and the count is gone. The unit is
    deliberately not the finest split available: splitting at BULLETS instead gives 49 offending
    chunks on this tree, 36 of them inside records that DO state a control, because it severs the
    canonical shape this file asks for — a control header with its rounds listed under it — from
    its own baseline. That pair is dated 2026-08-02 and counts this file's own bulleted blocks, so
    it moves when this file does; the decision it supports is pinned in the paragraph test rather
    than resting on it. Writing a blank line between two sweeps is what buys the finer check, and
    nothing here can make an author do it.
  * A clean control does not mean the round measured anything, and two other forms bound it.
    STALE BYTECODE (VMCP-135 (624)): re-measured here on CPython 3.12 with the `.pyc` header read
    directly, cache validity is the pair (source mtime in SECONDS, source size) — so a same-length
    rewrite replays the previous budget only when the mtime ALSO fails to advance a whole second,
    which is a scripted sweep's hazard rather than a hand edit's. The remedy needs the same
    correction: `PYTHONDONTWRITEBYTECODE=1` stops Python WRITING bytecode, not READING it — with a
    stale `.pyc` already on disk the round replayed the old value under that variable, and only
    deleting `__pycache__` moved it. THE MUTATION THAT NEVER RAN (VMCP-148 (646), whose WORKLOG
    records it — that card's own subject is a different defect, so the tree holds no trace):
    a tree copied with `cp -R` drags `.venv`, the ORIGINAL `src` lands earlier on `sys.path`, the
    mutation never reaches the interpreter, and control AND rounds come out green together — four
    false greens in a row. `vikunja_mcp.__file__` printed per round is what catches that; the
    control round is not, and this file does not claim otherwise.
  * It is a RATCHET, not a retrofit. `LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT` names the PARAGRAPHS
    that already quote a count without one. They are deliberately NOT "fixed": the control that
    belongs beside a historical number is the one measured in THAT environment at THAT sha, and
    it is unrecoverable. Writing today's `0 failed` next to a number measured last week would be
    a fabricated measurement — precisely the defect cards 646/655/663/674 exist to remove. So the
    list records which numbers this SCANNER cannot see a baseline for — narrower than
    "uninterpretable", since five of its entries, in three records, DO state one in a form the
    pattern cannot read (the list's own comment names all three). It may only SHRINK, and there
    are TWO routes, not one: re-MEASURING a sweep and its control, which is the only route for a
    historical number whose environment is gone; or, where the record already states a control the
    pattern cannot reach, moving that sentence into the paragraph that needs it — no new
    measurement, nothing fabricated. Saying only the first would misdescribe a fifth of the list.
    It GREW once, when 688 changed the unit under it — a rekey rather than a loosening, and the
    same prose is covered either way.
"""
import ast
import itertools
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
TESTS_DIR = REPO_ROOT / "tests"

# SCOPE: sweep records live in the test suite's prose, and confining the scan there is MEASURED
# rather than assumed — but measured PER SCOPE, because the two scopes answer differently and a
# single "only hit outside tests/" would be false. Ran the trigger over both on 2026-08-02.
# src/: exactly ONE hit in the whole package, the sentence in server.py describing the claimable
# check's EXIT CODES — not a pytest tally at all, and the row for it below shows the trigger
# cannot tell the two apart. A scanner whose sole finding in the package is a false positive has
# no business reading there. Repo markdown: five hits, every one of them inside the CLAUDE.md
# paragraphs THIS commit added, each already quoting its own control count; before it, none. That
# second number is self-referential by construction — the same trap `git log -S` has, where a
# phrase written into a file changes the answer the file gives about it — so it is dated, and a
# later reader should re-run it rather than trust it.

# A ROUND COUNT: a number of failing tests. Two forms are excluded, both because they were
# measured in this repo and both false positives. `(?<![:.\w])` drops a number that is part of an
# address or a longer token — `Bind for 0.0.0.0:3456 failed` in test_skill_contract quotes a
# docker error, not a round. `(?<!of )` drops a fraction-of-total — tests/integration/conftest's
# "containers: 5 of 9 failed" counts containers.
_ROUND_COUNT = re.compile(r"(?<![:.\w])(?<!of )\d+\s+failed\b", re.IGNORECASE)

# A CONTROL COUNT: the word `control` and a TALLY (`N failed` / `N passed`) close enough together
# to be one statement, in either order — "control 0 failed", "control 2 passed", "2 failed
# against an unmutated control round of 0". The tally is the load-bearing half. What it is chosen
# against is THIS pattern with the tally requirement replaced by a bare digit —
#     r"control\b[^.;]{0,60}?\d|\d[^.;]{0,60}?\bcontrol\b"
# — which prose using the word in a non-sweep sense can satisfy. Spell the weak form exactly that
# way before re-running any number below: the ratchet round further down measures four OTHER
# readings of "a digit near `control`", and every one of them answers differently. Measurements
# below are from 2026-08-02, `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1.
#
# On the two "other sense" rows of the pattern test further down, that weak form accepts BOTH and
# this one refuses BOTH — that is what requiring a tally buys. Those two rows are ADAPTATIONS of
# test_api_kanban.py prose, NOT strings this repo contains; the docstring above them owns that
# measurement and quotes the real wording (one of the two is wrapped across a line break at its
# site, so it is found only with whitespace flattened — which is how both predicates here read
# prose anyway). This comment said the OPPOSITE until the round that fixed it: 889befd corrected
# "the exact strings this repo contains" THERE and asserted it HERE in the same breath, and "the
# control at the same call site" occurs 0 times outside this file (every file in the checkout bar
# `.git`, counted raw and flattened). No test caught that — a reviewer did — and the ratchet below
# would not have: it reads this comment run, but only for the SHAPE it refuses. Over `tests/unit`:
# control 0 failed; a false repo-content quotation planted in this comment -> 0 failed; another
# planted in a docstring under tests/ -> 0 failed. VMCP-171 (695) carries that class.
#
# That weak form also vouches for 22 records under tests/ which this one refuses, and exactly ONE
# of them quotes a round count — test_api_kanban's honest-server-paginates, a LEGACY entry, which
# would therefore leave the list with nobody re-measuring it. One is the number that matters
# here: the ratchet's business is the list, not the tally of near-misses.
#
# `[^.;]` NARROWS the window; it does not bound it to a clause, and saying so would oversell it.
# Constructed and measured, all five accepted by the pattern below: "control PASS! ... says 0
# failed", the same with "?", with ",", with " - ", and with a LINE BREAK — because the scanner
# flattens whitespace before matching, so a bullet list is one string to it. What the exclusion
# really buys is 60 characters and a stop at `.`/`;`; a control and an unrelated number that sit
# closer than that, in the same record, still vouch for each other.
_CONTROL_COUNT = re.compile(
    r"control\b[^.;]{0,60}?\b\d+\s+(?:failed|passed)\b"
    r"|\b\d+\s+(?:failed|passed)\b[^.;]{0,60}?\bcontrol\b",
    re.IGNORECASE,
)

# How much of a record's opening text goes into its key. ONE constant for both halves of the key
# — the comment run's first line and the paragraph's first characters — because it is one
# decision: long enough that two chunks of the same record do not collide, short enough to read in
# an assertion message. Collisions are not silent either way; `_records` suffixes them loudly.
_KEY_HEAD = 48

# PARAGRAPHS whose prose has the SHAPE this scanner refuses — a round quoted as a number, with no
# control count in the SAME PARAGRAPH — as they stood when VMCP-167 (688) made the paragraph the
# unit. A SHAPE list is all it is, and the difference matters: FIVE of these entries, belonging to
# THREE records, do state a baseline in a form this pattern cannot read (test_the_degraded_stop_rule
# quotes a set-wise
# negative AND positive control; test_the_checkout_probe runs a fully controlled qualitative sweep
# and lands here only for an unrelated tally describing a CONSTRUCTED broken tree, not a mutation
# round; and the three QUANTIFIER rows below state one control for the whole section, in that
# section's SECOND paragraph — its first is the bare `--- … ---` header, which carries neither a
# count nor a control — reading "Every round quoted in this section is a FAILURE count against that
# same control of 0 failed", which is true, and which no per-count rule can carry across a
# paragraph boundary). Read it as "cannot confirm a baseline here", never as "these numbers are
# uninterpretable". See the module docstring: it may only SHRINK — and those three could leave
# TODAY, without any re-measurement, by repeating that sentence's control inside each of their own
# paragraphs. Deliberately not done here: it edits the measured prose of another card (VMCP-158 /
# 664) to satisfy a rule this card introduced, and the entry is honest as long as this comment
# names the case. Whoever wants the list shorter has that route and it fabricates nothing.
#
# It went from 7 record keys to 16 paragraph keys when the unit changed, and that recount IS 688's
# result rather than its cost. The split, COUNTED rather than reasoned — the first draft of this
# comment did the arithmetic in its head and got 8-and-1: the seven records already listed hold 13
# of the 16 paragraphs, so SIX of the nine additions sit inside them, where one grandfathered key
# was vouching for every paragraph under it, which is this card's own bug one level down. The
# other THREE are a single record that had been green outright.
LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT = frozenset({
    "tests/unit/test_api_kanban.py::_serving_lengths"
    "::¶sweep 2's own page draw widened to -> 1 failed /",
    "tests/unit/test_api_kanban.py::test_the_degraded_stop_rule_does_not_depend_on_bucket_order"
    "::¶WHAT IT PINS NOW — MEASURED, not argued (VMCP-12",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶THE COMPLETENESS LINE IS VMCP-130 (616)'s, AND W",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶keep_going = False -> 1 failed. `assert healthy ",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates"
    "::¶`_offset_pages` over-serving by +3 on EVERY page",
    "tests/unit/test_repo_browser_isolation.py"
    "::test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git"
    "::¶The last two assertions need a broken TREE rathe",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶WHY THIS STRING AND NOT THE OTHER TEN. #586 meas",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶AND WHY THE PIN SPANS TWO ENVS (VMCP-146 / #635)",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail "
    "::¶WHAT THE SECOND ROW DOES NOT BUY, so it is not o",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_spelled_ref"
    ":--- the prose's INTERPOLATED VALUES, not just it"
    "::¶#586 pinned next_task's prose against CLAUSE gro",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_spelled_ref"
    ":--- the prose's INTERPOLATED VALUES, not just it"
    "::¶WHAT THE ENVS HOLD APART. The cycle env gives it",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_spelled_ref"
    ":--- the prose's INTERPOLATED VALUES, not just it"
    "::¶DELIBERATELY NOT PINNED, measured rather than as",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶WITH THE ENV, the same `all` mutant is **2 faile",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶WHAT THE ENV BUYS BEYOND THE QUANTIFIER, as a bo",
    "tests/unit/test_workflow_sequence_gate.py::comments-above"
    ":test_a_tail_needs_retriage_when_ANY_of_its_blockers_sits_in_backlog_not_when_all_do"
    ":--- the QUANTIFIER over a tail's blockers (VMCP-"
    "::¶AND WHAT THESE ROWS ARE NOT EXCLUSIVE ABOUT, sta",
    "tests/unit/test_workflow_wip.py::comments-above:_clause_free_base"
    ":--- the free == 0 note is the base plus the ENUM"
    "::¶The base is READ from the other env rather than ",
})


def _docstrings(path: Path, source: str):
    """Every docstring in a module, keyed by its dotted qualname.

    A bare `node.name` would collide between a nested helper and its host, and a collision here is
    worse than a miss: two records would share one key and the ratchet could not name either.
    """
    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    yield qualname, doc
                yield from walk(child, f"{qualname}.")

    module_doc = ast.get_docstring(ast.parse(source), clean=False)
    if module_doc:
        yield "<module>", module_doc
    yield from walk(ast.parse(source), "")


def _comment_runs(source: str):
    """Every maximal run of consecutive `#` lines, keyed by the `def` it stands above.

    Sweep records are not only docstrings: three of the biggest in this repo are banner comments
    above a group of tests (test_workflow_sequence_gate, test_workflow_wip). Keying by LINE NUMBER
    would make the ratchet break on any edit above it; keying by the following definition survives
    the file moving under it, which is the whole point of a list that must stay accurate.

    The run's own opening line is the second half of the key, and it is not decoration — it closes
    a HOLE the first half had alone, found by construction rather than by reading. Several runs
    stand above the SAME definition, and keying on the definition alone collapsed them: over tests/
    on 2026-08-02, 961 records shared 790 keys, 72 of them colliding, and all three banner entries
    in the legacy list sat on colliding keys (`comments-above:_spelled_ref` covered SIX runs).
    Because the offender set is a set of KEYS, a grandfathered key vouched for every run beneath
    it. Measured from a control round of 0 failed, 1 passed, on the ratchet test below: the
    IDENTICAL new uncontrolled banner `# MUTATION-CHECKED: drop the guard -> 2 failed` gave
    1 passed inserted above `_spelled_ref`, and 1 failed inserted above a definition with no legacy
    entry. That is the regression this file exists to stop, shipping green in exactly the places
    most likely to grow another banner. `_docstrings` above calls a collision worse than a miss;
    this is what that looks like when it happens. With the opening line in the key both
    constructions give 1 failed, and reverting the key to the definition alone turns the ratchet
    red — so the second half is load-bearing, not belt-and-braces.

    EVERY NUMBER ABOVE IS UNDER THE RECORD UNIT, which VMCP-167 (688) replaced with the paragraph,
    so the conclusion survives and its REASON does not. Re-measured against a control of 0 failed,
    4 passed over the whole scanner file: that same banner above `_spelled_ref` is 1 failed WITH
    the opening line in the key and 1 failed WITHOUT it, and is NAMED both times — the paragraph
    half of the key now does the disambiguating that the opening line used to do alone, so the
    "1 passed" above is history rather than current behaviour. Dropping the opening line is still
    1 failed, but because ten legacy entries change key at once, not because a regression is
    swallowed. What the opening line alone still separates is narrower than the round above
    measured: two runs over ONE definition whose corresponding PARAGRAPHS agree. Kept for that,
    and because re-keying the list a second time in one commit buys nothing.
    """
    lines = source.splitlines()
    defs = [
        (n.lineno, n.name)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    ]
    defs.sort()

    def following(line_no: int) -> str:
        for lineno, name in defs:
            if lineno > line_no:
                return name
        return "<end of file>"

    def key(run: list[str], start: int) -> str:
        opening = " ".join(run[0].lstrip().lstrip("#").strip().split())[:_KEY_HEAD] or "<blank>"
        return f"comments-above:{following(start)}:{opening}"

    run: list[str] = []
    start = 0
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            if not run:
                start = i
            run.append(line)
            continue
        if run:
            yield key(run, start), "\n".join(run)
            run = []
    if run:
        yield key(run, start), "\n".join(run)


def _paragraphs(prose: str):
    """Every paragraph of a record: a maximal run of lines that are not blank.

    THE UNIT OF THE WHOLE SCANNER, and VMCP-167 (688) is the card that moved it here from the
    whole record. A predicate that runs over an entire docstring answers "is there a control
    SOMEWHERE in this", so one qualifying sentence immunises every other count in it — and the
    proof was not constructed, it was the live record that made a human file 668: its whole-file
    kill count (quoted there as a failure tally over 102 passing tests, and deliberately not
    reproduced here, because it is the number with no baseline) had none in its own paragraph, and
    the docstring passed anyway on the strength of a control clause 27 lines below about a
    DIFFERENT mutation. The failure mode concentrated exactly where the payoff was meant to be,
    since the long multi-mutation records are both the ones most likely to hold one qualifying
    sentence and the ones most worth reading.

    A BARE `#` LINE IS BLANK, and that conjunct is what makes this work on comment runs, which are
    half of this repo's biggest sweep records (test_workflow_sequence_gate, test_workflow_wip). A
    run has no blank lines at all — `#` alone is its paragraph separator, and this file's own
    comment above `_CONTROL_COUNT` is written that way, in four paragraphs. Without the conjunct
    every banner keeps the broken granularity: measured on this tree, splitting docstrings only
    gives 9 offending chunks in 7 records, and counting `#` too gives 16 in 8, the extra record
    being one that was green outright.

    THE CONJUNCT IS NOT SCOPED TO COMMENT RUNS, though it is only USEFUL there, and the difference
    is a false-positive channel rather than a tidy detail. This function does not know which
    extractor produced its argument, so a DOCSTRING whose line is nothing but `#` splits there
    too. Measured both halves: zero docstrings under tests/ contain such a line today, so nothing
    is currently affected; and a constructed one does split, its controlled half reading True and
    its severed tail False. Scoping the rule would need the record kind threaded in — deliberately
    not done for a channel with no occurrences, but it is a channel, and "a record written without
    blank lines is one chunk" is false in exactly this corner.

    NOT BULLETS, and that is a measurement rather than a preference. Splitting at list markers as
    well gives 49 offending chunks on this tree, 36 of them inside records that DO state a control,
    because the canonical shape this file asks authors for is a control header with its rounds
    listed under it — `Control round: 0 failed, 1 passed.` then `* drop the guard -> 1 failed` —
    and a bullet split severs every one of those from its baseline. That pair is DATED 2026-08-02
    and is self-referential in the way the scope comment below warns about: this file's own
    bulleted MUTATION-CHECKED blocks are counted by it, so writing the sentence moved it (it read
    20/11 before this card, 40/27 mid-card, 49/36 as shipped, all on the same reasoning). Re-run
    it rather than trust it; what does NOT rot is the decision, which is pinned in the paragraph
    test below by the canonical-shape assert, and which the direction of the number makes
    regardless of the marker definition — four readings of "list marker" give 49 offenders, and
    a fifth that needs no following space gives 53.

    THE PARAGRAPH KEEPS THAT BLOCK WHOLE, which is why it is the unit — but the honest version of
    that sentence is narrower than "the coarsest split that separates two sweeps", which is what
    it said first and which this file's own module docstring refutes: `WHAT IT CANNOT ENFORCE` is
    ONE paragraph holding several distinct sweeps. A blank line is what separates two sweeps, and
    only an author puts one there. This unit is the coarsest split that separates them WHEN THE
    AUTHOR HAS, and the finest that does not sever a control header from its own rounds.

    THE COST, priced rather than waved through: a control now has to sit in the SAME paragraph as
    the count it vouches for, so a section-wide control header followed by a blank line no longer
    reaches the rounds below it. That shape exists in this repo — one record, three paragraphs,
    listed in the ratchet with its wording quoted — and CLAUDE.md now asks for the control beside
    the count for exactly this reason.
    """
    chunk: list[str] = []
    for line in prose.splitlines():
        stripped = line.strip()
        if stripped.startswith("#"):
            stripped = stripped.lstrip("#").strip()
        if stripped:
            chunk.append(line)
            continue
        if chunk:
            yield "\n".join(chunk)
            chunk = []
    if chunk:
        yield "\n".join(chunk)


def _paragraph_head(paragraph: str) -> str:
    """The paragraph's opening text, for its key — flattened, `#` markers dropped, truncated.

    A POSITION would have been the obvious key and is the wrong one for the same reason
    `_comment_runs` refuses a line number: inserting one paragraph would renumber every paragraph
    below it, so the ratchet would break on edits that changed nothing it is about. The opening
    text survives the record moving under it and, unlike an index, says in the failure message
    WHICH paragraph has no baseline.
    """
    flat = " ".join(line.strip().lstrip("#").strip() for line in paragraph.splitlines())
    return " ".join(flat.split())[:_KEY_HEAD] or "<blank>"


def _records():
    """(key, prose) for every PARAGRAPH of every docstring and comment run under tests/.

    Distinctness is load-bearing rather than tidy: the offender set is a set of keys and the legacy
    list suppresses BY key, so two records under one key mean a grandfathered entry vouching for
    the other — measured, and written up in `_comment_runs`. Each extractor avoids its own
    collisions (dotted qualnames there, the opening line here) and `_paragraph_head` separates the
    paragraphs within one record; this counter is the backstop for what none of them rules out, a
    redefined function name, or two chunks under one record whose first 48 characters agree. It
    counts the FINAL key deliberately, so a collision BETWEEN PARAGRAPHS of one record is named
    rather than merged. What that is worth was measured, and it is narrower than either of this
    sentence's two earlier drafts — one said the alternative left the split "unguarded", the other
    said it goes red; both were written from reasoning. On today's tree the placement is
    INVISIBLE: from a control round of 0 failed, 4 passed, moving the counter up to the record key
    is 0 failed, because no record here has two paragraphs whose first 48 characters agree.
    CONSTRUCT one — a docstring carrying the same uncontrolled paragraph twice — and the
    difference shows up without changing the verdict: both placements are 1 failed, but the
    final-key counter names TWO offenders, the second wearing the loud `#2`, while the record-key
    counter names ONE. That is what it buys, and it is not nothing: the ratchet suppresses BY KEY,
    so a merged pair is a grandfathered entry vouching for a paragraph nobody listed — this
    card's own bug one level further down.
    """
    for path in sorted(TESTS_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        seen: dict[str, int] = {}
        for key, text in itertools.chain(_docstrings(path, source), _comment_runs(source)):
            for paragraph in _paragraphs(text):
                full = f"{relative}::{key}::¶{_paragraph_head(paragraph)}"
                seen[full] = seen.get(full, 0) + 1
                yield (full if seen[full] == 1 else f"{full}#{seen[full]}"), paragraph


def _quotes_a_round_count(prose: str) -> bool:
    return bool(_ROUND_COUNT.search(" ".join(prose.split())))


def _states_a_control_count(prose: str) -> bool:
    return bool(_CONTROL_COUNT.search(" ".join(prose.split())))


def test_a_sweep_record_that_quotes_a_failure_count_states_its_control_count():
    """The rule with teeth: a number of failures is a kill count only against a measured baseline.

    The ratchet is compared for EQUALITY, not containment, and both directions are load-bearing. A
    NEW record without a control is the regression this card exists to stop. A LEGACY record that
    grew a control (or was deleted, or renamed) must leave the list in the same commit — a
    grandfather list nobody prunes is how a guard turns into decoration, and this one names the
    records whose numbers a reader should not trust, so a stale entry misinforms.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, every round
    restored with `git checkout --` and the restore confirmed by re-running to the control. Control
    round: 0 failed, 1 passed.
      * append a NEW record quoting `-> 2 failed` with no control (a docstring in this file, then
        a `#` banner above a test in test_workflow_wip.py, to exercise both extractors)
        -> 1 failed each, the message naming the new key
      * append a new record quoting `-> 2 failed` WITH `control 0 failed` -> 0 failed, 1 passed:
        the rule accepts what it asks for, so it is not a ban on numbers
      * append a new record quoting `-> FAIL; control PASS` (no digits at all)
        -> 0 failed, 1 passed: qualitative rounds keep their qualitative control
      * append a new record quoting `-> 2 failed` with `control PASS` -> 1 failed, which is the
        card's whole thesis: the weak form must not satisfy the numeric one
      * drop one entry from LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT -> 1 failed, and add a
        non-existent one -> 1 failed, so the list cannot rot in either direction
      * weaken `_CONTROL_COUNT` to "a digit near the word control" -> 0 failed HERE, and that is
        a redundancy VMCP-167 (688) COST rather than a round that passed. Under the record unit
        it was 1 failed on the SECOND assert, because one legacy entry (test_api_kanban's
        honest-server-paginates) stopped being an offender under the looser regex. Re-measured at
        paragraph granularity, same selection of one test: the weak and the strong pattern give
        the IDENTICAL 16-key offender set, so the ratchet cannot see the weakening at all. It
        still vouches for 29 paragraphs this pattern refuses — none of which quote a round count,
        which is exactly why none of them reach the list. So the tally requirement is now held by
        ONE test instead of two, the pattern test below, which is its proper owner: the same
        weakening is 1 failed there, on the two rows that use the word in another sense. Written
        down rather than dropped, because a redundancy that vanishes quietly is how a guard turns
        into decoration
      * key a comment run by its definition ALONE, dropping the opening line -> 1 failed, ten
        legacy entries changing key at once. The SECOND half of this row no longer holds, and is
        corrected here rather than left standing: under the record unit the same new uncontrolled
        banner above `_spelled_ref` was 1 failed WITH the opening line in the key and 1 passed
        without it, swallowed by a grandfathered key. Re-measured at paragraph granularity it is
        1 failed BOTH ways and NAMED both times, because the paragraph head now does that
        disambiguation. The opening line still earns its place for the shape it alone separates —
        two runs above ONE definition whose corresponding paragraphs agree — but it is no longer
        what catches this construction

    RE-MEASURED FOR VMCP-167 (688), which moved the unit from the whole record to the PARAGRAPH.
    A new selection means a new control, so these rounds do not share the one above: the whole
    scanner file as the selection, `__pycache__` deleted per round, `PYTHONDONTWRITEBYTECODE=1`,
    every round restored from a COPY and the restore confirmed by returning to the control.
    Control round: 0 failed, 4 passed.
      * install the pre-image of the docstring VMCP-161 (668) was filed against -> 1 failed,
        naming its `IT GOES THROUGH _flat FOR ITS HARNESS CAP` paragraph. The SAME file under the
        pre-688 record unit, against that scanner's own control of 0 failed, is 0 failed. That
        pair is the card: not an argument about granularity but a record the scanner could not
        see, and the human who filed 668 could
      * add a NEW uncontrolled round to a comment run ALREADY in the ratchet (`_spelled_ref`'s)
        -> 1 failed, naming the new paragraph; the identical construction under the pre-688
        record unit is 0 failed, because one grandfathered RECORD key vouched for every paragraph
        beneath it. Being on the list used to buy silence for prose written afterwards
      * `_records` stops splitting, i.e. back to the record unit -> 1 failed on the FIRST assert:
        every key reverts to a record-shaped one the list does not name
      * a bare `#` line stops counting as blank, so comment runs stay whole -> 2 failed, here and
        on the paragraph pin below. Without that conjunct the split reaches docstrings only, and
        this repo's biggest sweep records are banner comments
      * `_paragraph_head` collapsed to a constant -> 2 failed, the message carrying the loud
        `#2`/`#5` suffixes `_records` appends rather than merging the paragraphs silently
      * the four appended-record rounds above were re-run under this unit and are unchanged:
        1 failed uncontrolled, 0 failed with `control 0 failed`, 0 failed for `-> FAIL; control
        PASS`, 1 failed for `control PASS` beside a number
    """
    offenders = sorted(
        key for key, prose in _records()
        if _quotes_a_round_count(prose) and not _states_a_control_count(prose)
    )
    new = sorted(set(offenders) - LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT)
    fixed = sorted(LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT - set(offenders))

    assert not new, (
        f"{new} quote a mutation round as a NUMBER of failures without stating the CONTROL "
        "round's number of failures IN THE SAME PARAGRAPH. `N failed` is a kill count only if the "
        "SAME selection failed zero times unmutated, and a -q summary line cannot tell you that: "
        "card 594 read six rows straight off one in a tree with 30 constant failures and was "
        "wrong by 16x. Run the unmutated round first and write its FAILED count into the record "
        "next to the mutation's — `control 0 failed; <mutation> -> 2 failed`. Not the pass total: "
        "that moves with every test the repo adds. `control PASS` is fine for a round reported as "
        "`-> FAIL`, and not for one reported as a number. The key names the paragraph, and the "
        "paragraph is the unit (VMCP-167 / 688): a control stated once for a whole section does "
        "NOT reach the rounds below the next blank line, so repeat it or close up the blank line. "
        "The rule is CLAUDE.md's Testing Philosophy"
    )
    assert not fixed, (
        f"{fixed} are listed in LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT but are no longer "
        "offenders — they gained a control count, or were renamed, moved or deleted, or their "
        "paragraph was re-wrapped so its first 48 characters changed. Take them out of the list "
        "in the same commit. The list is not a suppression file: it is the standing statement of "
        "which numbers in this suite have no measured baseline, so an entry that no longer "
        "applies misleads the next reader"
    )


def test_the_scanner_tells_a_pytest_tally_from_the_other_numbers_in_this_repo():
    """What the two patterns accept and refuse, pinned on prose rather than on the live suite.

    Without this the scanner's precision is invisible: the test above passes on today's tree for
    any regex that happens to reproduce today's offender set, including a `\\d+ failed` with no
    exclusions at all (which would then report a docker error message and an exit code as sweep
    records) and including one that never matches (which would report nothing, forever). The rows
    below are of two kinds, and saying which is the correction of an earlier version of this
    sentence that called them all repo strings. Measured verbatim against every `.py` and `.md`
    outside this file: 5 of the 13 occur word for word, two more are ADAPTATIONS of real ones
    (test_api_kanban's "a positive control at the same call site" and "The control: page 1 serving
    EXACTLY the stated 5"), and the rest are constructed to pin the pattern at its edges, which is
    what a pattern test is for.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, restores
    confirmed by re-running to the control. Control round: 0 failed, 1 passed.
      * drop `(?<![:.\\w])` from `_ROUND_COUNT` -> 1 failed on the docker-port row
      * drop `(?<!of )` -> 1 failed on the containers row
      * make `_CONTROL_COUNT` accept any digit near `control` -> 1 failed on the two rows that
        use the word in another sense. Those two rows exist BECAUSE of this round: the first
        version of this test claimed the weakening was caught by the `control PASS; -> 2 failed`
        row, ran it, and got 0 failed — that row is refused by the weak pattern too. The claim
        was true about the ratchet test above and false here, and only running it said so
      * widen `[^.;]` to `.` in `_CONTROL_COUNT` -> 1 failed, reported on the `control PASS; drop
        the rule -> 2 failed` row and NOT on the different-sentences row: both flip, and the loop
        asserts in order, so it stops at the first. Re-measured after an earlier version of this
        line named the later one of the same record
      * widen the 60-character window to 200 -> 1 failed on the long-clause row
      * delete either direction of the `_CONTROL_COUNT` alternation -> 1 failed, so both the
        "control first" and "count first" phrasings this repo actually uses stay covered
    """
    # (prose, quotes a round count, states a control count)
    rows = [
        ("`Bind for 0.0.0.0:3456 failed: port is already allocated`", False, False),
        ("full-suite runs against FRESH containers: 5 of 9 failed", False, False),
        ("the hub's idle check (exit 0 ran / 1 failed)", True, False),
        ("control PASS; drop the rule from SKILL.md -> FAIL", False, False),
        ("the control at the same call site (the threshold made 1 request)", False, False),
        ("control: page 1 of 2 served in full", False, False),
        ("control PASS; drop the rule -> 2 failed", True, False),
        ("control 0 failed; drop the rule -> 2 failed", True, True),
        ("control 2 passed; drop the `.playwright-mcp/` line -> `1 failed, 1 passed`", True, True),
        ("Whole file 2 failed against an unmutated control round of 0", True, True),
        ("the unmutated control round is 0 failed and the mutation is 2 failed", True, True),
        ("control PASS. An unrelated paragraph of the same record says 0 failed", True, False),
        ("control PASS on a clause long enough to run past sixty characters of prose before it "
         "reaches 0 failed", True, False),
    ]
    for prose, expected_round, expected_control in rows:
        assert _quotes_a_round_count(prose) is expected_round, \
            f"_ROUND_COUNT read {prose!r} as {not expected_round}"
        assert _states_a_control_count(prose) is expected_control, \
            f"_CONTROL_COUNT read {prose!r} as {not expected_control}"


def test_a_control_in_one_paragraph_does_not_vouch_for_the_next():
    """The UNIT itself, pinned on constructed prose instead of on today's offender set.

    The ratchet above passes for ANY splitter that happens to reproduce today's offenders —
    including one that never splits at all, which is precisely the state VMCP-167 (688) found and
    which shipped green for as long as it existed. So the split is pinned here directly, in both
    extractors' shapes and in both directions: a control vouches for its own paragraph, does NOT
    vouch for the next one, and the same text read WHOLE still reads as controlled. That last
    assert is what keeps this a test of the UNIT rather than of the two regexes — it states the
    pre-688 answer as an assertion, so the two granularities stay comparable and a future reader
    can see what changed rather than take it on trust.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, `__pycache__` deleted per round, the whole
    scanner file as the selection, every round restored from a COPY (never `git checkout --`:
    this card's edits are uncommitted while it is in Build) and the restore confirmed by
    returning to the control. Control round: 0 failed, 4 passed.
      * `_paragraphs` treats no line as blank, so every record is one chunk -> 2 failed, here and
        on the ratchet
      * a bare `#` line stops counting as blank -> 2 failed, and the comment-run half of this
        test is the half that goes: a run has no blank line, `#` alone is its separator
      * `_paragraph_head` collapsed to a constant -> 2 failed
      * `_records` stops splitting -> 1 failed on the RATCHET ONLY, not here, because these
        asserts call `_paragraphs` directly. That is deliberate: this test pins the splitter, the
        ratchet pins its use, and a mutation that hits only one of them says which is which
      * insert a blank line before the module docstring's last `WHAT IT CANNOT ENFORCE` bullet
        -> 2 failed, which is the round that made the last assert here worth writing. The bullet
        it guards says that block is ONE paragraph, and that is a claim about the text it is
        written in: it first shipped as a measured pair, "four bullets, 47 lines", and the next
        three edits to that same block falsified it three times running (51, 54, 56) while every
        test stayed green. Asserted, it cannot rot; dated, it rotted within the hour
    """
    docstring = (
        "Round A: control 0 failed; drop guard A -> 2 failed.\n"
        "\n"
        "Round B, never baselined: drop guard B -> 9 failed.\n"
    )
    comment_run = (
        "# Round A: control 0 failed; drop guard A -> 2 failed.\n"
        "#\n"
        "# Round B, never baselined: drop guard B -> 9 failed.\n"
    )
    for label, record in (("docstring", docstring), ("comment run", comment_run)):
        chunks = list(_paragraphs(record))
        assert len(chunks) == 2, \
            f"the {label} split into {len(chunks)} paragraphs, not 2 — a comment run's separator " \
            "is a bare `#`, a docstring's is a blank line, and both must count"
        first, second = chunks
        assert _quotes_a_round_count(first) and _states_a_control_count(first), \
            f"the {label}'s controlled paragraph stopped reading as controlled"
        assert _quotes_a_round_count(second), f"the {label}'s second round count went unseen"
        assert not _states_a_control_count(second), \
            f"the {label}'s Round B paragraph is vouched for by Round A's control — that is the " \
            "whole of VMCP-167 (688): one clause immunising a count it says nothing about"
        assert _states_a_control_count(record), \
            f"the {label} read WHOLE still states a control, which is exactly why the record " \
            "was the wrong unit; if this ever fails the two halves are no longer comparable"
        assert _paragraph_head(second) != _paragraph_head(first), \
            f"the {label}'s two paragraphs collapsed onto one key, so the ratchet could name " \
            "neither — the failure `_records` suffixes loudly rather than swallows"

    # The canonical shape this file ASKS authors for must survive the split: a control header and
    # its rounds are ONE paragraph. This is the cost of the unit, priced rather than asserted —
    # only a blank line separates the header from the rounds it vouches for.
    canonical = (
        "Control round: 0 failed, 1 passed.\n"
        "  * drop the guard -> 1 failed\n"
        "  * drop the other guard -> 2 failed\n"
    )
    assert [_states_a_control_count(p) for p in _paragraphs(canonical)] == [True], \
        "a control header with its rounds listed under it must stay ONE controlled paragraph — " \
        "it is the shape this file's own MUTATION-CHECKED blocks are written in"
    opened_up = canonical.replace("1 passed.\n", "1 passed.\n\n")
    assert [_states_a_control_count(p) for p in _paragraphs(opened_up)] == [True, False], \
        "the cost of the paragraph unit is not what this file says it is: a control header cut " \
        "off from its rounds by a blank line must stop vouching for them"

    # This module's own `WHAT IT CANNOT ENFORCE` block claims to be ONE paragraph, and that claim
    # is about the text it is written in — so it is asserted, not dated. Three successive edits
    # falsified the line count that used to stand here before it was replaced by this assert.
    module_doc = ast.get_docstring(ast.parse(Path(__file__).read_text(encoding="utf-8")),
                                   clean=False)
    hosting = [p for p in _paragraphs(module_doc) if "WHAT IT CANNOT ENFORCE" in p]
    assert len(hosting) == 1, \
        "the module docstring's `WHAT IT CANNOT ENFORCE` bullets are no longer ONE paragraph, so " \
        "the bullet above saying they are is now false — either restore the blank-line-free " \
        "block or rewrite that bullet, because it is this file's own example of the limit"
    assert "It is a RATCHET, not a retrofit." in hosting[0], \
        "the LAST bullet of `WHAT IT CANNOT ENFORCE` is no longer in the SAME paragraph as its " \
        "heading, so a blank line appeared inside the block — which is exactly what the bullet " \
        "claiming they are all one paragraph denies. Anchored on the bullet's opening sentence " \
        "rather than on the block's last words, because the last words are edited far more often"


def _testing_philosophy() -> str:
    """CLAUDE.md's Testing Philosophy section, sliced like every other prose pin in this repo.

    Scoped to the section rather than matched over the whole file for the reason `_gc_section` in
    test_skill_contract gives: `PYTHONDONTWRITEBYTECODE` and `.venv` are named elsewhere in
    CLAUDE.md too, so a whole-file substring could not tell "the rule is still stated" from "some
    other paragraph still mentions the words".
    """
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    start = text.find("\n## Testing Philosophy\n")
    assert start != -1, "CLAUDE.md no longer has a Testing Philosophy section to pin"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the Testing Philosophy section no longer ends where the next one begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the slice is not a proper subset of CLAUDE.md"
    return section


def test_claude_md_states_the_control_round_rule_and_its_limit():
    """The rule an agent READS, pinned next to the rule a scanner ENFORCES.

    Both halves are needed and neither substitutes for the other. CLAUDE.md is in every agent's
    context before it sweeps, so that is where behaviour changes; the scanner above is only met
    when it goes red, which is after the sweep was already reported. And the LIMIT is pinned as
    hard as the rule: a control round is the cheapest detector of a lying sweep, not a complete
    one, and #646's form — `.venv` dragged in by `cp -R`, the original `src` earlier on
    `sys.path`, mutation never applied, everything green — walks straight past it. A rule that
    left that unsaid would teach the next agent that a clean control means the round measured
    something, which is the same over-claim in a new place.

    MUTATION-CHECKED, `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test selected per round, CLAUDE.md
    restored from a COPY between rounds (never `git checkout --`: this card's own edit is
    uncommitted while the card is in Build). Control round: 0 failed, 1 passed.
      * delete the control-round paragraph -> 1 failed naming the missing rule
      * delete the limits paragraph, keeping the rule -> 1 failed naming the missing limit
      * soften `WRITE ITS FAILED COUNT` to "note the control" -> 1 failed
      * flip `never the pass total` to "and the pass total" -> 1 failed
      * move both paragraphs OUT of Testing Philosophy into the Releases section -> 1 failed, so
        the slicer is load-bearing and not decoration
      * delete the `## Testing Philosophy` heading itself -> 1 failed from the slicer, with its
        own message rather than a confusing IndexError
      * delete the sentence saying only removing `__pycache__` moved the round -> 1 failed: the
        variable alone reads as the whole remedy otherwise, and measurement says it is not
      * delete the same-paragraph clause VMCP-167 (688) added, keeping the rest -> 1 failed
        (re-measured with the whole scanner file as the selection, against its own control of
        0 failed, 4 passed). The scanner now REFUSES a shape this section's older wording
        permitted — one control header for a long section — so an agent who reads "beside" as
        "somewhere in this docstring" writes prose the suite rejects, and the rulebook has to
        say where the control goes, not just that there is one
    """
    section = _testing_philosophy()

    assert "UNMUTATED CONTROL round on the SAME selection" in section, \
        "CLAUDE.md no longer tells a sweep to open with an unmutated control round on the same " \
        "selection — the one check that would have caught #594's 16x inflation"
    assert "WRITE ITS FAILED COUNT beside the round's" in section, \
        "CLAUDE.md no longer asks for the control's COUNT. The word 'control' alone is what the " \
        "sweeps already had: 'control PASS' in a tree with 30 constant failures is true and useless"
    assert "Record the FAILED count, never the pass total" in section, \
        "CLAUDE.md no longer says WHICH number to record. A pass total goes stale with every " \
        "test the repo adds — the same reason the unit count above it is a floor"
    assert "is enforced IN THE SAME PARAGRAPH" in section, \
        "CLAUDE.md no longer says WHERE the control has to sit. The scanner's unit is the " \
        "paragraph (#688), so a section-wide control header is refused — an agent who reads " \
        "'beside' as 'somewhere in this docstring' writes the shape that made #668 filable"

    assert "A clean control does not mean the round MEASURED anything" in section, \
        "CLAUDE.md no longer bounds the control round. Sold as complete, it teaches that a green " \
        "control means the mutation applied — and #646's four false greens each had one"
    assert "PYTHONDONTWRITEBYTECODE=1" in section, \
        "CLAUDE.md no longer names the stale-bytecode form (#624): a constant rewritten to the " \
        "SAME LENGTH replays the previous budget when the mtime does not advance a whole second"
    assert "only deleting `__pycache__` moved" in section, \
        "CLAUDE.md no longer says the variable alone is not the remedy. Measured: it stops " \
        "Python WRITING bytecode, not READING it, so a stale .pyc already on disk still replays"
    assert "rsync -a --exclude .venv" in section and "vikunja_mcp.__file__" in section, \
        "CLAUDE.md no longer names the form a control round CANNOT catch (#646) or the check " \
        "that does: copy without .venv, and print where the package was actually imported from"


def test_claude_md_says_a_stale_figure_sweep_is_not_line_fed():
    """The OTHER sweep in this section — the text one that hunts stale figures — and its trap.

    Pinned in three pieces, and two of them are the counter-intuitive half rather than the rule.
    The rule alone ("do not be line-fed") invites the obvious fix, and the obvious fix is measured
    wrong: on `e86b2c9^` a whitespace CLASS in place of the literal space returns the same 15 hits
    in test_api_kanban.py and still misses the wrapped figure at :1473, because grep is fed one
    line at a time and no PATTERN changes that. A FLAG does, and the honest version of this rule
    has to say so: `grep -z` takes the file as one record and finds the figure, 16 hits to 15 —
    then reports every hit as line 1, which is why it replaces the blind spot rather than closing
    it. Measured on both greps here. And a spanning matcher reported
    without the DIFF against the per-line hits is unusable rather than merely noisy: of the few
    hundred hits `\\d+ (?:passed|failed)` returned over tests/ on 2026-08-02, all but TWO were
    ones the line-anchored sweep already found. The total is deliberately not pinned and the TWO
    is, because only one of them survived the day: THIS docstring lives under tests/, so writing
    it added 8 to the total, and rebasing onto a sibling's landing added 55 more — while the
    wrapped count stayed 2 across both.

    Why a pin at all, when the class has no live instance — the whole value of these sweeps is the
    NEGATIVE answer, so the rule is read exactly once per sweep, by an agent about to write "this
    file is clean" into a card. A paragraph nobody re-runs is the first thing a later edit drops.

    MUTATION-CHECKED, `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, exactly 1 test
    selected per round, CLAUDE.md restored from a COPY between rounds (never `git checkout --`:
    this card's own edit is uncommitted while the card is in Build). Control round: 0 failed,
    1 passed.
      * delete the paragraph entirely -> 1 failed, on the rule assert
      * keep the rule, delete the measured `[[:space:]]` counter-example -> 1 failed: without it
        the paragraph reads as "write a better regex", which is the fix that does not work
      * keep both, drop the requirement to report the DIFF -> 1 failed
      * move the paragraph out of Testing Philosophy into Releases -> 1 failed, so the section
        slicer is load-bearing for this pin too and not just inherited from the one above
      * restore -> 0 failed, 1 passed, back to the control
    """
    section = _testing_philosophy()

    assert "must not be LINE-FED" in section, \
        "CLAUDE.md no longer says a sweep for stale figures must not be line-fed. Prose here " \
        "wraps at ~100 columns, so `grep -rn` reports a file CLEAN at a wrapped figure — which " \
        "is the one answer a sweep exists to give"
    assert "[[:space:]]" in section, \
        "CLAUDE.md no longer carries the measured counter-example, and the rule is worth less " \
        "without it: loosening the PATTERN does not help, because grep is handed one line at a " \
        "time. Without this the next reader fixes the regex and stays blind"
    assert "DIFF against the per-line hits" in section, \
        "CLAUDE.md no longer says WHAT to report. A raw spanning hit list is dominated by what " \
        "the line-anchored sweep already found, so the difference is the only useful output — " \
        "and the noise a wrap-crossing pattern adds has to be eyeballed, not counted"
