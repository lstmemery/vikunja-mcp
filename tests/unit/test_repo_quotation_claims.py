"""A prose claim that quotes a string as being IN this repo, made checkable — tracker #695.

WHAT IS BROKEN WITHOUT THIS FILE. This repo's prose quotes itself constantly, and until now
nothing anywhere checked that a quoted string a sentence asserts is IN the checkout actually is.
The failure is not hypothetical and it is not rare. VMCP-153 (656)'s own correcting commit
`889befd`, whose subject ends "correct six measured claims (tracker #656)", introduced a seventh:
a comment claiming two example strings were what this repo held, "each in test_api_kanban.py",
where the first of the two occurred nowhere outside the file making the claim. It shipped through
CI, through review, and past the sweep scanner whose own pattern is defined thirteen lines below
that comment.

THE FIRST DRAFT OF THAT SENTENCE MISQUOTED IT, and the slip is left recorded rather than quietly
fixed, because it prices the whole exercise. It read `titled "corrects six measured claims"` — one
letter, written from memory, describing a string this repository does not contain. Nothing here
would have caught it either: a commit SUBJECT is not carried by `git ls-files`, so the corpus this
scanner searches has never held it. Two things at once, then — that the class is easy to commit
even while writing the guard against it, and that this guard's reach ends at the working tree.

AND IT SHIPS GREEN, which is the card. Re-measured before this file existed, in a clone of
`d1af833` with `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, selection `tests/unit`,
`collected 890 items` every round: control 0 failed; a fabricated repo-content quotation planted
in a COMMENT run 0 failed; the same planted in a DOCSTRING 0 failed; both at once 0 failed. Four
greens, one of them the control.

WHY THE OBVIOUS RULE IS NOT THE RULE. "Every quoted string in prose must be found in the tree" was
measured before it was rejected — through THIS file's own extraction and oracle, with the trigger
and the two floors switched off, so it is re-runnable rather than remembered: on the tree this
commit shipped from, 2,975 violations out of 11,299 quotations, against the 15 the shipped rule
asks about. Those digits perish — a rebase onto two sibling landings moved them once and widening
the scan moved them again — so what a reader acts on is asserted instead, in
`test_the_claim_keyed_rule_still_looks_at_a_tiny_fraction_of_the_quotations`. Red on
arrival and mostly wrong, because this repo's prose quotes things that are deliberately NOT repo
strings — constructed mutants pinned at a regex's edges, hypothetical banners, error text from
docker and git, quotations of OTHER repositories and of card descriptions, and wordings quoted
BECAUSE they were just retracted. The rule cannot be about the quotation. It has to be about the
CLAIM.

WHAT THIS FILE ENFORCES. A short, named list of ASSERTIVE IDIOMS — `_CLAIM_TRIGGERS` — turns the
sentence carrying one into a claim about repo content, and every quotation in that sentence must
then occur somewhere in the tree OUTSIDE THE FILE doing the claiming. Whitespace is flattened on
both sides, and that is load-bearing rather than tidy — held by a ROUND and not by a count.
Against a control of 0 failed, searching the corpus RAW so that a line wrap in the tree is no
longer bridged is 1 failed: one true claim in this tree points at a phrase that is wrapped across
a line break at its site in test_api_kanban.py, and unbridged it reads as a fabrication. Two
neighbouring rounds say WHICH half does that work, and both are 0 failed against the same control:
making `_flat` a no-op, and dropping the inner `.split()` from both flatteners. The bridging comes
from joining STRIPPED LINES with a single space, not from collapsing runs of spaces — a
distinction worth having before anyone simplifies either function. (An earlier draft of this
paragraph carried an inherited raw-0/flattened-1 pair, taken from the card and never re-run; it
described an upper-case spelling of that same phrase, which on this tree is raw 1.) The corpus is
what `git ls-files` carries, so a stray untracked file in somebody's checkout cannot vouch for a
fabrication — the same hole VMCP-194 (724) closed on the markdown side of the sibling scanner,
where any `.md` in any checkout silenced an assert.

THE FILE IS THE UNIT AND THE PARAGRAPH WAS TRIED FIRST — the sharpest thing measured here, and it
inverts what the obvious design gives you. This scanner's first shape excluded only the claiming
PARAGRAPH, and it would have shipped green through `889befd`: the fabricated phrase occurred twice
in that one file, at line 88 in the sentence asserting it and at line 336 as a constructed row of
a test, so the phantom vouched for itself and the check on the founding defect was a false green.
A file arguing about a phrase quotes the phrase — that is what arguing about it consists of — so
the file discussing a phantom is the likeliest place on earth to hold a second copy. Measured
on this commit's tree, moving the unit from paragraph to file costs exactly ONE extra ratchet
entry, and that entry is that same phantom. `_occurs_elsewhere` carries the argument; the
two-copies row of `test_a_claim_is_never_evidence_for_itself` carries the pin.

WHERE THE TRIGGER LIST STOPS, and it is a measurement rather than taste. Taken on this commit's
tree by driving this file's own predicate with nothing swapped but `_CLAIM_TRIGGERS` — ten lines,
so RE-RUN it rather than trusting the table, which moves with every landing:
  * the containment family alone ..................... 5 sentences fire, 2 unverifiable
  * plus `verbatim in` and `word for word` .......... 19 sentences fire, 3 unverifiable
  * plus `occurs in` / `appears in` ................. 38 sentences fire, 7 unverifiable
  * plus `the exact string/phrase/wording` .......... 40 sentences fire, 8 unverifiable
The four extra false reds the third row buys are all one defect — the trigger is incidental
English, not an assertion: a hypothetical assert written as `"decompose" in section`, a paraphrase
of a pin that was rejected, and TWO of this file's own examples of the spelling it deliberately
does NOT read. Those two are the sharpest argument against widening, because they are
self-inflicted: adding `appears in` would redden the very paragraphs explaining why `appears in`
is excluded. So the list stops at the second row, and the three survivors there are grandfathered
by name in `UNVERIFIABLE_QUOTATION_CLAIMS` with a reason each. Widening it later is a decision
with a price, and the price is written down.

WHAT WIDENING DID NOT COST is measured the same way, because "we widened it" is worthless without
a number beside it. The independent second pass constructed sixteen fabrications this gate shipped
green; TEN of them are now red. The trigger grew an adverb slot, the tree and the checkout as
subjects, a passive form of the same verb, the locative twin of the verbatim idiom, and the
hyphenated spelling of the word-for-word one; the scan grew to every tracked `.py` plus this file
itself; the delimiters grew a curly pair. All of it at a measured cost of ZERO false reds — the
offender set stood at the same three entries before and after every step. SIX of the sixteen are
still green and each is named in the bullets below: a single-quoted fabrication, one written after
code on a line, one longer than the quotation cap, and two more verbs, which no list of spellings
ever ends. That is why those were taken and single quotes were not.

AND THIS PARAGRAPH IS WHY THE LIST IS NAMED DESCRIPTIVELY RATHER THAN QUOTED. Its first draft put
the new idioms in backticks; the gate went red on itself, because a sentence that both carries a
trigger and quotes one is a claim about a string that lives only in a regex. The scanner cannot
tell an example from an assertion, so prose about the trigger list must not quote the trigger list.
Measured, not reasoned: control 0 failed, that draft 1 failed, naming this very paragraph.

WHAT IT CANNOT ENFORCE, priced rather than rounded up, because a "does not catch" section that
oversells is worse than none.
  * IT IS KEYED ON A PHRASE LIST, SO IT REPORTS ON THE PHRASE LIST. VMCP-155 (660) put that
    exactly: a sweep bounded by a regex reports on the regex, not on the class. A fabrication
    written "the phrase X appears in test_api_kanban.py" is invisible here, and that spelling is
    OUT on purpose — including it costs three false reds on today's prose, measured above. This
    is the whole reason CLAUDE.md now names the checked idioms: the gate is only as wide as the
    author's vocabulary overlaps it, so the vocabulary is written down where authors read. The
    independent second pass built eight further spellings that ship green, and the trigger was
    widened to catch what could be caught for free — an adverb slot, `the tree`/`the checkout`
    as subjects, and the hyphenated form — at a measured cost of ZERO false reds. What is still
    open needs no list to describe: any verb that is not `contain`, and any sentence that asserts
    the location without asserting containment.
  * THE DELIMITER SET IS FOUR, NOT ALL OF THEM, and straight single quotes are the named miss.
    Measured, reading `'…'` costs 2 false reds on the spot, because an English possessive opens a
    span that closes at the next apostrophe, and the scanned prose holds 761 multi-word spans of
    that shape to draw more from. So a fabrication in single quotes is invisible. Constructed and
    confirmed by the second pass, along with the shape below.
  * A QUOTATION LONGER THAN `_QUOTATION`'s 300-CHARACTER CAP IS INVISIBLE. The cap keeps a span
    from running the length of a flattened paragraph; a 343-character fabrication was constructed
    against it and shipped green. Nothing here bounds how long a quoted phrase may be, so this is
    a hole with a number rather than a judgement.
  * IT READS COMMENT RUNS, NOT TRAILING COMMENTS. `_comment_runs` collects lines that BEGIN with
    `#`; a claim written after code on the same line is never extracted, and the scanned scope
    holds 730 such lines across 40 files. Reading them needs `tokenize` rather than a line test,
    since a `#` inside a string literal is not a comment — deliberately not done here, and named
    because the number is large enough that "comments are covered" would be false.
  * IT CHECKS PRESENCE, NEVER MEANING. VMCP-194 (724)'s defect passes it untouched: a string that
    IS in CLAUDE.md, quoted accurately, and glossed as agreeing with a conclusion the sentence it
    comes from contradicts. Every character verified, the claim still false.
  * IT CHECKS PRESENCE, NEVER LOCATION. The sentence may name a file; this file does not read it.
    The `889befd` defect is caught because that phrase was nowhere at all, not because the scanner
    knew where test_api_kanban.py was. Right string, wrong file is invisible. Deliberate: parsing
    the location out of a sentence is a second trigger list with a second false-red budget, and
    the measurement that would justify it has not been made.
  * IT READS QUOTATIONS, NEVER POINTERS. A bare `:1473`, a `VMCP-N (id)` pair, a sha, a line
    number — none of them is a quoted string, so none of them is in scope. That is the class of
    VMCP-155 (660) and VMCP-198 (735) and it stays open here; 735's remedy was to make the tool
    hand back the ref, which is a different kind of fix from a scanner.
  * THE CORPUS IS THE WORKING TREE AND NOTHING ELSE. Commit messages, card descriptions, review
    comments, another repository — a claim quoting any of them is unanswerable here, and one of
    the three grandfathered entries is exactly that (a heading out of a Vikunja card). The
    misquotation recorded at the top of this docstring is the same boundary met from inside.
  * THREE SHAPES ARE SKIPPED BY CONSTRUCTION, and they are NOT equally evidenced — the honest
    split matters more than the list. A quotation carrying an ellipsis or an `<angle placeholder>`
    cannot be looked up verbatim, and that exemption is held by a round: removing it is 2 failed,
    one of them a true elided claim in test_skill_contract.py it spares. The `_MIN_CHARS` floor is
    held by a round too, at 2 failed. The `_MIN_WORDS` floor is held by NOTHING: lowering it to 1
    is 0 failed everywhere, and it is kept for a shape this tree does not currently hold. All
    three are holes an author could hide a fabrication in, and the third is a hole whose removal
    nothing would notice.
  * A CLAIM SPLIT FROM ITS QUOTATION BY A SENTENCE BOUNDARY IS MISSED. The scope is one sentence,
    split on a terminator followed by an opening character, which is what keeps `0.0.0.0:3456`
    and `test_api_kanban.py.` from splitting. Write the trigger in one sentence and the
    quotations in the next and nothing fires. And the split is CRUDER than "sentence": the
    terminator class holds `;`, so one English sentence carrying a semicolon between the trigger
    and its quotation is cut in half and misses. Constructed by the second pass.
  * A TRUE CLAIM ABOUT A FILE NOT YET STAGED READS AS FALSE. The corpus is `git ls-files`, i.e.
    the INDEX, so a new file written but not `git add`ed is invisible: the same bytes on disk go
    from red to green on the `add` alone. Measured. This repo's flow is write, run `pytest`,
    commit — so that red lands on the ordinary path, and the fix is to stage the file, not to
    reword the sentence. It is the price of the untracked-file hole being closed in the other
    direction, and both directions are now stated.
  * NO FILE ANYWHERE CAN CITE A STRING WHOSE ONLY HOME IS THIS SCANNER. The corpus excludes this
    file for EVERY claimant, not only for claims written in it, so CLAUDE.md quoting one of the
    assert messages below goes red. Measured. That is a structurally larger cost than the
    same-file case listed above, it is live today, and the remedy is to quote something else or
    to add a ratchet entry.
  * A CODE FENCE IS NOT A FENCE HERE. `_paragraphs` reads markdown as blank-line-separated text,
    so a fenced example showing what the gate CATCHES — the natural way to document it — is read
    as a claim and goes red. Measured on CLAUDE.md. Documenting the rule by example is therefore
    not free, which is worth knowing before someone tries.
  * A STRING THIS REPO EMITS BUT NEVER STORES CONTIGUOUSLY IS UNVERIFIABLE. Most agent-facing copy
    here is assembled from adjacent literals with escaped quotes, so the runtime message exists in
    no file as one span; quoting it word for word is red even though the quotation is exactly
    right. Measured by the second pass on `next_task`'s WIP refusal.
  * IT ANSWERS ABOUT THE TREE, NOT ABOUT HISTORY. A wording that WAS in the repo and was removed
    reads exactly like one that never existed — that is the second grandfathered entry, and
    VMCP-195 (732)'s independent check ran into the same wall from the other side.
  * A PHANTOM QUOTED IN A SECOND FILE STILL VERIFIES. The file unit fixes self-corroboration
    within one file and stops there: two files discussing the same fabricated phrase are, to this
    scanner, a repository that carries it. That is the residual of exactly the mode 732's check
    hit, moved one level up rather than closed, and nothing lexical can close it — a copy made to
    discuss a phrase and a copy that IS the phrase are the same bytes.
  * THE FILE UNIT COSTS A REAL CASE, not only a theoretical one: a claim about a string that
    genuinely lives elsewhere in its OWN file cannot verify and must either point at another file
    or be named in the ratchet. Measured on this commit's tree — the paragraph unit and the file
    unit differ by exactly one entry, and that entry is the phantom — this repo holds no such
    claim, so the cost today is zero. "Zero today" is a fact about this tree, not a property.
  * THE `git ls-files` CORPUS IS NOT PINNED BY A ROUND. Against a control of 0 failed on this
    file, widening it to include untracked files is 0 failed: nothing in the tests notices,
    because the hole it opens needs somebody's dirty checkout to be visible at all. Constructing
    that state means writing a file into the repository root while sibling agents run there,
    which costs more than the pin is worth; it is recorded here instead of asserted, and the
    reasoning for the choice lives in `_tracked_text_files`.
  * NEITHER IS THE `.py`/`.md` EXTENSION SPLIT. Prose in any other tracked text — a `.toml`, a
    `.yml`, a `.sh` — is not scanned at all, and nothing here says so by failing.
"""
import ast
import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
SELF = Path(__file__).resolve()

