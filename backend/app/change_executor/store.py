"""Executor-private change store (v2).

A SEPARATE SQLite database owned exclusively by the `elira-change-exec` principal —
the main Elira user gets no ACL on it. It holds the ChangeRun state machine, the
hashed one-time approval capabilities, and typed change evidence. Nothing in the main
it_ops store is trusted or referenced.

Migration is explicit and fail-closed. v2 adds no columns; it expands the DB-enforced
active-target predicate so `rollback_failed` remains locking until a typed resolution.
v1 is verified exactly before that index-only migration; v2 is verify-only.

Concurrency-critical transitions are CAS (compare-and-swap on `change_run_status`):
- capability consume + `pending_approval → applying` happen in ONE transaction, so a
  replayed/expired/mismatched/wrong-approver token is a no-op (state unchanged, token
  not consumed);
- `applying`-exits are additionally guarded by `attempt_token`, so a late worker whose
  run was already swept to `apply_unknown` can never overwrite the terminal state;
- the sweep only fires past `apply_deadline_at` and only `applying → apply_unknown`.
"""
from __future__ import annotations

import json
import os
import secrets
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any

from ._frozen import connect_sqlite

_SCHEMA_VERSION = 2

# States in which a target is considered ACTIVE — the partial unique index makes at most
# ONE change_run per target_id occupy this set (a second plan → 409). apply_unknown is a
# LOCKING state: it stays active until a manual, freshly-inspected resolution.
_V1_ACTIVE_STATES = ("pending_approval", "applying", "apply_unknown")
_ACTIVE_STATES = (*_V1_ACTIVE_STATES, "rollback_failed")
_INDEX_NAME = "uq_change_active_target"
# One source of truth for the index predicate → both the CREATE and the exact
# verification are built from it, so a tampered/weakened predicate (e.g. an extra
# `AND 0` that makes the index match no rows and stops enforcing uniqueness) can never
# pass structural verification.
_INDEX_PREDICATE = "change_run_status IN (" + ", ".join(f"'{s}'" for s in _ACTIVE_STATES) + ")"
_EXPECTED_INDEX_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX_NAME} ON change_runs(target_id) WHERE {_INDEX_PREDICATE}"
)
_V1_INDEX_PREDICATE = (
    "change_run_status IN (" + ", ".join(f"'{s}'" for s in _V1_ACTIVE_STATES) + ")"
)
_V1_EXPECTED_INDEX_DDL = (
    f"CREATE UNIQUE INDEX {_INDEX_NAME} ON change_runs(target_id) WHERE {_V1_INDEX_PREDICATE}"
)

_CREATE_SQL = f"""
CREATE TABLE IF NOT EXISTS change_runs (
    change_run_id TEXT PRIMARY KEY,
    target_id TEXT NOT NULL,
    unit TEXT NOT NULL,
    operation TEXT NOT NULL,
    change_run_status TEXT NOT NULL DEFAULT 'pending_approval',
    snapshot TEXT NOT NULL DEFAULT '{{}}',
    snapshot_main_pid INTEGER,
    snapshot_hash TEXT NOT NULL DEFAULT '',
    planned_argv_hash TEXT NOT NULL DEFAULT '',
    planned_binding TEXT NOT NULL DEFAULT '{{}}',
    verdict TEXT,
    approver TEXT NOT NULL DEFAULT '',
    approved_at REAL,
    apply_started_at REAL,
    apply_deadline_at REAL,
    attempt_token TEXT NOT NULL DEFAULT '',
    created_at REAL NOT NULL,
    updated_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS change_approvals (
    capability_hash TEXT PRIMARY KEY,
    change_run_id TEXT NOT NULL,
    action TEXT NOT NULL,
    argv_hash TEXT NOT NULL,
    snapshot_hash TEXT NOT NULL,
    expires_at REAL NOT NULL,
    used_at REAL,
    created_at REAL NOT NULL
);
CREATE TABLE IF NOT EXISTS change_evidence (
    evidence_id TEXT PRIMARY KEY,
    change_run_id TEXT NOT NULL,
    target_id TEXT NOT NULL,
    operation TEXT NOT NULL,
    result TEXT NOT NULL DEFAULT '{{}}',
    exit_status TEXT NOT NULL DEFAULT '',
    captured_at REAL NOT NULL
);
CREATE UNIQUE INDEX IF NOT EXISTS {_INDEX_NAME}
    ON change_runs(target_id)
    WHERE {_INDEX_PREDICATE};
"""

