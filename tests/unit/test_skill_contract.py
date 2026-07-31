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
stale on either side. One test below also reads the repo's own CLAUDE.md, because the integration
retry ceiling is DERIVED in both files and two independent copies of one derivation are exactly
what drifts apart.
"""
import ast
import inspect
import re
import subprocess
import textwrap
from importlib.resources import files
from pathlib import Path

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import config, server, setup_cmd, workflow, workspace_cmd


def _skill_text() -> str:
    # the packaged copy that actually ships in the wheel and self-heals onto consumers (#88)
    return files("vikunja_mcp").joinpath("skills/tracker/SKILL.md").read_text(encoding="utf-8")


def _workflow_src() -> str:
    return inspect.getsource(workflow)


SKILL_SOURCE_PATH = "src/vikunja_mcp/skills/tracker/SKILL.md"   # the copy the rulebook cites


def _calls_in(func) -> set[str]:
    """The plain names a function actually CALLS — parsed, not grepped.

    A substring pin cannot carry the premise below, and this is MEASURED, not feared: `main`
    names `_self_heal_installed_artifacts()` in an explanatory COMMENT as well as calling it, so
    deleting the call left `"_self_heal_installed_artifacts()" in getsource(main)` green; and the
    heal's own body IMPORTS `sync_installed_artifacts` on the line above the call, so replacing
    the call left that assertion green too. Both mutations are the drift the pin exists to catch,
    and both walked straight through it. An AST call-set survives neither."""
    tree = ast.parse(textwrap.dedent(inspect.getsource(func)))
    return {
        node.func.id
        for node in ast.walk(tree)
        if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
    }


def _freshness_section(text: str) -> str:
    """The section that tells an agent WHICH copy of this rulebook is authoritative.

    Scoped to its own section, like `_gc_section`: `install-skill` and the sync's opt-out env are
    named in the MANAGED header too, so a whole-file substring could not tell "the rule is still
    stated" from "the header still mentions the sync"."""
    start = text.find("\n## Какую копию этих правил ты читаешь\n")
    assert start != -1, "SKILL.md no longer states which copy of itself is authoritative"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the freshness section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the freshness slice is not a proper subset of SKILL.md"
    return section


def test_the_rulebook_says_which_copy_of_itself_is_authoritative():
    """VMCP-96 (552): the self-heal this whole module rests on fires ONCE — from `server.main`, at
    server start — and a session's server starts once. So the installed copy every agent reads is a
    SNAPSHOT taken before the session's first landing, while the repo copy moves with each one. On
    2026-07-30 eight tasks edited SKILL.md inside one session and every agent dispatched during it
    read the pre-session text; nothing broke only because the orchestrator's briefs happened to
    carry the current rules. The sharp case is not staleness but a wrong CONCLUSION: a task whose
    deliverable IS a SKILL.md edit cannot verify itself through the skill — it gets the old text
    back and reads "my edit did not take", correctly for what it can see and wrongly in fact.

    The fix is a rule, not a code path (a mid-session rewrite of `~/.claude` would be a write whose
    effect the running session cannot confirm; a per-call sync was rejected outright — filesystem
    writes on a stdio server's hot path for a problem that only bites during self-modification).
    So what needs a net is the rule's PREMISE and its REDIRECT TARGET, both of which are code facts
    that can drift out from under prose that self-heals onto every consumer with no review gate:

    1. Premise — the refresh is a server-START event. Move the sync anywhere else (per tool call, a
       timer) and the section's "this text does not move inside a session" becomes a lie shipped to
       everyone. Anchored on the CALL GRAPH (`_calls_in`, which see): the obvious substring form of
       this pin was written first and measured green through both mutations it claims to catch.
    2. Redirect target — the rule sends an agent to a PATH. If the skill source moves in the tree,
       an agent following the rule finds nothing and silently falls back to the stale copy it was
       told not to trust. Anchored on the file existing AND being byte-identical to the packaged
       rulebook, so the pin fails on a move and on a copy that stopped being the source.

    Deliberately NOT pinned: the wording of the self-verification and rollout bullets — prose is
    review's job (see this module's docstring); the slice above only holds the section itself open.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test): control PASS; drop the heal call from `server.main` -> FAIL; make the heal call
    something other than `sync_installed_artifacts` -> FAIL; re-value `SKILL_SYNC_OPT_OUT_ENV` ->
    FAIL; delete the cited path from the section -> FAIL; rename the section heading so the slice
    cannot find it -> FAIL (loudly, with its own message). The first two rounds are why `_calls_in`
    exists: under the substring pin they came back GREEN."""
    text = _skill_text()
    section = _freshness_section(text)

    # 1. the premise: the installed copy really is refreshed at server START, once
    assert "_self_heal_installed_artifacts" in _calls_in(server.main), \
        "SKILL.md says the installed copy is refreshed at server start, but main no longer heals"
    assert "sync_installed_artifacts" in _calls_in(server._self_heal_installed_artifacts), \
        "the server's start-time heal no longer calls sync_installed_artifacts"
    assert setup_cmd.SKILL_SYNC_OPT_OUT_ENV in text, \
        "SKILL.md names a sync opt-out env var that setup_cmd no longer defines under that name"

    # 2. the redirect target: the path the rule sends agents to IS the packaged rulebook
    assert SKILL_SOURCE_PATH in section, \
        "the rule no longer names the in-repo source copy it redirects agents to"
    source = Path(__file__).resolve().parents[2] / SKILL_SOURCE_PATH
    assert source.is_file(), f"the rulebook redirects agents to {SKILL_SOURCE_PATH}, which is gone"
    assert source.read_text(encoding="utf-8") == text, \
        f"{SKILL_SOURCE_PATH} is no longer the source the packaged rulebook is built from"


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


def _exclude_completeness_bullet(text: str) -> str:
    """The bullet that tells the pump what an INCOMPLETE `exclude` costs it (#527).

    Sliced to the bullet rather than matched over the whole file for the reason the sibling
    slices exist: `exclude` and `wip_saturated` are named a dozen times in this rulebook, so a
    whole-file substring could not tell "the rule is still stated" from "the words still occur
    somewhere". Deleting this bullet must fail the pin even though every word in it survives
    elsewhere."""
    start = text.find("\n- **Полнота `exclude`")
    assert start != -1, \
        "SKILL.md no longer tells the pump what an incomplete `exclude` costs it (#527)"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the exclude-completeness bullet no longer ends where the next one begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the exclude-completeness slice is not a proper subset"
    return bullet


def test_the_rulebook_says_wip_saturated_needs_a_complete_exclude_and_the_code_agrees():
    """#527: the rule and the code property it describes, pinned together so they cannot drift
    apart in either direction.

    The gap this closes was observed live: the same board in the same minute answers
    wip_saturated:true to a complete `exclude` and a resume at free:0 — with no wip_saturated key
    at all — to an incomplete one, because branch 1 (your active tasks) returns before the slot
    guard. SKILL.md justified `exclude` ONLY as double-dispatch avoidance, so a pump that had
    lost its in-flight set was reading a payload the rulebook did not explain.

    Three things are pinned as INSTRUCTIONS, not as vocabulary — each assertion names the action
    or the claim, so deleting a sentence while leaving its keywords in the bullet still fails:
    (a) saturation is conditional on a complete `exclude`, (b) the imperative for the confusing
    state — check your own set, not the board, (c) the order is deliberate and stays. (c) matters
    most: without it the next reader files this as a bug, and "fixing" it would make
    `vikunja-mcp claimable` — which passes NO exclude — report "no work" on a board holding
    resumable work, silently idling every hub loop that trusts it.

    The behavioural half drives the real Workflow to both outcomes, so a future reordering of
    next_task's branches fails HERE too, not only in some distant hub."""
    bullet = _exclude_completeness_bullet(_skill_text())
    assert "ТОЛЬКО если `exclude` полон" in bullet, \
        "SKILL.md no longer says wip_saturated requires a COMPLETE exclude"
    assert "проверяй СВОЙ `exclude`, а не доску" in bullet, \
        "SKILL.md no longer tells the pump where to look when a resume arrives at free:0"
    assert "порядок ветвей НЕ трогаем" in bullet, \
        "SKILL.md no longer says the branch order is deliberate — the next reader will 'fix' it"
    assert "vikunja-mcp claimable" in bullet, \
        "SKILL.md no longer names the contract that the branch order protects"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=2)
    held = [api.add_task(t, "Queue") for t in ("first", "second")]
    for task in held:
        wf.claim(task["id"])
    api.add_task("free work nobody can take", "Queue")

    complete = wf.next_task(exclude=[t["id"] for t in held])
    assert complete["task"] is None and complete["wip_saturated"] is True, \
        "a COMPLETE exclude no longer produces the saturation signal the rulebook promises"

    incomplete = wf.next_task(exclude=[held[0]["id"]])
    assert incomplete["task"] is not None and incomplete["resume"] is True
    assert incomplete["wip"]["free"] == 0
    assert "wip_saturated" not in incomplete, \
        "saturation became reachable with an incomplete exclude — SKILL.md's rule is now wrong, " \
        "and `vikunja-mcp claimable` (empty exclude) may no longer report resumable work"


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


def _integration_recipe(text: str) -> str:
    """The FENCED integration recipe — the block an agent copies, not the prose that explains it.

    Scoped to the fence on purpose, and not by a whole-file substring: `git push origin HEAD:main`
    alone appears twice in the rulebook (the fence, plus the parallel-drain bullet that summarises
    it in prose), so a file-wide search cannot tell "the recipe still says it" from "some paragraph
    mentions it" — exactly the weakness `_gc_section`'s docstring records having MEASURED. Exactly
    one such fence must exist: two would mean the recipe was duplicated, which is the drift this
    module exists to catch, not a state to tolerate."""
    blocks = [
        b for b in re.findall(r"```sh\n(.*?)```", text, re.S) if "git push origin HEAD:main" in b
    ]
    assert len(blocks) == 1, f"expected exactly 1 fenced integration recipe, found {len(blocks)}"
    recipe = blocks[0]
    assert 0 < len(recipe) < len(text), "the recipe slice is not a proper subset of SKILL.md"
    return recipe


def test_the_recipe_verifies_the_evidence_sha_actually_landed_on_main():
    """VMCP-77 (526): the recipe used to end at `git push origin HEAD:main` + `git rev-parse HEAD`,
    so "the push landed" rested on the absence of an error message. `rev-parse` cannot carry that
    weight — it (and `rev-parse --verify`) echoes back a full 40-hex sha with exit 0 whether or not
    the object exists — and existence is not ancestry either: a PRE-REBASE sha resolves fine while
    never reaching main, which under the parallel drain is the normal case, not the exotic one.
    Both commands were measured in a throwaway repo before being written into the rulebook.

    Pinned for the same reason the push refspec above is: SKILL.md self-heals onto every consumer
    on server start over the moving `stable` branch, with no per-consumer pin and no review gate,
    so an edit that "simplifies" this back to `rev-parse` ships to everyone silently. There is no
    code-side anchor to pin against — these are shell commands, not workflow symbols — so this is
    the `600`-interval kind of pin: a value that lives only in the rulebook.

    The literals are asserted WITH their quoting, which is load-bearing rather than stylistic:
    under zsh's `extendedglob` a bare `<sha>^{commit}` dies with `no matches found` before git
    runs, and that failure looks exactly like a bad sha. MUTATION-CHECKED (both directions, and
    with `__pycache__` cleared): delete either command line from the fence and this test fails;
    leave the fence alone but delete the surrounding explanation and it stays green — by design,
    prose wording is review's job, the copyable step is this net's."""
    recipe = _integration_recipe(_skill_text())
    assert 'git cat-file -e "<sha>^{commit}"' in recipe, \
        "the recipe no longer proves the evidence sha EXISTS (git cat-file -e)"
    assert 'git merge-base --is-ancestor "<sha>" origin/main' in recipe, \
        "the recipe no longer proves the evidence sha is ON the main branch (merge-base)"


def _tick_step_3(text: str) -> str:
    """The orchestrator tick's step 3 — the step that verifies a returning agent's evidence sha.

    Scoped to its own list item, like `_integration_recipe` and `_gc_section`: `review_task` and
    `call_human` are named all over the rulebook, so a whole-file substring could not tell "step 3
    still prescribes this" from "some other section mentions the tool"."""
    m = re.search(r"\n  3\. Агент вернулся с итогом(.*?)\n  4\. ", text, re.S)
    assert m, "the orchestrator tick's step 3 is no longer where this pin can find it"
    return m.group(1)


def test_the_evidence_mismatch_escalation_is_one_the_orchestrator_can_execute():
    """VMCP-77 (526), rework — the pin the FIRST pass of this card needed and did not have.

    That pass told the orchestrator to call `call_human` when a returning agent's evidence sha
    fails verification. At that moment the card is in Review (the returning agent's contract is
    that it already called `advance(to='review')`), and `call_human` is gated to ACTIVE_STAGES =
    Design/Build — so the escalation for the failure branch the card itself created could not run.
    A rule naming behaviour the tools do not have is exactly what this module exists to catch, and
    it went out anyway because the advice was WRITTEN rather than RUN.

    So this pin does not compare strings alone: it drives the real `Workflow` through the state
    step 3 is actually in and executes the prescribed escalation end to end — the refusal that
    motivates the rule, the `review_task` bounce that replaces it, the pump picking the card back
    up, and the fallback that only becomes legal once the card is in Build. Change either gate and
    the rulebook's clause stops being executable; this goes red before it ships to every consumer.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test): control PASS; add "Review" to ACTIVE_STAGES -> FAIL; make review_task move the card
    anywhere but Build -> FAIL; drop `review_task`/`needs_work` from step 3 -> FAIL; revert the
    clause to the `call_human` wording this card was returned for -> FAIL; rename step 3's opening
    words so the slice cannot find it -> FAIL (loudly, with its own message, never silently green).

    Deliberately NOT pinned: the wording of the reasoning around the clause, and the two rules no
    gate can carry (never `verdict='approve'` from the pump; the bounced card re-occupies a WIP
    slot). `review_task(approve)` from here executes fine — it is forbidden by the rulebook, not
    by the code, and pinning prose is review's job, per this module's docstring."""
    step3 = _tick_step_3(_skill_text())
    assert "review_task(" in step3 and "needs_work" in step3, \
        "tick step 3 no longer names the escalation that works from Review (review_task/needs_work)"
    assert hasattr(workflow.Workflow, "review_task"), "the escalation names a tool that is gone"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    task_id = api.add_task("its evidence sha never landed", "Queue")["id"]
    wf.claim(task_id)
    wf.advance(task_id, to="build", spec="…")
    wf.advance(task_id, to="review", worklog="…", evidence="0" * 40)
    assert api.stage_of(task_id) == "Review", "step 3 sees a card in Review — precondition"

    # 1. why the rule cannot say `call_human` here
    with pytest.raises(workflow.WorkflowError, match="Design/Build"):
        wf.call_human(task_id, "the reported evidence sha is not an ancestor of origin/main")

    # 2. what it says instead — and it moves the card somewhere an agent can work again
    bounced = wf.review_task(
        task_id, verdict="needs_work",
        report="evidence sha not on origin/main (merge-base --is-ancestor -> 1); not reviewed",
    )
    assert bounced["moved_to"] == "Build" and api.stage_of(task_id) == "Build"
    assert workflow.LABEL_REVIEW_FAILED in [
        label["title"] for label in api.get_task(task_id).get("labels") or []
    ]

    # 3. the pump gets it back on its own — no human needed to un-strand it
    nxt = wf.next_task()
    assert nxt["resume"] is True and nxt["stage"] == "Build"
    assert nxt["task"]["id"] == task_id

    # 4. and only NOW is the human channel open, for the agent that cannot re-push
    assert wf.call_human(task_id, "cannot re-push")["moved_to"] == "Your Call"


def _gc_section(text: str) -> str:
    """Just the `--gc` step of the orchestrator's tick, sliced out of the rulebook.

    ROUND-2 REVIEW, Minor: the assertions below used to be `code in text` — a WHOLE-FILE substring
    — and the rulebook explains these codes in TWO places, the `--gc` report and the `--release`
    recipe. MEASURED: delete the `dirty`/`unpushed` explanation out of the gc section entirely and
    the pin stayed green, because the release recipe's own prose still contains both words.
    Re-valuing a constant did fail, as claimed, but a pin that catches a rename and not a deletion
    guards the cheaper half of the risk. Scope it to the section that has to do the explaining.

    Both anchors are asserted rather than assumed: a slice that silently becomes empty would turn
    every assertion below red (loud, fine), but a slice that silently WIDENS to the whole file
    would restore exactly the weakness this exists to remove — so the width is checked too.
    """
    start = text.find("  1. `vikunja-mcp workspace --gc`")
    assert start != -1, "the orchestrator's tick no longer opens with the `workspace --gc` step"
    end = text.find("\n  2. ", start)
    assert end != -1, "the `--gc` step no longer ends where step 2 of the tick begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the --gc slice is not a proper subset of SKILL.md"
    assert "workspace --release <id>" not in section, \
        "the slice swallowed the --release recipe — the very prose it exists to exclude"
    return section


def test_the_gc_report_split_the_skill_teaches_is_the_one_the_code_produces():
    """VMCP-68: `--gc` reports its refusals in TWO lists — `kept` ("a human should look") and
    `expected` ("routine, no action") — and the rulebook is what tells the pump which one to read.
    That makes the list name and every `code` it cites part of the same auto-propagating contract as
    the signal keys above: rename or re-value one in workspace_cmd.py and every agent keeps reading
    a list, or matching a code, that no longer exists — silently, since a missing key just reads as
    "nothing to look at".

    Anchored on the CONSTANTS rather than on literals repeated here, so a changed VALUE fails until
    the rulebook is updated with it — and scoped to the `--gc` section (see `_gc_section`), so
    DELETING an explanation fails too instead of coasting on the `--release` recipe's prose.
    `expected` is anchored inside gc_workspaces' own source, not the module's: the module-level
    word appears in comments and helper names, so a bare module-wide substring would stay green
    through the very rename it claims to catch."""
    text = _skill_text()
    section = _gc_section(text)
    gc_src = inspect.getsource(workspace_cmd.gc_workspaces)
    assert '"expected": expected' in gc_src, \
        "SKILL.md tells the pump to read `expected` but gc_workspaces stopped returning that list"
    assert "`expected`" in section, "SKILL.md's --gc rule no longer names the `expected` list"
    for code in (
        workspace_cmd.CODE_DIRTY,             # kept, or expected while the card is parked
        workspace_cmd.CODE_UNPUSHED,          # the Your Call state that made `kept` never-empty
        workspace_cmd.CODE_UNREACHABLE_HEAD,  # routine in a REVIEW tree, an alarm in a build one
        workspace_cmd.CODE_DETACHED_BUILD,    # VMCP-86: a build tree off its own task/<id> branch
        workspace_cmd.CODE_HALF_CREATED,      # never expected: only a human can clear it
        workspace_cmd.CODE_SELF_TREE,
        workspace_cmd.CODE_RELEASE_ERROR,
    ):
        assert code in section, \
            f"refusal code {code!r} is no longer explained in SKILL.md's --gc report rule"
    assert workspace_cmd.CODE_NO_WORKTREE in text, \
        "the --release recipe no longer explains the no-worktree refusal"


def test_the_released_entrys_branch_leak_is_documented_where_agents_will_read_it():
    """VMCP-… (542), the hole VMCP-68's own reading rule opened. #517 made the one failure mode of
    a SUCCESSFUL release report itself honestly: `worktree remove` succeeded but `git branch -D`
    did not, so the entry is `released: true` PLUS `branch_deleted: false` and a `warning` naming
    the leaked branch. That entry therefore rides in `released` — the list VMCP-68's rule called
    the one nobody needs to read — so a rule of "read `kept`, skip the rest" hides it and
    `task/<id>` branches accumulate with nothing to notice.

    Pinned on both sides for the usual reason (the rulebook self-heals onto every consumer with no
    review gate): drop the keys in the code and the rulebook still teaches them; drop the prose and
    the pump goes back to skipping the list they arrive in."""
    text = _skill_text()
    release_src = inspect.getsource(workspace_cmd._release_locked)
    for key in ("branch_deleted", "warning"):
        assert f'result["{key}"]' in release_src, \
            f"_release_locked no longer reports {key!r} when the branch delete fails"
        assert key in text, f"SKILL.md no longer tells agents about the {key!r} key"
    assert "branch_deleted" in _gc_section(text), \
        "the --gc reading rule stopped covering `branch_deleted` — a leaked branch is invisible"


def _two_returns_rule(text: str) -> str:
    """The «Два возврата, два дерева» bullet — the rule that splits the two ways a task
    comes back to an agent.

    Sliced to its own top-level bullet, like `_gc_section` / `_tick_step_3`: `created`,
    `--release` and `task/<id>` are named all over the parallel-drain section, so a whole-file
    substring could not tell "the split is still stated" from "the words survive somewhere"."""
    start = text.find("- **Два возврата, два дерева.**")
    assert start != -1, "SKILL.md no longer splits the two ways a task comes back to an agent"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the two-returns rule no longer ends where the next top-level bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the two-returns slice is not a proper subset of SKILL.md"
    assert "Ревью слот не занимает" not in section, "the slice swallowed the following bullet"
    return section


def _git(cwd, *args) -> str:
    return subprocess.run(
        ["git", *args], cwd=cwd, check=True, capture_output=True, text=True
    ).stdout.strip()


@pytest.fixture
def git_repo(tmp_path, monkeypatch):
    """A clone on `main` with a local bare origin it has already pushed to — enough to run
    both return paths for real, with no network.

    A local copy rather than an import of `test_workspace_cmd`'s `repo` fixture: this module
    proves the RULEBOOK against the code, and a pin that goes red when an unrelated test module
    reshuffles its fixtures is a pin nobody trusts. `ENV_WORKTREE_ROOT` is cleared for that
    module's own measured reason — the pump exports it machine-wide, so an agent running this
    suite inside its own worktree would otherwise steer these trees at the AMBIENT root."""
    monkeypatch.delenv(config.ENV_WORKTREE_ROOT, raising=False)
    workspace_cmd._main_worktree.cache_clear()
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


