# Elira AI Documentation

This folder is the navigation layer for the project. Keep it current and small.
Historical plans belong here only when they still explain shipped behavior or a
known decision.

## Current Docs

- `ARCHITECTURE.md` - current backend/frontend/runtime architecture.
- `PROJECT_MAP.md` - repo structure, owners, and where to change things.
- `SERVER.md` - AI inference server summary (host, endpoints, models, access).
- `AGENT_BOUNDARY_REVIEW.md` - the Chat-Agent vs Code-Agent runtime boundary.
- `WORKPLAN_CODEX_CLAUDE.md` - single live coordination and handoff document for
  agent-core/context stabilization.
- `UI_BASELINE.md` - user-approved unified-workspace visual baseline and change
  constraints.
- `AGENT_CORE_AUDIT.md` - code-backed status of the supplied stabilization
  requirements.
- `CONTEXT_SYSTEM_AUDIT.md` - current context/compression/memory map and gaps.
- `POST_SERVER_BACKLOG.md` - current server migration status and remaining
  follow-up work.
- `AGENT_UX_PLAN.md` - active plan: Code Agent transcript UX (tool icons, token
  counter, live status). Branch `claude/agent-ux-toolcalls`.
- `CODE_AGENT_REWRITE_PLAN.md` - active plan: Claude Code/Codex-style runtime
  (token streaming, reliability, prompt trim). Fixes agent *behaviour*.
- `FRONTEND_REBUILD_PLAN.md` - active plan: rebuild the frontend (same stack:
  React+Vite+TS+Tailwind/shadcn, Tauri 2) on the v3 mockup, reusing the backend.
  The main rebuild is shipped; token streaming and final polish remain.
- `UNIFIED_WORKSPACE_REFACTOR.md` - superseded for the frontend by
  FRONTEND_REBUILD_PLAN (in-place refactor approach kept for reference).
- `CLAUDE_TASK_TEMPLATE.md` - task template for external agent work.
- `../ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md` - deferred track (D1-D3) and the
  runtime guardrails (P0-P12 are shipped; see code + ARCHITECTURE for current
  behaviour).

The completed P9-P12 plan and per-step preflight/proposal notes were removed in
the 2026-06-14 docs cleanup; their history remains in git.

## External Project Docs

The dedicated inference server lives in the sibling repo `Elira_AI_Server`
(same parent folder). `SERVER.md` summarizes it for in-repo work; the full
operational docs are:

- `../Elira_AI_Server/README.md`
- `../Elira_AI_Server/Server/ACCESS.md`
- `../Elira_AI_Server/docs/README.md`

## Maintenance Rules

- Use ASCII unless a file explicitly requires another encoding.
- Do not document removed runtimes as active options.
- Do not store secrets, private keys, API keys, or local `.env` values here.
- For runtime behavior, cite the current code path rather than a stale plan.
