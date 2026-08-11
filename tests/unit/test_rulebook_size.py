"""The two rulebooks have a SIZE CEILING, and it is a ratchet like `line-length`.

WHY THIS EXISTS, measured rather than argued. Both files enter an agent's context before it
does any work: CLAUDE.md on every session in this checkout — the orchestrator's, every
per-task agent's and every reviewer's, so ~7 contexts per round at `wip_limit = 3` — and
SKILL.md on every invocation of the `tracker` skill, in this checkout AND at every consumer,
since it ships inside the wheel while CLAUDE.md does not.

Left alone they grow without bound, because this repo's own dogfood loop appends each card's
post-mortem to them. That is not a projection: CLAUDE.md measured 14 063 characters at
`909df13`, 73 983 at `23dffc8` and 155 270 at `f77977e` — roughly a doubling per week, over
eleven days. A rulebook that doubles weekly stops being read and starts being skimmed, which
is the failure mode it exists to prevent.

WHAT THE FIX WAS, so the next reader does not undo it by accident. The prose was split into a
RULES layer (these two files) and an EVIDENCE layer (`docs/dossier/*.md` for CLAUDE.md,
`src/vikunja_mcp/skills/tracker/references/*.md` for SKILL.md, both linked from the rule they
belong to). Nothing was deleted wholesale; the measurements, constructed stands and refuted
wordings moved. So the way to satisfy this gate is to move new evidence to the dossier, NOT
to compress a rule until it stops being followable.

WHAT THIS GATE IS NOT. It does not check that the split is correct, that a rule is still
stated, or that a dossier is still reachable — those are the prose pins in
test_mutation_sweep_contract.py, test_skill_contract.py and test_repo_browser_isolation.py,
which is why this file is deliberately dumb. It measures ONE thing, in CHARACTERS rather than
bytes, for the reason CLAUDE.md's line-length rule gives: SKILL.md is majority Cyrillic, so a
byte count would report roughly 1.6x its real size and the ceiling would mean nothing.

THE UNIT IS A PROXY, AND KNOWING WHICH ONE MATTERS (#998). What this gate is defending is
CONTEXT, and context is priced in TOKENS. Characters stand in for tokens well enough while a
file stays in one language, and not at all across a change of language: measured, Cyrillic
prose runs about 0.46-0.48 tokens per character against 0.25-0.28 for Latin (the Latin figure
is the range over all 12 Latin markdown files this repo tracks, 0.2487-0.2834, not one sample),
so English says the same thing in slightly MORE characters for noticeably fewer tokens: a real
SKILL.md section translated by hand measured 1.69x more tokens in Russian, and an independent
reviewer's translation of a different section 1.63x. TOKENS ARE THE THIRD UNIT, and
the bytes-versus-characters argument above did not consider them — it is still correct, so do
not "fix" it. The reason this gate is not simply moved onto tokens is measured too: the only
available tokenizer (`tiktoken`) fetches its BPE table over the network from an OpenAI CDN
(1.68 MB; 2.62 s cold against 0.11 s warm), which would make `lint-and-unit` — the job that
decides whether `release` runs at all — depend on a third party's uptime, for a tokenizer that
is not even the one counting here. So the proxy stays and is LABELLED: every ceiling names the
script it was derived in, and a test below fails when a file stops matching it.

RATCHET DIRECTION. When a rulebook genuinely shrinks, lower its ceiling in the same commit —
that is the whole mechanism, and it is the same one `_HARD_LIMIT` uses in
test_line_length_gate.py. RAISING a ceiling is a decision, not a fix: it says the rules layer
itself grew, which is legitimate (a new subsystem, a new gate) and rare. If you are raising it
because a post-mortem would not fit, the post-mortem is in the wrong file. And if you are
raising it because a file was TRANSLATED, stop: re-derive the number from a token measurement
of the new text so it preserves the same budget, and move the declared script with it.
"""

import pathlib
import unicodedata

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# path -> (ceiling in CHARACTERS, script it was derived in, what it is)
#
# The script is load-bearing, not a label: characters are a PROXY for the tokens this gate
# actually cares about, and the rate differs by language (see the script test at the bottom).
# CLAUDE.md measures 0.0% Cyrillic by letter (12 of 25 758) and SKILL.md 85.6%, so these two
# numbers are quoted in different currencies — in characters SKILL.md's ceiling is 2.88x
# CLAUDE.md's, and converted at each file's OWN measured rate (0.4645 and 0.2608 tokens per
# character) 53 419 against 10 432, i.e. 5.12x. All measured at `d3884bc`.
#
# Headroom is deliberately modest — a few thousand characters, i.e. a rule or two — because a
# generous ceiling is the same as no ceiling.
# Measured at the split: CLAUDE.md 34 574 characters (down from 155 270 at `f77977e`) and
# SKILL.md 104 596 (down from 202 619 at the same commit). SKILL.md's ceiling has more headroom
# than CLAUDE.md's for a stated reason rather than a generous one: its two universal sections —
# «Следы работы» and «Второй независимый проход», needed by every agent on every task — were
# deliberately NOT moved to references, so its rules layer is genuinely larger and its next
# ratchet step is condensing those two in place, not relocating them.
_CEILINGS = {
    "CLAUDE.md": (
        40_000, "latin",
        "the repo rulebook — read by every session in this checkout",
    ),
    "src/vikunja_mcp/skills/tracker/SKILL.md": (
        115_000, "cyrillic",
        "the agent rulebook — ships in the wheel, so every consumer pays for it too",
    ),
}


