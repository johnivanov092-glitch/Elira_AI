# Phase 0 — Foundation: implementation plan (for approval)

> **SUPERSEDED SECURITY DECISION (2026-08-11):** разделы WinCred/DPAPI ниже
> описывают текущую историческую реализацию, но не целевую архитектуру. Новая
> работа должна мигрировать secrets в portable application-owned encrypted vault
> через workflow UI согласно `../universal-agent-workflow/PRD.md` и issue 02.

**Gate:** ships and is reviewed BEFORE Connection Enrollment. Foundation builds
the **security spine + data model + leak-closing + contract tests** — no user
enrollment flow yet, no ops-tools yet. Nothing here lets the model act on an
asset; it makes it *safe to* in Phase 1.

## Scope of Phase 0

1. `it_ops` durable store (assets/profiles/scopes/snapshots/evidence/change_runs + a secret-ref **state** table) + forward-only migration runner — **ONE `it_ops.sqlite3`, no second secrets DB**.
2. Secret vault (`secret_ref`) — **Windows Credential Manager is the only Phase-0 vault. DPAPI is an OS-internal protection mechanism beneath Credential Manager; the application never invokes DPAPI and has no fallback vault path.** (Windows-host design; no Fernet, no Linux path.)
3. Secure write-only intake endpoint (raw secret never logged/echoed/persisted-plaintext).
4. Close the **live leak surfaces** (this is the part that makes the DoD honest).
5. `itops` feature flag — OFF disables **endpoints + schema init + UI entrypoints + deferred tools** (bit-identical off).
6. Read-only "Активы / Подключения" Settings tab skeleton (state only, no value).
7. Contract tests proving no raw secret in any of the 12 surfaces (§Tests).

## Files

**New (backend):**
- `app/infrastructure/it_ops/__init__.py`
- `app/infrastructure/it_ops/store.py` — the 6-table store + `_init_db()` + `migrate_*`.
- `app/infrastructure/secrets/__init__.py`
- `app/infrastructure/secrets/vault.py` — `put_secret / resolve / state / revoke`; **Windows Credential Manager** backend (`CredWrite`/`CredRead`/`CredDelete` via `ctypes`) — Credential Manager protects the value with DPAPI under the hood (OS-internal; the app never invokes DPAPI or stores a blob). Windows-only (import guarded → the whole program is inert off-Windows).
- `app/infrastructure/secrets/wincred.py` — `ctypes` `advapi32` Credential Manager wrapper: **`CredWrite`/`CredRead`/`CredDelete` only**. No application-managed DPAPI ciphertext path in Phase 0 (a second/blob backend would be a future separate ADR).
- `app/api/routes/itops_routes.py` — `/api/itops/*` (intake write-only + asset/state reads).
- `app/application/it_ops/domain.py` — dataclasses (Asset, ConnectionProfile, OperationScope, Snapshot, Evidence, ChangeRun) — pure types, no I/O.
- `app/application/it_ops/redaction_sink.py` — the sink-level scrub wrapper (wraps event_bus + run_journal writes).

**Modified (backend, surgical):**
- `app/application/feature_flags.py` — add `"itops": "ELIRA_ITOPS"` to `_ENV_VAR` (auto-off via `_DEFAULTS`).
- `app/application/event_bus/store.py` — call the canonical scrub at the emit sink (currently trusts callers, `store.py:85-113`).
- `app/application/…/run_journal.py` — scrub VALUES, not only dict keys, at append.
- `app/api/routes/code_agent_routes.py` — `GET /mcp/servers` (`~695-699`) strips `secret_headers`/`env` values (write-only).
- `app/main.py` — register `itops_routes` router; call `it_ops.store._init_db()` at startup (resilient, mirrors monitoring).
- `app/core/redaction.py` — add a `mask_known_secret(value, text)` helper for the output canary (mask any resolved secret that reappears).

**New (frontend, minimal):**
- `frontend/src/workspace/settings/AssetsSection.tsx` — read-only list + secret STATE chips (present/temporary/rotated/revoked), no value, no form yet.
- `frontend/src/workspace/Settings.tsx` — 3 edits (union + NAV entry + render), Lazy.
- `frontend/src/workspace/settings/ExperimentalSection.tsx` — add `itops` to FLAG_META.
- `frontend/src/api/itops.ts` — typed client for the read endpoints.

