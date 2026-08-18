# Documentation

This project splits its writing in two, and the split is deliberate:

- **Rules** are short, imperative and binding. They live in [`CLAUDE.md`](../CLAUDE.md) at the
  repo root (for anyone working *in* this codebase) and in
  [`SKILL.md`](../src/vikunja_mcp/skills/tracker/SKILL.md) (for an agent working *through* the
  tracker). Both are loaded into every session's context, so both are kept small — their size is
  pinned by a test that ratchets downward.
- **Evidence** is long, specific and dated. It lives here, in `dossier/`, one file per
  subsystem, linked from the rule it belongs to.

The reason for the split is a failure this repo kept repeating: a guard would get "fixed" by
reasoning about it instead of measuring it, and the fix would introduce a new hole — four rounds
running, on the same file. The rules stayed short so they'd be read; the measurements moved here
so they'd still exist. **If you are about to change a guard, read its dossier first.** The rule
above it is terse precisely because the proof is down here.

> **Note on language.** The dossiers are written in Russian. Everything else — the README,
> `CLAUDE.md`, `SKILL.md`, the code and its docstrings — is English. Each entry below carries a
> one-line English summary so you can tell whether a file is worth translating for your purpose
> before you open it.

## The dossiers

| File | What's in it |
| --- | --- |
| [**workspace.md**](dossier/workspace.md) | The most important one. Per-task git worktrees and the `--gc` reaper: why `merge --ff-only` is not atomic and what a half-applied sync leaves behind, the typechange-onto-a-live-gitlink gap, why the probes read the *tree* instead of trusting git's messages, and why `git diff` had to become `diff-index`. Every guard in the file that can destroy work is argued for here with the state that was constructed to test it. |
| [**releases.md**](dossier/releases.md) | The `stable` channel: how the auto-release job races the task commits that trigger it, the four constructed ways a release could be "swallowed" and which two are closed, why a bare `--force-with-lease` was measured and rejected, and the run-timing data (40 runs) behind the rule that a run's *outcome* is read once and last. |
| [**testing.md**](dossier/testing.md) | How one mutation sweep lied by a factor of 16 — and in both directions at once. The four measured ways a control round can be blind (stale bytecode, a copied tree whose venv still points at the original source, a concurrent writer, a constant background failure), and what the naive "every quoted string must exist" rule would have cost. |
| [**browser.md**](dossier/browser.md) | Playwright profile isolation and artifact spill: exactly what leaked into the repo and how it was measured, why `PLAYWRIGHT_MCP_STORAGE_STATE` is set nowhere here despite being documented as the complement to `--isolated`, and where each gitignore gate is deliberately blind. |
| [**claimable.md**](dossier/claimable.md) | The `claimable` command's cross-repo contract: the dogfood regression that was costing $105/day (a supervisor booting a paid agent every poll tick that could claim nothing), and why the stderr breadcrumb trail must stay terse. |
| [**linting.md**](dossier/linting.md) | Why line length is two numbers and only one of them is a gate, what the 120→110 step actually cost, why running the formatter would not have caught the defect that created the rule, and the measured shape of both exemptions ruff applies on its own. |
| [**config.md**](dossier/config.md) | The four config layers and the two rules that run in opposite directions across them: secrets never come from the committed toml, team policy never comes from the environment. Plus why the WIP limit gates one transition rather than policing a count. |
| [**api.md**](dossier/api.md) | Vikunja REST gotchas: `PUT` creates and `POST` fully replaces, so every update is read-modify-write; what a kanban view update must always send or the board loses its columns; and why board pagination has no fallback page size (a guessed one truncated the board, and a truncated board got a live worktree reaped). |
| [**workflow.md**](dossier/workflow.md) | Stages, gates, comment markers, and push-review: why every task gets an independent review rather than just bug fixes. |

## Conventions worth knowing before you send a patch

Three house rules are load-bearing and none of them is obvious from reading the code:

1. **Measurements are dated or sha-anchored, never given as bare numbers.** A date does not name
   a tree. Where a reader will *act* on a figure, the property gets asserted in a test instead of
   written into prose.
2. **A mutation sweep opens with an unmutated control round**, and every round is reported as a
   delta against it, in the same paragraph. `N failed` means nothing unless you know the same
   selection failed zero times before anything was mutated.
3. **Counts of tests are floors, not exact figures.** An exact count is stale the moment a
   sibling lands; a floor still catches the thing it's for, which is a mistyped path selecting no
   tests at all and printing something that looks like a pass.

[CONTRIBUTING.md](../CONTRIBUTING.md) has the rest, with the commands.
