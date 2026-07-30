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

#585 added the other half of the same subject: the tests that hold what is deliberately NOT
configured. Its proposal — `PLAYWRIGHT_MCP_STORAGE_STATE`, upstream's documented complement to
`--isolated` — was measured, composes, and still does not ship, because the file it points at
is LIVE SESSION COOKIES and this repo is PUBLIC. So the pins below run the other way round:
that the committed settings never carry such a path, and that git cannot be made to carry the
file itself.
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
STORAGE_STATE_ENV = "PLAYWRIGHT_MCP_STORAGE_STATE"

# What `browser_storage_state` writes when it is given no filename, and the shape a human
# would reach for. Both are LIVE session cookies; this repo is public.
STORAGE_STATE_FILES = (
    "storage-state-2026-07-30T12-00-00-000Z.json",   # the tool's own default, at the root
    "playwright_storage_state.json",                 # the underscore spelling, also at the root
    "docs/my-storage-state.json",                    # nested: the rule must not be root-anchored
)

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


def test_the_committed_settings_never_carry_a_path_to_live_session_cookies():
    """#585: the follow-up card's proposal, measured and REFUSED — and the refusal is the pin.

    `PLAYWRIGHT_MCP_STORAGE_STATE` is upstream's documented complement to `--isolated`, and
    measured on the installed 0.0.78 it really does compose: with isolation on, the file's
    cookies and localStorage ARE restored (the browser then sent them, so the origin's own
    request log is what says so) and no profile touches disk. What it does NOT do is write:
    after a login, `browser_close` and a clean shutdown the file was byte-identical, and the
    next session read back the seed rather than the login. So there is no version of this that
    ships as a committed setting, for two INDEPENDENT reasons, and the test holds the weaker
    of them because it is the one an edit could plausibly forget:

    * the value is an absolute path to LIVE SESSION COOKIES — a credential for whatever the
      human was logged into, this project's tracker included — and this repository is PUBLIC.
      Its class is `.vikunja-mcp.env` and `VIKUNJA_NOTIFY_WEBHOOK`: env layers only, never a
      committed file. Its SIBLING in this very file is the opposite kind (team policy that
      belongs in git), which is exactly the mix that invites "well, the isolation flag lives
      here, so its companion should too".
    * a committed path would also be inert-to-hostile for everyone else: measured, a path
      whose file does not exist yet makes EVERY `browser_*` call fail with `Error reading
      storage state … ENOENT`, so a clone would get a browser that does not work at all.

    A negative pin can hold nothing and stay green, so this one is mutation-checked in the
    direction that matters — by ADDING the thing it forbids (`__pycache__` cleared between
    rounds, exactly 1 test selected per round, settings restored from a COPY): control PASS;
    add `PLAYWRIGHT_MCP_STORAGE_STATE` to the `env` block -> FAIL; add it with an innocuous
    empty value -> FAIL (an empty string is still a committed decision, and `envToString`
    would hand it straight to the loader); add a differently-named key ending in
    `_STORAGE_STATE` -> FAIL, which is the round that proves the check is on the whole env
    block and not on one literal string.
    """
    settings = REPO_ROOT / SETTINGS_PATH
    env = json.loads(settings.read_text(encoding="utf-8")).get("env", {})
    offenders = sorted(k for k in env if k.endswith("_STORAGE_STATE"))
    assert not offenders, \
        f"{SETTINGS_PATH} now commits {offenders} — a Playwright storage-state path points at " \
        "LIVE session cookies, which is a machine-local secret in a PUBLIC repo (env layers " \
        "only, like .vikunja-mcp.env), and a path that does not exist on someone else's disk " \
        "makes every browser_* call fail with `Error reading storage state … ENOENT`"


