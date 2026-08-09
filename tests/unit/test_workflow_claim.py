import re

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def test_next_task_empty_queue(env):
    api, wf = env
    assert wf.next_task()["task"] is None


def test_next_task_orders_queue_by_priority(env):
    api, wf = env
    api.add_task("low", "Queue", priority=1)
    top = api.add_task("high", "Queue", priority=5)
    api.add_task("backlog-idea", "Backlog", priority=10)   # не показывается
    res = wf.next_task()
    assert res["task"]["id"] == top["id"] and res["resume"] is False


def test_next_task_free_queue_task_carries_claim_note(env):
    """Свободная задача из Queue отдаётся с note: без него resume:false молчал и
    читался оркестратором как «делать нечего» — он стопался вместо claim→dispatch."""
    api, wf = env
    free = api.add_task("free", "Queue", priority=3)
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False
    assert res["note"] and "claim" in res["note"]  # инструкция thin-pump потоку, не молчание


def test_next_task_free_queue_note_overrides_steward_default(env):
    """Регресс к косяку в проекте-потребителе (DOGE): под generic-автолупом
    оркестратор счёл свежую задачу «не начинать новое без go-ahead» и остановил
    цикл. Note обязан явно перебить этот дефолт — claim свежей Queue-задачи это
    мандат (а не самовольная инициатива), и цикл под этим предлогом не стопаем."""
    api, wf = env
    api.add_task("free", "Queue", priority=3)
    note = wf.next_task()["note"]
    assert "mandate" in note      # взять свежую — мандат, не «самовольная инициатива»
    assert "stop" in note         # ...и НЕ останавливать /loop (собственно косяк DOGE)


def test_next_task_surfaces_readable_ref(env):
    """#82: the task next_task hands out carries a readable ref (identifier + id,
    "HGI-1 (107)"-shaped like the human's "VMCP-27 (82)"), not just the raw global id."""
    api, wf = env
    t = api.add_task("free", "Queue", priority=3)
    ref = wf.next_task()["task"]["ref"]
    assert ref == f"{api.tasks[t['id']]['identifier']} ({t['id']})"
    assert re.fullmatch(rf"HGI-\d+ \({t['id']}\)", ref)


def test_claim_surfaces_readable_ref(env):
    """claim echoes the same readable ref for the task it just moved to Design."""
    api, wf = env
    t = api.add_task("job", "Queue")
    ref = wf.claim(t["id"])["task"]["ref"]
    assert ref == f"{api.tasks[t['id']]['identifier']} ({t['id']})"
    assert re.fullmatch(rf"HGI-\d+ \({t['id']}\)", ref)


def test_next_task_requests_light_board_not_paging_done(env):
    """#43: next_task never reads Done/Backlog, so it fetches a LIGHT board — require_titles =
    the workflow stages it actually inspects — instead of forcing view_tasks to exhaustively
    page the unboundedly-growing Done on every call (the next_task-latency fix). The result is
    unchanged; only the fetch is cheaper. Assert the light board is requested and Done is NOT
    among the buckets whose full pages drive pagination."""
    api, wf = env
    free = api.add_task("free", "Queue", priority=1)
    res = wf.next_task()
    assert res["task"]["id"] == free["id"]                       # behavior unchanged
    req = api.last_require_titles
    assert req is not None                                       # a light board was requested…
    assert "Done" not in req and "Backlog" not in req           # …not forcing paging of Done/Backlog
    assert {"Queue", "Design", "Build", "Review"} <= set(req)   # the stages next_task reads


def test_next_task_skips_assigned_and_blocked(env):
    api, wf = env
    api.add_task("taken", "Queue", assignee={"id": 9, "username": "other"})
    api.add_task("stuck", "Queue", labels=("blocked",))
    free = api.add_task("free", "Queue")
    assert wf.next_task()["task"]["id"] == free["id"]


