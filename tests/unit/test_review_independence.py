"""#37 and #991: `require_review_independence` — the repo-toml flag that promotes "you do not
review your own work" from a convention to a GATE, and the ONE switch both surfaces now read.

#37 put the gate on `review_task`. #991 put the same flag on next_task's OFFER, which had been
skipping the caller's own cards unconditionally — so the two tools disagreed, next_task refusing
to offer what review_task would accept. Read this file as one flag with two halves; the second
half starts at its own banner below, with its own sweep record.

WHAT THIS FILE HAS TO PROTECT IS BOTH DIRECTIONS AT ONCE, and they pull opposite ways:

* OFF (the default, and what every consumer on `stable` runs today) the behaviour must be what
  it was before the gate existed — BOTH verdicts, the ownerless #705 bounce included. In a solo
  setup the missing authorship check is the CONDITION OF OPERATION, not a hole: one scoped token
  is the whole fleet, so implementer and reviewer are the SAME assignee and independence is
  carried by the agents' separated contexts. A gate that were on by default would refuse every
  review in this repo the moment it shipped.
* ON, a caller listed in the card's own assignees is refused, and refused BEFORE anything is
  written — the multi-identity hole this card was filed for, where the only thing standing
  between a self-approval and the `reviewed` label a human reads for Done is next_task's OFFER
  filter, which a direct call never consults.

Measured before the gate existed (both verdicts, on the fake): a verdict from the card's own
assignee was ACCEPTED, `approve` landing `reviewed` with the card left in Review. That is the
behaviour the OFF tests below keep and the ON tests refuse.

MUTATION SWEEP, run in a separate `git clone --no-hardlinks` with `__pycache__` cleared and
PYTHONDONTWRITEBYTECODE=1, `vikunja_mcp.__file__` printed every round and resolving inside the
clone, ONE selection throughout (this file + test_config.py + test_workflow_gates.py), `-q`
dropped so `collected` is printed, and each round read by COUNTING lines beginning `FAILED ` and
`ERROR ` separately rather than by the first `N failed` in stdout: control (opening) 0 failed, 0
errors, collected 170; deleting the gate's CALL SITE from `review_task` -> 6 failed, 0 errors,
collected 170; gutting the gate BODY so it always returns, call site intact -> 6 failed, 0
errors, collected 170; flipping the config default to True -> 4 failed, 0 errors, collected 170
(one of them `test_reads_repo_toml_and_env_token`, honest collateral — it compares a whole
`Config` against defaults); ALSO reading the flag from the env layers -> 2 failed, 0 errors,
collected 170; reading raw board assignees instead of the stale-tolerant helper -> 1 failed, 0
errors, collected 170, and that ONE is the #885 blackout test, which is the only thing standing
between this gate and a bypass on the exact shape where next_task's offer filter is gone too;
moving the gate to AFTER the approve verdict is written -> 3 failed, 0 errors, collected 170;
control (closing, restored) 0 failed, 0 errors, collected 170, clone clean. Collected is equal
in every round, so each number is a delta against the control and not a different selection.
"""
import inspect

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import claimable_cmd, server, workflow
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

REVIEWER_ID = 77


def _card_in_review(api, wf, *, assigned=True):
    """Drive a card to Review the ordinary way, then (for the ownerless case) clear the
    assignee — the order matters, since `advance` itself requires ownership, so an ownerless
    card can only ever ARRIVE in Review by a human clearing the assignee or hand-placing it,
    which is exactly the #705 shape."""
    task = api.add_task("job", "Design", assignee=api.me_user)
    wf.advance(task["id"], to="build", spec="s")
    wf.advance(task["id"], to="review", worklog="w", evidence="e",
               root_cause="the state was not subscribed to event X")
    if not assigned:
        api.tasks[task["id"]]["assignees"] = []
    return task


def _labels(api, task_id):
    return sorted(lb["title"] for lb in api.tasks[task_id]["labels"])


