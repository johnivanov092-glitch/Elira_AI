"""Thin facade — all settings logic lives in infrastructure/db/elira_settings_sqlite.py."""
from app.infrastructure.db.elira_settings_sqlite import (  # noqa: F401
    DB_PATH,
    DEFAULT_ROUTE_MAP,
    _connect,
    _ensure_route_map_column,
    get_route_model_map,
    get_settings,
    save_settings,
)
