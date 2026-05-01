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
  current desktop UI baseline is marked as `РёРґРµР°Р»СЊРЅС‹Р№ РІРёР·СѓР°Р»` for this stabilization wave.

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
  updated [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js) and [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) so dashboard now shows `Р›РёС‡РЅРѕСЃС‚СЊ Elira`, model consistency, quarantined candidates, and rollback action;
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
  stable historical questions such as past-year event lookups are no longer forced into mandatory web-search just because they start with `С‡С‚Рѕ` or `what`;
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
- Start: the backend prompt builder in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) prepended a visible `РЎРµР№С‡Р°СЃ: ...` line to every chat prompt, which encouraged replies like `РЎРµРіРѕРґРЅСЏ РїРѕРЅРµРґРµР»СЊРЅРёРє... Рё СЃРµР№С‡Р°СЃ 4:19` even in normal greetings.
- Finish:
  replaced the always-visible prompt line with an internal runtime datetime context in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py);
  added an explicit detector for direct date/time questions such as `РєР°РєР°СЏ СЃРµРіРѕРґРЅСЏ РґР°С‚Р°`, `РєР°РєРѕРµ СЃРµРіРѕРґРЅСЏ С‡РёСЃР»Рѕ`, `РєРѕС‚РѕСЂС‹Р№ С‡Р°СЃ`, and `СЃРєРѕР»СЊРєРѕ РІСЂРµРјРµРЅРё`;
  changed the prompt rules so ordinary chat must not mention the current date, time, or weekday unless the user directly asked for them, while direct date/time questions still receive a precise natural answer;
  added regression coverage in [backend/tests/test_runtime_datetime_prompt.py](/D:/AIWork/Elira_AI/backend/tests/test_runtime_datetime_prompt.py).
- Result:
  Elira now keeps local runtime date/time as internal awareness rather than a default visible greeting element;
  normal prompts like `РџСЂРёРІРµС‚` no longer need to trigger date/time small talk;
  direct questions such as `РљР°РєР°СЏ СЃРµРіРѕРґРЅСЏ РґР°С‚Р°?` or `РљРѕС‚РѕСЂС‹Р№ С‡Р°СЃ?` still get an exact answer using current local runtime time;
  backend verification for this change passed with `compileall`, targeted unit tests, full backend test discovery, and smoke-contract checks.

### 21. Draft-first chat creation in the sidebar
- Status: completed
- Scope: changed startup chat UX so opening Elira no longer auto-creates a new visible chat in the sidebar before the user actually starts a conversation.
- Start: the shell created a new sidebar chat immediately on startup, which made the left panel fill up with empty conversations even before the first user message.
- Finish:
  updated [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx) so app bootstrap opens into an empty draft state instead of forcing a new persisted chat on launch;
  changed send flow to materialize the chat only when the first message is actually submitted, while keeping the `РќРѕРІС‹Р№ С‡Р°С‚` button as an explicit independent action;
  updated [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js) so message creation and chat creation stay aligned with the backend response shape when a draft becomes a real chat.
- Result:
  startup now opens into a clean empty draft without polluting the sidebar;
  the first user message creates the real chat automatically and only then makes it appear in the chat list;
  the `РќРѕРІС‹Р№ С‡Р°С‚` button still creates a separate new chat immediately when the user wants that behavior explicitly.
### 22. N-intent web planner for 1-4+ current-world subtopics
- Status: completed
- Scope: generalized current-world web planning from a narrow `finance + local news` case into a true `N-intent` planner with overflow handling for `4+` live subtopics.
- Start: combined current-world prompts could already be split into two focused web searches, but anything beyond that still risked collapsing into a single search string or silently dropping extra live subtopics.
- Finish:
  rebuilt [backend/app/services/web_query_planner.py](/D:/AIWork/Elira_AI/backend/app/services/web_query_planner.py) into a general extractor that classifies subtopics as `finance`, `geo_news`, `general_news`, `status_current`, `price_rate`, `historical`, or `general_web`, merges same-intent finance fragments like `РєСѓСЂСЃ РґРѕР»Р»Р°СЂР° Рё РµРІСЂРѕ Рє С‚РµРЅРіРµ`, ranks subtopics by current-world priority, caps the total at `6`, and emits `passes`, `pass_count`, `overflow_applied`, and `uncovered_subqueries` in `web_plan`;
  extended the active multi-intent orchestration in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) so `_do_web_search` now executes `pass_1` and `pass_2` when needed, preserves partial success, emits richer `tool_results.web_search` metadata such as `passes`, `total_subqueries`, `overflow_applied`, and weak `uncovered_subqueries`, and keeps the final answer human-style instead of exposing planner/debug details;
  added regression coverage in [backend/tests/test_web_query_planner.py](/D:/AIWork/Elira_AI/backend/tests/test_web_query_planner.py) and [backend/tests/test_web_multi_intent_runtime.py](/D:/AIWork/Elira_AI/backend/tests/test_web_multi_intent_runtime.py), while keeping [backend/tests/test_temporal_internet_mode.py](/D:/AIWork/Elira_AI/backend/tests/test_temporal_internet_mode.py) and [backend/tests/test_web_engine_stack.py](/D:/AIWork/Elira_AI/backend/tests/test_web_engine_stack.py) green.
- Result:
  `1-3` current-world subtopics now run in one pass, while `4+` subtopics automatically use two web passes without asking the user to split the prompt manually;
  the runtime now exposes which subqueries went into `pass_1` and `pass_2`, whether overflow policy was applied, and which subtopics remained weak or uncovered;
  live verification of a `4`-subtopic prompt confirmed `2` passes with separate coverage for local incidents, finance, fuel price, and flight-status queries in one combined backend run.

### 23. Agent OS Phase 1 вЂ” Agent Registry with persistent state
- Status: completed
- Scope: built the foundation layer of the Agent OS вЂ” a persistent agent registry with identity, state, and run history tracking.
- Start: agents were stateless single-shot functions with hardcoded roles (Researcher, Programmer, Analyst), no persistent identity, no state between calls, and no inter-agent discoverability.
- Finish:
  added [backend/app/schemas/agent_registry.py](/D:/AIWork/Elira_AI/backend/app/schemas/agent_registry.py) with Pydantic models for agent definitions, state, run records, and API responses;
  added [backend/app/services/agent_registry.py](/D:/AIWork/Elira_AI/backend/app/services/agent_registry.py) with SQLite-backed CRUD (`data/agent_registry.db`), persistent agent state (JSON blob per agent), run history with duration/model/route tracking, builtin agent seeding from `AGENT_PROFILES`, and `resolve_agent()` for integration;
  added [backend/app/api/routes/agent_registry_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/agent_registry_routes.py) with REST endpoints under `/api/agent-os/agents/*` (register, list, get, update, delete, state CRUD, run history);
  integrated optional `agent_id` parameter into `run_agent()` in [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) вЂ” when provided, loads agent definition from registry, applies its system prompt and model preference, and records the run result (success or failure) with duration;
  registered the new router and builtin agent seeding in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py);
  added [backend/tests/test_agent_os_phase1.py](/D:/AIWork/Elira_AI/backend/tests/test_agent_os_phase1.py) with 15 tests covering CRUD, state persistence, run history, seed idempotency, and agent resolution вЂ” all passing.
