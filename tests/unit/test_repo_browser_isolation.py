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
that the committed settings never carry such a path, and that such a file does not reach git.

That second half is deliberately TWO guards of different kinds, because a name-based one
cannot do the job alone — and claiming it could was this card's own first defect, caught in
review by CONSTRUCTING the leak it did not cover. `browser_storage_state` accepts any filename
anywhere under its root, so `.gitignore` can only exclude a LIST of names. Hence FOUR directions
are pinned, one per thing the prose claims: `COVERED_NAMES` are excluded, `UNCOVERED_NAMES` are
NOT, `COLLATERAL_NAMES` are what the rules cost in ordinary files, and `CASE_VARIANT_NAMES` hold
that the list is a different list on Linux than on macOS. Each sentence describing the guard is
falsifiable against it, so none can quietly drift into promising more than it does again. The
guarantee that does NOT depend on the name is
`test_no_file_of_storage_state_shape_is_reachable_by_git`, which asks git what `git add -A`
would publish and reads each candidate's SHAPE — at any size, since a candidate too large to
read is reported rather than skipped (see `SHAPE_SCAN_MAX_BYTES`, whose first version made the
guard evadable by making the file bigger).

#629 is the same two-layer answer applied to the hole beside #607's directory rule. That rule
covers what the browser names ITSELF; a `filename` argument is resolved against the SERVER's cwd,
so it lands in the main checkout's ROOT and nothing excluded it. Layer one is four extension
rules; layer two is `test_no_file_of_browser_artifact_shape_is_reachable_by_git`, which reads
leading MAGIC BYTES because — measured — the extension does not decide the content at all: a
screenshot asked for as `shot.bin`, or with no extension, is still PNG. Layer two is complete
about NAMES, not about FORMATS: `tools/list` showed SEVEN tools taking a `filename` on the default
capability set, and one of them, `browser_network_request`, writes a raw response body of ANY
format (a GIF and a ZIP, constructed). What neither layer reaches is pinned too — the four that
write TEXT (snapshot, console, network, evaluate) drop the page's own text in the same root under
names and in formats no rule can tell from a legitimate file.

It is a gate, not a lock: it turns a leak red in the pre-push run this repo's integration
recipe already requires and in CI; it cannot stop a `git commit`. Nothing here can, and the two
mechanisms that look like they could were built rather than argued about. A `.git/hooks`
pre-commit hook is not carried by a clone at all. The obvious answer to that — commit the hooks
and point `core.hooksPath` at them — was constructed: origin with `.githooks/pre-commit` (exit
1) plus `core.hooksPath=.githooks` blocks its own commit; in a fresh clone the DIRECTORY arrives
and `core.hooksPath` does NOT (it is local config, not content), and the clone's commit went
through unblocked. Those two measurements generalise without needing a third: a hook manager can
only put its trigger in a file under `.git/` or in a local config key, and neither is content, so
every "stronger" option reduces to "works on whichever machine ran an installer" — which is
precisely the "correct on the author's disk and reaching nobody" failure the settings-delivery
pin below was written after being bitten by.