# The probe is a FILESYSTEM fact, exactly as `test_repo_browser_isolation` argues at length: a
# missing `.git` means "not a checkout, the property does not apply", while git EXITING NON-ZERO
# inside a real checkout is a failure this file reports rather than skips. Reading the exit code
# for the skip is what would turn the skip into an off-switch, so it is never read for that.
_IS_GIT_CHECKOUT = (REPO_ROOT / ".git").exists()

requires_git_checkout = pytest.mark.skipif(
    not _IS_GIT_CHECKOUT,
    reason=(
        f"{REPO_ROOT} has no .git, so the set of files this repository CARRIES is unknowable "
        "here (a `git archive` extraction, a copied tree). 'Is this string in the repo' has no "
        "answer without it: the property is NOT APPLICABLE, not broken"
    ),
)

# THE ASSERTIVE IDIOMS. A sentence containing one of these is read as claiming that the strings it
# quotes are in this checkout. Each is here because it is an ASSERTION about content rather than
# ordinary English about location — see the measurement in the module docstring for what the next
# two candidates cost. `verbatim in` is deliberately the two-word form: on the tree this commit
# shipped from the bare word matches 45 sentences against this form's 3, and nearly all of the
# difference is prose about bytes being passed through unchanged. Both figures move with every
# landing; the RATIO is the argument, and it is an order of magnitude.
_CLAIM_TRIGGERS = re.compile(
    r"(?:this|the)\s+(?:repo(?:sitory)?|tree|checkout)\s+(?:\w+\s+){0,2}contains"
    r"|\bis contained in\b"
    r"|\bverbatim\s+(?:in|at)\b"
    r"|\bword[-\s]for[-\s]word\b",
    re.IGNORECASE,
)

