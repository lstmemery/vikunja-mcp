"""A figure anchored to a SHA must name a commit this history really holds — tracker #699.

WHAT IS BROKEN WITHOUT THIS FILE. A measured count written into prose goes stale between the
measurement and the push, and the re-run SKILL.md prescribes after a rebase structurally cannot
see it: no test asserts a number that lives in a comment. VMCP-153 (656) shipped its own "22
records" at `93714d5` when the true value there was 21, because the sibling `197b1e3` landed in
between; the author re-ran the readiness criteria after rebasing, correctly got a green suite, and
shipped a false number anyway. Reproduced here before anything was designed, under the RECORD unit
that sentence was written in — not the paragraph unit, which is a different ruler and gives
different numbers — with each tree extracted by `git archive` and `__pycache__` cleared: 22 at
`889befd`, 21 at `93714d5`, 22 at `6dd2803`, 22 at `bba4fed`, 22 at `75a1e52`, 24 at `c1c2619`.
Those six are deliberately written in the idiom this file gates, so the gate checks them. The
shipped value is a one-commit DIP in that trajectory, which is why "re-run it yourself later" is
not a remedy for an absolute: no later run reproduces the tree it was true at.

VMCP-167 (688) answered that class with a rule, and this file is the teeth it did not have: a
count over the whole tree gets an ASSERT if a reader acts on it, a SHA if it is history, and never
a date. The rule is stated in CLAUDE.md's Testing Philosophy and in the module docstring of
test_mutation_sweep_contract.py, and 688 applied it BY HAND to that file's counts. Nothing checked
the result. A sha anchor is itself a claim — that some named commit contains the tree the figure
was taken on — and an anchor that does not resolve is worse than no anchor, because it looks
verified. This repo has already shipped both failure modes: `1761` "belongs to no committed tree
in the window it could have been taken in" (it was measured in an uncommitted working tree), and a
card reference composed from memory named a live unrelated card. What this file adds is that the
anchor half is now mechanical.

WHAT IT CHECKS, and it is narrower than the card's title. It reads the LABEL, never the FIGURE.
`22 at `bba4fed`` passes here whether the true value at `bba4fed` is 22 or 30 — all this asserts
is that a reader who wants to check can, because the tree is named and reachable. That is a
deliberate stopping point rather than an omission, and the alternative was priced rather than
dismissed: see WHY NOT DERIVE THE NUMBER below.

WHY THE TRIGGER IS THE WORD `at` AND NOT "a number near a hex token". Three candidate triggers
were measured over the checkout at `c1c2619` before this one was chosen, and all three are red on
arrival with false positives that are ordinary correct prose:
  * spelled-out numbers next to a counting noun ("one paragraph", "two records") — 43 paragraphs
    match, 36 carry no sha, and almost none of them are measurements at all.
  * digits next to a counting noun — 6 paragraphs carry no sha, one of them matching the string
    `688 record`, which is a CARD NUMBER.
  * every backticked 7-to-40-character hex token — 49 distinct, of which 6 fail a
    resolve-and-ancestor check and all 6 are legitimate: three are MAGIC BYTES quoted by
    test_repo_browser_isolation (`89504e470d0a1a0a` PNG, `ffd8ffe0` JPEG, `255044462d` for
    `%PDF-`), and three are commits in docs/superpowers/specs that the document quotes precisely
    BECAUSE a rebase orphaned them.
That is the same lesson VMCP-141 (629) reached about file extensions and VMCP-171 (695) reaches
about quoted strings: the token does not decide what the token means. The anchor idiom does. Over
the same checkout, and with THE RULER THIS FILE ACTUALLY SHIPS, `at `<hex>`` matches 42
occurrences of 15 distinct shas in 4 files — 34 of them in test_mutation_sweep_contract.py, where
the idiom lives, 3 in CLAUDE.md, 3 in that same docs/superpowers spec and 2 in
test_workflow_sequence_gate.py — and ZERO of them fail. That emphasis is not decoration: this
sentence first said 40 and 32, because the census script that produced it was written WITHOUT
`re.IGNORECASE` while `_ANCHOR` carries it. The two extra are sentence-initial capitals — two
comment lines opening `# At `<sha>`` — which the shipped pattern reads and the census did not. An
independent pass found it, and it is the sharpest single lesson available here: an anchor makes a
figure CHECKABLE, not TRUE, and a figure measured with a different ruler is wrong at its own sha.
Both false-positive classes above are excluded by the idiom as this repo writes it today rather
than by an exclusion list: no line here puts the anchor preposition in front of a magic-byte
literal, and the spec's orphans are written as "`e3d5be6` still resolves" and
"(`9ec979f` -> `16821e9`", never with the preposition. "As this repo writes it today" is the
honest scope — constructed, `A PNG stream starts at `<the PNG signature>`` IS flagged, so the
exclusion is a fact about the prose, not a property of the pattern.
That sentence is deliberately DESCRIPTIVE where its first draft was literal, and the reason is the
first thing this gate ever caught: written out, the example is itself an anchor to a tree that
does not exist, and the scan named this file on its first run. The same applies to the rounds
recorded below — a fabricated sha is described, never spelled. A guard whose own prose cannot
survive it is not a guard, and this one caught its author before it caught anyone else.

WHAT IT THEREFORE MISSES, stated flatly because a guard oversold is worse than one described. Most
hex tokens here are not written in the idiom at all — 34 distinct at `c1c2619`, against the 15
that are — and it says nothing about any of them. That figure carries a sha rather than standing
in the present tense for a reason this file is obliged to take its own medicine over: the landing
that adds this file adds anchors of its own, so a bare "in this repo" would be falsified by the
commit that wrote it. It also read 43 until an independent pass checked it, and HOW it was wrong
is worth keeping: 43 is 49 minus 6, the count of tokens that PASS a resolve-and-ancestor check —
a different quantity that happens to sit one paragraph above. A figure anchored as "the tree
`abc1234` carries" is unchecked, and so is every other preposition — "measured on", "as of",
"in", a bare parenthesis, and `at commit `<sha>``, where the intervening noun defeats the pattern.
A figure with NO anchor at all is
unchecked, and cannot be checked here: that is the class the three measurements above show is not
lexically separable from ordinary prose. And a resolvable ancestor that is simply the WRONG tree
for the figure passes — the label is checked, the arithmetic is not; constructed, a figure
anchored to a commit predating the file it describes is green.

AND THE CHECK ONLY EVER LOOSENS AS HISTORY MOVES, which an independent pass built rather than
argued. A sha that is not an ancestor YET — a sibling commit, an unmerged branch — is red today
and GREEN tomorrow with the prose byte-identical, because merging or rebasing makes it an
ancestor. So a red here is a prompt to re-measure, not a latch: an author who waits it out ships
the same stale figure. The direction that matters is still covered — your own pre-rebase commit
goes red at the moment you rebase, which is before the push rather than after it — but "it
protects its author" holds in one direction and not in the other, and the other is the commoner
shape under a 3-wide drain.

WHAT IT COSTS, which is not nothing and is a different thing from what it misses. Prose that
DELIBERATELY discusses an unreachable commit is correct prose, and if it happens to use the
preposition this gate calls it a defect. There is a live example of exactly that subject matter in
the tree — docs/superpowers/specs' write-up of a rebase orphaning a commit — and it passes only
because its three mentions are spelled "`e3d5be6` still resolves" and "(`9ec979f` -> `16821e9`"
rather than with the preposition. That is luck rather than design, so the failure message names
the escape explicitly instead of leaving an author to guess it. The alternative was an exclusion
list keyed on path or on sha, and it is worse for the reason the module's whole trigger argument
gives: a list has to be maintained by whoever next writes such a paragraph, and they are the one
person who will not know it exists. A message that says what to do is cheaper than a list nobody
prunes. Re-run on the rebased tree before pushing, every round below reproduced unchanged, which
is the one property that mattered — this card's own subject is a figure that moved under a rebase.
  AND THE COST HAS A SECOND FACE, found by construction rather than by listing: a PLACEHOLDER sha
in documentation. This repo ships a `--at <sha>` flag and describes its refusals, and prose
explaining that flag with a concrete example — a made-up seven-hex tree after the preposition —
is flagged, measured, `control 0 failed; mutation 1 failed`. That is unavoidable prose rather
than a wording slip, and it is the strongest argument anyone has against this gate. It is
accepted here because the collision needs BOTH a fabricated sha AND the preposition, the message
names the escape, and the alternative — teaching the pattern to recognise "this one is an
example" — is the same undecidable problem the three rejected triggers already failed at.
  ONE MORE, so nobody has to rediscover it: the walk asks git what it would PUBLISH, so an
untracked but UNIGNORED note in the worktree is scanned. That is deliberate (see `_prose_files`),
and it means a scratch file at the repo root holding an orphaned sha will redden the pre-push
run. Put scratch where `.gitignore` already covers it, or outside the checkout, which is what
SKILL.md asks for anyway.

WHY NOT DERIVE THE NUMBER (the card's shape 2), priced rather than argued. Having a test recompute
the count and assert the prose matches WOULD have caught 656's defect. Its cost was measured by
walking every one of the 123 commits from `889befd` to `c1c2619`, extracting each tree with `git
archive` and re-running the count: it moved at SIX of them — `197b1e3` 22->21, `72c6879` 21->22,
`690d648` 22->23, `6dd2803` 23->22, `2ae1aa2` 22->23, `e77b0cf` 23->24. SIXTY-ONE of those 123
commits are the release bot's version bumps and move nothing, so the rate over real landings is 6
in 62, about one in ten. (The split read 62/61 in the first draft — off by one in both halves,
found by an independent pass re-running `git log --format=%s | grep -c '^chore: v'`. The
conclusion is unchanged, which is exactly why nobody would have caught it by reading.)
Under a 3-wide drain that is roughly one unrelated card per ten forced to
edit one hot file to keep a number true, and two siblings editing the same comment line is a
conflict. The rejected shape is recorded with its number rather than with an adjective, because
the number is what makes it a trade rather than a preference.

AND WHY THIS IS NOT A TAUTOLOGY, which the naive version of shape 2 is — constructed and measured
rather than reasoned, in a separate `git clone --no-hardlinks` with one sibling-shaped paragraph
planted into an unrelated test file. Control round on the unplanted clone: control 0 failed, and
the derived value there is 24; under the plant it is 25. SHAPE A, a check that recomputes the
figure and compares it against a recomputation, is 0 failed BOTH before and after — it agrees with
itself by construction, and the value moving underneath it is invisible. SHAPE B, the same
recomputation compared against a literal written in prose, is 0 failed before and 1 failed after:
a real check, and really red on a landing that has nothing to do with it, which is the six-in-61
cost above wearing its other face. THIS gate, run on that same planted tree, is 0 failed. It never
computes a figure at all, comparing PROSE against GIT — two sources that cannot move together — so
a sibling landing cannot turn it red, and narrowing the ruler cannot turn it quietly green either,
which is what the constructed pattern test below is for.

AND IT PROTECTS ITS OWN AUTHOR, which is the property the card asks for and the reason this is an
ancestor check and not merely an existence check. Rebasing before the push is mandatory here, so a
figure anchored to your own not-yet-pushed commit is orphaned by the very step that precedes the
push: the sha still RESOLVES and stops being an ancestor. Constructed and measured below. That is
ADJACENT to the `1761` case rather than the same one, and the two are worth keeping apart: there
the figure was measured in an UNCOMMITTED tree, so there was no sha to write down at all and this
gate would have had nothing to read. What it covers is the step after — the moment an author does
commit and does anchor, a rebase makes a stale anchor say so out loud.

WHERE IT RUNS. Locally always, including from a linked worktree, which shares the main checkout's
objects. In CI it needs history that `actions/checkout` does not fetch by default: measured on a
`--depth 1` clone, `rev-list --count HEAD` is 1 and NOT ONE anchor resolves. This gate then SKIPS
— it goes quietly green, which is the worse of the two outcomes and is why the job is configured
for full history. An earlier draft of this sentence claimed the opposite, that a shallow run would
"report fifteen fabrications"; that was true of a design without the skip branch and false of the
code beside it, and it survived until an independent pass ran a shallow clone instead of reading
the paragraph. The skip is still right — a shallow checkout genuinely cannot answer, and
accusing fifteen honest anchors of being fabrications is not a better failure — so what carries
the weight is the SECOND test here, which reads the workflow as TEXT and needs no git: two facts
from two sources, so a shallow checkout can silence the one that needs history and cannot touch
the one that reads a file. Measured from that side too: on a `--depth 1` clone this module is
2 passed, 1 skipped, with the config test among the passes. That is the arrangement
test_repo_browser_isolation's checkout probe argues for, and here it is load-bearing rather than
decorative.
"""