def test_the_two_ways_a_task_comes_back_hand_back_the_trees_the_rulebook_promises(git_repo):
    """VMCP-82 (532): the rulebook used to describe ONE way a task comes back — "you return to
    the same worktree with your unfinished work" — while the code has two, so a rework agent
    could hunt for uncommitted work that was never there.

    * CRASH: nothing was released, so `_ensure_locked`'s early-return hands the same tree back
      (`created: false`) with everything in it, committed and not.
    * BOUNCE after review: the predecessor pushed and called `--release`, which removed the tree
      AND deleted `task/<id>`. There is nothing to reattach to, so a fresh tree is cut from the
      CURRENT `origin/main` — several commits ahead of the original base, clean, and already
      carrying the predecessor's work because a bounce can only follow a successful push.

    Pinned on BEHAVIOUR, not on prose, because the failure this card fixes is a rulebook that
    states one thing while the code does another — and only running the code can tell those
    apart. So both paths are executed against real git, and the rulebook's two payload tokens
    plus its two-command check ride along: change either side alone and this goes red.

    The load-bearing assertion is the sibling commit's presence in the reworked tree. It is what
    distinguishes "cut fresh from current main" from "reattached to a surviving branch" —
    `created: true` alone cannot, since a tree whose DIRECTORY was removed by hand takes the
    same value while reattaching to its old branch.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test, SKILL.md and workspace_cmd.py restored from copies kept aside — never `git checkout
    --`): control PASS; delete the rule from SKILL.md -> FAIL on the slice; drop `created: false`
    out of the rule -> FAIL; make `_release_locked` keep the branch (the change this card
    explicitly does NOT propose) -> FAIL on the leftover `task/22`, and again on the missing
    sibling commit when that first assertion is neutralised, so both halves of the bounce claim
    are load-bearing rather than one covering for the other."""
    rule = _two_returns_rule(_skill_text())
    repo = git_repo

    # --- return 1: the agent crashed. Nothing was released, so the tree comes back as it was.
    tree = Path(workspace_cmd.ensure_workspace(11, cwd=repo)["path"])
    (tree / "half.txt").write_text("committed, unfinished\n")
    _git(tree, "add", "half.txt")
    _git(tree, "commit", "-m", "wip")
    (tree / "scratch.txt").write_text("never committed\n")

    resumed = workspace_cmd.ensure_workspace(11, cwd=repo)
    assert resumed["created"] is False and Path(resumed["path"]) == tree, \
        "the crash path no longer hands back the SAME tree — SKILL.md promises `created: false`"
    assert (tree / "scratch.txt").exists(), "the resumed tree lost its uncommitted work"
    assert _git(tree, "log", "--oneline", "origin/main..HEAD"), \
        "the resumed tree lost the unfinished commits the rule tells the agent to expect"

    # --- return 2: the agent pushed, released its tree, and a reviewer bounced the card.
    done = Path(workspace_cmd.ensure_workspace(22, cwd=repo)["path"])
    (done / "shipped.txt").write_text("landed\n")
    _git(done, "add", "shipped.txt")
    _git(done, "commit", "-m", "done")
    _git(done, "push", "origin", "HEAD:main")
    shipped = _git(done, "rev-parse", "HEAD")
    assert workspace_cmd.release_workspace(22, cwd=repo)["released"] is True
    assert _git(repo, "branch", "--list", "task/22") == "", \
        "--release no longer deletes task/<id> — the bounce would reattach, not cut fresh"

    # a sibling lands on main while the card waits in Review
    _git(repo, "fetch", "origin")
    _git(repo, "merge", "--ff-only", "origin/main")
    (repo / "sibling.txt").write_text("someone else's task\n")
    _git(repo, "add", "sibling.txt")
    _git(repo, "commit", "-m", "sibling")
    _git(repo, "push", "origin", "main")

    rework = workspace_cmd.ensure_workspace(22, cwd=repo)
    fresh = Path(rework["path"])
    assert rework["created"] is True, "the bounced task no longer gets a freshly created tree"
    assert _git(fresh, "status", "--porcelain") == "", \
        "SKILL.md tells the rework agent there is no uncommitted work here — there now is"
    assert _git(fresh, "log", "--oneline", "origin/main..HEAD") == "", \
        "SKILL.md tells the rework agent nothing unpushed is here — there now is"
    assert (fresh / "sibling.txt").exists(), \
        "the reworked tree is NOT cut from the current origin/main (the sibling commit is absent)"
    assert (fresh / "shipped.txt").exists(), \
        "the predecessor's own work is missing — the rule says the bounce follows a landed push"
    landed = subprocess.run(["git", "merge-base", "--is-ancestor", shipped, "origin/main"],
                            cwd=fresh, capture_output=True)
    assert landed.returncode == 0, \
        "the predecessor's pushed commit is not on the main branch the fresh tree was cut from"

    # and the rulebook says both outcomes in the payload's own vocabulary, plus the two-command
    # check that answers "is there unfinished work here?" without the agent having to guess
    assert "`created: false`" in rule, "the rule no longer names the crash path's `created: false`"
    assert "`created: true`" in rule, "the rule no longer names the bounce path's `created: true`"
    assert "git status --porcelain" in rule and \
        "git log --oneline origin/<главная ветка>..HEAD" in _flat(rule), \
        "the rule lost the two commands that settle it without guessing"


def test_empty_queue_wakeup_interval_is_pinned():
    """The idle-loop wakeup interval is a hand-set human decision (#80: 20→10 min = 600s) with no
    code counterpart to anchor it — it lives only in the rulebook. Pin the value so an unrelated
    skill edit can't silently revert it; a deliberate change updates this one line on purpose."""
    assert "600" in _skill_text(), "the empty-queue ScheduleWakeup interval (600s, #80) vanished"


def _flat(text: str) -> str:
    """SKILL.md with every run of whitespace collapsed to one space — for pinning PROSE phrases.

    A markdown paragraph's line breaks are cosmetic: re-wrapping one is a meaning-preserving edit
    that must not turn a pin red, and the wrap can fall anywhere inside the phrase being pinned
    (the parallel-drain sentence below already breaks mid-clause). Fenced recipes are matched RAW
    instead (see `_integration_recipe`) — inside a fence a line break separates two commands, so
    flattening one would let a pin match text that is no longer a runnable step."""
    return re.sub(r"\s+", " ", text)


def test_the_integration_retry_ceiling_is_pinned():
    """VMCP-81, generalised by VMCP-94 (550): how many `fetch → rebase → re-verify → push` rounds a
    per-task agent runs before escalating via `call_human` is — like the wakeup interval above — a
    hand-set human number with NO code counterpart: nothing in workflow.py counts rounds, so this
    test is the only thing that can hold it. And it is DERIVED, not preferred. CI's auto-release
    pushes a `chore: vX.Y.Z [skip ci]` bump after every green landing (measured 2026-07-30 on this
    repo's first live parallel drain: 17 of the 46 commits that reached main that day were the
    bot's, arriving 37 s–2 m 55 s behind the task commit, median 1 m 41 s), so a losing push is the
    EXPECTED outcome, not an edge case — but that racer is BOUNDED: `[skip ci]` + GITHUB_TOKEN means
    it never triggers itself, so it never pushes twice in a row and costs at most one round on its
    own. The ceiling must exceed the worst purely MECHANICAL run, which at `wip_limit = N` is 2·(N−1)
    sibling+bump losses plus the trailing bump of the landing that beat you to the `fetch`, i.e.
    2·(N−1)+1 — so the ceiling is **2 × N**, the smallest value strictly above it. 6 is only that
    formula's N=3 instance (the default limit, which is this repo's own case), not the rule.

    Why a formula and not "6, plus advice to raise it": the rulebook self-heals onto every consumer
    over the moving `stable` branch, OVERWRITING local edits, with no per-consumer pin and no review
    gate (see this module's docstring) — and there is no config key for a retry ceiling. So "raise
    it if your limit is wider" is unactionable by construction: a consumer at `wip_limit = 4` (worst
    mechanical run 7) cannot edit a pinned 6, it can only be shipped a rule that COMPUTES. The
    variable therefore has to be one the agent already receives: `wip.limit`, from `next_task`'s
    `wip` payload, which `with_wip` attaches to EVERY branch of the result — which is why the
    rulebook can tell the orchestrator to carry the limit in its dispatch brief at all. That payload
    is the one part of this rule that DOES have a code counterpart, so it is pinned on both sides:
    rename the key or its `limit` field and this goes red, instead of leaving every agent computing
    a ceiling from a field that no longer arrives.

    The likely bad edit has moved. It used to be the walk-back to 3 (exactly the length of the
    commonest bad run, bump(A) → commit(B) → bump(B), so it reads as a sane-looking number to anyone
    re-tidying this prose without the derivation in hand, while calling a human onto pure arithmetic
    at the moment the next round would almost certainly have won). Now it is RE-COLLAPSING the
    formula into a constant — "it is always 6 here anyway" — which stays silently correct on this
    repo, at the default limit, and is silently wrong everywhere the drain is wider: at 4 a pinned 6
    escalates to a human on arithmetic alone. The three positive site pins below are all spelled
    `2 × wip.limit`, so a re-collapse cannot pass them.

    Pinned in all three places that carry the RULE, not once against the whole file (see
    `_gc_section` on why a whole-file substring is the weak form of this): the parallel-drain
    paragraph, the shell recipe's round count (scoped to the fence, so a deletion cannot coast on
    the prose that summarises it), and the escalation sentence that spends the ceiling on
    `call_human`. Deleting any ONE of the three then fails instead of coasting on the others — a
    recipe with no escalation sentence, or an escalation with no round count, is exactly the
    half-stated rule an agent would fill in with its own guess.

    Three further halves are pinned because without any one of them the rule stops being EXECUTABLE,
    which is this module's actual subject:
      * the DIAGNOSIS (`git log --oneline HEAD..origin/main`) and the `call_human` verdict it feeds.
        A round is owed only for a race that was LOST; an empty range means there was no race at all
        (protected branch, no push rights, pre-receive hook, wrong remote), where every further
        round re-loses identically at the cost of a full criteria run. The COUNT cannot tell those
        apart.
      * what the escalation SAYS once the ceiling is spent: the question carries the LIST of what
        won each round («N кругов подряд, вот что …»), not the count. This is pinned because the
        sentence it replaced — "hitting 6 means the loop is NOT converging" — is short, confident,
        and now FALSE above the default limit (under a wider drain, or with humans also pushing,
        pure mechanics reaches the ceiling), which makes it exactly what a tidying editor restores.
        With it back, an agent at `wip_limit >= 4` tells a human "the loop is broken" about pure
        arithmetic, and hands over a (wrong) diagnosis instead of the evidence to make one. The `N`
        is also the half the old spelling («шесть кругов подряд») cannot satisfy, so a PARTIAL
        revert — new ceiling, old escalation — fails here rather than shipping.
      * the brief-less FALLBACK to 6. `2 × wip.limit` with no limit in hand is an unfillable
        variable; drop the fallback and an agent dispatched by a pump that did not name the limit
        has no ceiling at all. Its mirror — the dispatch brief being told to carry the limit — is
        pinned too: lose that and every agent silently falls back to the default forever, i.e. the
        generalisation ships dead. VMCP-102 (559) put a READ of the repo toml in front of that
        constant, so the pin below is now on the constant alone (`— **бери 6**`) rather than on the
        whole sentence around it; what the read must say is pinned by
        `test_the_brief_less_ceiling_reads_the_repo_toml_before_it_falls_back`, which also
        re-derives the 6 instead of matching it.

    The negative half stays exactly as it was: the EXACT old 3-spellings a revert brings back. A
    bare `"3" not in text` would be vacuous (`wip_limit` defaults to 3, the measurements quote
    3 min) and would forbid the derivation prose that has to name the number it replaced — and for
    the same reason there is deliberately NO blanket `"6" not in text`: 6 is still legitimately the
    default instance and the fallback.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, SKILL.md restored to a clean `git diff` after): control PASS; re-collapse each of the
    three `2 × wip.limit` sites to a bare 6 -> FAIL, one at a time, each on its own message; delete
    the diagnosis command line from the fence -> FAIL; delete the fence's empty-range `call_human`
    branch -> FAIL; revert the escalation to the count-only «шесть кругов подряд» spelling -> FAIL;
    delete the `бери 6` fallback -> FAIL; drop `wip.limit` out of the dispatch brief -> FAIL; rename
    the `wip` payload key in workflow.py -> FAIL. (559 re-ran the two rounds its edits touch: delete
    the reworded fallback constant -> FAIL; delete the fence's empty-range `call_human` branch,
    which now sits one step further down the diagnosis -> FAIL.)"""
    text = _skill_text()
    flat = _flat(text)
    recipe = _integration_recipe(text)
    src = _workflow_src()

    # the three sites that carry the ceiling itself
    assert "ещё круг, до `2 × wip.limit`)" in flat, \
        "the parallel-drain rule no longer states the `2 × wip.limit` integration retry ceiling"
    assert "до 2 × wip.limit кругов" in recipe, \
        "the integration recipe's push step no longer states the `2 × wip.limit` retry ceiling"
    assert "круги кончились (`2 × wip.limit`, см. «Откуда потолок»)" in flat, \
        "the escalation sentence no longer spends the `2 × wip.limit` rounds before call_human"

    # the diagnosis: a round is owed only for a race that was LOST, and the count cannot say
    assert "git log --oneline HEAD..origin/main" in recipe, \
        "the recipe no longer diagnoses WHO won the race before spending a round"
    assert "call_human" in recipe, \
        "the recipe no longer escalates straight away when the range is empty (no race at all)"
    assert "«N кругов подряд, вот что" in flat, \
        "the escalation asks with a COUNT again — the human needs the LIST of what won each round"

    # the variable the formula reads, and the fallback for when the brief does not carry it
    assert "`wip.limit` из ответа `next_task`" in flat, \
        "the dispatch brief no longer carries wip.limit — every agent falls back to the default"
    assert "— **бери 6**" in flat, \
        "the brief-less fallback is gone — `2 × wip.limit` is then an unfillable variable"
    assert 'result["wip"] = wip' in src, \
        "SKILL.md computes the ceiling from next_task's `wip`, but with_wip stopped attaching it"
    assert '"limit": limit' in src, \
        "SKILL.md computes the ceiling from `wip.limit`, but the payload lost its `limit` field"

    for old in ("ещё круг, до 3)", "до 3 кругов", "отбило 3 раза подряд"):
        assert old not in text, \
            f"the reverted 3-round ceiling is back in SKILL.md ({old!r}) — see this test's docstring"


def _claude_md_text() -> str:
    """The repo's own CLAUDE.md — the SECOND, independent copy of the retry-ceiling derivation.

    Read off the working tree via `parents[2]`, the same way the freshness pin above reaches
    `SKILL_SOURCE_PATH`, and NOT through `importlib.resources`: CLAUDE.md is not packaged, it is a
    repo file, so its absence means the checkout is not what this suite assumes rather than a
    packaging change — asserted, so that case says so instead of surfacing as an OSError."""
    path = Path(__file__).resolve().parents[2] / "CLAUDE.md"
    assert path.is_file(), "CLAUDE.md is gone from the repo root — this pin has nothing to read"
    return path.read_text(encoding="utf-8")


def _claude_ceiling_paragraph(text: str) -> str:
    """CLAUDE.md's one paragraph that derives the integration retry ceiling.

    Sliced to that paragraph like `_gc_section` / `_drain_width_section` slice SKILL.md, and for
    the same measured reason: `wip_limit`, the default 3 and the words `2 ×` all appear elsewhere
    in this file (the config bullet states the default and its precedence, the dogfood section
    states this repo's own limit), so a whole-file scan could not tell "the derivation is still
    stated" from "those numbers survive somewhere". Width is asserted, not assumed — a slice that
    silently widened to the whole file would restore exactly the weakness it exists to remove."""
    start = text.find("**That bump commit is also a racer")
    assert start != -1, (
        "CLAUDE.md no longer opens its retry-ceiling paragraph where this pin can find it. If the "
        "paragraph was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n\n", start)
    assert end != -1, "the ceiling paragraph no longer ends where the next paragraph begins"
    paragraph = text[start:end]
    assert 0 < len(paragraph) < len(text), "the ceiling slice is not a proper subset of CLAUDE.md"
    assert "ci-skip marker" not in paragraph, \
        "the slice swallowed the following paragraph — the prose it exists to exclude"
    return paragraph


def _ceiling_derivation_section(text: str) -> str:
    """SKILL.md's «Откуда потолок» bullet — the rulebook's own copy of the same derivation.

    Scoped to the one bullet, like `_two_returns_rule`: the neighbouring bullets talk about the
    ceiling too (the escalation bullet spends it, the race-diagnosis bullet decides whether a
    round is owed at all), and the brief-less `**бери 6**` puts a bare 6 in a THIRD place, so a
    whole-file number hunt would mix the derivation's numbers with numbers that are not it.

    That third place lives INSIDE this slice, so anything added to the fallback sentence is read by
    the table regexes below. VMCP-102 (559) rewrote it and deliberately kept the `при <n> — <m>`
    shape out of the new prose; a future edit must do the same or move the regexes."""
    start = text.find("- **Откуда потолок и почему он")
    assert start != -1, (
        "SKILL.md no longer opens its «Откуда потолок» derivation where this pin can find it. If "
        "the bullet was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n  - **", start + 1)
    assert end != -1, "the derivation bullet no longer ends where the next bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the derivation slice is not a proper subset of SKILL.md"
    assert "Достиг потолка" not in section, "the slice swallowed the escalation bullet after it"
    return section


