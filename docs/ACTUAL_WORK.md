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

## 2026-03-30

### 12. Single runtime and rooted storage enforcement
- Status: completed
- Scope: removed runtime ambiguity between the rooted `data/` directory and the legacy `backend/data/` archive, and prevented launcher-level double backend startups.
- Start: the app could run against different processes and different state databases, which made chats, persona state, and visible behavior drift depending on which backend answered first.
- Finish:
  extended [backend/app/services/elira_memory_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_memory_sqlite.py) with append-only legacy chat migration and import tracking;
  added [backend/app/services/runtime_service.py](/D:/AIWork/Elira_AI/backend/app/services/runtime_service.py) and [backend/app/api/routes/runtime.py](/D:/AIWork/Elira_AI/backend/app/api/routes/runtime.py);
  registered runtime initialization in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py);
  added launcher preflight logic in [scripts/backend_preflight.ps1](/D:/AIWork/Elira_AI/scripts/backend_preflight.ps1), [Elira.bat](/D:/AIWork/Elira_AI/Elira.bat), [run_tauri_dev.bat](/D:/AIWork/Elira_AI/run_tauri_dev.bat), [Elira_Mobile.bat](/D:/AIWork/Elira_AI/Elira_Mobile.bat), and [scripts/run_backend.bat](/D:/AIWork/Elira_AI/scripts/run_backend.bat).
- Result:
  runtime now explicitly uses the rooted `data/` directory via `ELIRA_DATA_DIR`;
  legacy chats from `backend/data/elira_state.db` are imported append-only into the active rooted DB;
  launcher scripts reuse the repo backend on port `8000` instead of silently spawning duplicates, and they refuse to auto-start over a foreign process on the same port.

### 13. Strict Elira identity guard
- Status: completed
- Scope: stopped ordinary chat from revealing the underlying model as the assistant identity.
- Start: in normal user chat, Elira could answer as `Gemma` or describe herself as a large language model.
- Finish:
  strengthened persona rules in [backend/app/core/persona_defaults.py](/D:/AIWork/Elira_AI/backend/app/core/persona_defaults.py) and [backend/app/services/persona_service.py](/D:/AIWork/Elira_AI/backend/app/services/persona_service.py);
  added deterministic post-response identity protection in [backend/app/services/identity_guard.py](/D:/AIWork/Elira_AI/backend/app/services/identity_guard.py);
  integrated the guard into normal and streaming chat flows in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py).
- Result:
  on identity questions Elira now answers only as Elira;
  normal chat output no longer exposes `Gemma`, `Google DeepMind`, `LLM`, or similar model-self-identification phrases as the assistant persona;
  if a generated answer drifts, the backend rewrites or replaces the identity fragment before it is saved to history, cached, or shown as the final answer.

### 14. Runtime diagnostics in dashboard and regression coverage
- Status: completed
- Scope: exposed the active runtime/storage state in the UI and protected it with tests.
- Finish:
  added runtime fetching to [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js);
  added a runtime diagnostics card to [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx);
  extended [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py), [backend/tests/test_smoke_contract.py](/D:/AIWork/Elira_AI/backend/tests/test_smoke_contract.py), [backend/tests/test_persona_service.py](/D:/AIWork/Elira_AI/backend/tests/test_persona_service.py), and [backend/tests/test_memory_storage_regression.py](/D:/AIWork/Elira_AI/backend/tests/test_memory_storage_regression.py).
- Result:
  dashboard now shows which runtime is active, which `data_dir` it uses, whether a legacy archive still exists, and which persona version is active;
  smoke and unit tests now cover runtime status shape, append-only legacy chat migration, and identity-guard behavior.

### Comment
- Launcher behavior after this wave is intentionally strict:
  if port `8000` is already occupied by a foreign/system backend, startup now stops with a conflict message instead of silently launching a second backend over it.
- This is expected protective behavior, not a regression:
  the goal is to keep one runtime, one active DB, and one stable Elira identity source.

### Follow-up
- Launcher scripts were then upgraded again:
  if port `8000` already belongs to a process that answers as `elira-ai-api`, startup now auto-stops that stale Elira backend and starts a fresh one.
- Important:
  this auto-stop applies only to Elira's own backend health signature, not to arbitrary foreign services on port `8000`.

