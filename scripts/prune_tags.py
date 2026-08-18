"""Retention for the auto-release tag stream: decide which `vX.Y.Z` tags to drop.

Every green push to `main` cuts a tag, so the list grows at the rate the repo lands work (~8 a
day, measured). This reaps the middle of it while keeping the surface a rollback actually needs.

WHAT IS KEPT, in the order the rule applies:
  1. every `vX.Y.0` — the minor/major boundaries, permanently;
  2. the newest `--keep-recent` versions, as a CONTIGUOUS window (the practical rollback range);
  3. one tag per DAY for anything older than that window but within `--daily-days` of the newest;
  4. one tag per ISO calendar week beyond that;
  5. the tag `stable` points at, and the tag on the tip of `main`.
Everything else is deleted. A name outside the `vX.Y.Z` grammar is not a candidate in either
direction: `scripts/release.sh` records why (a foreign tag on the tip does not make it a
release, and that script's own stand creates a tag named `main`).

WHY THE RULE IS GENEROUS. Pruning here buys READABILITY and nothing else: every tag points at a
commit reachable from `main`, so deleting one frees no objects, and the whole tag list is about
0.6% of a fresh clone. Against that, a tag is cut only by the green-only `release` job, which
makes it a durable record that CI passed at that commit — durable in a way GitHub's own run
history is not, since runs expire. So the trade is a cosmetic gain against a real, unreconstructible
loss, and the retention is sized accordingly.

The decision is a pure function (`select`) so the rule is pinned by unit tests rather than by
reading YAML; the CLI wrapper only talks to git. It never deletes on its own — `--apply` is
required, and without it the plan is printed and nothing is touched.
"""
from __future__ import annotations

import argparse
import re
import subprocess
import sys
from dataclasses import dataclass
from datetime import datetime

# `vX.Y.Z`, all three numeric and nothing else. Deliberately NOT a loose "starts with v": the
# grammar is the whole definition of "a tag this pruner owns".
VERSION_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")

DEFAULT_KEEP_RECENT = 40
# Below this age (from the newest tag) thinning is per DAY; beyond it, per ISO week.
DEFAULT_DAILY_DAYS = 30


@dataclass(frozen=True)
class Tag:
    name: str
    when: datetime

    @property
    def version(self) -> tuple[int, int, int] | None:
        m = VERSION_RE.match(self.name)
        return (int(m[1]), int(m[2]), int(m[3])) if m else None


def select(
    tags: list[Tag], *, keep_recent: int = DEFAULT_KEEP_RECENT,
    daily_days: int = DEFAULT_DAILY_DAYS, protected: set[str],
) -> tuple[list[Tag], list[Tag]]:
    """Split version tags into (keep, delete). Non-version names are returned in NEITHER list.

    Sorting is done here rather than trusted from the caller: the input is whatever
    `git for-each-ref` printed, and a rule that depended on that order would be a different rule
    on a different git. Ordering is by parsed version tuple, never lexicographic — at this repo's
    three-digit patch numbers string order puts `v0.2.99` above `v0.2.320`, which would keep the
    oldest releases and delete the newest while reporting a perfectly healthy count.

    The age horizon is measured from the NEWEST TAG, not from wall-clock now. That keeps the
    function pure and its answer reproducible: a destructive tool whose plan changes between two
    runs over identical input is one nobody can review before letting it run.
    """
    versioned = sorted(
        (t for t in tags if t.version),
        key=lambda t: t.version,  # type: ignore[arg-type,return-value]
    )
    if keep_recent > 0:
        recent, older = versioned[-keep_recent:], versioned[:-keep_recent]
    else:
        recent, older = [], versioned

    keep_names = {t.name for t in recent}
    keep_names |= {t.name for t in versioned if t.version[2] == 0}      # type: ignore[index]
    keep_names |= {t.name for t in versioned if t.name in protected}

    # Thinning, in two tiers. The survivor of a bucket is always its NEWEST member: rolling back
    # "to that day/week" means rolling back to the last good state of it — the earliest would
    # hand back a state the period itself had already superseded.
    #
    # The daily tier exists because weekly alone was measured leaving FOUR points across this
    # repo's first month, emptying exactly the 5-to-30-day range an incident rollback reaches for.
    # `daily_days = 0` DISABLES the daily tier, and the comparison is strict for that reason: an
    # `age.days <= daily_days` spelling makes 0 mean "everything inside 24 hours", so the tier
    # could not be turned off and the weekly bucket of the newest week silently kept a SECOND
    # survivor beside it. Caught by the weekly test, which asks for the weekly tier by passing 0.
    newest = max((t.when for t in versioned), default=None)
    buckets: dict[tuple, Tag] = {}
    for t in older:
        fresh = (newest is not None and daily_days > 0
                 and (newest - t.when).days < daily_days)
        key = ("day", t.when.date()) if fresh else ("week", *t.when.isocalendar()[:2])
        if key not in buckets or t.when > buckets[key].when:
            buckets[key] = t
    keep_names |= {t.name for t in buckets.values()}

    keep = [t for t in versioned if t.name in keep_names]
    delete = [t for t in versioned if t.name not in keep_names]
    return keep, delete