_EXPECTED_COLUMNS: dict[str, set[str]] = {
    "change_runs": {
        "change_run_id", "target_id", "unit", "operation", "change_run_status", "snapshot",
        "snapshot_main_pid", "snapshot_hash", "planned_argv_hash", "planned_binding", "verdict",
        "approver", "approved_at", "apply_started_at", "apply_deadline_at", "attempt_token",
        "created_at", "updated_at",
    },
    "change_approvals": {
        "capability_hash", "change_run_id", "action", "argv_hash", "snapshot_hash",
        "expires_at", "used_at", "created_at",
    },
    "change_evidence": {
        "evidence_id", "change_run_id", "target_id", "operation", "result",
        "exit_status", "captured_at",
    },
}
_EXPECTED_PK: dict[str, str] = {
    "change_runs": "change_run_id", "change_approvals": "capability_hash",
    "change_evidence": "evidence_id",
}
_REQUIRED_NOTNULL: dict[str, set[str]] = {
    "change_runs": {"target_id", "unit", "operation", "change_run_status", "snapshot",
                    "snapshot_hash", "planned_argv_hash", "planned_binding", "approver",
                    "attempt_token", "created_at", "updated_at"},
    "change_approvals": {"change_run_id", "action", "argv_hash", "snapshot_hash",
                         "expires_at", "created_at"},
    "change_evidence": {"change_run_id", "target_id", "operation", "result",
                        "exit_status", "captured_at"},
}

# Test override; real deployments point ELIRA_CHANGE_STORE_PATH at an executor-owned
# location the main user cannot read/write.
_DB_PATH_OVERRIDE: str | None = None


class ChangeStoreUnavailable(RuntimeError):
    """The change store could not be opened/queried/migrated — fail-closed signal."""


class ActiveTargetConflict(RuntimeError):
    """A plan was attempted while an active (pending/applying/unknown) change_run already
    exists for the target — the DB partial-unique index rejected it (→ 409)."""


def _now() -> float:
    return time.time()


def _db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    env = os.environ.get("ELIRA_CHANGE_STORE_PATH", "").strip()
    if not env:
        raise ChangeStoreUnavailable("ELIRA_CHANGE_STORE_PATH is not set (executor-owned path required)")
    return env


def _connect() -> sqlite3.Connection:
    try:
        conn = connect_sqlite(_db_path(), row_factory=sqlite3.Row)
        return conn
    except (sqlite3.Error, OSError, ValueError) as exc:
        raise ChangeStoreUnavailable(f"change store unavailable: {exc}") from exc


def _table_info(conn: sqlite3.Connection, table: str) -> list[tuple]:
    return conn.execute(f"PRAGMA table_info({table})").fetchall()


def _table_exists(conn: sqlite3.Connection, table: str) -> bool:
    return conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone() is not None


def _verify_contract(conn: sqlite3.Connection, *, index_ddl: str = _EXPECTED_INDEX_DDL) -> None:
    """Exact structural contract: every table with its full column set + correct PK +
    all required NOT NULLs, AND the partial unique index (unique, on target_id, with the
    exact active-state predicate). Any mismatch raises → caller → ChangeStoreUnavailable."""
    for table, cols in _EXPECTED_COLUMNS.items():
        info = _table_info(conn, table)                 # (cid, name, type, notnull, dflt, pk)
        if not info:
            raise sqlite3.OperationalError(f"expected table {table!r} missing")
        names = {r[1] for r in info}
        notnull_cols = {r[1] for r in info if r[3] and int(r[3]) > 0}
        pk_cols = {r[1] for r in info if r[5] and int(r[5]) > 0}
        missing = set(cols) - names
        if missing:
            raise sqlite3.OperationalError(f"{table}: missing columns {sorted(missing)}")
        if pk_cols != {_EXPECTED_PK[table]}:
            raise sqlite3.OperationalError(
                f"{table}: primary key is {sorted(pk_cols) or 'none'}, expected [{_EXPECTED_PK[table]!r}]")
        for col in _REQUIRED_NOTNULL.get(table, set()):
            if col not in notnull_cols and col != _EXPECTED_PK[table]:
                raise sqlite3.OperationalError(f"{table}: column {col!r} must be NOT NULL")
    _verify_active_index(conn, expected_ddl=index_ddl)


def _normalize_ddl(sql: str) -> str:
    """Canonicalize a CREATE statement for exact comparison: lowercase, collapse
    whitespace, drop the optional `IF NOT EXISTS` (SQLite may or may not store it)."""
    return " ".join(str(sql or "").split()).lower().replace("if not exists ", "").strip()