Most of that asks GIT a question, so most of this file needs a git checkout to mean anything at
all — see `requires_git_checkout` below for what happens in a tree that is not one, and why the
answer is a skip rather than the 30 red tests it used to be.
"""
import inspect
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

# Names the `.gitignore` list is claimed to cover. Every one was CONSTRUCTED in a worktree and
# put to `git check-ignore --no-index -v` while answering the rework, not read off the pattern.
COVERED_NAMES = (
    # the tool's real default, measured: OUTPUT DIR, not the repo root
    ".playwright-mcp/storage-state-2026-07-30T20-35-12-331Z.json",
    "storage-state-2026-07-30T12-00-00-000Z.json",   # the same shape if pointed at the root
    "playwright_storage_state.json",                 # the underscore spelling
    "storagestate.json",                             # and the run-together one
    "docs/my-storage-state.json",                    # nested: the rule must not be root-anchored
    "state.json",                                    # "the obvious way" to ask the tool
    "state585.json",                                 # the leak review actually constructed
    "auth.json",
    "cookies.json",
    "session.json",
    "playwright/.auth/user.json",                    # Playwright's own documented convention
)

# Names it is claimed NOT to cover. Pinned on purpose: this list is the honesty half of the
# guard. Widening `.gitignore` over one of these is welcome, but it must be a DECISION that
# also updates the prose promising only partial coverage — so it has to turn this red first.
UNCOVERED_NAMES = (
    "tracker-login.json",     # an arbitrary plausible name; nothing safe would catch it
    "creds.json",             # plausible, simply not on the list
    "storage-state.json5",    # the right words, the wrong extension
)

# What the name list COSTS, which is a different question from what it covers and was stated
# too narrowly ("a file called exactly `state.json`") until it was measured. `state*.json` is a
# GLOB, and a .gitignore pattern without a slash matches at ANY DEPTH, so these ordinary files
# are hidden from `git add -A` too. Pinned because .gitignore now states this as the price, and
# a stated price with nothing holding it is how this card shipped an overclaim the first time.
COLLATERAL_NAMES = (
    "states.json",
    "state-machine.json",
    "state-cache.json",
    "src/data/state-defaults.json",     # any depth, not just the root
    "statement.json",                   # `state` is a PREFIX, not a word
)

# Names that the list covers on one platform and not the other. `core.ignorecase` is not a
# preference — git sets it from the filesystem at clone time: true on this repo's macOS
# checkouts, false on Linux, where CI runs. So the coverage table is platform-local, and
# `storageState.json` is the case that matters, because that camelCase is Playwright's OWN
# spelling for this thing (the `storageState` option; `context.storageState({path})`) and so is
# a name someone working from the upstream API can arrive at without inventing anything. No leak
# follows — the shape scan catches these under any name on either platform — but the list must
# not read as universal when it is not.
CASE_VARIANT_NAMES = (
    "storageState.json",
    "StorageState.json",
    "Auth.json",
    "Cookies.json",
    "State.json",
)

# --- #629: artifacts written under a CALLER-CHOSEN name -------------------------------------
#
# `.playwright-mcp/` (#607) covers the AUTO-named artifacts. A `filename` argument is resolved
# against the SERVER's cwd — the main checkout — so it lands in the repo ROOT instead, and until
# these rules nothing excluded it. Measured the same way as everything else here (@playwright/mcp
# 0.0.78, own `--isolated --headless` server, own stdio client, throwaway origin on 127.0.0.1).
#
# Names the extension rules are claimed to cover. The first two are the two spellings SKILL.md
# itself prints, and they belong to DIFFERENT procedures — a distinction the first draft of this
# comment lost. `shot-554.png` is the `npx playwright screenshot` one-liner, which SKILL.md has
# measured as landing in the AGENT's own worktree (a linked worktree carries this same .gitignore,
# so the rule covers it there too). `vmcp-554-probe.png` is the SHARED MCP browser, measured
# landing in the MAIN checkout's root — that one is the case this card is about.
EXPLICIT_ARTIFACT_COVERED = (
    "shot-629.png",                            # the CLI one-liner's shape (agent's own worktree)
    "vmcp-629-probe.png",                      # the shared browser's shape (main checkout root)
    "docs/diagram.png",                        # nested: the rules must not be root-anchored
    "screenshot.jpg",                          # `type: "jpeg"`, saved under either spelling
    "screenshot.jpeg",
    "page-2026-08-02T08-30-54-572Z.pdf",       # browser_pdf_save's own default name
    "src/vikunja_mcp/leak.pdf",
)

# What the extension rules DEMONSTRABLY miss. The honesty half, and here it is not a shortcoming
# of the list but of the whole idea of listing extensions: the format is chosen by the `type`
# argument (an enum of png|jpeg), never by the name. MEASURED — `filename: "vmcp629-noext"` and
# `filename: "vmcp629-shot.bin"` each produced the same 15,580 bytes that `file(1)` calls "PNG
# image data". The last four are the OTHER writers on the same run: on the DEFAULT capability set
# SEVEN tools accept a `filename` (six of them write), and browser_snapshot /
# browser_console_messages / browser_network_requests / browser_evaluate all dropped one in the
# root too, carrying the page's text and request query strings — a marker planted on the probe
# page came back in three of them, and a token placed in a request's query string in two. No
# extension rule and no magic number reaches those: they are plain text under arbitrary names,
# which is what a legitimate file here looks like.
EXPLICIT_ARTIFACT_UNCOVERED = (
    "vmcp629-noext",          # a screenshot with NO extension — still PNG bytes
    "vmcp629-shot.bin",       # a screenshot under an arbitrary extension — still PNG bytes
    "vmcp629-snap.md",        # browser_snapshot: the page's TEXT as markdown
    "vmcp629-net.json",       # browser_network_requests: URLs, query strings included
    "vmcp629-console.txt",    # browser_console_messages
    "vmcp629-eval.txt",       # browser_evaluate: whatever the JS returned
)

# Names that merely LOOK like the rules should swallow them. `*.png` is an extension match, not a
# substring one, and pinning that keeps a later "let's be thorough" widening (`*png*`) from
# silently hiding source files. Measured with `git check-ignore --no-index -v`, not read off the
# patterns.
EXTENSION_LOOKALIKE_NAMES = (
    "png.py",
    "my.png.py",
    "x.pngx",
    "notpng",
)

# Covered on one platform, not the other — the same `core.ignorecase` split the storage-state
# list has, measured on both settings. `shot.PNG` is not exotic: it is what any tool that
# upper-cases extensions produces, and macOS is where the MCP browser actually runs.
EXPLICIT_ARTIFACT_CASE_VARIANTS = (
    "shot.PNG",
    "Shot.Png",
    "screenshot.JPG",
    "screenshot.JPEG",
    "page.PDF",
)

# Leading bytes of the three formats these tools emit, transcribed from artifacts this card
# produced rather than from a reference table: PNG `89504e470d0a1a0a`, JPEG `ffd8ff…`, PDF
# `255044462d` (`%PDF-`). browser_take_screenshot's `type` enum is closed at png|jpeg and
# browser_pdf_save writes pdf.
#
# These are NOT the whole binary surface, and saying they were is the defect this card's second
# pass caught. `browser_network_request(part="response-body", filename=…)` — present in the
# DEFAULT capability set — writes the raw body of any request the page made, in whatever format
# the server sent: constructed on a probe page, a GIF (`47494638…`) and a ZIP (`504b0304…`)
# landed in the checkout root as `.bin` and matched nothing here. So the scan below is complete
# about NAMES and about these three FORMATS, and no more; widening it to "any binary" would mean
# guessing which of a repo's own files are artifacts, which is not a question bytes can answer.
BROWSER_BINARY_SIGNATURES = {
    "PNG": b"\x89PNG\r\n\x1a\n",
    "JPEG": b"\xff\xd8\xff",
    "PDF": b"%PDF-",
}

# Enough for the longest signature above, and the reason this scan needs no size ceiling: unlike
# the storage-state one it never parses a file, it reads a fixed prefix, so the hazard that made
# SHAPE_SCAN_MAX_BYTES necessary (a credential grown past the cap) has no analogue here. A file
# cannot hide a magic number by being large.
MAGIC_PREFIX_BYTES = 8

# The extensions whose rules the "price" argument rests on. Kept beside the pin that checks the
# argument is still true, so widening the rules and re-measuring the price happen in one place.
ARTIFACT_EXTENSION_GLOBS = ("*.png", "*.jpg", "*.jpeg", "*.pdf")

# Playwright's storage-state schema, measured off a real export: a JSON object with exactly
# these two list-valued keys (cookie entries carry name/value/domain/path/expires/httpOnly/
# secure/sameSite; origin entries carry origin/localStorage). This is what the name-independent
# guard matches on.
STORAGE_STATE_SHAPE_KEYS = ("cookies", "origins")

# The scan has to READ a candidate to classify it, so it needs a ceiling on a single read. What
# that ceiling may NOT be is a silent skip, and the first version of it was exactly that:
# `1 << 20`, justified by "a credential export that big is not a thing" — a universal about every
# storage state there is, inferred from one 455-byte export and never measured.
#
# MEASURED (constructed here, synthetic values throughout): a correctly-shaped export with one
# origin, one small session key and one 4 MiB localStorage entry is 4,194,662 bytes. Under the old
# cap it sat in the tree as `?? tracker-login.json` — the very name UNCOVERED_NAMES designates as
# layer two's whole reason to exist — and the suite measured `1 passed`; flipping ONLY the cap to
# `1 << 30` measured FAIL. Layer two was evadable by making the file BIGGER, and the cap alone was
# what allowed it.
# STRUCTURAL, not measured here, and it is what says no cap can be justified as "bigger than any
# credential": the format carries a `localStorage` array PER ORIGIN (that is the shape this file
# matches on), and Playwright fills it from every origin the context visited, so the size grows
# with origins x their contents and has no fixed upper bound to appeal to. Per-origin localStorage
# quotas are documented by browsers in the single-digit-MB range, which is why the 4 MiB above is
# an ordinary file rather than an exotic one — that number is cited, not measured here.
#
# So this is a READ-SAFETY ceiling, not a claim about credentials, and crossing it FAILS the gate
# instead of skipping the file: "too big to classify" and "a fat credential" look identical from
# here, and only a human can tell them apart.
#
# What it costs, measured on this tree: the candidate set (`git ls-files` union
# untracked-and-not-ignored) is 62 files / 1.582 MiB, the largest being `uv.lock` at 148,126 bytes
# — 453x below the ceiling, so today it neither skips nor reports anything, and the old 1 MiB cap
# had nothing above it either. That cap bought exactly zero while leaving the hole above. What
# this one buys: one `read_text` + `json.loads` cannot pull an unbounded working-tree file into
# memory. Measured at that size: a 67,109,130-byte shaped file reads and parses in 72 ms with a
# 128 MiB peak (tracemalloc), which is the most this gate will spend before it stops guessing and
# reports instead.
SHAPE_SCAN_MAX_BYTES = 64 << 20

# playwright-core's `envToBoolean`: only these two strings are true. "false"/"0" are false and
# ANYTHING ELSE is ignored entirely — which is why the value, not the key, is what gets pinned.
ENV_TRUE_VALUES = frozenset({"true", "1"})

# Every call site through which a pin in this file reaches git. Held as a constant so the scanner
# that enforces `requires_git_checkout` (see the last test) does not match its OWN source and
# report itself. `_git_bytes(` joined the list with #629: a door that is not listed here is a door
# the scanner cannot see, so adding one without adding it here would silently take the next
# git-backed pin out of the guard.
GIT_CALL_MARKERS = ("_git(", "_git_bytes(", "_ignore_rule(")

# --- Is this tree a git checkout at all? (#622) ---------------------------------------------
#
# Most pins here ask GIT a question and assert on its exit code, which makes them meaningless —
# not failing, MEANINGLESS — in a tree git does not track: a `git archive`/sdist extraction, a
# copied tree, any derived measurement environment. They used to FAIL there, 30 ids of them,
# with `fatal: not a git repository … assert 128 == 0`, and that cost a real measurement: #594's
# mutation sweep ran in exactly such a tree and its author read the raw `N failed` counts as
# mutation KILL counts. Every row of a six-row table was inflated by precisely these 30 and the
# headline conclusion came out wrong by a factor of 16. A CONSTANT failure is the dangerous kind:
# it survives any before/after comparison intact, so it does not look like noise, it looks
# like signal.
#
# The probe is a FILESYSTEM fact, deliberately NOT `git rev-parse --show-toplevel`, and the
# difference is not stylistic. `rev-parse` walks UP. Measured while writing this: an extraction
# placed inside another repository (a `build/` directory under a checkout is the ordinary way
# that happens) answers exit 0 there and names the OUTER repo — so a rev-parse probe would
# decline to skip, and these pins would go on to interrogate the wrong repository, reporting
# things like "`.claude/settings.json` is not carried by git" about a repo that was never asked
# about. `.git` at REPO_ROOT ITSELF (a directory in a main checkout, a `gitdir:` FILE in a linked
# worktree — which is where the parallel drain's per-task agents run, so that case is the common
# one, not an edge) is the question actually being asked, costs one stat, and cannot walk
# anywhere.
#
# It also never reads git's EXIT CODE, and that is what stops the skip from becoming an
# off-switch. "No repository" is a MISSING `.git`; "git failed" is a non-zero exit WITH a `.git`
# present — two different facts from two different sources, which is the only arrangement in
# which one cannot mask the other. So a git that is broken inside a real checkout (no binary, a
# corrupt repo, unreadable objects) leaves `.git` exactly where it is: nothing skips, and the
# original assertions go red as loudly as before. Both directions are pinned in
# `test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git`.
_IS_GIT_CHECKOUT = (REPO_ROOT / ".git").exists()

requires_git_checkout = pytest.mark.skipif(
    not _IS_GIT_CHECKOUT,
    reason=(
        f"{REPO_ROOT} has no .git, so it is not a git checkout (a `git archive`/sdist "
        "extraction, a copied tree). These pins ask git what it would publish and there is no "
        "git here to ask: the property is NOT APPLICABLE, not broken. In a checkout every one "
        "of them runs, and a git that FAILS there still goes red — this probe never reads "
        "git's exit code"
    ),
)


def _git(*args: str) -> subprocess.CompletedProcess:
    """git, always rooted at the repo — never at whatever cwd pytest happened to be started in."""
    return subprocess.run(
        ["git", *args], cwd=REPO_ROOT, capture_output=True, text=True
    )


def _git_bytes(*args: str) -> subprocess.CompletedProcess:
    """git, rooted at the repo, returning RAW BYTES — for reading a blob straight out of the index.

    Exists because a candidate's content is not always on disk: a path can be in the index with no
    worktree copy (deleted but not staged), and reading only the worktree turns that into a silent
    skip. Separate from `_git` because that one decodes as text, which mangles the first bytes of
    a PNG — the exact thing the caller is trying to look at.
    """
    return subprocess.run(["git", *args], cwd=REPO_ROOT, capture_output=True)


def _ignore_rule(path: str, *, ignorecase: bool | None = None) -> tuple[bool, str | None, str | None]:
    """Ask git whether it excludes `path`, AND which rule decided — `(ignored, source, pattern)`.

    `ignorecase` forces `core.ignorecase` for the call instead of taking whatever this checkout
    was cloned with. That is not a knob: git sets that key from the FILESYSTEM at clone time, so
    the same `.gitignore` covers a different set of names on macOS (true) than on Linux (false),
    which is where CI runs. Left as None the call reports what THIS machine does; passed
    explicitly it reports what a given platform does, identically on either — which is the only
    way a pin about case can mean the same thing in both places.

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
    config = () if ignorecase is None else ("-c", f"core.ignorecase={str(ignorecase).lower()}")
    verdict = _git(*config, "check-ignore", "--no-index", "-q", "--", path)
    details = _git(*config, "check-ignore", "--no-index", "-v", "--", path)
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


