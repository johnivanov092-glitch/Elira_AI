"""Executor-owned SQLite migration for the disposable Phase-6 canary.

No database path, SQL, schema name, or migration identifier comes from the model or
the main backend. The trusted registry binds one canary database to one fixed v1 -> v2
migration. The adapter creates a private backup after approval and before the write,
rechecks the approved snapshot while holding ``BEGIN IMMEDIATE``, applies one fixed
transaction, runs a typed post-check, and restores the backup only after a definite
post-check failure.
"""
from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
from pathlib import Path
from typing import Any
from urllib.parse import quote

from .registry import Target

MAX_DATABASE_BYTES = 4 * 1024 * 1024
MAX_ROWS = 1_000
MAX_VALUE_CHARS = 4_096
BUSY_TIMEOUT_SECONDS = 5

_RUN_ID = re.compile(r"^chg-[0-9a-f]{32}$")
_BEFORE_COLUMNS = (
    ("id", "INTEGER", 0, None, 1),
    ("value", "TEXT", 1, None, 0),
)
_AFTER_COLUMNS = (*_BEFORE_COLUMNS, ("verified_at", "REAL", 0, None, 0))
_PROJECTION_KEYS = (
    "database_id", "migration_id", "state", "quick_check", "journal_mode",
    "user_version", "schema_sha256", "row_count", "data_sha256",
)


class DatabaseChangeError(RuntimeError):
    """The canary database could not be inspected, backed up, migrated, or restored."""


def _uri(path: Path) -> str:
    return f"file:{quote(path.resolve().as_posix(), safe='/:')}?mode=ro&immutable=1"