import re
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
CI_WORKFLOW = REPO_ROOT / ".github" / "workflows" / "ci.yml"

# The anchor idiom: the preposition, then a backticked abbreviated-or-full sha. `[0-9a-f]{7,40}`
# is git's own range — 7 is the shortest abbreviation this repo writes and `core.abbrev` will not
# go below 4, 40 is a full sha-1. The preposition is what separates a measurement's anchor from
# every other hex token in the tree, and that separation is MEASURED rather than asserted: see the
# module docstring's three rejected triggers.
#   `re.IGNORECASE` REACHES THE CHARACTER CLASS, not only the word, and this comment asserted the
#   opposite until it was run: it said the flag was "on the word only" and that upper-case hex was
#   therefore excluded. Measured, the preposition followed by an upper-case abbreviation matches
#   under this flag and does NOT under `(?i:at)`. The behaviour is kept and the COMMENT corrected,
#   not the other way round, because the two failure modes are not symmetric — an upper-case sha
#   going unread is a MISS, while the false positive it was meant to avoid needs someone to write
#   the preposition in front of a magic-byte constant. Measured, this repo holds no upper-case hex
#   token at all, so both spellings behave identically on today's tree; and git resolves an
#   upper-case abbreviation anyway (`cat-file -e` and `merge-base --is-ancestor` both exit 0 on
#   one), so the checks below do not care. Pinned by a row in `_MATCHES` rather than left to this
#   paragraph, since a comment is exactly what was wrong here.
_ANCHOR = re.compile(r"\bat\s+`([0-9a-f]{7,40})`", re.IGNORECASE)

