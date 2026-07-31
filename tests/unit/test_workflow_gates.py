import os
import time

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.formatting import html_to_text
from vikunja_mcp.workflow import (
    _ATTACHMENT_TTL,
    _MAX_ATTACHMENT_NAME_BYTES,
    STAGES,
    Workflow,
    WorkflowError,
    _human_size,
    _safe_attachment_name,
)


@pytest.fixture
def env():
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = api.add_task("job", "Design", assignee=api.me_user)
    return api, wf, task


def test_advance_to_done_is_forbidden(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="human"):
        wf.advance(t["id"], to="done")


def test_advance_unknown_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="invalid"):
        wf.advance(t["id"], to="review2")


def test_advance_build_requires_spec(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="spec"):
        wf.advance(t["id"], to="build", spec="   ")
    wf.advance(t["id"], to="build", spec="сделаю X через Y")
    assert api.stage_of(t["id"]) == "Build"
    assert any(c.startswith("[spec]") for c in api.comments_text(t["id"]))


def test_advance_review_requires_worklog_and_evidence(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    with pytest.raises(WorkflowError, match="worklog"):
        wf.advance(t["id"], to="review", worklog="сделано")
    wf.advance(t["id"], to="review", worklog="сделано", evidence="commit abc123")
    assert api.stage_of(t["id"]) == "Review"
    joined = "\n".join(api.comments_text(t["id"]))
    assert "[worklog]" in joined and "commit abc123" in joined


def test_advance_review_report_includes_root_cause(env):
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(
        t["id"], to="review",
        worklog="починил рендер титула", evidence="commit deadbeef",
        root_cause="стейт лобби не подписан на смену экипировки",
    )
    report = next(c for c in api.comments_text(t["id"]) if c.startswith("[worklog]"))
    assert "Причина: стейт лобби не подписан" in report
    assert "Сделано: починил рендер титула" in report
    assert "Evidence: commit deadbeef" in report


def test_advance_wrong_source_stage(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="Build"):
        wf.advance(t["id"], to="review", worklog="w", evidence="e")  # задача ещё в Design


def test_advance_requires_ownership(env):
    api, wf, t = env
    api.tasks[t["id"]]["assignees"] = [{"id": 9, "username": "other"}]
    with pytest.raises(WorkflowError, match="claim"):
        wf.advance(t["id"], to="build", spec="s")


def test_call_human_keeps_assignee(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="question"):
        wf.call_human(t["id"], question="")
    wf.call_human(t["id"], question="какой из двух вариантов деплоя выбрать?")
    assert api.stage_of(t["id"]) == "Your Call"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[нужен человек]") for c in api.comments_text(t["id"]))


def test_return_task_unassigns_labels_and_moves_to_backlog(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="reason"):
        wf.return_task(t["id"], reason="")
    wf.return_task(t["id"], reason="нужен доступ к prod-базе")
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "blocked" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[blocked]") for c in api.comments_text(t["id"]))


def test_return_task_refuses_from_review_and_still_works_everywhere_else(env):
    """#590: `return_task` had NO stage gate at all — measured through the real `Workflow` over a
    FakeAPI board (not a live tracker), a card in Review passed with NO refusal and walked to
    Backlog, unassigned and labeled `blocked`. That is the door a reviewer reaches
    for once `call_human` refuses them (it is gated to Design/Build), and it quietly evicts someone
    else's finished, reviewed work from the pipeline for human re-triage.

    The gate is Review-ONLY and sits BEFORE `_require_mine`, so the multi-identity reviewer — whose
    card is the IMPLEMENTER's — reads the same stage refusal instead of "claim it first", advice
    that would be actively wrong (you never claim work you are reviewing).

    #627: that ORDER sentence used to live in this docstring as prose only — every card built here
    was `assignee=api.me_user`, so the "in Review AND not mine" cell no one asserted was the one
    cell the order decides. Measured on this tree: inverting the order (`_require_mine` first) left
    all three pins of #590 green AND the whole unit suite green (712 passed). The multi-identity
    branch below is what closes that, mirroring the sibling assertion already in the `call_human`
    test — the two tools share a ruling, so they must share a pin.

    All four parts below are load-bearing. The refusal must leave the BOARD untouched: a guard that
    raises after the comment/label/unassign already landed is not a guard. The CONTROL sweep is
    what makes the "must NOT happen" half mean anything — without it the first assertion stays
    green for a `return_task` that raises unconditionally, which is not the ruling: every stage
    except Review keeps working, because returning a half-claimed Queue/Design/Build card really is
    the "externally blocked" case this tool exists for. And the OWNERSHIP control is what makes the
    order assertion mean anything: `"not assigned to you" not in msg` names a guard it does not
    exercise, so on its own it would stay green for a `return_task` with no `_require_mine` AT ALL
    — measured, deleting that guard also left the whole suite green (712 passed). Together the two
    say what one cannot: BOTH guards are live, and in their intersection STAGE wins.

    MUTATION-CHECKED: control PASS; delete the Review gate -> FAIL (the card walks to Backlog);
    invert the order (`_require_mine` first) -> FAIL on the multi-identity branch; delete
    `_require_mine` -> FAIL on the ownership control."""
    api, wf, _t = env
    reviewed = api.add_task("someone else's finished work", "Review", assignee=api.me_user)

    with pytest.raises(WorkflowError) as excinfo:
        wf.return_task(reviewed["id"], reason="я не понимаю, чего от меня хотят")
    msg = str(excinfo.value)
    assert "review_task" in msg and "needs_work" in msg, \
        f"the refusal must name the reviewer's actual channel, not just say no: {msg}"

    # nothing happened: the gate fires BEFORE the comment / label / unassign / move
    assert api.stage_of(reviewed["id"]) == "Review"
    assert api.tasks[reviewed["id"]]["assignees"], "the refused return still unassigned the card"
    assert not any(lb["title"] == "blocked" for lb in api.tasks[reviewed["id"]]["labels"])
    assert not any(c.startswith("[blocked]") for c in api.comments_text(reviewed["id"]))

    # multi-identity: the card in Review is the IMPLEMENTER's. THIS is the cell that pins the
    # check ORDER — under the inverted order the reviewer reads "claim it first" and never hears
    # about the stage or about the channel that actually works.
    theirs = api.add_task("someone else's card in review", "Review")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.return_task(theirs["id"], reason="я не понимаю, чего от меня хотят")
    multi_msg = str(multi.value)
    assert "review_task" in multi_msg and "needs_work" in multi_msg, \
        f"the reviewer must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is the wrong advice for a reviewer: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Review"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still there and still runs — from an OPEN stage, where the Review gate
    # cannot mask it, someone else's card is still refused BY OWNERSHIP.
    not_mine = api.add_task("someone else's card in Design", "Design")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.return_task(not_mine["id"], reason="чужой сервис лежит")
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards return_task from an open stage: {owned.value}"
    assert api.stage_of(not_mine["id"]) == "Design"
    assert api.tasks[not_mine["id"]]["assignees"][0]["id"] == 77

    # CONTROL: every stage I deliberately left open still returns the card
    for stage in ("Design", "Build", "Queue", "Backlog", "Your Call"):
        open_task = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        wf.return_task(open_task["id"], reason="чужой сервис лежит")
        assert api.stage_of(open_task["id"]) == "Backlog", f"return_task broke from {stage}"
        assert api.tasks[open_task["id"]]["assignees"] == []
        assert any(lb["title"] == "blocked" for lb in api.tasks[open_task["id"]]["labels"])


def test_return_task_refuses_from_done_the_human_only_transition_run_backwards(env):
    """#626: `return_task` was ONE OF SEVERAL agent tools that moved a card OUT of Done — never
    the only one, and this test does not pin that it is. Measured through the real `Workflow` over
    a FakeAPI board, on a card driven the NORMAL way (Queue -> claim -> Design -> Build ->
    Review -> approve -> a human moves it to Done): it did not refuse, answered
    {"moved_to": "Backlog", "labeled": "blocked"}, and left the card in Backlog with NO assignee
    and BOTH labels — `reviewed` and `blocked` — the board claiming "approved" and "blocked" at
    once. On that same card advance (build/review/done), call_human, claim and review_task (both
    verdicts) all refuse — the transition CLAUDE.md calls human-only, run BACKWARDS, and an
    invariant that holds in only one direction is not an invariant.

    Shutting this door does not shut them all: human-only Done is nowhere expressed as one rule,
    so any tool that moves a card without checking its stage reproduces the hole. `decompose`
    measurably did — on the same card it walked the parent to Backlog with `epic`, and THIS diff
    did not touch it — and was gated separately by #649, which closed the last instance known then
    without closing the class. An earlier draft of this docstring opened by calling
    return_task "the ONE agent tool that moves a Done card"; probing the tools the first repro had
    skipped disproved it, and the opening sentence above is the corrected one.

    Same FORM as the Review gate (#590): a stage check BEFORE `_require_mine`, with a refusal that
    names the channel that does work. Done is not "stuck", so the door it points at is `file_task`
    (a follow-up card for a human to triage) — `call_human` refuses from Done too, and only a human
    can move THIS card back.

    Not a regression, and that was RUN, not inherited from the card: the same probe against a
    shadow copy of 51ab50d^ (the parent of the commit that gated Review) prints the same
    {"moved_to": "Backlog"} and the same `['reviewed', 'blocked']` after-state — the hole predates
    that card, which gated exactly Review.

    Four parts, all load-bearing, mirroring the sibling test above. The refusal must leave the
    BOARD untouched: a guard that raises after the comment/label/unassign already landed is not a
    guard. The multi-identity branch is what pins the check ORDER for the Done cell (the Review
    cell is pinned by #627), the OWNERSHIP control is what stops that negative assertion from
    passing for a `return_task` with no `_require_mine` at all, and the CONTROL sweep is what
    stops the whole test from passing for a `return_task` that refuses unconditionally — Your Call
    is deliberately in that sweep: unlike Done, that card is still the agent's OWN work in flight.

    MUTATION-CHECKED, each round naming the assertion it actually reddens (checked against the
    driver's raw output, because guessing the site is how a pin gets miscredited): control PASS;
    delete the Done gate -> FAIL at the first `pytest.raises` (the card walks to Backlog); put
    `_require_mine` before the stage gates -> FAIL on the multi-identity branch; delete
    `_require_mine` -> FAIL on the ownership control; widen the gate to ("Done", "Design") -> FAIL
    on the CONTROL sweep. An UNCONDITIONAL gate also fails, but at the ownership control, which it
    reaches first — so it is the widened mutant, not that one, that shows the sweep is live."""
    api, wf, _t = env

    # driven the NORMAL way, so the state the refusal protects is the real one
    accepted = api.add_task("work a human already accepted", "Queue")
    wf.claim(accepted["id"])
    wf.advance(accepted["id"], to="build", spec="сделаю X")
    wf.advance(accepted["id"], to="review", worklog="сделано", evidence="abc123")
    wf.review_task(accepted["id"], verdict="approve", report="ок")
    api.task_bucket[accepted["id"]] = api.bucket_id("Done")   # the HUMAN moves it — no tool can
    assert api.stage_of(accepted["id"]) == "Done"

    with pytest.raises(WorkflowError) as excinfo:
        wf.return_task(accepted["id"], reason="внешний блок")
    msg = str(excinfo.value)
    assert "Done" in msg and "file_task" in msg, \
        f"the refusal must say it is the human's transition and name the door that works: {msg}"

    # nothing happened: the gate fires BEFORE the comment / label / unassign / move
    assert api.stage_of(accepted["id"]) == "Done", "the refused return walked accepted work back"
    assert api.tasks[accepted["id"]]["assignees"], "the refused return still unassigned the card"
    labels = [lb["title"] for lb in api.tasks[accepted["id"]]["labels"]]
    assert "blocked" not in labels, f"the board now says approved AND blocked at once: {labels}"
    assert "reviewed" in labels, "the verdict label vanished"
    assert not any(c.startswith("[blocked]") for c in api.comments_text(accepted["id"]))

    # multi-identity: someone else's accepted card. THIS is the cell that pins the check ORDER —
    # under the inverted order the caller reads "claim it first", which for a card in Done is the
    # one thing that can never be right.
    theirs = api.add_task("someone else's accepted card", "Done")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.return_task(theirs["id"], reason="внешний блок")
    multi_msg = str(multi.value)
    assert "Done" in multi_msg and "file_task" in multi_msg, \
        f"the caller must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is never the answer for a Done card: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Done"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still live — from an OPEN stage someone else's card is refused BY
    # OWNERSHIP.
    not_mine = api.add_task("someone else's card in Build", "Build")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.return_task(not_mine["id"], reason="чужой сервис лежит")
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards return_task from an open stage: {owned.value}"

    # CONTROL: the five stages that stay open still return the card — the gate is Review+Done,
    # not "everything above Build"
    for stage in ("Design", "Build", "Queue", "Backlog", "Your Call"):
        open_task = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        wf.return_task(open_task["id"], reason="чужой сервис лежит")
        assert api.stage_of(open_task["id"]) == "Backlog", f"return_task broke from {stage}"


def test_decompose_refuses_from_done_the_other_half_of_the_same_bypass(env):
    """#649: the sibling hole #626 measured and deliberately left open — `decompose` walking a
    card OUT of Done — and the last instance of that bypass known when this landed. Measured
    through the real `Workflow` over a FakeAPI board, on a card driven the NORMAL way (Queue ->
    claim -> Design -> Build -> Review -> approve -> a human moves it to Done): it did not refuse,
    answered {"parent": {"moved_to": "Backlog", "labeled": "epic"}}, and left the card in Backlog
    with NO assignee, carrying `reviewed` AND `epic` at once, with two fresh children in Queue —
    the board claiming work a human accepted is now an unfinished container.

    Not a regression, and that was RUN rather than inherited: at 51ab50d^ (the parent of #590's
    commit) `decompose` reads the same `_find_task` -> `_require_mine` with no stage check at all.

    WHY PER-TOOL AND NOT ONE SHARED GUARD — the alternative was weighed against the call sites
    rather than by taste, and the reasoning is here because the next reader will ask. The only
    chokepoint every card-touching tool shares is `_find_task`, and it also serves the READ paths
    (get_task/comment/download_attachment/attach_file): shutting Done there makes an accepted card
    unreadable, a worse regression than the hole. A guard inside `_move` cannot see the source
    stage (it would need a board fetch per call, N of them for N children) and would fire only
    AFTER the children exist and `epic`/unassign have landed — a guard that raises after the fact
    is not a guard. A shared `_refuse_if_done(stage, tool)` helper is still one call per tool: it
    de-duplicates the TEXT, not the obligation to call it, so it closes no class.

    Two corrections to that reasoning, both from the independent second pass, both kept because a
    rationale is worth less than the measurements under it. (1) An earlier draft added that such a
    helper would rewrite "five refusals #590/#626/#627 pin verbatim". That is false twice over and
    was DISPROVED by construction: exactly ONE refusal in this family is pinned by a literal
    string (`call_human`'s prefix, below), every other by tokens — and a Done helper would touch
    only the TWO Done refusals, not five. Collapsing every Done refusal into one shared message
    left the suite green. The first two reasons carry this decision; that third one never did.
    (2) The read-path objection rules out an UNPARAMETERISED gate, and only that: with a Done
    guard inside `_find_task`, get_task/comment/attach_file/download_attachment on an accepted
    card all refuse — measured, by building it. A PARAMETERISED one is constructible
    (`_find_task(..., *, allow_done=False)` with the read paths opting in), closes the class
    fail-closed, and was measured green. It is not rejected here as impossible; it is out of this
    card's slice, which is one instance. So the CLASS stays open by construction and is filed for
    a human's ruling as #662, that option included; this test pins the INSTANCE.

    Same FORM as #590/#626: a stage check straight after `_find_task` and BEFORE `_require_mine`,
    with a refusal that names the channel that does work — `file_task`, because work an accepted
    card revealed is NEW work, not a split of this one (call_human refuses from Done too).

    Five parts, all load-bearing. The refusal must leave the board untouched AND create NOTHING:
    decompose's side effects start with children on the board, so a guard placed after them would
    leave orphans no refusal can take back — that assertion is this test's own, not inherited from
    the return_task sibling. The multi-identity branch pins the check ORDER (before this gate that
    case answered "not assigned to you — claim it first", the one answer that can never be right
    for a card a human accepted), the OWNERSHIP control stops that negative assertion from passing
    for a decompose with no `_require_mine` at all, and the CONTROL sweep stops the whole test from
    passing for a decompose that refuses unconditionally. Review is IN that sweep: `decompose`
    does walk a card out of Review today, which is the same shape #590 gated for `return_task` —
    measured, out of this card's slice (its subject is Done) and filed as #663, so read the sweep
    as "this diff did not touch it", not as a ruling that it is fine.

    MUTATION-CHECKED, each round naming the assertion it actually reddens, read out of the
    driver's raw output rather than guessed — guessing the site is how a pin gets miscredited, and
    it happened here on the first pass, so the correction is kept: control PASS; delete the Done
    gate -> FAIL at the first `pytest.raises` (and the #590/#626/#627 pins stay GREEN on that same
    mutant, which is what shows this hole was a separate one); put `_require_mine` before the gate
    -> FAIL on the multi-identity branch; delete `_require_mine` -> FAIL on the ownership control.
    An UNCONDITIONAL gate fails on the OWNERSHIP CONTROL, not on the sweep, because it reaches that
    control first — and so does widening the gate to ("Done", "Build"), for the un-obvious reason
    that the ownership control's card stands in Build. The widening that actually proves the sweep
    is live is therefore ("Done", "Backlog"), Backlog being the sweep's first stage -> FAIL inside
    the sweep loop."""
    api, wf, _t = env

    # driven the NORMAL way, so the state the refusal protects is the real one
    accepted = api.add_task("work a human already accepted", "Queue")
    wf.claim(accepted["id"])
    wf.advance(accepted["id"], to="build", spec="сделаю X")
    wf.advance(accepted["id"], to="review", worklog="сделано", evidence="abc123")
    wf.review_task(accepted["id"], verdict="approve", report="ок")
    api.task_bucket[accepted["id"]] = api.bucket_id("Done")   # the HUMAN moves it — no tool can
    assert api.stage_of(accepted["id"]) == "Done"
    cards_before = len(api.tasks)

    with pytest.raises(WorkflowError) as excinfo:
        wf.decompose(accepted["id"], [{"title": "часть A"}, {"title": "часть B"}])
    msg = str(excinfo.value)
    assert "Done" in msg and "file_task" in msg, \
        f"the refusal must say it is the human's transition and name the door that works: {msg}"

    # nothing happened: the gate fires BEFORE any child is created and before label/unassign/move
    assert len(api.tasks) == cards_before, "the refused decompose still put children on the board"
    assert api.stage_of(accepted["id"]) == "Done", "the refused decompose walked accepted work back"
    assert api.tasks[accepted["id"]]["assignees"], "the refused decompose still unassigned the card"
    labels = [lb["title"] for lb in api.tasks[accepted["id"]]["labels"]]
    assert "epic" not in labels, f"the board now calls accepted work an epic container: {labels}"
    assert "reviewed" in labels, "the verdict label vanished"
    assert not any(c.startswith("[decompose]") for c in api.comments_text(accepted["id"]))

    # multi-identity: someone else's accepted card. THIS is the cell that pins the check ORDER —
    # under the inverted order the caller reads "claim it first", which for a card in Done is the
    # one thing that can never be right. Measured before the gate: that is exactly what it said.
    theirs = api.add_task("someone else's accepted card", "Done")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.decompose(theirs["id"], [{"title": "A"}, {"title": "B"}])
    multi_msg = str(multi.value)
    assert "Done" in multi_msg and "file_task" in multi_msg, \
        f"the caller must read the STAGE refusal, not an ownership one: {multi_msg}"
    assert "not assigned to you" not in multi_msg, \
        f"ownership ran first — 'claim it first' is never the answer for a Done card: {multi_msg}"
    assert api.stage_of(theirs["id"]) == "Done"
    assert api.tasks[theirs["id"]]["assignees"][0]["id"] == 77

    # OWNERSHIP CONTROL: the assertion above is a NEGATIVE one about a guard it never reaches, so
    # pin that the guard is still live — from an OPEN stage someone else's card is refused BY
    # OWNERSHIP.
    not_mine = api.add_task("someone else's card in Build", "Build")
    api.tasks[not_mine["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as owned:
        wf.decompose(not_mine["id"], [{"title": "A"}, {"title": "B"}])
    assert "not assigned to you" in str(owned.value), \
        f"_require_mine no longer guards decompose from an open stage: {owned.value}"

    # CONTROL: the six stages that stay open still decompose — the gate is Done, not "anything
    # a human might be looking at"
    for stage in ("Backlog", "Queue", "Design", "Build", "Review", "Your Call"):
        open_task = api.add_task(f"big job in {stage}", stage, assignee=api.me_user)
        wf.decompose(open_task["id"], [{"title": "A"}, {"title": "B"}])
        assert api.stage_of(open_task["id"]) == "Backlog", f"decompose broke from {stage}"
        assert any(lb["title"] == "epic" for lb in api.tasks[open_task["id"]]["labels"])


def test_call_human_refuses_from_review_and_the_stage_check_precedes_ownership(env):
    """#590: the second half of the reviewer's dead end. `call_human` was already gated to
    Design/Build, but its refusal only said WHERE it doesn't work, and `_require_mine` ran FIRST —
    so the realistic reviewer (multi-identity: the card in Review is the implementer's) got
    "not assigned to you — claim it first" and was pointed at the one thing they must never do.

    Reordering changes no refusal SET (both checks are conjunctive) — only which message the
    "in Review AND not mine" case gets. The prefix is kept BYTE-IDENTICAL because SKILL.md quotes
    it verbatim, and the Review-only pointer is appended after it.

    Why a pointer instead of opening the gate: measured, parking from Review is LOSSY. This
    method's body `_move`s the card to Your Call, and from Your Call `review_task` refuses both
    verdicts — the verdict would die with the question.

    MUTATION-CHECKED: control PASS; drop the Review append -> FAIL; restore the old
    `_require_mine`-before-stage order -> FAIL on the multi-identity assertion."""
    api, wf, t = env
    prefix = "call_human works only from Design/Build; task is in Review"

    # solo: the card in Review is mine (I implemented it) -> stage refusal + the real channel
    mine = api.add_task("my own card, awaiting review", "Review", assignee=api.me_user)
    with pytest.raises(WorkflowError) as solo:
        wf.call_human(mine["id"], question="кто из двух вариантов правильный?")
    assert prefix in str(solo.value), \
        f"SKILL.md quotes this prefix verbatim — it must not drift: {solo.value}"
    assert "review_task" in str(solo.value), \
        f"the refusal must send the reviewer to the channel that works: {solo.value}"

    # multi-identity: the card is the IMPLEMENTER's. THIS is what pins the check order —
    # under the old order this said "claim it first" and never mentioned the stage at all.
    theirs = api.add_task("someone else's card in review", "Review")
    api.tasks[theirs["id"]]["assignees"] = [{"id": 77, "username": "agent-impl"}]
    with pytest.raises(WorkflowError) as multi:
        wf.call_human(theirs["id"], question="кто из двух вариантов правильный?")
    assert prefix in str(multi.value), \
        f"the reviewer must read the STAGE refusal, not an ownership one: {multi.value}"
    assert "not assigned to you" not in str(multi.value), \
        f"ownership ran first again — 'claim it first' is the wrong advice here: {multi.value}"

    # CONTROL: from Design, where the tool IS the right door, it still parks the card
    parked = wf.call_human(t["id"], question="какой вариант деплоя выбрать?")
    assert parked["moved_to"] == "Your Call"
    assert api.stage_of(t["id"]) == "Your Call"


def test_decompose_creates_children_in_queue_parent_epic(env):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="2"):
        wf.decompose(t["id"], subtasks=[{"title": "one"}])
    res = wf.decompose(t["id"], subtasks=[
        {"title": "step 1", "description": "d1", "priority": 3},
        {"title": "step 2"},
    ])
    assert len(res["created"]) == 2
    for child in res["created"]:
        assert api.stage_of(child["id"]) == "Queue"
        assert (child["id"], t["id"], "parenttask") in api.relations
    assert api.stage_of(t["id"]) == "Backlog"
    assert api.tasks[t["id"]]["assignees"] == []
    assert any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert any(c.startswith("[decompose]") for c in api.comments_text(t["id"]))


def test_decompose_partial_failure_reports_created_children(env):
    # A failure on the 2nd create_task (network/429) must not drop a bare VikunjaError:
    # the child created by the 1st call is already on the board, and a blind retry would
    # duplicate it. decompose must raise a WorkflowError that surfaces that partial result.
    api, wf, t = env
    real_create = api.create_task
    calls = {"n": 0}
    created_ids = []

    def flaky_create(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 2:
            raise VikunjaError(429, "rate limited")
        child = real_create(*args, **kwargs)
        created_ids.append(child["id"])
        return child

    api.create_task = flaky_create

    with pytest.raises(WorkflowError) as ei:
        wf.decompose(t["id"], subtasks=[
            {"title": "first child"},
            {"title": "second child"},
        ])
    msg = str(ei.value)
    first_id = created_ids[0]
    # the already-created first child is named by id AND title -> the human/agent can see
    # exactly what leaked instead of blindly retrying
    assert f"#{first_id}" in msg
    assert "first child" in msg
    assert "second child" not in msg  # the 2nd child was never created
    # the partial result really is on the board (in Queue) — not imaginary
    assert first_id in api.tasks
    assert api.stage_of(first_id) == "Queue"
    # the parent is left un-finalized: no epic label, still assigned, not moved to Backlog
    assert not any(lb["title"] == "epic" for lb in api.tasks[t["id"]]["labels"])
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert api.stage_of(t["id"]) == "Design"


def test_decompose_first_child_failure_reraises_bare_error(env):
    # nothing was created yet -> no partial result to report; the bare error is safe to
    # retry, so decompose must NOT wrap it into a misleading "already created" message.
    api, wf, t = env

    def failing_create(*args, **kwargs):
        raise VikunjaError(429, "rate limited")

    api.create_task = failing_create
    with pytest.raises(VikunjaError):
        wf.decompose(t["id"], subtasks=[{"title": "a"}, {"title": "b"}])


def test_file_task_files_finding_into_backlog_with_marker_and_relation(env):
    api, wf, t = env
    # пустой title — отказ
    with pytest.raises(WorkflowError, match="title"):
        wf.file_task(title="   ")
    # находка по ходу работы над t: паркуем в Backlog и связываем с t
    res = wf.file_task(
        title="race in claim self-heal window",
        description="заметил по ходу работы",
        priority=2,
        related_task_id=t["id"],
    )
    new_id = res["filed"]["id"]
    assert new_id != t["id"]
    assert api.stage_of(new_id) == "Backlog"          # Backlog, НЕ Queue — приоритизирует человек
    assert res["filed"]["stage"] == "Backlog"
    assert api.tasks[new_id]["priority"] == 2
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))
    assert (new_id, t["id"], "related") in api.relations
    assert res["related_to"] == t["id"]


def test_file_task_without_relation_has_no_link(env):
    api, wf, t = env
    res = wf.file_task(title="techdebt: refactor config walk-up")
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert not any(subj == new_id for subj, _other, _kind in api.relations)
    assert "related_to" not in res
    assert any(c.startswith("[filed-by-agent]") for c in api.comments_text(new_id))


def test_file_task_cross_project_lands_in_targets_backlog(env):
    api, wf, t = env
    # Backlog у цели НЕ первый бакет: дефолт-бакет = Inbox, так что пропущенный move
    # оставил бы карточку в Inbox и тест бы упал (create-в-нужном-проекте недостаточно).
    other = api.add_project("neighbor", buckets=["Inbox", *STAGES])
    res = wf.file_task(
        title="repo B: нужен эндпоинт для A",
        description="координация агент→агент",
        priority=1,
        related_task_id=t["id"],
        project_id=other["id"],
    )
    new_id = res["filed"]["id"]
    other_view = api.kanban_view(other["id"])
    other_backlog = next(
        b for b in api.buckets(other["id"], other_view["id"]) if b["title"] == "Backlog"
    )
    assert api.task_bucket[new_id] == other_backlog["id"]  # Backlog ЦЕЛИ, не свой
    assert res["filed"]["project_id"] == other["id"]
    assert res["filed"]["stage"] == "Backlog"
    assert (new_id, t["id"], "related") in api.relations   # связь через границу проектов
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert f"из проекта id={wf.project_id}" in marker      # provenance для людей цели
    assert f"#{t['id']}" in marker


def test_file_task_cross_project_no_access_fails_fast_nothing_created(env):
    api, wf, _t = env
    secret = api.add_project("secret", buckets=STAGES, forbidden=True)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project"):
        wf.file_task(title="x", project_id=secret["id"])
    assert len(api.tasks) == before        # fail-fast: доска резолвится ДО create_task


def test_file_task_cross_project_unknown_or_pseudo_project_refused(env):
    api, wf, _t = env
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="can't file into project 999999"):
        wf.file_task(title="x", project_id=999999)
    with pytest.raises(WorkflowError, match="positive"):
        wf.file_task(title="x", project_id=-1)  # псевдо-проекты Vikunja (favorites = -1)
    assert len(api.tasks) == before


