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


# --- next_task in parallel mode: exclude + slot accounting ---

def test_next_task_reports_wip_on_every_result():
    api, wf = _env(wip_limit=2)
    api.add_task("free", "Queue")
    res = wf.next_task()
    assert res["wip"] == {"active": 0, "limit": 2, "free": 2}


def test_wip_free_is_none_when_unlimited():
    api, wf = _env()
    assert wf.next_task()["wip"] == {"active": 0, "limit": None, "free": None}


def test_excluded_active_task_is_not_offered_again():
    """The orchestrator already has a live agent on it; re-offering would dispatch a second
    agent onto the same task. Liveness is a fact of the harness, so the CALLER states it."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "in flight")
    free = api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False


def test_excluded_task_still_occupies_its_slot():
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"] is None
    assert res["wip_saturated"] is True
    assert res["wip"] == {"active": 1, "limit": 1, "free": 0}


def test_empty_exclude_still_hands_back_the_active_task():
    """A killed turn loses the in-flight set. The next tick passes nothing, and abandoned
    work must surface as resume — this is the crash-recovery path, not a regression."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "abandoned")
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == held["id"]


def test_saturation_does_not_suppress_a_review_offer():
    """Background review is not 'your active task' and consumes no slot (SKILL.md rule)."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    other = api.add_task("someone else's work", "Review")
    api.add_comment(other["id"], "[worklog] done")
    res = wf.next_task(exclude=[held["id"]])
    assert res["review"] is True and res["task"]["id"] == other["id"]


def test_saturated_result_is_not_the_empty_queue():
    """The pump idles on an empty queue; it must WAIT (not sleep) when merely saturated."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res.get("wip_saturated") is True
    assert "empty" not in res["message"]


def test_excluded_review_task_is_not_offered_for_review():
    """review_task never assigns the reviewer to the reviewed task, so the pre-existing
    'my_id in assignees' self-review guard does NOT catch a task one of the pump's own
    live sub-agents is already reviewing — exclude is the ONLY thing standing between this
    board and a second agent dispatched onto the same review."""
    api, wf = _env()
    other = api.add_task("someone else's work", "Review")
    api.add_comment(other["id"], "[worklog] done")
    res = wf.next_task(exclude=[other["id"]])
    assert res["task"] is None
    assert "review" not in res


def test_excluded_stuck_queue_task_is_not_handed_back():
    """An unfinished claim (assigned to me, still sitting in Queue) that another live
    sub-agent is already finishing must not be handed back as a second 'call claim' — the
    same slot, dispatched twice."""
    api, wf = _env()
    stuck = api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task(exclude=[stuck["id"]])
    assert res["task"] is None
    assert "resume" not in res