def test_the_ceiling_numbers_in_both_files_re_derive_from_their_own_formula():
    """VMCP-98 (556): the test above pins the rulebook's three OPERATIVE `2 × wip.limit` sites as
    STRINGS, which is all a string can do — it never evaluates them. Two gaps follow from that, and
    card 556 was filed for one of them: CLAUDE.md carries a SECOND, independent write-up of the same
    derivation (the release section's "that bump commit is also a racer" paragraph), and nothing in
    this repo reads CLAUDE.md at all — measured while writing this test, `grep -rn "CLAUDE.md"
    tests/ scripts/` returned only prose mentions inside docstrings. So the copy that actually
    DRIFTED had no mechanical net whatever, and the numbers each file states about its own formula
    (5 vs 6 at the default, 2 / 8 / 10 at limits 1 / 4 / 5) had none either.

    What drifted is worth stating precisely, because it is subtle and it will recur: the paragraph
    quoted the WORST MECHANICAL RUN where the CEILING belongs. Those are two different quantities of
    one derivation — at `wip_limit = N` the worst purely mechanical run is 2·(N−1)+1 rounds
    (2·(N−1) sibling+bump losses, plus the trailing bump of the landing that beat you to the
    `fetch`), and the ceiling must sit STRICTLY ABOVE it or it fires on arithmetic, giving 2 × N. At
    the default 3 they read 5 and 6 — adjacent, both plausible, and indistinguishable by eye. That
    is why this pin RE-DERIVES rather than matches: a substring pin on "6" would be satisfied by the
    very confusion the card is about, since 6 is also a correct number elsewhere in the same
    sentence. A number you cannot reproduce from the formula printed next to it must fail.

    So each file is parsed for what it claims about itself and the claims are recomputed in Python:
    every stated (limit → ceiling) pair against `2 × limit`, the stated worst run against
    `2 × (default − 1) + 1`, and the strictly-above step BETWEEN them — the step whose absence was
    the defect. The default the prose reasons about is anchored on `config.DEFAULT_WIP_LIMIT`, the
    one code fact in this rule (tracker #524 made an unset `wip_limit` mean 3, not "no gate"), so
    re-valuing it drags both documents along instead of leaving them quietly describing a default
    the code stopped having. Finally the two tables are compared AS WHOLE MAPPINGS — deliberately
    not "where they overlap", the form review MEASURED useless: the per-file re-derivations already
    force each table to {n: 2n} on its own, so agreeing VALUES at a shared limit are implied, and
    an overlap-scoped comparison therefore cannot fail for any input while missing the divergence
    that IS possible — one file's table narrowing. Dropping `10 at 5` from CLAUDE.md while the
    shipped rulebook kept it passed green under that weaker form. Neither file's internal
    consistency can see the other going its own way; only the whole-mapping comparison can.

    The parsed pair COUNT is asserted before anything loops over it. Without that, a regex that
    stopped matching — a re-wrap, a reworded table — would make "every stated ceiling is correct"
    pass over an EMPTY set, i.e. go green precisely when the prose moved out from under the pin.
    That is the vacuous-pin failure mode this module has measured before (see `_calls_in`), and it
    is the one a numeric pin is most exposed to.

    Deliberately OUT of scope, and NOT pinned here: the brief-less fallback sentence and the
    empty-range race diagnosis. Card 559 (since landed) rewrote both, and a pin laid over prose
    another card is about to rewrite is a merge conflict dressed as a test. That reasoning survives
    the landing on its own merits — the fallback's bare 6 is not a step of THIS derivation but the
    default's instance quoted for an agent whose brief carried no limit, so re-deriving it here
    would assert a different rule. 559 re-derives it in its own test, against
    `2 × config.DEFAULT_WIP_LIMIT`; `test_the_integration_retry_ceiling_is_pinned` above keeps a
    string pin on the constant surviving at all. This test stays on the (limit -> ceiling) table.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, both files restored to a clean `git diff` after): control PASS; CLAUDE.md's ceiling at the
    default 6 -> 5, i.e. card 556's original defect re-committed -> FAIL; CLAUDE.md's `8 at 4` ->
    `6 at 4`, the wider-drain case card 550 exists for -> FAIL; SKILL.md's `при 4 — 8` -> `при 4 —
    6` -> FAIL; SKILL.md's worst run at the default 5 -> 6, collapsing the two distinct numbers into
    one -> FAIL on the worst-run derivation; delete CLAUDE.md's ceiling paragraph outright -> FAIL
    from the slicer, with its own message, never a confusing crash or a silent green. Added in
    review, by CONSTRUCTING the divergence rather than reading the diff: drop `, 10 at 5` from
    CLAUDE.md's table and leave SKILL.md's `при 5 — 10` -> FAIL on the cross-file mapping. That
    round is the reason the cross-file half is shaped the way it is — it was GREEN before."""
    def re_derive(where: str, default: int, worst: int, ceilings: dict[int, int]) -> None:
        assert default == config.DEFAULT_WIP_LIMIT, (
            f"{where} derives the ceiling at a default wip_limit of {default}, but "
            f"config.DEFAULT_WIP_LIMIT is {config.DEFAULT_WIP_LIMIT} — the prose reasons about a "
            f"default the code no longer has"
        )
        assert len(ceilings) >= 3, (
            f"{where}: only {len(ceilings)} (limit -> ceiling) pair(s) parsed out of the table it "
            f"states, so every arithmetic check below would be near-vacuous. A legitimate reword "
            f"means updating this pin's regex, not deleting the check"
        )
        assert worst == 2 * (default - 1) + 1, (
            f"{where} states a worst purely mechanical run of {worst} at wip_limit {default}, but "
            f"its own cited formula 2·(N−1)+1 gives {2 * (default - 1) + 1}"
        )
        assert default in ceilings, (
            f"{where} states a worst run at wip_limit {default} but no ceiling for that limit, so "
            f"the two numbers this card is about can no longer be compared at all"
        )
        # ordered BEFORE the table loop on purpose: both fire on a bad ceiling at the default, and
        # this one names what actually went wrong the first time (see the docstring) instead of
        # reporting it as an arbitrary arithmetic slip
        assert worst < ceilings[default], (
            f"{where}: the ceiling at wip_limit {default} is {ceilings[default]}, which is NOT "
            f"strictly above the worst mechanical run of {worst}. That is card 556 exactly — the "
            f"worst run quoted where the ceiling belongs sends an agent to a human on arithmetic"
        )
        for limit, ceiling in sorted(ceilings.items()):
            assert ceiling == 2 * limit, (
                f"{where} states a ceiling of {ceiling} at wip_limit {limit}, but the formula it "
                f"cites in the same breath, 2 × wip_limit, gives {2 * limit}. Fix the number; if "
                f"the FORMULA itself changed, change it in BOTH files and here"
            )

    # --- CLAUDE.md: the release section's racer paragraph
    claude = _flat(_claude_ceiling_paragraph(_claude_md_text()))
    worst_match = re.search(r"\*\*(\d+)\*\* at the default (\d+)", claude)
    assert worst_match, (
        "CLAUDE.md's ceiling paragraph no longer states the worst MECHANICAL run at the default "
        "limit in a shape this pin can read. Reword freely — but update this regex, do not drop it"
    )
    claude_worst, claude_default = int(worst_match.group(1)), int(worst_match.group(2))
    table = re.search(r"the ceiling is \*\*`2 × wip_limit`\*\*:(.*?)\.", claude)
    assert table, (
        "CLAUDE.md no longer follows its ceiling formula with the (limit -> ceiling) table this "
        "pin re-derives. Reword freely — but update this regex, do not delete the check"
    )
    claude_ceilings: dict[int, int] = {}
    for entry in table.group(1).split(","):
        numbers = re.findall(r"\d+", entry)
        assert len(numbers) == 2, (
            f"CLAUDE.md's ceiling-table entry {entry.strip()!r} is not the '<ceiling> at <limit>' "
            f"shape this pin parses; update the regex rather than removing the arithmetic check"
        )
        claude_ceilings[int(numbers[1])] = int(numbers[0])
    re_derive("CLAUDE.md's ceiling paragraph", claude_default, claude_worst, claude_ceilings)

    # --- SKILL.md: «Откуда потолок», the same derivation written for agents
    skill = _flat(_ceiling_derivation_section(_skill_text()))
    default_match = re.search(
        r"При дефолтном лимите (\d+) худший механический прогон равен (\d+), "
        r"а потолок — \*\*(\d+)\*\*",
        skill,
    )
    assert default_match, (
        "SKILL.md's «Откуда потолок» no longer states the worst run AND the ceiling at the default "
        "limit in a shape this pin can read. Reword freely — but update this regex, do not drop it"
    )
    skill_default, skill_worst = int(default_match.group(1)), int(default_match.group(2))
    skill_ceilings = {skill_default: int(default_match.group(3))}
    narrow = re.search(r"при лимите (\d+) потолок (\d+)", skill)
    assert narrow, (
        "SKILL.md's «Откуда потолок» no longer states the sequential case (limit 1), the instance "
        "that proves the rule is a formula and not the default's constant; update this regex"
    )
    skill_ceilings[int(narrow.group(1))] = int(narrow.group(2))
    for limit, ceiling in re.findall(r"при (\d+) — (\d+)", skill):
        skill_ceilings[int(limit)] = int(ceiling)
    re_derive("SKILL.md's «Откуда потолок»", skill_default, skill_worst, skill_ceilings)

    # --- and the two copies of one derivation must tabulate the SAME rule.
    # Compared as WHOLE mappings, not "where they overlap", which is the form review measured
    # useless: both re_derive calls above force each dict to {n: 2n} INDEPENDENTLY, so equal
    # VALUES at a shared limit are already implied and a per-limit value loop could not fail for
    # any input. What is NOT implied is the KEY SET — one file's table narrowing away from the
    # other — and that is a real divergence: dropping `10 at 5` from CLAUDE.md while the shipped
    # rulebook keeps it passed GREEN under the overlap-only comparison this replaced. Hence one
    # assertion that can actually fire, instead of a loop that cannot.
    assert claude_ceilings == skill_ceilings, (
        f"CLAUDE.md and SKILL.md no longer state the same (limit -> ceiling) table: CLAUDE.md has "
        f"{sorted(claude_ceilings.items())}, SKILL.md has {sorted(skill_ceilings.items())} — "
        f"limits only one of them tabulates: {sorted(set(claude_ceilings) ^ set(skill_ceilings))}. "
        f"Only SKILL.md self-heals onto every consumer, so a table that narrows on one side leaves "
        f"agents and maintainers reading different rules; restore the missing rows, or move BOTH "
        f"copies together (and this pin's regexes with them)"
    )


def _shared_resources_section(text: str) -> str:
    """The shared-resource rules a per-task agent follows under the parallel drain.

    Sliced like `_gc_section` / `_tick_step_3`, and for the same measured reason: `attach_file`,
    `Page URL` and `browser` would each be satisfiable somewhere else in a 800-line rulebook, so a
    whole-file substring could not tell "the rule is still stated where an agent reads it" from
    "the word survives in an unrelated paragraph". Width is asserted, not assumed — a slice that
    silently widened to the whole file would restore exactly the weakness it exists to remove."""
    start = text.find("## Общие ресурсы: worktree изолирует ФАЙЛЫ")
    assert start != -1, "the shared-resource section is no longer where this pin can find it"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the shared-resource section no longer ends at the next top-level heading"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the slice is not a proper subset of SKILL.md"
    assert "## Кто выполняет работу" not in section, "the slice swallowed the following section"
    return section


def test_the_shared_browser_rule_stays_detectable_rather_than_wishful():
    """VMCP-97 (554): the worktree isolates the working copy and NOTHING else, so a per-task agent
    under the parallel drain shares the browser, the scratch dir and any fixed port/container name
    with its siblings. Measured while writing the card: one `@playwright/mcp` per `claude` process
    (siblings are subagents of that one session, so one browser / one profile / one current page
    for all of them); no isolation parameter on any browser tool; `--isolated` / `--user-data-dir`
    are SERVER LAUNCH args, i.e. out of an agent's reach — and even there they isolate per MCP
    client, not per subagent. Interference therefore cannot be PREVENTED from inside an agent,
    only DETECTED, and every browser response prints `Page URL:` to detect it with.

    That asymmetry is what this pins, because it is the clause a later tidy-up would drop as
    belt-and-braces while leaving the reassuring half ("work in one burst") in place — turning a
    detectable failure into a silent one. And it is not merely inconvenient: the rulebook tells
    agents to ATTACH a verification screenshot as evidence, so a stolen page becomes a sibling's
    screenshot approved as this card's proof. `attach_file` is the two-sided anchor (the rule
    hands it a path in the MAIN checkout, since artifacts land in the MCP server's cwd, not in the
    agent's worktree) — rename it in workflow.py and `test_attachment_upload_rule_names_the_tool`
    goes red alongside this.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, and the section restored from a COPY rather than `git checkout` — the section is
    uncommitted while it is being written, so a git restore silently deletes the very thing under
    test and every later round then "fails" from the slicer instead of from its mutation, which is
    how the first attempt at this list produced three worthless greens-in-red):
    control PASS; soften the `Page URL` check to "compare the address" -> FAIL; drop the
    `attach_file` clause -> FAIL; drop the "не зови вовсе" ban on browser_close/browser_resize ->
    FAIL; drop the `browser_tabs` "a tab is not isolation" clause -> FAIL; rename the section
    heading -> FAIL loudly from the slicer, never silently green; reword the surrounding prose
    without touching the pinned clauses -> PASS (by design: wording is review's job, per this
    module's docstring). Each failure was read for its MESSAGE, not just its colour — a round that
    fails from the slicer proves nothing about the clause it claims to pin.

    REWORK (review of the first attempt): the check as first shipped FAILED OPEN. It claimed
    "every browser response prints `Page URL:`", which is false for `browser_take_screenshot` —
    the one call the evidence rule names. Measured on an own isolated server across four server
    configurations (default, `--snapshot-mode none`, `--output-dir`, no tab navigated yet):
    screenshot NEVER prints the line, snapshot ALWAYS does. The cause is in playwright-core's
    response renderer: the `Page` section renders when `_includeSnapshot !== "none" ||
    tabHeaders.some(h => h.changed)`; screenshot leaves `_includeSnapshot` at its "none" default
    and so depends on `changed` — a flag ANY previously serialized response consumes, a sibling's
    included — while `browser_snapshot` calls `setIncludeFullSnapshot()` ("explicit"), which
    satisfies the first disjunct unconditionally, config-independently. So an agent looking for
    the line in a screenshot response finds nothing and reads it as "no mismatch": absence of
    evidence taken for evidence of absence, the same shape as a `git rev-parse` that merely echoes
    its argument back. Hence the two pins added here: the rule must name `browser_snapshot` as
    what it checks WITH, and must say outright that a missing line is not confirmation.

    And the pin itself was the nit: it held the TOKEN `Page URL`, so a mutation that kept the
    token while deleting the imperative ("сверь / перейди заново / вывод не делай") passed. The
    branch an agent executes on a MISMATCH is the rule; the token is only where it reads it. Both
    are pinned now, which is what makes the mutation below bite.

    The same weakness then bit the INHERITED `attach_file` pin, and only a mutation round found
    it: this rework added a second mention of `attach_file` (the verify-before-attach clause), so
    `"attach_file" in section` was satisfied even after the clause naming WHICH path — absolute,
    in the main checkout — was deleted. Adding prose can silently defang a pin that was honest
    when it was written; the pin now holds that clause, not the word. Rounds added for both:

    T1, on top of the list above: delete the "зови `browser_snapshot` и сверяй `Page URL`"
    instruction while LEAVING the token in the section -> FAIL (this is the nit's round); soften
    "не печатает `Page URL` НИКОГДА" -> FAIL; drop "Нет строки — нет подтверждения" -> FAIL; gut
    the mismatch branch ("перейди заново, пересними, вывод не делай") -> FAIL; drop only "вывод
    не делай" -> FAIL; put the disproved "В каждом ответе печатается `Page URL:`" claim back ->
    FAIL on the negative pin; delete the attach_file PATH clause while the bare token survives
    elsewhere in the section -> FAIL."""
    section = _shared_resources_section(_skill_text())
    flat = _flat(section)
    # WHAT to read the page identity from — pinned as the INSTRUCTION, not as the token:
    # `Page URL` and `browser_snapshot` each occur elsewhere in this very section (the
    # no-isolation-parameter bullet names both tools), so a bare-token pin is satisfied by
    # prose that instructs nothing. That is the nit review raised, and this is its fix.
    assert "зови `browser_snapshot` и сверяй `Page URL`" in flat, \
        "the rule no longer tells the agent to verify WITH browser_snapshot — `Page URL` is the " \
        "one line browser_take_screenshot never prints, so the check would be looking at nothing"
    assert "не печатает `Page URL` НИКОГДА" in flat, \
        "the rule no longer states that browser_take_screenshot never prints `Page URL` — an " \
        "agent that looks for it there finds nothing and reads that as 'no mismatch'"
    assert "Нет строки — нет подтверждения" in flat, \
        "the rule no longer says a MISSING line is not confirmation — that is the fail-open " \
        "this rework exists to close (absence of evidence read as evidence of absence)"
    # WHAT TO DO about a mismatch: the branch IS the rule
    assert "перейди заново" in flat, \
        "the rule may still carry the `Page URL` token but no longer says what to DO when it " \
        "does not match (re-navigate and re-shoot) — a token is a word, not an instruction"
    assert "вывод не делай" in flat, \
        "the rule no longer forbids CONCLUDING from a page that may be a sibling's — " \
        "detect-don't-prevent is worthless if the agent may still use what it saw"
    assert "`attach_file` отдавай АБСОЛЮТНЫЙ путь В ГЛАВНОМ ЧЕКАУТЕ" in flat, \
        "the browser rule no longer says WHICH path attach_file must be given (absolute, in " \
        "the MAIN checkout) — the bare token now also occurs in the verify-before-attach " \
        "clause, so pinning the word alone would survive deleting the path rule"
    for tool in ("browser_close", "browser_resize"):
        assert tool in section, f"the rule no longer bans {tool} — it destroys a sibling's state"
    assert "browser_tabs" in section, \
        "the rule no longer explains that a tab is not isolation (global, shifting indices)"
    # the disproved claim this rework removed must not come back
    assert "В каждом ответе печатается" not in flat, \
        "the disproved claim that EVERY browser response prints `Page URL:` is back — it is " \
        "false for browser_take_screenshot, and it is what made the check fail open"


def test_the_shared_resource_rules_name_a_knob_the_agent_can_actually_reach():
    """The sibling failure this card was warned about: a rule that names a knob the agent cannot
    reach is worse than no rule. So the two collisions that ARE fixable from inside must keep
    their concrete recipe, not a platitude — a fixed container name and a fixed host port were
    both reproduced (`Conflict. The container name … is already in use`, `Bind for 0.0.0.0:3456
    failed: port is already allocated`), and the scratch dir was measured shared (179 entries
    written by different agents of one session in a day).

    The fenced recipe is checked to be a DIFFERENT fence from the integration recipe: `sh` blocks
    are what agents copy, and `_integration_recipe` asserts there is exactly one containing the
    push refspec. Adding a second `sh` fence to this file is safe only while it stays clear of
    that string, and this makes the two invariants fail independently instead of one silently
    invalidating the other's slice.

    MUTATION-CHECKED alongside the test above, same discipline: control PASS; replace `docker rm
    -f` with a "clean up afterwards" comment -> FAIL; replace the id-derived name and port with
    fixed ones (`vikunja-test-agent`, `PORT=23456`) -> FAIL; paste the push refspec into this
    section's fence -> FAIL here AND in
    `test_the_recipe_verifies_the_evidence_sha_actually_landed_on_main` ("expected exactly 1
    fenced integration recipe, found 2"), which is precisely the cross-invariant collision this
    assertion exists to make loud."""
    section = _shared_resources_section(_skill_text())
    assert "id" in section and "$ID" in section, \
        "the isolate-by-task-id recipe lost the task id it derives every shared name from"
    assert "docker rm -f" in section, \
        "the recipe no longer cleans up — a leaked container holds its name and port all day"
    assert "git push origin HEAD:main" not in section, \
        "this section's sh fence must not contain the push refspec — it would break the " \
        "exactly-one-integration-recipe invariant (_integration_recipe)"
    _integration_recipe(_skill_text())  # still exactly one, and still not this one


def _drain_width_section(text: str) -> str:
    """The «Ширина дренажа» bullet that explains what `limit` gates — sliced to that one item.

    Scoped like `_gc_section` / `_tick_step_3`, and for the same measured reason: `wip.limit`,
    `claim` and `active` appear all over the rulebook (the queue-discipline bullet, the parallel
    drain, the retry ceiling), so a whole-file substring could not tell "the rule is still stated
    where the pump reads the payload" from "some other section happens to use the words"."""
    start = text.find("- **`limit` — гейт на ОДИН переход (`claim`)")
    assert start != -1, \
        "the rulebook no longer states that `limit` gates ONE transition, not the active count"
    end = text.find("\n- **`wip_saturated", start)
    assert end != -1, "the drain-width slice no longer ends where the wip_saturated bullet begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the drain-width slice is not a proper subset of SKILL.md"
    assert "wip_saturated" not in section, \
        "the slice swallowed the next bullet — the prose it exists to exclude"
    return section


def test_the_wip_overshoot_the_rulebook_describes_is_one_the_code_produces():
    """VMCP-80 (529): `wip_limit` reads as an invariant on the active count everywhere it is
    written, but it is a gate on ONE transition — `claim`. `review_task(verdict='needs_work')`
    moves a card Review→Build without passing it, so `next_task` can honestly report
    `{"active": 4, "limit": 3}`. That behaviour is correct and deliberately unchanged (rework must
    be receivable at the limit or reviewed work strands); what shipped wrong was the documentation.

    So this pin does not compare strings alone — it DRIVES the real `Workflow` into the overshoot
    and checks the rulebook's claims against what came out. That is the point: the four sentences
    this card added to SKILL.md, `claim`'s tool docstring, CLAUDE.md and the drain design spec all
    assert a runtime state, and SKILL.md self-heals onto every consumer with no review gate of its
    own. If a later change makes the overshoot impossible (a second gate on the bounce, a clamped
    count), the rulebook does not go stale quietly — this goes red first.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly
    1 test): control PASS; clamp `active` to the limit in the wip payload -> FAIL; gate
    `review_task`'s needs_work bounce on the WIP limit -> FAIL; drop the over-budget clause from
    next_task's resume note -> FAIL; delete the rule from the drain-width bullet -> FAIL; delete
    the paragraph from claim's tool docstring -> FAIL; rename the bullet's opening words so the
    slice cannot find it -> FAIL (loudly, with its own message, never silently green).

    Deliberately NOT pinned: the two OTHER paths into the overshoot (a human moving a card out of
    Your Call, a lowered `wip_limit`) — neither is a tool call, so neither is expressible as a
    contract between the rulebook and workflow.py. They are covered behaviourally in
    tests/unit/test_workflow_wip.py, which is also where the "advance(to='build') is NOT such a
    path" correction lives."""
    section = _drain_width_section(_skill_text())
    assert "гейт" in section and "claim" in section, \
        "the drain-width rule no longer says WHICH transition the limit gates"
    assert "`active` ЗАКОННО" in section and "больше" in section, \
        "the rulebook no longer states that active may legitimately exceed limit"
    assert "НЕ порча доски" in section, \
        "the rulebook no longer tells the pump that an overshoot is not board corruption"
    assert "max(0, limit − active)" in _flat(section), \
        "the rulebook no longer explains why `free` cannot show the overshoot"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=3)

    def claim_fresh(title):
        task_id = api.add_task(title, "Queue")["id"]
        wf.claim(task_id)
        return task_id

    bounced = claim_fresh("reviewed, then bounced")
    wf.advance(bounced, to="build", spec="…")
    wf.advance(bounced, to="review", worklog="…", evidence="0" * 40)
    for n in range(3):                       # the pump refills the freed slot, as the tick does
        claim_fresh(f"held {n}")
    assert wf.next_task()["wip"] == {"active": 3, "limit": 3, "free": 0}, "precondition: full"

    # the bounce goes AROUND the gate — no ownership, no claim, no slot check
    wf.review_task(bounced, verdict="needs_work", report="not yet")
    over = wf.next_task()
    assert over["wip"] == {"active": 4, "limit": 3, "free": 0}, \
        "the rulebook documents active > limit, but the code no longer produces it"
    assert over["wip"]["free"] == 0, "free saturates at 0 — the reason the rule has to exist"

    # and the payload says so where the pump reads it, exactly as the rulebook promises
    assert "against a limit of 3" in over["note"], \
        "SKILL.md promises next_task's note discloses the overshoot, but the note dropped it"

    # documenting it is not permission: the gate still refuses, with the TRUE count
    with pytest.raises(workflow.WorkflowError, match=r"WIP limit reached \(4/3\)"):
        wf.claim(api.add_task("one too many", "Queue")["id"])

    # the same rule must reach an agent reading the TOOL, not just the rulebook
    claim_doc = inspect.getdoc(server.claim) or ""
    assert "NOT an invariant on the active count" in claim_doc and "review_task" in claim_doc, \
        "claim's tool docstring no longer says the WIP gate guards one transition, not the count"


