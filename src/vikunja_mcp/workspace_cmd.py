"""`vikunja-mcp workspace` — per-task git worktrees for the parallel drain.

THE ONLY MODULE IN THIS PACKAGE THAT RUNS GIT. server.py / workflow.py / api.py stay git-free
on purpose: the MCP server's job is HTTP to Vikunja, and a subprocess in that path would be a
new class of failure on a stdio server that must never crash.

WHY IT LIVES HERE AT ALL. `git worktree add` refuses to check out a branch that is already
checked out elsewhere, so two parallel agents cannot both sit on `main` — each needs its own
tree on its own throwaway `task/<id>` branch, and pushes with `git push origin HEAD:main` so
the "one task = one commit on main" rule and the CI auto-release survive untouched. Creating
that is trivial; REAPING it is not, and reaping is the part only the tracker can do — nothing
else knows whether the task behind an orphaned tree is still alive (see gc_workspaces).

SAFETY INVARIANT, taken verbatim from hgdev-acp's reaper: push OK -> remove, push FAIL -> KEEP.
Housekeeping must never be how an agent's work disappears.
"""
import argparse
import fcntl
import functools
import json
import os
import re
import subprocess
import time
from contextlib import contextmanager
from pathlib import Path

BUILD_NAME = "task-{task_id}"
REVIEW_NAME = "review-{task_id}"
BUILD_BRANCH = "task/{task_id}"
_NAME_RE = re.compile(r"^(task|review)-(\d+)$")
_ROLE_BY_PREFIX = {"task": "build", "review": "review"}

# WHY A `--release`/`--gc` REFUSAL CARRIES A CODE (VMCP-68), AND A CREATE REFUSAL DOES NOT
# (VMCP-110). This header used to read "WHY EVERY REFUSAL CARRIES A CODE" and was false of the
# create path in the same breath as its own body, which scopes the justification to `--gc`. Say the
# scope in the title: two later documents copied the universal out of here and had to be corrected.
# The codes below are produced ONLY by `_release_locked` and `gc_workspaces`. `_ensure_locked`
# refuses by RAISING `WorkspaceError`, which `run_workspace`'s catch-all renders as `{"error": …}`
# + exit 1 — no `code` key, on purpose: a code exists to feed a GRADER (`_keep_is_expected`, the
# only one in this package), and an orchestrator's answer to EVERY create refusal is the same
# single branch (SKILL.md's «Не завелось — цикл НЕ роняем»: degrade to one slot, keep draining).
# And it could not be made universal anyway — that catch-all covers an OPEN set (a non-repo, a
# malformed toml, a `ReadDeadlineExceeded`, an OSError), so a code there would be present-SOMETIMES,
# which is worse to parse than absent-always. Pinned both ways by
# test_the_two_refusal_channels_are_not_interchangeable; change the split and the docs move with it.
#
# THE PREDICATE THAT CARRIES A CODE IS `released: false`, NOT THE WORD "REFUSAL" (VMCP-142). A
# `--release` can still RAISE, and then it wears the create channel's shape by construction —
# `{"error": …}` + exit 1, no code — because it went through the same catch-all: a non-git cwd, a
# malformed toml, anything in that open set. What VMCP-142 closed is the state git's OWN `worktree
# list --porcelain` already names, so the module can recognise it BEFORE touching the tree: a tree
# pinned by `git worktree lock` (four spellings measured — with a reason, reasonless, on a review
# tree, and a locked entry whose directory is gone). Not every raise with intact work is like that
# — a worktree directory git cannot delete (mode 0500) still raises, and the gc isolation test
# depends on it doing so; what makes the lock codeable is that it is a NAMED git state, not an
# OS-level surprise. Quote the invariant over `released: false`; "every release-side refusal is
# coded" is the sentence that keeps drifting back into the docs, and it is FALSE both before and
# after 142 — 142 removed the instance that mattered, not the class.
#
# `--gc` has to grade its own refusals — routine vs
# "a human should look" (see _keep_is_expected) — and the only other thing a refusal carries is
# `reason`, which is PROSE: human-facing, deliberately reworded whenever a message turns out to
# mislead (the half-created diagnosis was reworded exactly that way). Grading on a substring of it
# would make every future rewording a silent reclassification. So the classification keys on these,
# and the prose stays free to change. Public, unprefixed, and asserted against SKILL.md by
# tests/unit/test_skill_contract.py: they are part of the CLI's JSON line, which the rulebook tells
# agents to read, so a value change here must drag the rulebook along.
CODE_NO_WORKTREE = "no-worktree"
CODE_HALF_CREATED = "half-created"
CODE_LOCKED = "locked"                # a human `git worktree lock` — see _release_locked's guard
CODE_DIRTY = "dirty"
CODE_UNPUSHED = "unpushed"
CODE_UNREACHABLE_HEAD = "unreachable-head"
CODE_DETACHED_BUILD = "detached-build"
CODE_SELF_TREE = "self-tree"          # --gc only: the tree gc itself is standing in
CODE_RELEASE_ERROR = "release-error"  # --gc only: _release_locked raised, sweep continued

# Review Important 2: EVERY git call in this module can run while `_repo_lock` is HELD (the
# network one — `git fetch origin` in _ensure_locked — provably does, before the idempotency
# early-return), so a call that blocks forever does not merely hang ITS caller: it wedges every
# other agent's ensure/--release/--gc on this repo, permanently, with no diagnostic. The things
# that block forever are closed here, in the ONE helper, so no call site can forget:
#   * an https credential prompt (no helper configured) -> GIT_TERMINAL_PROMPT=0;
#   * anything reading the inherited stdin -> stdin=DEVNULL, which is EOF and never a wait;
#   * an ssh host-key/passphrase prompt (ssh reads /dev/tty DIRECTLY, so neither of the two
#     above touches it) and a black-holed TCP connection -> the timeout below, the only
#     backstop that also covers what we cannot name in advance. Deliberately NOT closed by
#     exporting GIT_SSH_COMMAND="ssh -o BatchMode=yes": that env var OVERRIDES the user's
#     `core.sshCommand`, so injecting it would silently discard a configured identity
#     (`ssh -i ~/.ssh/id_rsa_…`, exactly how some of our own boxes are reached) and break
#     fetch outright for setups that work today — a new failure traded for a bounded stall.
# TWO bounds, because a timeout is itself a kill and the two kinds of call differ in what a kill
# COSTS. The network one (`fetch`) is the only call that can hang on something outside this
# machine, and killing it costs nothing — a half-fetched pack is discarded — so it gets the tight
# bound: 120s, an eternity for an incremental fetch on an already-cloned repo, and short enough
# that a wedged one frees the lock well inside an orchestrator tick instead of stacking ticks.
# Everything else is local disk and can only be SLOW, never hung on a peer — but killing a
# `worktree add` mid-checkout is destructive in a quiet way: git registers the admin dir with a
# "locked / initializing" marker BEFORE checking out, so a kill leaves an entry that `prune`
# refuses to drop and `_find` happily hands back as `created: false` — an agent dispatched into a
# half-populated tree. So local calls get a ceiling that exists only to catch a genuine hang
# (600s), never to police a big checkout on a slow disk.
_GIT_TIMEOUT = 600.0
_GIT_NET_TIMEOUT = 120.0

# git's OWN lock reason, written into `.git/worktrees/<n>/locked` by `worktree add` BEFORE it
# checks anything out and removed once the checkout finishes. A surviving one therefore means
# exactly one thing: that add never got to the end. Constructed and measured on git 2.50.1 (a
# smudge filter that sleeps + the _GIT_TIMEOUT kill above, no external killer needed): the entry
# stays listed as `locked initializing`, `git worktree prune` exits 0 and REFUSES to drop it, and
# the directory holds nothing but `.git` — every tracked file missing, the index all staged
# deletions. Which is why `_release_locked` used to call it "working tree is dirty (N entries)".
#
# MEASURED AND COUNTER-INTUITIVE, so do not "simplify" the guard on the assumption of a missing
# file: the state does NOT stay half-populated. `git worktree add` does the checkout in a CHILD
# (`git reset --hard --no-recurse-submodules`), and SIGKILLing the parent orphans that child onto
# PID 1, where it keeps going — files appeared 30s and 60s after the kill, one sleeping smudge
# each, until the tree was COMPLETE. What never happens is the marker being cleared, because the
# process that clears it is the parent we killed. So there are two phases and only the second is
# stable: a tree that may look perfectly fine and is locked FOREVER — which means git will refuse
# `--release`/`--gc` removal for good, and it leaks until a human intervenes. Hence a guard keyed
# on the lock's PRESENCE (any file-content heuristic would pass in phase two) and a message that
# does not promise which phase the reader is looking at.
_LOCK_INITIALIZING = "initializing"

# THE GRACE WINDOW (VMCP-71). `--gc` runs at tick start from the MAIN checkout, and a build tree is
# alive only while its task sits in Design/Build assigned to me — so the tree reads DEAD the instant
# its agent calls `advance(to='review')`, while that agent is still standing in it and has not yet
# called `--release`. Nothing else catches that overlap: the self-guard in gc_workspaces only covers
# a `--gc` invoked from INSIDE the tree, and `git push origin HEAD:main` moves the LOCAL
# `origin/main` ref, so the unpushed guard passes and the tree is removed with its branch. Under a
# parallel drain the overlap is the NORM — background agents outlive the orchestrator's turn — and
# the review side has the mirror case: `review_task(verdict='needs_work')` moves the card Review->
# Build, so a reviewer's tree dies the moment it files that verdict. Nothing is DESTROYED (only
# clean, fully-pushed trees are removed, and that work is already on main); the cost is a working
# directory vanishing under a running turn, which surfaces as confusing tool errors while an agent
# composes its report.
#
# HOW MUCH OF THAT MIRROR CASE THE WINDOW ACTUALLY COVERS (VMCP-84): CONDITIONALLY, and the
# condition is not the one the sentence above suggests. This window is measured from a WRITE, and a
# review tree can live its whole life without one — a reviewer that only READS (the Read tool,
# `git log`, `git show`) moves neither marker `_last_activity` looks at. What such a tree has
# instead is its BIRTH: `git worktree add` sets both, so the protection runs for
# `_REAP_GRACE_SECONDS` FROM CREATION rather than from the verdict. A review that fits inside the
# window is covered exactly like a build tree (and most do — which is why this reads as free); a
# LONGER read-only review is covered by nothing at all, and the first sweep after its `needs_work`
# may take the directory out from under it. Reviewers that run the suite or a `git diff` in their
# tree bump a marker and rejoin the covered case; ONLY the long, purely-read-only review is exposed.
# LEFT AS IS, deliberately, and this paragraph is the decision: the exposure is bounded to a
# vanishing cwd (a review tree is detached and clean, an in-tree commit is refused by the
# reachability guard, so there is nothing there to destroy), while every fix reintroduces something
# already rejected above — a FACT-based signal (nothing holds a process across an LLM's tool calls),
# or keeping a tree alive on a card the reaper must not be made to wait for. Touching a marker just
# to be seen would be gc's own bug (VMCP-90) rebuilt on the other side. SKILL.md's standing rule —
# never assume your tree survived `advance`/a verdict, re-`ensure` it — is what covers the rest, and
# it is a rule for BOTH roles precisely because this window is not a promise to either.
#
# WHY A CLOCK AND NOT A FACT. The semantically exact signal would be a held flock, and an LLM
# sub-agent holds no process across tool calls, so there is nothing to hold it. REJECTED (recorded
# so it is not re-proposed): treating a build tree as alive while its card sits in Review — a card
# waits in Review until a HUMAN moves it to Done, which would suspend the reaper indefinitely and
# defeat the module's purpose.
#
# WHY 30 MINUTES — derived from the window it must cover, not picked. That window is (last
# filesystem write inside the tree) -> (`--release`). By SKILL.md's own integration recipe the last
# write is the rebase / re-run of the done criteria just before `git push origin HEAD:main`; after
# it come the push, `git rev-parse HEAD`, the model turn that composes and calls
# `advance(to='review')` with the full work report (the largest single term: a long report with
# extended thinking, possibly a harness-retried API error), and the model turn that calls
# `--release`. Three to four LLM tool-call turns: 1-3 min typical, ~10-15 min pathological. 30 min is
# ~2x that pathological estimate and ~3 ticks of a `/loop 10m`. Rounded UP on purpose, because the
# costs are asymmetric: a dead tree lingering extra sweeps costs one directory on disk for at most
# this window plus a tick, blocks nothing (tree names are per task+role and `_find` reuses an
# existing one) and cannot delay a CRASHED agent's tree (that task is still in Design/Build, i.e.
# still alive, so this window never sees it) — while a window too short reintroduces the race
# itself. Deliberately a constant and not a config key: it is a bound on agent latency, not a
# per-repo preference, and SKILL.md keeps telling agents not to rely on their tree surviving
# `advance` at all — this is a backstop, not a promise.
_REAP_GRACE_SECONDS = 30 * 60