# The rows are CONSTRUCTED, not sampled from the tree, so that this pin cannot go stale when the
# tree's prose moves — the same reason test_mutation_sweep_contract pins its pattern with rows
# rather than with a count. `#` prefixes are not stripped anywhere here because the scan reads raw
# file text, which is how a comment's `at` reaches this pattern at all.
#   BE EXACT ABOUT WHAT THE REFUSED ROWS ARE, because the first draft of this comment was not and
#   VMCP-171 (695) is the card for that exact slip. They are ADAPTATIONS of prose this repo really
#   holds, not strings it contains verbatim, and the difference is checkable: the magic-byte row
#   merges two separate spellings from test_repo_browser_isolation, which writes the JPEG signature
#   truncated in one place and in full in another; and the two orphan rows come from
#   docs/superpowers/specs, which writes bold markers around "still resolves" and a real arrow
#   character where this row writes an ASCII one. The remaining five are pure constructions at the
#   pattern's edges — hex below git's abbreviation floor, non-hex inside the backticks, a ref name
#   rather than a sha, and the two that pin the word boundary. Nothing here claims to be a
#   quotation, which is what keeps this comment out of 695's slice rather than an instance of it.
_MATCHES = (
    "the weak form vouched for 22 records at `bba4fed`",
    "at `93714d549448302e46c3e48a543f30146e85c9df` it was 21",
    "AT `6dd2803` this card's own first commit",
    "measured at\n`75a1e52`",
    "# and nine at `52d6085`, where VMCP-155 (660)'s entry left",
    "the same tree written in upper case, at `BBA4FED`",
)
# The areas the scan must still be reaching, checked as a set rather than as "one `.py` and one
# `.md`" — VMCP-171 (695)'s auditor killed that weaker form by excluding `docs/` and planting
# there, and both halves were green. Named areas rather than named FILES because a file gets
# renamed and an area does not; named at all rather than counted because a count moves with every
# landing, which is this whole file's subject.
#   `pyproject.toml` is the exception that is named as a FILE, and deliberately — VMCP-233 (780).
# It is the one anchor-bearing file the old `("*.py", "*.md")` corpus could not see, so it is the
# single member of this tuple whose absence would mean the suffix filter had come back. An area
# would not say that: `.toml` names no area, and every other entry here was already reachable
# before 780. This is the floor that keeps the widening from being reverted in silence.
_SCAN_MUST_REACH = ("CLAUDE.md", "src/", "tests/", "docs/", "pyproject.toml")

