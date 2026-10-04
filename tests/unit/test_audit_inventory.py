import httpx
import inspect
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import server
from vikunja_mcp.api import VikunjaAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


def make_wf():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, language="en")


def test_project_tasks_reads_complete_project_collection_exhaustively():
    calls = []

    def handler(request):
        if request.url.path.endswith("/info"):
            return httpx.Response(200, json={"max_items_per_page": 2})
        calls.append(dict(request.url.params))
        assert request.url.path.endswith("/tasks")
        assert request.url.params["project_id"] == "3"
        page = int(request.url.params["page"])
        pages = {
            1: [{"id": 1}, {"id": 2}], 2: [], 3: [{"id": 3}], 4: [], 5: [],
        }
        return httpx.Response(200, json=pages[page])

    client = httpx.Client(
        base_url="https://t.example/api/v1", headers={"Authorization": "Bearer tk"},
        transport=httpx.MockTransport(handler),
    )
    api = VikunjaAPI("https://t.example", "tk", client=client)
    assert [task["id"] for task in api.project_tasks(3)] == [1, 2, 3]
    assert [int(params["page"]) for params in calls] == [1, 2, 3, 4, 5]


def test_list_tasks_exposes_timestamps_and_parent_done_state_for_audit():
    api, wf = make_wf()
    parent = api.add_task("Release epic", "Backlog", labels=["epic"])
    child = api.add_task("Release child", "Done")
    pending_child = api.add_task("Pending release child", "Build")
    complete_parent = api.add_task("Complete epic", "Backlog", labels=["epic"])
    complete_child = api.add_task("Complete child", "Done")
    api.add_relation(child["id"], parent["id"], "parenttask")
    api.add_relation(pending_child["id"], parent["id"], "parenttask")
    api.add_relation(complete_child["id"], complete_parent["id"], "parenttask")
    api.tasks[parent["id"]].update(
        created="2026-08-01T12:00:00Z", updated="2026-09-01T12:00:00Z",
        done=False, done_at="0001-01-01T00:00:00Z",
    )
    api.tasks[child["id"]].update(
        created="2026-08-02T12:00:00Z", updated="2026-09-02T12:00:00Z",
        done=True, done_at="2026-09-02T12:00:00Z",
    )

    result = wf.list_tasks()
    by_id = {task["id"]: task for task in result["results"]}
    assert result["count"] == 5
    assert by_id[parent["id"]]["created"] == "2026-08-01T12:00:00Z"
    assert by_id[parent["id"]]["updated"] == "2026-09-01T12:00:00Z"
    assert by_id[child["id"]]["done_at"] == "2026-09-02T12:00:00Z"
    assert by_id[child["id"]]["parent_task_id"] == parent["id"]
    assert set(by_id[parent["id"]]["child_task_ids"]) == {
        child["id"], pending_child["id"],
    }
    assert by_id[parent["id"]]["is_epic"] is True
    assert by_id[parent["id"]]["children_all_done"] is False
    assert by_id[complete_parent["id"]]["is_epic"] is True
    assert by_id[complete_parent["id"]]["child_task_ids"] == [complete_child["id"]]
    assert by_id[complete_parent["id"]]["children_all_done"] is True
    assert by_id[child["id"]]["done"] is True


def test_list_tasks_filters_by_stage_and_created_or_updated_cutoff():
    api, wf = make_wf()
    old = api.add_task("Old backlog", "Backlog")
    recent = api.add_task("Recent backlog", "Backlog")
    built = api.add_task("Old build", "Build")
    api.tasks[old["id"]].update(created="2026-01-01T00:00:00Z", updated="2026-08-01T00:00:00Z")
    api.tasks[recent["id"]].update(created="2026-08-01T00:00:00Z", updated="2026-09-01T00:00:00Z")
    api.tasks[built["id"]].update(created="2026-01-01T00:00:00Z", updated="2026-01-01T00:00:00Z")

    old_backlog = wf.list_tasks(stage="Backlog", created_before="2026-07-01T00:00:00Z")
    assert [t["id"] for t in old_backlog["results"]] == [old["id"]]
    recently_updated = wf.list_tasks(updated_before="2026-02-01T00:00:00Z")
    assert {t["id"] for t in recently_updated["results"]} == {built["id"]}


def test_list_tasks_includes_unbucketed_tasks_in_complete_project_inventory():
    api, wf = make_wf()
    orphan = api.add_task("Task without a kanban bucket", "Backlog")
    api.task_bucket.pop(orphan["id"])
    api.tasks[orphan["id"]].update(
        created="2026-01-01T00:00:00Z", updated="2026-01-02T00:00:00Z"
    )

    result = wf.list_tasks(created_before="2026-02-01T00:00:00Z")
    assert result["count"] == 1
    assert result["results"][0]["id"] == orphan["id"]
    assert result["results"][0]["stage"] is None


def test_list_tasks_rejects_invalid_cutoff():
    _, wf = make_wf()
    with pytest.raises(WorkflowError, match="ISO 8601"):
        wf.list_tasks(created_before="not-a-date")


def test_list_tasks_tool_exposes_the_read_only_inventory(monkeypatch):
    class StubWF:
        def list_tasks(self, **kwargs):
            assert kwargs == {
                "stage": "Backlog", "created_before": "2026-07-01T00:00:00Z",
                "updated_before": None,
            }
            return {"scope": "project 3", "count": 1, "results": [{"id": 7}]}

    fn = next(f for f in server._DEFERRED_TOOLS if f.__name__ == "list_tasks")
    assert list(inspect.signature(fn).parameters) == ["stage", "created_before", "updated_before"]
    monkeypatch.setattr(server, "_wf", lambda: StubWF())
    assert server.list_tasks(stage="Backlog", created_before="2026-07-01T00:00:00Z") == {
        "scope": "project 3", "count": 1, "results": [{"id": 7}],
    }


def test_get_task_includes_timestamps_and_parent_id():
    api, wf = make_wf()
    parent = api.add_task("Parent task", "Build")
    task = api.add_task("Dossier timestamps", "Build")
    api.tasks[task["id"]].update(
        created="2026-01-02T00:00:00Z", updated="2026-02-03T00:00:00Z",
        done_at="0001-01-01T00:00:00Z",
    )
    api.add_relation(task["id"], parent["id"], "parenttask")
    result = wf.get_task(task["id"])
    assert {key: result[key] for key in ("created", "updated", "done_at", "parent_task_id")} == {
        "created": "2026-01-02T00:00:00Z", "updated": "2026-02-03T00:00:00Z",
        "done_at": "0001-01-01T00:00:00Z", "parent_task_id": parent["id"],
    }
