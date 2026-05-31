"""Compatibility facade for chat temporal intent detection."""
from __future__ import annotations

from app.application.chat.temporal_intent import (
    ANALYTIC_TERMS,
    CURRENT_WORLD_TERMS,
    HISTORICAL_PATTERNS,
    HISTORICAL_TERMS,
    RELATIVE_TIME_TERMS,
    YEAR_RE,
    detect_temporal_intent,
)

__all__ = [
    "ANALYTIC_TERMS",
    "CURRENT_WORLD_TERMS",
    "HISTORICAL_PATTERNS",
    "HISTORICAL_TERMS",
    "RELATIVE_TIME_TERMS",
    "YEAR_RE",
    "detect_temporal_intent",
]
