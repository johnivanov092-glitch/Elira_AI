"""memory.py — SQLite память + FAISS/keyword поиск + профили пользователей.

Улучшения v7.3:
  • Дедупликация через MD5-хеш контента
  • add_memory() возвращает bool (True=добавлено, False=дубликат)
  • Инкрементальный FAISS-кеш (пересоздаётся только при изменении данных)
"""
import hashlib
from datetime import datetime
from typing import List, Dict, Any

from app.application.memory.bootstrap import (
    init_db as _app_init_db,
    load_settings as _app_load_settings,
    save_settings as _app_save_settings,
)
from app.application.memory.context import (
    build_memory_context as _app_build_memory_context,
    search_memories_weighted as _app_search_memories_weighted,
)
from app.application.memory.store import (
    add_memory as _app_add_memory,
    clear_memories as _app_clear_memories,
    create_mem_profile as _app_create_mem_profile,
    delete_mem_profile as _app_delete_mem_profile,
    delete_memory as _app_delete_memory,
    export_memories as _app_export_memories,
    import_memories_from_json as _app_import_memories_from_json,
    list_mem_profiles as _app_list_mem_profiles,
    load_memories as _app_load_memories,
    set_memory_pin as _app_set_memory_pin,
)
from app.application.memory.search import (
    keyword_search_memory as _app_keyword_search_memory,
    semantic_search_memory as _app_semantic_search_memory,
    vector_memory_capability_status as _app_vector_memory_capability_status,
)
from .config import DB_PATH, SETTINGS_PATH


def load_settings() -> dict:
    return _app_load_settings(settings_path=SETTINGS_PATH)


def save_settings(settings: dict):
    _app_save_settings(settings_path=SETTINGS_PATH, settings=settings)

def vector_memory_capability_status() -> Dict[str, Any]:
    return _app_vector_memory_capability_status()


# ── Дедупликация ──────────────────────────────────────────────────────────────
def _content_hash(text: str) -> str:
    """MD5-хеш нормализованного контента для дедупликации."""
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()


# ── Инициализация БД ─────────────────────────────────────────────────────────
def init_db():
    _app_init_db(
        db_path=DB_PATH,
        now_iso_func=lambda: datetime.now().isoformat(timespec="seconds"),
    )


# ── Профили памяти ────────────────────────────────────────────────────────────
def list_mem_profiles() -> List[Dict[str, Any]]:
    return _app_list_mem_profiles(db_path=str(DB_PATH))


def create_mem_profile(name: str, emoji: str = "👤") -> bool:
    return _app_create_mem_profile(
        db_path=str(DB_PATH),
        name=name,
        emoji=emoji,
        now_iso_func=lambda: datetime.now().isoformat(timespec="seconds"),
    )


def delete_mem_profile(name: str):
    _app_delete_mem_profile(db_path=str(DB_PATH), name=name)


# ── CRUD памяти ───────────────────────────────────────────────────────────────
def add_memory(content: str, source: str = "manual", pinned: bool = False,
               memory_type: str = "general", profile_name: str = "",
               deduplicate: bool = True) -> bool:
    return _app_add_memory(
        db_path=str(DB_PATH),
        content_hash_func=_content_hash,
        now_iso_func=lambda: datetime.now().isoformat(timespec="seconds"),
        content=content,
        source=source,
        pinned=pinned,
        memory_type=memory_type,
        profile_name=profile_name,
        deduplicate=deduplicate,
    )


def load_memories(limit: int = 500, only_pinned: bool = False, profile_name: str = ""):
    return _app_load_memories(
        db_path=str(DB_PATH),
        limit=limit,
        only_pinned=only_pinned,
        profile_name=profile_name,
    )


def delete_memory(memory_id: int):
    _app_delete_memory(db_path=str(DB_PATH), memory_id=memory_id)


