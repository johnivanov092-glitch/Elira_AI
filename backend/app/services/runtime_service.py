"""Thin facade — all runtime service logic lives in infrastructure/runtime/runtime_service.py."""
from app.infrastructure.runtime.runtime_service import (  # noqa: F401
    ACTIVE_DB_PATH,
    ROOT_DATA_DIR,
    _chat_count_for,
    _storage_mode,
    get_runtime_status,
    init_runtime_state,
)
