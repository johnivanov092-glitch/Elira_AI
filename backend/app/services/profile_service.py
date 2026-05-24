"""Compatibility facade for legacy memory profiles."""
from __future__ import annotations

from app.application.memory.profiles import create_profile, get_profiles, remove_profile

__all__ = ["create_profile", "get_profiles", "remove_profile"]
