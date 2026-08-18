"""Retention rule for the auto-release tag stream, as a pure function.

WHY THIS EXISTS. Every green push to `main` cuts a `vX.Y.Z` tag, so the tag list grows at the
rate the repo lands work — measured 331 tags over 41 days, about 8 a day. Nothing was reaping
them.

WHAT PRUNING DOES AND DOES NOT BUY, measured before the rule was written rather than assumed,
because the obvious motive is the wrong one. Every one of those 331 tags pointed at a commit
reachable from `origin/main`, so deleting all of them frees ZERO objects — the commits stay
alive through the branch. A fresh clone's `.git` was 4.8 MB with `packed-refs` at 36 KB, which
puts the whole tag list at roughly 0.6% of a clone, and the repo has no GitHub Releases at all,
so there is no releases page to clean either. The gain is READABILITY and nothing else, and that
is the reason the rule below keeps generously rather than aggressively: it is spending a real
guarantee to buy a cosmetic one.

WHAT A TAG IS EVIDENCE OF, which is what makes deleting one lossy. A tag here is cut only by the
`release` job, which runs only on green, so the tag is a durable record that CI passed at that
commit. GitHub's own run history is not durable — it expires (90 days by default) — so past that
horizon the tag is the ONLY surviving proof, and it cannot be reconstructed afterwards. That is
why `vX.Y.0` boundaries and one tag per calendar week are kept forever rather than aged out:
what survives has to stay a usable rollback surface, not merely a tail.

WHAT THE RULE DELIBERATELY NEVER TOUCHES. A name that is not `vX.Y.Z` is not a candidate at all,
in either direction. `scripts/release.sh` says why in its own comment: a foreign tag hung on the
tip by a human does not make it a release, and its own test stand creates a tag literally named
`main`. A pruner that reasoned about "old refs" instead of about this repo's version grammar
would eat those.
"""
import datetime as dt

import pytest

from scripts.prune_tags import Tag, select


BASE = dt.datetime.fromisoformat("2026-06-01T00:00:00+00:00")


def _t(name: str, when: str) -> Tag:
    return Tag(name=name, when=dt.datetime.fromisoformat(when))


# EIGHT tags a day for 30 days, oldest first — the shape the auto-release actually produces at
# this repo's landing rate (331 tags over 41 days, measured). A one-a-day fixture is the wrong
# stand and it hid a real property: with one tag per day the daily tier prunes NOTHING, so an
# idempotence round over it passes while proving nothing.
STREAM = [Tag(name=f"v0.2.{day * 8 + k}", when=BASE + dt.timedelta(days=day, hours=k))
          for day in range(30) for k in range(8)]
NEWEST = max(t.when for t in STREAM)


def test_a_short_history_loses_nothing():
    """Fewer tags than the recent window keeps every one of them. The first property anyone
    relies on: a young repo, or one already pruned, must be a no-op rather than a trim."""
    keep, delete = select(STREAM[:5], keep_recent=40, protected=set())
    assert delete == []
    assert [t.name for t in keep] == [t.name for t in STREAM[:5]]


def test_the_newest_window_is_kept_whole():
    """The rollback window is contiguous, not thinned: the newest `keep_recent` versions survive
    regardless of how they cluster in time. A day that lands eight tags must stay rollback-able
    at each of them, which is exactly the case weekly thinning would destroy."""
    keep, _ = select(STREAM, keep_recent=10, protected=set())
    newest = [t.name for t in STREAM[-10:]]
    assert set(newest) <= {t.name for t in keep}


def test_a_minor_boundary_is_kept_however_old():
    """`vX.Y.0` is permanent. It is the tag a human reaches for when rolling back past a bad
    stretch, and unlike a patch it names a deliberate decision rather than a landing."""
    old_minor = _t("v0.1.0", "2026-01-01T00:00:00+00:00")
    keep, delete = select([old_minor, *STREAM], keep_recent=5, protected=set())
    assert "v0.1.0" in {t.name for t in keep}
    assert "v0.1.0" not in {t.name for t in delete}


def test_beyond_the_window_one_tag_a_week_survives():
    """The weekly tier, reached here by passing `daily_days=0` — which is also the round that
    pinned 0 as a real OFF switch rather than "everything inside 24 hours". The half that
    matters: what survives each ISO week is the NEWEST tag in that week, not an arbitrary
    member. Rolling back to a week means rolling back to the last good state of it — the
    earliest would hand back a state the week itself had already moved on from."""
    keep, _ = select(STREAM, keep_recent=5, daily_days=0, protected=set())
    kept = {t.name for t in keep}
    # Only tags kept BY THINNING: a `vX.Y.0` in the same week is kept by its own permanent rule
    # and legitimately sits beside the week's survivor. Asserting one-per-week over the union
    # would be asserting that the minor boundary is aged out, which is the opposite of the rule.
    thinned = [t for t in STREAM[:-5] if t.name in kept and t.version[2] != 0]
    by_week: dict[tuple, list[str]] = {}
    for t in thinned:
        by_week.setdefault(t.when.isocalendar()[:2], []).append(t.name)
    assert all(len(names) == 1 for names in by_week.values()), by_week
    # and each survivor is its week's newest
    for (year, week), names in by_week.items():
        same_week = [t for t in STREAM[:-5] if t.when.isocalendar()[:2] == (year, week)]
        assert names == [same_week[-1].name]