def test_file_task_cross_project_target_without_backlog_refused(env):
    api, wf, _t = env
    virgin = api.add_project("virgin", buckets=["To-Do", "Doing", "Done"])  # без setup
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="Backlog"):
        wf.file_task(title="x", project_id=virgin["id"])
    assert len(api.tasks) == before


def test_file_task_explicit_own_project_id_is_todays_behavior(env):
    api, wf, t = env
    res = wf.file_task(title="own finding", related_task_id=t["id"], project_id=wf.project_id)
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Backlog"
    assert "project_id" not in res["filed"]    # без кросс-добавок в результате
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert marker == (
        f"[filed-by-agent] заведено агентом для триажа человеком "
        f"(по ходу работы над #{t['id']})"
    )


def test_file_task_cross_project_401_propagates_as_vikunja_error(env):
    """Binding contract (#140): a 401 from resolving the TARGET board must stay a VikunjaError —
    NOT be wrapped into a WorkflowError — so server._tool's rotated-token reload-and-retry still
    fires. Only 403/404 (a real access/shape problem) become an actionable WorkflowError; a 401
    (invalid/expired/rotated token) must propagate untouched. VikunjaError and WorkflowError are
    unrelated types, so pytest.raises(VikunjaError) here is RED if the 401 is ever wrapped."""
    api, wf, _t = env
    other = api.add_project("neighbor", buckets=STAGES)

    def boom(_pid):
        raise VikunjaError(401, '{"code":11,"message":"invalid token"}')

    api.kanban_view = boom                      # 401 lands on the target-board resolve
    with pytest.raises(VikunjaError) as ei:
        wf.file_task(title="x", project_id=other["id"])
    assert ei.value.status == 401


