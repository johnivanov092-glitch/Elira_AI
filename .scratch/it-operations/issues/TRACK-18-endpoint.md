# TRACK-18-endpoint — Endpoint and Asset Management

- **Layer:** Fleet ops
- **Status:** planned (not now)
- **Depends on:** PHASE-3 (Network), TRACK-10 (Identity)

## Scope
- Windows/Linux workstation inventory: hardware/OS posture, installed software, disk, Defender/EDR state, certificate status.
- Remote-control actions are SEPARATE from inventory and always approval-gated.

## Definition of Done (behavior, not labels)
- Posture inventory read-only; any remote-control action is a distinct, approval-gated ChangeRun.
