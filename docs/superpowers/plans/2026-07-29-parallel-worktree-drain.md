# Parallel Worktree Drain Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let one agent identity drain up to `wip_limit` tracker tasks concurrently, each per-task agent and each reviewer isolated in its own git worktree.

**Architecture:** Two independent layers. The **tracker layer** (`config.py`, `workflow.py`, `server.py`) only counts slots — a committed `wip_limit` number, a generalised gate in `claim()`, and a `next_task(exclude=…)` that reports `wip` and refuses to over-offer. The **workspace layer** is a new CLI subcommand (`workspace_cmd.py`, sibling of `claimable_cmd.py`) that owns git worktrees: create, release, and GC of trees orphaned by crashed agents. All git `subprocess` work lives in that one module; the MCP server never touches git. SKILL.md then teaches the orchestrator the parallel tick.

**Tech Stack:** Python 3.11+, uv, FastMCP, pytest, ruff. No new runtime dependencies — the workspace layer uses only stdlib (`subprocess`, `fcntl`, `argparse`, `json`, `re`, `pathlib`, `contextlib`).

**Spec:** `docs/superpowers/specs/2026-07-29-parallel-worktree-drain-design.md`

## Global Constraints

- Python 3.11+, managed with `uv`. **No new runtime dependencies** — stdlib only.
- `uv run ruff check .` must pass; line-length is 100.
- `uv run pytest tests/unit -q` must be green at the end of every task.
- The `vikunja-mcp claimable` cross-repo contract is **frozen**: keys `claimable`/`kind`/`task_id`, exit 0 = ran / 1 = failed, and a closed 7-value `kind` enum (`queue|resume|stuck_claim|review|empty|starving|cycle`). Adding a `kind` breaks hgdev-acp's hub **and has an inverted rollout order** — do not add one in this plan.
- `wip_limit` is committed team policy: read **only** from the repo `.vikunja-mcp.toml`, never from env. `worktree_root` is a machine-local path and therefore **does** take an env override (`VIKUNJA_WORKTREE_ROOT`).
- Ships inert: with no `wip_limit` set, every behaviour must be byte-for-byte what it is today. Existing tests must not need edits (except where a task says so explicitly).
- `skills/tracker/SKILL.md` is a **rulebook that ships in the wheel** and self-heals onto every consumer on MCP server start. It must never name repo-specific commands (no `uv run pytest`) — only concepts.
- Comment the **why** of each gate in the surrounding file's style (this codebase comments densely, mixing Russian and English; match the file you are editing).
- One task = one commit on `main`, `type(scope): summary`, with the `Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>` trailer.

## File Structure

| File | Responsibility | Task |
|---|---|---|
| `src/vikunja_mcp/config.py` | + `wip_limit: int | None`, + `worktree_root: str | None` | 1, 3 |
| `src/vikunja_mcp/workflow.py` | + `_effective_wip_limit()`, generalised `claim` gate, `next_task(exclude)` + `wip` payload, + `active_task_ids()` / `review_task_ids()` | 1, 2, 4 |
| `src/vikunja_mcp/server.py` | pass `wip_limit` into `Workflow`; `next_task` tool gains `exclude`; dispatch `workspace` in `main()` | 1, 2, 3 |
| `src/vikunja_mcp/claimable_cmd.py` | pass `wip_limit` through — verdict shape unchanged | 1 |
| `src/vikunja_mcp/workspace_cmd.py` | **new** — the only module in the package allowed to run git | 3, 4 |
| `src/vikunja_mcp/skills/tracker/SKILL.md` | parallel tick, integration recipe, `ordered` rule | 5 |
| `CLAUDE.md` | records the git-surface-isolation rule and the new config keys | 5 |
| `.vikunja-mcp.toml` | turns the feature on for this repo's own dogfood | 6 |
| `tests/unit/test_workflow_wip.py` | **new** — slot gate + `next_task` accounting | 1, 2 |
| `tests/unit/test_workspace_cmd.py` | **new** — real git in `tmp_path` | 3, 4 |
| `tests/unit/test_config.py` | + `wip_limit` / `worktree_root` resolution | 1, 3 |
| `tests/unit/test_claimable_cmd.py` | + pin that the verdict is unchanged under a limit | 2 |
| `tests/unit/test_skill_contract.py` | + pins for the new rulebook tokens | 5 |
| `tests/integration/test_workspace_gc.py` | **new** — `--gc` against a real board | 4 |

---

### Task 1: `wip_limit` config and the generalised claim gate

Ships completely inert: with no `wip_limit` in the toml, `claim` behaves exactly as today.

**Files:**
- Modify: `src/vikunja_mcp/config.py:30` (Config field), `:112-117` (return)
- Modify: `src/vikunja_mcp/workflow.py:154-162` (`__init__`), `:739-750` (the gate)
- Modify: `src/vikunja_mcp/server.py:80-85` (`_build_workflow`), `src/vikunja_mcp/claimable_cmd.py:83-86`
- Test: `tests/unit/test_config.py`, `tests/unit/test_workflow_wip.py` (new)

**Interfaces:**
- Consumes: nothing.
- Produces: `Config.wip_limit: int | None`; `Workflow(api, project_id, enforce_single_wip=False, wip_limit=None, notifier=None)`; `Workflow._effective_wip_limit() -> int | None` (`None` means no limit).

- [ ] **Step 1: Write the failing config tests**

Append to `tests/unit/test_config.py`:

```python
# --- wip_limit: the parallel-drain slot count (committed in the toml, generalises #38) ---

def test_wip_limit_defaults_to_none(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).wip_limit is None


def test_wip_limit_reads_from_toml(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = 3\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).wip_limit == 3


def test_wip_limit_is_never_read_from_env(tmp_path):
    """Committed TEAM POLICY, like enforce_single_wip: a machine-local env var must not
    quietly widen another repo's slot count."""
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_WIP_LIMIT": "9"})
    assert cfg.wip_limit is None


def test_wip_limit_below_one_is_a_config_error(tmp_path):
    """0 slots would silently wedge every claim — fail loudly at load instead."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = 0\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    with pytest.raises(ConfigError, match="wip_limit"):
        load_config(cwd=tmp_path, environ={})


def test_wip_limit_non_numeric_is_a_config_error(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nwip_limit = "many"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    with pytest.raises(ConfigError, match="wip_limit"):
        load_config(cwd=tmp_path, environ={})
```

Check the top of `tests/unit/test_config.py` — if `ConfigError` or `pytest` is not imported there, add `import pytest` and `from vikunja_mcp.config import ConfigError, load_config` to the existing import block rather than duplicating it.

- [ ] **Step 2: Run the config tests to verify they fail**

Run: `uv run pytest tests/unit/test_config.py -k wip_limit -v`
Expected: FAIL — `AttributeError: 'Config' object has no attribute 'wip_limit'`.

- [ ] **Step 3: Implement the config field**

In `src/vikunja_mcp/config.py`, add to the `Config` dataclass right after `enforce_single_wip`:

```python
    # how many tasks this token may hold in Design/Build AT ONCE — the parallel-drain slot
    # count, and the generalisation of enforce_single_wip (which is exactly wip_limit=1).
    # Committed TEAM POLICY of the same class: repo toml ONLY, never env, never a secret.
    # None (default) -> fall back to enforce_single_wip, i.e. today's behavior byte-for-byte.
    wip_limit: int | None = None
```

In `load_config`, after the `project_id` parse and before the `return Config(...)`:

```python
    raw_limit = repo.get("wip_limit")
    wip_limit: int | None = None
    if raw_limit is not None:
        try:
            wip_limit = int(raw_limit)
        except (TypeError, ValueError):
            raise ConfigError(f"wip_limit must be a number, got {raw_limit!r}")
        if wip_limit < 1:
            raise ConfigError(
                f"wip_limit must be >= 1 (got {wip_limit}) — omit the key entirely for no limit"
            )
```

and pass `wip_limit=wip_limit,` in the `Config(...)` call.

- [ ] **Step 4: Run the config tests to verify they pass**

Run: `uv run pytest tests/unit/test_config.py -k wip_limit -v`
Expected: PASS (5 tests).

- [ ] **Step 5: Write the failing gate tests**

