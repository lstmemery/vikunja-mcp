"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import fcntl
import json
import os
import shutil
import subprocess
import time
import tomllib
from pathlib import Path

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import workspace_cmd
from vikunja_mcp.api import _MAX_UNPROVEN_PAGES as MAX_UNPROVEN_PAGES
from vikunja_mcp.api import VikunjaAPI, VikunjaError
from vikunja_mcp.config import ENV_WORKTREE_ROOT
from vikunja_mcp.workflow import STAGES, Workflow
from vikunja_mcp.workspace_cmd import (
    ReadDeadlineExceeded,
    WorkspaceError,
    ensure_workspace,
    gc_workspaces,
    list_worktrees,
    release_workspace,
    run_workspace,
    worktree_root,
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


# --- VMCP-76 (525): a stand-in this file parks must never outlive the test that parked it ---
#
# Two tests need a git child that HANGS, so that the module's own timeout SIGKILLs the git process
# sitting on top of it: a smudge filter parks `worktree add`'s checkout (`_half_created_tree`) and
# an ssh stand-in parks `git fetch` (`test_the_fetch_under_the_lock_...`). `subprocess.run`'s kill
# reaches the DIRECT child ONLY, so whatever is parked UNDER it is reparented onto PID 1 and keeps
# running long after the test that built it has ended.
#
# MEASURED before this fix — `ps` around a run given its own `--basetemp`, so that a sibling agent
# running this same suite could not have its identically named processes counted as ours. ONE
# half-created test left THREE processes behind, all with their cwd inside the tmp worktree:
# `git reset --hard --no-recurse-submodules` at PPID 1, its `/bin/sh …/slow-smudge.sh`, and that
# shell's own `sleep 30` child — the third is easy to miss, because grepping `ps` for the two NAMES
# you expect does not match a bare `sleep`. They were still alive at t+56s and gone by t+61s past
# a 2.70s test (the test's own duration is a sample, 2.65–2.93s across runs, not a constant). The
# open fd on `<tmp>/work/.git/worktrees/task-<id>/index.lock` belongs to the CHECKOUT alone, not to
# all three: `lsof` on the shell lists its cwd, its pipes and the script, and no lock.
#
# Counted rather than derived, by polling `ps` through one pre-fix run of this FILE and unioning
# distinct pids: THIRTY-TWO processes — 6 orphaned checkouts, one per `_half_created_tree` call
# site as the file then stood; the 12 filter shells those spawn, TWO per site because the filter
# runs once per tracked file; those shells' 12 `sleep 30` children; and the fetch test's own
# stand-in plus its sleep. An arithmetic guess of "two per call site" is exactly half of that.
#
# What the leak was OBSERVED not to touch — inferred from where it wrote, not from a constraint
# that forbids it: every cwd and every open fd sat inside that run's own basetemp, so nothing
# reached the real checkout or a concurrent run under another basetemp. The cost is background
# processes holding a tmp dir pytest owns and may be deleting.
#
# So the stand-ins PARK ON A FILE instead of sleeping a flat 30s, and the fixture unlinks it. The
# reap is COOPERATIVE on purpose. `pgrep`-by-name is out: this suite runs beside sibling agents
# spawning processes with the very same argv, and killing one of those is a far worse failure than
# the leak. A blind SIGKILL of a pid recorded seconds ago is out for the same class of reason — a
# pid can be recycled. The pids are recorded for the WAIT: teardown PROVES the processes are gone
# rather than assuming the unlink worked.
_PARK_HOLD = "park-hold"      # while this file exists, a parked stand-in keeps parking
_PARK_PIDS = "park-pids"      # each stand-in appends its own pid here
_PARK_CEILING = 300           # x 0.1s, the backstop for a pytest that is ITSELF killed: teardown
                              # never runs then, and an UNBOUNDED park would leak worse than the
                              # bug being fixed. MEASURED at 33.2s PER PARK with the hold file
                              # never removed (30s of sleeping plus the loop's own fork/exec
                              # overhead) — and a half-created tree parks once per tracked file,
                              # so the survival bound a killed pytest actually leaves behind is
                              # TWO of those, measured at 66.0s. That is the flat `sleep 30` this
                              # replaced plus ~10%, i.e. no regression, but it is not below it


def _parking_script(tmp_path: Path, name: str, tail: str = "") -> Path:
    """An executable stand-in that parks until the fixture releases it, and says it is there.

    Hand it to something GIT will spawn (a filter, `GIT_SSH_COMMAND`), which is the whole point:
    the process that leaks is the git one in between. Launching it straight from a test body would
    make the test runner itself the parent the reaper derives — see `_live_parent`, which refuses
    that rather than waiting for pytest to exit.
    """
    script = tmp_path / name
    script.write_text(
        "#!/bin/sh\n"
        f'echo "$$" >> "{tmp_path / _PARK_PIDS}"\n'
        "i=0\n"
        f'while [ -e "{tmp_path / _PARK_HOLD}" ] && [ "$i" -lt {_PARK_CEILING} ]; do\n'
        "  sleep 0.1\n"
        "  i=$((i + 1))\n"
        "done\n"
        f"{tail}"
    )
    script.chmod(0o755)
    (tmp_path / _PARK_HOLD).write_text("")
    return script


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:      # alive, and no longer ours — i.e. the pid was recycled
        return True
    return True


def _ps_field(pid: int, field: str) -> str:
    return subprocess.run(
        ["ps", "-o", f"{field}=", "-p", str(pid)], capture_output=True, text=True
    ).stdout.strip()


def _live_parent(pid: int) -> set[int]:
    """The pid's parent, when that parent is something the reaper may legitimately wait out.

    Two parents it may not. PID 1 (or a blank answer, i.e. the child has just exited) means there
    is nothing left to wait for. THIS process means the stand-in was launched by the test runner
    rather than by git, and waiting on it would hang out the whole deadline and then fail on
    pytest's own pid. Not reachable with today's two stand-ins — both are spawned by git — but it
    costs one comparison to keep it unreachable rather than merely undocumented.
    """
    ppid = _ps_field(pid, "ppid")
    if not ppid.isdigit() or int(ppid) <= 1 or int(ppid) == os.getpid():
        return set()
    return {int(ppid)}


def _process_command(pid: int) -> str:
    return _ps_field(pid, "command")


def _parked_pids(tmp_path: Path) -> set[int]:
    """The stand-ins that recorded themselves, PLUS the live parent of any still running.

    The parent is what actually leaks in the smudge case — it is the orphaned
    `git reset --hard --no-recurse-submodules` (verified live: `sh …/slow-smudge.sh` at pid 1991
    had ppid 1990, which `ps` showed as exactly that git process) — so it has to be waited on.
    But it is DERIVED HERE rather than recorded by the script, because a parent is only ever
    safe to wait on while it is alive: the fetch stand-in's parent is the git that was SIGKILLed,
    and a pid recorded a second ago and since dead may already have been recycled onto something
    unrelated, which the reaper would then wait out the full deadline for and fail on. `ppid > 1`
    is the same rule in its other form — a stand-in already reparented onto PID 1 has no parent
    left to wait for.
    """
    pidfile = tmp_path / _PARK_PIDS
    if not pidfile.exists():
        return set()
    recorded = {int(token) for token in pidfile.read_text().split()}
    live = [child for child in recorded if _pid_alive(child)]
    return recorded | {pid for child in live for pid in _live_parent(child)}


def _reap_parked_children(tmp_path: Path, deadline: float = 30.0) -> None:
    """Release every parked stand-in and WAIT until nothing it left behind is still running."""
    # BEFORE the unlink: while everything is still parked, the orphaned checkout is reliably
    # visible as a live parent. That holds only WHILE something is parked — with the ceiling
    # forced to 0.1s and a repo big enough to keep the checkout churning, a live checkout was
    # measured missing from 8 of 20 samples — so it rests on every test body here finishing well
    # inside `_PARK_CEILING`, which at 33s they all do by two orders of magnitude.
    watched = _parked_pids(tmp_path)
    (tmp_path / _PARK_HOLD).unlink(missing_ok=True)
    alive = {pid for pid in watched if _pid_alive(pid)}
    end = time.monotonic() + deadline
    while alive and time.monotonic() < end:
        time.sleep(0.05)
        # UNION, not re-read: releasing the hold lets the orphaned checkout resume and spawn the
        # NEXT stand-in (one per tracked file), which records itself only now — while the parent
        # derived above drops back out of the file's view as soon as its child exits. Either half
        # alone returns with something still running.
        watched |= _parked_pids(tmp_path)
        alive = {pid for pid in watched if _pid_alive(pid)}
    assert not alive, (
        f"{sorted(alive)} outlived the test by {deadline:.0f}s after {tmp_path / _PARK_HOLD} was "
        f"removed: a stand-in stopped honouring the hold file, or something else is parking here"
    )


def _reaping_parked_children(tmp_path: Path):
    """The `repo` fixture's teardown half, as a plain generator SO THAT A TEST CAN DRIVE IT.

    The whole point of putting the reap after a `yield` is that pytest runs it whatever the test's
    outcome — and a test that passes cannot demonstrate that. Driving this generator by hand runs
    the very code the fixture runs, against real processes, in one round.
    """
    yield
    _reap_parked_children(tmp_path)


@pytest.fixture
def _parked_children_reaped(tmp_path):
    yield from _reaping_parked_children(tmp_path)


@pytest.fixture
def repo(tmp_path, monkeypatch, _parked_children_reaped):
    """A work repo on `main` with a local bare origin it has already pushed to.

    It requests `_parked_children_reaped` so that EVERY test built on this fixture is covered
    without having to know the leak exists — set up first, so torn down last, i.e. after
    everything else in the test has finished writing.
    """
    # A REAL review finding: once the pump exports VIKUNJA_WORKTREE_ROOT machine-wide (the
    # exact point of this feature), an agent running this suite inside its own worktree would
    # otherwise get every test here steered at the AMBIENT root instead of tmp_path — and the
    # litter it writes there survives the test run and poisons whatever runs next.
    monkeypatch.delenv(ENV_WORKTREE_ROOT, raising=False)
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "README.md").write_text("hi\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_create_makes_a_worktree_on_a_task_branch(repo):
    res = ensure_workspace(42, cwd=repo)
    path = Path(res["path"])
    assert res["created"] is True and res["branch"] == "task/42"
    assert path.is_dir() and (path / "README.md").exists()
    assert path.parent == worktree_root(repo) == repo.parent / "work.worktrees"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/42"


def test_create_is_idempotent(repo):
    first = ensure_workspace(42, cwd=repo)
    second = ensure_workspace(42, cwd=repo)
    assert second["path"] == first["path"] and second["created"] is False


def test_create_reuses_an_existing_branch_and_keeps_its_commits(repo):
    """The resume-after-crash path: the agent's unfinished commits must survive."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "wip.txt").write_text("half done\n")
    _git(path, "add", "wip.txt")
    _git(path, "commit", "-m", "wip")
    sha = _git(path, "rev-parse", "HEAD")
    _git(repo, "worktree", "remove", str(path))          # agent died, tree gone, branch kept

    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is True and again["branch"] == "task/42"
    assert _git(Path(again["path"]), "rev-parse", "HEAD") == sha


def test_review_role_is_a_separate_detached_tree(repo):
    build = ensure_workspace(42, cwd=repo)
    head = _git(repo, "rev-parse", "HEAD")
    review = ensure_workspace(42, role="review", at=head, cwd=repo)
    assert review["path"] != build["path"]
    assert Path(review["path"]).name == "review-42"
    assert _git(Path(review["path"]), "rev-parse", "HEAD") == head
    assert _git(Path(review["path"]), "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached


def test_release_removes_a_clean_pushed_tree_and_its_branch(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    res = release_workspace(42, cwd=repo)
    assert res["released"] is True
    assert not path.exists()
    assert "task/42" not in _git(repo, "branch", "--list", "task/42")


def test_release_refuses_a_dirty_tree(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "scratch.txt").write_text("uncommitted\n")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "dirty" in res["reason"]
    assert path.exists() and (path / "scratch.txt").exists()


def test_release_refuses_unpushed_commits(repo):
    """THE guard: housekeeping must never be how an agent's work disappears."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "real work")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "commit" in res["reason"]
    assert path.exists()
    assert _git(path, "log", "--oneline", "-1")


def test_release_of_a_missing_tree_is_not_an_error(repo):
    res = release_workspace(999, cwd=repo)
    assert res["released"] is False and "no worktree" in res["reason"]


def test_occupied_path_that_is_not_a_worktree_is_refused(repo):
    squatter = worktree_root(repo) / "task-42"
    squatter.mkdir(parents=True)
    (squatter / "precious.txt").write_text("do not clobber\n")
    with pytest.raises(WorkspaceError, match="not a registered worktree"):
        ensure_workspace(42, cwd=repo)
    assert (squatter / "precious.txt").exists()


def test_worktree_root_honours_an_explicit_override(repo, monkeypatch):
    monkeypatch.setenv("VIKUNJA_WORKTREE_ROOT", str(repo.parent / "elsewhere"))
    res = ensure_workspace(42, cwd=repo)
    assert Path(res["path"]).parent == repo.parent / "elsewhere"


def test_list_worktrees_reports_slashed_branch_names_intact(repo):
    ensure_workspace(42, cwd=repo)
    branches = {wt["branch"] for wt in list_worktrees(repo)}
    assert "task/42" in branches       # not "42" — refs/heads/task/42 must not be split


# --- review round 1, Finding 1: a symlinked root must still be found by realpath ---

def test_worktree_root_through_a_symlink_is_found_by_realpath(repo, monkeypatch):
    """`git worktree list` prints the REALPATH; worktree_root must resolve too, or a
    symlinked VIKUNJA_WORKTREE_ROOT makes a live, registered tree invisible to `_find` —
    breaking BOTH the resume-after-crash path (ensure_workspace re-clobbers a live tree)
    and release (falsely reports 'no worktree', leaking the tree forever)."""
    real = repo.parent / "real-trees"
    real.mkdir()
    link = repo.parent / "link-trees"
    link.symlink_to(real, target_is_directory=True)
    monkeypatch.setenv(ENV_WORKTREE_ROOT, str(link))

    first = ensure_workspace(42, cwd=repo)
    second = ensure_workspace(42, cwd=repo)          # resume path: must find the live tree
    assert second["created"] is False and second["path"] == first["path"]

    res = release_workspace(42, cwd=repo)            # release path: must find it too
    assert res["released"] is True


# --- review round 1, Finding 4: "head" (a sha) vs list_worktrees's "detached" (a bool) ---

def test_review_payload_head_sha_is_distinct_from_list_worktrees_detached_bool(repo):
    """Pin the two meanings so they can never drift back onto one key: ensure_workspace's
    review payload carries the checked-out SHA under 'head'; list_worktrees's 'detached' is
    git's own porcelain BOOL. Reusing one key for both only "worked" because a hex string
    is truthy."""
    head = _git(repo, "rev-parse", "HEAD")
    review = ensure_workspace(42, role="review", at=head, cwd=repo)
    assert review["head"] == head
    assert "detached" not in review

    entries = {wt["path"]: wt for wt in list_worktrees(repo)}
    wt = entries[Path(review["path"])]
    assert wt["detached"] is True
    assert isinstance(wt["detached"], bool)


# --- review round 1, Minor A: a review tree's unique-history guard must not misfire ---

def test_release_of_a_review_tree_reachable_from_a_branch_is_allowed(repo):
    """Case A. A review pinned at a build branch's tip — BY DEFINITION not yet on
    origin/main — must still be releasable: the commit is reachable from task/<id>, so
    nothing is lost. (Round 1 called this "ignores the unpushed guard"; round 2 replaced
    that blanket skip with a reachability check, and this case must keep passing under it —
    the branch-history guard above still does not apply to a detached tree, but the
    reachability check in the `else` branch below does, and task/<id> satisfies it.)"""
    build = ensure_workspace(8, cwd=repo)
    build_path = Path(build["path"])
    (build_path / "wip.txt").write_text("wip\n")
    _git(build_path, "add", "wip.txt")
    _git(build_path, "commit", "-m", "wip")
    tip = _git(build_path, "rev-parse", "HEAD")       # ahead of origin/main, never pushed

    review = ensure_workspace(8, role="review", at=tip, cwd=repo)
    res = release_workspace(8, role="review", cwd=repo)
    assert res["released"] is True
    assert not Path(review["path"]).exists()


def test_release_of_an_ordinary_review_tree_is_allowed(repo):
    """The everyday path, not a corner case: a review tree at origin/main with nothing
    committed inside it must RELEASE. Same code lines as Case A below, but this is the one
    that will actually run thousands of times — worth pinning on its own."""
    review = ensure_workspace(7, role="review", cwd=repo)   # at origin/main, untouched
    res = release_workspace(7, role="review", cwd=repo)
    assert res["released"] is True
    assert not Path(review["path"]).exists()


def test_release_of_a_review_tree_keeps_a_commit_made_inside_it(repo):
    """Case B — THE regression round 1 introduced: a reviewer can commit INSIDE a detached
    review tree (the dirty guard only catches uncommitted changes; a fresh commit makes the
    tree clean again). That commit is reachable from NO ref — `git worktree remove` has no
    unpushed-commit check for a detached HEAD, and a later `gc` would prune the object
    outright once the worktree's reflog is gone with it. Must KEEP, and the object must
    genuinely survive (not just the call returning False)."""
    review = ensure_workspace(7, role="review", cwd=repo)   # at origin/main by default
    path = Path(review["path"])
    (path / "review-notes.md").write_text("looks good, minor nit\n")
    _git(path, "add", "review-notes.md")
    _git(path, "commit", "-m", "review notes")
    sha = _git(path, "rev-parse", "HEAD")

    res = release_workspace(7, role="review", cwd=repo)

    assert res["released"] is False and "reachable from no ref" in res["reason"]
    assert path.exists()
    # NOT `rev-parse` — given a full 40-hex string, `rev-parse` echoes it back with exit 0
    # WITHOUT checking the object actually exists (verified against real git: even after the
    # object is truly gone — worktree remove + `reflog expire --expire-unreachable=now --all`
    # + `gc --prune=now` — `rev-parse <sha>` still prints it back). `cat-file -e` is the one
    # that actually looks the object up; `check=True` in the _git helper makes a missing
    # object raise, so this line can genuinely fail.
    _git(repo, "cat-file", "-e", f"{sha}^{{commit}}")


# --- review round 1, Minor B: an unknown role must be refused, not silently coerced ---

def test_ensure_workspace_rejects_an_unknown_role(repo):
    with pytest.raises(WorkspaceError, match="unknown role"):
        ensure_workspace(42, role="Build", cwd=repo)      # wrong case is NOT "build"


def test_release_workspace_rejects_an_unknown_role(repo):
    with pytest.raises(WorkspaceError, match="unknown role"):
        release_workspace(42, role="bogus", cwd=repo)


# --- review round 1, Finding 3: the CLI entry point + dispatch are a contract, not a demo ---

def test_run_workspace_release_of_missing_tree_is_exit_0(repo, monkeypatch, capsys):
    """A refusal is a NEGATIVE VERDICT, not a CLI failure: the command RAN, exit 0.

    Task 4 review (Minor): "path" now names WHERE a worktree for this task would have been —
    even a "nothing to release" verdict must be actionable, not just a bare task id.

    VMCP-68: and it carries a machine-readable `code` beside the prose `reason`, asserted here by
    WHOLE-DICT equality on purpose — the JSON line is a contract SKILL.md tells agents to branch
    on, so a key silently appearing or vanishing has to fail somewhere."""
    monkeypatch.chdir(repo)
    code = run_workspace(["--release", "999"])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"released": False, "task_id": 999, "role": "build",
                   "path": str(repo.parent / "work.worktrees" / "task-999"),
                   "code": workspace_cmd.CODE_NO_WORKTREE,
                   "reason": "no worktree for this task"}


