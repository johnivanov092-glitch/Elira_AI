"""Thin facade — all run history logic lives in infrastructure/db/run_history_service.py."""
from app.infrastructure.db.run_history_service import (  # noqa: F401
    DB_PATH,
    LEGACY_JSON_PATHS,
    RunHistoryService,
    _MAX_RUNS,
    _connect,
    _init_db,
    _load_legacy_runs,
    _rotate,
)