Create `tests/unit/test_workflow_wip.py`:

```python
"""The WIP slot gate — how many tasks one token may hold in Design/Build at once.

wip_limit generalises the #38 single-WIP flag (enforce_single_wip == wip_limit 1) and is what
makes the parallel drain bounded: without it a pump could claim the whole Queue in one tick.
"""
import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow, WorkflowError


def _env(**kwargs):
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3, **kwargs)


def _hold(api, wf, title):
    """Claim a fresh Queue task so it lands in Design and counts against the limit."""
    task = api.add_task(title, "Queue")
    wf.claim(task["id"])
    return task


def test_no_limit_by_default_lets_a_second_claim_through():
    """Ships inert: an unconfigured consumer keeps today's unbounded behavior."""
    api, wf = _env()
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


def test_limit_two_allows_two_and_refuses_the_third():
    api, wf = _env(wip_limit=2)
    _hold(api, wf, "first")
    _hold(api, wf, "second")
    third = api.add_task("third", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(third["id"])


def test_limit_one_is_the_legacy_single_wip_behaviour():
    api, wf = _env(wip_limit=1)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_wip_limit_wins_over_enforce_single_wip():
    """Both set -> the number is the truth; the legacy flag must not clamp it back to 1."""
    api, wf = _env(enforce_single_wip=True, wip_limit=2)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True


def test_legacy_flag_alone_still_means_one():
    api, wf = _env(enforce_single_wip=True)
    _hold(api, wf, "first")
    second = api.add_task("second", "Queue")
    with pytest.raises(WorkflowError, match="WIP limit"):
        wf.claim(second["id"])


def test_a_freed_slot_is_reusable():
    """advance to Review takes the task out of Design/Build, so the slot comes back."""
    api, wf = _env(wip_limit=1)
    first = _hold(api, wf, "first")
    wf.advance(first["id"], to="build", spec="do the thing")
    wf.advance(first["id"], to="review", worklog="did the thing", evidence="abc1234")
    second = api.add_task("second", "Queue")
    assert wf.claim(second["id"])["claimed"] is True
```

- [ ] **Step 6: Run the gate tests to verify they fail**

Run: `uv run pytest tests/unit/test_workflow_wip.py -v`
Expected: FAIL — `TypeError: __init__() got an unexpected keyword argument 'wip_limit'`.

- [ ] **Step 7: Implement the gate**

In `src/vikunja_mcp/workflow.py`, extend `__init__` (keep `enforce_single_wip` in place — existing callers and tests pass it positionally-by-name):

```python
    def __init__(
        self, api: Any, project_id: int, enforce_single_wip: bool = False,
        notifier: WebhookNotifier | None = None, wip_limit: int | None = None,
    ):
```

and after the `enforce_single_wip` assignment:

```python
        # parallel drain: how many tasks may be active (Design/Build) at once. None -> fall
        # back to the legacy flag, so an unconfigured consumer is unchanged. See
        # _effective_wip_limit for the precedence.
        self.wip_limit = wip_limit
```

Add the resolver next to `_my_active_tasks`:

```python
    def _effective_wip_limit(self) -> int | None:
        """How many active tasks this token may hold. None = no limit.

        Precedence: an explicit wip_limit is the truth; otherwise the legacy #38 flag means
        exactly 1; otherwise unlimited (today's default). Keeping both keys alive means an
        existing consumer that committed enforce_single_wip = true needs no edit."""
        if self.wip_limit is not None:
            return self.wip_limit
        return 1 if self.enforce_single_wip else None
```

Replace the `if self.enforce_single_wip:` block in `claim()` (currently `workflow.py:739-750`) with:

```python
        # WIP slot gate (generalises the #38 single-WIP flag): refuse a claim that would put
        # this token over its configured number of simultaneously active tasks. Reuse the board
        # snapshot claim already fetched — the old code called _my_active_tasks() with no board
        # and paid for a SECOND full board fetch on every gated claim.
        limit = self._effective_wip_limit()
        if limit is not None:
            active = self._my_active_tasks(board=board)
            if len(active) >= limit:
                names = ", ".join(f"#{t['id']}" for _stage, t in active)
                raise WorkflowError(
                    f"WIP limit reached ({len(active)}/{limit}) — you already hold {names}. "
                    f"Finish one (advance to Review) or return_task it before claiming another"
                )
```

- [ ] **Step 8: Run the gate tests to verify they pass**

Run: `uv run pytest tests/unit/test_workflow_wip.py -v`
Expected: PASS (6 tests).

- [ ] **Step 9: Update the two existing single-WIP tests that assert the old message**

`tests/unit/test_workflow_claim.py:243-285` matches on the old wording. Run them:

Run: `uv run pytest tests/unit/test_workflow_claim.py -v`
Expected: the `enforce_single_wip` tests FAIL on the message match.

Change only the assertion strings there from the old `"already have an active task"` / `"single-WIP limit"` wording to `"WIP limit"`. Do **not** change what they exercise — `enforce_single_wip=True` must keep meaning one slot.

- [ ] **Step 10: Wire the config through both entry points**

`src/vikunja_mcp/server.py`, in `_build_workflow`:

```python
    return Workflow(
        VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
        enforce_single_wip=cfg.enforce_single_wip,
        wip_limit=cfg.wip_limit,
        notifier=notifier,
    )
```

`src/vikunja_mcp/claimable_cmd.py`, in `run_claimable`:

```python
        wf = Workflow(
            VikunjaAPI(cfg.url, cfg.token), cfg.project_id,
            enforce_single_wip=cfg.enforce_single_wip,
            wip_limit=cfg.wip_limit,
        )
```

- [ ] **Step 11: Run the full unit suite and lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 12: Commit**

```bash
git add src/vikunja_mcp/config.py src/vikunja_mcp/workflow.py src/vikunja_mcp/server.py \
        src/vikunja_mcp/claimable_cmd.py tests/unit/test_config.py \
        tests/unit/test_workflow_wip.py tests/unit/test_workflow_claim.py
git commit -m "feat(workflow): wip_limit — a configurable slot gate generalising single-WIP"
```

---

### Task 2: `next_task(exclude=…)` and the `wip` payload

Teaches `next_task` to serve a pump that has several agents in flight, without letting it re-offer a task an agent is already on.

**Files:**
- Modify: `src/vikunja_mcp/workflow.py:343-530` (`next_task`)
- Modify: `src/vikunja_mcp/server.py` (the `next_task` tool)
- Test: `tests/unit/test_workflow_wip.py`, `tests/unit/test_claimable_cmd.py`

**Interfaces:**
- Consumes: `Workflow._effective_wip_limit()` from Task 1.
- Produces: `Workflow.next_task(exclude: list[int] | None = None) -> dict`; every result carries `wip: {"active": int, "limit": int | None, "free": int | None}`; a new saturated result `{"task": None, "wip_saturated": True, "wip": {...}, "message": str, "note": str}`.

- [ ] **Step 1: Write the failing tests**

Append to `tests/unit/test_workflow_wip.py`:

```python
# --- next_task in parallel mode: exclude + slot accounting ---

def test_next_task_reports_wip_on_every_result():
    api, wf = _env(wip_limit=2)
    api.add_task("free", "Queue")
    res = wf.next_task()
    assert res["wip"] == {"active": 0, "limit": 2, "free": 2}


def test_wip_free_is_none_when_unlimited():
    api, wf = _env()
    assert wf.next_task()["wip"] == {"active": 0, "limit": None, "free": None}


def test_excluded_active_task_is_not_offered_again():
    """The orchestrator already has a live agent on it; re-offering would dispatch a second
    agent onto the same task. Liveness is a fact of the harness, so the CALLER states it."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "in flight")
    free = api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"]["id"] == free["id"]
    assert res["resume"] is False


def test_excluded_task_still_occupies_its_slot():
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res["task"] is None
    assert res["wip_saturated"] is True
    assert res["wip"] == {"active": 1, "limit": 1, "free": 0}


def test_empty_exclude_still_hands_back_the_active_task():
    """A killed turn loses the in-flight set. The next tick passes nothing, and abandoned
    work must surface as resume — this is the crash-recovery path, not a regression."""
    api, wf = _env(wip_limit=2)
    held = _hold(api, wf, "abandoned")
    res = wf.next_task()
    assert res["resume"] is True and res["task"]["id"] == held["id"]


def test_saturation_does_not_suppress_a_review_offer():
    """Background review is not 'your active task' and consumes no slot (SKILL.md rule)."""
    api, wf = _env(wip_limit=1)
    _hold(api, wf, "in flight")
    other = api.add_task("someone else's work", "Review")
    api.add_comment(other["id"], "[worklog] done")
    res = wf.next_task(exclude=[held["id"]])
    assert res["review"] is True and res["task"]["id"] == other["id"]


def test_saturated_result_is_not_the_empty_queue():
    """The pump idles on an empty queue; it must WAIT (not sleep) when merely saturated."""
    api, wf = _env(wip_limit=1)
    held = _hold(api, wf, "in flight")
    api.add_task("free", "Queue")
    res = wf.next_task(exclude=[held["id"]])
    assert res.get("wip_saturated") is True
    assert "empty" not in res["message"]
```