# A SENTENCE BREAK on already-flattened prose: a terminator, whitespace, then something that opens
# a sentence. The lookahead is what keeps two very common shapes from splitting mid-claim —
# `0.0.0.0:3456` has no whitespace after its dots, and `test_api_kanban.py, the phrase` continues
# in lower case. `e.g. the` survives for the same reason. The cost is the opposite error: a
# sentence that genuinely ends before a lower-case word is not split, so a trigger reaches further
# than it should. That direction only ever ADDS candidates, and every added candidate that is not
# a repo string is visible as a red rather than as a silent miss.
_SENTENCE_BREAK = re.compile(r"(?<=[.;!?])\s+(?=[A-Z(«`\"—*\-]|\d)")

# A QUOTATION, in FOUR delimiters — and the fifth is left out on a measurement, not an oversight.
# Backticks are included even though they mostly hold identifiers, because a claim about a phrase
# is written with them often enough; `_MIN_CHARS`/`_MIN_WORDS` keep bare identifiers out rather
# than dropping the delimiter. Curly `“…”` was added after the independent second pass built a
# fabrication in them: measured over the whole scan it costs ZERO false reds, and the repo holds
# two such phrases. STRAIGHT SINGLE QUOTES ARE DELIBERATELY NOT READ, and the reason is
# apostrophes: an English possessive opens a span that closes at the next one, so `'s own first
# probe DID re-see 10 and 11 independently — recorded verbatim in the DESCRIPTION of` is what the
# delimiter actually captures. Measured, adding it is 2 false reds immediately and the scanned
# prose holds 761 multi-word single-quoted spans to draw more from. So a fabrication written in
# single quotes is invisible here — a real hole, taken knowingly, and named in the module
# docstring rather than left for the next audit to find.
_QUOTATION = re.compile(
    r'"([^"\n]{3,300})"' r"|«([^»\n]{3,300})»" r"|`([^`\n]{3,300})`" r"|“([^”\n]{3,300})”"
)

# NOT LOOKABLE UP VERBATIM, so not asked about: an elision, or an `<angle placeholder>` standing in
# for a value. Measured on this tree, this exemption spares exactly one true claim —
# test_skill_contract.py quoting a SKILL.md sentence with its middle elided — and it is a hole, in
# that a fabrication written with `…` in it is invisible.
_UNVERIFIABLE_BY_CONSTRUCTION = re.compile(r"…|\.\.\.|<[^>]{1,40}>")

# A quotation shorter than this is an identifier, a flag or a fragment, not a phrase somebody is
# asserting the presence of. Both floors apply: `_busy_timeout=5000` clears the character floor
# and fails the word floor, which is the shape that motivates having two.
_MIN_CHARS = 12
_MIN_WORDS = 2