def test_next_task_prefers_my_active(env):
    api, wf = env
    api.add_task("queued", "Queue", priority=5)
    mine = api.add_task("in build", "Build", assignee=api.me_user)
    res = wf.next_task()
    assert res["task"]["id"] == mine["id"] and res["resume"] is True
    assert res["stage"] == "Build"
    assert "reconcile" in res["note"] and "verify" in res["note"]  # resume => сначала перепроверь


def test_next_task_resumes_stuck_claim_in_queue(env):
    """F2: клейм с не доведённым до конца move (assign ok, move failed) — задача моя,
    но всё ещё в Queue. next_task обязан её вернуть, а не молча пропустить."""
    api, wf = env
    stuck = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True
    assert res["stage"] == "Queue"
    assert res["task"]["id"] == stuck["id"]
    assert "claim" in res["note"]


def test_next_task_stuck_claim_outranks_higher_priority_free_task(env):
    """Возврат к своему недоклейменному таску важнее, даже если в очереди есть
    более приоритетная свободная задача — сначала долечи то, что уже на тебе."""
    api, wf = env
    api.add_task("free-and-shiny", "Queue", priority=10)
    stuck = api.add_task("half-claimed", "Queue", priority=1, assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == stuck["id"]


def test_next_task_active_stage_still_wins_over_stuck_queue(env):
    """Активная Design/Build задача (обычный resume) приоритетнее недоклейменной в Queue."""
    api, wf = env
    api.add_task("half-claimed", "Queue", assignee=api.me_user)
    active = api.add_task("in build", "Build", assignee=api.me_user)
    res = wf.next_task()
    assert res["resume"] is True and res["stage"] == "Build" and res["task"]["id"] == active["id"]


def test_claim_happy_path(env):
    api, wf = env
    t = api.add_task("job", "Queue")
    res = wf.claim(t["id"])
    assert res["claimed"] is True
    assert api.stage_of(t["id"]) == "Design"
    assert api.tasks[t["id"]]["assignees"][0]["username"] == "agent-infra"
    assert any(c.startswith("[claim]") for c in api.comments_text(t["id"]))


def test_claim_refuses_outside_queue(env):
    api, wf = env
    t = api.add_task("wip", "Build")
    with pytest.raises(WorkflowError, match="Queue"):
        wf.claim(t["id"])


def test_claim_refuses_already_assigned(env):
    api, wf = env
    t = api.add_task("taken", "Queue", assignee={"id": 9, "username": "other"})
    with pytest.raises(WorkflowError, match="other"):
        wf.claim(t["id"])


def test_claim_self_heals_when_sole_assignee_is_already_me(env):
    """F2: партиальный клейм (assign прошёл, move — нет) или человек руками вернул
    заклеймленную задачу в Queue. Повторный claim должен долечить, а не отказывать."""
    api, wf = env
    t = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.claim(t["id"])
    assert res["claimed"] is True
    assert api.stage_of(t["id"]) == "Design"
    assert [a["id"] for a in api.tasks[t["id"]]["assignees"]] == [api.me_user["id"]]
    assert any(c.startswith("[claim]") for c in api.comments_text(t["id"]))


def test_claim_does_not_self_heal_outside_queue(env):
    """Сам себе назначен, но задача не в Queue — обычный отказ, self-heal тут не при чём."""
    api, wf = env
    t = api.add_task("half-claimed-elsewhere", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError, match="Queue"):
        wf.claim(t["id"])


def test_claim_race_lost_backs_off(env):
    """Гонка: между нашим assign и verify появился второй assignee -> снять себя, отказ."""
    api, wf = env
    t = api.add_task("contested", "Queue")

    original_add = api.add_assignee

    def racing_add(task_id, user_id):
        original_add(task_id, user_id)
        original_add(task_id, 9)   # конкурент успел между assign и re-read

    api.add_assignee = racing_add
    with pytest.raises(WorkflowError, match="race"):
        wf.claim(t["id"])
    assert all(a["id"] != 2 for a in api.tasks[t["id"]]["assignees"])  # себя сняли
    assert api.stage_of(t["id"]) == "Queue"                            # не двигали


def test_claim_raises_when_assignee_vanishes_normal_path(env):
    """Vanish-window: между нашим assign и re-read человек снял назначение — fresh без
    assignees. others пуст, но двигать в Design без ассайни нельзя (невидимое состояние):
    задача осталась бы вне next_task (не моя активная) и вне Queue (никто не заклеймит)."""
    api, wf = env
    t = api.add_task("job", "Queue")

    original_get = api.get_task

    def vanishing_get(task_id):
        api.remove_assignee(task_id, api.me_user["id"])   # человек снял в окно перед re-read
        return original_get(task_id)

    api.get_task = vanishing_get
    with pytest.raises(WorkflowError, match="vanished"):
        wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Queue"                # не уехала в Design
    assert api.tasks[t["id"]]["assignees"] == []           # без ассайни, как в реальном vanish


def test_claim_raises_when_assignee_vanishes_self_heal_path(env):
    """Тот же vanish, но self-heal путь: задача предзаклеймлена на меня (add_assignee не
    звался). Окно между re-read и move то же — отказ обязан сработать и здесь."""
    api, wf = env
    t = api.add_task("half-claimed", "Queue", assignee=api.me_user)

    original_get = api.get_task

    def vanishing_get(task_id):
        api.remove_assignee(task_id, api.me_user["id"])
        return original_get(task_id)

    api.get_task = vanishing_get
    with pytest.raises(WorkflowError, match="vanished"):
        wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Queue"
    assert api.tasks[t["id"]]["assignees"] == []


# --- #38: optional single-WIP gate in claim (enforce_single_wip, default off) ---

def test_claim_refused_with_active_task_when_wip_gate_on():
    """Flag on + caller already has a Design/Build task -> claiming a new Queue task is
    refused, and the message NAMES the active task so the agent knows what to finish."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, enforce_single_wip=True)
    active = api.add_task("mine-in-build", "Build", assignee=api.me_user)
    free = api.add_task("tempting", "Queue")
    with pytest.raises(WorkflowError, match=rf"#{active['id']}") as exc:
        wf.claim(free["id"])
    assert "return" in str(exc.value).lower()               # tells you how to override
    assert api.stage_of(free["id"]) == "Queue"              # not moved
    assert api.tasks[free["id"]]["assignees"] == []         # not assigned — hard refusal


def test_claim_allowed_with_active_task_when_wip_gate_off():
    """Default (flag off): the gate is INERT — claim works even with an active task, so
    the rollout changes nothing for consumers that don't opt in."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)   # enforce_single_wip defaults False
    api.add_task("mine-in-build", "Build", assignee=api.me_user)
    free = api.add_task("second", "Queue")
    res = wf.claim(free["id"])
    assert res["claimed"] is True
    assert api.stage_of(free["id"]) == "Design"


def test_claim_allowed_without_active_task_when_wip_gate_on():
    """Flag on but caller has no active Design/Build task -> claim proceeds normally."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, enforce_single_wip=True)
    free = api.add_task("first", "Queue")
    res = wf.claim(free["id"])
    assert res["claimed"] is True
    assert api.stage_of(free["id"]) == "Design"


def test_claim_wip_gate_still_self_heals_stuck_queue_claim():
    """Flag on, but a task pre-assigned to me is still in Queue (a stuck claim, NOT an
    active Design/Build task). The gate keys off Design/Build only, so self-heal still
    works — finishing a half-done claim isn't starting a second task."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, enforce_single_wip=True)
    stuck = api.add_task("half-claimed", "Queue", assignee=api.me_user)
    res = wf.claim(stuck["id"])
    assert res["claimed"] is True
    assert api.stage_of(stuck["id"]) == "Design"


# --- #885: the kanban copy of a task can arrive WITHOUT its assignees ----------------------
# Measured on the live tracker (project 10, 2026-08-06): claim(854) returned {"claimed": true},
# GET /api/v1/tasks/854 answered assignees=[(7, 'agent-vikunja-mcp')] and GET /api/v1/user
# answered id 7 — yet the copy of the same card on the KANBAN BOARD, which is what every
# ownership gate reads, came back with assignees=[]. Six advance() attempts over ~40 minutes all
# refused, and so did call_human/return_task/decompose: no tool could MOVE the card or make it
# anyone's, and the agent could not even ask a human ABOUT IT. Not "workable by no tool", though —
# get_task, comment, attach_file and file_task need no ownership and all still work on it
# (measured), which is what leaves the file_task workaround reachable.
#
# It does NOT occupy a WIP slot while it sits there — it LOSES one, and this header asserted the
# opposite for a round. `_my_active_tasks` reads the same board copy, so the card is invisible to
# the counter: measured at wip_limit = 3 with three cards claimed and ONE blacked out, the gate
# reads {'active': 2, 'limit': 3, 'free': 1} while three really are assigned, and a FOURTH claim
# is then ACCEPTED — four against a limit of three, where the healthy control refuses it with
# "WIP limit reached (3/3)". In Design/Build next_task does not offer such a card back either
# (task=None, against task=<id>/resume=True on that control), so a dead agent on one is not
# replaced automatically. That does NOT generalise to "never offered": in REVIEW the same blackout
# INVERTS it — the review branch skips cards the board copy calls mine, so a blacked-out card in
# Review is offered to its own AUTHOR (review=True) where the healthy control answers task=None.
# All of it is PRE-EXISTING and out of this card's scope; what was wrong was only the description.
#
# Rare and DURABLE, both measured the same day: exactly one of the 31 cards outside Done
# diverged, and re-assigning, moving columns and a full read-modify-write POST /tasks/854 each
# left the board copy empty. So the fix is two halves — recovery in the gates, and a claim that
# stops reporting silent success about a read it never made.


def test_claim_reports_when_the_kanban_copy_lost_the_assignee(env):
    """#885, half (b) — PREVENTION, in the sense of "stop being silent", not of refusing.

    claim's own assign-then-verify reads `GET /tasks/<id>`; every later ownership gate reads the
    KANBAN copy. Those are two different reads and #885 measured them disagreeing, so claim used
    to report plain success about a state it had never checked — that silence is what let a rare
    server quirk become a dead card. It now asks the second question with the SAME read the gates
    use and names the divergence in its own payload.

    It REPORTS rather than refuses on purpose: nothing on this side can repair the server's copy,
    so a refusal would make the card unclaimable forever. The other half (below) is what makes
    reporting sufficient. Delete the `kanban_assignee_divergence` block from claim and this test
    goes RED while every other claim test stays green — the payload key is the only witness.

    MUTATION SWEEP for the whole #885 section, selection tests/unit/test_workflow_claim.py, caches
    cleared and PYTHONDONTWRITEBYTECODE=1, every round collecting the same 31 items as the control
    and read by counting `FAILED `/`ERROR ` lines rather than the first `N failed` in stdout:
    control 0 failed, 0 errors. Recovery reverted so `_require_mine` judges the board copy again
    -> 3 failed. claim's divergence block deleted -> 1 failed. The divergence key made
    unconditional -> 2 failed. The re-read made unconditional -> 1 failed. The verification's
    `except` emptied so a failed board read propagates -> 1 failed. The re-read's `except` emptied
    so a failed `/tasks/<id>` propagates -> 1 failed. No round scored zero, so every half of this
    section is pinned by something. Re-run WHOLE at round two, after the RECOVER test below grew
    from three driven forms to five, this time with a control at BOTH ends: control 0 failed /
    0 errors at each end, the same six counts between them and the same 31 collected in every
    round, so widening that body moved nothing and this record needed no new numbers."""
    api, wf = env
    t = api.add_task("diverging card", "Queue")
    api.kanban_assignee_blackout.add(t["id"])
    res = wf.claim(t["id"])
    # the claim itself really did succeed — the task carries the assignee, only the board
    # copy does not
    assert res["claimed"] is True and api.stage_of(t["id"]) == "Design"
    assert api.me_user["id"] in [a["id"] for a in api.tasks[t["id"]]["assignees"]]
    note = res.get("kanban_assignee_divergence")
    assert note, f"claim reported success about a read it never made: {res}"
    assert "KANBAN" in note and str(t["id"]) in note
    assert "re-create the card" in note, f"the known workaround is not named: {note}"


def test_a_healthy_claim_carries_NO_divergence_key(env):
    """The counterpart, and the reason the key is worth reading: it appears ONLY on the anomaly.
    A field present on every result is the never-read signal #516 had to split `kept` in two to
    cure. Drop the `if divergence:` guard and this goes RED."""
    api, wf = env
    t = api.add_task("ordinary card", "Queue")
    res = wf.claim(t["id"])
    assert res["claimed"] is True
    assert "kanban_assignee_divergence" not in res, res


def test_the_divergence_check_never_fails_the_claim_it_verifies(env):
    """Best-effort by construction: a verification that can fail the claim is worse than none.
    Here the board read itself blows up AFTER the card has been moved and commented — the claim
    must still be reported as the success it is. Remove the `except` and this goes RED with the
    raised error instead of a claim result."""
    api, wf = env
    t = api.add_task("verification explodes", "Queue")
    calls = {"n": 0}
    real_view_tasks = api.view_tasks

    def flaky(*a, **kw):
        calls["n"] += 1
        if calls["n"] > 1:           # the FIRST board read is claim's own; this is the later one
            raise VikunjaError(500, "board read failed")
        return real_view_tasks(*a, **kw)

    api.view_tasks = flaky
    res = wf.claim(t["id"])
    assert res["claimed"] is True and api.stage_of(t["id"]) == "Design"
    assert "kanban_assignee_divergence" not in res, res


def test_ownership_gates_RECOVER_a_card_whose_board_copy_lost_its_assignee(env):
    """#885, half (a) — RECOVERY, and the half that actually unsticks the card.

    This is the live sequence: claim succeeds, then every ownership-gated tool refuses because
    `_require_mine` judged by the board copy. All FIVE ownership-gated forms are driven here, not
    argued about, because the card's cost was precisely that ALL of them refused at once — one
    passing tool would have left a way out. Five is the whole surface, not a sample: four tools
    call `_require_mine`, and `advance` reaches it in two of its three named forms — `review_task`
    needs no ownership by design, `to='done'` raises "only a human moves a task to Done" before
    the gate, and `to='design'` is not a transition at all.

    They do NOT all fit on one card, and that is the reason for the three below rather than a
    contrived ordering: `advance(to='review')` and `decompose` each END a card's usable life here
    — in Review, and in Backlog as an epic — so each gets a card of its own. An earlier round
    drove only the first three while this docstring claimed five, and named a "first loop" the
    body has never had.

    Revert `_require_mine` to `self._assignee_ids(task)` and this test goes RED at the FIRST of
    the five — asserts are sequential, so that one mutation shows you one red, not five. The
    property really is all five: driven independently under that same mutant, every one of them
    refuses with "not assigned to you" (measured). An earlier round wrote "every one of the five
    goes RED", which is the same over-read of one's own body this docstring already apologises
    for."""
    api, wf = env
    t = api.add_task("stuck between claim and advance", "Queue")
    api.kanban_assignee_blackout.add(t["id"])
    wf.claim(t["id"])
    assert api.stage_of(t["id"]) == "Design"

    # the tools that were dead on the live card
    assert wf.advance(t["id"], to="build", spec="approach")["moved_to"] == "Build"
    assert wf.call_human(t["id"], "a question")["moved_to"] == "Your Call"
    api.task_bucket[t["id"]] = api.bucket_id("Build")      # the human answers, card comes back
    assert wf.return_task(t["id"], reason="external blocker")["moved_to"] == "Backlog"

    # ...and the two forms that cannot follow those on the same card
    to_review = api.add_task("blacked out, taken to Review", "Queue")
    api.kanban_assignee_blackout.add(to_review["id"])
    wf.claim(to_review["id"])
    wf.advance(to_review["id"], to="build", spec="approach")
    assert wf.advance(
        to_review["id"], to="review", worklog="what was done", evidence="deadbeef",
    )["moved_to"] == "Review"

    to_split = api.add_task("blacked out, decomposed", "Queue")
    api.kanban_assignee_blackout.add(to_split["id"])
    wf.claim(to_split["id"])
    assert wf.decompose(
        to_split["id"], [{"title": "A"}, {"title": "B"}],
    )["parent"]["moved_to"] == "Backlog"


def test_the_recovery_re_read_happens_ONLY_on_an_empty_assignee_list(env):
    """The condition the human's answer named explicitly: re-read when the board copy is EMPTY,
    not always. The shape is one card in 31; a gate that re-read unconditionally would pay a GET
    per ownership check for it. Widen the guard to re-read always and the second half goes RED."""
    api, wf = env
    mine = api.add_task("mine", "Design", assignee=api.me_user)
    reads = []
    real_get_task = api.get_task
    api.get_task = lambda tid: (reads.append(tid), real_get_task(tid))[1]

    task, stage = wf._find_task(mine["id"])
    wf._require_mine(task, stage)                       # passes, and must not have re-read
    assert reads == [], f"a healthy card paid for a re-read it did not need: {reads}"

    # ...while the empty list — and only it — reaches `/tasks/<id>`
    orphan = api.add_task("no assignee", "Design")
    task, stage = wf._find_task(orphan["id"])
    with pytest.raises(WorkflowError):
        wf._require_mine(task, stage)
    assert reads == [orphan["id"]], reads


def test_a_board_copy_that_lost_SOMEBODY_ELSES_assignee_is_not_read_as_ownerless(env):
    """The interaction with #705/#734, which is not obvious and is wrong in the tempting
    direction. Their clause fires on "no assignee AT ALL" and tells the agent a human hand-placed
    an ownerless card. A card whose kanban copy merely LOST its assignees is not that card: the
    exit advice would name a hand-placement that never happened. Because the clause now asks the
    RE-READ list, such a card gets the OWNED refusal — the accurate diagnosis, which since #742
    also carries the foreign-card clause outside Queue. What is asserted here is the routing and
    not the wording (the byte-for-byte pins on both texts live in `test_workflow_gates.py`), so
    the two asserts below stay exactly as narrow as the thing this test is about.

    Keep `_require_mine` deciding the clause from the board copy while re-reading only for the
    ownership verdict and this goes RED."""
    api, wf = env
    theirs = api.add_task("their work", "Design", assignee={"id": 99, "username": "someone-else"})
    api.kanban_assignee_blackout.add(theirs["id"])
    with pytest.raises(WorkflowError) as exc:
        wf.advance(theirs["id"], to="build", spec="s")
    msg = str(exc.value)
    assert "not assigned to you" in msg
    assert "NO assignee at all" not in msg, f"a card with an owner got the ownerless exit: {msg}"


def test_a_failed_re_read_falls_back_to_the_refusal_it_would_have_given(env):
    """Best-effort, the other side: a diagnostic must not break its own gate. When `/tasks/<id>`
    cannot be read the ownership check answers exactly as it did before #885 — an ownership
    refusal, not a network error. Remove the `except` and this goes RED."""
    api, wf = env
    orphan = api.add_task("really ownerless", "Design")

    def boom(task_id):
        raise VikunjaError(503, "tracker unavailable")

    api.get_task = boom
    with pytest.raises(WorkflowError) as exc:
        wf.advance(orphan["id"], to="build", spec="s")
    assert "not assigned to you" in str(exc.value)