# --- git side, deliberately thin -------------------------------------------------------------

def _git(*args: str) -> str:
    out = subprocess.run(["git", *args], capture_output=True, text=True)
    if out.returncode != 0:
        raise SystemExit(f"git {' '.join(args)} failed: {out.stderr.strip()}")
    return out.stdout


def read_tags() -> list[Tag]:
    """Local tags with their creation dates. `creatordate` is the tagger date for an annotated
    tag (which is what the release job cuts) and the committer date for a lightweight one, so it
    answers "when was this release made" for both spellings."""
    raw = _git("for-each-ref", "--format=%(refname:short)%09%(creatordate:iso8601-strict)",
               "refs/tags")
    tags = []
    for line in raw.splitlines():
        name, _, when = line.partition("\t")
        if name and when:
            tags.append(Tag(name=name, when=datetime.fromisoformat(when)))
    return tags


def protected_names(tags: list[Tag]) -> set[str]:
    """Tags that must survive whatever the rule says: any tag pointing at the tip of the release
    channel, and any pointing at the tip of `main`. Read by SHA rather than by name so a channel
    sitting on an untagged commit is simply an empty answer instead of an error."""
    names = set()
    for ref in ("refs/remotes/origin/stable", "refs/remotes/origin/main", "HEAD"):
        head = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{ref}^{{commit}}"],
                              capture_output=True, text=True)
        if head.returncode != 0:
            continue
        sha = head.stdout.strip()
        for t in tags:
            peeled = subprocess.run(["git", "rev-parse", "-q", "--verify", f"{t.name}^{{commit}}"],
                                    capture_output=True, text=True)
            if peeled.returncode == 0 and peeled.stdout.strip() == sha:
                names.add(t.name)
    return names


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="prune_tags")
    p.add_argument("--keep-recent", type=int, default=DEFAULT_KEEP_RECENT,
                   help="size of the contiguous newest-versions window "
                        f"(default {DEFAULT_KEEP_RECENT})")
    p.add_argument("--daily-days", type=int, default=DEFAULT_DAILY_DAYS,
                   help=f"keep one tag per day within this many days of the newest "
                        f"(default {DEFAULT_DAILY_DAYS}); older than that, one per ISO week")
    p.add_argument("--apply", action="store_true",
                   help="actually delete on origin; without it the plan is printed "
                        "and nothing moves")
    p.add_argument("--remote", default="origin")
    args = p.parse_args(argv)

    tags = read_tags()
    keep, delete = select(tags, keep_recent=args.keep_recent, daily_days=args.daily_days,
                          protected=protected_names(tags))
    foreign = [t.name for t in tags if not t.version]

    print(f"version tags: {len(keep) + len(delete)}  keep: {len(keep)}  delete: {len(delete)}")
    if foreign:
        print(f"not this pruner's business, untouched: {sorted(foreign)}")
    for t in delete:
        print(f"  delete {t.name}  ({t.when.date()})")

    if not delete:
        print("nothing to prune")
        return 0
    if not keep:
        # A rule that kept nothing would mean the grammar or the input changed under us; deleting
        # every release tag is never the right answer to that.
        print("refusing: the retention rule kept NO tags, which cannot be right", file=sys.stderr)
        return 1
    if not args.apply:
        print("\ndry run — pass --apply to delete these on the remote")
        return 0

    # One push, many refspecs: a single round trip, and a partial failure is visible as such
    # rather than as a half-finished loop.
    _git("push", args.remote, *[f":refs/tags/{t.name}" for t in delete])
    print(f"deleted {len(delete)} tags on {args.remote}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