@requires_git_checkout
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


@requires_git_checkout
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


@requires_git_checkout
@pytest.mark.parametrize("path", COVERED_NAMES)
def test_the_listed_storage_state_names_are_excluded_by_this_repos_gitignore(path):
    """Layer one of the guard: the NAMES it forecloses — no more, and this test says no more.

    Measured while answering the card (@playwright/mcp 0.0.78, own stdio client, throwaway
    origin): `browser_storage_state` is the only producer, it is behind
    `PLAYWRIGHT_MCP_CAPS=storage`, and it resolves a relative `filename` against the MCP
    server's cwd — the main checkout. With no filename it writes
    `.playwright-mcp/storage-state-<timestamp>.json`; asked the obvious way
    (`filename: "state585.json"`) it dropped 455 bytes containing a live cookie in the repo
    root. Untracked, plausible-looking, one `git add -A` from being published forever.

    This asks GIT rather than grepping .gitignore — the lesson its siblings above record
    twice: a pattern can be present and defeated (by an earlier blanket rule, by a stray
    negation), and a green that comes from the machine's own `~/.config/git/ignore` is not a
    property of this repository at all. Hence both halves: git excludes the path, AND the rule
    that decided lives in THIS repo.

    What this does NOT establish is completeness — see `UNCOVERED_NAMES` and the shape scan
    below. The first version of this test was called `..._can_never_become_committed`, and
    review disproved the "never" by constructing a leak under a name the list missed.

    MUTATION-CHECKED, one round per RULE, deleting that rule's whole LINE (`__pycache__`
    cleared each round, the parametrised count verified with `--collect-only`, .gitignore
    restored from a COPY). Failing ids transcribed from the runs, not predicted:

    * control -> 11 passed
    * drop `*storage-state*.json` -> FAIL for `storage-state-…Z.json` and
      `docs/my-storage-state.json` (the `.playwright-mcp/` copy survives on the dir rule)
    * drop `*storage_state*.json` -> FAIL for `playwright_storage_state.json`
    * drop `*storagestate*.json`  -> FAIL for `storagestate.json`
    * drop `state*.json`          -> FAIL for `state.json` AND `state585.json`
    * drop `auth.json` / `cookies.json` / `session.json` -> FAIL for exactly that one each
    * drop `.auth/`               -> FAIL for `playwright/.auth/user.json`
    * drop `.playwright-mcp/`     -> PASS, all 11: the default export is still caught by the
      storage-state glob. That is why #607 gets its own pin below rather than riding on this
      one — deleting the directory rule has to be able to turn something red.
    * re-anchor as `/storage-state*.json` -> FAIL for `docs/my-storage-state.json` only,
      proving the rules are not root-only
    * rewrite the explanatory comment -> PASS

    A round-one driver deleted rules by SUBSTRING and reported the same clean result, but
    `state*.json` is a substring of `*storage-state*.json`, so the round labelled "drop
    `state*.json`" had actually mutated a different rule and nothing held `state*.json` at
    all. Rules here are therefore removed by exact line match. A mutation round is evidence
    only for the edit it really made.
    """
    ignored, source, pattern = _ignore_rule(path)
    assert ignored, \
        f"git would happily commit {path!r} — that file is a Playwright storage state: live " \
        "session cookies for whatever the browser was logged into, in a PUBLIC repo. This " \
        "name is on the list .gitignore is supposed to cover, so either the rule was dropped " \
        "or an earlier pattern now overrides it"
    assert source == ".gitignore", \
        f"{path!r} is ignored by {pattern!r} from {source}, not by this repo's own .gitignore " \
        "— the protection then exists only on machines whose global ignore file happens to " \
        "cover it, and vanishes silently in a fresh clone"


@requires_git_checkout
@pytest.mark.parametrize("path", UNCOVERED_NAMES)
def test_the_name_list_is_known_to_be_incomplete(path):
    """The honesty half — it pins what the guard does NOT do, so the prose cannot outrun it.

    A name list cannot be complete: `browser_storage_state` takes any filename anywhere under
    its root, and no pattern that would catch `tracker-login.json` is safe to write in a repo
    that also holds legitimate JSON. That is stated as a limit in `.gitignore` and in
    CLAUDE.md, and a stated limit with nothing holding it is how the first attempt at this
    card shipped "makes accidental commit impossible" over a guard that did not.

    So this fails if one of these ever BECOMES ignored. That is not a vote against widening
    the list — it is the requirement that widening be deliberate and land together with the
    sentence that describes the coverage. The failure message says exactly that.

    MUTATION-CHECKED in the only direction that tests it — by ADDING the rule it forbids
    (same discipline; .gitignore restored from a COPY): control 3 passed; append
    `tracker-login.json` -> FAIL on `[tracker-login.json]` alone, other two green; append
    `creds.json` -> FAIL on `[creds.json]` alone. Two separate rounds because a single one
    cannot tell "each name is checked on its own" from "the trio stands or falls together".
    """
    ignored, source, pattern = _ignore_rule(path)
    assert not ignored, \
        f"{path!r} is now ignored by {pattern!r} from {source}. If that was deliberate, this " \
        "is the intended tripwire, not a bug: move the name into COVERED_NAMES and update " \
        "the coverage sentences in .gitignore and CLAUDE.md, which currently tell the reader " \
        "this name is NOT protected. The guard and the claim about it move together"


@requires_git_checkout
@pytest.mark.parametrize("path", COLLATERAL_NAMES)
def test_the_state_glob_hides_more_than_a_file_called_state_json(path):
    """The PRICE of layer one, pinned in the same way its coverage is — because it was misstated.

    `.gitignore` used to price these rules as costing "a legitimate file called exactly
    `state.json`/`auth.json`/`cookies.json`/`session.json`". Measured with `git check-ignore
    --no-index -v`, both halves of that are wrong: `state*.json` is a glob over any basename
    STARTING with `state`, and a pattern containing no slash matches at any DEPTH, so
    `src/data/state-defaults.json` goes too — as does `pkg/auth.json` for the exact-name rules.
    Nothing is harmed today: `git ls-files '*.json'` returns exactly three files
    (`.claude/settings.json`, `.mcp.json`, `opencode.json`) and none of them matches. But the
    next contributor adding a legitimate `state-*.json` fixture would lose it from `git add -A`
    silently, and the comment they would read to find out told them it could not happen.

    This is not an argument for narrowing the rule — `state*.json` is what catches `state585.json`
    (the name review dropped a live cookie under) and `state.json` (the name this guard's own
    docstring had called "the obvious way" while not covering it). It is the requirement that the
    price stay stated at its measured size. Narrowing the rule turns this red, which is the same
    tripwire `test_the_name_list_is_known_to_be_incomplete` provides in the other direction.

    MUTATION-CHECKED (line-precise rule removal, `__pycache__` cleared, .gitignore restored from
    a COPY): control 5 passed; drop `state*.json` -> FAIL for all 5; narrow it to the literal
    `state.json` -> FAIL for all 5; anchor it as `/state*.json` -> FAIL for
    `src/data/state-defaults.json` alone, the round that isolates the depth half from the glob
    half.
    """
    ignored, source, pattern = _ignore_rule(path)
    assert ignored and source == ".gitignore", \
        f"{path!r} is no longer hidden by this repo's .gitignore (git applied {pattern!r} from " \
        f"{source}). That is a coverage change in disguise: `state*.json` was narrowed or " \
        "anchored, so the price stated in .gitignore — that the glob also swallows ordinary " \
        "`state…json` files at any depth — is now wrong in the other direction. If the rule was " \
        "narrowed on purpose, update that sentence and check `state.json`/`state585.json` are " \
        "still covered, because they are the names the leak was constructed under"


