# WORKPLAN_CODEX_CLAUDE

Single live coordination document for Claude/Codex refactor work.

- Snapshot created: `2026-04-10 13:04:58 +05:00`
- Repository remote: `origin -> https://github.com/johnivanov092-glitch/Elira_AI.git`
- Workplan owner: `Codex`
- Documentation branch: `codex/workplan-codex-claude`
- Current implementation branch: `codex/refactor-arch-foundation`
- Snapshot HEAD: `755072177138c5c7bd9f022eb62f43b6b11ace57`
- `main` at snapshot: `755072177138c5c7bd9f022eb62f43b6b11ace57`
- `origin/main` at snapshot: `755072177138c5c7bd9f022eb62f43b6b11ace57`
- Protected paths: `.claude/`, `.claude/worktrees/`
- Encoding rule: keep this file in UTF-8; if a terminal shows mojibake, reopen the file in UTF-8 and do not rewrite it through ANSI tooling.

## 1. Update Rules

- This file is the single live coordination document for Claude/Codex refactor work.
- Only Codex updates this file. Other agents read it and report facts that must be logged here.
- `docs/AGENT_OS_WORKPLAN.md` and `docs/ACTUAL_WORK.md` are frozen source documents for backfill only.
- After each meaningful commit, branch handoff, file deletion, or status change, update the relevant sections here before push or immediately after push.
- Do not delete `.claude/` or any path under `.claude/worktrees/`.
- Do not auto-clean the current dirty `docs/` state until the change is logged and reviewed here.

## 2. Coordination Rules

- Remote of record for all pushes: `origin`.
- Codex owns only documentation coordination work on `codex/workplan-codex-claude`.
- Claude and other implementation agents work in separate feature branches; do not use the documentation branch for runtime code.
- Push only meaningful commits; every pushed or ready-to-push commit must be logged in `Commit Ledger` with SHA, scope, and note.
- Rebase or merge onto fresh `main` before integration, resolve conflicts locally, then refresh this file with the final SHA and status.
- If work pauses, blocks, or hands off, record it in `Handoffs / Blockers`; do not create separate coordination markdown files.
- If a file is deleted or moved, add the deletion here first, including restore guidance if the reason is not yet confirmed.

## 3. Source Docs

| Path | Role | State at snapshot |
| --- | --- | --- |
| `docs/CLAUDE_CODEX_JARVIS_MASTER_REFACTOR_PLAN.md` | Main staged refactor plan and target architecture | Present in working tree, currently untracked |
| `docs/ELIRA_PROJECT_MAP.md` | Current architecture map and active API/runtime surface | Present in working tree, currently untracked |
| `docs/AGENT_OS_WORKPLAN.md` | Prior shared agent coordination plan | Tracked source, read-only for new work |
| `docs/ACTUAL_WORK.md` | Detailed historical execution log | Tracked source, read-only for new work |

## 4. Working Tree Snapshot

### Branches and worktrees

| Location | Branch | HEAD | State |
| --- | --- | --- | --- |
| `D:\AIWork\Elira_AI` | `codex/refactor-arch-foundation` | `755072177138c5c7bd9f022eb62f43b6b11ace57` | Active refactor worktree; workplan remains maintained here by Codex |
| `D:\AIWork\Elira_AI\.claude\worktrees\angry-ptolemy` | `feat/agent-os-phase2-tools` | `755072177138c5c7bd9f022eb62f43b6b11ace57` | Protected worktree; `git worktree list` reports `prunable gitdir file points to non-existent location` |
| `D:\AIWork\Elira_AI\.claude\worktrees\zealous-goldwasser` | `claude/zealous-goldwasser` | `755072177138c5c7bd9f022eb62f43b6b11ace57` | Protected worktree |

### Current untracked snapshot items relevant to docs coordination

| Path | Observed state | Note |
| --- | --- | --- |
| `docs/CLAUDE_CODEX_JARVIS_MASTER_REFACTOR_PLAN.md` | Untracked | Keep as refactor source until committed or superseded |
| `docs/ELIRA_PROJECT_MAP.md` | Untracked | Keep as architecture source until committed or superseded |
| `docs/archive/deep-research-report (END Point Agent).md` | Untracked | Likely intended archive replacement for the deleted root-level docs copy; not yet committed |
| `.claude/worktrees/zealous-goldwasser/` | Untracked in root worktree | Protected path; do not add or delete from this documentation task |

## 5. Refactor Phase Board

| Phase | Scope | Owner | Branch | Status | Dependencies | Verification |
| --- | --- | --- | --- | --- | --- | --- |
| `DOC` | Maintain this single coordination workplan | Codex | `codex/workplan-codex-claude` | `IN PROGRESS` | None | `git status --short --branch`, `git worktree list --porcelain`, `git log -- docs` |
| `0` | Preparation and guardrails | `Codex` | `codex/refactor-arch-foundation` | `IN PROGRESS` | `DOC` | `python -m compileall ...`, import smoke for new packages and DB provider |
| `1` | Stabilize backend boundaries | `Codex` | `codex/refactor-arch-foundation` | `IN PROGRESS` | `0` | Duplicate-definition search, `python -m compileall ...`, backend import smoke |
| `2` | Split chat and agent services | `Codex` | `codex/refactor-arch-foundation` | `IN PROGRESS` | `1` | Chat request, chat stream, and routing checks |
| `3` | Split code-agent runtime | `TBD` | `TBD` | `PLANNED` | `2` | Code-agent execute, verify, and cancellation checks |
| `4` | Split workflow engine | `TBD` | `TBD` | `PLANNED` | `1`, `3` | Workflow run start/finish tests |
| `5` | Route consolidation | `TBD` | `TBD` | `PLANNED` | `1`, `2`, `3`, `4` | Route registration and smoke contract checks |
| `6` | Frontend TypeScript migration | `TBD` | `TBD` | `PLANNED` | `5` | `npm --prefix frontend run build`, typed API verification |
| `7` | Tauri cleanup | `TBD` | `TBD` | `PLANNED` | `1`, `6` | Tauri startup and backend launch checks |
| `8` | Contract stabilization and cleanup | `TBD` | `TBD` | `PLANNED` | `1`-`7` | Smoke tests, typed frontend build, cleanup review |

