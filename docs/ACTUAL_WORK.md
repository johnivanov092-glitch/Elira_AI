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

### 27. Library runtime extraction and workflow seed facade repair
- Status: completed
- Scope: continued backend architecture decomposition by moving SQLite/file-library runtime logic out of the route layer and preserving legacy compatibility facades.
- Finish:
  added [backend/app/application/library/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/library/runtime.py) for library DB initialization, upload persistence, preview extraction, search, context building, activation, and deletion;
  reduced [backend/app/api/routes/library_sqlite.py](/D:/AIWork/Elira_AI/backend/app/api/routes/library_sqlite.py) to a thin FastAPI router that passes request data into the application runtime;
  reduced [backend/app/services/library_service.py](/D:/AIWork/Elira_AI/backend/app/services/library_service.py) to a compatibility facade for existing `/api/library/*` callers;
  repaired workflow builtin seeding through [backend/app/services/workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py), [backend/app/application/workflows/multi_agent.py](/D:/AIWork/Elira_AI/backend/app/application/workflows/multi_agent.py), and [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py) so isolated test data roots initialize workflow tables before seed.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  library upload/list/search/context behavior keeps the same API shape, while SQL and file processing now live under `application/library`;
  workflow startup and tests are protected against missing-table failures after the workflow extraction.

### 28. Local FLUX image generation runtime extraction
- Status: completed
- Scope: moved the legacy local FLUX image generation runtime out of the service layer while preserving `/api/image/*` behavior.
- Finish:
  moved the implementation from [backend/app/services/image_gen.py](/D:/AIWork/Elira_AI/backend/app/services/image_gen.py) into [backend/app/application/media/flux_schnell_runtime.py](/D:/AIWork/Elira_AI/backend/app/application/media/flux_schnell_runtime.py);
  reduced [backend/app/services/image_gen.py](/D:/AIWork/Elira_AI/backend/app/services/image_gen.py) to a compatibility facade exporting `generate_image`, `get_status`, and `unload_model`.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  local image generation internals now live under `application/media`, and route/service imports remain backward-compatible.

### 29. Git subprocess runtime extraction
- Status: completed
- Scope: moved Git subprocess integration out of the service layer while preserving existing helper imports.
- Finish:
  added [backend/app/infrastructure/git/runtime.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/git/runtime.py) and [backend/app/infrastructure/git/__init__.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/git/__init__.py);
  reduced [backend/app/services/git_service.py](/D:/AIWork/Elira_AI/backend/app/services/git_service.py) to a compatibility facade exporting `git_status`, `git_diff`, `git_log`, `git_commit`, `git_branches`, and `format_git_context`.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  Git integration now sits under `infrastructure/git`, and current API/tool callers can continue using `app.services.git_service`.

### 30. Project filesystem runtime extraction
- Status: completed
- Scope: moved project tree/read/write/search filesystem helpers out of the service layer while preserving existing imports used by project brain and patch tooling.
- Finish:
  added [backend/app/infrastructure/storage/project_files.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/storage/project_files.py) for safe repo-root path resolution, tree listing, file read/write, and text search;
  reduced [backend/app/services/project_service.py](/D:/AIWork/Elira_AI/backend/app/services/project_service.py) to a compatibility facade exporting `BASE_DIR`, `list_project_tree`, `read_project_file`, `write_project_file`, and `search_project`.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  project filesystem access now sits under `infrastructure/storage`, and legacy service imports remain backward-compatible.