def _reviewer(api, **kw):
    """A SECOND identity against the same tracker — what a provisioned reviewer token buys.
    Two Workflow objects over one FakeAPI is the fake's model of two MCP server processes."""
    wf = Workflow(api, project_id=3, **kw)
    wf._me_cache = {"id": REVIEWER_ID, "username": "agent-reviewer"}
    return wf


# --- flag OFF: byte-for-byte the pre-gate behaviour -------------------------------------

@pytest.mark.parametrize("verdict,expected_label,expected_stage", [
    ("approve", "reviewed", "Review"),
    ("needs_work", "review-failed", "Build"),
])
def test_off_by_default_a_self_verdict_is_still_accepted(verdict, expected_label,
                                                         expected_stage):
    """The solo path, and the reason the flag exists instead of an unconditional gate: the one
    token IS the assignee, so refusing here would end review for every current consumer."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    assert wf.require_review_independence is False
    task = _card_in_review(api, wf)

    out = wf.review_task(task["id"], verdict=verdict, report="checked by running")

    assert out["verdict"] == verdict
    assert expected_label in _labels(api, task["id"])
    assert api.stage_of(task["id"]) == expected_stage


def test_off_the_ownerless_bounce_still_routes_to_queue():
    """#705 rides on the same method and must not move: an ownerless card has no implementer to
    hand back to, so needs_work goes to QUEUE as free work rather than to Build."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    task = _card_in_review(api, wf, assigned=False)

    out = _reviewer(api).review_task(task["id"], verdict="needs_work", report="r")

    assert out["moved_to"] == "Queue"
    assert api.stage_of(task["id"]) == "Queue"


def test_off_the_gate_never_resolves_the_caller_identity():
    """OFF must cost NOTHING, not merely behave the same: the gate returns before touching
    `me()`, so no request is added to a path an external supervisor drives. Pinned by leaving
    the identity cache unpopulated and making a real `me()` call fail loudly."""
    api = FakeAPI(buckets=STAGES)
    author = Workflow(api, project_id=3)
    task = _card_in_review(api, author)

    # A FRESH Workflow, so the cache is empty the way a reviewer process's would be — the
    # card-building advances above necessarily resolved the author's identity already.
    wf = Workflow(api, project_id=3)

    def _boom():
        raise AssertionError("review_task resolved me() with the gate off")

    api.me = _boom
    assert wf._me_cache is None
    wf.review_task(task["id"], verdict="approve", report="r")
    assert wf._me_cache is None


# --- flag ON: the assignee is refused, a distinct identity passes -----------------------

@pytest.mark.parametrize("verdict", ["approve", "needs_work"])
def test_on_a_verdict_from_the_cards_own_assignee_is_refused(verdict):
    """BOTH verdicts, because they are separate code paths and only `approve` writes the label
    a human reads for Done — a gate that caught approve alone would still let an author bounce
    their own card back to themselves."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, wf)

    with pytest.raises(WorkflowError, match="cannot review it"):
        wf.review_task(task["id"], verdict=verdict, report="r")


@pytest.mark.parametrize("verdict", ["approve", "needs_work"])
def test_on_the_refusal_writes_nothing_at_all(verdict):
    """The gate sits before the verdict comment, the labels and the move, so a refused call
    leaves the card exactly as it was. A gate that refused AFTER commenting would leave a
    verdict-shaped comment with no verdict behind it — worse than no gate."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, wf)
    before_labels = _labels(api, task["id"])
    before_comments = len(api.comments_text(task["id"]))

    with pytest.raises(WorkflowError):
        wf.review_task(task["id"], verdict=verdict, report="r")

    assert _labels(api, task["id"]) == before_labels
    assert "reviewed" not in _labels(api, task["id"])
    assert len(api.comments_text(task["id"])) == before_comments
    assert api.stage_of(task["id"]) == "Review"


