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
    import numpy as np
except Exception:
    faiss = None
    np = None


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
def record_tool_usage(
    tool_name: str,
    task_hint: str,
    ok: bool,
    score: float = 1.0,
    notes: str = "",
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            "INSERT INTO tool_usage (tool_name, task_hint, ok, score, notes, created_at, profile_name) VALUES (?, ?, ?, ?, ?, ?, ?)",
            (
                (tool_name or "").strip()[:64],
                (task_hint or "").strip()[:300],
                int(bool(ok)),
                float(score),
                (notes or "").strip()[:1000],
                datetime.now().isoformat(timespec="seconds"),
                (profile_name or "").strip()[:64],
            ),
        )
        conn.commit()


def get_tool_preferences(task_hint: str = "", profile_name: str = "", limit: int = 5) -> List[Dict[str, Any]]:
    q_words = [w for w in (task_hint or "").lower().split() if len(w) >= 3]
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT tool_name, task_hint, ok, score, notes, created_at FROM tool_usage WHERE (? = '' OR profile_name = ?) ORDER BY id DESC LIMIT 200",
            (profile_name, profile_name),
        ).fetchall()

    stats: Dict[str, Dict[str, Any]] = {}
    for tool_name, th, ok, score, notes, created_at in rows:
        bag = (th or "").lower()
        relevance = 1.0 + sum(1 for w in q_words if w in bag) * 0.5 if q_words else 1.0
        s = stats.setdefault(tool_name, {"tool": tool_name, "score": 0.0, "runs": 0, "success": 0, "notes": []})
        s["score"] += float(score or 0) * relevance
        s["runs"] += 1
        s["success"] += int(bool(ok))
        if notes and len(s["notes"]) < 2:
            s["notes"].append(notes)

    ranked = sorted(stats.values(), key=lambda x: (x["score"], x["success"], -x["runs"]), reverse=True)
    return ranked[:limit]


def build_tool_memory_context(task_hint: str, profile_name: str = "", limit: int = 4) -> str:
    prefs = get_tool_preferences(task_hint, profile_name=profile_name, limit=limit)
    if not prefs:
        return ""
    lines = []
    for p in prefs:
        ratio = f"{p['success']}/{p['runs']}"
        note = f" · заметки: {' | '.join(p['notes'])}" if p["notes"] else ""
        lines.append(f"- {p['tool']} · успех {ratio} · score {p['score']:.1f}{note}")
    return "Предпочтения инструментов по прошлому опыту:\n" + "\n".join(lines)


# ── Persistent knowledge base ─────────────────────────────────────────────────
def add_kb_record(
    content: str,
    title: str = "",
    url: str = "",
    source: str = "manual",
    chunk_type: str = "note",
    profile_name: str = "",
    deduplicate: bool = True,
) -> bool:
    content = (content or "").strip()
    if not content:
        return False
    h = _content_hash(f"{title}\n{url}\n{content}")
    with sqlite3.connect(DB_PATH) as conn:
        if deduplicate:
            existing = conn.execute(
                "SELECT id FROM knowledge_chunks WHERE content_hash = ? AND profile_name = ? LIMIT 1",
                (h, profile_name),
            ).fetchone()
            if existing:
                return False
        conn.execute(
            "INSERT INTO knowledge_chunks (title, url, content, source, chunk_type, created_at, profile_name, content_hash) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (
                (title or "").strip()[:300],
                (url or "").strip()[:1000],
                content[:12000],
                (source or "").strip()[:64],
                (chunk_type or "note").strip()[:32],
                datetime.now().isoformat(timespec="seconds"),
                (profile_name or "").strip()[:64],
                h,
            ),
        )
        conn.commit()
    return True