def _verify_active_index(conn: sqlite3.Connection, *, expected_ddl: str = _EXPECTED_INDEX_DDL) -> None:
    """The one-active-per-target invariant is DB-enforced, so the index is part of the
    structural contract. Verify the EXACT normalized DDL against the single-source-of-truth
    definition (not a substring/predicate scan) — a weakened predicate that still contains
    the state literals (e.g. `... IN (...) AND 0`) would pass a substring check yet stop
    enforcing uniqueness, so nothing short of an exact match is safe. Also confirm the
    UNIQUE flag defensively."""
    row = conn.execute(
        "SELECT sql FROM sqlite_master WHERE type='index' AND name=?", (_INDEX_NAME,)).fetchone()
    if not row or not row[0]:
        raise sqlite3.OperationalError(f"partial unique index {_INDEX_NAME!r} missing")
    meta = next((r for r in conn.execute("PRAGMA index_list(change_runs)").fetchall()
                 if r[1] == _INDEX_NAME), None)
    if meta is None or int(meta[2]) != 1:               # r[2] == unique flag
        raise sqlite3.OperationalError(f"index {_INDEX_NAME!r} is not UNIQUE")
    if _normalize_ddl(row[0]) != _normalize_ddl(expected_ddl):
        raise sqlite3.OperationalError(
            f"index {_INDEX_NAME!r} DDL does not match the contract: {row[0]!r}")


def init_db() -> None:
    """Fail-closed init: fresh v0 → canonical v2; partial v0 → refuse; exact v1 →
    transactional index-only migration to v2; v2 → verify only; future → refuse."""
    conn = _connect()
    try:
        ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if ver > _SCHEMA_VERSION:
            raise ChangeStoreUnavailable(
                f"change store user_version={ver} newer than supported {_SCHEMA_VERSION} (fail-closed)")
        if ver == 0:
            present = [t for t in _EXPECTED_PK if _table_exists(conn, t)]
            if present:
                raise ChangeStoreUnavailable(
                    f"change store user_version=0 but tables exist {sorted(present)} — "
                    f"refusing a partial/unknown schema (fail-closed)")
            conn.executescript(_CREATE_SQL)
            _verify_contract(conn)
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        elif ver == _SCHEMA_VERSION:
            _verify_contract(conn)                      # verify only, change nothing
        elif ver == 1:
            _verify_contract(conn, index_ddl=_V1_EXPECTED_INDEX_DDL)
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(f"DROP INDEX {_INDEX_NAME}")
            conn.execute(_EXPECTED_INDEX_DDL)
            _verify_contract(conn)
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        else:
            raise ChangeStoreUnavailable(f"change store user_version={ver} has no known migration")
        conn.commit()
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise ChangeStoreUnavailable(f"change store init failed: {exc}") from exc
    finally:
        conn.close()


def _row(r: sqlite3.Row | None) -> dict[str, Any] | None:
    return dict(r) if r is not None else None


def new_change_run_id() -> str:
    return f"chg-{uuid.uuid4().hex}"


def new_attempt_token() -> str:
    return secrets.token_hex(16)


# ── plan / capability / CAS ──────────────────────────────────────────────────

def create_plan_with_capabilities(*, change_run_id: str, target_id: str, unit: str,
                                  operation: str, snapshot: str, snapshot_main_pid: int | None,
                                  snapshot_hash: str, planned_argv_hash: str, planned_binding: str,
                                  approve_hash: str, reject_hash: str,
                                  capability_expires_at: float) -> dict[str, Any]:
    """Create the `pending_approval` ChangeRun AND its two hashed one-time capabilities
    (approve + reject) in ONE transaction — a plan never exists without its capabilities
    and vice versa. Both capabilities are bound to this plan's argv/snapshot hashes. The
    partial unique index enforces one active run per target_id: a conflict rolls the whole
    transaction back and raises ActiveTargetConflict (→ 409). The raw capability tokens live
    only in Telegram callback_data — never persisted, never logged."""
    conn = _connect()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """INSERT INTO change_runs
               (change_run_id, target_id, unit, operation, change_run_status, snapshot,
                snapshot_main_pid, snapshot_hash, planned_argv_hash, planned_binding,
                created_at, updated_at)
               VALUES (?, ?, ?, ?, 'pending_approval', ?, ?, ?, ?, ?, ?, ?)""",
            (change_run_id, target_id, unit, operation, snapshot, snapshot_main_pid,
             snapshot_hash, planned_argv_hash, planned_binding, now, now))
        for cap_hash, action in ((approve_hash, "approve"), (reject_hash, "reject")):
            conn.execute(
                """INSERT INTO change_approvals
                   (capability_hash, change_run_id, action, argv_hash, snapshot_hash,
                    expires_at, used_at, created_at)
                   VALUES (?, ?, ?, ?, ?, ?, NULL, ?)""",
                (cap_hash, change_run_id, action, planned_argv_hash, snapshot_hash,
                 capability_expires_at, now))
        conn.commit()
        return _row(conn.execute("SELECT * FROM change_runs WHERE change_run_id=?",
                                 (change_run_id,)).fetchone())  # type: ignore[return-value]
    except sqlite3.IntegrityError as exc:
        conn.rollback()
        raise ActiveTargetConflict(f"an active change already exists for target {target_id!r}") from exc
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"create_plan_with_capabilities failed: {exc}") from exc
    finally:
        conn.close()