def test_run_workspace_error_is_one_json_line_exit_1(tmp_path, monkeypatch, capsys):
    """A real failure (here: not even a git repo) is one {"error"} line and exit 1 — never
    silently swallowed, never a bare traceback on a CLI a script parses."""
    monkeypatch.chdir(tmp_path)                        # no git repo at all here
    code = run_workspace(["42"])
    assert code == 1
    lines = [ln for ln in capsys.readouterr().out.splitlines() if ln.strip()]
    assert len(lines) == 1
    err = json.loads(lines[0])
    assert "WorkspaceError" in err["error"]


def test_run_workspace_with_no_args_is_one_json_error_line_exit_1(tmp_path, monkeypatch, capsys):
    # Harmless today (run_workspace raises before any git call), but isolate the cwd anyway —
    # this is one refactor away from touching whatever repo the test happens to run inside.
    monkeypatch.chdir(tmp_path)
    code = run_workspace([])
    assert code == 1
    err = json.loads(capsys.readouterr().out.strip())
    assert "task id" in err["error"]


def test_run_workspace_role_and_at_plumb_through_the_cli(repo, monkeypatch, capsys):
    monkeypatch.chdir(repo)
    head = _git(repo, "rev-parse", "HEAD")
    code = run_workspace(["42", "--role", "review", "--at", head])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out["role"] == "review" and out["head"] == head
    assert Path(out["path"]).name == "review-42"


# --- Task 4: workspace --gc — reap orphaned trees using tracker liveness ---

@pytest.fixture
def tracker():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def _grace_markers(tree: Path) -> list[Path]:
    """The mtimes VMCP-71's grace window reads, derived the way production derives them (git owns
    the `.git/worktrees/<n>` naming, so ask it rather than assemble the path)."""
    index = Path(_git(tree, "rev-parse", "--git-path", "index"))
    return [tree, index if index.is_absolute() else tree / index]


def _quiesce(tree: Path) -> None:
    """Age every marker so a DEAD tree reads as "gone quiet" and is eligible for the reaper NOW.

    VMCP-71 gave `--gc` a grace window: a dead tree touched within `_REAP_GRACE_SECONDS` is
    skipped, silently, so its agent cannot have its cwd removed between `advance(to='review')`
    and `--release`. Every test below that asserts a REAP (or a `kept` line, which is also a
    verdict only reached past the window) works on a tree created milliseconds earlier, so it has
    to say out loud that the tree has gone quiet. Call this AFTER the last git call in the tree —
    a commit or a `git status` rewrites the index and un-quiesces it.
    """
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    for target in _grace_markers(tree):
        if target.exists():
            # MEASURED: a half-created tree (`locked initializing`) has no index FILE at all — the
            # kill lands before git writes one, which is also why `git status` there reports every
            # tracked file as a staged deletion. Production stats each marker independently for
            # exactly this reason; the helper must not assume both exist either.
            os.utime(target, (old, old))
    # Self-check against PRODUCTION's own reader, so that a helper which stops covering a marker
    # fails here, legibly, instead of turning every reap assertion below into a silent skip.
    quiet_for = time.time() - workspace_cmd._last_activity(tree)
    assert quiet_for >= workspace_cmd._REAP_GRACE_SECONDS, (
        f"{tree} still reads as active ({quiet_for:.0f}s) — _grace_markers is missing a marker "
        f"that _last_activity looks at"
    )


def test_gc_reaps_a_tree_whose_task_is_no_longer_active(repo, tracker):
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
    _quiesce(path)
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert [r["task_id"] for r in res["released"]] == [42]
    assert not path.exists()


def test_gc_keeps_a_tree_whose_task_is_still_in_build(repo, tracker):
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_keeps_a_review_tree_while_the_card_is_in_review(repo, tracker):
    api, wf = tracker
    task = api.add_task("under review", "Review")
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_keeps_a_quiesced_review_tree_only_because_its_card_is_in_review(repo, tracker):
    """The BOARD is what keeps a reviewer's tree, not the clock.

    This is the promise SKILL.md makes to a reviewer in plain words — while the card sits in
    Review the sweep will not touch your worktree, however long it has stood without a single
    write — and until now nothing tested it. The sibling directly above does not: its tree is
    created milliseconds before the sweep, so the GRACE WINDOW alone explains the skip and the
    role-keyed liveness carries no weight in that fixture at all. MEASURED three times as
    siblings landed underneath (f891add, 7742d07, 4fe44e4 — 2026-07-30/31), the count moving
    with the suite and the verdict never: make review trees never alive by role (`if task_id in
    (set() if role == "review" else alive[role])`) and the whole PRE-EXISTING unit suite stays
    GREEN — 613 then 628 passed, including all 109 of this file's and 563's source-ORDER pin in
    test_skill_contract.py, which reads the token `alive[role]` and cannot see what it now
    contains — while a 31-minute-quiet review tree with its card in Review is REAPED and its
    directory is gone. With this test present it is the only red in the repository.

    So ROLE has to be the only thing left explaining the outcome. Both trees below are review
    trees, both at the SAME age (quiesced past `_REAP_GRACE_SECONDS`), both clean and pushed,
    both swept in ONE call: the single difference is where their card sits. The control is
    additionally ALIVE as a build task, which pins that liveness is keyed on the ROLE rather
    than on "this id is somewhere on the board" — and that is not a contrived state, it is
    exactly what a `needs_work` verdict leaves behind.

    The second half is that verdict, i.e. what 563 could only state as prose: nothing moves but
    the board, and the next sweep takes the same tree away.
    """
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")

    reviewing = api.add_task("under review", "Queue")          # card IN Review -> tree lives
    wf.claim(reviewing["id"])
    wf.advance(reviewing["id"], to="build", spec="approach")
    wf.advance(reviewing["id"], to="review", worklog="done", evidence="abc1234")

    bounced = api.add_task("already back in build", "Queue")   # card NOT in Review -> tree dies
    wf.claim(bounced["id"])
    wf.advance(bounced["id"], to="build", spec="approach")     # alive as BUILD, never as REVIEW

    live = Path(ensure_workspace(reviewing["id"], role="review", at=head, cwd=repo)["path"])
    dead = Path(ensure_workspace(bounced["id"], role="review", at=head, cwd=repo)["path"])
    _quiesce(live)                                             # both aged identically, and past
    _quiesce(dead)                                             # the window, before ONE sweep

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [bounced["id"]]
    assert not dead.exists()
    assert live.exists()
    assert res["kept"] == [] and res["expected"] == []         # a live tree is skipped silently

    # Second half: the reviewer's own verdict moves the card out of Review and nothing else
    # moves — same tree, same mtimes, same sweep call. First say out loud that the tree really
    # is still quiet, so a future sweep that started WRITING in the trees it keeps (the VMCP-90
    # regression, from the other side) cannot quietly turn the reap below into a grace-window
    # artefact. `_last_activity` reads the index through `_git_inspect`, which takes no optional
    # locks, so asking the question does not itself disturb the answer.
    quiet_for = time.time() - workspace_cmd._last_activity(live)
    assert quiet_for >= workspace_cmd._REAP_GRACE_SECONDS, (
        f"the kept review tree stopped reading as quiet ({quiet_for:.0f}s) — something wrote in "
        f"it during the first sweep, so the reap below would prove the clock, not the board"
    )

    wf.review_task(reviewing["id"], verdict="needs_work", report="repro'd; fix misses the cause")

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [reviewing["id"]]
    assert not live.exists()


def test_gc_never_reaps_unpushed_work_and_reports_it(repo, tracker):
    """The orphan of a crashed agent that got as far as committing: dead on the board, but
    its commits are the whole reason we keep it. GC must REPORT, not destroy."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "crashed mid-task")
    _quiesce(path)                                    # after the commit: it rewrites the index
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "commit" in res["kept"][0]["reason"]
    assert path.exists()


def test_gc_ignores_directories_that_are_not_task_worktrees(repo, tracker):
    api, wf = tracker
    stray = repo.parent / "unrelated"
    stray.mkdir()
    _git(repo, "worktree", "add", str(stray), "-b", "unrelated-branch")
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == [] and res["kept"] == []
    assert stray.exists()


def test_gc_from_inside_a_linked_worktree_still_reaps(repo, tracker):
    """Correction A: `repo_root(cwd)` (via `git rev-parse --show-toplevel`) returns the
    LINKED worktree's own toplevel when invoked from inside one, not the main repo's — the
    normal case once SKILL.md has per-task agents working inside their own tree. If
    gc_workspaces derived `worktree_root` from that unresolved root, every entry would fail
    the "is this one of ours" parent check and --gc would silently reap nothing while still
    reporting success. Run the sweep with cwd INSIDE a live tree and prove a DIFFERENT,
    dead-on-the-board tree still gets reaped."""
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    live_path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    dead_path = Path(ensure_workspace(42, cwd=repo)["path"])      # nothing on the board -> dead
    _quiesce(dead_path)

    res = gc_workspaces(cwd=live_path, workflow=wf)               # invoked FROM the live tree

    assert [r["task_id"] for r in res["released"]] == [42]
    assert not dead_path.exists()
    assert live_path.exists()                                     # the live tree survives too


# --- Task 4 review, round 1: Criticals ---

def test_release_from_inside_its_own_tree_succeeds_and_leaves_no_dangling_branch(repo):
    """Critical 1 repro: an agent's own 'I'm done, release me' call runs with cwd INSIDE the
    tree being released — SKILL.md's normal shape, not a corner case. Before the fix this
    raised a bare FileNotFoundError: `git worktree remove` SUCCEEDS even when its own
    subprocess cwd is the directory being removed (verified against real git), but the very
    next call, `git branch -D ... cwd=root`, needs `root` to still EXIST — and `root` was the
    just-deleted tree. The tree vanished (the real work had actually completed) while the CLI
    reported exit 1 and the branch leaked forever."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    res = release_workspace(42, cwd=path)                # cwd IS the tree being released
    assert res["released"] is True
    assert not path.exists()
    assert "task/42" not in _git(repo, "branch", "--list", "task/42")


def test_gc_from_inside_a_dead_tree_completes_the_whole_sweep(repo, tracker):
    """Critical 2 repro: --gc invoked from inside a worktree whose OWN task has also gone
    dead — an agent calls advance(to='review') and then runs its next --gc tick before it
    gets around to releasing itself, or just never does. Must not remove the tree gc is
    itself standing in (that is the process's shell cwd disappearing underneath it, not
    merely 'a red test'), and must not abort the sweep before reaping the OTHER dead tree.

    VMCP-71: the self tree is left YOUNG on purpose, so this test also pins the guard ORDER — the
    self-guard must run BEFORE the grace window. Flip them and a dead-and-young self tree is
    skipped silently, `kept` comes back empty, and this goes red. That order is the deliberate
    one: this guard KNOWS a process is standing in the tree, the window only suspects it, so the
    report a human can act on must win."""
    api, wf = tracker
    self_path = Path(ensure_workspace(42, cwd=repo)["path"])     # dead, and cwd is INSIDE it
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead, different tree
    _quiesce(other_path)

    res = gc_workspaces(cwd=self_path, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other_path.exists()
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "invoked from inside" in res["kept"][0]["reason"]
    assert self_path.exists()


def test_gc_from_inside_a_live_self_tree_reports_nothing(repo, tracker):
    """Round 2, Minor 1: --gc runs on EVERY tick from inside the agent's own tree — that is
    the mainline, not a corner case — so a healthy self-tree must not show up in `kept` every
    single sweep (a signal that is never empty is a signal nobody reads). The alive check now
    runs BEFORE the self-guard, so a LIVE self-tree is just another live tree: no entry in
    EITHER list. (test_gc_from_inside_a_dead_tree_completes_the_whole_sweep above is the
    complementary case — a DEAD self-tree must still be refused and reported.)"""
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    self_path = Path(ensure_workspace(task["id"], cwd=repo)["path"])   # alive, cwd is INSIDE it

    res = gc_workspaces(cwd=self_path, workflow=wf)

    assert res["released"] == []
    assert res["kept"] == []
    assert self_path.exists()


# --- Task 4 review, round 1: Importants ---

def test_gc_isolates_a_release_failure_and_keeps_sweeping_the_rest(repo, tracker):
    """Important 3: one bad tree must not abort the whole sweep and discard every verdict
    already decided for the OTHERS — reported like any other refusal, as CODE_RELEASE_ERROR.

    THE INJECTOR CHANGED IN VMCP-142, and the reason is the finding itself. This test used to
    lock the tree with `git worktree lock`, because git refusing to remove a locked tree is a
    real, non-contrived WorkspaceError. That state is no longer one: `_release_locked` now
    answers a lock with its own coded verdict, so the lock reaches `git worktree remove` never
    and this test would have pinned a branch nothing can reach. A read-only worktree DIRECTORY
    is the replacement and keeps the property that mattered — real git, real failure, no mock
    standing in for an untested branch: `git status` inside it still succeeds (so the dirty
    guard passes) and `git worktree remove` dies on `failed to delete …: Permission denied`.
    """
    if os.geteuid() == 0:
        pytest.skip("root ignores the directory mode this test uses to make removal fail")
    api, wf = tracker
    doomed_path = Path(ensure_workspace(42, cwd=repo)["path"])   # dead, clean, pushed
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead
    _quiesce(doomed_path)
    _quiesce(other_path)
    doomed_path.chmod(0o500)                                     # git cannot delete its contents
    try:
        res = gc_workspaces(cwd=repo, workflow=wf)
    finally:
        doomed_path.chmod(0o700)                                 # or tmp_path cleanup inherits it

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other_path.exists()
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [
        (42, workspace_cmd.CODE_RELEASE_ERROR)
    ]
    assert "WorkspaceError" in res["kept"][0]["reason"]
    assert doomed_path.exists()


def test_gc_reads_liveness_under_the_lock(repo, tracker):
    """Important 5: the liveness READ must happen INSIDE _repo_lock, not before it, or a task
    claimed (and its tree created — that call takes the SAME lock) between the read and the
    reap races the sweep: the fresh tree is clean and pushed, every guard passes, and it is
    destroyed under a just-dispatched agent. Proven the other way round: a probing Workflow
    tries a NON-BLOCKING second flock on gc's own lock file from inside liveness_board() — if
    the sweep already holds the lock at that point, the probe must fail with
    BlockingIOError (flock is per-open-file-description: even the SAME process contends with
    itself on a second, separately-opened fd)."""
    api, wf = tracker
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"

    class ProbingWorkflow:
        def liveness_board(self):
            with open(lock_path, "w") as fh:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            return wf.liveness_board()

        def active_task_ids(self, board=None):
            return wf.active_task_ids(board=board)

        def review_task_ids(self, board=None):
            return wf.review_task_ids(board=board)

        def parked_task_ids(self, board=None):
            return wf.parked_task_ids(board=board)

    gc_workspaces(cwd=repo, workflow=ProbingWorkflow())


def test_run_workspace_gc_dispatches_to_gc_workspaces(monkeypatch, capsys, tmp_path):
    """Important 6: Task 3 established run_workspace's dispatch as a TESTED contract; --gc
    must not be the one branch that only ever ran by hand.

    Round 2 hygiene: chdir into tmp_path even though gc_workspaces is stubbed here — the house
    negative-pin rule means someone WILL delete that stub one day to prove it bites, and at
    that moment "safe because gc_workspaces never really runs" stops being true. The isolation
    must be structural (an inert cwd), not incidental (a mock that happens to intercept it)."""
    monkeypatch.chdir(tmp_path)
    empty = {"released": [], "kept": [], "expected": []}     # VMCP-68: the real three-list shape
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: empty)
    code = run_workspace(["--gc"])
    assert code == 0
    assert json.loads(capsys.readouterr().out.strip()) == empty


def test_run_workspace_gc_combined_with_a_task_id_is_refused(monkeypatch, capsys, tmp_path):
    """Important 6: argparse alone lets `42 --gc` through and --gc silently wins, ignoring
    the task id the caller plainly meant to act on — that must be an explicit error."""
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above
    calls = []
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: calls.append(1))
    code = run_workspace(["42", "--gc"])
    assert code == 1
    assert not calls
    err = json.loads(capsys.readouterr().out.strip())
    assert "cannot be combined" in err["error"]


def test_run_workspace_gc_combined_with_release_is_refused(monkeypatch, capsys, tmp_path):
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above
    calls = []
    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", lambda: calls.append(1))
    code = run_workspace(["--release", "9", "--gc"])
    assert code == 1
    assert not calls
    err = json.loads(capsys.readouterr().out.strip())
    assert "cannot be combined" in err["error"]