# THE SWEEP-READ BOUNDS (VMCP-72). Two numbers, because gc's liveness read is SEVERAL requests
# and the lock it holds is repo-wide: `_READ_TIMEOUT_SECONDS` bounds ONE request,
# `_READ_DEADLINE_SECONDS` bounds the WHOLE read. The per-request bound alone cannot bound the
# hold, because the request COUNT is not a constant.
#
# MEASURED against the real tracker (public https, 3 rounds, read-only, board as VMCP-68 left
# it): the read is FOUR requests — GET /projects/<p>/views, GET /info, GET
# /projects/<p>/views/<v>/tasks?page=1, GET /user — totalling 0.89-1.10 s. (`buckets` is NOT in
# this path; workflow._bucket() is only used for MOVES.) `liveness_board` passes
# require_titles={Design,Build,Review,"Your Call"}, so paging stops once those buckets stop
# returning full pages: requests = 3 fixed + floor(max(|Design|,|Build|,|Review|,|Your Call|) /
# page_size) + 1, where page_size is the server's max_items_per_page (50 here).
#
# WHY THAT GROWS. TWO of those four columns are drained by a HUMAN, not by the pump: a card
# waits in Review until someone moves it to Done, and in Your Call until someone answers it.
# Review held 41 cards when this was written — nine short of the 50 that adds a page, and it
# gained one during the session that measured it. So the request count rises by one per 50 cards
# in EITHER column and has no upper bound, and with it the hold: MEASURED in a lab (a
# slow-but-correct fake Vikunja at 3 s/request, real httpx + real api.py), today's shape =
# 4 requests = 12.03 s held; 140 in Review = 6 requests = 18.03 s; 140 in Your Call and an EMPTY
# Review = 6 requests = 18.04 s, i.e. the newer column drives it exactly as hard. Exactly
# requests x latency. At the per-request ceiling that is 40 s of held lock today, 60 s at 140
# cards, unbounded upward. Everything queued behind it — every agent's `--release`, every
# `ensure` for a dispatch — waits that long, and at wip_limit = 3 those are precisely the agents
# trying to clean up after themselves.
#
# WHY 30 s. It must never fire on a tracker that is merely SLOW (a false abandon costs a skipped
# sweep), and must be small next to the tick it delays. 30 s is: >= 3x the per-request bound, so
# a single legitimately slow request is never truncated by the TOTAL; ~26-33x the measured
# healthy read; enough for 15 requests at a degraded 2 s each (~600 cards in Review) before a
# working read is abandoned; and 5% of a `/loop 10m` tick. Like `_REAP_GRACE_SECONDS` it is a
# constant and not a config key — a bound on housekeeping latency, not a per-repo preference.
#
# REJECTED, on the measurement above: (a) a CHEAPER liveness query — 3 of the 4 requests are
# FIXED overhead, so it could at best remove the ONE board page and would still leave the hold
# proportional to nothing it controls; and none exists anyway, since a task's stage is knowable
# only from the kanban board (Vikunja 2.3's task JSON carries no per-view bucket) so the
# alternative is a per-tree get_task — up to 2 x wip_limit requests, MORE than the one it
# replaces — and since VMCP-68 that one fetch has THREE consumers (active/review/parked), so a
# cheaper query has to answer all three or the saving is imaginary. (b) ACCEPTING the current
# bound and documenting the worst case — a worst case that grows by 10 s per 50 cards in a
# column only a human drains is not a bound. (c) A NON-BLOCKING lock (recorded in the dossier
# before this task existed) — it bounds how long gc WAITS, and the cost is how long it HOLDS.
#
# WHAT IS AND IS NOT BOUNDED. This covers the tracker READ. The rest of the hold is local git,
# already ceilinged per call by `_GIT_TIMEOUT` and in practice milliseconds per tree; the total
# hold is therefore (<= 30 s of read) + (local git per tree on disk).
_READ_TIMEOUT_SECONDS = 10.0
_READ_DEADLINE_SECONDS = 30.0


class WorkspaceError(Exception):
    """The message is printed as the CLI's JSON error line."""


class ReadDeadlineExceeded(WorkspaceError):
    """The sweep's liveness read ran out of its OVERALL budget — reported, nothing reaped.

    A `WorkspaceError` SUBCLASS, and that inheritance is a safety decision, not tidiness. Two
    layers of api.py would eat this if it were an httpx exception instead:
      * `_fetch_page_size` catches `(VikunjaError, httpx.HTTPError)` and resolves the page size
        to UNKNOWN — a spent budget would be SWALLOWED there and the read would carry on past its
        own deadline (and, VMCP-89, on an unknown page size it carries on paging EXHAUSTIVELY,
        so a swallowed deadline would cost more requests, not fewer);
      * `_req` retries `httpx.TransportError` on idempotent methods, so with retries ever
        re-enabled a deadline would be re-attempted rather than obeyed.
    Being a WorkspaceError, it propagates straight out of the read — before `gc_workspaces`
    enters its reap loop — and lands on the CLI's own `except Exception` as one JSON error line
    with exit 1, the same shape a `--gc` that cannot reach the tracker already has (SKILL.md: an
    erroring `--gc` degrades the pump, it does not stop it). MEASURED end to end through the CLI:
    exit 1 at 30.26 s on a read that would have taken 36 s, every tree still on disk — including
    one that was clean, pushed and otherwise due to be reaped — and the very next sweep against a
    healthy tracker succeeding in 0.74 s, i.e. the abandon released the lock rather than leaking
    it.

    Public (no leading underscore) on purpose: the class name IS the CLI's error string, and
    `{"error": "ReadDeadlineExceeded: ..."}` tells a human reading the pump's log that the
    tracker was too slow, not that the worktrees are broken.
    """


class _ReadDeadline:
    """An overall budget for gc's liveness read, enforced as an httpx REQUEST event hook.

    Enforced at the hook rather than around the call because the read's cost is spread over
    several requests inside `liveness_board()`/`active_task_ids()`, and there is no safe way to
    abandon a call from the outside: a thread that times out does not stop the socket read it is
    blocked in, so the lock would be released while a request was still in flight.

    Each request does two things:
      * REFUSE when the budget is spent — raising BEFORE the request is sent, so the abandon
        costs nothing and, crucially, happens before any liveness set exists to act on;
      * CLAMP that request's own timeout to what is LEFT, so the last request cannot overshoot
        the budget by a whole `_READ_TIMEOUT_SECONDS`. `request.extensions["timeout"]` is httpx's
        documented per-request override and is read by httpcore at send time; MEASURED honoured
        on httpx 0.28.1 against a slow server — a 10 s budget on a read that needs 18 s returned
        at 9.96 s, not 12 s.
    The clamp is why the budget's failure does not always name itself — a clamped request dies as
    httpx's own `ReadTimeout` — and why `_read_liveness` relabels one that fires with the budget
    already spent. Read that note before changing either half.

    ARMED EXPLICITLY, by `_read_liveness`, once its caller holds the lock. It is also
    armed at construction, so a caller that forgets still gets a bounded read rather than an
    unbounded one; the failure of forgetting is then a budget that started slightly early, never
    one that never starts. Deliberately NOT disarmed after the board fetch: `active_task_ids()`
    still issues the `/user` request, and any request a future sweep adds is covered by
    construction rather than by remembering to extend the window.

    `now` is injectable so the behaviour can be tested without sleeping; the default is
    `time.monotonic` (a DURATION must not move when NTP steps the wall clock — unlike
    `_last_activity`, which compares against file mtimes and therefore must use `time.time`).
    """

    def __init__(self, budget: float, now=time.monotonic) -> None:
        self.budget = budget
        self._now = now
        self.arm()

    def arm(self) -> None:
        self._expires_at = self._now() + self.budget

    def spent(self) -> bool:
        """Is the budget gone? Asked by `_read_liveness` to tell the budget's own doing from an
        unrelated failure that merely happened while it was running."""
        return self._now() >= self._expires_at

    def __call__(self, request) -> None:
        remaining = self._expires_at - self._now()
        if remaining <= 0:
            raise ReadDeadlineExceeded(
                f"the liveness read exceeded its {self.budget:.0f}s overall budget at "
                f"{request.method} {request.url.path} — the sweep was abandoned with the repo "
                f"lock released and NOTHING inspected or removed; the next tick sweeps again"
            )
        # every key explicitly, not just the ones already present: httpx always populates all
        # four (connect/read/write/pool) from the client's Timeout, but a missing mapping must
        # clamp rather than silently leave the request unbounded.
        current = request.extensions.get("timeout") or {}
        request.extensions = {
            **request.extensions,
            "timeout": {
                key: remaining if current.get(key) is None else min(current[key], remaining)
                for key in ("connect", "read", "write", "pool")
            },
        }


def _run_git(
    args: tuple[str, ...], cwd: Path | None, timeout: float | None,
    env_extra: dict[str, str] | None = None,
) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0", **(env_extra or {})}
    limit = _GIT_TIMEOUT if timeout is None else timeout
    try:
        return subprocess.run(
            ["git", *args], cwd=cwd, capture_output=True, text=True,
            stdin=subprocess.DEVNULL, env=env, timeout=limit,
        )
    except subprocess.TimeoutExpired:
        # convert here rather than let TimeoutExpired escape: the module's whole error
        # vocabulary is WorkspaceError (the CLI prints it, gc's per-tree handler reports it),
        # and "git … timed out" is the one message that names the actual failure.
        raise WorkspaceError(f"git {' '.join(args)} timed out after {limit:.0f}s") from None


def _git(
    *args: str, cwd: Path | None = None, timeout: float | None = None,
    env_extra: dict[str, str] | None = None,
) -> str:
    proc = _run_git(args, cwd, timeout, env_extra)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    return _run_git(args, cwd, None).returncode == 0


# THE ONE WAY THIS MODULE LOOKS INSIDE A WORKTREE IT MAY NOT WRITE TO (VMCP-90), and what it buys
# is the ENVIRONMENT, not tidiness. `git status --porcelain` REWRITES the index every time, even in
# a clean tree (measured, git 2.50.1): it writes the refreshed stat cache back. `_last_activity`
# reads that index mtime, so the sweep's own inspection used to be indistinguishable from an
# agent's footprint — the tree gc had just refused read as freshly touched on the NEXT sweep and
# was skipped by the grace window, silently, in NEITHER list. MEASURED over consecutive real
# sweeps: sweep 1 reported `kept=[unreachable-head, unpushed, half-created]`, sweeps 2 and 3
# reported only `half-created` (the refusals decided BEFORE any git call in the tree). So a
# standing alarm — the list that means "a human should look", which the pump reads EVERY tick —
# was absent from ~29 of every 30 minutes. Nothing was lost (re-ageing the markers brought every
# entry straight back); the signal was merely unreliable exactly when it mattered.
#
# GIT_OPTIONAL_LOCKS=0 is git's own switch for this ("prevent `git status` from refreshing the
# index as a side effect"), and it is the right SHAPE because it never has to tell gc's writes from
# anyone else's: gc simply stops writing. An agent's or a human's `git status`/`add`/`commit` in
# the tree does not set it and still bumps the index (measured), so the window keeps reading the
# one thing it exists to read — somebody may still be standing in this tree. The alternatives all
# had to draw that distinction after the fact: restoring the mtimes after a refusal blindly rewinds
# a commit an agent made DURING the inspection (a linked worktree's commit takes no lock of ours),
# and dropping the index from `_last_activity` deletes the only fresh marker an hour-old tree has.
#
# THE ENV VAR AND NOT `--no-optional-locks`, which git documents as equivalent: a git older than
# 2.15 does not know the FLAG and would fail the inspection outright — every dead tree turning into
# a `release-error`, i.e. a broken reaper traded for a delayed alarm — while it simply ignores an
# env var it never learned and degrades to the old cadence. Fail toward the old bug, never toward a
# new failure.
#
# ONE HELPER, so the rule is "gc never writes inside a tree it is inspecting" rather than "remember
# the flag at each call site". `status` is the only call that writes TODAY (`log`, `rev-parse` and
# `rev-parse --git-path` measured clean), but `git diff` refreshes the index the same way, so the
# next guard someone adds is covered by construction. COST: none measurable — the skipped
# write-back IS the difference. A 4000-file tree, clean and with 400 files modified: 21.8-21.9 ms
# without the write-back vs 22.0-22.2 ms with it.
def _git_inspect(*args: str, cwd: Path) -> str:
    """`_git` for a call that merely LOOKS at a worktree — read the note above before adding one."""
    return _git(*args, cwd=cwd, env_extra={"GIT_OPTIONAL_LOCKS": "0"})


def repo_root(cwd: Path | None = None) -> Path:
    return Path(_git("rev-parse", "--show-toplevel", cwd=cwd))