def consume_capability(*, capability_hash: str, approver: str, apply_deadline_seconds: float,
                       now: float | None = None) -> dict[str, Any] | None:
    """ONE transaction: verify the capability (present ∧ unused ∧ unexpired ∧ its
    change_run still `pending_approval` ∧ argv/snapshot still bound) and, for an `approve`
    capability, CAS `pending_approval → applying` (stamping approver/apply_started_at/
    apply_deadline_at/attempt_token) OR, for `reject`, CAS `→ rejected`; then mark the
    capability used. Returns the updated change_run on success, or None (a full no-op:
    state unchanged, capability NOT consumed) on any failure — replay, expiry, mismatch,
    or a lost CAS race."""
    now = _now() if now is None else now
    conn = _connect()
    try:
        conn.execute("BEGIN IMMEDIATE")
        cap = conn.execute("SELECT * FROM change_approvals WHERE capability_hash=?",
                           (capability_hash,)).fetchone()
        if cap is None or cap["used_at"] is not None or float(cap["expires_at"]) < now:
            conn.rollback()
            return None
        cr = conn.execute("SELECT * FROM change_runs WHERE change_run_id=?",
                          (cap["change_run_id"],)).fetchone()
        if (cr is None or cr["change_run_status"] != "pending_approval"
                or cap["argv_hash"] != cr["planned_argv_hash"]
                or cap["snapshot_hash"] != cr["snapshot_hash"]):
            conn.rollback()
            return None
        if cap["action"] == "approve":
            token = new_attempt_token()
            n = conn.execute(
                """UPDATE change_runs SET change_run_status='applying', approver=?, approved_at=?,
                   apply_started_at=?, apply_deadline_at=?, attempt_token=?, updated_at=?
                   WHERE change_run_id=? AND change_run_status='pending_approval'""",
                (approver, now, now, now + float(apply_deadline_seconds), token, now,
                 cr["change_run_id"])).rowcount
        else:
            n = conn.execute(
                """UPDATE change_runs SET change_run_status='rejected', approver=?, approved_at=?,
                   updated_at=? WHERE change_run_id=? AND change_run_status='pending_approval'""",
                (approver, now, now, cr["change_run_id"])).rowcount
        if n != 1:
            conn.rollback()
            return None
        conn.execute("UPDATE change_approvals SET used_at=? WHERE capability_hash=?",
                     (now, capability_hash))
        conn.commit()
        return _row(conn.execute("SELECT * FROM change_runs WHERE change_run_id=?",
                                 (cr["change_run_id"],)).fetchone())
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise ChangeStoreUnavailable(f"consume_capability failed: {exc}") from exc
    finally:
        conn.close()


def mark_delivery_failed(*, change_run_id: str) -> bool:
    """CAS `pending_approval → delivery_failed` AND invalidate (mark used) all of the run's
    capabilities, in ONE transaction. Called when the Telegram approval message could not
    be delivered — so a single send failure can never leave the target locked forever in
    pending_approval (the target frees immediately). Returns True iff it won the CAS."""
    conn = _connect()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        n = conn.execute(
            """UPDATE change_runs SET change_run_status='delivery_failed',
               verdict='approval message could not be delivered', updated_at=?
               WHERE change_run_id=? AND change_run_status='pending_approval'""",
            (now, change_run_id)).rowcount
        if n == 1:
            conn.execute("UPDATE change_approvals SET used_at=? "
                         "WHERE change_run_id=? AND used_at IS NULL", (now, change_run_id))
        conn.commit()
        return n == 1
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"mark_delivery_failed failed: {exc}") from exc
    finally:
        conn.close()


