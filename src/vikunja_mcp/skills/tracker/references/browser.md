# Browser (playwright): your own process, a shared profile, artifacts

> **A reference for SKILL.md, not rules of its own.** Read it **before calling any `browser_*`
> tool**. What is binding lives in SKILL.md itself — what is worked through here are the shapes of
> the answers, the measured gotchas and the reasons the rule is written exactly the way it is.

### Browser (playwright): you MAY bring up YOUR OWN — the shared one you can only notice

"Launch the browser so that it does not get in anyone else's way" is achievable, and it is the
default answer. Only the knob here is not a tool parameter but YOUR OWN PROCESS, which you bring up
yourself from bash: `npx` is everywhere, and no separate e2e runner in the project is needed for
this. The order is: need a browser — bring up your own; went into the SHARED one — treat the page
as somebody else's until you have cross-checked it.

#### Your own browser: how, and what it costs

**Only a screenshot needed** (the most frequent case — evidence for a card) — one line from your
own worktree, with no MCP at all:

```sh
npx -y playwright@latest screenshot --channel=chrome \
    --viewport-size=1280,800 "$URL" shot-554.png    # 554 = the id of YOUR task
```

Verified by running it: ~2 s; the process is your own and exits BY ITSELF (there is nothing to
kill); the file lands where you named it — in YOUR worktree, not in the main checkout; two such
runs from different worktrees at the same time gave `rc=0` and both files. `--channel=chrome` is
mandatory: without it the CLI demands `npx playwright install` (that refusal was obtained live).
**And it will also DIE together with the worktree:** `shot-554.png` is ignored by the `*.png` rule,
and the `dirty` guard does not see ignored files — `--release`/`--gc` will remove the worktree as
clean, naming the file in `removed_ignored` only after the fact. Need it later — attach it to the
card (`attach_file`) BEFORE `advance(to='review')`.

**Steps needed** (a click, typing, a wait between actions) — bring up YOUR OWN playwright server:

```sh
# 554 = the id of YOUR task. The directory here is NOT arbitrary: see the artifacts bullet below
npx -y @playwright/mcp@latest --isolated --headless --output-dir .playwright-mcp/554
```

`--isolated` = the profile is held in memory and never written to disk, so it does not intersect
with the session's shared browser profile at all. Verified on two own servers at once: the first
one's page survived two `navigate`s from the second, and the first one's `browser_close` did
nothing to the second. Ready in ~1 s, dies on SIGTERM in 0.1 s.

What this costs — know it BEFORE, not after:

- **it is NOT driven by the `browser_*` tools**: those go to the session's shared server. Your own
  you drive with a script over stdio (JSON-RPC, one line per call) — so for a simple screenshot
  take the CLI above, and bring up a server when there really are several steps;
- **it is a PROCESS, and it is yours**: did not kill it — it leaked exactly like the container
  above;
- **artifacts go to the SERVER's cwd** (your own you bring up from bash and declare no roots to it,
  so for YOUR server that is precisely the cwd) — launch it from your own worktree, **and ALWAYS
  give `--output-dir` as `.playwright-mcp/<task id>`.** More precisely about that "cwd", because
  whether the spill is dangerous depends on it: the ones auto-named by default go not into the root
  of the cwd but into `<cwd>/.playwright-mcp/` (measured by a run WITHOUT the flag: `git status`
  empty, `git add -A --dry-run` empty — the tool itself prints this in its refusal, `Allowed roots:
  <cwd>/.playwright-mcp, <cwd>`), whereas an explicit `filename` goes exactly into the cwd. **That
  is, the flag here does not save you from a spill, it REMOVES THE AMBIGUITY: it says WHERE, and
  substitutes your id.** What is dangerous is precisely the free choice of directory —
  `--output-dir` takes the auto-named ones away (`page-*.yml`, `console-*.log`, an auto-named
  screenshot — also `page-*.png`; the list is not exhaustive — `--save-session`, for one, adds its
  own), and `page-*.yml` is an aria snapshot, that is, the TEXT of the page together with the query
  strings of its links. A directory named simply after the id and lying in the worktree is covered
  by nothing IN THIS repository: measured — `736-out/` arrives in `git status` as `?? 736-out/`,
  and `git add -A --dry-run` PUTS INTO THE INDEX both `page-*.yml` and `console-*.log` (the
  screenshot there is covered only by the `*.png` rule). Under `.playwright-mcp/` the same three
  files are closed by one `.gitignore` line: `git status` empty, `git add -A --dry-run` empty;