@pytest.mark.parametrize("verdict,expected_label,expected_stage", [
    ("approve", "reviewed", "Review"),
    ("needs_work", "review-failed", "Build"),
])
def test_on_a_distinct_reviewer_identity_passes(verdict, expected_label, expected_stage):
    """The other half of the gate, and the half that makes it usable: with the flag on, the
    provisioned reviewer token reviews exactly as before. Without this the gate would be
    indistinguishable from "review_task is broken"."""
    api = FakeAPI(buckets=STAGES)
    implementer = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, implementer)

    out = _reviewer(api, require_review_independence=True).review_task(
        task["id"], verdict=verdict, report="reproduced and checked"
    )

    assert out["verdict"] == verdict
    assert expected_label in _labels(api, task["id"])
    assert api.stage_of(task["id"]) == expected_stage


def test_on_a_genuinely_ownerless_card_is_still_reviewable():
    """With nobody on the card there is no author to exclude, so the gate must NOT fire — and
    the #705 routing must survive it. Otherwise turning the flag on would strand exactly the
    cards a human hand-placed in Review, which no identity could then ever review."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, wf, assigned=False)

    out = _reviewer(api, require_review_independence=True).review_task(
        task["id"], verdict="needs_work", report="r"
    )

    assert out["moved_to"] == "Queue"


def test_on_the_gate_survives_a_blacked_out_kanban_copy():
    """#885 measured the BOARD copy of a card coming back with an empty `assignees` while the
    card really is assigned. Judged off that copy the gate would find nobody and PASS — on
    precisely the shape where the other protection is gone too, since the same blackout also
    deletes next_task's offer filter (it stops recognising the card as the caller's own and
    offers it to its own author). So the gate reads through the stale-tolerant helper, which
    re-reads /tasks/<id> when the copy is empty. Built by blanking the board copy while the
    authoritative task keeps its assignee."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, wf)
    real = api.tasks[task["id"]]["assignees"]
    assert [a["id"] for a in real] == [api.me_user["id"]]

    board_copy = {**api.tasks[task["id"]], "assignees": []}
    api.get_task = lambda tid, _t=api.tasks[task["id"]]: _t
    wf._find_task = lambda tid, *a, **k: (board_copy, "Review")

    with pytest.raises(WorkflowError, match="cannot review it"):
        wf.review_task(task["id"], verdict="approve", report="r")


