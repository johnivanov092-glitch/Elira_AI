"""Thin facade — all planner V2 logic lives in application/planning/planner_v2_service.py."""
from app.application.planning.planner_v2_service import (  # noqa: F401
    _CHAT_ONLY_PATTERNS,
    _CODE_WORDS,
    _LIBRARY_WORDS,
    _MEMORY_WORDS,
    _NEEDS_WEB_PATTERNS,
    _PROJECT_WORDS,
    _PYTHON_WORDS,
    _RESEARCH_WORDS,
    _count,
    _needs_web,
    PlannerV2Service,
)