def finalize_apply(*, change_run_id: str, attempt_token: str, status: str,
                   verdict: str = "") -> bool:
    """Token-guarded terminal CAS from `applying`. Returns True iff this attempt won
    (state was still `applying` AND attempt_token matches). A late worker whose run was
    swept to `apply_unknown` matches 0 rows → cannot overwrite it."""
    if status not in ("applied", "rolled_back", "rollback_failed", "command_failed",
                      "postcheck_failed", "aborted_before_apply"):
        raise ValueError(f"invalid terminal status {status!r}")
    conn = _connect()
    try:
        n = conn.execute(
            """UPDATE change_runs SET change_run_status=?, verdict=?, updated_at=?
               WHERE change_run_id=? AND change_run_status='applying' AND attempt_token=?""",
            (status, verdict, _now(), change_run_id, attempt_token)).rowcount
        conn.commit()
        return n == 1
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"finalize_apply failed: {exc}") from exc
    finally:
        conn.close()


def mark_apply_unknown(*, change_run_id: str, attempt_token: str, verdict: str) -> bool:
    """Token-guarded CAS `applying → apply_unknown` — used by the executor when it CAUGHT
    an ambiguous apply (e.g. an SSH timeout mid-restart) and the outcome is genuinely
    unknown. Distinct from the deadline sweep (the backstop for a CRASHED executor that
    couldn't report). Returns True iff this attempt won."""
    conn = _connect()
    try:
        n = conn.execute(
            """UPDATE change_runs SET change_run_status='apply_unknown', verdict=?, updated_at=?
               WHERE change_run_id=? AND change_run_status='applying' AND attempt_token=?""",
            (verdict, _now(), change_run_id, attempt_token)).rowcount
        conn.commit()
        return n == 1
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"mark_apply_unknown failed: {exc}") from exc
    finally:
        conn.close()


def sweep_stale_applying(now: float | None = None) -> list[str]:
    """CAS `applying → apply_unknown` for runs past their apply_deadline_at (only). A
    subsequent late finalize (token-guarded on state='applying') can no longer win."""
    now = _now() if now is None else now
    conn = _connect()
    try:
        candidates = [r[0] for r in conn.execute(
            "SELECT change_run_id FROM change_runs WHERE change_run_status='applying' "
            "AND apply_deadline_at IS NOT NULL AND apply_deadline_at < ?", (now,)).fetchall()]
        won: list[str] = []
        for cid in candidates:
            n = conn.execute(
                """UPDATE change_runs SET change_run_status='apply_unknown',
                   verdict='swept: apply deadline exceeded, outcome unknown', updated_at=?
                   WHERE change_run_id=? AND change_run_status='applying'""", (now, cid)).rowcount
            if n == 1:                      # only report the runs THIS sweep actually transitioned
                won.append(cid)
        conn.commit()
        return won
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"sweep failed: {exc}") from exc
    finally:
        conn.close()


# A resolution inspect is the executor's fresh `systemctl show` projection. The store
# owns the evidence contract for it — the caller passes only STRUCTURED fields, never a
# free operation/result/exit string.
_RESOLUTION_OP = "systemd_change:resolution_inspect"
_SYSTEMD_FIELD_KEYS = ("id", "load_state", "active_state", "sub_state", "unit_file_state",
                       "main_pid", "exec_main_status", "n_restarts", "fragment_path")
_REQUIRED_INSPECT_FIELDS = ("id", "load_state", "active_state", "sub_state", "main_pid")


def _validate_resolution_inspect(fields: Any, exit_status: Any) -> dict[str, Any] | None:
    """Validate a fresh resolution inspect and project it to the known systemd fields, or
    return None if it is not a definite, successful inspect. Rules: exit_status must be
    exactly "0"; `fields` must be a dict with non-empty string id/load_state/active_state/
    sub_state and an integer main_pid (not a bool). Only known keys are kept."""
    if str(exit_status) != "0" or not isinstance(fields, dict):
        return None
    for k in ("id", "load_state", "active_state", "sub_state"):
        v = fields.get(k)
        if not isinstance(v, str) or not v.strip():
            return None
    mp = fields.get("main_pid")
    if not isinstance(mp, int) or isinstance(mp, bool):
        return None
    projected: dict[str, Any] = {}
    for k in _SYSTEMD_FIELD_KEYS:
        v = fields.get(k)
        if k in _REQUIRED_INSPECT_FIELDS or v is not None:
            projected[k] = v
    return projected


