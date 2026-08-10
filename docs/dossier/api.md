# api.py — грабли Vikunja REST и пагинация доски

> **Это ДОСЬЕ, а не правила.** Правило живёт в `CLAUDE.md → Архитектура → api.py` — там оно короткое и
> обязательное к исполнению. Здесь лежит доказательная база: измерения, построенные
> стенды, опровергнутые формулировки и номера карточек.
>
> **Читай перед тем, как менять этот код.** Этот репозиторий уже чинил гарды
> рассуждением вместо измерения — по несколько раундов подряд. Если правило кажется
> избыточным, ответ почти наверняка здесь.

- `src/vikunja_mcp/api.py` — REST client. **Vikunja gotchas are codified here:
  PUT = create, POST = FULL-REPLACE update** → every update is
  read-modify-write; kanban view updates must always send
  `bucket_configuration_mode="manual"` + `position` + `title` + `view_kind`
  or the board loses its columns; board fetch paginates per bucket
  (page size read from `/info`'s `max_items_per_page`; when the server never says, the
  size is UNKNOWN — **never guessed** — and the loop pages until no NEW task arrives in
  the required buckets; dedupe page overlap by bucket+task id, then GLOBALLY by
  task id keeping the last-seen bucket so a task moved mid-pagination lands once).
  There is deliberately no fallback constant: a guessed size (the old
  `_PAGE_SIZE_FALLBACK` = 50) silently TRUNCATED the board on an instance whose real
  limit was smaller, and a truncated board told `--gc` a live task was gone — so it
  reaped a live worktree (tracker #543). "Unknown" must stay unknown. That branch is
  also BOUNDED — `_UNKNOWN_PAGE_SIZE_MAX_PAGES` requests, and hitting it RAISES rather
  than returning a short board (tracker #548): a truncated board is indistinguishable
  from tasks that are genuinely gone, so a read that cannot finish must fail LOUDLY.