_REFUSED = (
    "PNG `89504e470d0a1a0a`, JPEG `ffd8ffe0`, PDF `255044462d` (`%PDF-`)",
    "pre-rebase commit `e3d5be6` still resolves",
    "which changed its sha (`9ec979f` -> `16821e9`, and see",
    "at `abc123` is six characters, below git's abbreviation floor",
    "at `not-a-sha` and at `zzzzzzz` are not hex",
    "look at `HEAD` and at `origin/main`",
    "the commit that `bba4fed` supersedes",
    "reflowed to the format `75a1e52` shipped",
)


def _prose_files(publishable: set[str]):
    """Every file git would PUBLISH, as (relative path, text) — no suffix filter at all.

    THE SUFFIX TUPLE IS GONE, and VMCP-233 (780) is the card. It read `("*.py", "*.md")`, so a
    figure anchored inside `pyproject.toml` was never resolved: the sha need not exist and need
    not be an ancestor. That file carries EIGHT anchors, three of them added by VMCP-186 (711) in
    the same landing that corrected most of that card's figures, which is how the gap was noticed.
      The card suggested adding `"*.toml"`. Measured before writing this, the wider fix costs the
    same and closes more: `pyproject.toml` is the ONLY publishable file in this repo outside
    `.py`/`.md` that holds an anchor at all, so a three-suffix tuple would have closed today's
    exposure while leaving the SHAPE of the hole open — `scripts/release.sh`'s comment layer and
    the workflow YAML are both prose an anchor can land in, and neither is a `.toml`.
      Dropping the filter is also a step REMOVED rather than added: the publishable set already
    IS the corpus, and the suffix tuple only ever drove an `rglob` walk whose results were then
    filtered back against that set. Priced on the tree this landed on: 73 publishable files,
    3.20 MB, read in 0.05 s, zero undecodable, and ZERO false reds — all 107 anchors across ten
    files resolve and are ancestors. The `UnicodeDecodeError` guard below is what makes the
    no-filter form safe for a future binary; there is none today, which is why it stays marked
    as uncovered.

    MUTATION-CHECKED, `__pycache__` deleted per round then `PYTHONDONTWRITEBYTECODE=1`, this file
    as the selection, every round restored from a COPY with the restore confirmed by sha256, and
    every mutation asserted to have APPLIED before the round ran — the card's own filing records
    a first attempt that did NOT apply (a case mismatch) and returned a green that meant nothing.
    Control round: 0 failed.
      * plant a bogus anchor in `pyproject.toml` -> **1 failed**, naming the file and the sha
      * the SAME plant against the pre-780 `("*.py", "*.md")` corpus -> **0 failed**. That is
        the pair, and it is what makes this a fix rather than a tidy-up: one plant, one
        selection, one control, and the only difference is which files got read
      * revert the corpus alone, with NO plant anywhere -> **1 failed** on the floor assert,
        because `_SCAN_MUST_REACH` names `pyproject.toml`. A widening whose removal is green is
        a widening that gets removed; this is the round that says it cannot be

    Publishable, not "on disk outside dot-directories", and the difference is a decision rather
    than a tidy-up. The first version walked the filesystem with a dot-directory filter copied
    from `_repo_markdown`; the independent second pass then constructed the obvious complaint —
    an agent's own untracked scratch note at the repo root, holding a sha that a rebase orphaned,
    turns the MANDATORY pre-push `pytest tests/unit` red. Asking git instead settles it in the
    direction test_repo_browser_isolation already argues for: the question is what `git add -A`
    would publish, so `--cached` catches every committed file and `--others --exclude-standard`
    catches a file that is not committed YET but would be — which is how this gate caught its own
    author, on a file that was still untracked at the time.
      IGNORED scratch really does drop out, which is the part the heuristic did badly: it is
    `.gitignore` that decides, not the leading dot, so `.playwright-mcp/` and a build directory
    with no dot in its name are both excluded for the same reason instead of one by luck. What
    remains scanned is an untracked, UNIGNORED note — and that is deliberate rather than
    residual: such a file is one `git add -A` away from being repository prose, which is the whole
    premise of the sibling test that watches the same boundary for credentials.
    """
    for relative in sorted(publishable):
        path = REPO_ROOT / relative
        if not path.is_file():  # pragma: no cover - a staged deletion, absent from disk
            continue
        try:
            yield relative, path.read_text(encoding="utf-8")
        except UnicodeDecodeError:  # pragma: no cover - no such file in this repo
            continue


