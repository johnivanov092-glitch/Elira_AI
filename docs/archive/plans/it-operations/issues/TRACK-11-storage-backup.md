# TRACK-11-storage-backup — Storage, Backup and Restore

> Архив прежнего плана из `.scratch/`, перенесён 18.10.2026.
> Статусы относятся к исходному этапу; это не текущая очередь задач.


- **Layer:** Platform ops
- **Status:** planned (not now)
- **Depends on:** PHASE-4 (Infra)

## Scope
- Local disks, SMB/NFS shares, NAS, object storage, backup systems.
- Inventory: capacity, SMART/health where available, backup age, encryption, last successful job.
- 'Backup exists' is NOT success -> restore test or integrity verifier required. Retention/deletion = critical approval.

## Definition of Done (behavior, not labels)
- Restore/integrity verifier proves a backup is usable; deletion is critical-gated.
