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

## D3 — Full structured action envelopes

Only if telemetry shows local-model action-structuring errors are the main
limiter. Possible scope: Pydantic schemas for plan/action/tool-request/
tool-result/final-result/blocker; at most one repair retry; deterministic
fallback; a fast chat path with no mandatory JSON.
