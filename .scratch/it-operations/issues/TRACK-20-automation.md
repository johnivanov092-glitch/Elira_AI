# TRACK-20-automation — Automation and Scheduling

- **Layer:** Fleet ops
- **Status:** planned (not now)
- **Depends on:** ALL read/change workflows stable manually first

## Scope
- Only after each read/change workflow is stable manually.
- Scheduled runs are read-only by default.
- Scheduled changes require a PRE-APPROVED named ChangeRun template with bounded scope, expiry, notifications, and a rollback rule.

## Definition of Done (behavior, not labels)
- A scheduled read-only run works unattended; a scheduled change runs only from a pre-approved bounded template with expiry+rollback.

## Forbidden in this issue
Unbounded or unapproved scheduled changes.
