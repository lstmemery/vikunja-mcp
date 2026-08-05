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
import re
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
# landed in the checkout root as `.bin` and matched nothing here. One more is a cap away —
# `browser_start_video`, absent from the default set and present with
# `PLAYWRIGHT_MCP_CAPS=devtools`, NAMES a WebM under the caller's own `filename`; the bytes
# (`1a45dfa3…`) reach the root of the server's cwd when `browser_stop_video` stops the recording,
# not when `start` returns. So the scan below is complete
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

# --- #703: what SKILL.md PRESCRIBES as a caller-chosen `filename` ---------------------------
#
# The residual #629 measured and left open on purpose: the four tools that write the page's own
# TEXT (browser_snapshot / browser_console_messages / browser_network_requests /
# browser_evaluate). Neither layer above can reach them — the names measured were `.md`, `.txt`,
# `.json` and one with no extension, which is what this repo's own content looks like, and plain
# text has no leading signature to match. So #703 fixes it at the WRITE SITE instead: SKILL.md
# stops printing a bare name and prints a path under `.playwright-mcp/`, which #607's directory
# rule already covers wholesale — the one axis that is blind to both the name and the format.
#
# Measured while doing #703 (@playwright/mcp 0.0.78, own `--isolated --headless` server, own
# stdio client, throwaway origin, cwd = a real checkout of this repo):
#   * all four text writers accept the prefix and land in `.playwright-mcp/`, where
#     `git check-ignore --no-index -v` answers `.gitignore:26:.playwright-mcp/`;
#   * they also accept a SUBDIRECTORY (`src/vikunja_mcp/…md` landed beside the sources,
#     unignored), so the spill was never root-confined and a root-only rule could not have
#     covered it;
#   * `--output-dir` cannot be the fix: a caller-chosen `filename` is resolved by a different
#     function than the auto-named artifacts (`workspaceFile()` against the workspace/cwd versus
#     `outputFile()` against the output dir), so with `--output-dir` pointed OUTSIDE the repo the
#     auto-named files moved there and the explicit one still landed in the checkout root.
SKILL_MD_PATH = REPO_ROOT / "src" / "vikunja_mcp" / "skills" / "tracker" / "SKILL.md"

# The directory the rule now sends every caller-chosen artifact to — the browser's own output
# dir, excluded wholesale by #607's rule. Named once because two different assertions depend on
# it: that git excludes what SKILL.md prescribes, and that SKILL.md prescribes THIS place.
PLAYWRIGHT_OUTPUT_DIR = ".playwright-mcp"

# The `filename` value SKILL.md hands an agent to copy, in the spellings a rulebook actually uses:
# the Python-ish call form `filename="x"`, the JSON form `"filename": "x"` (which is what an MCP
# argument literally is), spaces around the separator, and a line wrap after it — SKILL.md is
# hand-wrapped near 90 columns, so the wrap is an editing accident waiting to happen rather than an
# adversarial case. A bare mention of the word (`` `filename` ``, of which this rulebook has
# several) carries no separator and is deliberately not a prescription.
#
# This list came from an independent pass whose brief was to defeat the pin, and it found the first
# two forms unseen by the original `filename=`-only pattern — a prescription written in JSON left
# the gate green. It is WIDER now and still not complete: no pattern over prose can be, and the
# docstring below says so instead of implying otherwise.
PRESCRIBED_FILENAME_RE = re.compile(r"""["'`]?\bfilename\b["'`]?\s*[:=]\s*["'`]?([^"'`\s,)]+)""")

# A floor, not a count: SKILL.md prints two such calls today (a screenshot and a snapshot) and
# the pin must not go quiet if a regex change silently matches nothing. Deliberately NOT pinned
# to the exact number — adding a third prescribed call is a fine thing to do and must not be a
# test edit.
MIN_PRESCRIBED_FILENAMES = 2

# --- #736: the OTHER path into the same directory — `--output-dir` ---------------------------
#
# #703 closed the caller-chosen `filename`. Beside it, untouched by that card, stood the recipe
# an agent runs to get its OWN browser at all:
#
#     npx -y @playwright/mcp@latest --isolated --headless --output-dir <каталог с id задачи>
#
# That flag feeds `outputFile()`, i.e. the AUTO-named artifacts — and one of them, `page-*.yml`,
# is the aria snapshot, which is the page's TEXT: exactly the payload #703 exists for, arriving
# by a different door. `<каталог с id задачи>` reads equally as a subdirectory of the worktree
# and as one of the scratchpad, and the first reading is not covered by anything.
#
# MEASURED for this card (@playwright/mcp@latest, own `--isolated --headless` server, own stdio
# client, throwaway origin on 127.0.0.1:20736, cwd = this worktree):
#   * `--output-dir 736-out` (inside the tree, named after the task exactly as the old recipe
#     invites) wrote `page-<ts>.yml` + `console-<ts>.log` + `page-<ts>.png`. `git status` showed
#     `?? 736-out/`; `git check-ignore --no-index -v` exited 1 for the `.yml` and the `.log` and
#     0 only for the `.png` (`.gitignore:123:*.png`, #629's rule); `git add -A --dry-run` STAGED
#     both text files. Their contents carried the probe markers planted on the page — the aria
#     text, and a link's `?token=` query string in the `.yml`, another in the `.log`.
#   * `--output-dir .playwright-mcp/736`: same three files, `git status` empty, `git add -A
#     --dry-run` empty, every one answering `.gitignore:26:.playwright-mcp/`.
#   * `--output-dir` pointed OUTSIDE the repo works — no `File access denied … outside allowed
#     roots`. That refusal is real but belongs to the caller-chosen `filename` path (a different
#     resolver); `--output-dir` DEFINES a root rather than escaping one. So "outside" is a valid
#     answer too, and SKILL.md says so; it is not the PRESCRIBED one, because it splits one
#     server's output across two places and leaves `.playwright-mcp/` uncreated.
#   * WHEN the directory appears: not at server start and not after `initialize` — the first
#     `browser_navigate` creates it, INCLUDING a missing `.playwright-mcp/` parent, after which a
#     caller-chosen `filename` under that prefix succeeded with no manual `mkdir -p`.
#
# The value SKILL.md prints after `--output-dir`, in the spellings this rulebook uses: a fenced
# command line, and prose where the flag and its value sit inside ONE backtick pair (`` `--output
# -dir .playwright-mcp/<id>` ``). What keeps the many BARE mentions of the flag out is `\s+`, and
# nothing cleverer: SKILL.md has nine `--output-dir` occurrences and seven of them are
# `` `--output-dir` `` followed by prose, where the next character is the closing BACKTICK, not
# whitespace, so the pattern never starts a value there.
#
# A negative lookahead `(?!`)` stood here first, with a comment crediting it for exactly that.
# The sweep disproved it: on the two rulebook-facing files, control 0 failed; deleting the
# lookahead measured 0 failed too, because `\s+` had been doing the work all along. Dead code is
# bad enough; dead code with a paragraph explaining its importance is what this repo keeps
# catching, so it is gone rather than kept "for safety".
#
# BOTH separators, and the `=` half is here because an independent attack pass BUILT the evasion,
# exactly as one did for `PRESCRIBED_FILENAME_RE` twenty lines up — the same defect, reintroduced
# by a pattern written from the spelling that happened to be in the file. Measured on the server
# first: `--output-dir=736-out` behaves identically to the space form (three artifacts in
# `736-out/`, `git add -A --dry-run` staging the `.yml` and the `.log`). Then on the gate, with
# only `\s+` here: control 0 failed; recipe rewritten to `--output-dir=554-out` -> 0 failed. Fully
# green while the line an agent copies spilled the page's text, because the recipe stopped
# matching and the lone prose value satisfied every assertion below. With `(?:\s+|=)` the same
# round measures 1 failed, and the equals form pointed at the RIGHT directory measures 0 — caught
# without a false red.
#
# The residual is real and is left LOUD on purpose: this reads PROSE. An `--output-dir` written
# WITHOUT backticks in a sentence hands the next word to git as if it were a path — measured, a
# round that unbackticks one prose mention goes red naming a Russian word. That is a false RED,
# which is noise; the failure mode this repo refuses is the quiet one.
PRESCRIBED_OUTPUT_DIR_RE = re.compile(r"--output-dir(?:\s+|=)([^\s`]+)")

# Same shape of floor as MIN_PRESCRIBED_FILENAMES, same reason: a regex that matches nothing
# passes everything. SKILL.md prints two such values today (the recipe and one prose restatement),
# and the floor stays at 1 rather than 2 for the same reason as its neighbour — adding or dropping
# a prose restatement is a fine edit and must not be a test edit. What that costs is measured, not
# guessed: a PROSE mention alone satisfies this floor, so deleting the flag from the fenced recipe
# left everything here green (control 0 failed; that mutation 0 failed).
#
# An earlier version of this comment went one step further and said raising the floor "would not
# have caught it either". That was a counterfactual, not a measurement, and an attack pass
# measured it FALSE: control 0 failed; floor 2 + the flag deleted from the fence -> 2 failed, one
# of them this very floor, because SKILL.md prints exactly ONE prose restatement today. The
# argument survives, the claim did not — a floor COUNTS, it cannot LOCATE, so at 2 it would catch
# this by accident today and false-red tomorrow on a correct edit that drops a restatement. The
# runnable line is pinned where a runnable line belongs, inside the fence, in
# test_skill_contract.py.
MIN_PRESCRIBED_OUTPUT_DIRS = 1

