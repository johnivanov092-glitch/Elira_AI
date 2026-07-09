# PHASE-7-device-adapters — Device Adapters

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-6

## Scope
- Generic network-device asset model; MikroTik = one adapter (reuse existing mikrotik MCP), alongside Linux/Windows and future devices.
- Read inventory/export first.
- Firewall/NAT/routing changes = separate ChangeRuns with export/snapshot, approval, post-change connectivity test, rollback.

## Definition of Done (behavior, not labels)
- Lab-only device canary.
- No production firewall changes during development.

## Forbidden in this issue
Duplicating the MCP roster; a second device runtime.