@requires_git_checkout
@pytest.mark.parametrize("path", CASE_VARIANT_NAMES)
def test_the_name_list_covers_a_different_set_on_linux_than_on_macos(path):
    """The coverage table is platform-local, and saying so is the whole point of this pin.

    git decides case-folding of ignore patterns from `core.ignorecase`, which it sets from the
    FILESYSTEM when the repo is cloned: true on macOS (where this repo is developed and where
    the playwright MCP server that writes these files actually runs), false on Linux (where CI
    runs). Measured on both settings: `storageState.json` — Playwright's OWN spelling for this
    thing, the `storageState` option and `context.storageState({path})`, so a name reachable
    straight from the upstream API — is covered here by `*storagestate*.json` and NOT covered on
    Linux. Same for `Auth.json`, `Cookies.json`, `State.json`.

    Both directions are asserted, with `core.ignorecase` FORCED rather than inherited, so this
    pin reads the same on either platform instead of quietly testing one of them. That is the
    trap in pinning a case fact at all: a pin that just asked "is `storageState.json` ignored?"
    would be green here and red in CI, or vice versa, and would be measuring the runner.

    No leak follows — layer two reads shape, not names, and catches these on either platform —
    so this is not a hole, it is a boundary that the .gitignore comment now states. Adding case
    variants to the rules is a fine decision; it just has to turn this red first and move the
    sentence with it.

    MUTATION-CHECKED (`__pycache__` cleared, the parametrised count verified with
    `--collect-only`, .gitignore restored from a COPY): control 5 passed; append a
    case-insensitive rule `*[Ss]torage[Ss]tate*.json` -> FAIL for `storageState.json` and
    `StorageState.json` on the Linux half, the two green -> so each name is judged on its own;
    append `Auth.json` -> FAIL for `Auth.json` alone; drop `*storagestate*.json` -> FAIL for
    `storageState.json` and `StorageState.json` on the macOS half. Both halves can fail, which
    is what makes this a statement about the DIFFERENCE and not about either platform.
    """
    on_macos, mac_source, _ = _ignore_rule(path, ignorecase=True)
    assert on_macos and mac_source == ".gitignore", \
        f"on a case-folding checkout (macOS, `core.ignorecase=true`) this repo's .gitignore no " \
        f"longer covers {path!r}. The .gitignore comment says it does, and macOS is where the " \
        "MCP server that writes these files actually runs — so this is the half that matters " \
        "in practice. Either restore the rule or move the name out of CASE_VARIANT_NAMES"

    on_linux, linux_source, linux_pattern = _ignore_rule(path, ignorecase=False)
    assert not on_linux, \
        f"on a case-SENSITIVE checkout (Linux, CI) {path!r} is now ignored by " \
        f"{linux_pattern!r} from {linux_source}. If that was deliberate — a case variant added " \
        "to the rules — this is the intended tripwire, not a bug: the .gitignore comment and " \
        "CLAUDE.md currently tell the reader this list is CASE-DEPENDENT and that this name is " \
        "covered on macOS only. Update both, and move the name into COVERED_NAMES"


@requires_git_checkout
@pytest.mark.parametrize("path", (
    ".playwright-mcp/page-2026-07-30T20-35-12-319Z.yml",
    ".playwright-mcp/storage-state-2026-07-30T20-35-12-331Z.json",
))
def test_the_playwright_output_dir_is_excluded(path):
    """#607, absorbed here because #585's own measurement lands in this exact directory.

    The MCP browser writes auto-named artifacts to `.playwright-mcp/` relative to the SERVER's
    cwd — the main checkout, not the per-task worktree the agent is standing in. Measured on
    one navigate: a `page-<timestamp>.yml` snapshot, which contains the page's TEXT, so a
    logged-in page is quoted into it verbatim. With the storage capability on, the default
    storage-state export lands there too. Until this rule the directory was `?? untracked`
    with nothing but agent discipline ("не коммить", SKILL.md) between it and `git add -A`.

    Pinned as two paths because they fail for different reasons: the `.yml` is covered ONLY by
    the directory rule (no storage-state glob can see it), while the `.json` would survive the
    directory rule's deletion via `*storage-state*.json` — so without the first parameter,
    deleting `.playwright-mcp/` would leave the whole file green.

    MUTATION-CHECKED (line-precise rule removal, `__pycache__` cleared, .gitignore restored
    from a COPY): control 2 passed; drop the `.playwright-mcp/` line -> `1 failed, 1 passed`,
    the failure being exactly the `.yml` id. Measured, not predicted — and it is the round
    that shows the `.json` parameter alone would have left this pin unable to fail.
    """
    ignored, source, pattern = _ignore_rule(path)
    assert ignored, \
        f"git would carry {path!r} — playwright's output dir is untracked artifact spill: " \
        "page snapshots quote the TEXT of whatever page was open, which for a logged-in page " \
        "is not something to publish from a PUBLIC repo"
    assert source == ".gitignore", \
        f"{path!r} is ignored by {pattern!r} from {source} rather than this repo's own " \
        ".gitignore, so it is not protected in a fresh clone"


@requires_git_checkout
@pytest.mark.parametrize("path", EXPLICIT_ARTIFACT_COVERED)
def test_the_browser_artifact_extensions_are_excluded_by_this_repos_gitignore(path):
    """#629: the hole immediately beside the rule above, and layer one of closing it.

    The directory rule catches what the browser names ITSELF. Ask for a name and the file goes
    somewhere else entirely: measured, `browser_take_screenshot(filename="x.png")` answered
    `- [Screenshot of viewport](./x.png)` and wrote it in the SERVER's cwd — the main checkout's
    root — where `git check-ignore --no-index`, run against this repo's pre-#629 `.gitignore`,
    exited 1 on it. That path is not an unusual one for an agent to take: SKILL.md prescribes the
    call that puts the file there, and separately tells the agent not to commit it. Until these
    rules that one sentence of discipline was the entire guard, which is the trade #607 already
    refused for the auto-named artifacts.

    Asks git rather than grepping, and checks the SOURCE, for the reasons its siblings above
    record: a pattern can be present and defeated, and a green that comes from the machine's own
    `~/.config/git/ignore` says nothing about this repository.

    MUTATION-CHECKED, one round per rule, deleting that rule's whole LINE (`__pycache__` cleared
    each round, .gitignore restored from a COPY and its `git hash-object` confirmed identical
    afterwards). The selection was the WHOLE FILE — 65 tests, `--collect-only`, of which 7 are
    this one — so a round's effect on SIBLING pins is visible instead of hidden by a narrow
    selection. Failing ids transcribed from the runs, not predicted:

    * control -> 65 passed
    * drop `*.png`  -> 5 failed: `shot-629.png`, `vmcp-629-probe.png`, `docs/diagram.png` here,
      PLUS `shot.PNG` and `Shot.Png` in the case pin below. Predicting three and measuring five
      is exactly why the selection is the whole file: one rule feeds two claims.
    * drop `*.jpg`  -> 2 failed: `screenshot.jpg` here, `screenshot.JPG` in the case pin
    * drop `*.jpeg` -> 2 failed: `screenshot.jpeg` here, `screenshot.JPEG` in the case pin
    * drop `*.pdf`  -> 3 failed: `page-…Z.pdf` and `src/vikunja_mcp/leak.pdf` here, `page.PDF`
      in the case pin
    * re-anchor as `/*.png` -> 1 failed, `docs/diagram.png` alone: the round that shows the
      rules are not root-only, and the only one that isolates a single id in this test
    * drop `.playwright-mcp/` -> 1 failed, and it is #607's pin, not one of these: the two rules
      are independent, and neither is silently propping up the other
    """
    ignored, source, pattern = _ignore_rule(path)
    assert ignored, \
        f"git would happily commit {path!r} — that is where the MCP browser drops a screenshot " \
        "or a PDF when the caller names it, and the caller here is an agent following SKILL.md. " \
        "A screenshot of a logged-in page is not something a PUBLIC repo should be one " \
        "`git add -A` away from publishing"
    assert source == ".gitignore", \
        f"{path!r} is ignored by {pattern!r} from {source}, not by this repo's own .gitignore " \
        "— that protection exists only on machines whose global ignore file happens to cover " \
        "it, and vanishes silently in a fresh clone"