def search_kb(query: str, top_k: int = 5, profile_name: str = "") -> List[Dict[str, Any]]:
    if not (query or "").strip():
        return []
    q_words = [w for w in query.lower().split() if len(w) >= 2]
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            "SELECT title, url, content, source, chunk_type, created_at FROM knowledge_chunks WHERE (? = '' OR profile_name = ?) ORDER BY id DESC LIMIT 1000",
            (profile_name, profile_name),
        ).fetchall()

    scored = []
    for title, url, content, source, chunk_type, created_at in rows:
        bag = f"{title}\n{url}\n{content}".lower()
        score = sum(2 for w in q_words if w in bag)
        if query.lower() in bag:
            score += 5
        if score > 0:
            scored.append({
                "title": title,
                "url": url,
                "content": content,
                "source": source,
                "chunk_type": chunk_type,
                "created_at": created_at,
                "score": score,
            })
    scored.sort(key=lambda x: (x["score"], x["created_at"]), reverse=True)
    return scored[:top_k]


def build_kb_context(query: str, profile_name: str = "", top_k: int = 4) -> str:
    hits = search_kb(query, top_k=top_k, profile_name=profile_name)
    if not hits:
        return ""
    parts = []
    for h in hits:
        header = h["title"] or h["url"] or h["source"]
        parts.append(f"- {header}\n{h['content'][:1500]}")
    return "Знания из persistent knowledge base:\n" + "\n\n".join(parts)


def get_kb_stats(profile_name: str = "") -> Dict[str, int]:
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            total = conn.execute("SELECT COUNT(*) FROM knowledge_chunks WHERE profile_name = ?", (profile_name,)).fetchone()[0]
        else:
            total = conn.execute("SELECT COUNT(*) FROM knowledge_chunks").fetchone()[0]
    return {"chunks": int(total)}

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
    if SentenceTransformer is None or faiss is None or np is None:
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



def record_task_run(
    task_text: str,
    route_mode: str,
    graph_used: str,
    final_status: str,
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO task_runs (profile_name, task_text, route_mode, graph_used, final_status)
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                (profile_name or "")[:64],
                (task_text or "")[:4000],
                (route_mode or "")[:64],
                (graph_used or "")[:2000],
                (final_status or "")[:64],
            ),
        )
        conn.commit()


def record_reflection(
    task_text: str,
    answer_text: str,
    reflection: dict,
    profile_name: str = "",
):
    reflection = reflection or {}
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO reflections (
                profile_name, task_text, answer_text,
                answered, grounded, complete, actionable, safe, notes
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (profile_name or "")[:64],
                (task_text or "")[:4000],
                (answer_text or "")[:12000],
                int(bool(reflection.get("answered", False))),
                int(bool(reflection.get("grounded", False))),
                int(bool(reflection.get("complete", False))),
                int(bool(reflection.get("actionable", False))),
                int(bool(reflection.get("safe", True))),
                str(reflection.get("notes", "") or "")[:4000],
            ),
        )
        conn.commit()


def get_recent_task_runs(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, task_text, route_mode, graph_used, final_status, created_at
                FROM task_runs
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, task_text, route_mode, graph_used, final_status, created_at
                FROM task_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "id": r[0],
            "task_text": r[1],
            "route_mode": r[2],
            "graph_used": r[3],
            "final_status": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


def get_recent_reflections(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, task_text, answered, grounded, complete, actionable, safe, notes, created_at
                FROM reflections
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, task_text, answered, grounded, complete, actionable, safe, notes, created_at
                FROM reflections
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "id": r[0],
            "task_text": r[1],
            "answered": bool(r[2]),
            "grounded": bool(r[3]),
            "complete": bool(r[4]),
            "actionable": bool(r[5]),
            "safe": bool(r[6]),
            "notes": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]



def add_working_memory(
    run_id: str,
    step_name: str,
    fact_type: str,
    content: str,
    score: float = 1.0,
    profile_name: str = "",
) -> bool:
    run_id = (run_id or "").strip()
    content = (content or "").strip()
    if not run_id or not content:
        return False
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO working_memory (run_id, profile_name, step_name, fact_type, content, score)
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                run_id[:80],
                (profile_name or "").strip()[:64],
                (step_name or "").strip()[:64],
                (fact_type or "").strip()[:32],
                content[:12000],
                float(score or 0.0),
            ),
        )
        conn.commit()
    return True


