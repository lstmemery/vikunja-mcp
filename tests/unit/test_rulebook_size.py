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

RATCHET DIRECTION. When a rulebook genuinely shrinks, lower its ceiling in the same commit —
that is the whole mechanism, and it is the same one `_HARD_LIMIT` uses in
test_line_length_gate.py. RAISING a ceiling is a decision, not a fix: it says the rules layer
itself grew, which is legitimate (a new subsystem, a new gate) and rare. If you are raising it
because a post-mortem would not fit, the post-mortem is in the wrong file.
"""

import pathlib

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]

# path -> (ceiling in CHARACTERS, what it is)
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
    "CLAUDE.md": (40_000, "the repo rulebook — read by every session in this checkout"),
    "src/vikunja_mcp/skills/tracker/SKILL.md": (
        115_000,
        "the agent rulebook — ships in the wheel, so every consumer pays for it too",
    ),
}


def test_each_rulebook_stays_under_its_ceiling():
    """The gate itself. One assert per file so a red names the file that grew."""
    for relative, (ceiling, what) in _CEILINGS.items():
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
        "paragraph — or this pin is reading the wrong file"
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