def test_the_browser_answer_leads_with_the_isolation_an_agent_can_launch_itself():
    """The human's card asked for parallel agents to PARALLELISE their tools — "playwright should
    launch so it does not disturb the others". The first attempt answered "cannot be done": true
    of the SHARED MCP browser (no isolation parameter on any tool; `--isolated`/`--user-data-dir`
    isolate per MCP client, and all siblings are one client), but false of the request, which
    review disproved by simply doing it. Reproduced here before documenting: `npx -y
    @playwright/mcp@latest --isolated --headless` from an own cwd is ready in ~1s and gives REAL
    per-agent isolation — two servers run at once, the first's page survived two `navigate`s by
    the second, and `browser_close` on the first did nothing to the second. For the common case
    (a screenshot as evidence) there is no MCP at all: `npx -y playwright@latest screenshot
    --channel=chrome URL file.png` takes ~2s, writes into the agent's OWN worktree and exits by
    itself; two concurrent runs from different worktrees both returned rc=0 with both files
    intact, and no browser process leaked.

    So the deliverable must LEAD with the launchable answer and keep detect-don't-prevent as the
    fallback for agents that use the shared server anyway. Order is the assertion: a later edit
    that demotes "launch your own" back to a conditional footnote — which is exactly how the
    first attempt buried it — restores the wrong answer to the card while every keyword still
    appears somewhere in the section. `--channel=chrome` is pinned because it is load-bearing,
    not decoration: without it the CLI refuses with "npx playwright install" (that refusal was
    reproduced), and a recipe an agent cannot run is worse than none — this section's other test
    exists for that same reason.

    MUTATION-CHECKED, same discipline as the two tests above (`__pycache__` cleared, exactly 1
    test selected per round, section restored from a COPY — never `git checkout`, which deletes
    the subject under test — and every failure read by its MESSAGE): control PASS; swap the two
    subsections so the shared-browser rules come first -> FAIL on the ordering assert; strip
    `--channel=chrome` from the screenshot RECIPE -> FAIL; strip `--isolated` from the launch
    RECIPE -> FAIL; strip `--headless` -> FAIL; restore the old "изнутри его изолировать НЕЛЬЗЯ"
    framing -> FAIL on the negative pin; reword the costs prose without touching the recipes ->
    PASS.

    Two of those rounds are why the flags are matched inside the fence. Written first as
    section-wide substrings, they stayed GREEN while the flag was stripped from the command an
    agent copies — `--channel=chrome` survives in the sentence explaining why it is required, and
    `--isolated` survives in THREE places including the bullet that says the flag is out of reach
    on the shared server. A pin satisfied by the prose about a flag, while the runnable line has
    lost it, is the same defect this card was returned for: a check that reports success from the
    wrong evidence."""
    section = _shared_resources_section(_skill_text())
    own = section.find("#### Свой браузер")
    shared = section.find("#### Общий браузер")
    assert own != -1, \
        "the section no longer has a 'свой браузер' subsection — the card's answer (an agent " \
        "CAN have its own browser) is gone, leaving only the disproved 'cannot be done'"
    assert shared != -1, \
        "the shared-browser subsection is gone — agents that use the session browser anyway " \
        "still need the detect-don't-prevent rules"
    assert own < shared, \
        "the own-browser answer no longer LEADS — demoting it below the shared-browser rules " \
        "is how the first attempt buried the one thing that actually answers the card"
    # The flags are pinned INSIDE the fenced recipes, not merely "somewhere in the section":
    # both `--isolated` and `--channel=chrome` also occur in the surrounding prose (and
    # `--isolated` occurs in the bullet explaining it is out of reach on the SHARED server —
    # the worst possible satisfier), so a section-wide substring stayed green in the mutation
    # round that stripped the flag from the runnable command. An agent copies the fence.
    fences = re.findall(r"```sh\n(.*?)```", section, re.S)
    cli = [f for f in fences if "playwright@latest screenshot" in f]
    assert len(cli) == 1, \
        "expected exactly 1 fenced one-line screenshot recipe in the shared-resources section, " \
        f"found {len(cli)} — it is the cheapest own-browser answer and the most-used one"
    assert "--channel=chrome" in cli[0], \
        "the screenshot RECIPE lost `--channel=chrome` — without it the CLI refuses with " \
        "'npx playwright install' (reproduced), so the line an agent copies does not run"
    srv = [f for f in fences if "@playwright/mcp@latest" in f]
    assert len(srv) == 1, \
        "expected exactly 1 fenced launch line for an agent's OWN playwright MCP server, " \
        f"found {len(srv)}"
    assert "--isolated" in srv[0], \
        "the own-server RECIPE lost `--isolated` — that flag is what keeps the profile in " \
        "memory and off the shared browser's disk profile; prose about it is not a command"
    assert "--headless" in srv[0], \
        "the own-server RECIPE lost `--headless` — the browser is headed by default, so a " \
        "window pops up on the human's screen"
    for old in ("изнутри его изолировать НЕЛЬЗЯ", "НЕ выполнимо"):
        assert old not in section, \
            f"the disproved framing is back in SKILL.md ({old!r}) — an agent CAN launch its " \
            "own isolated browser; see this test's docstring"


def _landed_check() -> str:
    """The one command that tells "my push landed after all" from "it really did not"."""
    return "git merge-base --is-ancestor HEAD origin/main"


def _race_check() -> str:
    """550's diagnosis: WHO won the race, once the work is known not to be on main."""
    return "git log --oneline HEAD..origin/main"


def test_a_rejected_push_asks_whether_the_work_landed_before_it_escalates():
    """VMCP-102 (559): 550 taught the rulebook that an EMPTY `HEAD..origin/main` after a rejected
    push means there was no race at all — protected branch, no push rights, a hook — so retrying is
    futile and the agent should escalate at once. Correct, and its reviewer constructed every one of
    those cases. It is one state short: an empty range ALSO means the push LANDED and the client
    reported failure anyway (a 502, a dropped connection). Constructed against real local repos two
    independent ways — a multi-ref push where `main` is accepted while a second ref is declined, and
    a successful push whose remote-tracking ref is then rewound (what a client holds when the
    response is lost) — both produce an empty range, indistinguishable from the genuine `pre-receive`
    refusal used as a control. So the rule as written woke a human about finished work, in an
    unattended loop: the precise failure 550 exists to remove, one state to the left.

    `git merge-base --is-ancestor HEAD origin/main` separates them — 0 in both landed constructions,
    1 in the control — and this test pins the two properties of HOW that got written down, both of
    which were measured rather than reasoned:

    * **ORDER.** The check runs BEFORE the range is looked at, not inside its empty branch. A push
      that landed and then had a sibling land on top shows a NON-EMPTY range, reads as honest
      mechanics, and is sent round again — and that retry silently corrupts the evidence: `git
      rebase origin/main` DROPS the already-upstream commit, HEAD moves to the sibling's tip, the
      push prints "Everything up-to-date", and `git rev-parse HEAD` then reports the SIBLING's sha,
      on which both of the recipe's landing checks pass. (This is also why the card's premise that
      the old blind-retry rule "self-healed" the case is wrong: it mis-attributed evidence.) Asking
      the landed question first answers both range shapes with one command. The order assertion is
      the load-bearing one here and it is not a tautology — the two positions come from two
      different substrings and swapping the lines makes it fail, which was measured.
    * **The fetch travels WITH it.** On a stale remote-tracking ref the same command answers 1 about
      work that is on main. Staleness can only produce a false 1, never a false 0 (an old value of
      main cannot contain an unpushed commit), so it is fail-safe — but it defeats the fix, so the
      pin is on the chained `git fetch origin && …`, not on the bare command.

    The exit-1 branch is pinned UNCHANGED by `test_the_integration_retry_ceiling_is_pinned` above,
    and that is deliberate: the wording risk here is the mirror of the bug. "An empty range is
    ambiguous, check before escalating" would teach an agent to read emptiness optimistically and
    stop escalating when it should. It is not ambiguous once fetched — the control gives empty AND
    exit 1 — so the decision is the exit code, and 550's branch keeps its wording verbatim.

    CLAUDE.md is checked for the same ORDER because it carries the second write-up of this rule and
    two copies of one rule drift (the lesson of 556, one card earlier).

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, both files restored to a clean `git diff` after): control PASS; delete the landed check
    from the fence -> FAIL; SWAP the two commands in the fence so the range is read first -> FAIL on
    the order assertion, which is the round that proves the two sides can disagree; strip `git fetch
    origin && ` off the landed check -> FAIL; delete the fence's exit-0 "spend no round, wake no
    one" branch -> FAIL; delete the prose bullet's exit-0 verdict -> FAIL; swap the two commands in
    CLAUDE.md's paragraph -> FAIL on the cross-file order."""
    text = _skill_text()
    flat = _flat(text)
    recipe = _integration_recipe(text)

    assert f"git fetch origin && {_landed_check()}" in recipe, (
        "the recipe no longer asks whether the push LANDED before diagnosing the race (or it "
        "dropped the fetch that makes the answer true — a stale tracking ref reports 'not landed'"
    )
    assert _race_check() in recipe, "the recipe lost 550's race diagnosis entirely"
    assert recipe.index(_landed_check()) < recipe.index(_race_check()), (
        "the recipe reads the race range BEFORE asking whether the work landed. A landed push with "
        "a sibling on top has a NON-empty range, so that order sends it round again — and the "
        "retry rebases the already-upstream commit away and mis-attributes the evidence sha"
    )
    assert "человека НЕ зови" in recipe, (
        "the recipe no longer says that a landed push spends no round and wakes nobody — the exit-0 "
        "branch without its verdict is the half-stated rule an agent fills in with a guess"
    )

    assert "**Код 0 — работа НА ГЛАВНОЙ**" in flat, \
        "the prose lost the exit-0 verdict: a landed push is evidence, not an escalation"
    assert "**Код 1 — работы там нет**" in flat, \
        "the prose lost the exit-1 verdict, which is the branch that must still escalate"
    assert "ВЫБРАСЫВАЕТ твой коммит" in flat, (
        "the prose no longer says WHY the landed check comes first — without the dropped-commit "
        "measurement the order reads as arbitrary and gets tidied back"
    )

    claude = _flat(_claude_ceiling_paragraph(_claude_md_text()))
    assert _landed_check() in claude and _race_check() in claude, (
        "CLAUDE.md's racer paragraph no longer states both steps of the rejected-push diagnosis"
    )
    assert claude.index(_landed_check()) < claude.index(_race_check()), (
        "CLAUDE.md states the two diagnosis steps in the opposite order to the shipped rulebook. "
        "Only SKILL.md reaches agents, so the copies must not drift; move BOTH or neither"
    )


def test_the_brief_less_ceiling_reads_the_repo_toml_before_it_falls_back():
    """VMCP-102 (559), the second half: `2 × wip.limit` needs the limit, and a per-task agent does
    not call `next_task`, so 550 told an agent whose brief omitted it to assume 6. That constant is
    safe at limits 1-3 (worst mechanical runs 1, 3, 5, all below it) and breaks from 4 up, where the
    worst run is 7 — so at `wip_limit = 4` the fallback calls a human onto the pure arithmetic the
    formula was introduced to stop. 4 is the flip point, not the only bad value.

    It does not need fixing so much as demoting: `wip_limit` is repo-toml-ONLY by design (config.py
    — never env, because it is committed team policy), and the toml is COMMITTED, so git materialises
    it into every linked worktree. Verified by looking rather than assuming, since that is exactly
    where a per-task agent stands: in `…worktrees/task-<id>` the toml is present and the gitignored
    `.vikunja-mcp.env` is not. So an agent with no limit in its brief READS one, and the constant
    survives only for "there is no toml at all".

    On that remaining domain the constant is no longer a guess but the derivation evaluated: no toml
    implies no `wip_limit` (it cannot live anywhere else) implies the documented default, whose
    ceiling is 2 × it. That is what this test asserts, and it asserts it ACROSS SOURCES rather than
    within one — the number SKILL.md prints versus `config.DEFAULT_WIP_LIMIT` doubled in Python, and
    the filename SKILL.md sends the agent to versus `config.REPO_FILE`. Both pairs can move
    independently, which is the property the same-source assertion this repo keeps re-inventing
    (two sides computed from one origin, therefore unable to disagree) does not have. Re-value
    `DEFAULT_WIP_LIMIT` and the prose goes red; rename the config file and the rulebook stops
    pointing agents at a file that exists.

    The ORDER inside the sentence is asserted too, for the same reason as the sibling test above: a
    fallback quoted before the read is a fallback that gets taken.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each run confirmed to select exactly 1
    test, all files restored to a clean `git diff` after): control PASS; `config.DEFAULT_WIP_LIMIT`
    3 -> 4 with the prose untouched -> FAIL (this is the round proving the two sides are independent
    and can disagree); SKILL.md's `бери 6` -> `бери 8` -> FAIL, the same assertion from the other
    side; `config.REPO_FILE` renamed -> FAIL; delete the toml-read clause, leaving the bare constant
    -> FAIL; move the constant ahead of the read -> FAIL on the order."""
    text = _skill_text()
    section = _flat(_ceiling_derivation_section(text))

    assert config.REPO_FILE in section, (
        f"SKILL.md's brief-less ceiling no longer names {config.REPO_FILE} as the place to READ "
        f"`wip_limit` from — either the clause was dropped, or config.py renamed the file and the "
        f"rulebook now sends every agent to one that does not exist"
    )
    assert "wip_limit" in section, "the derivation no longer names the key an agent must read"
    assert config.REPO_ENV_FILE in section, (
        f"the derivation no longer contrasts {config.REPO_FILE} with the gitignored "
        f"{config.REPO_ENV_FILE}. That contrast is the whole reason the read WORKS from a linked "
        f"worktree — the toml is committed and materialised there, the env file is not — and "
        f"without it the instruction reads like a guess about a file that might be absent"
    )

    m = re.search(r"— \*\*бери (\d+)\*\*", section)
    assert m, (
        "SKILL.md's last-resort ceiling is no longer stated in a shape this pin can read. Reword "
        "freely — but update this regex, do not drop the check"
    )
    fallback = int(m.group(1))
    expected = 2 * config.DEFAULT_WIP_LIMIT
    assert fallback == expected, (
        f"SKILL.md tells a brief-less agent with no repo toml to use a ceiling of {fallback}, but "
        f"'no toml' means no `wip_limit` at all, i.e. config.DEFAULT_WIP_LIMIT = "
        f"{config.DEFAULT_WIP_LIMIT}, whose ceiling by the formula both files state is {expected}. "
        f"The constant is only legitimate while it EQUALS the derivation on that one domain"
    )

    assert section.index(config.REPO_FILE) < section.index("**бери "), (
        "the brief-less rule quotes its constant before it tells the agent to read the real limit. "
        "A fallback offered first is a fallback taken first — which is how a consumer at "
        "wip_limit = 4 ends up escalating on arithmetic despite having the number on disk"
    )

    assert "он прочитает `wip_limit` из репо-конфига" in _flat(text), (
        "the orchestrator's dispatch brief still promises the agent a bare default when the limit "
        "is not named. Both halves have to agree, or the brief keeps teaching the old behaviour"
    )
def _rule_boundary_bullet(text: str) -> str:
    """The «Граница правила» bullet — what happens when the OTHER session is not a sibling.

    Sliced out of `_shared_resources_section` rather than out of the whole file, for the reason
    every slicer here records: `.claude/settings.json` is named TWICE inside this one bullet
    (the remedy, then the note that a project-scoped file really does reach the MCP server's
    env), so even a bullet-scoped substring has to be chosen with care — and a file-wide one
    could not tell "the rule is still stated" from "the path is mentioned somewhere".

    This bullet is currently the LAST item of its section, so the slice runs to the section's
    end — which `_shared_resources_section` already bounds at the next `##` heading, and which
    is asserted to be a proper subset there. If a later bullet is appended after it, `\\n- **`
    ends the slice earlier; both shapes are correct, neither can silently widen to the file.
    """
    section = _shared_resources_section(text)
    start = section.find("- **Граница правила.**")
    assert start != -1, \
        "SKILL.md no longer draws the boundary of the shared-browser rules (one session's " \
        "subagents) — an agent meeting a SECOND `claude` session has nothing to read"
    end = section.find("\n- **", start + 1)
    bullet = section[start:] if end == -1 else section[start:end]
    assert 0 < len(bullet) < len(section), \
        "the rule-boundary slice is not a proper subset of the shared-resources section"
    assert "#### Общий браузер" not in bullet, "the slice swallowed the subsection heading"
    return bullet


def test_the_cross_session_boundary_names_the_fix_and_not_only_the_symptom():
    """VMCP-… (558): the cross-session case is the ONE browser collision an agent cannot detect
    its way out of, and it is also the one that has a real fix — so the bullet has to carry the
    fix, not just the diagnosis.

    Measured while doing the card: `@playwright/mcp` derives its on-disk profile as
    `mcp-<channel>-<sha256(first MCP root path)[:7]>`, so two sessions collide only when they
    share a workspace ROOT; different repositories never do. The collision is loud (the second
    browser refuses to start after ~7 s of lock wait) and the remedy is one committed line —
    `PLAYWRIGHT_MCP_ISOLATED` in `.claude/settings.json`, the env equivalent of `--isolated`.

    Three clauses are pinned, each as an INSTRUCTION rather than as a token, because the failure
    mode of this bullet is not deletion but EROSION into a symptom report — "your second browser
    will not start, that is normal" — which reads as complete, ships to every consumer over the
    self-healing `stable` copy with no review gate (see this module's docstring), and leaves the
    reader believing there is nothing to do:

      * the DERIVATION (the profile formula) and the scope claim it supports. Without it the
        no-collision guarantee is an assertion an agent has no reason to trust, and the first
        person who hits an unrelated browser failure in a different repo will "generalise" the
        rule to cover it.
      * the remedy's KEY AND VALUE together. `"true"` is not decoration: playwright-core's
        `envToBoolean` accepts only `"true"`/`"1"` and silently ignores everything else, so a
        rulebook that names the variable without its value invites `"yes"` — a setting that
        looks configured and does nothing. (The repo's own settings file is pinned against the
        same fact in tests/unit/test_repo_browser_isolation.py.)
      * WHERE it goes. The bare path cannot carry this: it appears twice in this bullet, so
        pinning the token alone stays green while the sentence that says "the `env` block of
        that file" is deleted — the same defect the `attach_file` pin above was reworked for.

    Deliberately NOT pinned: the "do not add it to someone else's project silently" advice and
    the in-memory-profile cost. Both are prose judgements, which is review's job per this
    module's docstring; and pinning them would make a re-wording of the surrounding paragraph
    fail a test that is supposed to hold the RULE.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select
    exactly 1 test, SKILL.md restored from a COPY — never `git checkout --`, since this card's
    edits are uncommitted and a git restore would delete the subject under test): control PASS;
    delete the `PLAYWRIGHT_MCP_ISOLATED` sentence while leaving the surrounding prose intact ->
    FAIL; keep the variable but replace "в блоке `env` файла `.claude/settings.json`" with a
    vaguer "somewhere in the project config" -> FAIL, which is the round that proves the WHERE
    clause is pinned and not just the twice-occurring path; drop the profile formula -> FAIL;
    drop the "different repositories never collide" claim -> FAIL; replace `"true"` with
    `"yes"` -> FAIL; rename the bullet's opening words so the slice cannot find it -> FAIL
    loudly from the slicer; re-wrap the paragraph ACROSS two of the pinned phrases and rewrite
    the cost sentence -> PASS, by design (`_flat` is what makes a reflow a non-event)."""
    flat = _flat(_rule_boundary_bullet(_skill_text()))
    assert "`mcp-<канал>-<sha256(корень воркспейса)[:7]>`" in flat, \
        "the bullet no longer derives the browser profile from the workspace root — the " \
        "scope claim below it becomes an assertion the reader has no reason to believe"
    assert "РАЗНЫЕ репозитории не сталкиваются никогда" in flat, \
        "the bullet no longer says different workspace roots never collide — an agent will " \
        "read every unrelated browser failure as this one"
    assert '`PLAYWRIGHT_MCP_ISOLATED` = `"true"`' in flat, \
        "the bullet no longer names the fix with its VALUE — envToBoolean accepts only " \
        '"true"/"1" and IGNORES anything else, so the value is the fix, not decoration'
    assert "в блоке `env` файла `.claude/settings.json`" in flat, \
        "the bullet no longer says WHERE the variable goes (the `env` block of a project " \
        "`.claude/settings.json`) — the bare path also occurs in the sentence after it"


def test_the_cross_session_boundary_forecloses_the_storage_state_non_fix():
    """VMCP-113 (585): the bullet above names a fix AND its cost, and the cost has an obvious,
    upstream-documented-looking remedy that does not work. This pins the foreclosure.

    The bullet ends by telling an agent the price of `--isolated`: an in-memory profile, so
    browser logins stop surviving a restart. Upstream's README then describes `--storage-state`
    (env: `PLAYWRIGHT_MCP_STORAGE_STATE`) as the way to load cookies and localStorage INTO an
    isolated context — which reads exactly like the missing half, and is how this card came to
    be filed in the first place. Measured on the installed 0.0.78, it is half true and the
    wrong half: the file IS read when the browser context is created (cookies restored,
    confirmed by what the browser then sent to the origin), and it is NEVER written — after a
    login, `browser_close` and a clean shutdown the file was byte-identical, and the next
    session read back the seed rather than the login. A path to a not-yet-existing file makes
    EVERY `browser_*` call fail outright.

    Why a rulebook clause and not just a repo note: this bullet is the one place that tells an
    agent working in SOMEONE ELSE'S project what to do about a cross-session browser collision,
    and it already instructs it to report rather than edit. An agent that reads only "logins no
    longer persist" has been handed a problem with a plausible published solution, and the
    self-healing `stable` copy puts this text in front of every consumer with no review gate
    (see this module's docstring) — so the "do not propose it" has to travel with the cost.

    Pinned as the INSTRUCTION plus the MEASUREMENT that justifies it, never the variable name:
    the name alone would stay green through a rewrite that mentioned the variable while
    dropping the verdict, and could equally be satisfied by some future paragraph elsewhere.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select
    exactly 1 test, SKILL.md restored from a COPY — never `git checkout --`, since the subject
    is uncommitted while the card is in Build): control PASS; delete the whole clause while
    ADDING a mention of `PLAYWRIGHT_MCP_STORAGE_STATE` in a different section of the file ->
    FAIL, the round that proves this is not a keyword grep; delete only "не предлагай его как
    починку", keeping the measurement -> FAIL; delete only the never-written measurement,
    keeping the instruction -> FAIL; soften "НЕ ПИШЕТСЯ обратно НИКОГДА" to "пишется редко"
    -> FAIL; re-wrap the paragraph across every pinned phrase -> PASS."""
    flat = _flat(_rule_boundary_bullet(_skill_text()))
    assert "`PLAYWRIGHT_MCP_STORAGE_STATE` эту цену НЕ отменяет" in flat, \
        "the bullet states the cost of `--isolated` (logins stop persisting) without the " \
        "measured verdict on the remedy upstream's README appears to offer for it — the " \
        "reader is left one search away from re-deriving tracker #585"
    assert "не предлагай его как починку" in flat, \
        "the bullet no longer INSTRUCTS an agent not to propose PLAYWRIGHT_MCP_STORAGE_STATE " \
        "as the fix — and this is the bullet an agent reads while standing in someone else's " \
        "project, where a confident wrong suggestion is the whole risk"
    assert "НЕ ПИШЕТСЯ обратно НИКОГДА" in flat, \
        "the bullet no longer says WHY the remedy is not one (the file is only ever read, " \
        "never written back, so a login does not reach the next session). An instruction " \
        "without its reason is the first thing a later agent overrules"


