---
name: homeassistant-mcp
description: "MCP homeassistant: умный дом — состояния, включить/выключить, история датчиков."
metadata:
  title: Home Assistant — умный дом
---

# Home Assistant (MCP `homeassistant`)

Применяй для вопросов и команд про умный дом: свет, розетки, климат, датчики,
комнаты, автоматизации.

## Запуск

`mcp(action='start', server_id='homeassistant', query='search_entities get_entity entity_action')`
— для истории добавь `get_history get_statistics`, для сервисов `call_service`.

## Главные инструменты

- `homeassistant__search_entities_tool(query, limit?)` — найти устройство по
  имени (`query` — часть имени или id, например `kitchen`, `свет`).
- `homeassistant__get_entities_by_area(area, domain?)` — всё в комнате.
- `homeassistant__get_entity(entity_id, fields?)` — текущее состояние.
- `homeassistant__entity_action(entity_id, action, params?)` — `action`:
  `on` / `off` / `toggle`; `params` — например яркость.
- `homeassistant__call_service_tool(domain, service, data)` — любой сервис,
  например `climate.set_temperature`.
- `homeassistant__get_history(entity_id, hours)`,
  `homeassistant__get_statistics(entity_id, hours, period)` — прошлые значения.
- `homeassistant__list_automations()`, `homeassistant__get_error_log(...)`,
  `homeassistant__system_overview()`.

## Порядок

1. `entity_id` не угадывай: найди поиском или по комнате.
2. Выполни действие.
3. Проверь `get_entity` — состояние действительно изменилось; в ответе назови
   итоговое состояние.

## Ограничения

- `homeassistant__restart_ha` перезапускает весь дом — только по прямой просьбе.
- Замки, сигнализация, ворота — действие только по явной команде пользователя.

Конфиг: запись `homeassistant` в `data/mcp_servers.json` (HTTP, контейнер
hass-mcp на AI-сервере; сам Home Assistant — отдельно).
