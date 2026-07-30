"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import fcntl
import json
import os
import subprocess
import time
from pathlib import Path

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import workspace_cmd
from vikunja_mcp.config import ENV_WORKTREE_ROOT
from vikunja_mcp.workflow import STAGES, Workflow
from vikunja_mcp.workspace_cmd import (
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


@pytest.fixture
def repo(tmp_path, monkeypatch):
    """A work repo on `main` with a local bare origin it has already pushed to."""
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
    even a "nothing to release" verdict must be actionable, not just a bare task id."""
    monkeypatch.chdir(repo)
    code = run_workspace(["--release", "999"])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"released": False, "task_id": 999, "role": "build",
                   "path": str(repo.parent / "work.worktrees" / "task-999"),
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


def test_gc_reaps_a_tree_whose_task_is_no_longer_active(repo, tracker):
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
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


def test_gc_never_reaps_unpushed_work_and_reports_it(repo, tracker):
    """The orphan of a crashed agent that got as far as committing: dead on the board, but
    its commits are the whole reason we keep it. GC must REPORT, not destroy."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "crashed mid-task")
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
    merely 'a red test'), and must not abort the sweep before reaping the OTHER dead tree."""
    api, wf = tracker
    self_path = Path(ensure_workspace(42, cwd=repo)["path"])     # dead, and cwd is INSIDE it
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead, different tree

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
    already decided for the OTHERS. `git worktree lock` gives a REAL, non-contrived
    WorkspaceError (git refuses to remove a locked tree without --force) on an otherwise
    dead, clean, pushed tree — not a mocked failure standing in for an untested branch."""
    api, wf = tracker
    locked_path = Path(ensure_workspace(42, cwd=repo)["path"])   # dead, clean, pushed
    _git(repo, "worktree", "lock", str(locked_path))
    other_path = Path(ensure_workspace(43, cwd=repo)["path"])    # also dead

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [43]
    assert not other_path.exists()
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "WorkspaceError" in res["kept"][0]["reason"]
    assert locked_path.exists()


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

    gc_workspaces(cwd=repo, workflow=ProbingWorkflow())


def test_run_workspace_gc_dispatches_to_gc_workspaces(monkeypatch, capsys, tmp_path):
    """Important 6: Task 3 established run_workspace's dispatch as a TESTED contract; --gc
    must not be the one branch that only ever ran by hand.

    Round 2 hygiene: chdir into tmp_path even though gc_workspaces is stubbed here — the house
    negative-pin rule means someone WILL delete that stub one day to prove it bites, and at
    that moment "safe because gc_workspaces never really runs" stops being true. The isolation
    must be structural (an inert cwd), not incidental (a mock that happens to intercept it)."""
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr(
        "vikunja_mcp.workspace_cmd.gc_workspaces", lambda: {"released": [], "kept": []}
    )
    code = run_workspace(["--gc"])
    assert code == 0
    assert json.loads(capsys.readouterr().out.strip()) == {"released": [], "kept": []}


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
    wf = _build_workflow(repo)
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

    res = gc_workspaces(cwd=repo, workflow=wf)

    assert [r["task_id"] for r in res["released"]] == [task["id"]]
    assert not path.exists()