def _claude_workspace_bullet(text: str) -> str:
    """CLAUDE.md's `workspace_cmd.py` architecture bullet — where the refusal-channel split lives.

    Scoped to the one bullet for the reason every slicer in this module records: `code`,
    `--release`, `--gc` and `exit 1` all occur elsewhere in CLAUDE.md (the `claimable_cmd.py`
    bullet is entirely about a JSON line and an exit-code split; the dogfood section drives
    `workspace <id>`), so a whole-file scan could not tell "the split is still stated HERE, where
    an author editing this module will meet it" from "those tokens survive somewhere in the file".

    The end anchor is the next TOP-LEVEL bullet (`\\n- \\``), not the name of the bullet that
    happens to follow today: continuation lines are indented two spaces, so only a real sibling
    bullet can end the slice, and reordering the architecture list cannot silently widen it.
    """
    start = text.find("- `src/vikunja_mcp/workspace_cmd.py`")
    assert start != -1, (
        "CLAUDE.md no longer opens its `workspace_cmd.py` bullet where this pin can find it. If "
        "the bullet was legitimately renamed, move this anchor — do not delete the check"
    )
    end = text.find("\n- `", start + 1)
    assert end != -1, "the workspace bullet no longer ends where the next architecture bullet begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), \
        "the workspace slice is not a proper subset of CLAUDE.md"
    assert "claimable_cmd.py" not in bullet, "the slice swallowed the bullet BEFORE it"
    assert "process rules for agents" not in bullet, "the slice swallowed the bullet AFTER it"
    return bullet


# CANDIDATE shape only — "every … refusal … code" inside one sentence. Whether a candidate is a
# VIOLATION is decided by `_unscoped_code_universal` below, which reads the scope window; this
# pattern on its own says nothing about scoping, and must not be used as if it did. The `code`
# clause is what makes it usable at all — MEASURED, the correct bullet says "every refusal" twice
# on purpose (once quoted, to forbid the phrase; once as "on create every refusal has the same
# answer", which is the scoped truth), so a bare every/refusal scan would be red on the text it is
# meant to bless. The quantifier is a SET, not just "every" — MEASURED on the shipped bullet:
# with `\bevery\b` alone, adding "Each refusal carries a machine-readable `code`." beside the
# intact attribution PASSED, i.e. one synonym re-generalised straight past the pin. `[^.!?]` keeps
# every part of a candidate inside one sentence.
_CODE_UNIVERSAL_CANDIDATE = re.compile(
    r"(?i)\b(?:every|each|all|any)\b[^.!?]{0,32}?\b(?P<noun>refusals?)\b[^.!?]{0,72}?\bcode\b"
)
# Names one of the two channels, i.e. narrows what a quantifier ranges over. The release side is
# required in its FLAG spelling (`--release`/`--gc`), so prose that merely contains the word
# "release" cannot bless itself; a bare `create` DOES count, because the bullet legitimately
# quantifies over create refusals ("on create every refusal has the same answer").
_NAMES_A_CHANNEL = re.compile(r"(?i)--release|--gc|\bcreate\b")
# A `.`/`!`/`?` that actually ENDS a sentence: followed by whitespace or the end of the text, and
# not the dot of a one-letter abbreviation (`i.e.`, `e.g.`). Both halves are load-bearing and were
# measured SEPARATELY — see `_unscoped_code_universal`. This primitive is where the previous round
# went wrong: it located the sentence start with a bare `rfind(".")`, which counts the dot inside
# `SKILL.md`, so the window below began mid-clause and flagged true, correctly-scoped prose.
_SENTENCE_END = re.compile(r"(?<![.\s][A-Za-z])[.!?](?=\s|$)")
# the same claim, correctly scoped: the `code` is attributed to the release/gc channel
_SCOPED_CODE_CLAIM = re.compile(r"`--release`/`--gc`[^.!?]{0,160}?\bcode\b")


def _sentence_start(flat: str, pos: int) -> int:
    """Index just past the last real sentence terminator before `pos` (0 when there is none).

    Deliberately NOT `_SENTENCE_END.finditer(flat, 0, pos)`: an `endpos` makes `$` match there, so
    a dot sitting immediately before the candidate would count as a terminator on the strength of a
    boundary this function invented. Scanning the whole string and stopping evaluates every
    lookahead against the real neighbouring character.
    """
    start = 0
    for terminator in _SENTENCE_END.finditer(flat):
        if terminator.start() >= pos:
            break
        start = terminator.end()
    return start


def _unscoped_code_universal(flat: str):
    """The first `code` universal in `flat` that scopes itself to NEITHER refusal channel.

    A candidate is scoped when its SCOPE WINDOW — from the start of its sentence through the end of
    the `code` clause that closes the candidate — names a channel. All three legitimate shapes land
    inside that window, which is why it has those two edges and not narrower ones:
      * "Every `--release`/`--gc` refusal …" narrows at the noun phrase (this is the `gap` the
        original pattern captured and never read);
      * "on create every refusal …" narrows at the clause BEFORE the quantifier — so the window
        must extend LEFT past the quantifier, not merely cover the gap;
      * "Every refusal on the `--release`/`--gc` path carries a `code`" narrows AFTER the noun — so
        the window must also extend RIGHT, through the `code` clause.

    WHY THE RIGHT EDGE IS THERE, correcting a claim this function used to make. It previously
    stopped at the noun, justified by "a tail-inclusive window would bless the very sentence 580
    deleted". MEASURED, that is false as stated: the deleted sentence is "Every refusal carries a
    machine-readable `code`, and `--gc` GRADES them into two lists", and its `--gc` sits AFTER the
    word `code`, i.e. OUTSIDE a window that ends there — a tail-up-to-`code` window still FLAGS it.
    Only a window widened to the SENTENCE END blesses it, and that is the boundary the earlier
    measurement actually compared against. So the right edge costs nothing (the deleted sentence,
    the quantifier-synonym round and every other round are unchanged by it) and buys the
    post-nominal shape above, which the noun boundary rejected as a false positive.

    WHY THE LEFT EDGE NEEDS A REAL TERMINATOR. `_SENTENCE_END` replaces the bare `rfind(".")` this
    used to do, and both of its clauses were measured on their own, on prose whose TAIL names no
    channel so that only the left edge can rescue it:
      * `(?=\\s|$)` — "On create (see `SKILL.md`) every refusal gets the same treatment, so adding
        a machine-readable `code` would be pointless." True, scoped, and flagged under `rfind`,
        because the dot in `SKILL.md` cut the lead off before the word "create". That is the same
        accident that used to make this bullet's own create clause pass, so the fix had made a
        known-broken primitive load-bearing.
      * the abbreviation lookbehind — the same sentence with "i.e." in place of the code span is
        flagged even WITH `(?=\\s|$)`, because "i.e." really is a dot followed by a space. Left
        unhandled it looks fixed by accident: the reported wording of that case happens to say
        "create-side" in its tail, so the right edge rescues it and the false positive only
        resurfaces on the next rewording.

    NOT A PARSER, and here is everything it does not catch. Bounds, so a later reader does not
    trust it further than it goes:
      * a channel named ANYWHERE before `code` blesses the rest of the sentence, including a
        sentence that scopes itself and then over-generalises anyway ("a create refusal is
        `{\"error\"}` + exit 1, and every refusal carries a machine-readable `code`");
      * the claim split across two sentences ("Every refusal is uniform. Each one carries a
        machine-readable `code`.") — each half is harmless alone;
      * the quantifier AFTER the noun ("A refusal, every single one, carries a `code`");
      * the 32/72-character caps in the candidate pattern, which keep it inside one clause: a long
        qualifier that pushes `code` past 72 characters walks past ("Every refusal, whichever
        channel produced it and whatever the underlying reason turns out to be, carries a
        machine-readable `code`.");
      * a synonym for the NOUN ("Every rejection/failure carries a machine-readable `code`") and
        quantifier-free phrasings ("Refusals carry a `code`", "Both refusal channels carry a
        `code`");
      * in the other direction it is deliberately strict about the release side's spelling — the
        FLAG form is required, so "Every release/gc refusal …" is flagged, as is "On creation every
        refusal …"; and a sentence ending in a one-letter word ("… option A. Every refusal …")
        reads as one sentence too far left, the abbreviation lookbehind's own price.
    So: the drift this catches is the measured one — an unqualified universal — in every
    QUANTIFIER wording of it, which is the axis that actually re-generalised in review. The rest is
    review's job, and review is what caught every bound listed above.
    """
    for match in _CODE_UNIVERSAL_CANDIDATE.finditer(flat):
        window = flat[_sentence_start(flat, match.start()):match.end()]
        if not _NAMES_A_CHANNEL.search(window):
            return match
    return None


# Prose whose verdict is FIXED, so the window above is exercised by a GREEN run and not only under
# mutation. MEASURED and load-bearing: the shipped bullet yields ZERO candidates (the dot in
# `SKILL.md` cuts its create clause before `code` reaches the pattern), so the pin below would pass
# whatever this function did — every defect found in review so far was invisible until someone
# reflowed that clause, which is precisely the edit this pin exists to police. Every window
# variant considered and rejected disagrees with at least one row here: sentence-start..noun on
# three, gap-only on four, gap+tail on two, `rfind` for the left edge on two, no-abbreviation-guard
# on one, sentence-wide on three.
_SCOPE_WINDOW_EXAMPLES = (
    # (what the row is for, prose, is it a violation)
    ("the universal 580 deleted — a channel named only AFTER `code` does not scope it",
     "Every refusal carries a machine-readable `code`, and `--gc` GRADES them into two lists.",
     True),
    ("a quantifier synonym re-generalises just as well",
     "Each refusal carries a machine-readable `code`.", True),
    ("a channel named in the PREVIOUS sentence does not carry over",
     "`--gc` grades the codes it gets. Every refusal carries a machine-readable `code`.", True),
    ("scoped at the noun phrase — the wording this pin's failure message dictates",
     "Every `--release`/`--gc` refusal carries a machine-readable `code`.", False),
    ("scoped AFTER the noun — ordinary English, and why the window keeps its tail",
     "Every refusal on the `--release`/`--gc` path carries a machine-readable `code`.", False),
    ("scoped by the clause BEFORE the quantifier — why the window extends left",
     "On create every refusal has the same answer, so a `code` there would be a public value.",
     False),
    ("…and that clause survives a dot inside a code span",
     "On create (see `SKILL.md`) every refusal gets the same treatment, so adding a "
     "machine-readable `code` would be pointless.", False),
    ("…and an abbreviation",
     "On create, i.e. when the tree cannot be made, every refusal gets the same treatment, so "
     "adding a machine-readable `code` would be pointless.", False),
)


def test_the_code_universal_scope_window_agrees_with_its_worked_examples():
    """VMCP-110 (580): the scope window of `_unscoped_code_universal`, pinned on fixed prose.

    This exists because the pin below it is CURRENTLY VACUOUS as a check of the window: measured,
    CLAUDE.md's workspace bullet produces zero candidates, so the window never runs on a green
    suite and two rounds of review found false positives in it that no run would ever have shown.
    A prose pin that only exercises its own predicate under mutation is a predicate nobody is
    testing; these rows run it every time.

    The rows are not decoration — each is a wording that a real author might write about THIS
    module, and between them they discriminate every window boundary that was proposed and rejected
    while getting this right (see the comment on `_SCOPE_WINDOW_EXAMPLES` for which variant each
    row kills). They pin the PREDICATE, not CLAUDE.md; the document itself is pinned below.
    """
    for reason, prose, is_violation in _SCOPE_WINDOW_EXAMPLES:
        violation = _unscoped_code_universal(_flat(prose))
        assert (violation is not None) is is_violation, (
            f"the `code` universal's scope window disagrees with a worked example ({reason}): "
            f"{prose!r} should {'be flagged' if is_violation else 'pass'}, and it "
            f"{'passed' if violation is None else 'was flagged'}. If the window was changed on "
            f"purpose, re-measure this table — do not delete the row that disagrees"
        )


def test_the_claude_md_workspace_bullet_keeps_the_code_claim_scoped():
    """VMCP-110 (580): CLAUDE.md's workspace bullet used to state "Every refusal carries a
    machine-readable `code`" — a universal that is FALSE of the create channel, which refuses by
    raising and comes back as `{"error": …}` + exit 1 with no `code` at all. The behaviour is pinned
    in tests/unit/test_workspace_cmd.py::test_the_two_refusal_channels_are_not_interchangeable; this
    is the PROSE half, and it is the half that actually propagated: two later documents copied the
    universal out of this bullet (workspace_cmd.py's own CODE_* header and the plan doc), and a
    third would have.

    Why prose needs its own net here rather than review alone: this bullet is the standing brief
    every agent working in this repo reads before touching the module, so a false universal in it
    does not just sit there — it gets IMPLEMENTED. The plausible "fix" for a reader who believes it
    is to add a `code` to the create path, which is precisely the change 580 weighed and rejected
    (a code exists to feed `_keep_is_expected`, the only grader in the package; on create every
    refusal has the same answer, and the catch-all covers an OPEN set so a create-side code could
    only ever be present-SOMETIMES — worse to parse than absent-always).

    Three clauses are pinned, deliberately as ANCHORS rather than as sentences, because the failure
    mode is not deletion but re-wording that quietly re-generalises:
      * NO unscoped universal survives — the claim is caught by its SHAPE ("every … refusal …
        code" inside one sentence) whenever nothing from that sentence's start through its `code`
        clause names a channel, so a reflow, a synonym for "carries" or a synonym for "every"
        cannot walk past it. What counts as scoping, where the window's two edges are and what it
        does NOT catch are all in `_unscoped_code_universal`; the window itself is exercised on a
        green run by test_the_code_universal_scope_window_agrees_with_its_worked_examples, since
        this bullet yields no candidates of its own.
      * the CREATE channel is stated at all: its literal payload token and its exit code. A bullet
        that merely stops saying "every" would satisfy the first clause while leaving the reader
        with no idea what a create refusal looks like — which is the state that produced the drift.
      * the `code` is ATTRIBUTED to `--release`/`--gc`, not left floating. `--release`/`--gc` as a
        PAIR occurs exactly once in this file (measured), so this cannot pass on an unrelated
        mention.

    NOT pinned: the justification prose (the open-set argument, the "no consumer" argument). Those
    are review's job per this module's docstring, and pinning them would turn a legitimate
    re-wording of the rationale into a red test.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, CLAUDE.md restored from a COPY — never `git checkout --`): control PASS; restore the old
    "Every refusal carries a machine-readable `code`, and `--gc` GRADES them…" sentence in place of
    the scoped one -> FAIL, quoting the match; delete the whole create-channel paragraph -> FAIL on
    the `{"error"` clause; rename the bullet so the slicer's anchor misses -> FAIL loudly from the
    slicer, not a vacuous pass; widen the end anchor to the next `##` -> FAIL from the swallow
    guard.

    And mutation-checked in the other direction too, twice over, because BOTH earlier versions of
    this pin were RED ON CORRECT PROSE. The first captured the gap and never read it, so "scoped"
    was never actually tested; the second read a window that stopped at the noun and located that
    window's left edge with a bare `rfind(".")`. Five cases measured, on this bullet: the wording
    the failure message itself dictates ("Every `--release`/`--gc` refusal carries a
    machine-readable `code`, and `--gc` GRADES them…", create paragraph intact) -> PASS, where it
    used to fail and leave an author who OBEYED the error message with no way out but to weaken the
    pin; the bullet's own create clause reworded without its "SKILL.md" citation -> PASS; a create
    clause that KEEPS a citation ("on create (see `SKILL.md`) every refusal …") or uses an
    abbreviation ("on create, i.e. …") -> PASS, where the dot in each used to truncate the window's
    lead; post-nominal scoping ("Every refusal on the `--release`/`--gc` path carries a
    machine-readable `code`.") -> PASS, where the noun boundary rejected ordinary scoped English;
    and "Each refusal carries a machine-readable `code`." added beside the intact attribution ->
    FAIL, where the "every"-only pattern let it through.

    The vacuity question was then asked PROPERLY, because "the slicer can't miss" is not the same
    claim as "a miss can't pass": with the slice replaced by the WHOLE file AND the subset/swallow
    guards deleted AND the create paragraph gone, this test PASSES. That is not hypothetical —
    measured in CLAUDE.md, `{"error"` also occurs in the `server.py` bullet and `exit 1` in a later
    section, so both positive clauses have somewhere else to land. The `0 < len(bullet) < len(text)`
    guard is what stands between this test and that vacuous pass; it is load-bearing, not ceremony.
    """
    bullet = _claude_workspace_bullet(_claude_md_text())
    flat = _flat(bullet)

    violation = _unscoped_code_universal(flat)
    # the message is evaluated only when the assert FAILS, so .group() is safe here
    assert violation is None, (
        f"CLAUDE.md's workspace bullet states the UNSCOPED universal again: "
        f"{violation.group(0)!r}. Only a `--release`/`--gc` refusal carries a `code`; a CREATE "
        f'refusal is `{{"error": …}}` + exit 1 and carries none. Name the channel anywhere from '
        f"the start of that sentence through the `code` clause itself — \"every `--release`/`--gc` "
        f'refusal …", "every refusal on the `--release`/`--gc` path carries a `code`", or "on '
        f'create every refusal …" for a claim about the other side. What does NOT count is naming '
        f"it only AFTER `code`: the sentence this pin exists to forbid did exactly that (\"…carries "
        f"a machine-readable `code`, and `--gc` GRADES them…\"). Or, if the CODE really did change, "
        f"change the code and tests/unit/test_workspace_cmd.py's channel pin FIRST, then this prose"
    )
    assert '{"error"' in bullet, (
        'CLAUDE.md\'s workspace bullet no longer shows what a CREATE refusal looks like '
        '(`{"error": …}`). Without it the bullet says only what `--release`/`--gc` do, and the '
        'next reader re-generalises that to both channels — which is how 580 happened'
    )
    assert "exit 1" in flat, (
        "CLAUDE.md's workspace bullet no longer states the create channel's exit code. On create "
        "the EXIT CODE is the whole machine-readable verdict — dropping it is dropping the "
        "contract, not a detail"
    )
    assert _SCOPED_CODE_CLAIM.search(flat), (
        "CLAUDE.md's workspace bullet no longer attributes the machine-readable `code` to the "
        "`--release`/`--gc` channel. An unattributed `code` sentence reads as universal again"
    )


# The two in-repo DESIGN RECORDS of the parallel drain. Both already carry a scoped copy of the
# `code` claim, and both are read by agents arriving from a grep rather than from the top of the
# file — which is the whole reason the claim keeps regrowing here.
_DRAIN_DESIGN_DOCS = (
    "docs/superpowers/specs/2026-07-29-parallel-worktree-drain-design.md",
    "docs/superpowers/plans/2026-07-29-parallel-worktree-drain.md",
)


def _design_doc_flat(relpath: str) -> str:
    """A tracked design document, blockquote-stripped and flattened for the prose predicates.

    Read from the CHECKOUT by path, like `_claude_md_text` and unlike `_skill_text`: these are
    repo documents, not packaged resources, so `importlib.resources` cannot see them.

    The blockquote strip is not cosmetic and was MEASURED, not assumed. Every marker in the design
    record is a `>` block, and `_CODE_UNIVERSAL_CANDIDATE` caps the quantifier→`code` gap at 72
    characters to stay inside one clause; a `> ` leader adds two characters per WRAPPED LINE, which
    spends that budget on punctuation. Constructed the boundary case: "Every refusal <41 chars>
    carries a machine-readable `code` beside the reason", wrapped as a blockquote, is MISSED with
    the leaders left in and FLAGGED with them stripped. So stripping makes this pin strictly
    stricter, and a violation cannot hide behind the wrap it happens to fall on. (On today's text
    both spellings agree — the strip buys nothing yet, which is exactly when it is cheap to add.)
    """
    path = Path(__file__).resolve().parents[2] / relpath
    assert path.is_file(), (
        f"{relpath} is gone from the repo — this pin has nothing to read. If the design record "
        f"was moved or retired, move this path; do not delete the check"
    )
    return _flat(re.sub(r"(?m)^[ \t]*>+[ \t]?", " ", path.read_text(encoding="utf-8")))