@pytest.mark.parametrize("path", STORAGE_STATE_FILES)
def test_a_storage_state_file_can_never_become_committed(path):
    """The one thing #585 ships: the accident it forecloses.

    Measured while answering the card: `browser_storage_state` — the ONLY way to produce one
    of these — refuses every path outside the MCP client's workspace root (`File access
    denied: … is outside allowed roots`). Its workspace root IS the repository the session
    was opened in, so a live-cookie file can land in exactly one place: inside this public
    repo. Asked the obvious way (`filename: "state.json"`) it wrote to the repo ROOT; asked
    with no filename at all it writes `storage-state-<timestamp>.json` there. That artifact
    is untracked, plausible-looking, and one `git add -A` away from being published forever.

    So the guard is a `.gitignore` rule, and this asks GIT rather than grepping the file —
    the same lesson its sibling above records twice over: a pattern can be present and
    defeated (by an earlier blanket rule, by a stray negation), and a green that comes from
    the machine's own `~/.config/git/ignore` is not a property of this repository at all.
    Hence both halves: git excludes the path, AND the rule that decided lives in THIS repo.

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test id selected per round, .gitignore
    restored from a COPY): control PASS; delete the `*storage-state*.json` line -> FAIL for
    the two hyphenated paths; delete `*storage_state*.json` -> FAIL for the underscore one;
    anchor the rule as `/storage-state*.json` -> FAIL for the nested path, which is the round
    that proves the rule is not root-only; rewrite the explanatory comment above the rules
    -> PASS.
    """
    ignored, source, pattern = _ignore_rule(path)
    assert ignored, \
        f"git would happily commit {path!r} — that file is a Playwright storage state: live " \
        "session cookies for whatever the browser was logged into, in a PUBLIC repo. " \
        "`browser_storage_state` cannot write anywhere BUT inside this repo, so .gitignore " \
        "is the only thing standing between an export and a permanent leak"
    assert source == ".gitignore", \
        f"{path!r} is ignored by {pattern!r} from {source}, not by this repo's own .gitignore " \
        "— the protection then exists only on machines whose global ignore file happens to " \
        "cover it, and vanishes silently in a fresh clone"


def test_no_storage_state_file_is_tracked_today():
    """The ignore rule above prevents the NEXT accident; this one checks there was no last one.

    Cheap, and it is the assertion that would actually fire on the day it matters: an ignore
    pattern does nothing to a path git already tracks, so a file added before the rule existed
    would keep travelling to every clone while every pin above stayed green.

    MUTATION-CHECKED: control PASS; `git add -f` a file named `storage-state-x.json` -> FAIL
    (then unstaged, and the file deleted).
    """
    listed = _git("ls-files", "--", "*storage-state*.json", "*storage_state*.json")
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr.strip()}"
    assert not listed.stdout.split(), \
        f"git already carries {listed.stdout.split()} — a Playwright storage state is a live " \
        "credential and this repo is public; the .gitignore rule does not retract a file that " \
        "is already tracked, so this one has to be removed from the index by hand"


def test_claude_md_records_why_storage_state_is_not_configured_here():
    """A refusal nobody can find gets re-litigated — this card exists BECAUSE of that.

    #585 was filed off upstream's README, which describes `--storage-state` as the way to load
    cookies into an isolated context: entirely true, and it reads like the cure for the cost
    #558 paid. The next reader of that README will reach the same conclusion, so the measured
    reason it is NOT the cure has to sit next to the cost it appears to answer.

    Two clauses, both instructions rather than tokens: that the variable is set NOWHERE here
    (the decision), and that it is never written back (the measurement the decision rests on —
    without it the decision reads as caution, and caution is exactly what a later reader
    overrides). Deliberately not pinned: the ENOENT detail, the capability name, the file-size
    evidence — prose that a rewrite should be free to reshape.

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test selected, CLAUDE.md restored from a
    COPY): control PASS; delete the whole paragraph while leaving `PLAYWRIGHT_MCP_STORAGE_STATE`
    mentioned in the paragraph above it -> FAIL, which is the round that proves this pin is not
    a keyword grep; keep the paragraph but soften "does NOT buy that cost back" into "may not
    help" -> FAIL; delete only the never-written sentence -> FAIL; re-wrap the paragraph across
    both pinned phrases -> PASS.
    """
    text = (REPO_ROOT / "CLAUDE.md").read_text(encoding="utf-8")
    start = text.find("**Committed `.claude/settings.json` sets")
    assert start != -1, "CLAUDE.md no longer explains the committed browser-isolation setting"
    end = text.find("\n## ", start)
    assert end != -1, "the browser paragraphs no longer end at a heading"
    section = " ".join(text[start:end].split())
    assert 0 < len(section) < len(text), "the slice is not a proper subset of CLAUDE.md"

    assert f"`{STORAGE_STATE_ENV}` does NOT buy that cost back, and is deliberately set " \
        "NOWHERE here" in section, \
        "CLAUDE.md no longer states the DECISION that no committed file sets " \
        f"{STORAGE_STATE_ENV} — the next reader of upstream's README will propose it again, " \
        "exactly as tracker #585 did"
    assert "It is never WRITTEN" in section, \
        "CLAUDE.md no longer carries the measurement the refusal rests on (the file is read " \
        "and never written back, so a login does not survive into the next session). Without " \
        "it the refusal reads as mere caution and the next reader will overrule it"


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
