# Elira AI — Карта проекта (конструктор)

«Что куда идёт»: дерево репозитория, потоки данных, связь роутеров с логикой,
фронтенд и **все зависимости** с пояснением «зачем». Техническое описание
подсистем — в [`ARCHITECTURE.md`](ARCHITECTURE.md).

---

## 1. Дерево репозитория (верхний уровень)

```text
Elira_AI/
├── backend/                FastAPI-бэкенд (запуск из backend/.venv)
│   ├── app/                исходники (слои api/application/domain/infrastructure/core)
│   ├── tests/              pytest-набор
│   ├── requirements.txt           core-зависимости
│   └── requirements-optional.txt  тяжёлые/ленивые возможности
├── frontend/               React + Vite + TypeScript
│   ├── src/                исходники UI (api/components/styles)
│   └── dist/               собранный бандл (gitignored) — его отдаёт Tauri
├── src-tauri/              десктоп-оболочка (Rust/Tauri): Cargo.toml, build.rs, main.rs, tauri.conf.json
├── data/                   активный runtime-root (SQLite-БД, uploads, generated, ключ, плагины)
├── scripts/                вспомогательные скрипты (напр. smoke_contract_check.py)
├── docs/                   документация (индекс — docs/README.md; отложенные работы — POST_SERVER_BACKLOG.md; notes/ — датированные заметки-ревью)
├── package.json            корневой — оркестрация Tauri CLI
├── Elira.bat / Elira_Mobile.bat / run_tauri_dev.bat / build_exe.bat / kill_elira.bat   лаунчеры
└── README.md               витрина GitHub: питч, установка, порядок запуска, smoke-проверки
```

---

## 2. Поток запроса (как всё соединяется)

```text
        ┌─────────────────────────────────────────────────────────┐
        │  Tauri-окно (Rust, src-tauri)  →  грузит frontend/dist     │
        └─────────────────────────────────────────────────────────┘
                                  │  HTTP/SSE
                                  ▼
   React UI (frontend/src)  ──►  frontend/src/api/*  (тонкий клиент, client.ts)
                                  │   POST /api/...
                                  ▼
   FastAPI  (backend/app/main.py → api/routes/registry.py → ALL_ROUTERS)
                                  │   роутер делегирует вниз (логики не содержит)
                                  ▼
   application/*  (сценарии: chat, code_agent, memory, web_search, autopipeline, …)
                                  │
                ┌─────────────────┴─────────────────┐
                ▼                                     ▼
   domain/*  (чистая логика:            infrastructure/* + core/*  (адаптеры I/O:
   agents router/planner/                llm→Ollama, db→SQLite, search→web,
   orchestrator/reflection,              browser, git, shell, storage)
   tools, workflows)
                                  │
                                  ▼
            Ollama (LLM)  ·  SQLite (data/*.db)  ·  Web (Tavily/DDG/Wikipedia)
```