def resolve_after_inspect(*, change_run_id: str, resolver: str, inspect_fields: dict[str, Any],
                          inspect_exit_status: str) -> bool:
    """Resolve a LOCKED `apply_unknown` ONLY together with a fresh, VALID server-side
    inspect. The store owns the evidence contract: it fixes `operation`, requires
    `exit_status == "0"`, validates the required structured fields (id/load_state/
    active_state/sub_state/main_pid), and stamps `captured_at` — the caller cannot pass a
    free operation/result/exit string. In ONE transaction it writes the projected typed
    evidence AND CASes `apply_unknown → resolved_unknown`. If the inspect is
    invalid/non-zero, or the CAS does not fire (state is not apply_unknown), nothing is
    written and the target stays LOCKED."""
    projected = _validate_resolution_inspect(inspect_fields, inspect_exit_status)
    if projected is None:
        return False                    # invalid / non-zero inspect → no DB touch, still locked
    conn = _connect()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        # Read target_id + unit ONLY for a change_run still in apply_unknown, then require the
        # inspect to be OF that exact unit (id == unit) — a valid inspect of a DIFFERENT service
        # can never resolve this lock. All in one transaction with the evidence + CAS.
        row = conn.execute(
            "SELECT target_id, unit FROM change_runs "
            "WHERE change_run_id=? AND change_run_status='apply_unknown'",
            (change_run_id,)).fetchone()
        if row is None:                 # not apply_unknown → still locked, nothing written
            conn.rollback()
            return False
        target_id, unit = row[0], row[1]
        if projected.get("id") != unit:
            conn.rollback()
            return False
        conn.execute(
            """INSERT INTO change_evidence
               (evidence_id, change_run_id, target_id, operation, result, exit_status, captured_at)
               VALUES (?, ?, ?, ?, ?, '0', ?)""",
            (f"cev-{uuid.uuid4().hex}", change_run_id, target_id, _RESOLUTION_OP,
             json.dumps(projected, ensure_ascii=False), now))
        n = conn.execute(
            """UPDATE change_runs SET change_run_status='resolved_unknown', approver=?, updated_at=?
               WHERE change_run_id=? AND change_run_status='apply_unknown'""",
            (resolver, now, change_run_id)).rowcount
        if n != 1:                      # defensive: the row is locked in this tx, so this holds
            conn.rollback()
            return False
        conn.commit()
        return True
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise ChangeStoreUnavailable(f"resolve_after_inspect failed: {exc}") from exc
    finally:
        conn.close()


_DATABASE_RESOLUTION_OP = "database_change:resolution_inspect"
_DATABASE_HASH_KEYS = ("schema_sha256", "data_sha256")


def _validate_database_resolution(fields: Any, exit_status: Any) -> dict[str, Any] | None:
    if str(exit_status) != "0" or not isinstance(fields, dict):
        return None
    if (fields.get("kind") != "sqlite_migration"
            or fields.get("database_id") != "phase6-canary"
            or fields.get("migration_id") != "canary_add_verified_at_v2"
            or fields.get("state") not in ("before", "after")
            or fields.get("quick_check") != "ok"
            or fields.get("journal_mode") != "delete"):
        return None
    version = fields.get("user_version")
    row_count = fields.get("row_count")
    if (not isinstance(version, int) or isinstance(version, bool)
            or not isinstance(row_count, int) or isinstance(row_count, bool)
            or row_count < 0 or row_count > 1_000):
        return None
    for key in _DATABASE_HASH_KEYS:
        value = fields.get(key)
        if (not isinstance(value, str) or len(value) != 64
                or any(c not in "0123456789abcdef" for c in value)):
            return None
    if (fields["state"], version) not in (("before", 1), ("after", 2)):
        return None
    return {
        "kind": "sqlite_migration",
        "database_id": "phase6-canary",
        "migration_id": "canary_add_verified_at_v2",
        "state": fields["state"],
        "quick_check": "ok",
        "journal_mode": "delete",
        "user_version": version,
        "schema_sha256": fields["schema_sha256"],
        "row_count": row_count,
        "data_sha256": fields["data_sha256"],
    }