@functools.lru_cache(maxsize=None)
def _main_worktree(root: Path) -> Path:
    """Resolve `root` (the toplevel of ANY worktree — main OR linked) to the repo's MAIN
    worktree. Task 4 correction: `git rev-parse --show-toplevel`, run from INSIDE a linked
    worktree (the normal place for a per-task agent to be sitting, per SKILL.md), returns
    THAT worktree's own toplevel — not the main repo's. `worktree_root` derives its default
    sibling directory from the repo's name (`<repo>.worktrees`), so feeding it an unresolved
    linked-worktree root would compute a NESTED, wrong path — every real tree would then fail
    the "is this one of ours" parent check, and `--gc` would silently reap nothing while still
    reporting success. `git worktree list --porcelain` always lists the main worktree FIRST,
    from any linked tree (verified against real git), so it is the single source of truth.
    `.resolve()` because git already prints realpaths (Task 3 round-1 fix) and callers compare
    Path equality, not strings.

    MEMOISED (tracker #517), and the cache is on THIS function rather than on `worktree_root`
    deliberately. What costs anything here is the `git worktree list` SUBPROCESS, and the answer
    it computes — which worktree of this repo is the main one — cannot change while a process
    runs: `git worktree add/remove` append and drop LINKED entries, never the first one. What
    `worktree_root` adds on top (VIKUNJA_WORKTREE_ROOT, then the repo toml) is exactly the part
    that CAN change under a caller — the unit suite monkeypatches that env var per test — so
    caching the composite would freeze an override and turn a passing suite into a lying one.
    Unbounded because the key set is "toplevels this process has been handed", i.e. a handful.
    `cache_clear()` is part of the surface a test may use; nothing in the product calls it."""
    return list_worktrees(root)[0]["path"].resolve()


def worktree_root(root: Path) -> Path:
    """Where per-task trees live. Default: a SIBLING of the repo, never inside it — inside,
    pytest collection, ruff and `git add -A` would all sweep them up.

    `root` is canonicalised to the MAIN worktree first (see `_main_worktree`) so create,
    release and gc can never disagree about where trees live just because one of them happened
    to be invoked from inside a linked tree."""
    from vikunja_mcp.config import ENV_WORKTREE_ROOT, ConfigError, load_config

    root = _main_worktree(root)
    # env FIRST, on purpose: create/release need no tracker config at all, and load_config
    # RAISES without url/project_id — reading it first would throw away a perfectly good
    # VIKUNJA_WORKTREE_ROOT in any repo that is not tracker-configured.
    configured = os.environ.get(ENV_WORKTREE_ROOT)
    if not configured:
        try:
            configured = load_config(cwd=root).worktree_root
        except ConfigError:
            # ConfigError ONLY (review Minor 9): "this repo has no tracker config" is the
            # expected, fine case — create/release need none. A blanket `except Exception`
            # also swallowed the genuinely broken ones (malformed toml -> TOMLDecodeError,
            # an unreadable file -> OSError) and silently relocated every tree to the default
            # sibling directory, so a typo'd worktree_root would strand a live tree somewhere
            # the next `--release`/`--gc` no longer looks. Those must surface, not be guessed at.
            configured = None
    if configured:
        # .resolve() ALWAYS, even when `configured` is already absolute: `_find` compares
        # this path against `git worktree list --porcelain`, which prints the REALPATH. A
        # symlinked root (the macOS `/tmp` class of path, or a `/srv`→`/mnt` layout) would
        # otherwise never match — the resume-after-crash path breaks (a live tree reads as
        # "not registered" and gets refused-as-clobber) and release reports a false
        # "no worktree", leaking the tree forever.
        return (root / configured).resolve()
    return (root.parent / f"{root.name}.worktrees").resolve()


def default_base(root: Path) -> str:
    """The remote's default branch name. origin/HEAD is often absent in a fresh clone."""
    try:
        ref = _git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", cwd=root)
        return ref.removeprefix("refs/remotes/origin/")
    except WorkspaceError:
        return "main"


@contextmanager
def _repo_lock(root: Path):
    """Serialise worktree mutations: the pump dispatches agents concurrently, and two
    `worktree add` calls on one repo race. Same shape as hgdev-acp's per-mirror mutex.

    NOT reentrant — flock on a second fd in the same process would deadlock. Anything that
    needs the lock while holding it must call the _locked cores, never the public wrappers.
    """
    common = Path(_git("rev-parse", "--git-common-dir", cwd=root))
    if not common.is_absolute():
        common = (root / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def list_worktrees(root: Path) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in _git("worktree", "list", "--porcelain", cwd=root).splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                entries.append(current)
            current = {"path": Path(value), "branch": None, "detached": False, "head": None,
                       "locked": False, "lock_reason": None}
        elif key == "HEAD" and current is not None:
            # the checked-out COMMIT sha, same meaning as ensure_workspace's "head" (never the
            # "detached" BOOL below — one key, one meaning, per round 1's Finding 4). Taken from
            # the porcelain rather than a `rev-parse HEAD` with cwd=<tree>, on purpose: a
            # worktree whose directory is gone but which `prune` cannot drop (git refuses to
            # prune a LOCKED entry) is still listed here with its HEAD, while running git with
            # cwd inside it raises a bare FileNotFoundError that _git cannot convert.
            current["head"] = value
        elif key == "branch" and current is not None:
            # removeprefix, NOT rsplit("/") — refs/heads/task/42 must stay "task/42"
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached" and current is not None:
            current["detached"] = True
        elif key == "locked" and current is not None:
            # The key this parser used to DROP on the floor, and the whole finding: an entry can
            # be listed and unprunable and still be unusable. Two porcelain shapes, both measured:
            # a reason-less `git worktree lock` emits the bare line `locked` (partition gives an
            # empty value -> lock_reason None), a lock with a reason emits `locked <reason>`.
            # `locked` is the BOOL callers must gate on; `lock_reason` only refines the message
            # (see _locked_refusal). Deliberately NOT unescaped: git c-quotes a reason containing
            # newlines/control chars, and nothing here parses the reason — it is human-facing text,
            # while the only reason we ever COMPARE (git's own `initializing`) is always bare.
            current["locked"] = True
            current["lock_reason"] = value or None
    if current:
        entries.append(current)
    return entries


def _locked_refusal(task_id: int, role: str, wt: dict) -> str:
    """The message for a worktree that is registered, unprunable, and must NOT be handed back.

    Two wordings because the two causes need different things from the human. Note which one the
    CALLER gates on, though: `_ensure_locked` refuses on `wt["locked"]` alone and only asks this
    helper how to phrase it. Being wrong about the marker TEXT then costs a less specific message;
    being wrong about whether to refuse at all costs an agent working in a tree with no files in
    it. So the string comparison lives here, in the message, and never in the guard.
    """
    path = wt["path"]
    if wt["lock_reason"] == _LOCK_INITIALIZING:
        return (
            f"{path} is a HALF-CREATED worktree — git's own `locked {_LOCK_INITIALIZING}` marker, "
            f"left when a `git worktree add` is killed mid-checkout (a timeout, SIGKILL, ^C). Its "
            f"checkout may be incomplete (files missing, the index full of staged deletions) and "
            f"the marker is PERMANENT either way, so `git worktree prune` will not drop it and "
            f"`--release`/`--gc` can never reap it. Refusing to hand it back for task {task_id} "
            f"({role}). Nothing was removed: inspect it, then "
            f"`git worktree unlock {path} && git worktree remove -f -f {path}`"
        )
    reason = wt["lock_reason"] or "no reason given"
    return (
        f"{path} is a LOCKED worktree ({reason}) — refusing to hand it back for task {task_id} "
        f"({role}). A lock is a deliberate hands-off marker and git will not let `--release`/"
        f"`--gc` remove it either, so working in it would leave a tree nothing can reap. "
        f"`git worktree unlock {path}` to make it usable again"
    )


def _rebase_in_progress(wt_path: Path) -> bool:
    """Is a `git rebase` stopped mid-flight in this worktree?

    MESSAGE-ONLY, exactly like `_locked_refusal`'s marker comparison and for the same reason: the
    guards key on `branch is None` — the fact that makes a build tree unusable — and only ask this
    to choose WHICH recovery to name. So being wrong here costs wording, never a wrong refusal, and
    it must not be able to raise into a guard: an unreadable tree simply reports "no rebase" and
    gets the generic wording.

    BOTH backend directories, measured on git 2.50.1 — the same pair git's own `git status` checks.
    The default merge backend leaves `rebase-merge` (constructed with `git rebase origin/main
    --exec false`, i.e. this project's integration recipe stopped between replayed commits);
    `git rebase --apply` leaves `rebase-apply` (constructed with a first-commit conflict). Asked for
    BY NAME via `rev-parse --git-path` rather than assembled from `.git/worktrees/<n>/`, the same
    way `_last_activity` asks for the index: in a LINKED worktree those live per-tree and the
    mapping is git's to know. MEASURED both shapes — absolute inside a linked worktree
    (`…/.git/worktrees/task-540/rebase-merge`), relative in the main one (`.git/rebase-merge`) — so
    both are resolved against the tree rather than assumed.
    """
    for name in ("rebase-merge", "rebase-apply"):
        try:
            path = Path(_git_inspect("rev-parse", "--git-path", name, cwd=wt_path))
            if (path if path.is_absolute() else wt_path / path).exists():
                return True
        except (WorkspaceError, OSError):
            # OSError as well as WorkspaceError, for `_last_activity`'s reason: with cwd pointing
            # at a directory that is gone, subprocess.run raises a bare FileNotFoundError that
            # `_git` cannot convert (it only ever inspects `returncode`).
            return False
    return False


def _detached_build_refusal(root: Path, task_id: int, wt: dict, refusal: str) -> str:
    """The message for a BUILD worktree that is not standing on its own `task/<id>` branch.

    THE STATE (VMCP-86, constructed and measured in a throwaway repo, not reasoned about). A
    per-task agent runs SKILL.md's integration recipe — `git fetch origin && git rebase
    origin/main` — inside `task-<id>` and the turn running it is killed (session limit, API error).
    git detaches to `onto` BEFORE replaying anything and re-attaches `task/<id>` only at the END, so
    an interrupted rebase leaves the tree `git status`-CLEAN, DETACHED, with git's rebase state
    still on disk. Nothing in the tree's own shape says so, which is the whole bug: `_find` returned
    it, `created: false` said "here is your workspace", and the rulebook told the agent it was
    standing on its disposable branch.

    ONE message for BOTH refusals (`_ensure_locked` and `_release_locked`), with only the clause
    naming what was refused passed in. The diagnosis and the recovery are the same fact in both
    places, and two copies of a recovery drift — SKILL.md and this module have already had to be
    dragged back into agreement twice.

    WHY IT REFUSES RATHER THAN RECOVERS, and this is the load-bearing half. The recovery is `git
    rebase --continue` or `git rebase --abort`, and CHOOSING between them is not the tool's to make:
    `--abort` discards every commit the rebase had already replayed. That is the module's governing
    invariant ("housekeeping must never be how an agent's work disappears") applied to setup, the
    same call 514 made for a `locked initializing` tree — refuse loudly, name the two commands, let
    the agent that owns the work decide. It is also why this is not a "report it in the payload"
    warning: an agent that does not read the extra key commits onto a HEAD reachable from no ref and
    pushes it, and under-refusing there is silent while over-refusing is one legible error the pump
    already knows how to degrade around.
    """
    path = wt["path"]
    head = wt["head"]
    branch = BUILD_BRANCH.format(task_id=task_id)
    if _git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root):
        tip = _git("rev-parse", f"refs/heads/{branch}", cwd=root)
        where = (
            f"`{branch}` still points at {tip}, so the task's own commits are NOT lost — they are "
            f"on the branch, not on this HEAD"
        )
        back = (
            f"`git -C {path} log --oneline {branch}..HEAD` shows what only this HEAD names, and "
            f"`git -C {path} checkout {branch}` puts the tree back on its branch"
        )
    else:
        # the branch was deleted out from under the tree (a hand `branch -D`, or #517's release
        # that removed a tree and then failed to delete... in reverse). Then this HEAD is the ONLY
        # name for whatever was replayed, and "just check out the branch" would be advice that
        # destroys it.
        where = (
            f"`{branch}` does not exist any more, so this detached HEAD is the ONLY name for "
            f"whatever it holds — do not discard it before looking"
        )
        back = (
            f"`git -C {path} log --oneline` shows what it holds, and `git -C {path} checkout -b "
            f"{branch}` re-creates the branch on it"
        )
    if _rebase_in_progress(path):
        return (
            f"{path} is a build worktree stopped MID-REBASE — DETACHED at {head}, with git's own "
            f"rebase state still in place. That is what SKILL.md's integration recipe (`git fetch "
            f"origin && git rebase origin/main`) leaves behind when the turn running it is killed "
            f"between replayed commits. {where}. {refusal} Finish or undo the rebase IN THAT TREE "
            f"first, then ask again: `git -C {path} rebase --continue` (replay the rest) or `git "
            f"-C {path} rebase --abort` (back onto {branch}, discarding what was replayed). "
            f"Deliberately not chosen for you — `--abort` throws away replayed work"
        )
    return (
        f"{path} is a build worktree with a DETACHED HEAD ({head}) and no rebase in progress — a "
        f"build tree is CREATED on {branch} and is only ever taken off it by something that "
        f"stopped halfway (an interrupted rebase or bisect, a hand `checkout --detach`). {where}. "
        f"{refusal} Put it back on its branch before using it: {back}"
    )


def _find(root: Path, task_id: int, role: str) -> dict | None:
    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    target = worktree_root(root) / name
    for wt in list_worktrees(root):
        if wt["path"] == target:
            return wt
    return None


def _check_role(role: str) -> None:
    """The CLI is protected by argparse `choices`; the Python API the pump (and Task 4's
    gc_workspaces) calls directly is not — an unchecked role would silently branch build/
    review logic on `role == "build"` with anything else falling into "review"."""
    if role not in ("build", "review"):
        raise WorkspaceError(f"unknown role {role!r} — must be 'build' or 'review'")