def test_build_workflow_resolves_config_from_the_given_root(repo, monkeypatch):
    """Important 6 / Minor: gc_workspaces's only production path (workflow=None) must resolve
    config FROM the main worktree it was given, not the process's ambient cwd —
    `.vikunja-mcp.env` (the token) lives beside `.vikunja-mcp.toml` in the repo, found by
    config.py's own walk-up from `cwd`; a linked worktree has neither file."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    seen = {}

    def fake_load_config(cwd=None, environ=None):
        seen["cwd"] = cwd
        return config_mod.Config(url="http://example.invalid", token="t", project_id=7)

    monkeypatch.setattr(config_mod, "load_config", fake_load_config)
    wf, _deadline = _build_workflow(repo)          # VMCP-72: (workflow, read deadline)
    assert seen["cwd"] == repo
    assert wf.project_id == 7


# --- Task 4 review, round 1: Minors ---

def test_gc_ignores_a_stray_dir_under_the_root_not_named_like_a_task(repo, tracker):
    """Minor: a directory that lives INSIDE the workspace root (passes the parent check) but
    whose name doesn't match task-<id>/review-<id> must be SKIPPED, not crash the sweep.
    (test_gc_ignores_directories_that_are_not_task_worktrees above places its stray OUTSIDE
    the root, so it never reaches `_parse_workspace_name` at all — this is the sibling case
    that actually exercises the `parsed is None` branch.)"""
    api, wf = tracker
    ensure_workspace(1, cwd=repo)                          # anything, just to create wt_root
    wt_root = worktree_root(repo)
    hotfix = wt_root / "hotfix"
    _git(repo, "worktree", "add", str(hotfix), "-b", "hotfix-branch")

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert hotfix.exists()
    assert not any(k.get("path") == str(hotfix) for k in res["kept"])
    assert not any(r.get("path") == str(hotfix) for r in res["released"])


def test_gc_reaps_a_review_tree_once_the_card_leaves_review(repo, tracker):
    """Minor: the everyday review-side reap — nothing crashed, review just finished and the
    card moved on. test_gc_keeps_a_review_tree_while_the_card_is_in_review above only proves
    the KEEP side; this proves the matching REAP side actually fires."""
    api, wf = tracker
    task = api.add_task("reviewed and done", "Done")        # already past Review
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [task["id"]]
    assert not path.exists()


def test_gc_reaps_a_build_tree_once_its_task_reaches_review(repo, tracker):
    """Minor: the everyday build-side reap — the agent finished and advanced its OWN task to
    Review. The BUILD tree is now dead and must be reaped; it must not be kept just because
    the task still exists somewhere on the board.

    VMCP-71 added the one qualifier: reaped once the tree has gone QUIET. Same board state,
    without the `_quiesce`, is
    test_gc_skips_a_dead_tree_whose_agent_may_still_be_standing_in_it below — the two are the
    same case at two ages, and together they are the whole of the grace window."""
    api, wf = tracker
    task = api.add_task("moved to review", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [task["id"]]
    assert not path.exists()


# --- final whole-branch review, Critical 1: a reused review tree must never be silently stale ---

def _poisoned_review_tree(repo):
    """Build the state that triggers Critical 1 and that this module deliberately preserves: a
    review tree pinned at sha1 that holds a commit made INSIDE it (reviewer's notes), which
    `--release` refuses to remove (unreachable from any ref) and `--gc` cannot reap either — so
    `review-<id>` lives on. Then the author fixes the code and pushes sha2. Returns
    (tree path, the tree's actual HEAD, sha2)."""
    review = ensure_workspace(7, role="review", at=_git(repo, "rev-parse", "HEAD"), cwd=repo)
    path = Path(review["path"])
    (path / "notes.md").write_text("nit: rename this\n")
    _git(path, "add", "notes.md")
    _git(path, "commit", "-m", "review notes")
    pinned = _git(path, "rev-parse", "HEAD")

    (repo / "README.md").write_text("v2 FIXED\n")          # the fix the round-2 reviewer wants
    _git(repo, "add", "README.md")
    _git(repo, "commit", "-m", "fix")
    _git(repo, "push", "origin", "main")
    return path, pinned, _git(repo, "rev-parse", "HEAD")


def test_review_reuse_reports_the_head_it_is_actually_pinned_at(repo):
    """Half one of Critical 1: the reuse payload was missing the "head" key the CREATED payload
    carries, so nothing in the response could ever reveal where the tree really sits. A caller
    that gets what it asked for must still be told, in the same shape, what it got."""
    head = _git(repo, "rev-parse", "HEAD")
    first = ensure_workspace(7, role="review", at=head, cwd=repo)
    again = ensure_workspace(7, role="review", at=head, cwd=repo)     # same sha -> plain reuse
    assert again["created"] is False
    assert again["head"] == first["head"] == head
    assert _git(Path(again["path"]), "rev-parse", "HEAD") == again["head"]   # not a stale echo


def test_review_reuse_at_a_different_sha_is_refused_not_silently_stale(repo):
    """Half two, and THE finding: round 2 of a review asked for the fix's sha, silently got a
    tree still pinned at the PRE-FIX code, and cast a verdict on it. Refuse — and prove the
    refusal did not become the new destruction path: the unreleasable in-tree commit must still
    be there afterwards, object and all."""
    path, pinned, sha2 = _poisoned_review_tree(repo)
    assert pinned != sha2

    with pytest.raises(WorkspaceError, match="pinned at"):
        ensure_workspace(7, role="review", at=sha2, cwd=repo)

    assert _git(path, "rev-parse", "HEAD") == pinned          # HEAD not re-pointed
    _git(repo, "cat-file", "-e", f"{pinned}^{{commit}}")      # the commit object still exists
    assert (path / "notes.md").exists()
    assert (path / "README.md").read_text() == "hi\n"         # still the old tree, but refused


def test_review_reuse_without_at_still_reports_head_and_does_not_refuse(repo):
    """The bound of the refusal: no --at means "wherever it is is fine" (a resume dispatch that
    doesn't restate the sha), so reuse must succeed — while still naming the head."""
    path, pinned, _sha2 = _poisoned_review_tree(repo)
    again = ensure_workspace(7, role="review", cwd=repo)
    assert again["created"] is False and again["head"] == pinned
    assert Path(again["path"]) == path


def test_build_reuse_is_unaffected_by_the_review_refusal(repo):
    """A build tree is reused by BRANCH, and --at is rejected for it at the CLI (Minor 7): the
    new review-only branch must not leak into the build path."""
    ensure_workspace(42, cwd=repo)
    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is False and again["branch"] == "task/42"
    assert "head" not in again                     # review-only key, same as the created payload


# --- final whole-branch review, Important 2: no git call may block forever under the lock ---

def test_git_calls_do_not_inherit_a_blocking_stdin(repo, monkeypatch):
    """`git hash-object --stdin` genuinely READS stdin: with the DEVNULL redirect it sees EOF
    and returns the empty-blob hash instantly; inheriting a never-written pipe (what a terminal
    looks like to a subprocess) it blocks — and every git call here can be holding the repo-wide
    flock while it does. _GIT_TIMEOUT is dropped to 5s so that removing the redirect FAILS this
    test in seconds instead of hanging the suite."""
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", 5.0)
    expected = subprocess.run(["git", "hash-object", "--stdin"], cwd=repo, input="",
                              capture_output=True, text=True, check=True).stdout.strip()
    read_fd, write_fd = os.pipe()                  # nothing is ever written to it
    saved_stdin = os.dup(0)
    try:
        os.dup2(read_fd, 0)
        out = workspace_cmd._git("hash-object", "--stdin", cwd=repo)
    finally:
        os.dup2(saved_stdin, 0)
        for fd in (saved_stdin, read_fd, write_fd):
            os.close(fd)
    assert out == expected


def test_git_runs_with_terminal_prompts_disabled_and_keeps_the_callers_transport(
    repo, tmp_path, monkeypatch
):
    """An https remote with no credential helper prompts on the terminal and waits forever.
    Proven by making git launch a stand-in for ssh that dumps the environment git handed it —
    which also pins the other half: a GIT_SSH_COMMAND the CALLER set must survive untouched (an
    injected BatchMode default would override a configured `core.sshCommand` identity).

    Both variables go through `monkeypatch`, like every other env-touching test in this file.
    Raw `os.environ` assignment with `del` in a `finally` was worse than untidy here: `del`
    DESTROYS an ambient GIT_SSH_COMMAND (this suite runs on developer boxes and on CI runners
    that legitimately set it) instead of restoring it. And GIT_TERMINAL_PROMPT is now set to
    "1" BEFORE the call on purpose: with it merely unset, the assertion below passed
    spuriously on any machine exporting GIT_TERMINAL_PROMPT=0 — the child would report "0"
    whether or not `_run_git` set anything. Seeded with the OPPOSITE value, the assertion pins
    the property it names: `_run_git` OVERRIDES what the caller exported.
    """
    dump = tmp_path / "git-env.txt"
    fake_ssh = tmp_path / "fake-ssh.sh"
    fake_ssh.write_text(f'#!/bin/sh\nenv > "{dump}"\nexit 1\n')
    fake_ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(fake_ssh))
    monkeypatch.setenv("GIT_TERMINAL_PROMPT", "1")     # the ambient value the override must beat
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")
    with pytest.raises(WorkspaceError, match="failed"):
        workspace_cmd._git("fetch", "origin", cwd=repo)

    seen = dict(
        line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line
    )
    assert seen["GIT_TERMINAL_PROMPT"] == "0"          # NOT the "1" this process exported
    assert seen["GIT_SSH_COMMAND"] == str(fake_ssh)


def test_the_fetch_under_the_lock_times_out_instead_of_wedging_it_forever(
    repo, tmp_path, monkeypatch
):
    """`git fetch origin` runs INSIDE _repo_lock, before the idempotency early-return, and is
    the one call that can hang on something off this machine — an ssh host-key question read
    straight off /dev/tty (which neither GIT_TERMINAL_PROMPT nor a DEVNULL stdin can reach), a
    black-holed TCP connection. It must END, as the WorkspaceError the CLI and gc's per-tree
    handler already report. Driven through the REAL entry point, so it also pins that the fetch
    call site takes the tight NETWORK bound and not the 600s local ceiling: a stand-in for ssh
    parks for up to 30s, so a call site on the wrong constant fails this on the elapsed time.
    The stand-in is a `_parking_script` and not a flat `sleep 30` because the SIGKILL that ends
    the fetch does not reach it — see VMCP-76 above."""
    slow_ssh = _parking_script(tmp_path, "slow-ssh.sh")
    monkeypatch.setenv("GIT_SSH_COMMAND", str(slow_ssh))
    monkeypatch.setattr(workspace_cmd, "_GIT_NET_TIMEOUT", 2.0)
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")

    started = time.monotonic()
    with pytest.raises(WorkspaceError, match="fetch origin timed out after 2s"):
        ensure_workspace(42, cwd=repo)
    assert time.monotonic() - started < 15        # not the 30s sleep, not the 600s ceiling


def test_a_local_git_call_keeps_the_generous_ceiling(repo, monkeypatch):
    """The other direction of the two-bound split: killing a `worktree add` mid-checkout is
    destructive (git registers a "locked / initializing" entry BEFORE checking out, which
    `prune` will not drop and `_find` hands back as `created: false`), so local calls must NOT
    inherit the network bound — a big checkout on a slow disk is slow, not hung.

    Asserting the two CONSTANTS proved nothing about that: `worktree add` handed
    `timeout=_GIT_NET_TIMEOUT` tomorrow keeps a constants-only test green while reintroducing
    exactly the destructive kill this bound exists to prevent. So pin it AT THE CALL SITES —
    record the limit each git call actually resolves to (the same
    `_GIT_TIMEOUT if timeout is None else timeout` the subprocess is handed) and check which
    bound each one got. The recorder DELEGATES to the real `_run_git`, so this still drives the
    real create path end to end: it observes, it does not stand in for anything.
    """
    resolved: list[tuple[str, float]] = []
    real_run_git = workspace_cmd._run_git

    # the double mirrors `_run_git`'s signature EXACTLY, `env_extra` included (VMCP-90 added it):
    # a wrapper that drops a parameter the real one grew turns every caller of the new form into a
    # TypeError, which is a loud failure but in the wrong file.
    def recording_run_git(args, cwd, timeout, env_extra=None):
        resolved.append((
            " ".join(args), workspace_cmd._GIT_TIMEOUT if timeout is None else timeout
        ))
        return real_run_git(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", recording_run_git)
    res = ensure_workspace(42, cwd=repo)          # the real create path still works end to end
    assert res["created"] is True and (Path(res["path"]) / "README.md").exists()

    ceiling, network = workspace_cmd._GIT_TIMEOUT, workspace_cmd._GIT_NET_TIMEOUT
    assert ceiling >= 600 and network < ceiling            # the split itself, still worth pinning
    adds = [limit for cmd, limit in resolved if cmd.startswith("worktree add")]
    assert adds and set(adds) == {ceiling}                 # THE call site a kill corrupts
    fetches = [limit for cmd, limit in resolved if cmd.startswith("fetch")]
    assert fetches and set(fetches) == {network}           # the one call off this machine
    # and no OTHER local call site quietly took the network bound either
    assert {limit for cmd, limit in resolved if not cmd.startswith("fetch")} == {ceiling}


# --- final whole-branch review, Important 3: the board read under the lock must be bounded ---

def test_gc_builds_a_tracker_client_that_cannot_hold_the_lock_for_minutes(repo, monkeypatch):
    """gc reads the board INSIDE the repo lock (Important 5 put it there on purpose). With
    api.py's defaults an unreachable tracker costs 30s x 4 attempts + backoff ~= 2 minutes of
    held lock per request, and every agent's `--release` queues behind it. Pin the bound where
    it is set, since no unit test can make a real tracker hang."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    monkeypatch.setattr(config_mod, "load_config", lambda cwd=None, environ=None:
                        config_mod.Config(url="http://example.invalid", token="t", project_id=7))
    wf, _deadline = _build_workflow(repo)                 # VMCP-72: (workflow, read deadline)

    assert wf.api._MAX_RETRIES == 0                       # no backoff sleeps under the lock
    timeout = wf.api._client.timeout
    assert max(timeout.connect, timeout.read, timeout.write, timeout.pool) <= 10


def test_the_default_api_client_is_untouched_by_the_gc_bound():
    """The other direction: the short timeout is for gc ALONE. The MCP server's own client —
    which is not holding any lock and does want the transient retries — must keep the 30s
    default and its 3 retries."""
    from vikunja_mcp.api import VikunjaAPI

    api = VikunjaAPI("https://t.example", "tk")
    assert api._MAX_RETRIES == 3
    assert api._client.timeout.read == 30
    # VMCP-72: and no read budget either — the MCP server's own calls are not under any lock,
    # so a hook that abandoned them past 30s would be a new failure with nothing to gain.
    assert api._client.event_hooks["request"] == []


# --- final whole-branch review, Minor 7: silently ignored argument combinations ---

@pytest.mark.parametrize("argv, needle", [
    (["42", "--release", "9"], "already names the task"),   # acted on 9, dropped 42, exit 0
    (["--release", "9", "--at", "deadbee"], "--at is for creating"),
    (["--gc", "--role", "review"], "sweeps both roles"),
    (["--gc", "--at", "deadbee"], "sweeps both roles"),
    (["42", "--at", "deadbee"], "only to --role review"),   # --help says review-only; it wasn't
])
def test_run_workspace_refuses_silently_ignored_argument_combinations(
    argv, needle, monkeypatch, capsys, tmp_path
):
    """Same class as the `--gc` + task id combination that WAS rejected: argparse accepts all of
    these and one argument is quietly dropped. On a CLI a pump drives unattended, a dropped
    `--at` is how a reviewer ends up reading a revision nobody asked for."""
    monkeypatch.chdir(tmp_path)
    calls = []
    for name in ("gc_workspaces", "release_workspace", "ensure_workspace"):
        monkeypatch.setattr(f"vikunja_mcp.workspace_cmd.{name}",
                            lambda *a, **k: calls.append(a))
    code = run_workspace(argv)
    assert code == 1
    assert not calls                                  # refused BEFORE acting on either argument
    assert needle in json.loads(capsys.readouterr().out.strip())["error"]


def test_run_workspace_still_accepts_the_legitimate_combinations(repo, monkeypatch, capsys):
    """The guards must not overshoot: `--release <id> --role review` (the reviewer's own
    cleanup, where --role is MANDATORY) and a bare `<id> --role review --at <sha>` stay legal."""
    monkeypatch.chdir(repo)
    head = _git(repo, "rev-parse", "HEAD")
    assert run_workspace(["7", "--role", "review", "--at", head]) == 0
    capsys.readouterr()
    assert run_workspace(["--release", "7", "--role", "review"]) == 0
    assert json.loads(capsys.readouterr().out.strip())["released"] is True


def test_argparse_own_errors_still_exit_rather_than_print_json(monkeypatch, tmp_path, capsys):
    """Minor 10: argparse's own failures (`--role bogus`, `--help`) must keep ARGPARSE's
    behaviour — exit, with argparse's own status and argparse's own message — and must never be
    reshaped into this CLI's `{"error": …}` line + exit 1. What allows that is the handler being
    `except Exception`: SystemExit is a BaseException and is not caught. (The
    `except SystemExit: raise` clause that used to sit above it was dead by that same fact. Its
    REMOVAL is unobservable by construction, so no test can pin it — which is why this one pins
    the observable contract instead of claiming to pin the deletion.)

    A bare `pytest.raises(SystemExit)` was too weak to be even that: a clause that prints the
    JSON error line and THEN re-raises satisfies it, and that is precisely the failure this
    test's name warns about. So assert the whole shape — argparse's exit status (2, never this
    CLI's 1), NOTHING on stdout for a script to parse, and argparse's own diagnostic on stderr,
    the last so the test cannot be satisfied by our own code exiting 2 by hand.
    """
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit) as excinfo:
        run_workspace(["42", "--role", "bogus"])
    assert excinfo.value.code == 2                     # argparse's status, not this CLI's exit 1
    out, err = capsys.readouterr()
    assert out == ""                                   # no JSON line: nothing was swallowed
    assert "invalid choice" in err and "bogus" in err  # argparse's own message, not ours


# --- VMCP-66 (514): a killed `worktree add` leaves `locked initializing` — refuse, don't reuse ---

def _half_created_tree(repo, monkeypatch, task_id=42):
    """CONSTRUCT the state, do not simulate it: `git worktree add` killed mid-checkout.

    Driven through the REAL entry point with the module's own timeout, because that is the point
    of the finding — since `_GIT_TIMEOUT` landed, `ensure_workspace` can manufacture this state
    BY ITSELF, with no external killer. A `* filter=slow` smudge filter parks the checkout after
    git has already written `.git/worktrees/task-<id>/locked` = "initializing" and before it has
    written any file, and `subprocess.run(timeout=...)` SIGKILLs it there.

    Measured on git 2.50.1: the entry stays listed as `locked initializing`, `git worktree prune`
    exits 0 and keeps it, and the directory holds nothing but `.git`. Returns its path.

    ONE property to know before touching the assertions below: the half-populated directory is a
    TRANSIENT phase, not the state. `worktree add` checks out in a CHILD (`git reset --hard`), and
    SIGKILLing the parent orphans that child onto PID 1, from where it will finish the checkout
    file by file the moment its filter stops parking — while the lock marker (cleared only by the
    dead parent) stays forever. So the "only .git landed" assertion holds at construction time and
    is checked there; nothing afterwards may depend on a file being absent, and nothing may key off
    file contents to recognise the state. `test_ensure_refuses_any_locked_worktree_...` is the
    complementary case — a FULLY checked-out tree that is merely locked must be refused too, which
    is precisely phase two of this one.

    WHEN it resumes is what VMCP-76 (525) pinned down. It used to be a flat `sleep 30`, so the
    orphan resumed on its own clock, minutes into whatever ran next; now it parks on a hold file
    that `repo`'s teardown removes, so it resumes when THIS test is over and is waited out there.
    Either way nothing moves while the test body runs — the state this hands back is unchanged,
    and the `_quiesce`-LAST ordering below still means what it meant.
    """
    slow_smudge = _parking_script(repo.parent, "slow-smudge.sh", tail="cat\n")
    (repo / ".gitattributes").write_text("* filter=slow\n")
    _git(repo, "add", ".gitattributes")
    _git(repo, "commit", "-m", "slow smudge filter")
    _git(repo, "push", "origin", "main")
    _git(repo, "config", "filter.slow.smudge", str(slow_smudge))

    original = workspace_cmd._GIT_TIMEOUT
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", 2.0)
    with pytest.raises(WorkspaceError, match="worktree add .* timed out"):
        ensure_workspace(task_id, cwd=repo)
    # put both back: everything AFTER this point must run at full speed and un-smudged, so the
    # tests below exercise the guards and not a second timeout.
    monkeypatch.setattr(workspace_cmd, "_GIT_TIMEOUT", original)
    _git(repo, "config", "filter.slow.smudge", "cat")

    path = worktree_root(repo) / f"task-{task_id}"
    # the state itself, asserted where it is built so every test below inherits the guarantee
    assert path.is_dir() and not (path / "README.md").exists()   # partial: only .git landed
    assert "D" in _git(path, "status", "--porcelain")             # index full of staged deletions
    _git(repo, "worktree", "prune")                               # exits 0 and does NOT drop it
    assert "locked initializing" in _git(repo, "worktree", "list", "--porcelain")
    return path


def test_list_worktrees_surfaces_the_lock_it_used_to_drop(repo, monkeypatch):
    """The one line the card called the fix: the porcelain's `locked` key was parsed and thrown
    away, so no caller could tell a usable tree from a half-created one."""
    path = _half_created_tree(repo, monkeypatch)
    entries = {wt["path"]: wt for wt in list_worktrees(repo)}

    assert entries[path]["locked"] is True
    assert entries[path]["lock_reason"] == "initializing"
    assert entries[repo]["locked"] is False and entries[repo]["lock_reason"] is None


def test_list_worktrees_reports_a_reasonless_lock_as_locked_with_no_reason(repo):
    """The other porcelain shape: `git worktree lock` with no reason emits a BARE `locked` line,
    so the reason must come back None while `locked` still says True. Gate on the bool."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _git(repo, "worktree", "lock", str(path))
    wt = {w["path"]: w for w in list_worktrees(repo)}[path]
    assert wt["locked"] is True and wt["lock_reason"] is None


def test_ensure_refuses_a_half_created_worktree_instead_of_reusing_it(repo, monkeypatch):
    """THE finding. `_find` returns the entry and the idempotency early-return handed it back as
    `created: false` — dispatching an agent into a directory whose tracked files are missing and
    whose index is all staged deletions. Refuse; and prove the refusal did not become a new
    destruction path (the partial tree is the only trace of what killed the add)."""
    path = _half_created_tree(repo, monkeypatch)

    with pytest.raises(WorkspaceError, match="HALF-CREATED"):
        ensure_workspace(42, cwd=repo)
    # the recovery a human actually needs, in the message itself
    with pytest.raises(WorkspaceError, match=r"worktree unlock .*&& git worktree remove -f -f"):
        ensure_workspace(42, cwd=repo)

    assert path.is_dir()                                          # NOT silently removed
    assert "locked initializing" in _git(repo, "worktree", "list", "--porcelain")


def test_ensure_refuses_any_locked_worktree_not_only_the_initializing_marker(repo):
    """The BREADTH of the guard, pinned on its own: it gates on the `locked` BOOL, never on git's
    marker text. A tree we cannot vouch for must not be handed to an agent even when the lock says
    something else entirely — and a locked tree is one git will not let `--release`/`--gc` remove,
    so working in it would leave a tree nothing can reap. Narrow the guard to
    `lock_reason == "initializing"` and this test goes red."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _git(repo, "worktree", "lock", "--reason", "human is inspecting this", str(path))

    with pytest.raises(WorkspaceError, match="LOCKED worktree .human is inspecting this."):
        ensure_workspace(42, cwd=repo)
    assert path.is_dir()


def test_release_names_a_half_created_tree_instead_of_calling_it_dirty(repo, monkeypatch):
    """The reported symptom: release/gc keep it forever "(dirty)" and report that every tick. The
    OUTCOME was already right (keep, never destroy) — the DIAGNOSIS was not: `git status` inside
    the tree reports the staged deletion of every missing file, so a human was sent looking for
    uncommitted work that does not exist."""
    path = _half_created_tree(repo, monkeypatch)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert "half-created" in res["reason"] and "killed `worktree add`" in res["reason"]
    assert "dirty" not in res["reason"]
    assert "remove -f -f" in res["reason"]                        # the human's actual next step
    assert path.is_dir()


def test_gc_reports_a_half_created_tree_and_keeps_sweeping(repo, tracker, monkeypatch):
    """End to end through the unattended path, which is where this state is actually met: --gc
    runs every tick, so the half-created tree must produce ONE actionable `kept` line and must not
    cost the sweep its other verdicts."""
    api, wf = tracker
    half = _half_created_tree(repo, monkeypatch)
    other = Path(ensure_workspace(43, cwd=repo)["path"])          # dead, clean, pushed
    _quiesce(half)          # the checkout is PARKED, not finished — quiesce LAST, then sweep
    _quiesce(other)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other.exists()
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "half-created" in res["kept"][0]["reason"]
    assert half.is_dir()


# --- VMCP-76 (525): nothing this file parks may outlive the test that parked it ---

def test_a_parked_stand_in_does_not_outlive_the_test_that_parked_it(
    repo, monkeypatch, tmp_path, request
):
    """The leak itself, pinned by RUNNING the teardown against the real processes it must reap.

    FOUR separate things have to hold, and each is asserted against a runtime fact rather than
    against the source:

    1. `repo` pulls the reaper into the fixture closure — `request.fixturenames` is pytest's own
       resolved list, so dropping the dependency from `repo` fails here and not silently in
       whichever other test happens to park something next.
    2. The state really did leave processes running. Without this the test would pass just as
       happily against a `_parking_script` that quietly exits at once, i.e. it would stop pinning
       anything at all.
    3. What is waited on includes the orphaned CHECKOUT and not just the stand-in git spawned.
       This is the one the other three cannot cover: drop the parent derivation and every stand-in
       still gets reaped, but the reaper is free to return during the window between the last one
       exiting and git finishing — a race, so a test that only counted processes afterwards would
       pass most of the time.
    4. Driving `_reaping_parked_children` — the generator `repo` yields from, not a copy of it —
       runs the exact teardown pytest runs, and after it nothing is alive. The final check is the
       UNION of the set from step 2 and a fresh read, because neither alone covers it: releasing
       the hold lets the orphaned checkout resume and spawn one more stand-in that only a fresh
       read knows about, while the checkout itself is only visible in the earlier set (a parent is
       derived from a LIVE child, and by then its child has exited).

    Why the teardown and not a `finally` at the end of `_half_created_tree`: a test that FAILS
    mid-body never reaches the helper's tail, and a leak that depends on the assertions passing is
    the leak. Verified by forcing a failure — an `assert False` planted right after the helper in
    `test_ensure_refuses_a_half_created_worktree_instead_of_reusing_it`: the run went red and `ps`
    immediately after showed nothing of it left, neither stand-in nor checkout.

    MUTATION-CHECKED with a non-mutated CONTROL round first and another last. `__pycache__` was
    cleared between rounds, and every round selected exactly two tests — this one plus
    `test_the_reaper_never_waits_on_the_test_runner_itself`, which stays green in all of them
    except its own. Control 0 failed (2 passed); drop `_reap_parked_children(tmp_path)` from
    `_reaping_parked_children` -> 1 failed here, on step 4; drop `_parked_children_reaped` from
    `repo`'s parameters -> 1 failed here, on step 1; make `_reap_parked_children` skip the
    `unlink` -> 1 failed here on the 30s deadline AND 1 error, because the fixture's own teardown
    then hits the same deadline a second time (63s for that round, so it is not the `1 failed`
    shape the others are); make `_parked_pids` return only what the scripts recorded, deriving no
    live parent -> 1 failed here, on step 3; drop `_live_parent`'s own-pid guard -> 1 failed, in
    the OTHER test and not this one; closing control 0 failed.
    """
    assert "_parked_children_reaped" in request.fixturenames, (
        "`repo` stopped requesting the reaper, so nothing reaps the stand-ins of a test that "
        "never mentions it — which is every test in this file"
    )

    _half_created_tree(repo, monkeypatch)

    parked = {pid: _process_command(pid) for pid in _parked_pids(tmp_path)}
    assert parked, "the stand-in recorded no pid, so the reaper has nothing to wait on"
    assert [pid for pid in parked if _pid_alive(pid)], (
        "nothing was left running at all — this test cannot show a reap that had no work to do"
    )
    assert any("reset --hard" in command for command in parked.values()), (
        f"the watched set is {parked} — it does not include the orphaned CHECKOUT, only the "
        f"stand-in that git spawned. Waiting on the checkout is what keeps the reaper from "
        f"returning in the window between the last stand-in exiting and git finishing"
    )

    teardown = _reaping_parked_children(tmp_path)     # what `repo` runs after a test, pass OR fail
    next(teardown)
    with pytest.raises(StopIteration):
        next(teardown)

    assert not [pid for pid in set(parked) | _parked_pids(tmp_path) if _pid_alive(pid)]


def test_the_reaper_never_waits_on_the_test_runner_itself(tmp_path):
    """`_live_parent`'s own-pid guard, which the test above cannot reach: both stand-ins there are
    spawned BY GIT, so the parent it derives is a git process every time.

    A stand-in launched straight from a test body has pytest for a parent, and without the guard
    the reaper waits for the test runner to exit — i.e. burns the full 30s deadline and then fails
    naming pytest's own pid. Measured as exactly that before the guard existed (30.0s, two pids
    reported as outliving the test, one of them the runner). The child here is killed through the
    `Popen` handle we own, never by pid, and reaped in a `finally`.

    MUTATION-CHECKED in the same series as the test above, same two-test selection: control
    0 failed; drop `int(ppid) == os.getpid()` from `_live_parent` -> 1 failed, and here rather
    than in the other test, which stays green because git is what spawns its stand-ins.
    """
    proc = subprocess.Popen(["sleep", "30"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        assert _ps_field(proc.pid, "ppid").strip() == str(os.getpid()), (
            "this test is not exercising what it claims — the child's parent is not this process"
        )
        assert _live_parent(proc.pid) == set()
    finally:
        proc.kill()
        proc.wait()


# --- VMCP-110 (580): the two refusal CHANNELS are different shapes, and both ends need a net ---

def test_the_two_refusal_channels_are_not_interchangeable(repo, monkeypatch, capsys):
    """`workspace` refuses in two deliberately DIFFERENT shapes, and this pins both ends at once.

    CREATE refuses by RAISING; `run_workspace`'s catch-all renders that as one `{"error": …}` line
    and exit 1 — with NO `code` key. `--release`/`--gc` refuse by RETURNING: exit 0,
    `released: false`, and a machine-readable `code` beside the prose `reason`. Three documents had
    copied that second half out as the universal "every refusal carries a machine-readable `code`",
    which is simply false of the create half; 580 weighed making it uniform and re-ratified the
    split instead (see the CODE_* header in workspace_cmd.py for why a create-side code would have
    no consumer and could only ever be present-SOMETIMES). A re-ratified split needs a net in BOTH
    directions, so ONE state is driven through BOTH entry points here — same tree, same tick.

    The create half is asserted as a WHOLE KEY SET on purpose. The existing create test
    (`test_run_workspace_error_is_one_json_line_exit_1`) only checks `"WorkspaceError" in
    err["error"]`, with no whole-dict equality anywhere — so a `code` key appearing beside it would
    leave every existing test in this file green, which is exactly how someone could quietly make
    the OLD universal claim true and nobody would learn of it. `"code" not in payload` would not do
    either: it names the one key we happen to fear today, and the release channel is pinned by whole
    dict (`test_run_workspace_release_of_missing_tree_is_exit_0`) for the same reason.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, workspace_cmd.py restored from a COPY — never `git checkout --`, since this card's edits
    are uncommitted and siblings are live in neighbouring worktrees): control PASS; give
    `run_workspace`'s catch-all a `"code"` beside its `"error"` (the plausible "tidy-up" that makes
    the OLD universal claim true) -> FAIL on the create key set; delete `"code": CODE_HALF_CREATED`
    from `_release_locked`'s half-created refusal -> FAIL on the release half.

    And the size of the gap was measured, not assumed: under that first mutation the ENTIRE
    pre-existing suite — this file's own create test included — stays GREEN. Someone
    could have made the false universal true and no test in this repo would have said a word.
    """
    path = _half_created_tree(repo, monkeypatch)          # ONE state both channels can see
    monkeypatch.chdir(repo)

    assert run_workspace(["42"]) == 1, "a create refusal must be a CLI failure — exit 1"
    created = json.loads(capsys.readouterr().out.strip())
    assert set(created) == {"error"}, (
        f"the CREATE channel grew keys {sorted(set(created) - {'error'})}. If a `code` was added "
        f"here on purpose, that is the split changing: update the CODE_* header in "
        f"workspace_cmd.py and CLAUDE.md's workspace bullet with it, and say what consumer grades "
        f"it — do not just widen this assertion"
    )
    assert "WorkspaceError" in created["error"] and "HALF-CREATED" in created["error"]

    assert run_workspace(["--release", "42"]) == 0, (
        "a --release refusal is a NEGATIVE VERDICT, not a CLI failure: the command RAN"
    )
    released = json.loads(capsys.readouterr().out.strip())
    assert released["released"] is False
    # .get, not [] — an ABSENT code is the likelier regression of the two, and a bare KeyError
    # would swallow the message that says what to do about it
    assert released.get("code") == workspace_cmd.CODE_HALF_CREATED, (
        f"the --release channel came back with code {released.get('code')!r} for a state it "
        f"refuses as half-created. `--gc`'s _keep_is_expected grades on this key: an absent or "
        f"unknown code lands in `kept`, i.e. it tells a human to go and look at a tree the tool "
        f"already understands"
    )
    assert set(released) == {"released", "task_id", "role", "path", "code", "reason"}, (
        f"the RELEASE channel's key set moved to {sorted(released)}; SKILL.md tells agents to "
        f"branch on this JSON line, so a key appearing or vanishing has to fail somewhere"
    )
    assert path.is_dir()                                  # neither channel destroyed the evidence


def test_no_create_path_refusal_carries_a_code(repo, monkeypatch, capsys):
    """The BREADTH of the create half, swept cheaply. The claim above was wrong in ONE direction
    only, so pinning a single create refusal would leave every OTHER one free to grow a `code` and
    re-open the drift — and "measured over every one of them" is what CLAUDE.md now says out loud.

    `_half_created_tree` is deliberately not repeated here: it costs a real ~2 s `worktree add`
    timeout and the test above already drives that state through both channels. Everything below
    reuses a fixture this file already builds for another reason, or costs nothing at all.

    MUTATION-CHECKED alongside the test above: give `run_workspace`'s catch-all a `"code"` -> FAIL,
    naming the first refusal that grew one ("the detached build tree refusal came back as
    ['code', 'error']").
    """
    monkeypatch.chdir(repo)
    _interrupted_rebase_build_tree(repo)                       # 42: detached BUILD tree (VMCP-86)
    _path, _pinned, sha2 = _poisoned_review_tree(repo)         # 7: review tree pinned at sha1
    squatter = worktree_root(repo) / "task-99"                 # 99: occupied, not a worktree
    squatter.mkdir(parents=True, exist_ok=True)
    (squatter / "precious.txt").write_text("do not clobber\n")

    refusals = {
        "detached build tree": ["42"],
        "review tree pinned at another sha": ["7", "--role", "review", "--at", sha2],
        "occupied path": ["99"],
        "task id beside --release": ["42", "--release", "9"],
        "--at without --role review": ["42", "--at", sha2],
    }
    for what, argv in refusals.items():
        assert run_workspace(argv) == 1, f"the {what} refusal stopped being exit 1"
        payload = json.loads(capsys.readouterr().out.strip())  # readouterr DRAINS: once per call
        assert set(payload) == {"error"}, (
            f"the {what} refusal came back as {sorted(payload)} — the create channel is "
            f"`{{\"error\"}}` and exit 1, with the exit code as the whole machine-readable "
            f"verdict. See test_the_two_refusal_channels_are_not_interchangeable"
        )


# --- final whole-branch review, Minor 9: a broken config must surface, not relocate trees ---

def test_a_malformed_repo_toml_is_not_swallowed_into_the_default_root(repo):
    """`except Exception` around load_config treated "this toml is broken" exactly like "there
    is no tracker config here" — and silently put the tree in the default sibling directory,
    where a `worktree_root` the human meant to configure would never be looked for again.

    Pinned by TYPE, not by a message substring: `pytest.raises(Exception, match="[Ee]xpected")`
    accepts ANY exception whose text happens to contain "expected" — an unrelated AssertionError
    or a git failure inside `worktree_root` would have read as this finding being fixed. And the
    value the swallow used to return is spelled out first, so the test names the outcome it
    excludes ("into the default root") instead of only "something raised".
    """
    swallowed_default = repo.parent / "work.worktrees"
    assert worktree_root(repo) == swallowed_default            # where a swallow would put it
    (repo / ".vikunja-mcp.toml").write_text("[tracker\nurl = 'oops'\n")
    with pytest.raises(tomllib.TOMLDecodeError):
        worktree_root(repo)


def test_a_repo_with_no_tracker_config_still_falls_back_silently(repo):
    """The other direction, and the reason the try/except exists at all: create and release need
    no tracker config whatsoever, so ConfigError alone must stay swallowed."""
    assert worktree_root(repo) == repo.parent / "work.worktrees"


# --- VMCP-71 (519): a grace window, so a sweep cannot pull a tree out from under its own agent ---

def _advanced_to_review(repo, api, wf):
    """The exact race state: a claimed task whose agent has just called `advance(to='review')`.
    Its build tree is now DEAD by liveness (alive = Design/Build assigned to me) and is clean and
    fully pushed, so every release guard passes — the tree IS removable, and the only thing that
    should stop the reaper is how recently the agent touched it. Returns (task_id, path)."""
    task = api.add_task("just advanced to review", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")
    return task["id"], path


def test_gc_skips_a_dead_tree_whose_agent_may_still_be_standing_in_it(repo, tracker):
    """THE race, mechanically closed. `--gc` runs at tick start from the MAIN checkout, so the
    self-guard cannot help, and `git push origin HEAD:main` moved the local `origin/main`, so the
    unpushed guard cannot either: before the grace window this tree was removed WITH its branch
    while its agent stood in it, on its way from `advance(to='review')` to `--release`.

    Asserted absent from BOTH lists, not merely surviving: `kept` means "a human should look", and
    a tree that is only YOUNG is not that. And swept alongside a quiet dead sibling that IS
    reaped, because the skip must be per-tree — deferring one tree may not cost the sweep its
    other verdicts. (test_gc_reaps_a_build_tree_once_its_task_reaches_review is this same tree
    once it goes quiet.)"""
    api, wf = tracker
    task_id, young = _advanced_to_review(repo, api, wf)      # touched milliseconds ago
    quiet = Path(ensure_workspace(44, cwd=repo)["path"])     # dead too, but long since idle
    _quiesce(quiet)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [44] and not quiet.exists()
    assert res["kept"] == []                                     # young is NOT "look at this"
    assert young.is_dir() and (young / "README.md").exists()
    assert _git(repo, "branch", "--list", f"task/{task_id}")     # the branch survives too


def test_gc_grace_window_sees_a_commit_in_an_old_tree_through_the_index(repo, tracker):
    """The realistic shape of the race, and the reason the INDEX is one of the two markers: a
    tree the agent has worked in for an hour has a stale DIRECTORY mtime (nothing is created at
    its top level while files are merely edited), and the only fresh footprint at the moment the
    task leaves Build is the commit it left in the index.

    Constructed, not simulated: commit inside the tree and push it, so the tree stays clean and
    fully pushed (i.e. genuinely reapable — a skip is distinguishable from a guard's keep), then
    age ONLY the directory. Drop the index from `_last_activity` and this goes red."""
    api, wf = tracker
    _task_id, path = _advanced_to_review(repo, api, wf)
    (path / "feature.txt").write_text("the work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "the task's one commit")
    _git(path, "push", "origin", "HEAD:main")                    # local origin/main moves with it
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(path, (old, old))                                   # an hour-old working directory

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [] and res["kept"] == []
    assert path.is_dir() and (path / "feature.txt").exists()


def test_gc_grace_window_sees_top_level_churn_through_the_directory(repo, tracker):
    """The other marker, pinned on its own: an agent whose last GIT call is old but that is
    demonstrably still working — a verification run dropping an ignored artifact at the tree root
    (`.pytest_cache` and friends) bumps the directory while touching no index.

    Kept genuinely clean via the common `info/exclude`, so `git status --porcelain` stays empty
    and the tree really is reapable. Drop the worktree directory from `_last_activity` and this
    goes red — that half is also the only signal left when the index cannot be resolved at all."""
    api, wf = tracker
    _task_id, path = _advanced_to_review(repo, api, wf)
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    (common / "info").mkdir(exist_ok=True)
    (common / "info" / "exclude").write_text(".pytest_cache/\n")
    tree_dir, index = _grace_markers(path)
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(index, (old, old))
    (path / ".pytest_cache").mkdir()                    # ignored: the tree stays CLEAN...
    assert _git(path, "status", "--porcelain") == ""
    os.utime(index, (old, old))                         # ...but that status just rewrote the index
    assert tree_dir.stat().st_mtime > index.stat().st_mtime      # the state this test is about

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [] and res["kept"] == []
    assert path.is_dir()


def test_gc_does_not_skip_forever_on_an_mtime_in_the_future(repo, tracker):
    """The window is bounded BELOW as well as above. A timestamp in the future — clock skew, a
    restored backup, an unpacked archive — would otherwise read as "younger than N" on every
    sweep for as long as it lasts, and this skip is SILENT: the one combination that leaks a tree
    with nothing anywhere to notice. Anything outside the window falls through to the ordinary
    release guards, which still refuse to destroy work. Drop the `0 <=` and this goes red."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])           # dead: nothing on the board
    ahead = time.time() + 86_400
    for marker in _grace_markers(path):
        os.utime(marker, (ahead, ahead))

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42]
    assert not path.exists()


def test_last_activity_still_reports_a_future_mtime_when_every_marker_is_future(repo):
    """The all-future case belongs to the CALLER's `0 <=` bound, and must keep belonging to it.

    `_last_activity` drops future markers so one bad clock reading cannot suppress a good one
    (VMCP-84) — but when there is no good one left it reports the future value anyway rather than
    `None`. Both make the sweep fall through, so behaviour is identical today; the difference is
    that `None` would make the `0 <=` bound above unreachable, i.e. deletable without a single
    test going red, and the test that pins it (`..._does_not_skip_forever_...`) would then be
    pinning nothing. Keep the signal, keep the bound that reads it."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    ahead = time.time() + 86_400
    for marker in _grace_markers(path):
        os.utime(marker, (ahead, ahead))

    assert workspace_cmd._last_activity(path) > time.time()


def test_gc_grace_window_is_not_defeated_by_a_future_mtime_on_the_sibling_marker(repo, tracker):
    """VMCP-84, the defect this fixes: the fall-through above used to be decided on the `max()`
    OVER BOTH markers, so a single future mtime MASKED a genuinely fresh one and the tree was
    reaped with its agent still standing in it — the very race VMCP-71 exists to close, reopened
    by a clock reading that says nothing about whether anyone is working here.

    Both orientations, because the two markers move for different reasons and either can be the
    skewed one: task 42 = future DIRECTORY (a restored/copied tree) with an index the agent just
    wrote; task 43 = future INDEX (skew on whatever wrote it) with a directory touched moments
    ago. Swept alongside a quiet dead sibling that IS reaped, so a fix that simply stops reaping
    cannot pass. Revert `_last_activity` to a max over ALL markers and both trees are destroyed."""
    api, wf = tracker
    skewed_dir = Path(ensure_workspace(42, cwd=repo)["path"])     # dead: nothing on the board
    skewed_index = Path(ensure_workspace(43, cwd=repo)["path"])
    quiet = Path(ensure_workspace(44, cwd=repo)["path"])
    _quiesce(quiet)
    ahead = time.time() + 86_400
    os.utime(_grace_markers(skewed_dir)[0], (ahead, ahead))       # ...index stays fresh
    os.utime(_grace_markers(skewed_index)[1], (ahead, ahead))     # ...directory stays fresh

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [44] and not quiet.exists()
    assert res["kept"] == []                       # young is not "a human should look at this"
    assert skewed_dir.is_dir() and skewed_index.is_dir()
# --- VMCP-68 (516): `--gc` grades its refusals, so `kept` is only what a human should look at ---
#
# Every test here `_quiesce`s its tree, and that is the ORDER these two changes compose in: VMCP-71
# skips a young dead tree before any guard runs, so it produces no refusal to grade and lands in
# NEITHER list. `expected` is for a refusal that WAS made and is routine — never for a tree gc
# declined to inspect. Skip the quiesce and these tests go red on empty lists, loudly.

def _unpushed_build_tree(repo, task_id):
    """A dead build tree every release guard rightly refuses to remove: it holds a commit that is
    not on origin/main. This is what an agent leaves behind when its push was rejected or its
    rebase went sideways — and NOTHING about the tree itself says whether that is routine or
    alarming. Only the board does, which is the whole point of the grading."""
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "work in progress")
    _quiesce(path)                       # after the commit: it rewrites the index
    return path


def _parked(api, wf, title="waiting on a human"):
    """A card in Your Call with its assignee kept — what `call_human` leaves behind."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    wf.call_human(task["id"], "the rebase conflicted — which side wins?")
    return task


def test_gc_reports_a_parked_cards_unpushed_commit_as_expected_not_as_kept(repo, tracker):
    """THE case that made `kept` never-empty: an agent hits a conflict, calls `call_human`, and its
    card sits in Your Call for HOURS. The tree is dead by liveness the moment the card leaves
    Build, and its unpushed commit is exactly what the guard must refuse to destroy — so the sweep
    reported it on every single tick, and a signal that is never empty stops being read.

    Still reported (nothing hidden) and still not removed (`released: false`) — just not in the
    list SKILL.md tells a human to read."""
    api, wf = tracker
    task = _parked(api, wf)
    path = _unpushed_build_tree(repo, task["id"])

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []                                  # nothing for a human to look at
    assert [e["task_id"] for e in res["expected"]] == [task["id"]]
    assert res["expected"][0]["code"] == workspace_cmd.CODE_UNPUSHED
    assert res["expected"][0]["released"] is False             # reported, NOT removed
    assert path.exists()


def test_gc_still_shouts_about_an_unpushed_commit_when_the_card_is_not_parked(repo, tracker):
    """The mirror image, and why the grading needs the BOARD and not just the guard's identity:
    the very same refusal on a card nobody parked is work no agent is coming back for. Here the
    task was returned to Backlog (`return_task`) with its commits still in the tree."""
    api, wf = tracker
    task = api.add_task("abandoned mid-flight", "Queue")
    wf.claim(task["id"])
    path = _unpushed_build_tree(repo, task["id"])
    wf.return_task(task["id"], "the upstream service is down")     # -> Backlog, NOT parked

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []
    assert [k["task_id"] for k in res["kept"]] == [task["id"]]
    assert res["kept"][0]["code"] == workspace_cmd.CODE_UNPUSHED
    assert path.exists()


def test_gc_reports_a_parked_cards_dirty_tree_as_expected_too(repo, tracker):
    """The dirty half of the same state, which SKILL.md names explicitly: `call_human` is what an
    agent calls WHEN a rebase conflicts, and a conflicted rebase leaves the tree dirty rather than
    merely unpushed. Grading only `unpushed` would have left the noisier of the two shouting."""
    api, wf = tracker
    task = _parked(api, wf)
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    (path / "README.md").write_text("<<<<<<< HEAD\nhalf-resolved conflict\n")
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["code"] for e in res["expected"]] == [workspace_cmd.CODE_DIRTY]
    assert path.exists()


