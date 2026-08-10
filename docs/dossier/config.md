# config.py — 4-слойный конфиг, wip_limit, независимость ревью

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → config.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/config.py` — 4-layer config: env (`VIKUNJA_URL/TOKEN/PROJECT_ID`)
  > repo-local `.vikunja-mcp.env` (same dir as the toml, found by the same walk-up,
  gitignored) > repo `.vikunja-mcp.toml` (walk-up from cwd) > `~/.config/vikunja-mcp/env`.
  Token is NEVER read from the repo toml (so it can't be committed and used); optional
  `VIKUNJA_NOTIFY_WEBHOOK` (`notify.py` — best-effort Slack-shaped ping when `call_human`
  parks a card in Your Call) is a secret of the same class: env layers only, never the toml.
  Two parallel-drain keys sit on opposite sides of that split: `wip_limit` (how many
  Design/Build tasks one token may CLAIM into at once — not how many it may HOLD, and what the
  difference costs is spelled out further down this same bullet, at "a gate on ONE transition";
  generalises `enforce_single_wip`, which is
  exactly 1) is committed TEAM POLICY — repo toml ONLY, never env. **Unset means
  `DEFAULT_WIP_LIMIT` = 3, not "no gate"** (human decision, tracker #524 — the gate is always
  on, so every project drains 3-wide without a toml edit); precedence is explicit `wip_limit` →
  else 1 when `enforce_single_wip = true` → else 3, resolved in `workflow._effective_wip_limit`
  (which returns `int`, never `None`) while `Config.wip_limit is None` keeps meaning only "the
  key is absent". `wip_limit = 0` is a `ConfigError`, NOT the unbounded spelling: "no limit" is
  deliberately not expressible any more. **It is a gate on ONE transition (`claim`), not an
  invariant on the active count** (tracker #529): a card re-enters Build without passing it —
  `review_task(verdict='needs_work')` bounces it Review→Build, a human moves it out of Your Call
  or hand-places an assigned card, or the toml lowers the number while work is in flight — so
  `wip.active` legitimately EXCEEDS `wip.limit` (4/3 observed live), and that is correct, because
  rework must be receivable at the limit. `next_task`'s `free` is `max(0, limit - active)`, so the
  overshoot is invisible there and readable only from `active`/`limit`; `claim` keeps refusing and
  reports the true count. Making it impossible, or gating the second path, is deliberately NOT
  done — both would strand reviewed work. `worktree_root` /
  `VIKUNJA_WORKTREE_ROOT` (where per-task worktrees materialise, default a `<repo>.worktrees`
  sibling) is MACHINE-local, so unlike `wip_limit` the env layers DO win over the toml.
  **`require_review_independence` is a THIRD key on `wip_limit`'s side of that split — repo toml
  ONLY, never env, default FALSE** (tracker #37, the human's own answer picking a flag over an
  unconditional gate). On, `review_task` refuses a verdict from anyone in the card's own
  assignees; off, it does not resolve `me()` at all, so the behaviour and the request trail are
  what they were before the gate existed. **The default is the feature, not a soft rollout, and
  reading it as a hole gets the setup backwards.** In a SOLO setup one scoped token is the whole
  fleet — the orchestrator and every per-task agent it dispatches, reviewers included,
  authenticate as ONE assignee — so the ABSENCE of an authorship check is the CONDITION OF
  OPERATION; independence there is carried by the agents' separated CONTEXTS (push model, a
  sibling reviewer with a fresh context), which nothing server-side can observe. Turn it on
  without a second identity and NOBODY can review anything, here or at any consumer on `stable`,
  which is why this repo's own toml deliberately does NOT set it and why the refusal names the
  way back out. What it closes is the MULTI-IDENTITY hole, measured by BEHAVIOUR rather than
  read off the call graph: before the gate, a verdict from the card's own assignee was ACCEPTED
  on both verdicts, `approve` landing the `reviewed` label a human reads for Done — because
  `review_task` is the ONE mutating tool that never calls `_require_mine`, its `_assignee_ids`
  read being #705's ownerless ROUTING and never an authorship check. So "you don't review your
  own work" rested ENTIRELY on `next_task`'s OFFER filter, and an offer filter is a hint, not a
  gate: it is not consulted by a direct call, and #885's kanban blackout DELETES it outright.
  That last shape is why the gate reads assignees through `_kanban_assignees_may_be_stale`
  rather than raw — judged off the board copy it would find nobody and pass precisely the card
  whose other protection is already gone. A genuinely ownerless card still passes (no author to
  exclude) and its `needs_work` still routes to Queue. Wired in `server._build_workflow` only,
  NOT in `claimable_cmd`'s Workflow: that one runs `next_task` and nothing else, so the flag
  could never be consulted there and passing it would be dead wiring on the one path that must
  stay read-only and cheap.
