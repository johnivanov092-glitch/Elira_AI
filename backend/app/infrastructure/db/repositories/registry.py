from __future__ import annotations

from app.infrastructure.db.connection import (
    DEFAULT_SQLITE_JOURNAL_MODE,
    DEFAULT_SQLITE_TIMEOUT_SECONDS,
)
from app.infrastructure.db.repositories.base import SqliteRepository


class RegistryRepository(SqliteRepository):
    def __init__(
        self,
        *,
        db_name: str = "agent_registry.db",
        key_tables: tuple[str, ...] = ("agents", "agent_state", "agent_runs"),
        timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
        journal_mode: str | None = DEFAULT_SQLITE_JOURNAL_MODE,
    ) -> None:
        super().__init__(
            db_name=db_name,
            key_tables=key_tables,
            timeout=timeout,
            journal_mode=journal_mode,
        )
