---
name: playwright-mcp
description: "MCP playwright: браузер по шагам — клики, формы, проверка веб-приложения."
metadata:
  title: Playwright — браузер по шагам
---

# Playwright (MCP `playwright`)

Применяй, когда сайт нужно пройти по шагам: клики, формы, вкладки, проверка
своего веб-приложения, ошибки в консоли и запросы. Просто прочитать страницу —
`web_fetch`; одна форма — инструмент `browser` группы web.

## Запуск

`mcp(action='start', server_id='playwright', query='browser_navigate browser_snapshot browser_click browser_type browser_fill_form')`
— для отладки добавь `browser_console_messages browser_network_requests browser_take_screenshot`.

## Главные инструменты

- `playwright__browser_navigate(url)`.
- `playwright__browser_snapshot()` — дерево страницы с метками элементов
  (`ref`); основа для действий, лучше скриншота.
- `playwright__browser_click(target, element?)` — `target` = метка из snapshot.
- `playwright__browser_type(target, text, submit?)`,
  `playwright__browser_fill_form(fields)`, `playwright__browser_select_option(...)`.
- `playwright__browser_wait_for(text? | time?)`.
- `playwright__browser_console_messages(level)`,
  `playwright__browser_network_requests(static)`.
- `playwright__browser_take_screenshot(...)`, `playwright__browser_tabs(action)`,
  `playwright__browser_close()`.

## Порядок

navigate → snapshot → действие по метке → снова snapshot и проверка результата
→ … → `browser_close` в конце. Метки меняются после каждого изменения
страницы — бери их из свежего snapshot.

## Ограничения

- Пароли и платёжные данные сам не вводи — попроси пользователя.
- Отправка форм, покупки, публикации — только по прямой просьбе.
- `browser_run_code_unsafe` — крайний случай.

Конфиг: запись `playwright` в `data/mcp_servers.json` (npx `@playwright/mcp`, headless).
