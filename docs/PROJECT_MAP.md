# Project Map

## Root

```text
D:\AIWork\Elira_AI
|-- backend/          FastAPI backend and Python runtime
|-- frontend/         React/Vite/TypeScript UI
|-- src-tauri/        Tauri desktop shell
|-- data/             local runtime data root
|-- docs/             project documentation
|-- models/           local model artifacts, ignored
|-- scripts/          smoke and maintenance scripts
|-- Elira.bat         main Windows launcher
|-- run_tauri_dev.bat dev launcher
|-- Elira_Mobile.bat  LAN/mobile launcher
|-- kill_elira.bat    process cleanup launcher
|-- package.json      root Tauri/npm scripts
`-- README.md         repo entrypoint
```

Root log files are runtime noise and should not be committed.

## Backend

```text
backend/app
|-- api/              HTTP routes
|-- application/      use cases and orchestration
|-- core/             config, web runtime, legacy compatibility helpers
|-- domain/           domain models and tool specs
`-- infrastructure/   SQLite, LLM, search, IO adapters
```

Primary backend contracts:

| Area | Files |
|------|-------|
| FastAPI entrypoint | `backend/app/main.py` |
| API auth gate | `backend/app/core/auth.py` |
| Local LLM provider | `backend/app/infrastructure/llm/openai_compatible.py` |
| Local chat wrapper | `backend/app/application/chat/local_chat.py` |
| Project-brain LLM | `backend/app/application/project_brain/llm.py` |
| Model profiles | `backend/app/application/monitoring/store.py` |
| Code-agent loop | `backend/app/application/code_agent/agent_loop.py` |
| Run evidence ledger | `backend/app/application/code_agent/run_evidence.py` |
| Tool executor | `backend/app/application/agent_kernel/executor.py` |
| Tool registry | `backend/app/application/tool_registry/` |
| Tool providers | `backend/app/application/tool_providers/` |
| SQLite connection | `backend/app/infrastructure/db/connection.py` |

## Frontend

```text
frontend/src
|-- api/              domain API clients
|-- components/       UI shells and panels
|-- styles/           markdown and shared styles
|-- App.tsx           app root
|-- main.tsx          React entrypoint
|-- streamRegistry.ts stream persistence
`-- pickFolder.ts     Tauri folder picker helper
```

Run checks from repo root:

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
```

## Desktop

```text
src-tauri
|-- Cargo.toml
|-- Cargo.lock
|-- tauri.conf.json
`-- src/
```

Keep `src-tauri/Cargo.lock` tracked. Root `Cargo.lock` is ignored.

## Runtime Data

```text
data/
|-- *.db              runtime SQLite DBs, ignored
|-- generated/        generated artifacts, ignored
|-- uploads/          user uploads, ignored
|-- system/           runtime state, ignored
|-- elira_secret.key  Fernet key, auto-generated, ignored
|-- elira_api_token   per-machine API token, auto-generated, ignored
|-- plugins/          tracked plugin examples/config roots
`-- plugins_config.json
```

Do not commit runtime DB snapshots, uploaded files, generated artifacts, logs,
model weights, or `.env.local`.

## Server Project Boundary

`../Elira_AI_Server` (sibling repo in the same parent folder; summarized in
`docs/SERVER.md`) is a separate repo. It owns:

- hardware/BIOS/Ubuntu notes;
- ROCm/llama-server/Docker setup;
- server SSH access inventory;
- model serving and embedding service docs.

Elira owns:

- UI/backend runtime;
- project files and tools;
- memory and approvals;
- model-provider client code.

Do not merge these repos or move Elira backend state to the server during stage
1.
