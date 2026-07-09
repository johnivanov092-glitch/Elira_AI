# PHASE-8-reporting-ui — Reporting, UI and Operations

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-7

## Scope
- Asset inventory, connection health, scope picker, secret state (no value), audit timeline, evidence, rollback status.
- Runtime final report shows BOTH axes separately from runtime ok: task axis `completion_status` (confirmed/partial/failed/unverified/n-a) AND lifecycle axis `change_run_status` (planned…committed/applied_pending_verification/rollback_in_progress/rolled_back/rollback_failed). The approval card shows the rollback strategy (rollback_kind) up front.
- UI shows secret_ref state/age/rotate/revoke only, never the value.

## Definition of Done (behavior, not labels)
- Compact UI; no-secret-leakage snapshot tests.
- Revoked asset visibly fails.

## Forbidden in this issue
Any secret value in the UI or exports.