- Result:
  agents now have persistent identity, discoverable via API, with state that survives between calls;
  every agent run can be tracked with input/output summary, route, model, and duration;
  builtin agents (Universal, Researcher, Programmer, Analyst, Socrat) are auto-seeded on startup;
  existing chat flow is fully backward-compatible вЂ” `agent_id` is optional;
  branch `feat/agent-os-phase1-registry` pushed to origin.
- Next phases planned:
  Phase 2 вЂ” Tool Registry with JSON Schema (replace hardcoded tool dispatch);
  Phase 3 вЂ” Event Bus + inter-agent messaging;
  Phase 4 вЂ” Workflow Engine (DAG-based multi-step orchestration);
  Phase 5 вЂ” Monitoring + Sandboxing.

### 24. Agent OS Phase 3 - Event Bus + inter-agent messaging
- Status: completed
- Scope: built the Phase 3 Event Bus layer on branch `feat/agent-os-phase3-eventbus`, using the shared coordination rules from [docs/AGENT_OS_WORKPLAN.md](/D:/AIWork/Elira_AI/docs/AGENT_OS_WORKPLAN.md) and keeping Phase 2 boundaries intact.
- Start:
  claimed Phase 3 in the shared workplan and moved implementation onto the dedicated phase branch;
  confirmed that Phase 1 was already wired in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py), while Phase 2 remained out of scope except for the future `tool.executed` stub hook;
  locked the implementation pattern to `schema -> service -> routes -> main.py -> tests`, matching the Phase 1 registry structure before touching runtime code.
- Finish:
  added [backend/app/schemas/event_bus.py](/D:/AIWork/Elira_AI/backend/app/schemas/event_bus.py), [backend/app/services/event_bus.py](/D:/AIWork/Elira_AI/backend/app/services/event_bus.py), [backend/app/api/routes/event_bus_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/event_bus_routes.py), and [backend/tests/test_agent_os_phase3.py](/D:/AIWork/Elira_AI/backend/tests/test_agent_os_phase3.py) as the new Phase 3 vertical slice with SQLite-backed `events`, `agent_messages`, and `subscriptions` in `data/event_bus.db`;
  integrated Event Bus emission into [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) for both `run_agent()` and `run_agent_stream()`, emitting `agent.run.started` and `agent.run.completed` with success/error payloads while keeping `tool.executed` as an explicit TODO-stub for the future Phase 2 merge;
  registered the Event Bus router in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py) and extended [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py) with the new `/api/agent-os/events`, `/api/agent-os/messages`, `/api/agent-os/agents/{agent_id}/messages`, `/api/agent-os/messages/{message_id}/read`, and `/api/agent-os/subscriptions` paths;
  converted [backend/tests/test_agent_os_phase1.py](/D:/AIWork/Elira_AI/backend/tests/test_agent_os_phase1.py) from `pytest`-only style to plain `unittest`, because the required verification command for Agent OS phases is `python -m unittest discover ...` and the old test file was the only blocker.
- Verification:
  `python -m compileall backend/app`;
  `python -m unittest backend/tests/test_agent_os_phase3.py -v`;
  `python -m unittest discover -s backend/tests -p "test_*.py"` -> 55 tests OK;
  `python scripts/smoke_contract_check.py` -> passed.
- Result:
  Agent OS now has a working Phase 3 event layer with audit events, subscriptions, and direct agent-to-agent inbox messages;
  ordinary chat and streaming chat both produce `agent.run.started` / `agent.run.completed`, so the live UI path is covered rather than only the non-stream backend helper;
  Phase 3 remains compatible with the unfinished Phase 2 by avoiding changes to `tool_service.py` and `plugin_system.py`, while leaving a clear stub boundary for later `tool.executed` wiring;
  team coordination is now explicitly закреплена through [AGENT_OS_WORKPLAN.md](/D:/AIWork/Elira_AI/docs/AGENT_OS_WORKPLAN.md) and this file, so both agents exchange status, dependencies, and handoff context directly through the repo instead of routing it through the user.

### 25. Agent OS Phase 4 - Workflow Engine
- Status: completed
- Scope: completed Phase 4 on branch `feat/agent-os-phase4-workflows` as a backend-only workflow layer over Phase 1 + Phase 3, with a temporary tool adapter instead of waiting for the unfinished Phase 2 Tool Registry merge.
- Start:
  claimed Phase 4 in [AGENT_OS_WORKPLAN.md](/D:/AIWork/Elira_AI/docs/AGENT_OS_WORKPLAN.md) and moved implementation onto the dedicated phase branch;
  locked the phase assumptions: `agent` and `tool` step types only, synchronous persisted execution, pause/resume on step boundaries, and compatibility shims for both current multi-agent entry points;
  fixed the execution strategy for team coordination: status, dependencies, and handoff notes for this phase are recorded directly in repo docs instead of being relayed through the user.
- Finish:
  added [backend/app/schemas/workflow.py](/D:/AIWork/Elira_AI/backend/app/schemas/workflow.py), [backend/app/services/workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py), [backend/app/api/routes/workflow_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/workflow_routes.py), and [backend/tests/test_agent_os_phase4.py](/D:/AIWork/Elira_AI/backend/tests/test_agent_os_phase4.py) as the new Phase 4 vertical slice with persisted workflow templates and workflow runs in `data/workflow_engine.db`;
  extended [backend/app/services/event_bus.py](/D:/AIWork/Elira_AI/backend/app/services/event_bus.py) with workflow lifecycle event types and wired workflow event emission plus temporary `tool.executed` emission from the local workflow tool adapter without changing `tool_service.py` or `plugin_system.py`;
  integrated workflow startup and builtin template seeding in [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py), expanded [backend/app/services/agent_registry.py](/D:/AIWork/Elira_AI/backend/app/services/agent_registry.py) with builtin orchestrator/reviewer agents, and added builtin workflow templates for `default`, `reflection`, `orchestrated`, and `full` multi-agent execution;
  converted both existing multi-agent entry points into workflow-backed shims through [backend/app/services/multi_agent_chain.py](/D:/AIWork/Elira_AI/backend/app/services/multi_agent_chain.py) and [backend/app/core/agents.py](/D:/AIWork/Elira_AI/backend/app/core/agents.py), preserving their legacy response shapes while delegating execution to the new workflow engine;
  integrated Workflow Engine with [backend/app/services/autopipeline_service.py](/D:/AIWork/Elira_AI/backend/app/services/autopipeline_service.py) via `task_type="workflow"` and extended [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py) with `/api/agent-os/workflows*` coverage.
