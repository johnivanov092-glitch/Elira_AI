# Elira AI

Self-hosted AI workspace with a Tauri desktop shell, FastAPI backend,
React/TypeScript frontend, local project tools, memory, approvals, and local
inference through OpenAI-compatible endpoints.

Core state stays on the main PC. The AI server is used as an inference backend
only.

## Current Runtime

- Desktop shell: `src-tauri/` (Tauri 2.x).
- Backend API: `backend/app` (FastAPI, layered `application` / `domain` /
  `infrastructure`).
- Frontend: `frontend/` (React 18, Vite, TypeScript).
- Local model provider: OpenAI-compatible llama-server endpoint.
- Embeddings provider: OpenAI-compatible embeddings endpoint.
- Runtime data: root `data/` SQLite DBs and generated/user files.

Only the OpenAI-compatible local provider path is active in the current
architecture.

## Local Provider Environment

Put local machine secrets and provider settings in
`backend/.env.local` only. Do not commit that file.

```env
LLAMA_SERVER_ENABLED=true
LLAMA_SERVER_BASE_URL=http://192.168.88.15:8000/v1
LLAMA_SERVER_MODEL=local-model
LLAMA_SERVER_API_KEY=local
LLAMA_SERVER_TIMEOUT_SECONDS=600
LLAMA_SERVER_CONTEXT_WINDOW=65536

LOCAL_EMBED_ENABLED=true
LOCAL_EMBED_BASE_URL=http://192.168.88.15:8001/v1
LOCAL_EMBED_MODEL=local-embed
LOCAL_EMBED_API_KEY=local
LOCAL_EMBED_TIMEOUT_SECONDS=30
```

Defaults are defined in
`backend/app/infrastructure/llm/openai_compatible.py`.

## API Access (auth)

Loopback callers (the Tauri shell and the dev browser on `127.0.0.1`) are
trusted and need no token. Any non-local caller (LAN / mobile) must send a
bearer token, which closes the unauthenticated-command-execution path when the
backend is bound to `0.0.0.0` in mobile mode.

- The token is read from `ELIRA_API_TOKEN`; if unset it is auto-generated once
  into `data/elira_api_token` (untracked). Read that file to get the token.
- Mobile/LAN frontend: set `VITE_ELIRA_API_TOKEN` to the same value so the UI
  sends `Authorization: Bearer <token>`.
- Set `ELIRA_API_AUTH=off` to disable enforcement (trusted networks only).

## Setup

```powershell
# Backend
cd D:\AIWork\Elira_AI\backend
python -m venv .venv
.\.venv\Scripts\pip install -r requirements.txt

# Frontend
cd D:\AIWork\Elira_AI\frontend
npm install
```

`requirements.txt` is the human-edited core dependency list (bounded ranges).
`requirements.lock` is the fully pinned, resolved core set for reproducible
installs (`pip install -r requirements.lock`). Regenerate after editing
`requirements.txt`:

```powershell
cd D:\AIWork\Elira_AI\backend
.\.venv\Scripts\pip install pip-tools
.\.venv\Scripts\python.exe -m piptools compile requirements.txt -o requirements.lock --strip-extras
```

Optional heavy capabilities are installed separately:

```powershell
cd D:\AIWork\Elira_AI\backend
.\.venv\Scripts\pip install -r requirements-optional.txt
playwright install chromium
```

## Run

```powershell
# Backend
cd D:\AIWork\Elira_AI\backend
.\.venv\Scripts\python.exe -m uvicorn app.main:app --host 127.0.0.1 --port 8000 --reload

# Frontend browser UI
cd D:\AIWork\Elira_AI\frontend
npm run dev

# Desktop shell, from repo root
cd D:\AIWork\Elira_AI
npm run tauri dev
```

Windows launchers:

- `Elira.bat` - main local launcher.
- `run_tauri_dev.bat` - dev launcher.
- `Elira_Mobile.bat` - LAN/mobile launcher.
- `kill_elira.bat` - stops Elira processes.

## Verification

Use focused checks for small changes and the full backend suite for cross-cutting
runtime work.

```powershell
cd D:\AIWork\Elira_AI
backend\.venv\Scripts\python.exe -m pytest -q
npm --prefix frontend run typecheck
npm --prefix frontend run build
git diff --check
```

Backend import smoke:

```powershell
cd D:\AIWork\Elira_AI\backend
.\.venv\Scripts\python.exe -c "from app.main import app; print(len(app.routes), len(app.openapi().get('paths', {})))"
```

## Documentation

- `docs/README.md` - documentation index.
- `docs/ARCHITECTURE.md` - current architecture and contracts.
- `docs/PROJECT_MAP.md` - repository map and ownership.
- `docs/SERVER.md` - AI inference server summary (host, endpoints, models).
- `docs/POST_SERVER_BACKLOG.md` - current post-server status.

The dedicated inference server has its own repo, `Elira_AI_Server`, in the same
parent folder; its full operational docs live at
`../Elira_AI_Server/Server/ACCESS.md`.

## Repository Rules

- Keep runtime DBs, uploads, generated files, logs, models, caches, env
  files, and secrets out of git.
- `data/elira_secret.key` and `data/elira_api_token` are per-machine secrets:
  they auto-generate at runtime and must stay untracked (committing the Fernet
  key would let anyone with the repo decrypt stored data).
- Keep `data/plugins/` and `data/plugins_config.json` tracked intentionally.
- Extend the existing agent executor, tool registry, model provider, and DB
  modules instead of adding parallel runtimes.
- Prefer small, verified patches and commit only clean working trees.
