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
        generalisation ships dead.

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
    the `wip` payload key in workflow.py -> FAIL."""
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
    assert "не назвал — **бери 6**" in flat, \
        "the brief-less fallback is gone — `2 × wip.limit` is then an unfillable variable"
    assert 'result["wip"] = wip' in src, \
        "SKILL.md computes the ceiling from next_task's `wip`, but with_wip stopped attaching it"
    assert '"limit": limit' in src, \
        "SKILL.md computes the ceiling from `wip.limit`, but the payload lost its `limit` field"

    for old in ("ещё круг, до 3)", "до 3 кругов", "отбило 3 раза подряд"):
        assert old not in text, \
            f"the reverted 3-round ceiling is back in SKILL.md ({old!r}) — see this test's docstring"


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
    fails from the slicer proves nothing about the clause it claims to pin."""
    section = _shared_resources_section(_skill_text())
    assert "Page URL" in section, \
        "the browser rule no longer tells agents to VERIFY the page is still theirs"
    assert "attach_file" in section, \
        "the browser rule no longer says which path attach_file must be given"
    for tool in ("browser_close", "browser_resize"):
        assert tool in section, f"the rule no longer bans {tool} — it destroys a sibling's state"
    assert "browser_tabs" in section, \
        "the rule no longer explains that a tab is not isolation (global, shifting indices)"


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
