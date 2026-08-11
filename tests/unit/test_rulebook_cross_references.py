"""Every "see …" pointer in the rulebooks must name a section or bullet that exists — #997.

WHY THIS FILE EXISTS, and it is a defect class rather than a tidiness rule. SKILL.md routes an
agent to its references BY NAME: "see \"Commit+push is part of the transition to Review\"" is
how a per-task agent finds the integration recipe, and `references/*.md` point back the same
way. A pointer that resolves to nothing does not fail loudly — the agent simply does not find
the rule, and behaves as if it were never written.

WHAT PRODUCED IT AT SCALE. #997 translated 211 000 characters of rulebook with nine agents in
parallel, one per slice. Each owned some headings and had to GUESS at the wording of headings
owned by another slice in order to write a cross-reference at all. Measured over the RU
originals before the work started: 306 distinct quoted strings, 357 uses, of which 17 named a
heading — so the guessing was structural, not careless. It produced seven unfollowable
pointers, including ONE bullet carrying THREE different names: `drain.md` shipped "Having cast
a verdict, the reviewer releases its own tree" while `stuck.md` (twice) and `review.md` pointed
at two other wordings of it. An independent reviewer found that one by reading; this file is
the mechanism that reading is not.

WHAT IT CHECKS, and it is narrower than "the rulebooks are consistent". It reads the SHAPE of a
pointer — a quoted phrase introduced by see/under/in — and asks only whether some heading or
bold bullet title in the rulebook set contains it. It does not check that the pointer leads
somewhere USEFUL, and it cannot: a section can be renamed to something equally unhelpful and
this stays green. What it removes is the failure that is invisible to a reader of either file
alone, because both halves look fine on their own.

THREE MEASURED CONSTRUCTION NOTES, each of which cost a wrong answer first:

* **Bold bullet titles WRAP**, and a per-line regex silently misses every wrapped one. The
  first version of this scan matched `^\\s*[-*] \\*\\*(.+?)\\*\\*` without DOTALL and reported
  14 unfollowable pointers where the true number was 6 — it had failed to collect the targets,
  not found broken links. A checker that over-reports is not the safe direction here: it invites
  "fixing" pointers that were already correct.
* **Pointers wrap too**, so the text is whitespace-flattened before matching. Un-flattened, the
  same scan hid five more real breaks behind line ends — including the three-named bullet.
* **A pointer may legitimately name PROSE rather than a title.** `gc-report.md` says see "the
  NON-mechanical one" below, and that phrase is a sentence further down the same file. So the
  allowance below is deliberate and NOT a loophole to widen: a pointer that resolves to its own
  file's body text is accepted, everything else must name a real title.

MUTATION SWEEP, and it is the reason two defects in this file's own logic are described above
rather than shipped. Three rounds, each re-creating a failure this card actually produced, run
with `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1, read by counting `FAILED ` lines:
control (opening) 0 failed; re-break a pointer to its pre-fix wording -> 1 failed
(the pointer test); rename a target and leave its pointers behind -> 1 failed (the pointer
test); ship one rule under two wordings -> 1 failed (the duplicate-name test); control
(closing, restored) 0 failed.

THE FIRST TWO ROUNDS CAME BACK GREEN ON THE FIRST DRAFT, and that is the record worth keeping:
this gate was VACUOUS when it was first written and passing. The prose fallback was judged
against the whole file INCLUDING the pointer, so `phrase in own` was circular and always true;
and the pointer pattern demanded the quote immediately after see/in, so `in the bullet "X"` —
the form `review.md` actually uses — was never scanned at all. A gate written to catch a class
that had just been fixed by hand did not catch a single instance of it, and only the sweep said
so.
"""
import pathlib
import re

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
_TRACKER = REPO_ROOT / "src/vikunja_mcp/skills/tracker"

# see/under/in + a quoted phrase, with an OPTIONAL noun between them. The noun is not decoration:
# measured, the rulebooks write both `see "X"` and `in the bullet "X"`, and a pattern demanding
# the quote immediately after the verb missed the second form entirely — the mutation that
# renames a target while leaving its pointers behind stayed GREEN until this alternative existed.
# Adding a verb or a noun widens the gate, which is fine; narrowing it is what needs a reason.
_NOUN = r'(?:the\s+)?(?:section|bullet|item|rule|paragraph|heading)?\s*'
_TRIGGER = re.compile(r'(?:see|See|under|in)\s+' + _NOUN + r'(?="[^"]{6,95}")')
# A pointer may name SEVERAL targets in one breath — `see "A" and "B"`. Scanning only the first
# is the FOURTH blind side this gate shipped with, found by an independent reviewer: breaking
# the first name reddened, replacing the second with a heading that does not exist stayed GREEN,
# and six unfollowable pointers were live and passing at the time. So a trigger opens a CHAIN,
# and every link in it is checked.
# The connector between links is BOUNDED prose rather than a fixed word list: measured, the
# rulebooks join two names with everything from ` and ` to `, and that one stands INSIDE the
# section `. A word list kept missing the long ones one at a time, so the rule is instead: the
# next quoted name still belongs to the same pointer while the gap is short and does not end a
# sentence. `.` followed by a space closes the chain, and so does `)` — a pointer written
# inside a parenthetical ends WITH that parenthetical, and what follows is new prose. Both
# terminators were added because of a measured FALSE POSITIVE, not defensively.
_QUOTED = re.compile(r'"([^"]{6,95})"')
_CHAIN_GAP = 60