def _ensure_locked(root: Path, task_id: int, role: str, at: str | None) -> dict:
    _check_role(role)
    _git("worktree", "prune", cwd=root)
    # the ONE network call in this module, and it runs with the repo lock already held — hence
    # the tight bound rather than the local ceiling (see _GIT_NET_TIMEOUT)
    _git("fetch", "origin", cwd=root, timeout=_GIT_NET_TIMEOUT)
    wt_root = worktree_root(root)
    wt_root.mkdir(parents=True, exist_ok=True)
    base = f"origin/{default_base(root)}"

    existing = _find(root, task_id, role)
    if existing is not None:
        if existing["locked"]:
            # REFUSE, never reuse and never remove — same shape as the review-pinning refusal
            # below, for the same reason. `git worktree add` killed mid-checkout leaves an entry
            # that IS listed (so `_find` returns it) and that `prune` will NOT drop, so this
            # early-return used to hand back a directory containing nothing but `.git` as
            # `created: false`; the agent dispatched into it stands in a tree whose files are
            # missing and whose index is all staged deletions, and it commits from there.
            # Since `_GIT_TIMEOUT` landed, our OWN timeout can manufacture that state — no
            # external killer required — so this is a reachable path, not a theoretical one.
            #
            # Gated on the BOOL, not on the reason: a lock we cannot explain is still a tree we
            # cannot vouch for, and over-refusing degrades the pump to one slot with a legible
            # error (SKILL.md's "не завелось — цикл НЕ роняем"), while under-refusing silently
            # produces work built on an absent tree. Asymmetric, so fail toward the refusal.
            # And do NOT self-heal it by unlocking + force-removing: the partial checkout may be
            # the only trace of what killed the add, and "housekeeping must never be how work
            # disappears" applies to setup exactly as it does to reaping.
            raise WorkspaceError(_locked_refusal(task_id, role, existing))
        if role == "build" and existing["branch"] is None:
            # VMCP-86, and the information was ALREADY HERE: `list_worktrees` has always parsed
            # the porcelain's `detached` and left `branch` at None — this early-return simply
            # copied that None into the payload and called it a workspace. A build tree is created
            # on `task/<id>` and nothing in this module ever takes it off; detached therefore means
            # something stopped halfway, and the commonest something is this project's own
            # integration recipe interrupted mid-rebase (see _detached_build_refusal).
            #
            # Gated on `branch is None`, NOT on the rebase probe — same split as the lock guard
            # above, for the same asymmetry. What makes the tree unusable is that it is off its
            # branch; whether a rebase is still in progress only refines the message. Refuse on the
            # fact, phrase from the probe.
            #
            # `role == "build"` is the whole condition on the other side: a REVIEW tree is detached
            # BY DESIGN (`worktree add --detach`), so this must never fire there — the payload just
            # below deliberately reports `branch: None` for it.
            raise WorkspaceError(_detached_build_refusal(
                root, task_id, existing,
                refusal=f"Refusing to hand it back for task {task_id} (build): a caller that is "
                        f"told it stands on its disposable branch would commit onto a HEAD "
                        f"reachable from no ref, and its `git push origin HEAD:main` would push "
                        f"the replayed commit rather than the branch's work.",
            ))
        payload = {
            "role": role, "task_id": task_id, "path": str(existing["path"]),
            "branch": existing["branch"], "created": False,
        }
        if role == "review":
            # Review Critical 1 — the only bug on this branch that produced a WRONG VERDICT
            # rather than noise. This early-return fires before the role branch below, so `at`
            # used to be discarded in silence AND the payload carried no "head" (the created
            # one does): round 2 of a review asked for the fix's sha, got a tree still pinned
            # at the PRE-FIX sha, and nothing in the response said so — the reviewer read the
            # old code and approved it. The trigger is a state this module deliberately
            # preserves: a reviewer that commits notes inside its detached tree can never
            # release it (the reachability guard below refuses, correctly) and --gc cannot reap
            # it either, so review-<id> persists and poisons every later round for that task.
            #
            # REFUSE, never re-point: moving a detached HEAD (`checkout --detach <at>`) is
            # itself a destruction path — it would orphan exactly the in-tree commit the
            # reachability guard exists to protect. "push OK -> remove, push FAIL -> KEEP"
            # says housekeeping must never be how work disappears; the same holds for setup.
            payload["head"] = existing["head"]
            # `at^{commit}` and not a bare `at`: rev-parse ECHOES BACK a full 40-hex sha with
            # exit 0 without checking the object exists, so a bare comparison would silently
            # pass on garbage; the peel also makes an annotated tag comparable to a commit sha.
            if at is not None and _git("rev-parse", f"{at}^{{commit}}", cwd=root) != (
                existing["head"]
            ):
                raise WorkspaceError(
                    f"review tree for task {task_id} is pinned at {existing['head']} but --at "
                    f"asked for {at} — release it first ({existing['path']}); if --release "
                    f"refuses, it holds an in-tree commit that only a human should resolve"
                )
        return payload

    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    path = wt_root / name
    if path.exists():
        raise WorkspaceError(
            f"{path} exists but is not a registered worktree — refusing to clobber it"
        )

    if role == "review":
        _git("worktree", "add", "--detach", str(path), at or base, cwd=root)
        return {
            "role": "review", "task_id": task_id, "path": str(path),
            # "head", NOT "detached" — list_worktrees's "detached" is a BOOL (git's own
            # porcelain vocabulary); this is a SHA. Two producers must never reuse one key
            # for two different meanings (it "worked" only because a hex string is truthy).
            "branch": None, "head": _git("rev-parse", "HEAD", cwd=path), "created": True,
        }

    branch = BUILD_BRANCH.format(task_id=task_id)
    if _git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root):
        # the branch outlived its tree (crashed agent) — reattach, NEVER recreate: it carries
        # the unfinished commits the resume agent is coming back for
        _git("worktree", "add", str(path), branch, cwd=root)
    else:
        _git("worktree", "add", "-b", branch, str(path), base, cwd=root)
    return {
        "role": "build", "task_id": task_id, "path": str(path),
        "branch": branch, "base": base, "created": True,
    }


def _last_activity(wt_path: Path) -> float | None:
    """Newest NON-FUTURE mtime of the two footprints a WORKING agent leaves in a worktree — the
    input to the grace window (`_REAP_GRACE_SECONDS`). None when NEITHER can be read, which the
    caller must treat as "no opinion" and fall through to the ordinary guards: a directory that is
    already gone has nobody standing in it, and a silent skip that can never expire would leak a
    tree with no report at all.

      * the worktree DIRECTORY — entries created or removed at its top level (a new file, the
        `.pytest_cache` a verification run drops) and `git worktree add` itself, so a
        just-created tree is young by construction. Nothing gc does touches it.
      * its INDEX — every `git add`/`commit`/`rebase`, which is the footprint that matters here:
        a task cannot reach Review without a commit, so this is what is fresh at the moment the
        tree starts reading dead. Asked for BY NAME (`rev-parse --git-path index`) rather than
        assembled from the basename: git derives `.git/worktrees/<n>` from the directory name and
        disambiguates collisions itself, so that mapping is git's to know, not ours to guess.
        MEASURED: it may not EXIST — a half-created tree (`locked initializing`) is killed before
        git writes one, which is also why `git status` there reports every tracked file as a staged
        deletion — so each marker is stat'ed independently and a missing one simply does not vote.

    MEASURED on git 2.50.1, and the reason every git call the sweep makes inside a tree goes
    through `_git_inspect`: `git status --porcelain` REWRITES the index every single time, even in
    a clean tree. Until VMCP-90 that made gc's own inspection indistinguishable from an agent's
    footprint here — a tree gc had just refused read as freshly touched on the next sweep and was
    skipped by the window, silently, so a standing `kept` line surfaced about once per window
    instead of every tick. gc now takes no optional locks, so a fresh mtime on either marker means
    what it says: somebody OTHER than the sweep wrote here. Both markers stay — the fix is that gc
    stopped writing, NOT that this function stopped looking (dropping the index would blind it to
    exactly the hour-old tree whose only fresh footprint is the commit it just made).

    WHY THE MAX IS TAKEN OVER NON-FUTURE MARKERS ONLY (VMCP-84). An mtime in the FUTURE — clock
    skew, a restored backup, an unpacked archive — is not evidence of anything, and the caller
    deliberately refuses to honour one (its `0 <=` bound; a future value would otherwise read as
    "younger than N" on every sweep forever, and that skip is silent). But that refusal is decided
    on the value THIS function returns, so a plain max let one skewed marker MASK the other:
    constructed and measured on this code, a tree with a future directory mtime and an index the
    agent had just written was reaped — and so was the mirror case (future index, fresh directory).
    Two stats, and the useless one won. Dropping future markers before the max means a bad clock
    reading can no longer suppress a good one; nothing else moves, because a future value never
    survived to mean "young" in the first place.

    WHEN EVERY MARKER IS FUTURE there is no good one left, and this returns the future value ANYWAY
    rather than `None`. Both fall through to the ordinary guards, so the sweep behaves identically
    — but `None` means "no opinion" and would make the caller's `0 <=` bound unreachable, i.e.
    deletable with the whole suite still green. The bound stays the thing that decides that case;
    this function only stops letting it decide the OTHER case too.

    `now` is sampled AFTER the stats, never before: a marker an agent writes DURING this read would
    otherwise be compared against a `now` from before it existed, land in the future, and be
    discarded — throwing away the freshest evidence there is. Sampled last, every write that really
    happened precedes it.

    COST, since the sweep holds the repo-wide flock throughout: two stats and one local `rev-parse`
    per DEAD tree — live trees short-circuit before this is ever called — against a board read the
    same lock already covers.
    """
    candidates = [wt_path]
    try:
        index = Path(_git_inspect("rev-parse", "--git-path", "index", cwd=wt_path))
        candidates.append(index if index.is_absolute() else wt_path / index)
    except (WorkspaceError, OSError):
        # OSError as well as WorkspaceError: with cwd pointing at a directory that no longer
        # exists, subprocess.run raises a bare FileNotFoundError that `_git` cannot convert (it
        # only ever inspects `returncode`). Such an entry IS still listed — git refuses to prune a
        # LOCKED one — so this is reachable, and the directory mtime below fails the same way.
        pass
    mtimes: list[float] = []
    for candidate in candidates:
        try:
            mtimes.append(candidate.stat().st_mtime)
        except OSError:
            continue
    if not mtimes:
        return None
    now = time.time()
    real = [mtime for mtime in mtimes if mtime <= now]
    return max(real) if real else max(mtimes)


