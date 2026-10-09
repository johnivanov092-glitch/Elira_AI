"""Phase 6 slice 1: bounded, read-only inspection of named SQLite databases.

The caller supplies only a server-owned ``database_id``. Paths, SQL, allowed schemas,
query profiles and limits live here. The connection is opened with SQLite ``mode=ro``,
then tightened with ``query_only`` and an authorizer. Only schema metadata and one
fixed aggregate query are returned; row contents and connection strings never leave
this module.
"""
from __future__ import annotations

import sqlite3
import json
import tempfile
import time
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Iterator

from app.core.config import DATA_DIR


INSPECT_TIMEOUT_SECONDS = 3.0
MAX_TABLES = 64
MAX_COLUMNS_PER_TABLE = 64
MAX_IDENTIFIER_CHARS = 128
MAX_SCHEMA_JSON_CHARS = 4000
MAX_SCHEMA_SUMMARY_CHARS = 2500
MAX_DATABASE_BYTES = 256 * 1024 * 1024
BACKUP_STALE_SECONDS = 24 * 60 * 60


class DatabaseInspectError(ValueError):
    """A named database is unavailable or cannot be safely inspected."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class DatabaseSpec:
    database_id: str
    filename: str
    engine: str
    allowed_schemas: tuple[str, ...]
    query_profile: str
    backup_pattern: str


@dataclass(frozen=True)
class SnapshotMeta:
    file_bytes: int
    file_modified_at: float


_DATABASES: dict[str, DatabaseSpec] = {
    "elira-state": DatabaseSpec(
        database_id="elira-state",
        filename="elira_state.db",
        engine="sqlite",
        allowed_schemas=("main",),
        query_profile="conversation-counts",
        backup_pattern="elira_state.db.bak*",
    ),
}

_SAFE_PRAGMAS = {
    "quick_check",
    "user_version",
    "schema_version",
    "page_count",
    "freelist_count",
    "table_info",
}
_DENIED_ACTION_NAMES = (
    "SQLITE_INSERT", "SQLITE_UPDATE", "SQLITE_DELETE",
    "SQLITE_CREATE_INDEX", "SQLITE_CREATE_TABLE", "SQLITE_CREATE_TEMP_INDEX",
    "SQLITE_CREATE_TEMP_TABLE", "SQLITE_CREATE_TEMP_TRIGGER", "SQLITE_CREATE_TEMP_VIEW",
    "SQLITE_CREATE_TRIGGER", "SQLITE_CREATE_VIEW", "SQLITE_CREATE_VTABLE",
    "SQLITE_DROP_INDEX", "SQLITE_DROP_TABLE", "SQLITE_DROP_TEMP_INDEX",
    "SQLITE_DROP_TEMP_TABLE", "SQLITE_DROP_TEMP_TRIGGER", "SQLITE_DROP_TEMP_VIEW",
    "SQLITE_DROP_TRIGGER", "SQLITE_DROP_VIEW", "SQLITE_DROP_VTABLE",
    "SQLITE_ALTER_TABLE", "SQLITE_REINDEX", "SQLITE_ANALYZE",
    "SQLITE_ATTACH", "SQLITE_DETACH", "SQLITE_TRANSACTION", "SQLITE_SAVEPOINT",
)
_DENIED_ACTIONS = {
    int(getattr(sqlite3, name)) for name in _DENIED_ACTION_NAMES if hasattr(sqlite3, name)
}


def resolve_database(database_id: str) -> DatabaseSpec:
    did = str(database_id or "").strip()
    spec = _DATABASES.get(did)
    if spec is None:
        raise DatabaseInspectError("unknown_database")
    return spec


def _within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _database_path(spec: DatabaseSpec) -> Path:
    try:
        root = Path(DATA_DIR).resolve()
        candidate = root / spec.filename
        resolved = candidate.resolve(strict=True)
    except (OSError, RuntimeError) as exc:
        raise DatabaseInspectError("database_unavailable") from exc
    if not _within(resolved, root) or not resolved.is_file():
        raise DatabaseInspectError("database_outside_data_dir")
    return resolved


def _authorizer(action: int, arg1: str | None, arg2: str | None,
                _db_name: str | None, _trigger: str | None) -> int:
    if action in _DENIED_ACTIONS:
        return sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_PRAGMA", -1):
        return sqlite3.SQLITE_OK if str(arg1 or "").lower() in _SAFE_PRAGMAS else sqlite3.SQLITE_DENY
    if action == getattr(sqlite3, "SQLITE_FUNCTION", -1):
        function_name = str(arg2 or arg1 or "").lower()
        if function_name == "load_extension":
            return sqlite3.SQLITE_DENY
    return sqlite3.SQLITE_OK


def _configure_snapshot(conn: sqlite3.Connection) -> sqlite3.Connection:
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.set_authorizer(_authorizer)
    return conn


def _safe_stat(path: Path, reason: str = "database_unavailable"):
    try:
        return path.stat()
    except OSError as exc:
        raise DatabaseInspectError(reason) from exc


@contextmanager
def _snapshot_connection(path: Path) -> Iterator[tuple[sqlite3.Connection, SnapshotMeta]]:
    """Yield a consistent in-memory/temp snapshot without modifying the source DB.

    A live WAL database is copied with SQLite's online backup API from a normal
    ``mode=ro`` connection, so WAL state and locks are respected. With no WAL, the
    main file is copied as bytes first and opened from a private immutable temp file;
    this avoids creating ``-wal/-shm`` merely to inspect an idle WAL-capable database.
    """
    main_stat = _safe_stat(path)
    wal = Path(f"{path}-wal")
    shm = Path(f"{path}-shm")
    wal_exists, shm_exists = wal.exists(), shm.exists()
    if wal_exists != shm_exists:
        raise DatabaseInspectError("database_wal_incomplete")

    snapshot: sqlite3.Connection | None = None
    source: sqlite3.Connection | None = None
    temp_dir: tempfile.TemporaryDirectory[str] | None = None
    try:
        if wal_exists:
            wal_stat = _safe_stat(wal, "database_wal_unavailable")
            if main_stat.st_size + wal_stat.st_size > MAX_DATABASE_BYTES:
                raise DatabaseInspectError("database_too_large")
            try:
                source = sqlite3.connect(f"{path.as_uri()}?mode=ro", uri=True, timeout=1.0)
                source.execute("PRAGMA query_only=ON")
                snapshot = sqlite3.connect(":memory:")
                deadline = time.monotonic() + INSPECT_TIMEOUT_SECONDS

                def progress(_status: int, _remaining: int, _total: int) -> None:
                    if time.monotonic() > deadline:
                        raise DatabaseInspectError("snapshot_timed_out")

                source.backup(snapshot, pages=256, progress=progress, sleep=0.01)
            except DatabaseInspectError:
                raise
            except sqlite3.Error as exc:
                raise DatabaseInspectError("database_snapshot_failed") from exc
            finally:
                if source is not None:
                    source.close()
                    source = None
        else:
            if main_stat.st_size > MAX_DATABASE_BYTES:
                raise DatabaseInspectError("database_too_large")
            try:
                raw = path.read_bytes()
                after_copy = path.stat()
            except OSError as exc:
                raise DatabaseInspectError("database_snapshot_failed") from exc
            if (main_stat.st_size, main_stat.st_mtime_ns) != (after_copy.st_size, after_copy.st_mtime_ns):
                raise DatabaseInspectError("database_changed_during_snapshot")
            if wal.exists() or shm.exists():
                raise DatabaseInspectError("database_changed_during_snapshot")
            try:
                temp_dir = tempfile.TemporaryDirectory(prefix="elira-db-inspect-")
                snapshot_path = Path(temp_dir.name) / "snapshot.db"
                snapshot_path.write_bytes(raw)
                snapshot = sqlite3.connect(
                    f"{snapshot_path.as_uri()}?mode=ro&immutable=1", uri=True, timeout=1.0
                )
            except (OSError, sqlite3.Error) as exc:
                raise DatabaseInspectError("database_snapshot_failed") from exc

        if snapshot is None:
            raise DatabaseInspectError("database_snapshot_failed")
        yield _configure_snapshot(snapshot), SnapshotMeta(
            file_bytes=int(main_stat.st_size),
            file_modified_at=float(main_stat.st_mtime),
        )
    finally:
        if source is not None:
            try:
                source.close()
            except sqlite3.Error as exc:
                raise DatabaseInspectError("database_snapshot_close_failed") from exc
        if snapshot is not None:
            try:
                snapshot.close()
            except sqlite3.Error as exc:
                raise DatabaseInspectError("database_snapshot_close_failed") from exc
        if temp_dir is not None:
            try:
                temp_dir.cleanup()
            except OSError as exc:
                raise DatabaseInspectError("database_snapshot_cleanup_failed") from exc


def _scalar_pragma(conn: sqlite3.Connection, name: str) -> Any:
    if name not in _SAFE_PRAGMAS:
        raise DatabaseInspectError("unsafe_pragma")
    row = conn.execute(f"PRAGMA {name}").fetchone()
    return row[0] if row else None


def _bounded_identifier(value: Any) -> str:
    return str(value or "")[:MAX_IDENTIFIER_CHARS]


def _schema_projection(conn: sqlite3.Connection) -> tuple[list[dict[str, Any]], bool]:
    rows = conn.execute(
        "SELECT name, type FROM sqlite_master "
        "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%' "
        "ORDER BY name LIMIT ?",
        (MAX_TABLES + 1,),
    ).fetchall()
    truncated = len(rows) > MAX_TABLES
    out: list[dict[str, Any]] = []
    for row in rows[:MAX_TABLES]:
        name = str(row["name"])
        if len(name) > MAX_IDENTIFIER_CHARS * 4:
            raise DatabaseInspectError("schema_identifier_too_long")
        # The table-valued pragma_table_info(?) form emits synthetic SQLITE_UPDATE
        # authorizer events for sqlite_master. The ordinary PRAGMA form does not. Quote
        # the database-owned identifier and keep UPDATE denied without exceptions.
        quoted_name = name.replace('"', '""')
        cols = conn.execute(f'PRAGMA table_info("{quoted_name}")').fetchall()
        projected = {
            "name": _bounded_identifier(name),
            "type": "view" if row["type"] == "view" else "table",
            "columns": [],
            "columns_truncated": len(cols) > MAX_COLUMNS_PER_TABLE,
        }
        for col in cols[:MAX_COLUMNS_PER_TABLE]:
            candidate = {
                    "name": _bounded_identifier(col["name"]),
                    "type": _bounded_identifier(col["type"]),
                    "not_null": bool(col["notnull"]),
                    "primary_key": bool(col["pk"]),
            }
            trial = {**projected, "columns": [*projected["columns"], candidate]}
            if len(json.dumps([*out, trial], ensure_ascii=False, separators=(",", ":"))) > MAX_SCHEMA_JSON_CHARS:
                projected["columns_truncated"] = True
                truncated = True
                break
            projected = trial
        if len(json.dumps([*out, projected], ensure_ascii=False, separators=(",", ":"))) > MAX_SCHEMA_JSON_CHARS:
            truncated = True
            break
        out.append(projected)
    return out, truncated


def schema_summary(tables: list[dict[str, Any]], truncated: bool) -> str:
    chunks: list[str] = []
    for table in tables:
        columns = ",".join(str(col.get("name") or "") for col in table.get("columns") or [])
        suffix = ",..." if table.get("columns_truncated") else ""
        chunk = f"{table.get('name')}({columns}{suffix})"
        candidate = "; ".join([*chunks, chunk])
        if len(candidate) > MAX_SCHEMA_SUMMARY_CHARS:
            truncated = True
            break
        chunks.append(chunk)
    summary = "; ".join(chunks)
    if truncated:
        marker = "; ...[truncated]"
        summary = summary[:max(0, MAX_SCHEMA_SUMMARY_CHARS - len(marker))] + marker
    return summary


def _backup_projection(spec: DatabaseSpec, now: float) -> dict[str, Any]:
    try:
        root = Path(DATA_DIR).resolve()
        paths = list(root.glob(spec.backup_pattern))
    except (OSError, RuntimeError) as exc:
        raise DatabaseInspectError("backup_scan_failed") from exc
    candidates: list[tuple[Path, Any]] = []
    for candidate in paths:
        try:
            resolved = candidate.resolve(strict=True)
        except (OSError, RuntimeError):
            continue
        if not _within(resolved, root):
            continue
        try:
            stat = resolved.stat()
        except OSError:
            continue
        if resolved.is_file():
            candidates.append((resolved, stat))
    if not candidates:
        return {
            "status": "missing",
            "count": 0,
            "stale_after_seconds": BACKUP_STALE_SECONDS,
        }
    _latest, stat = max(candidates, key=lambda item: item[1].st_mtime)
    age = max(0, int(now - stat.st_mtime))
    return {
        "status": "fresh" if age <= BACKUP_STALE_SECONDS else "stale",
        "count": len(candidates),
        "latest_age_seconds": age,
        "latest_modified_at": stat.st_mtime,
        "latest_bytes": stat.st_size,
        "stale_after_seconds": BACKUP_STALE_SECONDS,
    }


def inspect_database(spec: DatabaseSpec) -> dict[str, Any]:
    """Return bounded metadata and fixed aggregate counts for one named database."""
    path = _database_path(spec)
    with _snapshot_connection(path) as (conn, snapshot_meta):
        deadline = time.monotonic() + INSPECT_TIMEOUT_SECONDS
        conn.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 1000)
        try:
            quick_check = str(_scalar_pragma(conn, "quick_check") or "")
            if quick_check != "ok":
                raise DatabaseInspectError("integrity_check_failed")
            user_version = int(_scalar_pragma(conn, "user_version") or 0)
            schema_version = int(_scalar_pragma(conn, "schema_version") or 0)
            page_count = int(_scalar_pragma(conn, "page_count") or 0)
            freelist_count = int(_scalar_pragma(conn, "freelist_count") or 0)
            table_count = int(conn.execute(
                "SELECT COUNT(*) FROM sqlite_master "
                "WHERE type IN ('table', 'view') AND name NOT LIKE 'sqlite_%'"
            ).fetchone()[0])
            required_names = {
                str(row[0]) for row in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table' AND name IN ('chats','messages')"
                ).fetchall()
            }
            if required_names != {"chats", "messages"}:
                raise DatabaseInspectError("required_table_missing")
            tables, schema_truncated = _schema_projection(conn)
            counts = conn.execute(
                "SELECT (SELECT COUNT(*) FROM chats) AS chat_count, "
                "(SELECT COUNT(*) FROM messages) AS message_count"
            ).fetchone()
        except DatabaseInspectError:
            raise
        except sqlite3.OperationalError as exc:
            if "interrupted" in str(exc).lower():
                raise DatabaseInspectError("inspect_timed_out") from exc
            raise DatabaseInspectError("database_query_failed") from exc
        except sqlite3.Error as exc:
            raise DatabaseInspectError("database_query_failed") from exc

    return {
        "database_id": spec.database_id,
        "engine": spec.engine,
        "status": "ok",
        "allowed_schemas": list(spec.allowed_schemas),
        "file_bytes": snapshot_meta.file_bytes,
        "file_modified_at": snapshot_meta.file_modified_at,
        "quick_check": quick_check,
        "user_version": user_version,
        "schema_version": schema_version,
        "migration_state": "unversioned" if user_version == 0 else f"version-{user_version}",
        "page_count": page_count,
        "freelist_count": freelist_count,
        "table_count": table_count,
        "schema_truncated": schema_truncated,
        "tables": tables,
        "schema_summary": schema_summary(tables, schema_truncated),
        "safe_query": {
            "profile": spec.query_profile,
            "status": "ok",
            "chat_count": int(counts["chat_count"]),
            "message_count": int(counts["message_count"]),
        },
        "backup": _backup_projection(spec, time.time()),
    }
