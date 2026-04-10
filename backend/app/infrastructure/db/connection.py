from __future__ import annotations

import logging
import sqlite3
from pathlib import Path


LOGGER = logging.getLogger(__name__)

DEFAULT_SQLITE_TIMEOUT_SECONDS = 5.0
DEFAULT_SQLITE_JOURNAL_MODE = "WAL"
_SUPPORTED_JOURNAL_MODES = {"DELETE", "TRUNCATE", "PERSIST", "MEMORY", "WAL", "OFF"}


def connect_sqlite(
    db_path: str | Path,
    *,
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
    row_factory: type[sqlite3.Row] | None = sqlite3.Row,
    journal_mode: str | None = DEFAULT_SQLITE_JOURNAL_MODE,
) -> sqlite3.Connection:
    path = Path(db_path)
    if not str(path).strip():
        raise ValueError("db_path is required")
    if timeout <= 0:
        raise ValueError("timeout must be positive")

    normalized_journal_mode: str | None = None
    if journal_mode is not None:
        normalized_journal_mode = str(journal_mode).strip().upper()
        if normalized_journal_mode not in _SUPPORTED_JOURNAL_MODES:
            raise ValueError(f"Unsupported sqlite journal mode: {journal_mode}")

    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(str(path), timeout=timeout)

    if row_factory is not None:
        connection.row_factory = row_factory

    if normalized_journal_mode is None:
        return connection

    try:
        connection.execute(f"PRAGMA journal_mode={normalized_journal_mode}")
    except sqlite3.DatabaseError:
        LOGGER.exception("Failed to initialize sqlite connection for %s", path)
        connection.close()
        raise

    return connection
