"""Thin facade — all memory service logic lives in application/memory/memory_service.py."""
from app.application.memory.memory_service import (  # noqa: F401
    _DEFAULT_PROFILE,
    _normalize_profile,
    add_memory,
    build_memory_context,
    delete_memory,
    list_memories,
    list_profiles,
    search_memory,
)