def test_gc_reports_a_review_trees_in_tree_commit_as_expected_forever(repo, tracker):
    """The other routine state, and the permanent one: a reviewer committed notes INSIDE its
    detached tree, so the reachability guard refuses to release it and `--gc` cannot reap it —
    there is no board state that ever clears this, which is exactly why it must not sit in the
    list a human is told to read. Expected regardless of any parked card (its card is in Done
    here); SKILL.md's fix is the reviewer's rule, not a chore for the human.

    The `role` assertion is half of the round-2 pin: this refusal is routine BECAUSE it is a
    review tree, so the test that proves it must say which role it observed — its twin below
    holds the same code to the opposite verdict in a build tree."""
    api, wf = tracker
    api.add_task("reviewed and done", "Done")             # task 7's card has LEFT Review -> dead
    path, pinned, _sha2 = _poisoned_review_tree(repo)     # review-7, holds an in-tree commit
    _quiesce(path)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["task_id"] for e in res["expected"]] == [7]
    assert res["expected"][0]["code"] == workspace_cmd.CODE_UNREACHABLE_HEAD
    assert res["expected"][0]["role"] == "review"
    assert _git(path, "rev-parse", "HEAD") == pinned       # and the commit is still there


def _interrupted_rebase_build_tree(repo, task_id=42):
    """CONSTRUCT the state, do not simulate it: a BUILD tree left mid-`git rebase origin/main`.

    Straight out of this project's own integration recipe — every per-task agent runs
    `git fetch origin && git rebase origin/main` before pushing, and a turn killed inside that
    (session limit, API error) leaves exactly this. `--exec false` is only the KILLER here; what
    the state IS gets asserted, not assumed: clean working tree, detached HEAD, and the replayed
    commit reachable from no ref (`task/<id>` still points at the PRE-rebase commit, which is
    also why the work itself is not at risk). Returns the tree path.
    """
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "work in progress")
    before = _git(path, "rev-parse", "HEAD")

    (repo / "sibling.txt").write_text("a sibling landed while we worked\n")
    _git(repo, "add", "sibling.txt")
    _git(repo, "commit", "-m", "sibling")
    _git(repo, "push", "origin", "main")

    _git(path, "fetch", "origin")
    rebase = subprocess.run(["git", "rebase", "origin/main", "--exec", "false"],
                            cwd=path, capture_output=True, text=True)
    assert rebase.returncode != 0, "the rebase was meant to be INTERRUPTED, not to complete"

    head = _git(path, "rev-parse", "HEAD")
    assert _git(path, "status", "--porcelain") == "", "an interrupted rebase leaves a CLEAN tree"
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert head != before, "nothing was replayed — the fixture stopped before it did any work"
    assert _git(repo, "for-each-ref", "--contains", head, "--format=%(refname)") == ""
    _quiesce(path)                                        # after the rebase: it rewrites the index
    return path


