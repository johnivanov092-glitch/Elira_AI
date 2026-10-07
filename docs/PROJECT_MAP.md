# Project Map

## Repository

```text
D:\AIWork\Elira_AI
├─ backend/       FastAPI + Python agent/runtime
├─ frontend/      React + Vite + TypeScript UI
├─ src-tauri/     Windows desktop shell and native UAC bridge
├─ integrations/  isolated portal utilities
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
| Chat sessions and shared folder layout | `backend/app/application/code_agent/sessions.py` |
| Workflow HTTP/API | `backend/app/api/routes/workflow_routes.py` |
| Durable event SSE | `backend/app/api/routes/event_bus_routes.py` |
| Multi-agent HTTP/SSE | `backend/app/api/routes/advanced_routes.py` |
| Agent coordinator, public stream/sync and journal adapters | `backend/app/application/code_agent/agent_loop.py` |
| Live-run control, upstream cancel and Workflow rendezvous | `backend/app/application/code_agent/run_control.py` |
| One model exchange and heartbeat | `backend/app/application/code_agent/model_turn.py` |
| Run-local tool activation and schema visibility | `backend/app/application/code_agent/runtime_activation.py` |
| Turn messages, work-phase guidance, skill/input context and pinned packing | `backend/app/application/code_agent/turn_context.py` |
| Selected tool call and Workflow execution branches | `backend/app/application/code_agent/tool_execution.py` |
| Ordered evidence/outcome/recovery/criteria accounting | `backend/app/application/code_agent/run_observations.py` |
| Final-answer acceptance and correction state | `backend/app/application/code_agent/answer_acceptance.py` |
| Context rollover | `backend/app/application/code_agent/delivery_session.py` |
| Context compaction and pinned runtime blocks | `backend/app/application/context/compaction.py` |
| File working set across compaction (read/changed files, unchanged re-read) | `backend/app/application/code_agent/working_set.py` |
| Durable run state and Resume | `backend/app/application/code_agent/run_journal.py` |
| Planning | `backend/app/application/code_agent/planning.py` |
| Prompts/schemas | `backend/app/application/code_agent/prompts.py`, `tool_schemas.py` |
| Capability groups and compatibility/evidence hints | `backend/app/application/code_agent/capabilities.py` |
| Observed tool/web evidence | `backend/app/application/code_agent/run_evidence.py` |
| Model-owned task decisions, current checks and file-delivery receipts | `backend/app/application/code_agent/task_outcomes.py` |
| Built-in tool implementations | `backend/app/application/code_agent/tools/` |
| Deterministic local price-list/BOM validation | `backend/app/application/code_agent/tools/_bom.py` |
| Runtime control adapter | `backend/app/application/code_agent/tools/_runtime_control.py` |
| Skills folder (catalog, pinned SKILL.md, git history; seeds from `skills/`) | `backend/app/application/code_agent/task_skills.py` |
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
| Settings MCP lifecycle API | `backend/app/api/routes/mcp_routes.py` |
| Reprocenter operator pagination | `integrations/reprocenter/operator_pagination.py` |
| Tool inventory | `backend/app/application/tool_registry/` |
| LLM client | `backend/app/infrastructure/llm/openai_compatible.py` |
| Portable vault | `backend/app/infrastructure/secrets/vault.py` |
| IT Ops persistence | `backend/app/infrastructure/it_ops/store.py` |
| Durable SSH-only MikroTik onboarding/inventory | `backend/app/application/it_ops/mikrotik_registry.py`, `mikrotik_runtime.py` |
| Memory facade | `backend/app/application/memory/facade.py` |
| Model-written memory provenance and trust | `backend/app/application/memory/tool_provenance.py`, `policy.py` |
| Curated memory storage, origin-aware dedup and correction | `backend/app/application/smart_memory/store.py` |
| Library upload/import, full-text paging and bounded relevance context | `backend/app/application/library/runtime.py`, `api/routes/library_sqlite.py` |
| Project Corpus ingestion/recall | `backend/app/application/code_agent/indexing.py` + existing `application/rag_memory` |
| Durable finite-job recovery | `backend/app/application/code_agent/tools/_run.py`, `_background_jobs.py`, `_job_worker.py` |
| Core/integration/IT Ops live Harness + scripted Workflow/Stop/Resume + durable audit | `backend/tests/smokes/routing_eval.py`, `routing_cases.json`, `journal_audit.py`, `driver.py` |
| Deterministic memory Harness | `backend/tests/smokes/memory_eval.py` |
| SQLite helper | `backend/app/infrastructure/db/connection.py` |

Do not create another loop, executor, registry, provider stack or DB facade.
The seven code-agent modules are leaves of the existing coordinator. Result
accounting before a yield and criterion/message updates after it remain distinct
stages; see `docs/ARCHITECTURE.md` for the Workflow and cancellation boundaries.

The release state machine is `scripts/elira_release.py`. Its Windows service
adapter is `scripts/foundation_service.py`; `foundation_windows.py` owns Windows
tokens, processes and authenticated IPC, `foundation_storage.py` publishes
protected bytes, and `foundation_client.py` is the user CLI. The installer is
`scripts/install_foundation.ps1`. `check_foundation.py` and the proof-only
`foundation_fixture.py` exercise the isolated Windows installation. Candidate
backend/UI code remains separate. See `docs/RELEASE_LIFECYCLE.md` for deployment
status and the legacy launcher path.

## Frontend owners

```text
frontend/src
├─ App.tsx
├─ api/
│  ├─ codeAgent.ts       code-agent SSE/client types
│  ├─ chatFolders.ts     shared chat-folder state and atomic operations
│  ├─ workflows.ts       Workflow requests/events/UAC bridge
│  └─ project.ts         projects + multi-agent API
└─ workspace/
   ├─ WorkspaceShell.tsx application workspace
   ├─ Composer.tsx       permission/reasoning/multi-agent controls
   ├─ backgroundRuns.ts  one owner for live run state
   ├─ chatFolders.ts     server-confirmed folder store and legacy-cache migration
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
├─ rag_memory.db             facts, embeddings, Project Corpus chunks + manifest
├─ library.db
├─ projects.db
├─ web_corpus.sqlite3
├─ elira_state.db
├─ drift_facts.db
├─ portable_vault.json       encrypted vault; backup includes smart/rag memory
├─ background_jobs/          machine-local job journal/spec/launch/result sidecars
├─ skills/                   Elira's skills: <name>/SKILL.md, scripts, .venv; own git
├─ archive/                  retired data folders (e.g. skill_development, skill_advisor)
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
- `docs/ARCHITECTURE_SIMPLIFICATION_AUDIT_RU.md`: historical 2026-08-23 refactor snapshot.
- `docs/TASK_SKILLS.md`: package lifecycle, verified outcomes and learned hints.
- `docs/ANSWER_CONTRACT.md`: accepted answers, file delivery and web provenance.
- `docs/RELEASE_LIFECYCLE.md`: managed application updates and recovery limits.
- `docs/UI_BASELINE.md`: approved visual baseline.
- `docs/SERVER.md`: inference server boundary.

## Final repository gate

```powershell
npm --prefix frontend run typecheck
npm --prefix frontend run build
backend\.venv\Scripts\python.exe -m pytest -q
```