def test_each_rulebook_stays_under_its_ceiling():
    """The gate itself. One assert per file so a red names the file that grew."""
    for relative, (ceiling, _script, what) in _CEILINGS.items():
        path = REPO_ROOT / relative
        assert path.is_file(), (
            f"{relative} is gone from the repo — this gate has nothing to measure. If it moved, "
            f"move this entry; do not delete the check"
        )
        size = len(path.read_text(encoding="utf-8"))
        assert size <= ceiling, (
            f"{relative} is {size} characters, over its ceiling of {ceiling} ({what}). This file "
            f"is in an agent's context before it does any work, so growth here is paid on every "
            f"session. Move the new prose to its evidence layer — docs/dossier/ for CLAUDE.md, "
            f"skills/tracker/references/ for SKILL.md — and link it from the rule it belongs to. "
            f"Raise the ceiling only if the RULES layer itself genuinely grew, and say so in the "
            f"commit message"
        )


def test_the_ceiling_is_measured_in_characters_and_not_bytes():
    """The unit is load-bearing, and only one of the two files can show it.

    SKILL.md is majority Cyrillic, where UTF-8 spends two bytes per character, so a byte count
    reports roughly 1.6x the real size. A gate written in bytes would therefore be far tighter on
    SKILL.md than on CLAUDE.md for no reason anyone chose, and re-tightening it after a split
    would silently ratchet against Russian prose specifically. This asserts the two units really
    do disagree on that file, so the docstring above stays true rather than decorative.
    """
    skill = REPO_ROOT / "src/vikunja_mcp/skills/tracker/SKILL.md"
    text = skill.read_text(encoding="utf-8")
    assert len(text.encode("utf-8")) > len(text) * 1.3, (
        "SKILL.md is no longer majority non-ASCII, so bytes and characters have converged and "
        "the paragraph above explaining why this gate counts characters is now describing a "
        "difference that does not exist. Either the file was translated — then rewrite that "
        "paragraph, AND see test_each_ceiling_declares_the_script_it_was_derived_in, which is "
        "the one that tells you what to do about the NUMBER (re-derive it from tokens, never "
        "bump it to fit) — or this pin is reading the wrong file"
    )


def test_every_rulebook_points_at_an_evidence_layer_that_exists():
    """A ceiling only works if there is somewhere for the evidence to go.

    Pinned because the failure is silent in exactly the wrong direction: with the dossier
    directory gone or renamed, every `→ Dossier: …` pointer in CLAUDE.md becomes a dead end, the
    next agent writes its post-mortem back into the rulebook because that is the only place left,
    and the gate above then reads as an obstacle rather than as a routing rule. Checks the
    DIRECTORIES rather than each link, because the links themselves are prose and move with the
    text; what must not vanish is the destination.
    """
    for relative in ("docs/dossier", "src/vikunja_mcp/skills/tracker/references"):
        directory = REPO_ROOT / relative
        assert directory.is_dir(), (
            f"{relative} does not exist, so the evidence layer this repo's rulebooks route their "
            f"post-mortems into is gone. The size ceiling next door assumes it is there: without "
            f"it there is nowhere to move prose to, and the ceiling becomes pressure to delete "
            f"measurements instead of relocating them"
        )
        assert any(directory.glob("*.md")), (
            f"{relative} exists but holds no markdown, which is the same failure one step later"
        )


def _cyrillic_share_of_letters(text: str) -> float:
    """What fraction of the letters are Cyrillic. Letters only, because markdown is full of
    punctuation, code spans and backticks that belong to no language."""
    letters = [c for c in text if c.isalpha()]
    if not letters:
        return 0.0
    cyrillic = sum(1 for c in letters if "CYRILLIC" in unicodedata.name(c, ""))
    return cyrillic / len(letters)


# Which script each ceiling was derived in. NOT decoration: see the test below.
_EXPECTED_SHARE = {"cyrillic": (0.5, 1.0), "latin": (0.0, 0.1)}