## Schemas (SQLite, forward-only)

**`data/it_ops.sqlite3`** (via `connect_sqlite`; `PRAGMA user_version` as a ladder):

```sql
CREATE TABLE IF NOT EXISTS assets (
  asset_id TEXT PRIMARY KEY,            -- slug, e.g. 'ubuntu-client-01'
  label TEXT NOT NULL, kind TEXT NOT NULL,
  endpoint TEXT, tags TEXT DEFAULT '[]',
  owner_scope TEXT NOT NULL,            -- project_scope_id
  lifecycle_state TEXT NOT NULL DEFAULT 'draft', -- draft|enabled|disabled|revoked
  created_at REAL NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS connection_profiles (
  profile_id TEXT PRIMARY KEY,
  asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
  transport TEXT NOT NULL,              -- ssh|winrm|mcp|local
  user TEXT, auth_ref TEXT,             -- auth_ref = secret_ref (nullable = key-only)
  ssh_alias TEXT, host_key_fingerprint TEXT,
  os_platform_meta TEXT DEFAULT '{}', last_health TEXT DEFAULT '{}',
  created_at REAL NOT NULL, updated_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS operation_scopes (   -- audit copy; live state is in-memory
  scope_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
  allowed_asset_ids TEXT DEFAULT '[]', cidrs TEXT DEFAULT '[]',
  local_roots TEXT DEFAULT '[]', service_ids TEXT DEFAULT '[]',
  config_roots TEXT DEFAULT '[]', db_profiles TEXT DEFAULT '[]',
  mode TEXT NOT NULL DEFAULT 'read_only',        -- read_only|change
  approved_by TEXT, approved_at REAL);

CREATE TABLE IF NOT EXISTS snapshots (
  snapshot_id TEXT PRIMARY KEY,
  change_run_id TEXT NOT NULL, asset_id TEXT NOT NULL,
  before_state TEXT, artifact_path TEXT,         -- inline OR pointer for large blobs
  content_hash TEXT NOT NULL, captured_at REAL NOT NULL,
  rollback_ref TEXT, restored_at REAL);

CREATE TABLE IF NOT EXISTS evidence (
  evidence_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
  change_run_id TEXT, target_identity TEXT NOT NULL,
  scanner_vantage TEXT NOT NULL,                 -- 'local' | asset_id
  operation TEXT NOT NULL, result TEXT DEFAULT '{}',
  exit_status TEXT, captured_at REAL NOT NULL);

CREATE TABLE IF NOT EXISTS change_runs (
  change_run_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, asset_id TEXT NOT NULL,
  plan TEXT DEFAULT '{}',
  approval_id TEXT,                              -- → agent_monitor.db approvals (no dup)
  snapshot_id TEXT,
  rollback_kind TEXT NOT NULL DEFAULT 'none',    -- automatic|manual|none (adapter-declared)
  -- TWO SEPARATE AXES (do not mix):
  change_run_status TEXT NOT NULL DEFAULT 'planned',
    -- LIFECYCLE axis: planned|approved|snapshotted|applied|committed
    --                 |applied_pending_verification|rollback_in_progress
    --                 |rolled_back|rollback_failed
  completion_status TEXT,                        -- TASK axis snapshot at finalize:
                                                 -- confirmed|partial|failed|unverified|n/a
                                                 -- (from CriteriaTracker, UNCHANGED; NOT an
                                                 --  extension of change_run_status)
  created_at REAL NOT NULL, updated_at REAL NOT NULL);

-- secret_ref STATE + metadata ONLY. The secret VALUE lives exclusively in Windows
-- Credential Manager — no ciphertext, no DPAPI blob, no secret value is ever
-- persisted in this application SQLite. (A second vault backend would be a future
-- separate ADR, never a hidden fallback here.)
CREATE TABLE IF NOT EXISTS secret_refs (
  secret_ref TEXT PRIMARY KEY,          -- opaque 'sref_<uuid4>' == Credential Manager target
  backend TEXT NOT NULL DEFAULT 'wincred', -- 'wincred' only in Foundation
  kind TEXT NOT NULL,                   -- password|private_key|token|connection_string
  asset_id TEXT,                        -- binding (nullable at intake)
  lifecycle TEXT NOT NULL DEFAULT 'temporary', -- temporary|persistent|rotated|revoked
  created_at REAL NOT NULL, rotated_at REAL, revoked_at REAL);
```

