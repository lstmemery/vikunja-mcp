"""`review_task_ids` against a REAL Vikunja board — the one part of --gc the fake cannot
prove, because it depends on the live view_tasks/bucket shape rather than on our mirror of it.
"""
import uuid

import pytest

from tests.integration.conftest import BASE, mint_scoped_token
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.setup_cmd import reconcile
from vikunja_mcp.workflow import Workflow

pytestmark = pytest.mark.skipif(not BASE, reason="VIKUNJA_TEST_URL not set")


@pytest.fixture(scope="module")
def gcproj(boss_jwt, agent_jwts):
    """Isolated project + canonical board + one scoped-token agent Workflow — mirrors
    test_sequence_gate.py's seqproj. The agent's Workflow is the subject under test: its
    scoped-token view_tasks is the exact read `gc_workspaces` performs via review_task_ids."""
    boss = VikunjaAPI(BASE, boss_jwt)
    pid = reconcile(boss, f"gc-{uuid.uuid4().hex[:8]}", shares=[("agent1", 1)])
    view = boss.kanban_view(pid)
    buckets = {b["title"]: b["id"] for b in boss.buckets(pid, view["id"])}

    def enqueue(title, stage="Queue", priority=0):
        t = boss.create_task(pid, title, priority=priority)
        boss.move_task(pid, view["id"], buckets[stage], t["id"])
        return t

    jwt1, _ = agent_jwts
    wf1 = Workflow(VikunjaAPI(BASE, mint_scoped_token(jwt1)), pid)
    return boss, pid, view, buckets, enqueue, wf1


def test_review_task_ids_sees_a_real_card_in_review(gcproj):
    """A card the agent does not own (created and moved entirely by boss) still shows up —
    review_task_ids is deliberately NOT filtered by assignee (a reviewer works someone else's
    card), and this is the one place that boundary is proven against the real board shape
    rather than FakeAPI's mirror of it."""
    _boss, _pid, _view, _buckets, enqueue, wf1 = gcproj
    task = enqueue("needs review", stage="Review")
    assert task["id"] in wf1.review_task_ids()
