"""The WIP slot gate — how many tasks one token may hold in Design/Build at once.

wip_limit generalises the #38 single-WIP flag (enforce_single_wip == wip_limit 1) and is what
makes the parallel drain bounded: without it a pump could claim the whole Queue in one tick.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


def _env(**kwargs):
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, **kwargs)


def _hold(api, wf, title):
    """Claim a fresh Queue task so it lands in Design and counts against the limit."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    return task


def test_no_limit_by_default_lets_a_second_claim_through():
    """Ships inert: an unconfigured consumer keeps today's unbounded behavior."""
    api, wf = _env()
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


def test_limit_two_allows_two_and_refuses_the_third():
    api, wf = _env(wip_limit=2)
    _hold(api, wf, "first")
    _hold(api, wf, "second")
    third = api.add_task("third", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(third["id"])


def test_limit_one_is_the_legacy_single_wip_behaviour():
    api, wf = _env(wip_limit=1)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_wip_limit_wins_over_enforce_single_wip():
    """Both set -> the number is the truth; the legacy flag must not clamp it back to 1."""
    api, wf = _env(enforce_single_wip=True, wip_limit=2)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


def test_legacy_flag_alone_still_means_one():
    api, wf = _env(enforce_single_wip=True)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_a_freed_slot_is_reusable():
    """advance to Review takes the task out of Design/Build, so the slot comes back."""
    api, wf = _env(wip_limit=1)
    first = _hold(api, wf, "first")
    wf.advance(first["id"], to="build", spec="do the thing")
    wf.advance(first["id"], to="review", worklog="did the thing", evidence="abc1234")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True