No `data/secrets.sqlite3` and no ciphertext/DPAPI blob in `it_ops.sqlite3`: the
secret value lives **only** in Windows Credential Manager; the application SQLite
holds `secret_ref` + metadata/lifecycle and nothing decryptable. DPAPI may be an
internal detail of the Windows credential stack, but no secret material is ever
written to the application DB.

## Migration strategy

- `_init_db()` at import (resilient, mirrors `monitoring/runtime.py:_init_db`):
  `CREATE TABLE IF NOT EXISTS` for all tables, then a chain of named idempotent
  `migrate_0001_*`, `migrate_0002_*` (add-column-if-missing via `PRAGMA
  table_info`, backfills). **Forward-only ladder** on `PRAGMA user_version`
  (apply N→N+1, never DROP). Durable records — `web_corpus`'s drop-on-change is
  explicitly forbidden here.
- Failure policy: a fatal migration must fail LOUD but must not wedge unrelated
  startup — wrap like monitoring, log the exact step, and leave the store
  `StoreUnavailable` (fail-soft reads) rather than crashing the app.

## Secret vault behavior (Windows Credential Manager primary)

- `put_secret(kind, raw, asset_id=None, lifecycle='temporary') -> secret_ref`:
  write the value into **Windows Credential Manager** (`CredWrite`,
  `CRED_TYPE_GENERIC`, target = the opaque `secret_ref`, per-user). **The value
  lives ONLY in Credential Manager** — no ciphertext, no DPAPI blob, nothing
  decryptable is written to `it_ops.sqlite3` (only `secret_ref` + metadata). **No
  Fernet, no Linux path; a second vault backend is a future ADR, not a fallback.**
  Raw is never logged, never returned by any read endpoint. Return the opaque ref.
- `resolve(secret_ref) -> str`: **runtime-only**, called INSIDE dispatch after all
  gates; reads the value from Credential Manager, uses it to build the transport
  (fed to the OS ssh layer / subprocess stdin, **never into argv**), then drops
  it. Not callable from any LLM-facing tool.
- `state(secret_ref) -> {kind, lifecycle, age, backend}` (no value).
- `revoke(secret_ref)`: `CredDelete` the entry + mark revoked (zero any blob); a
  resolve after revoke fails closed.
- **Off-Windows the vault import fails closed** → the whole `itops` program is
  inert (no endpoints, no tools), matching the flag-off contract.

## API contract (Phase 0)

- `POST /api/itops/secrets` **(write-only intake)** — body `{kind, value, asset_id?}`
  → `{secret_ref, state}`. The handler excludes the body from request logging,
  never echoes `value`, writes the value into **Windows Credential Manager**, and
  returns only the ref + state. Localhost/same-origin only. **This is the single
  place plaintext exists client→server.**
- `POST /api/itops/secrets/{ref}/revoke` → `{ok}`.
- `GET /api/itops/assets` → assets + connection state + secret **state** (never value).
- `GET /api/itops/secrets/{ref}` → **state only** (never value). (Or omit; state via assets.)
- **All `/api/itops/*` routes are disabled (404/`flag_disabled`) when the `itops`
  flag is OFF** — the flag gates the router mount, not just the UI.
- No enrollment / connect / scope-grant endpoints in Phase 0 (those are Phase 1–2).

## UI contract (Phase 0)

- One deferred flag `itops` in `ExperimentalSection` (OFF by default, no restart).
- `AssetsSection` (Lazy): read-only list; each asset shows kind, endpoint,
  lifecycle, connection health, and a **secret-state chip** (present/temporary/
  rotated/revoked) — **never a value, never a copy button**. Empty state until
  Phase 1 adds enrollment.
- No secret is ever rendered; the approval-card path is untouched in Phase 0.

## Tests (contract-first — behavior, not labels)

Backend `tests/test_itops_foundation.py` (Credential Manager mocked so tests run
headless; a Windows-only marker covers the real `CredWrite`/`CredRead` path):
1. **vault round-trip:** `put_secret`→`resolve` returns the value via Credential
   Manager; `state` never contains it.