`test_saturation_does_not_suppress_a_review_offer` is the branch-order pin: with the slot guard placed **after** the review loop, a saturated pump still gets handed a card to review. If you place the guard earlier, this test fails — that is the point.

- [ ] **Step 2: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_workflow_wip.py -k "wip or exclude or saturat" -v`
Expected: FAIL — `TypeError: next_task() got an unexpected keyword argument 'exclude'`, and `KeyError: 'wip'`.

- [ ] **Step 3: Implement `exclude` and the `wip` payload**

In `src/vikunja_mcp/workflow.py`, change the signature and insert the accounting immediately after `mine = self._my_active_tasks(raw)`:

```python
    def next_task(self, exclude: list[int] | None = None) -> dict:
```

```python
        mine = self._my_active_tasks(raw)
        # parallel drain: `exclude` names the tasks the CALLER already has a live
        # agent on. The tracker cannot know sub-agent liveness — that is a fact of the harness,
        # not of the board — so the pump states it. An excluded id is never OFFERED by any
        # branch, but it still OCCUPIES its slot: it is real work in progress. On a fresh tick
        # after a killed turn the set is empty and the abandoned task correctly resurfaces as
        # resume (the crash-recovery path).
        excluded = set(exclude or [])
        limit = self._effective_wip_limit()
        wip = {
            "active": len(mine),
            "limit": limit,
            "free": None if limit is None else max(0, limit - len(mine)),
        }

        def with_wip(result: dict) -> dict:
            result["wip"] = wip
            return result
```

Then:

1. Filter the resume branch — replace `mine.sort(...)`'s input by filtering first:

```python
        offerable = [st for st in mine if st[1]["id"] not in excluded]
        if offerable:
```
   and use `offerable` in place of `mine` for the rework-first computation and the sort (the `active_ids` set for rework-first should stay built from **all** of `mine`, so a chain predecessor held by another agent still ranks its successor correctly).

2. Wrap that branch's `return {...}` in `with_wip(...)`.

3. In the stuck-Queue branch, add `and t["id"] not in excluded` to the list comprehension, and wrap its return in `with_wip(...)`.

4. In the review loop, add right after the `for t in sorted(...)` line:

```python
            if t["id"] in excluded:
                continue
```
   and wrap that branch's return in `with_wip(...)`.

5. Immediately **after** the review loop and **before** the `full_board` / `resolve_full` block, insert the saturation guard — placed here deliberately, so a saturated pump never pays for the predecessor-gate scan:

```python
        # no free slot -> do not even look at the free queue. This is NOT an empty queue: the
        # pump must WAIT for a dispatched agent to return, not idle the tick. Reported alone —
        # `starving` describes a chain that cannot start, which is not the actionable fact when
        # there is nowhere to put a task anyway (and computing it can cost a board escalation).
        if wip["free"] == 0:
            return with_wip({
                "task": None,
                "wip_saturated": True,
                "message": (
                    f"all {limit} WIP slot(s) are busy ({wip['active']} active) — "
                    f"nothing can be claimed until one finishes"
                ),
                "note": (
                    "NOT an empty queue: wait for a dispatched agent to return, then call "
                    "next_task again. Do NOT claim, and do NOT end the tick / ScheduleWakeup "
                    "as if there were no work"
                ),
            })
```

6. Wrap the remaining three returns in `with_wip(...)`: the free-queue offer, the starving/cycle returns (`return with_wip(self._cycle_signal(...))` and `return with_wip(self._starving_tail(gated))`), and the empty-queue return.

- [ ] **Step 4: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_workflow_wip.py -v`
Expected: PASS.

- [ ] **Step 5: Add the cross-repo contract pin**

Append to `tests/unit/test_claimable_cmd.py`:

```python
def test_wip_saturation_is_unreachable_for_the_standalone_check():
    """The hub's `kind` enum is CLOSED and it fail-closes on an unknown value, so
    wip_saturated must never reach classify_next. It cannot: the CLI passes no `exclude`,
    so a non-empty active set always returns via the resume branch BEFORE the slot guard.
    This pins that reasoning — if a future edit lets saturation through, the verdict would
    silently degrade to 'empty' and every hub loop would idle on a board that has work."""
    api = FakeAPI(buckets=STAGES)
    wf = Workflow(api, project_id=3, wip_limit=1)
    task = api.add_task("held", "Queue")
    wf.claim(task["id"])
    api.add_task("free", "Queue")

    result = wf.next_task()
    assert "wip_saturated" not in result
    assert classify_next(result) == {"claimable": True, "kind": "resume", "task_id": task["id"]}
```

Match the module's existing imports (`FakeAPI`, `STAGES`, `Workflow`, `classify_next`); add whichever are missing to the existing import block.

- [ ] **Step 6: Expose `exclude` on the MCP tool**

In `src/vikunja_mcp/server.py`, change the `next_task` tool:

```python
@mcp.tool()
@_tool
def next_task(exclude: list[int] | None = None) -> dict:
```

and pass it through: `return _wf().next_task(exclude=exclude)`.

Add to the docstring (it is agent-facing UX copy — keep it prescriptive):

```
    PARALLEL DRAIN: pass `exclude` = the ids of tasks you ALREADY have a live agent on, so
    they are not handed back and dispatched twice. They still occupy their WIP slot. Every
    result carries wip: {active, limit, free}. free == 0 comes back as task:null PLUS
    wip_saturated:true — that means WAIT for an agent to return, NOT that the queue is empty.
```

- [ ] **Step 7: Run the full unit suite and lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 8: Commit**

```bash
git add src/vikunja_mcp/workflow.py src/vikunja_mcp/server.py \
        tests/unit/test_workflow_wip.py tests/unit/test_claimable_cmd.py
git commit -m "feat(workflow): next_task(exclude) + wip accounting for the parallel drain"
```

---

### Task 3: `workspace` CLI — create and release

The only module in the package that runs git. No tracker access at all in this task: create and release need neither token nor network.

**Files:**
- Create: `src/vikunja_mcp/workspace_cmd.py`
- Modify: `src/vikunja_mcp/config.py` (add `worktree_root`), `src/vikunja_mcp/server.py:main()`
- Test: `tests/unit/test_workspace_cmd.py` (new), `tests/unit/test_config.py`

**Interfaces:**
- Consumes: `Config.worktree_root`.
- Produces: `ensure_workspace(task_id: int, role: str = "build", at: str | None = None, cwd: Path | None = None) -> dict`; `release_workspace(task_id: int, role: str = "build", cwd: Path | None = None) -> dict`; `list_worktrees(root: Path) -> list[dict]` (entries `{"path": Path, "branch": str | None, "detached": bool}`); `repo_root(cwd) -> Path`; `worktree_root(root: Path) -> Path`; `_repo_lock(root)` context manager; `run_workspace(argv: list[str]) -> int`; `WorkspaceError`. Task 4 adds `gc_workspaces` to this module.

- [ ] **Step 1: Add the `worktree_root` config key with tests**

Append to `tests/unit/test_config.py`:

```python
def test_worktree_root_defaults_to_none(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text('[tracker]\nurl = "http://x"\nproject_id = 3\n')
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).worktree_root is None


def test_worktree_root_reads_from_toml(tmp_path):
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nworktree_root = "../wt"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    assert load_config(cwd=tmp_path, environ={}).worktree_root == "../wt"


def test_env_overrides_worktree_root(tmp_path):
    """Unlike wip_limit (team policy), the worktree location is MACHINE-local — env wins."""
    (tmp_path / ".vikunja-mcp.toml").write_text(
        '[tracker]\nurl = "http://x"\nproject_id = 3\nworktree_root = "../wt"\n'
    )
    (tmp_path / ".vikunja-mcp.env").write_text("VIKUNJA_TOKEN=t\n")
    cfg = load_config(cwd=tmp_path, environ={"VIKUNJA_WORKTREE_ROOT": "/srv/trees"})
    assert cfg.worktree_root == "/srv/trees"
```

Run: `uv run pytest tests/unit/test_config.py -k worktree_root -v` → FAIL.

Then in `config.py`: add `ENV_WORKTREE_ROOT = "VIKUNJA_WORKTREE_ROOT"` next to the other env constants, add the field

```python
    # where per-task git worktrees are materialised (parallel drain). MACHINE-local, unlike
    # wip_limit — so unlike it, the env layers DO win over the committed toml.
    # None -> workspace_cmd's default, a `<repo>.worktrees` sibling of the repo.
    worktree_root: str | None = None
```

and the resolution next to the others:

```python
    worktree_root = (
        env.get(ENV_WORKTREE_ROOT)
        or repo_env.get(ENV_WORKTREE_ROOT)
        or repo.get("worktree_root")
        or user.get(ENV_WORKTREE_ROOT)
        or None
    )
```

passing `worktree_root=worktree_root,` into `Config(...)`.

Run: `uv run pytest tests/unit/test_config.py -k worktree_root -v` → PASS.

- [ ] **Step 2: Write the failing workspace tests**

Create `tests/unit/test_workspace_cmd.py`:

```python
"""`vikunja-mcp workspace` against REAL git in tmp_path (a local origin, no network).

A fake would share this module's model of git and prove nothing about the one behaviour that
matters: that housekeeping can never destroy an agent's unpushed work.
"""
import subprocess
from pathlib import Path

import pytest

from vikunja_mcp.workspace_cmd import (
    WorkspaceError,
    ensure_workspace,
    list_worktrees,
    release_workspace,
    worktree_root,
)


def _git(cwd, *args):
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def repo(tmp_path):
    """A work repo on `main` with a local bare origin it has already pushed to."""
    origin = tmp_path / "origin.git"
    subprocess.run(["git", "init", "--bare", "-b", "main", str(origin)],
                   check=True, capture_output=True)
    work = tmp_path / "work"
    work.mkdir()
    _git(work, "init", "-b", "main")
    _git(work, "config", "user.email", "t@example.com")
    _git(work, "config", "user.name", "Tester")
    (work / "README.md").write_text("hi\n")
    _git(work, "add", "README.md")
    _git(work, "commit", "-m", "init")
    _git(work, "remote", "add", "origin", str(origin))
    _git(work, "push", "-u", "origin", "main")
    return work


def test_create_makes_a_worktree_on_a_task_branch(repo):
    res = ensure_workspace(42, cwd=repo)
    path = Path(res["path"])
    assert res["created"] is True and res["branch"] == "task/42"
    assert path.is_dir() and (path / "README.md").exists()
    assert path.parent == worktree_root(repo) == repo.parent / "work.worktrees"
    assert _git(path, "rev-parse", "--abbrev-ref", "HEAD") == "task/42"


def test_create_is_idempotent(repo):
    first = ensure_workspace(42, cwd=repo)
    second = ensure_workspace(42, cwd=repo)
    assert second["path"] == first["path"] and second["created"] is False


def test_create_reuses_an_existing_branch_and_keeps_its_commits(repo):
    """The resume-after-crash path: the agent's unfinished commits must survive."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "wip.txt").write_text("half done\n")
    _git(path, "add", "wip.txt")
    _git(path, "commit", "-m", "wip")
    sha = _git(path, "rev-parse", "HEAD")
    _git(repo, "worktree", "remove", str(path))          # agent died, tree gone, branch kept

    again = ensure_workspace(42, cwd=repo)
    assert again["created"] is True and again["branch"] == "task/42"
    assert _git(Path(again["path"]), "rev-parse", "HEAD") == sha


def test_review_role_is_a_separate_detached_tree(repo):
    build = ensure_workspace(42, cwd=repo)
    head = _git(repo, "rev-parse", "HEAD")
    review = ensure_workspace(42, role="review", at=head, cwd=repo)
    assert review["path"] != build["path"]
    assert Path(review["path"]).name == "review-42"
    assert _git(Path(review["path"]), "rev-parse", "HEAD") == head
    assert _git(Path(review["path"]), "rev-parse", "--abbrev-ref", "HEAD") == "HEAD"  # detached


def test_release_removes_a_clean_pushed_tree_and_its_branch(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    res = release_workspace(42, cwd=repo)
    assert res["released"] is True
    assert not path.exists()
    assert "task/42" not in _git(repo, "branch", "--list", "task/42")


def test_release_refuses_a_dirty_tree(repo):
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "scratch.txt").write_text("uncommitted\n")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "dirty" in res["reason"]
    assert path.exists() and (path / "scratch.txt").exists()


def test_release_refuses_unpushed_commits(repo):
    """THE guard: housekeeping must never be how an agent's work disappears."""
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "real work")
    res = release_workspace(42, cwd=repo)
    assert res["released"] is False and "commit" in res["reason"]
    assert path.exists()
    assert _git(path, "log", "--oneline", "-1")


def test_release_of_a_missing_tree_is_not_an_error(repo):
    res = release_workspace(999, cwd=repo)
    assert res["released"] is False and "no worktree" in res["reason"]


def test_occupied_path_that_is_not_a_worktree_is_refused(repo):
    squatter = worktree_root(repo) / "task-42"
    squatter.mkdir(parents=True)
    (squatter / "precious.txt").write_text("do not clobber\n")
    with pytest.raises(WorkspaceError, match="not a registered worktree"):
        ensure_workspace(42, cwd=repo)
    assert (squatter / "precious.txt").exists()


def test_worktree_root_honours_an_explicit_override(repo, monkeypatch):
    monkeypatch.setenv("VIKUNJA_WORKTREE_ROOT", str(repo.parent / "elsewhere"))
    res = ensure_workspace(42, cwd=repo)
    assert Path(res["path"]).parent == repo.parent / "elsewhere"


def test_list_worktrees_reports_slashed_branch_names_intact(repo):
    ensure_workspace(42, cwd=repo)
    branches = {wt["branch"] for wt in list_worktrees(repo)}
    assert "task/42" in branches       # not "42" — refs/heads/task/42 must not be split
```

- [ ] **Step 3: Run the tests to verify they fail**

Run: `uv run pytest tests/unit/test_workspace_cmd.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'vikunja_mcp.workspace_cmd'`.

- [ ] **Step 4: Implement `workspace_cmd.py`**

Create `src/vikunja_mcp/workspace_cmd.py`:

