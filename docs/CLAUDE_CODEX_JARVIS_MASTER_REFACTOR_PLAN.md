# Jarvis / Elira — Master Refactor Plan for Claude and Codex

Version: 1.0  
Date: 2026-04-10  
Audience: Claude Code, Codex, and human reviewer  
Status: Execution-ready planning document

---

## 1. Purpose

This document is the **single source of truth** for the architectural refactor of the current Jarvis / Elira project.

The goal is **not** to rewrite the whole product from scratch. The goal is to:

- stabilize the codebase,
- break backend and frontend monoliths into bounded modules,
- move the frontend to TypeScript,
- centralize persistence and runtime concerns,
- keep Python for AI/backend logic,
- keep Rust only for Tauri shell/process isolation,
- preserve product behavior during migration.

This plan is written so that **Claude or Codex can execute it incrementally** without inventing architecture on the fly.

---

## 2. Executive Decision Summary

### Keep
- **Python** for backend, AI orchestration, workflows, agents, tools, memory, RAG, code execution orchestration.
- **Rust** for Tauri shell, process management, path/runtime utilities, optional secure sandbox helpers.

### Change
- **Frontend must migrate from JSX/JavaScript to TypeScript/TSX**.
- Massive service files must be split into **application/domain/infrastructure** boundaries.
- Direct SQLite calls must be centralized behind repositories.
- Route surface must be reduced to bounded contexts.
- Legacy modules must be frozen and converted into temporary facades.

### Do not do now
- Do **not** rewrite the backend in Go.
- Do **not** rewrite the backend in Rust.
- Do **not** move product business logic into Tauri.
- Do **not** attempt a big-bang rewrite.

---

## 3. Current State Snapshot (Observed)

### Largest files
- `backend/app/core/agents.py` — **2865 lines**
- `backend/app/services/agents_service.py` — **2571 lines**
- `frontend/src/components/EliraChatShell.jsx` — **2362 lines**
- `backend/app/services/workflow_engine.py` — **1882 lines**
- `backend/app/services/code_agent_service.py` — **1641 lines**
- `frontend/src/components/IdeWorkspaceShell.jsx` — **1218 lines**
- `frontend/src/api/ide.js` — **940 lines**
- `backend/app/api/routes/project_brain.py` — **796 lines**

### Concrete structural problems
- Duplicate function definitions in `backend/app/services/agents_service.py`:
  - `_do_web_search` defined twice
  - `_do_temporal_web_search` defined twice
- Duplicate function definition in `backend/app/core/agents.py`:
  - `run_multi_agent` defined twice
- Backend route modules: **36** files in `backend/app/api/routes`
- Direct `sqlite3.connect(...)` usage: **24** occurrences across backend
- `frontend/src/components/EliraChatShell.jsx` contains roughly:
  - **64 `useState(...)`** calls
  - **12 `useEffect(...)`** calls
- Tauri shell has duplicate/ambiguous entrypoint layout:
  - `src-tauri/main.rs`
  - `src-tauri/src/main.rs`
- Evidence of encoding / mojibake issues in parts of repo

### Interpretation
The main bottleneck is **not language choice**. The main bottleneck is:
- oversized modules,
- weak architectural boundaries,
- duplicate logic,
- direct persistence access from many places,
- frontend state sprawl,
- route/service explosion,
- hidden coupling.

---

## 4. Primary Refactor Goals

1. **Reduce file size and responsibility overlap**.
2. **Create clear domain boundaries**.
3. **Introduce stable contracts between frontend and backend**.
4. **Make the codebase safe for iterative AI-assisted changes**.
5. **Preserve behavior while moving functionality to new modules**.
6. **Enable faster testing, onboarding, and feature delivery**.

---

## 5. Non-Goals

- No full rewrite.
- No change of product concept.
- No migration to a different desktop framework.
- No change to external model provider strategy unless required by module extraction.
- No broad redesign of prompts or agent behavior in the same phase as architecture stabilization.

---

## 6. Target Architecture

---

### 6.1 Backend target structure