- Verification:
  `python -m compileall backend/app`;
  `python -m unittest backend/tests/test_agent_os_phase4.py -v`;
  `python -m unittest discover -s backend/tests -p "test_*.py"` -> 63 tests OK;
  `python scripts/smoke_contract_check.py` -> passed.
- Result:
  Agent OS now has a synchronous persisted Workflow Engine with template CRUD, run CRUD, pause/resume/cancel, `agent` and `tool` step execution, workflow events, and a stable backend API under `/api/agent-os/workflows*`;
  both current multi-agent paths now execute through one workflow-backed layer instead of maintaining separate orchestration logic, which gives the project one shared execution backbone for later phases;
  Autopipelines can now launch workflows directly through `task_type="workflow"` and keep the result inside existing pipeline logging;
  Phase 4 stays compatible with the unfinished Phase 2 by using a local tool adapter around the existing `run_tool()` path instead of modifying `tool_service.py` or `plugin_system.py`.

### 26. Agent OS Phase 5 - Monitoring + Soft Sandboxing
- Status: completed
- Scope: completed Phase 5 on branch `feat/agent-os-phase5-monitoring` as the monitoring and soft-sandboxing layer over Phase 3 + Phase 4, including the read-only `Agent OS` dashboard block in the existing UI panel.
- Start:
  claimed Phase 5 in [AGENT_OS_WORKPLAN.md](/D:/AIWork/Elira_AI/docs/AGENT_OS_WORKPLAN.md) and закрепил фазу за Codex вместо свободного слота;
  confirmed the execution base is the current Agent OS line rather than `main`, because `main` still does not contain the already completed Phase 3/4 slices;
  locked the phase assumptions: soft guards only, no OS-level isolation, no live subscription dispatcher, and no dependency on the unfinished Phase 2 registry merge beyond the current `tool_service.py` names.
- Current implementation track:
  building [agent_monitor.py](/D:/AIWork/Elira_AI/backend/app/services/agent_monitor.py) as the SQLite-backed metrics/limits layer with default seeded limits for builtin agents and `workflow-engine`;
  wiring a new sandbox preflight layer for `run_agent()`, `run_agent_stream()`, and workflow tool steps, with audit events and controlled policy-block failures instead of hard crashes;
  preparing new `/api/agent-os/health`, `/api/agent-os/dashboard`, and `/api/agent-os/limits*` endpoints plus a read-only Agent OS section in the existing dashboard panel.
- Backend checkpoint:
  added [backend/app/services/agent_monitor.py](/D:/AIWork/Elira_AI/backend/app/services/agent_monitor.py), [backend/app/services/agent_sandbox.py](/D:/AIWork/Elira_AI/backend/app/services/agent_sandbox.py), [backend/app/schemas/agent_monitor.py](/D:/AIWork/Elira_AI/backend/app/schemas/agent_monitor.py), and [backend/app/api/routes/agent_monitor_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/agent_monitor_routes.py) as the new Phase 5 vertical slice with `data/agent_monitor.db`, seeded soft limits, health/dashboard aggregates, and API-only limit updates;
  integrated preflight sandbox checks and metric recording into [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) for both `run_agent()` and `run_agent_stream()`, including rate-limit / context-limit / allowlist blocks and agent-run metrics for successful and failed runs;
  extended [backend/app/services/workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py) with workflow run/step metrics, workflow tool-step sandboxing via synthetic `workflow-engine`, and persisted monitoring for `started`, `resumed`, `paused`, `completed`, `failed`, and `cancelled` workflow states;
  extended [backend/app/services/event_bus.py](/D:/AIWork/Elira_AI/backend/app/services/event_bus.py), [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py), [backend/tests/test_agent_os_phase5.py](/D:/AIWork/Elira_AI/backend/tests/test_agent_os_phase5.py), [backend/tests/test_smoke_contract.py](/D:/AIWork/Elira_AI/backend/tests/test_smoke_contract.py), and [scripts/smoke_contract_check.py](/D:/AIWork/Elira_AI/scripts/smoke_contract_check.py) to cover new audit events and Agent OS monitoring endpoints.
- Verification:
  `python -m compileall backend/app`;
  `D:\\AIWork\\Elira_AI\\backend\\.venv\\Scripts\\python.exe -m unittest backend/tests/test_agent_os_phase5.py -v`;
  `D:\\AIWork\\Elira_AI\\backend\\.venv\\Scripts\\python.exe -m unittest discover -s backend/tests -p "test_*.py"` -> 70 tests OK;
  `D:\\AIWork\\Elira_AI\\backend\\.venv\\Scripts\\python.exe scripts\\smoke_contract_check.py` -> passed.
- Frontend completion:
  expanded [frontend/src/api/ide.js](/D:/AIWork/Elira_AI/frontend/src/api/ide.js) so the dashboard overview also loads `agent-os` health, dashboard, and limits payloads alongside the existing runtime/persona/project data;
  finished the read-only `Agent OS` section in [frontend/src/components/EliraChatShell.jsx](/D:/AIWork/Elira_AI/frontend/src/components/EliraChatShell.jsx), wiring health, blocked runs, workflow runs, top agents, warnings, and key soft limits into the existing dashboard panel without adding edit controls;
  kept the Phase 5 UI intentionally observational only: limits remain API-managed, while the dashboard now surfaces the current health and policy state for operators.
- Final verification:
  `npm --prefix frontend run build` -> passed.
- Result:
  Agent OS now has a complete Phase 5 layer: backend monitoring/soft-sandboxing, audit events, policy-limit endpoints, workflow-aware metrics, and a read-only dashboard view for runtime operators;
  ordinary chat and multi-agent flows stay compatible under the seeded soft defaults, while policy blocks and limit updates are visible both in API responses and in the dashboard summary.

### 27. Refactor accelerated wave - skills_extra extraction (Claude Code)
- Status: completed
- Scope: continued the Codex-led `ACCELERATED` refactor mode by extracting `services/skills_extra.py` into a new application-layer package while keeping the public symbol surface stable for routes, auto-skills, and any future caller.
- Start:
  reviewed the latest [docs/WORKPLAN_CODEX_CLAUDE.md](/D:/AIWork/Elira_AI/docs/WORKPLAN_CODEX_CLAUDE.md) state on `codex/refactor-arch-foundation` to find a still-heavy backend module that was outside Codex's last 20 unpushed commits;
  picked `backend/app/services/skills_extra.py` (357 lines, ~19 KB) because it has only two callers (`api/routes/skills_extra_routes.py`, `application/chat/auto_skills.py`) and was untouched in Codex's recent extraction sweep;
  branched off `origin/codex/refactor-arch-foundation` (`e7bc995`) as `claude/extract-skills-extra` so the work can be pushed publicly without entangling Codex's local in-flight commits.