def test_file_task_queue_optin_lands_in_queue_ready_for_pickup(env):
    """#249: queue=True — явный опт-ин «человек попросил завести задачу в работу» (его
    указание и есть триаж). Карточка ложится сразу в Queue СВОЕГО проекта, неассайненная
    (→ сразу клеймабельна для next_task/claimable), маркер [filed-by-agent] честно
    фиксирует пропуск Backlog-триажа. Дефолт (queue=False) пинуют существующие тесты выше."""
    api, wf, t = env
    res = wf.file_task(
        title="переезд конфига на pydantic",
        description="человек явно попросил завести в работу",
        priority=1,
        related_task_id=t["id"],
        queue=True,
    )
    new_id = res["filed"]["id"]
    assert api.stage_of(new_id) == "Queue"             # сразу в Queue, не в Backlog
    assert res["filed"]["stage"] == "Queue"
    assert api.tasks[new_id]["assignees"] == []        # без ассайни → клеймабельна любым агентом
    assert "Queue" in res["note"]
    assert (new_id, t["id"], "related") in api.relations
    marker = next(c for c in api.comments_text(new_id) if c.startswith("[filed-by-agent]"))
    assert "Queue" in marker                           # провенанс: видно, что триаж пропущен


def test_file_task_queue_cross_project_refused_nothing_created(env):
    """#249: в ЧУЖУЮ Queue агент работу не инжектит — кросс-проектный файлинг остаётся
    Backlog-only (их доску триажит ИХ человек). Отказ fail-fast: ничего не создано.
    Граница гейта — именно КРОСС, а не сам параметр: явный СВОЙ project_id с queue=True
    работает (эквивалентен None, как пинует test_file_task_explicit_own_project_id...)."""
    api, wf, _t = env
    other = api.add_project("neighbor", buckets=STAGES)
    before = len(api.tasks)
    with pytest.raises(WorkflowError, match="queue"):
        wf.file_task(title="x", project_id=other["id"], queue=True)
    assert len(api.tasks) == before                    # fail-fast: карточка не создана
    res = wf.file_task(title="own queue ok", project_id=wf.project_id, queue=True)
    assert api.stage_of(res["filed"]["id"]) == "Queue"


