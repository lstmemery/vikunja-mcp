import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.evidence import EvidenceBlockError, has_valid_evidence_block, set_evidence_block
from vikunja_mcp.formatting import html_to_text
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError

EVIDENCE_BLOCK = """## Evidence
### What changed
Added a standard block to the task description.
### Verification
Command: pytest tests/unit/test_evidence_block.py -q
Key output: 1 passed
### Before / after
Before: advance moved tasks without a description Evidence block.
After: advance stores the complete block before moving to Review.
### Artifacts
src/vikunja_mcp/evidence.py; tests/unit/test_evidence_block.py
### Residual risks
Live tracker readback remains pending a user action.
### Approve if
Missing blocks are refused and reproduced evidence is required to approve.
"""


def _build_task():
    api = FakeAPI(buckets=STAGES)
    workflow = Workflow(api, project_id=3)
    task = api.add_task("evidence block", "Queue")
    workflow.claim(task["id"])
    workflow.advance(task["id"], to="build", spec="Implement the change safely.")
    return api, workflow, task["id"]


def test_advance_refuses_review_without_a_description_evidence_block():
    api, workflow, task_id = _build_task()

    with pytest.raises(WorkflowError, match="Evidence block"):
        workflow.advance(
            task_id, to="review", worklog="Changed the behavior and ran the checks.",
            evidence="abc123",
        )

    assert api.stage_of(task_id) == "Build"
    assert api.get_task(task_id)["description"] == ""
    assert not any(
        comment["comment"].startswith("[worklog]")
        for comment in api.comments(task_id)
    )


def test_advance_puts_the_evidence_block_first_and_preserves_the_description():
    api, workflow, task_id = _build_task()
    api.update_task(task_id, description="<p>Original task context.</p>")

    workflow.advance(
        task_id, to="review", worklog="Changed and verified.", evidence="abc123",
        evidence_block=EVIDENCE_BLOCK,
    )

    description = api.get_task(task_id)["description"]
    assert description.startswith("<!-- vikunja-mcp:evidence:start -->\n<h2>Evidence</h2>")
    assert description.index("<h2>Evidence</h2>") < description.index("<hr>")
    assert description.endswith("<p>Original task context.</p>")
    assert "<h3>Verification</h3>" in description
    assert "<strong>Command:</strong> <code>pytest tests/unit/test_evidence_block.py -q</code>" \
        in description


def test_advance_replaces_its_previous_block_without_duplicating_or_losing_context():
    api, workflow, task_id = _build_task()
    api.update_task(task_id, description="<p>Original task context.</p>")
    workflow.advance(
        task_id, to="review", worklog="First pass.", evidence="abc123",
        evidence_block=EVIDENCE_BLOCK,
    )

    api.move_task(3, api.view["id"], api.bucket_id("Build"), task_id)
    updated_block = EVIDENCE_BLOCK.replace(
        "Added a standard block to the task description.",
        "Replaced the earlier Evidence block on resubmission.",
    )
    workflow.advance(
        task_id, to="review", worklog="Second pass.", evidence="def456",
        evidence_block=updated_block,
    )

    description = api.get_task(task_id)["description"]
    assert description.count("<!-- vikunja-mcp:evidence:start -->") == 1
    assert "Replaced the earlier Evidence block on resubmission." in description
    assert "Added a standard block to the task description." not in description
    assert description.endswith("<p>Original task context.</p>")


def test_replacement_moves_a_buried_evidence_block_to_the_top():
    original = set_evidence_block("", EVIDENCE_BLOCK)
    buried = "<p>Introductory text.</p>\n" + original + "\n<hr>\n<p>Card details.</p>"

    updated = set_evidence_block(
        buried,
        EVIDENCE_BLOCK.replace(
            "Added a standard block to the task description.",
            "Moved the replacement block back to the top.",
        ),
    )

    assert updated.startswith("<!-- vikunja-mcp:evidence:start -->")
    assert "Moved the replacement block back to the top." in updated
    assert "<p>Introductory text.</p>" in updated
    assert "<p>Card details.</p>" in updated


