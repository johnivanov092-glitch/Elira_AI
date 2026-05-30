# ELIRA_PROJECT_MAP

Актуальная карта проекта Elira AI на 2026-04-07.

Документ описывает только текущую рабочую архитектуру после staged cleanup.

## 1. Корневая структура

```text
Elira_AI/
├── backend/
│   └── app/
│      ├── api/routes/      FastAPI routes
│      ├── services/        бизнес-логика
│      ├── core/            config, llm, files, defaults
│      ├── schemas/         pydantic-схемы
│      └── state/           runtime state
├── frontend/
│   └── src/
│      ├── api/             frontend API client
│      ├── components/      UI-компоненты
│      └── pages/
├── src-tauri/             desktop shell
├── data/                  активный data-root
└── docs/                  проектная документация
```

## 2. Главные frontend-точки

- [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx)
  Основной контейнер приложения:
  обычный чат,
  advanced/multi-agent,
  настройки,
  пайплайны,
  dashboard,
  переход во вкладку `Код`.

- [frontend/src/components/IdeWorkspaceShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/IdeWorkspaceShell.jsx)
  Отдельный `code-agent` workspace:
  code-сессии,
  project tree,
  coding-chat,
  diff/file/git/verify/history,
  terminal,
  контекстные вложения.

- [frontend/src/components/chat/ChatViewport.jsx](/D:/AIWork/Elira_AI/frontend/src/components/chat/ChatViewport.jsx)
  Рендер сообщений и live-stream.

- [frontend/src/components/chat/useUnifiedStreams.js](/D:/AIWork/Elira_AI/frontend/src/components/chat/useUnifiedStreams.js)
  Единый stream-controller для обычного чата, advanced и code-agent.

- [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js)
  Главный frontend API слой.

## 3. Главные backend-модули

### Чат и обычный runtime

- [backend/app/api/routes/chat.py](/D:/AIWork/Elira_AI/backend/app/api/routes/chat.py)
  Обычный chat API, включая `stream`.

- [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py)
  Основная маршрутизация обычного запроса:
  planner,
  memory,
  RAG,
  web,
  reflection,
  identity/provenance guard,
  stream/non-stream path.

- [backend/app/services/chat_service.py](/D:/AIWork/Elira_AI/backend/app/services/chat_service.py)
  Прямой вызов LLM для chat/run_chat_stream.

- [backend/app/core/llm.py](/D:/AIWork/Elira_AI/backend/app/core/llm.py)
  Ollama adapter:
  text chat,
  stream,
  model capability detection,
  native vision payload,
  persona prompt composition.

### Advanced / workflow

- [backend/app/api/routes/advanced_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/advanced_routes.py)
  Project open/tree/read/search,
  advanced multi-agent stream.

- [backend/app/services/workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py)
  Built-in workflow engine:
  `default`,
  `reflection`,
  `orchestrated`,
  `full`.

### Code-agent

- [backend/app/api/routes/code_agent_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/code_agent_routes.py)
  `POST /api/code-agent/stream`
  и
  `POST /api/code-agent/cancel/{run_id}`.

- [backend/app/services/code_agent_service.py](/D:/AIWork/Elira_AI/backend/app/services/code_agent_service.py)
  Основной code-agent runner:
  lazy project analysis,
  file search/read,
  diff proposal,
  sandbox verify,
  refusal reasons,
  action log events.

### Память

- [backend/app/services/smart_memory.py](/D:/AIWork/Elira_AI/backend/app/services/smart_memory.py)
  Факты, предпочтения, profile-scoped память.

- [backend/app/services/rag_memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/rag_memory_service.py)
  Retrieval / knowledge memory,
  embeddings,
  cosine similarity + keyword fallback.

- [backend/app/services/agent_memory_runtime.py](/D:/AIWork/Elira_AI/backend/app/services/agent_memory_runtime.py)
  Runtime bridge после удаления `memory.db`.

## 4. Активные API поверхности

Рабочий surface локального UI:

- `/api/chat/*`
- `/api/code-agent/*`
- `/api/project-brain/*`
- `/api/advanced/project/*`
- `/api/advanced/multi-agent*`
- `/api/smart-memory/*`
- `/api/pipelines/*`
- `/api/git/*`
- `/api/terminal/*`
- `/api/library*`

Удалённые legacy surface:

- `/api/elira/execute*`
- `/api/elira/devtools*`
- `/api/elira/task*`
- `/api/elira/supervisor*`
- `/api/elira/phase19*`
- `/api/elira/phase20*`
- `/api/elira/phase21*`
- `/api/elira/stabilization*`

`/api/memory/*` пока оставлен как compatibility wrapper.

## 5. Базы данных в `data/`

Текущий активный data-root: [data](/D:/AIWork/Elira_AI/data)

Актуальные SQLite базы:

- [data/elira_state.db](/D:/AIWork/Elira_AI/data/elira_state.db)
  Чаты, сообщения, настройки, metadata.