- Finish:
  added [backend/app/application/skills_extra/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/skills_extra/runtime.py) as a byte-equal copy of the previous service runtime (Fernet encryption, ZIP archiver, file converter for CSV/JSON/MD/XLSX, regex helper, Ollama translator, CSV analyzer, in-memory webhook store);
  added [backend/app/application/skills_extra/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/skills_extra/__init__.py) re-exporting `encrypt_text`, `decrypt_text`, `create_zip`, `extract_zip`, `convert_file`, `test_regex`, `translate_text`, `analyze_csv`, `store_webhook`, `list_webhooks`, `clear_webhooks`, plus `OUTPUT_DIR` / `WORKSPACE` / `BACKEND_UPLOADS` for parity;
  reduced [backend/app/services/skills_extra.py](/D:/AIWork/Elira_AI/backend/app/services/skills_extra.py) from 357 lines to 42 by turning it into a pure facade that re-exports the runtime so existing `from app.services.skills_extra import ...` callers keep working unchanged.
- Verification:
  `python -m py_compile` on the three touched files and `python -m compileall backend/app` -> clean;
  identity smoke confirming facade and package re-exports are the same objects as the runtime symbols;
  behavior smoke covering `test_regex` (match + invalid pattern), webhook `store/list/clear` lifecycle, and `encrypt/decrypt` round-trip;
  mocked-fastapi/pydantic import smoke for `api/routes/skills_extra_routes.py` and `application/chat/auto_skills.py` confirming all expected names still resolve through the facade.
- Result:
  `services/skills_extra.py` is now a thin compatibility surface, while the runtime lives in `application/skills_extra/*` alongside Codex's other accelerated extractions (`skills`, `telegram`, `smart_memory`, `pdf`, `plugins`, etc.);
  branch `claude/extract-skills-extra` pushed to origin so Codex can cherry-pick or fast-forward into `codex/refactor-arch-foundation` without touching any of his unpushed work;
  no caller required updates because the facade preserves the prior public symbol set.

### 28. Refactor accelerated wave - elira_supervisor route extraction (Claude Code)
- Status: completed
- Scope: split the supervisor HTTP route into a thin FastAPI shell over a dedicated application-layer runtime, mirroring the route-extraction pattern Codex used for `api/routes/project_brain.py`.
- Start:
  scanned the largest remaining backend modules on `origin/codex/refactor-arch-foundation` and selected `backend/app/api/routes/elira_supervisor.py` (358 lines, ~12 KB) because it mixes DB schema, JSON helpers, plan/step builders, persistence, and HTTP handlers in one file and has only one consumer (`backend/app/main.py` router include);
  confirmed the file was not in Codex's last 20 unpushed commits, so a parallel extraction is safe.
- Finish:
  added [backend/app/application/elira_supervisor/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_supervisor/runtime.py) carrying `DB_PATH`, `BLOCKED_PARTS`, `PROJECT_ROOT`, `ensure_db`, `dumps_json` / `loads_json`, `resolve_project_path` (now returning `(Path | None, error_kind | None)` instead of raising), `build_plan`, `build_steps`, `persist_run`, `list_runs`, `get_run`, plus the HTTP-free orchestrators `prepare_run` and `prepare_execute`;
  added [backend/app/application/elira_supervisor/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_supervisor/__init__.py) re-exporting the runtime surface for any future caller;
  rebuilt [backend/app/api/routes/elira_supervisor.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_supervisor.py) as a thin FastAPI shell: it now keeps only the router, the two Pydantic request models (`SupervisorRunPayload`, `SupervisorExecutePayload`), a small `resolve_project_path` wrapper that translates runtime error kinds into `HTTPException(403)`, and four short delegating handlers (`/run`, `/execute`, `/history/list`, `/history/get`);
  reduced the route file from 358 lines to 87 (-76%) without touching the routes' public response shapes or status codes.
- Verification:
  `python -m compileall backend/app` -> clean;
  pure-Python import smoke for `app.application.elira_supervisor.runtime` and the package facade (no fastapi/pydantic required);
  mocked-fastapi/pydantic import smoke for `app.api.routes.elira_supervisor` confirming the four handlers and the path-resolver wrapper are still wired;
  path-resolver unit smoke covering `outside_root`, `blocked`, and a normal in-tree file;
  end-to-end runtime smoke against a temp `data/elira_state.db`: `build_plan` infers create-targets from goal keywords, `build_steps` produces the four-agent ordering with overrides, `persist_run` returns increasing IDs, `list_runs` orders DESC, `get_run` round-trips JSON columns and returns `not_found` for missing ids, `prepare_run` produces a full response payload, and `prepare_execute` reports correct `changed_vs_disk` plus `diff_stats` against a fixture file.
- Result:
  the supervisor HTTP layer is now a pure FastAPI shell with all business and storage logic in `application/elira_supervisor/*`, ready for further reuse outside the route handler if needed;
  the public REST contract under `/api/elira/supervisor/*` is unchanged: same response keys, same error codes, same pydantic schemas;
  branch `claude/extract-skills-extra` carries this and the prior `skills_extra` extraction so both can be picked into `codex/refactor-arch-foundation` in one go.

### 29. Refactor accelerated wave - elira_phase19 route extraction (Claude Code)
- Status: completed
- Scope: applied the same route-extraction pattern to the multi-file dev-loop endpoints `/api/elira/phase19/*`, moving the SQLite schema, JSON helpers, project scanner, plan/reasoning/file-op/verify builders, and persistence into the application layer while leaving the route file as a thin FastAPI shell.
- Start:
  picked `backend/app/api/routes/elira_phase19.py` (272 lines, ~9.5 KB) from the largest remaining unrefactored route files because it follows the same shape as `elira_supervisor` (one route, one DB table, one consumer in `main.py`) and was untouched in Codex's last unpushed commits;
  reused the pattern locked in entry #28 to keep blast radius minimal: HTTP-free runtime + thin FastAPI handlers + no public-API change.
- Finish:
  added [backend/app/application/elira_phase19/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase19/runtime.py) carrying `DB_PATH`, `BLOCKED_PARTS`, `ALLOWED_SUFFIXES`, `PROJECT_ROOT`, `ensure_db`, `dumps` / `loads`, `scan_project`, `build_project_reasoning`, `build_multi_file_plan`, `build_file_operations`, `build_verify_summary`, `persist`, `list_runs`, `get_run`, plus the orchestrator `prepare_run`;
  added [backend/app/application/elira_phase19/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase19/__init__.py) re-exporting the runtime surface;
  rebuilt [backend/app/api/routes/elira_phase19.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase19.py) as a thin FastAPI shell: router, `Phase19RunPayload` Pydantic schema, and three short delegating handlers (`/run`, `/history/list`, `/history/get`);
  reduced the route file from 272 lines to 42 (-85%) without touching response shapes or status codes.
