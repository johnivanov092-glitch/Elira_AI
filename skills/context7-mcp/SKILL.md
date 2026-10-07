---
name: context7-mcp
description: "MCP context7: актуальная документация библиотек и фреймворков."
metadata:
  title: Context7 — документация библиотек
---

# Context7 (MCP `context7`)

Применяй, когда пишешь или чинишь код с внешней библиотекой и нужен актуальный
API: сигнатуры, параметры, примеры для нужной версии.

## Запуск

`mcp(action='start', server_id='context7')` — у сервера два инструмента.

## Инструменты

1. `context7__resolve-library-id(libraryName, query)` — найти id библиотеки
   (`libraryName`: `fastapi`; `query`: что нужно узнать). Ответ — id вида
   `/tiangolo/fastapi`.
2. `context7__query-docs(libraryId, query)` — документация по вопросу
   (`query` по-английски точнее: `dependency injection with yield`).

## Порядок

resolve-library-id → query-docs с конкретным вопросом → применяй найденное в
коде → проверь запуском. Версия важна: если в проекте старая версия, сверь
`requirements.txt` / `package.json` и уточни запрос.

## Ограничения

Документация Microsoft/.NET/Azure — навык `microsoft-docs-mcp`.

Конфиг: запись `context7` в `data/mcp_servers.json` (npx `@upstash/context7-mcp`).
