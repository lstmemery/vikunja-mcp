"""#37: `require_review_independence` — the repo-toml flag that promotes "you do not review
your own work" from a convention to a GATE on `review_task`.

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
import pytest

from tests.unit.fakes import FakeAPI
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
