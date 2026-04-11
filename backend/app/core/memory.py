"""memory.py — SQLite память + FAISS/keyword поиск + профили пользователей.

Улучшения v7.3:
  • Дедупликация через MD5-хеш контента
  • add_memory() возвращает bool (True=добавлено, False=дубликат)
  • Инкрементальный FAISS-кеш (пересоздаётся только при изменении данных)
"""
import hashlib
import json
import re
import sqlite3
from datetime import datetime
from typing import List, Dict, Any

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

try:
    from sentence_transformers import SentenceTransformer
except Exception:
    SentenceTransformer = None

_EMBEDDER = None
_FAISS_CACHE: Dict[str, Any] = {}


def _get_embedder():
    global _EMBEDDER
    if SentenceTransformer is None:
        return None
    if _EMBEDDER is None:
        _EMBEDDER = SentenceTransformer("sentence-transformers/all-MiniLM-L6-v2")
    return _EMBEDDER

try:
    import faiss
except Exception:
    faiss = None

try:
    import numpy as np
except Exception:
    np = None


def vector_memory_capability_status() -> Dict[str, Any]:
    missing: List[str] = []
    if SentenceTransformer is None:
        missing.append("sentence-transformers")
    if faiss is None:
        missing.append("faiss-cpu")
    if np is None:
        missing.append("numpy")

    available = not missing
    return {
        "feature": "vector_memory",
        "available": available,
        "mode": "vector" if available else "keyword_fallback",
        "reason": None if available else "optional_dependency_missing",
        "missing_packages": missing,
        "hint": None if available else "pip install -r requirements-optional.txt",
    }


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
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT id, name, emoji, created_at FROM mem_profiles ORDER BY id ASC"
        ).fetchall()
    return [{"id": r[0], "name": r[1], "emoji": r[2], "created_at": r[3]} for r in rows]


def create_mem_profile(name: str, emoji: str = "👤") -> bool:
    name = name.strip()
    if not name or len(name) > 40:
        return False
    try:
        with sqlite3.connect(DB_PATH) as conn:
            conn.execute(
                "INSERT OR IGNORE INTO mem_profiles (name, emoji, created_at) VALUES (?, ?, ?)",
                (name, emoji, datetime.now().isoformat(timespec="seconds")),
            )
            conn.commit()
        return True
    except Exception:
        return False


def delete_mem_profile(name: str):
    if name == "default":
        return
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM mem_profiles WHERE name = ?", (name,))
        conn.execute("DELETE FROM memories WHERE profile_name = ?", (name,))
        conn.commit()


# ── CRUD памяти ───────────────────────────────────────────────────────────────
def add_memory(content: str, source: str = "manual", pinned: bool = False,
               memory_type: str = "general", profile_name: str = "",
               deduplicate: bool = True) -> bool:
    """Добавляет запись в память. Возвращает True если добавлено, False если дубликат/пусто."""
    content = (content or "").strip()
    if not content:
        return False

    h = _content_hash(content)

    with sqlite3.connect(DB_PATH) as conn:
        # Проверка дубликата по хешу
        if deduplicate:
            existing = conn.execute(
                "SELECT id FROM memories WHERE content_hash = ? AND profile_name = ? LIMIT 1",
                (h, profile_name),
            ).fetchone()
            if existing:
                return False

        conn.execute(
            "INSERT INTO memories (content, source, created_at, pinned, memory_type, profile_name, content_hash) "
            "VALUES (?, ?, ?, ?, ?, ?, ?)",
            (content, source, datetime.now().isoformat(timespec="seconds"),
             int(pinned), memory_type, profile_name, h),
        )
        conn.commit()
    return True


def load_memories(limit: int = 500, only_pinned: bool = False, profile_name: str = ""):
    with sqlite3.connect(DB_PATH) as conn:
        sql = "SELECT id, content, source, created_at, pinned, memory_type, profile_name FROM memories"
        clauses, params = [], []
        if only_pinned:
            clauses.append("pinned = 1")
        if profile_name:
            clauses.append("profile_name = ?")
            params.append(profile_name)
        if clauses:
            sql += " WHERE " + " AND ".join(clauses)
        sql += " ORDER BY id DESC LIMIT ?"
        params.append(limit)
        return conn.execute(sql, tuple(params)).fetchall()


