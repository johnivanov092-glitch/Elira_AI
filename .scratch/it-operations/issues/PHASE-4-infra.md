# PHASE-4-infra — Infrastructure Operations

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-3

## Scope
- Adapter pattern (systemd, Windows Services, IIS, Docker/Compose; K8s later) - not generic shell lore.
- Inspect + health first; then controlled lifecycle changes.
- Each adapter operation declares `rollback_kind ∈ {automatic, manual, none}`.
- **Public `apply_change` and manual `rollback_change` are CRITICAL (approval-gated; rollback strategy shown on the approval card up front).** The **automatic compensating rollback is runtime-internal — NOT a model-callable tool, NOT in CRITICAL_TOOLS** — bound to the exact approved ChangeRun+snapshot, fired only after a real failed targeted verifier, no second approval.
- Change contract: inspect -> snapshot -> approval -> apply -> targeted health verifier -> automatic rollback ONLY if snapshot reversible AND rollback_kind=automatic; otherwise the recorded manual/none procedure.
- Two axes tracked: `completion_status` (task) and `change_run_status` (lifecycle: applied/rollback_in_progress/rolled_back/rollback_failed/applied_pending_verification/committed).

## Definition of Done (behavior, not labels)
- One Linux + one Windows live canary.
- Abandoned/cancelled runs leave no orphan server/process.
- verifier red + auto-rollback ok → completion_status=failed, change_run_status=rolled_back.
- verifier red + rollback failed → completion_status=failed, change_run_status=rollback_failed.
- non-reversible/manual after apply → change_run_status=applied_pending_verification + explicit next step (never silent commit or silent revert).

## Forbidden in this issue
Generic provider shell adapter.
