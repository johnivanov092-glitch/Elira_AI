# TRACK-15-patch — Patch and Package Lifecycle

- **Layer:** Delivery ops
- **Status:** planned (not now)
- **Depends on:** PHASE-4 (Infra), PHASE-5 (Config)

## Scope
- apt/dnf/pacman, winget/choco, Windows Update, language package managers.
- Inventory pending updates + security relevance first.
- Apply only with maintenance policy, reboot policy, health checks, rollback/recovery plan. Reboot = explicit critical operation.

## Definition of Done (behavior, not labels)
- Pending-update inventory; an apply respects maintenance+reboot policy, health-checks, and can recover/rollback.
