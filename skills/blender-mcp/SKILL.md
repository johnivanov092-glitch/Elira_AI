---
name: blender-mcp
description: "MCP blender: 3D-сцена в открытом Blender — объекты, материалы, скриншот."
metadata:
  title: Blender — 3D-сцена
---

# Blender (MCP `blender`)

Применяй для 3D-моделирования и сцен в Blender. Нужен открытый Blender с
подключённым аддоном BlenderMCP.

## Запуск

`mcp(action='start', server_id='blender', query='get_blender_status get_scene_info add_primitive modify_object set_material get_viewport_screenshot')`

## Главные инструменты

- `blender__get_blender_status()` — первым: видит ли сервер Blender. Не видит —
  попроси пользователя открыть Blender и подключить аддон.
- `blender__get_scene_info()`, `blender__get_object_info(object_name)` — что в сцене.
- `blender__add_primitive(primitive_type, name, location, rotation, scale)`,
  `blender__modify_object(name, location?, rotation?, scale?, visible?)`,
  `blender__duplicate_object(name)`, `blender__delete_object(name)`.
- `blender__set_material(object_name, color, metallic?, roughness?)`.
- `blender__batch_edit(operations)` — много правок за один вызов.
- `blender__execute_blender_code(code)` — Python в Blender, когда готового
  инструмента нет; небольшими шагами.
- `blender__get_viewport_screenshot(max_size?)` — посмотреть результат.
- Ассеты: `search_polyhaven_assets` / `download_polyhaven_asset`,
  `search_sketchfab_models` / `download_sketchfab_model`.

## Порядок

Перед изменением runtime сам делает резервную копию открытой сцены.
Простую правку — отдельным инструментом; циклы, процедурную расстановку и массовое
выравнивание — сразу одним `execute_blender_code`, а не десятками `batch_edit`.


Осмотри сцену → измени → скриншот вьюпорта и проверь глазами → поправь → когда
готово, сохрани сцену (через `execute_blender_code`: `bpy.ops.wm.save_mainfile()`)
только если пользователь просил сохранить.

## Ограничения

- Генерация моделей Hyper3D / Hunyuan3D — облачный ИИ; у Elira всё локально, не
  используй.
- Удаление объектов и перезапись файла сцены — только по просьбе.

Конфиг: запись `blender` в `data/mcp_servers.json`.