Два контура стрима независимы: `/api/chat/stream` (обычный чат) и
`/api/code-agent/stream` (код-агент) — подробности в
[`ARCHITECTURE.md` §5](ARCHITECTURE.md#5-два-окружения-главная-развилка).

---

## 3. Бэкенд: дерево `backend/app/` по слоям

```text
app/
├── main.py              старт: FastAPI, CORS, монтаж ALL_ROUTERS, init_db + seed_*
├── api/routes/          38 модулей-роутеров (HTTP-поверхность); registry.py = ALL_ROUTERS
├── application/         ~55 фич-модулей (use-cases). Ключевые:
│   ├── chat/                маршрутизация чата, planner, context, stream, finalization
│   ├── code_agent/          agent_loop, python_lab, инструменты код-агента
│   ├── smart_memory/ rag_memory/ memory/ elira_memory/    память и настройки
│   ├── web_search/ web/ web_query_planner/                веб-поиск и планирование запросов
│   ├── tool_registry/ tool_providers/ tools/ tools_exec/  инструменты и реестр
│   ├── agent_registry/ event_bus/ workflow_engine/ workflows/ monitoring/   Agent OS
│   ├── autopipeline/ task_planner/                        автоматизация (cron + задачи)
│   ├── media/ pdf/ file_extract/ file_ops/ library/       медиа, документы, файлы, вложения
│   ├── project_brain/ project_brain_engine/ projects/ project_patch/ advanced/   проекты
│   ├── persona/ planning/ policy/ skills/ skills_extra/ spotlight/ telegram/ terminal/ git/
│   └── integrations/ dashboard/ run_history/ response_cache/ ollama_models/ runtime/ elira_patch/
├── domain/              чистая логика без I/O
│   ├── agents/              router · planner · orchestrator · reflection
│   ├── tools/  workflows/  runtime/
├── infrastructure/      адаптеры к внешнему миру
│   ├── llm/                 Ollama
│   ├── db/ (+repositories/) общий connection-провайдер SQLite
│   ├── search/             веб-поиск (Tavily/DDG/Wikipedia, multisearch)
│   ├── browser/ git/ shell/ storage/ files/ integrations/ plugins/ cache/ vcs/ runtime/
├── core/                config · llm · web/web_engines/web_runtime · files · library
│                        · persona_defaults · temporal_intent · data_files
├── schemas/  state/  utils/   pydantic-схемы · runtime-состояние · утилиты (UTF-8 normalize)
```

---

## 4. Карта: роутер → что обслуживает

35 роутеров смонтированы в `ALL_ROUTERS`. Основные группы:

| API-поверхность | Роутер(ы) | Слой логики |
|-----------------|-----------|-------------|
| `/api/chat/*` (+ `stream`) | `chat` | `application/chat` + `domain/agents` |
| `/api/code-agent/*` | `code_agent_routes` | `application/code_agent` |
| `/api/agent-os/*` (agents/tools/events/workflows/monitor) | `agent_registry_routes`, `tool_registry_routes`, `event_bus_routes`, `workflow_routes`, `agent_monitor_routes` | соответствующие `application/*` (Agent OS) |
| `/api/smart-memory/*`, `/api/memory/*` | `smart_memory_routes`, `memory` | `application/smart_memory` · `rag_memory` |
| `/api/pipelines/*`, `/api/tasks/*` | `autopipeline_routes`, `task_planner_routes` | `application/autopipeline` · `task_planner` |
| `/api/advanced/*`, `/api/project-brain/*` | `advanced_routes`, `project_brain` | `application/advanced` · `project_brain*` · `projects` |
| `/api/web-search/*` | `web_search_routes`, `web_routes` | `application/web_search` · `infrastructure/search` |
| `/api/git/*`, `/api/terminal/*`, `/api/files*`, `/api/file-ops/*` | `git_routes`, `terminal`, `files`, `file_ops` | `application/git` · `terminal` · `file_ops` |
| `/api/library*`, `/api/pdf/*`, `/api/image/*` | `library`, `library_sqlite`, `pdf_routes`, `image_routes` | `application/library` · `pdf` · `media` |
| `/api/skills*`, `/api/tools-exec/*`, `/api/spotlight/*` | `skills_routes`, `skills_extra_routes`, `tools_exec`, `spotlight_routes` | `application/skills*` · `tools_exec` · `spotlight` |
| `/api/dashboard/*`, `/api/persona/*`, `/api/profiles/*`, `/api/models/*`, `/api/runtime/*` | `dashboard_routes`, `persona`, `profiles`, `models`, `runtime` | соответствующие `application/*` · `core` |
| `/api/integrations/*`, `/api/telegram/*` | `telegram_routes` | `application/integrations` · `telegram` |
| settings/state, debug, patch | `elira_state`, `debug`, `elira_patch` | `application/elira_memory` · `elira_patch` |

---

## 5. Фронтенд: что обслуживает что

```text
frontend/src/
├── main.tsx · App.tsx                вход
├── components/
│   ├── EliraChatShell.tsx            ОБЫЧНЫЙ ЧАТ: диалоги, настройки, пайплайны, dashboard, задачи
│   ├── CodeWorkspaceShell.tsx        КОД-АГЕНТ: оболочка-рельса (Codex-стиль)
│   ├── CodeAgentChatShell.tsx        КОД-АГЕНТ: чат (LIVE_RUNS стрим)
│   ├── IdeWorkspaceShell.tsx         КОД-АГЕНТ: дерево/файлы/diff/git/verify
│   ├── ProjectPanel.tsx              проекты + переключатель + пикер папки
│   ├── MemoryPanel · StatusPanels · TerminalPanel · SpotlightOverlay
│   ├── SshConfigDialog · McpConfigDialog   настройка провайдеров код-агента
│   └── MarkdownRenderer · ArtifactPanel · PlannerKeywordsPanel · ToastHost
├── api/   тонкий клиент по доменам (client.ts — база):
│   ├── chat.ts · chats.ts            → /api/chat/*      (обычный чат; normalizeChat стрингифицирует id)
│   ├── codeAgent.ts · ide.ts         → /api/code-agent/*, advanced project
│   ├── advanced.ts · project.ts      → /api/advanced/*
│   ├── smartMemory.ts                → /api/smart-memory/*
│   ├── pipelines.ts · tasks.ts       → /api/pipelines/*, /api/tasks/*
│   ├── git.ts · terminal.ts · fileOps.ts · library.ts
│   ├── tools.ts · plugins.ts · dashboard.ts · system.ts · agent.ts
│   ├── integrations.ts · telegram.ts · spotlight.ts · patch.ts · plannerKeywords.ts
│   └── apiUtils.ts
├── streamRegistry.ts                 переживание стримов между переключениями
├── pickFolder.ts                     общий Tauri-пикер папок
├── chatConstants.ts                  в т.ч. CHAT_WORKING_SKILLS
├── chatUtils.ts · elira_ru_labels.ts · vite-env.d.ts
└── styles.css · styles/markdown.css
```

---

## 6. Зависимости

### Backend — core (`backend/requirements.txt`)

| Пакет | Зачем |
|-------|-------|
| `python-dotenv` | загрузка `.env` / `.env.local` (ключи) |
| `fastapi`, `uvicorn[standard]` | веб-фреймворк и ASGI-сервер |
| `pydantic` | схемы/валидация |
| `ollama` | клиент локального LLM-движка |
| `httpx` | async HTTP |
| `python-multipart` | загрузка файлов (multipart) |
| `numpy` | векторная математика RAG (эмбеддинги как буферы) |
| `ddgs` | поиск DuckDuckGo |
| `requests`, `beautifulsoup4` | HTTP + парсинг HTML для веб-контекста |
| `pypdf` | чтение PDF |
| `python-docx` | генерация/чтение .docx |
| `cryptography` | шифрование (+ `data/elira_secret.key`) |
| `watchdog` | реалтайм авто-индекс проекта код-агента |

### Backend — optional (`requirements-optional.txt`, ленивая загрузка)
Тяжёлые возможности: генерация изображений, OCR (Tesseract), таблицы Excel/PDF,
pandas, скриншоты. Грузятся при первом использовании, не нужны для базового
чата + инструментов + поиска.

### Frontend (`frontend/package.json`)

| Пакет | Зачем |
|-------|-------|
| `react`, `react-dom` | UI |
| `@tauri-apps/api` | мост к нативной оболочке (диалоги, ФС) |
| `lucide-react` | иконки |
| `typescript`, `vite`, `@vitejs/plugin-react` (dev) | сборка/типизация |

### Десктоп (`package.json` корня + `src-tauri/`)
`@tauri-apps/cli` (dev) оркеструет сборку; `src-tauri/Cargo.toml` — Rust-зависимости
оболочки; `tauri.conf.json` — allowlist (`dialog.open`, `fileDropEnabled`) и
конфиг окна (вкомпилированы в бинарь → правки требуют полной пересборки Tauri).

---

## 7. Файлы данных (`data/`)

Активный runtime-root. SQLite-БД создаются сервисами через общий провайдер
`app/infrastructure/db/connection.py`.

| Файл/папка | Что | В git? |
|------------|-----|--------|
| `elira_state.db` | чаты, сообщения, настройки | нет |
| `smart_memory.db` · `rag_memory.db` | память / RAG | нет |
| `library.db` · `response_cache.db` | вложения / кеш ответов | нет |
| `code_agent_sessions.db` | сессии код-агента | нет |
| `agent_registry.db` · `tool_registry.db` · `event_bus.db` · `workflow_engine.db` · `agent_monitor.db` | Agent OS | нет |
| `autopipelines.db` · `task_planner.db` | автопайплайны / задачи | нет |
| `run_history.db` · `integrations.db` · `projects.db` | история / интеграции / проекты | нет |
| `generated/` · `uploads/` · `system/` | сгенерированные / загруженные / runtime-состояние | нет |
| `elira_secret.key` | ключ шифрования | **да** (осознанно) |
| `plugins/` · `plugins_config.json` | примеры плагинов и конфиг | **да** |

Секреты (Tavily и пр.) — только `backend/.env.local` (gitignored). Логика
`.gitignore`: корневой `data/` зеркалит `backend/data/` — runtime/личное вне git,
ключ и плагины остаются.

---

## 8. Где смотреть что

- Установка/запуск → корневой `README.md`.
- Как устроено и почему → [`ARCHITECTURE.md`](ARCHITECTURE.md).
- Что куда идёт и зависимости → этот файл.
- Отложенные работы (gate: миграция инференса на AI-server) →
  [`POST_SERVER_BACKLOG.md`](POST_SERVER_BACKLOG.md).
- Находки ревью agent-loop и детальные решения →
  [`AGENT_LOOP_FIX_PROPOSALS.md`](AGENT_LOOP_FIX_PROPOSALS.md) ·
  [`notes/2026-06-10_chat-review-agent-loop.md`](notes/2026-06-10_chat-review-agent-loop.md).

---

## Agent Runtime Map (P9-P12)

Status: current for `main` at `db6ae7a`.

### Canonical Runtime Owners

| Area | Canonical files |
|------|-----------------|
| Tool execution | `backend/app/application/agent_kernel/executor.py` |
| Deferred tool activation | `backend/app/application/agent_kernel/deferred_tools.py` |
| Tool catalog and ToolSpec policy | `backend/app/application/tool_registry/` |
| Tool dispatch providers | `backend/app/application/tool_providers/` |
| Policy preflight | `backend/app/application/agent_registry/sandbox.py` |
| Approvals, limits, metrics | `backend/app/application/monitoring/` |
| Audit events | `backend/app/application/event_bus/` |
| Chat routing and execution | `backend/app/application/chat/service.py`, `entrypoint_sync.py`, `entrypoint_stream.py` |
| Code-agent loop and meta-tools | `backend/app/application/code_agent/agent_loop.py`, `tools.py` |
| Context compaction | `backend/app/application/context/compaction.py` |
| Task planner runtime | `backend/app/application/task_planner/runtime.py` |
| MCP stdio client/provider | `backend/app/application/tool_providers/mcp_client.py`, `mcp_provider.py`, `mcp_runtime.py` |
| Inference telemetry | `backend/app/application/monitoring/inference.py` |

Do not add a second executor, registry, router, scheduler or runtime database
for this scope. Extend the owners above.

### Current Capability Boundaries

- Tool execution is fail-closed through the single executor.
- Deferred tool search is active for code-agent runs and remains run-scoped.
- MCP context support is stdio-only and bounded.
- Subagents are local, read-only and bounded.
- Streaming telemetry does not yet include exact token counts unless future raw
  stream chunks expose token usage.

### Main Regression Tests

| Capability | Test files |
|------------|------------|
| ToolSpec fail-closed policy | `backend/tests/test_p9_2_fixup.py`, `backend/tests/test_p9_2a2_scope_enforcement.py` |
| Model routing | `backend/tests/test_p9_3_chat_routing.py`, `backend/tests/test_p9_3_commit3.py` |
| Deferred tools | `backend/tests/test_p10_1_deferred_tools.py`, `backend/tests/test_p10_1_deferred_loop.py` |
| MCP stdio context | `backend/tests/test_p11_mcp_*.py` |
| Task checklist / subagents / telemetry / guard | `backend/tests/test_p12_*.py` |
