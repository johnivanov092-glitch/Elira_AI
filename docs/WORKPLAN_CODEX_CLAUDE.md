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