2. **DB holds no value and nothing decryptable:** reading `it_ops.sqlite3`
   directly yields only `secret_ref` + metadata/lifecycle — no ciphertext, no
   DPAPI blob, no plaintext; the value is only in Credential Manager.
3. **secret_ref-only across ALL 12 surfaces (core DoD):** simulate a tool call
   carrying `auth_ref=secret_ref` and a resolved value in flight → assert the raw
   value is absent from **every one** of: model messages, persisted chat, context
   summary, run history, `ToolExecutionRequest.args`, the `args_sha256` digest
   input, the approval display (`args_json`), SSE/event payloads, run journals,
   log lines, error text, and API GET responses.
4. **sink redaction:** emit an event / journal entry with a secret in a
   NON-secret-named field → the persisted row is redacted at the sink.
5. **`GET /mcp/servers` no longer leaks:** `secret_headers`/`env` values are
   stripped in the response.
6. **output canary:** a tool output string containing the resolved secret value is
   masked before it reaches history/UI/events.
7. **revoke fail-closed:** `resolve` after `revoke` (`CredDelete`) raises/returns
   closed.
8. **forward-only migration:** apply migrations twice (idempotent); an older
   `user_version` upgrades without dropping rows.
9. **flag OFF is fully inert (not just hidden UI):** with `itops` off —
   `/api/itops/*` routes return disabled/404, `_init_db` is not run (or the schema
   is never queried by the agent path), NO ops-tool is registered/activatable, and
   `build_tool_schemas()`/tool_search/base-prompt bytes are unchanged.
10. **revoked asset visibly fails:** a revoked `secret_ref` surfaces `revoked`
    state, not a value.
11. **two independent axes in the schema:** a `change_runs` row can hold
    `completion_status='failed'` together with `change_run_status='rolled_back'`
    (and separately `='rollback_failed'`); the migration + store accept both
    columns and never coerce one into the other.

Frontend: `AssetsSection` renders state chips; a snapshot test asserts **no
secret value** appears in the DOM for any asset fixture.

> **Scope legacy-coverage regressions (Phase 2, listed here for continuity):**
> with a bound OperationScope, the executor gate covers the EXISTING transport
> tools too — `ssh_run/ssh_run_ps/ssh_write/ssh_read/ssh_replace`, the host/assert
> tools, and a raw-SSH redirect through `run_bash`. Executor-path tests:
> (a) SSH ACL allows A+B, scope allows only A → `ssh_run(B)` blocked before
> dispatch; (b) `run_bash` raw-SSH redirect to B → blocked; (c) `scope_request(B)`
> + explicit user approval → B allowed; (d) an UNBOUND legacy run on B → unchanged
> old behavior (gate inert without a bound scope).

Release discipline: focused `test_itops_foundation.py` → full backend suite →
`npm run typecheck` + `build` (UI touched) → adversarial review of the leak
surfaces → **no live canary in Phase 0** (no asset actions yet).

## Migration / compatibility risks (Phase 0)

- **`ssh_acl.json` untouched** in Phase 0 — the Asset store is additive and does
  not yet supersede the ACL, so `is_ssh_enabled()` is unaffected.
- **Startup coupling:** `_init_db()` at import must be resilient (a bad migration
  must not wedge app startup) — mirror monitoring's guarded init.
- **Two-file flag sync:** add `itops` to `_ENV_VAR` AND the `elira_state` Literal
  in the same commit or the PUT 422s silently.
- **Windows-host vault:** Credential Manager (DPAPI internal) is Windows-only by
  design. **No Fernet, no Linux fallback** — off-Windows the vault import fails
  closed and the whole program is inert. A separate-Linux-backend deployment is a
  future, separately-threat-modelled decision, not covered here.
- **Sink-redaction perf:** scrubbing every event/journal write adds cost — keep
  the canonical scrub fast (compiled regex, key-name short-circuit) and covered
  by a perf-sanity assertion.
- **No behavioral change when off:** the whole program is inert with `itops` off;
  the compaction canary and existing smokes must stay green.

## What Foundation deliberately does NOT do

No enrollment card, no connect, no ops-tools, no scope grant, no snapshot/rollback
execution, no network/infra/config/db actions. Those are Phases 1–6, each its own
reviewed increment. Foundation only makes the contract real and tested.