### 15. Final legacy-root removal and one-data-root cleanup
- Status: completed
- Scope: finished the migration from `backend/data` into the rooted `data/` directory and physically removed the legacy runtime root.
- Start: the code already preferred rooted storage, but the old `backend/data` tree still existed on disk and still contained library metadata, generated files, plugin artifacts, and empty integration/task/pipeline SQLite files.
- Finish:
  removed legacy adoption from [backend/app/core/data_files.py](/D:/AIWork/Elira_AI/backend/app/core/data_files.py) and normalized storage consumers to rooted paths in [backend/app/services/library_service.py](/D:/AIWork/Elira_AI/backend/app/services/library_service.py), [backend/app/services/autopipeline_service.py](/D:/AIWork/Elira_AI/backend/app/services/autopipeline_service.py), [backend/app/services/task_planner_service.py](/D:/AIWork/Elira_AI/backend/app/services/task_planner_service.py), [backend/app/services/telegram_service.py](/D:/AIWork/Elira_AI/backend/app/services/telegram_service.py), [backend/app/services/response_cache.py](/D:/AIWork/Elira_AI/backend/app/services/response_cache.py), [backend/app/services/plugin_system.py](/D:/AIWork/Elira_AI/backend/app/services/plugin_system.py), [backend/app/services/skills_service.py](/D:/AIWork/Elira_AI/backend/app/services/skills_service.py), [backend/app/services/skills_extra.py](/D:/AIWork/Elira_AI/backend/app/services/skills_extra.py), [backend/app/services/image_gen.py](/D:/AIWork/Elira_AI/backend/app/services/image_gen.py), [backend/app/services/pdf_pro.py](/D:/AIWork/Elira_AI/backend/app/services/pdf_pro.py), [backend/app/api/routes/library_sqlite.py](/D:/AIWork/Elira_AI/backend/app/api/routes/library_sqlite.py), [backend/app/api/routes/file_ops.py](/D:/AIWork/Elira_AI/backend/app/api/routes/file_ops.py), [backend/app/api/routes/terminal.py](/D:/AIWork/Elira_AI/backend/app/api/routes/terminal.py), and related routes;
  migrated the remaining useful legacy payload into [data](/D:/AIWork/Elira_AI/data): copied `autopipelines.db`, `task_planner.db`, `integrations.db`, `plugins_config.json`, merged `library.db` with path normalization into `data/uploads`, copied `generated/*`, and preserved the conflicting legacy plugin as [example_hello.legacy-import.py](/D:/AIWork/Elira_AI/data/plugins/example_hello.legacy-import.py);
  deleted the test seed `RAG alpha memory` from [rag_memory.db](/D:/AIWork/Elira_AI/data/rag_memory.db) and cleaned [backend/app/services/rag_memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/rag_memory_service.py) so RAG context is internal prompt material, not raw user-facing `[fact]` text;
  removed the physical `D:\AIWork\Elira_AI\backend\data` directory after the merge.
- Result:
  the project now has exactly one runtime root: [data](/D:/AIWork/Elira_AI/data);
  dashboard/runtime diagnostics no longer need to describe `backend/data` as a normal state;
  library records point to rooted uploads under `data/uploads`;
  raw `RAG alpha memory` leakage is removed both from the active SQLite file and from prompt formatting;
  `/api/runtime/status` no longer exposes `legacy_data_dir`, `legacy_db_path`, `legacy_db_exists`, or `legacy_chat_count`, because the legacy root is gone rather than merely hidden.