def get_working_memory(run_id: str, profile_name: str = "", limit: int = 50) -> List[Dict[str, Any]]:
    run_id = (run_id or "").strip()
    if not run_id:
        return []
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, run_id, step_name, fact_type, content, score, created_at
                FROM working_memory
                WHERE run_id = ? AND profile_name = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (run_id, profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, run_id, step_name, fact_type, content, score, created_at
                FROM working_memory
                WHERE run_id = ?
                ORDER BY id ASC
                LIMIT ?
                """,
                (run_id, limit),
            ).fetchall()
    return [
        {
            "id": r[0],
            "run_id": r[1],
            "step_name": r[2],
            "fact_type": r[3],
            "content": r[4],
            "score": float(r[5] or 0.0),
            "created_at": r[6],
        }
        for r in rows
    ]


def build_working_memory_context(run_id: str, profile_name: str = "", limit: int = 12) -> str:
    items = get_working_memory(run_id, profile_name=profile_name, limit=100)
    if not items:
        return ""
    rank = {"goal": 5, "constraint": 4, "decision": 4, "finding": 3, "source": 2, "error": 1}
    items = sorted(
        items,
        key=lambda x: (rank.get(x.get("fact_type", ""), 0), x.get("score", 0.0), x.get("id", 0)),
        reverse=True,
    )[:limit]
    lines = []
    for item in items:
        kind = item.get("fact_type", "note")
        step = item.get("step_name", "")
        text = (item.get("content", "") or "").strip()
        if not text:
            continue
        lines.append(f"- [{kind} · {step}] {text[:700]}")
    return "Рабочая память текущего запуска:\n" + "\n".join(lines)


def clear_working_memory(run_id: str = "", profile_name: str = "") -> int:
    with sqlite3.connect(DB_PATH) as conn:
        if run_id and profile_name:
            cur = conn.execute(
                "DELETE FROM working_memory WHERE run_id = ? AND profile_name = ?",
                (run_id, profile_name),
            )
        elif run_id:
            cur = conn.execute("DELETE FROM working_memory WHERE run_id = ?", (run_id,))
        elif profile_name:
            cur = conn.execute("DELETE FROM working_memory WHERE profile_name = ?", (profile_name,))
        else:
            cur = conn.execute("DELETE FROM working_memory")
        conn.commit()
        return cur.rowcount or 0


def get_recent_working_memory_runs(profile_name: str = "", limit: int = 12) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT run_id, MIN(created_at) as started_at, MAX(created_at) as last_at, COUNT(*) as items
                FROM working_memory
                WHERE profile_name = ?
                GROUP BY run_id
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT run_id, MIN(created_at) as started_at, MAX(created_at) as last_at, COUNT(*) as items
                FROM working_memory
                GROUP BY run_id
                ORDER BY last_at DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [
        {
            "run_id": r[0],
            "started_at": r[1],
            "last_at": r[2],
            "items": r[3],
        }
        for r in rows
    ]


def record_self_improve_run(
    task_text: str,
    iteration: int,
    answer_text: str,
    critique,
    reflection,
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO self_improve_runs (
                profile_name, task_text, iteration, answer_text, critique_json, reflection_json
            )
            VALUES (?, ?, ?, ?, ?, ?)
            """,
            (
                (profile_name or "")[:64],
                (task_text or "")[:4000],
                int(iteration or 0),
                (answer_text or "")[:12000],
                json.dumps(critique or {}, ensure_ascii=False)[:4000],
                json.dumps(reflection or {}, ensure_ascii=False)[:4000],
            ),
        )
        conn.commit()


