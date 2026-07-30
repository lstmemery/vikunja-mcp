"""The packaged SKILL.md ↔ workflow.py contract — a cheap mechanical net under the rulebook.

SKILL.md is the agent RULEBOOK, not documentation. Since #88 the server refreshes every
consumer's installed copy on start (sync_installed_artifacts), so it auto-propagates over the
moving `stable` branch with NO per-consumer pin, NO test, and NO review gate of its own. That
inverts the old silent-drift risk (#116): a rule naming a stage / label / marker / next_task
signal the tools no longer have would now reach every agent, everywhere, with nothing to catch
it. These tests pin the MECHANICAL subset of the contract — every code token the rulebook cites
must still resolve in workflow.py, and every real stage must be documented. They deliberately do
NOT check semantic correctness (whether a rule is right) — that is what independent review,
widened to every change in #117, is for; this is only the net that catches a cited token going
stale on either side.
"""
import inspect
from importlib.resources import files

from vikunja_mcp import config, server, workflow


def _skill_text() -> str:
    # the packaged copy that actually ships in the wheel and self-heals onto consumers (#88)
    return files("vikunja_mcp").joinpath("skills/tracker/SKILL.md").read_text(encoding="utf-8")


def _workflow_src() -> str:
    return inspect.getsource(workflow)


def test_every_workflow_stage_is_documented_in_the_skill():
    """A stage rename in workflow.STAGES (e.g. #54 'Call to Human' → 'Your Call') must reach the
    rulebook: every real pipeline stage is named in the skill, so a code-only rename fails here."""
    text = _skill_text()
    for stage in workflow.STAGES:
        assert stage in text, f"stage {stage!r} (workflow.STAGES) is not documented in SKILL.md"


def test_board_labels_the_skill_names_match_the_workflow_constants():
    """The verdict/epic labels agents and humans act on are pinned to their code constants: change
    LABEL_REVIEWED's value and the skill (still naming the old label) fails until synced. LABEL_BUG
    / LABEL_BLOCKED are intentionally excluded — the skill surfaces those by behaviour (review_kind,
    return_task), not by their literal label name, so asserting them would be a false pin."""
    text = _skill_text()
    for const in (
        workflow.LABEL_EPIC, workflow.LABEL_EPIC_READY,
        workflow.LABEL_REVIEWED, workflow.LABEL_REVIEW_FAILED,
    ):
        assert const in text, f"label {const!r} is no longer named in SKILL.md"


def test_next_task_and_advance_signal_keys_are_grounded_in_the_code():
    """The result keys the orchestrator branches on — the #102/#105/#117 additions — must exist on
    BOTH sides. Rename one in workflow.py and the pump silently mis-branches, so the skill that
    tells it to key off the old name must move in lockstep. This is the exact drift #116 asked
    about: the hardcoded list here forces a code rename to drag both the test and the skill along."""
    text = _skill_text()
    src = _workflow_src()
    for key in (
        "review_needed", "review_kind",          # #117 — independent review of every change
        "starving", "waiting", "waiting_count",  # #102 — starving-tail signal
        "needs_retriage",                        # #102 — a chain head returned to Backlog
        "cycle", "cycle_tasks",                  # #105 — predecessor-cycle signal
        "resume",                                # active-task vs free-queue discriminator
    ):
        assert key in src, f"signal {key!r} is no longer produced by workflow.py"
        assert key in text, f"signal {key!r} is no longer documented in SKILL.md"


def test_comment_markers_the_skill_cites_are_still_emitted():
    """Grep-convention markers the skill points humans/agents at must still be the ones the code
    writes. Curated to the markers the skill shows in bracket form; the others the code emits
    ([claim]/[worklog]/[blocked]/[decompose]/[нужен человек]) the skill doesn't cite verbatim, so
    they are out of this contract by design (add one here only once the skill starts citing it)."""
    text = _skill_text()
    src = _workflow_src()
    for marker in ("[review]", "[spec]", "[filed-by-agent]"):
        assert marker in src, f"marker {marker!r} is no longer emitted by workflow.py"
        assert marker in text, f"marker {marker!r} is no longer cited in SKILL.md"


