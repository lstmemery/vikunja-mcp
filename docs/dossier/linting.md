# Длина строки: два числа, гейт только одно

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Команды` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

**Line length is TWO numbers, and only one of them is a gate (tracker #669).** Wrap at **100** —
that is `line-length`, the formatter's target and what this repo wraps to by hand — not perfectly,
see the band below, but everywhere it matters. CI goes red at **111**: `E501` is selected
with `[tool.ruff.lint.pycodestyle] max-line-length = 110` (#711 lowered it from 120; see the
ratchet paragraph below for what that cost and what it did not). The gap
between them is honest slack, not an oversight; the reasoning with its counts lives in
`pyproject.toml` beside the settings and is pinned in `tests/unit/test_line_length_gate.py`. Three
things follow for anyone writing prose here, which is most tasks. **The band 101-110 is convention
with nothing behind it** — a 103-character line ships green, so keep measuring your own additions
rather than reading a green `ruff check` as "wrapped correctly". **Measure in CHARACTERS, never
bytes**: ruff does, the shell reflex (`awk '{print length($0)}'`, `wc -c`) does not, and this prose
is full of em-dashes (3 bytes) and Cyrillic (2 bytes each) — at `d857280`, 1626 `.py` lines sat
at or under 100 characters while a byte counter would call them violations, and 1004 `.py` lines
did the same at the 110 ceiling. Those are two SEPARATE sets and neither contains the other,
which is measured rather than argued: 989 lines are in both, 637 in the first only and 15 in the
second only. Both figures count prose, so they move with every landing — re-measure rather than
quote them: those same two limits replayed at `3db8ef9` give 1015 and 569. **And re-measure over
`.py` alone**, which is the whole of what ruff reads here: every line-count in this paragraph is
`.py`-only except one contrast pair — run those same two scans over EVERY file in the tree and
they give 3824 and 3072 instead, more than twice as many, which is what the scope is worth.
**The 413 this paragraph used to print beside the 1015 was the 120 ruler and retired with the
ceiling** — it is not the same measurement taken earlier, and reading it as one would understate
the second set by 156 at that very sha. VMCP-132 (621)'s worklog records the mistake in those
words — "an awk byte-count had falsely flagged one line because of the em-dash". In python it
is `len(line)`, not `len(line.encode())`. And **"red at 111" has exactly TWO measured
exemptions that ruff applies ON ITS OWN — a `# noqa` silences it
too, but that is an opt-out someone writes, not a decision the rule makes — and the one an earlier
draft of this paragraph named, "the overlong part contains no whitespace", is NOT among them.**
Measured at `d857280` on ruff 0.15.20: `# ` followed by 109 `x` is 111 characters with no
whitespace past the limit, and it
FIRES. What ruff actually exempts is (1) a line holding fewer than two whitespace-separated
chunks — one unbroken token, so there is no break to demand — where even 201 characters pass, and
(2) a line whose LAST chunk contains the literal `://` while the rest fits the limit: a
136-character comment ending in a URL passes, the same URL followed by one more word fires. The
rule there is arithmetic on the WHOLE line — `total − width(last chunk) ≤ 110` — so 110
characters ahead of the URL passes and 111 fires, but a trailing space counts into `total`, which
is why "everything before the URL" is a paraphrase and not the predicate. Exemption (2) is a
SUBSTRING test, not URL recognition — `foo://…` is exempt and `www.example.com/…` is not. The
pin has no exemption at all and flags both shapes; that disagreement is deliberate. One honest
bound on this whole paragraph: ruff measures DISPLAY WIDTH, not `len(line)`, so a tab or a CJK
character makes it red below 111 — measured, zero lines in `src`/`tests`/`scripts` differ between
the two at any of the four shas named in `tests/unit/test_line_length_gate.py` (this paragraph
names two of them), so the distinction is real but currently theoretical.

Before #669 there was no gate at all: `line-length` was set, `E501` was **not** selected (it is
absent from ruff's default `E4,E7,E9,F`), so `line-length` drove only the formatter — which this
repo does not run — and `ruff check .` was green on a 140-character comment. **And running the
formatter would not have saved it:** measured, `ruff format` on the pre-fix `api.py` reformats the
file and leaves the 140-character line at 140, because it re-wraps code and does not reflow comment
or string content — 36 of the 77 overlong lines. That is how the defect #669 fixed got in: a hand
re-wrap in which one line absorbed the start of the next sentence instead of breaking, invisible to
every tool, to the card that shipped it and to that card's reviewer.
**It is a ratchet, not a preference.** #669 set it at 120 — the smallest round number above every
line the repo still held once its own defect was reflowed — so the gate could go on THAT DAY
rather than after a cosmetic diff through 18 files, nine of them under active concurrent edit.
#711 has since ratcheted it to **110**, and what made that step affordable is a measurement, not
resolve: of the 102 lines then sitting in the 101-120 band, only SIX were above 110 — six lines
in FIVE files — so a handful of hand re-wraps bought it. Read "halved" as the band's WIDTH, not
its population: the diff moved seven lines (one collateral) and took 102 unchecked lines to 95.
The rest of the distribution is why the band was
NOT closed outright: **59 of those 102 were exactly 101 characters** and 18 more were 102, i.e. the
population is a one-or-two-character tail past the wrap target rather than long lines, and #669's
count of what it is made of still holds over ITS 77 — 41 code, 22 string literals, 14 comments.
Read that composition the way #711's own filed correction does, not the way its first draft did:
"just re-wrap them" is false for a little over HALF, not for two thirds. Nineteen of those 22
literals are DOCSTRINGS, which re-wrap like prose, so pure-prose re-wraps are 33 of 77 and edits
that have to preserve an expression or a string VALUE are 44 — and a docstring re-wrap is still
not free the way a comment is, because it changes a string constant and therefore shows up in the
AST, where a comment cannot. Lowering it further is the intended direction and the same
measurement is how to price the next step, which at `d857280` is already on the shelf: the tree
holds 95 lines over 100 characters and NONE over 109, so a step to 109 costs zero re-wraps, 105
costs six, 104 costs seven and 102 costs eighteen. The decision point is the `_HARD_LIMIT`
assertion in `tests/unit/test_line_length_gate.py`, which pyproject must agree with.