def test_comment_and_get_task(env):
    api, wf, t = env
    with pytest.raises(WorkflowError):
        wf.comment(t["id"], text=" ")
    wf.comment(t["id"], text="нашёл гочу в API")
    dossier = wf.get_task(t["id"])
    assert dossier["stage"] == "Design"
    assert dossier["comments"][-1]["text"] == "нашёл гочу в API"
    assert dossier["assignees"] == ["agent-infra"]


def test_comments_stored_as_html_and_rendered_back_multiline(env):
    """#85: a multiline agent comment is STORED as escaped, structured HTML (so the
    Vikunja UI shows line breaks) yet get_task renders it back to clean multiline text
    (so the agent doesn't read tag soup), with markers and '<' both intact."""
    api, wf, t = env
    wf.comment(t["id"], text="строка 1\nстрока 2\n\nтег <id> и a < b")
    # raw stored form is HTML with paragraph + line-break structure and escaped '<'
    raw = api.comments(t["id"])[-1]["comment"]
    assert raw.count("<p>") == 2 and "<br>" in raw
    assert "&lt;id&gt;" in raw and "&lt; b" in raw
    # but the agent-facing dossier is plain multiline text, '<id>' unescaped, no tags
    text = wf.get_task(t["id"])["comments"][-1]["text"]
    assert text == "строка 1\nстрока 2\n\nтег <id> и a < b"