```text
backend/app/
  api/
    routers/
      chat.py
      code_agent.py
      workflows.py
      memory.py
      library.py
      runtime.py
      integrations.py

  application/
    chat/
      service.py
      stream_service.py
      context_builder.py
    code_agent/
      service.py
      run_service.py
      verify_service.py
      diff_service.py
    workflows/
      engine.py
      runner.py
      registry.py
    memory/
      service.py
    library/
      service.py

  domain/
    agents/
      orchestrator.py
      planner.py
      router.py
      prompts.py
      events.py
    tools/
      browser_tool.py
      terminal_tool.py
      python_tool.py
      file_tool.py
    runtime/
      sandbox.py
      execution_policy.py

  infrastructure/
    db/
      connection.py
      repositories/
        chats.py
        messages.py
        memory.py
        workflows.py
        registry.py
    llm/
      client.py
      model_router.py
    search/
      web_search.py
      query_planner.py
    storage/
      files.py
      library.py

  schemas/
  core/
    config.py
    logging.py
```

### 6.2 Frontend target structure

```text
frontend/src/
  app/
    providers/
    router/
    store/

  features/
    chat/
      api/
      model/
      ui/
    code-agent/
      api/
      model/
      ui/
    workflows/
      api/
      model/
      ui/
    memory/
      api/
      model/
      ui/

  entities/
    chat/
    message/
    artifact/
    project/

  shared/
    api/
      client.ts
      generated/
    lib/
    hooks/
    ui/
    types/
```

### 6.3 Tauri target structure

```text
src-tauri/
  src/
    main.rs
    commands.rs
    process_manager.rs
    paths.rs
```

---

## 7. Architectural Rules

These rules are mandatory for all Claude/Codex changes.

### 7.1 Size constraints
- Preferred module size: **150–350 lines**
- Upper soft limit for services: **500 lines**
- Upper hard limit for orchestration-heavy modules: **700 lines**
- React components: target **<250 lines**, hard cap **400 lines**

### 7.2 Dependency direction
- `api` may depend on `application`, `schemas`, `core`
- `application` may depend on `domain`, `infrastructure`, `schemas`
- `domain` must not depend on `api`
- `infrastructure` must not import UI or route code
- frontend `ui` should not directly embed networking logic when feature API modules exist

### 7.3 Persistence rules
- No new direct `sqlite3.connect(...)` outside `infrastructure/db/connection.py` or repository constructors.
- Any new DB access must go through repositories or a unit-of-work style gateway.

### 7.4 Legacy migration rules
- Legacy files may temporarily remain as facades.
- New functionality must not be added into frozen legacy monoliths.
- Every extraction must keep old public behavior stable.

### 7.5 Contract rules
- Backend responses must use Pydantic models or equivalent schema models.
- Frontend API typing must come from shared definitions or generated types.

---

## 8. Old-to-New Module Mapping

### 8.1 Backend monolith mapping

#### `backend/app/core/agents.py`
Move into:
- `backend/app/domain/agents/orchestrator.py`
- `backend/app/domain/agents/router.py`
- `backend/app/domain/agents/planner.py`
- `backend/app/domain/agents/events.py`
- `backend/app/domain/tools/python_tool.py`
- `backend/app/domain/tools/browser_tool.py`
- `backend/app/domain/runtime/sandbox.py`

#### `backend/app/services/agents_service.py`
Move into:
- `backend/app/application/chat/service.py`
- `backend/app/application/chat/stream_service.py`
- `backend/app/application/chat/context_builder.py`
- `backend/app/infrastructure/search/web_search.py`
- `backend/app/domain/agents/router.py`

#### `backend/app/services/code_agent_service.py`
Move into:
- `backend/app/application/code_agent/service.py`
- `backend/app/application/code_agent/run_service.py`
- `backend/app/application/code_agent/verify_service.py`
- `backend/app/application/code_agent/diff_service.py`
- `backend/app/domain/runtime/sandbox.py`

#### `backend/app/services/workflow_engine.py`
Move into:
- `backend/app/application/workflows/engine.py`
- `backend/app/application/workflows/runner.py`
- `backend/app/application/workflows/registry.py`
- `backend/app/infrastructure/db/repositories/workflows.py`

### 8.2 Frontend monolith mapping

