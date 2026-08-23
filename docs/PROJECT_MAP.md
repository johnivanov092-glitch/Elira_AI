# Project Map

## Repository

```text
D:\AIWork\Elira_AI
├─ backend/       FastAPI + Python agent/runtime
├─ frontend/      React + Vite + TypeScript UI
├─ src-tauri/     Windows desktop shell and native UAC bridge
├─ docs/          current architecture and operating docs
├─ data/          machine-local runtime data (mostly ignored)
├─ .agent/runs/   durable code-agent journals
├─ .scratch/      local issue tracker/work notes
└─ scripts/       maintenance helpers
```

The sibling `D:\AIWork\Elira_AI_Server` owns llama.cpp/model serving. Do not
move Elira's backend state or tools into that repository.

## Backend owners

| Area | Canonical owner |
|---|---|
| FastAPI app/lifecycle | `backend/app/main.py` |
| Mounted routers | `backend/app/api/routes/registry.py` |
| Code-agent HTTP/SSE | `backend/app/api/routes/code_agent_routes.py` |
| Workflow HTTP/API | `backend/app/api/routes/workflow_routes.py` |
| Durable event SSE | `backend/app/api/routes/event_bus_routes.py` |
| Multi-agent HTTP/SSE | `backend/app/api/routes/advanced_routes.py` |
| Agent loop | `backend/app/application/code_agent/agent_loop.py` |
| Context rollover | `backend/app/application/code_agent/delivery_session.py` |
| Planning | `backend/app/application/code_agent/planning.py` |
| Prompts/schemas | `backend/app/application/code_agent/prompts.py`, `tool_schemas.py` |
| Built-in capability groups | `backend/app/application/code_agent/capabilities.py` |
| Built-in tool implementations | `backend/app/application/code_agent/tools/` |
| Runtime control adapter | `backend/app/application/code_agent/tools/_runtime_control.py` |
| Runtime result contract | `backend/app/application/code_agent/tools/_runtime_control_contract.py` |
| Workflow/data runtime adapters | `backend/app/application/code_agent/tools/_runtime_control_workflows.py`, `_runtime_control_data.py` |
| Tool executor | `backend/app/application/agent_kernel/executor.py` |
| Workflow impact classification | `backend/app/application/agent_kernel/impact_policy.py` |
| Workflow engine | `backend/app/application/workflows/` |
| Workflow interval triggers | `backend/app/application/workflows/triggers.py` |
| Workflow request lifecycle/recovery/validation | `backend/app/application/workflows/request_lifecycle.py`, `request_recovery.py`, `request_validation.py` |
| Workflow step execution | `backend/app/domain/workflows/step_executor.py` |
| Runtime provider registry | `backend/app/application/tool_providers/runtime_registry.py` |
| SSH/MCP/LSP/IT Ops providers | `backend/app/application/tool_providers/` |
| Tool inventory | `backend/app/application/tool_registry/` |
| LLM client | `backend/app/infrastructure/llm/openai_compatible.py` |
| Portable vault | `backend/app/infrastructure/secrets/vault.py` |
| IT Ops persistence | `backend/app/infrastructure/it_ops/store.py` |
| Memory facade | `backend/app/application/memory/facade.py` |
| SQLite helper | `backend/app/infrastructure/db/connection.py` |

Do not create another loop, executor, registry, provider stack or DB facade.

## Frontend owners

```text
frontend/src
├─ App.tsx
├─ api/
│  ├─ codeAgent.ts       code-agent SSE/client types
│  ├─ workflows.ts       Workflow requests/events/UAC bridge
│  └─ project.ts         projects + multi-agent API
└─ workspace/
   ├─ WorkspaceShell.tsx application workspace
   ├─ Composer.tsx       permission/reasoning/multi-agent controls
   ├─ backgroundRuns.ts  one owner for live run state
   ├─ AgentTurn.tsx      agent result rendering
   ├─ WorkflowRequestCard.tsx
   ├─ useWorkflowRequestEvents.ts
   └─ Settings.tsx       minimal user settings
```

Do not create a second SSE reader inside a component. `backgroundRuns.ts` and
`useWorkflowRequestEvents.ts` own the live streams.

## Desktop owner

`src-tauri/src/main.rs` registers native commands. `run_elevated_command` is the
only Windows UAC execution bridge. It launches a separate elevated helper for a
single Workflow request; Elira itself stays under the current user token.

## Runtime data

Default location: `data/`; override: `ELIRA_DATA_DIR`.

```text
data/
├─ workflow_engine.db        templates, runs, requests, interval triggers
├─ tool_registry.db
├─ task_planner.db
├─ code_agent_sessions.db
├─ agent_monitor.db
├─ event_bus.db
├─ it_ops.sqlite3
├─ integrations.db
├─ smart_memory.db
├─ rag_memory.db
├─ library.db
├─ projects.db
├─ web_corpus.sqlite3
├─ elira_state.db
├─ drift_facts.db
├─ portable_vault.json
├─ mcp_servers.json
├─ lsp_servers.json
├─ ssh_acl.json          legacy filename; saved SSH shortcuts
└─ resources/
```

Never commit DB snapshots, vaults, resources, secrets, logs, model weights or
machine-local `.env.local` values.

## Documentation

- `docs/AGENT_ARCHITECTURE_GUIDE_RU.md`: full readable agent map.
- `docs/ARCHITECTURE.md`: canonical invariants and boundaries.
- `docs/ARCHITECTURE_SIMPLIFICATION_AUDIT_RU.md`: shipped refactor audit.
- `docs/UI_BASELINE.md`: approved visual baseline.
- `docs/SERVER.md`: inference server boundary.

## Final repository gate

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
