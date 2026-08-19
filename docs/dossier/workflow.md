# workflow.py — стадии, гейты, маркеры, push-ревью

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → workflow.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/workflow.py` — the product rules: stages, gates,
  assign-then-verify claim (with self-heal), review offering (verdict vs
  worklog timestamps), comment markers `[claim] [spec] [worklog] [needs-human]
  [blocked] [decompose] [review] [attach]` plus mutually-exclusive verdict
  labels `reviewed`/`review-failed` (push-review of EVERY task, not just bug fixes —
  tracker #117: `advance(to='review')` nudges `review_needed` + `review_kind`
  (`'bug'`|`'change'`, the reviewer's rubric) for any card WITHOUT the `epic` label,
  and resets a stale `reviewed`/`review-failed`). An epic container is the lone
  exception: its code lives in its children, each reviewed on its own advance.
  Behavior changes belong here, with a unit test per gate.