def test_each_ceiling_declares_the_script_it_was_derived_in():
    """THE UNIT IS A PROXY, AND A PROXY IS ONLY VALID FOR THE TEXT IT WAS CALIBRATED ON.

    This gate exists to bound what these files cost in an agent's CONTEXT, and that cost is
    counted in TOKENS. It counts CHARACTERS. While a file stays in one language the two move
    together and nothing is lost — but the conversion rate between them is language-dependent,
    so a ceiling means something different the moment the language changes.

    Measured (tiktoken cl100k_base, which is OpenAI's tokenizer — a PROXY for the one that
    actually counts here, so read the RATIO and not the absolutes): Cyrillic prose runs about
    0.46-0.48 tokens per character; Latin 0.25-0.28, which is the RANGE over all 12 Latin
    markdown files this repo tracks (0.2487 for docs/dossier/browser.md to 0.2834 for
    docs/dossier/config.md) rather than one sample — every tree-derived figure in this docstring
    measured at `d3884bc`. Same content translated by hand: a real SKILL.md section came out
    1.69x cheaper in English, and an independent reviewer's translation of a different section
    1.63x. Those two are properties of a SAMPLE, not of the tree, so they carry no anchor and
    should not be treated as constants. English is therefore slightly LONGER in characters
    (1.07x on that section) and distinctly cheaper in tokens.

    NONE OF THESE FIGURES IS GATED, and that is a limitation rather than an oversight: checking
    them needs a tokenizer, the only available one fetches over the network, and this file
    refuses to put that in CI (see the module docstring). The sha anchor is the substitute the
    repo already has — it does not re-derive the number, it guarantees a reader who wants to
    check CAN, because the tree is named and reachable (test_measured_figure_anchors.py).

    DO NOT ROUND THESE, and this is the defect that sent round 1 back. Round 1 wrote 0.46 and
    0.23 and derived "5.8x" from them while claiming to use "each file's own measured rate" —
    self-contradictory, since those rates are 0.4645 and 0.2608 and give 5.12x. Worse, 0.23 came
    from one hand-written paragraph and sits BELOW every Latin file in this repo, so the
    re-derivation this test PRESCRIBES would have come out 7-13% too generous: the procedure the
    gate hands you would have quietly loosened the gate.

    Two consequences, and the second is why this test exists rather than a paragraph:

    * The two ceilings below are not comparable to each other. In characters SKILL.md's is 2.88x
      CLAUDE.md's; converted at each file's own measured rate (0.4645, 0.2608) it is 5.12x — so
      the gate understates, by nearly half, how much more context SKILL.md is allowed to cost.
    * Translating a rulebook would push it AGAINST its ceiling while cutting the cost the
      ceiling exists to control. A ceiling raised at that moment "because we hit it" would be
      the gate defeating its own purpose. Re-DERIVE it from a token measurement of the new
      text instead.

    So each entry names its script, and this test fails when the file stops matching it.

    WHY THIS IS NOT THE NEIGHBOURING TEST WITH A LONGER MESSAGE. On a COMPLETE translation both
    go red, so the question is what each catches alone; built and measured on this file rather
    than reasoned. Translating SKILL.md 45% of the way — the realistic shape, since a 105 000-
    character rulebook gets translated section by section — leaves bytes/characters at 1.36,
    still above the 1.3 the neighbour tests, so ONLY this test fires; at 70% (ratio 1.20) both
    do. And turning CLAUDE.md Cyrillic fires only this one, because the neighbour reads SKILL.md
    and nothing else. The window where a rulebook has changed language enough to invalidate its
    ceiling but not enough to move a byte ratio is exactly where a ceiling gets bumped "to fit".

    MUTATION SWEEP, this file as the whole selection so no collateral can stand in for it,
    `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 each round, rounds read by COUNTING
    lines beginning `FAILED ` and naming which: control (opening) 0 failed, 0 errors, collected
    4; SKILL.md fully translated with the band intact -> 2 failed (this test AND the byte one);
    the same translation with the cyrillic band widened to 0..1 -> 1 failed (only the byte one),
    which is the pair proving the BAND is what catches it and not something else in the file;
    the band widened with no translation -> 0 failed, correctly, since there is nothing to
    catch; the declared script dropped from the entries -> 2 failed; control (closing, restored)
    0 failed, 0 errors, collected 4.
    """
    for relative, entry in _CEILINGS.items():
        assert len(entry) == 3, (
            f"the ceiling for {relative} does not declare the script it was derived in. "
            f"Characters are a PROXY for tokens and the rate is language-dependent, so a bare "
            f"number cannot say what it is worth. Entries are (ceiling, script, what)"
        )
        ceiling, script, _what = entry
        assert script in _EXPECTED_SHARE, (
            f"{relative} declares an unknown script {script!r}; known: {sorted(_EXPECTED_SHARE)}"
        )
        low, high = _EXPECTED_SHARE[script]
        share = _cyrillic_share_of_letters(
            (REPO_ROOT / relative).read_text(encoding="utf-8")
        )
        assert low <= share <= high, (
            f"{relative} is {share:.0%} Cyrillic by letter, which is outside the {low:.0%}-"
            f"{high:.0%} band its declared script {script!r} implies — the file changed "
            f"language while its ceiling of {ceiling} characters did not. DO NOT simply raise "
            f"or lower that number to fit. The ceiling bounds CONTEXT COST, which is counted "
            f"in tokens, and characters only stand in for tokens at a rate that depends on the "
            f"language: 0.46-0.48 tokens/character for Cyrillic against 0.25-0.28 for Latin "
            f"(the range over this repo's 12 Latin markdown files — do not round it DOWN, a "
            f"lower rate yields a more generous ceiling). "
            f"Re-derive the ceiling from a token measurement of the NEW text so it preserves "
            f"the same budget, then update the declared script in the same commit"
        )
