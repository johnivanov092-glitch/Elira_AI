"""Thin facade — all smart memory logic lives in application/memory/smart_memory.py.

Mutable module-level state (DB_PATH) lives in application/memory/smart_memory.py.
If tests need to redirect the database path, they should import that module directly:

    import app.application.memory.smart_memory as _sm
    _sm.DB_PATH = Path(tmpdir) / "smart_memory.db"
"""
from app.application.memory.smart_memory import (  # noqa: F401
    DEFAULT_PROFILE,
    DB_PATH,
    TOKEN_RE,
    _FACT_PATTERNS,
    _REMEMBER_PATTERNS,
    _STOP_WORDS,
    _classify_memory_text,
    _conn,
    _normalize_profile,
    _similarity,
    _tokenize,
    add_memory,
    clear_all_memories,
    delete_memory,
    extract_and_save,
    get_relevant_context,
    get_stats,
    init_memory_db,
    is_memory_command,
    list_memories,
    list_profiles,
    search_memory,
)
