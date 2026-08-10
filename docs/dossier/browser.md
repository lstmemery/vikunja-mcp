# Playwright — изоляция профиля и утечка артефактов в репозиторий

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Догфуд` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

**Committed `.claude/settings.json` sets `PLAYWRIGHT_MCP_ISOLATED=true`** (tracker #558)
— do not delete it as stray local state; `.gitignore` deliberately re-includes that one
file (`.claude/*` + `!.claude/settings.json`). `@playwright/mcp` derives its on-disk
browser profile as `mcp-<channel>-<sha256(first MCP root)[:7]>`, i.e. PER WORKSPACE ROOT,
so different repos never collide — but two `claude` sessions on the SAME repo (a human's
plus the hgdev-acp repo-agent, the normal case here) resolve to one profile, and the
second browser refuses to start at all: `Browser is already in use … use --isolated`,
after ~7 s of lock polling. The env var is the documented equivalent of `--isolated`
(in-memory profile), and project-scope settings DO reach a spawned MCP server's
environment — both measured. It is deliberately NOT a `.mcp.json` entry: a project
`.mcp.json` does not shadow a plugin-provided server (`claude mcp list` then shows BOTH
`plugin:playwright:playwright` and the new one), so that route adds a second browser
instead of fixing the first. Cost: the profile lives in memory, so browser logins do not
persist between sessions.

**`PLAYWRIGHT_MCP_STORAGE_STATE` does NOT buy that cost back, and is deliberately set
NOWHERE here** (tracker #585, measured on the 0.0.78 this machine actually runs). Upstream
documents it as `--isolated`'s complement and it is one — but only for LOADING. Measured:
with isolation the file IS read (cookie + localStorage restored, confirmed server-side by
the `Cookie:` header the browser then sent) and zero profiles hit disk, so the two really
do compose; WITHOUT isolation the same variable is silently ignored. It is never WRITTEN:
after a login, `browser_close` and a clean client shutdown the file stayed byte-identical
(md5, size, mtime_ns) — SIGTERM too — and session 2 read back the seed, not the login. So
it converts "log in every session" into "hand-maintain a seed file", not into persistent
logins. Two further measurements make a committed value actively harmful: a path whose
file does not exist yet makes EVERY `browser_*` call fail (`Error reading storage state …
ENOENT`), which is worse than the status quo for anyone who clones; and the value is a
machine-local path to LIVE SESSION COOKIES — a secret of the same class as
`.vikunja-mcp.env` and `VIKUNJA_NOTIFY_WEBHOOK`, which this repo keeps out of committed
files on principle. The only writer is the `browser_storage_state` tool (hidden behind
`PLAYWRIGHT_MCP_CAPS=storage`, which also exposes 16 other cookie/storage tools). BY
DEFAULT it refuses paths outside the MCP client's roots — the server's cwd, i.e. this
checkout, plus its `.playwright-mcp/` output dir — but that confinement is a default, not a
law: with `PLAYWRIGHT_MCP_ALLOW_UNRESTRICTED_FILE_ACCESS=true` (the same flag SKILL.md
offers agents for `file://`) the identical call wrote a working seed straight outside the
repo, and it restored correctly next session. So a machine-local opt-in that never touches
this repository IS constructible; it is still not worth shipping, for the reason above
rather than for a safety reason — what it yields is a hand-maintained seed that never
updates itself, which is not what the card asked for.

**The `.gitignore` guard reduces that accident; it does not make it impossible.** Claiming
otherwise was this card's own first defect — review disproved it by constructing the leak
under a name the rule missed, and a guard oversold is worse than one honestly described.
`browser_storage_state` takes ANY filename anywhere under its root, so a name-based rule can
only ever cover a LIST: here that is the tool's default
(`.playwright-mcp/storage-state-<timestamp>.json` — the output dir, NOT the repo root, and
`.playwright-mcp/` is ignored wholesale, which also settles #607's page snapshots), all three
lower-case spellings of `storage-state`, `state*.json`, `auth.json`/`cookies.json`/
`session.json`, and `.auth/` for Playwright's documented `playwright/.auth/user.json`. It does
NOT cover `tracker-login.json`, and no pattern that would is safe to write here. Two measured
qualifications on that list, both of which read as universal until you check them: the
`state*.json` glob also hides ordinary files (`states.json`, `src/data/state-defaults.json` —
any basename starting with `state`, at any depth), and the whole list is CASE-DEPENDENT — git
folds case only where `core.ignorecase` is true, which it takes from the filesystem at clone
time, so `storageState.json` (Playwright's own `context.storageState({path})` spelling) is
covered on a macOS checkout and NOT on Linux, where CI runs. The guarantee that
does not depend on the name is a unit test: it asks git what `git add -A` would publish and
fails on any file of storage-state SHAPE (`{"cookies": […], "origins": […]}`) under any name,
tracked or untracked, and at any SIZE — a candidate too big to read is reported rather than
skipped. **"Tracked" became true of the LOCAL run only at #630**, and the gap is worth knowing
because nothing about it was visible: the candidate LIST always came from git, but the BYTES
came from the worktree, so a shaped file that was staged and then deleted from disk, or whose
worktree copy was overwritten with `{}`, or that was committed and then removed locally without
committing the removal, read as clean — three states, all built, all `1 passed`, while
`git cat-file -p :<path>` still handed out the cookie. CI never saw any of it (a fresh checkout
has no divergence to have), which is why this was a hardening and not a leak. The scan now reads
BOTH copies of a tracked candidate — the index blob and the worktree bytes — and that UNION is
#630's own correction: its first version read the index INSTEAD of the worktree, and its
reviewer measured the trade. A committed, benign `package.json` whose worktree copy is
overwritten with a credential and left unstaged is published by `git add -A`, was caught by the
ORIGINAL scan, and went silent under the index-only one — a state more reachable than the three
above. **What makes it more reachable is the INDEX, not a flag: the reason this paragraph gave
until #817 — that the three "need a deliberate `git add -f`" — is measurably false.** Built
rather than reasoned about, on a fresh repository carrying this repo's real `.gitignore`: a
plain `git add tracker-login.json` is rc 0, and all three states assemble from it and are
caught. What the three need is the shaped file IN THE INDEX — one `git add`, plus a
`git commit` for the committed-then-deleted row — against ZERO for an overwrite of a file the
repo already tracks. The conclusion survives; only its reason changes, and that COUNT is the
whole of what "more reachable" is operationalised as here — it does not price the inverse
state's own precondition, a credential landing on an ALREADY-TRACKED path. `-f` is what an
IGNORED name costs, and only for a path git does not YET track: `git add auth.json` on that
stand is rc 1 with git's own "Use -f" hint, while the same name once TRACKED takes a plain
`git add` at rc 0, ignore rules having no say over a path git already carries. And that case
the shape gate COVERS rather than excuses — force-add a shaped `auth.json`, blank the worktree
copy, and it is reported, the index half being immune to ignore rules. It is simply not what
these three states are built from: `git check-ignore --no-index` exits non-zero on
`tracker-login.json` and on `package.json` alike. Neither source alone is "what git would
publish": `git add -A` stages the worktree, `git commit` publishes the index. The same change
reads BYTES rather than utf-8 text, closing three encodings that used to be skipped in silence —
`UnicodeDecodeError` is a `ValueError`, so a BOM'd or UTF-16 export fell into the same `continue`
as "not JSON at all". Not an encoding cure-all: a genuinely invalid byte is still refused, and
correctly, since it is not JSON in any encoding. The format holds a localStorage array PER ORIGIN,
filled from every origin the context
visited, so an export has no fixed upper size and "too large to classify" is exactly what a fat
credential looks like. That part was itself a bounce: the first version capped the scan at 1 MiB
on the reasoning that "a credential export that big is not a thing", and a correctly-shaped
4,194,662-byte export then walked past it with the suite green. That is a GATE — red in the pre-push `pytest` run the
integration recipe already requires, and red in CI — not a lock on `git commit`. Nothing here
is a lock, and the candidates were built rather than argued about: a `.git/hooks` pre-commit
hook does not reach a clone at all, and committing the hooks with `core.hooksPath` pointed at
them does not either — constructed, the DIRECTORY clones and `core.hooksPath` does not (it is
local config, not content), so the clone committed unblocked. A pre-commit framework installs
into `.git/hooks` from a per-clone step and fails the same way. Every stronger option reduces
to "works on whichever machine ran an installer". All of it — coverage, the names deliberately
left uncovered, the collateral, the case split — is pinned in
`tests/unit/test_repo_browser_isolation.py`.

**The same two layers now also cover the browser's OTHER output, because `.playwright-mcp/`
covers less than its name suggests** (tracker #629). That directory holds what the browser names
ITSELF; a `filename` argument is resolved against the SERVER's cwd — the main checkout — so it
lands in the repo ROOT. Measured on the same 0.0.78:
`browser_take_screenshot(filename="x.png")` wrote `./x.png` there, unignored, and SKILL.md
prescribes exactly that call. Layer one is four extension rules (`*.png`, `*.jpg`, `*.jpeg`,
`*.pdf`), affordable because this repo tracks no image or PDF and never has — measured 2026-08-02
with `git log --all --name-only`, which asks about ANY commit touching such a path on any ref,
not just additions. That is the standing `*.html` already had. **But extensions are the wrong axis
and the honest bound is sharper than "a list can't be complete": the name does not decide the
content at all.** Measured, a screenshot asked for as `shot.bin`, or with NO extension, is still
PNG, because the format comes from the `type` argument (png|jpeg). So layer two reads the leading
MAGIC BYTES of what `git add -A` could publish and fails on PNG/JPEG/PDF under any name, needing
no size ceiling because a magic number cannot be hidden by growing the file. **That phrase
"what `git add -A` could publish" was aspirational for two cards, and #819 is what made it true
of this layer.** It kept its own INLINE loop, which read the worktree and consulted the index
only as a FALLBACK — a choice, not the union its sibling had just spent two rounds arguing for —
so it sat one gate away from #630 through that whole argument and came out unchanged, and a
committed PNG whose worktree copy is overwritten with text passed in silence while `git commit`
published the PNG. That is the same defect #630 was bounced for, in the same file, found by
#630's own second-round reviewer and reproduced by construction. The loop is now
`_scan_for_browser_binary_signature(root)` on the SHARED `_publishable_copies` walk — which is
also the only shape that could ever be pinned, since the inline one was reachable only through
the real repository, where the two copies never disagree. Reusing that walk UNCHANGED was
measured and rejected: it blanks anything over `SHAPE_SCAN_MAX_BYTES`, which is right for a
`json.loads` and would have turned "that is a PNG" into "too large to look at" — so it grew a
`prefix` mode that reads eight bytes with no ceiling, and the default mode is byte-identical to
before. **The fix's own second independent pass then found a regression IN the fix, which is why
that pass is a rule here and not a courtesy:** the shared walk asks `cat-file -s` before reading a
blob, and an index entry that does not resolve failed that too and fell down a pre-existing
conflicted-entry skip — so a tracked candidate with no worktree copy and no object left went from
REPORTED to invisible, where the loop being replaced had read the blob directly and reported it.
Measured on a clone with the loose object deleted: pre-#819 `['asset.bin']`, the new walk `[]`.
Both that and the index-side ceiling guard are pinned now; the one branch that is NOT is named as
unpinned in the sweep record rather than counted as covered. **It is complete
about NAMES and about the three formats it names — NOT about formats.** That distinction was the
card's own first defect: an earlier draft called those three "the entire binary surface", and the
second pass disproved it by construction. `browser_network_request` — in the DEFAULT capability
set — takes `part: "response-body"` plus a `filename` and drops the RAW body of any request the
page made into the same root, in whatever format the server sent; measured, a GIF and a ZIP landed
as `.bin`, caught by no rule and no signature. One more binary format is a single capability away,
and the CAP NAME is the anchor that survives, not a tool count: `browser_start_video` is absent
from the default set and present with `PLAYWRIGHT_MCP_CAPS=devtools`. Naming and writing are
different calls there, which is worth stating precisely because the earlier draft did not: it is
`browser_start_video` that takes the `filename`, and it answers only "Video recording started."
with the cwd still empty; the WebM (`1a45dfa3…`) appears in the root of the server's cwd when
`browser_stop_video` answers `- [Video](./vmcp629-video.webm)`.
**A tool total for "every capability on" is deliberately NOT the anchor for those**, and that is
the correction this card was bounced for: in 690d648 `.gitignore` hung that label on a tool total
of 53, and 53 is not that set. That round carried the same label in two more files, on
acceptor/writer counts rather than on a total — enumerated in
`tests/unit/test_repo_browser_isolation.py`, with why a `git log -S` commit count is not a count
of occurrences. Nor does 53 name a
set at all: measured, three different cap combinations reach 53, with 10 or 11 `filename`
acceptors depending which. Every capability on is 69. Cap names survive; tool counts belong to an
npm package pulled at `@latest`, and the full measurement is in
`tests/unit/test_repo_browser_isolation.py`. What NEITHER layer reaches, measured rather than
assumed: on 0.0.78 `tools/list` shows SEVEN tools taking a `filename` on the default
capability set — the
one the shared session server runs, which its tool ROSTER says and the absence of a `--caps` flag
does not, since `PLAYWRIGHT_MCP_CAPS` and `--config` carry capabilities too — six of which write,
and `browser_snapshot`, `browser_console_messages`, `browser_network_requests` and
`browser_evaluate` drop the page's own
TEXT and its request query strings in the same root as plain text — no listable extension and no
signature. **What used to follow that clause was "indistinguishable from a legitimate file here",
and it was an overclaim: tracker #752 refuted it by BUILDING the discriminator it said could not
exist.** The word survives for exactly ONE of those four writers now. Extension and leading bytes
really are useless here — that half was measured on #629 and stands — but the third axis, SHAPE,
was never put to a test; it was waved off in `.gitignore` as "considered and dropped" on two
grounds, of which one was false and the other too wide. Three of the four write a MACHINE GRAMMAR
that a whole-file matcher separates from prose at a measured cost of ZERO false reds over this
repo: the aria snapshot is one YAML list item per line carrying `[ref=…]` tokens, the console log
is `[<n>ms] [LEVEL] … @ <url>` per line (or a totals header plus `[LEVEL]` lines when written to
an explicit `filename`), and the network log is `N. [METHOD] <url> => [<status>] …`. Only
`browser_evaluate` is genuinely indistinguishable, for a structural reason rather than for want of
a pattern — it writes whatever the evaluated JS returned, measured as a bare JSON string literal —
and no version of any gate could give it a grammar. The false ground was "a scan fires on any file
that DOCUMENTS the shape": true of a marker grep, false of a whole-file grammar, which is the same
distinction that lets the storage-state gate coexist with the paragraphs describing storage state.
That difference is now itself a measurement rather than an argument — the naive grep and the
grammar returned the SAME zero until this card landed, and
`test_the_naive_marker_grep_would_be_red_on_arrival_and_the_whole_file_grammar_is_not` re-derives
the divergence on every run, because the fixtures and this very paragraph are what make the naive
one red. The lock is `test_no_file_of_browser_text_artifact_shape_is_reachable_by_git`, built on
the same union-of-index-and-worktree candidate walk as the storage-state gate (now one shared
`_publishable_copies`, not two copies) and with the same refusal to skip a candidate too large to
read. Its own deliberate false NEGATIVES are named where it lives: an artifact of fewer than three
lines, and the two EMPTY forms, which carry no page content to leak.
A marker planted on a probe page came
back in three of those files, and a token placed in a request's query string in two — that second
count is CONDITIONAL, re-measured on #703 rather than carried over: the tokened URL is in the
network log always and in the CONSOLE log only when the request errors, which is what #629's probe
happened to do. Escaping the
checkout entirely is refused by default (`File access denied … outside allowed roots`, the roots
being the server's cwd and its `.playwright-mcp/`), so the spill is confined to exactly the
directory git can see — but NOT to its root: measured on #703, a `filename` carrying a
subdirectory (`src/vikunja_mcp/…md`) is accepted and lands beside the sources, so a root-anchored
rule would have missed it too.

**#703 closed that residual at the WRITE SITE, the only one of the three axes compared (extension,
leading bytes, directory) that reaches text.** SKILL.md no longer prints a bare name: it prescribes
`filename` under `.playwright-mcp/`, the one directory `.gitignore` already covers wholesale,
independently of name and format. Measured — all four text writers and the screenshot accept the
prefix and land there. Two things came with it, both of which read as details and are not. The
directory must EXIST first: a caller-chosen `filename` goes through `workspaceFile()`, which does
NOT mkdir, whereas the auto-named artifacts go through `outputFile()`, which does — measured
`ENOENT` on a snapshot, a screenshot and a nested path, all three resolved by that one function, so
SKILL.md carries the `mkdir -p`. And `--output-dir` is NOT the fix it looks like: it feeds
`outputFile()` only, so pointed OUTSIDE the repo it moved the auto-named files while the explicit
one still landed in the checkout root. Nor is there another knob to reach for, and the reason is
structural rather than a survey of env vars: the base of that resolve is the SERVER'S WORKSPACE —
`clientInfo.cwd = firstRootPath(clientRoots)`, i.e. the first root the MCP CLIENT declares, falling
back to the server's cwd only when a client declares none — so it is set by the client, not by
anything this repository can commit. (That is the same `cwd` whose hash names the browser profile
in #558's note above.) For a `claude` session both are the main checkout, which is why the
artifacts land there; "the checkout" is a property of that setup, not of the tool. What the fix
does NOT do: it is a rule for agents, not a lock, and it protects only where that directory is
ignored (here, yes; a consumer's repo is its own question). The mechanism under the rule is one
cross-file pin — `test_every_filename_skill_md_prescribes_is_excluded_by_this_repos_gitignore`
asks git whether this repo would publish each `filename` SKILL.md prints, so it goes red both if
the rulebook drifts back to a bare name and if `.gitignore` drops the directory rule. Its own
bound is pinned in its docstring: it reads PROSE, so it sees only the spellings its pattern
matches — an independent attack pass got a leaking value past the first version of it by writing
the prescription in JSON, which is what an MCP argument actually is.

**`--output-dir` was the SECOND door into that same directory, and #703 did not close it**
(tracker #736). The `filename` fix reaches the caller-named artifacts; the AUTO-named ones are
resolved by a different function, and where they go is set by the flag in SKILL.md's own recipe
for launching an agent's OWN browser, which said `--output-dir <каталог с id задачи>` — a
placeholder that reads equally as a subdirectory of the worktree and as one of the scratchpad.
Only the second reading was safe. Measured before fixing: with `--output-dir 736-out`, git
reported `?? 736-out/` and `git add -A --dry-run` STAGED both `page-<ts>.yml` — the ARIA
SNAPSHOT, i.e. the page's own text, a link's `?token=` query string included — and
`console-<ts>.log`; only the screenshot was covered, by the `*.png` rule above. The recipe now
says `--output-dir .playwright-mcp/<id>`, the same directory the `filename` rule already names,
and the pin above grew a second half that asks git the same question about each `--output-dir`
value the rulebook prints. Two measurements shape what is claimed for it. Pointing the flag
OUTSIDE the repo WORKS — no `File access denied … outside allowed roots`, because that refusal
belongs to the `filename` resolver and `--output-dir` defines a root rather than escaping one —
so "outside" stays a legitimate answer that SKILL.md names and no pin here can check, having no
path to put to git. And the directory is created by the first `browser_navigate`, not at server
start, which is why a `filename` under the same prefix then needs no manual `mkdir -p`: an
ordering fact, not a guarantee. The sweep also moved the fix: deleting `--output-dir` from the
FENCED recipe while leaving the prose mentions measured control 0 failed / mutation 0 failed,
because a prose restatement is still a value — so the flag's presence in the runnable line is
pinned next door, where `--isolated` and `--channel=chrome` already are.

**Two things that pin does NOT mean, both established by an attack pass that BUILT them rather
than argued them.** First, **the flag's absence is not a leak**: run with no `--output-dir` at
all, the DEFAULT output dir is `<cwd>/.playwright-mcp/` — the ignored one — with `git status`
empty and `git add -A --dry-run` staging nothing (the tool prints it too: `Allowed roots:
<cwd>/.playwright-mcp, <cwd>`, and #585's note recorded the same default long before). The card's
hazard is the FREE CHOICE the old placeholder invited, not the flag's absence, so that assertion
guards the naming rule and the first version of it claimed a leak it had borrowed from a round
where the flag was present and pointed somewhere bad. Second, **a pattern over prose is only as
wide as the spellings it was written from**: `--output-dir=VALUE` is accepted by the server and
spills identically, and against a `\s+`-only pattern the recipe rewritten to `--output-dir=554-out`
measured control 0 failed / mutation 0 failed — fully green. That is the SAME defect this file's
`filename` pattern was already widened for once; the pattern now takes `(?:\s+|=)`. A directory
that is merely *some* ignored directory is not the point either — eight rules here exclude
regardless of filename (`dist/`, `.venv/`, `__pycache__/`, `.pytest_cache/`, `.ruff_cache/`,
`.superpowers/`, `.auth/` and this one); `.playwright-mcp/` is the one that is ALSO where the
`filename` prescription sends things, which is what keeps it one directory to reason about.