def _anchors(publishable: set[str]):
    """(relative path, sha) for every anchor in the repo's prose, in a stable order."""
    for relative, text in _prose_files(publishable):
        for match in _ANCHOR.finditer(text):
            yield relative, match.group(1).lower()


# A FILESYSTEM fact, never git's exit code, copied deliberately from the checkout probe in
# test_repo_browser_isolation: "no repository" and "git is broken" have to come from two different
# sources, or the skip becomes an off-switch for the second. `.git` is a directory in a main
# checkout and a `gitdir:` FILE in a linked worktree, which is where the parallel drain's per-task
# agents run, so `.exists()` rather than `.is_dir()` is the question actually being asked.
_IS_GIT_CHECKOUT = (REPO_ROOT / ".git").exists()

requires_git_checkout = pytest.mark.skipif(
    not _IS_GIT_CHECKOUT,
    reason=(
        f"{REPO_ROOT} has no .git, so there is no history to resolve an anchor against "
        "(a `git archive`/sdist extraction, a copied tree). NOT APPLICABLE rather than broken; "
        "in a checkout this runs, and a git that FAILS there goes red rather than skipping"
    ),
)


def _git(*args: str) -> subprocess.CompletedProcess:
    """git, always rooted at the repo — never at whatever cwd pytest was started in."""
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True, text=True)


def test_the_anchor_pattern_reads_the_idiom_and_not_every_hex_token():
    """The RULER, pinned by construction so that narrowing it cannot go quietly green.

    This is the other half of the pair-mutation guard, and the pair is the point. The scan below
    is green on this tree, so DELETING either of its two git checks changes nothing — a violation
    has to be planted for the scan to say anything at all. That makes "narrow the pattern until it
    matches nothing" and "plant a violation" individually innocent and lethal together, which
    VMCP-194 (724) is the card for. A count of anchors would be the obvious guard and is the wrong
    one: it moves with every landing that writes prose. Constructed rows do not move.

    MUTATION-CHECKED, `__pycache__` deleted before each round and then PYTHONDONTWRITEBYTECODE=1,
    the selection being this file plus test_mutation_sweep_contract.py — 9 collected, that second
    file being in because its scanner reads this one's prose and a plant could move its offender
    set — every round restored from a COPY rather than by `git checkout --`, and every restore
    confirmed by returning to the control. Control round: control 0 failed.
      * `_ANCHOR` tightened to `[0-9a-f]{40}`, i.e. full shas only -> 1 failed, HERE and nowhere
        else. Planting a fabricated anchor on top is still 1 failed — the narrowed ruler cannot
        see the plant, so it adds nothing — and with this test also disabled it is 0 failed. The
        RULER therefore has exactly one guard, and saying so is the point: what closes the pair
        mutation is not redundancy but that the narrowing is LOUD ON ITS OWN, so there is no
        "individually innocent" half to combine with a plant. An earlier arrangement did cover it
        twice, by accident — the scan's reach assert counted files that HELD an anchor rather than
        files SCANNED, so a pattern change moved it. Separating the two (this test owns the ruler,
        the reach assert owns the walk) made each guard single-owner and each narrowing loud,
        which is the better trade even though the raw round count went down
      * `_ANCHOR` widened to a bare backticked hex token, dropping the preposition -> 2 failed:
        one here on the magic-byte row, one on the scan. The scan's message then lists the three
        PNG/JPEG/PDF literals, all of them correct prose — and NOT the three commits
        docs/superpowers/specs quotes because a rebase orphaned them, even though those are
        offenders too. The unresolvable assert runs first and short-circuits, so the second list
        never prints. An earlier draft of this bullet said "six offenders", which is the count of
        things that are WRONG and not the count a reader sees; the assert now carries the other
        list in its own message so the two do not hide each other
      * `\\bat` relaxed to `at`, so a word ENDING in those two letters matches -> 1 failed, on the
        two rows that exist for exactly this. It was 0 failed before those rows were written, and
        that is recorded rather than quietly fixed: the boundary was unpinned. It also separates
        NOTHING on today's tree — measured, zero occurrences anywhere of a word character
        immediately before the preposition-plus-hex shape. The class is ordinary prose all the
        same ("the commit that <sha> supersedes"), the rows are constructed so they cannot go
        stale, and one string is cheaper than the argument about whether it is needed
      * `re.IGNORECASE` narrowed to the word alone, `\\b(?i:at)` -> 1 failed, on the upper-case
        row. That round exists because the comment beside `_ANCHOR` asserted this narrowing was
        already the behaviour, and it was not; the row is what stops the same sentence from being
        written again
    """
    for row in _MATCHES:
        assert _ANCHOR.search(row), (
            f"the anchor pattern no longer reads {row!r}, which is a shape this repo's prose "
            "really writes. A figure anchored that way is now unchecked and the scan below has "
            "quietly narrowed — widen the pattern or say in the module docstring why this shape "
            "stopped being an anchor"
        )
    for row in _REFUSED:
        assert not _ANCHOR.search(row), (
            f"the anchor pattern now reads {row!r} as an anchor. Every row here is prose this "
            "repo really contains and none of them names a tree a figure was measured at — the "
            "magic-byte literals, the docs spec's deliberately-orphaned commits, and hex too "
            "short for git to abbreviate to. Matching them makes the scan below red on arrival"
        )