# CLAIMS WHOSE QUOTATION IS NOT A TREE STRING AND IS NOT MEANT TO BE — the ratchet, compared for
# EQUALITY like the sibling scanner's, so an entry that stops being unverifiable has to leave in
# the same commit. THREE entries, covering the three classes the card filing this named as the
# hard part of the whole idea. None of them can be told from a fabrication lexically:
#   * test_api_kanban.py quotes a heading out of a Vikunja CARD's description, and the sentence
#     itself says so ("verbatim in the DESCRIPTION of VMCP-127 (608)"). The tracker is not the
#     tree; nothing in the checkout can confirm or deny it.
#   * test_mutation_sweep_contract.py quotes the wording `889befd` used BEFORE it was corrected.
#     A retraction necessarily reproduces a string that is no longer there, so a correction is
#     lexically identical to the defect it corrects.
#   * the same file quotes the PHANTOM ITSELF — the fabricated half of that claim. It is here
#     rather than absent because the FILE unit is what makes this scanner work at all (see
#     `_occurs_elsewhere`), and under that unit a phrase whose only two copies are the sentence
#     discussing it and a constructed row in the same file reads as absent. Which it is: the
#     surrounding prose says so in its own words. This entry is the founding defect of the card,
#     kept visible on purpose rather than tuned out.
# Adding an entry is allowed and is not a defeat: what it must carry is the REASON, in this
# comment, in the author's own words. Deleting the check's teeth is what the equality stops.
UNVERIFIABLE_QUOTATION_CLAIMS = frozenset({
    "tests/unit/test_api_kanban.py::comments-above"
    ":test_a_server_serving_MORE_than_it_stated_still_reads_the_board_whole"
    "::¶not cited from VMCP-108",
    "tests/unit/test_mutation_sweep_contract.py::comments-above:_docstrings"
    "::¶the exact strings this repo contains",
    "tests/unit/test_mutation_sweep_contract.py::comments-above:_docstrings"
    "::¶the control at the same call site",
})


def _flat(text: str) -> str:
    """Whitespace-flattened, which is how a line-wrapped repo string becomes findable at all."""
    return " ".join(text.split())


def _uncommented(text: str) -> str:
    """Flattened with each line's leading `#` dropped — a comment run's own wrapping removed.

    Two flattenings rather than one because a phrase wrapped inside a COMMENT flattens to
    `... control at # the same call site` under `_flat` and is then unfindable. Both are searched;
    neither alone covers docstrings and comment runs together.
    """
    return " ".join(
        " ".join(line.strip().lstrip("#").strip().split()) for line in text.splitlines()
    ).strip()


def _tracked_text_files():
    """(path, body) for every UTF-8 file `git ls-files` carries, minus this scanner's own source.

    TRACKED, not walked: "is this string in the repo" is a question about what a clone gets, and a
    filesystem walk answers it with whatever happens to be lying in the working directory. A
    scratch file holding the fabricated phrase would otherwise make the claim verify.

    THIS FILE IS EXCLUDED FROM THE CORPUS, AND ONLY FROM THE CORPUS.
    `UNVERIFIABLE_QUOTATION_CLAIMS` holds the quotations verbatim; with this file in the corpus
    each of those literals would be found "elsewhere in the tree" — in the ratchet list itself —
    and every entry would stop being an offender, emptying the very list that names them. So the
    scanner's own bookkeeping is not evidence.

    THE SCAN IS A DIFFERENT LIST, `_tracked_names`, and it was not always. This function used to
    feed both, so excluding `SELF` here silently excluded this file from being READ — while the
    paragraph you are reading claimed, in bold, that it was read. The independent second pass
    planted a fabricated repo-content claim in this module's own docstring and measured it GREEN.
    The claim was false, the file it was written in was the one file the scanner could not see,
    and nothing in the suite noticed. Two lists now, two reasons, and neither borrows the other's.

    The remaining cost of the corpus exclusion is real and is NOT the one that sentence described:
    no file anywhere can cite a string whose only home is this scanner, because for every claimant
    this file is missing from the corpus. CLAUDE.md documenting this gate cannot quote its own
    error messages back. That is priced in the module docstring rather than discovered later.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert listed.returncode == 0, (
        f"`git ls-files` failed inside a checkout that has a .git ({listed.stderr.strip()}). "
        "The corpus this scanner searches is undefined, so every claim would read as "
        "unverifiable — that is a broken checkout, not a prose defect"
    )
    for name in listed.stdout.split("\0"):
        if not name:
            continue
        path = REPO_ROOT / name
        if path.resolve() == SELF or not path.is_file():
            continue
        try:
            yield name, path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue


def _tracked_names():
    """Every path `git ls-files` carries, INCLUDING this scanner's own source.

    Separate from `_tracked_text_files` on purpose, and the separation is the fix for the worst
    thing the independent second pass found. That function filters `SELF` because the CORPUS must
    not contain the ratchet list's own literals; the SCAN was built on top of it and inherited the
    filter, so this file was never read — while its docstring claimed the opposite in bold. A
    fabricated claim planted in the scanner's own module docstring was measured GREEN. Two lists,
    two reasons, and neither borrows the other's.
    """
    listed = subprocess.run(
        ["git", "ls-files", "-z"], cwd=REPO_ROOT, capture_output=True, text=True
    )
    assert listed.returncode == 0, f"`git ls-files` failed: {listed.stderr.strip()}"
    return [name for name in listed.stdout.split("\0") if name and (REPO_ROOT / name).is_file()]


def _corpus():
    return [(name, _flat(body), _uncommented(body)) for name, body in _tracked_text_files()]


def _occurrences(needle: str, corpus) -> int:
    """How many times the tree carries this phrase, counting each file under its BEST flattening.

    `max` and not a sum, which is not a detail: a phrase inside a docstring is found by both
    flattenings, so summing would report every ordinary occurrence twice and the number would not
    be a count of anything. `max` reports 1 for the ordinary case and 1 for the comment-wrapped
    case that only `_uncommented` can see, which is what makes the subtraction below arithmetic
    rather than a coincidence that happens to stay positive.
    """
    flat = _flat(needle)
    if not flat:
        return 0
    return sum(max(body.count(flat), uncommented.count(flat)) for _, body, uncommented in corpus)


def _occurs_elsewhere(needle: str, own_file: str, corpus) -> int:
    """Occurrences in the tree OUTSIDE the file making the claim.

    Some exclusion is mandatory or the check is vacuous by construction: a claim quotes the
    string, the claim is in the tree, so every string every claim quotes is "in the tree".

    THE UNIT IS THE FILE, AND THE PARAGRAPH WAS MEASURED AND REJECTED — on the very defect this
    card was filed for. At `889befd` the fabricated half of that claim, `the control at the same
    call site`, occurred TWICE in test_mutation_sweep_contract.py and nowhere else: at line 88 in
    the comment asserting it, and at line 336 as a constructed row of the pattern test.
    Subtracting only the claiming PARAGRAPH leaves one occurrence standing, so the phantom
    verifies itself and this whole file would have shipped green through the case it exists for.
    That is not an edge: a discussion of a phrase quotes the phrase, so the file arguing about a
    phantom is exactly the file most likely to hold a second copy of it. VMCP-195 (732)'s
    independent check met the same mode from the other side and called it going SILENT on a
    phantom quoted more than once.

    WHAT THE FILE UNIT COSTS, measured on this commit's tree rather than estimated: exactly ONE
    additional entry against the paragraph unit — and it is that same `the control at the same call site`,
    now correctly named as a string this checkout does not carry outside the file discussing it.
    A claim about a phrase that genuinely lives elsewhere in its OWN file can no longer verify;
    there is no such claim in this tree today, and the remedy for one is to point at the other
    file or to name it in the ratchet.
    """
    flat = _flat(needle)
    return sum(
        max(body.count(flat), uncommented.count(flat))
        for name, body, uncommented in corpus
        if name != own_file
    )


def _docstrings(source: str):
    """Every docstring in a module, keyed by its dotted qualname.

    The recursion descends through EVERY node and not only through definitions, which is a fix
    the independent second pass earned by construction: a `def` nested inside a `with` (or an
    `if`, or a `try`) is not a child of the module, so a definition-only walk never reaches its
    docstring and a fabricated claim parked there shipped green.
    """
    def walk(node, prefix):
        for child in ast.iter_child_nodes(node):
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                qualname = f"{prefix}{child.name}"
                doc = ast.get_docstring(child, clean=False)
                if doc:
                    yield qualname, doc
                yield from walk(child, f"{qualname}.")
            else:
                yield from walk(child, prefix)

    tree = ast.parse(source)
    module_doc = ast.get_docstring(tree, clean=False)
    if module_doc:
        yield "<module>", module_doc
    yield from walk(tree, "")


def _comment_runs(source: str):
    """Every maximal run of `#` lines, keyed by the definition below it — the sibling's idiom.

    Keyed by the following definition and not by a line number for the reason
    `test_mutation_sweep_contract._comment_runs` measured: a line-number key breaks the ratchet on
    any edit above the run, which is every edit.
    """
    lines = source.splitlines()
    defs = sorted(
        (n.lineno, n.name)
        for n in ast.walk(ast.parse(source))
        if isinstance(n, (ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef))
    )

    def following(line_no: int) -> str:
        for lineno, name in defs:
            if lineno > line_no:
                return name
        return "<end of file>"

    run: list[str] = []
    start = 0
    for i, line in enumerate(lines, 1):
        if line.lstrip().startswith("#"):
            if not run:
                start = i
            run.append(line)
            continue
        if run:
            yield f"comments-above:{following(start)}", "\n".join(run)
            run = []
    if run:
        yield f"comments-above:{following(start)}", "\n".join(run)


def _paragraphs(prose: str):
    """A maximal run of non-blank lines, where a bare `#` counts as blank — the sibling's unit."""
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


