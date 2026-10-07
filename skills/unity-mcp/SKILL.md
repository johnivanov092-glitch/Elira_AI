---
name: unity-mcp
description: "MCP unity: открытый Unity Editor — сцена, компоненты, C#-скрипты, консоль, тесты."
metadata:
  title: Unity — редактор игры
---

# Unity (MCP `unity`)

Применяй для работы в проекте Unity. Нужен открытый Unity Editor с пакетом
MCP for Unity (окно MCP for Unity → сервер подключён).

## Запуск

У сервера ~47 инструментов, поэтому всегда передавай `query` с нужными:
`mcp(action='start', server_id='unity', query='manage_editor read_console manage_scene find_gameobjects')`
— для скриптов: `create_script script_apply_edits validate_script refresh_unity`;
для тестов: `run_tests get_test_job`. Позже — `mcp(action='tools', ...)`.

## Главные инструменты

- `unity__manage_editor(action=...)` — состояние редактора, play/stop.
- `unity__read_console(...)` — ошибки и предупреждения; смотри после каждой
  правки скриптов.
- `unity__manage_scene(action='get_hierarchy' ...)`,
  `unity__find_gameobjects(...)` — что в сцене.
- `unity__manage_gameobject(action=create|modify|delete ...)`,
  `unity__manage_components(action=add|remove|set_property ...)`.
- Скрипты: `unity__create_script`, `unity__script_apply_edits` (структурные
  правки методов), `unity__apply_text_edits` (точечные), `unity__validate_script`.
- `unity__refresh_unity(...)` — обновить ассеты и перекомпилировать.
- `unity__run_tests(...)` → `unity__get_test_job(job_id)`.
- `unity__batch_execute(...)` — много команд за один вызов.
- `unity__unity_docs(...)`, `unity__unity_reflect(...)` — проверить, что класс или
  метод API действительно существует.

## Порядок

Осмотри (иерархия, консоль) → измени → `refresh_unity` → `read_console` без
ошибок компиляции → проверь результат (иерархия, тесты, play) → сохрани сцену,
если просили.

## Ограничения

- `generate_image` / `generate_audio` / `generate_model` — облачная генерация
  (fal.ai и др.); у Elira всё локально, не используй.
- `execute_code` и удаление ассетов — только когда другого способа нет или по просьбе.

Конфиг: запись `unity` в `data/mcp_servers.json`.