def test_worklog_comment_is_html_but_markers_still_detected(env):
    """The [worklog] report is stored as HTML, yet next_task's marker greps (and the
    comments_text helper) still see the leading marker."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="починил", evidence="commit c0ffee")
    raw = next(c["comment"] for c in api.comments(t["id"])
               if "[worklog]" in html_to_text(c["comment"]))
    assert raw.startswith("<p>[worklog]")          # stored as HTML
    # an independent reviewer is still offered this bug fix -> marker detection works
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    assert reviewer.next_task().get("review") is True


def test_get_task_returns_untruncated_description_and_related(env):
    """F3: get_task — полное досье, а не урезанная _summary (500 символов, без related)."""
    api, wf, t = env
    long_description = "х" * 600
    api.tasks[t["id"]]["description"] = long_description
    parent = api.add_task("epic", "Backlog")
    api.add_relation(t["id"], parent["id"], "parenttask")

    dossier = wf.get_task(t["id"])
    assert dossier["description"] == long_description
    assert len(dossier["description"]) > 500
    assert dossier["related"] == {
        "parenttask": [{"id": parent["id"], "title": "epic"}],
    }


def test_get_task_related_defaults_to_empty_dict_without_relations(env):
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["related"] == {}


def test_ref_composes_human_searchable_identifier():
    """#82: agents must echo the human-searchable ref "<identifier> (<id>)" — exactly the
    "VMCP-27 (82)" shape the human asked for — not the bare, unsearchable global id."""
    assert Workflow._ref({"id": 82, "identifier": "VMCP-27"}) == "VMCP-27 (82)"
    # project with no identifier prefix -> Vikunja returns "#<index>", which we keep
    assert Workflow._ref({"id": 82, "identifier": "#27"}) == "#27 (82)"
    # defensive fallback when identifier is empty/absent -> bare "#<id>"
    assert Workflow._ref({"id": 82, "identifier": ""}) == "#82"
    assert Workflow._ref({"id": 82}) == "#82"


def test_get_task_surfaces_searchable_ref(env):
    """get_task dossier carries the human-searchable ref alongside the raw id."""
    api, wf, t = env
    dossier = wf.get_task(t["id"])
    assert dossier["ref"] == f"{api.tasks[t['id']]['identifier']} ({t['id']})"
    assert dossier["ref"].startswith("HGI-") and dossier["ref"].endswith(f"({t['id']})")


def test_review_flow_for_bug_labels(env):
    api, wf, t = env
    # довели багфикс до Review
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")

    # имплементеру (assignee) ревью НЕ предлагается
    assert "review" not in wf.next_task()

    # свободному агенту — предлагается
    api2 = api  # тот же борд, другой "я"
    reviewer = type(wf)(api2, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]

    # пустой report / кривой verdict / не-Review задача — отказ
    import pytest as _pytest
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="approve", report="  ")
    with _pytest.raises(WorkflowError):
        reviewer.review_task(t["id"], verdict="lgtm", report="r")

    # needs_work: вердикт-коммент + возврат в Build, assignee сохранён
    reviewer.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    assert api.stage_of(t["id"]) == "Build"
    assert api.tasks[t["id"]]["assignees"][0]["id"] == api.me_user["id"]
    assert any(c.startswith("[review] NEEDS WORK") for c in api.comments_text(t["id"]))

    # после вердикта задача больше не предлагается на ревью (вернулась в Build);
    # доводим снова и апрувим
    wf.advance(t["id"], to="review", worklog="w2", evidence="e2")
    reviewer.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    assert api.stage_of(t["id"]) == "Review"
    assert any(c.startswith("[review] APPROVE") for c in api.comments_text(t["id"]))
    # свежий APPROVE (новее последнего worklog) закрывает ревью — задача не предлагается
    res = reviewer.next_task()
    assert not res.get("review"), res


def test_review_offered_for_non_bug_task_kind_change(env):
    """#117: независимое ревью теперь на ВСЕ задачи, не только bug — не-баг в Review
    предлагается свободному агенту с review_kind='change'."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True
    assert offered["task"]["id"] == t["id"]
    assert offered["review_kind"] == "change"


def test_review_not_offered_without_worklog_report(env):
    """#117 guard: a card in Review with no [worklog] has nothing to review — a card parked in
    Review by hand (no work report) is NOT offered for independent review (advance→review always
    posts a worklog, so real Review cards have one)."""
    api, wf, t = env
    api.add_task("parked by hand", "Review")   # no worklog, no verdict, unassigned
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    res = reviewer.next_task()
    assert "review" not in res


def test_review_not_offered_for_epic_container(env):
    """#117: epic-контейнер (нет своего кода — evidence в детях) НЕ предлагается на ревью,
    даже неназначенный и с worklog — исключение по метке epic, а не по assignee."""
    api, wf, t = env
    epic = api.add_task("epic container", "Review", labels=("epic",))
    api.add_comment(epic["id"], "[worklog] собрано")   # отчёт есть, вердикта нет
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    res = reviewer.next_task()
    assert "review" not in res      # epic отфильтрован — на ревью не выдаётся


def test_review_reoffered_after_needs_work_rework(env):
    """Цикл: needs_work -> доработка -> Review снова -> задача ОПЯТЬ предлагается на ревью."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1")

    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    assert reviewer.next_task().get("review") is True

    reviewer.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert not reviewer.next_task().get("review")          # в Build — ревьюить нечего

    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2")
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]  # re-offer!

    reviewer.review_task(t["id"], verdict="approve", report="теперь по причине")
    assert not reviewer.next_task().get("review")          # свежий вердикт закрыл цикл


def _label_titles(api, task_id):
    return [lb["title"] for lb in api.tasks[task_id]["labels"]]


def _to_review(wf, task_id):
    wf.advance(task_id, to="build", spec="s")
    return wf.advance(task_id, to="review", worklog="w", evidence="e")


def test_review_approve_adds_reviewed_strips_review_failed(env):
    """approve вешает reviewed и снимает review-failed (взаимоисключающие вердикт-метки)."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент апрува на задаче ещё висит review-failed (belt-and-suspenders на всякий)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "review-failed"})
    wf.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    titles = _label_titles(api, t["id"])
    assert "reviewed" in titles
    assert "review-failed" not in titles
    assert api.stage_of(t["id"]) == "Review"  # апрув оставляет задачу в Review для человека


def test_review_needs_work_adds_review_failed_strips_reviewed(env):
    """needs_work вешает review-failed и снимает reviewed."""
    api, wf, t = env
    _to_review(wf, t["id"])
    # на момент needs_work на задаче висит reviewed (например, была одобрена и переоткрыта)
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})
    wf.review_task(t["id"], verdict="needs_work", report="фикс лечит симптом")
    titles = _label_titles(api, t["id"])
    assert "review-failed" in titles
    assert "reviewed" not in titles
    assert api.stage_of(t["id"]) == "Build"  # needs_work возвращает задачу в Build