def test_the_drain_design_records_keep_the_code_claim_scoped():
    """VMCP-122 (597): the same `code` universal, in the DESIGN RECORDS — its fourth regrowth.

    580 scoped this claim in three places and pinned exactly one of them (CLAUDE.md, above). The
    seed then turned up a FOURTH time, in the spec doc's `--release` marker: "Every refusal now
    also carries a machine-readable `code`". That copy was *contextually* scoped — its host
    paragraph is `--release` and its opening clause says "the POLICY above" — which is precisely
    why it survived three sweeps that were looking for the obvious shape.

    WHY IT WAS STILL WORTH FIXING, and therefore worth pinning: MEASURED, `git grep
    machine-readable` over that document prints the marker's line beside §3's "no machine-readable
    key at all", and NEITHER line names a channel. A reader who lands out of context — a grep, a
    diff hunk, a deep link — sees one document flatly contradicting itself and has only the
    document's own banner to break the tie, which tells them a MARKED passage is the stronger
    claim. The marker was the wrong branch to trust.

    WHY THE WHOLE FILE AND NOT A SLICE. Every other prose pin in this module slices first, because
    a bare token scan cannot tell "the rule is still stated HERE" from "the token survives
    somewhere". This one needs no slice: `_unscoped_code_universal` does not scan for tokens, it
    decides violation-vs-scoped per candidate, so correct prose in the rest of the file is simply
    not a candidate. MEASURED on both documents: the spec doc yields exactly ONE violation before
    the fix and NONE after; the plan doc yields none either way — but NOT because it is scoped,
    which matters enough to have its own paragraph below; and the spec doc's own CORRECT §3
    sentence, "every create-path refusal is codeless", is clean, since `codeless` is not
    `\\bcode\\b`. A slicer here would only add a second thing to drift.

    NOT VACUOUS, and that is measured rather than argued: run this predicate over the spec doc as
    it stood at the parent commit and it FAILS, naming the sentence. Contrast the CLAUDE.md pin
    above, whose bullet yields ZERO candidates on a green run — this one exercises the window on
    real prose in both directions.

    NOT PINNED: anything else in either document. They are design records whose own banner says
    the markers are not exhaustive and that an unmarked passage means "nobody has checked it".
    This pin makes exactly one claim — that neither record restates the `code` payload as a
    universal over both refusal channels — and deliberately leaves the rest free to age, which is
    what a design record is for.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, documents restored from a COPY — never `git checkout --`, since they are uncommitted
    while the card is in Build): control PASS; restore the unscoped "Every refusal now also carries
    a machine-readable `code`" to the spec doc's `--release` marker -> FAIL, quoting the match; the
    same universal added to the PLAN doc -> FAIL naming the plan doc, i.e. the loop really does
    read both; point `_DRAIN_DESIGN_DOCS` at a renamed path -> FAIL loudly from the `is_file`
    guard, not a vacuous pass.

    ONE ROUND CAME BACK GREEN, and it is recorded as a BOUND rather than quietly dropped, because
    it narrows what this pin may be SAID to protect. Re-generalising the plan doc's OWN scoped
    clause — "the `code:` key on every `--release`/`--gc` refusal" back to "every refusal" — does
    NOT fail; nor does restoring 580's EXACT deleted wording there ("the `code:` key on every
    refusal (#516)"). Both yield ZERO CANDIDATES, and the cause is structural, not semantic:
    `_CODE_UNIVERSAL_CANDIDATE` requires quantifier → refusal → `code` IN THAT ORDER, and the plan
    doc puts `code` FIRST. Measured on that sentence — the first `\\bcode\\b` sits 14 characters
    BEFORE the quantifier, and the next one after it is 86 to 105 characters later, past the
    72-character cap as well. The plan doc is invisible to this predicate in both directions.

    SO READ THE COVERAGE HONESTLY. This pin protects the plan doc against a NEW compact universal
    added to it (the round above proves that much) and NOT against its existing clause being
    re-generalised: the plan doc's correctness rests on 580's WORDING, not on this test. The spec
    doc is the file this pin actually guards, and even there it guards ONE SHAPE — an independent
    review of this card measured nine real wordings of the same claim and found this predicate
    flags one of them. See `_unscoped_code_universal`'s own bounds list for the misses it already
    documents. That is why 580's ruling stands unchanged: review catches the rest, and this is a
    net under the copy-paste form of the drift, not a proof that the claim cannot regrow.
    """
    for relpath in _DRAIN_DESIGN_DOCS:
        violation = _unscoped_code_universal(_design_doc_flat(relpath))
        # the message is evaluated only when the assert FAILS, so .group() is safe here
        assert violation is None, (
            f"{relpath} states the `code` payload as an UNSCOPED universal: "
            f"{violation.group(0)!r}. Only a `--release`/`--gc` refusal carries a `code`; a CREATE "
            f'refusal is `{{"error": …}}` + exit 1 and carries none. In a design record this is '
            f"worse than in prose that describes today's code, because a MARKED passage claims "
            f"someone CHECKED it — see this document's own banner. Name the channel anywhere from "
            f"the start of that sentence through the `code` clause itself. Being inside a marker "
            f"attached to the `--release` paragraph does NOT count: that is exactly the copy "
            f"tracker #597 had to fix, and a grep prints the line without its host"
        )


def _reviewer_tree_rule(text: str) -> str:
    """The «Ревьюер, вынеся вердикт, освобождает своё дерево» bullet — the ONLY place a REVIEWER
    reads about its own worktree.

    Sliced, and the slice IS the point of VMCP-104 (563). Both phrases pinned below already occur
    in this file, in the BUILD agent's sections: «не считай, что ты всё ещё стоишь в своём дереве»
    lives in the `call_human` paragraph of the push recipe, and the `no-worktree` refusal is
    explained in the `--release` breakdown. A whole-file substring therefore cannot tell "the
    reviewer is told" from "the build agent is told" — which was exactly the state this card found:
    the rule was stated twice for build and only IMPLIED for review («дерево будет жить, пока
    карточка не уйдёт из Review» — while `needs_work` IS that departure).
    """
    start = text.find("- **Ревьюер, вынеся вердикт, освобождает своё дерево:**")
    assert start != -1, "SKILL.md no longer has a rule about the reviewer's own worktree"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the reviewer's tree rule no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the reviewer-tree slice is not a proper subset of SKILL.md"
    assert "Не завелось" not in bullet, "the slice swallowed the following bullet"
    return bullet


def test_the_reviewers_tree_rule_says_its_own_verdict_can_take_the_directory_away():
    """VMCP-104 (563): a review tree is alive BY ROLE — while the card sits in Review the sweep
    skips it whatever its age — so the reviewer's exposure is exactly its OWN `needs_work`, which
    moves the card to Build and kills the tree that same second. The rulebook stated the standing
    "re-`ensure`, do not assume your cwd survived" rule twice for the BUILD agent and never for the
    reviewer; workspace_cmd's own grace-window comment meanwhile asserts it "is a rule for BOTH
    roles", so the code was documenting a rule the rulebook only half-carried.

    MEASURED on this code before writing the prose (throwaway probe, real git repo + FakeAPI):
    a review tree quiesced past `_REAP_GRACE_SECONDS` with its card in Review -> `{released: [],
    kept: [], expected: []}`; after `approve` -> same, card still in Review; after `needs_work` ->
    the very next sweep returns it in `released` and the directory is gone; `--release --role
    review` on it -> exit 0, `{released: false, code: "no-worktree"}`.

    Pinned on both sides, since SKILL.md self-heals onto every consumer with no review gate:
      * the two board-facing CLAIMS are anchored in workflow.review_task itself — approve must
        keep the card in Review (no `_move` in that branch) and needs_work must take it out. Make
        approve move the card and this rulebook paragraph becomes false; the test fails first.
      * the ORDERING claim ("grace-окно до него даже не доходит") is anchored in gc_workspaces:
        the by-role liveness check has to run BEFORE `_last_activity` is ever consulted.
      * the two PROSE imperatives are pinned inside the slice, not the file, for the reason
        `_reviewer_tree_rule` records.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select exactly
    1 test, SKILL.md restored from a COPY — never `git checkout --`, the edits are uncommitted):
    control PASS; delete the whole «Из Review карточку двигает не только человек» sub-bullet while
    LEAVING both phrases in their build-side homes -> FAIL on the imperative; delete only the
    imperative sentence and keep the rest of the sub-bullet -> FAIL; delete the `no-worktree`
    sub-bullet while the `--release` breakdown still explains that code -> FAIL; make
    `review_task`'s approve branch call `_move` -> FAIL; move the by-role check below
    `_last_activity` in gc_workspaces -> FAIL; re-wrap the paragraph across the pinned phrases ->
    PASS by design (`_flat`)."""
    text = _skill_text()
    bullet = _reviewer_tree_rule(text)
    flat = _flat(bullet)

    review_src = inspect.getsource(workflow.Workflow.review_task)
    approve_at = review_src.index('if verdict == "approve":')
    needs_work_at = review_src.index('[review] NEEDS WORK')
    assert "_move(" not in review_src[approve_at:needs_work_at], \
        "review_task's approve branch now MOVES the card — SKILL.md tells the reviewer the " \
        "tree survives an approve, which would become false"
    assert 'self._move(task_id, "Build")' in review_src[needs_work_at:], \
        "needs_work no longer takes the card out of Review — the whole hazard this rule " \
        "documents (your own verdict kills your tree) would be gone"
    assert "`approve` карточку НЕ двигает" in flat, \
        "the reviewer's rule no longer says approve leaves the card (and the tree) alone"
    assert "needs_work" in flat, \
        "the reviewer's rule no longer names the verdict that takes its own directory away"

    gc_src = inspect.getsource(workspace_cmd.gc_workspaces)
    assert gc_src.index("alive[role]") < gc_src.index("_last_activity("), \
        "gc now consults the grace window BEFORE by-role liveness — SKILL.md tells the " \
        "reviewer the window is never even reached while the card sits in Review"

    assert "не считай, что ты всё ещё стоишь в своём дереве" in flat, \
        "the reviewer is no longer told not to assume its cwd survived the verdict — the " \
        "phrase still occurs in the BUILD agent's sections, which is why this pin is scoped"
    assert "`workspace <id> --role review --at <sha>` заново" in flat, \
        "the reviewer is no longer told HOW to get a directory back (re-ensure it), only that " \
        "it may be gone"


def _degraded_workspace_bullet(text: str) -> str:
    """The «Не завелось — цикл НЕ роняем» bullet — where the pump learns to tell a `workspace`
    FAILURE (exit 1, `error`) from a `--release` that simply declined (exit 0, `released: false`).

    Sliced for the same reason as `_reviewer_tree_rule`: every refusal code it contrasts is also
    explained, at length, in the `--release` breakdown further down, so a file-wide substring
    cannot tell "this bullet still distinguishes them" from "the words exist somewhere"."""
    start = text.find("- **Не завелось — цикл НЕ роняем.**")
    assert start != -1, \
        "SKILL.md no longer tells the pump that a failed `workspace` is not a reason to stop"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the «Не завелось» bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the «Не завелось» slice is not a proper subset of SKILL.md"
    assert "Ревьюер, вынеся вердикт" not in bullet, "the slice swallowed the preceding bullet"
    return bullet


def test_the_released_false_shorthand_never_teaches_only_the_protective_reading():
    """VMCP-104 (563), the second half: `released: false` is taught in TWO places, and the short
    one used to collapse it to a single meaning — «это не сбой инструмента, а „у тебя осталась
    несохранённая работа"». That reading is wrong for `no-worktree`, whose meaning is the
    opposite (the tree is already gone and nothing is owed), and `no-worktree` is precisely the
    reviewer's routine outcome after a `needs_work` that outlived the grace window.

    Both sites are pinned to name the code, anchored on the CONSTANT so a re-value fails here
    rather than silently in the field. Scoped to each bullet: `no-worktree` appears in the
    `--release` breakdown too, so a file-wide substring would stay green with either shorthand
    collapsed back.

    MUTATION-CHECKED (same protocol as above): control PASS; restore the old one-meaning wording
    in «Не завелось» while the breakdown still explains all three codes -> FAIL; delete the
    reviewer's third-reading sub-bullet -> FAIL; re-value CODE_NO_WORKTREE -> FAIL on both."""
    text = _skill_text()
    for name, section in (
        ("«Не завелось — цикл НЕ роняем»", _degraded_workspace_bullet(text)),
        ("the reviewer's tree rule", _reviewer_tree_rule(text)),
    ):
        assert workspace_cmd.CODE_NO_WORKTREE in section, \
            f"{name} teaches `released: false` without its third reading — an agent whose tree " \
            f"is already gone reads a routine success as a protective refusal"
    degraded = _flat(_degraded_workspace_bullet(text))
    for code in (workspace_cmd.CODE_DIRTY, workspace_cmd.CODE_UNPUSHED):
        assert code in degraded, \
            f"the shorthand no longer says which codes DO mean unsaved work ({code}) — " \
            f"'read the `code`' without the codes sends the reader to the wrong half"


def _wip_saturated_bullet(text: str) -> str:
    """The «`wip_saturated: true` — это НЕ пустая очередь» bullet — sliced to that one item.

    Scoped like `_drain_width_section` / `_exclude_completeness_bullet`, for the same measured
    reason: `wip_saturated`, `ScheduleWakeup` and «пустая очередь» each occur many times in this
    rulebook, so a whole-file substring could not tell "the rule is still stated where the pump
    reads the payload" from "the words survive somewhere else"."""
    start = text.find("\n- **`wip_saturated: true` — это НЕ пустая очередь")
    assert start != -1, \
        "SKILL.md no longer tells the pump that wip_saturated is not an empty queue"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the wip_saturated bullet no longer ends where the next one begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the wip_saturated slice is not a proper subset of SKILL.md"
    return bullet


# The rulebook QUOTES this rendered phrase verbatim, numbers and all, and calls it the only place
# in the payload where both numbers stand side by side in prose. Owned here as a literal and
# asserted against BOTH sides below, so the three copies (this test, SKILL.md, workflow.py) cannot
# drift apart in any direction — and none of the three can be edited into agreement with itself.
_SATURATED_NUMBERS_4_OF_3 = "all 3 WIP slot(s) are busy (4 active)"


def test_the_rulebook_quotes_the_saturated_message_and_the_payload_still_renders_it():
    """SKILL.md makes two promises about the `wip_saturated` payload; nothing held either.

    #586 measured the gap on both. (1) The message interpolates `limit` and `active`, and the
    rulebook quotes the RENDERED pair — «all 3 WIP slot(s) are busy (4 active)» — as the one place
    a pump can see an overshoot in prose (`free` saturates at 0 and cannot show it). Swapping the
    two interpolations, so the payload reads "all 4 … (3 active)" and inverts the diagnosis, passed
    the whole suite: 596 passed. (2) The same bullet's operative instruction is "do NOT
    ScheduleWakeup — this is not an empty queue"; replacing the note with the string "no work right
    now" also passed, 596 passed. The message's only guard was `"empty" not in message`
    (test_workflow_wip), which pins a hazard word rather than a value, and the note had none at all.

    Pinned as the VALUES and the IMPERATIVE, not as prose: the rest of both strings stays free to be
    reworded, which is why this is not one of the byte-exact pins #586 deliberately refused to add
    to the nine remaining static payload strings. Reaching wip_saturated at 4-of-3 also needs a
    COMPLETE `exclude` — the resume branch is offered first — so this env is the exact state the
    rulebook describes, not a convenient one."""
    text = _skill_text()
    bullet = _wip_saturated_bullet(text)
    assert _SATURATED_NUMBERS_4_OF_3 in _flat(bullet), \
        "SKILL.md no longer quotes the rendered number pair it calls the payload's only prose view"
    assert "ScheduleWakeup" in bullet and "НЕ уступай ход" in bullet, \
        "the rulebook no longer forbids idling the tick on a saturated board"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3, wip_limit=3)

    def claim_fresh(title):
        task_id = api.add_task(title, "Queue")["id"]
        wf.claim(task_id)
        return task_id

    bounced = claim_fresh("reviewed, then bounced")
    wf.advance(bounced, to="build", spec="…")
    wf.advance(bounced, to="review", worklog="…", evidence="0" * 40)
    for n in range(3):                       # the pump refills the freed slot, as the tick does
        claim_fresh(f"held {n}")
    wf.review_task(bounced, verdict="needs_work", report="not yet")   # around the gate -> 4 of 3

    res = wf.next_task(exclude=wf.active_task_ids())
    assert res["task"] is None and res["wip_saturated"] is True, \
        "a COMPLETE exclude no longer produces the saturation signal the rulebook promises"
    assert res["wip"] == {"active": 4, "limit": 3, "free": 0}, \
        "precondition: this must be the 4-of-3 state SKILL.md spells the quote with"

    assert _SATURATED_NUMBERS_4_OF_3 in res["message"], res["message"]
    assert "ScheduleWakeup" in res["note"] and "Do NOT claim" in res["note"], res["note"]


def _stuck_section(text: str) -> str:
    """The «Застрял? Выход зависит от РОЛИ» section — where a stuck agent is told which door is
    its own.

    Sliced like `_freshness_section` / `_wip_saturated_bullet`, and here the slicing is MEASURED,
    not stylistic: `review_task`, `needs_work` and `file_task` each occur several times elsewhere
    in this rulebook (the review sections, the push recipe's Review-stage escalation, the
    independent-review rules). A whole-file substring therefore stays GREEN with the reviewer's
    bullet deleted outright — it cannot tell "the reviewer is told where to go" from "the words
    exist somewhere". Verified by running exactly that mutation both ways."""
    start = text.find("\n## Застрял? Выход зависит от РОЛИ\n")
    assert start != -1, "SKILL.md no longer has the section that routes a stuck agent by role"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the stuck section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the stuck slice is not a proper subset of SKILL.md"
    return section


def test_the_rulebook_routes_a_stuck_REVIEWER_to_the_only_door_that_is_open_to_it():
    """#590: the rulebook offered a stuck agent exactly two doors, `call_human` and `return_task`,
    and a REVIEWER has neither. It works exclusively from Review, where `call_human` is gated to
    Design/Build and (as of this card) `return_task` refuses too — and in multi-identity the card
    isn't even theirs. Measured before the gate landed: `return_task` from Review passed with no
    refusal and walked reviewed work to Backlog, unassigned and labeled `blocked` (the journal and
    any `reviewed` label survive — it is the STAGE and the assignment that are walked back, and
    `next_task` stops offering the card). That was the rulebook's own "stuck?" advice quietly
    resetting the pipeline state it never mentioned.

    So the prose now names the reviewer's ONE channel — `review_task(verdict='needs_work')`, which
    hands the card back to its implementer in Build, who owns it and may `call_human` from there —
    plus `file_task` for a finding outside the card's slice.

    Pinned against the TOOLS, not just as words: both refusals are exercised through the real
    Workflow below, so if a future change reopens either door the rulebook's "оба выхода
    нерабочие" cannot keep shipping to every consumer as truth. (The reverse drift — code gates
    that the prose stops mentioning — is what the sliced substring half catches.)

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; delete the reviewer bullet from the section while LEAVING `review_task` /
    `needs_work` / `file_task` everywhere else in the file -> FAIL (and the whole-file substring
    this slice replaces was measured GREEN on that same mutation); drop return_task's Review gate
    -> FAIL; drop call_human's Review pointer -> FAIL; rename the heading -> FAIL loudly."""
    text = _skill_text()
    section = _stuck_section(text)

    # the prose: the reviewer's door, and the two it is NOT
    assert "review_task" in section and "needs_work" in section, \
        "the stuck section no longer names the reviewer's only working channel"
    assert "file_task" in section, \
        "the stuck section no longer routes an out-of-slice finding to file_task"
    assert "return_task" in section and "call_human" in section, \
        "the stuck section no longer contrasts the reviewer's door with the implementer's two"

    # the code: both doors really are shut from Review, which is what the prose asserts
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    card = api.add_task("under review", "Review", assignee=api.me_user)

    with pytest.raises(workflow.WorkflowError) as returned:
        wf.return_task(card["id"], reason="не понимаю задачу")
    assert "review_task" in str(returned.value), \
        "SKILL.md says return_task refuses from Review and points at review_task; it no longer does"
    assert api.stage_of(card["id"]) == "Review", "the refusal moved the card anyway"

    with pytest.raises(workflow.WorkflowError) as called:
        wf.call_human(card["id"], question="какой из двух вариантов правильный?")
    assert "review_task" in str(called.value), \
        "SKILL.md says call_human refuses from Review and points at review_task; it no longer does"

    # ...and the door the prose sends them to is genuinely open
    assert wf.review_task(
        card["id"], verdict="needs_work", report="вопрос человеку: какой из двух вариантов?"
    )["moved_to"] == "Build"


def _return_task_bullet(text: str) -> str:
    """The `return_task` bullet inside the stuck section — where its shut stages are spelled out.

    Sliced to the BULLET, not the section, and that is measured too: «Done» occurs in this same
    section's reviewer sub-bullet («карточка … ждущей человеческого Done») and `file_task` occurs
    in its last sub-bullet, so even a SECTION-wide substring stays green with this bullet's Done
    sentence deleted. The bullet is the smallest slice that can tell "return_task's shut stages are
    written down" from "those words exist nearby"."""
    start = text.find("\n- **`return_task`** — внешняя блокировка")
    assert start != -1, "SKILL.md no longer describes return_task in the stuck section"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the return_task bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the return_task slice is not a proper subset of SKILL.md"
    assert "У РЕВЬЮЕРА" not in bullet, "the slice swallowed the reviewer's bullet"
    return bullet


def test_the_rulebook_names_BOTH_stages_return_task_refuses_from():
    """#626: after #590 this bullet said «Из Review он ОТКАЗЫВАЕТ … Из остальных стадий он
    по-прежнему работает» — a sentence that POSITIVELY described the Done path as normal, and it
    self-heals onto every consumer. Measured at the time: `return_task` really did walk a card out
    of Done (the transition CLAUDE.md calls human-only) — one of SEVERAL agent tools that could,
    never the only one, so the rulebook was advertising an agent bypass of that invariant as
    supported. `decompose` was the other known one — measured on the same card, untouched by THIS
    diff, gated separately by #649 — so shutting this door did not shut them all, and the class
    (no single rule anywhere) is still open by construction.

    Both halves are pinned against the TOOL, not just as words, because prose and gate drifting
    apart is the failure this card is about: the gate must refuse from Done AND the bullet must say
    so, and the five stages the bullet still promises must genuinely stay open.

    Token presence is NOT enough here, and that is measured rather than assumed: an earlier version
    of this pin asserted only that «Review», «Done» and `file_task` occur in the bullet, and a
    mutant that INVERTED the rule — «отказывает только из Review, а из Done … работает штатно» —
    kept all three words and sailed through GREEN. That mutant is precisely the sentence this card
    exists to delete, so the pin now asserts the RULE (the enumeration of shut stages, verbatim)
    and derives the open list from `workflow.STAGES`, which also ties the prose to the code: add a
    stage, or shut another one, and this fails until the rulebook is updated too. Reword the bullet
    freely — but the rule has to still be spelled out, and then this string moves with it.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; delete the Done sentence from the bullet while leaving «Done» and `file_task`
    elsewhere in the section -> FAIL (and a SECTION-wide substring was measured GREEN on that same
    mutation); INVERT the rule keeping every token -> FAIL; drop `Your Call` from the open list ->
    FAIL; drop return_task's Done gate -> FAIL; drop its Review gate -> FAIL."""
    text = _skill_text()
    bullet = _return_task_bullet(text)

    # the RULE, not its vocabulary: which stages are shut, spelled out
    assert "ОТКАЗЫВАЕТ из ДВУХ стадий — Review и Done" in bullet, \
        "the bullet no longer states WHICH stages return_task refuses from (#590 Review, #626 Done)"
    assert "file_task" in bullet, \
        "the bullet no longer routes unusable Done work to file_task, the one channel left"
    # ...and the open list must be exactly the complement, straight out of the code
    for stage in workflow.STAGES:
        if stage in ("Review", "Done"):
            continue
        assert stage in bullet, \
            f"the bullet promises the OTHER stages keep working but never names {stage!r}"

    # the code: both doors really are shut
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)

    accepted = api.add_task("accepted by a human", "Done", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError) as done:
        wf.return_task(accepted["id"], reason="внешний блок")
    assert "file_task" in str(done.value), \
        "SKILL.md says return_task refuses from Done and points at file_task; it no longer does"
    assert api.stage_of(accepted["id"]) == "Done", "the refusal walked the accepted card back anyway"

    under_review = api.add_task("under review", "Review", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError):
        wf.return_task(under_review["id"], reason="внешний блок")
    assert api.stage_of(under_review["id"]) == "Review"

    # ...and the five stages the same bullet still promises are genuinely open
    for stage in ("Backlog", "Queue", "Design", "Build", "Your Call"):
        card = api.add_task(f"blocked in {stage}", stage, assignee=api.me_user)
        assert wf.return_task(card["id"], reason="чужой сервис лежит")["moved_to"] == "Backlog", \
            f"the bullet promises return_task still works from {stage}; it does not"