#### `frontend/src/components/EliraChatShell.jsx`
Move into:
- `frontend/src/features/chat/ui/ChatShell.tsx`
- `frontend/src/features/chat/ui/ChatSidebar.tsx`
- `frontend/src/features/chat/ui/ChatViewport.tsx`
- `frontend/src/features/chat/ui/Composer.tsx`
- `frontend/src/features/chat/model/chatStore.ts`
- `frontend/src/features/chat/model/useChatStreaming.ts`
- `frontend/src/features/chat/model/useChatSession.ts`

#### `frontend/src/components/IdeWorkspaceShell.jsx`
Move into:
- `frontend/src/features/code-agent/ui/WorkspaceShell.tsx`
- `frontend/src/features/code-agent/model/workspaceStore.ts`
- `frontend/src/features/code-agent/model/useWorkspaceRuntime.ts`

#### `frontend/src/api/ide.js`
Move into:
- `frontend/src/shared/api/client.ts`
- `frontend/src/features/chat/api/chat.ts`
- `frontend/src/features/code-agent/api/codeAgent.ts`
- `frontend/src/features/workflows/api/workflows.ts`
- `frontend/src/shared/types/api.ts`

### 8.3 Tauri mapping

#### `src-tauri/main.rs` + `src-tauri/src/main.rs`
Keep only:
- `src-tauri/src/main.rs`

Extract helpers into:
- `src-tauri/src/commands.rs`
- `src-tauri/src/process_manager.rs`
- `src-tauri/src/paths.rs`

---

## 9. Refactor Program — Phase by Phase

---

### Phase 0 — Preparation and Guardrails

#### Objective
Create safety nets so the codebase can be moved without uncontrolled regressions.

#### Tasks
1. Add or tighten lint/type gates:
   - backend: `ruff`, optional `mypy` in gradual mode
   - frontend: `eslint`, `typescript`, `prettier`
   - repo: `pre-commit`
2. Record current behavior with smoke tests:
   - chat send/receive
   - code agent run
   - workflows basic execution
   - file/library basic read path
3. Freeze legacy files with clear headers:
   - `core/agents.py`
   - `services/agents_service.py`
   - `services/code_agent_service.py`
   - `services/workflow_engine.py`
4. Add architecture README files at new module boundaries.
5. Normalize UTF-8 encoding where safe.

#### Deliverables
- lint commands committed
- smoke tests committed
- legacy freeze comments added
- scaffold directories created

#### Exit criteria
- current app still runs
- CI checks run locally
- no new code is added to frozen monoliths

---

### Phase 1 — Stabilize Backend Boundaries

#### Objective
Stop architectural drift and remove the most dangerous duplications.

#### Tasks
1. Remove duplicate function definitions:
   - deduplicate `_do_web_search`
   - deduplicate `_do_temporal_web_search`
   - deduplicate `run_multi_agent`
2. Create `infrastructure/db/connection.py`.
3. Add repository scaffolds:
   - chats
   - messages
   - memory
   - workflows
   - registry
4. Start routing DB access through repositories for touched code.
5. Reduce `main.py` to app assembly only.
6. Group route registration by bounded contexts.

#### Deliverables
- unique backend behavior for duplicated functions
- shared DB connection/provider layer
- initial repository layer
- simplified app wiring

#### Exit criteria
- no duplicate definitions remain
- all new DB code goes through shared DB layer
- application still passes smoke tests

---

### Phase 2 — Split Chat/Agent Services

#### Objective
Break `agents_service.py` and `core/agents.py` into coherent layers.

#### Extract from `agents_service.py`
- request orchestration
- context building
- streaming response logic
- web context enrichment
- attachment context resolution
- model selection / agent routing

#### Extract from `core/agents.py`
- agent orchestration
- planning
- multi-agent coordination
- tool execution wrappers
- runtime events

#### New target modules
```text
backend/app/application/chat/service.py
backend/app/application/chat/stream_service.py
backend/app/application/chat/context_builder.py
backend/app/domain/agents/orchestrator.py
backend/app/domain/agents/router.py
backend/app/domain/agents/planner.py
backend/app/domain/agents/events.py
backend/app/infrastructure/search/web_search.py
```

#### Deliverables
- old chat routes call new application services
- orchestration logic is no longer mixed with route/persistence glue
- tool and planner logic moved away from API/service monoliths

#### Exit criteria
- `agents_service.py` shrinks to facade size or is retired
- `core/agents.py` shrinks below 1000 lines or is fully replaced
- chat flow remains functionally equivalent