@requires_git_checkout
def test_every_sha_anchored_figure_names_a_commit_this_history_holds():
    """A figure labelled with a tree must name a tree a later reader can actually open.

    The two checks are separate because they fail apart and mean different things. `cat-file -e`
    answering 128 is "no such commit": a fabricated sha, a typo, or a number measured in a tree
    that was never committed — the `1761` case test_mutation_sweep_contract writes up, where no
    sha could have been named because none existed. `merge-base --is-ancestor` answering 1 is "the
    commit exists but this history does not contain it": a pre-rebase commit of your own, an
    orphan, a commit from a branch that was dropped. The second is the one that catches the author
    of THIS card, and every other author under the parallel drain: rebase before pushing is
    mandatory here, so a figure anchored to your own un-pushed HEAD is orphaned by the very step
    that precedes the push.

    IT READS THE LABEL, NOT THE FIGURE. Nothing here re-derives a count, which is what keeps it
    from being both a tautology and a nuisance — see the module docstring. A wrong number with a
    real anchor still ships; what cannot ship is a number whose tree cannot be opened.

    MUTATION-CHECKED, `__pycache__` deleted before each round and then PYTHONDONTWRITEBYTECODE=1,
    the selection being this file plus test_mutation_sweep_contract.py (9 collected), every round
    restored from a COPY and confirmed by returning to the control. Control round:
    control 0 failed.
      * a fabricated anchor — the preposition and seven hex characters naming no commit, described
        rather than spelled for the reason the module docstring gives — planted in this file's own
        prose -> 1 failed, naming the file and the sha
      * THE TWO CHECKS DO NOT FAIL APART on that plant, and the first draft of this block claimed
        they do. Measured: with the existence check deleted the same plant is 1 failed, NOT 0,
        because `merge-base --is-ancestor` also refuses a sha git cannot resolve. So the existence
        branch does not own the catch — it owns the DIAGNOSIS, and without it the surviving
        message reads "these commits exist but are not ancestors of HEAD" about a commit that does
        not exist, sending a reader to look for a rebase that never happened
      * an anchor on a real commit that is NOT an ancestor, using the spec's own orphan
        `16821e9` -> 1 failed; with the ancestor check deleted the same plant is 0 failed, and
        THAT one is a true negative pin — nothing else in the file reaches it
      * the author's own case, constructed rather than reasoned, in its own
        `git clone --no-hardlinks`: a commit made there, its abbreviated sha written into this
        file's prose as an anchor, committed, then `git rebase` onto a base that had moved. Before
        the rebase the round is 0 failed; after it the sha still RESOLVES (exit 0) and is no
        longer an ancestor (exit 1), and the round is 1 failed, naming it. That is the whole
        self-protection claim, and it fires exactly where SKILL.md already requires a re-run:
        after the rebase, before the push
      * the scan restricted to `.md` files only -> 1 failed on the reach assert, no plant needed
      * THE WALK NARROWED IN A WAY THE FIRST VERSION COULD NOT SEE, which an independent pass
        built and which is why `_SCAN_MUST_REACH` exists. That version asked only for one `.py`
        and one `.md` among the files holding anchors. Excluding `docs/` from the walk was
        0 failed, and excluding it AND planting a violation inside `docs/` was 0 failed too — a
        genuinely silent pair, in the assert whose own message claimed to close exactly that.
        Against named areas and files SCANNED rather than files that happen to hold an anchor,
        both rounds are 1 failed. The reach assert deleted on its own is still 0 failed, and that
        is correct: it defends nothing on a clean tree, being the guard for a narrowing rather
        than for a violation. Both rounds with it deleted -> 0 failed, which is its negative pin
    """
    shallow = _git("rev-parse", "--is-shallow-repository")
    assert shallow.returncode == 0, (
        f"`git rev-parse --is-shallow-repository` failed (exit {shallow.returncode}): "
        f"{shallow.stderr.strip()}. There IS a .git here, so this is a broken git rather than a "
        "missing one, and it goes red rather than skipping"
    )
    if shallow.stdout.strip() == "true":
        pytest.skip(
            "this checkout is SHALLOW, so no anchor older than its depth can be resolved and "
            "every one of them would be reported as a fabrication. The property is unmeasurable "
            "here, not violated. CI must not land in this branch silently — that is why "
            "test_ci_checks_out_the_history_this_gate_needs reads the workflow as text and "
            "needs no git at all"
        )

    listed = _git("ls-files", "--cached", "--others", "--exclude-standard")
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr.strip()}"
    publishable = set(listed.stdout.splitlines())

    # What the WALK reached, not what happened to hold an anchor — a narrowing has to be visible
    # even in an area that carries no anchors today, or the guard only works where it is not
    # needed. `src/` is exactly that area: it holds SKILL.md and the package, and no anchor.
    scanned = {relative for relative, _text in _prose_files(publishable)}

    verdicts: dict[str, str | None] = {}
    unresolvable, not_in_history = [], []
    for relative, sha in _anchors(publishable):
        if sha not in verdicts:
            # Memoised per SHA, not per occurrence: the same tree is anchored many times over and
            # each miss costs two subprocesses. Distinct shas are a fraction of occurrences.
            if _git("cat-file", "-e", f"{sha}^{{commit}}").returncode != 0:
                verdicts[sha] = "unresolvable"
            elif _git("merge-base", "--is-ancestor", sha, "HEAD").returncode != 0:
                verdicts[sha] = "not_in_history"
            else:
                verdicts[sha] = None
        if verdicts[sha] == "unresolvable":
            unresolvable.append(f"{relative}: `{sha}`")
        elif verdicts[sha] == "not_in_history":
            not_in_history.append(f"{relative}: `{sha}`")

    missing_roots = sorted(r for r in _SCAN_MUST_REACH if not any(f.startswith(r) for f in scanned))
    assert not missing_roots, (
        f"the anchor scan reached nothing under {missing_roots} (it reached "
        f"{sorted({f.split('/')[0] for f in scanned})}). Every one of those areas holds prose that "
        "can write measured figures, so a scan that stops reaching one has been narrowed — by the "
        "file walk or by the publishable filter. This is the half of a PAIR "
        "mutation that is otherwise silent: narrowing the walk changes nothing on a clean tree, "
        "and then a violation planted in the excluded area sails through. The earlier form of "
        "this assert asked only for one `.py` and one `.md`, and an independent pass killed it by "
        "excluding `docs/` and planting there — both rounds green. If an area genuinely stops "
        "existing, drop it from `_SCAN_MUST_REACH` deliberately rather than loosening this"
    )
    assert not unresolvable, (
        f"these figures are anchored to commits that do not exist: {unresolvable}. An anchor is a "
        "promise that a later reader can open the tree the number was taken on, and a sha that "
        "does not resolve is worse than no anchor because it looks checked. The usual cause is a "
        "figure measured in an UNCOMMITTED working tree, which is the `1761` case written up in "
        "test_mutation_sweep_contract: there is no sha to name, so the figure has to become an "
        "assert or a property instead. Re-measure at a committed tree and anchor to THAT. "
        f"(Anchors that resolve but are off this history, reported by the NEXT assert and listed "
        f"here because this one runs first and would otherwise hide them: {not_in_history})"
    )
    assert not not_in_history, (
        f"these commits exist but are not ancestors of HEAD: {not_in_history}. Almost always a "
        "figure anchored to your own pre-rebase commit: `git rebase origin/main` before pushing "
        "is mandatory here, and it orphans the sha you just wrote down. Re-measure on the "
        "REBASED tree and anchor to a commit that is on the main line — or, if the prose is "
        "deliberately discussing an orphaned commit (docs/superpowers/specs does exactly that), "
        "write it without the `at` preposition, which is how those three already pass"
    )