```python
"""`vikunja-mcp workspace` — per-task git worktrees for the parallel drain.

THE ONLY MODULE IN THIS PACKAGE THAT RUNS GIT. server.py / workflow.py / api.py stay git-free
on purpose: the MCP server's job is HTTP to Vikunja, and a subprocess in that path would be a
new class of failure on a stdio server that must never crash.

WHY IT LIVES HERE AT ALL. `git worktree add` refuses to check out a branch that is already
checked out elsewhere, so two parallel agents cannot both sit on `main` — each needs its own
tree on its own throwaway `task/<id>` branch, and pushes with `git push origin HEAD:main` so
the "one task = one commit on main" rule and the CI auto-release survive untouched. Creating
that is trivial; REAPING it is not, and reaping is the part only the tracker can do — nothing
else knows whether the task behind an orphaned tree is still alive (see gc_workspaces).

SAFETY INVARIANT, taken verbatim from hgdev-acp's reaper: push OK -> remove, push FAIL -> KEEP.
Housekeeping must never be how an agent's work disappears.
"""
import argparse
import fcntl
import json
import re
import subprocess
from contextlib import contextmanager
from pathlib import Path

BUILD_NAME = "task-{task_id}"
REVIEW_NAME = "review-{task_id}"
BUILD_BRANCH = "task/{task_id}"
_NAME_RE = re.compile(r"^(task|review)-(\d+)$")
_ROLE_BY_PREFIX = {"task": "build", "review": "review"}


class WorkspaceError(Exception):
    """The message is printed as the CLI's JSON error line."""


def _git(*args: str, cwd: Path | None = None) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    if proc.returncode != 0:
        detail = proc.stderr.strip() or proc.stdout.strip()
        raise WorkspaceError(f"git {' '.join(args)} failed: {detail}")
    return proc.stdout.strip()


def _git_ok(*args: str, cwd: Path | None = None) -> bool:
    return subprocess.run(["git", *args], cwd=cwd, capture_output=True).returncode == 0


def repo_root(cwd: Path | None = None) -> Path:
    return Path(_git("rev-parse", "--show-toplevel", cwd=cwd))


def worktree_root(root: Path) -> Path:
    """Where per-task trees live. Default: a SIBLING of the repo, never inside it — inside,
    pytest collection, ruff and `git add -A` would all sweep them up."""
    import os

    from vikunja_mcp.config import ENV_WORKTREE_ROOT, load_config

    # env FIRST, on purpose: create/release need no tracker config at all, and load_config
    # RAISES without url/project_id — reading it first would throw away a perfectly good
    # VIKUNJA_WORKTREE_ROOT in any repo that is not tracker-configured.
    configured = os.environ.get(ENV_WORKTREE_ROOT)
    if not configured:
        try:
            configured = load_config(cwd=root).worktree_root
        except Exception:  # noqa: BLE001 — no tracker config is fine; create/release need none
            configured = None
    if configured:
        path = Path(configured)
        return path if path.is_absolute() else (root / path).resolve()
    return root.parent / f"{root.name}.worktrees"


def default_base(root: Path) -> str:
    """The remote's default branch name. origin/HEAD is often absent in a fresh clone."""
    try:
        ref = _git("symbolic-ref", "--quiet", "refs/remotes/origin/HEAD", cwd=root)
        return ref.removeprefix("refs/remotes/origin/")
    except WorkspaceError:
        return "main"


@contextmanager
def _repo_lock(root: Path):
    """Serialise worktree mutations: the pump dispatches agents concurrently, and two
    `worktree add` calls on one repo race. Same shape as hgdev-acp's per-mirror mutex.

    NOT reentrant — flock on a second fd in the same process would deadlock. Anything that
    needs the lock while holding it must call the _locked cores, never the public wrappers.
    """
    common = Path(_git("rev-parse", "--git-common-dir", cwd=root))
    if not common.is_absolute():
        common = (root / common).resolve()
    lock_path = common / "vikunja-mcp-worktree.lock"
    with open(lock_path, "w") as fh:
        fcntl.flock(fh, fcntl.LOCK_EX)
        try:
            yield
        finally:
            fcntl.flock(fh, fcntl.LOCK_UN)


def list_worktrees(root: Path) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in _git("worktree", "list", "--porcelain", cwd=root).splitlines():
        if not line.strip():
            if current:
                entries.append(current)
                current = None
            continue
        key, _, value = line.partition(" ")
        if key == "worktree":
            if current:
                entries.append(current)
            current = {"path": Path(value), "branch": None, "detached": False}
        elif key == "branch" and current is not None:
            # removeprefix, NOT rsplit("/") — refs/heads/task/42 must stay "task/42"
            current["branch"] = value.removeprefix("refs/heads/")
        elif key == "detached" and current is not None:
            current["detached"] = True
    if current:
        entries.append(current)
    return entries


def _find(root: Path, task_id: int, role: str) -> dict | None:
    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    target = worktree_root(root) / name
    for wt in list_worktrees(root):
        if wt["path"] == target:
            return wt
    return None


def _ensure_locked(root: Path, task_id: int, role: str, at: str | None) -> dict:
    _git("worktree", "prune", cwd=root)
    _git("fetch", "origin", cwd=root)
    wt_root = worktree_root(root)
    wt_root.mkdir(parents=True, exist_ok=True)
    base = f"origin/{default_base(root)}"

    existing = _find(root, task_id, role)
    if existing is not None:
        return {
            "role": role, "task_id": task_id, "path": str(existing["path"]),
            "branch": existing["branch"], "created": False,
        }

    name = (BUILD_NAME if role == "build" else REVIEW_NAME).format(task_id=task_id)
    path = wt_root / name
    if path.exists():
        raise WorkspaceError(
            f"{path} exists but is not a registered worktree — refusing to clobber it"
        )

    if role == "review":
        _git("worktree", "add", "--detach", str(path), at or base, cwd=root)
        return {
            "role": "review", "task_id": task_id, "path": str(path),
            "branch": None, "detached": _git("rev-parse", "HEAD", cwd=path), "created": True,
        }

    branch = BUILD_BRANCH.format(task_id=task_id)
    if _git_ok("show-ref", "--verify", "--quiet", f"refs/heads/{branch}", cwd=root):
        # the branch outlived its tree (crashed agent) — reattach, NEVER recreate: it carries
        # the unfinished commits the resume agent is coming back for
        _git("worktree", "add", str(path), branch, cwd=root)
    else:
        _git("worktree", "add", "-b", branch, str(path), base, cwd=root)
    return {
        "role": "build", "task_id": task_id, "path": str(path),
        "branch": branch, "base": base, "created": True,
    }


def _release_locked(root: Path, task_id: int, role: str) -> dict:
    _git("worktree", "prune", cwd=root)
    wt = _find(root, task_id, role)
    if wt is None:
        return {"released": False, "task_id": task_id, "role": role,
                "reason": "no worktree for this task"}
    path = wt["path"]
    dirty = _git("status", "--porcelain", cwd=path)
    if dirty:
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "reason": f"working tree is dirty ({len(dirty.splitlines())} entries)"}
    base = f"origin/{default_base(root)}"
    unpushed = _git("log", "--oneline", f"{base}..HEAD", cwd=path)
    if unpushed:
        return {"released": False, "task_id": task_id, "role": role, "path": str(path),
                "reason": f"{len(unpushed.splitlines())} commit(s) not on {base}"}
    _git("worktree", "remove", str(path), cwd=root)
    if wt["branch"]:
        _git("branch", "-D", wt["branch"], cwd=root)
    return {"released": True, "task_id": task_id, "role": role,
            "path": str(path), "branch": wt["branch"]}


def ensure_workspace(
    task_id: int, role: str = "build", at: str | None = None, cwd: Path | None = None
) -> dict:
    root = repo_root(cwd)
    with _repo_lock(root):
        return _ensure_locked(root, task_id, role, at)


def release_workspace(task_id: int, role: str = "build", cwd: Path | None = None) -> dict:
    root = repo_root(cwd)
    with _repo_lock(root):
        return _release_locked(root, task_id, role)


def run_workspace(argv: list[str]) -> int:
    parser = argparse.ArgumentParser(prog="vikunja-mcp workspace")
    parser.add_argument("task_id", nargs="?", type=int, help="create a workspace for this task")
    parser.add_argument("--role", choices=("build", "review"), default="build")
    parser.add_argument("--at", help="review role: the ref to check out (default origin/<main>)")
    parser.add_argument("--release", type=int, metavar="TASK_ID")
    try:
        args = parser.parse_args(argv)
        if args.release is not None:
            result = release_workspace(args.release, role=args.role)
        elif args.task_id is not None:
            result = ensure_workspace(args.task_id, role=args.role, at=args.at)
        else:
            raise WorkspaceError("give a task id to create, or --release <task id>")
    except SystemExit:
        raise
    except Exception as e:      # noqa: BLE001 — a CLI: ANY failure is one JSON line + exit 1
        print(json.dumps({"error": f"{e.__class__.__name__}: {e}"}))
        return 1
    print(json.dumps(result))
    return 0
```

- [ ] **Step 5: Run the tests to verify they pass**

Run: `uv run pytest tests/unit/test_workspace_cmd.py -v`
Expected: PASS (12 tests).