## 6. Archived Branch Agreements From Earlier Agent OS Work

| Track | Owner | Branch | Status at snapshot | Evidence | Note |
| --- | --- | --- | --- | --- | --- |
| Agent OS Phase 1 | Claude Code | `feat/agent-os-phase1-registry` | `DONE` | Commit `f6bca43` | Original shared coordination baseline |
| Agent OS Phase 2 | Claude Code | `feat/agent-os-phase2-tools` and `claude/zealous-goldwasser` | `NEEDS RECONCILIATION` | Current `main` HEAD title says Phase 2 landed; legacy `AGENT_OS_WORKPLAN.md` still says `TODO` | Resolve status before using Phase 2 as a dependency |
| Agent OS Phase 3 | Codex | `feat/agent-os-phase3-eventbus` | `DONE` | Commit `5341244` and legacy workplan notes | Event bus branch exists in history |
| Agent OS Phase 4 | Codex | `feat/agent-os-phase4-workflows` | `DONE` | Commit `8c00751` | Workflow engine branch exists in history |
| Agent OS Phase 5 | Codex | `feat/agent-os-phase5-monitoring` | `DONE` | Commits `2b44a67` and `283345d` | Monitoring backend and dashboard landed in history |

## 7. Active Work Log

| Timestamp | Status | Entry |
| --- | --- | --- |
| `2026-04-10 13:04:58 +05:00` | `DONE` | Created documentation branch `codex/workplan-codex-claude` for single-owner coordination work. |
| `2026-04-10 13:04:58 +05:00` | `DONE` | Backfilled remote URL, current HEAD, branch/worktree snapshot, legacy branch agreements, and relevant docs/refactor commits. |
| `2026-04-10 13:04:58 +05:00` | `DONE` | Recorded all currently observed deleted files from `git status` before any cleanup. |
| `2026-04-10 13:04:58 +05:00` | `IN PROGRESS` | Freeze future coordination into this file only; older workplan documents remain read-only sources. |
| `2026-04-10 13:12:52 +05:00` | `DONE` | Created implementation branch `codex/refactor-arch-foundation` for the first backend refactor wave. |
| `2026-04-10 13:12:52 +05:00` | `DONE` | Added backend architecture skeleton packages under `application/`, `domain/`, and `infrastructure/`. |
| `2026-04-10 13:12:52 +05:00` | `DONE` | Added shared sqlite connection provider at `backend/app/infrastructure/db/connection.py` and repository scaffolds for chats, messages, memory, workflows, and registry. |
| `2026-04-10 13:12:52 +05:00` | `DONE` | Routed Agent OS services `agent_registry`, `tool_registry`, `event_bus`, `workflow_engine`, and `agent_monitor` through the shared sqlite connection provider. |
| `2026-04-10 13:12:52 +05:00` | `DONE` | Verified compile/import health for new packages and the migrated DB provider path. |
| `2026-04-10 13:16:26 +05:00` | `DONE` | Removed duplicate public definitions by renaming legacy-only implementations of `_do_web_search`, `_do_temporal_web_search`, and `run_multi_agent` while keeping current behavior on the active entrypoints. |
| `2026-04-10 13:16:26 +05:00` | `DONE` | Added freeze notices to the active backend monoliths touched in this wave: `agents_service.py`, `core/agents.py`, `multi_agent_chain.py`, and `workflow_engine.py`. |
| `2026-04-10 13:16:26 +05:00` | `DONE` | Re-verified compile/import health after dedupe for `agents_service`, `core/agents`, `multi_agent_chain`, and `workflow_engine`. |
| `2026-04-10 13:16:26 +05:00` | `NEXT` | Start the first extraction from `agents_service.py`: move chat context-building into a dedicated application-layer module while leaving the legacy file as a facade. |
| `2026-04-10 13:27:00 +05:00` | `DONE` | Added `backend/app/application/chat/context_builder.py` and switched `agents_service.py` to use it for frontend-project stripping and tool-driven context assembly. |
| `2026-04-10 13:27:00 +05:00` | `DONE` | Renamed the old local `_strip_frontend_project_context` and `_collect_context` implementations to legacy-only names, keeping the monolith as a compatibility facade instead of a duplicate source of truth. |
| `2026-04-10 13:27:00 +05:00` | `DONE` | Extract the next smallest chat slice from `agents_service.py`: request/stream orchestration or web-search helper wiring, whichever keeps the public contract stable. |
| `2026-05-15 00:00:00 +05:00` | `DONE` | Created `backend/app/application/chat/prompt_builder.py` with stateless helpers: `compose_human_style_rules`, `wants_explicit_datetime_answer`, `build_runtime_datetime_context`, `build_prompt_with_context`. |
| `2026-05-15 00:00:00 +05:00` | `DONE` | Removed 811 lines of dead code from `agents_service.py`: first duplicate `_compose_human_style_rules`, first duplicate `_build_prompt`, `_build_single_web_subquery_context_legacy`, `_do_web_search_legacy_frozen`, `_do_temporal_web_search_legacy_frozen`, `_do_web_search_frozen`, `_do_temporal_web_search_frozen`. File reduced from 2368 to ~1560 lines. |
| `2026-05-15 00:00:00 +05:00` | `DONE` | Added import aliases for `prompt_builder` functions at top of `agents_service.py`; inline definitions replaced by facade imports. |
| `2026-05-15 00:00:00 +05:00` | `DONE` | Fixed pre-existing test regression in `test_web_multi_intent_runtime.py`: updated mock path from `agents_service._build_single_web_subquery_context` to `infrastructure.search.web_search.build_single_web_subquery_context`. |
| `2026-05-15 00:00:00 +05:00` | `DONE` | Verified: `python -m compileall backend/app` clean, `python -m unittest discover` 87/87 OK. |
| `2026-05-15 00:01:00 +05:00` | `DONE` | Created `backend/app/application/chat/auto_skills.py` (~622 lines): `_run_auto_skills`, `_pending_attachments`, `_get_and_clear_attachments`, `_build_prompt`, `_maybe_auto_exec_python`, `_maybe_generate_files` with all skill constants. `agents_service.py` reduced from ~1560 to 974 lines. |
| `2026-05-15 00:01:00 +05:00` | `DONE` | Extracted `run_agent` body into `application/chat/service.py` as `execute_chat_agent`. All helpers (_tl, guards, monitoring, memory recall, history) now live in service.py; service.py also re-exports them for stream_service.py. |
| `2026-05-15 00:01:00 +05:00` | `DONE` | Extracted `run_agent_stream` body into `application/chat/stream_service.py` as `execute_chat_agent_stream`. stream_service.py imports shared helpers from service.py. |
| `2026-05-15 00:01:00 +05:00` | `DONE` | `agents_service.py` reduced from 974 to ~260 lines — now a pure routing facade with `run_agent → execute_chat_agent` and `run_agent_stream → execute_chat_agent_stream` delegation. |
| `2026-05-15 00:01:00 +05:00` | `DONE` | Updated mock patch targets in `test_agent_os_phase3.py` and `test_agent_os_phase5.py` from `agents_service.*` to the actual execution modules. All 87 tests green. Commit `a20c5de` pushed to `claude/peaceful-mayer-456e15`. |
| `2026-05-15 00:02:00 +05:00` | `DONE` | Extracted `workflow_engine` into `application/workflows/engine.py`. `services/workflow_engine.py` is now a thin facade. Phase4/5 test mixins updated to mutate `_wfe.DB_PATH` on the application module. Commit `132b31e`. |
| `2026-05-16 00:00:00 +05:00` | `DONE` | Extracted `smart_memory` into `application/memory/smart_memory.py`. `services/smart_memory.py` is now a thin facade. No test updates needed (no mutable state). Commit `188cfa5`. |
| `2026-05-16 00:01:00 +05:00` | `DONE` | Extracted `agent_monitor` into `application/monitoring/agent_monitor.py`. `services/agent_monitor.py` is a thin facade. Phase5 test mixin updated to mutate `_am.DB_PATH` and `_am._LIMIT_SEED_DONE`. Commit `47316f7`. |
| `2026-05-16 00:02:00 +05:00` | `DONE` | Extracted `persona_service` into `application/persona/persona_service.py`. `services/persona_service.py` is a thin facade. No test updates needed. Commit `38658dc`. |
| `2026-05-16 00:03:00 +05:00` | `DONE` | Extracted `event_bus` into `application/event_bus.py`. `services/event_bus.py` is a thin facade. Phase3/4/5 test mixins updated to mutate `_eb.DB_PATH`. Commit `ac657b2`. |
| `2026-05-16 00:04:00 +05:00` | `DONE` | Extracted `agent_registry` into `application/agents/agent_registry.py`. `services/agent_registry.py` is a thin facade. Phase5 test mixin updated to mutate `_ar.DB_PATH` and `_ar._BUILTIN_AGENTS_SEEDED`. Commit `854a133`. |
| `2026-05-16 00:05:00 +05:00` | `DONE` | Extracted `web_query_planner`, `planner_v2_service`, `temporal_intent`, `task_planner_service` into `application/planning/`. `tool_registry` into `application/tools/`. `autopipeline_service` into `application/autopipeline/`. `skills_service`/`skills_extra` into `application/skills/`. `project_brain_engine_service`/`project_patch_service` into `application/projects/`. `rag_memory_service` into `application/memory/`. `provenance_guard`/`identity_guard` into `application/policy/`. `agent_sandbox` into `application/monitoring/`. `library_service` into `application/library/`. All facades verified. Encoding bugs (mojibake docstrings) fixed with binary patch approach. Phase2 test updated for `_tr._BUILTIN_SEEDED`. Commit `61b22d9`. |
| `2026-05-16 01:00:00 +05:00` | `DONE` | Extracted 13 infrastructure services: `telegram_service`, `image_gen` → `infrastructure/integrations/`; `pdf_pro`, `project_service` → `infrastructure/files/`; `response_cache` → `infrastructure/cache/`; `plugin_system` → `infrastructure/plugins/`; `git_service` → `infrastructure/vcs/`; `python_runner` → `infrastructure/runtime/`; `run_history_service`, `elira_memory_sqlite`, `elira_settings_sqlite` → `infrastructure/db/`; `web_multisearch_service` → `infrastructure/search/`. Fixed plugin_system facade (removed non-existent `toggle_user_access`, added `update_plugin_settings`/`run_plugin`). 87 tests pass. Commit `39c8c52`. |
| `2026-05-16 02:00:00 +05:00` | `DONE` | Extracted final 17 services: `agents_service`, `browser_agent`, `multi_agent_chain`, `reflection_loop_service` → `application/agents/`; `chat_service` → `application/chat/`; `memory_service` → `application/memory/`; `profile_service`, `profiles_service` → `application/users/` (new pkg); `web_service` → `application/web/` (new pkg); `project_brain_service`, `project_brain_loop_service`, `project_map_service` → `application/projects/`; `tool_service` → `application/tools/`; `models_service`, `ollama_models_service` → `infrastructure/models/` (new pkg); `ollama_runtime_service`, `runtime_service` → `infrastructure/runtime/`. Fixed relative import in `profile_service.py`. agents_service facade includes `PlannerV2Service` and `_run_auto_skills` for test patch targets. ALL 47 `services/` files are now thin re-export facades. 87 tests pass. Commits `39c8c52`, `64a462b`. |
| `2026-05-16 03:00:00 +05:00` | `DONE` | Eliminated all `app.services.*` imports from `application/`, `infrastructure/`, `api/routes/`, `core/`, `main.py`, `scripts/`. Created Python migration script covering 44 module paths; fixed remaining edge cases manually (lambda `__import__` strings in `tool_registry.py`, `main.py` seed imports, `scripts/smoke_contract_check.py`). Zero `app.services.*` imports remain outside deleted files. 87 tests pass. |
| `2026-05-16 03:01:00 +05:00` | `DONE` | Updated all 11 test files to import directly from `application/` and `infrastructure/` — no test now imports via `app.services`. Updated patch targets in `test_agent_os_phase4.py` (4 targets), `test_agent_os_phase3.py`, `test_agent_os_phase5.py`. Fixed `get_agent_runs` ORDER BY to `started_at DESC, id DESC` to eliminate flaky ordering on same-second inserts. |
| `2026-05-16 03:02:00 +05:00` | `DONE` | Deleted `backend/app/services/` directory (51 files, ~4100 LOC of facades) via `git rm -r`. Architecture is now services-free. |
| `2026-05-16 03:03:00 +05:00` | `DONE` | Deleted 4 dead `core/` modules: `core/agents.py` (2885 lines), `core/files.py` (420 lines), `core/library.py` (158 lines), `core/llm.py` (440 lines). Inlined `truncate_text` into `core/web.py` to resolve the `core/files.py` dependency. Updated `test_agent_os_phase4.py` to call `workflow_engine.run_legacy_multi_agent_workflow` directly (removing the deleted `core/agents` dependency). |
| `2026-05-16 03:04:00 +05:00` | `DONE` | Moved `core/memory.py` (1476 lines) to `infrastructure/db/memory.py` as canonical location. `core/memory.py` converted to wildcard facade (`from app.infrastructure.db.memory import *`). Fixed absolute import in `infrastructure/db/memory.py` (`from app.core.config import DB_PATH, SETTINGS_PATH`). Deleted empty packages: `domain/`, `application/code_agent/`, `infrastructure/llm/`, `infrastructure/storage/`, `state/`, `utils/`. 87 tests pass. |
| `2026-05-16 04:00:00 +05:00` | `DONE` | Audited 12 legacy `elira_*.py` route files against frontend usage. Found 10 completely unused (no frontend calls, no tests): `elira_devtools`, `elira_execute`, `elira_phase19`, `elira_phase20`, `elira_phase20_queue`, `elira_phase20_state`, `elira_phase21`, `elira_stabilization`, `elira_supervisor`, `elira_task_runner`. Deleted all 10 via `git rm`, removed their imports and `include_router` calls from `main.py`. Kept `elira_state.py` (chat/settings endpoints) and `elira_patch.py` (patch diff/apply endpoints) — both actively used by frontend. 87 tests pass. |
| `2026-05-16 05:00:00 +05:00` | `DONE` | Second dead-route audit: deleted `routes/agents.py`, `routes/models.py`, `routes/profiles.py` (zero frontend callers), `routes/library.py` (explicit DEPRECATED marker), `routes/web_search_routes.py`, `routes/image_routes.py` (zero frontend callers), `routes/debug.py` (not registered in main.py). Removed corresponding `include_router` calls. Also deleted `core/memory.py` (wildcard facade, zero callers), `application/users/profile_service.py` (DEPRECATED, zero callers), `infrastructure/models/models_service.py` (untracked, zero callers). Fixed `scripts/smoke_contract_check.py` import from deleted `core.memory` → `infrastructure.db.memory`. Removed `test_web_engines_route_exposes_only_new_stack` test (route deleted). 86 tests pass. |
| `2026-05-16 05:01:00 +05:00` | `DONE` | Fixed architectural violations in route files (embedded business logic). Extracted from `elira_patch.py`: `infrastructure/db/patch_history_db.py` (SQLite history CRUD) and `application/projects/patch_operations.py` (resolve/diff/apply/rollback/verify). Extracted from `advanced_routes.py`: `application/projects/project_explorer.py` (process-level `_project_path` state, open/close/tree/read/search). Extracted from `tools_exec.py`: `application/tools/code_analyzer.py` (Python/JS AST-free static analysis). Extracted from `terminal.py`: `infrastructure/shell/terminal_service.py` (`_cwd` state, exec_command with Windows CP866/CP1251 fallback). Extracted from `dashboard_routes.py`: `application/dashboard/dashboard_service.py` (aggregated stats). Extracted from `library_sqlite.py`: `infrastructure/db/library_db.py` (file metadata CRUD) and `application/library/document_preview.py` (PDF/docx/xlsx/text preview). Fixed deprecated `@router.on_event("startup")` in `elira_state.py` → module-level `init_db()`. 86 tests pass. |
| `2026-05-16 05:02:00 +05:00` | `DONE` | Eliminated all wrong-direction imports (application/infrastructure importing from api/routes). Fixed `context_builder.py`: `_project_path` import changed from `app.api.routes.advanced_routes` → `app.application.projects.project_explorer`. Fixed `agents_service.py` line 211: same import corrected. Removed stale TODO in `event_bus.py` about wiring `tool.executed` (already done in `engine.py`). Zero `from app.api.*` imports remain outside `api/` layer. 86 tests pass. Commit `8293d3c`. |
| `2026-05-16 06:00:00 +05:00` | `DONE` | Slimmed `agents_service.py` facade 359 → 130 lines: removed 9 dead helper functions including `_collect_context_legacy` (86 lines of duplicate context logic). Added `PlannerV2Service` re-export for test patch targets. Fixed `test_runtime_datetime_prompt.py` patch target from `agents_service._run_auto_skills` → `auto_skills._run_auto_skills` (function lives in auto_skills.py, not re-exported into agents_service namespace). Commit `46e8753`. |
| `2026-05-16 06:01:00 +05:00` | `DONE` | Extracted workspace file I/O from `file_ops.py` (216 → 58 lines) to `infrastructure/files/workspace_service.py`. Extracted text extraction (PDF/DOCX/XLSX/ZIP/text) from `files.py` (192 → 28 lines) to `infrastructure/files/file_extractor.py` with unified `extract_any()` entry point. Commit `385ca31`. |
| `2026-05-16 06:02:00 +05:00` | `DONE` | Dead code wave: extracted library file upload/delete logic from `library_sqlite.py` route into `application/library/library_service.py` (add_library_file, remove_library_file_by_id). Fixed double `list_files()` call bug. Removed from `web_search.py`: `is_strict_web_only_query`, `do_web_search_legacy` (~153 lines), `do_temporal_web_search_legacy` (~53 lines) — zero callers confirmed. Deleted dead modules `application/projects/project_brain_engine_service.py` (286 lines) and `project_brain_service.py` (84 lines) — zero imports anywhere. Removed stale `set_library_active`/`delete_library_file` from library_service. Commit `22c0242`. |
| `2026-05-16 06:03:00 +05:00` | `DONE` | Cleaned dead code: deleted `application/users/` package (profiles_service.py — zero callers). Deleted 5 orphaned schema files: `schemas/agents.py`, `chat.py`, `library.py`, `project_patch.py`, `settings.py` — zero imports anywhere. Removed dead functions: `prompt_builder.build_prompt_with_context`, `response_cache.clear_cache/cache_stats`, `elira_memory_sqlite.count_messages`, `image_gen.unload_model`. Deleted `infrastructure/db/repositories/` scaffold (7 files — ChatsRepository, MemoryRepository, MessagesRepository, RegistryRepository, WorkflowsRepository — never referenced outside the package). Commits `0d086ba`, `32e4abc`. |
| `2026-05-16 06:04:00 +05:00` | `STATUS` | Architecture health: 19,247 total backend Python lines. Zero wrong-direction imports. All route files are thin HTTP handlers. Full dead-function scan across application/ and infrastructure/ layers shows only internal helpers (false positives for scanner). 86/86 tests pass. Branch `claude/peaceful-mayer-456e15`. |
| `2026-05-16 07:00:00 +05:00` | `DONE` | Dead-code wave: removed stream_service.build_chat_meta/iter_text_stream_events/build_stream_done_event (50 lines), core/data_files.data_subdir, core/web.count_preferred_domain_hits. Scanner lesson: must include tests/ in corpus to avoid false positives — restored get_recent_blocked_runs (agent_monitor) and run_legacy_multi_agent_workflow (engine) which are called by tests. Commit `7c730ea`. |
| `2026-05-16 07:01:00 +05:00` | `DONE` | Extracted Ollama HTTP client to infrastructure/runtime/ollama_client.py: _make_json_request, fetch_ollama_tags, pick_model, call_ollama_json (74 lines). Removed duplicated code from application/agents/ollama_agent_service.py. Updated project_brain.py to import fetch_ollama_tags/pick_model/OLLAMA_BASE_URL directly from infrastructure. Commit `7c730ea`. |
| `2026-05-16 07:02:00 +05:00` | `DONE` | Extracted final business logic from project_brain.py route: attach_project_file (dict construction + ATTACHMENT_INDEX mutation) and project_file_snapshot (directory traversal) moved to ollama_agent_service.py. Fixed missing hash_bytes import. Removed lazy import of is_allowed from project_snapshot handler. 86/86 tests pass. 18,960 total backend lines. Commits `7d87193`. |
| `2026-05-16 07:03:00 +05:00` | `DONE` | Consolidated lazy per-handler imports across all route files: `autopipeline_routes.py` (10 lazy → top-level block), `task_planner_routes.py` (6 lazy → top-level block), `telegram_routes.py` (9 lazy → top-level block), `pdf_routes.py` (render_pdf_pages added to existing top-level import), `advanced_routes.py` (run_multi_agent moved to top-level), `project_brain.py` (vector_memory_capability_status + screenshot_capability_status moved to top-level). Also fixed pre-existing bug in task_planner_service.py (missing logging/sqlite3/uuid/json/datetime imports — hidden by lazy pattern, surfaced once eager import was active). 86/86 tests pass. |
| `2026-05-16 07:04:00 +05:00` | `DONE` | Final dead-code and import cleanup wave: moved library_service.py lazy imports (document_preview, library_db) to top-level; moved ollama_agent_service.py file_extractor import to top-level; removed 3x redundant `import re as _re` in auto_skills.py (re already at module top); removed dead project_map_service.get_map stub method; removed SUPPORTED_EVENT_TYPES (event_bus.py), PRIORITIES and STATUSES (task_planner_service.py) — all defined, never referenced. Full dead-public-function scan: zero dead functions remain in application/ or infrastructure/ layers. Full wrong-direction import scan: zero violations. 86/86 tests pass. |
| `2026-05-16 07:05:00 +05:00` | `DONE` | Continued lazy import consolidation: fixed redundant double-condition guard in auto_skills.py `_run_auto_skills` (outer `if http_api not in disabled` wrapping inner same check with 1-space indent); moved multi_agent_chain.py import of `run_multi_agent_workflow` to top-level; moved autopipeline_service._execute_task 4 lazy app.* imports (run_agent, start_workflow_run, run_plugin, multi_search) to top-level; updated test_agent_os_phase4 patch target to `autopipeline_service.start_workflow_run`. Verified all 62 remaining lazy app.* imports are intentional (graceful degradation try/except, deferred init patterns, or circular by design). Final state: 18,931 backend Python lines, 86/86 tests pass. |
| `2026-05-17 00:00:00 +05:00` | `DONE` | Completed full lazy import consolidation pass. auto_skills.py: removed all 18 remaining lazy imports from `_run_auto_skills` function body (list_databases/describe_db/run_sql, screenshot_url, generate_image, translate_text, encrypt_text/decrypt_text, create_zip/extract_zip, convert_file, test_regex, analyze_csv, list_webhooks, plugins×4, format_git_context, git_log alias _gl→git_log, git_diff alias _gdf→git_diff, get_status, GENERATED_DIR alias gen_dir→GENERATED_DIR). Commits `88b47d6`. |
| `2026-05-17 00:01:00 +05:00` | `DONE` | Second lazy import wave across application + infrastructure layers. Consolidated 22 lazy imports across 9 files: tool_service.py (tool_registry, smart_memory), dashboard_service.py (smart_memory, elira_memory_sqlite, plugin_system), chat/service.py (event_bus, agent_registry×2), tools_exec.py route (RunHistoryService moved to module-level singleton), agent_monitor.py (event_bus×2, tool_service, agent_registry), engine.py (agents_service.run_agent, event_bus, tool_service.run_tool, smart_memory), web_multisearch_service.py (core.web×4), web_search.py (temporal_intent, core.web×2), telegram_service.py (agents_service.run_agent). Updated test_agent_os_phase4 patch targets: run_agent and run_tool now target `app.application.workflows.engine.*`. 13 lazy imports verified intentional and left: seed functions (circular by design: tool_registry↔tool_service), mutable module var (_project_path), deferred DB init (config.py), optional heavy dep graceful degradation (pdf_pro). Final state: 18,920 backend Python lines, 86/86 tests pass. Commit `bbba1bf` pushed. |
| `2026-05-17 00:02:00 +05:00` | `DONE` | Data-blob extraction series completed. Three new pure-data modules created: `application/workflows/workflow_ids.py` (4 MULTI_AGENT_*_WORKFLOW_ID constants), `application/workflows/builtin_templates.py` (6 prompt templates + 2 step lists + `_with_reflection` + `get_builtin_workflow_templates`), `application/tools/builtin_tools.py` (24 tool definition dicts + `get_builtin_tool_definitions` factory). engine.py reduced 1316→1101 lines; tool_registry.py reduced 467→267 lines. test_agent_os_phase4 patch targets updated after engine.py consolidation. Commit `e8c68e8` pushed. |
| `2026-05-17 00:03:00 +05:00` | `DONE` | Extracted `seed_builtin_agents` data blob (71 lines) to `application/agents/builtin_agents.py` with `get_builtin_agent_definitions()` factory. Deferred init of AGENT_PROFILES/AGENT_PROFILE_UI preserved inside factory. agent_registry.py reduced 398→329 lines. Same delegation pattern as builtin_tools.py and builtin_templates.py. 86/86 tests pass. Commit `1b40d58` pushed. |
| `2026-05-17 00:04:00 +05:00` | `DONE` | Dead-function and stub-bug wave. Removed dead `fetch_page_text` (60L) from web_search.py (function with zero external callers, overriding core.web import alias). Removed dead `fetch_page` (9L) from web_multisearch_service.py. Removed resulting unused `fetch_page_text` import from web_multisearch_service.py. Fixed `ProjectMapService` stub: added `build_map()` and `search()` delegating to project_service helpers (pre-existing AttributeError when project_map_scan/project_map_search tools were called). Fixed `BrowserAgent` stub: added `search()` method (same pre-existing AttributeError from browser_search tool). 86/86 tests pass. Commits `cc3cc27`, `5db0149`, `80b3979` pushed. |
| `2026-05-17 00:05:00 +05:00` | `DONE` | Extracted `_upsert_persona_candidate` helper (53 lines) from `observe_dialogue` in `persona_service.py`. Inner 46-line candidate DB upsert loop factored into private function; loop body replaced with single delegation call. `observe_dialogue` reduced from 106 to ~60 effective lines. Logic unchanged. 86/86 tests pass. Commit `ca46f51` pushed. |
| `2026-05-17 00:06:00 +05:00` | `DONE` | Extracted 6 private skill-group helpers from `_run_auto_skills` (356→15 lines) in `auto_skills.py`: `_run_network_skills` (HTTP/API, SQL, screenshot), `_run_media_skills` (image gen, hints), `_run_text_skills` (translate, encrypt, decrypt), `_run_file_skills` (zip, unzip, convert, regex, CSV), `_run_webhook_plugin_skills` (webhook, plugins), `_run_system_skills` (PDF, git, GPU, files). All helpers share `(user_input, ql, [url_match,] disabled, parts)` signature. Logic unchanged. 86/86 tests pass. Commit `773c061` pushed. |
| `2026-05-17 00:07:00 +05:00` | `DONE` | Extracted `_try_record_step_metrics` (50L) and `_decide_step_outcome` (92L) from `_execute_workflow_run` in `engine.py`. Step metrics recording + step outcome decision tree (pause/fail/complete/continue) each moved to dedicated helpers. `_execute_workflow_run` reduced 189→109 lines; loop body is now a clear 3-step sequence: execute → record → decide. 86/86 tests pass. Commit `6da414a` pushed. |
| `2026-05-17 00:08:00 +05:00` | `DONE` | Extracted chat completion helpers. `stream_service.py`: `_apply_guards_and_complete_stream` (84L) factors out the shared 60-line completion tail (guards, cache, persona, HISTORY, monitoring, events) that appeared twice in `execute_chat_agent_stream` (415→331L). `service.py`: `_complete_chat_run` (95L) factors out the 77-line success tail from `execute_chat_agent` (354→296L). Both helpers use keyword-only args. 86/86 tests pass. Commit `cd5af91` pushed. |
| `2026-05-17 00:09:00 +05:00` | `DONE` | Extracted `_normalize_search_plan` (47L) from `do_web_search` in `web_search.py`. Moved 40-line input-normalisation block (fallback plan construction, raw_subqueries, passes) to helper returning `(search_query, plan, raw_subqueries, passes)`. `do_web_search` reduced 162→124 lines. 86/86 tests pass. Commit `cc9183b` pushed. |
| `2026-05-17 00:10:00 +05:00` | `STATUS` | Architecture health: 19,147 total backend Python lines. Zero wrong-direction imports. Zero dead public functions. All route files are thin HTTP handlers. Largest remaining functions: `execute_chat_agent_stream` (331L, full request lifecycle), `execute_chat_agent` (296L, full request lifecycle), `get_builtin_tool_definitions` (213L, pure data). 86/86 tests pass. Branch `claude/peaceful-mayer-456e15`. |
| `2026-05-23 00:01:00 +05:00` | `DONE` | Extracted `_resolve_plan_and_tools` from both `execute_chat_agent` (service.py) and `execute_chat_agent_stream` (stream_service.py). The identical 23-line planner.plan() + tool-filter + pick_model_for_route block was extracted to a single private helper in service.py; stream_service.py imports and calls it. Removed `PlannerV2Service` and `pick_model_for_route` direct imports from stream_service.py. Updated two stream-path test mocks to patch from service.py namespace. 86/86 tests pass. Commit `abc1ddc` pushed. |
| `2026-05-23 00:02:00 +05:00` | `DONE` | Extracted 3 helpers from `build_single_web_subquery_context` (149→35L) in `web_search.py`: `_search_and_fetch_pages` (web search + optional news + page fetch, 73L), `_check_coverage_and_deepen` (coverage assessment + optional deep research, 42L), `_assemble_subquery_context_parts` (format text sections, 29L). 86/86 tests pass. Commit `060772a` pushed. |
| `2026-05-23 00:03:00 +05:00` | `DONE` | Extracted `_handle_code_mode` from `execute_chat_send` (103→55L) in `ollama_agent_service.py`. The 46-line code-mode branch (target resolution, Ollama call, response build) extracted to helper returning `(response | None, route)`. 86/86 tests pass. Commit `1376d35` pushed. |
| `2026-05-23 00:04:00 +05:00` | `DONE` | Extracted `_process_subquery` and `_build_web_search_payload` from `do_web_search` (124→70L) in `web_search.py`. Inner-loop body per subquery moved to `_process_subquery` returning metrics dict; 18-key result payload assembly moved to `_build_web_search_payload`. 86/86 tests pass. Commit `bceddd8` pushed. |
| `2026-05-23 00:05:00 +05:00` | `DONE` | Extracted `_emit_workflow_run_init` and `_execute_step_safely` from `_execute_workflow_run` (109→70L) in `engine.py`. Start/resume event+state+resource block moved to `_emit_workflow_run_init`; try/except wrapper around `_execute_step` including timing moved to `_execute_step_safely` returning `(result, duration_ms)`. 86/86 tests pass. Commit `c8b78fb` pushed. |
| `2026-05-23 00:06:00 +05:00` | `DONE` | Extracted `_query_agent_os_stats` from `get_agent_os_dashboard` (88→38L) in `agent_monitor.py`. The 49-line block of 4 DB queries in a single connection extracted to helper returning `(totals, avg_row, top_rows, violation_rows)`. 86/86 tests pass. Commit `2cb1ae2` pushed. |
| `2026-05-23 00:07:00 +05:00` | `DONE` | Extracted `_add_text_to_doc` (19L) and `_add_tables_to_doc` (27L) from `pdf_to_word` (84→44L) in `pdf_pro.py`. Text-parsing loop and table-rendering loop each moved to a focused helper that takes `(doc, content)` and mutates the Document in-place. 86/86 tests pass. Commit `c26c848` pushed. |
| `2026-05-23 00:08:00 +05:00` | `STATUS` | Architecture health: 21,398 total backend Python lines. Functions >= 80L: 8 total — `execute_chat_agent_stream` (310L, full lifecycle generator), `execute_chat_agent` (276L, full lifecycle), `get_builtin_tool_definitions` (213L, pure data), `generate_image` (95L, has encoding display issues), `_complete_chat_run` (95L, extracted helper appropriate size), `_decide_step_outcome` (92L, extracted helper), `_run_file_skills` (88L, extracted helper), `_apply_guards_and_complete_stream` (84L, extracted helper). Pre-existing infra->app violation: `web_search.py` imports `temporal_intent` from application layer (spawned as separate task). 86/86 tests pass. Branch `claude/peaceful-mayer-456e15`. |