def _detached_build_tree_whose_head_is_reachable(repo, task_id=43):
    """THE HOLE VMCP-86 MEASURED, constructed: an interrupted rebase whose HEAD is on the ONTO
    commit, i.e. reachable from origin/main.

    Not an exotic variant — it is where `git rebase` spends its FIRST moment, since it detaches to
    `onto` before replaying anything, so a turn killed at the start lands exactly here. Reproduced
    deterministically as the other everyday route to the same state: the first commit conflicts,
    the agent resolves it in the sibling's favour (so the resolution stages nothing, leaving the
    tree CLEAN), and the turn dies before `git rebase --continue`.

    What makes it the hole is the combination: the tree is detached, so `_release_locked`'s
    `origin/main..HEAD` guard is skipped for the branch; and HEAD is reachable, so the guard that
    replaces it passes. `task/<id>` and its unpushed commit are checked by NEITHER.
    """
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "contested.txt").write_text("mine\n")
    _git(path, "add", "contested.txt")
    _git(path, "commit", "-m", "the task's work, never pushed")

    (repo / "contested.txt").write_text("theirs\n")
    _git(repo, "add", "contested.txt")
    _git(repo, "commit", "-m", "sibling touched the same file")
    _git(repo, "push", "origin", "main")

    _git(path, "fetch", "origin")
    rebase = subprocess.run(["git", "rebase", "origin/main"], cwd=path,
                            capture_output=True, text=True)
    assert rebase.returncode != 0, "the rebase was meant to STOP on the conflict"
    _git(path, "checkout", "--ours", "contested.txt")      # resolve in the sibling's favour…
    _git(path, "add", "contested.txt")                     # …then the turn dies, no --continue

    head = _git(path, "rev-parse", "HEAD")
    assert _git(path, "status", "--porcelain") == "", "the resolved-to-onto tree must read CLEAN"
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert head == _git(repo, "rev-parse", "origin/main"), "HEAD must sit on the ONTO commit"
    assert _git(repo, "log", "--oneline", f"origin/main..task/{task_id}") != "", \
        "the branch must still hold work that is NOT on origin/main — that is the point"
    _quiesce(path)
    return path


def test_ensure_refuses_a_build_tree_an_interrupted_rebase_left_detached(repo):
    """VMCP-86, THE bug: `ensure_workspace` found the directory, returned `created: false`, and the
    resume agent was dropped into a half-finished rebase on a detached HEAD while SKILL.md told it
    it was standing on `task/<id>`. Its `git push origin HEAD:main` would push the replayed commit.

    The information was never missing — `list_worktrees` reports `branch: None` — so the fix is
    that ensure ACTS on it, in the module's established shape for a state it cannot safely reason
    about: refuse loudly and name the recovery (514's `locked initializing` refusal). The recovery
    commands are asserted verbatim because they ARE the payload of the refusal; an error that only
    says "detached" leaves the agent exactly as stuck as the silent hand-back did.

    And it must be a pure refusal: nothing recovered on the agent's behalf (`--abort` would discard
    the replayed commit), so HEAD, the branch and the rebase state are all still there afterwards.
    """
    path = _interrupted_rebase_build_tree(repo)
    head_before = _git(path, "rev-parse", "HEAD")

    with pytest.raises(WorkspaceError) as excinfo:
        ensure_workspace(42, cwd=repo)

    message = str(excinfo.value)
    assert str(path) in message and "task/42" in message
    assert f"git -C {path} rebase --continue" in message
    assert f"git -C {path} rebase --abort" in message
    # nothing was decided for the agent
    assert _git(path, "rev-parse", "HEAD") == head_before
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert workspace_cmd._rebase_in_progress(path) is True