def resolve_database_after_inspect(*, change_run_id: str, resolver: str,
                                   inspect_fields: dict[str, Any],
                                   inspect_exit_status: str) -> bool:
    """Resolve a locking database run only from a fresh typed inspect of the bound canary.

    The store owns the operation label and compares the inspect against its own immutable
    plan snapshot in the same transaction as evidence insertion and the status CAS.
    """
    projected = _validate_database_resolution(inspect_fields, inspect_exit_status)
    if projected is None:
        return False
    conn = _connect()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT target_id, unit, snapshot FROM change_runs WHERE change_run_id=? "
            "AND change_run_status IN ('apply_unknown','rollback_failed')",
            (change_run_id,),
        ).fetchone()
        if row is None or row["unit"] != "phase6-canary":
            conn.rollback()
            return False
        try:
            snapshot = json.loads(row["snapshot"])
        except (ValueError, TypeError):
            conn.rollback()
            return False
        same_target = (
            snapshot.get("kind") == "sqlite_migration"
            and snapshot.get("database_id") == projected["database_id"]
            and snapshot.get("migration_id") == projected["migration_id"]
            and snapshot.get("row_count") == projected["row_count"]
            and snapshot.get("data_sha256") == projected["data_sha256"]
        )
        if not same_target:
            conn.rollback()
            return False
        if projected["state"] == "after":
            final_status = "resolved_applied"
        elif (projected["schema_sha256"] == snapshot.get("schema_sha256")
              and projected["user_version"] == snapshot.get("user_version")):
            final_status = "resolved_rolled_back"
        else:
            conn.rollback()
            return False
        conn.execute(
            """INSERT INTO change_evidence
               (evidence_id, change_run_id, target_id, operation, result, exit_status, captured_at)
               VALUES (?, ?, ?, ?, ?, '0', ?)""",
            (f"cev-{uuid.uuid4().hex}", change_run_id, row["target_id"],
             _DATABASE_RESOLUTION_OP, json.dumps(projected, ensure_ascii=False), now),
        )
        changed = conn.execute(
            """UPDATE change_runs SET change_run_status=?, approver=?, updated_at=?
               WHERE change_run_id=?
                 AND change_run_status IN ('apply_unknown','rollback_failed')""",
            (final_status, resolver, now, change_run_id),
        ).rowcount
        if changed != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise ChangeStoreUnavailable(f"resolve_database_after_inspect failed: {exc}") from exc
    finally:
        conn.close()


_CONFIG_RESOLUTION_OP = "config_change:resolution_inspect"


def _validate_config_resolution(fields: Any, exit_status: Any) -> dict[str, Any] | None:
    if str(exit_status) != "0" or not isinstance(fields, dict):
        return None
    if fields.get("kind") != "netdata_config" or fields.get("config_id") != "netdata-main":
        return None
    before = fields.get("before_sha256")
    planned = fields.get("planned_sha256")
    service = fields.get("service")
    if (not isinstance(before, str) or len(before) != 64
            or not isinstance(planned, str) or len(planned) != 64
            or not isinstance(service, dict)):
        return None
    if any(c not in "0123456789abcdef" for c in before + planned):
        return None
    for key in ("id", "load_state", "active_state", "sub_state"):
        if not isinstance(service.get(key), str) or not service[key].strip():
            return None
    pid = service.get("main_pid")
    if not isinstance(pid, int) or isinstance(pid, bool):
        return None
    return {"kind": "netdata_config", "config_id": "netdata-main",
            "before_sha256": before, "planned_sha256": planned,
            "service": {k: service[k] for k in
                        ("id", "load_state", "active_state", "sub_state", "main_pid")}}


