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
  * One control vouches for the WHOLE record, not for each number in it. Constructed and measured:
    a banner reading "Round A: control 0 failed; drop guard A -> 2 failed. Round B, never
    baselined: drop guard B -> 9 failed. Round C, likewise never baselined: drop guard C ->
    41 failed." satisfies this scanner completely. Per-number pairing is not recoverable from
    prose, so the rule the assert states is enforced per RECORD and asked of the author per ROUND.
    Re-measured 2026-08-02 on a REAL record instead of a constructed one, `__pycache__` cleared
    and PYTHONDONTWRITEBYTECODE=1: the pre-image of the docstring VMCP-161 (668) was filed
    against — it quotes "5 failed / 102 passed", wrapped across two lines at its site so only a
    whitespace-flattened read finds it, and no paragraph of its own states a control — is clean
    here, 0 failed, because a control clause about a DIFFERENT mutation sits 27 lines further down
    the SAME docstring; deleting that one clause turns it red, 1 failed, naming the key.
    VMCP-167 (688) carries the granularity question, and two candidate units were measured for it.
    Splitting a record at BLANK LINES catches that pre-image and flags no new record today: 9
    offending paragraphs among the 36 that the 7 already-listed records contain. But that result
    is SPLITTER-DEPENDENT, which is the finding rather than a caveat on it — a comment run has no
    blank line, its separator is a bare `#`, and counting that as one too gives 16 offending
    chunks across 8 records, the eighth being new. Splitting at BULLETS is the wrong unit either
    way: 20 offenders, 11 of them inside records that DO state a control, and all 11 are this
    file's own MUTATION-CHECKED blocks — a control header with its rounds listed under it, which
    is the shape this file asks authors to write.
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
  * It is a RATCHET, not a retrofit. `LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT` names the records
    that already quote a count without one. They are deliberately NOT "fixed": the control that
    belongs beside a historical number is the one measured in THAT environment at THAT sha, and
    it is unrecoverable. Writing today's `0 failed` next to a number measured last week would be
    a fabricated measurement — precisely the defect cards 646/655/663/674 exist to remove. So the
    list records which numbers this SCANNER cannot see a baseline for — narrower than
    "uninterpretable", since two entries do state one in a form the pattern cannot read (the list's
    own comment names them). It may only SHRINK, by someone re-measuring a sweep and its control.
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

# Records whose prose has the SHAPE this scanner refuses — a round quoted as a number, with no
# control count in the SAME record — as they stood when #656 landed. A SHAPE list is all it is,
# and the difference matters: at least two entries do state a baseline in a form this pattern
# cannot read (test_the_degraded_stop_rule quotes a set-wise negative AND positive control;
# test_the_checkout_probe runs a fully controlled qualitative sweep and lands here only for an
# unrelated tally describing a CONSTRUCTED broken tree, not a mutation round). Read it as "cannot
# confirm a baseline here", never as "these numbers are uninterpretable". See the module
# docstring: it may only SHRINK, and only by re-MEASURING the sweep behind an entry.
LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT = frozenset({
    "tests/unit/test_api_kanban.py::_serving_lengths",
    "tests/unit/test_api_kanban.py::test_the_degraded_stop_rule_does_not_depend_on_bucket_order",
    "tests/unit/test_api_kanban.py::test_neither_read_loses_a_task_an_honest_server_paginates",
    "tests/unit/test_repo_browser_isolation.py"
    "::test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_blocker_moved_to_backlog"
    ":--- the starving-tail message is the plain tail ",
    "tests/unit/test_workflow_sequence_gate.py::comments-above:_spelled_ref"
    ":--- the prose's INTERPOLATED VALUES, not just it",
    "tests/unit/test_workflow_wip.py::comments-above:_clause_free_base"
    ":--- the free == 0 note is the base plus the ENUM",
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
        opening = " ".join(run[0].lstrip().lstrip("#").strip().split())[:48] or "<blank>"
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


def _records():
    """(key, prose) for every docstring and comment run under tests/, EVERY KEY DISTINCT.

    Distinctness is load-bearing rather than tidy: the offender set is a set of keys and the legacy
    list suppresses BY key, so two records under one key mean a grandfathered entry vouching for
    the other — measured, and written up in `_comment_runs`. Each extractor avoids its own
    collisions (dotted qualnames there, the opening line here); this counter is the backstop for
    what neither rules out, a redefined function name or two runs above one definition whose first
    48 characters agree. A `#2` suffix in a message means exactly that, and it is loud, not silent.
    """
    for path in sorted(TESTS_DIR.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        relative = path.relative_to(REPO_ROOT).as_posix()
        seen: dict[str, int] = {}
        for key, text in itertools.chain(_docstrings(path, source), _comment_runs(source)):
            full = f"{relative}::{key}"
            seen[full] = seen.get(full, 0) + 1
            yield (full if seen[full] == 1 else f"{full}#{seen[full]}"), text


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
      * weaken `_CONTROL_COUNT` to "a digit near the word control" -> 1 failed, on the SECOND
        assert rather than the first: no NEW offender appears, but one legacy entry
        (test_api_kanban's honest-server-paginates) stops being one, and a list that may shrink
        only by re-measurement must not shrink by a looser regex. Re-measured 2026-08-02 over four
        readings of "a digit near control" — clause-bounded window or any characters, one
        direction or both, and the loosest "the word anywhere plus any digit anywhere": the first
        three move that one entry, the loosest moves three (only 3 of the 7 entries contain the
        word at all, so three is the ceiling). Never the 3 this row shipped with; that figure did
        not reproduce, and this is the corrected one. The tally requirement is held by the ratchet
        rather than by taste either way
      * key a comment run by its definition ALONE, dropping the opening line -> 1 failed: three
        legacy entries collapse onto keys shared with other runs, which is the hole `_comment_runs`
        documents. The same new uncontrolled banner is 1 failed above `_spelled_ref` WITH the
        opening line in the key and was 1 passed without it
    """
    offenders = sorted(
        key for key, prose in _records()
        if _quotes_a_round_count(prose) and not _states_a_control_count(prose)
    )
    new = sorted(set(offenders) - LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT)
    fixed = sorted(LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT - set(offenders))

    assert not new, (
        f"{new} quote a mutation round as a NUMBER of failures without stating the CONTROL "
        "round's number of failures. `N failed` is a kill count only if the SAME selection "
        "failed zero times unmutated, and a -q summary line cannot tell you that: card 594 read "
        "six rows straight off one in a tree with 30 constant failures and was wrong by 16x. Run "
        "the unmutated round first and write its FAILED count into the record next to the "
        "mutation's — `control 0 failed; <mutation> -> 2 failed`. Not the pass total: that moves "
        "with every test the repo adds. `control PASS` is fine for a round reported as `-> FAIL`, "
        "and not for one reported as a number. The rule is CLAUDE.md's Testing Philosophy"
    )
    assert not fixed, (
        f"{fixed} are listed in LEGACY_RECORDS_WITHOUT_A_CONTROL_COUNT but are no longer "
        "offenders — they gained a control count, or were renamed, moved or deleted. Take them "
        "out of the list in the same commit. The list is not a suppression file: it is the "
        "standing statement of which numbers in this suite have no measured baseline, so an "
        "entry that no longer applies misleads the next reader"
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