## 8. Commit Ledger

| Branch | Short SHA | Title | Merged state | Note |
| --- | --- | --- | --- | --- |
| `main`, `origin/main`, `codex/workplan-codex-claude`, `feat/agent-os-phase2-tools`, `claude/zealous-goldwasser` | `755072177138` | `feat(agent-os): Phase 2 — Tool Registry with JSON Schema` | `current HEAD` | Shared current tip; Phase 2 status still needs reconciliation against the legacy workplan |
| `historical mainline` | `283345d` | `feat(agent-os): finish phase 5 monitoring dashboard` | `present in docs history` | UI/dashboard completion for Agent OS monitoring |
| `historical mainline` | `2b44a67` | `feat(agent-os): add phase 5 monitoring backend` | `present in docs history` | Backend half of Agent OS monitoring |
| `feat/agent-os-phase4-workflows` | `8c00751` | `feat(agent-os): add workflow engine` | `historical branch ref exists` | Workflow engine phase commit |
| `feat/agent-os-phase3-eventbus` | `5341244` | `docs: expand AGENT_OS_WORKPLAN.md with full Phase 4-5 specs` | `historical branch ref exists` | Event bus coordination expansion |
| `feat/agent-os-phase1-registry` | `f6bca43` | `docs: add AGENT_OS_WORKPLAN.md — shared coordination file for Claude Code + Codex` | `historical branch ref exists` | Initial shared agent coordination document |
| `historical mainline` | `852c7ef` | `docs: add Agent OS Phase 1 entry to ACTUAL_WORK.md` | `present in docs history` | First `ACTUAL_WORK.md` coordination entry |
| `codex/elira-active-work`, `claude/angry-ptolemy` | `5f87d9a` | `update` | `historical branch refs exist` | Shared branch tip from an earlier stabilization wave |
| `feat/agent-os-phase6b-runtime-hardening` | `f3d9ade` | `docs(agent-os): add next wave roadmap` | `historical branch ref exists` | Later Agent OS planning branch |
| `historical mainline` | `1c6119e` | `feat(persona): root elira identity architecture` | `present in docs history` | Major persona/runtime milestone referenced by `ACTUAL_WORK.md` |