@requires_git_checkout
@pytest.mark.parametrize("path", EXPLICIT_ARTIFACT_UNCOVERED)
def test_the_extension_rules_do_not_reach_a_screenshot_under_another_name(path):
    """The honesty half of #629 — and here the gap is structural, not an oversight in the list.

    An extension rule assumes the name says what the file is. It does not: the format comes from
    the `type` argument (an enum of png|jpeg) and the name is passed through untouched. MEASURED
    in both directions, `file(1)` judging each time — `filename: "vmcp629-noext"` (no extension at
    all) and `filename: "vmcp629-shot.bin"` each wrote 15,580 bytes of "PNG image data"; a `.jpg`
    name with `type` left at its png default wrote PNG; and a `.png` name with `type: "jpeg"`
    wrote JPEG, i.e. an extension rule can be "covering" a file that is really the other format.
    So layer one cannot be completed by adding extensions, and
    `test_no_file_of_browser_artifact_shape_is_reachable_by_git` below is what actually covers
    those cases: it reads bytes.

    The last four parameters are the part NEITHER layer reaches, which is why they are pinned
    here rather than quietly omitted. `tools/list` showed SEVEN tools with a `filename` property
    on the DEFAULT capability set — the one the shared session server runs, whose cwd IS the main
    checkout — of which six write and one (`browser_run_code_unsafe`) reads. With every
    capability on it is ten and eight. browser_snapshot, browser_console_messages,
    browser_network_requests and browser_evaluate all put their output in the checkout root too.
    Measured content: a marker planted in the probe page's text came back inside the snapshot,
    the extensionless snapshot and the evaluate result; a token placed in a request's query
    string came back in the console and the network dumps. They are plain text under a caller's
    name — no extension worth excluding, no magic number to read — so the honest statement is
    that this guard reduces the accident and does not foreclose it (filed as #703, since closing
    it means changing what SKILL.md asks agents to pass, not adding a rule). Widening is welcome;
    it has to turn this red first and move the sentence in .gitignore with it.

    One writer is NOT claimed as measured: `browser_network_request` (singular) exposes the same
    `filename` and was never exercised here, so it is counted among the six by its own schema
    rather than by observation.

    MUTATION-CHECKED in the direction that tests a negative — by ADDING what it forbids
    (`__pycache__` cleared, whole file selected, .gitignore restored from a COPY): control
    65 passed, 6 of them this test; append `vmcp629-shot.bin` -> 1 failed, that id alone, the
    other five green; append `*.md` -> 1 failed, `vmcp629-snap.md` alone. Two rounds, because one
    cannot distinguish "each name is judged on its own" from "the six stand or fall together".
    """
    ignored, source, pattern = _ignore_rule(path)
    assert not ignored, \
        f"{path!r} is now ignored by {pattern!r} from {source}. If that was deliberate this is " \
        "the intended tripwire, not a bug: .gitignore and CLAUDE.md currently tell the reader " \
        "that the extension list misses screenshots saved under an arbitrary name and misses " \
        "the text writers (snapshot/console/network/evaluate) entirely. Move the name into " \
        "EXPLICIT_ARTIFACT_COVERED and update both, so the guard and the claim about it move " \
        "together"


@requires_git_checkout
@pytest.mark.parametrize("path", EXTENSION_LOOKALIKE_NAMES)
def test_the_artifact_rules_match_an_extension_and_not_a_substring(path):
    """The other direction of "does it catch exactly what is claimed": what it must NOT catch.

    `*.png` matches a trailing extension. The tempting thoroughness edit (`*png*`, or dropping
    the dot) would also swallow `png.py` and `x.pngx` — source files — out of `git add -A`, and
    losing a source file that way is silent in exactly the manner this whole file exists to
    prevent. Measured, all four are NOT ignored today.

    MUTATION-CHECKED (whole file selected, `__pycache__` cleared, .gitignore restored from a
    COPY): control 65 passed, 4 of them this test; replace `*.png` with `*png*` -> 4 failed, all
    four ids; replace it with `*.png*` -> 2 failed, `my.png.py` and `x.pngx` ONLY.

    That second round was predicted as three and measured as two, and the miss is worth keeping:
    `png.py` contains no `.png` substring at all (it is `png` + `.py`), so the dot-bearing
    widening leaves it alone while the dotless one swallows it. Two rounds failing on DIFFERENT
    subsets is what shows each name is judged on its own — and that the four are not
    interchangeable padding.
    """
    ignored, source, pattern = _ignore_rule(path)
    assert not ignored, \
        f"{path!r} is now excluded by {pattern!r} from {source}, but it is a source file, not a " \
        "browser artifact. The artifact rules are extension matches on purpose; a substring " \
        "spelling like `*png*` hides real code from `git add -A` without saying so"


@requires_git_checkout
@pytest.mark.parametrize("path", EXPLICIT_ARTIFACT_CASE_VARIANTS)
def test_the_artifact_rules_cover_a_different_set_on_linux_than_on_macos(path):
    """Same platform split as the storage-state list, measured for these rules rather than assumed.

    git folds the case of ignore patterns only where `core.ignorecase` is true, which it takes
    from the FILESYSTEM at clone time: true on this repo's macOS checkouts (where the MCP browser
    that writes these files actually runs), false on Linux, where CI runs. Measured on both
    settings with the flag FORCED, so this pin reads the same on either platform instead of
    quietly measuring the runner.

    No leak follows for the binary formats — the magic-byte scan below reads bytes, not names, on
    either platform — but the list must not read as universal when it is not.

    MUTATION-CHECKED (whole file selected — 65 tests, 5 of them this one — `__pycache__` cleared,
    .gitignore restored from a COPY): control 65 passed; append `*.PNG` -> 1 failed, `shot.PNG`
    on the Linux half, with `Shot.Png` still green, so each name is judged on its own and an
    upper-case rule is NOT a case-insensitive one; drop `*.png` -> `shot.PNG` and `Shot.Png` fail
    on the macOS half (inside that round's 5, alongside the three plain-name ids). Both halves
    can fail, which is what makes this a statement about the DIFFERENCE rather than about either
    platform.
    """
    on_macos, mac_source, _ = _ignore_rule(path, ignorecase=True)
    assert on_macos and mac_source == ".gitignore", \
        f"on a case-folding checkout (macOS, `core.ignorecase=true`) this repo's .gitignore no " \
        f"longer covers {path!r}. That is the half that matters in practice: macOS is where the " \
        "MCP browser writing these files runs. Either restore the rule or move the name out of " \
        "EXPLICIT_ARTIFACT_CASE_VARIANTS"

    on_linux, linux_source, linux_pattern = _ignore_rule(path, ignorecase=False)
    assert not on_linux, \
        f"on a case-SENSITIVE checkout (Linux, CI) {path!r} is now ignored by " \
        f"{linux_pattern!r} from {linux_source}. If a case variant was added on purpose this is " \
        "the intended tripwire: .gitignore and CLAUDE.md tell the reader this list is " \
        "CASE-DEPENDENT and that this name is covered on macOS only. Update both and move the " \
        "name into EXPLICIT_ARTIFACT_COVERED"