# WHAT `git status --porcelain` CANNOT SEE, AND WHY IT IS A HOLE IN THIS MODULE'S OWN INVARIANT
# (VMCP-185). The header of this file promises "push OK -> remove, push FAIL -> KEEP … housekeeping
# must never be how an agent's work disappears", and the dirty guard below is half of how that is
# kept. But plain `--porcelain` does not report IGNORED paths at all, so for that guard a tree
# holding nothing but ignored files is CLEAN. MEASURED (real git 2.50.1, a bare origin, a throwaway
# tree): a dead build tree with everything committed and pushed (`status --porcelain` empty,
# `origin/main..HEAD` empty) plus `secrets.env` and `scratch/notes.txt` on disk was released by
# BOTH paths — `--release` returned `{"released": true}` and `--gc` put it in `released` — the
# directory and both files gone, and NOTHING in `kept`, `expected` or `warning` said so. Untracked
# but NON-ignored files (`??`) the guard does see and does hold on, so the hole is exactly the
# ignored ones.
#
# IT IS NOT HYPOTHETICAL, AND THE REAL EXPOSURE IS NOT THE ONE IT LOOKS LIKE. `.vikunja-mcp.env`
# (this repo's token) and `.playwright-mcp/` both live in the MAIN checkout, which nothing here
# ever removes — measured across the four live worktrees on this machine, all four had neither.
# What IS at risk sits in the per-task tree, and SKILL.md PRESCRIBES writing it there: its browser
# recipes produce `shot-<id>.png` in the agent's own worktree (ignored by this repo's `*.png`) and
# `--output-dir .playwright-mcp/<id>` under it (ignored wholesale). Measured on a stand carrying
# this repo's real ignore rules: both were destroyed by a `released: true`, silently.
#
# WHY THIS ONLY REPORTS AND NEVER HOLDS — the alternative was measured and rejected. Making the
# guard `--porcelain --ignored` refuses a tree that holds ANY ignored path, and the mandated gate
# (`uv run pytest`) CREATES `.venv` on its first invocation, so every build tree that ran the gates
# is permanently "dirty": measured live, 3 of 3 build trees (7, 6 and 2 ignored entries — all of
# them `.venv/`, `__pycache__/`, `.ruff_cache/`, `.pytest_cache/`) and 0 for the one review tree
# that never ran anything. `--gc` would stop reaping ANYTHING, trees would pile up, and the next
# human would turn the guard off outright. A destroy-only-with-a-flag variant collapses into the
# same two ends: unset, nothing is ever reaped; always set, it is today's behaviour with a longer
# argv. So the tree is still removed, and what changes is that the removal STOPS BEING SILENT.
#
# SAY THAT PLAINLY RATHER THAN OVERSELL IT: naming a loss is not preventing one. The work is gone
# either way; only the silence is fixed. Whether the guard should also HOLD is a product decision
# left to a human (filed separately), not guessed at here.
#
# THE FILTER BELOW IS A LIST, AND A LIST ROTS — SO IT WAS PUT WHERE ROT IS CHEAP. It decides only
# what gets REPORTED, never what gets removed. A build tool this set has never heard of appears ->
# its directory is not recognised -> it is named in `removed_ignored` -> one noisy line in a
# `released` entry, and the reaper keeps reaping. That is the whole cost of it being out of date.
# The DANGEROUS direction is the other one: a name ADDED here is a class of file this module will
# destroy without a word again, so add only what is reproducible by construction (a virtualenv, a
# bytecode or tool cache, an npm install) and never something an agent AUTHORS. `.playwright-mcp/`
# and `.vikunja-mcp.env` are deliberately absent and pinned absent by test_the_detritus_filter_
# does_not_cover_what_an_agent_authors. Same fail-toward-shouting direction as `_keep_is_expected`:
# unrecognised means REPORTED.
#
# **THAT PIN IS EXACTLY TWO NAMES WIDE — do not read it as a guard on the direction.** Measured by
# an independent second pass: adding `.playwright-mcp` to this set fails 2 tests, while adding
# `dist`, `build`, `out`, `artifacts`, `screenshots` in one go fails NONE — and those are precisely
# the names an agent parks authored output under. A test that pinned the whole direction would have
# to enumerate the complement of this set, which is not a thing; so what stands between a future
# widening and a silent loss is this paragraph, not the suite. Two further measured qualifications.
# The matching is CASE-SENSITIVE (`A.PYC`, `.ds_store` are NOT recognised), which fails open — they
# get reported — and so is only noise, but on a `core.ignorecase=true` checkout they are the same
# files this set recognises in lower case. And `.claude/*` is NOT here although this repo ignores
# it: measured, a worktree session writing `settings.local.json` or `mailbox/` would then put the
# field on EVERY released entry, which is the never-read signal this filter exists to avoid — none
# of the four live trees held one on 2026-08-03, so it is a risk rather than a defect, and the
# cheap failure (a noisy line) is the one this design deliberately buys.
_REPRODUCIBLE_IGNORED_DIRS = frozenset({
    ".venv", "venv",                 # measured: `uv run` creates it on the first gate command
    "__pycache__",                   # measured in 3 of 3 live build trees
    ".pytest_cache", ".ruff_cache", ".mypy_cache", ".tox",
    "node_modules",
})
_REPRODUCIBLE_IGNORED_LEAVES = frozenset({".DS_Store"})
_REPRODUCIBLE_IGNORED_SUFFIXES = (".pyc", ".pyo")
# The report is BOUNDED, and the bound is about the CONSUMER, not about tidiness: `--gc` runs
# unattended and its one JSON line is read by a hub process. Measured by the second pass — 3000
# loose ignored files at a tree root produce 3000 entries and a 50,133-byte line, and a sibling
# project has already lost a daemon session to an oversized read (hgdev-acp, a >24.5 KiB log pull).
# Nothing is hidden by the cap: past it the entry also carries `removed_ignored_truncated` with the
# TRUE total, so the count survives even when the names do not. Absence of that key means the list
# is complete. Contrived state (git collapses ignored DIRECTORIES into one entry, so this needs
# thousands of loose FILES), which is why it is a cap and not a refusal.
_MAX_REPORTED_IGNORED = 50


def _inspect_status(path: Path) -> tuple[list[str], list[str]]:
    """One `git status` call, split into (what the dirty guard counts, what is merely IGNORED).

    ONE call, not two: `--ignored` only ADDS `!! ` lines and leaves every other line byte-identical
    — measured on a tree carrying both kinds at once (`['M README.md', '?? plain.txt']` before and
    after, with `!! .playwright-mcp/` and `!! shot-8001.png` alongside). So the dirty guard keeps
    seeing exactly what it saw before, including its entry COUNT, which is user-visible in the
    refusal text. Cost of the wider walk on a real tree with a real `.venv`, 5 runs each: 17.6-25.6
    ms plain against 26.6-36.5 ms with `--ignored`; no extra git invocation at all.

    `_git_inspect`, like the call it replaces: this looks inside a tree we may end up refusing to
    touch, and it must not leave a footprint the grace window later mistakes for an agent's
    (VMCP-90). Re-measured for the wider walk: with `core.untrackedCache=true` and files under an
    ignored `.venv/`, both grace markers stay byte-identical, while the same command WITHOUT
    `GIT_OPTIONAL_LOCKS=0` moves the index mtime.

    BOTH HALVES WENT BLIND UNDER ONE GIT SETTING, and VMCP-223 (766) closes it here with the
    `-c status.showUntrackedFiles=normal` prefix below. With `status.showUntrackedFiles = no`
    (config, ANY level — repo, global, system; a linked worktree shares `.git/config` with the
    main checkout) the command prints NEITHER `??` NOR `!!` lines. Measured on a real bare origin
    plus a real worktree, BEFORE the prefix: a tree holding an untracked-and-NOT-ignored
    `REAL-WORK.txt` and an ignored `shot-766.png` returned the EMPTY STRING, the dirty guard
    passed, `release_workspace` answered `{"released": true}` with no `code`, no `warning` and no
    `removed_ignored` — and the file was gone. That is the module's own invariant ("push OK ->
    remove, push FAIL -> KEEP … housekeeping is never how an agent's work disappears") failing
    whole rather than at an edge, and `--gc` does it unattended, on every tick.

    WHY THIS IS A FIX AND NOT THE PRODUCT DECISION 710 WAS TOLD TO LEAVE ALONE, because the two
    look alike and the difference is the whole justification. The open question (VMCP-221, 764)
    is whether `dirty` should be WIDENED to hold a tree for IGNORED files — today it deliberately
    does not, and changing that has a price (a tree that passed every gate would be held by its
    own `.venv`). That question is untouched here. This one is the opposite direction: the guard
    already CLAIMS untracked-and-not-ignored, that claim is its entire reason to exist, and a
    performance knob was silently taking it away. Restoring a claimed scope is not widening one.
    Measured rather than argued: with the setting at its DEFAULT the prefix changes nothing —
    same refusals, same entry counts, same `removed_ignored` — so no tree that used to be
    released is held now. What changed is that the answer stopped depending on someone else's
    config.

    A per-invocation `-c` deliberately, never `git config`: the user's setting is not rewritten,
    only what THIS inspection sees. Someone who set it for speed keeps it everywhere else, and a
    CLEAN tree under that setting still releases normally (measured) — which is the objection
    that ruled out refusing outright while the setting is in force.
    """
    dirty: list[str] = []
    ignored: list[str] = []
    for line in _git_inspect(
        "-c", "status.showUntrackedFiles=normal",
        "status", "--porcelain", "--ignored", cwd=path,
    ).splitlines():
        if line.startswith("!! "):
            ignored.append(line[3:])
        else:
            dirty.append(line)
    return dirty, ignored


def _is_reproducible_ignored(entry: str) -> bool:
    """Is this ignored path recognisably regenerable build output (-> not worth reporting)?

    Fails toward REPORTING, in every uncertain case. A path git had to QUOTE (a newline, a tab, a
    non-ASCII byte under `core.quotePath`) is never called routine: it arrives escaped, so matching
    it component-wise would be matching the escape rather than the name.

    KNOWN BOUND, deliberate: `--ignored` collapses an ignored DIRECTORY into one entry, so a file
    an agent hid INSIDE `.venv/` is covered by that entry and goes unreported. Working inside a
    directory the repo declares regenerable is not a case this can serve.
    """
    if entry.startswith('"'):
        return False
    parts = [component for component in entry.rstrip("/").split("/") if component]
    if not parts:
        return False
    if any(component in _REPRODUCIBLE_IGNORED_DIRS for component in parts):
        return True
    leaf = parts[-1]
    return leaf in _REPRODUCIBLE_IGNORED_LEAVES or leaf.endswith(_REPRODUCIBLE_IGNORED_SUFFIXES)