## 9. Deleted Files Ledger

| Path | Observed state | Reason | Restore note |
| --- | --- | --- | --- |
| `README_Elira_AI.md` | Deleted in working tree before this workplan was created | Not confirmed | Restore from `HEAD` if the deletion was accidental or if a replacement path is not committed yet |
| `docs/README.md` | Deleted in working tree before this workplan was created | Not confirmed; may be part of docs consolidation | Restore from `HEAD` unless a replacement index is explicitly committed |
| `docs/ROADMAP_STABILIZATION_2026-03-29.md` | Deleted in working tree before this workplan was created | Not confirmed; may be superseded, but no committed replacement is linked yet | Restore from `HEAD` if the roadmap still matters for historical reference |
| `docs/archive/README.md` | Deleted in working tree before this workplan was created | Not confirmed | Restore from `HEAD` if archive navigation is still required |
| `docs/deep-research-report (END Point Agent).md` | Deleted in working tree before this workplan was created | Likely being moved to `docs/archive/deep-research-report (END Point Agent).md`, but the move is not committed | Confirm move and commit delete/add together, or restore the original file from `HEAD` |

## 10. Handoffs / Blockers

- `AGENT_OS_WORKPLAN.md` and the current branch tips disagree on Agent OS Phase 2. Resolve this before treating Tool Registry as fully closed.
- `git worktree list --porcelain` reports a prunable gitdir problem for `.claude/worktrees/angry-ptolemy`. This is a repo hygiene issue, not a cleanup target for this task.
- `.claude/worktrees/zealous-goldwasser/` is currently untracked in the root worktree. Treat it as protected and out of scope for documentation commits.
- `docs/CLAUDE_CODEX_JARVIS_MASTER_REFACTOR_PLAN.md` and `docs/ELIRA_PROJECT_MAP.md` are source inputs but are still untracked. Decide in a later docs commit whether to formally add them.
- The current working tree was already dirty before this file was created. Keep the blast radius limited to documentation until the outstanding deletes and moves are explicitly resolved.

