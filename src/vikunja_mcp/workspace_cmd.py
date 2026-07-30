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
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

BUILD_NAME = "task-{task_id}"
REVIEW_NAME = "review-{task_id}"
BUILD_BRANCH = "task/{task_id}"
_NAME_RE = re.compile(r"^(task|review)-(\d+)$")
_ROLE_BY_PREFIX = {"task": "build", "review": "review"}


class WorkspaceError(Exception):
    """The message is printed as the CLI's JSON error line."""


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


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
    import os

    from vikunja_mcp.config import ENV_WORKTREE_ROOT, load_config

    root = _main_worktree(root)
    # env FIRST, on purpose: create/release need no tracker config at all, and load_config
    # RAISES without url/project_id — reading it first would throw away a perfectly good
    # VIKUNJA_WORKTREE_ROOT in any repo that is not tracker-configured.
    configured = os.environ.get(ENV_WORKTREE_ROOT)
    if not configured:
        try:
            configured = load_config(cwd=root).worktree_root
        except Exception:  # noqa: BLE001 — no tracker config is fine; create/release need none
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
            current = {"path": Path(value), "branch": None, "detached": False}
        elif key == "branch" and current is not None:
            # removeprefix, NOT rsplit("/") — refs/heads/task/42 must stay "task/42"
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached" and current is not None:
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


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
    _git("fetch", "origin", cwd=root)
    wt_root = worktree_root(root)
    wt_root.mkdir(parents=True, exist_ok=True)
    base = f"origin/{default_base(root)}"

    existing = _find(root, task_id, role)
    if existing is not None:
        return {
            "role": role, "task_id": task_id, "path": str(existing["path"]),
            "branch": existing["branch"], "created": False,
        }

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


def _release_locked(root: Path, task_id: int, role: str) -> dict:
    _check_role(role)
    _git("worktree", "prune", cwd=root)
    wt = _find(root, task_id, role)
    if wt is None:
        return {"released": False, "task_id": task_id, "role": role,
                "reason": "no worktree for this task"}
    path = wt["path"]
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
    root = repo_root(cwd)
    with _repo_lock(root):
        return _ensure_locked(root, task_id, role, at)


def release_workspace(task_id: int, role: str = "build", cwd: Path | None = None) -> dict:
    root = repo_root(cwd)
    with _repo_lock(root):
        return _release_locked(root, task_id, role)


def _parse_workspace_name(name: str) -> tuple[str, int] | None:
    match = _NAME_RE.match(name)
    if match is None:
        return None
    return _ROLE_BY_PREFIX[match.group(1)], int(match.group(2))


def _build_workflow():
    from vikunja_mcp.api import VikunjaAPI
    from vikunja_mcp.config import load_config
    from vikunja_mcp.workflow import Workflow

    cfg = load_config()
    return Workflow(VikunjaAPI(cfg.url, cfg.token), cfg.project_id)


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
    from, per SKILL.md) — `root` below is whatever toplevel that resolves to, and every path
    derivation from it (`worktree_root`) canonicalises to the MAIN worktree internally, so the
    "is this one of ours" check below never disagrees with create/release about where trees
    live regardless of where --gc itself was invoked.
    """
    root = repo_root(cwd)
    wf = workflow if workflow is not None else _build_workflow()
    alive = {"build": set(wf.active_task_ids()), "review": set(wf.review_task_ids())}
    wt_root = worktree_root(root)

    released, kept = [], []
    # ONE lock for the whole sweep: _repo_lock is not reentrant, so call the _locked core, never
    # the public release_workspace wrapper (that would deadlock on its own flock).
    with _repo_lock(root):
        for wt in list_worktrees(root):
            if wt["path"].parent != wt_root:
                continue                       # not ours — never touch a hand-made worktree
            parsed = _parse_workspace_name(wt["path"].name)
            if parsed is None:
                continue
            role, task_id = parsed
            if task_id in alive[role]:
                continue
            result = _release_locked(root, task_id, role)
            (released if result["released"] else kept).append(result)
    return {"released": released, "kept": kept}


def run_workspace(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vikunja-mcp workspace")
    parser.add_argument("task_id", nargs="?", type=int, help="create a workspace for this task")
    parser.add_argument("--role", choices=("build", "review"), default="build")
    parser.add_argument("--at", help="review role: the ref to check out (default origin/<main>)")
    parser.add_argument("--release", type=int, metavar="TASK_ID")
    parser.add_argument("--gc", action="store_true",
                         help="reap worktrees whose task is no longer alive on the board")
    try:
        args = parser.parse_args(argv)
        if args.gc:
            result = gc_workspaces()
        elif args.release is not None:
            result = release_workspace(args.release, role=args.role)
        elif args.task_id is not None:
            result = ensure_workspace(args.task_id, role=args.role, at=args.at)
        else:
            raise WorkspaceError("give a task id to create, or --release <task id>")
    except SystemExit:
        raise
    except Exception as e:      # noqa: BLE001 — a CLI: ANY failure is one JSON line + exit 1
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        return 1
    print(json.dumps(result))
    return 0
