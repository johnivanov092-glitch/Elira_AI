# P10.1 Preflight — Deferred Tool Search

Status: **preflight only (no code yet)**. Target base: `main @ 282abb4`.
Spec source: `ELIRA_RUNTIME_INTELLIGENCE_ROADMAP.md` §P10.1. This note plans the
step; it does not change runtime behavior.

## Exact objective
Add a read-only meta-tool `tool_search` so a code-agent run starts with a small
base toolset and discovers/activates the rest **run-scoped**, with the executor
enforcing a run-scoped allowlist **before provider dispatch**:

- Base set at run start (in prompt): `read_file`, `glob`, `grep`, `recall`,
  `tool_search` only.
- All other tools stay in the existing ToolSpec registry but are NOT added to the
  prompt.
- `tool_search(query)` searches by name / description / category / source and
  activates matches **for the current run only**, with a count cap.
- The executor holds/receives the run-scoped allowlist and checks it before
  dispatch; an unknown or unactivated tool is blocked before the provider, even
  if the model guesses the name.
- Schema activation does NOT bypass policy, scopes, or approvals.
- MCP and plugin tools participate on the same rules; untrusted content cannot
  auto-activate a side-effect tool.
- **Rollout boundary:** code-agent only this step. Executor enforcement is a
  shared run-scoped mechanism reusable by chat/workflows later (no second
  contour). The chat path is unchanged this step. (P11 starts only after P10.1
  and reuses this executor enforcement.)

## Files likely touched (per roadmap §P10.1)
- `backend/app/application/agent_kernel/executor.py` — run-scoped allowlist check
  at the single dispatch path.
- `backend/app/application/tool_registry/runtime.py`, `store.py` — read-only
  search over ToolSpec metadata (name/description/category/source).
- `backend/app/application/tool_providers/registry.py` — schema collection /
  base-set filtering.
- `backend/app/application/code_agent/tools.py`, `agent_loop.py` — base toolset
  at run start, the `tool_search` tool, mid-run schema re-injection.
- `backend/app/application/monitoring/` — metrics/events for search + activation.
- `backend/tests/` — new coverage.

## Existing modules to reuse (do not rebuild)
- ToolSpec registry + P9.2 fail-closed classification (`policy_classified`,
  `permission`, `scopes`, `enabled`) — activation must respect these as-is.
- Executor single dispatch path + P9.2 per-call enforcement
  (`preflight_or_raise` / `allowed_tools`) — extend with the run-scoped
  allowlist; do not add a parallel enforcement point.
- `tool_providers` `ToolRegistry` / `collect_schemas()` — reuse for schema
  sourcing and the base-set filter.
- Approvals (`agent_monitor.db`, `/api/agent-os` approvals) — deferred tools go
  through the same approval gate; unchanged.
- `monitoring.record_metric` (`agent_metrics.details_json`) — reuse for
  `tool.search` / `tool.activated` metrics (no schema change).
- code-agent `run_id` + cancel registry — reuse run identity for allowlist
  scoping.

## What must NOT be duplicated
- No second tool contour: one ToolSpec registry, one executor dispatch path.
- No new tool DB; search reads the existing tool registry store.
- Do not reimplement policy/scope/approval checks — reuse the P9.2 gates.
- The run-scoped allowlist is enforced at the executor dispatch, not as a
  separate pre-check inside the agent loop.
- Do not modify the chat path this step.

## Acceptance tests (roadmap Критерий Готовности + derived)
- Code-agent starts with only the small base schema set in the prompt.
- `tool_search("web")` finds and activates web tool(s) for the current run.
- An unactivated tool cannot be invoked by a guessed name (blocked).
- An unknown tool never reaches a provider.
- Policy + approvals are identical for direct and deferred tools.
- Activation honors the per-run count cap.
- Untrusted content cannot auto-activate a side-effect tool.
- MCP/plugin tools are discoverable + activatable under the same rules, and stay
  fail-closed if unclassified/disabled.
- Run isolation: one run's activations do not leak into another (keyed by
  `run_id`).

## Main risks
- Name-guessing bypass — must block at dispatch, not only by prompt omission.
- Run-scoped state lifecycle — where the allowlist lives (in-memory keyed by
  `run_id` vs threaded run context), cleanup on run end/cancel, concurrency
  isolation.
- Base-set sufficiency — too small and the agent cannot bootstrap discovery;
  `tool_search` must always be present.
- Cap tuning — too low blocks assembling needed tools; too high reintroduces
  schema bloat.
- Mid-run schema re-injection — surfacing activated schemas on the next turn
  without disrupting context/compaction.
- Interaction with the P9.2 lockdown — forbidden/disabled/unclassified tools must
  remain non-activatable.
- Keeping the mechanism reusable for chat/workflows without regressing the chat
  path.

## Open questions
- Where is the run-scoped allowlist stored — an executor-held map keyed by
  `run_id`, or threaded via the existing run context? Lifetime/cleanup policy?
- Exact base toolset for code-agent (confirm `read_file`/`glob`/`grep`/`recall`/
  `tool_search`; are any write/run tools gated strictly behind search?).
- Activation cap value (per run); does `tool_search` rank results — keyword-only
  or reuse `rag_memory` embeddings?
- How are newly-activated schemas surfaced to the model mid-run (re-collect from
  the run allowlist each turn?).
- The controlled direct-call path for explicitly user-initiated tools — entry
  point, and how policy/scope/approval stay mandatory there.
- Metrics shape — separate `tool.search` / `tool.activated` metric types vs extra
  `details` on existing run metrics.
