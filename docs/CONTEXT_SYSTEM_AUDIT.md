# Context System Audit

Audit date: **2026-06-20**
Scope: supplied Context System / Compression / Memory plan against current
code.

Implementation update: the 2026-06-20 slice added one active context profile,
structured usage, a bounded persistent task ledger and additive persistence
fields. Compression audit population, protected-item editing and consistency
rollback remain follow-up work. See `WORKPLAN_CODEX_CLAUDE.md`.

## Executive finding

Elira already has context trimming, code-agent compaction with rolling summaries,
tool-output truncation, persistent chats/code sessions, RAG/smart memory and
pinning. It does not yet have one structured usage model, a durable task ledger,
protected memory records, compression audit history or consistency rollback.
Those features must extend the existing context/session infrastructure.

## Current message and context flow

### Code-agent

1. `frontend/src/workspace/useAgentRun.ts` builds user/assistant history.
2. `frontend/src/api/codeAgent.ts` sends it with `num_ctx`.
3. `code_agent_routes.py` validates the request.
4. `agent_loop.py` builds system + history + current user messages.
5. `application/context/compaction.py` compacts above 70% of `num_ctx`.
6. `openai_compatible.py` normalizes messages and sends
   `/v1/chat/completions`.
7. Tool output is head/tail truncated before the next LLM call.

### Chat

Chat entrypoints collect project, web, memory, RAG and history context through
`application/chat/`. `core/llm.py` estimates tokens and trims context categories
before provider calls. This is separate behavior from code-agent compaction and
must be converged incrementally, not replaced in one rewrite.

## Existing capabilities

- Rough token estimation in backend and frontend.
- Category-aware trimming for legacy/direct chat contexts.
- 70% code-agent compaction threshold.
- Generated rolling summary plus deterministic fallback.
- Preservation of recent turns and system messages.
- Summary size cap and tool-result flattening.
- Code-session persistence including turns, model, context value and pinned
  session state.
- Persistent chat messages and RAG/smart-memory stores.
- `usage` and `context_compacted` SSE event types.

## Confirmed gaps

### Active context profile

Server discovery returns `n_ctx=131072`, but frontend/API/monitoring/profile
defaults remain 16384. This must be fixed before building usage percentages; an
indicator based on 16K would be misleading.

### Token accounting

Current estimators are character heuristics. There is no shared structured
breakdown for system, chat, summary, tools, RAG, OCR, vision, code, documents,
reserved output and free budget.

### Compaction policy

Current code has one 70% trigger, not staged prepare/auto/strong/critical states.
Summary preservation is prompt-based and size-capped, but required facts are not
schema-validated before replacement.

### Task memory

There is no dedicated task ledger with steps, reasons, evidence, commands,
errors, metrics and next step. Session turns/tool cards are not a substitute for
a bounded durable ledger.

### Protected memory

Chats/sessions can be pinned, but individual commands, decisions, test results
or configuration facts cannot be protected independently from compaction.

### Compression audit and rollback

The runtime emits a compaction event and logs message counts, but does not
persist before/after token metrics, hashes, preserved blocks, trigger reason or
validation/rollback state.

### UI wiring

The API type includes `usage` and `context_compacted`, but
`useAgentRun.ts` currently ignores both. The approved UI has no context meter.
Placement is intentionally deferred until a real backend usage contract exists
and the user approves a minimal visual proposal.

## Safe target architecture

- Keep `backend/app/application/context/` as the context subsystem.
- Add small typed modules there only when a bounded slice needs them: profile,
  budget, packing or validation. Do not create a second runtime root.
- Reuse `openai_compatible.list_models()` for live server context metadata.
- Reuse existing model profiles and monitoring limits for explicit caps.
- Extend existing code-session/chat persistence with additive migrations and
  existing DB connection helpers.
- Keep large artifacts outside live messages; store bounded references and
  summaries.
- Expose one backend usage schema before touching the UI.

## Required sequencing

1. Fix active profile/default drift.
2. Define one structured usage schema and deterministic budget calculation.
3. Feed existing chat and code-agent packers through that calculation.
4. Add schema-validated rolling summary and compression audit.
5. Design/add bounded task ledger and protected items.
6. Add restore/consistency tests.
7. Propose and approve minimal UI placement.
8. Wire the UI without changing the baseline layout.

## Main risks

- Treating the server maximum as permission to always send 128K.
- Duplicating existing memory/context/tool infrastructure.
- Persisting unbounded tool output or secrets in task memory.
- Replacing history before validating that critical facts survived.
- Building a UI percentage from stale or approximate data.
- Mixing schema migrations, context behavior and UI changes in one slice.