def _prose_paragraphs():
    """(site, paragraph) over python prose in tests/ and src/, plus every tracked markdown file.

    The scan is WIDER than the sibling scanner's tests/-only scope, and that is affordable rather
    than brave: on this commit's tree src/ and markdown contribute ZERO unverifiable claims under
    this trigger list — every entry of the ratchet is under tests/ — so the width costs nothing
    today and covers the two places a repo-content claim is most likely to be written next, a tool
    docstring and CLAUDE.md. It is not free of teeth either: planting a fabrication in CLAUDE.md is
    1 failed against a control of 0 failed, which the sibling scanner's tests/-only scope misses.

    ONE COST OF THE WIDTH IS NOT PAID TODAY BUT IS REAL. `docs/superpowers/` is VENDORED prose — it
    talks about other repositories — and it is the largest markdown scope here. On this commit's
    tree the trigger fires in exactly four files, none of them under `docs/`, so the vendored text
    costs nothing now; a future vendor update could land a sentence that fires, and the answer then
    is a ratchet entry naming it, not a narrowing. Said here so that red is not a surprise.
    """
    for name in sorted(_tracked_names()):
        path = REPO_ROOT / name
        try:
            body = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        if name.endswith(".py"):
            try:
                records = list(_docstrings(body)) + list(_comment_runs(body))
            except SyntaxError:
                continue
            for key, text in records:
                for paragraph in _paragraphs(text):
                    yield f"{name}::{key}", paragraph
        elif name.endswith(".md"):
            for paragraph in _paragraphs(body):
                yield name, paragraph


def _quotations_a_claim_makes(paragraph: str):
    """Every quotation inside a sentence of this paragraph that carries an assertive idiom."""
    for sentence in _SENTENCE_BREAK.split(_uncommented(paragraph)):
        if not _CLAIM_TRIGGERS.search(sentence):
            continue
        for match in _QUOTATION.finditer(sentence):
            quoted = next(g for g in match.groups() if g is not None).strip()
            if len(quoted) < _MIN_CHARS or len(quoted.split()) < _MIN_WORDS:
                continue
            if _UNVERIFIABLE_BY_CONSTRUCTION.search(quoted):
                continue
            yield quoted


def _unverifiable_claims(corpus):
    """`site::¶quotation` for every claimed quotation this checkout does not carry."""
    for site, paragraph in _prose_paragraphs():
        own_file = site.split("::")[0]
        for quoted in _quotations_a_claim_makes(paragraph):
            if _occurs_elsewhere(quoted, own_file, corpus) <= 0:
                yield f"{site}::¶{quoted}"