- **There is NOT ONE knob here, and listing FLAGS is useless — the axis is the DIRECTORY (#751).**
  The same output directory is set by FIVE spellings, all measured by running them on 0.0.78:
  `--output-dir` itself through a space and the same one through an equals sign (`--output-dir=`),
  a `--config <path>` file with an `"outputDir"` key, and TWO environment variables —
  `PLAYWRIGHT_MCP_OUTPUT_DIR` and `PLAYWRIGHT_MCP_CONFIG` (the second picks up the same config file
  with NO flag at all). The spill is ONE AND THE SAME for all of them: a config whose `"outputDir"`
  pointed at `cfg-out-751` took all three auto-named files there — `git status` showed
  `?? cfg-out-751/`, and `git add -A --dry-run` STAGED `page-*.yml` and `console-*.log`; the first
  held the page's aria text together with the `?token=` from a link's query string, the second the
  console and the same token, and covered was only the `.png`, by the `*.png` rule. **But an
  EXPLICIT `--output-dir` BEATS the other three** — measured pairwise against the config and
  against each of the two variables: the three files landed in `.playwright-mcp/751` every time,
  and the foreign directory was not created at all. Hence the rule, and it is about ONE line: **do
  NOT REMOVE the flag from the launch line** — it is exactly what makes the recipe robust both to a
  stray config and to an inherited environment variable. Taking a config INSTEAD of the flag —
  write `{"outputDir": ".playwright-mcp/<id>"}` in it, the very same directory. And know the BOUND
  of this rule: nobody will show you an environment variable in a recipe, so a pin over prose does
  not see it at all — it is inherited from whoever launched you, and the only protection from it is
  the same one, the flag on the line;
- **an explicit `filename` knows nothing about `--output-dir` at all**: it is resolved against the
  cwd (verified — with `--output-dir` pointed outside the checkout the auto-named ones went there,
  while a snapshot with an explicit name still landed in the root), so the rule "a `filename` only
  with the `.playwright-mcp/` prefix" (see "The shared browser") is exactly the same here. The
  recipe above does NOT require a manual `mkdir -p` — but that is a fact about ORDER, not a
  guarantee: measured, with `--output-dir .playwright-mcp/<id>` the directory is there neither
  before the server starts nor after `initialize`, and BOTH levels (including the missing parent
  `.playwright-mcp/`) are created by the very first `browser_navigate` — after which an explicit
  `filename` under the same prefix goes through without ENOENT. That order is natural (there is no
  page before `navigate`, and the explicit writers work off the page), but if your first writing
  call comes for some reason before any auto-named output — the directory is not there yet. Took
  `--output-dir` outside — the parent will not appear at all
  ever, and then before the first explicit `filename` you need `mkdir -p "$PWD/.playwright-mcp"` in
  the worktree you brought the server up from. Neither kind of artifact is part of your diff — do
  not commit them;
- **the bound of this is exactly the same as that of the `filename` rule: it is a RULE, not a lock,
  and it is local.** `--output-dir` accepts any directory, and it goes outside the repository
  FREELY — measured, there is NO `File access denied … outside allowed roots` refusal here (that
  refusal is about an explicit `filename`, which has a different resolver). The prescription above
  is ONE so that there is one directory for both kinds of artifact rather than "choose for
  yourself"; but it rests on `.playwright-mcp/` being ignored IN THIS repository. In a FOREIGN one
  ask yourself — `git check-ignore --no-index -v -- .playwright-mcp/page-x.yml` — and if there is
  no answer, TAKE THE DIRECTORY OUTSIDE the repository: git then does not see the artifacts at all,
  at the cost of the manual `mkdir` above and one server's output spread over two places;
- **`file://` is blocked** by default (`Access to "file:" protocol is blocked`): bring up
  `python3 -m http.server $PORT` with a port derived from the id (see above), or add
  `--allow-unrestricted-file-access`;
- **`--headless` is mandatory** — by default the browser is headed, and the window will crawl out
  onto the human's screen.

Your project's own browser tooling (an e2e runner, your own playwright script) is the same case:
your own process, run it from your own worktree, derive the dev server's port from the id.

#### The shared browser: it cannot be prevented — it can be NOTICED

If you do work with the shared MCP browser after all, proceed from the facts (verified on a live
session):

- **It is ONE per session, not per agent.** Every `claude` process has exactly one child
  `@playwright/mcp`, and your siblings are subagents of THE SAME session: which means one server,
  one browser, one profile, one CURRENT page and one viewport for all of you.
- **The tools have no isolation parameter.** `browser_navigate` takes only a url,
  `browser_snapshot`/`browser_take_screenshot` work with "the current page", and `browser_tabs`
  indexes the GLOBAL list of tabs, where the indexes shift the moment a sibling closes its own.
  Your own tab is NOT isolation.
- **`--isolated`/`--user-data-dir` on the SHARED server are arguments of its LAUNCH** in
  `.mcp.json`/the plugin's config: unavailable to an agent, and they isolate per MCP CLIENT, not
  per subagent. That is exactly why your own browser is a separate PROCESS and not a flag on the
  shared one.

The rules are not "prevent it" but "notice it and redo it":

- **Work in ONE unbroken run**: navigate → looked → took the screenshot. The window in which a
  sibling takes the page away is exactly the pauses between your calls.
- **Do not believe the page is still yours — and cross-check with what ANSWERS.**
  `browser_take_screenshot` NEVER prints `Page URL` (verified with your own server in four
  configurations), so looking for that line in its answer is pointless. Right next to the
  screenshot — and before `attach_file` — call `browser_snapshot` and cross-check the `Page URL`
  FROM ITS answer against where YOU navigated. It does not match — a sibling took the page away:
  navigate again, re-shoot, draw no conclusion.
- **No line — no confirmation.** A `browser_snapshot` that succeeded ALWAYS prints `Page URL`: it
  requests a snapshot, and with it the `Page` section renders unconditionally — it depends neither
  on "the page has changed" (that flag is eaten by any previous answer, a sibling's included) nor
  on `--snapshot-mode`. Which means the line is missing only when the call did NOT succeed, and
  that is "could not check", not "there is no divergence". The absence of an error message is not
  proof: the same trap as with a sha that is echoed back.
- **Do not call `browser_close` and `browser_resize` at all**: they act on the shared browser —
  they close the page and change the viewport for EVERYONE, in the middle of somebody else's work.
- **The shared browser's files fall into the MAIN CHECKOUT, not into your worktree**, and by
  default — into its ROOT. Formally that is not "the server's cwd" but its WORKING ROOT: the server
  takes the first root declared by the MCP client, and only if the client declared no roots — its
  own cwd; for a `claude` session that is its working directory, i.e. the main checkout (verified
  on a screenshot ordered by an agent from a task worktree). What git sees out of that root varies:
  a bare screenshot is covered here by the `*.png/*.jpg/*.jpeg/*.pdf` rules, whereas a TEXT
  artifact with a bare name is covered by nothing and is visible in `git status` as `?? untracked`
  (measured on five files).
- **Therefore ALWAYS give `filename` with the `.playwright-mcp/` prefix:**
  `browser_take_screenshot(filename=".playwright-mcp/vmcp-554-probe.png")`,
  `browser_snapshot(filename=".playwright-mcp/vmcp-554-snap.md")`. That is the very directory the
  browser itself puts its auto-named output into, and in this repository ONE `.gitignore` line
  covers it wholesale. For TEXT artifacts there is nothing to cover a bare name with, and that is
  not an unfinished list: `filename`
  is accepted by seven tools of the default `@playwright/mcp` **0.0.78** set (the count is
  version-bound, and the recipe above puts `@latest` — on another version re-check it yourself; the
  prefix rule itself does NOT depend on the number, it is about the DIRECTORY, and survives any
  count), of which `browser_snapshot`, `browser_console_messages`, `browser_network_requests` and
  `browser_evaluate` put down the TEXT of the page and the query strings of its requests (measured:
  a marker from the page came back in three files; a URL with a token from a query string — in the
  network log ALWAYS, and in the console log additionally when the request answered with an error —
  verified in both forms). Such a file has neither an extension that can be listed (`.md`, `.txt`,
  `.json`, no extension at all — that is ordinary repository content), nor a signature that can be
  recognised by its first bytes. Of the three axes compared here (extension, first bytes,
  directory), the directory is the only one that takes both text and bytes under any name, and does
  it with an already existing line at that. The task id in the name is still needed: the directory
  is shared across the whole session, and the same name from two agents is the same collision as
  with the container name and the port above.
- **CREATE the directory before that — the tool does not create it itself.** An explicit `filename`
  is resolved by a different function than the auto-named ones, and only the second does `mkdir`;
  on a missing directory any such call fails with `Error: ENOENT: no such file or directory, open
  '…/.playwright-mcp/…'` (measured on a snapshot, on a screenshot and on a nested path; one and the
  same code resolves them all, which is why the other writers behave the same). In the default
  configuration the directory is created by the very first `browser_navigate` — but that is a
  consequence of somebody else's setting, not a guarantee, and with `--output-dir` pointed outside
  it will not appear at all:

  ```sh
  MAIN=$(git worktree list --porcelain | head -1 | cut -d' ' -f2)  # main checkout = session root
  mkdir -p "$MAIN/.playwright-mcp"
  ```

- **Give `attach_file` an ABSOLUTE path `<main checkout>/.playwright-mcp/<name>`** — in your own
  worktree that file does not exist. Do not commit the artifact itself: it should not get into the
  diff at all.
- **The bound of this rule.** It protects exactly where `.playwright-mcp/` is ignored (in this
  repository — yes; in a foreign one look for yourself); and it is a RULE, not a lock: the tools
  will still accept a bare name, and the spill is not even confined to the root — a `filename` with
  a subdirectory (`src/…`) is accepted and lands beside the sources. By default you cannot get
  outside the working root (`File access denied … outside allowed roots`; there are two roots — the
  root itself and its output directory), so under default flags the spill stays where git sees it;
  `--allow-unrestricted-file-access` lifts that ban entirely.
- **Before you attach a screenshot as evidence** (see "A visually verifiable result") — cross-check
  the `Page URL` with a snapshot once more. This is the only place where the race spoils not
  convenience but the EVIDENCE: a screenshot of somebody else's page, attached to your card, the
  reviewer will take for your work.
- **The bound of the rule.** Everything said is about subagents of ONE session. If a SECOND `claude`
  session works in the same project directory, it has its own server, but the browser profile is
  shared — and the second browser simply will not come up: `Browser is already in use … use
  --isolated` after ~7 s of waiting for the lock. The refusal is LOUD, not quiet — it will not
  silently slip somebody else's page to you. The profile is derived as
  `mcp-<channel>-<sha256(workspace root)[:7]>`, so DIFFERENT repositories never collide: what is
  left is exactly two sessions with ONE root. This is not your mistake, and it is fixed at the
  PROJECT level with one line — `PLAYWRIGHT_MCP_ISOLATED` = `"true"` in the `env` block of the file
  `.claude/settings.json` (the env equivalent of `--isolated`; verified that a project-scoped
  `.claude/settings.json` does reach the MCP server's environment). The cost is the profile in
  memory: logins do not persist between sessions. And `PLAYWRIGHT_MCP_STORAGE_STATE` does NOT
  cancel that cost — do not offer it as a fix: measured, the file is only READ (when the context is
  created) and is NEVER written back, so a login from session 1 does not travel over into session
  2, and a path to a file that does not exist yet drops EVERY `browser_*` call.
  Do not introduce this into somebody else's project silently: look at whether it
  is already on, and if not — say so in your report. None of this concerns your own `--isolated`
  browser — it has no profile on disk.