def _sha_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(128 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _schema_rows(conn: sqlite3.Connection) -> tuple[tuple[Any, ...], ...]:
    rows = conn.execute("PRAGMA table_info(canary_items)").fetchall()
    return tuple(
        (str(row[1]), str(row[2]).upper(), int(row[3]), row[4], int(row[5]))
        for row in rows
    )


def _schema_sha(columns: tuple[tuple[Any, ...], ...]) -> str:
    raw = json.dumps(columns, ensure_ascii=True, separators=(",", ":"))
    return hashlib.sha256(raw.encode("ascii")).hexdigest()


def _projection(conn: sqlite3.Connection, target: Target) -> dict[str, Any]:
    tables = [str(row[0]) for row in conn.execute(
        "SELECT name FROM sqlite_schema WHERE type='table' AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name"
    )]
    if tables != ["canary_items"]:
        raise DatabaseChangeError("database_schema_mismatch")
    quick = [str(row[0]) for row in conn.execute("PRAGMA quick_check(1)").fetchall()]
    quick_check = "ok" if quick == ["ok"] else "failed"
    journal_mode = str(conn.execute("PRAGMA journal_mode").fetchone()[0]).lower()
    user_version = int(conn.execute("PRAGMA user_version").fetchone()[0])
    columns = _schema_rows(conn)
    if columns == _BEFORE_COLUMNS and user_version == 1:
        state = "before"
    elif columns == _AFTER_COLUMNS and user_version == 2:
        state = "after"
    else:
        state = "unknown"
    rows = conn.execute(
        "SELECT id, value FROM canary_items ORDER BY id LIMIT ?", (MAX_ROWS + 1,)
    ).fetchall()
    if len(rows) > MAX_ROWS:
        raise DatabaseChangeError("database_row_cap_exceeded")
    digest = hashlib.sha256()
    for row in rows:
        item_id, value = row[0], row[1]
        if not isinstance(item_id, int) or isinstance(item_id, bool) or not isinstance(value, str):
            raise DatabaseChangeError("database_row_contract_mismatch")
        if len(value) > MAX_VALUE_CHARS:
            raise DatabaseChangeError("database_value_cap_exceeded")
        digest.update(json.dumps([item_id, value], ensure_ascii=False,
                                 separators=(",", ":")).encode("utf-8"))
        digest.update(b"\n")
    return {
        "kind": "sqlite_migration",
        "database_id": target.database_id,
        "migration_id": target.migration_id,
        "state": state,
        "quick_check": quick_check,
        "journal_mode": journal_mode,
        "user_version": user_version,
        "schema_sha256": _schema_sha(columns),
        "row_count": len(rows),
        "data_sha256": digest.hexdigest(),
    }


def _safe_source(target: Target) -> Path:
    path = Path(target.database_path)
    try:
        stat = path.stat()
    except OSError as exc:
        raise DatabaseChangeError("database_unavailable") from exc
    if not path.is_file() or stat.st_size <= 0 or stat.st_size > MAX_DATABASE_BYTES:
        raise DatabaseChangeError("database_size_invalid")
    for suffix in ("-wal", "-shm", "-journal"):
        if Path(f"{path}{suffix}").exists():
            raise DatabaseChangeError("database_sidecar_present")
    return path


def inspect(target: Target) -> tuple[bool, str, dict[str, Any]]:
    """Typed inspection with no row values, paths, SQL, or connection strings returned."""
    try:
        path = _safe_source(target)
        before = path.stat()
        conn = sqlite3.connect(_uri(path), uri=True, timeout=BUSY_TIMEOUT_SECONDS)
        try:
            conn.execute("PRAGMA query_only=ON")
            projected = _projection(conn, target)
        finally:
            conn.close()
        after = path.stat()
        if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise DatabaseChangeError("database_changed_during_inspect")
        projected["database_sha256"] = _sha_file(path)
        return True, "0", projected
    except (DatabaseChangeError, sqlite3.Error, OSError, ValueError):
        return False, "error", {}


def plannable(fields: dict[str, Any], target: Target) -> bool:
    return (
        fields.get("kind") == "sqlite_migration"
        and fields.get("database_id") == target.database_id
        and fields.get("migration_id") == target.migration_id
        and fields.get("state") == "before"
        and fields.get("quick_check") == "ok"
        and fields.get("journal_mode") == "delete"
        and fields.get("user_version") == 1
    )


def applied(fields: dict[str, Any], snapshot: dict[str, Any], target: Target) -> bool:
    return (
        fields.get("kind") == "sqlite_migration"
        and fields.get("database_id") == target.database_id
        and fields.get("migration_id") == target.migration_id
        and fields.get("state") == "after"
        and fields.get("quick_check") == "ok"
        and fields.get("journal_mode") == "delete"
        and fields.get("user_version") == 2
        and fields.get("row_count") == snapshot.get("row_count")
        and fields.get("data_sha256") == snapshot.get("data_sha256")
    )


def matches_before(fields: dict[str, Any], snapshot: dict[str, Any], target: Target) -> bool:
    return plannable(fields, target) and all(fields.get(key) == snapshot.get(key)
                                             for key in _PROJECTION_KEYS)


def planned_operation(target: Target) -> list[str]:
    """A conceptual, non-executable operation binding used only for capability hashing."""
    return ["sqlite-migration", target.database_id, target.migration_id]


def backup_path(target: Target, change_run_id: str) -> Path:
    if not _RUN_ID.fullmatch(str(change_run_id or "")):
        raise DatabaseChangeError("invalid_change_run_id")
    return Path(target.backup_dir) / f"{change_run_id}.sqlite3"


def _remove_backup(path: Path) -> None:
    try:
        path.unlink(missing_ok=True)
    except OSError:
        pass


def _create_backup(target: Target, change_run_id: str) -> tuple[Path, str, dict[str, Any]]:
    source = _safe_source(target)
    destination = backup_path(target, change_run_id)
    if not destination.parent.is_dir():
        raise DatabaseChangeError("backup_directory_unavailable")
    if destination.exists():
        raise DatabaseChangeError("backup_already_exists")
    src = sqlite3.connect(_uri(source), uri=True, timeout=BUSY_TIMEOUT_SECONDS)
    dst = sqlite3.connect(destination, timeout=BUSY_TIMEOUT_SECONDS)
    try:
        src.backup(dst)
        dst.commit()
        projected = _projection(dst, target)
    except Exception:
        _remove_backup(destination)
        raise
    finally:
        dst.close()
        src.close()
    try:
        with destination.open("r+b") as fh:
            os.fsync(fh.fileno())
    except OSError:
        _remove_backup(destination)
        raise
    return destination, _sha_file(destination), projected


def apply_fixed_migration(conn: sqlite3.Connection) -> None:
    """The only migration in this slice. No caller-controlled SQL exists."""
    conn.execute("ALTER TABLE canary_items ADD COLUMN verified_at REAL")
    conn.execute("PRAGMA user_version=2")


def _restore(target: Target, backup: Path, expected_sha256: str) -> bool:
    source = Path(target.database_path)
    temp_name = ""
    try:
        _safe_source(target)
        if _sha_file(backup) != expected_sha256:
            return False
        fd, temp_name = tempfile.mkstemp(prefix=".elira-db-restore-", suffix=".sqlite3",
                                         dir=source.parent)
        with os.fdopen(fd, "wb") as out, backup.open("rb") as inp:
            shutil.copyfileobj(inp, out, length=128 * 1024)
            out.flush()
            os.fsync(out.fileno())
        os.replace(temp_name, source)
        temp_name = ""
        definite, _exit, restored = inspect(target)
        return definite and restored.get("state") == "before"
    except (DatabaseChangeError, OSError, sqlite3.Error):
        return False
    finally:
        if temp_name:
            try:
                Path(temp_name).unlink(missing_ok=True)
            except OSError:
                pass


def apply(target: Target, *, change_run_id: str, snapshot: dict[str, Any]) -> tuple[str, str, dict[str, Any]]:
    """Apply the fixed migration and return a strictly projected terminal/unknown result."""
    evidence: dict[str, Any] = {
        "outcome": "command_failed",
        "database_id": target.database_id,
        "migration_id": target.migration_id,
        "before_user_version": 1,
        "after_user_version": 2,
        "rollback_attempted": False,
    }
    backup: Path | None = None
    try:
        backup, backup_sha, backup_projection = _create_backup(target, change_run_id)
        evidence["backup_sha256"] = backup_sha
        if not matches_before(backup_projection, snapshot, target):
            evidence["outcome"] = "aborted_before_apply"
            return "aborted_before_apply", "0", evidence

        conn = sqlite3.connect(target.database_path, timeout=BUSY_TIMEOUT_SECONDS)
        committed = False
        try:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute("BEGIN IMMEDIATE")
            locked = _projection(conn, target)
            if not matches_before(locked, snapshot, target):
                conn.rollback()
                evidence["outcome"] = "aborted_before_apply"
                return "aborted_before_apply", "0", evidence
            apply_fixed_migration(conn)
            conn.commit()
            committed = True
        except (sqlite3.Error, OSError, DatabaseChangeError):
            try:
                conn.rollback()
            except sqlite3.Error:
                pass
        finally:
            conn.close()

        definite, post_exit, post = inspect(target)
        if not committed:
            if definite and matches_before(post, snapshot, target):
                evidence["outcome"] = "command_failed"
                return "command_failed", "error", evidence
            evidence["outcome"] = "apply_unknown"
            return "apply_unknown", post_exit, evidence
        if not definite:
            evidence["outcome"] = "apply_unknown"
            return "apply_unknown", post_exit, evidence
        if applied(post, snapshot, target):
            evidence.update({
                "outcome": "applied",
                "row_count": post["row_count"],
                "schema_sha256": post["schema_sha256"],
            })
            return "applied", "0", evidence

        evidence["rollback_attempted"] = True
        if _restore(target, backup, backup_sha):
            evidence["outcome"] = "rolled_back"
            return "rolled_back", "0", evidence
        evidence["outcome"] = "rollback_failed"
        return "rollback_failed", "error", evidence
    except (DatabaseChangeError, sqlite3.Error, OSError):
        return "command_failed", "error", evidence


def cleanup_backup(target: Target, change_run_id: str) -> None:
    _remove_backup(backup_path(target, change_run_id))
