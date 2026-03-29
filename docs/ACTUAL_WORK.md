# Actual Work

Live repair log for concrete backend/runtime fixes.

## 2026-03-29

### 1. Storage path repair for SQL / memory / RAG
- Status: completed
- Scope: unified the active storage path for `smart_memory`, `rag_memory`, `elira_state`, and run history under the rooted `data/` directory.
- Start: detected split-brain storage between `data/` and `backend/data/`.
- Finish: added [backend/app/core/data_files.py](/D:/AIWork/Elira_AI/backend/app/core/data_files.py) with rooted path resolution and safe legacy adoption from `backend/data/` when the rooted file is missing or effectively empty.
- Result:
  `smart_memory.db`, `rag_memory.db`, `elira_state.db`, and run history now resolve through one shared data root.
  Existing user data from `backend/data/` is adopted into the active rooted storage instead of being silently ignored.

### 2. smart_memory runtime repair
- Status: completed
- Scope: fixed broken memory search/runtime and made profile-scoped memory real.
- Start: `smart_memory` search/add/context routes were crashing because of broken regex and corrupted word-boundary patterns.
- Finish: rewrote [backend/app/services/smart_memory.py](/D:/AIWork/Elira_AI/backend/app/services/smart_memory.py) with:
  safe tokenization,
  repaired memory command detection,
  repaired category classification,
  SQLite schema migration for `profile_name`,
  profile-aware add/search/list/context/delete,
  profile stats and profile listing.
- Result:
  `/api/memory/add`, `/api/memory/search`, and `/api/memory/context/{profile}` work again.
  memory data is no longer mixed between different profiles.

### 3. Public memory API alignment
- Status: completed
- Scope: aligned the profile-aware route contract with the real storage layer.
- Start: [backend/app/services/memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/memory_service.py) normalized `profile` but ignored it in storage and filtering.
- Finish: updated [backend/app/services/memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/memory_service.py) to pass `profile` through all list/add/search/delete/context operations and to expose real profiles.
- Result:
  `/api/memory/items/default` and `/api/memory/items/other-profile` now return different data when profiles differ.

### 4. Chat state / settings SQL consistency
- Status: completed
- Scope: made `elira_state.db` self-healing and consistent across chat/settings access.
- Start: chat state could still drift, and older adopted databases could fail if `init_db()` had not been called first.
- Finish:
  updated [backend/app/services/elira_memory_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_memory_sqlite.py) to use rooted storage adoption and run `init_db()` on import;
  updated [backend/app/services/elira_settings_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_settings_sqlite.py) to rely on the same database and ensure the base schema exists before touching settings columns.
- Result:
  legacy chats/messages from `backend/data/elira_state.db` are visible again in the active app storage.

### 5. Run history SQL upgrade
- Status: completed
- Scope: removed JSON as the active source for run history and moved the live source to SQLite.
- Start: dashboard and run history still depended on `run_history.json`.
- Finish:
  rewrote [backend/app/services/run_history_service.py](/D:/AIWork/Elira_AI/backend/app/services/run_history_service.py) to store history in `run_history.db`,
  imported legacy JSON history into SQLite when needed,
  preserved route compatibility for existing readers,
  switched [backend/app/api/routes/dashboard_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/dashboard_routes.py) to use the service instead of reading JSON directly.
- Result:
  run history is now backed by SQLite, with legacy JSON auto-imported once into the active database.

### 6. Regression guard
- Status: completed
- Scope: added an isolated regression test for storage adoption and profile isolation.
- Finish: added [backend/tests/test_memory_storage_regression.py](/D:/AIWork/Elira_AI/backend/tests/test_memory_storage_regression.py).
- Result:
  test creates temporary `data` and `legacy-data`, verifies legacy adoption for SQL/JSON-backed state, and confirms that memory profiles stay isolated.

### 7. Verification
- Status: completed
- Checks:
  `python -m compileall backend/app`
  `python -m unittest discover -s backend/tests -p "test_*.py"`
  FastAPI import and OpenAPI generation
  direct route checks for `/api/memory/*` and `/api/dashboard/stats`