def test_advance_review_resubmit_strips_review_failed(env):
    """Ресабмит в Review (после needs_work) снимает review-failed — reset вердикта."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1")
    wf.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert "review-failed" in _label_titles(api, t["id"])  # needs_work повесил
    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2")
    assert "review-failed" not in _label_titles(api, t["id"])  # ресабмит снял


def test_advance_review_first_submit_no_review_failed_label(env):
    """Первый сабмит в Review: review-failed нет — снятие это no-op, метка НЕ добавляется."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w", evidence="e")  # не падает
    assert "review-failed" not in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Review"


def test_manual_bounce_of_approved_card_clears_reviewed_on_resubmit(env):
    """#119 — сам репортнутый баг. Одобренную карточку (метка `reviewed`) человек РУКАМИ
    вытаскивает из Review на доработку — ни одна тулза не срабатывает, поэтому `reviewed`
    переживает переезд. После доработки агент ресабмитит через advance(to='review'):
    несвежий `reviewed` ДОЛЖЕН исчезнуть (ресабмит инвалидирует любой прошлый вердикт),
    иначе карточка въезжает в новый Review с чужим APPROVE. И карточка снова предлагается
    на независимое ревью (оффер цепляется за свежесть [worklog], а не за метку)."""
    api, wf, t = env
    # довели до Review и получили независимый approve -> на карточке метка reviewed
    _to_review(wf, t["id"])
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    reviewer.review_task(t["id"], verdict="approve", report="воспроизвёл, фикс по причине")
    assert "reviewed" in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Review"
    assert not reviewer.next_task().get("review")   # свежий APPROVE закрыл ревью

    # ЧЕЛОВЕК руками тащит одобренную карточку из Review обратно в Build на доработку.
    # update_task(bucket_id=) задачу НЕ двигает — ручной drag в FakeAPI это прямая правка
    # task_bucket. Ни одна тулза не сработала -> reviewed переживает переезд (это и есть баг).
    api.task_bucket[t["id"]] = api.bucket_id("Build")
    assert api.stage_of(t["id"]) == "Build"
    assert "reviewed" in _label_titles(api, t["id"])

    # агент дорабатывает и ресабмитит в Review -> reviewed должен уйти, review-failed тоже нет
    wf.advance(t["id"], to="review", worklog="доработал по замечанию человека", evidence="e2")
    titles = _label_titles(api, t["id"])
    assert "reviewed" not in titles           # ключевая проверка: несвежий вердикт снят
    assert "review-failed" not in titles
    assert api.stage_of(t["id"]) == "Review"
    # ресабмит снова уходит на независимое ревью (свежий [worklog] новее прошлого [review])
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]


def test_advance_to_build_clears_stale_verdict_labels(env):
    """#119: человек может утащить вердикт-несущую карточку (reviewed ИЛИ review-failed) аж
    в Design; когда агент (пере)входит в сборку через advance(to='build'), несвежий вердикт
    снимается — карточка в активной (пере)сборке не несёт действующего вердикта."""
    api, wf, t = env                               # t стартует в Design, назначена на меня
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})
    wf.advance(t["id"], to="build", spec="доработка после ручного возврата человеком")
    assert "reviewed" not in _label_titles(api, t["id"])
    assert api.stage_of(t["id"]) == "Build"


def test_advance_to_build_fresh_claim_adds_no_verdict_labels(env):
    """Свежий клейм: advance(to='build') на задаче без вердикт-меток — чистый no-op снятия,
    никакие метки не появляются (страхуемся, что helper не добавляет, а только снимает)."""
    api, wf, t = env
    wf.advance(t["id"], to="build", spec="s")
    assert _label_titles(api, t["id"]) == []
    assert api.stage_of(t["id"]) == "Build"


def test_resubmit_after_needs_work_clears_review_failed_and_no_reviewed(env):
    """Цикл needs_work: карточка в Build с review-failed, reviewed уже снят. Ресабмит через
    advance(to='review') снимает review-failed и НЕ воскрешает reviewed — чистый лист."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    wf.advance(t["id"], to="build", spec="s")
    wf.advance(t["id"], to="review", worklog="w1", evidence="e1")
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    reviewer.review_task(t["id"], verdict="needs_work", report="не закрыта причина")
    assert "review-failed" in _label_titles(api, t["id"])
    assert "reviewed" not in _label_titles(api, t["id"])
    wf.advance(t["id"], to="review", worklog="w2: доработано", evidence="e2")
    titles = _label_titles(api, t["id"])
    assert "review-failed" not in titles   # ресабмит снял
    assert "reviewed" not in titles        # и не воскресил


def test_stale_reviewed_label_does_not_suppress_review_offering(env):
    """#119 разбор подавления: оффер ревью в next_task цепляется за СВЕЖЕСТЬ комментов
    [worklog]/[review], а НЕ за метку `reviewed`. Карточка со стале-`reviewed`, у которой
    последний [worklog] новее последнего [review] (тут [review] вообще нет), всё равно
    предлагается на ревью — значит метка это косметическая ложь на доске, а не
    функциональная блокировка следующего ревью (поэтому задача человека и попала на новое
    ревью, несмотря на несвежий бейдж)."""
    api, wf, t = env
    _to_review(wf, t["id"])                                        # свежий [worklog], вердикта нет
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "reviewed"})  # стале-бейдж вручную
    reviewer = type(wf)(api, project_id=3)
    reviewer._me_cache = {"id": 77, "username": "agent-reviewer"}
    offered = reviewer.next_task()
    assert offered.get("review") is True and offered["task"]["id"] == t["id"]


def test_advance_review_bug_returns_review_needed_note(env):
    """advance(to='review') на баге отдаёт review_needed=True, review_kind='bug' + подсказку."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "bug"})
    res = _to_review(wf, t["id"])
    assert res["review_needed"] is True
    assert res["review_kind"] == "bug"
    assert res.get("note")


def test_advance_review_non_bug_returns_review_needed_kind_change(env):
    """#117: не-баг (feat/chore/docs) теперь ТОЖЕ требует независимого ревью —
    review_needed=True с review_kind='change' (root_cause не нужен)."""
    api, wf, t = env
    res = _to_review(wf, t["id"])
    assert res["review_needed"] is True
    assert res["review_kind"] == "change"
    assert res.get("note")


def test_advance_review_epic_container_no_review_needed(env):
    """#117: epic-контейнер (нет своего кода) НЕ триггерит независимое ревью —
    review_needed/review_kind отсутствуют, payload голый (как #94)."""
    api, wf, t = env
    api.tasks[t["id"]]["labels"].append({"id": 999, "title": "epic"})
    res = _to_review(wf, t["id"])
    assert "review_needed" not in res
    assert "review_kind" not in res
    assert res == {"moved_to": "Review", "task_id": t["id"]}


def test_fake_remove_label_idempotent_and_mirrors_client(env):
    """FakeAPI.remove_label зеркалит клиент и идемпотентен (отсутствующий id — no-op)."""
    api, wf, t = env
    lb = api.get_or_create_label("reviewed")
    api.add_label(t["id"], lb["id"])
    assert "reviewed" in _label_titles(api, t["id"])
    api.remove_label(t["id"], lb["id"])
    assert "reviewed" not in _label_titles(api, t["id"])
    # повторное снятие того же id и снятие никогда не висевшего id — без ошибки
    api.remove_label(t["id"], lb["id"])
    api.remove_label(t["id"], 123456)
    assert "reviewed" not in _label_titles(api, t["id"])


# --- вложения: get_task.attachments + download_attachment (#139) ---------------------


@pytest.fixture
def att_root(tmp_path, monkeypatch):
    """Redirect downloaded-attachment temp files under pytest's tmp_path so tests don't
    litter the real system temp dir — prod deliberately leaves them for the TTL sweep."""
    root = tmp_path / "att-root"
    monkeypatch.setattr("vikunja_mcp.workflow._ATTACHMENT_ROOT", str(root))
    return root