def _release_locked(root: Path, task_id: int, role: str) -> dict:
    _check_role(role)
    _git("worktree", "prune", cwd=root)
    wt = _find(root, task_id, role)
    if wt is None:
        # Review Minor: every OTHER refusal below carries "path" — a human reading `kept`
        # needs a location to act on even when there is nothing to remove. The expected (but
        # absent) path is still informative: it says WHERE a worktree for this task would be.
        name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
        return {"released": False, "task_id": task_id, "role": role,
                "path": str(worktree_root(root) / name), "code": CODE_NO_WORKTREE,
                "reason": "no worktree for this task"}
    path = wt["path"]
    if wt["lock_reason"] == _LOCK_INITIALIZING:
        # The OUTCOME here is unchanged — a half-created tree was already kept, on every tick,
        # forever. What was wrong is the DIAGNOSIS: `git status` inside it reports the staged
        # deletions of every missing file, so the guard below called it "working tree is dirty
        # (N entries)" and sent a human looking for uncommitted work that does not exist.
        # Say what it actually is, once, in a line `--gc`'s `kept` can be acted on.
        #
        # Keyed on the marker TEXT (unlike _ensure_locked's guard, which keys on the bool): both
        # branches KEEP the tree, so a miss costs only wording — and since VMCP-142 the fall-
        # through is a coded verdict too, so a miss no longer changes the CHANNEL either.
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_HALF_CREATED,
                "reason": f"half-created worktree (git's own `locked {_LOCK_INITIALIZING}` "
                          f"marker from a killed `worktree add`) — needs a human: "
                          f"`git worktree unlock {path} && git worktree remove -f -f {path}`"}
    if wt["locked"]:
        # VMCP-142, and it REVERSES the note that used to sit above: a human `git worktree lock`
        # was deliberately left to fall through to `git worktree remove`'s own refusal ("cannot
        # remove a locked working tree … use 'remove -f -f' to override or unlock first"), on the
        # ground that git's message is already correct and specific and a synthesised reason would
        # replace git's report with our guess. The MESSAGE was never the problem; the CHANNEL was.
        # That refusal arrives as a raise, which `run_workspace` renders as `{"error": …}` + exit 1
        # — the CREATE channel — and SKILL.md reads that shape as "the tool could NOT do the work":
        # its «Не завелось — цикл НЕ роняем» branch is written for a failed CREATE, and it is the
        # only rule an agent has for an `{"error"}` + rc 1 line (degrade to one slot, keep
        # draining), while this tree is the other kind entirely, the kind rc 0 + `released: false`
        # + `code` exists for. The work is intact and a HUMAN pinned it; an agent that reads that
        # as a broken tool and moves on is the one outcome nobody wants.
        #
        # Keyed on the `locked` BOOL, like _ensure_locked's guard and for its reason: a tree we
        # cannot vouch for is refused whatever the lock SAYS, and a reasonless lock (`lock_reason
        # is None`, a real porcelain shape) must not slip past a text comparison. Its own prose
        # rather than `_locked_refusal`'s, for the same reason the half-created branch above has
        # its own: that helper is create-side ("refusing to hand it back", "working in it would
        # leave a tree nothing can reap"), and neither clause describes what happened here.
        #
        # PLACED BEFORE THE FIRST GIT CALL WITH CWD INSIDE THE TREE (the `git status` inspect
        # below), which is load-bearing rather than tidy: a locked entry survives `git worktree
        # prune` (measured), so an entry whose DIRECTORY a human moved or deleted is still handed
        # back by `_find`, and `_git_inspect(cwd=<gone>)` raises a bare FileNotFoundError that
        # `_git` cannot convert into anything. Same root, a different mechanism, and only an
        # ordering ahead of that call answers both with one guard. It also decides which code a
        # locked-AND-dirty tree reports — the lock, because it is the fact that makes the tree
        # unremovable, and "commit and retry" would be advice that cannot work. See the grading
        # note by `_EXPECTED_IN_A_PARKED_BUILD_TREE` for what that changes in `--gc`.
        #
        # `-f -f` is deliberately NOT offered here, unlike the half-created message above: there
        # the tree is unusable debris and force is the recovery, here the lock IS the human's
        # instruction and the tool must not teach agents how to override it. Grading: neither
        # `_EXPECTED_*` set holds this code, so `--gc` files it under `kept` by the fail-toward-
        # shouting default — see the policy note there for why a lock is never routine.
        reason = wt["lock_reason"] or "no reason given"
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_LOCKED,
                "reason": f"worktree is LOCKED ({reason}) — a deliberate hands-off marker, and "
                          f"git refuses to remove a locked tree. Nothing was removed and nothing "
                          f"was lost: `git worktree unlock {path}`, then release it again"}
    # Both halves of one status call — see _inspect_status. `ignored` is not a guard input: it is
    # read HERE, before anything is removed, because it is the last moment at which the payload
    # this removal is about to destroy can still be named (VMCP-185).
    dirty, ignored = _inspect_status(path)
    if dirty:
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_DIRTY,
                "reason": f"working tree is dirty ({len(dirty)} entries)"}
    if wt["branch"] is not None:
        # a task/<id> BRANCH's unique history is only safe once it's on origin — the
        # unpushed-commits guard.
        base = f"origin/{default_base(root)}"
        unpushed = _git_inspect("log", "--oneline", f"{base}..HEAD", cwd=path)
        if unpushed:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "code": CODE_UNPUSHED,
                    "reason": f"{len(unpushed.splitlines())} commit(s) not on {base}"}
    elif role == "build":
        # VMCP-86: the branch below assumes "detached ⇒ this is a review tree", and a build tree
        # left detached by an interrupted rebase falls straight into it — with the guard ABOVE
        # skipped, because that one keys on `wt["branch"]` and a detached tree has none. So the
        # unpushed history of `task/<id>` — which still exists and still holds the agent's commits
        # — goes UNCHECKED, and whether the tree is destroyed comes down to the unrelated question
        # of whether its HEAD happens to be reachable.
        #
        # MEASURED, in a throwaway repo, on the code as it stood: a rebase interrupted with HEAD
        # still on `onto` (git detaches there BEFORE replaying anything, so a turn killed at the
        # start lands exactly there; also reachable via a first-commit conflict resolved in the
        # sibling's favour, which leaves the tree clean) gave `{"released": true}` — the directory
        # DELETED, `task/541` left behind holding one commit that was not on origin/main, and
        # nothing in the report saying so. `--gc` does that unattended, every tick.
        #
        # Refuse instead, FIRST in the detached branch: which of HEAD and `task/<id>` is "the work"
        # is exactly the question this module cannot answer for the agent, and it is the more
        # specific statement about the same tree than "reachable from no ref" (the ordering
        # argument gc's self-guard-before-grace-window makes). `unreachable-head` is left untouched
        # for the review trees it was written about; `_keep_is_expected`'s `role` conjunct STAYS as
        # a backstop should anything reach it with a build tree again.
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "code": CODE_DETACHED_BUILD,
                "reason": _detached_build_refusal(
                    root, task_id, wt,
                    refusal="Refusing to release it: the unpushed-commits guard that protects a "
                            "build tree cannot run on a tree that is not on its branch, so "
                            "removing it here would report success while the branch's own commits "
                            "went unchecked.",
                )}
    else:
        # a review tree is DETACHED — it holds no branch, so the guard above cannot apply —
        # but its HEAD is NOT automatically safe either: anyone can commit INSIDE a detached
        # tree (the dirty check above only catches UNCOMMITTED changes; a fresh commit makes
        # the tree clean again). Verified against real git: nothing else protects that commit
        # — `git worktree remove` has no unpushed-commit check for a detached HEAD, and a
        # later `gc` would prune the object outright once the worktree admin dir (and its
        # reflog) is gone. The one thing that DOES make removal safe is the commit being
        # reachable from some other ref — a review pinned at a build branch's tip is exactly
        # that (task/<id> still names it, BY DEFINITION not yet on origin/main, which is why
        # the branch-history guard above must not run here); a commit made only inside this
        # detached tree is reachable from nothing and must be kept.
        #
        # KNOWN, DELIBERATE bound: this only inspects HEAD. A commit made and then moved off
        # HEAD (`reset --hard HEAD~1`, `checkout --detach <older>`) is released and destroyed
        # unseen. NOT a gap specific to this branch — the task/<id> path above has the exact
        # same shape (`origin/base..HEAD` also only ever looks at HEAD, and `branch -D`
        # finishes off whatever the branch no longer points at): "HEAD is the work" is a bound
        # of the whole module, not an oversight in this guard alone.
        head = _git_inspect("rev-parse", "HEAD", cwd=path)
        reachable = _git("for-each-ref", "--contains", head, "--format=%(refname)", cwd=root)
        if not reachable:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "code": CODE_UNREACHABLE_HEAD,
                    "reason": f"detached HEAD {head} is reachable from no ref"}
    _git("worktree", "remove", str(path), cwd=root)
    result = {"released": True, "task_id": task_id, "role": role,
              "path": str(path), "branch": wt["branch"]}
    destroyed = [entry for entry in ignored if not _is_reproducible_ignored(entry)]
    if destroyed:
        # ADDED ONLY WHEN NON-EMPTY, exactly like `branch_deleted`/`warning` above and for the same
        # reason: the absence of the key is the "nothing to see" signal. A field present on every
        # released entry (which it would be, unfiltered — every build tree carries `.venv/`) is the
        # never-read signal VMCP-68 had to split `kept` in two to cure, reintroduced in `released`.
        # SKILL.md's rule is therefore "read `kept`, and scan `released` for `branch_deleted:
        # false` AND `removed_ignored`".
        #
        # ONE DIRECTION ONLY, and the rulebook must say so: the key's PRESENCE proves something
        # unrecognised was destroyed, but its ABSENCE does not prove nothing was. `--ignored`
        # collapses an ignored DIRECTORY into a single entry, so a file an agent left inside
        # `.venv/` is covered by `.venv/`, filtered as routine, and destroyed unnamed (measured).
        result["removed_ignored"] = destroyed[:_MAX_REPORTED_IGNORED]
        if len(destroyed) > _MAX_REPORTED_IGNORED:
            result["removed_ignored_truncated"] = len(destroyed)
    if wt["branch"]:
        try:
            _git("branch", "-D", wt["branch"], cwd=root)
        except WorkspaceError as e:
            # tracker #517. The ONE window in which this function can fail with the tree ALREADY
            # GONE, and letting it raise made both callers lie about it in opposite directions:
            # `--gc`'s except-handler recorded `released: False` with a `path` that no longer
            # exists, and `--release` reported `{"error"}` + exit 1 for an operation that had
            # already succeeded. `released: false` is not a neutral "it didn't work" either —
            # SKILL.md teaches it as "PROTECTION: you still have unsaved work in there", and
            # sending an agent to rescue work from a directory git just deleted is the worst of
            # the available wrong answers.
            #
            # So report what actually happened: the tree IS released (every guard above passed,
            # meaning it was clean and pushed — nothing was lost), and the BRANCH leaked. That
            # leak is recoverable by construction, not a silent corruption: `_ensure_locked`
            # reattaches a surviving `task/<id>` instead of recreating it, which is the same
            # path a hand-deleted tree takes. Reported anyway, because a `branch -D` that fails
            # here means something unexpected about the repo, and a human should get the one
            # command that finishes the job.
            #
            # Keys added ONLY on failure, so their absence is the success signal and no existing
            # consumer of the released entry has to learn a new field. WorkspaceError only: a
            # bare OSError from a vanished cwd is a different bug and must not be laundered into
            # "released with a warning".
            result["branch_deleted"] = False
            result["warning"] = (
                f"worktree removed, but `git branch -D {wt['branch']}` failed ({e}) — the "
                f"branch leaked. Nothing was lost (the tree was clean and pushed) and a later "
                f"workspace call for task {task_id} will reattach to it; to finish the cleanup "
                f"by hand: `git branch -D {wt['branch']}`"
            )
    return result


def ensure_workspace(
    task_id: int, role: str = "build", at: str | None = None, cwd: Path | None = None
) -> dict:
    # Review Critical 1: canonicalise to the MAIN worktree HERE, once, so every `cwd=root` git
    # call inside _ensure_locked/_release_locked runs against a directory that is never itself
    # a tree being created/removed — see _main_worktree and release_workspace below for why.
    root = _main_worktree(repo_root(cwd))
    with _repo_lock(root):
        return _ensure_locked(root, task_id, role, at)


def release_workspace(task_id: int, role: str = "build", cwd: Path | None = None) -> dict:
    # Review Critical 1: an agent's own "I'm done, release me" call runs with cwd INSIDE the
    # very tree being released (the normal case per SKILL.md). Left uncanonicalised, `root`
    # would equal the tree's own path, and `_release_locked`'s `git worktree remove` (which
    # SUCCEEDS even when its subprocess cwd is the directory being removed — verified against
    # real git) would be immediately followed by `git branch -D ... cwd=root`, whose Python
    # subprocess.run(cwd=root) needs `root` to still EXIST — root having just been deleted
    # raises a bare FileNotFoundError that `_git` cannot convert (it only inspects
    # `returncode`), the branch leaks, and the CLI reports exit 1 for an operation that had
    # actually already succeeded. Canonicalising to the MAIN worktree (which is never the tree
    # being released) makes `root` a stable directory for the whole call.
    root = _main_worktree(repo_root(cwd))
    with _repo_lock(root):
        return _release_locked(root, task_id, role)


def _parse_workspace_name(name: str) -> tuple[str, int] | None:
    match = _NAME_RE.match(name)
    if match is None:
        return None
    return _ROLE_BY_PREFIX[match.group(1)], int(match.group(2))


def _build_workflow(root: Path) -> tuple:
    """(workflow, deadline) for one sweep. The deadline is returned rather than kept private
    because only the CALLER knows when the clock should start: it must be armed once the repo
    lock is HELD (see gc_workspaces), never here — a budget started before the flock would be
    spent WAITING for it, so every sweep under contention would abandon itself.

    Review Minor: `cwd=root` (the MAIN worktree — see gc_workspaces) is load-bearing, not
    decorative. `.vikunja-mcp.env` (the token) sits BESIDE `.vikunja-mcp.toml` in the repo,
    found by config.py's own walk-up from `cwd` — a linked worktree has neither file, so
    `load_config()` with no cwd would silently miss them whenever gc runs from inside one
    (the normal invocation site per SKILL.md), and fall through to env/user config or raise.
    """
    from vikunja_mcp.api import VikunjaAPI
    from vikunja_mcp.config import load_config
    from vikunja_mcp.workflow import Workflow

    cfg = load_config(cwd=root)
    # Review Important 3: BOUND the read, because it happens INSIDE the repo lock (Important 5
    # put it there deliberately, to close the race where a tree created between the read and the
    # reap is destroyed under a just-dispatched agent — do not move it back out). With api.py's
    # defaults an unreachable tracker costs 30s x 4 attempts + backoff ~= 2 MINUTES of held
    # lock per request, and everything queued behind it — every agent's `--release` — waits;
    # at wip_limit = 2 those are precisely the agents trying to clean up after themselves.
    #   * timeout 10s: generous for a JSON API that normally answers in tens of ms, and a
    #     black hole (the pathological case) now costs 10s of lock, not 120s.
    #   * no retries: this is HOUSEKEEPING on a loop. A transient 429/5xx costs one skipped
    #     sweep and the next tick retries anyway — sleeping through a backoff while holding a
    #     lock other agents need is strictly worse than trying again in ten minutes.
    # NOT the alternative (take the lock non-blocking, skip the sweep on contention): that
    # bounds how long gc WAITS, and the problem is how long gc HOLDS — it would not shorten
    # the wait of a single `--release` queued behind a hung board read by one second.
    #
    # VMCP-72: the per-request bound above is NOT the hold. The read is several requests and the
    # count grows with the board (see `_READ_DEADLINE_SECONDS`), so the deadline hook bounds the
    # TOTAL — the only one of the two that is invariant to page count.
    deadline = _ReadDeadline(_READ_DEADLINE_SECONDS)
    api = VikunjaAPI(
        cfg.url, cfg.token, timeout=_READ_TIMEOUT_SECONDS, max_retries=0,
        event_hooks={"request": [deadline]},
    )
    return Workflow(api, cfg.project_id), deadline