def test_advance_refuses_an_incomplete_block_before_writing_or_moving():
    api, workflow, task_id = _build_task()
    incomplete = EVIDENCE_BLOCK.replace("Key output: 1 passed\n", "")

    with pytest.raises(WorkflowError, match="Key output"):
        workflow.advance(
            task_id, to="review", worklog="Changed and verified.", evidence="abc123",
            evidence_block=incomplete,
        )

    assert api.stage_of(task_id) == "Build"
    assert api.get_task(task_id)["description"] == ""
    assert not any(
        html_to_text(comment["comment"]).startswith("[worklog]")
        for comment in api.comments(task_id)
    )


def test_review_approval_refuses_a_missing_description_evidence_block():
    api = FakeAPI(buckets=STAGES)
    workflow = Workflow(api, project_id=3)
    task = api.add_task("missing evidence", "Review", assignee=api.me_user)

    with pytest.raises(WorkflowError, match="Evidence block"):
        workflow.review_task(
            task["id"], verdict="approve",
            report="Re-ran pytest tests/unit: 12 passed.",
        )

    assert not any("[review]" in comment["comment"] for comment in api.comments(task["id"]))


def test_review_approval_refuses_an_evidence_block_below_other_description_text():
    api = FakeAPI(buckets=STAGES)
    workflow = Workflow(api, project_id=3)
    task = api.add_task("buried evidence", "Review", assignee=api.me_user)
    block = set_evidence_block("", EVIDENCE_BLOCK)
    api.update_task(task["id"], description="<p>Unrelated text comes first.</p>\n" + block)

    assert not has_valid_evidence_block(api.get_task(task["id"])["description"])
    with pytest.raises(WorkflowError, match="no valid Evidence block"):
        workflow.review_task(
            task["id"], verdict="approve", report="Commands passed.",
            evidence_reproduced=True,
        )


def test_verification_requires_command_and_output_pairs_in_order():
    malformed = EVIDENCE_BLOCK.replace(
        "Command: pytest tests/unit/test_evidence_block.py -q\n"
        "Key output: 1 passed",
        "Command: pytest tests/unit/test_evidence_block.py -q\n"
        "Command: pytest tests/unit -q\n"
        "Key output: 1 passed\n"
        "Key output: all selected tests passed",
    )

    with pytest.raises(EvidenceBlockError, match="alternating"):
        set_evidence_block("", malformed)


def test_review_approval_refuses_an_unreproduced_block():
    api, workflow, task_id = _build_task()
    workflow.advance(
        task_id, to="review", worklog="Changed and verified.", evidence="abc123",
        evidence_block=EVIDENCE_BLOCK,
    )

    with pytest.raises(WorkflowError, match="was not reproduced"):
        workflow.review_task(
            task_id, verdict="approve", report="The command was not run.",
            evidence_reproduced=False,
        )

    assert api.stage_of(task_id) == "Review"
    assert not any("[review]" in comment["comment"] for comment in api.comments(task_id))


def test_review_approval_records_that_the_evidence_block_was_reproduced():
    api, workflow, task_id = _build_task()
    workflow.advance(
        task_id, to="review", worklog="Changed and verified.", evidence="abc123",
        evidence_block=EVIDENCE_BLOCK,
    )

    result = workflow.review_task(
        task_id, verdict="approve",
        report="Command: pytest tests/unit; key output: 6 passed.",
        evidence_reproduced=True,
    )

    assert result["verdict"] == "approve"
    review = html_to_text(api.comments(task_id)[-1]["comment"])
    assert review.startswith("[review] APPROVE\nEvidence block reproduced: yes\n")


def test_needs_work_can_reject_missing_or_unreproduced_evidence():
    api = FakeAPI(buckets=STAGES)
    workflow = Workflow(api, project_id=3)
    task = api.add_task("missing evidence", "Review", assignee=api.me_user)

    result = workflow.review_task(
        task["id"], verdict="needs_work", report="Description Evidence block is missing.",
        evidence_reproduced=False,
    )

    assert result["verdict"] == "needs_work"
    assert api.stage_of(task["id"]) == "Build"
    assert "Evidence block reproduced: no" in api.comments(task["id"])[-1]["comment"]
