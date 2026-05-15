from __future__ import annotations

from typing import Any

from app.services.smart_memory import (
    add_memory as _sm_add_memory,
    delete_memory as _sm_delete_memory,
    get_relevant_context as _sm_get_relevant_context,
    list_memories as _sm_list_memories,
    list_profiles as _sm_list_profiles,
    search_memory as _sm_search_memory,
)

_DEFAULT_PROFILE = "default"


def _normalize_profile(profile: str | None) -> str:
    value = (profile or "").strip()
    return value or _DEFAULT_PROFILE


def list_profiles() -> dict[str, Any]:
    return _sm_list_profiles()


def list_memories(profile: str) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    result = _sm_list_memories(limit=500, profile_name=normalized)
    return {
        "ok": True,
        "profile": normalized,
        "items": result.get("items", []),
        "count": result.get("count", 0),
    }


def add_memory(profile: str, text: str, source: str = "manual") -> dict[str, Any]:
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
    normalized = _normalize_profile(profile)
    try:
        mem_id = int(item_id)
    except Exception:
        return {"ok": False, "profile": normalized, "error": "Invalid memory id"}

    result = _sm_delete_memory(mem_id, profile_name=normalized)
    result["profile"] = normalized
    return result


def search_memory(profile: str, query: str, limit: int = 10) -> dict[str, Any]:
    normalized = _normalize_profile(profile)
    result = _sm_search_memory(
        query=query,
        limit=max(1, int(limit)),
        profile_name=normalized,
    )
    result["profile"] = normalized
    return result


def build_memory_context(profile: str, query: str, limit: int = 5) -> str:
    normalized = _normalize_profile(profile)
    return _sm_get_relevant_context(
        query=query,
        max_items=max(1, int(limit)),
        profile_name=normalized,
    )