def _decompose_bullet(text: str) -> str:
    """The `decompose` bullet in «Декомпозиция и файлинг находок» — where an agent looks the tool
    up, and therefore where its shut stage has to be written.

    Sliced to the BULLET rather than the section, and the difference was measured on the mutant
    that deletes this bullet's Done sentences outright: at SECTION scope «Done» survives in the
    epic-lifecycle bullet («весь набор … в Done уводит ЧЕЛОВЕК») and `file_task` survives in its
    own bullet a few lines below, so a section-wide TOKEN check stays GREEN on exactly the
    deletion this pin exists to catch. Stated precisely, because the honest half matters too: the
    verbatim RULE string is gone at either scope, so it is the token assertions — «Done», the open
    stage list — that the bullet slice makes meaningful, not every assertion here."""
    start = text.find("\n- **`decompose` — про ТВОЙ таск.**")
    assert start != -1, "SKILL.md no longer describes decompose in the decomposition section"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the decompose bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the decompose slice is not a proper subset of SKILL.md"
    assert "Жизненный цикл эпика" not in bullet, "the slice swallowed the epic-lifecycle bullet"
    return bullet


def test_the_rulebook_says_decompose_refuses_from_done_too():
    """#649: the sibling hole #626 measured and left open. Until this landed, `decompose` walked a
    card a human had ACCEPTED out of Done — to Backlog, unassigned, carrying `reviewed` and `epic`
    at once, with fresh children in Queue — while the rulebook said nothing about it in the place
    an agent reads decompose. The gate and the sentence land together because prose and gate
    drifting apart is the failure this whole file exists to catch: #626's own bullet advertised
    the Done path as normal for a year of nobody noticing.

    Pinned against the TOOL as well as the words. The rule (WHICH stage is shut) is asserted
    verbatim, because token presence is measurably not enough — that was proven on #626's pin,
    where a mutant inverting the rule kept every token and sailed through green. The open list is
    derived from `workflow.STAGES`, so adding a stage or shutting another one fails here until the
    rulebook is updated too; reword the bullet freely, but the rule has to still be spelled out.

    The `return_task` bullet's caveat is checked from here too, and it is the reason this pin is
    not just about decompose: that caveat is what stops a reader concluding «из Done теперь не
    уводит ничто» from a clean sweep. #626 wrote it naming decompose as the live counter-example;
    this card removes that counter-example, so the caveat now has to carry the CLASS instead (the
    rule is nowhere written once — the next mutating tool reopens the hole). A caveat that decayed
    into «all doors are shut» would be worse than none, so it is pinned, not trusted.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, restore sha256-verified): control
    PASS; drop decompose's Done gate -> FAIL on the code half (DID NOT RAISE); delete the Done
    sentences from the decompose bullet -> FAIL on the rule assertion (which reddens at either
    scope — it is the TOKEN assertions the bullet slice protects, and on that same mutant a
    section-wide token check was measured GREEN); INVERT the
    rule keeping every token -> FAIL; drop `Review` from the open list -> FAIL, and this one only
    reddens because the list is sliced: measured on the deletion mutant, «Review», «Backlog» and
    «Queue» all still occur in this bullet for unrelated reasons, so a bullet-wide `in` would have
    stayed green; soften the return_task caveat into "nothing walks a card out of Done any more"
    -> FAIL."""
    text = _skill_text()
    bullet = _decompose_bullet(text)

    # the RULE, not its vocabulary: which stage is shut, spelled out
    rule_at = bullet.find("**Он ОТКАЗЫВАЕТ из ОДНОЙ стадии — Done")
    assert rule_at != -1, \
        "the decompose bullet no longer states WHICH stage decompose refuses from (#649 Done)"
    assert "file_task" in bullet, \
        "the bullet no longer routes work an accepted card revealed to file_task"
    # ...and the open list must be exactly the complement, straight out of the code. Scoped to the
    # parenthesised list, NOT the whole bullet: this bullet talks about Backlog, Queue and Review
    # for unrelated reasons ("подзадачи встанут в Queue", "доехала до Review"), so a bullet-wide
    # `in` would stay green with a stage quietly dropped from the promise. Measured, not assumed.
    open_list = bullet[rule_at:bullet.find(")", rule_at) + 1]
    for stage in workflow.STAGES:
        if stage == "Done":
            continue
        assert stage in open_list, \
            f"the bullet promises the OTHER stages keep working but never names {stage!r}"

    # the caveat next door must now carry the CLASS, not a counter-example this card just removed
    stuck = _return_task_bullet(text)
    assert "#649" in stuck, \
        "the return_task caveat still names decompose as an OPEN bypass, or stopped naming it"
    assert "следующий мутирующий тул" in stuck, \
        "the caveat decayed into 'every door is shut' — the class is still open by construction"

    # the code: the door really is shut, and the six others really are open
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)

    accepted = api.add_task("accepted by a human", "Done", assignee=api.me_user)
    with pytest.raises(workflow.WorkflowError) as done:
        wf.decompose(accepted["id"], [{"title": "A"}, {"title": "B"}])
    assert "file_task" in str(done.value), \
        "SKILL.md says decompose refuses from Done and points at file_task; it no longer does"
    assert api.stage_of(accepted["id"]) == "Done", "the refusal split the accepted card anyway"

    for stage in ("Backlog", "Queue", "Design", "Build", "Review", "Your Call"):
        card = api.add_task(f"big job in {stage}", stage, assignee=api.me_user)
        assert wf.decompose(card["id"], [{"title": "A"}, {"title": "B"}])["parent"]["moved_to"] \
            == "Backlog", f"the bullet promises decompose still works from {stage}; it does not"


def _crashed_agent_bullet(text: str) -> str:
    """The «Пер-таск-агент УПАЛ» bullet — the pump's whole restart rule, both roles.

    Sliced, and the slice is the ONLY form that can carry VMCP-118 (591). The sentence this pin
    is about — «ветка предложения ревью … пропускает карточки, назначенные на тебя» — ALREADY
    occurs verbatim in the drain tick's step 3, where it explains a DIFFERENT situation (an
    orchestrator that cannot verify an evidence sha). So a whole-file substring cannot tell
    "the restart rule now covers a dead reviewer" from "step 3 still explains the same mechanism
    for its own purpose", which was exactly the gap: the restart rule promised a mechanism
    (`next_task` hands the task back) that exists for build and NOT for review."""
    start = text.find("- **Пер-таск-агент УПАЛ (ошибка рантайма/API)")
    assert start != -1, "SKILL.md no longer tells the pump to restart a crashed per-task agent"
    end = text.find("\n- **", start + 1)
    assert end != -1, "the crashed-agent bullet no longer ends where the next top-level bullet does"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the crashed-agent slice is not a proper subset of SKILL.md"
    assert "Пер-таск-агент ведёт ВЕСЬ таск сам" not in bullet, \
        "the slice swallowed the following bullet"
    return bullet


def _independent_review_section(text: str) -> str:
    """The «Независимое ревью изменений» section — the reviewer's OWN rubric, and the one place
    in this file that is addressed to the reviewer and nobody else.

    Sliced for the same measured reason as `_gc_section` / `_reviewer_tree_rule`: every token the
    pins below name already lives elsewhere in this file, in BUILD-side prose — `git rev-parse
    HEAD` is in the integration recipe, `git show <sha из evidence>` is in «Два возврата, два
    дерева», and `[review]` appears throughout. A whole-file substring would stay green with the
    reviewer's own rule deleted."""
    start = text.find("\n## Независимое ревью изменений")
    assert start != -1, "SKILL.md no longer has a section on independent review"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the independent-review section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the review-section slice is not a proper subset"
    assert "Застрял?" not in section, "the slice swallowed the following section"
    return section


def test_the_restart_rule_says_its_own_mechanism_is_build_only():
    """VMCP-118 (591): the pump's restart rule rests on «снова зовёт `next_task` (задача всё ещё
    за ним)». That premise is TRUE for a build agent and FALSE for a reviewer, and the rulebook
    stated only the half that holds — the catalogue's «упавший review-саб-агент нигде не
    перезапускается».

    MEASURED before the prose was written (real `Workflow` over FakeAPI, solo setup — the dogfood
    one): a card driven to Review and still assigned to me makes `next_task()` answer
    `{"task": null, "message": "the queue is empty — no work for the agent"}`, on every call; the
    same identity with a card in Build gets `{"task": 107, "resume": true, "stage": "Build"}`. A
    SECOND token sees the Review card as `{"review": true}` — so the blindness is a property of
    the solo setup, which is the setup this rulebook is written for.

    Anchored in the branch that causes it: the review-offer loop must keep skipping cards assigned
    to the caller (delete that conjunct and a dead reviewer WOULD be reminded, making this
    paragraph false), and the resume path that DOES hand build work back must still exist — that
    contrast is the whole point of the added clause.

    MUTATION-CHECKED: delete the added sub-bullet while step 3 of the drain tick keeps the same
    sentence verbatim -> FAIL (a whole-file substring on that sentence stays GREEN on the same
    mutant, which is why this is sliced); make the review branch stop skipping my own cards ->
    FAIL; re-wrap the paragraph -> PASS by design (`_flat`)."""
    flat = _flat(_crashed_agent_bullet(_skill_text()))
    assert "Упал РЕВЬЮЕР" in flat, \
        "the restart rule no longer says anything about a reviewer that died"
    assert "пропускает карточки, назначенные на тебя" in flat, \
        "the restart rule no longer names WHY a dead reviewer is never handed back"

    src = inspect.getsource(workflow.Workflow.next_task)
    review_at = src.index('for t in sorted(board.get("Review", [])')
    assert "my_id in self._assignee_ids(t)" in src[review_at:], \
        "the review-offer branch no longer skips cards assigned to the caller — SKILL.md tells " \
        "the pump a dead reviewer is never reminded, which would become false"
    assert "_my_active_tasks" in src, \
        "next_task no longer has the resume path for active work — the build half of the " \
        "contrast the restart rule draws would be gone"


def test_the_reviewer_is_told_to_establish_it_is_looking_at_the_reviewed_code():
    """VMCP-118 (591), three catalogue entries closed by one rule: «проверяй, а не предполагай»
    was never said to the reviewer; «без пути в брифе работай там, где стоишь» is harmless for a
    build agent and means "review the main branch" for a reviewer; and the reviewer's isolation at
    `wip.limit: 1` was not described at all, though background review runs at ANY limit.

    MEASURED on this code (throwaway repo, real git): `ensure_workspace(id, role="review",
    at=None)` against an EXISTING tree pinned at a stale sha returns `{"created": false, "head":
    <the OLD sha>}` with no refusal of any kind, while the same call WITH `--at <new sha>` raises
    («review tree for task N is pinned at X but --at asked for Y»). So the loud guard is the one
    the caller can forget to ask for, and the reviewer's own `git rev-parse HEAD` is the check
    that does not depend on the pump getting its flags right.

    Anchored in the two facts the prose asserts about the tool: the pinned-at check really is
    conditional on `--at`, and creating a review tree really does read neither the board nor the
    limit (only `--gc` builds a Workflow) — which is what makes "нужно при ЛЮБОМ `wip.limit`" a
    statement about the code rather than a preference.

    MUTATION-CHECKED: delete the added bullet while `git rev-parse HEAD` and `git show <sha из
    evidence>` stay in their build-side homes -> FAIL (whole-file substrings on BOTH stay GREEN on
    that mutant); drop the round-2 `[review]` clause from the dossier list -> FAIL; make the
    pinned-at check unconditional -> FAIL; re-wrap -> PASS by design."""
    flat = _flat(_independent_review_section(_skill_text()))
    assert "при ЛЮБОМ `wip.limit`" in flat, \
        "the reviewer is no longer told its own worktree is not a parallel-drain-only affair"
    assert "git rev-parse HEAD" in flat, \
        "the reviewer is no longer told to verify its tree holds the sha under review"
    assert "git show <sha из evidence>" in flat, \
        "the reviewer with no tree is no longer given the fallback that reads the RIGHT code"
    assert "прошлый `[review]`" in flat, \
        "the round-2 reviewer is no longer told to read the previous verdict"
    # the placement residue this card rules on: the reviewer's tree rules are WRITTEN, but they
    # live inside a section headed `wip.limit > 1`, so at limit 1 nothing routes the reviewer to
    # them. Fixed by POINTING from the rubric (always read) rather than by moving the text.
    assert "Ревьюер, вынеся вердикт, освобождает своё дерево" in flat, \
        "the rubric no longer points at the bullet holding the rest of the reviewer's tree " \
        "rules — at wip.limit 1 the reviewer never reaches that section on its own"
    assert "Параллельный дренаж" in flat, \
        "the rubric no longer warns that the pointed-at bullet sits behind a wip.limit > 1 " \
        "heading that does not apply to the reviewer"
    # ...and the pointer must keep resolving: a renamed target would leave a dangling reference
    assert "- **Ревьюер, вынеся вердикт, освобождает своё дерево:**" in _skill_text(), \
        "the bullet the rubric points at no longer exists under that name"

    ensure_src = inspect.getsource(workspace_cmd._ensure_locked)
    assert "if at is not None and" in ensure_src, \
        "the pinned-at guard is no longer conditional on --at — SKILL.md tells the reviewer a " \
        "tree can come back stale in SILENCE, which is only true while this check can be skipped"
    assert '"created": False,' in ensure_src, \
        "an existing worktree is no longer handed back as created: false — the stale-tree hazard " \
        "the rule is about would not arise"
    assert "wip_limit" not in ensure_src and "_build_workflow" not in ensure_src, \
        "creating a workspace now reads the board/limit — 'нужно при ЛЮБОМ wip.limit' rests on " \
        "this path needing neither"
    assert "_build_workflow" in inspect.getsource(workspace_cmd.gc_workspaces), \
        "--gc no longer builds the Workflow — the read/no-read split the rule cites is gone"


def test_the_reviewers_release_rule_carries_the_refusal_its_own_cure_cannot_answer(git_repo):
    """VMCP-118 (591), the ONE entry the catalogue had confirmed by running it: the `dirty` guard
    in `_release_locked` is ROLE-AGNOSTIC, but its cure («доведи до пуша и повтори») is written on
    the build side and is FORBIDDEN to a reviewer — its tree is detached, has no branch, and a
    commit inside it is `unreachable-head` forever. So the role got the refusal and a recipe it
    may not run.

    MEASURED (throwaway repo, real git): a review tree holding ONE untracked file ->
    `{"released": false, "code": "dirty", "reason": "working tree is dirty (1 entries)"}`; the same
    tree after deleting that file -> `{"released": true}`. And the cost of not clearing it is
    measured here rather than argued: `_keep_is_expected` grades that refusal `kept` — the list a
    human is told to read in full — on every tick, unless the card happens to be parked.

    Anchored BEHAVIOURALLY rather than by reading the guard: the claim "роли НЕ РАЗЛИЧАЕТ" is
    about what a REVIEW tree does when it holds a stray file, so this builds exactly that state
    and runs `release_workspace` on it — then removes the file and runs it again, so the cure the
    prose prescribes is measured to work rather than asserted to exist. An index or substring pin
    over `_release_locked` would go green for `if dirty and role == "build":`, which is precisely
    the mutation that would make this paragraph false.

    MUTATION-CHECKED: delete the added paragraph while the build side keeps its full `dirty`
    breakdown -> FAIL (a whole-file substring on `dirty` stays GREEN); make the dirty guard
    role-conditional -> FAIL; add CODE_DIRTY to `_EXPECTED_IN_A_REVIEW_TREE` -> FAIL; re-wrap ->
    PASS by design."""
    flat = _flat(_reviewer_tree_rule(_skill_text()))
    assert "`dirty` роли НЕ РАЗЛИЧАЕТ" in flat, \
        "the reviewer is no longer told the dirty refusal is aimed at it too"
    assert "убери файл из дерева" in flat, \
        "the reviewer is no longer given the ONE cure it is allowed to run"

    # the state the prose is about: a REVIEW tree the reviewer left one file in
    tree = Path(workspace_cmd.ensure_workspace(7, role="review", cwd=git_repo)["path"])
    stray = tree / "reviewer-scratch.md"
    stray.write_text("probe\n")
    refused = workspace_cmd.release_workspace(7, role="review", cwd=git_repo)
    assert refused["released"] is False and refused["code"] == workspace_cmd.CODE_DIRTY, \
        f"a review tree holding a stray file no longer refuses release as dirty: {refused} — " \
        f"SKILL.md tells the reviewer this refusal is aimed at it too"
    assert tree.is_dir(), "the refusal removed the directory anyway"

    # ...and the ONE cure the reviewer is allowed to run really does clear it
    stray.unlink()
    assert workspace_cmd.release_workspace(7, role="review", cwd=git_repo)["released"] is True, \
        "removing the stray file no longer releases the tree — the rulebook prescribes exactly " \
        "that as the reviewer's only available cure"

    entry = {"code": workspace_cmd.CODE_DIRTY, "role": "review", "task_id": 7}
    assert not workspace_cmd._keep_is_expected(entry, set()), \
        "a dirty review tree is now graded `expected` — SKILL.md tells the reviewer an uncleared " \
        "file shouts at a human every tick, which is the reason the cure matters"
    assert workspace_cmd._keep_is_expected(entry, {7}), \
        "control: a PARKED card's dirty tree must still be `expected`, else the assertion above " \
        "would pass merely because nothing is ever graded routine"


def test_the_skill_verification_trap_is_addressed_to_the_reviewer_as_well():
    """VMCP-118 (591): the trap «правку этого файла нельзя проверять вызовом скилла» was written
    in build lexicon — it ended «в `[worklog]` пиши, чем именно проверял», a marker only the
    implementer posts. The role that actually walks into it is the REVIEWER, whose own rubric
    orders it to verify BY RUNNING, and whose only available "run" for a rules change is the skill
    call that returns the frozen snapshot. Same rule, one word wider.

    Pinned inside the freshness section for `_freshness_section`'s own recorded reason, and
    MUTATION-CHECKED by putting the build-only wording back while `[review]` stays everywhere else
    in the file -> FAIL."""
    flat = _flat(_freshness_section(_skill_text()))
    assert "ни РЕВЬЮЕРУ" in flat, \
        "the snapshot trap no longer names the role that most often walks into it"
    assert "`[review]` у ревьюера" in flat, \
        "the trap still tells only the implementer where to record what it checked"


def test_the_container_name_recipe_says_the_reviewers_id_is_not_its_own():
    """VMCP-118 (591): the shared-resource recipe derives a container NAME from the task id
    (`NAME=vikunja-test-$ID`) — and for a REVIEWER that id belongs to somebody else's card, so it
    collides with the container of that card's own build agent, which «Чек-пойнть рано» expressly
    allows to still be working after `advance`.

    Dropped from this card's plan at first, on the ground that the collision is LOUD (docker exits
    125, and the rulebook quotes both refusals verbatim). Independent adjudication measured that
    the loudness does not bound the cost, and the measurement stands: the name refusal reads «You
    have to remove (or rename) that container», the recipe's own cleanup line is `docker rm -f
    "$NAME"  # ОБЯЗАТЕЛЬНО`, and `docker rm -f` against a sibling's RUNNING container exits 0 with
    no warning. So the loud error routes an obedient reader straight into destroying a sibling's
    work. The neighbouring-value escape hatch does not cover it either: «возьми соседний» occurs
    once, on the `lsof` PORT line, and a name has no lsof.

    Kept word-level on purpose (this card's whole ruling is that attention is the scarce
    resource): the fix is a comment on the existing `ID=` line, not a new bullet.

    MUTATION-CHECKED: delete the added comment while the section keeps `vikunja-test-$ID`, the
    quoted docker refusals and the mandatory `docker rm -f` -> FAIL."""
    section = _shared_resources_section(_skill_text())
    flat = _flat(section)
    assert "РЕВЬЮЕР: он ЧУЖОЙ" in flat, \
        "the id-derived naming recipe no longer warns the reviewer that the id is not its own"
    assert "убьёт ЕГО работающий контейнер" in flat, \
        "the recipe no longer names the destructive outcome the loud refusal routes an obedient " \
        "reader towards"
    # controls: the collision the warning is about must still be constructible from this recipe
    assert "NAME=vikunja-test-$ID" in section, \
        "the recipe no longer derives the container name from the task id — the hazard the " \
        "warning describes would not exist"
    assert 'docker rm -f "$NAME"' in section, \
        "the recipe no longer prescribes the removal that makes the collision destructive"


def _second_pass_section(text: str) -> str:
    """The «Второй независимый проход по СВОЕМУ тексту» section — the prose rule, and the only
    place that says how a finding arriving AFTER the verdict is recorded.

    Sliced like `_stuck_section` / `_independent_review_section`, and here the slicing is MEASURED
    rather than stylistic: `[review] APPROVE` occurs a SECOND time in this file, in the reviewer's
    rubric («человек увидит `[review] APPROVE` и примет решение о Done»), so a whole-file
    substring cannot tell "the discriminator is still taught here" from "the words survive in the
    rubric". The heading is anchored WITH its `## ` prefix because the section's title is also
    cited from two other places (the implementer's `advance(to='review')` bullet and the reviewer's
    `review_kind` rubric) — a bare title match would land on one of those pointers instead."""
    start = text.find("\n## Второй независимый проход по СВОЕМУ тексту\n")
    assert start != -1, \
        "SKILL.md no longer has the section on a second independent pass over one's own prose"
    end = text.find("\n## ", start + 1)
    assert end != -1, "the second-pass section no longer ends where the next section begins"
    section = text[start:end]
    assert 0 < len(section) < len(text), "the second-pass slice is not a proper subset of SKILL.md"
    assert "Декомпозиция и файлинг" not in section, "the slice swallowed the following section"
    return section


