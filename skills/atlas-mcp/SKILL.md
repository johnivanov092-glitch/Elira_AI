---
name: atlas-mcp
description: "MCP atlas: карта кода Elira — где что лежит, паспорт файла, решения, проверки правок."
metadata:
  title: Atlas — карта кода Elira
---

# Atlas — карта кода Elira (MCP `atlas`)

Применяй, когда задача о собственном коде Elira (`D:\AIWork\Elira_AI`): понять,
как устроена подсистема, найти место правки, узнать, кто зависит от файла,
проверить правку. Для чужих проектов Atlas не нужен.

## Запуск

`mcp(action='start', server_id='atlas', query='atlas_overview atlas_find atlas_file')`
— `query` перечисляет нужные инструменты; другие позже: `mcp(action='tools',
server_id='atlas', query='atlas_check atlas_tests')`.

## Главные инструменты

- `atlas__atlas_overview()` — слои и подсистемы проекта, по строке на каждую.
- `atlas__atlas_find(query)` — где в коде нужное место (по смыслу, по-русски можно).
- `atlas__atlas_file(path, symbol?)` — паспорт файла: функции, связи, правила.
- `atlas__atlas_contract(name)` — подсистема: файлы, инварианты, кто зависит.
- `atlas__atlas_decisions(target?)` — решения и запреты пользователя по коду.
  Отклонённое решение не предлагай заново.
- `atlas__atlas_check(paths?)` — проверки изменённых файлов (инварианты,
  кодировка, дубли).
- `atlas__atlas_tests(action='run', paths?)` — тесты файлов, которые зависят от правки.

## Порядок

1. Найди место (`atlas_find` / `atlas_overview`), прочитай паспорт (`atlas_file`)
   и решения (`atlas_decisions`).
2. Сам код читай обычным `read_file` — паспорт не заменяет код.
3. После правки — `atlas_check`, затем `atlas_tests`.

## Ограничения

- `atlas_decision_add` записывает только решение, которое сказал пользователь,
  не твою идею.
- `atlas_try`, `atlas_golden`, `atlas_sandbox` запускают песочницу Elira — долго и
  нагружает модель; только по прямой просьбе.
- Пути в ответах Atlas — относительно корня Elira_AI.

Конфиг: запись `atlas` в `data/mcp_servers.json` (код Atlas — `D:\AIWork\Elira_Atlas`).