---

### Phase 3 — Split Code Agent Runtime

#### Objective
Separate code-agent concerns into explicit runtime services.

#### Concerns to isolate
- run lifecycle
- workspace discovery
- patch/diff generation
- verify/build/test
- sandbox/policy execution
- cancellation and status updates

#### New target modules
```text
backend/app/application/code_agent/service.py
backend/app/application/code_agent/run_service.py
backend/app/application/code_agent/verify_service.py
backend/app/application/code_agent/diff_service.py
backend/app/domain/runtime/sandbox.py
backend/app/domain/runtime/execution_policy.py
```

#### Deliverables
- main code-agent API routes call small services
- verify/build/test separated from orchestration
- sandbox rules localized

#### Exit criteria
- `code_agent_service.py` becomes facade or disappears
- runtime paths are testable in isolation

---

### Phase 4 — Split Workflow Engine

#### Objective
Turn workflow execution into modular application services with clear persistence boundaries.

#### Concerns to isolate
- workflow registry
- workflow execution engine
- state transitions
- built-in workflow definitions
- run history persistence

#### New target modules
```text
backend/app/application/workflows/engine.py
backend/app/application/workflows/runner.py
backend/app/application/workflows/registry.py
backend/app/infrastructure/db/repositories/workflows.py
```

#### Deliverables
- workflow route layer becomes thin
- persistence separated from execution
- registry and runtime behavior documented

#### Exit criteria
- `workflow_engine.py` no longer contains mixed concerns
- workflow runs can be unit tested without route scaffolding

---

### Phase 5 — Route Consolidation

#### Objective
Reduce route sprawl and align API surface with domain boundaries.

#### Current state
There are 36 route modules. This is too fragmented for the current project scale.

#### Target bounded contexts
- `chat.py`
- `code_agent.py`
- `workflows.py`
- `memory.py`
- `library.py`
- `runtime.py`
- `integrations.py`

#### Consolidation strategy
Map current routes into contexts:

##### Chat / agents context
- `agents.py`
- `chat.py`
- parts of `models.py`
- parts of `web_search_routes.py`

##### Code-agent/runtime context
- `code_agent_routes.py`
- `terminal.py`
- `tools_exec.py`
- `git_routes.py`
- runtime-related parts of `debug.py`

##### Memory/library context
- `memory.py`
- `smart_memory_routes.py`
- `library.py`
- `library_sqlite.py`
- parts of `project_brain.py`

##### Integrations context
- `telegram_routes.py`
- `image_routes.py`
- `pdf_routes.py`
- skills/tool registry related routes

##### Workflow context
- `workflow_routes.py`
- `autopipeline_routes.py`
- `task_planner_routes.py`

#### Exit criteria
- route count materially reduced
- shared response models reused
- no route file becomes another giant monolith

---

### Phase 6 — Frontend TypeScript Migration

#### Objective
Move the frontend from brittle shell components to typed feature modules.

#### Step 1 — Enable TS incrementally
- add `tsconfig.json`
- allow mixed `.jsx` + `.tsx` temporarily
- add `vite-env.d.ts` or equivalent
- wire strictness progressively

#### Step 2 — Extract API layer
Create:
```text
frontend/src/shared/api/client.ts
frontend/src/shared/types/api.ts
frontend/src/features/chat/api/chat.ts
frontend/src/features/code-agent/api/codeAgent.ts
frontend/src/features/workflows/api/workflows.ts
```

#### Step 3 — Split state concerns
- server state: `TanStack Query`
- local UI state: `Zustand`
- transient UI behavior: local hooks

#### Step 4 — Split `EliraChatShell.jsx`
Create:
```text
frontend/src/features/chat/ui/ChatShell.tsx
frontend/src/features/chat/ui/ChatHeader.tsx
frontend/src/features/chat/ui/ChatSidebar.tsx
frontend/src/features/chat/ui/ChatViewport.tsx
frontend/src/features/chat/ui/Composer.tsx
frontend/src/features/chat/model/chatStore.ts
frontend/src/features/chat/model/useChatStreaming.ts
frontend/src/features/chat/model/useChatSession.ts
```

