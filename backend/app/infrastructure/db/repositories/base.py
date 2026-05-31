from __future__ import annotations

import sqlite3
from dataclasses import dataclass
from pathlib import Path

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import (
    DEFAULT_SQLITE_JOURNAL_MODE,
    DEFAULT_SQLITE_TIMEOUT_SECONDS,
    connect_sqlite,
)


@dataclass(slots=True)
class SqliteRepository:
    db_name: str
    key_tables: tuple[str, ...] = ()
    timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS
    journal_mode: str | None = DEFAULT_SQLITE_JOURNAL_MODE

    def __post_init__(self) -> None:
        if not self.db_name or not self.db_name.strip():
            raise ValueError("db_name is required")
        if self.timeout <= 0:
            raise ValueError("timeout must be positive")

    @property
    def db_path(self) -> Path:
        key_tables = self.key_tables or None
        return sqlite_data_file(self.db_name, key_tables=key_tables)

    def connect(self) -> sqlite3.Connection:
        return connect_sqlite(
            self.db_path,
            timeout=self.timeout,
            journal_mode=self.journal_mode,
        )