- [ ] **Step 6: Prove the two safety guards are real (negative pins)**

House rule: a "must NOT happen" test can pin the wrong guard and stay green. Verify each one bites.

1. In `_release_locked`, comment out the `if dirty:` return. Run `uv run pytest tests/unit/test_workspace_cmd.py -k dirty -v`. Expected: **FAIL**. Restore it.
2. Comment out the `if unpushed:` return. Run `uv run pytest tests/unit/test_workspace_cmd.py -k unpushed -v`. Expected: **FAIL**. Restore it.

If either still passes, the test is fictional — fix the test before continuing.

- [ ] **Step 7: Wire the subcommand into `main()`**

In `src/vikunja_mcp/server.py`, add right after the `claimable` block (before `_self_heal_installed_artifacts()`):

```python
    # `workspace` — per-task git worktrees for the parallel drain. Dispatched before the
    # self-heal for the same reasons as `claimable`: it is called per task by the pump and
    # must start fast, and it must not touch ~/.claude.
    if args and args[0] == "workspace":
        from vikunja_mcp.workspace_cmd import run_workspace

        raise SystemExit(run_workspace(args[1:]))
```

- [ ] **Step 8: Smoke-test the CLI by hand**

Run:
```bash
uv run vikunja-mcp workspace 999 && uv run vikunja-mcp workspace --release 999
```
Expected: two JSON lines — a created path under `../vikunja-mcp.worktrees/task-999`, then `{"released": true, …}`. Confirm with `git worktree list` that nothing is left behind.

- [ ] **Step 9: Run the full unit suite and lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 10: Commit**

```bash
git add src/vikunja_mcp/workspace_cmd.py src/vikunja_mcp/server.py src/vikunja_mcp/config.py \
        tests/unit/test_workspace_cmd.py tests/unit/test_config.py
git commit -m "feat(cli): workspace — per-task git worktrees, never reaping unpushed work"
```

---

### Task 4: `workspace --gc` and the tracker liveness accessors

The reason the workspace layer lives in vikunja-mcp: only the tracker knows which task behind an orphaned tree is still alive.

**Files:**
- Modify: `src/vikunja_mcp/workflow.py` (two accessors), `src/vikunja_mcp/workspace_cmd.py` (`gc_workspaces`, `--gc`)
- Test: `tests/unit/test_workspace_cmd.py`, `tests/integration/test_workspace_gc.py` (new)

**Interfaces:**
- Consumes: `list_worktrees`, `_release_locked`, `_repo_lock`, `worktree_root` from Task 3.
- Produces: `Workflow.active_task_ids() -> list[int]`; `Workflow.review_task_ids() -> list[int]`; `gc_workspaces(cwd: Path | None = None, workflow=None) -> dict` returning `{"released": [...], "kept": [...]}`.

- [ ] **Step 1: Write the failing accessor tests**

Append to `tests/unit/test_workflow_wip.py`:

```python
# --- liveness accessors: what workspace --gc asks the tracker ---

def test_active_task_ids_lists_my_design_and_build_tasks():
    api, wf = _env()
    first = _hold(api, wf, "designing")
    second = _hold(api, wf, "building")
    wf.advance(second["id"], to="build", spec="approach")
    api.add_task("someone else's queue item", "Queue")
    assert sorted(wf.active_task_ids()) == sorted([first["id"], second["id"]])


def test_review_task_ids_includes_cards_i_do_not_own():
    """A review tree is alive while the CARD is in Review — the reviewer is never its
    assignee, so keying this off ownership would reap a running reviewer's tree."""
    api, wf = _env()
    mine = _hold(api, wf, "mine")
    wf.advance(mine["id"], to="build", spec="approach")
    wf.advance(mine["id"], to="review", worklog="done", evidence="abc1234")
    theirs = api.add_task("theirs", "Review")
    assert sorted(wf.review_task_ids()) == sorted([mine["id"], theirs["id"]])
```

Run: `uv run pytest tests/unit/test_workflow_wip.py -k task_ids -v` → FAIL (`AttributeError`).

- [ ] **Step 2: Implement the accessors**

In `src/vikunja_mcp/workflow.py`, next to `_my_active_tasks`:

```python
    def active_task_ids(self) -> list[int]:
        """Ids of tasks in an ACTIVE stage (Design/Build) assigned to me — the live BUILD set.

        Public on purpose: `vikunja-mcp workspace --gc` needs it to tell a crashed agent's
        orphaned worktree from a live one, and that boundary deserves a real interface rather
        than a CLI reaching into _my_active_tasks."""
        return [t["id"] for _stage, t in self._my_active_tasks()]

    def review_task_ids(self) -> list[int]:
        """Ids of every task sitting in Review — the live REVIEW set.

        Deliberately NOT filtered by assignee: a reviewer works on someone ELSE's card, so
        ownership would reap the tree out from under a running review."""
        board = self._board(require_titles=frozenset({"Review"}))
        return [
            t["id"] for bucket in board if bucket["title"] == "Review"
            for t in (bucket.get("tasks") or [])
        ]
```

Run: `uv run pytest tests/unit/test_workflow_wip.py -k task_ids -v` → PASS.

- [ ] **Step 3: Write the failing GC tests**

Append to `tests/unit/test_workspace_cmd.py`:

```python
from tests.unit.fakes import FakeAPI
from vikunja_mcp.workflow import STAGES, Workflow
from vikunja_mcp.workspace_cmd import gc_workspaces


@pytest.fixture
def tracker():
    api = FakeAPI(buckets=STAGES)
    return api, Workflow(api, project_id=3)


def test_gc_reaps_a_tree_whose_task_is_no_longer_active(repo, tracker):
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])          # nothing on the board -> dead
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert [r["task_id"] for r in res["released"]] == [42]
    assert not path.exists()


def test_gc_keeps_a_tree_whose_task_is_still_in_build(repo, tracker):
    api, wf = tracker
    task = api.add_task("live work", "Queue")
    wf.claim(task["id"])
    path = Path(ensure_workspace(task["id"], cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_keeps_a_review_tree_while_the_card_is_in_review(repo, tracker):
    api, wf = tracker
    task = api.add_task("under review", "Review")
    head = _git(repo, "rev-parse", "HEAD")
    path = Path(ensure_workspace(task["id"], role="review", at=head, cwd=repo)["path"])
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert path.exists()


def test_gc_never_reaps_unpushed_work_and_reports_it(repo, tracker):
    """The orphan of a crashed agent that got as far as committing: dead on the board, but
    its commits are the whole reason we keep it. GC must REPORT, not destroy."""
    api, wf = tracker
    path = Path(ensure_workspace(42, cwd=repo)["path"])
    (path / "feature.txt").write_text("real work\n")
    _git(path, "add", "feature.txt")
    _git(path, "commit", "-m", "crashed mid-task")
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == []
    assert [k["task_id"] for k in res["kept"]] == [42]
    assert "commit" in res["kept"][0]["reason"]
    assert path.exists()


def test_gc_ignores_directories_that_are_not_task_worktrees(repo, tracker):
    api, wf = tracker
    stray = repo.parent / "unrelated"
    stray.mkdir()
    _git(repo, "worktree", "add", str(stray), "-b", "unrelated-branch")
    res = gc_workspaces(cwd=repo, workflow=wf)
    assert res["released"] == [] and res["kept"] == []
    assert stray.exists()
```

Run: `uv run pytest tests/unit/test_workspace_cmd.py -k gc -v` → FAIL (`ImportError: cannot import name 'gc_workspaces'`).

- [ ] **Step 4: Implement `gc_workspaces`**

Add to `src/vikunja_mcp/workspace_cmd.py`:

```python
def _parse_workspace_name(name: str) -> tuple[str, int] | None:
    match = _NAME_RE.match(name)
    if match is None:
        return None
    return _ROLE_BY_PREFIX[match.group(1)], int(match.group(2))


def _build_workflow():
    from vikunja_mcp.api import VikunjaAPI
    from vikunja_mcp.config import load_config
    from vikunja_mcp.workflow import Workflow

    cfg = load_config()
    return Workflow(VikunjaAPI(cfg.url, cfg.token), cfg.project_id)


def gc_workspaces(cwd: Path | None = None, workflow=None) -> dict:
    """Reap worktrees whose task is no longer alive on the board.

    THE tracker-aware operation, and the reason this module ships with the tracker: a crashed
    agent leaves a tree behind, and nothing but the board can say whether the task behind it is
    still being worked. Liveness differs by role and must not be conflated — a BUILD tree is
    alive while its task is in Design/Build assigned to me, a REVIEW tree while its card is in
    Review (any assignee). Read-only against the tracker, same class as `claimable`.

    The safety guards of release still apply: a dead task whose tree holds unpushed commits is
    KEPT and REPORTED, never destroyed.
    """
    root = repo_root(cwd)
    wf = workflow if workflow is not None else _build_workflow()
    alive = {"build": set(wf.active_task_ids()), "review": set(wf.review_task_ids())}
    wt_root = worktree_root(root)

    released, kept = [], []
    # ONE lock for the whole sweep: _repo_lock is not reentrant, so call the _locked core, never
    # the public release_workspace wrapper (that would deadlock on its own flock).
    with _repo_lock(root):
        for wt in list_worktrees(root):
            if wt["path"].parent != wt_root:
                continue                       # not ours — never touch a hand-made worktree
            parsed = _parse_workspace_name(wt["path"].name)
            if parsed is None:
                continue
            role, task_id = parsed
            if task_id in alive[role]:
                continue
            result = _release_locked(root, task_id, role)
            (released if result["released"] else kept).append(result)
    return {"released": released, "kept": kept}
```

Add `--gc` to `run_workspace`:

```python
    parser.add_argument("--gc", action="store_true",
                        help="reap worktrees whose task is no longer alive on the board")
```

and, as the FIRST branch of the dispatch chain:

```python
        if args.gc:
            result = gc_workspaces()
        elif args.release is not None:
```

- [ ] **Step 5: Run the GC tests to verify they pass**

Run: `uv run pytest tests/unit/test_workspace_cmd.py -v`
Expected: PASS (17 tests).

- [ ] **Step 6: Prove the GC guard bites (negative pin)**

In `gc_workspaces`, temporarily replace `alive` with `{"build": set(), "review": set()}` and run `uv run pytest tests/unit/test_workspace_cmd.py -k "gc_keeps" -v`. Expected: **FAIL** (both keep-tests). Restore. If they pass, the tests are not exercising liveness.

- [ ] **Step 7: Add the integration test**

Create `tests/integration/test_workspace_gc.py`:

```python
"""`review_task_ids` against a REAL Vikunja board — the one part of --gc the fake cannot
prove, because it depends on the live view_tasks/bucket shape rather than on our mirror of it."""


def test_review_task_ids_sees_a_real_card_in_review(workflow, api):
    task = api.add_task_to_bucket_stage("needs review", "Review")
    assert task["id"] in workflow.review_task_ids()
```

Open `tests/integration/conftest.py` first and use its existing fixtures and helpers verbatim — mirror how `tests/integration/test_sequence_gate.py` puts a task into a named bucket rather than inventing `add_task_to_bucket_stage`.

Run: see CLAUDE.md for the container recipe, then
`VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration/test_workspace_gc.py -q`
Expected: PASS.

- [ ] **Step 8: Run the full unit suite and lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/vikunja_mcp/workflow.py src/vikunja_mcp/workspace_cmd.py \
        tests/unit/test_workspace_cmd.py tests/unit/test_workflow_wip.py \
        tests/integration/test_workspace_gc.py
git commit -m "feat(cli): workspace --gc — reap orphaned trees using tracker liveness"
```

---

### Task 5: The rulebook — SKILL.md and CLAUDE.md

Everything so far is inert machinery. This task is what makes an agent use it — and it auto-propagates to every consumer on the next server start, so it is the highest-blast-radius change in the plan.

**Files:**
- Modify: `src/vikunja_mcp/skills/tracker/SKILL.md`, `CLAUDE.md`
- Test: `tests/unit/test_skill_contract.py`

**Interfaces:**
- Consumes: `next_task(exclude=…)` + `wip`/`wip_saturated` (Task 2), the `workspace` CLI (Tasks 3–4).
- Produces: no code interface.

- [ ] **Step 1: Add the contract pins first**

Append to `tests/unit/test_skill_contract.py`:

```python
def test_the_parallel_drain_rules_cite_real_signals():
    """The rulebook self-heals onto every consumer with no review gate of its own, so a rule
    naming a signal the code does not emit would reach every agent everywhere. Pin the tokens."""
    text = _skill_text()
    source = _workflow_src()
    for token in ("wip_saturated", "exclude", "wip_limit"):
        assert token in text, f"{token!r} is not documented in SKILL.md"
        assert token in source, f"{token!r} is documented in SKILL.md but gone from workflow.py"


def test_the_integration_recipe_is_present_and_pushes_to_the_default_branch():
    """Parallel agents sit on task/<id> branches; a plain `git push` would push the BRANCH and
    leave main untouched, silently stranding finished work outside the release pipeline."""
    text = _skill_text()
    assert "git push origin HEAD:main" in text
    assert "workspace --gc" in text
```

Run: `uv run pytest tests/unit/test_skill_contract.py -k "parallel or integration_recipe" -v`
Expected: FAIL — the tokens are not in SKILL.md yet.

- [ ] **Step 2: Rewrite the drain rule in SKILL.md**

Replace the bullet **«Дренаж последовательный, не параллельный»** (in «Непрерывная работа (loop)») with:

```markdown
- **Ширина дренажа задаётся конфигом, а не тобой.** `next_task` в каждом ответе отдаёт
  `wip: {active, limit, free}`. `limit: null` или `1` — дренаж ПОСЛЕДОВАТЕЛЬНЫЙ: claim →
  задиспатчил пер-таск-агента → дождался, что он довёл задачу до Review → только тогда
  следующая. `limit > 1` — можно держать до `limit` пер-таск-агентов одновременно, каждого
  в СВОЁМ worktree (см. «Параллельный дренаж»). Два агента в одном рабочем каталоге не
  держим НИКОГДА — они подерутся за файлы и за HEAD.
- **`wip_saturated: true` — это НЕ пустая очередь.** Слоты заняты, задача есть, брать некуда:
  дождись возврата любого агента и снова зови `next_task`. Не клейми и НЕ уступай ход
  (`ScheduleWakeup` здесь — потерянный тик).
```

- [ ] **Step 3: Add the «Параллельный дренаж» section to SKILL.md**

Insert a new section right after «Непрерывная работа (loop)»:

```markdown
## Параллельный дренаж (когда `wip.limit > 1`)

- **Тик оркестратора:**
  1. `vikunja-mcp workspace --gc` — убрать деревья от упавших в прошлых тиках агентов;
  2. пока `wip.free > 0`: `next_task(exclude=[id задач, на которых у тебя ПРЯМО СЕЙЧАС
     живёт агент])` → `claim` → `vikunja-mcp workspace <id>` → диспатч ФОНОВОГО
     пер-таск-агента, путь из ответа — в бриф как его рабочий каталог;
  3. агент вернулся → диспатчишь его ревьюера в фоне (ему —
     `vikunja-mcp workspace <id> --role review --at <sha из evidence>`) → слот свободен → к п.2;
  4. `wip_saturated` → ждёшь возврата агента; `task: null` без него и никого в работе →
     уступаешь ход.
- **`exclude` ведёшь ТЫ, и только в пределах тика.** Трекер не знает, жив ли твой саб-агент.
  Убитый ход теряет этот набор — и это нормально: на следующем тике пустой `exclude` вернёт
  задачу как «твою активную», и работает обычное правило «агент упал → диспатчь свежий
  resume-агент». Он вернётся в ТОТ ЖЕ worktree со своей недоделанной работой.
- **Ревью слот не занимает** — оно фоновое и не «твоя активная задача».
- **Не завелось (не git-репо, нет `origin`, `workspace` вернул `error`) — цикл НЕ роняем.**
  Работаем в один слот в основном чекауте, то есть ровно как в последовательном режиме.