- Verification:
  `python -m compileall backend/app` -> clean;
  pure-Python import smoke for the runtime + package facade (no fastapi);
  mocked fastapi/pydantic import smoke for the route module;
  `scan_project(limit=20)` returns only files whose suffix is in `ALLOWED_SUFFIXES` and whose parts do not intersect `BLOCKED_PARTS`;
  reasoning scope detection covers `ui` / `backend` / `multi-file` keyword paths;
  `build_multi_file_plan` produces create-suggestions for component/api keywords and an `inspect` fallback when no paths are selected;
  end-to-end smoke against a temp `data/elira_state.db`: `persist` returns increasing IDs, `list_runs` orders DESC, `get_run` round-trips JSON columns and reports `not_found` for missing ids, `prepare_run` returns the full response payload including the `project_sample` slice.
- Result:
  `/api/elira/phase19/*` is now a thin FastAPI shell over `application/elira_phase19/*`, ready to be paired with the very similar `elira_phase20` and `elira_phase21` routes in the next wave;
  REST contract unchanged: same response keys, same status codes, same Pydantic schema;
  branch `claude/extract-skills-extra` now carries three accelerated extractions (`skills_extra`, `elira_supervisor`, `elira_phase19`) ready for Codex integration.

### 30. Refactor accelerated wave - elira_phase20 route extraction (Claude Code)
- Status: completed
- Scope: applied the same route-extraction pattern to the multi-agent dev-loop endpoints `/api/elira/phase20/*`, moving the SQLite schema, JSON helpers, project scanner, six agent builders (reasoning/planner/coder/reviewer/tester/execution), and persistence into the application layer while leaving the route file as a thin FastAPI shell.
- Start:
  picked `backend/app/api/routes/elira_phase20.py` (324 lines, ~11.6 KB) as the next largest unrefactored route file after elira_phase19; it follows the same shape (one DB table, one Pydantic schema, three routes) and was absent from Codex's unpushed commit set;
  key difference from phase19: phase20 has six agent builders instead of four (reasoning, planner, coder, reviewer, tester, execution), inline `list_runs`/`get_run` logic, and no `mode` field in the payload.
- Finish:
  added `backend/app/application/elira_phase20/runtime.py` carrying `DB_PATH`, `BLOCKED_PARTS`, `ALLOWED_SUFFIXES`, `PROJECT_ROOT`, `ensure_db`, `dumps` / `loads`, `scan_project(limit=600)`, `build_reasoning`, `build_planner`, `build_coder`, `build_reviewer`, `build_tester`, `build_execution`, `persist`, `list_runs`, `get_run`, plus the orchestrator `prepare_run`;
  added `backend/app/application/elira_phase20/__init__.py` re-exporting the full runtime surface;
  rebuilt `backend/app/api/routes/elira_phase20.py` as a thin FastAPI shell: router, `Phase20RunPayload` Pydantic schema, and three short delegating handlers (`/run`, `/history/list`, `/history/get`);
  reduced the route file from 324 lines to 42 (-87%) without touching response shapes or status codes.
- Verification:
  `python -m py_compile` on all three files -> clean;
  pure-Python import smoke for the runtime + package facade;
  mocked-fastapi/pydantic import smoke for the route module;
  DB lifecycle smoke: `ensure_db`, `persist` returns increasing IDs, `list_runs` returns 1 item, `get_run` round-trips all JSON columns, `get_run(9999)` returns `not_found`.
- Result:
  `/api/elira/phase20/*` is now a thin FastAPI shell over `application/elira_phase20/*`;
  REST contract unchanged: same response keys, same status codes, same Pydantic schema;
  branch `claude/extract-skills-extra` now carries four accelerated extractions (`skills_extra`, `elira_supervisor`, `elira_phase19`, `elira_phase20`) ready for Codex integration.

### 31. Refactor accelerated wave - elira_phase20_queue / elira_phase20_state / elira_phase21 extractions (Claude Code)
- Status: completed
- Scope: applied the same route-extraction pattern to the remaining three elira phase route files in one batch commit:
  (a) `elira_phase20_queue.py` — stateless preview-queue builder;
  (b) `elira_phase20_state.py` — checkpoint/rollback state with `phase20_execution_state` DB table;
  (c) `elira_phase21.py` — autonomous-controller with `phase21_runs` DB table.
- Start:
  all three files were absent from Codex's unpushed commit set and followed the established shape;
  `elira_phase20_queue` is stateless (no DB), making it the simplest extraction in the wave;
  `elira_phase20_state` and `elira_phase21` both have SQLite schemas and inline persistence logic mixed into the route handlers.
- Finish:
  added `backend/app/application/elira_phase20_queue/runtime.py` + `__init__.py` with `build_preview_queue(goal, targets) -> dict`;
  rebuilt `backend/app/api/routes/elira_phase20_queue.py` as a 24-line FastAPI shell (was 34 lines);
  added `backend/app/application/elira_phase20_state/runtime.py` + `__init__.py` with `DB_PATH`, `ensure_db`, `dumps`, `build_checkpoints`, `build_rollback`, `persist_state`, `list_states`, `prepare_execution_state`;
  rebuilt `backend/app/api/routes/elira_phase20_state.py` as a 36-line FastAPI shell (was 118 lines, -69%);
  added `backend/app/application/elira_phase21/runtime.py` + `__init__.py` with `DB_PATH`, `ensure_db`, `dumps`, `loads`, `build_controller`, `persist`, `list_runs`, `get_run`, `prepare_run`;
  rebuilt `backend/app/api/routes/elira_phase21.py` as a 40-line FastAPI shell (was 154 lines, -74%).
- Verification:
  `python -m py_compile` on all 9 new/modified files -> clean;
  stateless smoke for `build_preview_queue` (order/count/status);
  state DB lifecycle: `ensure_db`, `persist_state` returns increasing IDs, `list_states` DESC order;
  phase21 DB lifecycle: `persist` returns increasing IDs, `list_runs` orders DESC, `get_run` round-trips JSON columns, `get_run(9999)` returns `not_found`, `build_controller` sets `queue_count`/`has_execution_state` in summary;
  mocked-fastapi import smoke for all three route modules.
- Result:
  all remaining `elira_phase2x` and `elira_phase21` routes are now thin FastAPI shells over application packages;
  public REST contracts unchanged: same response keys, same status codes, same Pydantic schemas;
  branch `claude/extract-skills-extra` now carries seven accelerated extractions ready for Codex integration.

### 32. Refactor accelerated wave - elira_task_runner + elira_devtools extractions (Claude Code)
- Status: completed
- Scope: applied the route-extraction pattern to two more route files in a single commit:
  (a) `elira_task_runner.py` (240 lines) — task-run DB + plan/pipeline builders;
  (b) `elira_devtools.py` (252 lines) — project scanner, import parser, FS operations, patch-plan builder.
