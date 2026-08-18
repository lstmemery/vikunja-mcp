# Security

## Reporting a vulnerability

Please **don't open a public issue** for a security problem. Use GitHub's private vulnerability
reporting on this repository (Security → Report a vulnerability), which opens a private thread
with the maintainer.

Include what you'd want to receive: what you did, what happened, what you expected, and the
version or commit you were on. A proof of concept helps but isn't required to file.

## The threat model, briefly

**The pipeline gates are not a security boundary.** `advance` refusing to reach Done, `claim`
refusing past the WIP limit, `review_task` refusing a self-verdict — these are guardrails that
keep an autonomous agent inside a process. They are enforced client-side, in this server, on
behalf of the agent using it. Anyone holding the token can bypass every one of them by calling
the Vikunja REST API directly.

**The real boundary is the scoped API token.** Vikunja mints tokens with per-route permission
groups, and that is what actually limits what an agent can do to your instance. Scope it to the
project you mean and nothing else. Two groups are non-obvious and required, or every tool 401s:

- `other:user` — `GET /api/v1/user`, without which the server cannot identify itself and
  claim/advance/review all break;
- `projects:views_buckets` — reading the kanban columns, without which the board cannot be read
  at all.

Treat a leaked agent token as you would any write credential to that project: revoke it in
Vikunja, mint a new one, update the env file.

## Secrets and this repo's layout

Two config values are secrets and are read from the environment layers **only** — never from the
committed `.vikunja-mcp.toml`:

- `VIKUNJA_TOKEN`
- `VIKUNJA_NOTIFY_WEBHOOK` — whoever holds an incoming-webhook URL can post into your channel,
  so it is a credential of the same class.

Put them in `.vikunja-mcp.env` beside the toml (**gitignore it**) or in
`~/.config/vikunja-mcp/env` with `chmod 600`. The toml is designed to be committable precisely
because a token cannot be read from it even if someone puts one there.

## Browser session state

If you drive a browser from an agent in a checkout of this repo, note that a Playwright
*storage state* file is live session cookies for whatever that browser was logged into — a
credential, not an artifact. This repo sets `PLAYWRIGHT_MCP_STORAGE_STATE` nowhere, ignores
files of that shape by name, and additionally fails a unit test on any file of storage-state
*shape* that git could publish under any name, reading both the index blob and the worktree
bytes. That last part is a gate, not a lock: it turns the pre-push test run and CI red, and it
cannot stop a `git commit` that skips them.

Write browser artifacts under `.playwright-mcp/` — the one directory ignored wholesale,
independent of filename and format.

## Scope

In scope: anything in this repository — the MCP server, the CLI commands, the packaged skill,
the config resolution, the worktree tooling.

Out of scope: vulnerabilities in [Vikunja](https://vikunja.io) itself (report those upstream),
and the general fact that an agent with a write token can write. If you believe a *gate* can be
bypassed in a way that surprises an operator who read the docs, that is worth reporting even
though the gates aren't a security boundary — a misleading guardrail is a real defect.