def get_recent_self_improve_runs(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, task_text, iteration, critique_json, reflection_json, created_at
                FROM self_improve_runs
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, task_text, iteration, critique_json, reflection_json, created_at
                FROM self_improve_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [
        {
            "id": r[0],
            "task_text": r[1],
            "iteration": r[2],
            "critique_json": r[3],
            "reflection_json": r[4],
            "created_at": r[5],
        }
        for r in rows
    ]


# ── V8 Learning Router ────────────────────────────────────────────────────────
def record_v8_strategy_usage(
    strategy: str,
    route_mode: str,
    task_hint: str,
    ok: bool,
    score: float = 1.0,
    latency: float = 0.0,
    notes: str = "",
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO v8_strategy_usage (
                strategy, route_mode, task_hint, ok, score, latency, notes, created_at, profile_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (strategy or "").strip()[:64],
                (route_mode or "").strip()[:64],
                (task_hint or "").strip()[:500],
                int(bool(ok)),
                float(score),
                float(latency or 0.0),
                (notes or "").strip()[:1000],
                datetime.now().isoformat(timespec="seconds"),
                (profile_name or "").strip()[:64],
            ),
        )
        conn.commit()


def get_v8_strategy_preferences(task_hint: str = "", profile_name: str = "", limit: int = 5):
    q_words = [w for w in re.findall(r"[\wа-яА-ЯёЁ-]+", (task_hint or "").lower()) if len(w) >= 3]
    with sqlite3.connect(DB_PATH) as conn:
        rows = conn.execute(
            """
            SELECT strategy, route_mode, task_hint, ok, score, latency, notes, created_at
            FROM v8_strategy_usage
            WHERE (? = '' OR profile_name = ?)
            ORDER BY id DESC
            LIMIT 400
            """,
            (profile_name, profile_name),
        ).fetchall()

    stats = {}
    for strategy, route_mode, hint, ok, score, latency, notes, created_at in rows:
        bag = (hint or "").lower()
        relevance = 1.0 + sum(1 for w in q_words if w in bag) * 0.4 if q_words else 1.0
        s = stats.setdefault(strategy, {
            "strategy": strategy,
            "route_mode": route_mode,
            "score": 0.0,
            "runs": 0,
            "success": 0,
            "latency_sum": 0.0,
            "notes": [],
        })
        s["score"] += float(score or 0.0) * relevance
        s["runs"] += 1
        s["success"] += int(bool(ok))
        s["latency_sum"] += float(latency or 0.0)
        if notes and len(s["notes"]) < 2:
            s["notes"].append(str(notes))

    ranked = []
    for item in stats.values():
        runs = max(int(item["runs"]), 1)
        item["success_rate"] = round(float(item["success"]) / runs, 2)
        item["avg_latency"] = round(float(item["latency_sum"]) / runs, 3)
        ranked.append(item)

    ranked.sort(
        key=lambda x: (x["success_rate"], x["score"], -x["avg_latency"], x["runs"]),
        reverse=True,
    )
    return ranked[:limit]


def build_v8_strategy_context(task_hint: str, profile_name: str = "", limit: int = 4) -> str:
    prefs = get_v8_strategy_preferences(task_hint, profile_name=profile_name, limit=limit)
    if not prefs:
        return ""
    lines = []
    for p in prefs:
        note = f" · notes: {' | '.join(p['notes'])}" if p.get("notes") else ""
        lines.append(
            f"- {p['strategy']} · success {p['success_rate']} · runs {p['runs']} · latency {p['avg_latency']}s{note}"
        )
    return "Предпочтения V8 strategy router:\n" + "\n".join(lines)


def get_recent_v8_strategy_runs(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, strategy, route_mode, task_hint, ok, score, latency, notes, created_at
                FROM v8_strategy_usage
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, strategy, route_mode, task_hint, ok, score, latency, notes, created_at
                FROM v8_strategy_usage
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()

    return [
        {
            "id": r[0],
            "strategy": r[1],
            "route_mode": r[2],
            "task_hint": r[3],
            "ok": bool(r[4]),
            "score": float(r[5] or 0.0),
            "latency": float(r[6] or 0.0),
            "notes": r[7],
            "created_at": r[8],
        }
        for r in rows
    ]


# ── Web Knowledge Learning ────────────────────────────────────────────────────
def record_web_learning_run(
    query: str,
    url: str = "",
    title: str = "",
    source_kind: str = "web",
    ok: bool = True,
    saved_kb: int = 0,
    saved_memory: int = 0,
    notes: str = "",
    profile_name: str = "",
):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO web_learning_runs (
                query, url, title, source_kind, ok, saved_kb, saved_memory, notes, created_at, profile_name
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                (query or "")[:2000],
                (url or "")[:1000],
                (title or "")[:300],
                (source_kind or "web")[:64],
                int(bool(ok)),
                int(saved_kb or 0),
                int(saved_memory or 0),
                (notes or "")[:2000],
                datetime.now().isoformat(timespec="seconds"),
                (profile_name or "")[:64],
            ),
        )
        conn.commit()


def get_recent_web_learning_runs(profile_name: str = "", limit: int = 20):
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, query, url, title, source_kind, ok, saved_kb, saved_memory, notes, created_at
                FROM web_learning_runs
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, query, url, title, source_kind, ok, saved_kb, saved_memory, notes, created_at
                FROM web_learning_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [
        {
            "id": r[0],
            "query": r[1],
            "url": r[2],
            "title": r[3],
            "source_kind": r[4],
            "ok": bool(r[5]),
            "saved_kb": int(r[6] or 0),
            "saved_memory": int(r[7] or 0),
            "notes": r[8],
            "created_at": r[9],
        }
        for r in rows
    ]


def build_web_learning_context(query: str, profile_name: str = "", limit: int = 4) -> str:
    q_words = [w for w in re.findall(r"\w+", (query or "").lower()) if len(w) >= 3]
    rows = get_recent_web_learning_runs(profile_name=profile_name, limit=80)
    scored = []
    for row in rows:
        bag = f"{row.get('query','')} {row.get('title','')} {row.get('notes','')} {row.get('url','')}".lower()
        score = sum(1 for w in q_words if w in bag)
        if score > 0:
            scored.append((score, row))
    scored.sort(key=lambda x: (x[0], x[1].get("created_at", "")), reverse=True)
    chosen = [row for _, row in scored[:limit]]
    if not chosen:
        return ""
    lines = []
    for row in chosen:
        title = row.get("title") or row.get("url") or row.get("source_kind") or "web"
        lines.append(
            f"- {title} · ok={row.get('ok')} · KB={row.get('saved_kb',0)} · MEM={row.get('saved_memory',0)}\n"
            f"  query: {row.get('query','')[:180]}"
        )
    return "История web knowledge learning:\n" + "\n".join(lines)


# ── Compaction Layer ──────────────────────────────────────────────────────────
_STOPWORDS_RU = {
    "это","как","что","для","или","при","под","над","без","есть","было","быть","если","чтобы",
    "когда","потом","тогда","только","очень","также","этого","того","который","которая","которые",
    "сейчас","здесь","пока","теперь","нужно","можно","будет","были","после","перед","между","через",
    "про","надо","ещё","уже","всё","всем","всех","тут","там","где","какой","какая","какие","какое",
    "user","assistant"
}
_STOPWORDS_EN = {
    "this","that","with","from","have","will","would","about","there","their","them","they","into",
    "your","just","than","then","what","when","where","which","while","should","could","also","very",
    "been","were","here","some","more","most","such","each","other","into","user","assistant"
}


def _extract_memory_topics(texts: List[str], limit: int = 8) -> List[str]:
    words = []
    for text in texts:
        for w in re.findall(r"[\wа-яА-ЯёЁ-]+", (text or "").lower()):
            if len(w) < 4:
                continue
            if w in _STOPWORDS_RU or w in _STOPWORDS_EN:
                continue
            words.append(w)
    freq = {}
    for w in words:
        freq[w] = freq.get(w, 0) + 1
    ranked = sorted(freq.items(), key=lambda x: (-x[1], x[0]))
    return [w for w, _ in ranked[:limit]]


def _build_compaction_summary(rows: List[tuple]) -> str:
    raw_texts = []
    snippets = []
    for _, content, source, created_at, pinned, memory_type, profile_name in rows:
        txt = _clean_memory_text(content, max_chars=260)
        if not txt:
            continue
        raw_texts.append(content or "")
        prefix = memory_type or "memory"
        snippets.append(f"- [{prefix}] {txt}")
    topics = _extract_memory_topics(raw_texts, limit=8)
    header = f"Сводка памяти (компакция {len(rows)} записей)"
    lines = [header]
    if topics:
        lines.append("Темы: " + ", ".join(topics))
    lines.append("Ключевые фрагменты:")
    lines.extend(snippets[:12])
    return "\n".join(lines)


def record_memory_compaction_run(profile_name: str = "", source_count: int = 0,
                                 summary_count: int = 0, deleted_count: int = 0,
                                 notes: str = ""):
    with sqlite3.connect(DB_PATH) as conn:
        conn.execute(
            """
            INSERT INTO memory_compaction_runs
            (profile_name, source_count, summary_count, deleted_count, notes)
            VALUES (?, ?, ?, ?, ?)
            """,
            ((profile_name or "")[:64], int(source_count), int(summary_count), int(deleted_count), (notes or "")[:2000]),
        )
        conn.commit()


def get_recent_memory_compaction_runs(profile_name: str = "", limit: int = 20) -> List[Dict[str, Any]]:
    with sqlite3.connect(DB_PATH) as conn:
        if profile_name:
            rows = conn.execute(
                """
                SELECT id, profile_name, source_count, summary_count, deleted_count, notes, created_at
                FROM memory_compaction_runs
                WHERE profile_name = ?
                ORDER BY id DESC
                LIMIT ?
                """,
                (profile_name, limit),
            ).fetchall()
        else:
            rows = conn.execute(
                """
                SELECT id, profile_name, source_count, summary_count, deleted_count, notes, created_at
                FROM memory_compaction_runs
                ORDER BY id DESC
                LIMIT ?
                """,
                (limit,),
            ).fetchall()
    return [
        {
            "id": r[0],
            "profile_name": r[1],
            "source_count": int(r[2] or 0),
            "summary_count": int(r[3] or 0),
            "deleted_count": int(r[4] or 0),
            "notes": r[5] or "",
            "created_at": r[6],
        }
        for r in rows
    ]


def compact_memory(profile_name: str = "", keep_recent: int = 120, chunk_size: int = 18,
                   dry_run: bool = False) -> Dict[str, Any]:
    rows = load_memories(5000, profile_name=profile_name)
    candidates = []
    protected_types = {"profile", "pinned", "insight", "summary", "file", "orchestrator"}
    for row in rows:
        rid, content, source, created_at, pinned, memory_type, prof = row
        mt = (memory_type or "").lower()
        if pinned or mt in protected_types:
            continue
        if source == "compaction":
            continue
        if mt not in {"chat", "chat_snapshot", "general"}:
            continue
        candidates.append(row)

    # rows are newest first; compact only older part
    older = list(reversed(candidates[keep_recent:]))
    if not older:
        result = {"source_count": 0, "summary_count": 0, "deleted_count": 0, "notes": "Нет подходящих записей"}
        record_memory_compaction_run(profile_name, 0, 0, 0, result["notes"])
        return result

    groups = [older[i:i + max(6, chunk_size)] for i in range(0, len(older), max(6, chunk_size))]
    summary_count = 0
    deleted_count = 0
    source_count = 0

    for group in groups:
        summary = _build_compaction_summary(group)
        source_count += len(group)
        if not dry_run:
            added = add_memory(
                summary,
                source="compaction",
                pinned=False,
                memory_type="summary",
                profile_name=profile_name,
                deduplicate=False,
            )
            if added:
                summary_count += 1
            with sqlite3.connect(DB_PATH) as conn:
                conn.executemany("DELETE FROM memories WHERE id = ?", [(row[0],) for row in group])
                conn.commit()
            deleted_count += len(group)
        else:
            summary_count += 1
            deleted_count += len(group)

    notes = f"Компакция выполнена: исходных={source_count}, summary={summary_count}, удалено={deleted_count}, dry_run={dry_run}"
    record_memory_compaction_run(profile_name, source_count, summary_count, deleted_count, notes)
    return {
        "source_count": source_count,
        "summary_count": summary_count,
        "deleted_count": deleted_count,
        "notes": notes,
        "dry_run": dry_run,
    }
