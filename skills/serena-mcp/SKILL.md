---
name: serena-mcp
description: "MCP serena: код по символам — определения, вызовы, переименование, диагностика."
metadata:
  title: Serena — код по символам
---

# Serena (MCP `serena`)

Применяй в большом коде, когда grep не хватает: найти определение, все места
вызова, безопасно переименовать символ, заменить тело функции, получить
диагностику языкового сервера.

## Проект

Serena работает с проектом из `--project` в записи `serena` в
`data/mcp_servers.json` (сейчас — папка Elira_AI). Для другого проекта поменяй
этот аргумент: `mcp(action='add', server_id='serena', config={'args': [...]})`
с полным списком args, затем `mcp(action='restart', server_id='serena')`.
Первый запуск на проекте может идти минуту — языковой сервер индексирует код.

## Запуск

`mcp(action='start', server_id='serena', query='get_symbols_overview find_symbol find_referencing_symbols')`
— для правок: `replace_symbol_body insert_after_symbol rename_symbol`.

## Главные инструменты

- `serena__get_symbols_overview(relative_path)` — классы и функции файла.
- `serena__find_symbol(name_path_pattern, relative_path?, include_body?)` —
  найти символ (`MyClass/method`).
- `serena__find_referencing_symbols(name_path, relative_path)` — кто использует.
- `serena__get_diagnostics_for_file(relative_path)` — ошибки и предупреждения.
- `serena__replace_symbol_body(name_path, relative_path, body)`,
  `serena__insert_after_symbol(...)`, `serena__rename_symbol(name_path,
  relative_path, new_name)`, `serena__safe_delete_symbol(...)`.

## Порядок

Обзор файла → find_symbol с телом → find_referencing_symbols перед изменением
сигнатуры → правка → диагностика и тесты проекта.

## Ограничения

Пути — относительно проекта Serena. Простые правки делай обычным `edit_file`;
Serena — для поиска связей и массовых переименований. Память Serena
(`write_memory` и т.п.) не используй — у Elira своя память.

Конфиг: запись `serena` в `data/mcp_servers.json`.
