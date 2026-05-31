"""Skills compatibility facade."""
from __future__ import annotations

from app.application.skills.runtime import (
    OUTPUT_DIR,
    describe_db,
    generate_excel,
    generate_word,
    http_request,
    list_databases,
    run_sql,
    screenshot_capability_status,
    screenshot_url,
)

__all__ = [
    "OUTPUT_DIR",
    "describe_db",
    "generate_excel",
    "generate_word",
    "http_request",
    "list_databases",
    "run_sql",
    "screenshot_capability_status",
    "screenshot_url",
]