- Result:
  compile and tests passed;
  OpenAPI is still `187` routes / `179` paths;
  memory routes respond successfully;
  dashboard stats read from the SQL-backed run history service.

### 8. UI visual recovery and Russian localization
- Status: completed
- Scope: restored readable interface text, returned action icons to the UI, and replaced unstable emoji-style glyphs with safe SVG icons in the main shell and code workspace.
- Start: Tauri rendered several interface actions and labels as `?`, while part of the UI still had temporary ASCII placeholders and mixed English labels.
- Finish:
  updated [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) and [frontend/src/components/IdeWorkspaceShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/IdeWorkspaceShell.jsx);
  restored Russian labels for chat, tasks, Telegram, pipelines, dashboard, export menu, code workspace, library search, and settings;
  moved visible interface icons to `lucide-react` SVG icons to avoid Tauri font fallback and question-mark rendering.
- Result:
  the interface is readable again in the desktop app;
  critical action buttons display real icons instead of `?`;
  Russian UI labels are consistent across the main user-facing panels.
- Marker:
  current desktop UI baseline is marked as `идеальный визуал` for this stabilization wave.

### 9. UI verification
- Status: completed
- Checks:
  `npm --prefix frontend run build`
- Result:
  frontend build passed after the visual recovery patch.

## Next queued work

### A. Logging foundation
- Status: pending
- Target:
  access logging for all HTTP requests,
  audit logging for key actions,
  rotating file logs under `logs/`.

### B. Remaining storage normalization outside the repaired scope
- Status: pending
- Target:
  `response_cache`,
  `library.db`,
  other services still using relative `data/...` paths outside this repair wave.

### 10. Rooted Elira persona architecture
- Status: completed
- Scope: implemented one global Elira personality shared across all profiles and models, with versioning, quarantine-based learning, rollback, and dashboard visibility.
- Start: profile prompts still duplicated full personalities, persona state did not exist as a first-class store, and no backend/UI contract exposed the active personality version.
- Finish:
  added [backend/app/core/persona_defaults.py](/D:/AIWork/Elira_AI/backend/app/core/persona_defaults.py) as the clean Elira core plus profile mode overlays;
  added [backend/app/services/persona_service.py](/D:/AIWork/Elira_AI/backend/app/services/persona_service.py) with `persona_versions`, `persona_candidates`, `persona_learning_events`, `persona_model_calibrations`, and `persona_audit_log` inside `elira_state.db`;
  wired prompt composition in [backend/app/services/chat_service.py](/D:/AIWork/Elira_AI/backend/app/services/chat_service.py) and [backend/app/core/llm.py](/D:/AIWork/Elira_AI/backend/app/core/llm.py) so system prompts are now built as `active persona snapshot -> profile overlay -> model calibration -> runtime constraints`;
  connected runtime learning hooks in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) and [backend/app/core/agents.py](/D:/AIWork/Elira_AI/backend/app/core/agents.py);
  added persona routes in [backend/app/api/routes/persona.py](/D:/AIWork/Elira_AI/backend/app/api/routes/persona.py) and registered them in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py);
  updated [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js) and [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) so dashboard now shows `Личность Elira`, model consistency, quarantined candidates, and rollback action;
  preserved compatibility for existing `agent_profile` and `route_model_map` settings while normalizing default profile handling in [backend/app/services/elira_memory_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_memory_sqlite.py) and [backend/app/services/elira_settings_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_settings_sqlite.py).
- Result:
  Elira now has one rooted personality per local installation;
  profile switching changes mode, not identity;
  all dialogs can teach the system, but promotions only happen through quarantine, thresholds, version creation, and rollback.

### 11. Persona regression guards
- Status: completed
- Scope: added automated checks for the new persona API and lifecycle.
- Finish:
  extended [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py) with `/api/persona/status` and shape validation;
  updated [backend/tests/test_smoke_contract.py](/D:/AIWork/Elira_AI/backend/tests/test_smoke_contract.py);
  added [backend/tests/test_persona_service.py](/D:/AIWork/Elira_AI/backend/tests/test_persona_service.py) to verify bootstrap, learning-driven promotion, calibration persistence, and rollback.
- Result:
  persona architecture is now guarded by both smoke and unit tests, not only by manual runtime checks.
