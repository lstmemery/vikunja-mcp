"""#992: `Workflow(api, project_id)` refuses a non-int at the CALL SITE instead of dying two
layers down as a 404.

WHAT IT COST, measured on the live instance at `4bfc51a` while taking the board for the #965
self-review — a real script, not a constructed one:

    wf = Workflow(api, cfg)   # a Config where the project_id goes
    wf.project_id             # -> Config(url='https://…', token='tk_…', …)
    wf._board()               # -> VikunjaError: Vikunja API 404: {"message":"Not Found"}

The constructor took the object silently, so the failure surfaced in the HTTP layer and named a
project that does not exist. That points AWAY from the cause: "404 Not Found" reads as a wrong
id, a deleted project or a token without access — three things to check before the real one.

WHY THE GUARD CANNOT FIRE IN PRODUCTION, and why it is still worth having. All three sites that
build a Workflow (`server._workflow`, `claimable_cmd.run_claimable`, `workspace_cmd`) pass
`cfg.project_id`, and `load_config` puts it through `int(raw_pid)` and raises ConfigError
otherwise — so the stdio server can never reach this raise, and `_tool`'s "never crash the
server" rule is not weakened by it being a TypeError. What it protects is hand-written code:
scripts, probes, tests and future call sites, which is exactly where it already bit once.

WHY TypeError AND NOT WorkflowError. `_tool` converts WorkflowError into an `{"error": ...}`
tool result, which is right for a workflow REFUSAL and wrong here: a programmer's mistake would
reach the agent looking exactly like a gate saying no. This is the one decision in the card with
a defensible other side, so it is named rather than assumed.

MUTATION SWEEP, one selection throughout (this file + test_workflow_gates.py + test_config.py +
test_server.py), `__pycache__` cleared and PYTHONDONTWRITEBYTECODE=1 each round, `-q` dropped so
`collected` is printed, each round read by COUNTING lines beginning `FAILED ` and `ERROR `
separately rather than by the first `N failed` in stdout: control (opening) 0 failed, 0 errors,
collected 221; the whole guard deleted -> 7 failed, 0 errors, collected 221; the bool arm
dropped, leaving a bare `isinstance(project_id, int)` -> 1 failed, 0 errors, collected 221; the
message switched from the type to the VALUE (`{project_id!r}`) -> 2 failed, 0 errors, collected
221, and that pair is the token-leak pin plus the type-name assertion it also breaks; control
(closing, restored) 0 failed, 0 errors, collected 221, `vikunja_mcp.__file__` resolving inside
this checkout every round. Collected is equal in every round, so each number is a delta against
the control and not a different selection.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.config import Config
from vikunja_mcp.workflow import STAGES, Workflow

TOKEN = "tk_deadbeefdeadbeefdeadbeefdeadbeef"


def _config():
    return Config(url="https://tracker.example", token=TOKEN, project_id=10)


def test_a_config_where_the_project_id_goes_is_refused_at_the_call_site():
    """The measured mistake itself. The refusal has to arrive from the CONSTRUCTOR — anything
    later is the 404 this card exists to replace."""
    with pytest.raises(TypeError) as err:
        Workflow(FakeAPI(buckets=STAGES), _config())

    msg = str(err.value)
    assert "project_id" in msg, f"the refusal must name the parameter it is about: {msg}"
    assert "Config" in msg, f"the refusal must name the TYPE that arrived: {msg}"


def test_the_refusal_names_the_type_and_never_the_value():
    """A Config carries the API token — a secret of the same class as `.vikunja-mcp.env`, which
    this repo keeps out of the toml on purpose. An exception message goes to stderr, to logs and
    into agents' worklogs and tracker comments, so `f"got {project_id!r}"` — the obvious spelling
    — would publish the token to all three. Asserted as the ABSENCE of the substring rather than
    as "the message looks fine"."""
    with pytest.raises(TypeError) as err:
        Workflow(FakeAPI(buckets=STAGES), _config())

    msg = str(err.value)
    assert TOKEN not in msg, f"the refusal leaked the token: {msg}"
    assert "tk_" not in msg, f"the refusal leaked a token-shaped string: {msg}"


def test_a_bool_is_refused_even_though_it_is_an_int():
    """`isinstance(True, int)` is True in Python, so the obvious guard would pass a bool straight
    through to the URL and produce the very 404 this card removes. Separate arm, separate pin."""
    for value in (True, False):
        with pytest.raises(TypeError, match="bool"):
            Workflow(FakeAPI(buckets=STAGES), value)


@pytest.mark.parametrize("value", ["10", 10.0, None, [10]])
def test_other_non_ints_are_refused_too(value):
    """`"10"` is the interesting one: it WOULD have worked today, since the id reaches the URL by
    interpolation. Refusing it is a deliberate tightening, not a preserved behaviour — a value
    that silently works while being the wrong type is the class of drift this card is about."""
    with pytest.raises(TypeError):
        Workflow(FakeAPI(buckets=STAGES), value)


def test_an_int_project_id_still_builds_and_works():
    """The other half, without which the guard is indistinguishable from "Workflow is broken"."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, 3)
    assert wf.project_id == 3
    t = api.add_task("free work", "Queue")
    assert wf.next_task()["task"]["id"] == t["id"]


def test_the_production_call_sites_shape_still_builds():
    """Guards against the failure mode that would make this card a net loss: a guard that is red
    on its own callers. Built the way `server._workflow` builds it — every keyword it passes,
    with `cfg.project_id` as the positional — so a stricter check that broke that wiring fails
    HERE rather than at a consumer's session start."""
    cfg = _config()
    wf = Workflow(
        FakeAPI(buckets=STAGES), cfg.project_id,
        enforce_single_wip=False, wip_limit=3, notifier=None,
        require_review_independence=False,
    )
    assert wf.project_id == 10
