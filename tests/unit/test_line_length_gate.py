"""The line-length gate, made mechanical — tracker #669.

WHAT WAS BROKEN WITHOUT THIS FILE. `pyproject.toml` declared `[tool.ruff] line-length = 100` and
nothing checked it. Ruff's default rule set is E4/E7/E9/F, and E501 (line-too-long) is not in it,
so `line-length` reached only the FORMATTER. `ruff check .` was green on a 140-character comment in
api.py, and would have been green on a 400-character one for a structural reason rather than a
lucky one: with E501 unselected, not one selected rule looks at length at all.

AND "ONLY THE FORMATTER" IS NOT THE NEAR-MISS IT SOUNDS LIKE — running `ruff format` would NOT have
caught this, which is the fact that makes the gate worth its config. Measured: `ruff format
--line-length 100` on the pre-fix api.py reports "1 file reformatted" and leaves the longest line at
140. The formatter re-wraps CODE; it does not reflow comments or string content, and 36 of the 77
overlong lines were comments or string literals. So the unrun formatter was never a latent safety
net for this defect class, and E501 is not a second opinion on it — it is the only opinion.

THE COST WAS NOT THEORETICAL. Cards in this family were measuring their own lines by hand — VMCP-132
(621)'s worklog reports doing exactly that — and its second independent pass still had to catch a
reflow slip of its own, "because my replacement text ended mid-line and absorbed the tail of the
following sentence". That slip is reported by 621's worklog as 134 characters and is NOT
verifiable from this repository's history, which is the honest way to cite it: the second pass
caught it before the commit, so no such line was ever committed (an exhaustive scan of every `+`
line in every commit finds no 134-character Python line). The 140-character line this file exists
because of is the same accident one card earlier, and that one DID ship: it survived its author and
its review, and the card that eventually reported it was reviewing something else. That is the shape
to keep in mind — a re-wrap that swallows a sentence start leaves every word present and the
paragraph readable, so it is cheap for a linter to see and easy for a reader to slide over.

WHY THE HARD LIMIT IS 120 AND NOT 100, since that is the part a reader will want to argue with.
Measured on 2026-08-02, over `src/`, `tests/` and `scripts/`: seventy-seven lines exceeded 100
CHARACTERS across 18 files — 41 of them code, 22 inside string literals (19 of those docstrings)
and only 14 comments — and seventy-six of the seventy-seven were 116 characters or shorter. The
defect was 140: a 24-character outlier above every other PYTHON line in those three directories.
(Repo-wide it is not an outlier at all — 543 lines of markdown and other non-Python files exceed
140, the longest at 1520 — but ruff reads none of them, so they are not what the number is about.)
So the population is one real defect plus a long tail of one-to-sixteen-character overshoots, in
eighteen files of which nine took 3 to 14 commits in the preceding 48 hours while eight took none
at all. Counted rather than characterised, since the tail is easy to caricature: of the 76, 22 are
pure string, 19 carry code and a comment and a string on one line, 13 are pure comment, 10 are code
with a trailing comment, 7 are code with a string, and 5 are pure code — so the single largest
class is a plain string line, and "code with some kind of trailing comment" is 29 of 76, the
largest grouping but nothing like a majority. A gate at 100 would have demanded a 76-line cosmetic
diff through all of that to catch a class it is not needed for; a gate at 120 was red on exactly
the one real defect and green the moment it was reflowed. `per-file-ignores` was the third candidate
and is worse than either, for a reason that is measurable rather than aesthetic: its list would have
had to name all EIGHTEEN files — api.py, workflow.py, server.py, setup_cmd.py and workspace_cmd.py
plus thirteen test files carrying 53 of the 77 lines — so the gate would have been OFF in the very
file where the defect happened, and left on only where no violation has ever occurred. The remaining
band is tracked as card #711.

WHAT IT DOES NOT BUY, priced rather than rounded up. TWO gaps, both measured. The band from 101 to
120 characters is convention with no tool behind it: wrap at 100, but nothing will stop you at 103.
That is a deliberate trade and not an oversight — the alternative was the 76-line diff above. The
band shrinks by cleanup, and the ratchet direction is the pinned 120 below: lowering it is a
decision someone makes on purpose, in this file, with the count of the day in hand. SECOND, and
this is why the scan below is not merely a duplicate of ruff: E501 does not fire at all on a line
whose overlong portion contains NO WHITESPACE — a long URL, one unbroken token — because ruff will
not demand a break where none is possible. Measured at limit 120: a 136-character comment ending in
a long URL and a 136-character assignment of a long URL both pass, while a 149-character line with
a space past the limit and a 151-character line of ordinary prose are both caught. The character
scan below has no such exemption, so it flags the URL shape that ruff waves through; that is the
one place the two deliberately disagree, and if a genuinely unwrappable line ever has to exceed 120
this is where that argument gets had.

WHY CHARACTERS AND NOT BYTES, which is the trap this repo keeps walking into by hand. Ruff counts
CHARACTERS. The shell reflex — `awk '{ if (length($0) > 100) ... }'`, `wc -c` — counts BYTES, and
this repo's prose is full of em-dashes (3 bytes) and Cyrillic (2 bytes each). Measured on the
pre-image 3db8ef9: 1015 lines sit at or under 100 characters while exceeding 100 bytes, and 413 do
so at 120. A byte counter calls every one of them a violation. Those two are DIFFERENT sets and
neither contains the other — a line over 100 characters cannot be in the first — so do not read the
413 as a subset of the 1015. And the 1015 is dated to a sha on purpose: it counts em-dashes in
prose, so it moves whenever prose lands, this file's own docstring included (on the tree that ships
it the figure is already 1020). Re-measure rather than quote it. VMCP-132 (621)'s worklog records
the mistake in this repo in those words: "an awk byte-count had falsely flagged one line because of
the em-dash". The last test below pins that the distinction is still live here, so the paragraph
cannot rot into a story about a hazard that no longer exists.

MUTATION, run 2026-08-02 with `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1, kept to one
paragraph so every count below is answered by the control that opens it: control 0 failed;
dropping `E501` from the select list -> 1 failed (and in that same tree `ruff check .` on a
deliberately injected 121-character line reported "All checks passed!", which is the whole reason
the select list is pinned rather than trusted); raising `max-line-length` to 500 -> 1 failed;
deleting the `[tool.ruff.lint.pycodestyle]` table outright -> 1 failed; injecting a 121-character
line into api.py -> 1 failed here, plus 1 error from ruff itself; injecting the 136-character URL
comment described above -> 1 failed here while `ruff check .` reported "All checks passed!"; and
`ignore = ["E501"]` with `select` untouched, carrying an injected 400-character line -> 2 failed
here (the suppression pin AND the scan below) while ruff reported "All checks passed!". Each round
was restored and the control re-run at the end, again 0 failed. That last round is the only two-
failure one because it mutates config and source together; the first draft of this sentence said
1 and was corrected by re-running it, not by re-reading it.

WHAT THOSE ROUNDS DIVIDE INTO, stated carefully because the obvious summary is wrong. Rounds one,
two and six are invisible to ruff: it reports nothing, because a linter cannot report that its own
rule was switched off. Round five is the exemption ruff applies deliberately. Round four is the
ordinary case both channels answer. Round THREE is neither — deleting the pycodestyle table does
not switch E501 off, it silently TIGHTENS it to `line-length`, and ruff duly reports 76 errors on
the grandfathered band. So that round is loud in both channels, and the earlier draft of this
paragraph filed it under "invisible to ruff", which was false; the second independent pass caught
it by running the round instead of reasoning about it. Worth keeping as written, because the
failure mode it describes — a config edit that looks like a relaxation and lands as a tightening —
is the one most likely to be attempted by someone who found this file annoying.
"""

