# Структура агента Elira

Это корневая оперативная карта runtime агента. Канонические архитектурные
решения остаются в `docs/ARCHITECTURE.md`, а карта модулей — в
`docs/PROJECT_MAP.md`. Отдельного «каталога навыков» нет: навыки пользователя
являются представлением существующих capability-групп и инструментов, а не
вторым реестром исполнения.

## Поток выполнения

```text
Запрос пользователя
  -> детерминированный preflight (route_request_capabilities)
  -> core tools + выбранные capability groups
  -> OpenAI-compatible tool schemas
  -> ToolRegistry: единственный владелец dispatch
  -> ToolExecutor: permissions, result contract, audit
  -> встроенный / SSH / LSP / MCP handler
  -> явный ToolResult {ok: bool, text: str, ...}
  -> journal, evidence и ответ модели
```

## Источники истины

- Состав групп: `backend/app/application/code_agent/capabilities.py`.
- Описание аргументов модели: `backend/app/application/code_agent/tool_schemas.py`.
- Владение встроенными вызовами: `backend/app/application/code_agent/tools/_dispatch.py`.
- ToolSpec для inventory, аудита, лимитов и базового mutation-профиля:
  `backend/app/application/tool_registry/builtins.py` и provider registries.
- Единое исполнение: `backend/app/application/agent_kernel/executor.py`.
- Классификация конкретного вызова read/change:
  `backend/app/application/agent_kernel/impact_policy.py`.

## Инварианты

1. `capability_load` управляет только видимостью schemas в prompt. Он не выдаёт
   разрешение и не создаёт новый executor.
2. Каждый исполняемый tool имеет ровно одного dispatch owner. Новый registry,
   executor или параллельный DB-слой не добавляется.
3. Permission определяется для конкретного вызова. Смешанные инструменты
   классифицируются по аргументам: например, screenshot является чтением, а
   click/type/fill — изменением внешнего состояния.
4. Каждый handler возвращает явный boolean `ok`. Отсутствующий `ok` является
   ошибкой контракта; текст с префиксом `ERROR:` сам по себе не классифицируется,
   потому что может быть легитимным содержимым файла.
5. Journal и evidence получают статус только из структурированного результата,
   а не угадывают успех по тексту модели или инструмента.

## Экран «Навыки»

Если экран понадобится, backend строит его read-only представление автоматически
из capability registry, tool schemas и ToolSpec. Экран не хранит отдельные
trigger words, prompt-тексты или имена инструментов и не участвует в dispatch.
Так UI не может рассинхронизироваться с реально доступным runtime.

Минимальная карточка навыка может содержать:

- стабильный id capability-группы;
- локализованное название и описание группы;
- доступность группы и её инструментов;
- read/change профиль из ToolSpec, уточнённый для конкретного вызова через
  `impact_policy`;
- runtime/provider status, если он применим.

## Добавление возможности

1. Добавить handler или provider adapter к существующему dispatch seam.
2. Добавить schema и ToolSpec с тем же каноническим именем.
3. Включить имя в одну capability-группу либо в компактное core.
4. Вернуть явный `{ok, text}` во всех ветках handler.
5. Добавить контрактный тест `capability -> schema -> owner -> handler` и тест
   permission-классификации, если инструмент смешивает чтение и изменение.
