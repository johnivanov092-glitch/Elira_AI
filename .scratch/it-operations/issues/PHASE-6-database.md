# PHASE-6-database — Database Operations

- **Layer:** Core ops
- **Status:** planned
- **Depends on:** PHASE-5

## Scope
- Database profiles use secret_ref + explicit allowed schemas.
- Read-only first: schema, migration status, backup freshness, safe query.
- Write/migration only after backup/snapshot + explicit approval; transaction where supported; post-query verifier; rollback procedure recorded.

## Definition of Done (behavior, not labels)
- Disposable DB canaries: read, failed migration, successful migration, restore/rollback.
- NO production DB mutation in initial live tests.

## Forbidden in this issue
Connection string in args/output/journal.
