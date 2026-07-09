# PHASE-5-config — Configuration Operations

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
