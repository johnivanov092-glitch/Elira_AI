"""Profiles service — compatibility shim.

All logic lives in ``app.application.profiles.runtime``.
Public API re-exported for all callers: api/routes/profiles.
"""
from __future__ import annotations

from app.application.profiles.runtime import get_profiles

__all__ = ["get_profiles"]
