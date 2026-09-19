"""delegated_move — the scoped, per-card, user-instruction-gated delegation (delegation.py).

The gates, each driven on the real Workflow over FakeAPI:
  * not armed -> refuse (policy None, or armed=False)
  * action outside the closed allowlist -> refuse by name
  * record file missing / unparsable -> refuse, naming the path
  * no matching record / expired record -> refuse
  * mark-done: audit comment posted BEFORE the move; a failed audit write refuses and
    the card is NOT moved
  * mark-done self-certification guard: a card created by the agent's own account
    refuses unless the independent `reviewed` label is present — unless the
    deployment set VIKUNJA_DELEGATION_AGENT_MARK_DONE, which lifts ONLY that
    created_by/verdict read (instruction + record + audit stay mandatory, and the
    already-Done / Icebox / expiry / audit-failure refusals stay put)
  * triage-to-queue: Backlog -> Queue only, card stays UNASSIGNED, audit posted after
  * move-stage (2026-09-18 captain widening): ANY column-to-column move on the user's
    recorded instruction — target named in the record's `stage` key; Done in BOTH
    directions and Icebox reachable on instruction; a move TO Done passes the
    self-certification guard (and its opt-out); moves touching the Done boundary
    audit BEFORE the move; unknown stage names and no-op targets refuse
  * add-label / clear-label: verdict labels (reviewed / review-failed) and epic refuse
  * a Done card refuses every non-mark-done action (closed work stays closed)
  * config: armed mode reads ONLY the designated env-delegated token and refuses the
    shared agent token; unarmed mode keeps the ordinary chain

Record ids: FakeAPI's id counter is shared with buckets, so every record is written
from the ACTUAL task id after the card exists — never a guessed number.
"""

from datetime import datetime, timedelta, timezone

import httpx
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.api import VikunjaError
from vikunja_mcp.config import ConfigError, load_config
from vikunja_mcp.delegation import (
    ACTIONS,
    PROTECTED_LABELS,
    AuthorizedMove,
    DelegationPolicy,
    audit_text,
    find_authorized,
    load_delegation,
    parse_authorized_file,
)
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

NOW = datetime(2026, 9, 16, 12, 0, tzinfo=timezone.utc)


def _iso(dt):
    return dt.strftime("%Y-%m-%dT%H:%M:%SZ")


def live_window():
    """(authorized_at, expires) strings around REAL now — the fixtures that must be
    live when the record is read. Hard-coded dates date-rot: the 2026-09-16/17 pair
    was already expired the day after it was written, which turned twenty tests red
    overnight without a single line of production code changing. Every fixture that
    needs a LIVE record derives its window from the clock; only tests that assert the
    expiry refusal itself use fixed past dates."""
    now = datetime.now(timezone.utc)
    return _iso(now - timedelta(hours=2)), _iso(now + timedelta(days=1))


def record_block(
    task_id,
    action,
    label=None,
    instruction="close card 968 after review",
    evidence=None,
    authorized_at=None,
    expires=None,
    stage=None,
):
    if authorized_at is None or expires is None:
        d_authorize, d_expire = live_window()
        authorized_at = authorized_at or d_authorize
        expires = expires or d_expire
    parts = ["[[authorized_move]]", f"task_id = {task_id}", f'action = "{action}"']
    if label is not None:
        parts.append(f'label = "{label}"')
    if stage is not None:
        parts.append(f'stage = "{stage}"')
    parts.append(f'instruction = "{instruction}"')
    if evidence is not None:
        parts.append(f'evidence = "{evidence}"')
    parts += [f"authorized_at = {authorized_at}", f"expires = {expires}", ""]
    return "\n".join(parts)