@requires_git_checkout
def test_no_file_of_browser_artifact_shape_is_reachable_by_git():
    """Layer two of #629: the guard that reads BYTES, so the caller's name cannot dodge it.

    The extension list forecloses names somebody thought of; this forecloses THREE FORMATS under
    any name. `browser_take_screenshot`'s `type` is an enum of png|jpeg and `browser_pdf_save`
    writes pdf, so those three are what the screenshot path can produce — and this scan is what
    actually covers `vmcp629-noext` and `vmcp629-shot.bin`, the two measured cases where a
    screenshot arrived under a name no rule matches.

    **It is complete about NAMES, not about FORMATS, and the difference was measured rather than
    reasoned.** An earlier draft of this docstring said three formats were "the whole binary
    surface"; the card's second pass disproved it by construction, and the counter-example is in
    the default capability set: `browser_network_request(part="response-body", filename=…)`
    writes the raw body of any request the page made, in whatever format the server sent. A GIF
    and a ZIP were dropped into a probe checkout's root as `.bin` files and matched nothing here.
    Extending to "any binary" is not the repair — it would mean deciding by bytes which of a
    repo's files are artifacts, which bytes cannot say.

    Candidate set is git's own answer to "what could `git add -A` publish": the index plus
    untracked-and-not-ignored. The index half is what still sees a `git add -f` and a file
    committed before any rule existed, which no ignore pattern can retract.

    Content comes from the working tree, FALLING BACK to the index blob when a tracked path has
    no worktree copy — and that branch is not a nicety. Measured: a PNG committed as `asset.bin`
    with its worktree copy deleted was listed by `git ls-files`, was invisible to `Path.is_file`,
    and the earlier version of this loop skipped it in silence while both pins stayed green. A
    candidate whose bytes cannot be read from EITHER source is reported as unreadable rather than
    skipped, on the same principle the storage-state scan applies to a file too big to parse.

    Unlike that scan this one needs NO size ceiling, and the contrast is the point: it never
    parses a file, it reads `MAGIC_PREFIX_BYTES` from the front. A credential could be grown past
    a cap; a magic number cannot be hidden by making the file bigger.

    Honest boundary, since this card is about claims outrunning evidence: the same run measured
    browser_snapshot, browser_console_messages, browser_network_requests and browser_evaluate
    writing the page's text and its request query strings into the checkout root under
    caller-chosen names, as plain text with no signature to match. Those are listed in
    EXPLICIT_ARTIFACT_UNCOVERED and are covered by nothing here. And this is a GATE, not a lock:
    red in the pre-push run and in CI, powerless against a `git commit` and blind to a file that
    never reaches this working tree at all.

    Offending PATHS are reported; contents never are.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, exactly 1 test selected per round,
    every probe a REAL artifact copied from this card's own playwright run — PNG 15,580 bytes
    starting `89504e470d0a1a0a`, JPEG 17,255 starting `ffd8ffe0`, PDF 20,220 starting
    `255044462d` — every probe deleted afterwards, and no `git checkout`/`restore` anywhere near
    a tree holding uncommitted work. Reported strings transcribed from the runs:

    * control -> PASS
    * the real screenshot as `vmcp629-shot.bin`, a name NO ignore rule covers and the exact case
      the extension list cannot reach -> FAIL, `['vmcp629-shot.bin (PNG)']`
    * the same bytes as `shot.png`, which IS ignored -> PASS, correctly: `git add -A` cannot
      take it, so it is not in the candidate set
    * that same ignored file then `git add -f`-ed into the index -> FAIL, `['shot.png (PNG)']`.
      The round that proves the index half is not decorative — and it fails
      `test_no_image_or_pdf_asset_is_tracked_today` at the same time, which is the intended
      overlap: an ignore rule cannot retract a tracked path.
    * the real JPEG as `vmcp629-shot.bin` -> FAIL, `['vmcp629-shot.bin (JPEG)']`
    * the real PDF as `vmcp629-shot.bin` -> FAIL, `['vmcp629-shot.bin (PDF)']`
    * a text file whose body merely MENTIONS `%PDF-` and a PNG header rather than starting with
      one -> PASS, so the matcher is anchored at the front and is not a substring search
    * the index-blob branch, built because the second pass found the hole it fills: a real PNG
      `git add -f`-ed as `vmcp629-committed.bin` and then DELETED from the working tree -> FAIL,
      `['vmcp629-committed.bin (PNG)']`, while `test_no_image_or_pdf_asset_is_tracked_today`
      passes on the same state (the name is `.bin`, outside its globs) — so nothing else was
      covering it. Disabling ONLY that branch, on the identical state -> PASS, which is the
      pre-fix behaviour and what makes the branch load-bearing rather than decorative.
    """
    tracked, untracked = set(), set()
    for args, sink in ((("ls-files", "-z"), tracked),
                       (("ls-files", "--others", "--exclude-standard", "-z"), untracked)):
        listed = _git(*args)
        assert listed.returncode == 0, f"git {' '.join(args)} failed: {listed.stderr.strip()}"
        sink.update(p for p in listed.stdout.split("\0") if p)

    offenders, unreadable = [], []
    for rel in sorted(tracked | untracked):
        path = REPO_ROOT / rel
        head = b""
        try:
            if path.is_file():
                with path.open("rb") as handle:
                    head = handle.read(MAGIC_PREFIX_BYTES)
            elif rel in tracked:
                # in the index, no worktree copy: read the BLOB, never skip — see docstring
                blob = _git_bytes("cat-file", "blob", f":{rel}")
                if blob.returncode != 0:
                    unreadable.append(rel)
                    continue
                head = blob.stdout[:MAGIC_PREFIX_BYTES]
            else:
                continue  # untracked and already gone: nothing git could publish either
        except OSError:
            unreadable.append(rel)
            continue
        for fmt, signature in BROWSER_BINARY_SIGNATURES.items():
            if head.startswith(signature):
                offenders.append(f"{rel} ({fmt})")
                break

    assert not unreadable, \
        f"{unreadable} — git lists these as publishable but their bytes could not be read from " \
        "either the working tree or the index, so their format is UNKNOWN. Reported rather than " \
        "skipped: 'could not look' and 'looked and found nothing' are different answers, and " \
        "conflating them is how the sibling storage-state scan was once evaded"

    assert not offenders, \
        f"{offenders} — git can publish {'a file' if len(offenders) == 1 else 'files'} whose " \
        "leading bytes are one of the three formats the MCP browser writes (PNG/JPEG from " \
        "browser_take_screenshot, PDF from browser_pdf_save). Named anything at all, that is a " \
        "picture of whatever page the browser had open, from a PUBLIC repo. The extension list " \
        "in .gitignore does not cover every name — that is expected, and is why this check " \
        "reads bytes. If the file is a deliberate asset rather than spill, it needs `git add " \
        "-f` AND a decision recorded here: this repo has tracked no image or PDF in its history"


@requires_git_checkout
def test_no_image_or_pdf_asset_is_tracked_today():
    """The PRICE argument of #629, pinned — because it is what makes these rules affordable.

    Excluding four whole extensions is only cheap while the repo has nothing of them. Measured
    2026-08-02: `git ls-files` returns nothing for any of them, and `git log --all --name-only`
    — deliberately broader than `--diff-filter=A`, since it reports ANY commit that touched such
    a path on ANY ref rather than only additions — returns nothing either. That is the same
    standing `*.html` above already had when its rule went in.

    The measurement is DATED rather than given as "over N commits", which is how the first draft
    of it read. `git rev-list --all --count` answered 378 and then 379 within the same hour, on
    the same tree, because siblings land while a card is being written — and `--all` counts other
    agents' `task/*` branches too, so it was never the denominator the sentence implied.

    The day someone `git add -f`s a legitimate diagram, that sentence in .gitignore stops being
    true and the price stops being zero — so this fails then, deliberately. It is not a vote
    against ever committing an asset; it is the requirement that doing so update the paragraph
    that currently tells the reader it has never happened.

    Distinct from the shape scan next door, which needs the file to still START with a magic
    number: this one fires on a tracked `.png` that is empty, truncated or re-encoded, and it
    reads the INDEX, which no ignore rule can retract a path from.

    MUTATION-CHECKED (real artifact, `git rm --cached` and delete afterwards): control PASS;
    `git add -f` a real screenshot as `docs/diagram.png` -> FAIL, `git now carries
    ['docs/diagram.png']`; the same forcing under the root name `shot.png` -> FAIL naming that
    instead, which is the round showing this reads the INDEX rather than a fixed path.
    """
    listed = _git("ls-files", "--", *ARTIFACT_EXTENSION_GLOBS)
    assert listed.returncode == 0, f"git ls-files failed: {listed.stderr.strip()}"
    assert not listed.stdout.split(), \
        f"git now carries {listed.stdout.split()} — the .gitignore paragraph justifying " \
        f"{list(ARTIFACT_EXTENSION_GLOBS)} says this repo tracks no image or PDF and never has, " \
        "which is what makes excluding those extensions cost nothing. If this asset is " \
        "deliberate, that paragraph has to be rewritten with the new price; if it is browser " \
        "spill, it has to come out of the index — an ignore rule does not retract a tracked path"