### 16. Dynamic temporal internet mode and hidden provenance
- Status: completed
- Scope: replaced brittle year-based web triggers with dynamic temporal detection and stopped ordinary chat from exposing raw memory/RAG provenance.
- Start: current-world questions could still depend on hardcoded year checks, and ordinary replies could leak `[fact]`, `RAG`, or memory/source phrasing into the visible answer.
- Finish:
  added [backend/app/services/temporal_intent.py](/D:/AIWork/Elira_AI/backend/app/services/temporal_intent.py) and rebuilt [backend/app/services/planner_v2_service.py](/D:/AIWork/Elira_AI/backend/app/services/planner_v2_service.py) so the planner now classifies requests as `hard`, `soft`, `stable_historical`, or `none` based on any explicit year, relative-time phrases, and current-world signals instead of literal `2024/2025/2026` triggers;
  added [backend/app/services/provenance_guard.py](/D:/AIWork/Elira_AI/backend/app/services/provenance_guard.py) and integrated it into [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) after the identity guard for normal, streaming, and cached responses;
  updated [backend/app/services/response_cache.py](/D:/AIWork/Elira_AI/backend/app/services/response_cache.py) so temporal/freshness-sensitive prompts are not cached as stable knowledge;
  updated [backend/app/services/smart_memory.py](/D:/AIWork/Elira_AI/backend/app/services/smart_memory.py) and [backend/app/services/rag_memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/rag_memory_service.py) to stop formatting internal context as raw `[fact]` or `Relevant user memory` blocks;
  added [backend/tests/test_temporal_internet_mode.py](/D:/AIWork/Elira_AI/backend/tests/test_temporal_internet_mode.py) to lock future-year routing, stable historical behavior, cache freshness rules, provenance cleanup, and hidden memory formatting.
- Result:
  temporal/current-world requests now trigger web-enabled planning without hardcoding a specific calendar year;
  stable historical questions such as past-year event lookups are no longer forced into mandatory web-search just because they start with `что` or `what`;
  normal chat output is post-processed to remove raw `[fact]`, `RAG`, and technical memory/source markers, while provenance questions are rewritten into natural language instead of internal prompt jargon;
  internet is now treated as a freshness-aware second knowledge base in the planning layer, while ordinary answers stay human-style by default instead of becoming a link dump.

### 17. WebSearch hardening: Tavily + DuckDuckGo + Wikipedia stack
- Status: completed
- Scope: removed the degraded `Google/Bing/Yandex/SearXNG/Brave` stack from active runtime orchestration and locked `Tavily` as the primary web layer, with `DuckDuckGo/DDG News` as fallback and `Wikipedia` as the knowledge layer.
- Start: the runtime still behaved mostly like `DuckDuckGo` plus leftovers from older HTML-scraping engines, while diagnostics and `/api/web/*` still advertised engines that were no longer reliable in practice.
- Finish:
  rewrote [backend/app/core/web.py](/D:/AIWork/Elira_AI/backend/app/core/web.py) around `SUPPORTED_SEARCH_ENGINES = ("tavily", "duckduckgo", "wikipedia")`, provider health, API-key-aware failover, Tavily deep-search, DDG fallback, and Wikipedia knowledge fallback;
  updated [backend/app/services/web_service.py](/D:/AIWork/Elira_AI/backend/app/services/web_service.py), [backend/app/services/web_multisearch_service.py](/D:/AIWork/Elira_AI/backend/app/services/web_multisearch_service.py), [backend/app/api/routes/web_search_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/web_search_routes.py), and [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) so current-world and deep-search orchestration now prefer `Tavily` with `DuckDuckGo` fallback, stable historical lookups can prefer `Wikipedia`, and raw URLs/engine labels are no longer injected into normal prompt-context blocks;
  extended [backend/app/services/runtime_service.py](/D:/AIWork/Elira_AI/backend/app/services/runtime_service.py), [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js), and [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) with live web diagnostics such as `primary_engine`, `fallback_engines`, `available_engines`, API-key presence, degraded mode, and runtime warnings;
  expanded regression coverage in [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py) and added [backend/tests/test_web_engine_stack.py](/D:/AIWork/Elira_AI/backend/tests/test_web_engine_stack.py);
  wired launcher-side local secret loading through ignored `backend/.env.local` so Tavily can be enabled on the local machine without committing API keys.
- Result:
  Elira now has one explicit web engine contract: `Tavily` when the key is present, `DuckDuckGo + Wikipedia` when it is missing;
  `Google`, `Bing`, `Yandex`, `SearXNG`, and `Brave` are no longer part of active runtime defaults or `/api/web/engines`;
  dashboard/runtime diagnostics show the real search stack instead of a fake multi-search catalog;
  ordinary answers remain human-style while temporal/current-world search can still go deeper when the first pass is weak.

### 18. Tavily failover hardening
- Status: completed
- Scope: verified that exhausted or rejected Tavily requests do not stop search and cleaned the fallback path so Tavily error rows do not leak into ordinary web results.
- Finish:
  hardened [backend/app/core/web.py](/D:/AIWork/Elira_AI/backend/app/core/web.py) so engine failures are logged internally instead of being injected back as synthetic search results;
  extended [backend/tests/test_web_engine_stack.py](/D:/AIWork/Elira_AI/backend/tests/test_web_engine_stack.py) with a regression check for `402`-style Tavily failure and clean fallback to `DuckDuckGo`.