import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]

# Scanned by DIRECTORY rather than by `git ls-files`, so the pin still runs in a tree without a
# `.git` (an exported archive, a `git archive` copy used for an A/B harness). Ruff, running from
# the repo root, additionally honours `.gitignore`; these three directories plus any top-level
# `*.py` hold every Python file this project ships or tests, and nothing that is generated —
# verified, `git ls-files '*.py'` lists nothing outside them. The top-level glob is here because
# the directory list alone had a measured hole: a 400-character line in a NEW top-level `.py` was
# caught by ruff and MISSED by this scan. What is still not covered is a whole new top-level
# PACKAGE directory; that is deliberate, since the alternative is walking the repo root and
# excluding `.venv`, and a scan that has to be taught what to skip goes wrong more quietly than
# one that has to be taught where to look.
_SCANNED = ("src", "tests", "scripts")

# The declared wrap target and the enforced ceiling. They are DIFFERENT numbers on purpose; see
# the module docstring. Both are asserted against pyproject.toml rather than assumed, because the
# defect this file closes was a declared number with nothing reading it.
_WRAP_TARGET = 100
_HARD_LIMIT = 120


def _ruff_config() -> dict:
    pyproject = REPO_ROOT / "pyproject.toml"
    assert pyproject.is_file(), f"no pyproject.toml at {pyproject} — the gate cannot be read"
    return tomllib.loads(pyproject.read_text(encoding="utf-8"))["tool"]["ruff"]


def _python_files() -> list[Path]:
    files = sorted(p for name in _SCANNED for p in (REPO_ROOT / name).rglob("*.py"))
    files += sorted(REPO_ROOT.glob("*.py"))
    assert files, "scanned no Python files at all — the scan roots moved and this pin went blind"
    return files


def _overlong(limit: int) -> list[tuple[str, int, int, int]]:
    """Every line longer than `limit` CHARACTERS, as (path, lineno, chars, bytes)."""
    out = []
    for path in _python_files():
        rel = path.relative_to(REPO_ROOT).as_posix()
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1):
            if len(line) > limit:
                out.append((rel, lineno, len(line), len(line.encode("utf-8"))))
    return out