def test_the_post_verdict_note_rides_on_a_comment_tool_with_no_stage_or_ownership_gate():
    """#618: the second-pass rule ends by telling an agent NOT to hold a late finding for a second
    `review_task` — fix the verdict as soon as you are sure, then append the finding with a plain
    `comment`, because «гейтов по стадии и владению у него нет, он работает и из Review, и после
    вердикта». That clause is a claim about CODE, and it is the half the whole instruction rests
    on: grow a stage or an ownership gate on `Workflow.comment` and the rulebook keeps teaching a
    flow that now raises — to every consumer, since SKILL.md self-heals onto them at server start
    with no per-consumer pin and no review gate (see this module's docstring).

    Today `comment` checks exactly two things — the text is not blank, and the task is on this
    project's board — and neither is a stage or an owner. So the pin is BEHAVIOURAL: it builds the
    state the rule is actually about (a card sitting in Review, assigned to somebody ELSE, whose
    verdict is already recorded) and appends the note through the real Workflow. A substring pin
    over `comment`'s source could not carry this — an added gate is new code, not a missing token,
    so the assertion would stay green through the very drift it claims to catch.

    The prose half is deliberately thin (short substrings, read from the flattened section): the
    wording of this section is still being polished, and pinning sentences is review's job — this
    module only holds the section open and checks it still promises the property.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; add a stage gate to `Workflow.comment` (raise when the task is in Review) ->
    FAIL; delete the clause from SKILL.md -> FAIL; rename the heading -> FAIL loudly, with its own
    message. Re-wrapping the paragraph -> PASS by design (`_flat`)."""
    flat = _flat(_second_pass_section(_skill_text()))
    assert "`comment`" in flat, \
        "the second-pass rule no longer names the tool a post-verdict finding is appended with"
    assert "по стадии и владению" in flat, \
        "the rule no longer says the comment tool is free of stage/ownership gates — the reason " \
        "it can be used at all once the verdict is in"
    assert "после вердикта" in flat, \
        "the rule no longer says the note may be written AFTER the verdict is recorded"

    # the state the rule is about: someone ELSE's card, in Review, already judged
    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    implementer = {"id": 99, "username": "agent-implementer"}
    assert implementer["id"] != api.me_user["id"], "control: the card must not be the reviewer's"
    card = api.add_task("prose deliverable, reviewed", "Review", assignee=implementer)
    wf.review_task(card["id"], verdict="approve", report="прогнал tests/unit -q, зелено")

    note = "[review] post-verdict: второй проход вернулся, одна находка — атрибуция"
    assert wf.comment(card["id"], note) == {"commented": card["id"]}, \
        "SKILL.md tells an agent to append a post-verdict finding with `comment`; it no longer " \
        "accepts a card in Review that belongs to someone else"
    assert api.comments_text(card["id"])[-1] == note, \
        "the post-verdict note did not reach the card's comment stream verbatim"
    assert api.stage_of(card["id"]) == "Review", "appending a note moved the card"

    # ...and the second `review_task` the rule says is unnecessary is genuinely not what happened
    assert sum(c.startswith("[review] APPROVE") for c in api.comments_text(card["id"])) == 1, \
        "control: the note must be an extra comment, not a second verdict"


def test_only_the_review_tool_writes_a_comment_that_opens_with_its_verdict_line():
    """#618: the same rule tells an agent HOW to tell a tool verdict from a note appended by hand.
    A verdict written by the tool always opens with its own line — `[review] APPROVE` or `[review]
    NEEDS WORK`, `первой строкой` — and the post-verdict notes the rule points at have no such
    line, which is how a reader knows they were appended with `comment` instead. That is the
    reader's only discriminator, and it is grounded entirely in two f-strings inside `review_task`.
    Reword either one and every agent (and every human reading the journal) keeps applying a test
    that no longer separates anything, silently — a hand-written note and a recorded verdict would
    look alike.

    Deliberately a PARAPHRASE, and the claim it makes is narrow: the only spans above quoted FROM
    SKILL.md are the three the assertions below pin (`[review] APPROVE`, `[review] NEEDS WORK`,
    `первой строкой`) — the remaining backticked tokens are this codebase's own identifiers and a
    shell command, not citations. VMCP-148 (646)'s ruling, and not a style choice: this paragraph
    opened with a «…» citation of a phrase SKILL.md does not contain (`grep -c` = 0), which sent
    the next reader hunting for text that was not there. Its history is worth stating because git
    cannot tell it — 618's second pass flagged the draft wording, the implementer reworded
    SKILL.md BEFORE committing, and only this docstring's copy of the pre-edit phrasing landed, so
    `git log -S` finds that phrase in this file and in SKILL.md at NO commit. Re-pinning the quote
    to today's wording would only restart the same clock.

    What the paraphrase buys, and what it does not: the three quoted spans are read by an
    assertion, so THOSE cannot go stale quietly. The prose around them is NOT pinned — reword the
    rule's clause about post-verdict notes into its own opposite while leaving those three tokens
    standing, and this test stays green (measured on this card). So a re-wrap or a meaning-
    preserving re-wording will not break this docstring, and a meaning-CHANGING one will not flag
    it either: re-read this paragraph against SKILL.md whenever that section moves. Do not
    "helpfully" restore a citation here — the citation is the part that rotted last time.

    Pinned on the comment the tool actually WRITES, not on the source that formats it: the claim is
    about the FIRST LINE a reader sees, which is a property of the stored comment after the
    text->HTML->text round trip every agent comment makes (#85), so it is read back through
    `comments_text` exactly as an agent would. Both verdicts are driven, because they are separate
    f-strings and a mutation of one is invisible in the other.

    The other half of the discriminator is that `comment` writes the agent's text through
    UNCHANGED — it prepends no marker of its own — so a note carrying the `[review]` marker in its
    body still does not open with a verdict line. That is asserted here too: without it "the tool
    prints X first" would be a fact about one tool rather than a test a reader can apply.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, selection confirmed at exactly 1 test):
    control PASS; re-spell the approve line `[review] approved` -> FAIL; re-spell the needs_work
    line `[review] NEEDS-WORK` -> FAIL (each verdict is its own f-string, and the two rounds are
    what proves neither is covering for the other); delete the clause from SKILL.md -> FAIL, with
    the reviewer's rubric still citing `[review] APPROVE`, so a whole-file substring on THAT token
    was measured GREEN on the same mutant — which is what `_second_pass_section` slices for (the
    `первой строкой` half would have gone red either way); rename the heading -> FAIL loudly."""
    section = _second_pass_section(_skill_text())
    flat = _flat(section)
    assert "`[review] APPROVE`" in flat and "`[review] NEEDS WORK`" in flat, \
        "the rule no longer quotes the two verdict lines a reader tells the tool's comment by"
    assert "первой строкой" in flat, \
        "the rule no longer says the verdict line is the FIRST line — the discriminator is gone"

    api = FakeAPI(buckets=workflow.STAGES)
    wf = workflow.Workflow(api, project_id=3)
    implementer = {"id": 99, "username": "agent-implementer"}

    approved = api.add_task("verdict: approve", "Review", assignee=implementer)
    wf.review_task(approved["id"], verdict="approve", report="перепрогнал замеры, сходится")
    bounced = api.add_task("verdict: needs_work", "Review", assignee=implementer)
    wf.review_task(bounced["id"], verdict="needs_work", report="утверждение шире своего замера")

    assert api.comments_text(approved["id"])[-1].splitlines()[0] == "[review] APPROVE", \
        "the approve verdict no longer opens with the line SKILL.md tells readers to look for"
    assert api.comments_text(bounced["id"])[-1].splitlines()[0] == "[review] NEEDS WORK", \
        "the needs_work verdict no longer opens with the line SKILL.md tells readers to look for"

    # ...and a hand-written note, marker and all, is still distinguishable from both
    note = "[review] post-verdict: находка приехала после вердикта, решения не меняет"
    wf.comment(approved["id"], note)
    written = api.comments_text(approved["id"])[-1]
    assert written.splitlines()[0] == note, \
        "`comment` no longer writes the agent's first line through unchanged — SKILL.md's " \
        "discriminator assumes a hand-written note opens with whatever the agent typed"
    assert not written.startswith(("[review] APPROVE", "[review] NEEDS WORK")), \
        "a hand-written note now opens with a verdict line — the rulebook's way of telling a " \
        "post-verdict note from a recorded verdict no longer separates them"


def _post_push_ci_bullet(text: str) -> str:
    """SKILL.md's bullet on what to check AFTER the push — existence and outcome, in that order.

    Sliced, not scanned whole-file, for the reason `_gc_section` records having MEASURED: every
    token below occurs elsewhere in this file. `gh run list` and the run's `status`/`conclusion`
    are named a second time in the REVIEWER's own backstop bullet (deliberately — it re-reads the
    same run later), `[skip ci]` and its family live in the marker bullet above, and «прогон» is
    everywhere. A file-wide substring could not tell "the build-side rule is still stated" from
    "the reviewer's copy of it survives", which is exactly the drift these pins exist to catch."""
    start = text.find("  - **После пуша проверок ДВЕ")
    assert start != -1, (
        "SKILL.md no longer opens its post-push bullet where this pin can find it. If the bullet "
        "was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n  - **Пуш обязателен", start)
    assert end != -1, "the post-push bullet no longer ends where the «Пуш обязателен» bullet begins"
    bullet = text[start:end]
    assert 0 < len(bullet) < len(text), "the post-push slice is not a proper subset of SKILL.md"
    assert "Пуш обязателен" not in bullet, "the slice swallowed the following bullet"
    return bullet


def _claude_ci_outcome_paragraph(text: str) -> str:
    """CLAUDE.md's paragraph carrying the SECOND copy of the same two-check rule.

    Scoped like `_claude_ceiling_paragraph`, and for its measured reason: `stable`, `integration`
    and `release` all appear elsewhere in this file (the whole Releases section is about them), so
    a whole-file scan could not tell "the outcome rule is still stated" from "those words survive
    somewhere"."""
    start = text.find("**A run that EXISTS is not a run that PASSED")
    assert start != -1, (
        "CLAUDE.md no longer opens its CI-outcome paragraph where this pin can find it. If the "
        "paragraph was legitimately reworded, move this anchor — do not delete the check"
    )
    end = text.find("\n\n", start)
    assert end != -1, "the CI-outcome paragraph no longer ends where the next paragraph begins"
    paragraph = text[start:end]
    assert 0 < len(paragraph) < len(text), "the outcome slice is not a proper subset of CLAUDE.md"
    assert "Manual procedure" not in paragraph, "the slice swallowed the following section"
    return paragraph


def test_the_post_push_check_reads_the_runs_OUTCOME_and_not_only_its_existence():
    """VMCP-128 (614): the post-push rule required a CI run to EXIST for your sha — the right guard
    against a swallowed ci-skip marker, and it says nothing about whether the run PASSED. Measured
    hole, not a feared one: overnight into 2026-07-31, 7 of 15 consecutive runs on `main` ended red
    (all seven `lint-and-unit` success + `integration` failure + `release` skipped, so `stable`
    never moved), and on every one of them an agent truthfully reported "a run exists".

    Naively strengthening it breaks both ways, because the run is ASYNCHRONOUS: "wait for green"
    blocks an agent for minutes and dies with a killed turn, while "read `conclusion` right after
    the push" reads a run that has not started answering. The measurements that decide the shape,
    taken on this repo's 40 most recent `main` runs and timed on each run's FIRST attempt (two
    were later re-run by hand; a re-run's `updatedAt` measures how long a HUMAN took to press
    `gh run rerun` — 31 min and 3 h 26 min — not CI, while the runner queue itself was 0 s on 35
    of 38 and never above 80 s):

    * EXISTENCE asks about a fact that does not ripen — the run is created or it never will be —
      so it stays where it was, right after the push. How long GitHub takes to CREATE the run was
      NOT measured here (a committer-date proxy is polluted by the agent's own criteria re-run in
      between), so the rule says to ask twice before raising the marker alarm rather than pretend
      a number it does not have;
    * the OUTCOME does ripen: a run concludes 42–120 s after it appears, median 60 s — but an
      agent's own tail (`advance(to='review')`, the report, `--release`) costs about that long,
      so reading it LAST costs nothing and usually answers;
    * red runs lean fast but do NOT separate: 42–55 s (n=9, median 46) against 53–120 s (n=31,
      median 65) for green — the bands OVERLAP at 53–55 s, so duration alone never tells a slow
      red from a fast green. The first version of this test claimed a clean 42–48 vs 53–120 split;
      that was an artifact of dropping the two re-run runs entirely, and `8b4bfa5`'s FIRST attempt
      is an ordinary push-triggered red at 55 s (`run_started_at == created_at`, no queue) sitting
      inside the green band. The MECHANISM was wrong too, and per-job timing says so: `integration`
      is never the critical path (16–29 s against `lint-and-unit`'s 38–46 s), so it cannot make a
      run shorter by failing early. A run's length is set by `lint-and-unit`; a GREEN run then also
      runs `release` (8–15 s), which a red one skips. Both corrections came from the second
      independent pass over this prose, which is why the lean is now stated as a lean;
    * `gh run list --commit <SHORT sha>` returns `[]` with exit code 0 — indistinguishable from
      "no run", i.e. a false ci-skip alarm. The full 40-char sha is load-bearing, so the rule
      quotes `"$(git rev-parse HEAD)"` rather than a bare sha;
    * an in-flight run renders `conclusion` as the EMPTY STRING, caught live:
      `{"conclusion":"","databaseId":30636770459,"status":"in_progress"}`. Empty is not `null`, so
      a jq `// "unknown"` fallback silently does not fire — which is the second reason the rule
      branches on `status` rather than dressing up `conclusion`;
    * urgency is bounded but not zero: a later green landing moves `stable` with the red commit
      already in it (verified — red `8fc53f8` is an ancestor of today's `stable`; that night the
      catch-up ran 1–48 min), so the lasting cost is the LAST landing of a session.

    What this pins is the SHAPE of the answer, in both files, because two copies of one rule drift
    (the lesson of 556):

    * both checks are stated, and the existence one is not weakened into the outcome one — the
      marker bullet it guards must still be there;
    * the branch is on `status` FIRST. This is the load-bearing bit and it is not decoration: a
      running run's `conclusion` carries neither verdict, so `conclusion != "success" ⇒ not green`
      reads every in-flight run as red, and a rule that cries wolf on the common case is a rule
      agents learn to ignore;
    * the third state has a name that is NEITHER verdict (`НЕИЗВЕСТНО`), and the rule says not to
      wait for it — otherwise it collapses back into one of the two broken naive forms;
    * the deferral has a real addressee. The build side hands the unknown case to the card's
      independent reviewer, who is late BY CONSTRUCTION (starts later, works for minutes, against a
      run that concludes in ≤2 min) — so the reviewer's own bullet must exist, or the hand-off
      dangles. It is not invented ceremony: the implementer AND the reviewer of VMCP-129 (615) both
      checked CI this way unprompted.

    MUTATION-CHECKED — 13 rounds, `__pycache__` cleared between them, every round confirmed to
    select exactly 1 test, both files restored from copies afterwards with `git diff` clean.
    Controls before and after: PASS. Each of these turns it RED: delete SKILL.md's
    `status == "completed"` premise; delete the not-completed branch head; reword that branch to
    «считай, что прогон в порядке»; drop the full-sha caveat; delete the reviewer's backstop bullet
    head; drop the reviewer's «САМ ПО СЕБЕ ещё не `needs_work`» grading; delete CLAUDE.md's
    outcome-paragraph anchor; drift CLAUDE.md's window to 42–130 s while SKILL.md keeps 42–120;
    delete the ci-skip marker bullet head; drop CLAUDE.md's `status` FIRST ordering; drift
    CLAUDE.md's median to 95 s alone; replace CLAUDE.md's per-job `16–29 s` with a vague phrase;
    regress SKILL.md's red band to the falsified 42–48.

    Three of those thirteen exist BECAUSE the round found the pin missing, not to confirm it. The
    median drift and the per-job mechanism were gaps the second independent pass demonstrated on
    the green suite; the «прогон в порядке» rewrite was found by this matrix itself — naming the
    third state «НЕИЗВЕСТНО» and then telling the agent to treat it as fine satisfied every other
    assertion here, which is the precise failure the whole card exists to remove."""
    text = _skill_text()
    bullet = _flat(_post_push_ci_bullet(text))

    # 1. the existing marker guard is DEEPENED, not replaced: its bullet must still be there
    assert "**В СООБЩЕНИИ КОММИТА не должно быть литерального ci-skip-маркера" in text, (
        "the ci-skip marker bullet is gone. The outcome check was added ALONGSIDE it, not instead "
        "of it — a green-looking task with no run at all is still the louder failure"
    )

    # 2. both checks are named, and the sha-precise form carries its measured caveat
    assert "**СУЩЕСТВОВАНИЕ — сразу после пуша.**" in bullet, \
        "the post-push bullet no longer states the existence check — the ci-skip guard lost its home"
    assert "**ИСХОД — ОДИН взгляд, ПОСЛЕДНИМ действием хода.**" in bullet, (
        "the post-push bullet no longer states the OUTCOME check, which is the whole of 614: "
        "'a run exists' was true on all seven red runs nobody noticed"
    )
    assert 'gh run list --commit "$(git rev-parse HEAD)"' in bullet, \
        "the existence check no longer quotes a form that produces the FULL sha"
    assert "40-символьный" in bullet and "`[]`" in bullet, (
        "the full-sha caveat is gone. `gh run list --commit <short sha>` returns [] with exit 0 — "
        "measured — so without it the recipe manufactures a false 'no run' ci-skip alarm"
    )

    # 3. THE load-bearing property: `status` decides, `conclusion` only means something after it
    assert "`conclusion` осмыслен ТОЛЬКО при `status == \"completed\"`" in bullet, (
        "the rule no longer says `conclusion` is meaningful only once `status` is completed. "
        "Without that premise an agent branches on `conclusion` and reads every in-flight run as "
        "not-green — the naive form this card exists to rule out"
    )
    assert "сломанная проверка" in bullet, (
        "the bullet no longer NAMES the broken form (`conclusion` != success ⇒ not green). "
        "Stating the right rule without the wrong one is what gets tidied back"
    )

    # 4. three states, and the third is neither verdict and is not waited on
    for branch in ("`completed` + `success`", "`completed` + `failure`", "не `completed`"):
        assert branch in bullet, f"the post-push bullet no longer answers the {branch} state"
    assert "не `completed` — это **НЕИЗВЕСТНО**, а не «зелёно» и не «красно»" in bullet, (
        "the in-flight state lost its own name. Calling it green hides the measured hole; calling "
        "it red cries wolf on the common case — it has to be reported as unknown"
    )
    assert "Не жди" in bullet, (
        "the in-flight branch no longer forbids waiting — 'wait for green' is the other naive form, "
        "and it blocks an agent for minutes and dies with a killed turn"
    )
    assert "не пиши «прогон в порядке»" in bullet, (
        "the in-flight branch no longer FORBIDS reporting the run as fine. Naming the state "
        "«НЕИЗВЕСТНО» and then telling the agent to treat it as fine passes every other pin here "
        "— measured: that exact rewrite kept this test green until this assertion was added"
    )

    # 5. the deferral needs a real addressee: the reviewer's backstop must exist
    review = _flat(_independent_review_section(text))
    assert "ты единственный, кто по построению ОПОЗДАЛ" in review, (
        "the reviewer's CI-outcome backstop is gone, so the build side's «не дождался» branch now "
        "hands the unknown case to nobody — which is the original hole with an extra step"
    )
    assert "--commit <ПОЛНЫЙ sha из evidence>" in review, \
        "the reviewer's backstop no longer names a sha-precise command it can actually run"
    assert "САМ ПО СЕБЕ ещё не `needs_work`" in review, (
        "the reviewer's backstop lost the grading. A red run bounced without reading `jobs` turns "
        "an environment failure into a round trip through the implementer"
    )

    # 6. CLAUDE.md carries the same rule, with the same numbers (two copies of one rule drift)
    claude = _flat(_claude_ci_outcome_paragraph(_claude_md_text()))
    assert 'status == "completed"' in claude and "`status` FIRST" in claude, (
        "CLAUDE.md's copy no longer states the status-before-conclusion order that SKILL.md ships. "
        "Only SKILL.md reaches agents, so the copies must not drift; move BOTH or neither"
    )
    assert "UNKNOWN, never as green" in claude, \
        "CLAUDE.md's copy no longer says what an in-flight run is reported as"
    for window in ("42–120", "42–55", "53–120"):
        assert window in claude and window in bullet, (
            f"the measured window {window} s is missing from one of the two files. These are one "
            f"measurement written down twice — re-measure BOTH or neither"
        )
    # The MEDIAN gets its own re-derivation rather than a place in that loop, and it is here
    # because the second independent pass over this card's prose FOUND IT UNGUARDED: it set
    # CLAUDE.md's median to 95 s, left SKILL.md's at 60, and the suite stayed green — while this
    # test's own message promised "one measurement written down twice". A bare "60" substring
    # would not fix that either (it matches any number containing 60), so each file is read
    # through its own phrasing and the two values are compared.
    skill_median = re.search(r"медиана (\d+) с", bullet)
    claude_median = re.search(r"median (\d+) s", claude)
    assert skill_median, "SKILL.md's outcome bullet no longer states a median run duration"
    assert claude_median, "CLAUDE.md's outcome paragraph no longer states a median run duration"
    assert skill_median.group(1) == claude_median.group(1), (
        f"the two files disagree on the median run duration: SKILL.md says "
        f"{skill_median.group(1)} s, CLAUDE.md says {claude_median.group(1)} s. One measurement, "
        f"two write-ups — re-measure BOTH or neither"
    )
    assert "16–29" in claude and "16–29" in bullet, (
        "the per-job timing that explains WHY red runs lean fast is missing from one of the two "
        "files. Without it the lean reads as `integration` failing early — which is measurably "
        "false, and was this card's own first defect"
    )