def test_between_the_window_and_the_weeks_one_tag_a_day_survives():
    """The middle tier, and the measurement that put it there. Weekly thinning alone left FOUR
    rollback points across the repo's first month — on a repo landing ~8 tags a day, which makes
    the 5-to-30-day range, where an incident-driven rollback actually lands, the emptiest part of
    the surface. So inside `daily_days` of the newest tag the survivor is per DAY, and only past
    that per week.

    The horizon is measured from the NEWEST TAG rather than from wall-clock now, so the function
    stays pure and a run of it is reproducible: a rule keyed to `datetime.now()` gives a
    different answer on a re-run of the same input, which is not a property a destructive tool
    should have."""
    keep, _ = select(STREAM, keep_recent=5, daily_days=20, protected=set())
    kept = {t.name for t in keep}
    newest = max(t.when for t in STREAM)
    daily = [t for t in STREAM[:-5]
             if t.name in kept and t.version[2] != 0 and (newest - t.when).days <= 20]
    by_day: dict[dt.date, list[str]] = {}
    for t in daily:
        by_day.setdefault(t.when.date(), []).append(t.name)
    assert all(len(names) == 1 for names in by_day.values()), by_day
    # the daily tier is strictly more generous than weekly alone on the same input
    weekly_only, _ = select(STREAM, keep_recent=5, daily_days=0, protected=set())
    assert len(keep) > len(weekly_only)


def test_a_protected_tag_survives_a_rule_that_would_have_dropped_it():
    """`stable`'s tag and the tip's tag are passed in as protected. This is the pin that keeps a
    prune from cutting the ref the channel is currently pointing at — the one deletion that would
    turn a cosmetic cleanup into an outage of the documented rollback path."""
    doomed = STREAM[0].name                       # oldest, and not a week's newest
    keep, delete = select(STREAM, keep_recent=5, protected={doomed})
    assert doomed in {t.name for t in keep}
    assert doomed not in {t.name for t in delete}


def test_a_protected_name_that_is_not_in_the_list_is_harmless():
    """Protection is a filter, never a source: naming a tag that does not exist must not
    materialise it into the keep set, or a stale `stable` read would invent a ref."""
    keep, delete = select(STREAM, keep_recent=5, protected={"v9.9.9"})
    assert "v9.9.9" not in {t.name for t in keep} | {t.name for t in delete}


@pytest.mark.parametrize("name", ["main", "nightly", "v1.2", "v1.2.3.4", "release-2026", "v1.2.3a"])
def test_a_name_outside_the_version_grammar_is_never_a_candidate(name):
    """Not deleted, and not counted as kept-by-the-rule either — simply not this pruner's
    business. `scripts/release.sh` records the case that motivates it: its own stand hangs a tag
    named `main` on the tip, and a human's foreign tag does not make a commit a release."""
    stranger = _t(name, "2026-01-01T00:00:00+00:00")
    keep, delete = select([stranger, *STREAM], keep_recent=5, protected=set())
    assert name not in {t.name for t in delete}
    assert name not in {t.name for t in keep}


def test_running_it_again_deletes_nothing():
    """Idempotence, stated as a round rather than as an intention: feeding the survivors back in
    must be a no-op. A prune that keeps finding work on an unchanged tag list would delete its
    own weekly survivors one week at a time, which is how a 'keep one per week' rule quietly
    becomes 'keep none'."""
    keep, delete = select(STREAM, keep_recent=5, protected=set())
    assert delete, "the fixture is meant to have something to prune"
    again_keep, again_delete = select(keep, keep_recent=5, protected=set())
    assert again_delete == []
    assert {t.name for t in again_keep} == {t.name for t in keep}


def test_ordering_of_the_input_does_not_change_the_answer():
    """The caller feeds whatever `git for-each-ref` printed. Sorting is this function's job, and
    a rule that silently depended on input order would be a different rule on a different git."""
    forward, _ = select(STREAM, keep_recent=5, protected=set())
    backward, _ = select(list(reversed(STREAM)), keep_recent=5, protected=set())
    assert {t.name for t in forward} == {t.name for t in backward}


def test_version_order_is_numeric_and_not_lexicographic():
    """`v0.2.9` is older than `v0.2.10`, which string sorting gets backwards — and at this repo's
    three-digit patch numbers a lexicographic window would keep `v0.2.99` and drop `v0.2.320`,
    throwing away the newest releases while reporting a healthy count."""
    tags = [Tag(name=f"v0.2.{n}", when=BASE + dt.timedelta(minutes=n))
            for n in (9, 10, 99, 100, 320)]
    keep, delete = select(tags, keep_recent=2, protected=set())
    assert {t.name for t in keep} >= {"v0.2.100", "v0.2.320"}
    assert "v0.2.320" not in {t.name for t in delete}
