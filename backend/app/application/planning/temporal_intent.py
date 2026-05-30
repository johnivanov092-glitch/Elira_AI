"""Re-export facade — implementation lives in app.core.temporal_intent."""
from app.core.temporal_intent import detect_temporal_intent as detect_temporal_intent  # noqa: F401

__all__ = ["detect_temporal_intent"]
