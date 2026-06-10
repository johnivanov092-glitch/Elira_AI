# Elira AI

**Self-hosted AI agent platform** — a fully local, private AI workspace: Tauri
desktop shell · FastAPI backend · React/TypeScript UI · local LLMs via Ollama.
Everything — chats, memory, keys, generated files — stays on your machine.

**Language / Язык: [English](#english) · [Русский](#русский)**

---

## English

### Highlights

- **Policy-gated agent kernel.** Every tool call passes through a single
  fail-closed executor: permission tiers (`auto` / `require_approval` /
  `forbidden`), human-approval gates with TTL and argument-digest binding,
  scope enforcement, full audit trail. A tool without a classified spec never
  reaches dispatch.
- **Engineered for local 7–30B models.** The runtime compensates for
  small-model weaknesses: inline tool-call recovery, context compaction,
  run-scoped deferred tool activation (`tool_search`), model routing profiles,
  per-call inference telemetry.
- **2,800+ backend tests** (pytest) and a strict-mode TypeScript frontend
  (`noUnusedLocals`) — gates run before every commit.
- **Two independent environments:** a chat assistant (skill routing, memory,
  web search, document/image generation, pipelines, Telegram) and a code agent
  (file tools, git, terminal, SSH, MCP).
- **MCP integration** (stdio: tools, resources, prompts — bounded, fail-closed
  registration) plus a plugin system and workflow engine.
- **Built with a multi-agent AI workflow:** executor/reviewer roles with an
  enforced review gate — a commit cannot land without a reviewer `PASS`.

No cloud dependency for the core loop; web search is the only optional
outbound network use. Keys stay in local env files, data stays on disk.

### Stack

FastAPI + Pydantic v2 · React 18 + Vite + TypeScript (strict) · Tauri 1.x
(Rust) · Ollama · SQLite · Playwright · SDXL/FLUX image generation (optional)

### Quick start

**Core** — enough to run backend, frontend, dashboard, tasks, pipelines,
Telegram panel, and the desktop shell.

```bash
# Backend
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Frontend
cd frontend
npm install
```

**Optional** — heavy capabilities, loaded lazily on first use. The app starts
without them; the dashboard and `/api/project-brain/status` report any missing
capability (`available`, `reason`, `missing_packages`, `hint`).

```bash
cd backend
.venv\Scripts\pip install -r requirements-optional.txt
playwright install chromium   # only for the screenshot skill
```

| Section | Enables |
|---------|---------|
| Image generation | SDXL Turbo / FLUX.1-schnell (`torch`, `diffusers`, `transformers`, …) |
| Document parsing | Excel, PDF tables, OCR (`pdfplumber`, `pandas`, `openpyxl`, `pytesseract`, `pdf2image`) |
| Browser screenshots | `playwright` |

### Startup order

```bash
# Backend (127.0.0.1:8000)
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend dev server (browser UI, 5173)
cd frontend
npm run dev

# Tauri desktop (from repo root)
npm run tauri dev
```

The frontend expects the backend at `http://127.0.0.1:8000` (or
`VITE_API_BASE_URL` if set). Recommended order: backend → frontend (if testing
browser UI) → Tauri.

> Deploy note: the desktop app serves the built bundle `frontend/dist`. After
> any frontend change run `npm --prefix frontend run build` and restart the
> app. Changes to `src-tauri/tauri.conf.json` need a full Tauri rebuild.

### Windows launchers

- `Elira.bat` — starts the backend, prints capability notes, opens Tauri.
- `run_tauri_dev.bat` — installs frontend packages if needed, starts backend, runs Tauri dev.
- `Elira_Mobile.bat` — LAN / mobile launcher.
- `kill_elira.bat` — stops all Elira processes.

### Smoke checks

```bash
# Backend imports + compile
cd backend
.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes), len(app.openapi().get('paths', {})))"
.venv\Scripts\python.exe -m compileall app

# Contract + tests
backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py
backend\.venv\Scripts\python.exe -m pytest -q

# Frontend build + typecheck
npm --prefix frontend run build
cd frontend && npx tsc --noEmit
```

### Documentation

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — technical documentation (how it works, why).
- [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md) — project tree, data flow, and all dependencies.
- [`docs/README.md`](docs/README.md) — docs index.

### Project layout

- `backend/` — FastAPI API with layered `application` / `domain` / `infrastructure`.
- `frontend/` — React + Vite + TypeScript UI.
- `src-tauri/` — Rust/Tauri desktop shell.
- `scripts/` — smoke and utility scripts.
- `data/` — local runtime root (SQLite DBs, uploads, generated, secret key).
- `docs/` — project documentation.

### Status

Personal research project in active development, used daily as a working
tool. Agent runtime phases P0–P12 (kernel, policy, approvals, deferred tools,
MCP context, telemetry) are implemented and tested; deferred work is tracked
honestly in [`docs/POST_SERVER_BACKLOG.md`](docs/POST_SERVER_BACKLOG.md).

---

## Русский

**Self-hosted платформа AI-агентов** — полностью локальный приватный
AI-воркспейс: десктоп на Tauri · FastAPI-бэкенд · React/TypeScript UI ·
локальные модели через Ollama. Всё — чаты, память, ключи, файлы — на машине
пользователя.

### Ключевые особенности

- **Policy-ядро исполнения инструментов.** Каждый tool call проходит через
  единый fail-closed executor: тиры разрешений (`auto` / `require_approval` /
  `forbidden`), human-approval гейты с TTL и привязкой к digest аргументов,
  scope-контроль, полный аудит. Инструмент без классифицированной спецификации
  не доходит до выполнения.
- **Инженерия под локальные модели 7–30B.** Runtime компенсирует слабости
  малых моделей: восстановление inline tool-calls, компакция контекста,
  отложенная активация инструментов (`tool_search`), профили маршрутизации
  моделей, телеметрия инференса на каждый вызов.
- **2 800+ автотестов бэкенда** (pytest) и strict-режим TypeScript на фронте
  (`noUnusedLocals`) — гейты прогоняются перед каждым коммитом.
- **Два независимых окружения:** чат-ассистент (маршрутизация навыков, память,
  веб-поиск, генерация документов/картинок, автопайплайны, Telegram) и
  код-агент (файловые инструменты, git, терминал, SSH, MCP).
- **Интеграция MCP** (stdio: tools, resources, prompts — ограниченная,
  fail-closed регистрация), система плагинов и workflow-движок.
- **Разработка через мультиагентный AI-пайплайн:** роли executor/reviewer с
  принудительным ревью-гейтом — коммит не проходит без `PASS` ревьюера.

Для основного цикла внешняя сеть не нужна; единственный опциональный выход в
сеть — веб-поиск. Ключи — в локальных env-файлах, данные — на диске.

### Зависимости

**Базовые** — их достаточно для бэкенда, фронтенда, дашборда, задач,
пайплайнов, Telegram-панели и десктоп-оболочки.

```bash
# Бэкенд
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Фронтенд
cd frontend
npm install
```

**Опциональные** — тяжёлые возможности, грузятся лениво при первом
использовании. Приложение запускается и без них; дашборд и
`/api/project-brain/status` сообщают о недостающей возможности (`available`,
`reason`, `missing_packages`, `hint`).

```bash
cd backend
.venv\Scripts\pip install -r requirements-optional.txt
playwright install chromium   # только для навыка скриншотов
```

| Раздел | Что включает |
|--------|--------------|
| Генерация изображений | SDXL Turbo / FLUX.1-schnell (`torch`, `diffusers`, `transformers`, …) |
| Разбор документов | Excel, таблицы PDF, OCR (`pdfplumber`, `pandas`, `openpyxl`, `pytesseract`, `pdf2image`) |
| Скриншоты браузера | `playwright` |

### Порядок запуска

```bash
# Бэкенд (127.0.0.1:8000)
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Dev-сервер фронтенда (браузерный UI, 5173)
cd frontend
npm run dev

# Десктоп Tauri (из корня репозитория)
npm run tauri dev
```

Фронтенд ожидает бэкенд на `http://127.0.0.1:8000` (или `VITE_API_BASE_URL`,
если задан). Рекомендуемый порядок: бэкенд → фронтенд (если тестируешь
браузерный UI) → Tauri.

> Про деплой: десктоп отдаёт собранный бандл `frontend/dist`. После любой
> правки фронта выполни `npm --prefix frontend run build` и перезапусти
> приложение. Изменения `src-tauri/tauri.conf.json` требуют полной пересборки
> Tauri.

### Лаунчеры (Windows)

- `Elira.bat` — запускает бэкенд, печатает заметки о возможностях, открывает Tauri.
- `run_tauri_dev.bat` — ставит пакеты фронта при необходимости, запускает бэкенд, поднимает Tauri dev.
- `Elira_Mobile.bat` — лаунчер для локальной сети / мобильного.
- `kill_elira.bat` — останавливает все процессы Elira.

### Smoke-проверки

```bash
# Импорты + компиляция бэкенда
cd backend
.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes), len(app.openapi().get('paths', {})))"
.venv\Scripts\python.exe -m compileall app

# Контракт + тесты
backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py
backend\.venv\Scripts\python.exe -m pytest -q

# Сборка + типизация фронта
npm --prefix frontend run build
cd frontend && npx tsc --noEmit
```

### Документация

- [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md) — техническая документация (как устроено и почему).
- [`docs/PROJECT_MAP.md`](docs/PROJECT_MAP.md) — дерево проекта, потоки данных и все зависимости.
- [`docs/README.md`](docs/README.md) — индекс документации.

### Структура проекта

- `backend/` — FastAPI API со слоями `application` / `domain` / `infrastructure`.
- `frontend/` — UI на React + Vite + TypeScript.
- `src-tauri/` — десктоп-оболочка на Rust/Tauri.
- `scripts/` — smoke- и вспомогательные скрипты.
- `data/` — локальный runtime-root (SQLite-БД, uploads, generated, ключ).
- `docs/` — документация проекта.

### Статус

Личный исследовательский проект в активной разработке, используется ежедневно
как рабочий инструмент. Фазы агентного runtime P0–P12 (kernel, policy,
approvals, deferred tools, MCP-контекст, телеметрия) реализованы и покрыты
тестами; отложенные работы честно зафиксированы в
[`docs/POST_SERVER_BACKLOG.md`](docs/POST_SERVER_BACKLOG.md).