# THE GRADING POLICY (VMCP-68), and the shape is the point: expectedness is a property of the
# guard AND of the board AND of the ROLE, never of the guard alone. Both sets below now name the
# role they are about, and they arrived at it by the same route a card apart — see VMCP-91.
#   * _EXPECTED_IN_A_PARKED_BUILD_TREE — the two guards that protect ORDINARY in-progress work.
#     Both are routine in a BUILD tree while the task's card waits in Your Call: `call_human` parks
#     the card the moment a rebase conflicts (dirty) or a push is rejected (unpushed), which is
#     exactly when the tree holds unsaved work, and it stays that way for HOURS until a human
#     answers. The card is already the human's signal; a `kept` line every tick adds nothing. The
#     SAME two refusals on a card that is NOT parked mean work nobody is coming back for — that one
#     has to shout.
#
#     VMCP-91 ADDED THE `role` CONJUNCT, and it is the exact mirror of the one the review set got
#     in VMCP-68's round 2: every word of the justification above is about the BUILD agent — its
#     conflict, its rejected push, its `call_human` — while `dirty` is emitted by a guard that
#     "роли НЕ РАЗЛИЧАЕТ" (SKILL.md tells the reviewer so, and test_skill_contract builds the
#     state), so a REVIEW tree was laundered by a parked card it merely shares a task id with.
#     MEASURED before the fix, one sweep, three quiesced dead trees, all three cards in Your Call:
#     `kept=[]`, `expected=[(107,'review','dirty'), (110,'build','dirty'),
#     (113,'review','unpushed')]` — i.e. a human saw NOTHING. Reachable on the ordinary path for
#     `dirty`: the reviewer files `needs_work` without `--release`, the card goes back to Build,
#     the build agent hits a conflict and calls `call_human`. `unpushed` in a review tree needs a
#     hand-made branch (the tool only ever creates them detached), so it is constructible rather
#     than routine — both are in the set because the set is graded per CODE, and a conjunct that
#     held for only the reachable half would be a second thing to keep true.
#
#     A reviewer's tree is not excused by ANY board state, which is why the conjunct is on the role
#     rather than a wider parked set: the reviewer's contract is a verdict as a tracker COMMENT, so
#     a draft left in its tree is precisely the thing SKILL.md tells it to clear, and the parked
#     card belongs to someone else's unsaved work. The build side is untouched — deliberately, it
#     is the whole reason the set exists.
#   * _EXPECTED_IN_A_REVIEW_TREE — a detached REVIEW tree holding an in-tree commit. Permanent by
#     construction (the reachability guard rightly refuses to release it and `--gc` cannot reap
#     it), so it is the entry that would otherwise make `kept` non-empty FOREVER. SKILL.md's
#     answer is the fix, and it is a rule for the reviewer, not a chore for the human: write the
#     verdict as a tracker comment, never as a commit in the tree.
#
#     ROUND-2 REVIEW, and the reason this one is keyed on the ROLE and not on the code alone: the
#     whole justification above is about a REVIEW tree, but `unreachable-head` is emitted by the
#     detached branch of `_release_locked`, which a BUILD tree also reaches — the review's
#     counter-case came straight out of this project's own integration recipe. CONSTRUCTED AND
#     MEASURED: `git fetch origin && git rebase origin/main` interrupted mid-replay (a killed
#     turn, this project's documented failure mode) leaves the build tree `git status`-CLEAN,
#     DETACHED (`branch: None`), with the replayed commit reachable from no ref. Once the card
#     leaves Build the tree is dead, so the sweep grades it — and graded on the code alone it was
#     `expected`, i.e. filed under "do not look", forever, for a state ONLY a human can clear.
#     That is the shape of CODE_HALF_CREATED, which is correctly never routine. So: routine in a
#     review tree, an alarm in a build tree. This does NOT bring back the never-empty `kept` the
#     split exists to fix — an interrupted rebase is an incident, not a state the pipeline
#     produces on the happy path.
#
#     VMCP-86 KEPT THIS CONJUNCT AS A BACKSTOP, deliberately, and it is no longer the ONLY thing
#     standing between that build tree and a routine grading: `_release_locked` now refuses a
#     DETACHED BUILD tree upstream, with its own CODE_DETACHED_BUILD, so today nothing reaches
#     here with `unreachable-head` on a build tree. Dropping the conjunct on that ground would be
#     the same mistake in reverse — it would make the grading depend on a guard three hundred
#     lines away staying exactly as it is, and the whole policy is "fail toward shouting". Pinned
#     directly (test_keep_grading_of_unreachable_head_still_turns_on_the_role) rather than through
#     a sweep, precisely because no sweep can construct it any more.
# Neither set contains CODE_DETACHED_BUILD, CODE_HALF_CREATED, CODE_LOCKED, CODE_NO_WORKTREE,
# CODE_RELEASE_ERROR or CODE_SELF_TREE — all six, and that membership is DERIVED rather than
# restated: test_the_policy_comment_enumerations_are_derived_from_the_code reads the RUN OF NAMES
# above — the list itself, not this paragraph, because prose down here mentions some of them again
# — and fails unless it is exactly the codes in neither set. Derived because as a hand-kept copy it
# rotted twice, silently and in two different ways. It opened (VMCP-68) naming four and closing
# cleanly on "the other three"; VMCP-142 inserted CODE_LOCKED at position two and rewrote that to
# "the LAST three", which slid the referent past the new member and left it in a list with no bin at
# all. The other rot is older and simpler: VMCP-86 declared CODE_DETACHED_BUILD and never added it
# here, so the list was already short by one when VMCP-142 arrived. Neither rot was caught by the
# card that caused it; both were caught by VMCP-91, which was rewriting a DIFFERENT closed
# enumeration further down this block (the four bins under the grid), so what this needs is not
# more care but an assert.
#
# A parked card must not launder any of the six, and each has its own reason to shout. Read the
# bins as WHY-NOT-ROUTINE, not as a taxonomy: CODE_LOCKED also happens to be cleared by a git
# command (`git worktree unlock`), so "needs a git command" is not what separates the groups.
# CODE_HALF_CREATED and CODE_DETACHED_BUILD are the two whose refusal is a HANDOFF of specific
# commands against the tree — the first to a human, the second to the AGENT (`git rebase
# --continue`/`--abort`, which is why that refusal spells both out, since only the agent can know
# which) — and a parked card neither runs them nor makes them unnecessary.
# CODE_LOCKED has the paragraph directly below to itself, and that pointer is the repair for the
# orphaning: it was never unexplained, only unreferenced. CODE_NO_WORKTREE, CODE_RELEASE_ERROR and
# CODE_SELF_TREE describe gc itself, not the work in the tree.
#
# CODE_LOCKED (VMCP-142) is the one whose grading was an actual decision rather than a reading, so
# say why it is `kept`. A human `git worktree lock` IS an explicit human action, which sounds like
# the definition of "expected" — but expectedness here means "the pipeline produces this on the
# happy path AND the human already has a signal for it". Neither holds: nothing on the board says a
# tree is pinned (that is exactly what the parked card does for `dirty`/`unpushed`), and the lock
# makes the tree unreapable for as long as it stands, so filed under "do not look" it would leak in
# silence. That is the shape of CODE_HALF_CREATED, which is correctly never routine.
#
# WHAT VMCP-142 DID AND DID NOT MOVE in `--gc`, measured on both sides rather than assumed (the
# first wording here claimed "the alarm did not move" and a second pass disproved it). A locked tree
# that is otherwise CLEAN and PUSHED — the one that used to reach `git worktree remove` — kept its
# list: `release-error`/`kept` before, `locked`/`kept` now. But a locked BUILD tree that is ALSO
# dirty or unpushed never reached the remove at all: the guards above answered first, so under a
# PARKED card it graded `expected`, and now the lock answers first and it grades `kept`. That IS a
# move, in the safe direction: the parked card excuses unsaved work because the human will come back
# to it, and a lock is not unsaved work — nothing on that card says the tree cannot be reaped at
# all. It moved the REVIEW tree the SAME way at the time (`dirty`/`expected` -> `locked`/`kept`, in
# BOTH roles); what VMCP-91 changed is that the review half is no longer a move at all, because
# `dirty`/`unpushed` there are `kept` with or without the lock. A second pass caught this paragraph
# narrowing itself to "a locked BUILD tree" and then claiming the review tree "was never a move" —
# the third wording of a paragraph whose whole subject is not overstating a measurement.
#
# THE WHOLE GRID, since the absence of one is what let VMCP-91's hole live through two rounds of
# review of the very function it is in. Every code this module can emit, against both roles and
# both board states — `E` = expected, `K` = kept:
#
#     code               build+parked  build  review+parked  review
#     dirty                   E          K          K           K
#     unpushed                E          K          K           K
#     unreachable-head        K          K          E           E
#     detached-build          K          K          K           K
#     half-created            K          K          K           K
#     locked                  K          K          K           K
#     no-worktree             K          K          K           K
#     self-tree               K          K          K           K
#     release-error           K          K          K           K
#     <unknown / absent>      K          K          K           K
#
# A THIRD COLUMN the fix closed, which the card never named and only running the grid surfaced: an
# entry whose `role` key is ABSENT ALTOGETHER. Before, `dirty`/`unpushed` on a role-less entry under
# a parked card graded `expected`. The old docstring did NOT overclaim here — it said the `.get`
# made a role-less entry "fail the review conjunct", which was exactly true, because the review
# branch was the only one that read `role` at all; the build branch never asked. Now both do, so a
# role-less entry is `kept` in every cell. CONTRACT HARDENING, NOT A LIVE BUG, and worth saying so
# rather than counting it as a third defect: every `_release_locked` return and both entries `--gc`
# synthesises carry a `role`, so nothing in production reaches that column today.
#
# THREE of the ten rows above are not all-`K` — the two codes excused in a parked BUILD tree, plus
# `unreachable-head` in a review tree — and they come from TWO sets because the first two share
# theirs. The conjuncts differ in NUMBER, not just in content, and that asymmetry is the policy
# rather than an accident: the build pair needs THREE conditions (code, role, and the card in Your
# Call) because the card is the human's second signal, while `unreachable-head` needs TWO (code and
# role) because a reviewer's in-tree commit has a rule — write the verdict as a comment — rather
# than a chore, so no board state ever clears or excuses it. Every other row is `kept` in all four
# columns, and a parked card must never launder one: a broken tool state, a statement about gc
# itself, a standing human lock (CODE_LOCKED — its own paragraph above says why an explicit human
# action is still not routine), or a code this module does not know at all. Pinned as a grid by
# test_the_grading_grid_is_all_kept_outside_the_four_named_cells, so a new code lands in `kept` by
# default and a widened set has to argue with a test.
_EXPECTED_IN_A_PARKED_BUILD_TREE = frozenset({CODE_DIRTY, CODE_UNPUSHED})
_EXPECTED_IN_A_REVIEW_TREE = frozenset({CODE_UNREACHABLE_HEAD})


def _keep_is_expected(entry: dict, parked: set[int]) -> bool:
    """Is this refusal the routine state of a healthy board (-> `expected`), or something a human
    should look at (-> `kept`)?

    Fails toward SHOUTING, and that direction is deliberate: a code that is unknown here — a new
    guard, a renamed constant, a reason produced by something that never learned to set `code` —
    is UNEXPECTED, so it lands in `kept`. Wrong-and-noisy costs a human one glance; wrong-and-quiet
    is how the never-read signal this split exists to fix comes back in a new guise.

    Same direction on `role`, in BOTH branches (VMCP-68 for the review one, VMCP-91 for the build
    one): read with `.get`, so an entry that somehow carries no role fails its conjunct and shouts
    rather than KeyError-ing the sweep or being waved through. `task_id` stays a subscript because
    a refusal without one cannot be graded against the board at all and a KeyError beats a guess.
    """
    code = entry.get("code")
    if code in _EXPECTED_IN_A_REVIEW_TREE:
        return entry.get("role") == "review"
    return (code in _EXPECTED_IN_A_PARKED_BUILD_TREE
            and entry.get("role") == "build"
            and entry["task_id"] in parked)


def _read_liveness(wf, deadline) -> tuple[dict, set]:
    """ONE sweep's entire tracker read — the board plus every set derived from it — bounded as a
    whole (VMCP-72). Lifted out of `gc_workspaces` because "the thing the budget covers" is a
    unit worth naming: the budget must not stop at the board fetch, since `active_task_ids` still
    costs the `/user` request after it.

    ARMED HERE, which is to say once the CALLER holds the lock — never at construction. The
    budget exists to bound the HOLD and `_repo_lock` BLOCKS: started before the flock it would be
    spent waiting for another agent's sweep, and every contended tick would abandon a read it
    never got to start.

    WHY THE RELABELLING, which is the part that came out of running it rather than reasoning
    about it. The budget can end a read two ways and only one of them names itself: a request
    REFUSED before it is sent raises `ReadDeadlineExceeded`, but a request the budget CLAMPED
    mid-flight dies as httpx's own `ReadTimeout`. MEASURED end-to-end through the CLI against a
    slow tracker (6 s per request, a three-page board): the sweep stopped dead on the budget at
    30.27 s — correct — and reported `{"error": "ReadTimeout: timed out"}`, which a human cannot
    tell from ONE request timing out at 10 s. Right behaviour, unreadable report. So a failure
    raised with the budget already spent is re-raised as what it actually is, keeping the
    original as its `__cause__` and in its text.

    Narrow on purpose: `deadline.spent()` is the whole condition, so a failure with budget still
    on the clock — a 500, a refused connection, a bad token — propagates untouched rather than
    being laundered into "the tracker was slow". EVERY branch here raises or returns; none
    swallows. That is the KEEP invariant: `gc_workspaces` reaps nothing it has not read.
    """
    if deadline is not None:
        deadline.arm()
    try:
        board = wf.liveness_board()
        alive = {"build": set(wf.active_task_ids(board=board)),
                 "review": set(wf.review_task_ids(board=board))}
        # NOT a liveness set (a parked card's tree is dead, deliberately) — it only GRADES the
        # refusals below, off the same single fetch. See _keep_is_expected.
        parked = set(wf.parked_task_ids(board=board))
        return alive, parked
    except ReadDeadlineExceeded:
        raise                                       # already says what it is
    except Exception as exc:                        # noqa: BLE001 — re-raised either way
        if deadline is None or not deadline.spent():
            raise
        raise ReadDeadlineExceeded(
            f"the liveness read exceeded its {deadline.budget:.0f}s overall budget "
            f"({exc.__class__.__name__}: {exc}) — the sweep was abandoned with the repo lock "
            f"released and NOTHING inspected or removed; the next tick sweeps again"
        ) from exc