def test_gc_reaps_a_build_tree_once_its_task_reaches_review(repo, tracker):
    """Minor: the everyday build-side reap — the agent finished and advanced its OWN task to
    Review. The BUILD tree is now dead and must be reaped; it must not be kept just because
    the task still exists somewhere on the board."""
    api, wf = tracker
    task = api.add_task("moved to review", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    wf.advance(task["id"], to="build", spec="approach")
    wf.advance(task["id"], to="review", worklog="done", evidence="abc1234")

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


def test_git_runs_with_terminal_prompts_disabled_and_keeps_the_callers_transport(repo, tmp_path):
    """An https remote with no credential helper prompts on the terminal and waits forever.
    Proven by making git launch a stand-in for ssh that dumps the environment git handed it —
    which also pins the other half: a GIT_SSH_COMMAND the CALLER set must survive untouched (an
    injected BatchMode default would override a configured `core.sshCommand` identity)."""
    dump = tmp_path / "git-env.txt"
    fake_ssh = tmp_path / "fake-ssh.sh"
    fake_ssh.write_text(f'#!/bin/sh\nenv > "{dump}"\nexit 1\n')
    fake_ssh.chmod(0o755)
    os.environ["GIT_SSH_COMMAND"] = str(fake_ssh)
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")
    try:
        with pytest.raises(WorkspaceError, match="failed"):
            workspace_cmd._git("fetch", "origin", cwd=repo)
    finally:
        del os.environ["GIT_SSH_COMMAND"]

    seen = dict(
        line.split("=", 1) for line in dump.read_text().splitlines() if "=" in line
    )
    assert seen["GIT_TERMINAL_PROMPT"] == "0"
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
    sleeps 30s, so a call site on the wrong constant fails this on the elapsed time."""
    slow_ssh = tmp_path / "slow-ssh.sh"
    slow_ssh.write_text("#!/bin/sh\nsleep 30\n")
    slow_ssh.chmod(0o755)
    monkeypatch.setenv("GIT_SSH_COMMAND", str(slow_ssh))
    monkeypatch.setattr(workspace_cmd, "_GIT_NET_TIMEOUT", 2.0)
    _git(repo, "remote", "set-url", "origin", "ssh://git@127.0.0.1/nowhere.git")

    started = time.monotonic()
    with pytest.raises(WorkspaceError, match="fetch origin timed out after 2s"):
        ensure_workspace(42, cwd=repo)
    assert time.monotonic() - started < 15        # not the 30s sleep, not the 600s ceiling


def test_a_local_git_call_keeps_the_generous_ceiling(repo):
    """The other direction of the two-bound split: killing a `worktree add` mid-checkout is
    destructive (git registers a "locked / initializing" entry BEFORE checking out, which
    `prune` will not drop and `_find` hands back as `created: false`), so local calls must NOT
    inherit the network bound — a big checkout on a slow disk is slow, not hung."""
    assert workspace_cmd._GIT_TIMEOUT >= 600
    assert workspace_cmd._GIT_NET_TIMEOUT < workspace_cmd._GIT_TIMEOUT
    ensure_workspace(42, cwd=repo)                # the real create path still works end to end


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
    wf = _build_workflow(repo)

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


def test_argparse_own_errors_still_exit_rather_than_print_json(monkeypatch, tmp_path):
    """Minor 10: `except SystemExit: raise` was dead code (SystemExit is a BaseException, so the
    `except Exception` below never caught it). Removing it changes nothing — pinned here so the
    next reader does not have to re-derive that."""
    monkeypatch.chdir(tmp_path)
    with pytest.raises(SystemExit):
        run_workspace(["42", "--role", "bogus"])


# --- final whole-branch review, Minor 9: a broken config must surface, not relocate trees ---

def test_a_malformed_repo_toml_is_not_swallowed_into_the_default_root(repo):
    """`except Exception` around load_config treated "this toml is broken" exactly like "there
    is no tracker config here" — and silently put the tree in the default sibling directory,
    where a `worktree_root` the human meant to configure would never be looked for again."""
    (repo / ".vikunja-mcp.toml").write_text("[tracker\nurl = 'oops'\n")
    with pytest.raises(Exception, match="[Ee]xpected"):        # tomllib.TOMLDecodeError
        worktree_root(repo)


def test_a_repo_with_no_tracker_config_still_falls_back_silently(repo):
    """The other direction, and the reason the try/except exists at all: create and release need
    no tracker config whatsoever, so ConfigError alone must stay swallowed."""
    assert worktree_root(repo) == repo.parent / "work.worktrees"
