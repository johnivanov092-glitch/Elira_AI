"""IT Operations durable store — schema, forward-only migrations, and CRUD.

Mirrors the monitoring-store convention: `CREATE TABLE IF NOT EXISTS` + additive,
idempotent column migrations, plus a forward-only `PRAGMA user_version` ladder.
Durable records — no drop-on-change.

Contract:
  * enum values (asset/secret kind + lifecycle, rollback_kind, both status axes)
    are validated against the domain enums BEFORE any SQL — an invalid value is a
    ValueError, not a silent bad row.
  * every query/commit/migration sqlite error becomes StoreUnavailable, with
    rollback + close guaranteed (fail-soft).
  * migrations are honest: user_version is stamped ONLY after the on-disk shape
    actually matches the version (all expected columns present); an unrecoverable
    schema raises StoreUnavailable and does NOT bump the version.
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any, Callable

from app.core.data_files import data_file
from app.domain import it_ops as _dom
from app.infrastructure.db.connection import connect_sqlite

# Forward-only ladder. Bump ONLY with a matching column spec; never DROP.
_SCHEMA_VERSION = 1

# Test override (mirrors web_corpus store). None → the real data file.
_DB_PATH_OVERRIDE: str | None = None

_CREATE_SQL = """
CREATE TABLE IF NOT EXISTS assets (
    asset_id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    kind TEXT NOT NULL,
    endpoint TEXT DEFAULT '',
    tags TEXT NOT NULL DEFAULT '[]',
    owner_scope TEXT NOT NULL DEFAULT '',
    lifecycle_state TEXT NOT NULL DEFAULT 'draft',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS connection_profiles (
    profile_id TEXT PRIMARY KEY,
    asset_id TEXT NOT NULL REFERENCES assets(asset_id) ON DELETE CASCADE,
    transport TEXT NOT NULL,
    user TEXT DEFAULT '',
    auth_ref TEXT,
    ssh_alias TEXT DEFAULT '',
    host_key_fingerprint TEXT DEFAULT '',
    os_platform_meta TEXT NOT NULL DEFAULT '{}',
    last_health TEXT NOT NULL DEFAULT '{}',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS operation_scopes (
    scope_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    allowed_asset_ids TEXT NOT NULL DEFAULT '[]',
    cidrs TEXT NOT NULL DEFAULT '[]',
    local_roots TEXT NOT NULL DEFAULT '[]',
    service_ids TEXT NOT NULL DEFAULT '[]',
    config_roots TEXT NOT NULL DEFAULT '[]',
    db_profiles TEXT NOT NULL DEFAULT '[]',
    mode TEXT NOT NULL DEFAULT 'read_only',
    approved_by TEXT DEFAULT '',
    approved_at REAL
);
CREATE TABLE IF NOT EXISTS snapshots (
    snapshot_id TEXT PRIMARY KEY,
    change_run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    before_state TEXT DEFAULT '',
    artifact_path TEXT DEFAULT '',
    content_hash TEXT NOT NULL DEFAULT '',
    captured_at REAL NOT NULL,
    rollback_ref TEXT DEFAULT '',
    restored_at REAL
);
CREATE TABLE IF NOT EXISTS evidence (
    evidence_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    change_run_id TEXT,
    target_identity TEXT NOT NULL,
    scanner_vantage TEXT NOT NULL,
    operation TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{}',
    exit_status TEXT DEFAULT '',
    captured_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS change_runs (
    change_run_id TEXT PRIMARY KEY,
    run_id TEXT NOT NULL,
    asset_id TEXT NOT NULL,
    plan TEXT NOT NULL DEFAULT '{}',
    approval_id TEXT,
    snapshot_id TEXT,
    rollback_kind TEXT NOT NULL DEFAULT 'none',
    change_run_status TEXT NOT NULL DEFAULT 'planned',
    completion_status TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS secret_refs (
    secret_ref TEXT PRIMARY KEY,
    backend TEXT NOT NULL DEFAULT 'wincred',
    kind TEXT NOT NULL,
    asset_id TEXT,
    lifecycle TEXT NOT NULL DEFAULT 'provisioning',
    origin TEXT NOT NULL DEFAULT 'secure_intake',
    recovery_attempts INTEGER NOT NULL DEFAULT 0,
    last_recovery_at REAL,
    created_at REAL NOT NULL,
    rotated_at REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_profiles_asset ON connection_profiles(asset_id);
CREATE INDEX IF NOT EXISTS idx_change_runs_run ON change_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence(run_id);
"""

# Expected columns per table → the additive `ALTER TABLE ADD COLUMN` clause used to
# bring an OLDER on-disk table up to the current shape. Adds are nullable / have a
# default so they are safe on a table with existing rows.
_EXPECTED_COLUMNS: dict[str, dict[str, str]] = {
    "assets": {
        "asset_id": "asset_id TEXT", "label": "label TEXT",
        "kind": "kind TEXT", "endpoint": "endpoint TEXT DEFAULT ''",
        "tags": "tags TEXT NOT NULL DEFAULT '[]'",
        "owner_scope": "owner_scope TEXT NOT NULL DEFAULT ''",
        "lifecycle_state": "lifecycle_state TEXT NOT NULL DEFAULT 'draft'",
        "created_at": "created_at REAL", "updated_at": "updated_at REAL",
    },
    "connection_profiles": {
        "profile_id": "profile_id TEXT", "asset_id": "asset_id TEXT",
        "transport": "transport TEXT", "user": "user TEXT DEFAULT ''",
        "auth_ref": "auth_ref TEXT", "ssh_alias": "ssh_alias TEXT DEFAULT ''",
        "host_key_fingerprint": "host_key_fingerprint TEXT DEFAULT ''",
        "os_platform_meta": "os_platform_meta TEXT NOT NULL DEFAULT '{}'",
        "last_health": "last_health TEXT NOT NULL DEFAULT '{}'",
        "created_at": "created_at REAL", "updated_at": "updated_at REAL",
    },
    "operation_scopes": {
        "scope_id": "scope_id TEXT", "run_id": "run_id TEXT",
        "allowed_asset_ids": "allowed_asset_ids TEXT NOT NULL DEFAULT '[]'",
        "cidrs": "cidrs TEXT NOT NULL DEFAULT '[]'",
        "local_roots": "local_roots TEXT NOT NULL DEFAULT '[]'",
        "service_ids": "service_ids TEXT NOT NULL DEFAULT '[]'",
        "config_roots": "config_roots TEXT NOT NULL DEFAULT '[]'",
        "db_profiles": "db_profiles TEXT NOT NULL DEFAULT '[]'",
        "mode": "mode TEXT NOT NULL DEFAULT 'read_only'",
        "approved_by": "approved_by TEXT DEFAULT ''", "approved_at": "approved_at REAL",
    },
    "snapshots": {
        "snapshot_id": "snapshot_id TEXT", "change_run_id": "change_run_id TEXT",
        "asset_id": "asset_id TEXT", "before_state": "before_state TEXT DEFAULT ''",
        "artifact_path": "artifact_path TEXT DEFAULT ''",
        "content_hash": "content_hash TEXT NOT NULL DEFAULT ''",
        "captured_at": "captured_at REAL", "rollback_ref": "rollback_ref TEXT DEFAULT ''",
        "restored_at": "restored_at REAL",
    },
    "evidence": {
        "evidence_id": "evidence_id TEXT", "run_id": "run_id TEXT",
        "change_run_id": "change_run_id TEXT", "target_identity": "target_identity TEXT",
        "scanner_vantage": "scanner_vantage TEXT", "operation": "operation TEXT",
        "result": "result TEXT NOT NULL DEFAULT '{}'", "exit_status": "exit_status TEXT DEFAULT ''",
        "captured_at": "captured_at REAL",
    },
    "change_runs": {
        "change_run_id": "change_run_id TEXT", "run_id": "run_id TEXT",
        "asset_id": "asset_id TEXT", "plan": "plan TEXT NOT NULL DEFAULT '{}'",
        "approval_id": "approval_id TEXT", "snapshot_id": "snapshot_id TEXT",
        "rollback_kind": "rollback_kind TEXT NOT NULL DEFAULT 'none'",
        "change_run_status": "change_run_status TEXT NOT NULL DEFAULT 'planned'",
        "completion_status": "completion_status TEXT",
        "created_at": "created_at REAL", "updated_at": "updated_at REAL",
    },
    "secret_refs": {
        "secret_ref": "secret_ref TEXT", "backend": "backend TEXT NOT NULL DEFAULT 'wincred'",
        "kind": "kind TEXT", "asset_id": "asset_id TEXT",
        "lifecycle": "lifecycle TEXT NOT NULL DEFAULT 'provisioning'",
        "origin": "origin TEXT NOT NULL DEFAULT 'secure_intake'",
        "recovery_attempts": "recovery_attempts INTEGER NOT NULL DEFAULT 0",
        "last_recovery_at": "last_recovery_at REAL",
        "created_at": "created_at REAL", "rotated_at": "rotated_at REAL",
        "revoked_at": "revoked_at REAL",
    },
}

# Max auto-recovery attempts across startups. After this, a record goes terminal
# `recovery_failed` (visible state) and is never auto-deleted/retried again — only
# explicit manual recovery later.
MAX_RECOVERY_ATTEMPTS = 2


class StoreUnavailable(RuntimeError):
    """The IT-Ops store could not be opened/queried/migrated — fail-soft signal."""


def _now() -> float:
    return time.time()


def _db_path() -> str:
    return _DB_PATH_OVERRIDE or str(data_file("it_ops.sqlite3"))


def _connect() -> sqlite3.Connection:
    try:
        conn = connect_sqlite(_db_path(), row_factory=sqlite3.Row)
        conn.execute("PRAGMA foreign_keys=ON")
        return conn
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise StoreUnavailable(f"it_ops store unavailable: {exc}") from exc


# ── enum validation (BEFORE any SQL) ────────────────────────────────────────

def _check(value: Any, allowed: tuple, what: str) -> None:
    if value not in allowed:
        raise ValueError(f"invalid {what}: {value!r} (allowed: {list(allowed)})")


# ── init + honest migration ─────────────────────────────────────────────────

# Structural compatibility spec: single-column primary key, core columns that MUST
# be NOT NULL, and declared foreign keys. Names alone are not enough — a legacy
# table with the right column names but a missing PK, a nullable core column, or a
# missing FK is structurally incompatible and is rejected (StoreUnavailable).
_EXPECTED_PK: dict[str, str] = {
    "assets": "asset_id", "connection_profiles": "profile_id",
    "operation_scopes": "scope_id", "snapshots": "snapshot_id",
    "evidence": "evidence_id", "change_runs": "change_run_id",
    "secret_refs": "secret_ref",
}
# Core columns required to be NOT NULL (only columns present in ANY compatible
# legacy schema — additive/optional columns are NOT listed here).
_REQUIRED_NOTNULL: dict[str, set[str]] = {
    "assets": {"label", "kind", "created_at", "updated_at"},
    "connection_profiles": {"asset_id", "transport", "created_at", "updated_at"},
    "operation_scopes": {"run_id"},
    "snapshots": {"change_run_id", "asset_id", "captured_at"},
    "evidence": {"run_id", "target_identity", "scanner_vantage", "operation", "captured_at"},
    "change_runs": {"run_id", "asset_id", "created_at", "updated_at"},
    "secret_refs": {"kind", "origin", "created_at"},
}
# Declared foreign keys: (from_col, ref_table, ref_col, on_delete).
_EXPECTED_FK: dict[str, list[tuple[str, str, str, str]]] = {
    "connection_profiles": [("asset_id", "assets", "asset_id", "CASCADE")],
}


def init_db() -> None:
    """Initialize the it_ops store with a NARROW, fail-closed model. it_ops v1 was
    never released, so there are no partial-v0 schemas in the wild to migrate:

      * user_version=0 AND no itops tables → create the canonical v1, stamp v1;
      * user_version=0 AND any itops table already exists → StoreUnavailable, DB
        left untouched, version NOT stamped (refuse a partial/unknown schema);
      * user_version=1 → VERIFY the exact structural contract, change nothing;
      * user_version>1 → fail-closed (a newer build wrote it).

    Any sqlite failure or a contract mismatch → StoreUnavailable, version unchanged.
    Future v2+ will be an explicit migration from a known prior version, not an
    ad-hoc column patch."""
    conn = _connect()
    try:
        ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if ver > _SCHEMA_VERSION:
            raise StoreUnavailable(
                f"it_ops schema user_version={ver} is newer than supported "
                f"{_SCHEMA_VERSION} (fail-closed)")
        if ver == 0:
            present = [t for t in _EXPECTED_PK if _table_exists(conn, t)]
            if present:
                raise StoreUnavailable(
                    f"it_ops user_version=0 but tables already exist {sorted(present)} — "
                    f"refusing to touch a partial/unknown schema (fail-closed)")
            conn.executescript(_CREATE_SQL)
            _verify_contract(conn)          # our own create must satisfy the contract
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        else:  # ver == _SCHEMA_VERSION (1): verify only, change nothing.
            _verify_contract(conn)
        conn.commit()
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StoreUnavailable(f"it_ops init failed: {exc}") from exc
    finally:
        conn.close()


def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _verify_contract(conn: sqlite3.Connection) -> None:
    """Verify the EXACT structural contract: every table present with its full
    expected column set, the correct single-column PK, core NOT NULL constraints,
    and declared FKs (including on_delete). Any mismatch raises — the caller turns
    it into StoreUnavailable and leaves the DB/version untouched."""
    for table, cols in _EXPECTED_COLUMNS.items():
        info = _table_info(conn, table)          # rows: (cid, name, type, notnull, dflt, pk)
        if not info:
            raise sqlite3.OperationalError(f"expected table {table!r} missing")
        names = {r[1] for r in info}
        notnull_cols = {r[1] for r in info if r[3] and int(r[3]) > 0}
        pk_cols = {r[1] for r in info if r[5] and int(r[5]) > 0}
        missing = set(cols) - names
        if missing:
            raise sqlite3.OperationalError(f"{table}: missing columns {sorted(missing)}")
        pk = _EXPECTED_PK[table]
        if pk_cols != {pk}:
            raise sqlite3.OperationalError(
                f"{table}: primary key is {sorted(pk_cols) or 'none'}, expected [{pk!r}]")
        for col in _REQUIRED_NOTNULL.get(table, set()):
            if col not in notnull_cols and col != pk:
                raise sqlite3.OperationalError(
                    f"{table}: core column {col!r} must be NOT NULL")
    for table, fks in _EXPECTED_FK.items():
        declared = conn.execute(f"PRAGMA foreign_key_list({table})").fetchall()
        # rows: (id, seq, table, from, to, on_update, on_delete, match)
        have = {(r[3], r[2], r[4], str(r[6]).upper()) for r in declared}
        for col, ref_table, ref_col, on_delete in fks:
            if (col, ref_table, ref_col, on_delete.upper()) not in have:
                raise sqlite3.OperationalError(
                    f"{table}: FK {col!r}->{ref_table}.{ref_col} on_delete={on_delete} "
                    f"not found (have {sorted(have)})")


# ── error-contract wrapper ──────────────────────────────────────────────────

def _wrap(op: Callable[[sqlite3.Connection], Any]) -> Any:
    """Run *op* on a fresh connection; any sqlite error → StoreUnavailable with
    rollback + close guaranteed."""
    conn = _connect()
    try:
        return op(conn)
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise StoreUnavailable(f"it_ops store operation failed: {exc}") from exc
    finally:
        conn.close()


def _dumps(v: Any) -> str:
    return json.dumps(v if v is not None else [], ensure_ascii=False)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


# ── assets ──────────────────────────────────────────────────────────────────

def upsert_asset(*, asset_id: str, label: str, kind: str, endpoint: str = "",
                 tags: list[str] | None = None, owner_scope: str = "",
                 lifecycle_state: str = "draft") -> dict[str, Any]:
    _check(kind, _dom.ASSET_KINDS, "asset kind")
    _check(lifecycle_state, _dom.ASSET_LIFECYCLE, "asset lifecycle_state")

    def op(conn):
        now = _now()
        conn.execute(
            "INSERT INTO assets (asset_id, label, kind, endpoint, tags, owner_scope,"
            " lifecycle_state, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?)"
            " ON CONFLICT(asset_id) DO UPDATE SET label=excluded.label, kind=excluded.kind,"
            " endpoint=excluded.endpoint, tags=excluded.tags, owner_scope=excluded.owner_scope,"
            " lifecycle_state=excluded.lifecycle_state, updated_at=excluded.updated_at",
            (asset_id, label, kind, endpoint, _dumps(tags or []), owner_scope,
             lifecycle_state, now, now))
        conn.commit()
        r = conn.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return _asset_row(r)
    return _wrap(op)


def get_asset(asset_id: str) -> dict[str, Any] | None:
    def op(conn):
        r = conn.execute("SELECT * FROM assets WHERE asset_id=?", (asset_id,)).fetchone()
        return _asset_row(r) if r else None
    return _wrap(op)


def list_assets() -> list[dict[str, Any]]:
    def op(conn):
        rows = conn.execute("SELECT * FROM assets ORDER BY created_at ASC").fetchall()
        return [_asset_row(r) for r in rows]
    return _wrap(op)


def set_asset_lifecycle(asset_id: str, lifecycle_state: str) -> None:
    _check(lifecycle_state, _dom.ASSET_LIFECYCLE, "asset lifecycle_state")

    def op(conn):
        conn.execute("UPDATE assets SET lifecycle_state=?, updated_at=? WHERE asset_id=?",
                     (lifecycle_state, _now(), asset_id))
        conn.commit()
    _wrap(op)


def _asset_row(r: sqlite3.Row) -> dict[str, Any]:
    return {
        "asset_id": r["asset_id"], "label": r["label"], "kind": r["kind"],
        "endpoint": r["endpoint"], "tags": _loads(r["tags"], []),
        "owner_scope": r["owner_scope"], "lifecycle_state": r["lifecycle_state"],
        "created_at": r["created_at"], "updated_at": r["updated_at"],
    }


# ── secret_refs (STATE only; value is in Credential Manager) ─────────────────

def put_secret_ref(*, secret_ref: str, kind: str, backend: str = "wincred",
                   asset_id: str | None = None, lifecycle: str = "provisioning",
                   origin: str = "secure_intake") -> None:
    _check(kind, _dom.SECRET_KINDS, "secret kind")
    _check(backend, _dom.SECRET_BACKENDS, "secret backend")   # Phase 0: wincred only
    _check(lifecycle, _dom.SECRET_LIFECYCLE, "secret lifecycle")
    _check(origin, _dom.SECRET_ORIGINS, "secret origin")

    def op(conn):
        conn.execute(
            "INSERT INTO secret_refs (secret_ref, backend, kind, asset_id, lifecycle, origin, created_at)"
            " VALUES (?,?,?,?,?,?,?) ON CONFLICT(secret_ref) DO UPDATE SET"
            " kind=excluded.kind, asset_id=excluded.asset_id, lifecycle=excluded.lifecycle",
            (secret_ref, backend, kind, asset_id, lifecycle, origin, _now()))
        conn.commit()
    _wrap(op)


def set_secret_lifecycle(secret_ref: str, lifecycle: str) -> None:
    """Transition a secret_ref's lifecycle (validated against the enum). Used by the
    vault provisioning saga and by revoke."""
    _check(lifecycle, _dom.SECRET_LIFECYCLE, "secret lifecycle")

    def op(conn):
        ts_col = ", revoked_at=?" if lifecycle == "revoked" else ""
        params = [lifecycle]
        if lifecycle == "revoked":
            params.append(_now())
        params.append(secret_ref)
        conn.execute(f"UPDATE secret_refs SET lifecycle=?{ts_col} WHERE secret_ref=?", params)
        conn.commit()
    _wrap(op)


def list_secret_refs_by_lifecycle(lifecycles: tuple[str, ...]) -> list[dict[str, Any]]:
    """secret_ref STATE rows in the given lifecycles — used by the vault recovery
    path to find incomplete (provisioning / cleanup_pending) records."""
    if not lifecycles:
        return []
    placeholders = ",".join("?" for _ in lifecycles)

    def op(conn):
        rows = conn.execute(
            f"SELECT * FROM secret_refs WHERE lifecycle IN ({placeholders})"
            f" ORDER BY created_at ASC", tuple(lifecycles)).fetchall()
        return [_secret_row(r) for r in rows]
    return _wrap(op)


def _secret_row(r: sqlite3.Row) -> dict[str, Any]:
    return {"secret_ref": r["secret_ref"], "backend": r["backend"], "kind": r["kind"],
            "asset_id": r["asset_id"], "lifecycle": r["lifecycle"], "origin": r["origin"],
            "recovery_attempts": r["recovery_attempts"], "last_recovery_at": r["last_recovery_at"],
            "created_at": r["created_at"], "rotated_at": r["rotated_at"],
            "revoked_at": r["revoked_at"]}


def secret_ref_state(secret_ref: str) -> dict[str, Any] | None:
    """STATE only — never a value (there is no value column)."""
    def op(conn):
        r = conn.execute("SELECT * FROM secret_refs WHERE secret_ref=?", (secret_ref,)).fetchone()
        return _secret_row(r) if r else None
    return _wrap(op)


def delete_secret_ref(secret_ref: str) -> None:
    """Remove the state row entirely — used by the vault's atomicity compensation
    (metadata write failed → nothing should remain)."""
    def op(conn):
        conn.execute("DELETE FROM secret_refs WHERE secret_ref=?", (secret_ref,))
        conn.commit()
    _wrap(op)


def mark_secret_revoked(secret_ref: str) -> None:
    def op(conn):
        conn.execute("UPDATE secret_refs SET lifecycle='revoked', revoked_at=? WHERE secret_ref=?",
                     (_now(), secret_ref))
        conn.commit()
    _wrap(op)


def finalize_provisioning(secret_ref: str, lifecycle: str) -> bool:
    """Atomic CAS: provisioning → *lifecycle*. Returns True ONLY if this call made
    the transition (rowcount==1). False = state conflict (e.g. a recovery pass
    already claimed the record) — the caller must NOT treat that as success."""
    _check(lifecycle, _dom.SECRET_LIFECYCLE, "secret lifecycle")

    def op(conn):
        cur = conn.execute(
            "UPDATE secret_refs SET lifecycle=? WHERE secret_ref=? AND lifecycle='provisioning'",
            (lifecycle, secret_ref))
        conn.commit()
        return cur.rowcount == 1
    return _wrap(op)


def claim_for_recovery(secret_ref: str) -> bool:
    """Atomic CAS claim: provisioning|cleanup_pending → recovering, incrementing
    recovery_attempts. Returns True ONLY if this call won the claim (rowcount==1).

    Guarded so auto-recovery is a COMPENSATING action of the secure-intake path
    only: the claim requires origin='secure_intake' AND recovery_attempts <
    MAX_RECOVERY_ATTEMPTS. A record already recovering / finalized / gone, one from
    another origin, or one that exhausted its attempts yields False."""
    def op(conn):
        cur = conn.execute(
            "UPDATE secret_refs SET lifecycle='recovering',"
            " recovery_attempts=recovery_attempts+1, last_recovery_at=?"
            " WHERE secret_ref=? AND lifecycle IN ('provisioning','cleanup_pending')"
            " AND origin='secure_intake' AND recovery_attempts < ?",
            (_now(), secret_ref, MAX_RECOVERY_ATTEMPTS))
        conn.commit()
        return cur.rowcount == 1
    return _wrap(op)


def mark_recovery_failed(secret_ref: str) -> bool:
    """Terminal CAS: an incomplete record that exhausted its auto-recovery attempts
    (>= MAX_RECOVERY_ATTEMPTS) goes to `recovery_failed` — visible, never
    auto-deleted/retried again (manual recovery only). Returns True on transition."""
    def op(conn):
        cur = conn.execute(
            "UPDATE secret_refs SET lifecycle='recovery_failed' WHERE secret_ref=?"
            " AND lifecycle IN ('provisioning','cleanup_pending')"
            " AND origin='secure_intake' AND recovery_attempts >= ?",
            (secret_ref, MAX_RECOVERY_ATTEMPTS))
        conn.commit()
        return cur.rowcount == 1
    return _wrap(op)


def delete_claimed_recovery(secret_ref: str) -> bool:
    """Delete a record ONLY if it is in `recovering` (i.e. claimed by THIS pass).
    Returns True on delete (rowcount==1); False = state conflict, not success."""
    def op(conn):
        cur = conn.execute(
            "DELETE FROM secret_refs WHERE secret_ref=? AND lifecycle='recovering'",
            (secret_ref,))
        conn.commit()
        return cur.rowcount == 1
    return _wrap(op)


# ── change_runs (TWO axes — never merged) ───────────────────────────────────

def create_change_run(*, change_run_id: str, run_id: str, asset_id: str,
                       plan: dict | None = None, rollback_kind: str = "none",
                       approval_id: str | None = None) -> None:
    _check(rollback_kind, _dom.ROLLBACK_KINDS, "rollback_kind")

    def op(conn):
        now = _now()
        conn.execute(
            "INSERT INTO change_runs (change_run_id, run_id, asset_id, plan, approval_id,"
            " snapshot_id, rollback_kind, change_run_status, completion_status, created_at, updated_at)"
            " VALUES (?,?,?,?,?,?,?,?,?,?,?)",
            (change_run_id, run_id, asset_id, json.dumps(plan or {}, ensure_ascii=False),
             approval_id, None, rollback_kind, "planned", None, now, now))
        conn.commit()
    _wrap(op)


def update_change_run(change_run_id: str, *, change_run_status: str | None = None,
                      completion_status: str | None = None, snapshot_id: str | None = None,
                      approval_id: str | None = None) -> None:
    """Update either/both axes INDEPENDENTLY. Each value is validated against its
    OWN axis before SQL — the two are never coerced into one another."""
    if change_run_status is not None:
        _check(change_run_status, _dom.CHANGE_RUN_STATUS, "change_run_status")
    if completion_status is not None:
        _check(completion_status, _dom.COMPLETION_STATUS, "completion_status")

    sets, vals = [], []
    if change_run_status is not None:
        sets.append("change_run_status=?"); vals.append(change_run_status)
    if completion_status is not None:
        sets.append("completion_status=?"); vals.append(completion_status)
    if snapshot_id is not None:
        sets.append("snapshot_id=?"); vals.append(snapshot_id)
    if approval_id is not None:
        sets.append("approval_id=?"); vals.append(approval_id)
    if not sets:
        return
    sets.append("updated_at=?"); vals.append(_now())
    vals.append(change_run_id)

    def op(conn):
        conn.execute(f"UPDATE change_runs SET {', '.join(sets)} WHERE change_run_id=?", vals)
        conn.commit()
    _wrap(op)


def get_change_run(change_run_id: str) -> dict[str, Any] | None:
    def op(conn):
        r = conn.execute("SELECT * FROM change_runs WHERE change_run_id=?", (change_run_id,)).fetchone()
        if not r:
            return None
        return {"change_run_id": r["change_run_id"], "run_id": r["run_id"],
                "asset_id": r["asset_id"], "plan": _loads(r["plan"], {}),
                "approval_id": r["approval_id"], "snapshot_id": r["snapshot_id"],
                "rollback_kind": r["rollback_kind"],
                "change_run_status": r["change_run_status"],
                "completion_status": r["completion_status"]}
    return _wrap(op)