def gc_workspaces(cwd: Path | None = None, workflow=None) -> dict:
    """Reap worktrees whose task is no longer alive on the board.

    THE tracker-aware operation, and the reason this module ships with the tracker: a crashed
    agent leaves a tree behind, and nothing but the board can say whether the task behind it is
    still being worked. Liveness differs by role and must not be conflated — a BUILD tree is
    alive while its task is in Design/Build assigned to me, a REVIEW tree while its card is in
    Review (any assignee — a reviewer works on someone ELSE's card, so filtering by ownership
    would reap the tree out from under a running review). Read-only against the tracker, same
    class as `claimable`.

    The safety guards of release still apply: a dead task whose tree holds unpushed commits or
    a dirty working tree is KEPT and REPORTED, never destroyed — `--gc` runs on every
    orchestrator tick, unattended, so this is the one place a mistake is not a red test but an
    agent's work silently destroyed while nobody is watching.

    `cwd` may be INSIDE a linked worktree (the normal place for a per-task agent to run this
    from, per SKILL.md) — `here` below is exactly that tree's toplevel (or the main repo's, if
    invoked from there), and `root` canonicalises it to the MAIN worktree so every path
    derivation (`worktree_root`, `_build_workflow`'s config lookup) agrees with create/release
    regardless of where --gc itself was invoked (review Critical 1's fix, applied here too).

    Review Critical 2: `here` is ALSO never reaped once its task reads as DEAD — a task leaves
    Build the instant its agent calls `advance(to='review')`, and that agent is very often
    still sitting in the tree afterwards (about to release it itself, or simply running its
    next --gc tick before it gets there). Destroying the directory a live process is standing
    in is not "a red test", it is that process's shell cwd vanishing underneath it. Round 2,
    Minor 1: this guard runs AFTER the liveness check, not before — a LIVE self-tree (the
    mainline: --gc runs every tick from inside the agent's own tree) is just another live tree
    and produces no entry in either list; only a self-tree that is ALSO dead reaches this
    guard and gets refused-and-reported, which is the one case a human actually needs to see.

    VMCP-71: the self-guard above covers only a --gc invoked from INSIDE the tree, and the pump
    invokes it from the MAIN checkout — so a dead tree that was TOUCHED moments ago is skipped
    too, silently and in neither list, and reaped on a later sweep (`_REAP_GRACE_SECONDS`,
    `_last_activity`). That is the same overlap seen from the other side: a task leaves Build at
    `advance(to='review')` and a card leaves Review at a `needs_work` verdict, both while the
    agent that did it is still standing in the tree.

    VMCP-68: the refusals are reported in TWO lists, because "a human should look" and "expected,
    no action" were one list and the routine states never let it be empty — a Your Call card's
    unsaved work (every tick for hours) and a review tree's in-tree commit (forever). `kept` is
    now only the first kind, so EMPTY means nothing to read; `expected` is the second kind, kept
    and reported (nothing is hidden, and nothing is removed either — every entry still carries
    `released: false`) but not worth a look. The grading is `_keep_is_expected`, keyed on each
    refusal's `code`, its `role` and the board's parked set, and it fails toward `kept`. Round 2's
    fix to the live self-tree was this same failure in an earlier guise: whatever is added here
    later, the test to write is "on a healthy board, `kept` is empty".

    The two compose in one direction only, and it is the right one: a tree skipped as YOUNG never
    reaches a release guard, so it produces no refusal to grade and appears in NEITHER list —
    `expected` is for a refusal that WAS made and is routine, never for a tree gc declined to
    inspect.

    THE CADENCE THAT COMES OUT OF THAT COMPOSITION, measured across consecutive sweeps rather than
    reasoned about, because a report is read tick by tick: a standing refusal is reported on EVERY
    tick, so an empty `kept` means what VMCP-68 built it to mean. It did NOT use to: inspecting a
    tree means running `git status` inside it, that rewrites the index, and the next sweep then
    read the tree as freshly touched and skipped it as young — so `dirty` / `unpushed` /
    `unreachable-head` surfaced about once per `_REAP_GRACE_SECONDS` while refusals decided BEFORE
    any git call in the tree (`half-created`, `self-tree`) came every tick. VMCP-90 closed that at
    the source: gc's own inspection takes no optional locks (`_git_inspect`), so it is not a write
    and cannot pass for activity.

    AND IT CANNOT WIDEN THE REAPER, which is the direction that would have mattered: gc reaches
    `git status` only inside `_release_locked`, which then either REMOVES the tree (nothing
    survives to carry a taint) or REFUSES it — so the taint only ever lived on a tree some guard
    was already keeping, and no refusal depends on age. Dropping it therefore changes what is
    REPORTED, never what is removed: a tree gc now re-inspects every tick is one that has been
    quiet for a full window of SOMEBODY ELSE's activity, which is exactly the window's own
    promise. Both directions are pinned —
    test_gc_reports_a_standing_alarm_on_every_consecutive_sweep and
    test_gc_still_defers_to_a_real_write_in_a_tree_it_has_already_inspected.

    VMCP-72: the read under the lock is bounded OVERALL, not just per request
    (`_READ_DEADLINE_SECONDS`) — its request count grows with the board, so a per-request bound
    could not bound the hold. Past the budget the read RAISES, here, before a single tree has
    been inspected: the sweep is skipped whole and the next tick does it again. That direction is
    the invariant — a truncated or failed `alive` set must never reach the loop below, where a
    live tree missing from it would read as dead. It applies to the WHOLE read, so VMCP-68's
    `parked` set — a third consumer of the same fetch, and the one that made "Your Call" drive
    pagination too — is inside the budget rather than beside it.
    """
    here = repo_root(cwd).resolve()
    root = _main_worktree(here)
    # an injected workflow (tests) brings no client and therefore no deadline — the bound is a
    # property of the client gc BUILDS, so there is nothing to arm on a caller-supplied one.
    wf, deadline = (workflow, None) if workflow is not None else _build_workflow(root)
    wt_root = worktree_root(root)

    released, kept, expected = [], [], []
    # ONE lock for the whole sweep: _repo_lock is not reentrant, so call the _locked core, never
    # the public release_workspace wrapper (that would deadlock on its own flock).
    with _repo_lock(root):
        # Review Important 5: the liveness READ must happen INSIDE the lock. Taken before it,
        # a task could be claimed and its tree created between the read and the reap (that
        # `ensure_workspace` call serialises against the SWEEP via the same flock, but not
        # against a liveness snapshot taken before the flock was even acquired) — the fresh
        # tree is clean and pushed, so every guard below passes and it is destroyed out from
        # under a just-dispatched agent. One board fetch serves every set (Important 4), and
        # VMCP-72 bounds that whole read — arming the budget with the lock already held.
        alive, parked = _read_liveness(wf, deadline)
        for wt in list_worktrees(root):
            if wt["path"].parent != wt_root:
                # not ours — skip a hand-made worktree. Review Minor 12a: this guard is NOT
                # what protects that worktree, and a future refactor must not believe it is.
                # Constructed and measured: with this line deleted, a hand-made `task-77`
                # worktree outside the root is STILL untouched — `_release_locked` re-derives
                # the canonical path from `worktree_root` and never trusts the enumerated one,
                # so it simply finds nothing there (`released: []`, `kept: [77] "no worktree
                # for this task"`, tree and branch intact). Re-measured on this wave: all 59
                # workspace tests stay green with the guard deleted, so nothing here pins it —
                # the comment IS the pin. What the guard actually buys is the ABSENCE of that
                # bogus `kept` entry — real value (the `kept` signal discipline in SKILL.md
                # depends on it staying quiet), but not safety. Let `_release_locked` trust the
                # enumerated path and this line becomes load-bearing overnight, silently.
                continue
            parsed = _parse_workspace_name(wt["path"].name)
            if parsed is None:
                continue                       # under our root but not task-<id>/review-<id>
            role, task_id = parsed
            if task_id in alive[role]:
                # Review round 2, Minor 1: the alive check runs BEFORE the self-guard below.
                # --gc runs on every tick from inside the agent's OWN tree (the docstring's
                # own mainline), so a healthy self-tree used to fall through to the self-guard
                # and get reported under `kept` on every single sweep — a signal that is never
                # empty is a signal nobody reads. A live self-tree now takes this branch like
                # any other live tree: no entry in EITHER list. A DEAD self-tree still reaches
                # the guard below and is still refused and reported — exactly the case a human
                # needs to see.
                continue
            if wt["path"] == here:
                # Critical 2's guard: never reap the tree gc itself is running from — reached
                # only once the tree is ALREADY known dead (see above), and BEFORE the grace
                # window below (see its own note on why that order is the deliberate one).
                #
                # Straight into `kept`, never graded (VMCP-68): this refusal is about gc's own
                # invocation site, so no board state can make it routine — CODE_SELF_TREE is in
                # neither expected set. Same for the exception below.
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "code": CODE_SELF_TREE,
                    "reason": "gc was invoked from inside this worktree — refusing to remove it",
                })
                continue
            last = _last_activity(wt["path"])
            if last is not None and 0 <= time.time() - last < _REAP_GRACE_SECONDS:
                # VMCP-71's grace window: this tree is dead but was touched moments ago, so its
                # agent may still be standing in it between `advance(to='review')` and
                # `--release`. Defer to a later sweep — the reap is postponed, never cancelled.
                #
                # SILENTLY, in NEITHER list, deliberately: `kept` means "a human should look", and
                # a tree that is merely YOUNG is not that. A previous round already had to fix
                # `kept` becoming never-empty; the pump's every tick would otherwise carry an
                # entry for every tree that finished in the last half hour.
                #
                # AFTER the self-guard above, also deliberately: "gc was invoked from inside this
                # worktree" is the stronger and more specific statement about the same tree (that
                # one KNOWS a process is there, this one only suspects it), and being young must
                # not silence a report a human can act on. Pinned by
                # test_gc_from_inside_a_dead_tree_completes_the_whole_sweep, whose self-tree is
                # left young precisely so this ordering cannot be flipped unnoticed.
                #
                # `0 <=` bounds the window BELOW as well as above: an mtime in the FUTURE (clock
                # skew, a restored backup, an unpacked archive) would otherwise read as young on
                # every sweep forever, and this skip is silent — the one combination that leaks a
                # tree with nothing to notice. Out-of-window in either direction falls through to
                # the release guards, which still refuse to destroy anything that holds work.
                #
                # It decides only the case where EVERY marker is future (VMCP-84). While one real
                # marker survives, `_last_activity` no longer offers the future one at all — this
                # bound used to be reached with a MAX taken over both, so a skewed mtime masked a
                # fresh one and the tree was reaped mid-turn. The two now split the job cleanly:
                # which markers count is the reader's, what an all-bad reading means is this line's.
                continue
            try:
                result = _release_locked(root, task_id, role)
            except Exception as e:  # noqa: BLE001 — Important 3: one bad tree (locked, a race,
                # a permission error — anything _git surfaces as WorkspaceError, or worse) must
                # never abort the sweep and discard every verdict already decided for the OTHER
                # trees. Report it exactly like any other refusal and keep going.
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "code": CODE_RELEASE_ERROR,
                    "reason": f"{e.__class__.__name__}: {e}",
                })
                continue
            if result["released"]:
                released.append(result)
            else:
                (expected if _keep_is_expected(result, parked) else kept).append(result)
    return {"released": released, "kept": kept, "expected": expected}


def run_workspace(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vikunja-mcp workspace")
    parser.add_argument("task_id", nargs="?", type=int, help="create a workspace for this task")
    # default=None, NOT "build": every guard below has to answer "did the caller ASK for a
    # role?", and an eager default makes that unanswerable — `--gc --role review` would be
    # indistinguishable from a plain `--gc`. The default is applied once, just below.
    parser.add_argument("--role", choices=("build", "review"), default=None)
    parser.add_argument("--at", help="review role: the ref to check out (default origin/<main>)")
    parser.add_argument("--release", type=int, metavar="TASK_ID")
    parser.add_argument("--gc", action="store_true",
                         help="reap worktrees whose task is no longer alive on the board")
    try:
        args = parser.parse_args(argv)
        role = args.role or "build"
        # Review Important 6 + Minor 7: argparse lets every one of these combinations through,
        # and each USED to be accepted with one of the arguments silently dropped — `42
        # --release 9` acted on 9 and forgot 42; `--gc --at <sha>` swept anyway; `42 --at <sha>`
        # (no --role review) ignored the sha even though --help says it is review-only. A
        # silently ignored argument on a CLI a pump drives unattended is how a reviewer ends up
        # somewhere it never asked to be. Refuse instead; the caller can always say it again.
        if args.gc:
            if args.task_id is not None or args.release is not None:
                raise WorkspaceError("--gc cannot be combined with a task id or --release")
            if args.role is not None or args.at is not None:
                raise WorkspaceError("--gc takes no --role/--at: it sweeps both roles, at no ref")
            result = gc_workspaces()
        elif args.release is not None:
            if args.task_id is not None:
                raise WorkspaceError(
                    f"--release {args.release} already names the task — drop the positional "
                    f"{args.task_id}, or drop --release to CREATE a workspace for it"
                )
            if args.at is not None:
                raise WorkspaceError("--at is for creating a review tree, not for --release")
            result = release_workspace(args.release, role=role)
        elif args.task_id is not None:
            if args.at is not None and role != "review":
                raise WorkspaceError("--at applies only to --role review")
            result = ensure_workspace(args.task_id, role=role, at=args.at)
        else:
            raise WorkspaceError("give a task id to create, or --release <task id>")
    # NO `except SystemExit: raise` here (review Minor 10): SystemExit derives from
    # BaseException, so the `except Exception` below never caught it in the first place —
    # argparse's own exits (`--role bogus`, `--help`) pass straight through either way. The
    # clause read as load-bearing and was not.
    except Exception as e:      # noqa: BLE001 — a CLI: ANY failure is one JSON line + exit 1
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        return 1
    print(json.dumps(result))
    return 0
