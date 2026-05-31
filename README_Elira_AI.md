# Elira AI

Local AI workspace with:
- FastAPI backend
- React + Vite frontend
- Tauri desktop shell
- Ollama for local inference

## Documentation map

Use the repo root README for:
- dependencies;
- startup order;
- launchers;
- smoke checks.

Use `docs/` for:
- current project status;
- what is already done;
- what still needs to be finished;
- stabilization roadmap;
- logging follow-up.

Primary docs:
- `README_Elira_AI.md` - setup, dependencies, startup, checks
- `docs/README.md` - docs index
- `docs/ROADMAP_STABILIZATION_2026-03-29.md` - active status and next work

## Dependencies

### Core dependencies
Core dependencies are enough to start the backend, frontend, dashboard, tasks, pipelines, Telegram panel, and desktop shell.

Backend:
```bash
cd backend
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
```

Frontend:
```bash
cd frontend
npm install
```

### Optional dependencies
Optional packages unlock degraded features that are now reported in `/api/project-brain/status`.

Install them with:
```bash
cd backend
.venv\Scripts\pip install -r requirements-optional.txt
playwright install chromium
```

Optional packages and what they enable:
- `sentence-transformers` + `faiss-cpu`: vector memory instead of keyword fallback
- `playwright`: screenshot skill

If optional packages are missing:
- the app still starts
- dashboard shows the missing capability
- `/api/project-brain/status` reports `available`, `reason`, `missing_packages`, and `hint`

## Startup order

### Backend
```bash
cd backend
.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload
```

### Frontend
```bash
cd frontend
npm run dev
```

Frontend expects the backend at:
- `http://127.0.0.1:8000`
- or `VITE_API_BASE_URL` if set

### Tauri desktop
From the repo root:
```bash
npm run tauri dev
```

Recommended local order:
1. Start backend on `127.0.0.1:8000`
2. Start frontend dev server on `5173` if you are testing the browser UI
3. Start Tauri if you are testing the desktop shell

## Windows launchers

- `Elira.bat`: starts backend, prints capability notes, then opens Tauri
- `run_tauri_dev.bat`: installs frontend packages if needed, starts backend, then runs Tauri dev
- `Elira_Mobile.bat`: LAN/mobile launcher

## Smoke checks

Backend:
```bash
cd backend
.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes), len(app.openapi().get('paths', {})))"
.venv\Scripts\python.exe -m compileall app
```

Contract checks:
```bash
backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py
backend\.venv\Scripts\python.exe -m unittest discover -s backend/tests -p "test_*.py"
```

Frontend build:
```bash
npm --prefix frontend run build
```

## Runtime smoke checklist

In browser dev mode and in Tauri:
- open `Dashboard`
- open `Tasks`
- open `Pipelines`
- open `Telegram`
- verify requests go to `127.0.0.1:8000`
- stop backend and confirm panels show an error state instead of empty content
- verify missing optional packages appear in the dashboard capability cards

## Project layout

- `backend/`: API, services, storage, orchestration
- `frontend/`: UI
- `src-tauri/`: desktop shell
- `scripts/`: smoke and utility scripts
- `docs/`: project docs

For current implementation status and remaining work, go to `docs/README.md` and `docs/ROADMAP_STABILIZATION_2026-03-29.md`.