@requires_git_checkout
def test_a_string_this_repo_says_it_contains_is_a_string_this_repo_contains():
    """The ratchet: every quoted string asserted to be here is here, or is named and explained.

    EQUALITY, not containment, and both directions carry weight — the sibling scanner's argument
    applies unchanged. A NEW unverifiable claim is the regression this card exists to stop. An
    entry that becomes verifiable (the prose is corrected, the string lands, the sentence is
    rewritten) must leave in the same commit, because this list is a public statement about which
    quotations a reader should not trust.

    MUTATION-CHECKED in an isolated `git clone --no-hardlinks`, module path confirmed inside the
    clone each round, `__pycache__` deleted and then `PYTHONDONTWRITEBYTECODE=1`, `--tb=no` so no
    traceback can feed a docstring's own numbers back to the reader, every round restored from a
    pristine COPY and refused unless its target matched exactly once. Selection: this file alone,
    `collected 14 items` every round. Control round: 0 failed. Each row gives the ROUND's failed
    count and then names which test died, because on this selection several rounds kill more than
    one and the count alone would hide which.
      * plant a fabricated claim in a COMMENT run of test_mutation_sweep_contract.py -> 2 failed,
        here and on the screen test's matching row. Plant the same shape in a DOCSTRING of
        test_api_kanban.py -> 2 failed, likewise. The SECOND death in each is an artefact worth
        naming rather than hiding: the screen rows assert their fabrications are absent from the
        checkout, so planting one into the tree falsifies the row for a different reason than it
        falsifies this list. Both plants were 0 failed over the WHOLE of tests/unit before this
        file existed, which is the card
      * plant one in CLAUDE.md, a markdown paragraph -> 1 failed, here: what the widened scan buys
      * drop `SELF` from the corpus exclusion, so this file's own ratchet list counts as evidence
        -> 4 failed: here, on the scope test, and on both fabrication rows of the screen test,
        which then find their own literals. Every grandfathered entry stops being an offender at
        once. The self-reference this repo keeps stepping on, as a round rather than a worry
      * drop the exclusion in `_occurs_elsewhere`, so a claim vouches for itself -> 2 failed, here
        and on `test_a_claim_is_never_evidence_for_itself`
      * empty `UNVERIFIABLE_QUOTATION_CLAIMS` -> 1 failed, here; add a non-existent entry
        -> 1 failed, here
      * PAIRED, because each half alone looks innocent: drop the exclusion AND empty the list
        -> 1 failed, and NOT here — this assert goes green, because the scan is vacuous and the
        list agrees with it. The one death is `test_a_claim_is_never_evidence_for_itself`, which
        is the reason that test is separate from this one
      * revert the tree scan to the claiming PARAGRAPH as the unit -> 1 failed, HERE and here
        alone. The third ratchet entry stops being an offender, because that phantom's second
        copy sits in the same file. The oracle pin does not see it: it asks `_occurs_elsewhere`
        directly and that function was not touched. Two tests, two different halves of the unit
      * drop `this repo contains` from `_CLAIM_TRIGGERS` -> 2 failed, here and on one row of the
        screen test. Drop `verbatim in` -> 1 failed, here ALONE, the screen test surviving because
        its fabrication rows carry a second trigger each. Drop `word for word` -> 1 failed, on the
        screen test ALONE, this assert surviving because no ratchet entry rests on that idiom.
        The three triggers are held by different tests and no one test holds all three
      * make `_UNVERIFIABLE_BY_CONSTRUCTION` match nothing -> 2 failed, here and on the ELISION
        row only: the exemption spares a real elided claim in test_skill_contract.py, so removing
        it adds a fourth offender to this list. The placeholder row survives, because
        `git show <rev>:<path>` is a string this checkout really carries once it is looked up
      * `_MIN_CHARS = 4` -> 2 failed, here and on the screen test's short-quotation row: lowering
        the floor pulls a fragment into the tree scan as well
      * make `_SENTENCE_BREAK` never split, so a trigger reaches its whole paragraph -> 1 failed,
        here. The sentence unit is load-bearing on today's prose and not only in the false-red
        budget the module docstring measures
      * `_MIN_WORDS = 1` -> 1 failed, here — a knob that was 0 failed everywhere until the scan
        widened, which is why the widening is a round of its own and not a tidy-up
      * make the SCAN reuse the corpus builder, so it inherits the `SELF` exclusion -> 1 failed,
        on the scope test. That is the shape the independent second pass found shipped: a
        fabricated claim in this file's own docstring, green, while the docstring said otherwise
      * read only `tests/` python instead of every tracked `.py` -> 1 failed, on the scope test
      * `_occurrences` searching only the raw flattening -> 1 failed, and NOT here: on the oracle
        pin alone. No claim in this tree needs the comment flattening to verify, so the tree scan
        is blind to that half — which is why the oracle pin asserts it directly
      * widen the corpus AND the scan to untracked files -> 0 failed anywhere; that choice is
        argued in the module docstring and pinned by nothing, which is said there too
    """
    unverifiable = sorted(_unverifiable_claims(_corpus()))
    assert unverifiable == sorted(UNVERIFIABLE_QUOTATION_CLAIMS), (
        "the set of quotations this repo's prose ASSERTS it contains, but does not, has moved.\n"
        f"  found:   {unverifiable}\n"
        f"  grandfathered: {sorted(UNVERIFIABLE_QUOTATION_CLAIMS)}\n"
        "A NEW entry means a sentence claims a string is in this checkout and it is not — either "
        "the quotation is wrong (fix the prose: `git grep -F` the flattened phrase) or the "
        "quotation was never meant to be a repo string (a card description, another repo, a tool's "
        "output, a wording quoted because it was retracted), in which case add it here WITH ITS "
        "REASON in the comment above.\n"
        "A MISSING entry has TWO causes and only one of them means delete it. The claim may have "
        "become verifiable — the prose was corrected, or the string landed — and then the entry "
        "misinforms and goes. But a phantom also stops being an offender the moment ANY OTHER "
        "tracked file quotes it, including a file merely discussing the mistake, because this "
        "scanner reads bytes and cannot tell a copy that IS the phrase from a copy ABOUT it. "
        "`git grep -F` the phrase first: if its only new home is prose describing the defect, the "
        "entry is still true and what you have found is this scanner's limit, not a fix. Do not "
        "delete the record of a defect because someone wrote about it."
    )