- Result:
  if Tavily rejects the request or runs out of credits, Elira continues through `DuckDuckGo + Wikipedia` instead of breaking the search flow;
  ordinary chat and web pipelines no longer receive fake `Search error (tavily)` rows as if they were real sources;
  failover remains automatic while the Tavily key exists locally.

### 19. Local Tavily wiring and operator notes
- Status: completed
- Scope: documented how Tavily is connected on the local machine and what to expect in runtime after the key is enabled.
- Finish:
  connected local launcher-side secret loading through [Elira.bat](/D:/AIWork/Elira_AI/Elira.bat), [run_tauri_dev.bat](/D:/AIWork/Elira_AI/run_tauri_dev.bat), [Elira_Mobile.bat](/D:/AIWork/Elira_AI/Elira_Mobile.bat), and [scripts/run_backend.bat](/D:/AIWork/Elira_AI/scripts/run_backend.bat);
  stored the local key in ignored [backend/.env.local](/D:/AIWork/Elira_AI/backend/.env.local) and protected it with [.gitignore](/D:/AIWork/Elira_AI/.gitignore);
  kept Tavily integration on direct HTTP requests in [backend/app/core/web.py](/D:/AIWork/Elira_AI/backend/app/core/web.py), so no separate Tavily desktop app and no `pip install tavily-python` are required for the current implementation.
- Result:
  the active search chain is now `Tavily -> DuckDuckGo -> Wikipedia`;
  if Tavily credits are exhausted or Tavily returns `401/402/429`, Elira keeps searching through the fallback chain instead of losing web search;
  the local key is runtime-only and should not be committed into git or copied into docs;
  the runtime card still infers `primary_engine` from configured availability, not from Tavily billing state, so live credit exhaustion can still show `tavily` in diagnostics even though the real query already fell back to `DuckDuckGo + Wikipedia`.

### 20. Internal time awareness without unsolicited date/time replies
- Status: completed
- Scope: kept Elira aware of current local date/time internally, but stopped ordinary chat from blurting out the current date or time unless the user explicitly asks.
- Start: the backend prompt builder in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) prepended a visible `Сейчас: ...` line to every chat prompt, which encouraged replies like `Сегодня понедельник... и сейчас 4:19` even in normal greetings.
- Finish:
  replaced the always-visible prompt line with an internal runtime datetime context in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py);
  added an explicit detector for direct date/time questions such as `какая сегодня дата`, `какое сегодня число`, `который час`, and `сколько времени`;
  changed the prompt rules so ordinary chat must not mention the current date, time, or weekday unless the user directly asked for them, while direct date/time questions still receive a precise natural answer;
  added regression coverage in [backend/tests/test_runtime_datetime_prompt.py](/D:/AIWork/Elira_AI/backend/tests/test_runtime_datetime_prompt.py).
- Result:
  Elira now keeps local runtime date/time as internal awareness rather than a default visible greeting element;
  normal prompts like `Привет` no longer need to trigger date/time small talk;
  direct questions such as `Какая сегодня дата?` or `Который час?` still get an exact answer using current local runtime time;
  backend verification for this change passed with `compileall`, targeted unit tests, full backend test discovery, and smoke-contract checks.

### 21. Draft-first chat creation in the sidebar
- Status: completed
- Scope: changed startup chat UX so opening Elira no longer auto-creates a new visible chat in the sidebar before the user actually starts a conversation.
- Start: the shell created a new sidebar chat immediately on startup, which made the left panel fill up with empty conversations even before the first user message.
- Finish:
  updated [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) so app bootstrap opens into an empty draft state instead of forcing a new persisted chat on launch;
  changed send flow to materialize the chat only when the first message is actually submitted, while keeping the `Новый чат` button as an explicit independent action;
  updated [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js) so message creation and chat creation stay aligned with the backend response shape when a draft becomes a real chat.
- Result:
  startup now opens into a clean empty draft without polluting the sidebar;
  the first user message creates the real chat automatically and only then makes it appear in the chat list;
  the `Новый чат` button still creates a separate new chat immediately when the user wants that behavior explicitly.