def clear_memories(profile_name: str = ""):
    _app_clear_memories(db_path=str(DB_PATH), profile_name=profile_name)


def set_memory_pin(memory_id: int, pinned: bool):
    _app_set_memory_pin(db_path=str(DB_PATH), memory_id=memory_id, pinned=pinned)


def export_memories(profile_name: str = "") -> str:
    return _app_export_memories(
        profile_name=profile_name,
        load_memories_func=load_memories,
    )


def import_memories_from_json(text: str, max_items: int = 2000):
    _app_import_memories_from_json(
        text=text,
        add_memory_func=add_memory,
        max_items=max_items,
    )




# ── Tool memory ───────────────────────────────────────────────────────────────

# ═══════════════════════════════════════════════════════════════
# TOOL MEMORY & KNOWLEDGE BASE -- extracted to domain/memory/knowledge_base.py
# ═══════════════════════════════════════════════════════════════

from app.domain.memory.knowledge_base import (  # noqa: E402
    record_tool_usage,
    get_tool_preferences,
    build_tool_memory_context,
    add_kb_record,
    search_kb,
    build_kb_context,
    get_kb_stats,
)


# ── Поиск ─────────────────────────────────────────────────────────────────────
def keyword_search_memory(query: str, top_k: int = 10, profile_name: str = "") -> List[str]:
    return _app_keyword_search_memory(
        query=query,
        top_k=top_k,
        profile_name=profile_name,
        load_memories_func=load_memories,
    )


def semantic_search_memory(query: str, top_k: int = 5, profile_name: str = "") -> List[str]:
    return _app_semantic_search_memory(
        query=query,
        top_k=top_k,
        profile_name=profile_name,
        load_memories_func=load_memories,
    )


def search_memories_weighted(query: str, profile_name: str = "", top_k: int = 8) -> List[Dict[str, Any]]:
    return _app_search_memories_weighted(
        query=query,
        profile_name=profile_name,
        top_k=top_k,
        load_memories_func=load_memories,
        semantic_search_memory_func=semantic_search_memory,
        keyword_search_memory_func=keyword_search_memory,
        content_hash_func=_content_hash,
    )


def build_memory_context(query: str, profile_name: str, top_k: int = 5) -> str:
    return _app_build_memory_context(
        query=query,
        profile_name=profile_name,
        top_k=top_k,
        load_memories_func=load_memories,
        semantic_search_memory_func=semantic_search_memory,
        keyword_search_memory_func=keyword_search_memory,
        content_hash_func=_content_hash,
    )




# ═══════════════════════════════════════════════════════════════
# TASK & REFLECTION TRACKING -- extracted to domain/memory/task_tracking.py
# ═══════════════════════════════════════════════════════════════

from app.domain.memory.task_tracking import (  # noqa: E402
    record_task_run,
    record_reflection,
    get_recent_task_runs,
    get_recent_reflections,
)





# ═══════════════════════════════════════════════════════════════
# WORKING MEMORY & COMPACTION -- extracted to domain/memory/working_memory.py
# ═══════════════════════════════════════════════════════════════

from app.domain.memory.working_memory import (  # noqa: E402
    add_working_memory,
    get_working_memory,
    build_working_memory_context,
    clear_working_memory,
    get_recent_working_memory_runs,
    record_memory_compaction_run,
    get_recent_memory_compaction_runs,
    compact_memory,
)



# ═══════════════════════════════════════════════════════════════
# STRATEGY & LEARNING -- extracted to domain/memory/strategy_tracking.py
# ═══════════════════════════════════════════════════════════════

from app.domain.memory.strategy_tracking import (  # noqa: E402
    record_self_improve_run,
    get_recent_self_improve_runs,
    record_v8_strategy_usage,
    get_v8_strategy_preferences,
    build_v8_strategy_context,
    get_recent_v8_strategy_runs,
    record_web_learning_run,
    get_recent_web_learning_runs,
    build_web_learning_context,
)

