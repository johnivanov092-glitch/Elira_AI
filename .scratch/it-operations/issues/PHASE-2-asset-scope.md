# PHASE-2-asset-scope — Asset Scope and Policy

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-1

## Scope
- Per-run OperationScope enforcement (asset_ids, host aliases, CIDRs, paths, services, DB profiles) as a NEW gate inside execute_tool (between agent-scope and approval).
- **Scope miss ALWAYS returns `blocked` before dispatch — NO generic "falls through to approval".**
- **When a scope IS bound, it covers the EXISTING transport tools too:** `ssh_run`, `ssh_run_ps`, `ssh_write`, `ssh_read`, `ssh_replace`, the host/assert tools, AND a raw-SSH redirect through `run_bash` (via the existing SSH-detection) — each checked against the bound asset/host before dispatch.
- Widening scope is a SEPARATE runtime-owned `scope_request` flow with explicit user approval (patterned on inline ssh_request_host); the model cannot turn an arbitrary out-of-scope tool-call into an approval for a new target.
- New itops tools with NO bound OperationScope fail closed (blocked); an ordinary legacy code-agent run with NO bound OperationScope keeps prior semantics (gate only bites when a scope is bound).
- Model cannot modify the registry or ACL directly. A scope violation also joins the never-auto-approve set (bypass cannot defeat scope).

## Definition of Done (behavior, not labels)
- Out-of-scope target blocked BEFORE dispatch (never silently escalated to approval).
- Widening happens only via the explicit scope-request flow the user approves.
- New itops tool with no bound scope → blocked; unbound legacy run → unchanged.
- Explicitly-added target works; removal/revocation blocks the very NEXT call immediately.

### Executor-path regressions (required)
- SSH ACL allows A **and** B; bound scope allows only asset/host A → `ssh_run(B)` **blocked before dispatch**.
- Raw `run_bash` SSH redirect to B (scoped run) → **blocked before dispatch**.
- `scope_request(B)` + explicit user approval → B allowed.
- **Unbound** legacy run on B → **unchanged old behavior** (gate inert without a bound scope).

## Forbidden in this issue
Model-initiated scope widening; scope-miss auto-escalating to approval; a second executor/registry.