@requires_git_checkout
def test_no_file_of_storage_state_shape_is_reachable_by_git():
    """Layer two: the guard that does NOT depend on the name, and the reason the card shipped.

    A name list forecloses names somebody thought of. This forecloses the SHAPE. Playwright's
    storage state is a JSON object with two list-valued keys, `cookies` and `origins`
    (measured off a real 455-byte export; cookie entries carry name/value/domain/path/
    expires/httpOnly/secure/sameSite). Any file of that shape is a credential regardless of
    what it is called, so `tracker-login.json` — the exact name the ignore list cannot
    safely cover — is caught here.

    The candidate set is git's own answer to "what would `git add -A` publish": the index
    (`ls-files`, which already includes anything staged) plus untracked-and-not-ignored
    (`ls-files --others --exclude-standard`). Using `--exclude-standard` is deliberate and not
    a weakening: a path some machine's global ignore hides is a path `git add -A` will not
    take on that machine either, so the scan's scope tracks the actual risk. The index half is
    immune to ignore rules altogether, which is what makes this also cover the two cases the
    name layer structurally cannot — a `git add -f`, and a file committed before any rule
    existed. In CI, where the checkout is clean, the index half is the whole scan.

    Size is the axis this test got WRONG on its first outing, and the fix is in
    `SHAPE_SCAN_MAX_BYTES`: the ceiling on a single read is a cost bound, not a statement that
    credentials are small, so a candidate above it is REPORTED as unclassified rather than
    skipped. There is no size at which this scan quietly stops looking, because there is no
    size above which a storage state stops being one.

    Honest boundary, stated because this card is about claims outrunning evidence: this is a
    GATE, not a lock. It goes red in the pre-push `uv run pytest tests/unit -q` the
    integration recipe already requires, and in CI. It does not stop `git commit`, and it
    cannot see a file that never reaches this working tree.

    Offending PATHS are reported; contents never are. Reading one to classify it is
    unavoidable, printing it would defeat the purpose.

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test selected per round, no
    `git checkout --` anywhere near an untracked subject; every probe synthetic, every probe
    deleted): control PASS; write a real storage-state-shaped file (synthetic values) as
    `tracker-login.json`, which NO ignore rule covers -> FAIL naming that path; the same content
    under `state.json`, which IS ignored -> PASS, correctly, since `git add -A` cannot take it;
    that same ignored file then `git add -f`-ed into the index -> FAIL, the round that proves
    the index half is not decorative; a non-storage-state JSON object carrying only one of the
    two keys -> PASS, so the matcher is not "any JSON".

    And the rounds that hold the size axis, all built rather than reasoned about:

    * the same shape grown to 4,194,662 bytes by one 4 MiB localStorage entry (well inside a
      single origin's quota), still `?? tracker-login.json` -> FAIL. Against the code as it
      stood, with `SHAPE_SCAN_MAX_BYTES = 1 << 20`, that same file measured `1 passed`, and
      flipping ONLY that constant to `1 << 30` measured FAIL: the ceiling, alone, was what let
      it through. The guard was evadable by making the file BIGGER.
    * the same shape grown PAST the ceiling (67,109,154 bytes) -> FAIL, on the second
      assertion, naming it unclassified. That is the round the old code could not produce at
      all: it reported nothing.
    * the 4 MiB file again with the ceiling put BACK to `1 << 20`, nothing else touched -> still
      FAIL, and the message measured is "could NOT be classified", i.e. the file crossed from
      `offenders` to `unclassified` and the verdict did not move. Predicted PASS, measured FAIL,
      and the measurement is the stronger statement: there is no value of the ceiling at which
      this file goes quiet. Lowering it trades a precise finding for a vague one, never for
      silence — which is the whole repair, since the ceiling is now about what the scan can
      afford to read and not about how big a credential can be.
    * a large file that is NOT of the shape gets no special case: above the ceiling it is
      reported too, because not reading it is exactly why its shape is unknown. The remedy the
      message gives (`.gitignore` it) also removes it from `git add -A`'s reach, so the noise
      resolves in the same direction as the guard.
    """
    candidates = set()
    for args in (("ls-files", "-z"), ("ls-files", "--others", "--exclude-standard", "-z")):
        listed = _git(*args)
        assert listed.returncode == 0, f"git {' '.join(args)} failed: {listed.stderr.strip()}"
        candidates.update(p for p in listed.stdout.split("\0") if p)

    offenders, unclassified = [], []
    for rel in sorted(candidates):
        path = REPO_ROOT / rel
        try:
            if not path.is_file():
                continue
            size = path.stat().st_size
        except OSError:
            continue
        if size == 0:
            continue  # an empty file cannot be a JSON object
        if size > SHAPE_SCAN_MAX_BYTES:
            unclassified.append(rel)  # REPORTED, never skipped — see SHAPE_SCAN_MAX_BYTES
            continue
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue  # unreadable, not text, or not JSON — cannot be a storage state
        if isinstance(data, dict) and all(
            isinstance(data.get(key), list) for key in STORAGE_STATE_SHAPE_KEYS
        ):
            offenders.append(rel)

    assert not offenders, \
        f"{offenders} — git can publish {'a file' if len(offenders) == 1 else 'files'} shaped " \
        f"like a Playwright storage state (a JSON object with {list(STORAGE_STATE_SHAPE_KEYS)} " \
        "lists), i.e. LIVE session cookies, from a PUBLIC repo. The .gitignore name list did " \
        "not cover this name, which is expected — that is why this check reads shape instead. " \
        "Delete the file (and `git rm --cached` it if it is tracked); do not just rename it"

    assert not unclassified, \
        f"{unclassified} — bigger than the {SHAPE_SCAN_MAX_BYTES}-byte ceiling this scan will " \
        "read, so it could NOT be classified. That is reported rather than skipped on purpose: " \
        "a storage state carries the full localStorage of every origin visited (5-10 MB of " \
        "quota EACH), so a large file is precisely what a fat credential looks like, and a " \
        "silent skip here is how this guard was evaded once already. Look at what it is. If it " \
        "is not a credential: `.gitignore` it — which also removes it from `git add -A`'s reach " \
        "— or, if it genuinely has to be committable, raise SHAPE_SCAN_MAX_BYTES together with " \
        "the measurement in the comment that justifies it"


@requires_git_checkout
def test_no_storage_state_file_is_tracked_today():
    """The name-shaped version of the same question, kept because it answers a wider one.

    The shape scan above needs a file to still parse as a storage state. This one fires on a
    tracked file that merely bears one of the guarded names — truncated, re-encoded, wrapped —
    where the shape scan would shrug. Cheap, and it is the assertion that would actually fire
    on the day it matters: an ignore pattern does nothing to a path git already tracks, so a
    file added before the rules existed keeps travelling to every clone while the layer-one
    pins stay green.

    MUTATION-CHECKED: control PASS; `git add -f` a file named `storage-state-x.json` -> FAIL
    (then unstaged, and the file deleted).
    """
    listed = _git(
        "ls-files", "--",
        "*storage-state*.json", "*storage_state*.json", "*storagestate*.json",
        "state*.json", "auth.json", "cookies.json", "session.json", "*/.auth/*",
    )
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

    Three clauses, all instructions rather than tokens: that the variable is set NOWHERE here
    (the decision), that it is never written back (the measurement the decision rests on —
    without it the decision reads as caution, and caution is exactly what a later reader
    overrides), and that the `.gitignore` guard REDUCES rather than forecloses the accident.
    The third was added by the rework: the first version of this card told CLAUDE.md, the
    commit message and a test name alike that a leak was impossible, which measurement then
    disproved. An overstated guard invites exactly the "then I need not think about it" the
    first two clauses exist to prevent, so the honest bound is pinned next to them.
    Deliberately not pinned: the ENOENT detail, the capability name, the file-size evidence —
    prose that a rewrite should be free to reshape.

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test selected, CLAUDE.md restored from a
    COPY): control PASS; delete the whole paragraph while leaving `PLAYWRIGHT_MCP_STORAGE_STATE`
    mentioned in the paragraph above it -> FAIL, which is the round that proves this pin is not
    a keyword grep; keep the paragraph but soften "does NOT buy that cost back" into "may not
    help" -> FAIL; delete only the never-written sentence -> FAIL; delete only the guard-bound
    sentence while leaving `.gitignore` discussed in the same slice -> FAIL; restore the old
    overclaim ("makes accidental commit impossible") in its place -> FAIL; re-wrap the
    paragraph across all three pinned phrases -> PASS.
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
    assert "The `.gitignore` guard reduces that accident; it does not make it impossible." \
        in section, \
        "CLAUDE.md no longer states the BOUND on the .gitignore guard. That sentence is the " \
        "correction this card was bounced for: a name-based rule cannot cover a filename " \
        "nobody listed, and the first version of this text promised it could. A reader who " \
        "believes the guard is total stops checking what they commit"


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