@pytest.mark.parametrize(
    "sentence, flagged, why",
    [
        # THE REPRODUCTION, in the two shapes measured green before this file existed.
        (
            'verbatim in test_api_kanban.py, the phrase "a wholly invented baseline clause" is '
            "one of the strings this repo contains",
            True,
            "the planted comment-run fabrication",
        ),
        (
            'the phrase "an utterly imaginary paging preamble" occurs word for word in '
            "test_workflow_gates.py",
            True,
            "the planted docstring fabrication, under a different trigger",
        ),
        (
            'both examples are strings this repo really contains, "a fabricated baseline clause '
            'nobody wrote" and "control: page 1"',
            True,
            "889befd's own shape: two quotations, one real, one not",
        ),
        # THE OTHER SIDE. Ordinary prose that quotes without asserting must stay green.
        (
            'the drain refuses with "Bind for 0.0.0.0:3456 failed" when a sibling holds the port',
            False,
            "a quoted error message, no trigger",
        ),
        (
            'a reviewer might write "this looks fine to me" and mean nothing by it',
            False,
            "a hypothetical utterance, no trigger",
        ),
        (
            'the mutant "отказывает только из Review" is constructed, not quoted',
            False,
            "a constructed mutant, no trigger",
        ),
        (
            "`git show <rev>:<path>` settles it word for word",
            False,
            "a trigger, but the quotation is an angle placeholder",
        ),
        (
            'the sentence «ветка предложения … назначенные на тебя» occurs verbatim in step 3',
            False,
            "a trigger, but the quotation is elided",
        ),
        (
            "the flag `--isolated` appears word for word in the launch line",
            False,
            "a trigger, but the quotation is one short identifier",
        ),
        (
            'the note says "a fake bit" word for word',
            False,
            "a trigger, but the quotation is under the character floor",
        ),
    ],
)
def test_the_screen_reads_an_assertion_and_not_merely_a_quotation(sentence, flagged, why):
    """The predicate itself, driven by construction — the half a tree scan can never hold.

    A scan over the tree passes trivially when nothing in the tree fires, so on its own it cannot
    tell a working screen from a broken one. These rows are the screen's behaviour stated
    independently of what the repo happens to contain today: three fabrications it must flag,
    including the exact two plants that shipped green before this file, and six pieces of ordinary
    prose it must not. The negative rows are not decoration — the naive rule measured 2,160
    violations on this repo's real prose, and every one of these six shapes is drawn from that set.

    The corpus here is the REAL one and the claiming file is a name no tracked file has, so a row
    asserting `flagged` is asserting the quotation is absent from the whole checkout as well. That
    is deliberate: a row that only exercised a regex would go stale the moment the tree gained the
    phrase, and would say nothing about the oracle it is paired with.

    MUTATION-CHECKED, same discipline and stand as the ratchet, this file alone, `collected 13
    items` every round, control 0 failed. Rows are numbered as listed above.
      * drop `this repo contains` from `_CLAIM_TRIGGERS` -> 2 failed, ONE of them here: row 3
        only. Row 1 survives because it carries `verbatim in` as well, which is why the round is
        not the two rows a reader would predict from the trigger's spelling
      * drop `word for word` -> 1 failed, here: row 2
      * drop `verbatim in` -> 1 failed, and NOT here. Every fabrication row carries a second
        trigger, so this test cannot see that idiom go; the ratchet can, because its
        `not cited from VMCP-108` entry is the tree's only `verbatim in` claim
      * make `_UNVERIFIABLE_BY_CONSTRUCTION` match nothing -> 2 failed, ONE of them here: the
        ELISION row. The placeholder row survives, and the reason is worth knowing before trusting
        that exemption — `git show <rev>:<path>` is a phrase this checkout really carries, so with
        the exemption gone it simply verifies. That row pins the exemption's INTENT, not its
        necessity, and only the elision row kills it
      * `_MIN_CHARS = 4` -> 2 failed, ONE of them here: the short-quotation row. The identifier row
        survives on the word floor alone
      * `_MIN_WORDS = 1` -> 1 failed, and NOT here: on the ratchet. It was 0 failed everywhere
        until the scan widened to every tracked `.py` and to this file, which is worth recording
        as its own small lesson — a knob can look unpinned only because the scan was too narrow to
        reach anything it holds. Its own row here is still `--isolated`, which the CHARACTER floor
        also stops, so this test never sees the word floor go
      * `_SENTENCE_BREAK` never splits -> 1 failed, and not here: every row is one sentence, so
        this test is blind to the unit by construction and the ratchet is what holds it
    """
    quotations = list(_quotations_a_claim_makes(sentence))
    corpus = _corpus()
    unverifiable = [q for q in quotations if _occurs_elsewhere(q, "<constructed>", corpus) <= 0]
    assert bool(unverifiable) is flagged, (
        f"{why}: expected flagged={flagged}, got {unverifiable or 'nothing'} "
        f"(quotations the screen read as claimed: {quotations})"
    )


def test_a_claim_is_never_evidence_for_itself():
    """The exclusion, pinned on a synthetic corpus so no change to the tree or the list moves it.

    This is the test that kills the paired mutation the ratchet cannot see. Drop the exclusion and
    every claim verifies against its own text; empty the ratchet list in the same commit and the
    tree scan is green with a scanner that measures nothing — measured against a control of
    0 failed on this file, that pair is 1 failed and this is the one that dies. Here the corpus is
    supplied, so neither the tree nor the list can absorb the change.

    THE SECOND HALF IS THE ONE THE CARD IS ABOUT. A phantom under discussion gets quoted twice in
    the file discussing it — the sentence asserting it, and a constructed row demonstrating it —
    and at `889befd` that is exactly the shape the founding defect had. Two copies in ONE file are
    not two witnesses, and the row below is what forbids reading them as such.

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * `_occurs_elsewhere` drops its `name != own_file` filter -> 2 failed, here and on the
        ratchet. PAIRED with emptying the ratchet list -> 1 failed, here ALONE: that is the whole
        reason this test is not folded into the ratchet
      * the tree scan reverts to the claiming PARAGRAPH as its unit -> 1 failed, on the RATCHET
        alone. This test does not see it, because it pins `_occurs_elsewhere` and that function
        was not touched — the two tests hold different halves of the same decision, and neither
        is redundant
      * `_occurrences` searches only the raw flattening -> 1 failed, HERE alone, on the
        comment-wrapped row. The ratchet is green: no claim in this tree needs that flattening to
        verify today, so without this row that half of `_occurrences` would be unheld
    """
    phrase = "a phrase that lives in exactly one file"
    claim = f'the string "{phrase}" occurs word for word in this repository'
    corpus = [("only.py", _flat(claim), _uncommented(claim))]

    assert _occurrences(phrase, corpus) == 1, "the corpus must hold the claim itself"
    assert _occurs_elsewhere(phrase, "only.py", corpus) == 0, (
        "a claim quoting a string is not evidence that the string is anywhere else. Without the "
        "exclusion every claim in this repo verifies against its own text and the scan is vacuous"
    )
    assert _occurs_elsewhere(phrase, "somewhere-else.py", corpus) == 1, (
        "asked from another file the same phrase IS found — so the zero above is the exclusion "
        "working, not the corpus being empty"
    )

    discussed = f"{claim}\n\nand a constructed row quoting '{phrase}' to show the shape"
    twice = [("discussion.py", _flat(discussed), _uncommented(discussed))]
    assert _occurrences(phrase, twice) == 2, "the discussion must hold two copies"
    assert _occurs_elsewhere(phrase, "discussion.py", twice) == 0, (
        "two copies inside ONE file are not two witnesses. A file arguing about a phantom quotes "
        "it more than once by nature, so a rule that subtracts only the claiming PARAGRAPH leaves "
        "the second copy standing and the phantom verifies itself — measured on `889befd`, where "
        "that is exactly how the defect this file exists for would have shipped green"
    )

    wrapped = "# the string spans\n# a comment line break here"
    needle = "spans a comment line break"
    wrapped_corpus = [("wrapped.py", _flat(wrapped), _uncommented(wrapped))]
    assert _occurrences(needle, wrapped_corpus) == 1, (
        "a phrase wrapped across a COMMENT's line break is findable only once the `#` markers are "
        "dropped — `_flat` alone leaves a `#` in the middle of it and reports the phrase absent"
    )
    assert _occurs_elsewhere(needle, "elsewhere.py", wrapped_corpus) == 1, (
        "and it must stay findable when the question comes from another file, or every claim "
        "about a phrase wrapped inside a comment run reads as a fabrication"
    )


