# DEPRECATED: logic moved to profiles_service.py and memory_service.py
# Nobody imports this file. Kept as a shim for backward-compat only.
from __future__ import annotations

from app.application.profiles.runtime import get_profiles  # noqa: F401

__all__ = ["get_profiles"]
