# Elira AI — Техническая документация

Единый технический документ проекта. Описывает **только текущую рабочую
архитектуру**. Парный документ — [`PROJECT_MAP.md`](PROJECT_MAP.md) — это
«конструктор»: дерево репозитория, потоки данных и зависимости.

> Эта архитектура — результат большого совместного рефакторинга **Codex**
> (распил backend-монолита на слои `application`/`domain`/`infrastructure`,
> консолидация роутов, очистка Tauri) и **Claude** (Agent OS, миграция фронта на
> TypeScript, извлечение reflection/orchestrator, стабилизация чата). Раздел
> [«История»](#история-как-сложилась-архитектура) фиксирует вклад обоих.

---

## 1. Что такое Elira AI

Elira AI — **полностью локальный приватный AI-воркспейс**: десктоп-приложение
на Tauri поверх FastAPI-бэкенда и локальных моделей Ollama. Всё — чаты, память,
ключи, сгенерированные файлы — живёт на машине пользователя. Внешняя сеть нужна
только опционально (веб-поиск).

В приложении два самостоятельных окружения:

- **Обычный чат** — ассистент с маршрутизацией навыков (planner), памятью,
  веб-поиском, генерацией документов/картинок, автопайплайнами.
- **Код-агент** — отдельный воркспейс для работы с кодом: файловые инструменты,
  git, терминал, SSH и MCP.

---

## 2. Технологический стек

| Слой | Технологии |
|------|------------|
| Десктоп-оболочка | **Tauri 1.x** (Rust, `src-tauri/`). В проде отдаёт собранный бандл `frontend/dist`. Корневой `package.json` оркеструет CLI (`@tauri-apps/cli`). |
| Фронтенд | **React 18 + Vite 5 + TypeScript 6**, `@tauri-apps/api`, `lucide-react`. `tsconfig` — `strict` + `noUnusedLocals`. |
| Бэкенд | **FastAPI + Uvicorn + Pydantic v2**. Запускается из `backend/.venv`. |
| LLM-движок | **Ollama** локально (`127.0.0.1:11434`). Модель по умолчанию `gemma3:4b`. |
| Поиск/парсинг | `ddgs` (DuckDuckGo), `requests`, `beautifulsoup4`, `pypdf`; Tavily через ключ из `.env.local`. |
| Данные/числа | SQLite (через общий провайдер соединений), `numpy` (эмбеддинги хранятся как буферы). |
| Документы | `python-docx`; тяжёлые парсеры (Excel/таблицы PDF, OCR, pandas, картинки, скриншоты) — в `requirements-optional.txt`, грузятся лениво при первом использовании. |
| Секреты/шифрование | `cryptography` (+ `data/elira_secret.key`). |
| Файловый watcher | `watchdog` (реалтайм авто-индекс проекта код-агента). |

Полный разбор зависимостей и «зачем каждая» — в
[`PROJECT_MAP.md` §6](PROJECT_MAP.md#6-зависимости).

---

## 3. Модель запуска и процессы

Запущенное приложение — это **три процесса**:

1. **Tauri-окно** (Rust) — нативная оболочка, грузит UI.
2. **Frontend-бандл** — статика `frontend/dist`, которую отдаёт Tauri (в проде)
   или Vite dev-сервер (в разработке).
3. **FastAPI-бэкенд** — поднимается из `backend/.venv`, слушает `127.0.0.1:8000`,
   общается с локальной Ollama.

На старте `backend/app/main.py`:
- создаёт `FastAPI`, настраивает CORS (localhost + LAN для мобильного режима);
- монтирует все роутеры из `app/api/routes/registry.py` (`ALL_ROUTERS`);
- инициализирует БД и сидит встроенные сущности: `init_db()`, `seed_builtin_agents()`,
  `seed_builtin_workflows()`, `seed_default_limits()`, `seed_builtin_tools()`.

**Лаунчеры (корень репозитория):**

| Файл | Назначение |
|------|------------|
| `Elira.bat` | Запуск десктоп-приложения. |
| `Elira_Mobile.bat` | Режим с доступом по локальной сети (мобильный). |
| `run_tauri_dev.bat` | Dev-режим Tauri (полная пересборка оболочки). |
| `build_exe.bat` | Сборка установщика/exe. |
| `kill_elira.bat` | Останов всех процессов Elira. |

---

## 4. Архитектура бэкенда

Чистая слоёная структура (`backend/app/`):

```
api/routes/      HTTP-поверхность (FastAPI-роутеры) — тонкие, делегируют вниз
application/     Сценарии/use-cases — основная бизнес-логика по фичам (~55 модулей)
domain/          Чистая логика без I/O: agents (router/planner/orchestrator/reflection), tools, workflows, runtime
infrastructure/  Адаптеры к внешнему миру: llm, db, search, browser, git, shell, storage, integrations, plugins, cache
core/            Конфиг и фундамент: config, llm (Ollama-адаптер), web/web_engines, files, persona_defaults, data_files
schemas/         Pydantic-схемы          state/  runtime-состояние          utils/  утилиты (в т.ч. UTF-8 normalize)
```

**Правило направления зависимостей:** `api → application → domain` и
`application/domain → infrastructure`. Роутер не содержит логики; вся логика — в
`application`/`domain`; весь I/O (БД, LLM, сеть, ФС) — в `infrastructure`/`core`.

Каждый сервис со своим состоянием создаёт собственную SQLite-БД через общий
провайдер `app/infrastructure/db/connection.py` (см. §7).

---

## 5. Два окружения (главная развилка)

Это **полностью раздельные** контуры. Их нельзя путать.

| | Обычный чат | Код-агент |
|---|---|---|
| Frontend-shell | `EliraChatShell.tsx` | `CodeWorkspaceShell.tsx` / `CodeAgentChatShell.tsx` |
| Хранилище | `data/elira_state.db` (чаты + сообщения + настройки) | `data/code_agent_sessions.db` (`turns_json`) |
| Стрим | `executeStream` → `POST /api/chat/stream` | `streamCodeAgent` → `POST /api/code-agent/stream` |
| Реестр стримов (frontend) | `chatRuns` (модульный) | `LIVE_RUNS` (модульный) |
| Инструменты | навыки чата через `planner_v2` | `read/write/edit_file`, `glob`, `grep`, `run_bash` + SSH + MCP через `ToolProvider`/`ToolRegistry` |
| Проект | вкладка «Проекты» владеет глобальным advanced-проектом | независимый `projectRoot` (эндпоинты advanced принимают `root`) |

Ключевые инварианты:

- **ID чатов — строки на фронте.** Бэкенд хранит INTEGER, но `normalizeChat`
  стрингифицирует id; всё сравнение по строкам. Возврат к числовым сравнениям
  однажды молча ломал стрим (`42 !== "42"`).
- **Остановка стрима:** в WebView2 `abort()` не отменяет надёжно `reader.read()` —
  `executeStream` явно делает `reader.cancel()` и игнорирует поздние токены.
- **Независимость проектов:** только `ProjectPanel` вызывает `openAdvancedProject`;
  код-агент читает дерево/файлы строго в своём `projectRoot`.

### Специфика код-агента

Lazy whole-project analysis → поиск/чтение файлов → сбор diff **до** `Apply` →
verify в песочнице → ручное применение. Изменения в живое дерево автоматически не
вносятся; есть action-log и причины отказа (refusal reasons). Локальные модели
4–7B слабы в function-calling, поэтому `agent_loop` восстанавливает инлайновые
вызовы (JSON и синтаксис `name(args)`).

---

## 6. Ключевые подсистемы

### Маршрутизация и оркестрация
`application/chat` + `domain/agents` (`router`, `planner`, `orchestrator`,
`reflection`). `planner_v2` классифицирует запрос и выбирает навыки/инструменты
(его словарь инструментов — источник правды о том, что авто-триггерится).
`route_model_map` (настройки пользователя) выбирает модель под тип задачи
(`chat`/`code`/`project`/`research`); сентинелы `""`/`auto`/`авто` означают
авто-роутинг, иначе уважается явный выбор модели. Флаг `orchestration_enabled`
включает многошаговую оркестрацию (по умолчанию выключен — на 4B быстрее).

### Инструменты и реестр
`application/tool_registry` + `application/tool_providers` + `domain/tools`.
Динамический реестр (`data/tool_registry.db`) вместо `if/elif`. Провайдеры:
встроенный (Builtin), SSH, MCP. Плагины регистрируются как `source="plugin"`.

### Память
- `application/smart_memory` (`data/smart_memory.db`) — факты, предпочтения,
  profile-scoped память; отдача гейтится по релевантности.
- `application/rag_memory` (`data/rag_memory.db`) — retrieval/knowledge,
  эмбеддинги (Ollama `nomic-embed-text`), cosine similarity + keyword fallback,
  векторы как `numpy`-BLOB.
- `application/elira_memory` — настройки и состояние чата.

### Веб-поиск
`infrastructure/search` + `core/web*`. Стек **Tavily → DuckDuckGo (`ddgs`) →
Wikipedia** с failover; `multisearch` и новостной режим. Ключ Tavily — только в
`backend/.env.local` (грузится через `load_dotenv`, читается `os.getenv`), в git
его нет. Есть скрытое внутреннее осознание времени (temporal intent).

### Agent OS  *(работа Codex + Claude)*
Слой «операционной системы для агентов», API-префикс `/api/agent-os/*`:

| Подсистема | Модуль | БД | Кто |
|------------|--------|----|----|
| Agent Registry — идентичность/состояние/история агентов | `application/agent_registry` | `agent_registry.db` | Claude |
| Tool Registry — реестр инструментов с JSON-схемами | `application/tool_registry` | `tool_registry.db` | Claude |
| Event Bus — события, сообщения между агентами, подписки | `application/event_bus` | `event_bus.db` | Codex |
| Workflow Engine — шаблоны и прогоны воркфлоу | `application/workflow_engine` + `application/workflows` | `workflow_engine.db` | Codex |
| Monitoring + Sandboxing — метрики, лимиты, soft-песочница | `application/monitoring` | `agent_monitor.db` | Codex |

В дашборде есть read-only секция «Agent OS» (health/dashboard/limits).

### Автоматизация
- `application/autopipeline` (`data/autopipelines.db`) — лёгкий планировщик на
  `threading.Timer` (тик 30с, автозапуск при импорте). Типы задач: `prompt`,
  `web_search`, `plugin`, `workflow`, `http`. Хранит логи прогонов.
- `application/task_planner` (`data/task_planner.db`) — задачи/todo
  (`/api/tasks/*`), статусы и приоритеты.

### Мультимодальность и медиа
`core/llm.py` проверяет capabilities модели через Ollama и для vision-моделей
шлёт картинки нативным image-payload; иначе — `default_vision_model`. OCR — не
основной путь, только fallback и для preview/индексации. `application/media` —
генерация изображений (SDXL Turbo / FLUX.1-schnell). `application/pdf` — чтение
PDF + OCR (авто-детект бинаря Tesseract). `application/file_extract`/`file_ops` —
извлечение текста и операции с файлами; `library` — вложения (`data/library.db`:
`media_kind`, `mime_type`, `stored_path`, `sha256`, `preview`, `extracted_text`).

---

## 7. Хранение данных

Активный runtime-root — **`data/`** (а не `backend/data/`). Каждый сервис
создаёт свою SQLite-БД через общий провайдер
`app/infrastructure/db/connection.py`.

| Файл | Содержимое |
|------|-----------|
| `elira_state.db` | Чаты, сообщения, настройки, метаданные. |
| `smart_memory.db` / `rag_memory.db` | Умная память / RAG-retrieval. |
| `library.db` | Вложения и извлечённый текст. |
| `response_cache.db` | Кеш LLM-ответов. |
| `code_agent_sessions.db` | Сессии код-агента (`turns_json`). |
| `agent_registry.db` / `tool_registry.db` / `event_bus.db` / `workflow_engine.db` / `agent_monitor.db` | Agent OS. |
| `autopipelines.db` / `task_planner.db` | Автопайплайны / задачи. |
| `run_history.db` | История запусков. |
| `integrations.db` | Интеграции (в т.ч. Telegram). |
| `projects.db` | Реестр проектов код-агента. |

**Не в git** (только локально): все `data/*.db`, `data/generated/`,
`data/uploads/`, `data/system/` (gitignored). **В git остаются** осознанно:
`data/elira_secret.key`, `data/plugins/` (примеры плагинов), `data/plugins_config.json`.
Секреты (Tavily и т.п.) — только в `backend/.env.local` (gitignored).

---

## 8. Фронтенд

`frontend/src/`:
- `main.tsx` / `App.tsx` — вход.
- `components/` — UI: `EliraChatShell` (обычный чат + настройки + пайплайны +
  dashboard), `CodeWorkspaceShell`/`CodeAgentChatShell`/`IdeWorkspaceShell`
  (код-агент), `ProjectPanel`, `MemoryPanel`, `StatusPanels`, `TerminalPanel`,
  `SpotlightOverlay`, `Ssh/McpConfigDialog`, `MarkdownRenderer`, `ArtifactPanel`,
  `PlannerKeywordsPanel`, `ToastHost`.
- `api/` — тонкий клиент по доменам (`chat`, `codeAgent`, `advanced`,
  `smartMemory`, `pipelines`, `tasks`, `git`, `terminal`, `library`, `tools`,
  `dashboard`, `system`, `integrations`, `telegram`, `spotlight`, …). Базовый
  `client.ts`.
- `streamRegistry.ts` / `pickFolder.ts` — переживание стримов и общий пикер
  папок (Tauri dialog).
- `chatConstants.ts` — в т.ч. `CHAT_WORKING_SKILLS` (какие навыки реально
  работают в чате).

---

## 9. Сборка, деплой и конвенции

**Дисциплина деплоя (на этом чаще всего спотыкаются):**
- Фронт — это бандл `frontend/dist` (gitignored), его отдаёт Tauri. После **любой**
  правки фронта: `npm --prefix frontend run build` **и перезапуск приложения**.
  Правки в исходниках сами по себе в окне не появляются.
- Изменения `src-tauri/tauri.conf.json` вкомпилированы в бинарь → нужна **полная
  пересборка Tauri** (`run_tauri_dev.bat` / `build_exe.bat`), не просто `build`.
- Бэкенд запускается из **`backend/.venv`** — проверять через
  `backend/.venv/Scripts/python.exe`, не системный Python.

**Гейты перед коммитом:**
- Frontend: `cd frontend && npx tsc --noEmit` — 0 ошибок (`noUnusedLocals`).
- Backend: релевантный `pytest` (полный прогон для cross-cutting изменений)
  через venv-Python.
- Не коммитить `data/*.db` и `data/uploads/*`.

**Жёсткие правила проекта:**
- Никаких заглушек и мёртвого кода. Код = конечная логика.
- Зависимости — либо используются, либо удаляются.
- Секреты — только локально (`.env.local`); runtime-данные — вне git.
- `ruff` F401 легитимно срабатывает только на фасад-реэкспортах (package
  `__init__.py`, `core/web.py`, `workflow_engine/runtime.py`) — это публичный API.

---

## 10. Модели и навыки

Локальные модели 4–7B слабы и ненадёжны в function-calling; `agent_loop`
восстанавливает инлайновые вызовы (JSON + `name(args)`). Надёжность растёт с
размером — **целевые модели 14–20B**. В обычном чате реально работают только
навыки из `CHAT_WORKING_SKILLS` (`frontend/src/chatConstants.ts`), остальные
показаны неактивными до перехода на большие модели (включаются добавлением id в
этот набор). Настройки моделей: `default_model`, `default_coding_model`,
`default_vision_model`, `agent_profile`, `ollama_context`, `route_model_map`,
`orchestration_enabled`.

---

## История: как сложилась архитектура

- **Agent OS (фазы 1–5)** — совместно: Phase 1 Agent Registry и Phase 2 Tool
  Registry (Claude); Phase 3 Event Bus, Phase 4 Workflow Engine, Phase 5
  Monitoring+Sandboxing (Codex). Phase 6 — миграция фронта на TypeScript (Claude).
- **Большой backend-рефактор** — Codex: распил монолитов `agents_service.py` и
  `core/agents.py` на слои `application`/`domain`/`infrastructure` через общий
  SQLite-провайдер, консолидация роутов, очистка дублей и Tauri-старта. Claude
  выделил `domain/agents/reflection.py` и `orchestrator.py`, убрал все `*_frozen`
  копии, починил стрим обычного чата и сделал контуры чат/код-агент независимыми.

Координация велась без посредника-пользователя через рабочие планы; теперь эти
планы свёрнуты в эту документацию.