@requires_git_checkout
def test_the_claim_keyed_rule_still_looks_at_a_tiny_fraction_of_the_quotations():
    """The design's central figure, as a PROPERTY — because as a number it rots every landing.

    The module docstring rejects "every quoted string must be in the tree" on a violation count,
    and that count is honest but perishable: re-measured across a single rebase onto two sibling
    landings it moved from 2,879 of 10,928 to 2,891 of 11,095. What a reader ACTS on is not the
    digit, it is the RATIO — the claim-keyed rule adjudicates a tiny fraction of what the naive
    one would — so that is asserted here and the digits stay in the prose as history.

    The violation count itself is deliberately NOT asserted: computing it costs 26 s against 0.7 s
    for this, both timed here, and `lint-and-unit` is what sets a run's length — 38-46 s, a figure
    this file takes from CLAUDE.md's release section rather than re-measuring — so a 26-second
    assert would be paid on every landing to restate what the ratio already says.

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * `_CLAIM_TRIGGERS` matches every sentence -> 3 failed: here, on the ratchet (whose offender
        set becomes the naive one, hundreds of entries) and on ONE row of the screen test — only
        one, because the other negative rows quote strings this checkout really carries
      * the threshold raised past the measured ratio -> 1 failed, here. Two rounds, because the
        first attempt used the measured value itself and was 0 failed, because quotations over
        claimed does not divide evenly and `> floor(ratio)` is still true by one. At the next
        integer up, and at 5000, it is 1 failed.
        The headroom is real and the off-by-one is recorded rather than smoothed over — a round
        that reads 0 failed for an arithmetic reason looks exactly like a threshold with slack
    """
    all_quotations, claimed_quotations = 0, 0
    for _site, paragraph in _prose_paragraphs():
        all_quotations += sum(1 for _ in _QUOTATION.finditer(_uncommented(paragraph)))
        claimed_quotations += sum(1 for _ in _quotations_a_claim_makes(paragraph))
    assert all_quotations > 100 * claimed_quotations, (
        f"this repo's prose holds {all_quotations} quotations and the claim-keyed rule asks about "
        f"{claimed_quotations} of them — no longer two orders of magnitude apart. The module "
        "docstring rejects the naive rule on exactly that gap, so either the trigger list has "
        "widened far past what was measured, or this repo has stopped quoting things it does not "
        "contain. Both are worth knowing before trusting the WHY THE OBVIOUS RULE section"
    )


@requires_git_checkout
def test_the_scan_reaches_every_scope_it_says_it_covers():
    """The scan's REACH, asserted — because on today's tree nothing else notices it shrinking.

    This exists because two mutations were measured and BOTH were silent. From a control of
    0 failed on this file, before this test was written: making the scan skip markdown entirely
    -> 0 failed, and making it skip src/ -> 0 failed. Neither scope holds an unverifiable claim
    today, so the ratchet is identical with them and without them, and a narrowing would ship
    green — the same shape VMCP-194 (724) caught on the sibling scanner, where a guard was
    satisfied by material other than the material it named. A scan that silently stops reading a
    scope is worse than one that never read it, because the module docstring says the scope is
    covered.

    It also pins the corpus exclusion from the other side: the scanner's own source must NOT be
    in the corpus, which is what stops its ratchet list from vouching for itself.

    IT ASKS ABOUT PYTHON PROSE BY EXTENSION, and the first spelling did not — it asked only that
    some site start with `src/`, and from a control of 0 failed the round meant to kill it came
    back 0 failed. The reason is a file this repo really has:
    `src/vikunja_mcp/skills/tracker/SKILL.md` is tracked markdown living under `src/`, so it
    satisfied a `src/` prefix while every docstring and comment in the package had gone. A guard
    answered by material other than the material it names is the exact shape VMCP-194 (724) caught
    next door, met here by construction rather than by reading.

    MUTATION-CHECKED, this file alone, `collected 14 items`, control 0 failed:
      * the scan skips markdown -> 1 failed, here (0 failed before this test existed)
      * the scan skips PYTHON prose under src/ -> 1 failed, here. Under the prefix-only spelling
        the identical mutation was 0 failed, SKILL.md standing in for the whole package
      * `SELF` is not excluded from the corpus -> 4 failed, here, on the ratchet and on both
        fabrication rows of the screen test, which then find their own literals in the corpus
    """
    sites = [site for site, _ in _prose_paragraphs()]
    files = {site.split("::")[0] for site in sites}
    for prefix, what in (
        ("tests/", "the test suite's own prose"),
        ("src/", "the package's docstrings and comments"),
        ("scripts/", "the release helpers, which are tracked python outside both"),
    ):
        assert any(
            site.startswith(prefix) and site.split("::")[0].endswith(".py") for site in sites
        ), (
            f"the scan reaches no PYTHON paragraph under {prefix} ({what}), which the module "
            "docstring says it covers. The `.py` conjunct is load-bearing: SKILL.md is tracked "
            "markdown under `src/`, and without it this assert passes with the whole package's "
            "prose unread. Nothing else in this file notices either — no scope here holds an "
            "unverifiable claim today, so the ratchet is identical with the scope and without it"
        )
    assert "CLAUDE.md" in files, (
        "the scan reaches no CLAUDE.md paragraph, so the markdown half of the scope is off. That "
        "half is where the rule itself is written and where the next repo-content claim is most "
        "likely to be made"
    )
    assert SELF.relative_to(REPO_ROOT).as_posix() in files, (
        "THIS FILE IS NOT BEING SCANNED, which is how it shipped once: the scan was built on the "
        "corpus builder and inherited its `SELF` exclusion, so a fabricated repo-content claim "
        "planted in this very module's docstring was measured green while the docstring claimed "
        "in bold that it was read. The corpus excludes this file; the scan must not"
    )

    corpus_names = {name for name, _, _ in _corpus()}
    assert corpus_names, "the corpus is empty; every claim would read as unverifiable"
    assert SELF.relative_to(REPO_ROOT).as_posix() not in corpus_names, (
        "this scanner's own source is in the corpus, so the quotations its ratchet list holds "
        "verbatim count as evidence that the tree carries them — the list would then vouch for "
        "itself and empty out. See `_tracked_text_files`"
    )
