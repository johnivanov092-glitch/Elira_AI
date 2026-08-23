# PHASE-0-foundation — исторический Foundation / contract

> **SUPERSEDED (2026-08-23):** вся задача заменена единым Workflow control plane,
> `runtime_control` и portable vault из
> `../../universal-agent-workflow/issues/02-portable-workflow-vault.md`.
> Упомянутые ниже `/api/itops/*`, feature flag, operation scopes и отдельный
> Assets UI удалены и не должны восстанавливаться.

- **Layer:** Foundation
- **Status:** superseded / closed
- **Depends on:** —

## Scope
- Domain-model store (Asset, ConnectionProfile, OperationScope, Snapshot, Evidence, ChangeRun + secret_ref STATE table) in ONE new infrastructure/it_ops/store.py — forward-only migrations, NO second secrets DB.
- Secret vault + secret_ref: **secret VALUE only in Windows Credential Manager**; `it_ops.sqlite3` holds only `secret_ref` + metadata/lifecycle — NO ciphertext, NO DPAPI blob, nothing decryptable in the app SQLite. NO Fernet, NO Linux path (Windows-host; AI-server never receives ops creds). A second vault backend = future separate ADR. Secure write-only intake endpoint.
- change_runs schema carries `rollback_kind` + **TWO separate axes**: `change_run_status` (lifecycle: planned…committed/applied_pending_verification/rollback_in_progress/rolled_back/rollback_failed) and `completion_status` (task axis, unchanged) — never merged.
- Close live leak surfaces: sink-level redaction on event_bus + run_journal; fix GET /mcp/servers verbatim secret leak; output canary for resolved values.
- itops feature flag (two-file sync). Read-only Assets Settings tab (state only).
- Full detail: .scratch/it-operations/FOUNDATION_IMPL.md.

## Definition of Done (behavior, not labels)
- Contract tests: raw secret in NONE of the 12 surfaces — model messages, persisted chat, context summary, history, tool args, args_sha256 input, approval display, SSE/events, journals, logs, error text, API GET responses.
- Vault round-trip via Credential Manager; the DB holds no plaintext value; revoke (CredDelete) fail-closed.
- GET /mcp/servers no longer returns secret values.
- **itops flag OFF disables endpoints + schema init + UI entrypoints + deferred tools** (not just hides UI).
- Forward-only migration idempotent.

## Forbidden in this issue
No ops-tools, no enrollment flow, no asset actions.
