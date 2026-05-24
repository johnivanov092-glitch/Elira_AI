from __future__ import annotations

from app.infrastructure.db.connection import (
    DEFAULT_SQLITE_JOURNAL_MODE,
    DEFAULT_SQLITE_TIMEOUT_SECONDS,
)
from app.infrastructure.db.repositories.base import SqliteRepository


class MessagesRepository(SqliteRepository):
    def __init__(
        self,
        *,
        timeout: float = DEFAULT_SQLITE_TIMEOUT_SECONDS,
        journal_mode: str | None = DEFAULT_SQLITE_JOURNAL_MODE,
    ) -> None:
        super().__init__(
            db_name="elira_state.db",
            key_tables=("messages",),
            timeout=timeout,
            journal_mode=journal_mode,
        )
