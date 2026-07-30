"""The repo-config half of #558: the committed settings that give each session its own browser.

`@playwright/mcp` derives its on-disk profile as `mcp-<channel>-<sha256(first MCP root)[:7]>`,
so DIFFERENT repositories never collide by construction. The residual is two `claude` sessions
with the SAME workspace root: one profile, and the second browser refuses to start at all
(`Browser is already in use … use --isolated`). The remedy is one committed line — the env
equivalent of that flag, in this repo's `.claude/settings.json`.

What makes it worth a test is HOW it fails. Every part of this fix is silent when it breaks:

* the VALUE is parsed by playwright-core's `envToBoolean`, which accepts only `"true"` and
  `"1"`; `"yes"` is not "truthy", it is IGNORED — the key is still there, the browser is not
  isolated, and nothing anywhere says so.
* the file only reaches clones and NEW WORKTREES (where the per-task agents of the parallel
  drain live) if git carries it. `.gitignore` hides `.claude/*` and re-includes exactly this
  one path; drop the `!` line and the fix evaporates with no other signal — the file keeps
  sitting in the author's checkout, working, for them alone.

So the pins here are deliberately not "the key is present": they hold the value against the
set that actually flips the flag, and they ask GIT — not a grep — whether the file survives
the ignore rules. The rulebook half of the same card (the `Граница правила` bullet that tells
an agent what to do when it meets this in someone else's project) is pinned next door, in
tests/unit/test_skill_contract.py, which is the module that owns SKILL.md.
"""
import json
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]

SETTINGS_PATH = ".claude/settings.json"          # repo-relative: the spelling git speaks
LOCAL_STATE_PATH = ".claude/settings.local.json"  # machine-local sibling, must stay hidden
GITIGNORE_NEGATION = "!.claude/settings.json"
ISOLATION_ENV = "PLAYWRIGHT_MCP_ISOLATED"

# playwright-core's `envToBoolean`: only these two strings are true. "false"/"0" are false and
# ANYTHING ELSE is ignored entirely — which is why the value, not the key, is what gets pinned.
ENV_TRUE_VALUES = frozenset({"true", "1"})


