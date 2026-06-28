"""Compatibility shim for the /api/memory route.

The actual logic now lives in :mod:`app.application.memory.facade` (the single
"one door" over both memory engines). This module keeps the route's existing
profile-first signatures and response shapes and delegates to the facade.
"""

from __future__ import annotations

from typing import Any

from app.application.memory import facade


_DEFAULT_PROFILE = "default"


def _normalize_profile(profile: str | None) -> str:
    value = (profile or "").strip()
    return value or _DEFAULT_PROFILE


def list_profiles() -> dict[str, Any]:
    return facade.list_profiles()


def list_memories(profile: str) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    result = facade.list_facts(limit=500, profile=normalized)
    return {
        "ok": True,
        "profile": normalized,
        "items": result.get("items", []),
        "count": result.get("count", 0),
    }


def add_memory(profile: str, text: str, source: str = "manual") -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    result = facade.add_fact(
        text,
        category="fact",
        source=source or "manual",
        importance=6,
        profile=normalized,
    )
    result["profile"] = normalized
    return result


def delete_memory(profile: str, item_id: str) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    try:
        mem_id = int(item_id)
    except Exception:
        return {"ok": False, "profile": normalized, "error": "Invalid memory id"}

    result = facade.delete_fact(mem_id, profile=normalized)
    result["profile"] = normalized
    return result


def search_memory(profile: str, query: str, limit: int = 10) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    result = facade.search_facts(query, limit=max(1, int(limit)), profile=normalized)
    result["profile"] = normalized
    return result


def build_memory_context(profile: str, query: str, limit: int = 5) -> str:
    normalized = _normalize_profile(profile)
    return facade.fact_context(query, max_items=max(1, int(limit)), profile=normalized)