def test_e501_is_selected_and_not_suppressed_again():
    """Selecting E501 is necessary and NOT sufficient: three other keys can switch it back off."""
    lint = _ruff_config()["lint"]
    assert "E501" in lint["select"], (
        f"E501 is gone from [tool.ruff.lint] select ({lint['select']!r}) — with it removed "
        "`ruff check .` is green on a line of ANY length (measured on a 400-character one), which "
        "is exactly the state tracker #669 found and closed."
    )
    # Selecting a rule does not keep it on. MEASURED: with `select` untouched, `ignore = ["E501"]`
    # makes `ruff check .` report "All checks passed!" on a 400-character line while the assertion
    # above still passes. `per-file-ignores` does the same per path. So the pin has to read the
    # suppression keys too, or it certifies a gate that is not running.
    assert "E501" not in lint.get("ignore", []), (
        "E501 is selected AND ignored, which is a silent no-op: `ignore` wins. If the intent was "
        "to retire the gate, remove it from `select` so this file says so in one place."
    )
    suppressed = {
        path: codes
        for path, codes in lint.get("per-file-ignores", {}).items()
        if "E501" in codes
    }
    assert not suppressed, (
        f"E501 is switched off per-path for {sorted(suppressed)} — the shape tracker #669 rejected "
        "on measurement: a per-file ignore list sized to make a 100-character gate green would "
        "have had to name all 18 files that carried an overlong line, api.py among them, leaving "
        "the gate off in the one file where the defect actually happened."
    )


def test_the_hard_limit_is_declared_where_the_lint_rule_reads_it():
    """`line-length` is the wrap target; pycodestyle's `max-line-length` is what E501 enforces."""
    cfg = _ruff_config()
    assert cfg["line-length"] == _WRAP_TARGET, (
        f"the wrap target moved to {cfg['line-length']} — the docstrings and comments that quote "
        f"{_WRAP_TARGET} as the number to wrap at are now wrong, starting with this file's."
    )
    pycodestyle = cfg["lint"].get("pycodestyle")
    assert pycodestyle is not None, (
        "[tool.ruff.lint.pycodestyle] is gone, so E501 has silently fallen back to `line-length` "
        f"= {_WRAP_TARGET}. That is not a tightening, it is a break: the repo carries lines in the "
        "101-120 band by an explicit, measured decision, and CI will now be red on all of them."
    )
    assert pycodestyle.get("max-line-length") == _HARD_LIMIT, (
        f"the enforced ceiling is {pycodestyle.get('max-line-length')!r}, not {_HARD_LIMIT}. "
        "Moving it is a legitimate decision and this assertion is where it gets made on purpose: "
        "LOWERING it needs the lines between the new value and 120 reflowed first (count them, do "
        "not trust this docstring's figure — it was only ever true at the sha that wrote it), and "
        "RAISING it needs a reason, because 120 was chosen as the smallest round number above "
        "every line that existed on the day the gate went in."
    )


def test_no_python_line_exceeds_the_hard_limit():
    """Belt to E501's braces, STRICTER in one way and NARROWER in another.

    Stricter: no exemption for an unwrappable long token, so it flags the long-URL shape ruff
    waves through. Narrower: it reads only the scan roots above, while ruff reads the repo. Both
    channels are needed because each covers the other's blind spot — this one survives `ignore`,
    `per-file-ignores` and a dropped CI step; ruff survives a new top-level package directory.
    """
    offenders = _overlong(_HARD_LIMIT)
    assert not offenders, (
        f"{len(offenders)} line(s) over {_HARD_LIMIT} CHARACTERS: "
        + "; ".join(f"{p}:{n} ({c} chars)" for p, n, c, _ in offenders[:10])
        + f". Wrap at {_WRAP_TARGET}. A line this far over is usually not a long sentence but a "
        "reflow accident — a paragraph re-wrapped by hand where one line absorbed the start of "
        "the next sentence instead of breaking. That is what tracker #669 fixed in api.py, and "
        "the reason the fix is a re-wrap rather than an edit: the words were all still there."
    )


def test_the_character_versus_byte_distinction_is_still_live_in_this_repo():
    """Pins the hazard the docstring above is about, so the warning cannot rot into folklore."""
    files = _python_files()
    fooled = [
        (path.relative_to(REPO_ROOT).as_posix(), lineno, len(line), len(raw))
        for path in files
        for lineno, line in enumerate(path.read_text(encoding="utf-8").split("\n"), 1)
        if len(line) <= _HARD_LIMIT < len(raw := line.encode("utf-8"))
    ]
    assert fooled, (
        "not one line in the repository is now within the character limit while exceeding it in "
        "BYTES. If that is real, the em-dash/Cyrillic prose is gone and the warning in this "
        "file's docstring — measure in characters, never with `awk length()` or `wc -c` — has "
        "become advice about a hazard that no longer exists. Re-measure before deleting it."
    )