#### Step 5 — Split `IdeWorkspaceShell.jsx`
Create:
```text
frontend/src/features/code-agent/ui/WorkspaceShell.tsx
frontend/src/features/code-agent/ui/WorkspaceHeader.tsx
frontend/src/features/code-agent/ui/WorkspaceTabs.tsx
frontend/src/features/code-agent/model/workspaceStore.ts
frontend/src/features/code-agent/model/useWorkspaceRuntime.ts
```

#### Exit criteria
- no new logic added to legacy JSX shells
- `ide.js` removed or reduced to shim
- key feature screens are TSX and typed

---

### Phase 7 — Tauri Cleanup

#### Objective
Keep Tauri minimal and reliable.

#### Tasks
1. Keep one entrypoint: `src-tauri/src/main.rs`
2. Consolidate shell commands into `commands.rs`
3. Centralize backend process management in `process_manager.rs`
4. Centralize path resolution in `paths.rs`
5. Avoid pushing business logic into Rust
6. Add healthcheck/restart strategy for backend process

#### Exit criteria
- one clear startup path
- one clear shutdown path
- path and process logic isolated

---

### Phase 8 — Contract Stabilization and Cleanup

#### Objective
Finish the migration by removing compatibility debt.

#### Tasks
1. Generate or formalize shared API types
2. Remove dead legacy wrappers
3. Remove direct sqlite usage from remaining app code
4. Remove duplicated helpers superseded by structured modules
5. Fix lingering encoding issues
6. Add architecture documentation for maintainers

#### Exit criteria
- legacy facades are minimal or deleted
- typed contracts are stable
- core flows are covered by smoke tests and targeted unit tests

---

## 10. Detailed Backend Work Items

### 10.1 Chat / Agent domain
Extract these concerns from current monoliths:
- request normalization
- attachment context collection
- memory context enrichment
- web context planning and execution
- model/provider selection
- stream and non-stream response paths
- event emission and monitoring hooks
- prompt assembly boundaries

### 10.2 Search layer
Create dedicated search infrastructure:
- `web_search.py` for actual external query execution
- `query_planner.py` for search intent decomposition
- no search helper duplication in service monoliths

### 10.3 Runtime / sandbox
Unify:
- shell execution policy
- Python runner integration
- timeouts
- workspace boundaries
- safe file access rules
- cancellation hooks

### 10.4 Persistence
Introduce repository methods such as:
- `ChatsRepository.create_session(...)`
- `MessagesRepository.append_message(...)`
- `MemoryRepository.get_context(...)`
- `WorkflowsRepository.save_run(...)`
- `RegistryRepository.list_tools(...)`

---

## 11. Detailed Frontend Work Items

### 11.1 Shared client
Build a single HTTP abstraction in `shared/api/client.ts`:
- base URL resolution
- error normalization
- JSON parsing
- typed response wrappers
- optional stream support helpers

### 11.2 Feature APIs
Each feature owns its endpoint wrappers:
- chat
- code-agent
- workflows
- memory

### 11.3 State policy
Use this split consistently:
- query cache / server synchronization → `TanStack Query`
- cross-component UI state → `Zustand`
- local interaction state → component-local `useState`

### 11.4 UI policy
- container components call hooks and render structure
- presentational components receive props and stay simple
- avoid single mega-shell components

---

## 12. Database Strategy

### Current issue
SQLite is accessed from too many places directly.

### Target
One shared DB gateway and repository layer.

### Required changes
- create `infrastructure/db/connection.py`
- define repository constructors around that connection provider
- keep schema/migrations centralized
- route legacy services through repositories incrementally

### Rule
Any file touched during refactor must not add new ad-hoc SQLite access.

---

## 13. API Contract Strategy

### Backend
- define request/response models under `schemas/`
- use clear typed envelopes where useful
- keep backward compatibility temporarily if frontend depends on old shapes

### Frontend
- create shared TS types first
- later optionally generate from OpenAPI
- stop passing untyped response blobs through giant components

---

## 14. Testing Strategy

### Must-have smoke tests
1. Chat request/response
2. Streaming chat
3. Code-agent execute or dry-run
4. Workflow run start/finish
5. Library or file retrieval
6. Tauri launches backend correctly

### Unit tests to add during extraction
- repository tests
- context builder tests
- routing/planner tests
- code-agent verify/diff tests
- workflow engine state transition tests