def test_ensure_hands_the_tree_back_once_the_agent_has_cleared_the_rebase(repo):
    """The refusal must be a POINTER, not a dead end — the whole reason it names two commands the
    agent can run. Run one of them and the ordinary resume path works again, with the branch's
    commits (the ones the rebase was replaying) intact."""
    path = _interrupted_rebase_build_tree(repo)
    with pytest.raises(WorkspaceError):
        ensure_workspace(42, cwd=repo)

    _git(path, "rebase", "--abort")                       # the agent's call, not the tool's

    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is False and again["branch"] == "task/42"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/42"
    assert "work in progress" in _git(path, "log", "--oneline", "-1")


def test_ensure_still_hands_back_a_review_tree_which_is_detached_by_design(repo):
    """The other side of the `role == "build"` conjunct, and the regression that would matter most:
    a review tree is created with `worktree add --detach` and therefore ALWAYS has `branch: None`.
    Refuse on detachedness alone and every second `--role review` call for a task dies."""
    first = ensure_workspace(7, role="review", cwd=repo)
    second = ensure_workspace(7, role="review", cwd=repo)
    assert first["created"] is True and second["created"] is False
    assert second["branch"] is None and second["path"] == first["path"]


def test_release_refuses_a_detached_build_tree_and_says_what_it_is(repo):
    """The mirror refusal. This state used to come out as `unreachable-head` — true, but it names
    a symptom of the wrong thing (the replayed commit) and offers no recovery, so `--gc`'s `kept`
    line told a human "reachable from no ref" about a tree whose actual problem is that it is off
    its branch mid-rebase."""
    path = _interrupted_rebase_build_tree(repo)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "MID-REBASE" in res["reason"] and f"git -C {path} rebase --abort" in res["reason"]
    assert path.exists()


def test_release_no_longer_destroys_a_detached_build_tree_whose_branch_is_unpushed(repo):
    """THE measured hole (see `_detached_build_tree_whose_head_is_reachable`), and the one case
    here where the OLD behaviour was `released: true`, not merely a bad message.

    A build tree detached with its HEAD on `onto` passed every guard: `dirty` (clean), the
    branch-history guard (skipped — `wt["branch"]` is None), the reachability guard (origin/main
    contains HEAD). So `--release` — and `--gc`, unattended, every tick — removed the directory and
    reported success while `task/43` still held a commit that was not on origin/main, and no key in
    the payload said so. The work survives on the branch, so this was never data loss; it was a
    report that said the opposite of what happened."""
    path = _detached_build_tree_whose_head_is_reachable(repo)
    unpushed_before = _git(repo, "log", "--oneline", "origin/main..task/43")

    res = release_workspace(43, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert path.exists()
    assert _git(repo, "log", "--oneline", "origin/main..task/43") == unpushed_before
    # and the message names the state, not the reachability of a commit nobody asked about
    assert "reachable from no ref" not in res["reason"]


def _detached_build_tree_without_a_rebase(repo, task_id=44):
    """A build tree off its branch with NO rebase state — the other half of the refusal, and the
    reason the guard keys on `branch is None` rather than on the rebase probe. A rebase is the
    commonest way a tree ends up here, not the only one (`git bisect`, a hand `checkout --detach`,
    a rebase somebody half-cleared), and all of them break the same promise: nothing committed
    here reaches `task/<id>`."""
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "wip.txt").write_text("real work\n")
    _git(path, "add", "wip.txt")
    _git(path, "commit", "-m", "work in progress")
    _git(path, "checkout", "--detach", "HEAD")
    assert [w for w in list_worktrees(repo) if w["path"] == path][0]["branch"] is None
    assert workspace_cmd._rebase_in_progress(path) is False
    _quiesce(path)
    return path


