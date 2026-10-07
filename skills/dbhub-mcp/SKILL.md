---
name: dbhub-mcp
description: "MCP dbhub: SQL-запросы к базам SQL Server и PostgreSQL."
metadata:
  title: DBHub — базы данных SQL
---

# DBHub (MCP `dbhub`)

Применяй, когда нужно посмотреть структуру базы или данные: какие таблицы,
сколько записей, выборка по условию.

## Базы

Список баз — в файле из аргумента `--config` записи `dbhub` в
`data/mcp_servers.json` (сейчас `data/dbhub.toml`): у каждой базы свой `id`
(например `mssql_main` — SQL Server, `db2` — PostgreSQL). Строки подключения
содержат пароли — не выводи их в ответ.

## Запуск

`mcp(action='start', server_id='dbhub')` — в ответе `available_tool_names`:
у каждой базы свои инструменты (поиск объектов и выполнение SQL с `id` базы в
имени). Нужные дальше — `mcp(action='tools', server_id='dbhub', query='search_objects execute_sql')`.

## Порядок

1. Сначала структура: поиск объектов (схемы, таблицы, столбцы) нужной базы.
2. Затем `SELECT` с ограничением (`TOP 50` в SQL Server, `LIMIT 50` в
   PostgreSQL) и нужными столбцами, не `SELECT *` по большой таблице.
3. Числа в ответе — только из результата запроса; итоги считай SQL
   (`COUNT`, `SUM`, `GROUP BY`), а не в уме.

## Ограничения

- `INSERT`/`UPDATE`/`DELETE`/`ALTER`/`DROP` — только по прямой просьбе
  пользователя и после показа точного запроса.
- Диалект зависит от базы: SQL Server — `TOP`, `GETDATE()`; PostgreSQL — `LIMIT`,
  `now()`.

Конфиг: запись `dbhub` в `data/mcp_servers.json` (npx `@bytebase/dbhub`).