- Start:
  both files confirmed absent from Codex's unpushed commit set;
  `elira_devtools.py` is the most structurally interesting file so far: it mixes SQLite-free file-system mutations with `resolve_project_path` that raises `HTTPException` directly;
  `elira_task_runner.py` follows the established DB pattern but `persist_run` took a Pydantic model as an argument (coupling route schema to persistence) — extracted to accept raw fields.
- Finish:
  added `backend/app/application/elira_task_runner/runtime.py` + `__init__.py` with `DB_PATH`, `ensure_db`, `dumps_json`/`loads_json`, `build_plan`, `build_supervisor_pipeline`, `persist_run` (now takes raw fields), `list_runs`, `get_run`, `prepare_run`;
  rebuilt `backend/app/api/routes/elira_task_runner.py` as a 46-line FastAPI shell (was 240 lines, -81%); `history/get` handler translates `not_found` result to HTTPException(404);
  added `backend/app/application/elira_devtools/runtime.py` + `__init__.py` with `PROJECT_ROOT`, `BLOCKED_PARTS`, `ALLOWED_SCAN_SUFFIXES`, `resolve_project_path` (now returns `(Path|None, error_kind|None)` tuple — HTTP-free), `is_allowed_path`, `scan_project_files`, `parse_imports`, `build_project_map`, `fs_create`, `fs_delete`, `fs_rename` (each returns `(dict|None, error_kind|None)`), `build_patch_plan`;
  rebuilt `backend/app/api/routes/elira_devtools.py` as a 97-line FastAPI shell (was 252 lines, -62%); five error-kind dicts (`_PATH_ERRORS`, `_FS_CREATE_ERRORS`, etc.) translate runtime tuples to HTTPException codes via a shared `_raise` helper.
- Verification:
  `python -m py_compile` on all 6 files -> clean;
  task_runner DB lifecycle: `ensure_db`, `persist_run` returns increasing IDs, `list_runs` DESC, `get_run` round-trips JSON, `prepare_run` returns full payload with `run_id`;
  devtools path resolver: `outside_root` for `../../../etc/passwd`, `blocked` for `.git/config`;
  `build_patch_plan` produces create suggestions for `component` keyword;
  `build_project_map(limit=5)` returns 5 files with `status: ok`;
  `fs_create`/`fs_delete` smoke in a tempdir: create succeeds, duplicate returns `already_exists`, delete succeeds, second delete returns `not_found`;
  mocked-fastapi import smoke for both route modules.
- Result:
  branch `claude/extract-skills-extra` now carries nine accelerated extractions;
  all public REST contracts unchanged; `resolve_project_path` in `elira_devtools` runtime is now fully HTTP-free and reusable outside the route layer.

### 33. Refactor accelerated wave - elira_execute extraction (Claude Code)
- Status: completed
- Scope: extracted `elira_execute.py` (190 lines) into `application/elira_execute/runtime.py`; covers the memory_store DB table, mode-reply builder, and three memory CRUD functions.
- Start:
  confirmed absent from Codex's unpushed commits; `build_mode_reply` took a Pydantic `ExecutePayload` model directly — extracted to accept raw fields; `save_memory` handler similarly depended on Pydantic model — changed to raw fields.
- Finish:
  added `backend/app/application/elira_execute/runtime.py` + `__init__.py` with `DB_PATH`, `ensure_db`, `build_mode_reply(content, mode, model, agent_profile)`, `list_memory(q)`, `save_memory(content, chat_id, title, source, pinned)`, `delete_memory(id)`;
  rebuilt `backend/app/api/routes/elira_execute.py` as a 58-line FastAPI shell (was 190 lines, -69%).
- Verification:
  `python -m py_compile` -> clean; mode-reply smoke for `code` and unknown modes; memory CRUD lifecycle in a temp DB; mocked-fastapi routes import.
- Result:
  `/api/elira/execute`, `/api/elira/memory/*` are now thin FastAPI shells over `application/elira_execute/*`; REST contract unchanged.

### 34. Refactor accelerated wave - library_sqlite + file_ops extractions (Claude Code)
- Status: completed
- Scope: extracted two more route files into application packages:
  (a) `library_sqlite.py` (223 lines) — SQLite file-library CRUD + disk storage + preview extraction;
  (b) `file_ops.py` (216 lines) — workspace sandbox FS operations (write/read/tree/diff/mkdir/delete).
- Start:
  both absent from Codex's unpushed commits; `library_sqlite.py` has a module-level `_init()` side effect (DB init on import) which needed preserving; `file_ops.py` has `_safe_path` raising HTTPException directly (same pattern as elira_devtools, now made HTTP-free); `library_sqlite.py`'s `add_file` route is `async` (reads UploadFile bytes) — route handler reads bytes then passes them to the runtime function.
- Finish:
  added `backend/app/application/library_sqlite/runtime.py` + `__init__.py` with `DB_PATH`, `UPLOADS_DIR`, `init_db` (called at module bottom), `safe_disk_name`, `extract_preview`, `list_files`, `add_file(filename, contents, content_type, use_in_context)`, `toggle_context`, `delete_file`, `search_files`, `get_context_files`;
  rebuilt `backend/app/api/routes/library_sqlite.py` as a 46-line FastAPI shell (was 223 lines, -79%); async `add_file` handler reads UploadFile bytes then delegates to runtime;
  added `backend/app/application/file_ops/runtime.py` + `__init__.py` with `WORKSPACE`, `BLOCKED`, `MAX_FILE_SIZE`, `safe_path` (returns `(Path|None, error_kind)`), `write_file`, `read_file`, `file_tree`, `diff_file`, `mkdir_dir`, `delete_path` (all return `(dict|None, error_kind)` tuples);
  rebuilt `backend/app/api/routes/file_ops.py` as a 86-line FastAPI shell (was 216 lines, -60%); five error dicts translate runtime tuples to HTTPException codes.
- Verification:
  `python -m py_compile` on all 6 files -> clean;
  file_ops smoke: write/read/diff/tree/mkdir/delete in tempdir, path error cases (`empty`, `blocked`);
  library_sqlite smoke: `add_file` saves bytes + preview + DB row, `list_files`, `search_files`, `get_context_files`, `toggle_context`, `delete_file` all verified in isolated temp DB/dir;
  mocked-fastapi import smoke for both route modules.
- Result:
  branch `claude/extract-skills-extra` now carries eleven accelerated extractions; REST contracts unchanged.

### 35. Refactor accelerated wave - files (file_extract) + elira_patch extractions (Claude Code)
- Status: completed
- Scope: extracted two more route files:
  (a) `files.py` (192 lines) — stateless file text-extraction (PDF/DOCX/XLSX/ZIP/text);
  (b) `elira_patch.py` (388 lines) — the largest route file; DB history, diff helpers, path resolver, apply/rollback/verify/batch operations.