def rig(tmp_path, *, record=None, armed=True):
    """(api, workflow) on a fresh board. record=None -> an EMPTY record file. Pass a
    (task_id, block) pair to write a real record first."""
    api = FakeAPI(buckets=STAGES)
    path = tmp_path / "delegation-authorized.toml"
    path.write_text(record[1] if record else "")
    pol = (
        DelegationPolicy(armed=True, authorized_file=path)
        if armed
        else DelegationPolicy(
            armed=False,
            authorized_file=path,
        )
    )
    return api, Workflow(api, project_id=3, delegation=pol)


def armed_workflow_for(api, tmp_path, task_id, action, label=None, **kw):
    """A Workflow armed with a record written AFTER the card exists, so the task_id is
    the card's real one."""
    path = tmp_path / "delegation-authorized.toml"
    path.write_text(record_block(task_id, action, label=label, **kw))
    return Workflow(
        api, project_id=3, delegation=DelegationPolicy(armed=True, authorized_file=path)
    )


def test_not_armed_refuses_before_anything_else(tmp_path):
    api, w = rig(tmp_path, armed=False)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "NOT armed" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")
    assert api.comments_text(task["id"]) == []


def test_delegation_policy_none_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    w = Workflow(api, project_id=3)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "NOT armed" in str(exc.value)


def test_unknown_action_refuses_naming_the_allowlist(tmp_path):
    api, w = rig(tmp_path, record=(0, ""))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "delete")
    assert ", ".join(sorted(ACTIONS)) in str(exc.value)


def test_missing_record_file_refuses_naming_the_path(tmp_path):
    api = FakeAPI(buckets=STAGES)
    w = Workflow(
        api,
        project_id=3,
        delegation=DelegationPolicy(
            armed=True,
            authorized_file=tmp_path / "absent.toml",
        ),
    )
    task = api.add_task("a card", "Backlog")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "triage-to-queue")
    assert "absent.toml" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Backlog")


def test_unparsable_record_refuses_rather_than_reading_as_empty(tmp_path):
    api, w = rig(tmp_path, record=(0, "[authorized_move]\nnot valid toml =\n"))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "does not parse" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_no_matching_record_refuses(tmp_path):
    api, w = rig(tmp_path, record=(0, record_block(999, "mark-done", evidence="e")))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "no [[authorized_move]] record matches" in str(exc.value)


