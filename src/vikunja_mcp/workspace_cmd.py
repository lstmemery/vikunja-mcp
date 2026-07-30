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


class WorkspaceError(Exception):
    """The message is printed as the CLI's JSON error line."""


def _run_git(
    args: tuple[str, ...], cwd: Path | None, timeout: float | None
) -> subprocess.CompletedProcess:
    env = {**os.environ, "GIT_TERMINAL_PROMPT": "0"}
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


def _git(*args: str, cwd: Path | None = None, timeout: float | None = None) -> str:
    proc = _run_git(args, cwd, timeout)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    return _run_git(args, cwd, None).returncode == 0


def repo_root(cwd: Path | None = None) -> Path:
    return Path(_git("rev-parse", "--show-toplevel", cwd=cwd))


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
    Path equality, not strings."""
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
    """Newest mtime of the two footprints a WORKING agent leaves in a worktree — the input to the
    grace window (`_REAP_GRACE_SECONDS`). None when NEITHER can be read, which the caller must
    treat as "no opinion" and fall through to the ordinary guards: a directory that is already
    gone has nobody standing in it, and a silent skip that can never expire would leak a tree
    with no report at all.

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

    MEASURED on git 2.50.1, and load-bearing when reading the caller: `git status --porcelain`
    REWRITES the index every single time, even in a clean tree — so gc's own inspection bumps this
    marker. That can only make a tree look YOUNGER, i.e. keep it, and a genuinely reapable tree is
    REMOVED by the same sweep that inspects it; so the taint can only re-delay a tree some guard is
    already keeping (dirty, unpushed, half-created), whose `kept` line then reports about once per
    grace window instead of every tick. Quieter, never a lost verdict.

    COST, since the sweep holds the repo-wide flock throughout: two stats and one local `rev-parse`
    per DEAD tree — live trees short-circuit before this is ever called — against a board read the
    same lock already covers.
    """
    candidates = [wt_path]
    try:
        index = Path(_git("rev-parse", "--git-path", "index", cwd=wt_path))
        candidates.append(index if index.is_absolute() else wt_path / index)
    except (WorkspaceError, OSError):
        # OSError as well as WorkspaceError: with cwd pointing at a directory that no longer
        # exists, subprocess.run raises a bare FileNotFoundError that `_git` cannot convert (it
        # only ever inspects `returncode`). Such an entry IS still listed — git refuses to prune a
        # LOCKED one — so this is reachable, and the directory mtime below fails the same way.
        pass
    newest: float | None = None
    for candidate in candidates:
        try:
            mtime = candidate.stat().st_mtime
        except OSError:
            continue
        newest = mtime if newest is None else max(newest, mtime)
    return newest


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
                "path": str(worktree_root(root) / name), "reason": "no worktree for this task"}
    path = wt["path"]
    if wt["lock_reason"] == _LOCK_INITIALIZING:
        # The OUTCOME here is unchanged — a half-created tree was already kept, on every tick,
        # forever. What was wrong is the DIAGNOSIS: `git status` inside it reports the staged
        # deletions of every missing file, so the guard below called it "working tree is dirty
        # (N entries)" and sent a human looking for uncommitted work that does not exist.
        # Say what it actually is, once, in a line `--gc`'s `kept` can be acted on.
        #
        # Keyed on the marker TEXT (unlike _ensure_locked's guard, which keys on the bool): both
        # branches KEEP the tree, so a miss costs only wording. Deliberately narrow, because a
        # human `git worktree lock` should keep falling through to `git worktree remove`'s own
        # refusal ("is a locked working tree, use 'remove -f -f' if you insist") — that message is
        # already correct and specific, and swallowing it into a synthesised reason would replace
        # git's report with our guess about it.
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "reason": f"half-created worktree (git's own `locked {_LOCK_INITIALIZING}` "
                          f"marker from a killed `worktree add`) — needs a human: "
                          f"`git worktree unlock {path} && git worktree remove -f -f {path}`"}
    dirty = _git("status", "--porcelain", cwd=path)
    if dirty:
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "reason": f"working tree is dirty ({len(dirty.splitlines())} entries)"}
    if wt["branch"] is not None:
        # a task/<id> BRANCH's unique history is only safe once it's on origin — the
        # unpushed-commits guard.
        base = f"origin/{default_base(root)}"
        unpushed = _git("log", "--oneline", f"{base}..HEAD", cwd=path)
        if unpushed:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "reason": f"{len(unpushed.splitlines())} commit(s) not on {base}"}
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
        head = _git("rev-parse", "HEAD", cwd=path)
        reachable = _git("for-each-ref", "--contains", head, "--format=%(refname)", cwd=root)
        if not reachable:
            return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                    "reason": f"detached HEAD {head} is reachable from no ref"}
    _git("worktree", "remove", str(path), cwd=root)
    if wt["branch"]:
        _git("branch", "-D", wt["branch"], cwd=root)
    return {"released": True, "task_id": task_id, "role": role,
            "path": str(path), "branch": wt["branch"]}


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


def _build_workflow(root: Path):
    # Review Minor: `cwd=root` (the MAIN worktree — see gc_workspaces) is load-bearing, not
    # decorative. `.vikunja-mcp.env` (the token) sits BESIDE `.vikunja-mcp.toml` in the repo,
    # found by config.py's own walk-up from `cwd` — a linked worktree has neither file, so
    # `load_config()` with no cwd would silently miss them whenever gc runs from inside one
    # (the normal invocation site per SKILL.md), and fall through to env/user config or raise.
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
    return Workflow(
        VikunjaAPI(cfg.url, cfg.token, timeout=10, max_retries=0), cfg.project_id
    )


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
    """
    here = repo_root(cwd).resolve()
    root = _main_worktree(here)
    wf = workflow if workflow is not None else _build_workflow(root)
    wt_root = worktree_root(root)

    released, kept = [], []
    # ONE lock for the whole sweep: _repo_lock is not reentrant, so call the _locked core, never
    # the public release_workspace wrapper (that would deadlock on its own flock).
    with _repo_lock(root):
        # Review Important 5: the liveness READ must happen INSIDE the lock. Taken before it,
        # a task could be claimed and its tree created between the read and the reap (that
        # `ensure_workspace` call serialises against the SWEEP via the same flock, but not
        # against a liveness snapshot taken before the flock was even acquired) — the fresh
        # tree is clean and pushed, so every guard below passes and it is destroyed out from
        # under a just-dispatched agent. One board fetch serves both sets (Important 4).
        board = wf.liveness_board()
        alive = {"build": set(wf.active_task_ids(board=board)),
                 "review": set(wf.review_task_ids(board=board))}
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
                # depends on it staying quiet), but not
                # safety. Let `_release_locked` trust the enumerated path and this line becomes
                # load-bearing overnight, silently.
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
                # only once the tree is ALREADY known dead (see above).
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]),
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
                continue
            try:
                result = _release_locked(root, task_id, role)
            except Exception as e:  # noqa: BLE001 — Important 3: one bad tree (locked, a race,
                # a permission error — anything _git surfaces as WorkspaceError, or worse) must
                # never abort the sweep and discard every verdict already decided for the OTHER
                # trees. Report it exactly like any other refusal and keep going.
                kept.append({
                    "released": False, "task_id": task_id, "role": role,
                    "path": str(wt["path"]), "reason": f"{e.__class__.__name__}: {e}",
                })
                continue
            (released if result["released"] else kept).append(result)
    return {"released": released, "kept": kept}


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
