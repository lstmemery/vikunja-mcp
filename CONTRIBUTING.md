# Contributing

Patches welcome. This file covers the mechanics; [`docs/`](docs/) covers the reasoning behind
the rules that look arbitrary.

## Setup

```bash
uv sync                        # Python 3.11+, uv
uv run ruff check .
uv run pytest tests/unit -q    # 500+ unit tests, no network, no container
uv run vikunja-mcp --version
```

Unit tests drive the workflow through an in-memory fake of the REST client
(`tests/unit/fakes.py::FakeAPI`) — **keep it 1:1 when you extend `VikunjaAPI`**, or the fake
starts agreeing with code that the real server would refuse.

## Integration tests

They exercise a real Vikunja container and skip themselves unless `VIKUNJA_TEST_URL` is set.
They exist to catch what a fake cannot: permission scopes, pagination shape, relation shapes,
rate limits.

```bash
docker run -d --name vikunja-test -p 3456:3456 \
  -e VIKUNJA_DATABASE_TYPE=sqlite -e VIKUNJA_DATABASE_PATH=/tmp/vikunja.db \
  -e VIKUNJA_FILES_BASEPATH=/tmp/files -e VIKUNJA_SERVICE_JWTSECRET=integration-test-secret \
  -e VIKUNJA_SERVICE_PUBLICURL=http://localhost:3456/ -e VIKUNJA_SERVICE_ENABLEREGISTRATION=true \
  vikunja/vikunja:2.3.0
until curl -sf http://localhost:3456/api/v1/info >/dev/null; do sleep 1; done

VIKUNJA_TEST_URL=http://localhost:3456 uv run pytest tests/integration -q
docker rm -f vikunja-test
```

Vikunja rate-limits `/login` (10 requests per 60s, shared with `/register`); the integration
`conftest` retries on HTTP 429 with backoff. That is expected, not a bug.

## Where changes belong

| Change | File |
| --- | --- |
| A process rule, a stage, a gate | `src/vikunja_mcp/workflow.py` — with a unit test per gate |
| REST behavior, pagination, a Vikunja quirk | `src/vikunja_mcp/api.py` |
| Tool wiring, agent-facing docstrings | `src/vikunja_mcp/server.py` |
| Anything that shells out to git | `src/vikunja_mcp/workspace_cmd.py` — and nowhere else |

That last row is a rule rather than an accident: a subprocess in the stdio server's path is a
new class of crash, so `server.py`, `workflow.py` and `api.py` stay git-free.

Tool docstrings in `server.py` are **agent-facing UX copy**. They are the only instructions many
agents will read, so keep them prescriptive — when to call the tool, not merely what it does.

## House rules that are easy to trip over

**Line length is two numbers.** Wrap at **100**; CI goes red at **111**. The band between is
honest slack, not permission — a 103-character line ships green, so measure your own additions
rather than reading a green `ruff check` as "wrapped correctly". Measure in **characters**, not
bytes: this repo's prose is full of em-dashes (3 bytes each). It is a ratchet — lowering the
ceiling is the intended direction.

**A mutation sweep needs a control round.** If you claim a test pins something, prove it: run the
same selection *unmutated* first and report every round as a delta against that control, in the
same paragraph. Count failures by lines beginning `FAILED `, never by pytest's `N failed`
summary — this repo's docstrings contain sweep records, and a failing test prints its own
docstring inside the traceback, so a naive parser reads the mutant's own prose and reports zero.
Drop `-q` in a scripted sweep, or you get no `collected` line to cross-check against.

**Test counts are floors.** `500+` is a tripwire for a mistyped path selecting nothing; don't
re-pin it to an exact number, which is stale by the next landing. If a figure genuinely needs
precision, name the sha it was measured at — a date does not name a tree.

**Prose that quotes a repo string is checked.** A sentence using one of the assertive idioms in
`tests/unit/test_repo_quotation_claims.py` must have every phrase it quotes actually occur
somewhere in the tree. If you are quoting something *not* from this repo — another project, a
tool's output, a wording quoted because it was retracted — add it to that file's ratchet with
your reason.

**Changing a guard? Read its dossier first.** Especially in `workspace_cmd.py`. The rules in
`CLAUDE.md` are short because the measurements behind them are in
[`docs/dossier/`](docs/dossier/); this repo has repeatedly re-broken a data-loss guard by
reasoning about it instead of constructing the state and measuring.

## Commits

Build the message with a heredoc, not `-m`:

```bash
git commit -F - <<'MSG'
fix(workflow): what changed and why (tracker #N)
MSG
```

Inside double quotes a backtick is command substitution, and the house style wraps every
identifier in backticks — so `-m "fix \`claim\`…"` silently eats part of your message, or worse,
inserts the output of whatever it ran. The quoting of the heredoc delimiter is load-bearing.

**Never let a CI-skip marker into a commit message, including inside a code span.** It is matched
anywhere in the message, so a commit that merely *quotes* the auto-release commit's subject
cancels its own CI run. The push succeeds and the change looks landed, but no run exists, no
release fires, and it never reaches consumers. After pushing, confirm a run exists for your sha
with the **full 40-character** sha (an abbreviated one returns `[]`, which reads exactly like
"no run"):

```bash
gh run list --commit "$(git rev-parse HEAD)"
```

A run that *exists* is not a run that *passed* — check `status` before `conclusion`, because an
in-flight run renders `conclusion` as the empty string rather than null.

## Releases

Don't bump versions by hand for a patch. Every green push to `main` auto-bumps, tags, and moves
the `stable` branch. Minor and major bumps are a deliberate hand-edited commit; rollback is
moving `stable` back onto an older tag. See [`docs/dossier/releases.md`](docs/dossier/releases.md).

Because that cuts a tag per landing (~8 a day), a weekly `prune-tags` workflow reaps the middle
of the stream. It keeps every `vX.Y.0`, the newest 40 versions contiguously, one tag a day for
the last 30 days, one a week beyond that, and whatever `stable` and `main` currently point at.
The rule is a pure function in `scripts/prune_tags.py`, pinned by tests — change it there, not
in the YAML, and run it with no `--apply` first to see the plan:

```bash
python scripts/prune_tags.py            # prints what it would delete, touches nothing
```

Note what a tag *is* before widening that rule: it is cut only by the green-only `release` job,
so it is a durable record that CI passed at that commit — durable in a way GitHub's run history
is not, since runs expire. Pruning buys readability, not space (every tag points at a commit
reachable from `main`, so deleting one frees no objects).