# --- #751: the SAME directory again, set from a CONFIG FILE rather than from a flag -----------
#
# `--config <path>` reads a JSON file, and `{"outputDir": …}` in it sets exactly what
# `--output-dir` sets. MEASURED for this card on the same 0.0.78 (own `--isolated --headless`
# server, own stdio client, own throwaway origin on 127.0.0.1:20751, cwd = this worktree): with
# an `"outputDir"` naming `cfg-out-751`, the three auto-named artifacts landed there, `git
# status` showed `?? cfg-out-751/`, and `git add -A --dry-run` STAGED `page-*.yml` and
# `console-*.log`. Their contents carried the probe markers — the aria text and a link's
# `?token=` query string in the `.yml`, the console line and the same token in the `.log`. Only
# the `.png` was covered, by #629's `*.png`. That is #736's leak arriving through a third door.
#
# TWO THINGS THE CARD'S OWN FRAMING GOT WRONG, both measured rather than reasoned, and both
# shaping this pin instead of just its comment.
#
# FIRST, "the THIRD door" undercounts, which is the same overclaim-by-enumeration this file has
# been bitten by before. Two ENV VARS set the same directory with no flag on the line at all:
# `PLAYWRIGHT_MCP_OUTPUT_DIR=env-out-751` put the three artifacts in `env-out-751/`, and
# `PLAYWRIGHT_MCP_CONFIG=<path>` picked up the very same config file. So enumerating FLAGS can
# never close this; the axis is the DIRECTORY, which is why the prescription SKILL.md states —
# and this pin checks — is a directory rather than a flag list.
#
# SECOND, and it is what keeps the exposure small: the EXPLICIT `--output-dir` flag BEATS all
# three. Measured pairwise — the flag against the config, against `PLAYWRIGHT_MCP_OUTPUT_DIR`,
# and against `PLAYWRIGHT_MCP_CONFIG` — every round put the three files in `.playwright-mcp/751`
# and created the foreign directory NOT AT ALL. So the recipe is safe from a stray config and
# from an inherited env var for as long as it keeps its flag, which is pinned inside the fence by
# `test_the_browser_answer_leads_with_the_isolation_an_agent_can_launch_itself`. What is left
# open is the agent that DROPS the flag and configures the server instead, and that is what
# SKILL.md now names and this pattern now reads.
#
# The JSON spelling, because that is what an `--config` file holds and what SKILL.md prints:
# `"outputDir": "<value>"`. The value stops at the closing quote, at a comma or at the closing
# brace, so an inline one-key object reads correctly. A BARE `` `"outputDir"` `` in prose does
# NOT match and must not: there is no colon after it, so the pattern never starts a value there —
# which is what lets the paragraph above NAME the key without handing this pin a word to check.
PRESCRIBED_CONFIG_OUTPUT_DIR_RE = re.compile(
    r"""["'`]?\boutputDir\b["'`]?\s*:\s*["'`]([^"'`\s,}]+)"""
)

# Same shape of floor and same reason as its two neighbours: a pattern that matches nothing
# passes everything. SKILL.md prints exactly ONE such value today — the prescription in the
# `--config` bullet — and the floor sits at 1 because that is also the honest minimum: this is a
# rule about where a config must point, not a demand that the rulebook carry N examples.
MIN_PRESCRIBED_CONFIG_OUTPUT_DIRS = 1

# What an `--output-dir` actually receives, so the pin asks git about a FILE rather than about a
# directory name. Both are auto-named artifacts measured landing there in this card's runs, and
# both are the case no other rule in this repo reaches: the `.yml` is the page's aria text, the
# `.log` its console (URLs with query strings included).
#
# The `.png` from the same run is deliberately NOT probed, and the reason is narrower than the
# first draft of this comment claimed. #629's `*.png` is DIRECTORY-BLIND — measured,
# `git check-ignore` answers `.gitignore:123:*.png` for `554-out/page-<ts>.png` and for
# `any/dir/at/all/page-x.png` alike — so a png probe can never report a directory drift. Measured
# rather than reasoned: narrowing the probes to the png AND drifting the recipe to `554-out`
# still went red, but on the separate RULE assertion below, with the git-backed check green.
# So a png probe would not make this pin decoration; it would make the GIT-BACKED half of it
# blind, leaving the rule assertion below as the only assertion HERE that still sees a directory
# drift — and that one checks the prefix as a string, not what git would do with it.
AUTO_NAMED_PROBES = ("page-2026-08-02T22-28-55-167Z.yml", "console-2026-08-02T22-28-55-042Z.log")

# Playwright's storage-state schema, measured off a real export: a JSON object with exactly
# these two list-valued keys (cookie entries carry name/value/domain/path/expires/httpOnly/
# secure/sameSite; origin entries carry origin/localStorage). This is what the name-independent
# guard matches on.
STORAGE_STATE_SHAPE_KEYS = ("cookies", "origins")

# ---------------------------------------------------------------------------------------------
# The browser's TEXT artifacts, which until VMCP-209 (752) had no content lock at all — the
# asymmetry that card was filed for. The binary ones are held by leading magic bytes under any
# name (#629) and a storage state by the two list-valued keys above; the text ones were held only
# by PROSE rules, which pin what the rulebook PRINTS (#703's explicit `filename`, #736's
# `--output-dir`, #751's `--config`) and not what lands on disk. Every future flag the rulebook
# does not print walks past all three.
#
# `.gitignore` used to say a content axis had been "considered and dropped", on two grounds. Both
# were re-measured on this card and only ONE of them survived.
#
# WRONG: "it fires on any file that DOCUMENTS it — this comment included." That is true of a
# MARKER GREP and false of the classifier below, and the difference is the same one the
# storage-state gate already relies on: that gate does not scan for the string `"cookies"`, it
# requires the candidate to BE a JSON object with two list-valued keys, so a file that merely
# discusses the shape is not a candidate. The text analogue is a WHOLE-FILE grammar — EVERY
# non-blank line must match, over a minimum line count — which makes a fenced six-line quotation
# inside two hundred lines of prose a non-candidate rather than a false red. Measured both ways
# on this tree, and the two answers differ only AFTER this card lands: see
# `test_the_naive_marker_grep_would_be_red_on_arrival_and_the_whole_file_grammar_is_not`.
#
# RIGHT, and narrowed rather than dropped: "it reaches `browser_snapshot` alone of the four."
# Measured against real artifacts from an own `--isolated --headless` server (0.0.78 line,
# `@playwright/mcp@latest` on 2026-08-05, own throwaway origin, own stdio client), it reaches
# THREE of the four text writers, and the fourth is out of reach for a structural reason rather
# than for want of trying: `browser_evaluate` writes whatever the evaluated JS returned, so it
# has no grammar to match. Its measured output was the JSON string literal
# `"<page text with \n escapes>"`, which is indistinguishable from any other JSON string — the
# one place the retired word "indistinguishable" is still the right one.
#
# The grammars, transcribed from those runs rather than from the filing card (which had two
# details wrong, both corrected here: the console level is `WARNING`, not `WARN`, and a `ref`
# token is frame-qualified — `[ref=f1e1]` — on any page that is not the first document):
#
#   aria snapshot (`page-*.yml`, and `browser_snapshot(filename=…)` byte-identically):
#       - generic [active] [ref=e1]:
#         - heading "…" [level=1] [ref=e2]
#         - link "go next" [ref=e4] [cursor=pointer]:
#           - /url: /next?token=…
#   console  (`console-*.log`):   [      54ms] [LOG] … @ http://…:9
#   console  (explicit filename): Total messages: 3 (Errors: 1, Warnings: 1) / [LOG] … @ …
#   network  (explicit filename): 2. [GET] http://…?token=… => [404] Not Found
#
# Two properties of the aria form were probed rather than assumed, because a matcher requiring
# EVERY line to match is only as good as its worst input. Multi-line page content — `<pre>`,
# `<textarea>`, `<blockquote>` — is FLATTENED onto one line, and embedded quotes are escaped, so
# the one-item-per-line grammar survives content designed to break it. And the `ref` counter
# restarts per snapshot but keeps its `[ref=` prefix in both spellings.
_ARIA_ITEM_LINE = re.compile(r"^\s*-\s\S")
_ARIA_REF_TOKEN = re.compile(r"\[ref=[0-9a-z]+\]")
# Two thresholds, and both are floors on EVIDENCE rather than on size. A single `- foo` line is
# an ordinary markdown bullet; a single `[ref=e1]` is somebody quoting one. A snapshot of even
# an empty body carries the root node plus its children, and the smallest real one measured here
# was 6 lines with 6 refs.
ARIA_MIN_ITEM_LINES = 3
ARIA_MIN_REF_LINES = 2
_CONSOLE_TIMED_LINE = re.compile(r"^\[\s*\d+ms\]\s\[[A-Z]+\]\s")
_CONSOLE_PLAIN_LINE = re.compile(r"^\[[A-Z]+\]\s")
_CONSOLE_TOTALS_HEADER = re.compile(r"^Total messages: \d+")
_NETWORK_REQUEST_LINE = re.compile(r"^\d+\.\s\[[A-Z]+\]\s\S+\s=>\s\[\d+\]\s")
_NETWORK_NOTE_LINE = re.compile(r"^Note: ")

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
GIT_CALL_MARKERS = (
    "_git(", "_git_bytes(", "_ignore_rule(",
    # VMCP-141 (630) lifted the shape scan into a helper so its pins could drive the SHIPPED
    # scanner instead of a copy. That put a git call one level of indirection away, and this
    # gate reads a test's OWN source — so the refactor would have exempted every caller.
    # The door is the call carrying REPO_ROOT, not the helper: a pin that hands it a
    # throwaway clone it built itself runs fine outside a checkout, and marking those would
    # SKIP tests that work — trading a false alarm for lost coverage, which is the same
    # trade #622 exists to refuse, just pointing the other way.
    "_scan_for_storage_state_shape(REPO_ROOT",
    # VMCP-209 (752) added a second scan over the same walk, and its own false-red pin drives
    # the shared walk directly. Same door, same rule: the marker is the call carrying REPO_ROOT.
    "_scan_for_browser_text_artifact_shape(REPO_ROOT",
    "_publishable_copies(REPO_ROOT",
)

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