def _rulebooks() -> list[pathlib.Path]:
    return [_TRACKER / "SKILL.md", *sorted((_TRACKER / "references").glob("*.md"))]


def _flat(text: str) -> str:
    return re.sub(r"\s+", " ", text)


def _norm(s: str) -> str:
    return " ".join(re.sub(r"[^a-z0-9 ]", " ", s.lower()).split())


def _titles(files) -> set[str]:
    """Headings AND bold bullet titles. The bold span may wrap, hence re.S — see the docstring."""
    out: set[str] = set()
    for f in files:
        text = f.read_text(encoding="utf-8")
        for m in re.finditer(r"^#{1,4} (.+?)\s*$", text, re.M):
            out.add(m.group(1).strip())
        for m in re.finditer(r"^[ \t]*[-*] \*\*(.+?)\*\*", text, re.M | re.S):
            out.add(_flat(m.group(1)).strip().rstrip(":.,—"))
    return out


def test_every_rulebook_pointer_names_something_that_exists():
    """The gate. A red here means an agent sent somewhere that is not there."""
    files = _rulebooks()
    assert files and files[0].is_file(), "the rulebooks moved — repoint this scan, do not delete it"
    titles = {_norm(t) for t in _titles(files)}
    assert len(titles) > 100, (
        f"only {len(titles)} titles collected from {len(files)} rulebooks, which is too few to "
        f"be real — the extraction broke, and a broken extraction makes every pointer look "
        f"unfollowable. Check the bold-title regex before believing any failure below"
    )

    broken = []
    for f in files:
        body = _flat(f.read_text(encoding="utf-8"))
        # The prose fallback must be judged on the file MINUS its pointers. Measured: with the
        # pointers left in, `phrase in own` is CIRCULAR — the quoted phrase is itself part of the
        # body, so the fallback always succeeded and this whole gate was vacuous. Both mutations
        # written to prove it bites (re-break a pointer; re-introduce the three-named bullet)
        # came back GREEN until this line existed.
        names = []
        for trig in _TRIGGER.finditer(body):
            pos = trig.end()
            while (link := _QUOTED.search(body, pos)) is not None:
                gap = body[pos:link.start()]
                if len(gap) > _CHAIN_GAP or ". " in gap or ")" in gap:
                    break
                names.append(link.group(1))
                pos = link.end()
        own = _norm(re.sub(r'"[^"]{6,95}"', " ", body))
        for raw in names:
            phrase = _norm(raw.strip().rstrip(":.,—"))
            if any(phrase == t or phrase in t for t in titles):
                continue
            if phrase in own:          # names prose in its own file — see the docstring
                continue
            broken.append(f"{f.name}: \"{raw}\"")

    assert not broken, (
        "these pointers name a section or bullet that no rulebook has, so an agent following "
        "them finds nothing and proceeds as if the rule did not exist:\n  "
        + "\n  ".join(sorted(set(broken)))
        + "\nFix the POINTER to the owner's exact wording, not the owner to the pointer — "
        "several pointers may name one target, and renaming the target breaks the others."
    )


def test_one_target_is_not_known_by_several_names():
    """The three-named-bullet shape, caught directly rather than through its symptoms.

    The reviewer-releases-its-tree bullet shipped under three wordings across three files. Every
    pointer still RESOLVED under a substring match, so the gate above would have stayed green on
    two of them while a human reading either file would have found no such heading. What makes it
    detectable is that the near-duplicates differ only in wording: same content words, different
    order or articles.
    """
    titles = sorted(_titles(_rulebooks()))
    seen: dict[frozenset, str] = {}
    clashes = []
    for t in titles:
        words = frozenset(w for w in _norm(t).split() if len(w) > 3)
        if len(words) < 4:
            continue
        if words in seen and seen[words] != t:
            clashes.append(f"{seen[words]!r} vs {t!r}")
        seen[words] = t
    assert not clashes, (
        "these titles carry the same content words in different wordings, so they are almost "
        "certainly one rule written twice — pointers at it will split between them:\n  "
        + "\n  ".join(clashes)
    )
