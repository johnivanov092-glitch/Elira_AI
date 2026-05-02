# -*- coding: utf-8 -*-
"""Application-layer runtime for the memory service.

Profile-normalising façade over ``app.application.smart_memory``.
Adds a consistent ``profile`` key to all results and validates memory IDs.
Pure Python logic — DB access is delegated to the smart_memory layer.
"""
from __future__ import annotations

from typing import Any

_DEFAULT_PROFILE = "default"


# ── helpers ───────────────────────────────────────────────────────────────────

def _normalize_profile(profile: str | None) -> str:
    value = (profile or "").strip()
    return value or _DEFAULT_PROFILE


# ── public API ────────────────────────────────────────────────────────────────

def list_profiles() -> dict[str, Any]:
    """List all memory profiles."""
    from app.services.smart_memory import list_profiles as _sm_list_profiles
    return _sm_list_profiles()


def list_memories(profile: str) -> dict[str, Any]:
    """Return up to 500 memories for *profile*.

    Returns:
      ok, profile, items, count
    """
    from app.services.smart_memory import list_memories as _sm_list_memories
    normalized = _normalize_profile(profile)
    result = _sm_list_memories(limit=500, profile_name=normalized)
    return {
        "ok": True,
        "profile": normalized,
        "items": result.get("items", []),
        "count": result.get("count", 0),
    }


def add_memory(profile: str, text: str, source: str = "manual") -> dict[str, Any]:
    """Add a new memory entry under *profile*.

    Returns the smart_memory result dict augmented with ``profile``.
    """
    from app.services.smart_memory import add_memory as _sm_add_memory
    normalized = _normalize_profile(profile)
    result = _sm_add_memory(
        text=text,
        category="fact",
        source=source or "manual",
        importance=6,
        profile_name=normalized,
    )
    result["profile"] = normalized
    return result


def delete_memory(profile: str, item_id: str) -> dict[str, Any]:
    """Delete memory *item_id* from *profile*.

    Returns the smart_memory result dict augmented with ``profile``.
    """
    from app.services.smart_memory import delete_memory as _sm_delete_memory
    normalized = _normalize_profile(profile)
    try:
        mem_id = int(item_id)
    except Exception:
        return {"ok": False, "profile": normalized, "error": "Invalid memory id"}

    result = _sm_delete_memory(mem_id, profile_name=normalized)
    result["profile"] = normalized
    return result


def search_memory(profile: str, query: str, limit: int = 10) -> dict[str, Any]:
    """Full-text search over memories for *profile*.

    Returns the smart_memory result dict augmented with ``profile``.
    """
    from app.services.smart_memory import search_memory as _sm_search_memory
    normalized = _normalize_profile(profile)
    result = _sm_search_memory(
        query=query,
        limit=max(1, int(limit)),
        profile_name=normalized,
    )
    result["profile"] = normalized
    return result


def build_memory_context(profile: str, query: str, limit: int = 5) -> str:
    """Return a context string of the most relevant memories for *query*."""
    from app.services.smart_memory import get_relevant_context as _sm_get_relevant_context
    normalized = _normalize_profile(profile)
    return _sm_get_relevant_context(
        query=query,
        max_items=max(1, int(limit)),
        profile_name=normalized,
    )
