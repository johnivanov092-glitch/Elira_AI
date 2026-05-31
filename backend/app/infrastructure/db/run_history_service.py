"""Run history compatibility facade."""

from __future__ import annotations

from app.application.run_history.service import (
    DB_PATH,
    LEGACY_JSON_PATHS,
    RunHistoryService,
    _connect,
    _init_db,
    _load_legacy_runs,
    _rotate,
)

__all__ = [
    "DB_PATH",
    "LEGACY_JSON_PATHS",
    "RunHistoryService",
    "_connect",
    "_init_db",
    "_load_legacy_runs",
    "_rotate",
]
