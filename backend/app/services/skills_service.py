"""Thin facade — all skills service logic lives in application/skills/skills_service.py."""
from app.application.skills.skills_service import (  # noqa: F401
    ALLOWED_DB_DIRS,
    BLOCKED_HOSTS,
    OUTPUT_DIR,
    _safe_db,
    describe_db,
    generate_excel,
    generate_word,
    http_request,
    list_databases,
    run_sql,
    screenshot_capability_status,
    screenshot_url,
)