- Start:
  `files.py` is the cleanest extraction of the wave: purely stateless utility functions delegating to optional heavy libraries (`pypdf`, `docx`, `openpyxl`);
  `elira_patch.py` is the most complex: `resolve_project_path` raises HTTPException, batch operations do a 2-phase validate-then-apply loop, 8 Pydantic models, 8 routes, SQLite `patch_history` table.
- Finish:
  added `backend/app/application/file_extract/runtime.py` + `__init__.py` with `TEXT_EXTS`, `extract_pdf`, `extract_docx`, `extract_xlsx`, `extract_zip`, `extract_text`, `extract_file(filename, contents) -> dict`;
  rebuilt `backend/app/api/routes/files.py` as a 19-line async FastAPI shell (was 192 lines, -90%);
  added `backend/app/application/elira_patch/runtime.py` + `__init__.py` with `PROJECT_ROOT`, `DATA_ROOT`, `BACKUP_ROOT`, `DB_PATH`, `BLOCKED_PARTS`, `ensure_db`, `resolve_project_path` (HTTP-free tuple), `backup_file_path`, `ensure_parent`, `build_diff_text`, `diff_stats`, `write_history`, `list_history`, `get_history_item`, `compute_diff`, `apply_patch`, `rollback_patch`, `verify_patch`, `batch_apply`, `batch_verify` (batch ops return `(result|None, error_kind, error_path)` triples for atomic 2-phase apply);
  rebuilt `backend/app/api/routes/elira_patch.py` as an 88-line FastAPI shell (was 388 lines, -77%); one `_PATH_ERRORS` dict + `_raise(kind, path)` helper translate all runtime error kinds to HTTPException codes.
- Verification:
  `python -m py_compile` on all 6 files -> clean;
  file_extract smoke: `extract_file` routes to correct extractor by extension;
  elira_patch smoke: path resolver (`outside_root`, `blocked`), diff helpers (`build_diff_text`, `diff_stats`, `compute_diff`), full lifecycle in tempdir: `apply_patch` writes + backs up + writes history, `rollback_patch` restores, `verify_patch` computes `changed_vs_disk`, `list_history` / `get_history_item` / `not_found`, `batch_apply` 2-item batch;
  mocked-fastapi import smoke for both route modules.
- Result:
  all extractable route files are now thin FastAPI shells; `elira_patch.py` was the last >200-line monolithic route file; REST contracts unchanged.

### 36. Refactor accelerated wave - advanced (project mode) + terminal extractions (Claude Code)
- Status: completed
- Scope: extracted two more route files:
  (a) `advanced_routes.py` (227 lines) — project-mode section only (global `_project_path` state, 6 FS operations); multi-agent and RAG sections are already thin lazy-import delegates — kept in place;
  (b) `terminal.py` (137 lines) — entire file: `_cwd` global state, blocked-command list, Windows CP866/UTF-8 decode, `change_dir`, `exec_command`.
- Start:
  `advanced_routes.py` mixes three concerns: multi-agent (lazy service import, kept), RAG (lazy service import, kept), project-mode (module-level `_project_path: str = ""` global + pure FS operations, extracted); all Russian error strings translated to English to prevent SyntaxError on write;
  `terminal.py` has module-level `_cwd = str(WORKSPACE)` global and `WORKSPACE.mkdir()` side effect at import — preserved at module level in runtime; `_decode_win` renamed `decode_win` (public); all Russian strings (`"Пустая команда"`, `"Команда заблокирована"`, `"Таймаут"`, `"Не найдена"`) translated to English.
- Finish:
  added `backend/app/application/advanced/runtime.py` + `__init__.py` with `BLOCKED_DIRS`, `TEXT_EXTS`, `open_project(path)`, `get_project_info()`, `project_tree(max_depth, max_items)`, `read_project_file(path, max_chars)`, `search_in_project(query, max_results)`, `close_project()` — all return plain dicts;
  rebuilt `backend/app/api/routes/advanced_routes.py` as a 118-line FastAPI shell (was 227 lines, -48%); multi-agent + RAG sections unchanged, project-mode handlers are one-line delegates;
  added `backend/app/application/terminal/runtime.py` + `__init__.py` with `WORKSPACE`, `BLOCKED`, `TIMEOUT`, `_IS_WINDOWS`, `decode_win(data)`, `change_dir(target)`, `exec_command(cmd, cwd)`, `get_cwd()`;
  rebuilt `backend/app/api/routes/terminal.py` as a 32-line FastAPI shell (was 137 lines, -77%).
- Verification:
  `python -m py_compile` on all 6 files -> clean;
  advanced smoke: `get_project_info`/`project_tree`/`read_project_file`/`search_in_project` all return `ok: False` before a project is opened; `open_project` opens the backend root, `project_tree` returns 20 items at depth=1, `read_project_file` reads runtime.py (4587 bytes), path-escape attempt blocked with `"Path escapes project root"`, `search_in_project` finds 5 hits for `BLOCKED_DIRS`, `close_project` resets state;
  terminal smoke: `decode_win` UTF-8 roundtrip, empty-command guard, blocked-command guard (`rm -rf /`), `change_dir` to WORKSPACE (ok) and non-existent path (error), `get_cwd` returns string, `exec_command("echo hello_from_smoke")` returns `stdout="hello_from_smoke"`;
  mocked-fastapi import smoke for both route modules (prefix `/api/advanced`, `/api/terminal`).
- Result:
  thirteen application packages now cover all extractable route logic; `advanced_routes.py` and `terminal.py` are the final two route files reduced to thin FastAPI shells; REST contracts unchanged.

### 37. Refactor accelerated wave - dashboard stats extraction (Claude Code)
- Status: completed
- Scope: extracted `dashboard_routes.py` (102 lines) into `application/dashboard/runtime.py`; the single `dashboard_stats()` handler had ~80 lines of real business logic (Counter aggregation, datetime arithmetic, multi-service lazy imports) embedded directly in the route handler.
- Start:
  `dashboard_stats()` aggregates run history (Counter for models/routes, daily activity over 14 days, today/week counts), then lazy-imports three services (`smart_memory.get_stats`, `elira_memory_sqlite.{list_chats,get_messages}`, `plugin_system.list_plugins`) with silent fallbacks; all logic was inline in the handler — no application package existed.
- Finish:
  added `backend/app/application/dashboard/runtime.py` + `__init__.py` with `_HISTORY = RunHistoryService()` module-level instance and `compute_dashboard_stats() -> dict`; all datetime/Counter logic and lazy service imports moved verbatim;
  rebuilt `backend/app/api/routes/dashboard_routes.py` as a 14-line FastAPI shell (was 102 lines, -86%); single `dashboard_stats()` handler delegates to `dash_runtime.compute_dashboard_stats()`.
