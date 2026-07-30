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
import re
from importlib.resources import files

import pytest

from tests.unit.fakes import FakeAPI
from vikunja_mcp import config, server, workflow, workspace_cmd


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


def test_empty_queue_wakeup_interval_is_pinned():
    """The idle-loop wakeup interval is a hand-set human decision (#80: 20→10 min = 600s) with no
    code counterpart to anchor it — it lives only in the rulebook. Pin the value so an unrelated
    skill edit can't silently revert it; a deliberate change updates this one line on purpose."""
    assert "600" in _skill_text(), "the empty-queue ScheduleWakeup interval (600s, #80) vanished"


def test_the_integration_retry_ceiling_is_pinned():
    """VMCP-81: how many `fetch → rebase → re-verify → push` rounds a per-task agent runs before
    escalating via `call_human` is — like the wakeup interval above — a hand-set human number with
    NO code counterpart: nothing in workflow.py counts rounds, so this test is the only thing that
    can hold it. And it is DERIVED, not preferred. CI's auto-release pushes a `chore: vX.Y.Z [skip
    ci]` bump after every green landing (measured 2026-07-30 on this repo's first live parallel
    drain: 17 of the 46 commits that reached main that day were the bot's, arriving 37 s–2 m 55 s
    behind the task commit, median 1 m 41 s), so a losing push is the EXPECTED outcome, not an edge
    case — but that racer is BOUNDED: `[skip ci]` + GITHUB_TOKEN means it never triggers itself, so
    it never pushes twice in a row and costs at most one round on its own. The ceiling must exceed
    the worst purely MECHANICAL run, which at `wip_limit = N` is 2·(N−1) sibling+bump losses plus
    the trailing bump of the landing that beat you to the `fetch` — 5 at the default limit of 3.
    Hence 6 = 5 + 1: below it the loop is still converging, at it the loop provably is NOT.

    Why it needs a pin at all: the rulebook self-heals onto every consumer over the moving `stable`
    branch with no per-consumer pin and no review gate (see this module's docstring), so a silent
    walk-back ships to every agent everywhere. And the walk-back is the LIKELY edit, not a typo —
    the old 3 was exactly the length of the commonest bad run (bump(A) → commit(B) → bump(B)), so
    it reads as a sane-looking number to anyone re-tidying this prose without the derivation in
    hand, while restoring it calls a human onto pure arithmetic at the moment the next round would
    almost certainly have won.

    Pinned in all three places that carry the RULE, not once against the whole file (see
    `_gc_section` on why a whole-file substring is the weak form of this): the parallel-drain
    paragraph, the shell recipe's round count, and the escalation sentence that spends the ceiling
    on `call_human`. Deleting any ONE of the three then fails instead of coasting on the others —
    a recipe with no escalation sentence, or an escalation with no round count, is exactly the
    half-stated rule an agent would fill in with its own guess. The negative half pins the EXACT
    old spellings a revert brings back; a bare `"3" not in text` would be vacuous (`wip_limit`
    defaults to 3 and the measurements above quote 3 min), and it would forbid the derivation
    prose that has to name the number it replaced."""
    text = _skill_text()
    assert "ещё круг, до 6)" in text, \
        "the parallel-drain rule no longer states the 6-round integration retry ceiling"
    assert "до 6 кругов" in text, \
        "the integration recipe's push step no longer states the 6-round retry ceiling"
    assert "отбило 6 раз подряд" in text, \
        "the escalation sentence no longer spends 6 rounds before call_human"
    for old in ("ещё круг, до 3)", "до 3 кругов", "отбило 3 раза подряд"):
        assert old not in text, \
            f"the reverted 3-round ceiling is back in SKILL.md ({old!r}) — see this test's docstring"
