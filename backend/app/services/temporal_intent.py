"""Temporal-intent detection service — compatibility shim.

All logic lives in ``app.application.temporal_intent.runtime``.
``detect_temporal_intent`` re-exported for all callers:
planner_v2, response_cache, infrastructure/search, and tests.
"""
from __future__ import annotations

from app.application.temporal_intent.runtime import detect_temporal_intent

__all__ = ["detect_temporal_intent"]