def _git(*args: str) -> subprocess.CompletedProcess:
    """git, always rooted at the repo — never at whatever cwd pytest happened to be started in."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _ignore_rule(path: str) -> tuple[bool, str | None, str | None]:
    """Ask git whether it excludes `path`, AND which rule decided — `(ignored, source, pattern)`.

    Measured rather than inferred from the .gitignore text, and every part of this call shape
    was established by experiment, including one that came back as a false green:

    * `--no-index` is REQUIRED. Without it `check-ignore` consults the index first and reports
      every TRACKED path as "not ignored" — so the moment this settings file is committed the
      check becomes a tautology that passes under any .gitignore at all. Measured: blanket
      `.claude/` + the file staged -> exit 1 (not ignored) with the index, exit 0 (ignored)
      with `--no-index`.
    * the verdict comes from the QUIET call, never the verbose one. `-v` exits 0 whenever ANY
      pattern matched, a NEGATIVE one included, so it reports "ignored" for a file the repo
      deliberately re-includes. Measured on both.
    * the SOURCE is why `-v` is called at all. The first version of this helper returned the
      verdict alone, and the mutation round that deletes `.claude/*` came back GREEN: the
      machine running the suite has `**/.claude/settings.local.json` in `~/.config/git/ignore`,
      so "is the local state still hidden?" was being answered by the user's own global config
      instead of by this repo. A pin that passes because of a file outside the repo is not a
      pin, so the caller checks that the deciding rule LIVES IN this repo's `.gitignore`.

    Exit 128 (no repo, no git, a bad path) is neither 0 nor 1 and would otherwise read as
    "not ignored" — a green from a failed measurement, the exact failure shape this repo has
    been bitten by. It raises instead.
    """
    verdict = _git("check-ignore", "--no-index", "-q", "--", path)
    details = _git("check-ignore", "--no-index", "-v", "--", path)
    for proc in (verdict, details):
        if proc.returncode not in (0, 1):
            raise AssertionError(
                f"git check-ignore could not answer for {path!r} "
                f"(exit {proc.returncode}): {proc.stderr.strip()}"
            )
    source = pattern = None
    if details.stdout.strip():
        # `<source>:<line>:<pattern>\t<path>` — the source is a path, so split off the left
        location = details.stdout.splitlines()[0].split("\t", 1)[0]
        source, _line, pattern = location.split(":", 2)
    return verdict.returncode == 0, source, pattern


def test_the_committed_settings_turn_playwright_isolation_on():
    """#558: the one line that stops the second session's browser from refusing to start.

    Pinned as the VALUE, not the key. `envToBoolean` treats `"true"`/`"1"` as true and
    silently ignores everything else, so `"yes"` — the obvious "surely that reads as true"
    edit — leaves an isolation setting that looks configured in every diff and every editor
    while the profile stays shared. A `in ENV_TRUE_VALUES` membership check also rejects JSON's
    own `true` literal (Python `True`), which is the other natural-looking edit: it is a bool,
    it is not the string the env layer can carry, and an env var can only ever be a string.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, each round confirmed to select
    exactly 1 test, and the settings file restored from a COPY — never `git checkout --`,
    which would delete a file this card has not committed yet): control PASS; value
    `"true"` -> `"yes"` -> FAIL; value -> JSON `true` -> FAIL on the type assertion; drop the
    `env` block -> FAIL; truncate the file to malformed JSON -> FAIL on the parse guard;
    delete the file -> FAIL.
    """
    settings = REPO_ROOT / SETTINGS_PATH
    assert settings.is_file(), \
        f"{SETTINGS_PATH} is gone — two `claude` sessions in this repo share one browser " \
        "profile again, and the second one cannot start a browser at all"

    try:
        data = json.loads(settings.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:  # Claude Code skips a settings file it cannot parse
        raise AssertionError(f"{SETTINGS_PATH} is not valid JSON, so it configures nothing: {exc}")

    env = data.get("env")
    assert isinstance(env, dict), \
        f"{SETTINGS_PATH} no longer has an `env` block — that is the only part of this file " \
        "that reaches the MCP server's environment"
    value = env.get(ISOLATION_ENV)
    assert isinstance(value, str), \
        f"{SETTINGS_PATH} sets {ISOLATION_ENV} to {value!r}, which is not a string — an env " \
        "var is always a string, and a JSON bool here configures nothing"
    assert value in ENV_TRUE_VALUES, \
        f"{ISOLATION_ENV} is {value!r}; playwright-core's envToBoolean accepts only " \
        f"{sorted(ENV_TRUE_VALUES)} as true and IGNORES anything else, so this setting is " \
        "present-looking and inert"


def test_the_settings_file_is_actually_carried_by_git():
    """Being right in the author's checkout is not the property — REACHING everyone else is.

    A per-task agent under the parallel drain works in a fresh `git worktree`, and a consumer
    works in a clone; both materialise from git, so an untracked `.claude/settings.json`
    configures exactly one directory on one machine while looking, locally, entirely fixed.
    `git ls-files` reads the INDEX, which is the earliest point at which that stops being
    true — staged is enough, and a commit keeps it.

    MUTATION-CHECKED, same discipline as its sibling above: control PASS; `git rm --cached`
    the file (leaving it on disk, exactly the state that fools a local inspection) -> FAIL.
    That second state was not hypothetical — it is how this pin's very first run came back,
    because the settings file had been written and never added. The remedy was correct on the
    author's disk and reached nobody, which is precisely the failure this test exists for.
    """
    listed = _git("ls-files", "--", SETTINGS_PATH)
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr.strip()}"
    assert listed.stdout.split(), \
        f"{SETTINGS_PATH} exists on disk but git does not carry it, so it reaches no clone " \
        "and no new worktree — every per-task agent of the parallel drain gets a repo without it"


def test_gitignore_still_lets_the_settings_file_through():
    """The negation is the whole delivery mechanism, and it can be defeated without being removed.

    `.gitignore` hides `.claude/*` (machine-local state: settings.local.json, worktrees/,
    mailbox/, the scheduler's lock) and re-includes this one shared file. The trap the file's
    own comment records is that the blanket spelling `.claude/` makes the re-inclusion
    IMPOSSIBLE — git does not descend into an excluded directory, so a later `!` line can
    never fire. Both spellings look equally reasonable in a diff, and the `!` line survives
    the swap untouched, so a pin that greps for the `!` line and stops is a false green.

    Hence assertions of three different kinds: the negation is still WRITTEN (deleting it and
    the blanket together would leave the file deliverable but no longer deliberately so), git
    ITSELF still delivers the file and does so BECAUSE OF a rule in this repo (the half a grep
    cannot answer), and the sibling machine-local path is STILL hidden by this repo's own rule
    — without which the negation would be vacuous: a .gitignore that hides nothing under
    `.claude` trivially "re-includes" everything, and the next agent's settings.local.json
    lands in someone's commit.

    Every verdict is paired with its SOURCE, which is not decoration — see `_ignore_rule`:
    written without it, the local-state half was answered by `~/.config/git/ignore` on the
    machine that ran the suite, and the mutation that deletes `.claude/*` passed.

    The assertions run git-first and literal-last so that each one has a mutation that REACHES
    it — an assertion no round can reach is a claim nobody has tested. The literal spelling is
    pinned deliberately, as the statement of intent behind the behaviour above it: a legitimate
    respelling (`!/.claude/settings.json` is equivalent to git) has to update this line on
    purpose, which is the point.

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test selected per round, .gitignore
    restored from a COPY): control PASS; delete the `!.claude/settings.json` line -> FAIL on
    the verdict (git now excludes it); replace `.claude/*` with a blanket `.claude/` while
    KEEPING the `!` line -> FAIL on the verdict too, which is the round that justifies asking
    git at all; delete the `.claude/*` line so nothing under `.claude` is hidden -> FAIL on the
    local-state provenance (and see `_ignore_rule`: without the source this round passed);
    delete BOTH lines -> FAIL on provenance, because no rule in this repo decides the settings
    file's fate any more; respell the negation as `!/.claude/settings.json` -> FAIL on the
    literal; rewrite the explanatory comment above the rules -> PASS.
    """
    gitignore = REPO_ROOT / ".gitignore"
    assert gitignore.is_file(), ".gitignore is gone"

    ignored, source, pattern = _ignore_rule(SETTINGS_PATH)
    assert not ignored, \
        f"git excludes {SETTINGS_PATH}: the rule it applied is {pattern!r} from {source}. " \
        "Either the re-inclusion is gone, or `.claude/*` became a blanket `.claude/` — git " \
        "never descends into an excluded directory, so a `!` line below one can never fire " \
        "(the comment in .gitignore says exactly this)"
    assert source == ".gitignore" and (pattern or "").startswith("!"), \
        f"{SETTINGS_PATH} gets through by accident, not by decision: the rule git actually " \
        f"applied is {pattern!r} from {source} — this repo's .gitignore must be what " \
        "re-includes it, or the delivery survives only until someone hides `.claude` again"

    local_ignored, local_source, _ = _ignore_rule(LOCAL_STATE_PATH)
    assert local_ignored and local_source == ".gitignore", \
        f"this repo's .gitignore no longer hides {LOCAL_STATE_PATH} (git used " \
        f"{local_source}) — the `.claude/*` rule that makes the re-inclusion meaningful is " \
        "gone, and machine-local Claude Code state is now commitable by anyone whose own " \
        "global ignore file does not happen to cover it"

    lines = [line.strip() for line in gitignore.read_text(encoding="utf-8").splitlines()]
    assert GITIGNORE_NEGATION in lines, \
        f".gitignore no longer says {GITIGNORE_NEGATION!r} verbatim — git may still deliver " \
        f"{SETTINGS_PATH} by some other spelling, but the line this repo's comment explains " \
        "(and that reviewers look for) is gone; update this pin only on purpose"


@pytest.mark.parametrize("value", ["yes", "True", "on", "", "false", "0"])
def test_the_accepted_true_set_is_the_one_playwright_actually_parses(value):
    """The set above is a MEASURED external fact, not a preference, so it gets its own guard.

    `envToBoolean` reads `"true"`/`"1"` as true, `"false"`/`"0"` as false, and returns
    undefined for anything else — meaning a typo'd value is not a falsy value, it is no value:
    the flag falls back to its default (not isolated) with no warning. Widening
    `ENV_TRUE_VALUES` to be forgiving — the tempting "surely `True` and `yes` count too" edit
    — would defang the pin in the test above without touching a single assertion in it, so
    the boundary is asserted here rather than left to reviewers to remember.
    """
    assert value not in ENV_TRUE_VALUES, \
        f"{value!r} was added to ENV_TRUE_VALUES, but playwright-core's envToBoolean does not " \
        "read it as true — the isolation pin now passes on a setting that does nothing"
