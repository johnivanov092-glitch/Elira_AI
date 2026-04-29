"""Elira settings SQLite compatibility facade."""

from __future__ import annotations

from app.application.elira_memory.settings import (
    DB_PATH,
    DEFAULT_ROUTE_MAP,
    _connect,
    _ensure_route_map_column,
    get_route_model_map,
    get_settings,
    save_settings,
)

__all__ = [
    "DB_PATH",
    "DEFAULT_ROUTE_MAP",
    "_connect",
    "_ensure_route_map_column",
    "get_route_model_map",
    "get_settings",
    "save_settings",
]
