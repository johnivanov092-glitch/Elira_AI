"""IT Operations durable store — schema, forward-only migrations, and CRUD.

Mirrors the monitoring-store convention: `CREATE TABLE IF NOT EXISTS` + a chain of
named, idempotent, additive `migrate_*` functions, plus a forward-only
`PRAGMA user_version` ladder. Durable records — no drop-on-change.

Fail-soft: every public op is wrapped so a store outage degrades to a clear
`StoreUnavailable` rather than crashing the caller (mirrors web_corpus).
"""
from __future__ import annotations

import json
import sqlite3
import time
from pathlib import Path
from typing import Any

from app.core.data_files import data_file
from app.infrastructure.db.connection import connect_sqlite

# Forward-only ladder. Bump ONLY with a matching migrate step; never DROP.
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
    -- TWO SEPARATE AXES (never merged):
    change_run_status TEXT NOT NULL DEFAULT 'planned',
    completion_status TEXT,
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
-- secret_ref STATE + metadata ONLY. The secret VALUE lives exclusively in Windows
-- Credential Manager. No ciphertext, no DPAPI blob, no value is ever stored here.
CREATE TABLE IF NOT EXISTS secret_refs (
    secret_ref TEXT PRIMARY KEY,
    backend TEXT NOT NULL DEFAULT 'wincred',
    kind TEXT NOT NULL,
    asset_id TEXT,
    lifecycle TEXT NOT NULL DEFAULT 'temporary',
    created_at REAL NOT NULL,
    rotated_at REAL,
    revoked_at REAL
);
CREATE INDEX IF NOT EXISTS idx_profiles_asset ON connection_profiles(asset_id);
CREATE INDEX IF NOT EXISTS idx_change_runs_run ON change_runs(run_id);
CREATE INDEX IF NOT EXISTS idx_evidence_run ON evidence(run_id);
"""


class StoreUnavailable(RuntimeError):
    """The IT-Ops store could not be opened/queried — fail-soft signal."""


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
        # fail-soft: a bad path / unreadable DB degrades to StoreUnavailable rather
        # than crashing the caller (mirrors web_corpus store).
        raise StoreUnavailable(f"it_ops store unavailable: {exc}") from exc


def init_db() -> None:
    """Create tables if missing and run forward-only migrations. Resilient: safe to
    call at import and repeatedly (idempotent). Never drops."""
    conn = _connect()
    try:
        conn.executescript(_CREATE_SQL)
        _run_migrations(conn)
        conn.commit()
    finally:
        conn.close()


def _run_migrations(conn: sqlite3.Connection) -> None:
    """Forward-only ladder on PRAGMA user_version. Each step is additive and
    idempotent; a step N is applied only when user_version < N, and the version is
    stamped forward afterward. NEVER drop-on-change (durable records)."""
    ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
    # (No column migrations yet at v1 — the ladder is here so future additive
    #  steps land as `if ver < N: <add-column-if-missing>; ver = N`.)
    if ver < _SCHEMA_VERSION:
        conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")


def _wrap(op):
    conn = _connect()
    try:
        return op(conn)
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
        return get_asset(asset_id)
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
                   asset_id: str | None = None, lifecycle: str = "temporary") -> None:
    def op(conn):
        conn.execute(
            "INSERT INTO secret_refs (secret_ref, backend, kind, asset_id, lifecycle, created_at)"
            " VALUES (?,?,?,?,?,?) ON CONFLICT(secret_ref) DO UPDATE SET"
            " kind=excluded.kind, asset_id=excluded.asset_id, lifecycle=excluded.lifecycle",
            (secret_ref, backend, kind, asset_id, lifecycle, _now()))
        conn.commit()
    _wrap(op)


def secret_ref_state(secret_ref: str) -> dict[str, Any] | None:
    """STATE only — never a value (there is no value column)."""
    def op(conn):
        r = conn.execute("SELECT * FROM secret_refs WHERE secret_ref=?", (secret_ref,)).fetchone()
        if not r:
            return None
        return {"secret_ref": r["secret_ref"], "backend": r["backend"], "kind": r["kind"],
                "asset_id": r["asset_id"], "lifecycle": r["lifecycle"],
                "created_at": r["created_at"], "rotated_at": r["rotated_at"],
                "revoked_at": r["revoked_at"]}
    return _wrap(op)


def mark_secret_revoked(secret_ref: str) -> None:
    def op(conn):
        conn.execute("UPDATE secret_refs SET lifecycle='revoked', revoked_at=? WHERE secret_ref=?",
                     (_now(), secret_ref))
        conn.commit()
    _wrap(op)


# ── change_runs (TWO axes — never merged) ───────────────────────────────────

def create_change_run(*, change_run_id: str, run_id: str, asset_id: str,
                       plan: dict | None = None, rollback_kind: str = "none",
                       approval_id: str | None = None) -> None:
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
    """Update either/both axes INDEPENDENTLY. The two status columns are never
    coerced into one another."""
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
