"""The WIP slot gate — how many tasks one token may hold in Design/Build at once.

wip_limit generalises the #38 single-WIP flag (enforce_single_wip == wip_limit 1) and is what
makes the parallel drain bounded: without it a pump could claim the whole Queue in one tick.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.config import DEFAULT_WIP_LIMIT
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


def _env(**kwargs):
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, **kwargs)


def _hold(api, wf, title):
    """Claim a fresh Queue task so it lands in Design and counts against the limit."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    return task


def test_the_unset_default_holds_three_and_refuses_the_fourth():
    """An unconfigured consumer gets THREE slots and a live gate — the human's decision of
    2026-07-30 (tracker #524), replacing the "unset = no gate at all" this test used to pin.

    This is where the number 3 is pinned BEHAVIOURALLY, and only here: the refusal itself has
    to say 3/3, so a fourth claim going through (or the count drifting) fails the test. Every
    other assertion about the default reads DEFAULT_WIP_LIMIT instead of repeating the literal."""
    api, wf = _env()
    for title in ("first", "second", "third"):
        _hold(api, wf, title)
    fourth = api.add_task("fourth", "Queue")
    with pytest.raises(WorkflowError, match=r"WIP limit reached \(3/3\)"):
        wf.claim(fourth["id"])


def test_the_unset_default_comes_from_the_shared_constant():
    """One definition of the number, two readers: workflow's fallback and config.py's < 1
    refusal. A second literal 3 hidden in _effective_wip_limit would drift away from the
    constant the config error advertises, so pin the identity rather than the value."""
    _api, wf = _env()
    assert wf._effective_wip_limit() == DEFAULT_WIP_LIMIT


def test_limit_two_allows_two_and_refuses_the_third():
    """An explicit number must stay the truth in BOTH directions — 2 is narrower than the
    default of 3, so this doubles as "the default is not a floor"."""
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


def test_wip_reports_the_default_when_the_toml_configures_nothing():
    """The payload the rulebook branches on must never say `null` again: an unconfigured
    consumer now reads limit/free as the default number, which is what tells the pump it may
    run a parallel drain without anyone editing a toml (tracker #524)."""
    _api, wf = _env()
    assert wf.next_task()["wip"] == {
        "active": 0, "limit": DEFAULT_WIP_LIMIT, "free": DEFAULT_WIP_LIMIT,
    }


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


# --- the `stage` payload invariant the rulebook's tick branches on ---

