# TRACK-13-containers — Containers and Orchestration

- **Layer:** Platform ops
- **Status:** planned (not now)
- **Depends on:** PHASE-4 (Infra)

## Scope
- Docker/Compose first; Kubernetes only after Docker is stable.
- Inspect image/container/service/logs/health first.
- Deploy: inspect -> compose/config validation -> snapshot/tag -> apply -> health -> rollback. Image pull/prune/volume-delete/cluster changes = critical.

## Definition of Done (behavior, not labels)
- Inspect read-only; a deploy validates, snapshots/tags, applies, health-verifies, rolls back on failure.