### Frontend tests
- API client behavior
- chat store behavior
- key hooks
- one or two Playwright smoke flows if practical

---

## 15. Quality Gates

### Backend
- `ruff check`
- `ruff format` or formatter of choice
- targeted `pytest`
- optional gradual `mypy`

### Frontend
- `eslint`
- `tsc --noEmit`
- build passes
- optional component tests

### Repo-wide
- no duplicate function definitions
- no new giant files
- no new direct DB connections outside approved layer

---

## 16. Suggested Milestone / Sprint Plan

### Sprint 1 — Guardrails and backend stabilization
- add quality gates
- deduplicate functions
- add DB connection/provider layer
- create repository scaffolds
- simplify main app assembly
- create new architecture skeleton

### Sprint 2 — Chat/agents and code-agent extraction
- split chat services
- split agent orchestration
- split code-agent runtime
- keep legacy wrappers calling new modules

### Sprint 3 — Workflows, route consolidation, frontend API migration
- split workflow engine
- reduce route sprawl
- create typed API layer in frontend
- start TS migration of shell components

### Sprint 4 — Frontend shell decomposition
- migrate chat shell to TSX
- migrate workspace shell to TSX
- introduce state stores/hooks/query split

### Sprint 5 — Cleanup
- remove dead legacy facades
- finish Tauri cleanup
- finish docs, tests, contract stabilization

---

## 17. Concrete Definition of Done

The refactor is considered complete when all of the following are true:

1. No core backend monolith exceeds agreed size limits.
2. `agents_service.py`, `core/agents.py`, `code_agent_service.py`, `workflow_engine.py` are removed or reduced to minimal facades.
3. Frontend main shells are decomposed and typed.
4. `frontend/src/api/ide.js` is replaced by typed feature API modules.
5. New DB writes/reads go through repositories.
6. Route layer is grouped by bounded contexts.
7. Tauri has one startup entrypoint path.
8. Smoke tests for core flows pass.
9. No duplicate function definitions remain.
10. No new code was added to legacy frozen monoliths during migration.

---

## 18. Risks and Mitigations

### Risk: hidden coupling in legacy services
**Mitigation:** extract one concern at a time, keep compatibility facades, add smoke tests first.

### Risk: frontend regressions during TS migration
**Mitigation:** keep mixed JS/TS phase temporarily, migrate API first, then hooks, then shells.

### Risk: route consolidation breaks callers
**Mitigation:** preserve old route paths until frontend is moved, then deprecate.

### Risk: SQLite behavior changes
**Mitigation:** keep schema stable initially, change only access layer first.

### Risk: AI agent starts rewriting too much at once
**Mitigation:** enforce small PR/task units and strict execution protocol below.

---

## 19. Execution Protocol for Claude and Codex

This section is mandatory.

### Global instructions
- Do not perform a full rewrite.
- Make **small, reviewable changes**.
- Prefer extraction and delegation over mutation.
- Preserve runtime behavior unless task explicitly says otherwise.
- After each step, leave imports/build/tests in a working state.
- Do not invent new abstractions unless they solve a current coupling problem.

### Change unit policy
Each implementation task should be scoped so it can usually fit into one PR or one AI run:
- one backend extraction,
- one repository introduction,
- one route consolidation,
- one frontend feature API extraction,
- one shell component split.

### Every task must include
1. files created
2. files modified
3. migration notes
4. verification steps
5. rollback notes if risky

### Forbidden behavior
- no mass renames without mapping
- no silent API changes
- no mixing architecture refactor with product redesign
- no moving everything into a new framework
- no adding new features into legacy monoliths

---

## 20. Claude Prompt Template

Use this when giving the plan to Claude.

```md
You are implementing a staged architecture refactor for an existing project.

Follow this plan strictly:
- Preserve behavior.
- Do not rewrite the whole project.
- Work in small steps.
- Prefer extraction into new modules and keep legacy files as temporary facades.
- Do not add new business logic into frozen monoliths.
- Respect these architecture layers: api / application / domain / infrastructure.
- For frontend, migrate from JS/JSX to TypeScript/TSX incrementally.
- For backend persistence, do not add new direct sqlite3.connect calls.
- After each task, output: files changed, summary, risks, verification commands.

Current priority task:
[PASTE ONE PHASE OR SUBTASK HERE]

Definition of success:
- build still works
- imports resolved
- no duplicate logic added
- task completed with minimal diff surface
```