def test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git():
    """#622: the skip must say "not applicable" WITHOUT ever being able to say it about a failure.

    A skip is the one repair that can do more damage than the bug it fixes: 30 red tests are at
    least visible, whereas 30 silently skipped ones look exactly like 30 passing ones in the
    `-q` summary line this repo reads its verdicts from. So the two claims `requires_git_checkout`
    makes are asserted here, one per branch, and this test carries no marker — it is the one that
    has to run on BOTH sides of the very condition it is checking.

    In a CHECKOUT: nothing may be skipped (the marker's condition must be false — that is the
    gate's whole value, and an inverted probe would take all 30 pins offline while the suite
    stayed green), and git must actually ANSWER. That second assertion is the trap the card named
    explicitly: a git that is present but broken here — no binary, corrupt repo, unreadable
    objects — is a FAILURE, not a missing repository, and it goes red on this line instead of
    disappearing into a skip. It can only work because the probe reads the filesystem and this
    reads git: one source cannot mask the other. `--show-toplevel` is also required to name
    REPO_ROOT itself rather than some ancestor, which is what would be happening if a `.git` ever
    turned up here belonging to a repo this tree is merely nested inside.

    In a NON-checkout: something must actually be skipped, and the reason has to name the tree —
    a skip whose reason does not say WHICH tree was found wanting is how a measurement
    environment gets misread a second time.

    `is_checkout` is RECOMPUTED here rather than read from `_IS_GIT_CHECKOUT`, and that is the
    whole reason this test can fail at all. Its first version branched on the module constant —
    the thing under test — so inverting the probe merely sent this test down the OTHER branch,
    where every assertion held. Measured, in a real checkout: `11 passed, 30 skipped`, no
    failures: the gate silently offline and the suite green, i.e. precisely the outcome this
    docstring claims to forbid, produced by the round written to prove it could not happen. The
    local `dot_git.exists()` is therefore a SECOND, independent statement of the contract — "the
    marker skips exactly when REPO_ROOT has no `.git`" — and defining the probe some other way
    has to come and reconcile with it here, on purpose.

    MUTATION-CHECKED (`__pycache__` cleared between rounds, the WHOLE file selected per round so
    that the skip count is visible and not just the failure count, source restored from a COPY):
    control PASS in the checkout and PASS in a `git archive` extraction; invert the probe to
    `not (REPO_ROOT / ".git").exists()` -> FAIL in the checkout, the round the first version of
    this test passed; force `_IS_GIT_CHECKOUT = True` inside an extraction -> FAIL; leave the
    constant honest and invert only the MARKER's condition -> FAIL, the round that shows the two
    assertions cover two different mutation sites.

    The last two assertions need a broken TREE rather than a broken source, so they were built:
    a `.git` file pointing at a gitdir that does NOT exist (which takes some doing — see below)
    -> the whole file measures `31 failed, 10 passed, 0 skipped`, i.e. the 30 pins go red exactly
    as they did before this marker existed, plus this one naming the cause, while the 10 that
    pass are precisely the pins that never needed git. Nothing hides in a skip, which is the
    property; and a `.git` file pointing at a repo whose `core.worktree` names a DIFFERENT
    directory -> FAIL on the REPO_ROOT comparison, git having answered exit 0 about that other
    tree. That second one had to be hunted for, and the hunt is the finding: a nested extraction
    does NOT reach it (the probe returns early), and neither does a copied linked worktree — its
    `.git` file still points at a gitdir that EXISTS, and git derives the toplevel from where the
    `.git` file SITS, so `rev-parse` succeeds and names the copy. A dangling gitdir is therefore
    a rarer shape than it first looks (the source repo has to be gone, or on another machine), not
    the ordinary residue of an interrupted worktree operation. Reachable only via `core.worktree`,
    but reachable — which is what stops that line from being an assertion no round can arrive at,
    the kind this file elsewhere calls a claim nobody has tested.
    """
    dot_git = REPO_ROOT / ".git"
    is_checkout = dot_git.exists()  # recomputed, NOT read off the constant — see docstring
    skips = requires_git_checkout.mark.args[0]
    reason = requires_git_checkout.mark.kwargs.get("reason") or ""

    assert _IS_GIT_CHECKOUT is is_checkout, \
        f"the checkout probe says {_IS_GIT_CHECKOUT}, but {dot_git} " \
        f"{'exists' if is_checkout else 'does not exist'}. The contract is exactly `.git` at " \
        "REPO_ROOT: read any other way, the probe can call a real checkout derived — taking all " \
        "30 git-backed pins offline while the suite stays green — or call a `git archive` " \
        "extraction a checkout and put the 30 red failures back (tracker #622)"
    assert skips == (not is_checkout), \
        f"requires_git_checkout would {'SKIP' if skips else 'RUN'} the git-backed pins in a " \
        f"tree that {'IS' if is_checkout else 'is NOT'} a checkout. Skipping them in a real " \
        "checkout takes the storage-state gate offline while the suite reports nothing but " \
        "green, which is strictly worse than the 30 red tests this marker replaced"

    if not is_checkout:
        assert str(REPO_ROOT) in reason, \
            "the skip reason no longer names the tree it applies to. Whoever reads a summary " \
            "line of skips next has to be told WHICH directory was judged not-a-checkout, or " \
            "they are back to guessing what their measurement environment did"
        return

    probe = _git("rev-parse", "--show-toplevel")
    assert probe.returncode == 0, \
        f"{dot_git} exists, so this tree IS a repository, but git could not answer " \
        f"`rev-parse --show-toplevel` (exit {probe.returncode}): {probe.stderr.strip()}. That " \
        "is a BROKEN git, not a missing repository, and the difference is the whole point: it " \
        "must be red here rather than skipped by the marker, which is why the marker reads the " \
        "filesystem and never git's exit code"
    assert Path(probe.stdout.strip()).resolve() == REPO_ROOT, \
        f"git says the enclosing repository is {probe.stdout.strip()!r}, not {REPO_ROOT}. The " \
        "pins in this file assert about THIS tree, so a git that answers about an ancestor " \
        "would be answering a question nobody asked"


def test_every_pin_here_that_shells_out_to_git_declares_it():
    """The marker has to keep being APPLIED, and remembering is not a mechanism.

    The 30 failures this card removed were not written deliberately — they accumulated, one pin
    at a time, because nothing connected "this test runs git" to "this test needs a checkout".
    Leave that connection to reviewers and the next git-backed pin re-opens the hole for the
    next derived measurement environment to fall into.

    So the rule is enforced from the source: a test function that reaches git — through `_git`,
    `_git_bytes` or `_ignore_rule`, the doors named in `GIT_CALL_MARKERS` — must either carry
    `requires_git_checkout` or branch on `_IS_GIT_CHECKOUT` itself. That the list is a CONSTANT is
    itself load-bearing: #629 added a third door, and a door left out of the list is one this
    scanner cannot see. The second alternative is not a loophole, it is what
    `test_the_checkout_probe_is_not_an_off_switch_for_a_broken_git` needs in order to assert
    anything about the checkout branch at all: it must run unmarked, on both sides.

    The marker is matched as the WHOLE mark, not by its name: an `Mark(name="skipif", …)` with
    some other condition would otherwise satisfy this, and "skipped for an unrelated reason" is
    not the property being enforced. What stays approximate — stated because this file's habit is
    to price its guards rather than round them up — is the escape hatch: `_IS_GIT_CHECKOUT` is
    looked for as a SUBSTRING of the function's source, docstring included, so a test that merely
    mentions the constant in prose while calling git unguarded would pass. That bound is accepted
    rather than engineered away: the hatch exists for exactly one test, taking it is a deliberate
    act by someone editing this file, and the behaviour it protects is held on the checkout side
    by the pin above regardless.

    (`GIT_CALL_MARKERS` is a constant rather than two literals here for a mundane reason: written
    inline, this scanner's own source would contain the tokens and it would report itself.)

    MUTATION-CHECKED (`__pycache__` cleared, exactly 1 test selected per round): control PASS;
    remove `@requires_git_checkout` from `test_no_storage_state_file_is_tracked_today` -> FAIL
    naming that function alone; remove it from the parametrised
    `test_the_playwright_output_dir_is_excluded` -> FAIL naming that one, so a decorator stacked
    above `parametrize` is seen too; add a new unmarked test that calls `_ignore_rule` -> FAIL
    naming it; replace the marker with an UNRELATED `pytest.mark.skipif(False, reason=…)` ->
    FAIL, the round that distinguishes matching the whole mark from matching its name, and which
    an earlier version of this scanner passed.
    """
    unguarded = []
    for name, obj in sorted(globals().items()):
        if not name.startswith("test_") or not callable(obj):
            continue
        source = inspect.getsource(obj)
        if not any(marker in source for marker in GIT_CALL_MARKERS):
            continue
        marked = any(
            mark == requires_git_checkout.mark for mark in getattr(obj, "pytestmark", [])
        )
        if not marked and "_IS_GIT_CHECKOUT" not in source:
            unguarded.append(name)

    assert not unguarded, \
        f"{unguarded} shell out to git without declaring that they need a git checkout. Add " \
        "`@requires_git_checkout` (or branch on `_IS_GIT_CHECKOUT` if the test must run on both " \
        "sides). Outside a checkout — a `git archive`/sdist extraction, a copied tree, a " \
        "mutation-sweep environment — such a test cannot pass and cannot mean anything: it " \
        "reports `fatal: not a git repository … assert 128 == 0`, which is indistinguishable " \
        "from a real finding in a `-q` summary and CONSTANT, so it survives every before/after " \
        "comparison looking like signal. That is tracker #622, and it already corrupted one " \
        "mutation sweep's numbers (#594) by exactly the count of tests in this position"
