"""IT Operations program (Phase 0+) — application layer.

The LLM proposes; the runtime owns asset scope, credentials, approval, snapshot,
verifier, rollback and final status. See docs/IT_OPERATIONS_PLAN.md. Persistence
lives in the single infrastructure store `app.infrastructure.it_ops.store`; secret
VALUES live only in Windows Credential Manager (never in the DB).
"""