```

- [ ] **Step 4: Rewrite the commit+push rule in SKILL.md**

In «Следы работы», replace the bullet «**Коммит+пуш — часть перевода в Review**» body (keep the heading) so the recipe reads:

```markdown
  - **Интеграция — это rebase + ПОВТОРНАЯ проверка + пуш, а не просто `git push`.** Ты сидишь
    на одноразовой ветке `task/<id>`, поэтому пушить надо ЯВНО в главную ветку:

    ```sh
    git add <файлы этой задачи>
    git commit -m "type(scope): … (tracker #N)"
    git fetch origin && git rebase origin/main
    # ПРОГНАТЬ КРИТЕРИИ ГОТОВНОСТИ ЗАНОВО (те, что дал оркестратор в брифе)
    git push origin HEAD:main      # отбило — повтори блок, максимум 3 круга
    git rev-parse HEAD             # evidence — sha ПОСЛЕ успешного пуша, не до
    ```

    Повторный прогон после rebase — не перестраховка: пока ты работал, в `main` мог приехать
    сосед, и merge мог склеить два по отдельности верных изменения в одно неверное. Конфликт
    rebase разруливаешь сам (контекст задачи у тебя); не получается — `call_human`, задача
    остаётся в Build, worktree никуда не денется. Отбило 3 раза подряд — тоже `call_human`.
  - **После `advance(to='review')` освободи дерево:** `vikunja-mcp workspace --release <id>`.
    Откажет (грязно / есть незапушенное) — это ЗАЩИТА, а не ошибка: разберись, что осталось,
    и не пытайся снести дерево руками.
```

- [ ] **Step 5: Add the `ordered` rule to «Декомпозиция и файлинг находок»**

Append to the `decompose` bullet:

```markdown
  **При параллельном дренаже `ordered` — не косметика.** `ordered=False` означает «эти
  подзадачи можно строить ОДНОВРЕМЕННО», и оркестратор именно так и поступит. Сомневаешься,
  трогают ли они один и тот же код — ставь `ordered=True`: цепочка отпускает следующую
  подзадачу ровно тогда, когда предыдущая доехала до Review, то есть когда её коммит уже
  в `main`.
```

- [ ] **Step 6: Run the pins to verify they pass**

Run: `uv run pytest tests/unit/test_skill_contract.py -v`
Expected: PASS (including the pre-existing pins — check none broke).

- [ ] **Step 7: Update CLAUDE.md**

In the Architecture list, extend the `config.py` bullet to name the two new keys, and add a new bullet after `claimable_cmd.py`:

```markdown
- `src/vikunja_mcp/workspace_cmd.py` — `vikunja-mcp workspace`: per-task git worktrees for
  the parallel drain (`wip_limit > 1`). **The ONLY module in the package that runs git** —
  `server.py`/`workflow.py`/`api.py` stay git-free by rule, not by accident. `git worktree add`
  refuses a branch already checked out, so each agent gets its own `task/<id>` branch and
  pushes with `git push origin HEAD:main` — "one task = one commit on main" and the CI
  auto-release survive untouched. Create/release need neither token nor network; only `--gc`
  talks to the tracker, because only the board can say whether the task behind an orphaned
  tree is still alive (build tree ⇔ Design/Build assigned to me, review tree ⇔ card in Review).
  Safety invariant taken from hgdev-acp: push OK → remove, push FAIL → KEEP. Housekeeping is
  never how an agent's work disappears.
```

Also update the Dogfood section's "orchestrator is a thin pump" sentence to note that with `wip_limit > 1` the pump keeps up to `wip_limit` per-task agents in flight, each in its own worktree.

- [ ] **Step 8: Run the full unit suite and lint**

Run: `uv run pytest tests/unit -q && uv run ruff check .`
Expected: all green.

- [ ] **Step 9: Commit**

```bash
git add src/vikunja_mcp/skills/tracker/SKILL.md CLAUDE.md tests/unit/test_skill_contract.py
git commit -m "docs(skill): parallel drain rules — worktree per task, rebase-then-push"
```

---

### Task 6: Dogfood — turn it on for this repo

Tests share the implementation's model of the world. This repo has already paid for that lesson once: eleven features of tests and cross-model reviews all missed a bug that ninety minutes of dogfooding found. Do not skip this task.

**Files:**
- Modify: `.vikunja-mcp.toml`
- Test: a real `/loop` run against the live tracker

**Interfaces:**
- Consumes: everything from Tasks 1–5.
- Produces: nothing new — a verdict.

- [ ] **Step 1: Turn on two slots**

Replace the commented-out `enforce_single_wip` note in `.vikunja-mcp.toml` with:

```toml
wip_limit = 2   # parallel drain: two per-task agents at a time, each in its own worktree
                # (vikunja-mcp workspace). 2, not more, on purpose — it bounds the blast
                # radius of two unrelated tasks landing on main in the same minute.
```

- [ ] **Step 2: Verify the config resolves**

Run: `uv run python -c "from vikunja_mcp.config import load_config; print(load_config().wip_limit)"`
Expected: `2`.

- [ ] **Step 3: Verify the loop's idle check is unchanged**

Run: `uv run vikunja-mcp claimable`
Expected: one JSON line whose `kind` is one of the seven known values. **`wip_saturated` must not appear anywhere in the output** — if it does, the cross-repo contract with hgdev-acp is broken and this must be fixed before anything is pushed.

- [ ] **Step 4: Queue two genuinely independent tasks and run the drain**

Put two unrelated tasks in Queue (no `follows`/`blocked` between them, and ideally touching different files). Run the orchestrator loop and watch for all of:

- two worktrees appear under `../vikunja-mcp.worktrees/`, on branches `task/<id>`;
- `next_task` stops offering work at two active tasks (`wip_saturated`) rather than claiming a third;
- both tasks reach Review, each with its own commit on `main`, and `git log --oneline` shows them as separate commits — not one mixed diff;
- each `evidence` sha actually exists (`git rev-parse <sha>` — a subagent can report a sha that never landed);
- the build worktrees are gone after `--release`, and `git worktree list` is clean.

- [ ] **Step 5: Verify GC reaps a crashed agent's tree**

Create a workspace for a task that is not on the board and confirm the sweep:

```bash
uv run vikunja-mcp workspace 999999
uv run vikunja-mcp workspace --gc
git worktree list
```
Expected: `--gc` reports `task_id: 999999` under `released`, and `git worktree list` no longer shows it.

- [ ] **Step 6: Commit**

```bash
git add .vikunja-mcp.toml
git commit -m "chore(tracker): wip_limit = 2 — dogfood the parallel worktree drain"
```

- [ ] **Step 7: Report what the dogfood found**

Write up what actually happened — especially anything the tests agreed on and reality did not. If the drain produced a bad merge, a stranded worktree, or a push race the recipe did not survive, that is a finding, not a failure of the run: file it (`file_task`) before moving on.

---

## Self-Review

**Spec coverage.** Every section of the spec maps to a task: config + claim gate → Task 1; `next_task` `exclude`/`wip`/`wip_saturated` + the `claimable` contract pin → Task 2; the dependency analysis → no code needed (the existing sequence gate), with its two behavioural consequences landing as the `ordered` rule and the rebase-then-re-verify recipe in Task 5; the `workspace` CLI create/release/layout/lock/git-isolation → Task 3; `--gc` and role-specific liveness → Task 4; the orchestrator tick, per-task agent rules, degradation table → Task 5; rollout step 4 → Task 6.

**Known gap, accepted:** the spec's error-handling row "push rejected repeatedly (>3 rounds) → `call_human`" is enforced by prose in SKILL.md, not by code — there is nothing in this codebase that runs the agent's git for it. Task 5 states the bound explicitly so it is at least reviewable.

**Type consistency.** `_effective_wip_limit()` (Task 1) is the single limit resolver used by both `claim` and `next_task`. `wip` is `{"active", "limit", "free"}` everywhere. Workspace dicts use `task_id`/`role`/`path`/`branch`/`created` on create and `released`/`reason` on release, and `gc_workspaces` returns `_release_locked` results verbatim, so `kept[i]["reason"]` and `released[i]["task_id"]` are the same shapes the release tests already pin. `_repo_lock` is documented as non-reentrant at its definition and every in-lock caller uses the `_locked` cores.