def delete_memory(memory_id: int):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("DELETE FROM memories WHERE id = ?", (memory_id,))
        conn.commit()


def clear_memories(profile_name: str = ""):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            conn.execute("DELETE FROM memories WHERE profile_name = ?", (profile_name,))
        else:
            conn.execute("DELETE FROM memories")
        conn.commit()


def set_memory_pin(memory_id: int, pinned: bool):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute("UPDATE memories SET pinned = ? WHERE id = ?", (int(pinned), memory_id))
        conn.commit()


def export_memories(profile_name: str = "") -> str:
    rows = load_memories(5000, profile_name=profile_name)
    payload = [
        {"id": rid, "content": content, "source": source, "created_at": created_at,
         "pinned": pinned, "memory_type": memory_type, "profile_name": prof}
        for rid, content, source, created_at, pinned, memory_type, prof in rows
    ]
    return json.dumps(payload, ensure_ascii=False, indent=2)


def import_memories_from_json(text: str, max_items: int = 2000):
    data = json.loads(text)
    if not isinstance(data, list):
        raise ValueError("JSON должен быть списком объектов")
    if len(data) > max_items:
        raise ValueError(f"Слишком много записей: {len(data)} > {max_items}")
    for item in data:
        content = str(item.get("content", "")).strip()[:10000]
        if content:
            add_memory(
                content=content,
                source=str(item.get("source", "import"))[:64],
                pinned=bool(item.get("pinned", False)),
                memory_type=str(item.get("memory_type", "general"))[:32],
                profile_name=str(item.get("profile_name", ""))[:64],
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
    rows = load_memories(2000, profile_name=profile_name)
    if not query.strip():
        return []
    q_words = query.lower().split()
    scored = []
    for _, text, *_ in rows:
        low = text.lower()
        score = sum(2 for w in q_words if w in low)
        if query.lower() in low:
            score += 5
        if score > 0:
            scored.append((score, text))
    scored.sort(reverse=True)
    return [t for _, t in scored[:top_k]]


def semantic_search_memory(query: str, top_k: int = 5, profile_name: str = "") -> List[str]:
    rows = load_memories(1000, profile_name=profile_name)
    texts = [r[1] for r in rows]
    if not query or not texts:
        return []

    # Fallback если нет FAISS/SentenceTransformer
    if not vector_memory_capability_status()["available"]:
        scored = [(sum(1 for w in query.lower().split() if w in t.lower()), t) for t in texts]
        scored.sort(reverse=True)
        return [t for s, t in scored[:top_k] if s > 0]

    model = _get_embedder()
    if model is None:
        return []

    # Инкрементальный кеш: пересоздаём индекс только при изменении данных
    try:
        texts_key = hashlib.md5(("||".join(texts)).encode()).hexdigest()
        global _FAISS_CACHE

        if _FAISS_CACHE.get("key") == texts_key and _FAISS_CACHE.get("index") is not None:
            index = _FAISS_CACHE["index"]
        else:
            emb = np.array(model.encode(texts, normalize_embeddings=True), dtype="float32")
            index = faiss.IndexFlatIP(emb.shape[1])
            index.add(emb)
            _FAISS_CACHE = {"key": texts_key, "index": index}
    except Exception:
        emb = np.array(model.encode(texts, normalize_embeddings=True), dtype="float32")
        index = faiss.IndexFlatIP(emb.shape[1])
        index.add(emb)

    qv = np.array(model.encode([query], normalize_embeddings=True), dtype="float32")
    _, ids = index.search(qv, min(top_k, len(texts)))
    return [texts[i] for i in ids[0] if i != -1]


def _memory_type_weight(memory_type: str, pinned: bool = False, source: str = "") -> float:
    memory_type = (memory_type or "").strip().lower()
    source = (source or "").strip().lower()
    weight_map = {
        "profile": 4.2,
        "pinned": 4.0,
        "insight": 3.4,
        "orchestrator": 3.1,
        "summary": 2.9,
        "file": 2.4,
        "chat_snapshot": 1.8,
        "chat": 1.0,
        "general": 1.3,
    }
    weight = weight_map.get(memory_type, 1.2)
    if pinned:
        weight += 1.2
    if source.startswith("manual"):
        weight += 0.3
    return weight


def _clean_memory_text(text: str, max_chars: int = 900) -> str:
    text = re.sub(r"\s+", " ", (text or "")).strip()
    if len(text) <= max_chars:
        return text
    return text[:max_chars] + " …"


def _memory_query_words(query: str) -> List[str]:
    return [w for w in re.findall(r"[\wа-яА-ЯёЁ-]+", (query or "").lower()) if len(w) >= 3]


def search_memories_weighted(query: str, profile_name: str = "", top_k: int = 8) -> List[Dict[str, Any]]:
    rows = load_memories(2500, profile_name=profile_name)
    if not rows:
        return []

    q = (query or "").strip().lower()
    q_words = _memory_query_words(query)

    semantic_hits = set(semantic_search_memory(query, top_k=max(top_k * 2, 8), profile_name=profile_name)) if q else set()
    keyword_hits = set(keyword_search_memory(query, top_k=max(top_k * 2, 8), profile_name=profile_name)) if q else set()

    scored: List[Dict[str, Any]] = []
    for rid, content, source, created_at, pinned, memory_type, prof in rows:
        text = (content or "").strip()
        if not text:
            continue

        score = _memory_type_weight(memory_type, bool(pinned), source)
        low = text.lower()

        if q:
            if q in low:
                score += 8.0
            score += sum(1.6 for w in q_words if w in low)
            if text in semantic_hits:
                score += 3.0
            if text in keyword_hits:
                score += 2.2

        if (memory_type or "").lower() == "chat" and score < 5:
            score -= 1.4
        if len(text) > 3000:
            score -= 0.5
        if score <= 0:
            continue

        scored.append({
            "id": rid,
            "content": text,
            "source": source,
            "created_at": created_at,
            "pinned": bool(pinned),
            "memory_type": memory_type,
            "profile_name": prof,
            "score": round(score, 3),
        })

    scored.sort(key=lambda x: (x["score"], x["created_at"], x["id"]), reverse=True)

    unique: List[Dict[str, Any]] = []
    seen = set()
    for item in scored:
        key = _content_hash(_clean_memory_text(item["content"], max_chars=400))
        if key in seen:
            continue
        seen.add(key)
        unique.append(item)
        if len(unique) >= top_k:
            break
    return unique


def build_memory_context(query: str, profile_name: str, top_k: int = 5) -> str:
    pinned_rows = load_memories(20, only_pinned=True, profile_name=profile_name)
    weighted = search_memories_weighted(query, profile_name=profile_name, top_k=max(top_k + 3, 8))
    kb_ctx   = build_kb_context(query, profile_name=profile_name, top_k=max(2, top_k // 2))
    tool_ctx = build_tool_memory_context(query, profile_name=profile_name, limit=3)
    weblearn = build_web_learning_context(query, profile_name=profile_name, limit=3)

    parts = []

    if pinned_rows:
        pinned_lines = []
        seen_pinned = set()
        for row in pinned_rows[:6]:
            txt = _clean_memory_text(row[1], max_chars=700)
            h = _content_hash(txt)
            if h in seen_pinned:
                continue
            seen_pinned.add(h)
            pinned_lines.append(f"- {txt}")
        if pinned_lines:
            parts.append("Закреплённая память:\n" + "\n".join(pinned_lines))

    if weighted:
        weighted_lines = []
        pinned_hashes = {_content_hash(_clean_memory_text(r[1], max_chars=700)) for r in pinned_rows[:20]}
        for item in weighted:
            txt = _clean_memory_text(item["content"], max_chars=700)
            h = _content_hash(txt)
            if h in pinned_hashes:
                continue
            tag = (item.get("memory_type") or "general").lower()
            weighted_lines.append(f"- [{tag}] {txt}")
        if weighted_lines:
            parts.append("Релевантная память:\n" + "\n".join(weighted_lines[:max(top_k, 6)]))

    if kb_ctx:
        parts.append(kb_ctx)
    if tool_ctx:
        parts.append(tool_ctx)
    if weblearn:
        parts.append(weblearn)

    context = "\n\n".join(p for p in parts if p.strip())
    return context[:16000]




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

