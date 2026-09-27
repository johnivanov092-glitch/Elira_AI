# Elira AI Documentation

This folder is the navigation layer for the project. Keep it current and small.
Historical plans belong in `archive/` once the work they describe has shipped or
been superseded; they stay only for the decisions/history they record.

## Current Docs

- [ARCHITECTURE.md](ARCHITECTURE.md) - current backend/frontend/runtime architecture.
- [AGENT_ARCHITECTURE_GUIDE_RU.md](AGENT_ARCHITECTURE_GUIDE_RU.md) - Russian guide with flows,
  APIs, stores, permissions, Windows/UAC, reasoning and prompt cache.
- [PROJECT_MAP.md](PROJECT_MAP.md) - repo structure, owners, and where to change things.
- [ELIRA_SELF_MODEL.md](ELIRA_SELF_MODEL.md) - compact agent/runtime map and boundaries.
- [SERVER.md](SERVER.md) - inference endpoints, dated model observations and access.
  The agent runs as a client; the model runs on the separate `Elira_AI_Server`.
- [UI_BASELINE.md](UI_BASELINE.md) - user-approved unified-workspace visual baseline and change
  constraints (locked baseline - do not regress without sign-off).
- [BACKLOG.md](BACKLOG.md) - continuation work and links to current acceptance.
- [AGENT_EVALS.md](AGENT_EVALS.md) - real Workflow-path evals, legacy profile-assertion
  limitation, tools, MCP, answers, latency and token usage.
- [ANSWER_CONTRACT.md](ANSWER_CONTRACT.md) - draft/accepted answers, web provenance, continuation,
  speech and reproducible persona/source diagnostics.
- [TASK_SKILLS.md](TASK_SKILLS.md) - model-selected task skills, checked packages and context lifecycle.
- [RELEASE_LIFECYCLE.md](RELEASE_LIFECYCLE.md) - separate launch/recovery supervisor and atomic app releases.
- [VOICE_CLONING.md](VOICE_CLONING.md) - voice runtime and dated voice experiments.
- [CLAUDE_TASK_TEMPLATE.md](CLAUDE_TASK_TEMPLATE.md) - optional external-agent task template.
- [agents/](agents/) - domain, local issue tracker and triage conventions.
- [Built-in task skills](../skills/README.md) - instruction packages selected by Elira.

## Acceptance and Dated Evidence

[AUTONOMY_ADVISOR_ACCEPTANCE_RU.md](research/AUTONOMY_ADVISOR_ACCEPTANCE_RU.md)
tracks the current autonomous-development package: implementation, canonical
verification, real Qwen scenarios and deployment are separate statuses. A passed
build does not imply that the live autonomy scenario passed or the release is active.

The following preserve observations and decisions at their stated dates. They
are not current launch instructions or a live probe of the server:

- [Core proposal](research/AUTONOMOUS_CORE_PROPOSAL_RU.md) - agreed initial boundaries.
- [Delivery, 2026-09-26](research/AUTONOMOUS_DELIVERY_2026-09-26_RU.md) - initial implementation and release evidence.
- [Live skill proof](research/AUTONOMOUS_LIVE_PROOF_RU.md) - recorded Qwen creation and reuse.
- [Recovery acceptance](research/AUTONOMOUS_RECOVERY_ACCEPTANCE_RU.md) - recorded create/repair/reuse and web-source work.
- [Windows Foundation acceptance](research/FOUNDATION_WINDOWS_ACCEPTANCE_RU.md) - service identity, ACL and release recovery evidence; separate from live Qwen/product acceptance.
- [Laya CPU evaluation](research/LAYA_CPU_EVALUATION_RU.md) - rejected experiment and removal evidence.
- [Model inventory, 2026-09-26](research/MODEL_INVENTORY_2026-09-26_RU.md) - installed/running model snapshot.
- [Post-server record](POST_SERVER_BACKLOG.md) - migration and dated quality measurements.
- [Simplification audit](ARCHITECTURE_SIMPLIFICATION_AUDIT_RU.md) - 2026-08-23 architecture snapshot.

Evidence paths under `.scratch/`, `.runtime/` and `.agent/` refer to local run
artifacts and are generally ignored by Git. Historical tracked PRDs under
`.scratch/` are not the active implementation queue. Imported engineering-skill
templates under `.agents/skills/` are tooling instructions, not product behavior.

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
- `archive/DEFERRED_TRACK.md` - final status of the completed/superseded D1-D3
  track.
- `archive/UI_AFTER_CONTEXT_2026-06-20.png` - dated UI screenshot artifact.
- `archive/ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md` - pre-split roadmap snapshot
  (deferred track later closed in `archive/DEFERRED_TRACK.md`, guardrails moved
  to `ARCHITECTURE.md`).

The completed P9-P12 plan and per-step preflight/proposal notes were removed in
the 2026-06-14 docs cleanup; their history remains in git.

## External Project Docs

The dedicated inference server lives in the sibling repo `Elira_AI_Server`
(same parent folder). `SERVER.md` summarizes it for in-repo work; the full
operational docs use these local checkout paths (outside this GitHub repository):

- `../Elira_AI_Server/README.md`
- `../Elira_AI_Server/Server/ACCESS.md`
- `../Elira_AI_Server/docs/README.md`

## Maintenance Rules

- Use UTF-8 without BOM and LF. Keep Russian text readable Cyrillic.
- Do not document removed runtimes as active options.
- Do not store secrets, private keys, API keys, or local `.env` values here.
- For runtime behavior, cite the current code path rather than a stale plan.
- When a plan ships or is superseded, move it to `archive/` and update this index.