def test_on_in_a_solo_setup_nobody_can_review_and_the_refusal_says_so():
    """The flag's NAMED COST, pinned rather than hidden. Solo means the only token is always
    the assignee, so with the flag on every card becomes unreviewable — by design (it is a hard
    gate), which is why the default is off and why a project turns it on in the same step it
    provisions a second identity. What must not happen is that cost arriving as a bare "not
    allowed": the refusal has to name the flag, the fix (a second token) and the way back out
    (remove the key), or an operator who set it prematurely cannot tell a policy from a bug."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    task = _card_in_review(api, wf)

    with pytest.raises(WorkflowError) as err:
        wf.review_task(task["id"], verdict="approve", report="r")

    msg = str(err.value)
    assert "require_review_independence" in msg
    assert "tracker-reviewer" in msg
    assert "nobody" in msg


# --- the SAME flag on next_task's OFFER, #991 -------------------------------------------
#
# The defect these pin is a DIVERGENCE between two tools, not a missing feature. Until #991
# the offer filter skipped a card assigned to the caller UNCONDITIONALLY, while `review_task`
# accepted that very verdict whenever the flag was off (the first test in this file). So
# next_task refused to OFFER what review_task would happily RECORD, and in a solo setup —
# where CLAUDE.md calls one scoped token the condition of operation — every card in Review is
# the caller's, so nothing was ever offered and `claimable`'s kind='review' was unreachable.
# Both surfaces now read the same flag.
#
# MUTATION SWEEP for #991, one selection throughout (this file + test_claimable_cmd.py +
# test_skill_contract.py + test_workflow_gates.py), `__pycache__` cleared and
# PYTHONDONTWRITEBYTECODE=1 each round, `vikunja_mcp.__file__` printed every round and resolving
# inside this checkout, `-q` dropped so `collected` is printed, and each round read by COUNTING
# lines beginning `FAILED ` and `ERROR ` separately rather than by the first `N failed` in
# stdout: control (opening) 0 failed, 0 errors, collected 223; the authorship skip made
# UNCONDITIONAL again, i.e. the pre-#991 code -> 6 failed, 0 errors, collected 223; the
# authorship skip DELETED outright, so the flag stops mattering in either direction -> 2 failed,
# 0 errors, collected 223; the sort reverted to a bare `-priority`, dropping not-mine-first
# -> 1 failed, 0 errors, collected 223; the worklog-FRESHNESS check deleted -> 5 failed, 0
# errors, collected 223, which is what makes the dogfood board in test_claimable_cmd.py
# non-vacuous now that authorship no longer filters it; control (closing, restored) 0 failed, 0
# errors, collected 223. Collected is equal in every round, so each number is a delta against
# the control and not a different selection.


def _offerable(api, wf, *, assignee=None, priority=0, title="job"):
    """A card sitting in Review with a [worklog] and no verdict — the shape the offer branch
    accepts. Built through the API rather than through `advance` so the card can belong to an
    identity this Workflow does not authenticate as."""
    task = api.add_task(title, "Review", priority=priority,
                        assignee=assignee if assignee is not None else api.me_user)
    api.add_comment(task["id"], "[worklog] did the work, checked by running")
    return task


def test_off_a_card_in_review_is_offered_to_its_own_assignee():
    """THE SOLO PATH, and the whole point of #991. With the flag off the offer filter must not
    hide the card from the identity that implemented it: independence is carried by the agents'
    separated CONTEXTS (a sibling reviewer dispatched with a fresh context), which nothing
    server-side can observe — the same reading that makes `review_task` accept the verdict.
    Measured before the fix: three next_task ticks on ONE such card answered "the queue is
    empty — no work for the agent" every time."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    assert wf.require_review_independence is False
    task = _offerable(api, wf)

    out = wf.next_task()

    assert out.get("review") is True, out
    assert out["stage"] == "Review"
    assert out["task"]["id"] == task["id"]