def test_every_task_bearing_next_task_result_carries_its_stage():
    """SKILL.md decides "claim or not" by `stage` (Queue -> claim, even when resume is true,
    because that finishes a partial claim; Design/Build -> already yours). That rule is only
    writable if EVERY branch that hands back a task says which stage it came from. Two branches
    used to omit it — the free queue and the review offer — and the free queue is the most common
    branch there is, so the rulebook's discriminator was missing exactly where it mattered and the
    rule got written wrong twice (rounds 2 and 3 of review).

    Scope, stated honestly: this walks the four task-bearing shapes that exist TODAY (free queue,
    stuck claim, active task, review offer) and fails if any of them drops `stage`. It is an
    enumeration, not a guarantee — a FIFTH branch added later without `stage` would not fail
    here, because nothing enforces the invariant structurally. Extend this test when you add a
    branch; that obligation is the whole point of keeping all four in one place."""
    api, wf = _env(wip_limit=3)

    free = api.add_task("free", "Queue")
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False and res["stage"] == "Queue", "free queue lost its stage"

    stuck = api.add_task("stuck claim", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["task"]["id"] == stuck["id"]
    assert res["resume"] is True and res["stage"] == "Queue", "stuck-in-Queue lost its stage"

    wf.claim(stuck["id"])                                  # now MY active task, in Design
    res = wf.next_task()
    assert res["task"]["id"] == stuck["id"]
    assert res["resume"] is True and res["stage"] == "Design", "the active task lost its stage"

    theirs = api.add_task("someone else's work", "Review")
    api.add_comment(theirs["id"], "[worklog] done")
    res = wf.next_task(exclude=[stuck["id"]])
    assert res["task"]["id"] == theirs["id"]
    assert res["review"] is True and res["stage"] == "Review", "the review offer lost its stage"


# --- liveness accessors: what workspace --gc asks the tracker ---

def test_active_task_ids_lists_my_design_and_build_tasks():
    api, wf = _env()
    first = _hold(api, wf, "designing")
    second = _hold(api, wf, "building")
    wf.advance(second["id"], to="build", spec="approach")
    api.add_task("someone else's queue item", "Queue")
    assert sorted(wf.active_task_ids()) == sorted([first["id"], second["id"]])


def test_review_task_ids_includes_cards_i_do_not_own():
    """A review tree is alive while the CARD is in Review — the reviewer is never its
    assignee, so keying this off ownership would reap a running reviewer's tree."""
    api, wf = _env()
    mine = _hold(api, wf, "mine")
    wf.advance(mine["id"], to="build", spec="approach")
    wf.advance(mine["id"], to="review", worklog="done", evidence="abc1234")
    theirs = api.add_task("theirs", "Review")
    assert sorted(wf.review_task_ids()) == sorted([mine["id"], theirs["id"]])


def test_parked_task_ids_lists_your_call_cards_and_nothing_else():
    """VMCP-68: NOT a liveness set — a parked card's tree is dead on purpose. `workspace --gc`
    reads it to GRADE its refusals: the same "unpushed commits" refusal is routine while a human
    still owes the card an answer, and an alarm anywhere else. So it must name exactly the Your
    Call column: an active task of mine and a card in Review must not leak into it."""
    api, wf = _env()
    parked = _hold(api, wf, "waiting on a human")
    wf.call_human(parked["id"], "which option do you want?")
    _hold(api, wf, "still mine, still active")
    api.add_task("under review", "Review")
    assert wf.parked_task_ids() == [parked["id"]]


def test_your_call_is_paged_exhaustively_on_the_liveness_board():
    """The truncation this set would otherwise die of: `require_titles` decides which buckets keep
    the pagination loop going, and a parked id left on an unread page reads as NOT parked — gc then
    grades a routine refusal as an alarm, quietly, and only on boards busy enough to fill a page.
    Squeeze the fake's page size to 1 (it mirrors the real client: non-required buckets are
    truncated to their first page) and require the SECOND parked card to still come back."""
    api, wf = _env()
    first = _hold(api, wf, "parked first")
    wf.call_human(first["id"], "?")
    second = _hold(api, wf, "parked second")
    wf.call_human(second["id"], "?")
    api.page_size = 1
    assert sorted(wf.parked_task_ids()) == sorted([first["id"], second["id"]])


def test_a_shared_liveness_board_serves_every_accessor_with_one_fetch():
    """Review finding (Important 4): gc_workspaces calls these accessors every tick — on
    FakeAPI's own view_tasks_calls counter, prove one liveness_board() fetch is enough for
    all of them, matching the #43 discipline next_task already follows for its own board reads.
    VMCP-68 added the third (parked_task_ids); it must ride the same fetch, since the whole read
    happens INSIDE the repo-wide flock every other agent's --release queues behind."""
    api, wf = _env()
    first = _hold(api, wf, "designing")
    parked = _hold(api, wf, "waiting on a human")
    wf.call_human(parked["id"], "which option?")
    api.add_task("under review", "Review")
    board = wf.liveness_board()
    calls_before = api.view_tasks_calls
    active = wf.active_task_ids(board=board)
    reviewing = wf.review_task_ids(board=board)
    waiting = wf.parked_task_ids(board=board)
    assert api.view_tasks_calls == calls_before          # NO extra fetch when a board is passed
    assert active == [first["id"]]
    assert len(reviewing) == 1
    assert waiting == [parked["id"]]
