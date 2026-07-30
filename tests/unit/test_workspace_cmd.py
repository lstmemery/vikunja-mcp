"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import json
import subprocess
from pathlib import Path

import pytest

from tests.unit.fakes import FakeAPI
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
    """A refusal is a NEGATIVE VERDICT, not a CLI failure: the command RAN, exit 0."""
    monkeypatch.chdir(repo)
    code = run_workspace(["--release", "999"])
    assert code == 0
    out = json.loads(capsys.readouterr().out.strip())
    assert out == {"released": False, "task_id": 999, "role": "build",
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
