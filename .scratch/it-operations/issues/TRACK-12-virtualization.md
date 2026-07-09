# TRACK-12-virtualization — Virtualization and Compute

- **Layer:** Platform ops
- **Status:** planned (not now)
- **Depends on:** PHASE-4 (Infra)

## Scope
- Hyper-V, Proxmox, VMware; cloud instances later.
- VM/container inventory, power state, snapshot, health, console metadata.
- Stop/start/reboot/resize/delete -> snapshot + approval + post-health.

## Definition of Done (behavior, not labels)
- Inventory read-only; a power/resize/delete op snapshots, approves, and verifies post-health.

## Forbidden in this issue
Generic provider shell adapter.
