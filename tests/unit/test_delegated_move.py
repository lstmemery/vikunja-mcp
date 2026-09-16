"""delegated_move — the scoped, per-card, user-instruction-gated delegation (delegation.py).

The gates, each driven on the real Workflow over FakeAPI:
  * not armed -> refuse (policy None, or armed=False)
  * action outside the closed allowlist -> refuse by name
  * record file missing / unparsable -> refuse, naming the path
  * no matching record / expired record -> refuse
  * mark-done: audit comment posted BEFORE the move; a failed audit write refuses and
    the card is NOT moved
  * mark-done self-certification guard: a card created by the agent's own account
    refuses unless the independent `reviewed` label is present
  * triage-to-queue: Backlog -> Queue only, card stays UNASSIGNED, audit posted after
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


def record_block(
    task_id,
    action,
    label=None,
    instruction="close card 968 after review",
    evidence=None,
    authorized_at="2026-09-16T08:00:00Z",
    expires="2026-09-17T00:00:00Z",
):
    parts = [
        "[[authorized_move]]",
        f"task_id = {task_id}",
        f'action = "{action}"',
    ]
    if label is not None:
        parts.append(f'label = "{label}"')
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
        w.delegated_move(task["id"], "mark-done", "user said so", evidence="ran it")
    assert "NOT armed" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")
    assert api.comments_text(task["id"]) == []


def test_delegation_policy_none_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    w = Workflow(api, project_id=3)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "user said so", evidence="ran it")
    assert "NOT armed" in str(exc.value)


def test_unknown_action_refuses_naming_the_allowlist(tmp_path):
    api, w = rig(tmp_path, record=(0, ""))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "delete", "user said so")
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
        w.delegated_move(task["id"], "triage-to-queue", "user said so")
    assert "absent.toml" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Backlog")


def test_unparsable_record_refuses_rather_than_reading_as_empty(tmp_path):
    api, w = rig(tmp_path, record=(0, "[authorized_move]\nnot valid toml =\n"))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "user said so", evidence="ran it")
    assert "does not parse" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_no_matching_record_refuses(tmp_path):
    api, w = rig(tmp_path, record=(0, record_block(999, "mark-done", evidence="e")))
    task = api.add_task("a card", "Build", assignee=api.me_user)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "user said so", evidence="ran it")
    assert "no [[authorized_move]] record matches" in str(exc.value)


def test_expired_record_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = armed_workflow_for(
        api,
        tmp_path,
        task["id"],
        "mark-done",
        evidence="ran it",
        authorized_at="2026-09-01T00:00:00Z",
        expires="2026-09-02T00:00:00Z",
    )
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "user said so", evidence="ran it")
    assert "expired at" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_mark_done_human_authored_happy_path_audits_before_the_move(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Review", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    result = w.delegated_move(
        task["id"], "mark-done", "close it — I checked the review myself", evidence="ran it"
    )
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
        w.delegated_move(task["id"], "mark-done", "close it", evidence="ran it")
    assert "certifying its own work" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")


def test_mark_done_self_authored_card_passes_once_reviewed_landed(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task(
        "agent's own card", "Review", assignee=api.me_user, created_by="me", labels=("reviewed",)
    )
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    result = w.delegated_move(task["id"], "mark-done", "close it", evidence="ran it")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_mark_done_card_already_in_done_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "close it", evidence="ran it")
    assert "already in Done" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_mark_done_icebox_card_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("frozen", "Icebox")
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="ran it")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "mark-done", "close it", evidence="ran it")
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
        w.delegated_move(task["id"], "mark-done", "close it", evidence="ran it")
    assert "card was NOT moved" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Review")
    assert calls == [task["id"]]


def test_mark_done_from_build_also_reaches_done_on_instruction(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("stuck build card", "Build", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "mark-done", evidence="sha abc1234")
    result = w.delegated_move(task["id"], "mark-done", "close it directly", evidence="sha abc1234")
    assert result["moved_to"] == "Done"
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_triage_to_queue_happy_path_keeps_card_unassigned(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("backlog card", "Backlog")
    w = armed_workflow_for(
        api, tmp_path, task["id"], "triage-to-queue", instruction="work on this one now"
    )
    result = w.delegated_move(task["id"], "triage-to-queue", "work on this one now")
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
        w.delegated_move(task["id"], "triage-to-queue", "work on this")
    assert "from Backlog to Queue only" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Build")


def test_triage_to_queue_refuses_a_done_card_without_firing(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done")
    w = armed_workflow_for(api, tmp_path, task["id"], "triage-to-queue")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "triage-to-queue", "work on this")
    assert "stays closed" in str(exc.value)
    assert api.task_bucket[task["id"]] == api.bucket_id("Done")


def test_add_label_happy_path(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = armed_workflow_for(
        api, tmp_path, task["id"], "add-label", label="blocked", instruction="mark it blocked"
    )
    result = w.delegated_move(task["id"], "add-label", "mark it blocked", label="blocked")
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
    result = w.delegated_move(task["id"], "clear-label", "it is not a bug", label="bug")
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
            w.delegated_move(task["id"], "add-label", "user asked", label=label)
        assert "outside delegation" in str(exc.value)
        titles = [lb["title"] for lb in api.get_task(task["id"])["labels"]]
        assert label not in titles


def test_label_action_on_done_card_refuses(tmp_path):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("closed", "Done")
    w = armed_workflow_for(api, tmp_path, task["id"], "add-label", label="blocked")
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "add-label", "tag it", label="blocked")
    assert "stays closed" in str(exc.value)


def test_audit_after_failure_demands_a_manual_audit(tmp_path, monkeypatch):
    api = FakeAPI(buckets=STAGES)
    task = api.add_task("a card", "Build", assignee=api.me_user)
    w = armed_workflow_for(api, tmp_path, task["id"], "add-label", label="blocked")

    def failing_add_comment(task_id, text):
        raise VikunjaError(403, "no comments scope")

    monkeypatch.setattr(api, "add_comment", failing_add_comment)
    with pytest.raises(WorkflowError) as exc:
        w.delegated_move(task["id"], "add-label", "mark it blocked", label="blocked")
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
    text = audit_text("mark-done", 968, "close 968", record, "omp-delegated", evidence="ran it")
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