### 31. Web multi-search runtime extraction
- Status: completed
- Scope: moved multi-engine web search wrappers out of the service layer while preserving existing imports.
- Finish:
  added [backend/app/infrastructure/search/multisearch.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/search/multisearch.py) for `multi_search`, `deep_search`, `news_search`, `fetch_page`, and `WebMultiSearchService`;
  reduced [backend/app/services/web_multisearch_service.py](/D:/AIWork/Elira_AI/backend/app/services/web_multisearch_service.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  multi-engine web search helpers now sit under `infrastructure/search`, and service-level imports remain backward-compatible.

### 32. Chat temporal intent extraction
- Status: completed
- Scope: moved temporal/current-world query classification out of the service layer while preserving existing imports used by planner and cache policy.
- Finish:
  added [backend/app/application/chat/temporal_intent.py](/D:/AIWork/Elira_AI/backend/app/application/chat/temporal_intent.py) for `detect_temporal_intent` and its classifier constants;
  reduced [backend/app/services/temporal_intent.py](/D:/AIWork/Elira_AI/backend/app/services/temporal_intent.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  temporal intent logic now sits under `application/chat`, and legacy imports from `app.services.temporal_intent` remain compatible.

### 33. Chat planner v2 extraction
- Status: completed
- Scope: moved chat/tool route planning out of the service layer while preserving existing imports used by agents_service.
- Finish:
  added [backend/app/application/chat/planner_v2.py](/D:/AIWork/Elira_AI/backend/app/application/chat/planner_v2.py) for `PlannerV2Service`;
  reduced [backend/app/services/planner_v2_service.py](/D:/AIWork/Elira_AI/backend/app/services/planner_v2_service.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  chat request planning now sits under `application/chat`, and `app.services.planner_v2_service` remains backward-compatible.

### 34. Web service search facade extraction
- Status: completed
- Scope: moved public web search helper wrappers out of the service layer while preserving existing tool-registry imports.
- Finish:
  added `search_web` and `research_web` compatibility entrypoints to [backend/app/infrastructure/search/web_search.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/search/web_search.py);
  reduced [backend/app/services/web_service.py](/D:/AIWork/Elira_AI/backend/app/services/web_service.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  legacy callers can continue using `app.services.web_service`, while public web search wrappers now sit under `infrastructure/search`.

### 35. LLM model listing and Python runner facade extraction
- Status: completed
- Scope: batched low-risk service extractions for runtime utility code while preserving existing route/tool imports.
- Finish:
  added [backend/app/infrastructure/llm/ollama_models.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/llm/ollama_models.py) for `get_models` and `list_ollama_models`;
  added [backend/app/domain/runtime/python_runner.py](/D:/AIWork/Elira_AI/backend/app/domain/runtime/python_runner.py) for restricted Python execution;
  reduced [backend/app/services/models_service.py](/D:/AIWork/Elira_AI/backend/app/services/models_service.py), [backend/app/services/ollama_runtime_service.py](/D:/AIWork/Elira_AI/backend/app/services/ollama_runtime_service.py), and [backend/app/services/python_runner.py](/D:/AIWork/Elira_AI/backend/app/services/python_runner.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  Ollama model listing now sits under `infrastructure/llm`, restricted Python execution sits under `domain/runtime`, and old service imports remain backward-compatible.

### 36. Runtime status and response cache extraction
- Status: completed
- Scope: moved runtime status and response-cache orchestration out of the service layer while preserving existing backend imports.
- Finish:
  added [backend/app/application/runtime/status.py](/D:/AIWork/Elira_AI/backend/app/application/runtime/status.py) for runtime health/status assembly;
  added [backend/app/application/response_cache/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/response_cache/runtime.py) for response cache DB wiring and policy/store orchestration;
  reduced [backend/app/services/runtime_service.py](/D:/AIWork/Elira_AI/backend/app/services/runtime_service.py) and [backend/app/services/response_cache.py](/D:/AIWork/Elira_AI/backend/app/services/response_cache.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  runtime status now sits under `application/runtime`, response cache runtime now sits under `application/response_cache`, and old service imports remain backward-compatible.

### 37. Memory and profile facade extraction
- Status: completed
- Scope: moved memory route helpers and profile listing helpers out of the service layer while preserving existing route imports.
- Finish:
  added [backend/app/application/memory/service.py](/D:/AIWork/Elira_AI/backend/app/application/memory/service.py) for memory API helper orchestration;
  added [backend/app/application/memory/profiles.py](/D:/AIWork/Elira_AI/backend/app/application/memory/profiles.py) for legacy memory profile helpers;
  added [backend/app/application/persona/profiles.py](/D:/AIWork/Elira_AI/backend/app/application/persona/profiles.py) for persona profile listing;
  reduced [backend/app/services/memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/memory_service.py), [backend/app/services/profile_service.py](/D:/AIWork/Elira_AI/backend/app/services/profile_service.py), and [backend/app/services/profiles_service.py](/D:/AIWork/Elira_AI/backend/app/services/profiles_service.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  memory/profile route helpers now sit under `application/memory` and `application/persona`, and old service imports remain backward-compatible.

### 38. Chat Ollama runtime and reflection loop extraction
- Status: completed
- Scope: moved direct Ollama chat runtime and reflection-loop orchestration out of the service layer while preserving existing chat imports.
- Finish:
  added [backend/app/application/chat/ollama_chat.py](/D:/AIWork/Elira_AI/backend/app/application/chat/ollama_chat.py) for `normalize_profile`, `run_chat`, and `run_chat_stream`;
  added [backend/app/application/chat/reflection_loop.py](/D:/AIWork/Elira_AI/backend/app/application/chat/reflection_loop.py) for `run_reflection_loop`;
  reduced [backend/app/services/chat_service.py](/D:/AIWork/Elira_AI/backend/app/services/chat_service.py) and [backend/app/services/reflection_loop_service.py](/D:/AIWork/Elira_AI/backend/app/services/reflection_loop_service.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  local chat model execution now sits under `application/chat`, reflection-loop logic is colocated with chat orchestration, and old service imports remain backward-compatible.

### 39. Tool, browser stub, and deprecated Ollama model facade extraction
- Status: completed
- Scope: batched small service-layer extractions that do not touch mutable DB-backed service state.
- Finish:
  added [backend/app/application/tool_registry/service.py](/D:/AIWork/Elira_AI/backend/app/application/tool_registry/service.py) for tool-service compatibility helpers;
  added [backend/app/infrastructure/browser/agent.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/browser/agent.py) and [backend/app/infrastructure/browser/__init__.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/browser/__init__.py) for the browser agent stub;
  added [backend/app/application/project_brain/map_service.py](/D:/AIWork/Elira_AI/backend/app/application/project_brain/map_service.py) and [backend/app/application/project_brain/loop_service.py](/D:/AIWork/Elira_AI/backend/app/application/project_brain/loop_service.py) for project-brain compatibility stubs;
  extended [backend/app/infrastructure/llm/ollama_models.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/llm/ollama_models.py) with deprecated `list_models` compatibility;
  reduced `tool_service`, `browser_agent`, `project_map_service`, `project_brain_loop_service`, and `ollama_models_service` under [backend/app/services](/D:/AIWork/Elira_AI/backend/app/services) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  small compatibility helpers now sit under application/infrastructure modules, while existing service imports and Agent OS tool seeding remain backward-compatible.

### 40. Project patch service wrapper extraction
- Status: completed
- Scope: moved the ProjectPatchService wrapper out of the service layer while preserving project brain/tool registry imports.
- Finish:
  added [backend/app/application/project_patch/service.py](/D:/AIWork/Elira_AI/backend/app/application/project_patch/service.py) for the backward-compatible `ProjectPatchService` wrapper over `ProjectPatchRuntime`;
  reduced [backend/app/services/project_patch_service.py](/D:/AIWork/Elira_AI/backend/app/services/project_patch_service.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  project patch wrapper construction now sits under `application/project_patch`, and old service imports remain backward-compatible.

### 41. Run history service wiring extraction
- Status: completed
- Scope: moved run-history DB wiring and concrete service construction out of the legacy service layer while preserving existing dashboard/agent imports.
- Finish:
  added [backend/app/application/run_history/service.py](/D:/AIWork/Elira_AI/backend/app/application/run_history/service.py) for `DB_PATH`, legacy JSON migration wiring, DB init, and the concrete `RunHistoryService`;
  reduced [backend/app/services/run_history_service.py](/D:/AIWork/Elira_AI/backend/app/services/run_history_service.py) to a compatibility facade with the previous exports.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  run-history persistence wiring now sits under `application/run_history`, and old service imports remain backward-compatible.

### 42. Chat guard policy extraction
- Status: completed
- Scope: moved response guard policy modules out of the legacy service layer without changing guard behavior or legacy imports.
- Finish:
  added [backend/app/application/chat/identity_guard.py](/D:/AIWork/Elira_AI/backend/app/application/chat/identity_guard.py) and [backend/app/application/chat/provenance_guard.py](/D:/AIWork/Elira_AI/backend/app/application/chat/provenance_guard.py);
  updated [backend/app/application/chat/post_processing.py](/D:/AIWork/Elira_AI/backend/app/application/chat/post_processing.py) to import guards from `application/chat`;
  reduced [backend/app/services/identity_guard.py](/D:/AIWork/Elira_AI/backend/app/services/identity_guard.py) and [backend/app/services/provenance_guard.py](/D:/AIWork/Elira_AI/backend/app/services/provenance_guard.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  chat response guard policies now sit under `application/chat`, while old service imports remain backward-compatible.

### 43. Task planner and RAG memory service wiring extraction
- Status: completed
- Scope: batched two existing runtime-backed service wrappers into application-layer wiring modules while preserving legacy imports.
- Finish:
  added [backend/app/application/task_planner/service.py](/D:/AIWork/Elira_AI/backend/app/application/task_planner/service.py) for task-planner DB wiring, init, and public helper functions;
  added [backend/app/application/rag_memory/service.py](/D:/AIWork/Elira_AI/backend/app/application/rag_memory/service.py) for RAG DB wiring, seed cleanup, embedding glue, and public helper functions;
  reduced [backend/app/services/task_planner_service.py](/D:/AIWork/Elira_AI/backend/app/services/task_planner_service.py) and [backend/app/services/rag_memory_service.py](/D:/AIWork/Elira_AI/backend/app/services/rag_memory_service.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  task-planner and RAG-memory concrete persistence wiring now sits under `application/*/service.py`, and old service imports remain backward-compatible.

### 44. Agent sandbox policy extraction
- Status: completed
- Scope: moved agent sandbox preflight policy out of the legacy service layer while preserving legacy service imports and Agent OS behavior.
- Finish:
  added [backend/app/application/agent_registry/sandbox.py](/D:/AIWork/Elira_AI/backend/app/application/agent_registry/sandbox.py) for agent id resolution, sandbox policy errors, tool/context/rate preflight checks, and sandbox block recording;
  updated [backend/app/application/workflows/step_results.py](/D:/AIWork/Elira_AI/backend/app/application/workflows/step_results.py), [backend/app/domain/workflows/step_executor.py](/D:/AIWork/Elira_AI/backend/app/domain/workflows/step_executor.py), and [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) to use the application sandbox module;
  reduced [backend/app/services/agent_sandbox.py](/D:/AIWork/Elira_AI/backend/app/services/agent_sandbox.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  sandbox policy now sits under `application/agent_registry`, and old service imports remain backward-compatible.

### 45. Elira state and settings wiring extraction
- Status: completed
- Scope: moved Elira chat/settings SQLite wiring out of the legacy service layer while preserving existing state routes and service imports.
- Finish:
  added [backend/app/application/elira_memory/service.py](/D:/AIWork/Elira_AI/backend/app/application/elira_memory/service.py) for Elira chat/message/settings table wiring over `application/elira_memory/runtime`;
  added [backend/app/application/elira_memory/settings.py](/D:/AIWork/Elira_AI/backend/app/application/elira_memory/settings.py) for settings and route-model-map persistence;
  updated state/runtime/persona/config imports to use the application modules;
  reduced [backend/app/services/elira_memory_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_memory_sqlite.py) and [backend/app/services/elira_settings_sqlite.py](/D:/AIWork/Elira_AI/backend/app/services/elira_settings_sqlite.py) to compatibility facades.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  Elira chat state and settings persistence wiring now sits under `application/elira_memory`, and old service imports remain backward-compatible.

### 46. Persona service facade extraction
- Status: completed
- Scope: moved persona prompt/service orchestration out of the legacy service layer while preserving persona routes, chat imports, and legacy service imports.
- Finish:
  added [backend/app/application/persona/service.py](/D:/AIWork/Elira_AI/backend/app/application/persona/service.py) for `build_persona_prompt` and persona store/evolution public exports;
  updated persona route, chat finalization, Ollama chat runtime, profile previews, core LLM, domain orchestrator runtime, and agent service imports to use `application/persona/service`;
  reduced [backend/app/services/persona_service.py](/D:/AIWork/Elira_AI/backend/app/services/persona_service.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  persona service orchestration now sits under `application/persona`, and old service imports remain backward-compatible.

### 47. Skills extra runtime extraction
- Status: completed
- Scope: integrated the Claude `skills_extra` extraction into the current refactor branch while preserving legacy service imports.
- Finish:
  added [backend/app/application/skills_extra/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/skills_extra/runtime.py) for archive, conversion, regex, translation, CSV analysis, and webhook helper runtime;
  added [backend/app/application/skills_extra/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/skills_extra/__init__.py) with the public runtime exports;
  reduced [backend/app/services/skills_extra.py](/D:/AIWork/Elira_AI/backend/app/services/skills_extra.py) to a compatibility facade.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  `skills_extra` implementation now sits under `application/skills_extra`, and old service imports remain backward-compatible.

### 48. Elira supervisor route runtime extraction
- Status: completed
- Scope: integrated the Claude `elira_supervisor` route split into the current refactor branch while preserving the public supervisor API routes.
- Finish:
  added [backend/app/application/elira_supervisor/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_supervisor/runtime.py) for supervisor DB bootstrap, path validation, planning, step building, persistence, history reads, and execute/run payload assembly;
  added [backend/app/application/elira_supervisor/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_supervisor/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/elira_supervisor.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_supervisor.py) to a FastAPI shell with request models, HTTP error translation, and delegating handlers.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  supervisor business and persistence logic now sits under `application/elira_supervisor`, while `/api/elira/supervisor/*` remains route-compatible.

### 49. Elira phase19 route runtime extraction
- Status: completed
- Scope: integrated the Claude `elira_phase19` route split into the current refactor branch while preserving the public phase19 API routes.
- Finish:
  added [backend/app/application/elira_phase19/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase19/runtime.py) for phase19 DB bootstrap, project scanning, plan/reasoning/file-operation builders, verification summary, persistence, history reads, and run payload assembly;
  added [backend/app/application/elira_phase19/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase19/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/elira_phase19.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase19.py) to a FastAPI shell with the request model and delegating handlers.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  phase19 business and persistence logic now sits under `application/elira_phase19`, while `/api/elira/phase19/*` remains route-compatible.

### 50. Elira phase20 route runtime extraction
- Status: completed
- Scope: continued the phase route split by moving phase20 planning/execution helpers out of the FastAPI route while preserving the public phase20 endpoints.
- Finish:
  added [backend/app/application/elira_phase20/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase20/runtime.py) for phase20 DB bootstrap, project scanning, reasoning/planner/coder/reviewer/tester/execution builders, persistence, history reads, and run payload assembly;
  added [backend/app/application/elira_phase20/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase20/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/elira_phase20.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase20.py) to a FastAPI shell with the request model and delegating handlers.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  phase20 business and persistence logic now sits under `application/elira_phase20`, while `/api/elira/phase20/*` remains route-compatible.

### 51. Elira phase21 route runtime extraction
- Status: completed
- Scope: completed the phase19/20/21 route split by moving phase21 controller/runtime logic out of the FastAPI route while preserving the public phase21 endpoints.
- Finish:
  added [backend/app/application/elira_phase21/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase21/runtime.py) for phase21 DB bootstrap, controller building, persistence, history reads, and run payload assembly;
  added [backend/app/application/elira_phase21/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase21/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/elira_phase21.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase21.py) to a FastAPI shell with the request model and delegating handlers.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  phase21 business and persistence logic now sits under `application/elira_phase21`, while `/api/elira/phase21/*` remains route-compatible.

### 52. Elira phase20 queue and execution-state runtime extraction
- Status: completed
- Scope: continued integrating the non-overlapping Claude route-split work by moving phase20 preview queue and execution-state logic out of FastAPI route modules.
- Finish:
  added [backend/app/application/elira_phase20_queue/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase20_queue/runtime.py) for preview queue payload assembly;
  added [backend/app/application/elira_phase20_state/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_phase20_state/runtime.py) for execution-state DB bootstrap, checkpoint/rollback building, persistence, and list reads;
  added package exports for both runtimes and reduced [backend/app/api/routes/elira_phase20_queue.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase20_queue.py) plus [backend/app/api/routes/elira_phase20_state.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_phase20_state.py) to FastAPI shells.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  phase20 queue/state business and persistence logic now sits under `application/elira_phase20_*`, while `/api/elira/phase20/preview-queue` and `/execution-state*` remain route-compatible.

### 53. Elira execute and memory runtime extraction
- Status: completed
- Scope: moved the `/api/elira/execute` mode-reply builder and `/api/elira/memory/*` SQLite CRUD out of the FastAPI route while preserving existing response strings and DB table usage.
- Finish:
  added [backend/app/application/elira_execute/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_execute/runtime.py) for `memory_store` bootstrap, mode reply construction, memory listing, save, and delete operations;
  added [backend/app/application/elira_execute/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_execute/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/elira_execute.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_execute.py) to request models and delegating handlers.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  Elira execute/memory route business and persistence logic now sits under `application/elira_execute`, while `/api/elira/execute` and `/api/elira/memory/*` remain route-compatible.

### 54. Elira task runner and devtools runtime extraction
- Status: completed
- Scope: continued the Claude route-split queue by moving task-runner planning/history logic and Elira devtools filesystem/project-map/patch-plan logic out of FastAPI route modules without changing public endpoints.
- Finish:
  added [backend/app/application/elira_task_runner/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_task_runner/runtime.py) for task plan building, supervisor pipeline assembly, `task_runs` persistence, and history reads;
  added [backend/app/application/elira_devtools/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_devtools/runtime.py) for project scanning, import parsing, guarded filesystem operations, and patch-plan payload assembly;
  added package exports for both runtimes and reduced [backend/app/api/routes/elira_task_runner.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_task_runner.py) plus [backend/app/api/routes/elira_devtools.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_devtools.py) to FastAPI shells with request models and HTTP error translation.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  task runner and devtools business logic now sits under `application/elira_task_runner` and `application/elira_devtools`, while `/api/elira/task/*`, `/api/elira/project/map`, `/api/elira/fs/*`, and `/api/elira/patch/plan` remain route-compatible.

### 55. Workspace file-ops runtime extraction
- Status: completed
- Scope: continued the route-split queue by moving workspace file operation logic out of `file_ops.py`; the paired Claude `library_sqlite` split was skipped because this branch already delegates `/api/lib/*` to `application/library/runtime.py`.
- Finish:
  added [backend/app/application/file_ops/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/file_ops/runtime.py) for safe workspace path resolution, write/read/tree/diff/mkdir/delete operations, and runtime error payloads;
  added [backend/app/application/file_ops/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/file_ops/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/file_ops.py](/D:/AIWork/Elira_AI/backend/app/api/routes/file_ops.py) to request models, route handlers, and HTTP error translation.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  `/api/file-ops/*` remains route-compatible while workspace filesystem behavior now sits under `application/file_ops`.

### 56. File text extraction runtime extraction
- Status: completed
- Scope: continued the file extraction split by moving PDF/DOCX/XLSX/ZIP/text extraction helpers out of the `/api/files` route while preserving the existing response shape and extractor messages.
- Finish:
  added [backend/app/application/file_extract/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/file_extract/runtime.py) for `TEXT_EXTS`, PDF/DOCX/XLSX/ZIP/plain-text extraction helpers, and `extract_file` dispatch;
  added [backend/app/application/file_extract/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/file_extract/__init__.py) with the runtime public exports;
  reduced [backend/app/api/routes/files.py](/D:/AIWork/Elira_AI/backend/app/api/routes/files.py) to an async upload reader and FastAPI error wrapper.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  `/api/files/extract-text` remains route-compatible while file text extraction logic now sits under `application/file_extract`.

### 57. Agent OS service runtime alias extraction
- Status: completed
- Scope: finished the incomplete Claude service-runtime extraction by moving Event Bus, Monitoring, Tool Registry, and Workflow Engine service state/wiring into application runtimes.
- Finish:
  added [backend/app/application/event_bus/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/event_bus/runtime.py) for event bus DB bootstrap, conversion helpers, event/message/subscription wrappers, and module-level state;
  added [backend/app/application/monitoring/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/monitoring/runtime.py) for agent limit seeding, metrics, resource usage, sandbox block recording, and Agent OS health/dashboard wrappers;
  added [backend/app/application/tool_registry/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/tool_registry/runtime.py) for tool registry DB bootstrap, handlers, builtin seeding, CRUD, execution, and validation;
  added [backend/app/application/workflow_engine/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/workflow_engine/runtime.py) plus package init for workflow-engine DB path/state and workflow template/run wrappers;
  reduced [backend/app/services/event_bus.py](/D:/AIWork/Elira_AI/backend/app/services/event_bus.py), [backend/app/services/agent_monitor.py](/D:/AIWork/Elira_AI/backend/app/services/agent_monitor.py), [backend/app/services/tool_registry.py](/D:/AIWork/Elira_AI/backend/app/services/tool_registry.py), and [backend/app/services/workflow_engine.py](/D:/AIWork/Elira_AI/backend/app/services/workflow_engine.py) to compatibility aliases that preserve mutable module state such as `DB_PATH`.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  Agent OS service facade code now lives under `application/*/runtime.py`, while legacy `app.services.*` imports remain compatible.

### 58. Elira phase naming compatibility refactor planned
- Status: completed
- Scope: recorded the requested follow-up to replace milestone-style `elira_phase*` module names with domain names as a separate compatibility refactor.
- Finish:
  updated [docs/WORKPLAN_CODEX_CLAUDE.md](/D:/AIWork/Elira_AI/docs/WORKPLAN_CODEX_CLAUDE.md) with a planned task for renaming `elira_phase19`, `elira_phase20`, `elira_phase20_queue`, `elira_phase20_state`, and `elira_phase21` while preserving legacy route paths and import aliases;
  documented the current domain meaning: multi-file dev loop, planner/coder/reviewer/tester execution loop, preview queue, checkpoint/rollback execution state, and controller/orchestration.
- Verification:
  `git diff --cached --check` after staging.
- Result:
  the non-engineering `elira_phase*` naming issue is now tracked as an explicit future compatibility refactor instead of being mixed into unrelated runtime extraction work.

### 59. Elira phase module compatibility rename
- Status: completed
- Scope: implemented the planned compatibility rename for milestone-style `elira_phase*` modules without changing public `/api/elira/phase*` route paths or SQLite table names.
- Finish:
  added domain-named route modules [backend/app/api/routes/elira_multi_file_loop.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_multi_file_loop.py), [backend/app/api/routes/elira_execution_loop.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_execution_loop.py), [backend/app/api/routes/elira_preview_queue.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_preview_queue.py), [backend/app/api/routes/elira_execution_state.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_execution_state.py), and [backend/app/api/routes/elira_execution_controller.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_execution_controller.py);
  added matching application packages under `application/elira_multi_file_loop`, `application/elira_execution_loop`, `application/elira_preview_queue`, `application/elira_execution_state`, and `application/elira_execution_controller`;
  rewired [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py) to register the domain-named route modules;
  reduced the old `elira_phase*` route and application modules to compatibility aliases so legacy imports keep working.
- Verification:
  `python -m compileall backend/app`;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed.
- Result:
  active code now uses engineer-facing names for multi-file loop, execution loop, preview queue, execution state, and execution controller while preserving legacy route/import compatibility.

### 60. Elira patch runtime extraction
- Status: completed
- Scope: continued the route-split queue by moving `/api/elira/patch` filesystem patching, backup, diff, verification, and history persistence logic out of the FastAPI route.
- Finish:
  added [backend/app/application/elira_patch/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/elira_patch/runtime.py) for project path guards, patch backup/apply/rollback, batch apply/verify, diff stats, and `patch_history` persistence;
  added [backend/app/application/elira_patch/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/elira_patch/__init__.py) with runtime exports;
  reduced [backend/app/api/routes/elira_patch.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_patch.py) to request models, endpoint wiring, and HTTP error translation.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted `/api/elira/patch` route smoke for diff, verify, and missing-file HTTP translation -> passed.
- Result:
  `/api/elira/patch/*` remains route-compatible while patch runtime behavior now sits under `application/elira_patch`.

### 61. Terminal route runtime extraction
- Status: completed
- Scope: continued the route-split queue by moving `/api/terminal` command execution, cwd state, Windows output decoding, blocked command checks, and timeout handling out of the FastAPI route.
- Finish:
  added [backend/app/application/terminal/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/terminal/runtime.py) for terminal workspace state, `exec_command`, `change_dir`, `get_cwd`, and Windows decode helpers;
  added [backend/app/application/terminal/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/terminal/__init__.py) with runtime exports;
  reduced [backend/app/api/routes/terminal.py](/D:/AIWork/Elira_AI/backend/app/api/routes/terminal.py) to request models and endpoint wiring.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted `/api/terminal` route smoke for cwd, empty command, blocked command, and `cd` -> passed.
- Result:
  `/api/terminal/*` remains route-compatible while terminal runtime behavior now sits under `application/terminal`.

### 62. Dashboard route runtime extraction
- Status: completed
- Scope: continued after Claude's separate worktree by applying the next small route split locally instead of merging the wide divergent Claude branch.
- Finish:
  added [backend/app/application/dashboard/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/dashboard/runtime.py) for run-history aggregation, daily activity, memory/chat/plugin counters, and dashboard response assembly;
  added [backend/app/application/dashboard/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/dashboard/__init__.py) with runtime exports;
  reduced [backend/app/api/routes/dashboard_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/dashboard_routes.py) to endpoint wiring.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted `/api/dashboard/stats` route smoke for response keys and 14-day activity shape -> passed.
- Result:
  `/api/dashboard/stats` remains route-compatible while dashboard aggregation now sits under `application/dashboard`.

### 63. Tools exec route runtime extraction
- Status: completed
- Scope: continued the route-split queue by moving `/api/tools` Python execution delegation, code analysis, and run-history lookup out of the FastAPI route.
- Finish:
  added [backend/app/application/tools_exec/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/tools_exec/runtime.py) for `run_python`, `analyze_code`, and `get_run_history`;
  added [backend/app/application/tools_exec/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/tools_exec/__init__.py) with runtime exports;
  reduced [backend/app/api/routes/tools_exec.py](/D:/AIWork/Elira_AI/backend/app/api/routes/tools_exec.py) to request models and endpoint wiring.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted `/api/tools` route smoke for Python/TypeScript analysis and run-history response shape -> passed.
- Result:
  `/api/tools/*` remains route-compatible while tools-exec runtime behavior now sits under `application/tools_exec`.

### 64. Route service-facade import cleanup
- Status: completed
- Scope: removed remaining route-to-service-facade imports where an application/runtime implementation already exists, without changing public routes.
- Finish:
  updated [backend/app/api/routes/pdf_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/pdf_routes.py) to call `application/pdf/runtime.py` directly;
  updated [backend/app/application/file_extract/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/file_extract/runtime.py) to use the PDF application runtime directly;
  updated [backend/app/api/routes/task_planner_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/task_planner_routes.py) to use `application/task_planner/service.py`;
  updated [backend/app/api/routes/autopipeline_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/autopipeline_routes.py) to use `application/autopipeline/runtime.py` while preserving lazy imports.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted task planner/autopipeline route smoke -> passed.
- Result:
  PDF, file extraction, task planner, and autopipeline paths now bypass service compatibility facades where application-layer modules are already available.

### 65. Web search route runtime extraction
- Status: completed
- Scope: continued the route-split queue by moving `/api/web` search orchestration wrappers and engine metadata out of the FastAPI route.
- Finish:
  added [backend/app/application/web_search/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/web_search/runtime.py) for default engine normalization, search/deep-search/news/fetch delegation, and engine metadata;
  added [backend/app/application/web_search/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/web_search/__init__.py) with runtime exports;
  reduced [backend/app/api/routes/web_search_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/web_search_routes.py) to request models and endpoint wiring.
- Verification:
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  targeted `/api/web/engines` route smoke and engine normalization check -> passed.
- Result:
  `/api/web/*` remains route-compatible while web-search route orchestration now sits under `application/web_search`.

### 66. Multi-agent workflow facade cleanup
- Status: completed
- Scope: continued the compatibility-import cleanup after confirming `services/image_gen.py` is already a thin facade over `application/media/flux_schnell_runtime.py`.
- Finish:
  reduced [backend/app/services/multi_agent_chain.py](/D:/AIWork/Elira_AI/backend/app/services/multi_agent_chain.py) to the public `run_multi_agent(...)` compatibility wrapper over `application.workflows.multi_agent.run_multi_agent_workflow`;
  updated [backend/app/api/routes/advanced_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/advanced_routes.py) so `/api/advanced/multi-agent` calls the application workflow runtime directly;
  removed the dead inline legacy multi-agent implementation from the service facade.
- Verification:
  `python -m py_compile backend/app/services/multi_agent_chain.py backend/app/api/routes/advanced_routes.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_agent_os_phase4.WorkflowCompatibilityShimTest` -> 2 tests OK;
  targeted `/api/advanced/multi-agent` route smoke with mocked workflow runtime -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Multi-agent public behavior stays workflow-backed while the obsolete service monolith body is gone.

### 67. Route imports for image and RAG runtimes
- Status: completed
- Scope: continued small compatibility-import cleanup for routes that were still calling already-extracted application runtimes through service facades.
- Finish:
  updated [backend/app/api/routes/image_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/image_routes.py) to call `application.media.flux_schnell_runtime` directly for generate/status/unload;
  updated [backend/app/api/routes/advanced_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/advanced_routes.py) to call `application.rag_memory.service` directly for RAG add/search/list/delete/stats.
- Verification:
  `python -m py_compile backend/app/api/routes/advanced_routes.py backend/app/api/routes/image_routes.py` -> passed;
  targeted advanced RAG and image route smoke with mocked application runtimes -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Image and advanced RAG endpoints keep the same public route behavior while bypassing redundant service compatibility facades.

### 68. Route imports for runtime, profiles, memory, and library
- Status: completed
- Scope: continued compatibility-import cleanup in the shared Codex branch after checking Claude's `claude/extract-skills-extra` branch and confirming it is too broad to merge wholesale.
- Finish:
  updated [backend/app/api/routes/runtime.py](/D:/AIWork/Elira_AI/backend/app/api/routes/runtime.py) to call `application.runtime.status` directly;
  updated [backend/app/api/routes/profiles.py](/D:/AIWork/Elira_AI/backend/app/api/routes/profiles.py) to call `application.persona.profiles` directly;
  updated [backend/app/api/routes/memory.py](/D:/AIWork/Elira_AI/backend/app/api/routes/memory.py) to call `application.memory.service` directly;
  updated [backend/app/api/routes/library.py](/D:/AIWork/Elira_AI/backend/app/api/routes/library.py) to call `application.library.runtime` directly.
- Verification:
  `python -m py_compile backend/app/api/routes/runtime.py backend/app/api/routes/profiles.py backend/app/api/routes/memory.py backend/app/api/routes/library.py` -> passed;
  targeted mocked route smoke for runtime/profiles/memory/library -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Four more routes now bypass redundant service facades while preserving public route behavior.

### 69. Route imports for Telegram, smart memory, and extra skills
- Status: completed
- Scope: continued route compatibility-import cleanup for service facades that already re-export application packages.
- Finish:
  updated [backend/app/api/routes/telegram_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/telegram_routes.py) lazy imports to call `application.telegram`;
  updated [backend/app/api/routes/smart_memory_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/smart_memory_routes.py) to call `application.smart_memory`;
  updated [backend/app/api/routes/skills_extra_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/skills_extra_routes.py) to call `application.skills_extra` and `application.plugins`.
- Verification:
  `python -m py_compile backend/app/api/routes/telegram_routes.py backend/app/api/routes/smart_memory_routes.py backend/app/api/routes/skills_extra_routes.py` -> passed;
  targeted mocked route smoke for Telegram, smart-memory, extra-skill, and plugin endpoints -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Telegram, smart-memory, extra-skill, and plugin routes now bypass redundant service facades while keeping route contracts stable.

### 70. Debug library route import cleanup
- Status: completed
- Scope: removed the remaining debug route dependency on the library service facade where the application runtime is already available.
- Finish:
  updated [backend/app/api/routes/debug.py](/D:/AIWork/Elira_AI/backend/app/api/routes/debug.py) so `/api/debug/library` imports `list_library_files` and `build_library_context` from `application.library.runtime`.
- Verification:
  `python -m py_compile backend/app/api/routes/debug.py` -> passed;
  targeted mocked `/api/debug/library` route smoke -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  The debug library route now bypasses the redundant library service facade without changing response shape.

### 71. Git and Ollama application facades
- Status: completed
- Scope: continued route compatibility cleanup for routes that still depended on service facades backed directly by infrastructure modules.
- Finish:
  added [backend/app/application/git/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/git/runtime.py) and [backend/app/application/git/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/git/__init__.py) as the application facade for Git helpers;
  added [backend/app/application/ollama_models/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/ollama_models/runtime.py) and [backend/app/application/ollama_models/__init__.py](/D:/AIWork/Elira_AI/backend/app/application/ollama_models/__init__.py) as the application facade for Ollama model listing;
  updated [backend/app/api/routes/git_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/git_routes.py), [backend/app/api/routes/models.py](/D:/AIWork/Elira_AI/backend/app/api/routes/models.py), and [backend/app/api/routes/elira_state.py](/D:/AIWork/Elira_AI/backend/app/api/routes/elira_state.py) to call those application facades;
  updated `services/git_service.py`, `services/models_service.py`, and `services/ollama_runtime_service.py` to remain compatibility facades over the new application modules.
- Verification:
  `python -m py_compile` on the new facades, touched routes, and touched service facades -> passed;
  targeted mocked route smoke for Git, `/api/models`, and `/api/elira/models` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Git and Ollama model routes now depend on application facades instead of service facades while legacy service imports remain compatible.

### 72. Skills route import cleanup
- Status: completed
- Scope: continued route compatibility cleanup for the skills service facade.
- Finish:
  updated [backend/app/api/routes/skills_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/skills_routes.py) to call `application.skills` directly;
  updated [backend/app/api/routes/project_brain.py](/D:/AIWork/Elira_AI/backend/app/api/routes/project_brain.py) so project-brain capability status imports `screenshot_capability_status` from `application.skills`.
- Verification:
  `python -m py_compile backend/app/api/routes/skills_routes.py backend/app/api/routes/project_brain.py` -> passed;
  targeted mocked route smoke for skills endpoints and project-brain status -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Skills routes and project-brain capability checks now bypass the redundant skills service facade.

### 73. Agent OS route import cleanup
- Status: completed
- Scope: continued route compatibility cleanup for Agent OS routes whose service modules are already aliases over application runtimes.
- Finish:
  updated [backend/app/api/routes/agent_monitor_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/agent_monitor_routes.py) to import `application.monitoring.runtime`;
  updated [backend/app/api/routes/event_bus_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/event_bus_routes.py) to import `application.event_bus.runtime`;
  updated [backend/app/api/routes/tool_registry_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/tool_registry_routes.py) to import `application.tool_registry.runtime`.
- Verification:
  `python -m py_compile backend/app/api/routes/agent_monitor_routes.py backend/app/api/routes/event_bus_routes.py backend/app/api/routes/tool_registry_routes.py` -> passed;
  targeted mocked Agent OS route smoke for monitor, event bus, and tool registry -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Agent OS monitor, event bus, and tool registry routes now bypass redundant service aliases while preserving route contracts.

### 74. Chat agent runtime facade
- Status: completed
- Scope: moved chat/agent route orchestration entry points out of the service namespace while keeping legacy imports patch-compatible.
- Finish:
  added [backend/app/application/chat/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/chat/runtime.py) as the chat agent runtime composition module;
  updated [backend/app/services/agents_service.py](/D:/AIWork/Elira_AI/backend/app/services/agents_service.py) to remain a `sys.modules` compatibility alias over `application.chat.runtime`;
  updated [backend/app/api/routes/agents.py](/D:/AIWork/Elira_AI/backend/app/api/routes/agents.py) and [backend/app/api/routes/chat.py](/D:/AIWork/Elira_AI/backend/app/api/routes/chat.py) to import `run_agent` / `run_agent_stream` from `application.chat.runtime`.
- Verification:
  targeted chat runtime compatibility smoke confirmed `app.services.agents_service` and `app.application.chat.runtime` share one module object;
  `python -m py_compile backend/app/application/chat/runtime.py backend/app/services/agents_service.py backend/app/api/routes/agents.py backend/app/api/routes/chat.py` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  `/api/agents` and `/api/chat` no longer import chat agent entry points through `services.agents_service`, while existing legacy service patch points remain compatible.

### 75. Agent Registry runtime facade
- Status: completed
- Scope: moved the Agent Registry route off the service namespace while preserving legacy mutable test/runtime state.
- Finish:
  added [backend/app/application/agent_registry/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/agent_registry/runtime.py) as the Agent Registry runtime module with DB path wiring, built-in seeding, and CRUD/run/state functions;
  updated [backend/app/services/agent_registry.py](/D:/AIWork/Elira_AI/backend/app/services/agent_registry.py) to remain a `sys.modules` compatibility alias over `application.agent_registry.runtime`;
  updated [backend/app/api/routes/agent_registry_routes.py](/D:/AIWork/Elira_AI/backend/app/api/routes/agent_registry_routes.py) to import the runtime directly.
- Verification:
  targeted Agent Registry compatibility smoke confirmed legacy service imports and application runtime share one module object, including `DB_PATH` and `_init_db`;
  `python -m py_compile backend/app/application/agent_registry/runtime.py backend/app/services/agent_registry.py backend/app/api/routes/agent_registry_routes.py` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  `agent_registry_routes.py` no longer imports through `services.agent_registry`, while legacy callers can still patch and mutate the same runtime module.

### 76. Agent OS internal runtime imports
- Status: completed
- Scope: continued cleanup after route-layer service imports were removed; moved startup and Agent OS internal helpers to existing application runtimes.
- Finish:
  updated [backend/app/main.py](/D:/AIWork/Elira_AI/backend/app/main.py) startup seeding and runtime initialization imports to use application runtimes;
  updated Agent OS sandbox, chat monitoring/event recording, workflow events/execution, monitoring runtime/reporting, and workflow step executor imports to use application runtimes where compatibility-safe;
  intentionally left workflow tool execution on `services.tool_service` because existing workflow tests patch that facade.
- Verification:
  `python -m py_compile` on all touched runtime/startup files -> passed;
  targeted startup/workflow compatibility smoke confirmed `app.main` imports, service alias patching still affects workflow agent step execution, and the application chat runtime remains the active module object;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Agent OS startup and internal application helpers now depend on application runtimes instead of service aliases, without breaking legacy workflow patch points.

### 77. Chat runtime direct application dependencies
- Status: completed
- Scope: reduced service-alias imports inside the newly extracted chat runtime without changing public chat entry points.
- Finish:
  updated [backend/app/application/chat/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/chat/runtime.py) to import chat execution, planner, reflection, response cache, run history, smart memory, RAG context, and Agent Registry resolution from application modules directly;
  intentionally left `services.tool_service` for tool execution compatibility.
- Verification:
  targeted chat runtime direct-import smoke confirmed legacy `agents_service` still shares the runtime module and patching runtime globals remains effective;
  `python -m py_compile backend/app/application/chat/runtime.py backend/app/services/agents_service.py` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Chat runtime composition now depends on application modules for extracted subsystems while preserving the legacy service alias behavior.

### 78. Auto-skills direct application imports
- Status: completed
- Scope: removed remaining service-facade imports from the chat auto-skills dispatcher where application runtimes already exist.
- Finish:
  updated [backend/app/application/chat/auto_skills.py](/D:/AIWork/Elira_AI/backend/app/application/chat/auto_skills.py) to import skills, extra skills, plugins, Git helpers, and image generation/status from application modules directly.
- Verification:
  `python -m py_compile backend/app/application/chat/auto_skills.py` -> passed;
  targeted auto-skills direct-import smoke with mocked HTTP, docx generation, and Git status paths -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  The auto-skills dispatcher now bypasses redundant skills/plugin/git/image service facades while keeping lazy import behavior.

### 79. Autopipeline, Telegram, and web-query runtime imports
- Status: completed
- Scope: removed additional non-route service-facade imports where direct runtime modules already exist.
- Finish:
  updated [backend/app/application/autopipeline/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/autopipeline/runtime.py) to import chat runtime, multi-search, and plugins directly;
  updated [backend/app/application/telegram/runtime.py](/D:/AIWork/Elira_AI/backend/app/application/telegram/runtime.py) to import chat runtime directly;
  updated [backend/app/infrastructure/search/web_query.py](/D:/AIWork/Elira_AI/backend/app/infrastructure/search/web_query.py) to import temporal intent detection from `application.chat.temporal_intent`.
- Verification:
  `python -m py_compile backend/app/application/autopipeline/runtime.py backend/app/application/telegram/runtime.py backend/app/infrastructure/search/web_query.py` -> passed;
  targeted direct-import smoke confirmed autopipeline prompt execution still sees legacy `agents_service.run_agent` patching, web-query cleaning works, and telegram runtime imports -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 87 tests OK.
- Result:
  Autopipeline, Telegram, and web-query helpers now bypass redundant service facades while preserving legacy chat patch compatibility.

### 80. Selective Claude test import — memory context/search
- Status: completed
- Scope: selectively imported the safe tests-only part of Claude commit `90f5573` without merging the broad `claude/extract-skills-extra` branch.
- Finish:
  added [backend/tests/test_memory_context_search_pure.py](/D:/AIWork/Elira_AI/backend/tests/test_memory_context_search_pure.py) covering `application.memory.context` pure helpers and `application.memory.search` callback-driven helper paths.
- Verification:
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_memory_context_search_pure` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 134 tests OK.
- Result:
  One Claude tests-only commit was integrated on the Codex branch with local documentation numbering preserved.

### 81. Selective Claude test import — memory web knowledge/bootstrap
- Status: completed
- Scope: selectively imported the safe tests-only part of Claude commit `b8dc5c5` without merging the broad `claude/extract-skills-extra` branch.
- Finish:
  added [backend/tests/test_memory_web_knowledge_bootstrap.py](/D:/AIWork/Elira_AI/backend/tests/test_memory_web_knowledge_bootstrap.py) covering `application.memory.web_knowledge` pure string helpers and `application.memory.bootstrap` settings file I/O.
- Verification:
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_memory_web_knowledge_bootstrap` -> passed;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 181 tests OK.
- Result:
  A second Claude tests-only commit was integrated on the Codex branch with local documentation numbering preserved.

### 82. Selective Claude test import — chat auto-skills/Agent OS helpers
- Status: completed
- Scope: selectively imported the safe tests-only part of Claude commit `b5efaab` without merging the broad `claude/extract-skills-extra` branch.
- Finish:
  added [backend/tests/test_chat_auto_skills_agent_os.py](/D:/AIWork/Elira_AI/backend/tests/test_chat_auto_skills_agent_os.py) covering `application.chat.auto_skills` trigger paths and `application.chat.agent_os` helper/fire-and-forget paths.
- Verification:
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_chat_auto_skills_agent_os` -> 32 tests OK;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 213 tests OK.
- Result:
  A third Claude tests-only commit was integrated on the Codex branch with local documentation numbering preserved.

### 83. Selective Claude test import — workflow lifecycle/multi-agent helpers
- Status: completed
- Scope: selectively imported the safe tests-only part of Claude commit `e1e3084` without merging the broad `claude/extract-skills-extra` branch.
- Finish:
  added [backend/tests/test_workflows_lifecycle_multi_agent.py](/D:/AIWork/Elira_AI/backend/tests/test_workflows_lifecycle_multi_agent.py) covering `application.workflows.lifecycle` orchestration helpers and `application.workflows.multi_agent` built-in template helpers.
- Verification:
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest backend.tests.test_workflows_lifecycle_multi_agent` -> 44 tests OK;
  `python -m compileall backend/app` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe scripts\smoke_contract_check.py` -> passed;
  `D:\AIWork\Elira_AI\backend\.venv\Scripts\python.exe -m unittest discover -s backend\tests -p "test_*.py"` -> 257 tests OK.
- Result:
  A fourth Claude tests-only commit was integrated on the Codex branch with local documentation numbering preserved.
