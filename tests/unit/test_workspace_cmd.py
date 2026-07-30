"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import json
import subprocess
from pathlib import Path

import pytest

from vikunja_mcp.config import ENV_WORKTREE_ROOT
from vikunja_mcp.workspace_cmd import (
    WorkspaceError,
    ensure_workspace,
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

def test_release_of_a_review_tree_ignores_the_unpushed_guard(repo):
    """A review tree is DETACHED and holds no branch of its own — nothing is lost by
    removing it, so the unpushed-commits guard (which protects a task/<id> BRANCH's unique
    history) must not apply. Without this, a review pinned at a build branch's tip — which
    is BY DEFINITION not yet on origin/main — could never be released."""
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


def test_run_workspace_with_no_args_is_one_json_error_line_exit_1(capsys):
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
