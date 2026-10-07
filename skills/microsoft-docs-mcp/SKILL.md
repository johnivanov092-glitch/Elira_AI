---
name: microsoft-docs-mcp
description: "MCP microsoft_docs: Microsoft Learn — Windows, PowerShell, .NET, SQL Server."
metadata:
  title: Microsoft Learn — документация
---

# Microsoft Learn (MCP `microsoft_docs`)

Применяй для вопросов по продуктам Microsoft: Windows и Windows Server,
PowerShell, .NET, SQL Server, Office, Azure — когда нужен первоисточник.

Обрати внимание: id сервера `microsoft_docs` (с подчёркиванием), навык —
`microsoft-docs-mcp`.

## Запуск

`mcp(action='start', server_id='microsoft_docs')` — у сервера три инструмента.

## Инструменты

- `microsoft_docs__microsoft_docs_search(query)` — найти страницы (короткие
  выдержки и ссылки).
- `microsoft_docs__microsoft_docs_fetch(url)` — прочитать страницу целиком.
- `microsoft_docs__microsoft_code_sample_search(query, language?)` — примеры
  кода (`language`: `powershell`, `csharp`, `sql`...).

## Порядок

search → fetch нужной страницы → ответ со ссылкой на страницу Learn.
Запросы по-английски находят точнее.

Конфиг: запись `microsoft_docs` в `data/mcp_servers.json` (HTTP, learn.microsoft.com).
