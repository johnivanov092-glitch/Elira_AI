"""Compatibility facade for runtime status."""
from __future__ import annotations

from app.application.runtime.status import (
    ACTIVE_DB_PATH,
    ROOT_DATA_DIR,
    get_runtime_status,
    init_runtime_state,
)

__all__ = [
    "ACTIVE_DB_PATH",
    "ROOT_DATA_DIR",
    "get_runtime_status",
    "init_runtime_state",
]