def test_attachment_upload_rule_names_the_tool_that_backs_it():
    """#137: the rulebook's 'attach a screenshot of visually-verifiable work' rule must name the
    tool that performs it, and that tool must still exist in workflow.py — so renaming attach_file
    drags the skill along (the same skill<->code net as the signal keys). The behaviour rule is
    worthless if it points at a tool the code no longer exposes."""
    assert "attach_file" in _workflow_src(), "workflow.py no longer defines attach_file"
    assert "attach_file" in _skill_text(), "SKILL.md no longer names the attach_file tool"


def test_the_parallel_drain_rules_cite_real_signals():
    """The parallel drain (wip_limit > 1) is the first feature where the rulebook tells the pump to
    BRANCH on a payload key AND to shell out to a CLI — and the rulebook reaches every consumer by
    itself (see this module's docstring), with no per-consumer pin and no review gate. So pin both
    directions of the three tokens the whole mode hangs off: named in SKILL.md, and still real in
    the code. `wip_saturated` is the "wait, don't idle" discriminator, `exclude` the
    caller-maintained liveness set next_task cannot infer, `wip_limit` the config key that turns
    the mode on at all.

    Round-1 review: the code-side anchors must be RENAME-SENSITIVE, or this pin is theatre. A bare
    `"exclude" in workflow_src` is satisfied by an unrelated comment ("parenttask is deliberately
    excluded"), and a bare `"wip_limit" in workflow_src` is satisfied by the method name
    `_effective_wip_limit` — while the thing SKILL.md actually cites, the repo-toml KEY, lives in
    config.py, which this test never read. Both would have stayed green through the very rename
    they claim to catch. Anchor each on the exact construct whose name the rulebook depends on:
    next_task's parameter, and config.py's lookup of the toml key.

    Round-2 review: `exclude` is anchored in BOTH modules. The pump does not call
    Workflow.next_task — it calls the MCP TOOL (server.py), so a rename there alone would leave a
    workflow-only pin green while every agent's `exclude=[…]` silently became an unknown kwarg."""
    text = _skill_text()
    src = _workflow_src()
    config_src = inspect.getsource(config)
    server_src = inspect.getsource(server)
    for token in ("wip_saturated", "exclude", "wip_limit"):
        assert token in text, f"{token!r} is not documented in SKILL.md"
    assert "wip_saturated" in src, "SKILL.md keys off wip_saturated but workflow.py stopped emitting it"
    assert "exclude: list[int]" in src, \
        "SKILL.md tells the pump to pass exclude=… but Workflow.next_task lost that parameter"
    assert "exclude: list[int]" in server_src, \
        "SKILL.md tells the pump to pass exclude=… but the next_task TOOL lost that parameter"
    assert 'repo.get("wip_limit")' in config_src, \
        "SKILL.md names wip_limit as the repo-config key but config.py no longer reads that key"


def test_the_integration_recipe_pushes_to_the_main_branch_and_names_gc():
    """Under the parallel drain a per-task agent sits in its own worktree on a THROWAWAY task/<id>
    branch, so a bare `git push` pushes that branch and leaves the main branch — and therefore the
    release pipeline — without the work, while every tool still reports success. The explicit
    refspec is the whole point of the integration recipe, so pin it verbatim. `workspace --gc` is
    pinned for the mirror-image reason: nothing else reaps a tree whose work has LEFT the board —
    a task that reached Review/Done or went back to Backlog/Your Call, a card that left Review —
    so without it those trees accumulate forever. (Round-2: NOT "crashed agents' trees", the
    inversion this docstring used to state. A crashed agent's task stays in Design/Build assigned
    to it, so liveness deliberately SPARES that tree — it is what the resume agent comes back to.)
    The orchestrator's tick in this rulebook is the only place that rule can live."""
    text = _skill_text()
    assert "git push origin HEAD:main" in text, "the explicit push-to-main refspec vanished"
    assert "workspace --gc" in text, "the tick no longer reaps dead worktrees (workspace --gc)"


def test_empty_queue_wakeup_interval_is_pinned():
    """The idle-loop wakeup interval is a hand-set human decision (#80: 20→10 min = 600s) with no
    code counterpart to anchor it — it lives only in the rulebook. Pin the value so an unrelated
    skill edit can't silently revert it; a deliberate change updates this one line on purpose."""
    assert "600" in _skill_text(), "the empty-queue ScheduleWakeup interval (600s, #80) vanished"