- [data/smart_memory.db](/D:/AIWork/Elira_AI/data/smart_memory.db)
  Умная память:
  факты,
  предпочтения,
  profile-scoped memory.

- [data/rag_memory.db](/D:/AIWork/Elira_AI/data/rag_memory.db)
  RAG-память и knowledge retrieval.

- [data/library.db](/D:/AIWork/Elira_AI/data/library.db)
  Файловая библиотека и вложения.

- [data/response_cache.db](/D:/AIWork/Elira_AI/data/response_cache.db)
  Кеш LLM-ответов.

- [data/agent_registry.db](/D:/AIWork/Elira_AI/data/agent_registry.db)
  Реестр агентов.

- [data/tool_registry.db](/D:/AIWork/Elira_AI/data/tool_registry.db)
  Реестр инструментов.

- [data/agent_monitor.db](/D:/AIWork/Elira_AI/data/agent_monitor.db)
  Метрики, лимиты, защита runtime.

- [data/event_bus.db](/D:/AIWork/Elira_AI/data/event_bus.db)
  Runtime events.

- [data/autopipelines.db](/D:/AIWork/Elira_AI/data/autopipelines.db)
  Пайплайны и cron-run state.

- [data/task_planner.db](/D:/AIWork/Elira_AI/data/task_planner.db)
  Задачи и todo.

- [data/workflow_engine.db](/D:/AIWork/Elira_AI/data/workflow_engine.db)
  Workflow templates и runs.

- [data/run_history.db](/D:/AIWork/Elira_AI/data/run_history.db)
  История запусков и итогов.

- [data/integrations.db](/D:/AIWork/Elira_AI/data/integrations.db)
  Интеграции, включая Telegram.

Важно:

- `memory.db` удалена.
- `run_history.json` удалён; `run_history` теперь SQLite-only.
- `backend/data` не является активным runtime-root.

## 6. Память: что реально используется

### smart_memory

Источник:
- [data/smart_memory.db](/D:/AIWork/Elira_AI/data/smart_memory.db)

Назначение:
- факты пользователя
- предпочтения
- рабочие записи памяти по профилям

Где используется:
- обычный чат
- memory panel
- profile-aware memory API

### rag_memory

Источник:
- [data/rag_memory.db](/D:/AIWork/Elira_AI/data/rag_memory.db)

Назначение:
- retrieval context
- knowledge chunks
- embedding-based lookup

Где используется:
- обычный чат
- retrieval/knowledge контур

### legacy memory

- `memory.db` удалена.
- [backend/app/core/memory.py](/D:/AIWork/Elira_AI/backend/app/core/memory.py) удалён.
- Старый смешанный memory/telemetry/self-improve слой выведен из активной архитектуры.

## 7. Streaming-модель

### Обычный чат

- Пользовательский запрос идёт через [chat.py](/D:/AIWork/Elira_AI/backend/app/api/routes/chat.py)
- Затем в [agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py)
- Затем в [chat_service.py](/D:/AIWork/Elira_AI/backend/app/services/chat_service.py)
- UI рендерит поток через unified stream controller

### Advanced / multi-agent

- Используется workflow-based path, не свободная swarm-оркестрация
- Основной runtime: [workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py)
- Frontend получает единый стрим с phase/action rendering

### Code-agent

- Отдельный chat/workspace path
- Один основной coding-chat
- Diff собирается до `Apply`
- Verify идёт в sandbox
- Изменения в live-tree не применяются автоматически

## 8. Native Multimodal и Safe Code-Agent V2

- library хранит:
  `media_kind`,
  `mime_type`,
  `stored_path`,
  `sha256`,
  `preview`,
  `extracted_text`

- обычный чат и code-agent передают:
  `attachment_ids`

- [backend/app/core/llm.py](/D:/AIWork/Elira_AI/backend/app/core/llm.py)
  проверяет capabilities модели через Ollama
  и, если модель vision-совместима, передаёт изображения как native image payload

- если активная модель без vision:
  используется `default_vision_model`

- OCR не является основным multimodal-путём;
  он остаётся только как fallback и для preview/indexing

- code-agent использует:
  lazy whole-project analysis,
  action log,
  sandbox verify,
  refusal reasons,
  ручной `Apply`

## 9. Настройки, которые реально важны

В [data/elira_state.db](/D:/AIWork/Elira_AI/data/elira_state.db) и через settings UI/API используются:

- `default_model`
- `default_coding_model`
- `default_vision_model`
- `agent_profile`
- `ollama_context`
- `route_model_map`
- `orchestration_enabled`

Для code-session metadata:

- `workspace_kind`
- `project_root`

## 10. Документы в `docs/`

- [docs/ACTUAL_WORK.md](/D:/AIWork/Elira_AI/docs/ACTUAL_WORK.md)
  Единый журнал выполненных работ.

- [docs/ELIRA_PROJECT_MAP.md](/D:/AIWork/Elira_AI/docs/ELIRA_PROJECT_MAP.md)
  Текущая карта проекта.
