# Elira AI

**Local, private AI workspace** — FastAPI backend · React + Vite frontend · Tauri
desktop shell · Ollama for local inference. Everything runs on your machine.

**Language / Язык: [English](#english) · [Русский](#русский)**

---

## English

### What it is

A fully local AI workspace with two environments:
- **Chat** — assistant with skill routing (planner), memory, web search,
  document/image generation, and autopipelines.
- **Code agent** — separate workspace for working with code: file tools, git,
  terminal, SSH, and MCP.

No cloud dependency for the core loop; web search is the only optional outbound
network use. Keys stay in local env files, data stays on disk.

### Documentation

- `docs/ARCHITECTURE.md` — technical documentation (how it works, why).
- `docs/PROJECT_MAP.md` — project tree, data flow, and all dependencies.
- `docs/README.md` — docs index.

This root README covers setup, dependencies, startup order, launchers, and smoke
checks.

### Dependencies

**Core** — enough to run backend, frontend, dashboard, tasks, pipelines, Telegram
panel, and the desktop shell.

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

The frontend expects the backend at `http://127.0.0.1:8000` (or `VITE_API_BASE_URL`
if set). Recommended order: backend → frontend (if testing browser UI) → Tauri.

> Deploy note: the desktop app serves the built bundle `frontend/dist`. After any
> frontend change run `npm --prefix frontend run build` and restart the app.
> Changes to `src-tauri/tauri.conf.json` need a full Tauri rebuild.

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

### Project layout

- `backend/` — FastAPI API with layered `application` / `domain` / `infrastructure`.
- `frontend/` — React + Vite + TypeScript UI.
- `src-tauri/` — Rust/Tauri desktop shell.
- `scripts/` — smoke and utility scripts.
- `data/` — local runtime root (SQLite DBs, uploads, generated, secret key).
- `docs/` — project documentation.

---

## Русский

### Что это

Полностью локальный приватный AI-воркспейс с двумя окружениями:
- **Чат** — ассистент с маршрутизацией навыков (planner), памятью, веб-поиском,
  генерацией документов/картинок, автопайплайнами.
- **Код-агент** — отдельный воркспейс для работы с кодом: файловые инструменты,
  git, терминал, SSH и MCP.

Для основного цикла внешняя сеть не нужна; единственный опциональный выход в сеть —
веб-поиск. Ключи — в локальных env-файлах, данные — на диске.

### Документация

- `docs/ARCHITECTURE.md` — техническая документация (как устроено и почему).
- `docs/PROJECT_MAP.md` — дерево проекта, потоки данных и все зависимости.
- `docs/README.md` — индекс документации.

Этот корневой README — про установку, зависимости, порядок запуска, лаунчеры и
smoke-проверки.

### Зависимости

**Базовые** — их достаточно для бэкенда, фронтенда, дашборда, задач, пайплайнов,
Telegram-панели и десктоп-оболочки.

```bash
# Бэкенд
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt

# Фронтенд
cd frontend
npm install
```

**Опциональные** — тяжёлые возможности, грузятся лениво при первом использовании.
Приложение запускается и без них; дашборд и `/api/project-brain/status` сообщают о
недостающей возможности (`available`, `reason`, `missing_packages`, `hint`).

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

Фронтенд ожидает бэкенд на `http://127.0.0.1:8000` (или `VITE_API_BASE_URL`, если
задан). Рекомендуемый порядок: бэкенд → фронтенд (если тестируешь браузерный UI) →
Tauri.

> Про деплой: десктоп отдаёт собранный бандл `frontend/dist`. После любой правки
> фронта выполни `npm --prefix frontend run build` и перезапусти приложение.
> Изменения `src-tauri/tauri.conf.json` требуют полной пересборки Tauri.

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

### Структура проекта

- `backend/` — FastAPI API со слоями `application` / `domain` / `infrastructure`.
- `frontend/` — UI на React + Vite + TypeScript.
- `src-tauri/` — десктоп-оболочка на Rust/Tauri.
- `scripts/` — smoke- и вспомогательные скрипты.
- `data/` — локальный runtime-root (SQLite-БД, uploads, generated, ключ).
- `docs/` — документация проекта.
