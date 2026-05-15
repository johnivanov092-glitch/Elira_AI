"""Thin facade — all temporal intent logic lives in application/planning/temporal_intent.py."""
from app.application.planning.temporal_intent import (  # noqa: F401
    _collect_years,
    _contains_any,
    detect_temporal_intent,
)