def test_on_a_card_in_review_is_still_hidden_from_its_own_assignee():
    """The other direction, and the one that must NOT move: with the flag on, authorship is a
    GATE, so offering the author their own card would advertise work `review_task` is about to
    refuse. This is the pin that makes the fix conditional rather than a deletion."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, require_review_independence=True)
    _offerable(api, wf)

    out = wf.next_task()

    assert out.get("task") is None, out
    assert out.get("review") is None


def test_off_someone_elses_card_is_offered_before_your_own():
    """The residual #991 opens and what closes most of it. `require_review_independence = false`
    does NOT mean "solo" — it means the key was never set, which is also every MULTI-IDENTITY
    repo that never opted in. There the offer filter was the only thing keeping an author away
    from their own card (`_require_review_independence`'s docstring says so). Making the offer
    unconditional would hand it straight back, so the sort prefers cards that are NOT yours;
    your own arrives only once no one else's is left. Priority is deliberately stacked AGAINST
    the expected answer, so a test that passed on the old `-priority` sort alone cannot pass
    here by accident."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3)
    other = {"id": 77, "username": "agent-impl"}
    mine = _offerable(api, wf, priority=5, title="mine, urgent")
    theirs = _offerable(api, wf, assignee=other, priority=1, title="theirs, ordinary")

    first = wf.next_task()
    assert first["task"]["id"] == theirs["id"], first

    # and mine is not LOST, only deprioritised: exclude theirs and it comes forward.
    second = wf.next_task(exclude=[theirs["id"]])
    assert second["task"]["id"] == mine["id"], second


# --- the AGENT-FACING copy has to move with the branch, #991 round 2 ------------------------
#
# WHY THESE EXIST AT ALL. Round 1 changed the branch, the rulebook and its references, and
# still left `next_task`'s own tool docstring saying the offer is "not your own" — read by
# every agent in every session, and the single most likely place to be believed. It survived
# because NOTHING pinned it: the rulebook has test_skill_contract, the dossier is prose nobody
# executes, and the tool docstrings had no tie to the code they describe. An independent
# reviewer found it by reading; that is not a mechanism, so here is one.
#
# The tie is deliberately to the CODE, not to a wording: each test first establishes what the
# branch actually does, and only then requires the copy to agree. Delete the condition and
# these fail for the same reason the prose would become false.
#
# MUTATION SWEEP for these two, one selection throughout (this file + test_claimable_cmd.py +
# test_skill_contract.py + test_server.py), `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1
# each round, `-q` dropped so `collected` is printed, rounds read by COUNTING lines beginning
# `FAILED ` and `ERROR ` separately: control (opening) 0 failed, 0 errors, collected 178; the
# next_task tool docstring reverted to its pre-#991 «and not your own» -> 1 failed, 0 errors,
# collected 178; the claimable header crediting authorship again for the 2026-07-14 board -> 1
# failed, 0 errors, collected 178; the offer branch itself made unconditional, i.e. the copy
# left true and the CODE broken -> 6 failed, 0 errors, collected 178; control (closing,
# restored) 0 failed, 0 errors, collected 178. Collected is equal in every round. The first two
# rounds are the point: BOTH of those wordings shipped in round 1 of this card and no round
# would have gone red, which is why an independent reviewer had to find them by reading.


def _offer_branch_source() -> str:
    src = inspect.getsource(workflow.Workflow.next_task)
    return src[src.index('for t in sorted(board.get("Review", [])'):]


def test_the_next_task_tool_docstring_agrees_with_the_offer_branch():
    """CLAUDE.md calls tool docstrings agent-facing RULES, to be kept prescriptive — so a stale
    one is not a documentation nit, it is a rule that lies. This one lying costs money: an agent
    told the offer is never its own card will not cast a verdict on it, nothing then trips the
    freshness guard, and `claimable` answers kind='review' on every poll — the 2026-07-14
    no-op boot loop, rebuilt out of prose."""
    assert "self.require_review_independence and my_id in self._assignee_ids(t)" \
        in _offer_branch_source(), \
        "the offer branch stopped being conditional — fix this test WITH the docstring"

    doc = server.next_task.__doc__
    assert doc, "next_task lost its docstring — that IS the agent's copy of this rule"
    assert "not your own" not in doc, \
        "the tool docstring still says the review offer excludes your own card, which the " \
        "branch above no longer does with the flag off (its default)"
    assert "YOUR OWN INCLUDED" in doc, \
        "the tool docstring no longer states the fact an agent must act on: it WILL be offered " \
        "its own card, and it is expected to review it from a fresh context"
    assert "require_review_independence" in doc, \
        "the docstring states the default but not the flag that changes it"
    assert "exclude" in doc, \
        "the docstring omits the cost of the re-offer — a card stays in this lane until a " \
        "verdict lands, so a dispatched review must be excluded for the rest of the tick"


def test_the_claimable_module_docstring_no_longer_credits_authorship():
    """The same stale sentence lived in `claimable_cmd`'s own header, where it explains the
    $105/day incident to whoever next touches the cross-repo contract. Left alone it would
    teach the opposite of the fix: that an own card in Review is by definition nothing to do."""
    doc = claimable_cmd.__doc__
    assert doc, "claimable_cmd lost its module docstring"
    assert "you never independently review your own work" not in doc, \
        "the incident write-up still credits the authorship filter for keeping that board " \
        "quiet; since #991 it is worklog freshness, and the difference is the whole fix"
    assert "FRESHNESS" in doc, "the write-up does not name the guard that actually holds it"
