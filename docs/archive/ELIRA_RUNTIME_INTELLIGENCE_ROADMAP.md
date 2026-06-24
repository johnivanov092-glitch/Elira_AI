# Elira Runtime Intelligence Roadmap (archived)

> **Archived 2026-06-24.** This file is kept for history only. Its live content
> was split out:
>
> - Deferred track (D1–D3) → [`../DEFERRED_TRACK.md`](../DEFERRED_TRACK.md)
> - Runtime guardrails + quality bar → [`../ARCHITECTURE.md`](../ARCHITECTURE.md)
>   (→ Runtime Guardrails)
>
> P0–P12 shipped long ago; verify shipped behaviour from code and tests, not
> from this snapshot. The text below is the pre-split copy, frozen.

---

> Status: P0–P12 implementation is **complete and merged**. This file is now a
> lean forward-looking doc: the deferred track and the runtime guardrails. The
> current architecture is maintained in [`docs/ARCHITECTURE.md`](../ARCHITECTURE.md)
> and [`docs/PROJECT_MAP.md`](../PROJECT_MAP.md); shipped behaviour should be
> verified from code and tests, not from historical planning. The completed
> P9–P12 plan was removed in the 2026-06-14 docs cleanup (history in git).

## Deferred Track (after P12)

These are useful but not on the critical path. Start each only on real need,
after its own spec and review. Keep stdio MCP as the default transport.

### D1 — Remote MCP (streamable HTTP)
Only when remote MCP servers are actually needed. Requires: SSRF guard; block
private/metadata endpoints; HTTPS by default; separate secret storage;
timeouts + bounded retry; health status; disabled by default.

### D2 — LSP context provider
Separate stage after the core runtime is stable. Requires: disabled by default;
read-only tools only (diagnostics, definition, references); result limits +
provenance; explicit shutdown; child-process cleanup (correct process-tree
termination on Windows); a mock LSP server in tests.

### D3 — Full structured action envelopes
Only if telemetry shows local-model action-structuring errors are the main
limiter. Possible scope: Pydantic schemas for plan/action/tool-request/
tool-result/final-result/blocker; at most one repair retry; deterministic
fallback; a fast chat path with no mandatory JSON.

## Runtime Guardrails (still binding)

- No second executor, kernel DB, or general tool registry.
- No full OS access by default; no plugin code imported into the backend
  process.
- A Python thread timeout is not a real cancellation of a side effect.
- No infinite retries.
- No cloud profile without explicit consent.
- No recursive subagents.
- Durable state is not enough without startup recovery.
- Untrusted content must never change policy, scopes, approvals, or tool
  activation.
- Do not add remote MCP transport / LSP child processes to the main path before
  the relevant stage; do not overload the local model's prompt with dozens of
  schemas.

## Quality Bar (every new capability)

1. The model sees the minimum necessary context.
2. An unknown or unactivated tool is blocked before dispatch.
3. Every side effect passes policy and approval.
4. Untrusted content cannot escalate privileges.
5. Errors are bounded and observable.
6. A durable run can be safely recovered after restart.
7. A weak model gets a deterministic fallback.
