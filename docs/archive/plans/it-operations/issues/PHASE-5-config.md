# PHASE-5-config — Configuration Operations

> Архив прежнего плана из `.scratch/`, перенесён 18.10.2026.
> Статусы относятся к исходному этапу; это не текущая очередь задач.


- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-4

## Scope
- Typed handlers: JSON, YAML, XML, nginx, PowerShell (GPO/IIS later).
- Parse -> semantic diff -> native validation -> snapshot -> apply -> reload/restart -> runtime health verifier -> rollback.
- 'File contains string' NEVER confirms the service accepted the config.

## Definition of Done (behavior, not labels)
- Valid apply; invalid-config rejection BEFORE reload; failed-health rollback.
- Exact before/after evidence.

## Forbidden in this issue
Textual-contains as proof of acceptance.