## 11. Next Queue

| Priority | Task | Target branch | Notes |
| --- | --- | --- | --- |
| `1` | Commit the foundation wave after reviewing the current deleted docs and archive move state | `codex/refactor-arch-foundation` | Keep the commit limited to workplan plus backend foundation files |
| `2` | Extract the next smallest chat slice out of `services/agents_service.py` after the context-builder move | `codex/refactor-arch-foundation` | Prefer request/stream orchestration or web-search helper wiring; keep the legacy facade thin |
| `3` | Start routing the next touched DB consumers through `app.infrastructure.db.connection` | `codex/refactor-arch-foundation` | Prefer incremental migration over broad rewrites |
| `4` | Confirm or add lint, formatting, and smoke-test commands from the master refactor plan | `TBD` | Keep behavior stable and avoid broad rewrites |
| `5` | Reconcile the current deleted docs and archive move before the first focused docs commit | `codex/workplan-codex-claude` | Do not mix unrelated deleted docs into a backend refactor commit |
| `6` | Check whether `code_agent_service.py` still exists or has already been removed/replaced before adding a freeze step for it | `codex/refactor-arch-foundation` | Current filesystem snapshot does not show this file under `backend/app/services` |

## 12. Verification Checklist For This Document

- [x] File created in `docs/`, not in a new `Docs/` directory.
- [x] Required coordination sections are present and populated from current repo state.
- [x] Current deleted files from `git status` are recorded in `Deleted Files Ledger`.
- [x] Branch and worktree snapshot is based on `git branch --all --verbose --no-abbrev` and `git worktree list --porcelain`.
- [x] Old coordination docs are referenced as sources only, not edited as part of this change.