def resolve_config_after_inspect(*, change_run_id: str, resolver: str,
                                 inspect_fields: dict[str, Any],
                                 inspect_exit_status: str) -> bool:
    """Resolve a locking config `apply_unknown`/`rollback_failed` only from a fresh,
    typed inspect of the bound config and unit. Current planned hash + healthy service →
    `resolved_applied`; original hash + healthy service → `resolved_rolled_back`.
    Anything else remains locked."""
    projected = _validate_config_resolution(inspect_fields, inspect_exit_status)
    if projected is None:
        return False
    conn = _connect()
    try:
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            "SELECT target_id, unit, snapshot, snapshot_main_pid FROM change_runs WHERE change_run_id=? "
            "AND change_run_status IN ('apply_unknown','rollback_failed')",
            (change_run_id,)).fetchone()
        if row is None:
            conn.rollback()
            return False
        try:
            snapshot = json.loads(row["snapshot"])
        except (ValueError, TypeError):
            conn.rollback()
            return False
        service = projected["service"]
        if (snapshot.get("kind") != "netdata_config"
                or snapshot.get("config_id") != projected["config_id"]
                or service.get("id") != row["unit"]
                or service.get("load_state") != "loaded"
                or service.get("active_state") != "active"
                or service.get("sub_state") != "running"
                or int(service.get("main_pid") or 0) <= 0):
            conn.rollback()
            return False
        current_sha = projected["before_sha256"]
        if current_sha == snapshot.get("planned_sha256"):
            if int(service.get("main_pid") or 0) == int(row["snapshot_main_pid"] or -1):
                conn.rollback()
                return False
            final_status = "resolved_applied"
        elif current_sha == snapshot.get("before_sha256"):
            final_status = "resolved_rolled_back"
        else:
            conn.rollback()
            return False
        conn.execute(
            """INSERT INTO change_evidence
               (evidence_id, change_run_id, target_id, operation, result, exit_status, captured_at)
               VALUES (?, ?, ?, ?, ?, '0', ?)""",
            (f"cev-{uuid.uuid4().hex}", change_run_id, row["target_id"], _CONFIG_RESOLUTION_OP,
             json.dumps(projected, ensure_ascii=False), now))
        n = conn.execute(
            """UPDATE change_runs SET change_run_status=?, approver=?, updated_at=?
               WHERE change_run_id=? AND change_run_status IN ('apply_unknown','rollback_failed')""",
            (final_status, resolver, now, change_run_id)).rowcount
        if n != 1:
            conn.rollback()
            return False
        conn.commit()
        return True
    except sqlite3.Error as exc:
        try:
            conn.rollback()
        except sqlite3.Error:
            pass
        raise ChangeStoreUnavailable(f"resolve_config_after_inspect failed: {exc}") from exc
    finally:
        conn.close()


def expire_pending(now: float | None = None) -> list[str]:
    """CAS `pending_approval → expired` for runs whose newest approval capability has
    expired (a stale plan can't be approved against drifted state)."""
    now = _now() if now is None else now
    conn = _connect()
    try:
        candidates = [r[0] for r in conn.execute(
            """SELECT cr.change_run_id FROM change_runs cr
               WHERE cr.change_run_status='pending_approval'
                 AND NOT EXISTS (SELECT 1 FROM change_approvals ca
                                 WHERE ca.change_run_id=cr.change_run_id
                                   AND ca.used_at IS NULL AND ca.expires_at >= ?)""",
            (now,)).fetchall()]
        won: list[str] = []
        for cid in candidates:
            n = conn.execute(
                """UPDATE change_runs SET change_run_status='expired', updated_at=?
                   WHERE change_run_id=? AND change_run_status='pending_approval'""",
                (now, cid)).rowcount
            if n == 1:                      # only CAS winners
                won.append(cid)
        conn.commit()
        return won
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"expire_pending failed: {exc}") from exc
    finally:
        conn.close()


def record_change_evidence(*, change_run_id: str, target_id: str, operation: str,
                           result: str, exit_status: str = "") -> None:
    conn = _connect()
    try:
        conn.execute(
            """INSERT INTO change_evidence
               (evidence_id, change_run_id, target_id, operation, result, exit_status, captured_at)
               VALUES (?, ?, ?, ?, ?, ?, ?)""",
            (f"cev-{uuid.uuid4().hex}", change_run_id, target_id, operation, result,
             exit_status, _now()))
        conn.commit()
    except sqlite3.Error as exc:
        conn.rollback()
        raise ChangeStoreUnavailable(f"record_change_evidence failed: {exc}") from exc
    finally:
        conn.close()


def get_change_run(change_run_id: str) -> dict[str, Any] | None:
    conn = _connect()
    try:
        return _row(conn.execute("SELECT * FROM change_runs WHERE change_run_id=?",
                                 (change_run_id,)).fetchone())
    except sqlite3.Error as exc:
        raise ChangeStoreUnavailable(f"get_change_run failed: {exc}") from exc
    finally:
        conn.close()


def list_change_evidence(change_run_id: str) -> list[dict[str, Any]]:
    conn = _connect()
    try:
        return [dict(r) for r in conn.execute(
            "SELECT * FROM change_evidence WHERE change_run_id=? ORDER BY captured_at ASC",
            (change_run_id,)).fetchall()]
    except sqlite3.Error as exc:
        raise ChangeStoreUnavailable(f"list_change_evidence failed: {exc}") from exc
    finally:
        conn.close()