_CHECKOUT = "actions/checkout"
# YAML says a `#` opens a comment at the start of a line or after whitespace, and nowhere else —
# so this is the whole of comment-stripping for a file with no `#` inside a quoted scalar, which
# is checked by the caller reading the result back.
_YAML_COMMENT = re.compile(r"(?:^|\s)#.*$")


def _checkout_step(job: str) -> str | None:
    """The checkout step's own block inside a job slice, comments stripped — or None if absent.

    A SUBSTRING TEST OVER THE WHOLE JOB IS NOT ENOUGH, and that is measured rather than foreseen.
    Control round, this file plus test_mutation_sweep_contract.py: control 0 failed. An
    independent second pass then replaced the `with:` block with the single line
    `# TODO: re-enable fetch-depth once the runner cache stops thrashing` — a comment, which is
    what a temporary disable really looks like — and the round was 0 failed, against 1 failed for
    deleting the line outright. CI would have checked out depth 1, the gate above would have taken
    its skip branch on every run, and the suite would have stayed green: verbatim the outcome that
    assert's own message says it prevents. The same hole accepted the key under any OTHER step of
    the job, or in a third job spliced between this one and `integration:`.

    Reading the STEP closes both: a comment is gone before the search, and a key belonging to a
    different step is outside the block. Done by indentation rather than with a YAML parser
    because this repo has no yaml dependency and adding one to run a unit test is a worse trade
    than twelve lines that the caller checks by printing what they read.
    """
    lines = [_YAML_COMMENT.sub("", line).rstrip() for line in job.splitlines()]
    for i, line in enumerate(lines):
        stripped = line.strip()
        if not (stripped.startswith("- ") and _CHECKOUT in stripped):
            continue
        indent = len(line) - len(line.lstrip())
        block = [line]
        for following in lines[i + 1:]:
            if not following.strip():
                continue
            if len(following) - len(following.lstrip()) <= indent:
                break
            block.append(following)
        return "\n".join(block)
    return None


