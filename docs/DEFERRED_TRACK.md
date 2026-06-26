# Deferred Track

Forward-looking capabilities that are intentionally **not yet implemented**.
They are useful but not on the critical path. Start each only on real need,
after its own spec and review. Keep stdio MCP as the default transport.

The runtime guardrails and quality bar that bound any new work live in
[`ARCHITECTURE.md`](ARCHITECTURE.md) (→ Runtime Guardrails).

## D1 — Remote MCP (streamable HTTP) — ✅ done

Only when remote MCP servers are actually needed. Requires: SSRF guard; block
private/metadata endpoints; HTTPS by default; separate secret storage;
timeouts + bounded retry; health status; disabled by default.

Implemented as a sibling `McpHttpClient` (`mcp_http_client.py`) with the same
public API as the stdio `McpClient`; sanitizing helpers shared via
`mcp_sanitize.py`. The HTTP transport is gated behind `ELIRA_REMOTE_MCP`
(off by default) — a configured `http` server refuses to start until the
operator opts in, and stdio remains the default transport, unaffected.

## D2 — LSP context provider

Separate stage after the core runtime is stable. Requires: disabled by default;
read-only tools only (diagnostics, definition, references); result limits +
provenance; explicit shutdown; child-process cleanup (correct process-tree
termination on Windows); a mock LSP server in tests.

## D3 — Full structured action envelopes — ✅ done

Only if telemetry shows local-model action-structuring errors are the main
limiter. Possible scope: Pydantic schemas for plan/action/tool-request/
tool-result/final-result/blocker; at most one repair retry; deterministic
fallback; a fast chat path with no mandatory JSON.

Implemented as a leaf module `action_envelopes.py` (next to
`inline_tool_calls.py`): Pydantic v2 schemas for all six envelope kinds
(`extra="forbid"` so a malformed/hallucinated shape fails strict parse), a
strict `parse_envelope`, an `extract_envelope` that recovers an embedded
envelope from model prose, and `validate_tool_request` — the hot-path hook
that checks each recovered tool call (known tool + dict args) before
dispatch. The whole layer is gated behind `ELIRA_ACTION_ENVELOPES` (off by
default — same pattern as D1/D2). When off, `envelopes_enabled()` is False
and the agent-loop hot path is byte-for-byte unchanged: conversational
turns never pay for JSON validation (this is the "fast chat path"). When
on, a malformed tool-request triggers exactly one repair retry
(`REPAIR_INSTRUCTION`), then falls back deterministically to the existing
inline-recovery behaviour rather than looping.