def test_get_task_surfaces_attachment_metadata(env):
    """#139 Part 1: an agent SEES a card's files ({id,name,mime,size}) instead of guessing —
    metadata only (no bytes), read from the raw task JSON under the existing read scope."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nfake"
    att = api.add_attachment(t["id"], "shot.png", "image/png", data)
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"] == [
        {"id": att["id"], "name": "shot.png", "mime": "image/png", "size": len(data)}
    ]


def test_get_task_attachments_empty_list_when_none(env):
    """No attachments -> [] (the real server sends None; the dossier normalizes it), consistent
    with related/labels/assignees always being present even when empty."""
    api, wf, t = env
    assert wf.get_task(t["id"])["attachments"] == []


def test_download_attachment_writes_temp_file_with_original_name(env, att_root):
    """#139 Part 2: returns the PATH to a temp file that keeps the ORIGINAL filename (an image
    renderer keys off the .png extension) and holds the EXACT bytes; size/mime reported too."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nrealish-bytes"
    att = api.add_attachment(t["id"], "screenshot.png", "image/png", data)
    res = wf.download_attachment(t["id"], att["id"])
    assert os.path.basename(res["path"]) == "screenshot.png"   # extension preserved
    assert res["path"].startswith(str(att_root))               # under the dedicated temp root
    assert os.path.isfile(res["path"])
    with open(res["path"], "rb") as fh:
        assert fh.read() == data                               # exact bytes on disk
    assert res["name"] == "screenshot.png"
    assert res["mime"] == "image/png"
    assert res["size"] == len(data)


def test_download_attachment_unknown_id_lists_available(env, att_root):
    """A wrong attachment id fails actionably — naming the task's real attachments — not a bare
    404 the agent can't act on."""
    api, wf, t = env
    api.add_attachment(t["id"], "a.png", "image/png", b"x")
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 987654)


def test_download_attachment_when_task_has_none(env, att_root):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 1)


def test_download_attachment_refuses_oversized_before_download(env, att_root):
    """A huge file is refused via its METADATA size BEFORE any bytes are pulled — actionable,
    not a memory blowup. Stored bytes stay tiny; only the reported metadata size is large."""
    api, wf, t = env
    att = api.add_attachment(
        t["id"], "huge.bin", "application/octet-stream", b"x", size=26 * 1024 * 1024
    )
    with pytest.raises(WorkflowError, match="cap"):
        wf.download_attachment(t["id"], att["id"])


def test_download_attachment_sanitizes_traversal_filename(env, att_root):
    """A crafted filename can't escape the temp dir — only the basename is used, so the file
    lands INSIDE the per-download temp subdir, never at the traversal target."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "../../../../etc/evil.png", "image/png", b"data")
    res = wf.download_attachment(t["id"], att["id"])
    assert os.path.basename(res["path"]) == "evil.png"   # only the basename survives
    assert res["path"].startswith(str(att_root))         # stays under the temp root


def test_download_attachment_sweeps_stale_temp_dirs(env, att_root):
    """The best-effort TTL sweep reaps a PREVIOUS download's dir on the next call, bounding the
    leak — without deleting a fresh file the agent is about to Read."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "a.png", "image/png", b"x")
    stale_dir = os.path.dirname(wf.download_attachment(t["id"], att["id"])["path"])
    old = time.time() - (_ATTACHMENT_TTL + 60)
    os.utime(stale_dir, (old, old))                      # backdate past the TTL
    fresh = wf.download_attachment(t["id"], att["id"])["path"]
    assert not os.path.exists(stale_dir)                 # stale reaped by the sweep
    assert os.path.exists(fresh)                         # the just-written one kept


# --- вложения: attach_file (upload, #137) --------------------------------------------


def test_attach_file_uploads_and_round_trips_into_get_task(env, tmp_path):
    """#137: attach_file uploads a LOCAL file; it lands on the card and get_task then surfaces its
    metadata (round-trip). The basename is the name, size/mime are reported, and a new
    attachment_id comes back — so an agent can cite it as evidence."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nfinished-ui"
    src = tmp_path / "shot.png"
    src.write_bytes(data)
    res = wf.attach_file(t["id"], str(src))
    assert res["attached"] is True
    assert res["name"] == "shot.png"
    assert res["size"] == len(data)
    assert res["mime"] == "image/png"                    # guessed from the extension
    assert res["attachment_id"] is not None
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"] == [
        {"id": res["attachment_id"], "name": "shot.png", "mime": "image/png", "size": len(data)}
    ]


def test_attach_file_missing_path_is_actionable(env, tmp_path):
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no file to attach"):
        wf.attach_file(t["id"], str(tmp_path / "nope.png"))


def test_attach_file_directory_is_refused(env, tmp_path):
    """A directory is not a regular file -> refused by the isfile guard (never uploaded as junk),
    just like a missing path."""
    api, wf, t = env
    with pytest.raises(WorkflowError, match="no file to attach"):
        wf.attach_file(t["id"], str(tmp_path))


def test_attach_file_refuses_oversized_before_reading(env, tmp_path, monkeypatch):
    """A file over the cap is refused via getsize BEFORE its bytes are read AND before any upload
    — actionable, no huge buffer, no wasted wire call."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    calls = {"n": 0}
    orig = api.upload_attachment

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(api, "upload_attachment", spy)
    src = tmp_path / "big.bin"
    src.write_bytes(b"x" * 50)
    with pytest.raises(WorkflowError, match="cap"):
        wf.attach_file(t["id"], str(src))
    assert calls["n"] == 0                               # refused before any upload


def test_attach_file_uses_basename_not_full_path(env, tmp_path):
    """The attachment name is the basename, never the caller's full local path (which would leak
    the local dir layout and confuse an extension-keyed renderer)."""
    api, wf, t = env
    nested = tmp_path / "deep" / "dir"
    nested.mkdir(parents=True)
    src = nested / "evidence.png"
    src.write_bytes(b"data")
    res = wf.attach_file(t["id"], str(src))
    assert res["name"] == "evidence.png"


def test_attach_file_follows_symlink_to_a_real_file(env, tmp_path):
    """A symlink pointing at a REAL file is resolved (realpath) and uploaded — a screenshot dir can
    legitimately be symlinked; only the target's basename is used for the name."""
    api, wf, t = env
    target = tmp_path / "real.png"
    target.write_bytes(b"pngbytes")
    link = tmp_path / "link.png"
    link.symlink_to(target)
    res = wf.attach_file(t["id"], str(link))
    assert res["attached"] is True and res["size"] == len(b"pngbytes")


