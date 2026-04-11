"""memory.py — SQLite память + FAISS/keyword поиск + профили пользователей.

Улучшения v7.3:
  • Дедупликация через MD5-хеш контента
  • add_memory() возвращает bool (True=добавлено, False=дубликат)
  • Инкрементальный FAISS-кеш (пересоздаётся только при изменении данных)
"""
import hashlib
import json
import sqlite3
from datetime import datetime
from typing import List, Dict, Any

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


# ── Настройки устройства (settings.json) ─────────────────────────────────────
_SETTINGS_DEFAULTS = {
    "active_mem_profile": "default",
    "model":              "qwen3:8b",
}


def load_settings() -> dict:
    """Читает settings.json. Возвращает дефолты если файл не существует."""
    try:
        if SETTINGS_PATH.exists():
            data = json.loads(SETTINGS_PATH.read_text(encoding="utf-8"))
            # Заполняем отсутствующие ключи дефолтами
            return {**_SETTINGS_DEFAULTS, **data}
    except Exception:
        pass
    return dict(_SETTINGS_DEFAULTS)


def save_settings(settings: dict):
    """Сохраняет settings.json атомарно."""
    try:
        SETTINGS_PATH.write_text(
            json.dumps(settings, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
    except Exception:
        pass

def vector_memory_capability_status() -> Dict[str, Any]:
    return _app_vector_memory_capability_status()


# ── Дедупликация ──────────────────────────────────────────────────────────────
def _content_hash(text: str) -> str:
    """MD5-хеш нормализованного контента для дедупликации."""
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()


# ── Инициализация БД ─────────────────────────────────────────────────────────
def init_db():
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS memories (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                content      TEXT    NOT NULL,
                source       TEXT,
                created_at   TEXT    NOT NULL,
                pinned       INTEGER DEFAULT 0,
                memory_type  TEXT    DEFAULT 'general',
                profile_name TEXT    DEFAULT '',
                content_hash TEXT    DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS mem_profiles (
                id         INTEGER PRIMARY KEY AUTOINCREMENT,
                name       TEXT NOT NULL UNIQUE,
                emoji      TEXT DEFAULT '👤',
                created_at TEXT NOT NULL
            )
        """)
        # миграции для старых БД
        for sql in [
            "ALTER TABLE memories ADD COLUMN pinned INTEGER DEFAULT 0",
            "ALTER TABLE memories ADD COLUMN memory_type TEXT DEFAULT 'general'",
            "ALTER TABLE memories ADD COLUMN profile_name TEXT DEFAULT ''",
            "ALTER TABLE memories ADD COLUMN content_hash TEXT DEFAULT ''",
        ]:
            try:
                conn.execute(sql)
            except Exception:
                pass
        conn.execute(
            "INSERT OR IGNORE INTO mem_profiles (name, emoji, created_at) VALUES (?, ?, ?)",
            ("default", "👤", datetime.now().isoformat(timespec="seconds")),
        )
        conn.execute("""
            CREATE TABLE IF NOT EXISTS tool_usage (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                tool_name    TEXT NOT NULL,
                task_hint    TEXT DEFAULT '',
                ok           INTEGER DEFAULT 1,
                score        REAL DEFAULT 1.0,
                notes        TEXT DEFAULT '',
                created_at   TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS knowledge_chunks (
                id           INTEGER PRIMARY KEY AUTOINCREMENT,
                title        TEXT DEFAULT '',
                url          TEXT DEFAULT '',
                content      TEXT NOT NULL,
                source       TEXT DEFAULT '',
                chunk_type   TEXT DEFAULT 'note',
                created_at   TEXT NOT NULL,
                profile_name TEXT DEFAULT '',
                content_hash TEXT DEFAULT ''
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS task_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                route_mode TEXT,
                graph_used TEXT,
                final_status TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS reflections (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                answer_text TEXT,
                answered INTEGER,
                grounded INTEGER,
                complete INTEGER,
                actionable INTEGER,
                safe INTEGER,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS working_memory (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT,
                profile_name TEXT,
                step_name TEXT,
                fact_type TEXT,
                content TEXT,
                score REAL DEFAULT 1.0,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.execute("""
            CREATE TABLE IF NOT EXISTS self_improve_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                task_text TEXT,
                iteration INTEGER,
                answer_text TEXT,
                critique_json TEXT,
                reflection_json TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS v8_strategy_usage (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                strategy TEXT NOT NULL,
                route_mode TEXT DEFAULT '',
                task_hint TEXT DEFAULT '',
                ok INTEGER DEFAULT 1,
                score REAL DEFAULT 1.0,
                latency REAL DEFAULT 0.0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_learning_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query TEXT DEFAULT '',
                url TEXT DEFAULT '',
                title TEXT DEFAULT '',
                source_kind TEXT DEFAULT 'web',
                ok INTEGER DEFAULT 1,
                saved_kb INTEGER DEFAULT 0,
                saved_memory INTEGER DEFAULT 0,
                notes TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                profile_name TEXT DEFAULT ''
            )
        """)

        conn.execute("""
            CREATE TABLE IF NOT EXISTS memory_compaction_runs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                profile_name TEXT,
                source_count INTEGER DEFAULT 0,
                summary_count INTEGER DEFAULT 0,
                deleted_count INTEGER DEFAULT 0,
                notes TEXT,
                created_at TEXT DEFAULT CURRENT_TIMESTAMP
            )
        """)
        conn.commit()


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