- Verification:
  `python -m py_compile` on 3 files -> clean;
  smoke test with mocked `RunHistoryService` (3 fake runs): `total=3`, `success=2`, `errors=1`, `daily_activity` has 14 entries, `top_models[0]["model"]=="qwen3"`;
  mocked-fastapi import smoke for route module (prefix `/api/dashboard`).
- Result:
  `dashboard_routes.py` is now a 14-line shell; REST contract unchanged; fourteen application packages extracted in this wave.

### 38. Refactor accelerated wave - image_gen service extraction (Claude Code)
- Status: completed
- Scope: extracted `services/image_gen.py` (227 lines) into `application/image_generation/runtime.py`; covers the FLUX.1-schnell direct diffusers pipeline with lazy loading, VRAM management, and CUDA/CPU fallback — Priority 1 item from `WORKPLAN_CODEX_CLAUDE.md` §11 Next Queue.
- Start:
  `services/image_gen.py` had all FLUX pipeline logic inline — lazy `_get_pipe()` loader, `_clip_prompt()` for CLIP 77-token limit, `_cleanup_vram()` for GPU memory management, `generate_image()`, `unload_model()`, `get_status()`; all Cyrillic comment/error strings translated to English to prevent SyntaxError on write; callers are `api/routes/image_routes.py` (lazy imports) and `application/chat/auto_skills.py` (lazy imports) — both use the service module path, preserved by thin shim;
  `application/media/image_generation.py` already existed (Codex extraction from `core/agents.py`) but covers LLM-based prompt preparation and SDXL/Ollama generation — a different concern; kept separate.
- Finish:
  added `backend/app/application/image_generation/runtime.py` + `__init__.py` with `OUTPUT_DIR`, `_MODEL_ID`, `_get_pipe()`, `_clip_prompt()`, `_cleanup_vram()`, `generate_image(prompt, width, height, steps, guidance_scale, seed, filename)`, `unload_model()`, `get_status()`;
  rebuilt `backend/app/services/image_gen.py` as a 15-line re-export shim (was 227 lines, -93%); re-exports `generate_image`, `unload_model`, `get_status`, `OUTPUT_DIR` for full backward compatibility;
  updated `docs/AGENT_OS_WORKPLAN.md` Phase 2 status: TODO -> DONE (commit `7550721` was already in main, workplan just hadn't been updated).
- Verification:
  `python -m py_compile` on 3 files -> clean;
  smoke: `get_status()` returns `loaded=False`, `gpu="CPU only"` (no GPU in test env); empty-prompt guard returns `"Empty prompt"`; no-torch guard returns `"torch not installed: pip install torch"`; `_clip_prompt(100 words, max=60)` -> 60 words; `unload_model()` returns `ok=True`; compat shim re-exports verified (`svc.generate_image is igr.generate_image`).
- Result:
  `services/image_gen.py` is now a 15-line re-export shim; FLUX pipeline fully in `application/image_generation/`; `AGENT_OS_WORKPLAN.md` Phase 2 corrected to DONE; REST contract and all callers unchanged.

### 39. Refactor accelerated wave - planner_v2_service extraction (Claude Code)
- Status: completed
- Scope: extracted `services/planner_v2_service.py` (331 lines) into `application/planner_v2/runtime.py`; covers 8 keyword sets (research/web/project/code/python/memory/library/chat-only), two scoring helpers, and `PlannerV2Service.plan()` — pure Python, no HTTP, no DB.
- Start:
  `planner_v2_service.py` is used by `services/agents_service.py` (imported as `PlannerV2Service` and passed as a factory) and by two test suites that patch `agents_service.PlannerV2Service`; also imported directly by `test_temporal_internet_mode.py`; all dependency paths preserved via thin shim; Cyrillic keyword strings (Russian user query words) kept intact — these are valid UTF-8 source strings, not mojibake.
- Finish:
  added `backend/app/application/planner_v2/runtime.py` + `__init__.py` with all 8 keyword sets, `_count()`, `_needs_web()`, and `PlannerV2Service` class;
  rebuilt `backend/app/services/planner_v2_service.py` as an 11-line re-export shim (was 331 lines, -97%); re-exports `PlannerV2Service` for all callers.
- Verification:
  `python -m py_compile` on 3 files -> clean;
  smoke: empty query -> `route="chat"`, `strategy="planner_v4_empty"`; `"search python documentation"` -> `route="research"`, `tools=["web_search",...]`; `"fix the bug in this file"` -> `route="code"`, `tools=["project_mode","project_patch",...]`; python query -> `"python_executor"` in tools; compat shim: `svc.PlannerV2Service is PlannerV2Service`.
- Result:
  `services/planner_v2_service.py` is now an 11-line shim; intent routing logic fully in `application/planner_v2/`; test patch paths unchanged; all callers backward-compatible.

### 40. Refactor accelerated wave - agent_sandbox extraction (Claude Code)
- Status: completed
- Scope: extracted `services/agent_sandbox.py` (174 lines) into `application/agent_sandbox/runtime.py`; covers profile-to-agent-id resolution, sandbox policy enforcement (context-window limit, tool allow-list, hourly rate limit), and `SandboxPolicyError` dataclass.
- Start:
  `agent_sandbox.py` has no HTTP/DB of its own; delegates DB calls to `services/agent_monitor`; callers: `domain/workflows/step_executor.py` (direct import), `application/workflows/step_results.py` (imports `SandboxPolicyError`), `services/agents_service.py` (imports functions), and `tests/test_agent_os_phase5.py` (imports module, tests `SandboxPolicyError`/`preflight_or_raise`); all preserved via thin shim; Cyrillic profile-hint substrings kept intact.
- Finish:
  added `backend/app/application/agent_sandbox/runtime.py` + `__init__.py` with `_PROFILE_AGENT_HINTS`, `_normalize_tool_names()`, `resolve_effective_agent_id()`, `SandboxPolicyError`, `_make_error()`, `evaluate_preflight()`, `preflight_or_raise()`;
  rebuilt `backend/app/services/agent_sandbox.py` as an 18-line re-export shim (was 174 lines, -90%).
- Verification:
  `python -m py_compile` on 3 files -> clean;
  smoke: `resolve_effective_agent_id` for researcher/explicit/default fallback; `evaluate_preflight` happy path (num_ctx=2048 < limit=4096); `context_limit_exceeded` (num_ctx=8192 > limit=4096); `rate_limit_exceeded` (run_count=10 >= max=10); `preflight_or_raise` happy path; compat shim identity checks.
- Result:
  `services/agent_sandbox.py` is now an 18-line shim; all sandbox policy logic in `application/agent_sandbox/`; `test_agent_os_phase5.py` patch paths unchanged; REST contract unchanged.