def _git(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """git, always rooted at the repo — never at whatever cwd pytest happened to be started in."""
    return subprocess.run(
        ["git", *args], cwd=cwd or REPO_ROOT, capture_output=True, text=True
    )


def _git_bytes(*args: str, cwd: Path | None = None) -> subprocess.CompletedProcess:
    """git, rooted at the repo, returning RAW BYTES — for reading a blob straight out of the index.

    Exists because a candidate's content is not always on disk: a path can be in the index with no
    worktree copy (deleted but not staged), and reading only the worktree turns that into a silent
    skip. Separate from `_git` because that one decodes as text, which mangles the first bytes of
    a PNG — the exact thing the caller is trying to look at.
    """
    return subprocess.run(["git", *args], cwd=cwd or REPO_ROOT, capture_output=True)


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
    checkout — of which six write and one (`browser_run_code_unsafe`) reads. Measured on 0.0.78
    by spawning the server and reading `tools/list` over stdio. What says the SHARED server is on
    that set is its ROSTER, not the absence of a `--caps` flag: capabilities also arrive by
    `PLAYWRIGHT_MCP_CAPS` (the channel this file's cap-setting measurements use) and by
    `--config`. Nor does the flag settle it the other way: measured, `--caps bogus` serves the
    default 24, for the reason recorded below. So the bare `npx @playwright/mcp@latest` in the
    plugin's own `.mcp.json` is evidence and not proof.
    Measured on the roster instead: the browser tools this session is offered are these same
    default 24, name for name, with no `browser_pdf_save`, `browser_start_video` or
    `browser_storage_state` among them.

    A tool total "with every capability on" is deliberately NOT the anchor used for the
    non-default tools named around here, and that is the correction this round was bounced for.
    The label APPEARED in 690d648 three times over, in three files and on three different
    quantities: `.gitignore` hung it on a tool total of 53, CLAUDE.md on an acceptor count of
    "ten", this docstring on "ten and eight" acceptors and writers. Only the first is a TOTAL,
    which is what this paragraph retracts; the other two are wrong too and are corrected below
    (11 and nine). A pickaxe search for the lower-case phrase finds only the first two, and TWO
    things hide the third from it independently: the phrase wraps across a line break there, and
    it opens a sentence — `git log -S` is case-sensitive, so even unwrapped the capital `W`
    would not match. Do not read a COMMIT COUNT off that search either: a sentence quoting the
    phrase writes it back into a file the search reads, so its answer moves with the discussion
    rather than with the defect.
    53 is not that set, and 53 does not identify a set at all: measured,
    `pdf,devtools,storage` gives 53 tools with 11 `filename` acceptors, while
    `config,devtools,storage` and `pdf,storage,vision,testing` each give 53 with 10 — so which
    run produced that sentence cannot be recovered from the number. Every capability on really is
    69, i.e. all twelve members of the `ToolCapability` union in the package's own `config.d.ts`,
    also with 11 acceptors. Three measured traps stand behind the muddle. Unknown cap names are
    accepted SILENTLY: `PLAYWRIGHT_MCP_CAPS=bogus` starts cleanly, writes nothing to stderr and
    serves the default 24, so a cap that does not exist reads exactly like a cap that adds
    nothing.
    And "a cap that adds nothing" is itself CHANNEL-DEPENDENT, which is the trap that put
    `tracing` and `verify` into these cap lists in the first place (#704). The two channels agree
    on all twelve DECLARED caps — `--caps=<all 12>` and `PLAYWRIGHT_MCP_CAPS=<all 12>` both serve
    69 with the same 11 acceptors — so it is tempting to call them equivalent; they are not.
    Measured: `--caps=tracing` serves 35 and `PLAYWRIGHT_MCP_CAPS=tracing` serves 24, and
    `--caps=pdf,tracing` serves 36 against the env form's 25. The cause is in the bundled
    `playwright-core/lib/coreBundle.js`, in the CLI's own `.action()` handler —
    `if (options.caps?.includes("tracing")) options.caps.push("devtools")` — an undeclared
    back-compat alias that the env path (`configFromEnv`) never runs, so `tracing` is a live
    +11 on the flag and inert as a variable. `verify` serves 24 on BOTH and is simply not a
    capability, despite `browser_verify_*` tools existing under `testing`. So a cap sweep is
    comparable only against sweeps taken through the SAME channel, and "measured +0" means
    nothing without saying which one carried it.
    And "writers = acceptors − 1" stops holding once `storage` is on, because
    `browser_set_storage_state` reads its file too ("Path to the storage state file to restore
    from") — by the schemas that is 11 acceptors and NINE writers, not ten. Those two numbers are
    read off the schemas; the writing is not, and it is not confined to the default set either.
    Every one of the nine has its output recorded here as OBSERVED: the six default ones above,
    `browser_pdf_save` (the PDF signature at `BROWSER_BINARY_SIGNATURES` was transcribed from a
    file it wrote, and its own default name is pinned in `EXPLICIT_ARTIFACT_COVERED`),
    `browser_start_video` (the WebM — but measured, it only NAMES the file and returns with the
    cwd empty; the bytes arrive when `browser_stop_video` stops the recording, so the acceptor
    and the write are not the same call), and `browser_storage_state` (`state585.json`, 455
    bytes, at the storage-state test above — card #585's run, not this card's). Counts move with
    an external package pulled at a floating `@latest`; a cap name does not. Reproducing either
    costs a server spawn plus the `initialize` / `initialized` / `tools/list` handshake —
    0.0.78's `--help` lists no tools, so there is no one-command form. browser_snapshot,
    browser_console_messages, browser_network_requests and browser_evaluate all put their output
    in the checkout root too.
    Measured content: a marker planted in the probe page's text came back inside the snapshot,
    the extensionless snapshot and the evaluate result; a token placed in a request's query
    string came back in the console and the network dumps. They are plain text under a caller's
    name — no extension worth excluding, no magic number to read — so the honest statement is
    that this guard reduces the accident and does not foreclose it (filed as #703, since closing
    it means changing what SKILL.md asks agents to pass, not adding a rule). Widening is welcome;
    it has to turn this red first and move the sentence in .gitignore with it.

    One clause of the round that shipped this docstring is RETRACTED here: it said
    `browser_network_request` (singular) "was never exercised here" and counted it among the six
    by its own schema rather than by observation. The same round's second pass then drove exactly
    that tool with `part: "response-body"` plus a `filename` and landed a GIF and a ZIP in a probe
    checkout's root — recorded above at `BROWSER_BINARY_SIGNATURES`, i.e. the file contradicted
    itself as shipped. That writer is observed, not inferred.

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
    One more format needs only a capability: `browser_start_video` is absent from the default
    set and present with `PLAYWRIGHT_MCP_CAPS=devtools`; the WebM it NAMES (`1a45dfa3…`) reaches
    the root of the server's cwd when `browser_stop_video` stops the recording.
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
def test_every_filename_skill_md_prescribes_is_excluded_by_this_repos_gitignore():
    """#703: the one axis that reaches the TEXT writers, pinned across BOTH files it depends on.

    #629 closed the binary surface with two layers and recorded the residual in prose: the four
    tools that write the page's own text under a caller-chosen name are reachable by neither.
    That was not an oversight in either layer, it is what those layers ARE. An extension list
    cannot help — the names measured were `.md`, `.txt`, `.json` and one with no extension, and
    this repo tracks 7 `.md`, 3 `.json`, a `.yml` and two extensionless files, so any pattern wide
    enough to catch the spill hides the repo's own content. A signature cannot help either: the
    payload is plain text.

    What is left is the DIRECTORY, and it costs one rule that is already there (`.playwright-mcp/`,
    #607) and blind to both name and format. So the fix is a change to what SKILL.md tells agents
    to PASS — which is why #629 refused to make it and filed this card instead.

    A prescription is a sentence, and this repo's own .gitignore says what a sentence of discipline
    is worth. This is the mechanism under it, and it is deliberately a CROSS-FILE pin rather than a
    string match: it takes every `filename` value SKILL.md prints and asks GIT whether this repo
    would publish it. It therefore goes red from EITHER side — a rulebook that drifts back to a
    bare name, or a `.gitignore` that stops covering the directory the rulebook now depends on.

    WHAT IT CANNOT DO — found by an independent pass that BUILT the evasion rather than arguing it,
    and re-measured here. The pin sees only a prescription written in a spelling
    `PRESCRIBED_FILENAME_RE` matches. Same clone, same selection, control 0 failed: against the
    first version of the pattern (`filename=` alone) a rule added to SKILL.md as
    `{"filename": "vmcp-703-console.txt"}` — a bare name, and the form an MCP argument literally
    takes — measured 0 failed, i.e. the rulebook prescribed the leaking form and nothing went red;
    with the pattern as it now stands, the identical edit measured 1 failed. It covers that form,
    spaces around the separator and a line wrap after it (this file is hand-wrapped, so the wrap is
    an editing accident, not an attack), and it remains a pattern over PROSE: a value that reaches
    an agent by some other spelling reaches this pin by none. Same class one level up, also built:
    a sentence AFTER the pinned clauses that contradicts them ("if the directory is missing, just
    pass a bare name") leaves everything here green. These are gates on the text this repo ships,
    not proofs about what an agent will do with it.

    The last two assertions are what keep the pin from decorating, and BOTH exist because the
    sweep below disproved a predicted round. The git-backed checks alone were measured GREEN when
    the prefix was stripped from the screenshot example — correctly, since `*.png` covers that name
    in any directory, so "would git publish it" simply cannot see the drift. Hence one assertion on
    the RULE as SKILL.md states it (every prescribed value under the output dir), and one on the
    EXAMPLES (at least one whose bare basename git WOULD publish, i.e. one the directory alone
    saves — the `.md` snapshot, which is the case this whole card is about). Without the second, a
    rulebook whose examples had drifted to screenshots only would keep this pin green while
    teaching agents nothing #629 had not already covered.

    Honest boundary, stated because this card's family is claims outrunning evidence. This holds
    the RULE, not the behaviour: an agent that passes a bare name still spills, and for the TEXT
    writers nothing detects it — the magic-byte scan above does catch a bare-named PNG/JPEG/PDF,
    which is precisely the half #629 could close and this one cannot. The spill is not even
    root-confined: measured, a `filename` carrying a subdirectory landed beside the sources. It is
    also local twice over. `.playwright-mcp/` is ignored in THIS repo, while SKILL.md self-heals
    onto consumers whose .gitignore this pin cannot see; and the directory itself is relative to
    the MCP server's WORKSPACE, which is the first root the client declares (`clientInfo.cwd =
    firstRootPath(clientRoots)`), falling back to the server's cwd only when a client declares
    none. For the session's shared browser those coincide with the main checkout — which is how
    #554 measured the artifact landing there, and what the `mkdir` recipe in SKILL.md resolves —
    but "the checkout" is a consequence of that setup, not a property of the tool. What the pin
    buys is that the rulebook no longer PRESCRIBES the leaking form, and that the form it does
    prescribe is checked against the rules of the repository that ships it.

    MUTATION-CHECKED in a `git clone --no-hardlinks` of this branch (never a `cp -R`, which drags
    `.venv` and puts the ORIGINAL `src` earlier on `sys.path`, after which every round is green);
    `vikunja_mcp.__file__` printed each round and confirmed to be the CLONE's;
    `__pycache__` deleted before each round AND `PYTHONDONTWRITEBYTECODE=1`, since that variable
    stops Python writing bytecode, not reading a stale `.pyc`; sources restored from a COPY.
    Selection is BOTH rulebook-facing files (`test_repo_browser_isolation.py` +
    `test_skill_contract.py`, 112 tests) so a round's effect on the contract pins next door is
    visible rather than hidden by a narrow selection. Failed counts, never pass totals:

    * control 0 failed
    * strip the `.playwright-mcp/` prefix from BOTH prescribed calls in SKILL.md -> 1 failed, this
      test, naming `vmcp-554-snap.md` as unignored (`vmcp-554-probe.png` is NOT named: `*.png`
      covers it wherever it sits)
    * strip the prefix from the `.md` call ONLY -> 1 failed, same message: the case no extension
      rule can cover is caught on its own
    * strip the prefix from the `.png` call ONLY -> 1 failed, on the RULE assertion, naming
      `vmcp-554-probe.png` as prescribed outside the output dir. PREDICTED as a failure of the
      git-backed check and MEASURED, before that assertion existed, as **0 failed** — the round
      that added it. A pin that only asks git cannot see a screenshot drifting back to a root path
    * swap the prefix for a directory this repo does NOT ignore (`docs/`) -> 1 failed, naming
      `docs/vmcp-554-snap.md` as publishable: the round that shows this is not a string match on
      one blessed path but a question put to git
    * make BOTH prescribed examples `.png` -> 1 failed, on the EXAMPLES assertion (`assert []`):
      every value stays ignored and every value stays under the output dir, yet nothing left in
      the rulebook demonstrates the writer this card exists for
    * delete the `.playwright-mcp/` line from .gitignore -> 2 failed: this test (both prescribed
      values now unignored) and #607's `test_the_playwright_output_dir_is_excluded`. The round
      that shows the pin reaches the ignore rules and not only the prose
    * break the regex so it matches nothing -> 1 failed on the floor, not a silent green
    * add a THIRD correctly-prefixed prescription -> 0 failed: writing another rule is not a test
      edit

    And the rounds that land next door, on the contract pins this card also moved (same selection,
    control 0 failed): gut the prefix INSTRUCTION while leaving both example values intact -> 1
    failed, `test_the_shared_browser_rule_stays_detectable_rather_than_wishful` — the round that
    matters most here, since the values alone would keep THIS test green; drop the mkdir/ENOENT
    precondition -> 1 failed, same id; revert the `attach_file` clause to its pre-#703 root path
    -> 1 failed, same id.

    ============================================================================================
    #736: THE SECOND DOOR INTO THE SAME DIRECTORY, gated by the second half of this test.

    The name of this test still says `filename`, and that is now narrower than what it checks —
    kept deliberately, because the name is quoted in four places (.gitignore, CLAUDE.md and two
    docstrings in test_skill_contract.py) and a rename buys nothing a sentence cannot. What it
    checks is BOTH routes SKILL.md prescribes today — a caller-chosen `filename` and
    `--output-dir` — each in the spellings its own pattern matches. Not "every path": the browser
    has other flags that write, and one this rulebook never prints is one this pin never sees.

    #703 fixed the caller-chosen `filename`. Beside it stood the recipe that launches an agent's
    own server — `--output-dir <каталог с id задачи>` — untouched by that card and feeding the
    OTHER resolver, `outputFile()`, which is where the AUTO-named artifacts go. One of them is
    `page-*.yml`, the aria snapshot, i.e. the page's own text: the exact payload #703 exists for,
    arriving through a door #703 did not close. The placeholder reads equally as a subdirectory of
    the worktree and as one of the scratchpad, and only the second reading was safe.

    Reproduced BEFORE the fix, not argued (constants above carry the full measurement): with
    `--output-dir 736-out` git reported `?? 736-out/` and `git add -A --dry-run` STAGED the `.yml`
    and the `.log`, both carrying markers planted on the probe page — including a link's `?token=`
    query string. Only the screenshot was covered, by #629's `*.png`.

    MUTATION-CHECKED with the same discipline as the rounds above (`git clone --no-hardlinks` of
    this branch; `__pycache__` deleted each round AND `PYTHONDONTWRITEBYTECODE=1`;
    `vikunja_mcp.__file__` printed and confirmed to be the CLONE's; sources restored from a COPY
    and `git status --porcelain` confirmed EMPTY after every round; `collected 115 items` printed
    and cross-checked against the count of `^FAILED` lines each time). Failed counts, never pass
    totals:

    * control 0 failed
    * revert the recipe to the old `<каталог с id задачи>` placeholder -> 1 failed, this test.
      The round this card exists for
    * point the recipe at `554-out`, a plain directory named after the task -> 1 failed
    * point it at `dist/554` — ignored HERE, but for an unrelated reason -> 1 failed, and on the
      RULE assertion, with the git-backed check GREEN. That measurement is why the rule assertion
      is separate rather than folded in, exactly as #703 found for the screenshot example
    * delete the `.playwright-mcp/` line from .gitignore -> 2 failed: this test and #607's
      `test_the_playwright_output_dir_is_excluded`. The pin reaches the ignore rules, not only
      the prose
    * break `PRESCRIBED_OUTPUT_DIR_RE` so it matches nothing -> 1 failed on the floor
    * add a THIRD correctly-prefixed value -> 0 failed: writing another rule is not a test edit
    * narrow `AUTO_NAMED_PROBES` to the screenshot AND drift the recipe to `554-out` -> 1 failed,
      but on the RULE assertion: `*.png` is directory-blind, so the git-backed half went blind
      with it. See that constant — the round corrected an overclaim in its own comment
    * remove the negative lookahead the regex was written with -> **0 failed**. It was dead code:
      `\\s+` already refuses to start a value where a backtick follows the flag. Removed, and the
      comment that credited it rewritten
    * unbacktick ONE prose mention of the flag -> 1 failed. A FALSE red, kept as the honest
      residual: this reads prose, and prose punctuation can hand it a Russian word as a path
    * rewrite the recipe in the EQUALS spelling with a bad directory (`--output-dir=554-out`) ->
      1 failed. Against the pattern as first written (`\\s+` only) the same round measured **0
      failed** — the bypass an attack pass BUILT, after first measuring on the server that the
      equals form spills identically. See PRESCRIBED_OUTPUT_DIR_RE
    * the equals spelling with the CORRECT directory -> 0 failed: the widened pattern costs no
      false red
    * set the floor to 2 AND delete the flag from the fence -> 2 failed, one of them the floor.
      The round that disproved a COUNTERFACTUAL this docstring's neighbour had asserted as
      measured; see MIN_PRESCRIBED_OUTPUT_DIRS

    THE ROUND THAT CHANGED THE FIX, and the reason a sweep is run instead of predicted (same
    selection, control 0 failed): delete
    `--output-dir` from the FENCED launch line entirely, leaving the several prose mentions of the
    flag alone -> **0 failed**. Everything here stayed green while the line an agent copies had
    lost the flag — because this pin asks whether git would publish each value the file PRINTS,
    and a prose restatement is still a value. That is the same defect #703's neighbour recorded
    for `--isolated` and `--channel=chrome`, so it was closed where those are: one assertion in
    `test_the_browser_answer_leads_with_the_isolation_an_agent_can_launch_itself`, matching INSIDE
    the fence. Re-run after it -> 1 failed, there. What that assertion guards is the NAMING rule
    and not a leak — the first version of it claimed a leak, and an independent attack pass
    disproved that by running the server with no `--output-dir` at all: the DEFAULT output dir is
    `<cwd>/.playwright-mcp/`, the ignored one, `git status` empty and `git add -A --dry-run`
    staging nothing. Re-measured here. Its own comment carries the correction.

    HONEST BOUNDARY, and it is not the same one #703 has. Two facts measured for this card:
    `--output-dir` pointed OUTSIDE the repo WORKS — there is no `File access denied … outside
    allowed roots` here, because that refusal belongs to the caller-chosen `filename` path and
    `--output-dir` defines a root rather than escaping one. So "outside the repo" is a genuine
    second answer, and SKILL.md says so; it is not the prescribed one, and this pin cannot check
    it, because a directory outside the repository has no path to put to git. And the directory
    appears on the first `browser_navigate` — measured absent at server start and absent after
    `initialize`, present after that one call —
    which is what makes a caller-chosen `filename` under the same prefix work with no manual
    `mkdir -p` in the measured order, and is a fact about ordering rather than a guarantee.
    Everything the #703 boundary above says still applies unchanged: this holds the RULE, not the
    behaviour; `.playwright-mcp/` is ignored in THIS repo while SKILL.md self-heals onto consumers
    whose .gitignore no pin here can see; and a flag this rulebook never prints is a flag this
    pin never sees.

    ============================== #751 ========================================================
    #751: THE THIRD DOOR — a CONFIG FILE — gated by the third block of this test.

    `--config <path>` with `{"outputDir": …}` sets the same directory the flag sets, and the same
    two text artifacts spill from it. Reproduced before the fix rather than argued (the constant
    PRESCRIBED_CONFIG_OUTPUT_DIR_RE carries the full measurement): `git status` showed
    `?? cfg-out-751/` and `git add -A --dry-run` staged the `.yml` and the `.log`, the first
    carrying the probe page's aria text and a link's `?token=` query string.

    THE CARD'S OWN FRAME WAS THE FIRST THING TO GO, and re-measuring is what did it. It was filed
    as "the THIRD door", and the doors number at least FIVE: beside the flag in both spellings and
    the config file, `PLAYWRIGHT_MCP_OUTPUT_DIR` and `PLAYWRIGHT_MCP_CONFIG` set the same
    directory with NO flag on the line — both measured spilling identically. Enumerating flags can
    therefore never close this, which is why the
    prescription and this pin are about a DIRECTORY. Against that, the fact that bounds the
    exposure, also measured: the explicit `--output-dir` BEATS all three (pairwise, each round
    put the files in `.playwright-mcp/751` and never created the foreign directory), so the
    recipe is safe from a stray config and an inherited env var as long as it keeps its flag —
    which the fence assertion in test_skill_contract.py holds. What is left is the agent that
    drops the flag and configures instead.

    MUTATION-CHECKED in a separate clone with this file's usual discipline (`git clone
    --no-hardlinks`; `__pycache__` deleted each round AND `PYTHONDONTWRITEBYTECODE=1`;
    `vikunja_mcp.__file__` printed and confirmed to be the CLONE's; every mutation asserted to
    have LANDED before the round and the sources restored from a copy after it). Failed counts,
    never pass totals, and the control is in this same paragraph rather than assumed:

    READ THE ASSERTION, NOT THE COUNT, for the first three rounds — and that is a correction this
    card made to its own first draft rather than a style note. All three blocks of this test are
    assertions inside ONE test function, so pytest stops at the first one that fires and the
    round count is 1 whichever of them did. Against control 0 failed, a draft of this list
    predicted 2 failed for the first round below, reasoning that two assertions were violated;
    both ARE, and the round still measures 1 failed. So every round here was re-run printing the
    AssertionError, and what is recorded is which assertion spoke:

    * control 0 failed
    * point the prescribed `outputDir` at `cfg-out-751`, a plain directory in the tree -> 1
      failed, on the GIT-BACKED assertion, naming both text probes under that directory
    * point it at `dist/751` — ignored HERE, for an unrelated reason -> 1 failed, on the RULE
      assertion, with the git-backed one reached and GREEN. The same measurement that made #703
      and #736 each keep their two assertions separate, reproduced for this door not inherited
    * break PRESCRIBED_CONFIG_OUTPUT_DIR_RE so it matches nothing -> 1 failed, on the floor
    * delete the `.playwright-mcp/` line from .gitignore -> 2 failed, and here the count DOES
      mean two: this test and #607's `test_the_playwright_output_dir_is_excluded`, which is a
      different test function. This block reaches the ignore rules and not only the prose
    * delete the whole `--config` bullet from SKILL.md -> 1 failed, on the floor: the rulebook
      cannot drift back to silence about this door without going red
    * control again 0 failed

    FALSE REDS PRICED BEFORE LANDING, on the tree as it stands: the new pattern finds exactly one
    value in SKILL.md (`.playwright-mcp/<id>`), which is the only file this pin reads, and the
    existing `--output-dir` pattern still finds the same two values it found before — the
    `--config` paragraph names the equals spelling as `` `--output-dir=` ``, where the closing
    BACKTICK is the next character, so `[^\\s`]+` never starts a value there. Run over every
    tracked file, the new pattern has exactly one other hit in the whole repository — the
    `"<value>"` written in this file's own comment to show the spelling — and it costs nothing,
    because no pin reads this file for prescriptions. That second half was checked rather than
    assumed: a draft of this paragraph claimed the pattern matched nowhere else at all.
    Whole unit suite 1004 passed, ruff clean, `uv sync --locked` clean.

    BOUNDARY, and it is a real one rather than a hedge: the two ENV VARS are beyond ANY pin over
    prose. They are not written on the launch line at all — they are inherited from whoever
    started the agent — so nothing this file reads can see them, and the only defence against
    them is the flag the recipe carries. SKILL.md says exactly that instead of implying coverage
    it does not have.
    """
    text = SKILL_MD_PATH.read_text(encoding="utf-8")
    prescribed = sorted(set(PRESCRIBED_FILENAME_RE.findall(text)))

    assert len(prescribed) >= MIN_PRESCRIBED_FILENAMES, \
        f"SKILL.md prints {len(prescribed)} `filename=` prescriptions ({prescribed}), fewer than " \
        f"the {MIN_PRESCRIBED_FILENAMES} it is supposed to. Either the rule that tells agents " \
        "where browser artifacts must go was deleted, or this pin's regex stopped matching it — " \
        "and a guard that matches nothing passes everything, which is the failure mode this " \
        "floor exists to make loud"

    unignored, foreign = [], []
    for value in prescribed:
        ignored, source, _pattern = _ignore_rule(value)
        if not ignored:
            unignored.append(value)
        elif source != ".gitignore":
            foreign.append(f"{value} (ignored by {source})")

    assert not unignored, \
        f"{unignored} — SKILL.md tells an agent to pass {'this' if len(unignored) == 1 else 'these'} " \
        "as a browser tool's `filename`, and git would publish the result. That argument is " \
        "resolved against the MCP server's workspace, i.e. the MAIN CHECKOUT, so the file lands " \
        "in a PUBLIC repo carrying whatever page the browser had open — its text, and the query " \
        "strings of the requests it made. Put the value back under `.playwright-mcp/`: not the " \
        "only directory this repo excludes regardless of filename (measured, eight rules do " \
        "that — `dist/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, " \
        "`.superpowers/`, `.auth/` and this one), but the only one that is ALSO where the " \
        "`--output-dir` prescription sends the AUTO-named artifacts, which is what keeps it one " \
        "directory to reason about"

    assert not foreign, \
        f"{foreign} — covered by somebody else's ignore file rather than by this repo's own " \
        ".gitignore, so the protection exists on whichever machine happens to have that rule and " \
        "vanishes in a fresh clone. That is how a green here can mean nothing at all"

    astray = [value for value in prescribed if not value.startswith(f"{PLAYWRIGHT_OUTPUT_DIR}/")]
    assert not astray, \
        f"{astray} — prescribed outside `{PLAYWRIGHT_OUTPUT_DIR}/`. git happens not to publish " \
        "this one, but the RULE SKILL.md states is `filename` ВСЕГДА with that prefix, and the " \
        "assertions above cannot see the difference: a bare `.png` is covered by #629's " \
        "extension rules whatever directory it is in, so a rulebook drifting back to root paths " \
        "would read as safe there. Measured on this repo's own sweep, which is why this " \
        "assertion exists at all: stripping the prefix from the screenshot example alone left " \
        "the git-backed checks completely green"

    load_bearing = [
        value for value in prescribed
        if "/" in value and not _ignore_rule(Path(value).name)[0]
    ]
    assert load_bearing, \
        f"none of {prescribed} is covered by its DIRECTORY: strip the path off each and git " \
        "still refuses to publish it, which means every example SKILL.md prints happens to be " \
        "one the #629 extension rules already caught. The whole point of #703 is the writer " \
        "whose name no rule can match — a text snapshot, a console dump, a network log — so at " \
        "least one prescribed value must be one that is safe ONLY because of where it is put. " \
        "An examples list that has drifted to screenshots only still teaches agents the case " \
        "this card was filed about wrongly, and the directory rule would then be doing no work " \
        "that #629 was not already doing"

    # --- #736: the same question, put to the OTHER door into the same directory --------------
    out_dirs = sorted(set(PRESCRIBED_OUTPUT_DIR_RE.findall(text)))

    assert len(out_dirs) >= MIN_PRESCRIBED_OUTPUT_DIRS, \
        f"SKILL.md prints {len(out_dirs)} `--output-dir` values ({out_dirs}), fewer than the " \
        f"{MIN_PRESCRIBED_OUTPUT_DIRS} it is supposed to. Either the own-browser launch recipe " \
        "stopped naming its output directory — which is the ambiguity #736 exists to remove, " \
        "not a leak: the DEFAULT is `.playwright-mcp/`, measured — or this pin's regex stopped " \
        "matching it, and a guard that matches nothing passes everything"

    spilling = []
    for value in out_dirs:
        for probe in AUTO_NAMED_PROBES:
            candidate = f"{value}/{probe}"
            ignored, source, _pattern = _ignore_rule(candidate)
            if not ignored:
                spilling.append(candidate)
            elif source != ".gitignore":
                spilling.append(f"{candidate} (ignored by {source}, not by this repo)")

    assert not spilling, \
        f"{spilling} — SKILL.md tells an agent to launch its own browser with this " \
        "`--output-dir`, and git would publish what lands there. `page-*.yml` is the ARIA " \
        "SNAPSHOT: the page's own text, plus the query strings of its links. Measured for " \
        "#736, a directory merely named after the task (`736-out/`) shows up as `?? 736-out/` " \
        "and `git add -A` stages both the .yml and the .log — only the screenshot is covered, " \
        "by #629's `*.png`. Point the flag at `.playwright-mcp/<id>`: not the only directory " \
        "this repo excludes regardless of filename (measured, eight rules do that — `dist/`, " \
        "`.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`, `.superpowers/`, `.auth/` " \
        "and this one), but the only one that is ALSO where the `filename` prescription already " \
        "sends the caller-named artifacts, which is what keeps it one directory to reason about"

    out_astray = [v for v in out_dirs if not v.startswith(f"{PLAYWRIGHT_OUTPUT_DIR}/")]
    assert not out_astray, \
        f"{out_astray} — prescribed outside `{PLAYWRIGHT_OUTPUT_DIR}/`. git may happen not to " \
        "publish it, and that is precisely why this assertion is separate: measured on this " \
        "card's sweep, swapping the recipe to `dist/554` (ignored here for an unrelated reason) " \
        "left the git-backed check above completely green while the rule SKILL.md states was " \
        "gone. The rule is ONE place — the same `.playwright-mcp/` the `filename` prescription " \
        "names — so that an agent has one directory to reason about and this repo one line of " \
        ".gitignore to keep"

    # --- #751: the same question a THIRD time, put to the CONFIG FILE ------------------------
    cfg_dirs = sorted(set(PRESCRIBED_CONFIG_OUTPUT_DIR_RE.findall(text)))

    assert len(cfg_dirs) >= MIN_PRESCRIBED_CONFIG_OUTPUT_DIRS, \
        f"SKILL.md prints {len(cfg_dirs)} `outputDir` values ({cfg_dirs}), fewer than the " \
        f"{MIN_PRESCRIBED_CONFIG_OUTPUT_DIRS} it is supposed to. A config file's `outputDir` " \
        "sets the SAME directory `--output-dir` sets — measured — so the rulebook has to say " \
        "where it must point for the agent that configures the server instead of flagging it. " \
        "Either that prescription was deleted or this pin's regex stopped matching it, and a " \
        "guard that matches nothing passes everything"

    cfg_spilling = []
    for value in cfg_dirs:
        for probe in AUTO_NAMED_PROBES:
            candidate = f"{value}/{probe}"
            ignored, source, _pattern = _ignore_rule(candidate)
            if not ignored:
                cfg_spilling.append(candidate)
            elif source != ".gitignore":
                cfg_spilling.append(f"{candidate} (ignored by {source}, not by this repo)")

    assert not cfg_spilling, \
        f"{cfg_spilling} — SKILL.md tells an agent to put this in a `--config` file's " \
        "`outputDir`, and git would publish what lands there. This is the SAME spill as the " \
        "`--output-dir` assertion above, through a different door: measured for #751, a config " \
        "whose `outputDir` named a plain directory in the tree left `?? cfg-out-751/` in `git " \
        "status` while `git add -A --dry-run` staged the `.yml` and the `.log`, the first " \
        "carrying the page's aria text and a link's `?token=` query string"

    cfg_astray = [v for v in cfg_dirs if not v.startswith(f"{PLAYWRIGHT_OUTPUT_DIR}/")]
    assert not cfg_astray, \
        f"{cfg_astray} — prescribed outside `{PLAYWRIGHT_OUTPUT_DIR}/`. Separate from the " \
        "git-backed check above for the reason its `--output-dir` twin records: git may happen " \
        "not to publish a value that is ignored here for an unrelated reason, and the RULE " \
        "SKILL.md states is ONE directory for every knob that sets it — the flag in either " \
        "spelling, this config key, and the two env vars that need no flag at all"


def _publishable_copies(root: Path):
    """Every copy of every path `git add -A` could publish, yielded as `(rel, size, raw)`.

    ONE walk, shared by both shape scans below rather than written twice — VMCP-209 (752)
    extracted it unchanged from the storage-state scan. The classifier is the easy half; the
    subtle half is WHICH BYTES count as publishable, and that took VMCP-141 (630) plus its
    reviewer two rounds to get right. A second hand-written copy would be the one nobody
    re-measured, and the two gates would drift apart in silence.

    `raw` is `b""` when `size` exceeds `SHAPE_SCAN_MAX_BYTES`; the caller decides what to do
    with a candidate it cannot afford to read, and both callers REPORT rather than skip.

    TRACKED and UNTRACKED are kept apart, which VMCP-141 (630) is about. The candidate LIST
    always came from git; the BYTES came from the worktree, and where the two disagree the scan
    went quiet. Built rather than reasoned about, on a clean clone: a shaped file `git add -f`ed
    and then DELETED from the worktree -> 1 passed; the worktree copy overwritten with `{}`
    while the index blob stayed a cookie -> 1 passed; committed and then deleted locally without
    committing the deletion (` D` in status) -> 1 passed. In all three `git cat-file -p :<path>`
    still hands out the credential. CI never saw it (a fresh checkout is clean, and there the
    index half IS the whole scan), which is why this is a hardening rather than a leak — but the
    local run was claiming a guarantee it did not have.

    EVERY copy of one candidate is yielded — the UNION, not a choice. #630's first version READ
    THE INDEX INSTEAD OF THE WORKTREE, and its reviewer measured that this swapped one blind spot
    for a wider one. Both copies matter, for two different commands: for a tracked-and-modified
    path `git add -A` stages the WORKTREE bytes, while `git commit` (with nothing staged)
    publishes the INDEX blob. Reading either alone leaves a real state silent.

    Measured, on a real repo, with the index-only version: a committed benign `package.json`
    whose worktree copy was overwritten with a credential and NOT staged — `git status` says
    ` M`, `git add -A --dry-run` says `add 'package.json'`, and the scan reported NOTHING,
    while the ORIGINAL worktree-only scan caught it. That state is also more reachable than
    the three above: those need a deliberate `git add -f`, this needs only an overwrite of a
    tracked file.
    """
    tracked, untracked = set(), set()
    for args, into in ((("ls-files", "-z"), tracked),
                       (("ls-files", "--others", "--exclude-standard", "-z"), untracked)):
        listed = _git(*args, cwd=root)
        assert listed.returncode == 0, f"git {' '.join(args)} failed: {listed.stderr.strip()}"
        into.update(p for p in listed.stdout.split("\0") if p)

    for rel in sorted(tracked | untracked):
        if rel in tracked:
            sized = _git("--no-pager", "cat-file", "-s", f":{rel}", cwd=root)
            if sized.returncode == 0:            # a conflicted entry has no single size — skip it
                size = int(sized.stdout.strip())
                if size > SHAPE_SCAN_MAX_BYTES:
                    yield rel, size, b""
                elif size:
                    blob = _git_bytes("--no-pager", "cat-file", "blob", f":{rel}", cwd=root)
                    if blob.returncode == 0:
                        yield rel, size, blob.stdout
        path = root / rel
        try:
            if path.is_file():
                size = path.stat().st_size
                if size > SHAPE_SCAN_MAX_BYTES:
                    yield rel, size, b""
                elif size:
                    yield rel, size, path.read_bytes()
        except OSError:
            pass


def _classify_browser_text_artifact(raw: bytes) -> str | None:
    """Name the browser text artifact these bytes ARE, or None — VMCP-209 (752).

    WHOLE-FILE, and that is the whole design. Each grammar demands that EVERY non-blank line
    match it, so the classifier answers "this file IS an aria snapshot", never "this file
    mentions one" — the same distinction that lets the storage-state gate coexist with the
    paragraphs describing the storage-state shape. A marker grep cannot make that distinction,
    and the cost of not making it is measured next door rather than argued.

    Bytes, not text, and a decode failure is a NEGATIVE rather than an exception: these are
    text artifacts, so anything that is not UTF-8 is not one. That is a narrower claim than the
    storage-state gate's, which accepts UTF-16 and BOMs because `json.loads` detects them; here
    the producer writes UTF-8 (measured) and a line grammar has no encoding sniffer to borrow.
    """
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError:
        return None
    body = [ln for ln in text.splitlines() if ln.strip()]
    if not body:
        return None

    if (len(body) >= ARIA_MIN_ITEM_LINES
            and all(_ARIA_ITEM_LINE.match(ln) for ln in body)
            and sum(1 for ln in body if _ARIA_REF_TOKEN.search(ln)) >= ARIA_MIN_REF_LINES):
        return "aria snapshot (the page's own text, and its links' query strings)"

    # The explicit-`filename` console export opens with a totals header and drops the per-line
    # timing; the auto-named `console-*.log` keeps the timing and has no header. Accept either,
    # and require at least one MESSAGE line: a header on its own is what an empty console
    # produces (measured: `Total messages: 0 (Errors: 0, Warnings: 0)`), and an artifact with no
    # messages has no page content in it to leak.
    messages = body[1:] if _CONSOLE_TOTALS_HEADER.match(body[0]) else body
    if messages and (all(_CONSOLE_TIMED_LINE.match(ln) for ln in messages)
                     or all(_CONSOLE_PLAIN_LINE.match(ln) for ln in messages)):
        return "console log (page console output, and the URLs it names)"

    # The weakest of the three grammars, and the reason is worth stating rather than hiding: it
    # has to tolerate a trailing PROSE line ("Note: 1 static request not shown, …") whose wording
    # belongs to one version of the tool, where the other two are machine formats throughout. It
    # still requires at least one request line, so the note alone — an empty network log — is not
    # classified, for the same reason the bare console header is not.
    if (any(_NETWORK_REQUEST_LINE.match(ln) for ln in body)
            and all(_NETWORK_REQUEST_LINE.match(ln) or _NETWORK_NOTE_LINE.match(ln)
                    for ln in body)):
        return "network log (every URL the page requested, query strings included)"

    return None


def _scan_for_browser_text_artifact_shape(root: Path) -> tuple[list[tuple[str, str]], list[str]]:
    """The text-artifact half of the shape gate — VMCP-209 (752). Same walk, same honesty about
    a candidate too large to read: reported, never skipped."""
    offenders: list[tuple[str, str]] = []
    unclassified: list[str] = []
    named = set()
    for rel, size, raw in _publishable_copies(root):
        if size > SHAPE_SCAN_MAX_BYTES:
            if rel not in unclassified:
                unclassified.append(rel)
            continue
        kind = _classify_browser_text_artifact(raw)
        if kind and rel not in named:
            named.add(rel)
            offenders.append((rel, kind))
    return offenders, unclassified


def _scan_for_storage_state_shape(root: Path) -> tuple[list[str], list[str]]:
    """The shape scan, as ONE implementation, parameterised by repo root — VMCP-141 (630).

    It takes a root so the pins can drive the SHIPPED scanner over a throwaway clone instead of
    a copy of it. That mattered here rather than in the abstract: the first version of those pins
    duplicated this loop, and the mutation "read the worktree instead of the index" — the exact
    defect 630 is about — left them GREEN, because they were measuring the copy. A pin that
    cannot see its own subject change is not a pin.

    The real test below passes REPO_ROOT and nothing else does, so the parameter buys the pins
    their subject without giving anyone a scanner aimed somewhere else by default.

    The candidate walk it drives lives in `_publishable_copies`, shared with the text-artifact
    scan since VMCP-209 (752); everything the walk itself had to learn is documented there.
    """
    offenders, unclassified = [], []
    for rel, size, raw in _publishable_copies(root):
        if size > SHAPE_SCAN_MAX_BYTES:
            if rel not in unclassified:
                unclassified.append(rel)      # REPORTED, never skipped — see SHAPE_SCAN_MAX_BYTES
            continue
        try:
            # BYTES, not `read_text(encoding="utf-8")`. That call sat inside this same
            # `except (OSError, ValueError)` and `UnicodeDecodeError` is a subclass of
            # `ValueError`, so a shaped file written with a BOM or in UTF-16 was skipped in
            # SILENCE. Measured: `json.loads` on BYTES accepts UTF-8-with-BOM, UTF-16 and
            # UTF-32 (it detects the encoding itself), while the text path fails on all three.
            # Precisely those three axes close — a file with a genuinely invalid byte still
            # raises, and should: it is not valid JSON in any encoding, so that is a correct
            # refusal rather than a miss. No real producer writes those forms either
            # (playwright-core 1.62.0 writes utf8 via JSON.stringify), which is why this is
            # hardening: a `continue` that goes quiet on a shaped file is the class this repo
            # keeps filing cards about, reachable or not.
            data = json.loads(raw)
        except (OSError, ValueError):
            continue  # unreadable, not text, or not JSON — cannot be a storage state
        if isinstance(data, dict) and all(
            isinstance(data.get(key), list) for key in STORAGE_STATE_SHAPE_KEYS
        ) and rel not in offenders:
            offenders.append(rel)          # named ONCE, whichever copy carries the credential

    return offenders, unclassified


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
    offenders, unclassified = _scan_for_storage_state_shape(REPO_ROOT)

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


# --- VMCP-209 (752): the TEXT artifacts had no content lock at all ---------------------------
#
# Real bytes, from an own `--isolated --headless` server driven over stdio against an own
# throwaway origin — not transcribed from the filing card, which had two details wrong. Kept as
# fixtures so the pins below prove the gate against what the tool ACTUALLY writes; the long
# console line is split across source lines only to stay inside this repo's wrap, and carries no
# newline of its own.
_ARIA_ARTIFACT = (
    b'- generic [active] [ref=e1]:\n'
    b'  - heading "VMCP752-ARIA-MARKER" [level=1] [ref=e2]\n'
    b'  - paragraph [ref=e3]: "secret line: VMCP752-BODY-MARKER"\n'
    b'  - link "go next" [ref=e4] [cursor=pointer]:\n'
    b'    - /url: /next?token=VMCP752-QUERY-MARKER\n'
    b'  - button "press me" [ref=e5]\n'
)
# The same page snapshotted after a second navigation: `ref` tokens come frame-qualified.
_ARIA_ARTIFACT_FRAME_QUALIFIED = _ARIA_ARTIFACT.replace(b"[ref=e", b"[ref=f1e")
_CONSOLE_AUTO_NAMED = (
    b"[      54ms] [LOG] VMCP752-CONSOLE-MARKER @ http://127.0.0.1:20752/:9\n"
    b"[      54ms] [WARNING] a warning line @ http://127.0.0.1:20752/:10\n"
    b"[      64ms] [ERROR] Failed to load resource: the server responded with a status of "
    b"404 (Not Found) @ http://127.0.0.1:20752/api?token=VMCP752-NETWORK-MARKER:0\n"
)
_CONSOLE_EXPLICIT = (
    b"Total messages: 3 (Errors: 1, Warnings: 1)\n\n"
    b"[LOG] VMCP752-CONSOLE-MARKER @ http://127.0.0.1:20752/:9\n"
    b"[WARNING] a warning line @ http://127.0.0.1:20752/:10\n"
)
_NETWORK_EXPLICIT = (
    b"2. [GET] http://127.0.0.1:20752/api?token=VMCP752-NETWORK-MARKER => [404] Not Found\n\n"
    b'Note: 1 static request not shown, run with "static" option to see it.\n'
)
# The two EMPTY forms and the evaluate dump: measured outputs that carry no page content, and
# therefore correctly classify as nothing. Named here so "not classified" stays a decision.
_CONSOLE_EMPTY = b"Total messages: 0 (Errors: 0, Warnings: 0)\n"
_NETWORK_EMPTY = b'\nNote: 1 static request not shown, run with "static" option to see it.\n'
_EVALUATE_DUMP = (
    b'"VMCP752-ARIA-MARKER\\n\\nsecret line: VMCP752-BODY-MARKER\\n\\ngo next\\npress me "\n'
)

_REAL_TEXT_ARTIFACTS = {
    "aria snapshot": _ARIA_ARTIFACT,
    "aria snapshot, frame-qualified refs": _ARIA_ARTIFACT_FRAME_QUALIFIED,
    "console log, auto-named": _CONSOLE_AUTO_NAMED,
    "console log, explicit filename": _CONSOLE_EXPLICIT,
    "network log, explicit filename": _NETWORK_EXPLICIT,
}


@requires_git_checkout
def test_no_file_of_browser_text_artifact_shape_is_reachable_by_git():
    """The lock the TEXT artifacts did not have — VMCP-209 (752), filed off #736's review.

    THE ASYMMETRY THIS CLOSES. A screenshot is held by its leading magic bytes under any name
    (#629) and a storage state by its two list-valued keys, both independently of what the file
    is called. The browser's text output had neither: it was held only by rules over PROSE —
    #703 pins the `filename` SKILL.md prints, #736 the `--output-dir` it prints, #751 the
    `--config` it prints — and every one of those pins asks what the RULEBOOK SAYS, never what
    is on disk. #751 is the proof that this matters: `--config '{"outputDir": …}'` moved every
    auto-named artifact past both existing pins, and closing that ROUTE left the CLASS open, one
    undocumented flag wide.

    WHAT IT MATCHES, and why a whole-file grammar rather than a marker grep. Each shape demands
    that EVERY non-blank line match, over a floor of lines, so the question answered is "is this
    file an artifact" and not "does this file mention one". That is the same move the
    storage-state gate makes by parsing JSON instead of grepping for `"cookies"`, and it is what
    lets this gate coexist with the paragraphs — in `.gitignore`, in CLAUDE.md, in this very
    file — that quote the shapes in order to describe them. The alternative was measured, not
    assumed: see the next test, where the naive grep is red on arrival and this is not.

    MEASURED FALSE REDS OVER THIS REPO: zero, for all three grammars, over the candidate set
    `_publishable_copies` yields (73 tracked + 0 untracked-and-not-ignored at the time of
    writing, `.github/workflows/ci.yml` being the only `.yml` and this file the densest quoter
    of the shapes). That number is what made the lock landable rather than a nuisance, and it is
    re-derived on every run by the pin below rather than trusted from this sentence.

    WHAT IT DOES NOT REACH, measured rather than conceded. `browser_evaluate` writes whatever
    the evaluated JS returned — its measured output is a JSON string literal — so it has no
    grammar, and no version of this gate could give it one. That is the one place CLAUDE.md's
    retired "indistinguishable from a legitimate file" is still exactly right, and it is now
    said of ONE writer instead of four. The two EMPTY forms (a console export with no messages,
    a network export with only its trailing note) are also unclassified, and correctly: an
    artifact with no messages in it has no page content to leak.

    Same honest boundary as its sibling: this is a GATE — red in the pre-push `uv run pytest
    tests/unit -q` and red in CI — not a lock on `git commit`, and it cannot see a file that
    never reaches this working tree. A candidate above `SHAPE_SCAN_MAX_BYTES` is REPORTED rather
    than skipped, for the reason that constant documents at length. Offending PATHS are named;
    contents never are.
    """
    offenders, unclassified = _scan_for_browser_text_artifact_shape(REPO_ROOT)

    assert not offenders, \
        f"{offenders} — git can publish a file that IS a browser text artifact. Every line " \
        "matches the shape named beside the path, so this is not a file that mentions one. " \
        "These carry the page's own text and the query strings of everything it requested, " \
        "which on a logged-in page is a credential, and THIS REPO IS PUBLIC. The `.gitignore` " \
        "rules cannot help: they cover the `.playwright-mcp/` DIRECTORY and a list of " \
        "extensions, and a text artifact under a caller-chosen name in the repo root is " \
        "outside both. Delete it (and `git rm --cached` it if tracked); do not just rename it. " \
        "If you need it, put it under `.playwright-mcp/`, which SKILL.md already prescribes"

    assert not unclassified, \
        f"{unclassified} — bigger than the {SHAPE_SCAN_MAX_BYTES}-byte ceiling this scan will " \
        "read, so it could NOT be classified. Reported rather than skipped, for the same " \
        "reason the storage-state scan reports: an aria snapshot has no fixed upper size " \
        "either (it is the whole accessibility tree of whatever page was open), so 'too big to " \
        "classify' is what a fat one looks like. Look at what it is, then `.gitignore` it — " \
        "which also removes it from `git add -A`'s reach"


@requires_git_checkout
def test_the_naive_marker_grep_would_be_red_on_arrival_and_the_whole_file_grammar_is_not():
    """The measurement that chose the design — VMCP-209 (752), and it only became visible when
    this card landed.

    `.gitignore` recorded the content axis as "considered and dropped", on two grounds. The
    first — that a scan for aria markers "fires on any file that DOCUMENTS it — this comment
    included" — is TRUE of a marker grep and FALSE of a whole-file grammar, and the two answers
    were IDENTICAL (zero hits each) right up until this card, because nothing in the tree quoted
    a `[ref=…]` token yet. The moment fixtures and prose arrive, they diverge: this test is what
    makes the divergence a fact the suite re-derives rather than a claim in a docstring.

    So it asserts the SPLIT, not two numbers: the naive grep must hit at least one legitimate
    file (it will hit this one, and it may hit `.gitignore` and CLAUDE.md as the prose there
    grows), while the shipped grammar hits none. Reading it the other way round is the point —
    a future edit that quotes more artifact text makes the naive number go UP and must leave the
    grammar at zero. The day the grammar stops being zero, the gate above has found something.

    The second ground `.gitignore` gave — "it reaches `browser_snapshot` alone of the four" —
    was narrowed by measurement rather than dropped: three of the four text writers have a
    grammar, and the fourth cannot have one. That is recorded where the classifier lives.
    """
    naive_hits, grammar_hits = [], []
    for rel, size, raw in _publishable_copies(REPO_ROOT):
        if size > SHAPE_SCAN_MAX_BYTES:
            continue
        try:
            text = raw.decode("utf-8")
        except UnicodeDecodeError:
            continue
        if _ARIA_REF_TOKEN.search(text) and rel not in naive_hits:
            naive_hits.append(rel)
        if _classify_browser_text_artifact(raw) and rel not in grammar_hits:
            grammar_hits.append(rel)

    assert naive_hits, \
        "the naive marker grep now hits NOTHING in this repo, which means the prose and " \
        "fixtures that made it red on arrival have been deleted. That is not a pass: this " \
        "pin exists to hold the reason the shipped gate is a whole-file grammar instead of a " \
        "grep, and with nothing quoting an artifact the two designs are indistinguishable again"

    assert not grammar_hits, \
        f"the whole-file grammar hit {grammar_hits}. Either a real artifact reached the tree — " \
        "in which case the gate above is already failing and telling you which — or a " \
        "legitimate file has drifted into a shape where EVERY non-blank line matches an " \
        "artifact grammar. If it is the second, that is a false red and the grammar must be " \
        "narrowed, not the file rewritten"


@pytest.mark.parametrize("label", sorted(_REAL_TEXT_ARTIFACTS))
def test_a_real_browser_text_artifact_is_caught_under_an_innocuous_name(clone, label):
    """Every measured artifact shape, staged under a name no rule here covers, must be named.

    `notes.md` is the point: it is not `page-*.yml`, not under `.playwright-mcp/`, matches no
    extension rule and no ignore pattern — exactly the file #703's prose rule asks an agent not
    to create and cannot stop. The name is deliberately the most ordinary one in the repo's own
    vocabulary, since a gate that only catches suspicious names is the name layer again.
    """
    _stage(clone, "notes.md", _REAL_TEXT_ARTIFACTS[label])
    offenders, unclassified = _scan_for_browser_text_artifact_shape(clone)
    assert [rel for rel, _ in offenders] == ["notes.md"], (
        f"a real {label} staged as notes.md was not caught: {offenders}, {unclassified}")


@pytest.mark.parametrize("label,body", [("console with no messages", _CONSOLE_EMPTY),
                                        ("network with no requests", _NETWORK_EMPTY),
                                        ("browser_evaluate dump", _EVALUATE_DUMP),
                                        ("an ordinary markdown bullet list",
                                         b"- one\n- two\n- three\n"),
                                        ("a changelog entry with one quoted ref",
                                         b"- fixed the [ref=e1] token handling\n"),
                                        ("a two-line aria excerpt quoted in prose",
                                         b'- heading "Title" [ref=e1]\n'
                                         b'- paragraph [ref=e2]: body text\n')])
def test_the_shapes_that_are_deliberately_not_classified(label, body):
    """The negative half, and every row is a decision rather than an oversight.

    The two EMPTY forms and the `browser_evaluate` dump are measured outputs of the real tool
    that carry no page content — nothing to leak, so classifying them would be pure false-red
    surface. The last three rows are the false-red frontier from the other side, and each pins
    a DIFFERENT threshold — which is a correction, not a flourish: an earlier version of this
    test claimed the changelog row was held by the line floor, and the sweep disproved it.
    Dropping `ARIA_MIN_ITEM_LINES` from 3 to 1 measured 0 failed, because that row has only ONE
    ref and the REF floor was catching it either way, i.e. the line floor was pinned by nothing.
    Measured after adding the two-line excerpt (selection this file, control 0 failed):
    `ARIA_MIN_REF_LINES` 2 -> 0 kills the bullet-list row, and `ARIA_MIN_ITEM_LINES` 3 -> 1
    kills the excerpt row. Both floors are now load-bearing and each has its own witness.

    The excerpt row also names this gate's one deliberate false NEGATIVE: a genuine snapshot of
    fewer than three lines walks past. Measured, the smallest real one produced here was six
    lines with six refs (a snapshot carries the root node and its children, not a fragment), and
    a two-line excerpt in prose is the commoner object by far — this file, `.gitignore` and
    CLAUDE.md all contain one. The trade is taken knowingly and in that direction: a missed
    two-line artifact leaks two lines, while a false red on documentation gets the whole gate
    deleted by whoever hits it.
    """
    assert _classify_browser_text_artifact(body) is None, \
        f"{label} was classified as an artifact — that is a false red at the source"


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


# --- VMCP-141 (630): the shape gate read the INDEX for names and the WORKTREE for bytes --------

_SHAPE = b'{"cookies": [{"name": "s", "value": "SECRET"}], "origins": []}'


@pytest.fixture
def clone(tmp_path):
    """A throwaway git repo. NEVER this checkout: every round below stages a file of exactly the
    shape the real gate forbids, so building them here would leave the repository holding what
    its own guard exists to reject."""
    root = tmp_path / "clone"
    root.mkdir()
    for args in (("init", "-b", "main"), ("config", "user.email", "t@e.com"),
                 ("config", "user.name", "T")):
        subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)
    (root / "README.md").write_text("hi\n")
    subprocess.run(["git", "add", "README.md"], cwd=root, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "init"], cwd=root, check=True, capture_output=True)
    return root


def _stage(clone: Path, name: str, body: bytes) -> None:
    (clone / name).write_bytes(body)
    subprocess.run(["git", "add", "-f", name], cwd=clone, check=True, capture_output=True)


@pytest.mark.parametrize("state", ["worktree-copy-deleted", "worktree-copy-blanked",
                                   "committed-then-deleted-locally"])
def test_the_shape_gate_reads_the_INDEX_for_a_tracked_candidate(clone, state):
    """The three states that used to pass in silence, each rebuilt.

    In all three `git cat-file -p :tracker-login.json` still hands out the cookie, so `git add -A`
    would publish it — while the pre-#630 scan, reading `path.read_text()`, saw either nothing or
    `{}` and said the repo was clean. Measured on a clean clone before the fix: 1 passed, 1 passed,
    1 passed. CI never saw any of it, because a fresh checkout has no worktree/index divergence to
    have — which is exactly why the LOCAL run was the half making a promise it could not keep.
    """
    _stage(clone, "tracker-login.json", _SHAPE)
    if state == "worktree-copy-deleted":
        (clone / "tracker-login.json").unlink()
    elif state == "worktree-copy-blanked":
        (clone / "tracker-login.json").write_bytes(b"{}")
    else:
        subprocess.run(["git", "commit", "-m", "add"], cwd=clone, check=True, capture_output=True)
        (clone / "tracker-login.json").unlink()

    offenders, _ = _scan_for_storage_state_shape(clone)
    assert offenders == ["tracker-login.json"], (
        f"state {state!r}: the index still holds a storage state and `git add -A` would publish "
        f"it, but the scan reported {offenders}"
    )


# MUTATION-CHECKED for #630, selection `tests/unit/test_repo_browser_isolation.py`, `__pycache__`
# deleted and then PYTHONDONTWRITEBYTECODE=1, each round restored from a byte copy and the file
# confirmed sha256-identical; the script refuses unless its target matches exactly once.
# Control round: 0 failed.
#   * read the WORKTREE for tracked candidates too (the pre-#630 body) -> 3 failed, the three
#     index rows above
#   * `json.loads(raw.decode("utf-8"))` (the pre-#630 call) -> 3 failed, the three encoding rows
#
# THE FIRST VERSION OF THESE PINS MEASURED NOTHING, and that is recorded because the failure is
# invisible from a green run. They were written against a COPY of the scan loop living in this
# file, so the `worktree` round above came back 74 PASSED — the mutation landed in the shipped
# scanner while the pins exercised the duplicate beside it. That is the "a pin protects less
# than its name promises" class, caught only because the round was run at all. The fix was to
# lift the real loop into `_scan_for_storage_state_shape(root)` and point both the real test and
# these rounds at it; the numbers above are from AFTER that, and the copy is gone.


@pytest.mark.parametrize(
    ("label", "body"),
    [
        ("utf-8-BOM", b"\xef\xbb\xbf" + _SHAPE),
        ("utf-16", _SHAPE.decode().encode("utf-16")),
        ("utf-32", _SHAPE.decode().encode("utf-32")),
    ],
)
def test_the_shape_gate_survives_an_encoding_it_does_not_expect(clone, label, body):
    """`json.loads(path.read_text(encoding="utf-8"))` lived inside `except (OSError, ValueError)`,
    and `UnicodeDecodeError` is a subclass of `ValueError` — so a shaped file in any of these
    encodings was skipped without a word. Measured: from BYTES `json.loads` accepts all three
    (it sniffs the encoding); from text it raises on all three.

    Narrower than "encoding is covered", which is the claim the card offered and this test
    declines to make: a file with a genuinely invalid byte still raises, and must — it is not
    valid JSON in any encoding, so that is a correct refusal rather than a silent skip. No real
    producer writes these forms either (playwright-core 1.62.0 writes utf8 via JSON.stringify).
    The reason to close them anyway is the shape of the bug, not its reachability: a `continue`
    that goes quiet on a credential is the class, and this repo grades that class by what it
    could hide, not by what it does hide today.
    """
    _stage(clone, "tracker-login.json", body)
    offenders, _ = _scan_for_storage_state_shape(clone)
    assert offenders == ["tracker-login.json"], f"{label} slipped past the shape scan"


def test_an_invalid_byte_is_still_refused_and_that_is_correct(clone):
    """The control for the row above — the boundary of what switching to bytes buys."""
    _stage(clone, "tracker-login.json", _SHAPE[:5] + b"\xff" + _SHAPE[5:])
    offenders, _ = _scan_for_storage_state_shape(clone)
    assert offenders == [], "a byte sequence that is not JSON in any encoding is not a candidate"


def test_an_ordinary_tracked_file_is_still_not_an_offender(clone):
    """The other control: the index half must not start reporting things that are merely JSON."""
    _stage(clone, "package.json", b'{"name": "x", "cookies": "not-a-list"}')
    offenders, _ = _scan_for_storage_state_shape(clone)
    assert offenders == []


@pytest.mark.parametrize("staged", [False, True])
def test_the_shape_gate_reads_the_WORKTREE_too_for_a_tracked_candidate(clone, staged):
    """The axis #630's FIRST version broke, and the reason the scan reads a UNION.

    That version swapped the sources instead of adding one: it read the INDEX for every tracked
    path and stopped looking at the worktree. Its reviewer built the inverse of the three states
    the card was filed for and measured the trade — a committed, benign `package.json` whose
    worktree copy is overwritten with a credential and NOT staged. `git status` says ` M`,
    `git add -A --dry-run` says `add 'package.json'`, the ORIGINAL worktree-only scan caught it,
    and the index-only version reported nothing.

    That state is also MORE reachable than the three it was traded for: those need a deliberate
    `git add -f`, this needs only an overwrite of a file the repo already tracks.

    Both parametrisations are the same file with the credential in a different place — unstaged
    (only the worktree has it) and staged (both do) — so the row also pins that a union names the
    path ONCE rather than twice.
    """
    (clone / "package.json").write_bytes(b'{"name": "x"}\n')
    subprocess.run(["git", "add", "package.json"], cwd=clone, check=True, capture_output=True)
    subprocess.run(["git", "commit", "-m", "benign"], cwd=clone, check=True, capture_output=True)
    (clone / "package.json").write_bytes(_SHAPE)
    if staged:
        subprocess.run(["git", "add", "package.json"], cwd=clone, check=True, capture_output=True)

    offenders, _ = _scan_for_storage_state_shape(clone)
    assert offenders == ["package.json"], (
        f"staged={staged}: `git add -A` would publish this worktree copy, and the scan reported "
        f"{offenders}. Reading only the index is what #630's first version did"
    )