---

## 21. Codex Prompt Template

Use this when giving the plan to Codex.

```md
Apply the following refactor task to the existing repository.

Constraints:
1. Keep behavior stable.
2. Make minimal necessary edits.
3. Create new modules where indicated and route existing code through them.
4. Leave legacy modules as facades when needed.
5. Do not add new direct DB access outside the shared DB/repository layer.
6. Keep files small and cohesive.
7. Do not change unrelated code.

For this task, do the following:
[PASTE ONE PHASE OR SUBTASK HERE]

When done, provide:
- summary of files changed
- why this matches the architecture plan
- any follow-up tasks
- quick verification steps
```

---

## 22. Task Breakdown for AI Execution

Below is the recommended exact order for Claude/Codex.

### Task 1
Create architecture skeleton folders and `__init__` files. Do not move logic yet.

### Task 2
Add `infrastructure/db/connection.py` and repository scaffolds.

### Task 3
Deduplicate `_do_web_search`, `_do_temporal_web_search`, and `run_multi_agent`.

### Task 4
Extract chat context builder from `agents_service.py`.

### Task 5
Extract chat streaming service from `agents_service.py`.

### Task 6
Extract web search helper logic into `infrastructure/search/web_search.py`.

### Task 7
Extract agent routing/planning from `core/agents.py`.

### Task 8
Extract code-agent verify/diff/runtime pieces from `code_agent_service.py`.

### Task 9
Extract workflow registry and workflow runner from `workflow_engine.py`.

### Task 10
Create `frontend/src/shared/api/client.ts` and move one endpoint group at a time from `ide.js`.

### Task 11
Create chat feature store/hooks and split `EliraChatShell.jsx` into TSX containers/components.

### Task 12
Create workspace feature store/hooks and split `IdeWorkspaceShell.jsx`.

### Task 13
Consolidate Tauri startup/process logic.

### Task 14
Remove or minimize legacy facades after migration.

---

## 23. Repository Standards to Add

### Suggested files
- `.editorconfig`
- `.gitattributes` with UTF-8-friendly settings where appropriate
- `pyproject.toml` lint config
- `frontend/tsconfig.json`
- `frontend/.eslintrc*`
- `.pre-commit-config.yaml`

### Suggested conventions
- UTF-8 everywhere
- explicit exports
- no wildcard imports in newly created architecture modules
- clear function naming by concern

---

## 24. Example PR / Patch Titles

- `refactor: add backend architecture skeleton and repository layer`
- `refactor: extract chat context builder from agents_service`
- `refactor: extract chat streaming service`
- `refactor: split agent orchestrator from core agents`
- `refactor: extract code-agent verify and diff services`
- `refactor: split workflow engine into runner and registry`
- `refactor: add typed frontend API client`
- `refactor: split EliraChatShell into TSX feature modules`
- `refactor: simplify tauri process startup`
- `chore: remove legacy facades after migration`

---

## 25. Final Recommendation

The correct radical move for this repository is **architectural decomposition**, not language replacement.

### Final stack recommendation
- **Backend:** Python + FastAPI + Pydantic + repository layer
- **Frontend:** React + TypeScript + TanStack Query + Zustand
- **Desktop shell:** Tauri + Rust

### Most important immediate actions
1. freeze legacy monoliths,
2. add shared DB layer,
3. split chat/agent orchestration,
4. split code-agent runtime,
5. migrate frontend API to TypeScript,
6. decompose giant shell components,
7. clean Tauri entrypoints.

This plan should be executed incrementally, with each change preserving behavior and shrinking the blast radius of future edits.

---

## 26. Quick Start Checklist

### For human reviewer
- [ ] approve target architecture
- [ ] approve backend/frontend language decisions
- [ ] approve repository pattern for SQLite
- [ ] approve TS migration for frontend
- [ ] approve phased rollout

### For Claude/Codex
- [ ] create skeleton
- [ ] add DB connection/repositories
- [ ] deduplicate duplicated functions
- [ ] extract chat modules
- [ ] extract code-agent modules
- [ ] extract workflow modules
- [ ] move frontend API to TS
- [ ] split shell components
- [ ] clean Tauri
- [ ] remove legacy facades