def test_attach_file_needs_no_ownership_so_a_reviewer_can_attach(tmp_path):
    """Unlike advance/call_human, attach_file does NOT require the task be yours: a reviewer
    attaching a screenshot to SOMEONE ELSE's task in Review must work — only board membership is
    checked, symmetric with download_attachment."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    other = {"id": 999, "username": "someone-else"}
    t = api.add_task("не моя", "Review", assignee=other)      # assigned to another user
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    res = wf.attach_file(t["id"], str(src))                   # must not raise "not assigned to you"
    assert res["attached"] is True


def test_attach_file_unknown_task_is_actionable(env, tmp_path):
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    with pytest.raises(WorkflowError, match="not found"):
        wf.attach_file(987654, str(src))


# --- вложения: журнальный след аплоада в комментах (#184) -----------------------------


def test_attach_file_journals_the_upload_as_an_attach_comment(env, tmp_path):
    """#184: a successful upload leaves a TRACE in the comment journal — the human browsing the
    comments sees '[attach] shot.png (image/png, 2.0 КБ)' in the stream instead of having to
    discover the file in the attachments widget. Name, mime and human-readable size are all in
    the comment; without a note there is no dangling ' — ' separator."""
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"x" * 2048)
    res = wf.attach_file(t["id"], str(src))
    assert res["journal_comment"] is True
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert journal == ["[attach] shot.png (image/png, 2.0 КБ)"]


def test_attach_file_note_lands_in_the_journal_comment(env, tmp_path):
    """The agent says WHAT the file shows via note= — it rides in the SAME journal comment, so
    the human reads 'бот приложил board.png — доска после reconcile' as part of the story, not
    as two disconnected entries."""
    api, wf, t = env
    src = tmp_path / "board.png"
    src.write_bytes(b"png")
    wf.attach_file(t["id"], str(src), note="доска после reconcile")
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert len(journal) == 1
    assert "board.png" in journal[0]
    assert journal[0].endswith("— доска после reconcile")


def test_attach_file_blank_note_is_ignored(env, tmp_path):
    """A whitespace-only note is not a note: the journal line stays clean (no trailing ' — ')."""
    api, wf, t = env
    src = tmp_path / "s.png"
    src.write_bytes(b"png")
    wf.attach_file(t["id"], str(src), note="   ")
    journal = [c for c in api.comments_text(t["id"]) if c.startswith("[attach]")]
    assert journal == ["[attach] s.png (image/png, 3 Б)"]


def test_attach_file_journal_comment_failure_never_fails_the_upload(env, tmp_path, monkeypatch):
    """The journal comment is posted AFTER the upload has already landed, so its failure must NOT
    surface as a tool error: {'error': ...} reads as 'the attach failed' and provokes a blind
    retry that would DUPLICATE the attachment. Instead the result keeps attached=True, flags
    journal_comment=False, and the note says exactly what to do (don't re-upload; comment()
    manually if the trace matters)."""
    api, wf, t = env

    def boom(task_id, text):
        raise VikunjaError(500, "comments down")

    monkeypatch.setattr(api, "add_comment", boom)
    src = tmp_path / "shot.png"
    src.write_bytes(b"png")
    res = wf.attach_file(t["id"], str(src))          # must not raise
    assert res["attached"] is True
    assert res["journal_comment"] is False
    assert "re-upload" in res["note"]                # actionable: the file IS there, don't retry
    dossier = wf.get_task(t["id"])
    assert [a["name"] for a in dossier["attachments"]] == ["shot.png"]


def test_human_size_units():
    """Journal sizes are human-readable (Б/КБ/МБ) — a human reads '1.4 МБ', not 1468006."""
    assert _human_size(512) == "512 Б"
    assert _human_size(2048) == "2.0 КБ"
    assert _human_size(5 * 1024 * 1024) == "5.0 МБ"


# --- вложения: hardening (#146) — sanitize имени, post-read caps, id-confusion --------


def test_safe_attachment_name_strips_nul_and_controls():
    """A server-controlled attachment name can carry a NUL/C0-control/DEL byte (which makes open()
    raise ValueError) or run past the filesystem's ~255-byte limit (open() -> OSError); the sanitizer
    neutralizes both while keeping the traversal-stripping (basename only) and the extension."""
    dirty = _safe_attachment_name("he\x00l\x01lo\x7f\n.png", "fallback.bin")
    assert not any(c in dirty for c in "\x00\x01\x7f\n")     # control bytes gone
    assert _safe_attachment_name("shot.png", "fallback.bin") == "shot.png"   # normal untouched
    assert _safe_attachment_name("../../etc/evil.png", "fallback.bin") == "evil.png"  # traversal
    assert _safe_attachment_name("\x00", "fallback.bin") == "fallback.bin"   # empty after strip
    long = _safe_attachment_name("a" * 300 + ".png", "fallback.bin")
    assert len(long.encode("utf-8")) <= _MAX_ATTACHMENT_NAME_BYTES            # within the budget
    assert long.endswith(".png")                             # extension preserved


def test_download_attachment_server_name_with_nul_does_not_crash(env, att_root):
    """A server attachment name carrying a NUL byte must NOT crash the download — open() raises
    ValueError on a NUL in a path. The byte is stripped and the EXACT bytes still land on disk."""
    api, wf, t = env
    data = b"\x89PNG\r\n\x1a\nbody"
    att = api.add_attachment(t["id"], "he\x00llo.png", "image/png", data)
    res = wf.download_attachment(t["id"], att["id"])         # must not raise
    base = os.path.basename(res["path"])
    assert "\x00" not in base
    with open(res["path"], "rb") as fh:
        assert fh.read() == data                             # exact bytes despite the dirty name


def test_download_attachment_server_name_over_255_bytes_is_truncated(env, att_root):
    """A pathologically long server name (open() would OSError 'File name too long') is truncated
    to the byte budget while keeping the extension, so the file is actually written to disk."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "a" * 300 + ".png", "image/png", b"pngbytes")
    res = wf.download_attachment(t["id"], att["id"])         # must not OSError
    base = os.path.basename(res["path"])
    assert len(base.encode("utf-8")) <= _MAX_ATTACHMENT_NAME_BYTES
    assert base.endswith(".png")
    assert os.path.isfile(res["path"])                       # proves open() did not fail


def test_download_attachment_post_read_cap_catches_lying_metadata(env, att_root, monkeypatch):
    """Second-line defense: the METADATA size is a cheap pre-check but can under-report (or be
    missing/0). After the bytes are actually pulled, len(data) is re-checked against the cap. Here
    metadata lies (5 < cap) yet the real payload is 50 -> refused POST-read. Without the post-read
    check the oversized file would simply be written to a temp file and reported as fine."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    att = api.add_attachment(
        t["id"], "liar.bin", "application/octet-stream", data=b"x" * 50, size=5
    )
    with pytest.raises(WorkflowError, match="cap"):
        wf.download_attachment(t["id"], att["id"])


def test_attach_file_nul_in_path_is_actionable(env, tmp_path):
    """os.path.realpath raises ValueError on a NUL byte in the path; attach_file must surface an
    actionable WorkflowError naming the bad path, never a raw ValueError the agent can't act on."""
    api, wf, t = env
    with pytest.raises(WorkflowError):
        wf.attach_file(t["id"], "/tmp/x\x00y.png")


def test_attach_file_vanishes_between_size_and_read_is_actionable(env, tmp_path, monkeypatch):
    """A TOCTOU window: the file passes the isfile guard, getsize runs, then the file is removed
    before open() -> FileNotFoundError. attach_file must turn that (and any OSError from the
    getsize/open region) into an actionable WorkflowError, not a raw traceback."""
    api, wf, t = env
    src = tmp_path / "shot.png"
    src.write_bytes(b"png-bytes")

    def vanishing_getsize(_path):
        os.remove(str(src))          # simulate the race: file gone after the size check
        return 5

    monkeypatch.setattr("vikunja_mcp.workflow.os.path.getsize", vanishing_getsize)
    with pytest.raises(WorkflowError):
        wf.attach_file(t["id"], str(src))


def test_attach_file_post_read_cap_catches_lying_getsize(env, tmp_path, monkeypatch):
    """Mirror of the download post-read cap: getsize is a cheap pre-check but can lie (the file
    grows between stat and read). After reading, len(data) is re-checked against the cap. Here
    getsize reports 5 (passes the pre-check) but the file is really 50 -> refused POST-read, and
    nothing is uploaded."""
    api, wf, t = env
    monkeypatch.setattr("vikunja_mcp.workflow._MAX_ATTACHMENT_BYTES", 10)
    src = tmp_path / "grower.bin"
    src.write_bytes(b"x" * 50)
    monkeypatch.setattr("vikunja_mcp.workflow.os.path.getsize", lambda _p: 5)  # lie: 5 < cap
    calls = {"n": 0}
    orig = api.upload_attachment

    def spy(*a, **k):
        calls["n"] += 1
        return orig(*a, **k)

    monkeypatch.setattr(api, "upload_attachment", spy)
    with pytest.raises(WorkflowError, match="cap"):
        wf.attach_file(t["id"], str(src))
    assert calls["n"] == 0                                   # refused before any upload persisted


def test_get_task_attachment_id_is_attachment_id_not_file_id(env, att_root):
    """get_task must surface the ATTACHMENT id (task["attachments"][].id), NOT the inner file.id.
    On a real server the two DESYNC (the `files` table advances on any upload); download_attachment
    keys off this id, so emitting file.id would hand the agent an id the download endpoint 404s on.
    GREEN with correct code; mutating get_task to emit a["file"]["id"] makes it RED (proves it
    isn't blind — the #118/#125 lesson)."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "shot.png", "image/png", b"png", file_id=999000)
    assert att["id"] != 999000 and att["file"]["id"] == 999000       # the desync is real
    dossier = wf.get_task(t["id"])
    assert dossier["attachments"][0]["id"] == att["id"]             # the attachment id...
    assert dossier["attachments"][0]["id"] != 999000                # ...never the file id
    res = wf.download_attachment(t["id"], att["id"])                # and it's the downloadable id
    assert res["name"] == "shot.png"


def test_download_attachment_keys_off_attachment_id_not_file_id(env, att_root):
    """download_attachment keys off the ATTACHMENT id, never file.id — the two desync on a real
    server, so a file.id-keyed fetch would pull the wrong file or 404. Downloading by the
    attachment id yields the bytes; the file.id is NOT a valid attachment id -> actionable
    'no attachment'. GREEN with correct code; mutating download to match a["file"]["id"] makes it
    RED."""
    api, wf, t = env
    att = api.add_attachment(t["id"], "shot.png", "image/png", b"png", file_id=999000)
    res = wf.download_attachment(t["id"], att["id"])               # by attachment id -> the bytes
    with open(res["path"], "rb") as fh:
        assert fh.read() == b"png"
    with pytest.raises(WorkflowError, match="no attachment"):
        wf.download_attachment(t["id"], 999000)                    # file.id is not an attachment id
