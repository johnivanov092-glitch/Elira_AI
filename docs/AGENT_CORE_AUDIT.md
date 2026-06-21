# Agent Core Audit

Audit date: **2026-06-20**
Scope: supplied Agent Core Stabilization plan against the current checkout.

Implementation update: the confirmed 16K/120s drift, message-boundary failure,
XML-like tool-trace containment and bounded session-state foundation were
addressed in the 2026-06-20 stabilization slice. See
`WORKPLAN_CODEX_CLAUDE.md` for shipped details and remaining work.

## Executive finding

The plan describes several systems as missing, but most already exist. The safe
path is to harden existing boundaries. The highest-priority confirmed defect is
configuration drift: the live main server exposes 128K, while multiple client,
API, profile and monitoring defaults still enforce 16K; local configuration
also retains a 120-second provider timeout.

## Runtime map

| Concern | Current implementation |
| --- | --- |
| Code-agent entry/API | `backend/app/api/routes/code_agent_routes.py` |
| Agent loop | `backend/app/application/code_agent/agent_loop.py` |
| Tool schemas/execution | `code_agent/tool_schemas.py`, `code_agent/tools.py` |
| Provider aggregation | `application/tool_providers/registry.py` |
| Persistent ToolSpec registry | `application/tool_registry/` |
| Unified policy executor | `application/agent_kernel/executor.py` |
| OpenAI-compatible client | `infrastructure/llm/openai_compatible.py` |
| Chat entrypoints | `application/chat/entrypoint_sync.py`, `entrypoint_stream.py` |
| Context compaction | `application/context/compaction.py` |
| Chat/code session state | `application/elira_memory/`, `code_agent/sessions.py` |
| Workspace UI | `frontend/src/workspace/` |

## Requirement status

### Tool registry — implemented, harden only

Confirmed:

- Enabled providers expose only their schemas.
- Unknown/hallucinated tool names are rejected.
- Deferred run-scoped activation limits schemas sent to the model.
- ToolSpec includes permission, enabled state, timeout, output limit, scopes and
  idempotency.

Do not create another registry. Add unavailable-tool regressions around the
existing provider/registry boundary if the historical failure still reproduces.

### Message sanitizer — partial

`_normalize_messages_for_request()` normalizes native tool calls and repairs
tool-call IDs. `_coerce_history()` filters client history and empty content.

Missing at the provider boundary:

- explicit allowed-role enforcement;
- empty non-tool turn removal;
- trailing consecutive assistant normalization;
- a regression for the exact `Cannot have 2 or more assistant messages at the
  end of the list` server error.

### Endpoint routing — largely centralized

Main LLM and embedding configuration already live in
`openai_compatible.py`. Live checks confirmed:

- `http://192.168.88.15:8000/v1/models` → main model, 128K;
- `http://192.168.88.15:8001/v1/models` → embedding model, 4K;
- `http://192.168.88.15:8002/health` → OCR healthy on CPU.

Gap: HTTP errors include status/body but not a structured service/base/path
diagnostic. Do not duplicate endpoint configuration to add that diagnostic.

### Timeout policy — inconsistent

- Provider code default: 300 seconds.
- Code-agent run default: 600 seconds.
- Checked-in/local env value: 120 seconds.
- ToolSpec supports per-tool timeouts; shell execution has a separate bound.

There is no single task-class timeout policy. First remove configuration drift,
then add bounded selection through existing route/profile data.

### Context limits — confirmed defect

Live server: `131072`. Confirmed 16K defaults/caps exist in:

- `backend/.env.local` and `backend/.env.example`;
- `frontend/src/api/codeAgent.ts`, `chat.ts`, `agent.ts`;
- chat/code API request models;
- `core/config.py` safe-context table;
- monitoring default limits and model profiles;
- persisted chat settings defaults.

Existing code discovers server `n_ctx`, but most calculations use
`min(requested, server/profile/monitoring cap)` and never raise a stale 16K
request. This explains why the 128K server is still used as 16K by clients.

### Tool-call leakage — partial guard

`inline_tool_calls.py` recovers JSON and call-expression tool calls and rejects
unknown names. It does not parse or suppress XML-like `<tool_call>` traces. If
such content is not recovered as a known tool call, the loop can treat it as a
final user-visible answer. Add a failing regression before implementing the
smallest parser/containment fix.

### Final answer on terminal paths — implemented

The agent loop calls `_wrap_up_text()` for deadline and max-step exits, emits a
`final_response`, then emits a truthful `done` with `ok=False` and a structured
stop reason. Existing tests cover max steps and deadline wrap-up.

Remaining work: cover all terminal paths and ensure frontend rendering retains
the final text even when `done.ok` is false.

### Long-task behavior — partial

Implemented: 600-second run budget, cancellation, bounded tool output,
compaction events, per-tool limits and max-step cap.

Missing/unfinished: token-by-token code-agent streaming, streamed tool-call
delta assembly, task-class heartbeat semantics and repeated-identical-call
guard. Continue the existing `CODE_AGENT_REWRITE_PLAN.md` path.

## Existing relevant tests

- `test_code_agent_loop.py`
- `test_context_compaction.py`
- `test_code_agent_tool_truncation.py`
- `test_openai_compatible_provider.py`
- `test_p9_2b_timeout_taxonomy.py`
- `test_p10_1_deferred_tools.py`
- `test_p10_1_tool_search.py`
- `test_tool_providers.py`

## First implementation slice

Active context profile and timeout truth, as defined in
`WORKPLAN_CODEX_CLAUDE.md` S1. Do not combine this with UI work or persistence
schema changes.