def test_expired_record_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    past = datetime.now(timezone.utc) - timedelta(days=2)
    w = armed_workflow_for(
        api,
        tmp_path,
        task["id"],
        "mark-done",
        evidence="ran it",
        authorized_at=_iso(past - timedelta(days=1)),
        expires=_iso(past),
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "expired at" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_mark_done_human_authored_happy_path_audits_before_the_move(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Review", assignee=api.me_user)
    w = armed_workflow_for(
        api, tmp_path, task["id"], "mark-done",
        instruction="close it — I checked the review myself", evidence="ran it",
    )
    result = w.delegated_move(task["id"], "mark-done")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")
    comments = api.comments_text(task["id"])
    assert comments, "the audit comment must land"
    assert comments[0].startswith("[delegated-move]")
    assert "close it — I checked the review myself" in comments[0]
    assert "evidence: ran it" in comments[0]


def test_mark_done_refuses_self_authored_card_without_a_verdict(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("agent's own card", "Review", assignee=api.me_user, created_by="me")
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "certifying its own work" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")
    assert api.comments_text(task["id"]) == []


def test_mark_done_self_authored_card_passes_once_reviewed_landed(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task(
        "agent's own card", "Review", assignee=api.me_user, created_by="me", labels=("reviewed",)
    )
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    result = w.delegated_move(task["id"], "mark-done")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


# --- the self-certification opt-out (VIKUNJA_DELEGATION_AGENT_MARK_DONE) ---


def agent_mark_done_workflow(api, tmp_path, task_id, action, label=None, **kw):
    """An armed Workflow whose delegation policy carries the self-certification opt-out.
    Everything else is the ordinary armed rig: the record is written AFTER the card
    exists, so the task_id is the card's real one."""
    path = tmp_path / "delegation-authorized.toml"
    path.write_text(record_block(task_id, action, label=label, **kw))
    return Workflow(
        api, project_id=3,
        delegation=DelegationPolicy(
            armed=True, authorized_file=path, agent_mark_done=True,
        ),
    )


def test_opt_out_lets_the_users_recorded_instruction_close_an_agent_authored_card(tmp_path):
    """The captain's case: an errand card the agent filed from chat has no review flow
    to pass; on the user's explicit instruction (quoted in the record, audited on the
    card) the deployment's opt-out turns that instruction into the certification."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("toilet seat", "Queue", assignee=api.me_user, created_by="me")
    w = agent_mark_done_workflow(
        api, tmp_path, task["id"], "mark-done",
        instruction="toilet seat already repaired — close it",
        evidence="confirmed repaired in chat",
    )
    result = w.delegated_move(task["id"], "mark-done")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")
    comments = api.comments_text(task["id"])
    assert comments, "the audit comment must still land"
    assert comments[0].startswith("[delegated-move]")
    assert "toilet seat already repaired — close it" in comments[0]
    assert "evidence: confirmed repaired in chat" in comments[0]


def test_opt_out_still_requires_the_record_and_the_instruction(tmp_path):
    """The opt-out lifts ONLY the created_by/verdict read — the record file with the
    quoted instruction stays mandatory, so a flag-on deployment has not bought a
    blanket Done grant."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("errand", "Queue", assignee=api.me_user, created_by="me")
    path = tmp_path / "delegation-authorized.toml"
    path.write_text("")          # armed, flag on, but no record at all
    w = Workflow(
        api, project_id=3,
        delegation=DelegationPolicy(
            armed=True, authorized_file=path, agent_mark_done=True,
        ),
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "does not parse" in str(exc.value)
    assert "no [[authorized_move]]" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")
    assert api.comments_text(task["id"]) == []


def test_opt_out_still_refuses_an_expired_record_on_an_agent_authored_card(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("errand", "Queue", assignee=api.me_user, created_by="me")
    past = datetime.now(timezone.utc) - timedelta(days=2)
    w = agent_mark_done_workflow(
        api, tmp_path, task["id"], "mark-done",
        authorized_at=_iso(past - timedelta(days=1)), expires=_iso(past),
        evidence="ran it",
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "expired" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")


def test_opt_out_does_not_lift_the_already_done_refusal(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done", assignee=api.me_user, created_by="me")
    w = agent_mark_done_workflow(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "already in Done" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_opt_out_does_not_lift_the_icebox_refusal(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("frozen", "Icebox", created_by="me")
    w = agent_mark_done_workflow(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "Icebox" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Icebox")


def test_opt_out_still_refuses_when_the_audit_write_fails(tmp_path, monkeypatch):
    """An unaudited Done is exactly what the feature must not produce — the opt-out
    changes who certifies, never whether the trail exists."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("errand", "Queue", assignee=api.me_user, created_by="me")
    w = agent_mark_done_workflow(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    calls = []

    def failing_add_comment(task_id, text):
        calls.append(task_id)
        raise VikunjaError(500, "comment refused")

    monkeypatch.setattr(api, "add_comment", failing_add_comment)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "audit comment" in str(exc.value)
    assert calls == [task["id"]], "the audit fires BEFORE the move, so the card stays put"
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")


def test_mark_done_card_already_in_done_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "already in Done" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_mark_done_icebox_card_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("frozen", "Icebox")
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "Icebox" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Icebox")


def test_mark_done_audit_write_failure_refuses_and_leaves_the_card(tmp_path, monkeypatch):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Review", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    calls = []
    real = api.add_comment

    def failing_add_comment(task_id, text):
        if not calls:
            calls.append(task_id)
            raise httpx.ConnectError("tracker unreachable")
        return real(task_id, text)

    monkeypatch.setattr(api, "add_comment", failing_add_comment)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done")
    assert "card was NOT moved" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")
    assert calls == [task["id"]]


def test_mark_done_from_build_also_reaches_done_on_instruction(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("stuck build card", "Build", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="sha abc1234")
    result = w.delegated_move(task["id"], "mark-done")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


# --- move-stage: the 2026-09-18 captain widening (any move on his instruction) ---


def move_stage_workflow(api, tmp_path, task_id, stage, instruction, agent_mark_done=False):
    """An armed Workflow whose record authorizes ONE move-stage to `stage` for the
    card (written after the card exists, so the id is the card's real one)."""
    path = tmp_path / "delegation-authorized.toml"
    path.write_text(
        record_block(task_id, "move-stage", stage=stage, instruction=instruction)
    )
    return Workflow(
        api, project_id=3,
        delegation=DelegationPolicy(
            armed=True, authorized_file=path, agent_mark_done=agent_mark_done,
        ),
    )


def test_move_stage_moves_between_working_columns_on_instruction(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("stuck card", "Build", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Review", "send it back to review")
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Review"
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")
    comments = api.comments_text(task["id"])
    assert comments[-1].startswith("[delegated-move]")
    assert "send it back to review" in comments[-1]


def test_move_stage_keeps_the_assignee_untouched(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Queue", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Design", "take it into design")
    w.delegated_move(task["id"], "move-stage")
    assert api.get_task(task["id"])["assignees"] == [api.me_user]


def test_move_stage_into_done_on_instruction(tmp_path):
    """The captain's case, general form: an errand card parked anywhere moves to Done
    on his instruction; the audit comment lands BEFORE the move (Done-boundary
    order)."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("anniversary card", "Queue", assignee=api.me_user)
    w = move_stage_workflow(
        api, tmp_path, task["id"], "Done", "anniversary card is done — close it"
    )
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")
    comments = api.comments_text(task["id"])
    assert comments, "the audit comment must land"
    assert comments[0].startswith("[delegated-move]")


def test_move_stage_out_of_done_reopens_on_instruction(tmp_path):
    """The reopen the human-only rule used to reserve: on the user's recorded
    instruction the delegated token moves a closed card back to the board, audited
    BEFORE the move."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed too soon", "Done")
    w = move_stage_workflow(api, tmp_path, task["id"], "Queue", "reopen it — more to do")
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Queue"
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")
    comments = api.comments_text(task["id"])
    assert comments and comments[0].startswith("[delegated-move]")


def test_move_stage_out_of_icebox_on_instruction(tmp_path):
    """Card 956's refusal, lifted on the captain's instruction: his order IS the human
    call the freezer gate wanted, quoted verbatim in the record and audited on the
    card."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("frozen errand", "Icebox")
    w = move_stage_workflow(api, tmp_path, task["id"], "Queue", "thaw it and book it")
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Queue"
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")
    comments = api.comments_text(task["id"])
    assert comments and comments[-1].startswith("[delegated-move]")


def test_move_stage_into_icebox_on_instruction(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("someday", "Backlog")
    w = move_stage_workflow(api, tmp_path, task["id"], "Icebox", "park it for winter")
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Icebox"
    assert api.task_bucket[task["id"]] == api.bucket_id("Icebox")


def test_move_stage_unknown_stage_name_refuses_by_name(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Doing", "move it along")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "not a column of this board" in str(exc.value)
    assert "Doing" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_move_stage_to_the_current_stage_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Build", "move it to build")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "already in 'Build'" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_move_stage_to_done_refuses_self_authored_card_without_opt_out(tmp_path):
    """The widening does not certify agent work: a move to Done on a card the agent's
    own account created passes the SAME self-certification guard mark-done has."""
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("agent's own card", "Queue", assignee=api.me_user, created_by="me")
    w = move_stage_workflow(api, tmp_path, task["id"], "Done", "close it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "certifying its own work" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")
    assert api.comments_text(task["id"]) == []


def test_move_stage_to_done_passes_self_authored_card_with_opt_out(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("toilet seat", "Icebox", created_by="me")
    w = move_stage_workflow(
        api, tmp_path, task["id"], "Done", "repaired last week — close it",
        agent_mark_done=True,
    )
    result = w.delegated_move(task["id"], "move-stage")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")
    comments = api.comments_text(task["id"])
    assert comments and comments[0].startswith("[delegated-move]")


def test_move_stage_to_done_audit_failure_refuses_and_leaves_the_card(
    tmp_path, monkeypatch,
):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Queue", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Done", "close it")
    monkeypatch.setattr(
        api, "add_comment", lambda tid, text: (_ for _ in ()).throw(VikunjaError(403, "no"))
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "card was NOT moved" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")


def test_move_stage_audit_failure_after_move_demands_a_manual_audit(
    tmp_path, monkeypatch,
):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = move_stage_workflow(api, tmp_path, task["id"], "Review", "send it back")
    monkeypatch.setattr(
        api, "add_comment", lambda tid, text: (_ for _ in ()).throw(VikunjaError(403, "no"))
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "MOVED but its audit comment failed" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")


def test_move_stage_record_requires_the_stage_key(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'move-stage'\n"
        "instruction = 'x'\nauthorized_at = 2026-09-16T00:00:00Z\n"
        "expires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "move-stage requires stage" in str(exc.value)


def test_parse_refuses_stage_on_non_move_actions(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "stage = 'Done'\ninstruction = 'x'\nevidence = 'y'\n"
        "authorized_at = 2026-09-16T00:00:00Z\nexpires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "stage is only meaningful for move-stage" in str(exc.value)


def test_parse_accepts_a_move_stage_record(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 956\naction = 'move-stage'\n"
        "stage = 'Queue'\ninstruction = 'thaw 956'\n"
        "authorized_at = 2026-09-16T00:00:00Z\nexpires = 2026-09-17T00:00:00Z\n"
    )
    entries = parse_authorized_file(path)
    assert entries[0].stage == "Queue"
    assert entries[0].label is None and entries[0].evidence is None


def test_move_stage_refuses_without_a_matching_record(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    path = tmp_path / "delegation-authorized.toml"
    path.write_text(record_block(999, "move-stage", stage="Queue"))
    w = Workflow(
        api, project_id=3,
        delegation=DelegationPolicy(armed=True, authorized_file=path),
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "move-stage")
    assert "no [[authorized_move]] record matches" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_triage_to_queue_happy_path_keeps_card_unassigned(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("backlog card", "Backlog")
    w = armed_workflow_for(
        api, tmp_path, task["id"], "triage-to-queue", instruction="work on this one now"
    )
    result = w.delegated_move(task["id"], "triage-to-queue")
    assert result["moved_to"] == "Queue"
    assert api.task_bucket[task["id"]] == api.bucket_id("Queue")
    assert api.get_task(task["id"])["assignees"] == []
    comments = api.comments_text(task["id"])
    assert comments and comments[-1].startswith("[delegated-move]")
    assert "work on this one now" in comments[-1]


def test_triage_to_queue_outside_backlog_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("build card", "Build", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "triage-to-queue")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "triage-to-queue")
    assert "from Backlog to Queue only" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_triage_to_queue_refuses_a_done_card_without_firing(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done")
    w = armed_workflow_for(api, tmp_path, task["id"], "triage-to-queue")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "triage-to-queue")
    assert "stays closed" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_add_label_happy_path(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = armed_workflow_for(
        api, tmp_path, task["id"], "add-label", label="blocked", instruction="mark it blocked"
    )
    result = w.delegated_move(task["id"], "add-label", label="blocked")
    assert result["label"] == "blocked"
    titles = [lb["title"] for lb in api.get_task(task["id"])["labels"]]
    assert "blocked" in titles
    comments = api.comments_text(task["id"])
    assert comments[-1].startswith("[delegated-move]")


def test_clear_label_happy_path(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user, labels=("bug",))
    w = armed_workflow_for(
        api, tmp_path, task["id"], "clear-label", label="bug", instruction="it is not a bug"
    )
    result = w.delegated_move(task["id"], "clear-label", label="bug")
    assert result["label"] == "bug"
    titles = [lb["title"] for lb in api.get_task(task["id"])["labels"]]
    assert titles == []


def test_verdict_labels_refuse(tmp_path):
    for label in sorted(PROTECTED_LABELS):
        sub = tmp_path / label
        sub.mkdir()
        api = FakeAPI(buckets=STAGES)
        task = api.add_task("a card", "Build", assignee=api.me_user)
        w = armed_workflow_for(
            api, sub, task["id"], "add-label", label=label, instruction="user asked"
        )
        with pytest.raises(WorkflowError) as exc:
            w.delegated_move(task["id"], "add-label", label=label)
        assert "outside delegation" in str(exc.value)
        titles = [lb["title"] for lb in api.get_task(task["id"])["labels"]]
        assert label not in titles


def test_label_action_on_done_card_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done")
    w = armed_workflow_for(api, tmp_path, task["id"], "add-label", label="blocked")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "add-label", label="blocked")
    assert "stays closed" in str(exc.value)


def test_audit_after_failure_demands_a_manual_audit(tmp_path, monkeypatch):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "add-label", label="blocked")

    def failing_add_comment(task_id, text):
        raise VikunjaError(403, "no comments scope")

    monkeypatch.setattr(api, "add_comment", failing_add_comment)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "add-label", label="blocked")
    assert "MOVED but its audit comment failed" in str(exc.value)
    assert "comment(task_id" in str(exc.value)
    assert "blocked" in [lb["title"] for lb in api.get_task(task["id"])["labels"]]


# --- the record parser and the finder ---


def test_parse_refuses_unknown_keys(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = 'x'\nevidence = 'y'\nauthorized_at = 2026-09-16T00:00:00Z\n"
        "expires = 2026-09-17T00:00:00Z\ngranted_by = 'user'\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "granted_by" in str(exc.value)


def test_parse_refuses_label_missing_on_add_label(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'add-label'\n"
        "instruction = 'x'\nauthorized_at = 2026-09-16T00:00:00Z\n"
        "expires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "label" in str(exc.value)


def test_parse_refuses_blank_instruction(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = ''\nevidence = 'y'\nauthorized_at = 2026-09-16T00:00:00Z\n"
        "expires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "instruction" in str(exc.value)


def test_parse_refuses_mark_done_without_evidence(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = 'x'\nauthorized_at = 2026-09-16T00:00:00Z\n"
        "expires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "evidence" in str(exc.value)


def test_parse_refuses_naive_datetime(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = 'x'\nevidence = 'y'\n"
        "authorized_at = 2026-09-16T00:00:00\nexpires = 2026-09-17T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "timezone" in str(exc.value)


def test_parse_refuses_inverted_window(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = 'x'\nevidence = 'y'\n"
        "authorized_at = 2026-09-17T00:00:00Z\nexpires = 2026-09-16T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "not after authorized_at" in str(exc.value)


def test_parse_refuses_a_window_longer_than_the_record_ceiling(tmp_path):
    """MAX_RECORD_AGE is enforced: a block whose window stretches past the week's
    ceiling refuses at parse time, so a long-lived grant can never sit in the file
    delegating a card for years."""
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 1\naction = 'mark-done'\n"
        "instruction = 'x'\nevidence = 'y'\n"
        "authorized_at = 2026-09-01T00:00:00Z\nexpires = 2027-09-01T00:00:00Z\n"
    )
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "ceiling" in str(exc.value)
    assert "7 days" in str(exc.value)


def test_parse_refuses_empty_file(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text("")
    with pytest.raises(ValueError) as exc:
        parse_authorized_file(path)
    assert "no [[authorized_move]]" in str(exc.value)


def test_parse_accepts_a_well_formed_record(tmp_path):
    path = tmp_path / "r.toml"
    path.write_text(
        "[[authorized_move]]\ntask_id = 968\naction = 'mark-done'\n"
        "instruction = 'close 968'\nevidence = 'ran it'\n"
        "authorized_at = 2026-09-16T00:00:00Z\nexpires = 2026-09-17T00:00:00Z\n"
    )
    entries = parse_authorized_file(path)
    assert entries == [
        AuthorizedMove(
            task_id=968,
            action="mark-done",
            label=None,
            instruction="close 968",
            evidence="ran it",
            authorized_at=datetime(2026, 9, 16, tzinfo=timezone.utc),
            expires=datetime(2026, 9, 17, tzinfo=timezone.utc),
        )
    ]


def test_finder_matches_label_case_insensitively():
    record = AuthorizedMove(
        task_id=968,
        action="add-label",
        label="Blocked",
        instruction="x",
        evidence=None,
        authorized_at=NOW - timedelta(hours=1),
        expires=NOW + timedelta(days=1),
    )
    found, refusal = find_authorized([record], 968, "add-label", "blocked", NOW)
    assert found is record and refusal is None
    found, refusal = find_authorized([record], 968, "add-label", "unrelated", NOW)
    assert found is None and "no [[authorized_move]]" in refusal


def test_finder_refuses_duplicate_live_records():
    def rec(days):
        return AuthorizedMove(
            task_id=968,
            action="mark-done",
            label=None,
            instruction="x",
            evidence="y",
            authorized_at=NOW - timedelta(hours=1),
            expires=NOW + timedelta(days=days),
        )

    found, refusal = find_authorized([rec(1), rec(2)], 968, "mark-done", None, NOW)
    assert found is None and "remove the duplicates" in refusal


def test_audit_text_quotes_the_instruction_and_carries_the_record():
    record = AuthorizedMove(
        task_id=968,
        action="mark-done",
        label=None,
        instruction="close 968",
        evidence="ran it",
        authorized_at=NOW - timedelta(hours=1),
        expires=NOW + timedelta(days=1),
    )
    text = audit_text(record, "omp-delegated")
    assert text.startswith("[delegated-move] ")
    assert "> close 968" in text
    assert "evidence: ran it" in text
    assert record.expires.isoformat() in text


# --- config: the designated token swap ---


def test_armed_mode_reads_the_designated_token_file(tmp_path, monkeypatch):
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    delegated = tmp_path / "env-delegated"
    delegated.write_text("VIKUNJA_TOKEN=tk_delegated_narrow\n")
    monkeypatch.setattr(cfg_mod, "DELEGATED_ENV_FILE", delegated)
    monkeypatch.setattr(cfg_mod, "USER_ENV_FILE", tmp_path / "shared-nonexistent")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_DELEGATION": "1"})
    assert cfg.token == "tk_delegated_narrow"
    assert cfg.delegation is not None and cfg.delegation.armed is True
    assert cfg.delegation.authorized_file == cfg_mod.load_delegation({}).authorized_file
    assert cfg.delegation.agent_mark_done is False      # default: the guard holds


def test_armed_mode_carries_the_self_certification_opt_out(tmp_path, monkeypatch):
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    delegated = tmp_path / "env-delegated"
    delegated.write_text("VIKUNJA_TOKEN=tk_delegated_narrow\n")
    monkeypatch.setattr(cfg_mod, "DELEGATED_ENV_FILE", delegated)
    monkeypatch.setattr(cfg_mod, "USER_ENV_FILE", tmp_path / "shared-nonexistent")
    cfg = load_config(
        cwd=tmp_path,
        environ={"VIKUNJA_DELEGATION": "1", "VIKUNJA_DELEGATION_AGENT_MARK_DONE": "1"},
    )
    assert cfg.delegation is not None
    assert cfg.delegation.armed is True and cfg.delegation.agent_mark_done is True


def test_armed_mode_refuses_without_the_designated_file(tmp_path, monkeypatch):
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    monkeypatch.setattr(cfg_mod, "DELEGATED_ENV_FILE", tmp_path / "absent")
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_DELEGATION": "1"})
    # the shared token was available in the USER env file, and the refusal must STILL
    # happen — the ordinary chain is deliberately not consulted in armed mode
    assert "no token was found in" in str(exc.value)
    assert "absent" in str(exc.value)


def test_armed_mode_refuses_a_copied_shared_token(tmp_path, monkeypatch):
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    shared = tmp_path / "shared"
    shared.write_text("VIKUNJA_TOKEN=tk_shared_agent\n")
    delegated = tmp_path / "env-delegated"
    delegated.write_text("VIKUNJA_TOKEN=tk_shared_agent\n")
    monkeypatch.setattr(cfg_mod, "DELEGATED_ENV_FILE", delegated)
    monkeypatch.setattr(cfg_mod, "USER_ENV_FILE", shared)
    with pytest.raises(ConfigError) as exc:
        load_config(cwd=tmp_path, environ={"VIKUNJA_DELEGATION": "1"})
    assert "SAME as the shared agent token" in str(exc.value)


def test_armed_mode_refuses_a_shared_token_passed_via_env(tmp_path, monkeypatch):
    """The same-token refusal covers the ENV source too: the `vikunja-delegated`
    registration's env block carrying VIKUNJA_TOKEN is exactly where a copied shared
    agent token arrives, and it must refuse the same way the designated file does —
    not load the full identity and arm the delegated toolset on it."""
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    shared = tmp_path / "shared"
    shared.write_text("VIKUNJA_TOKEN=tk_shared_agent\n")
    monkeypatch.setattr(cfg_mod, "USER_ENV_FILE", shared)
    with pytest.raises(ConfigError) as exc:
        load_config(
            cwd=tmp_path,
            environ={"VIKUNJA_DELEGATION": "1", "VIKUNJA_TOKEN": "tk_shared_agent"},
        )
    assert "SAME as the shared agent token" in str(exc.value)


def test_unarmed_mode_keeps_the_ordinary_token_chain(tmp_path, monkeypatch):
    from vikunja_mcp import config as cfg_mod

    toml = tmp_path / ".vikunja-mcp.toml"
    toml.write_text('[tracker]\nurl = "https://t.example"\nproject_id = 3\n')
    shared = tmp_path / "shared"
    shared.write_text("VIKUNJA_TOKEN=tk_shared_agent\n")
    monkeypatch.setattr(cfg_mod, "USER_ENV_FILE", shared)
    cfg = load_config(cwd=tmp_path, environ={})
    assert cfg.token == "tk_shared_agent"
    assert cfg.delegation is None


def test_load_delegation_reads_the_arm_flag():
    assert load_delegation({"VIKUNJA_DELEGATION": "1"}).armed is True
    assert load_delegation({"VIKUNJA_DELEGATION": "true"}).armed is True
    assert load_delegation({}).armed is False
    assert load_delegation({"VIKUNJA_DELEGATION": "0"}).armed is False


def test_load_delegation_reads_the_self_certification_opt_out():
    from vikunja_mcp.delegation import ENV_AGENT_MARK_DONE

    assert load_delegation({}).agent_mark_done is False
    assert load_delegation({ENV_AGENT_MARK_DONE: "0"}).agent_mark_done is False
    assert load_delegation({ENV_AGENT_MARK_DONE: ""}).agent_mark_done is False
    # same closed truthy set as the arm switch: an unknown spelling reads OFF
    assert load_delegation({ENV_AGENT_MARK_DONE: "yes-please"}).agent_mark_done is False
    for truthy in ("1", "true", "yes", "on"):
        assert load_delegation({ENV_AGENT_MARK_DONE: truthy}).agent_mark_done is True
    # the opt-out does not arm anything by itself
    pol = load_delegation({ENV_AGENT_MARK_DONE: "1"})
    assert pol.armed is False