def test_release_refuses_a_detached_build_tree_with_no_rebase_in_progress(repo):
    """Same refusal, different recovery — and the message must not claim a rebase that is not
    there. Pinned because the wording is chosen by a PROBE (`_rebase_in_progress`) while the guard
    itself keys on `branch is None`: mixing those up would either refuse the wrong trees or promise
    the reader a `rebase --continue` that exits 'no rebase in progress'."""
    path = _detached_build_tree_without_a_rebase(repo)

    res = release_workspace(44, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "no rebase in progress" in res["reason"]
    assert "rebase --continue" not in res["reason"]
    assert f"git -C {path} checkout task/44" in res["reason"]
    assert path.exists()


def test_the_detached_build_refusal_does_not_advise_discarding_an_orphaned_head(repo):
    """The branch can be gone (a hand `git branch -D`, or #517's leaked-branch path in reverse) and
    then this detached HEAD is the ONLY name for the commits in the tree. `checkout task/<id>` —
    the recovery the ordinary case names — would then be advice that orphans them, so the message
    has to be built from the fact rather than written once and assumed.

    (Constructed WITHOUT a rebase in flight on purpose: git refuses `branch -D` for a branch a
    worktree is mid-rebase on — measured, `cannot delete branch 'task/42' used by worktree at …` —
    so the rebase variant of this state cannot be reached from the outside at all.)"""
    path = _detached_build_tree_without_a_rebase(repo, task_id=45)
    _git(repo, "branch", "-D", "task/45")

    res = release_workspace(45, cwd=repo)

    assert res["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert "does not exist any more" in res["reason"]
    assert f"git -C {path} checkout task/45" not in res["reason"]
    assert f"git -C {path} checkout -b task/45" in res["reason"]


def test_gc_shouts_about_a_build_tree_an_interrupted_rebase_left_detached(repo, tracker):
    """ROUND-2 REVIEW of VMCP-68, THE finding: this refusal used to be graded routine on the code
    alone, on a justification (a reviewer's in-tree notes, above) that is entirely about REVIEW
    trees — while a BUILD tree reaches the same detached branch of `_release_locked` after an
    interrupted rebase.

    Nothing about that is routine, and grading it `expected` filed it under "do not look" FOREVER —
    the exact shape of `half-created`, which this module correctly calls never-routine. VMCP-86
    changed WHICH code the build tree emits (`detached-build`, which names the state and its
    recovery instead of the reachability of the replayed commit) but not the verdict this test
    exists for: `kept`, never `expected`, and the tree never destroyed."""
    api, wf = tracker
    api.add_task("its card has moved on", "Done")         # task 42 is no longer in Build -> dead
    path = _interrupted_rebase_build_tree(repo)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []                          # NOT filed under "no action needed"
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert res["kept"][0]["code"] == workspace_cmd.CODE_DETACHED_BUILD
    assert res["kept"][0]["role"] == "build"
    assert path.exists()                                  # and still refused, never destroyed


def test_keep_grading_of_unreachable_head_still_turns_on_the_role(repo):
    """VMCP-68's `role` conjunct, pinned DIRECTLY because no sweep can construct it any more: since
    VMCP-86 a detached build tree is refused upstream with its own code, so `unreachable-head`
    now only ever arrives from a review tree. The conjunct is kept as a backstop — the grading
    policy's rule is "fail toward shouting", and letting it decay into "expected on the code alone"
    would restore VMCP-68's round-2 bug the moment anything routes a build tree back here.

    Delete the conjunct (`return True` on the code alone) and the first assertion goes red."""
    build = {"code": workspace_cmd.CODE_UNREACHABLE_HEAD, "role": "build", "task_id": 1}
    review = {"code": workspace_cmd.CODE_UNREACHABLE_HEAD, "role": "review", "task_id": 1}
    assert workspace_cmd._keep_is_expected(build, parked=set()) is False
    assert workspace_cmd._keep_is_expected(review, parked=set()) is True


def test_a_parked_card_never_launders_a_half_created_tree_into_expected(repo, tracker, monkeypatch):
    """The boundary of "parked ⇒ routine": it applies to the two guards that protect ORDINARY
    in-progress work, never to a broken tool state. A half-created tree (git's own `locked
    initializing` from a killed `worktree add`) needs a human with two git commands whether or not
    its card happens to be parked — grade it quiet and the one refusal nobody else can resolve
    disappears from the only list anybody reads."""
    api, wf = tracker
    task = _parked(api, wf)
    half = _half_created_tree(repo, monkeypatch, task_id=task["id"])
    _quiesce(half)          # the checkout is PARKED, not finished — quiesce LAST, then sweep

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == []
    assert [k["code"] for k in res["kept"]] == [workspace_cmd.CODE_HALF_CREATED]


def test_a_parked_card_past_the_first_page_is_still_graded_as_parked(repo, tracker):
    """`liveness_board` must page Your Call EXHAUSTIVELY (it is in require_titles), because a
    parked id that pagination truncated away reads as NOT parked — and gc then grades a routine
    refusal as an alarm, quietly, and only on the boards busy enough to fill a page. Squeeze the
    fake's page size to 1 so the card under test sits past the first page of Your Call."""
    api, wf = tracker
    api.page_size = 1
    _parked(api, wf, title="parked earlier, fills page 1")
    task = _parked(api, wf, title="parked second, past the page")
    _unpushed_build_tree(repo, task["id"])

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["kept"] == []
    assert [e["task_id"] for e in res["expected"]] == [task["id"]]


# --- VMCP-91 (547): the parked-card branch turns on the ROLE too ---

def test_a_parked_card_does_not_launder_a_reviewers_dirty_tree(repo, tracker):
    """VMCP-91, and it is VMCP-68's round-2 finding one axis over: `dirty`/`unpushed` were graded
    routine by the PARKED CARD alone, on a justification written entirely about the BUILD agent
    (`call_human` is what an agent calls when a rebase conflicts, so its tree holds unsaved work
    while the card waits). A REVIEW tree reaches the very same role-agnostic `dirty` guard, so a
    reviewer's stranded draft was filed under "do not look" for as long as a card it merely shares
    a task id with sat in Your Call.

    Reachable on the ordinary path, which is what this builds: the reviewer files `needs_work`
    without `--release`, the card goes back to Build, the build agent hits a conflict and calls
    `call_human`. BOTH trees in ONE sweep, at the same age, with the same code and the same board
    state — the only difference that can MOVE the verdict is the role (the fixtures also differ in
    task id and in which file is dirty: the task id DOES reach `_keep_is_expected` — it is the only
    key the grader takes by subscript rather than `.get`, `entry["task_id"] in parked` — but BOTH
    cards are parked, so it cannot change the answer, while which file is dirty never reaches the
    grader at all), so the test holds one code to opposite verdicts and cannot pass by nothing ever
    being graded routine."""
    api, wf = tracker
    head = _git(repo, "rev-parse", "HEAD")

    build_card = _parked(api, wf, title="build agent parked on a conflict")
    build_tree = Path(ensure_workspace(build_card["id"], cwd=repo)["path"])
    (build_tree / "README.md").write_text("<<<<<<< HEAD\nhalf-resolved conflict\n")

    review_card = _parked(api, wf, title="bounced back to build, then parked")
    review_tree = Path(
        ensure_workspace(review_card["id"], role="review", at=head, cwd=repo)["path"])
    (review_tree / "review-notes.md").write_text("draft verdict: needs work because...\n")

    _quiesce(build_tree)
    _quiesce(review_tree)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert sorted((e["task_id"], e["role"], e["code"]) for e in res["expected"]) == [
        (build_card["id"], "build", workspace_cmd.CODE_DIRTY)], \
        "the BUILD tree under a parked card is the state this branch exists for — it must stay " \
        "`expected`, or the fix has traded one unread signal for a noisy one"
    assert sorted((k["task_id"], k["role"], k["code"]) for k in res["kept"]) == [
        (review_card["id"], "review", workspace_cmd.CODE_DIRTY)], \
        "a reviewer's dirty tree is graded `expected` again — the parked card belongs to someone " \
        "else's unsaved work, and the reviewer's contract is a verdict as a tracker COMMENT"
    assert build_tree.exists() and review_tree.exists()    # refused, never destroyed, either way


def test_the_grading_grid_is_all_kept_outside_the_four_named_cells():
    """The completeness pin, and it exists because of this card's own history. Two branches of
    `_keep_is_expected` had the same role-shaped hole; VMCP-68's round-2 review closed one, and the
    mirror survived in the other for two more rounds of review OF THAT FUNCTION. What nobody had was
    the whole grid in one place, so "we checked the branch we were looking at" kept reading as "we
    checked it".

    Every code the module declares, both roles PLUS a role-less entry, both board states. FOUR of
    the 66 cells are routine, spread over THREE codes and coming from TWO sets (the two the parked
    build tree excuses share one); every other cell is `kept`. Counted as CELLS on purpose — this
    pin's first wording said "two rows", which is the miscount-inside-a-closed-enumeration that the
    card it belongs to was filed against. The `declared <= grid` assertion is the part that survives
    the next card: a new `CODE_*` constant fails here until somebody grades it deliberately, rather
    than inheriting a default nobody read.

    MUTATION-CHECKED, control 0 failed each time, `__pycache__` purged between rounds: drop the
    `role == "build"` conjunct -> 3 failed; invert it to "review" -> 6; drop `role == "review"`
    -> 2; drop the `parked` conjunct -> 6; add CODE_DIRTY to the review set -> 4; add CODE_LOCKED
    to the build set -> 2; declare a new ungraded `CODE_*` -> 1 (this test alone). This test is in
    every one of those counts."""
    codes = [workspace_cmd.CODE_DIRTY, workspace_cmd.CODE_UNPUSHED,
             workspace_cmd.CODE_UNREACHABLE_HEAD, workspace_cmd.CODE_DETACHED_BUILD,
             workspace_cmd.CODE_HALF_CREATED, workspace_cmd.CODE_LOCKED,
             workspace_cmd.CODE_NO_WORKTREE, workspace_cmd.CODE_SELF_TREE,
             workspace_cmd.CODE_RELEASE_ERROR,
             "a-code-nobody-declared", None]           # the fail-toward-shouting fallbacks
    declared = {v for k, v in vars(workspace_cmd).items()
                if k.startswith("CODE_") and isinstance(v, str)}
    assert declared <= set(codes), \
        f"a declared code is ungraded by this grid: {sorted(declared - set(codes))} — add it to " \
        f"the row list AND to the expected-cell set below, deliberately"

    routine = set()
    for code in codes:
        for role in ("build", "review", None):
            for parked in (True, False):
                entry = {"task_id": 7}
                if code is not None:
                    entry["code"] = code
                if role is not None:
                    entry["role"] = role
                if workspace_cmd._keep_is_expected(entry, {7} if parked else set()):
                    routine.add((code, role, parked))

    assert routine == {
        (workspace_cmd.CODE_DIRTY, "build", True),
        (workspace_cmd.CODE_UNPUSHED, "build", True),
        (workspace_cmd.CODE_UNREACHABLE_HEAD, "review", True),
        (workspace_cmd.CODE_UNREACHABLE_HEAD, "review", False),
    }, "the set of cells graded routine moved — every OTHER cell in this grid must be `kept`"


# --- VMCP-69 (517): the two behaviour leftovers of the parallel-drain branch ---

def test_the_main_worktree_lookup_runs_git_once_however_often_it_is_asked(repo, monkeypatch):
    """`worktree_root` is called from `_find`, from `_release_locked`'s not-found branch and once
    per sweep, and each call used to spawn its own `git worktree list --porcelain`. Memoised on
    `_main_worktree`, because that is where the subprocess is and the answer cannot change while
    the process runs. Disable the lru_cache and this counts three listings instead of one."""
    workspace_cmd._main_worktree.cache_clear()
    seen = []
    real = workspace_cmd._run_git

    def counting(args, cwd, timeout, env_extra=None):
        seen.append(tuple(args))
        return real(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", counting)

    assert worktree_root(repo) == worktree_root(repo) == worktree_root(repo)

    assert [c for c in seen if c[:2] == ("worktree", "list")] == [
        ("worktree", "list", "--porcelain")
    ]


def test_the_env_override_is_not_frozen_by_that_memoisation(repo, monkeypatch):
    """The cache is deliberately on `_main_worktree` and NOT on `worktree_root`: the latter reads
    VIKUNJA_WORKTREE_ROOT and the repo toml, which callers (and this suite) change underneath it.
    Move the cache up a level and this goes red — the second answer would still be the first."""
    workspace_cmd._main_worktree.cache_clear()
    first = worktree_root(repo)
    monkeypatch.setenv(ENV_WORKTREE_ROOT, "elsewhere")
    assert worktree_root(repo) == (repo / "elsewhere").resolve() != first


def _fail_branch_delete(monkeypatch):
    """Make `git branch -D` fail while everything else stays real git. The window is otherwise
    unreachable on demand, and it is the whole point of the guard: the worktree is ALREADY gone
    by the time this fires."""
    real = workspace_cmd._run_git

    def selective(args, cwd, timeout, env_extra=None):
        if args[:2] == ("branch", "-D"):
            raise WorkspaceError(f"git {' '.join(args)} failed: simulated ref-store failure")
        return real(args, cwd, timeout, env_extra)

    monkeypatch.setattr(workspace_cmd, "_run_git", selective)


def test_a_branch_delete_failure_does_not_report_a_tree_that_is_already_gone(repo, monkeypatch):
    """`worktree remove` succeeded, `branch -D` did not. Reporting `released: False` there is not
    a neutral "it failed": SKILL.md teaches that field as "PROTECTION — your unsaved work is still
    in the tree", so it sent a human to a directory git had just deleted. Say what happened."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    _fail_branch_delete(monkeypatch)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is True                       # the tree really is gone...
    assert not path.exists()
    assert res["branch_deleted"] is False                # ...and the branch really is not
    assert "task/42" in res["warning"] and "branch -D" in res["warning"]
    monkeypatch.undo()
    assert "task/42" in _git(repo, "branch", "--list", "task/42")


def test_a_leaked_branch_is_recoverable_by_the_ordinary_resume_path(repo, monkeypatch):
    """Why `released: True` is honest rather than a shrug: a surviving `task/<id>` is the same
    state a hand-deleted tree leaves, and `_ensure_locked` reattaches to it instead of recreating
    it. Nothing is lost and no cleanup is required before the task can be worked again."""
    ensure_workspace(42, cwd=repo)
    _fail_branch_delete(monkeypatch)
    release_workspace(42, cwd=repo)
    monkeypatch.undo()

    again = ensure_workspace(42, cwd=repo)

    assert again["created"] is True and again["branch"] == "task/42"
    assert Path(again["path"]).is_dir()


def test_gc_files_a_branch_delete_failure_under_released_not_kept(repo, tracker, monkeypatch):
    """The sweep's side of the same bug: the per-tree `except` recorded a `kept` entry whose
    `path` no longer existed, and `kept` is the list SKILL.md tells the pump a human must read.
    Fixed at the source, so the sweep needs no special case — and 516 is free to keep rewriting
    that handler."""
    _api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])       # dead: nothing on the board
    _quiesce(path)                                            # past VMCP-71's grace window
    _fail_branch_delete(monkeypatch)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [42]
    assert res["kept"] == [] and res["expected"] == []      # not a refusal at all, so ungraded
    assert res["released"][0]["branch_deleted"] is False
    assert not path.exists()


def test_a_clean_release_says_nothing_about_the_branch(repo):
    """The failure keys are added ONLY on failure, so their ABSENCE is the success signal and no
    existing consumer of a released entry has to learn a new field."""
    ensure_workspace(42, cwd=repo)
    res = release_workspace(42, cwd=repo)
    assert res["released"] is True
    assert "branch_deleted" not in res and "warning" not in res
# --- VMCP-72: the sweep's liveness read is bounded OVERALL, not just per request ---
#
# Modelled rather than slept: the deadline measures DURATIONS, so a test that really waited would
# be slow AND flaky. `_FakeClock` is the clock the deadline reads and the transport advances, so
# "how long did this read hold the lock" is an exact number here instead of a stopwatch.


class _FakeClock:
    def __init__(self):
        self.t = 0.0

    def __call__(self):
        return self.t


def _slow_board_client(clock, seconds_per_request, sent, attempted, *,
                       review=0, your_call=0, page_size=50, hooks=()):
    """A REAL httpx.Client over a transport that models a tracker answering every request
    `seconds_per_request` late — and, like a real socket, giving up when that exceeds the timeout
    it was handed. That second half is what makes the clamp observable at all: httpcore reads
    `request.extensions["timeout"]` when it sends, MockTransport does not, so the model must.
    (Verified against the real thing before it was modelled: a 10 s budget on a read needing 18 s
    came back at 9.96 s with a ReadTimeout, not at 12 s.)

    `attempted` records every request the CLIENT tried to send (hook level, so it includes the
    ones the deadline refuses); `sent` records only those that reached the transport. Two lists
    because the difference between them IS the deadline's effect.
    """
    def record(request):
        attempted.append(str(request.url).split("/api/v1")[-1])

    def handler(request):
        allowed = (request.extensions.get("timeout") or {}).get("read")
        took = seconds_per_request if allowed is None else min(seconds_per_request, allowed)
        clock.t += took
        sent.append(str(request.url).split("/api/v1")[-1])
        if allowed is not None and seconds_per_request > allowed:
            raise httpx.ReadTimeout("timed out", request=request)
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": page_size})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "K"}])
        page = int(request.url.params.get("page", 1))
        counts = {"Review": review, "Your Call": your_call}
        return httpx.Response(200, json=[
            {"id": index, "title": title, "tasks": [
                {"id": index * 10_000 + i, "title": f"t{i}", "assignees": []}
                for i in range((page - 1) * page_size,
                               min(page * page_size, counts.get(title, 0)))
            ]}
            for index, title in enumerate(STAGES, start=1)
        ])

    return httpx.Client(
        base_url="http://tracker.invalid/api/v1",
        transport=httpx.MockTransport(handler),
        timeout=workspace_cmd._READ_TIMEOUT_SECONDS,
        event_hooks={"request": [record, *hooks]},
    )


def _workflow_on(client):
    """The REAL Workflow and the REAL api.py client loop — only the socket is modelled. The
    swallowing that matters (`_fetch_page_size` eating httpx errors) lives in api.py, so a fake
    api here would prove nothing about it."""
    return Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)


def _read(wf, deadline=None):
    """PRODUCTION's own read helper — the whole unit the budget covers, arming included. Driven
    here rather than imitated: the relabelling and the arming are the behaviour under test, and a
    test-local copy of the read would exercise neither."""
    return workspace_cmd._read_liveness(wf, deadline)


def test_the_liveness_read_costs_one_more_request_per_page_of_a_human_drained_column():
    """The premise of `_READ_DEADLINE_SECONDS`: the hold is the REQUEST COUNT, and the count is
    not a constant. `liveness_board` pages Review and (since VMCP-68) Your Call exhaustively —
    the two columns the pump cannot drain, because a card leaves them only when a human moves it
    to Done or answers it. So a per-request bound cannot bound the hold: it multiplies.

    Measured against the real tracker at 4 requests / ~1 s; modelled here at 3 s per request so
    the arithmetic is visible. (Since VMCP-103 every board read also spends ONE confirming page
    past its last page with content — a short page stopped proving a bucket is exhausted — so the
    counts here are the old ones plus that flat one, on both boards. VMCP-108 adds ONE more, once
    per read and independent of the board: `views()` is now paged too, and this transport models
    the real 2.3.0 behaviour of serving the whole list and ignoring `?page=`, so it stops on the
    confirming page — the flat +1 the uniform rule costs.)"""
    clock, sent, attempted = _FakeClock(), [], []
    wf = _workflow_on(_slow_board_client(clock, 3.0, sent, attempted, review=41, your_call=5))
    _read(wf)
    assert len(sent) == 6 and clock.t == pytest.approx(18.0)      # today's board (+1 confirming)

    for column in ({"review": 140}, {"your_call": 140}):          # EITHER one drives it
        clock, sent, attempted = _FakeClock(), [], []
        wf = _workflow_on(_slow_board_client(clock, 3.0, sent, attempted, **column))
        _read(wf)
        assert len(sent) == 8, f"{column}: {sent}"
        assert clock.t == pytest.approx(24.0), f"{column} held the lock {clock.t}s"


def test_the_sweep_read_is_bounded_overall_not_only_per_request():
    """The fix itself, stated as the delta it buys. The SAME read, at the SAME per-request
    ceiling: unbounded it holds the lock for eight times that ceiling, budgeted it stops at the
    budget and abandons — refusing the next request BEFORE sending it, so the abandon costs
    nothing more.

    At this deliberately absurd 10 s/request the budget is now spent before the board pages are
    reached at all (VMCP-108's paged `views()` costs one of the three requests that fit). That is
    an artefact of the model, not of production: the same read was MEASURED against the real
    tracker at ~0.25 s/request, where the extra page is noise against a 30 s budget. What the
    assertions below pin is the property that does not depend on the rate — the read is abandoned
    BEFORE any liveness set exists to act on, which is the whole reason the budget is enforced at
    the request hook rather than around the call."""
    per_request = workspace_cmd._READ_TIMEOUT_SECONDS
    budget = workspace_cmd._READ_DEADLINE_SECONDS

    clock, sent, attempted = _FakeClock(), [], []
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted, your_call=140))
    _read(wf)
    assert len(sent) == 8 and clock.t == pytest.approx(8 * per_request)   # 80s of held lock
    # 140 Your Call cards at 50/page = 3 pages + VMCP-103's confirming page; the other four
    # requests are /info, the two views pages and the /user active_task_ids needs.
    assert sum("/tasks" in url for url in sent) == 4

    clock, sent, attempted = _FakeClock(), [], []
    deadline = workspace_cmd._ReadDeadline(budget, now=clock)
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted,
                                         your_call=140, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded):
        _read(wf, deadline)
    assert clock.t == pytest.approx(budget)          # the hold IS the budget, not 80s
    assert len(attempted) == len(sent) + 1           # one refused before it went out
    assert len(sent) == budget / per_request         # and it stopped ON the budget, not past it
    # abandoned with the board unread — no liveness set was ever built, so nothing could be reaped
    assert not any("/tasks" in url for url in sent)


def test_a_spent_read_budget_is_not_swallowed_by_the_page_size_fallback():
    """`api._fetch_page_size` swallows `(VikunjaError, httpx.HTTPError)` and answers "unknown".
    Were `ReadDeadlineExceeded` an httpx exception, a budget that ran out at `/info` would be
    EATEN right there and the read would carry on past its own deadline — the bound silently gone
    on exactly the boards big enough to need it. Being a WorkspaceError, it stops the read where
    it fires: nothing after `/info` is even attempted.

    Since VMCP-108 `/info` is the FIRST request of any read — every list GET resolves the page
    size before it pages — so the way to land the refusal inside that `except` is a budget that is
    already spent when the read starts, which is exactly the state a caller handed a used-up
    deadline is in. Nothing else can reach `/info` any more, and a test that let the refusal fall
    on the NEXT request instead would pass without ever entering the swallowing frame."""
    clock, sent, attempted = _FakeClock(), [], []
    deadline = workspace_cmd._ReadDeadline(0.0, now=clock)
    wf = _workflow_on(_slow_board_client(clock, workspace_cmd._READ_TIMEOUT_SECONDS,
                                         sent, attempted, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded):
        _read(wf, deadline)
    assert attempted == ["/info"]     # refused INSIDE _fetch_page_size — and not swallowed there
    assert sent == []


def test_the_read_budget_clamps_each_request_to_what_is_left():
    """Without the clamp the LAST request keeps its own full ceiling, so a read that starts one
    tick inside the budget overshoots it by a whole `_READ_TIMEOUT_SECONDS` — the bound would be
    "budget plus a timeout", not the budget. Clamped, the read ends ON the budget.

    And it is reported as the BUDGET, not as the bare `ReadTimeout` the clamp actually raises —
    the finding that came out of running this end to end (see `_read_liveness`): a sweep that
    stops exactly on its budget and says "timed out" is indistinguishable from one request timing
    out, so the operator learns nothing from the line that matters most."""
    clock, sent, attempted = _FakeClock(), [], []
    per_request = workspace_cmd._READ_TIMEOUT_SECONDS
    budget = 2.5 * per_request                     # runs out HALFWAY through the third request
    deadline = workspace_cmd._ReadDeadline(budget, now=clock)
    wf = _workflow_on(_slow_board_client(clock, per_request, sent, attempted,
                                         your_call=140, hooks=[deadline]))
    with pytest.raises(ReadDeadlineExceeded) as caught:
        _read(wf, deadline)
    assert clock.t == pytest.approx(budget)        # not 3 x per_request
    assert "overall budget" in str(caught.value)
    assert isinstance(caught.value.__cause__, httpx.ReadTimeout)   # cause kept, not lost


def test_a_failure_with_budget_left_is_not_laundered_into_a_deadline():
    """The other direction of the relabelling, and the reason it keys on `spent()` alone: a read
    that fails while the budget still has time on it — a 500, a refused connection, a bad token —
    is NOT the tracker being slow, and calling it that would send an operator hunting latency for
    a broken token. It must propagate exactly as it is."""
    clock = _FakeClock()          # never advanced: the budget is untouched when this fails
    deadline = workspace_cmd._ReadDeadline(workspace_cmd._READ_DEADLINE_SECONDS, now=clock)

    def refuse(request):
        raise httpx.ConnectError("connection refused", request=request)

    client = httpx.Client(base_url="http://tracker.invalid/api/v1",
                          transport=httpx.MockTransport(refuse),
                          event_hooks={"request": [deadline]})
    with pytest.raises(httpx.ConnectError):
        _read(_workflow_on(client), deadline)


def test_gc_reaps_nothing_when_the_liveness_read_is_abandoned(repo, tracker):
    """THE invariant, and the one that makes this a latency fix rather than a data-loss bug: an
    abandoned read must leave every tree alone — including one that is dead, quiet and otherwise
    perfectly reapable. A partial or failed `alive` set can never reach the reap loop, because
    the read raises before the loop is entered. Also proves the lock is RELEASED on that path:
    bounding the hold is pointless if abandoning leaks it."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
    _quiesce(path)

    class AbandoningWorkflow:
        """What the deadline hook looks like from gc's side: the read raises, nothing else runs."""

        def liveness_board(self):
            raise ReadDeadlineExceeded("the liveness read exceeded its overall budget")

        def active_task_ids(self, board=None):
            raise AssertionError("a liveness set was computed from an abandoned read")

        review_task_ids = parked_task_ids = active_task_ids

    with pytest.raises(ReadDeadlineExceeded):
        gc_workspaces(cwd=repo, workflow=AbandoningWorkflow())

    assert path.exists()                                          # KEEP
    assert _git(repo, "branch", "--list", "task/42").strip()      # and its branch
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    with open(common / "vikunja-mcp-worktree.lock", "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)            # free again, not leaked
        fcntl.flock(fh, fcntl.LOCK_UN)


def test_gc_arms_the_read_budget_only_once_it_holds_the_lock(repo, tracker, monkeypatch):
    """The budget bounds the HOLD, and `_repo_lock` BLOCKS. Armed at construction — before the
    flock — a sweep queued behind another agent's ensure/--release/--gc would spend its budget
    WAITING, then abandon a read it never got to start: every contended tick failing, forever,
    having done nothing wrong. Proven the way Important 5 proves the read's placement: the probe
    for a second, non-blocking flock must FAIL at arming time, i.e. the lock is already held."""
    api, wf = tracker
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"
    events = []

    class ProbingDeadline:
        def arm(self):
            with open(lock_path, "w") as fh:
                with pytest.raises(BlockingIOError):
                    fcntl.flock(fh, fcntl.LOCK_EX | fcntl.LOCK_NB)
            events.append("armed")

    class RecordingWorkflow:
        def liveness_board(self):
            events.append("read")
            return wf.liveness_board()

        def active_task_ids(self, board=None):
            return wf.active_task_ids(board=board)

        def review_task_ids(self, board=None):
            return wf.review_task_ids(board=board)

        def parked_task_ids(self, board=None):
            return wf.parked_task_ids(board=board)

    monkeypatch.setattr(workspace_cmd, "_build_workflow",
                        lambda root: (RecordingWorkflow(), ProbingDeadline()))
    gc_workspaces(cwd=repo)                     # workflow=None -> the PRODUCTION path
    assert events == ["armed", "read"]          # armed under the lock, and before the read


def test_the_gc_client_carries_the_read_budget_as_a_request_hook(repo, monkeypatch):
    """The bound is only real if it is on the client gc actually BUILDS. Pinned here because
    every other test in this group installs the hook itself, so a `_build_workflow` that quietly
    stopped attaching it would leave them all green and the production sweep unbounded."""
    from vikunja_mcp import config as config_mod
    from vikunja_mcp.workspace_cmd import _build_workflow

    monkeypatch.setattr(config_mod, "load_config", lambda cwd=None, environ=None:
                        config_mod.Config(url="http://example.invalid", token="t", project_id=7))
    wf, deadline = _build_workflow(repo)

    assert isinstance(deadline, workspace_cmd._ReadDeadline)
    assert deadline in wf.api._client.event_hooks["request"]
    assert deadline.budget == workspace_cmd._READ_DEADLINE_SECONDS


def test_an_abandoned_sweep_is_one_json_error_line_the_pump_can_read(monkeypatch, capsys,
                                                                     tmp_path):
    """`ReadDeadlineExceeded` is public and unprefixed because the class name IS the CLI's error
    string. A pump that sees `{"error": "ReadDeadlineExceeded: ..."}` knows the tracker was slow,
    not that its worktrees are broken — and exit 1 puts it on the path SKILL.md already covers
    ("--gc не достучался до трекера": degrade the drain, never stop it)."""
    monkeypatch.chdir(tmp_path)                    # see the hygiene note above

    def boom():
        raise ReadDeadlineExceeded("the liveness read exceeded its 30s overall budget")

    monkeypatch.setattr("vikunja_mcp.workspace_cmd.gc_workspaces", boom)
    assert run_workspace(["--gc"]) == 1
    err = json.loads(capsys.readouterr().out.strip())["error"]
    assert err.startswith("ReadDeadlineExceeded: ") and "overall budget" in err


# --- VMCP-89: a page size the client had to GUESS must never be able to end in a reap ---

def _paginating_tracker(page_size, tasks_by_stage, *, info_status=200, sent=None):
    """A REAL httpx client over a tracker that pages each bucket's `tasks` the way Vikunja 2.3
    does — `page_size` at a time, per bucket, driven by `?page=` — and whose `/info` can be made
    to fail.

    Real client + real api.py, not `FakeAPI`, because the whole mechanism lives in api.py: the
    fake resolves no page size at all, so a board truncated by a WRONG one is invisible to it —
    the shape this project has already been bitten by (a fake that shares the code's own wrong
    model proves nothing about it).
    """
    def handler(request):
        path = request.url.path
        if sent is not None:
            sent.append(f"{path.split('/api/v1')[-1]}?{request.url.params}".rstrip("?"))
        if path.endswith("/info"):
            if info_status != 200:
                return httpx.Response(info_status, json={"message": "boom"})
            return httpx.Response(200, json={"max_items_per_page": page_size})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "Kanban"}])
        page = int(request.url.params.get("page", 1))
        return httpx.Response(200, json=[
            {"id": index, "title": title, "tasks": [
                {"id": tid, "title": f"t{tid}", "assignees": [{"id": 1}]}
                for tid in tasks_by_stage.get(title, [])[(page - 1) * page_size:page * page_size]
            ]}
            for index, title in enumerate(STAGES, start=1)
        ])

    return httpx.Client(base_url="http://tracker.invalid/api/v1",
                        transport=httpx.MockTransport(handler))


@pytest.mark.parametrize("info_status, why", [
    (200, "the control: with /info healthy the same board reads whole"),
    (500, "the bug: /info failed, so the page size had to be guessed"),
])
def test_gc_keeps_every_live_tree_when_the_servers_pages_are_smaller_than_the_guess(
    repo, info_status, why
):
    """THE data-loss path this task exists for, constructed rather than reasoned about.

    A server whose real `max_items_per_page` is 3 and an `/info` that fails: the client used to
    fall back to a hardcoded 50, and `view_tasks` stopped paging as soon as no bucket returned a
    FULL page — which on this server is never. The board silently ended after page 1, the tasks
    past it read as gone, and `--gc` destroyed their worktrees. Observed before the fix, on this
    exact test: `released=[804, 805]`, two LIVE trees and their `task/*` branches deleted, in a
    sweep that reported success.

    The 200 row is the control, and it is what makes the 500 row trustworthy: the identical board
    and the identical trees, with the ONLY difference being whether the client could know the page
    size. Both must keep all five.
    """
    live = [801, 802, 803, 804, 805]                 # 5 tasks, 3 per page -> two pages
    trees = {}
    for task_id in live:
        trees[task_id] = Path(ensure_workspace(task_id, cwd=repo)["path"])
        _quiesce(trees[task_id])                     # past the grace window: reapable if dead
    client = _paginating_tracker(3, {"Build": live}, info_status=info_status)
    wf = Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["released"] == [], why
    assert res["kept"] == []
    for task_id, path in trees.items():
        assert path.is_dir(), f"{why}: live tree for {task_id} was destroyed"
        assert _git(repo, "branch", "--list", f"task/{task_id}").strip()



# --- VMCP-92 (548): the DEGRADED read is bounded, and its bound keeps gc at KEEP ---

def test_gc_keeps_every_tree_when_the_degraded_board_read_hits_its_ceiling(repo):
    """The bound added to `view_tasks` on an unknown page size RAISES rather than returning a
    short board, and this is why that direction matters — end to end through the reaper rather
    than reasoned about.

    A server whose `/info` is down and whose board never converges (a brand-new Build task on
    every page) used to page forever; it now raises, and the exception has to leave `view_tasks`,
    leave `_read_liveness` and abandon the sweep BEFORE the reap loop. Had the read returned its
    partial board instead, every one of these quiesced trees would read as dead and be destroyed
    — the exact VMCP-89 reap, re-opened by a different route."""
    trees = {}
    for task_id in (801, 802, 803):
        trees[task_id] = Path(ensure_workspace(task_id, cwd=repo)["path"])
        _quiesce(trees[task_id])

    sent = []

    def handler(request):
        path = request.url.path
        if path.endswith("/info"):
            return httpx.Response(503, json={"message": "unavailable"})
        if path.endswith("/user"):
            return httpx.Response(200, json={"id": 1, "username": "agent"})
        if path.endswith("/views"):
            return httpx.Response(200, json=[{"id": 7, "view_kind": "kanban", "title": "Kanban"}])
        page = int(request.url.params.get("page", 1))
        sent.append(page)
        if len(sent) > 3 * MAX_UNPROVEN_PAGES:      # the loop does not terminate -> fail LOUDLY
            raise RuntimeError("the liveness read paged past three times the ceiling")
        return httpx.Response(200, json=[
            {"id": 2, "title": "Build",
             "tasks": [{"id": 9000 + page, "title": f"t{page}", "assignees": [{"id": 1}]}]},
        ])

    client = httpx.Client(base_url="http://tracker.invalid/api/v1",
                          transport=httpx.MockTransport(handler))
    wf = Workflow(VikunjaAPI("http://tracker.invalid", "t", client=client, max_retries=0), 10)

    with pytest.raises(VikunjaError) as exc:
        gc_workspaces(cwd=repo, workflow=wf)

    assert "never finished paging" in exc.value.message
    assert len(sent) == MAX_UNPROVEN_PAGES
    for task_id, path in trees.items():
        assert path.is_dir(), f"live tree for {task_id} was destroyed"
        assert _git(repo, "branch", "--list", f"task/{task_id}").strip()


# --- VMCP-90 (545): gc's own inspection is not the tree's activity ---
#
# The interaction between VMCP-71 (the grace window) and VMCP-68 (`kept` means "a human should
# look"): inspecting a tree meant running `git status` in it, that rewrites the index, and the next
# sweep read its own footprint as an agent's and skipped the tree silently. MEASURED before the
# fix, three consecutive sweeps over the same quiesced trees: sweep 1 `kept=[unreachable-head,
# unpushed, half-created]`, sweeps 2 and 3 `kept=[half-created]` — a standing alarm absent from
# ~29 of every 30 minutes of ticks. These pin BOTH directions, because getting it wrong the other
# way (a window that no longer defers to a real write) destroys a working directory under a
# running agent, which is far worse than a delayed alarm.

def test_gc_reports_a_standing_alarm_on_every_consecutive_sweep(repo, tracker):
    """THE defect. Quiesced ONCE, then swept three times back to back with nothing else touching
    the tree: the only writer between sweeps is gc itself, so the entry must appear every time.

    Two trees, because the split was diagnostic: `unpushed` is decided by a guard gc reaches
    THROUGH `git status`, `half-created` before any git call in the tree at all — before the fix
    the first vanished after sweep 1 and the second did not. Make `_git_inspect` a plain `_git`
    again and this goes red on sweep 2."""
    api, wf = tracker
    unpushed = _unpushed_build_tree(repo, 42)
    half = Path(ensure_workspace(99, cwd=repo)["path"])
    common = Path(_git(repo, "rev-parse", "--git-common-dir"))
    if not common.is_absolute():
        common = (repo / common).resolve()
    (common / "worktrees" / half.name / "locked").write_text(workspace_cmd._LOCK_INITIALIZING)
    _quiesce(half)

    sweeps = [
        sorted((e["task_id"], e["code"]) for e in gc_workspaces(cwd=repo, workflow=wf)["kept"])
        for _ in range(3)
    ]

    assert sweeps == [[(42, workspace_cmd.CODE_UNPUSHED),
                       (99, workspace_cmd.CODE_HALF_CREATED)]] * 3
    assert unpushed.is_dir() and half.is_dir()          # reported, never removed


def test_gcs_own_sweep_leaves_the_grace_markers_untouched(repo, tracker):
    """The mechanism, pinned directly rather than through its consequence: a whole sweep over a
    tree it refuses must leave BOTH markers `_last_activity` reads exactly as it found them.

    Cheap net for the next guard added to `_release_locked` — `git diff` refreshes the index the
    same way `git status` does, so a new inspection wired through plain `_git` fails here, in one
    line, instead of quietly restoring the cadence bug two releases later."""
    api, wf = tracker
    path = _unpushed_build_tree(repo, 42)
    before = [m.stat().st_mtime_ns for m in _grace_markers(path)]

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [k["code"] for k in res["kept"]] == [workspace_cmd.CODE_UNPUSHED]   # it DID inspect
    assert [m.stat().st_mtime_ns for m in _grace_markers(path)] == before


def test_gc_still_defers_to_a_real_write_in_a_tree_it_has_already_inspected(repo, tracker):
    """THE INVARIANT, in the direction that destroys work if it is wrong. A tree gc has already
    inspected once must still read as YOUNG the moment something real writes in it — otherwise the
    fix above trades a late alarm for a working directory vanishing under a running agent.

    Constructed so only the window stands between the tree and removal: sweep 1 refuses it
    (`unpushed`), then the agent — still standing in it between `advance(to='review')` and
    `--release` — pushes, which satisfies the last guard, and runs the `git status` SKILL.md's own
    recipe has it run. The directory is aged back afterwards so the INDEX is the only fresh marker
    left, i.e. the one an hour-old tree really has. Sweep 2 must skip it silently, in neither list.
    Drop the index from `_last_activity` and this does not merely fail — the tree is destroyed."""
    api, wf = tracker
    path = _unpushed_build_tree(repo, 42)
    first = gc_workspaces(cwd=repo, workflow=wf)
    assert [(k["task_id"], k["code"]) for k in first["kept"]] == [(42, workspace_cmd.CODE_UNPUSHED)]

    _git(path, "push", "origin", "HEAD:main")           # every release guard now passes
    tree_dir, _index = _grace_markers(path)
    old = time.time() - workspace_cmd._REAP_GRACE_SECONDS - 60
    os.utime(tree_dir, (old, old))
    _git(path, "status", "--porcelain")                 # the agent's own call: it DOES take the lock
    os.utime(tree_dir, (old, old))                      # ...so the index is the only fresh marker

    second = gc_workspaces(cwd=repo, workflow=wf)

    assert second == {"released": [], "kept": [], "expected": []}
    assert path.is_dir() and (path / "feature.txt").exists()


# --- VMCP-142 (631): a HUMAN `git worktree lock` is a coded VERDICT, not a raise ---

def _human_locked(repo, task_id, role="build", reason="human says hands off", at=None):
    """A tree a human pinned with `git worktree lock` — the state this section is about.

    Deliberately built through the real CLI helpers and real git: the whole finding was that the
    module's own guards all pass on this tree and the refusal only happens inside `git worktree
    remove`, which no fake would reproduce.
    """
    path = Path(ensure_workspace(task_id, role=role, at=at, cwd=repo)["path"])
    args = ["worktree", "lock"]
    if reason is not None:
        args += ["--reason", reason]
    _git(repo, *args, str(path))
    return path


def test_release_refuses_a_human_locked_tree_with_a_code_in_both_porcelain_shapes(repo):
    """THE finding, reproduced before the fix and pinned here in the shape the fix produces.

    A tree a human locked, and OTHERWISE clean, pushed and on its branch, passed every guard in
    `_release_locked` — not half-created, not dirty, not unpushed, not detached — and then died
    inside `git worktree remove` ("fatal: cannot remove a locked working tree"). That qualifier is
    load-bearing and was missing from the first draft of this docstring: a locked tree that is ALSO
    dirty or unpushed never reached the remove at all, because those guards answered first (see
    `test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty`).
    `run_workspace`'s catch-all rendered that raise as `{"error": …}` +
    exit 1, i.e. the CREATE channel, for a state that is unambiguously the OTHER kind: the work is
    intact and a human deliberately pinned the tree. SKILL.md has agents branch on exactly that
    split, so the shape decided whether an agent shrugged ("the tool could not run, degrade to one
    slot") or reported a tree a human is holding.

    BOTH porcelain shapes, because the guard must key on the `locked` BOOL and never on the reason
    TEXT: a bare `git worktree lock` reports `lock_reason: None`, so a guard written against a
    reason string would refuse the documented case and sail past the reasonless one.
    """
    path = _human_locked(repo, 42)

    res = release_workspace(42, cwd=repo)

    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_LOCKED
    assert "human says hands off" in res["reason"]              # git's own reason, not our guess
    assert f"git worktree unlock {path}" in res["reason"]       # the human's actual next step
    assert path.is_dir()

    _git(repo, "worktree", "unlock", str(path))
    _git(repo, "worktree", "lock", str(path))                   # the REASONLESS shape
    bare = release_workspace(42, cwd=repo)
    assert bare["released"] is False and bare["code"] == workspace_cmd.CODE_LOCKED
    assert f"git worktree unlock {path}" in bare["reason"]
    assert path.is_dir()


def test_release_refuses_a_locked_review_tree_with_the_same_code(repo):
    """Same guard, the other role. A reviewer's detached tree reaches the refusal down a DIFFERENT
    branch of `_release_locked` (no `task/<id>` branch, so the unpushed-commits guard is skipped
    for the reachability one), and the lock has to be answered before either — a lock is a
    statement about the DIRECTORY, and git refuses removal regardless of what is checked out."""
    head = _git(repo, "rev-parse", "HEAD")
    path = _human_locked(repo, 7, role="review", reason="reviewer pinned this", at=head)

    res = release_workspace(7, role="review", cwd=repo)

    assert res["released"] is False and res["code"] == workspace_cmd.CODE_LOCKED
    assert res["role"] == "review"
    assert "reviewer pinned this" in res["reason"]
    assert path.is_dir()


def test_release_of_a_locked_entry_whose_directory_is_gone_is_a_verdict_not_a_crash(repo):
    """The state that pins the guard's PLACEMENT rather than its condition, and the one that used
    to fail loudest: a locked entry whose directory a human moved or deleted.

    `git worktree prune` REFUSES to drop a locked entry (measured, and the reason `list_worktrees`
    reads HEAD out of the porcelain instead of running git inside the tree), so `_find` still hands
    it back — and the very next line used to be `_git_inspect("status", cwd=path)`, whose
    `subprocess.run(cwd=<gone>)` raises a bare `FileNotFoundError` that `_git` cannot convert into
    a WorkspaceError. Verdict shape aside, that is a DIFFERENT mechanism from the lock refusal with
    the same root, and only a guard placed BEFORE the first git call with cwd inside the tree
    answers both. Move the lock guard below the dirty check and this test goes red while every
    other test in this section stays green."""
    path = _human_locked(repo, 44, reason="moved this aside")
    shutil.rmtree(path)

    res = release_workspace(44, cwd=repo)

    assert res["released"] is False and res["code"] == workspace_cmd.CODE_LOCKED
    assert not path.exists()
    # the entry is still registered, so a human still has something to unlock
    assert "task-44" in _git(repo, "worktree", "list", "--porcelain")


def test_run_workspace_release_of_a_locked_tree_stays_in_the_release_channel(
    repo, monkeypatch, capsys
):
    """End to end through the CLI, which is where the channel is actually observable — and the
    whole point of the card. Exit 0 and the release channel's EXACT key set, asserted as a whole
    for the reason `test_the_two_refusal_channels_are_not_interchangeable` gives: a shape that
    grows or loses a key silently is one SKILL.md's branch stops fitting."""
    path = _human_locked(repo, 42)
    monkeypatch.chdir(repo)

    assert run_workspace(["--release", "42"]) == 0, (
        "a locked tree is a NEGATIVE VERDICT, not a CLI failure: the command RAN and is protecting "
        "a tree a human pinned"
    )
    payload = json.loads(capsys.readouterr().out.strip())
    assert payload["released"] is False
    assert payload.get("code") == workspace_cmd.CODE_LOCKED
    assert set(payload) == {"released", "task_id", "role", "path", "code", "reason"}, (
        f"the RELEASE channel's key set moved to {sorted(payload)} for a locked tree"
    )
    assert path.is_dir()


def test_gc_reports_a_human_locked_tree_in_kept_and_keeps_sweeping(repo, tracker):
    """The unattended path: `--gc` meets this state on a tick, and it must produce ONE actionable
    `kept` line without costing the sweep its other verdicts.

    It already kept the tree before the fix — via the catch-all that turns any raise into
    `release-error` — so what changes here is WHICH code a human reads: `locked` names the state
    and the one command that clears it, where `release-error` handed them git's text and left the
    diagnosis to them."""
    api, wf = tracker
    locked = _human_locked(repo, 42)
    other = Path(ensure_workspace(43, cwd=repo)["path"])          # also dead, clean, pushed
    _quiesce(locked)
    _quiesce(other)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other.exists()
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [(42, workspace_cmd.CODE_LOCKED)]
    assert res["expected"] == []
    assert locked.is_dir()


def test_a_human_lock_stays_in_kept_even_when_the_board_would_excuse_it(repo, tracker):
    """THE GRADING DECISION, constructed rather than asserted against the frozensets.

    `_keep_is_expected` excuses a refusal only in two board states, and this tree is built to
    satisfy BOTH conjuncts at once — it is a REVIEW tree (what excuses `unreachable-head`) whose
    card is PARKED in Your Call (what excuses `dirty`/`unpushed`) — so the only thing that can send
    it to `kept` is the code itself.

    Why `kept` and not `expected`: `expected` is for states the pipeline produces on the happy path
    AND that already carry their own signal to the human (the parked card IS that signal, for
    hours, while `call_human` waits). A `git worktree lock` is neither — nothing on the board says
    a tree is pinned, and the lock makes it permanently unreapable until a human clears it, which
    is the shape of `half-created`, the code the policy calls correctly-never-routine. Add
    CODE_LOCKED to either `_EXPECTED_*` set and this goes red."""
    api, wf = tracker
    task = api.add_task("parked work", "Queue")
    task_id = task["id"]
    wf.claim(task_id)
    wf.call_human(task_id, "rebase conflict")                     # -> Your Call, i.e. parked
    assert task_id in set(wf.parked_task_ids())
    head = _git(repo, "rev-parse", "HEAD")
    locked = _human_locked(repo, task_id, role="review", reason="human is inspecting", at=head)
    _quiesce(locked)

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert res["expected"] == [], "a human lock was graded routine — nobody reads `expected`"
    assert [(k["task_id"], k["code"]) for k in res["kept"]] == [
        (task_id, workspace_cmd.CODE_LOCKED)
    ]
    assert locked.is_dir()


def test_a_locked_tree_reports_the_lock_even_when_it_is_also_dirty(repo, tracker):
    """THE ONE BEHAVIOUR CHANGE THIS CARD MADE BEYOND THE CHANNEL, found by an independent second
    pass over its own prose and measured on both sides rather than reasoned about.

    The lock guard sits ahead of the dirty/unpushed guards, so a tree that is locked AND dirty now
    reports `locked` where it used to report `dirty`. That is the right code — the lock is what
    makes the tree unremovable, and `dirty`'s recovery ("commit, push, retry") cannot work while it
    stands — but it also changes how `--gc` GRADES the tree when its card is parked in Your Call:
    `dirty`/`unpushed` are excused there (`expected`, the list nobody reads), a lock is not
    (`kept`). MEASURED on the pre-fix code with the same construction: `expected: [(42, "dirty")]`
    and `expected: [(42, "unpushed")]`; now `kept: [(42, "locked")]` for both.

    The direction is the safe one: the parked card excuses UNSAVED WORK because a human is coming
    back to it, and a lock is not unsaved work — nothing on that card says the tree cannot be
    reaped at all. Pinned so a future reordering cannot flip it back silently, into a list whose
    whole purpose is that it does not get read."""
    api, wf = tracker
    task = api.add_task("parked work", "Queue")
    task_id = task["id"]
    wf.claim(task_id)
    wf.call_human(task_id, "rebase conflict")                     # -> Your Call, i.e. parked
    path = Path(ensure_workspace(task_id, cwd=repo)["path"])
    (path / "scratch.txt").write_text("uncommitted\n")            # what `dirty` would answer
    _git(repo, "worktree", "lock", "--reason", "human says hands off", str(path))

    res = release_workspace(task_id, cwd=repo)
    assert res["released"] is False
    assert res["code"] == workspace_cmd.CODE_LOCKED, (
        "a dirty tree that is ALSO locked must report the lock: `dirty`'s recovery is to commit "
        "and retry, and no retry can release a locked tree"
    )

    _quiesce(path)
    swept = gc_workspaces(cwd=repo, workflow=wf)

    assert swept["expected"] == [], (
        "a locked tree was filed under `expected` because its card is parked — the parked card "
        "excuses unsaved work, not a lock nothing can reap"
    )
    assert [(k["task_id"], k["code"]) for k in swept["kept"]] == [
        (task_id, workspace_cmd.CODE_LOCKED)
    ]
    assert path.is_dir() and (path / "scratch.txt").exists()