def test_ci_checks_out_the_history_this_gate_needs():
    """The gate above is only real in CI if CI has history — and by default it does not.

    `actions/checkout` fetches depth 1 unless told otherwise. Measured on a `--depth 1` clone of
    `c1c2619`: `rev-parse --is-shallow-repository` is true, `rev-list --count HEAD` is 1, and not
    one of this repo's 15 anchors resolves. So without `fetch-depth: 0` the gate above takes its
    skip branch on every CI run and the whole check is a local-only courtesy.

    This test reads the workflow as TEXT and never runs git, which is the point: the two facts
    come from two sources, so a shallow checkout cannot silence both. The pin is on the JOB that
    runs the unit suite, not on the file as a whole — the release job has carried `fetch-depth: 0`
    since long before this card, for an unrelated reason, and a whole-file substring would be
    satisfied by it while `lint-and-unit` stayed shallow.

    MUTATION-CHECKED, `__pycache__` deleted before each round and then PYTHONDONTWRITEBYTECODE=1,
    the selection being this file plus test_mutation_sweep_contract.py (9 collected). Control
    round: control 0 failed.
      * `fetch-depth: 0` deleted from the lint-and-unit job, leaving the release job's -> 1 failed
      * the same line moved into the integration job instead -> 1 failed, which is exactly what
        slicing the job buys over grepping the file, since both rounds leave a `fetch-depth: 0`
        somewhere in it
      * THE LINE COMMENTED OUT rather than deleted, which is what a temporary disable really looks
        like -> 1 failed. Under the substring form this assert shipped with it was 0 failed, and
        an independent pass is what found that; reading the STEP with comments stripped is the fix
      * and the arrangement checked from the other side, on a real `--depth 1` clone of this
        branch rather than by argument: the git-backed test SKIPS there, naming shallowness as the
        reason, while this one still passes — 2 passed, 1 skipped. That is the two-source property
        working. A shallow checkout silences the test that needs history and cannot touch the one
        that reads a file
    """
    text = CI_WORKFLOW.read_text(encoding="utf-8")
    start = text.find("\n  lint-and-unit:\n")
    assert start != -1, (
        "the ci workflow no longer has a `lint-and-unit:` job at the expected indentation, so "
        "this pin cannot tell which job runs the unit suite. Re-slice it against the job that "
        "does"
    )
    end = text.find("\n  integration:", start + 1)
    assert end != -1, "the lint-and-unit job no longer ends where `integration:` begins"
    job = text[start:end]
    assert 0 < len(job) < len(text), "the job slice is not a proper subset of the workflow"

    step = _checkout_step(job)
    assert step is not None, (
        f"the lint-and-unit job has no `- uses: {_CHECKOUT}` step any more, so this pin cannot "
        "say anything about how much history it fetches. Re-slice it against whatever action "
        "checks the repository out"
    )
    assert "fetch-depth: 0" in step, (
        "the job that runs `pytest tests/unit` no longer checks out full history, so "
        "test_every_sha_anchored_figure_names_a_commit_this_history_holds SKIPS on every CI run: "
        "`actions/checkout` fetches depth 1 by default, and measured on such a clone none of "
        "this repo's sha anchors resolve at all. Restore `fetch-depth: 0` under this step's "
        f"`with:`, or delete that gate honestly rather than leaving it green-by-skip. The step "
        f"as read, comments stripped:\n{step}"
    )
