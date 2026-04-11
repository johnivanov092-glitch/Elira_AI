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
| `2026-04-10 13:27:00 +05:00` | `NEXT` | Extract the next smallest chat slice from `agents_service.py`: request/stream orchestration or web-search helper wiring, whichever keeps the public contract stable. |
| `2026-04-10 13:46:00 +05:00` | `DONE` | Added `backend/app/application/chat/service.py` for shared chat planning/tool-selection preparation and task-context assembly. |
| `2026-04-10 13:46:00 +05:00` | `DONE` | Switched both `run_agent` and `run_agent_stream` in `agents_service.py` to the new application-layer prep helpers while keeping the legacy API surface intact. |
| `2026-04-10 13:46:00 +05:00` | `NEXT` | Extract the next chat slice from `agents_service.py`: streaming finalization/cached-stream helpers or web-search enrichment, whichever stays below the current risk threshold. |
| `2026-04-10 14:02:00 +05:00` | `DONE` | Added `backend/app/application/chat/stream_service.py` for shared response meta construction, cached token streaming, and stream completion payload assembly. |
| `2026-04-10 14:02:00 +05:00` | `DONE` | Rewired `agents_service.py` to use the new stream helpers in cached-stream and success finalization paths without changing the public chat API. |
| `2026-04-10 14:02:00 +05:00` | `NEXT` | Extract the next lowest-risk slice from `agents_service.py`: cached/finalization monitoring emission or web-search enrichment helpers, whichever keeps compatibility simplest. |
| `2026-04-10 14:26:00 +05:00` | `DONE` | Added `backend/app/infrastructure/search/web_search.py` and moved active web-search execution, subquery context building, and temporal freshness enrichment into the infrastructure layer. |
| `2026-04-10 14:26:00 +05:00` | `DONE` | Switched active web-search entrypoints in `agents_service.py` to thin facades over the new infrastructure search module while leaving legacy inline code as frozen reference. |
| `2026-04-10 14:26:00 +05:00` | `NEXT` | Extract the next lowest-risk slice from `agents_service.py` or `core/agents.py`: monitoring/finalization emission, router selection, or planner/task-graph orchestration depending on conflict risk. |
| `2026-04-10 14:42:00 +05:00` | `DONE` | Integrated the next `core/agents.py` refactor wave into this branch: routing and strategy selection extracted into `backend/app/domain/agents/router.py` with thin facades left in `core/agents.py`. |
| `2026-04-10 14:42:00 +05:00` | `DONE` | Integrated planner/task-graph extraction into `backend/app/domain/agents/planner.py`; `core/agents.py` now delegates active planner and task-graph entrypoints through compatibility facades. |
| `2026-04-10 14:42:00 +05:00` | `NEXT` | Extract the next bounded slice after router/planner: finalize remaining orchestration helpers in `core/agents.py` or move monitoring/finalization emission out of `agents_service.py`. |
| `2026-04-10 21:16:32 +05:00` | `DONE` | Added `backend/app/domain/tools/terminal_tool.py` and moved dangerous-command detection plus bounded terminal execution out of `core/agents.py` into the domain tools layer. |
| `2026-04-10 21:16:32 +05:00` | `DONE` | Left `core/agents.py` on thin compatibility facades for `is_dangerous_command` and `run_terminal`, keeping the public entrypoints stable while freezing the old inline implementation. |
| `2026-04-10 21:16:32 +05:00` | `DONE` | Re-verified compile/import health for `backend/app/domain/tools/terminal_tool.py` and the updated `core/agents.py` facade path. |
| `2026-04-10 21:16:32 +05:00` | `NEXT` | Extract the next lowest-risk helper slice from `core/agents.py` or `agents_service.py`: browser/read-only tooling helpers or monitoring/finalization emission, whichever stays compatibility-safe. |
| `2026-04-10 21:20:25 +05:00` | `DONE` | Added `backend/app/application/memory/web_knowledge.py` and moved browser text normalization, browser RAG record building, and web-knowledge record assembly out of `core/agents.py`. |
| `2026-04-10 21:20:25 +05:00` | `DONE` | Left `core/agents.py` on thin compatibility facades for `_clean_browser_text`, `_chunk_browser_text`, `build_browser_rag_records`, and `build_web_knowledge_records` while freezing the old inline implementations. |
| `2026-04-10 21:20:25 +05:00` | `DONE` | Re-verified compile/import health for `backend/app/application/memory/web_knowledge.py` and the updated `core/agents.py` browser knowledge path. |
| `2026-04-10 21:20:25 +05:00` | `NEXT` | Extract the next lowest-risk slice from `core/agents.py` or `agents_service.py`: browser action execution helpers, browser agent traversal helpers, or monitoring/finalization emission. |
| `2026-04-10 21:23:47 +05:00` | `DONE` | Added `backend/app/domain/tools/browser_action_tool.py` and moved browser runtime hints, Playwright availability checks, action sanitization, browser action planning, and action execution out of `core/agents.py`. |
| `2026-04-10 21:23:47 +05:00` | `DONE` | Left `core/agents.py` on thin compatibility facades for `_browser_runtime_hint`, `_sanitize_browser_actions`, `browser_actions_from_goal`, `run_browser_actions`, and `sync_playwright_available` while freezing the old inline implementations. |
| `2026-04-10 21:23:47 +05:00` | `DONE` | Re-verified compile/import health for `backend/app/domain/tools/browser_action_tool.py` and the updated `core/agents.py` browser action path. |
| `2026-04-10 21:23:47 +05:00` | `NEXT` | Extract the next bounded slice from `core/agents.py` or `agents_service.py`: browser agent traversal helpers or monitoring/finalization emission, whichever remains lowest-risk. |
| `2026-04-10 21:31:17 +05:00` | `DONE` | Added `backend/app/domain/tools/browser_agent_tool.py` and moved browser traversal helpers, page payload assembly, link collection/ranking, and bounded multi-page reading out of `core/agents.py`. |
| `2026-04-10 21:31:17 +05:00` | `DONE` | Left `core/agents.py` on thin compatibility facades for `_goal_keywords`, `_extract_page_payload`, `_collect_links`, `_score_link`, `_rank_links`, and `run_browser_agent` while freezing the old inline implementations. |
| `2026-04-10 21:31:17 +05:00` | `DONE` | Re-verified compile/import health for `backend/app/domain/tools/browser_agent_tool.py`, `core/agents.py`, and `domain/agents/planner.py` on the preserved `run_browser_agent` import path. |
| `2026-04-10 21:31:17 +05:00` | `NEXT` | Extract the next bounded slice from `agents_service.py` or `core/agents.py`: monitoring/finalization emission or the next runtime helper cluster that stays compatibility-safe. |
| `2026-04-11 00:30:00 +05:00` | `DONE` | [Claude Code] Created `domain/agents/reflection.py` (~240 lines) — reflect_and_improve_answer, reflection_v2, count_false_flags, regenerate_answer_from_context, safe_json_object, get_fallback_node_v8, run_graph_with_retry_v8 (Master Plan Task 7 continuation). |
| `2026-04-11 00:30:00 +05:00` | `DONE` | [Claude Code] Created `domain/agents/orchestrator.py` (~470 lines) — run_agent_v8 (graph-based strategy dispatch with memory/KB/tool hints/reflection), run_self_improving_agent (iterative critique+improve loop). |
| `2026-04-11 00:30:00 +05:00` | `DONE` | [Claude Code] Rewired core/agents.py: reflection_v2, _safe_json_object, _count_false_flags, regenerate_answer_from_context, get_fallback_node_v8, run_graph_with_retry_v8, reflect_and_improve_answer, run_agent_v8, run_self_improving_agent as thin facades. core/agents.py reduced from 3029 to 2146 lines (-880 lines). |
| `2026-04-11 00:30:00 +05:00` | `DONE` | [Claude Code] Re-verified compile/import health for all domain/agents modules: reflection.py, orchestrator.py, planner.py, router.py, and core/agents.py. |
| `2026-04-11 00:30:00 +05:00` | `NEXT` | core/agents.py remaining non-facade code: execute_python_with_capture, self_heal_python_code, generate_file_code, run_build_loop (~350 lines python lab), image generation (~250 lines), persist_web_knowledge (~100 lines), run_multi_agent (thin wrapper to workflow_engine). Next target: extract image generation or python lab into domain modules. |

## 8. Commit Ledger

| Branch | Short SHA | Title | Merged state | Note |
| --- | --- | --- | --- | --- |
| `claude/refactor-master-plan` | `23520b1` | `refactor(agents): extract reflection and orchestrator into domain modules` | `pushed to origin` | Task 7 final — reflection.py (~240 lines) + orchestrator.py (~470 lines), core/agents.py 3029→2146 lines |
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
| `2` | Extract the next smallest slice out of `services/agents_service.py` or `core/agents.py` after browser traversal extraction | `codex/refactor-arch-foundation` | Prefer monitoring/finalization emission or another isolated runtime helper cluster while keeping the legacy facades thin |
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
