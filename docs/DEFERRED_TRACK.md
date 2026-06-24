# Deferred Track

Forward-looking capabilities that are intentionally **not yet implemented**.
They are useful but not on the critical path. Start each only on real need,
after its own spec and review. Keep stdio MCP as the default transport.

The runtime guardrails and quality bar that bound any new work live in
[`ARCHITECTURE.md`](ARCHITECTURE.md) (→ Runtime Guardrails).

## D1 — Remote MCP (streamable HTTP)

Only when remote MCP servers are actually needed. Requires: SSRF guard; block
private/metadata endpoints; HTTPS by default; separate secret storage;
timeouts + bounded retry; health status; disabled by default.

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
