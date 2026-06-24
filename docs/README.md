# Elira AI Documentation

This folder is the navigation layer for the project. Keep it current and small.
Historical plans belong in `archive/` once the work they describe has shipped or
been superseded; they stay only for the decisions/history they record.

## Current Docs

- `ARCHITECTURE.md` - current backend/frontend/runtime architecture.
- `PROJECT_MAP.md` - repo structure, owners, and where to change things.
- `SERVER.md` - AI inference server summary (host, endpoints, models, access).
  The agent runs as a client; the model runs on the separate `Elira_AI_Server`.
- `AGENT_BOUNDARY_REVIEW.md` - the Chat-Agent vs Code-Agent runtime boundary.
- `UI_BASELINE.md` - user-approved unified-workspace visual baseline and change
  constraints (locked baseline - do not regress without sign-off).
- `POST_SERVER_BACKLOG.md` - current server migration status and remaining
  follow-up work.
- `CLAUDE_TASK_TEMPLATE.md` - task template for external agent work.
- `agents/` - skill definitions (domain, issue-tracker, triage-labels).
- `DEFERRED_TRACK.md` - forward-looking deferred track (D1-D3, not yet
  implemented). The runtime guardrails now live in `ARCHITECTURE.md`.

## Archive

`archive/` holds plans/audits whose work has shipped or been superseded. They are
kept for historical context only; for current behaviour cite the code or the
Current Docs above, not these.

- `archive/FRONTEND_REBUILD_PLAN.md` - frontend rebuild plan (shipped).
- `archive/AGENT_UX_PLAN.md` - Code Agent transcript UX plan (shipped; UI
  approved).
- `archive/CODE_AGENT_REWRITE_PLAN.md` - code-agent runtime rewrite plan
  (token streaming / reliability shipped).
- `archive/UNIFIED_WORKSPACE_REFACTOR.md` - in-place refactor approach,
  superseded by the frontend rebuild.
- `archive/WORKPLAN_CODEX_CLAUDE.md` - past agent-core/context coordination
  handoff (work stabilized).
- `archive/UI_WIRING_PLAN.md` - tools/skills UI wiring plan (wiring complete).
- `archive/AGENT_CORE_AUDIT.md` - point-in-time stabilization audit (2026-06-20).
- `archive/CONTEXT_SYSTEM_AUDIT.md` - point-in-time context/memory audit
  (2026-06-20).
- `archive/UI_AFTER_CONTEXT_2026-06-20.png` - dated UI screenshot artifact.
- `archive/ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md` - pre-split roadmap snapshot
  (deferred track moved to `DEFERRED_TRACK.md`, guardrails to `ARCHITECTURE.md`).

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
- When a plan ships or is superseded, move it to `archive/` and update this index.
