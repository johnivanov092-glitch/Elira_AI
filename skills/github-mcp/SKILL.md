---
name: github-mcp
description: "MCP github: удалённый GitHub — поиск, файлы репозитория, issues, pull requests."
metadata:
  title: GitHub — репозитории, issues, PR
---

# GitHub (MCP `github`)

Применяй для удалённого GitHub: найти репозиторий, прочитать файл или README
без клонирования, посмотреть issues/PR/коммиты, создать issue или PR.
Локальный репозиторий — обычный `git` через `run_bash`, MCP не нужен.

## Запуск

`mcp(action='start', server_id='github', query='search_repositories get_file_contents list_issues')`
— для PR: `list_pull_requests get_pull_request get_pull_request_files`.

## Главные инструменты

- `github__search_repositories(query)`, `github__search_code(q)`,
  `github__search_issues(q)` — поиск (синтаксис GitHub search: `repo:owner/name`,
  `language:python`).
- `github__get_file_contents(owner, repo, path, branch?)` — файл или папка.
- `github__list_commits(owner, repo, sha?)`.
- `github__list_issues(owner, repo, state?)`, `github__get_issue(...)`.
- `github__list_pull_requests(...)`, `github__get_pull_request(...)`,
  `github__get_pull_request_files(...)`, `github__get_pull_request_status(...)`.
- Запись: `create_issue`, `add_issue_comment`, `create_branch`,
  `create_or_update_file`, `push_files`, `create_pull_request`,
  `merge_pull_request`.

## Токен

Сейчас сервер может работать без токена — тогда только публичное чтение и
строгий лимит запросов. Для записи и приватных репозиториев нужен токен:
`mcp(action='add', server_id='github', config={'env_secret_refs':
{'GITHUB_PERSONAL_ACCESS_TOKEN': '<ссылка sref_…>'}})` — секрет вводится
карточкой, в конфиг попадает только ссылка; затем `mcp(action='restart', ...)`.

## Ограничения

- Создание, изменение, слияние на GitHub — только по прямой просьбе: это видно
  другим людям.
- Ответ про репозиторий — по прочитанным файлам, не по памяти.

Конфиг: запись `github` в `data/mcp_servers.json`.
