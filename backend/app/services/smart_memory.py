"""
smart_memory.py — умная память Jarvis.

Возможности:
  • SQLite хранилище (не JSON)
  • Авто-извлечение фактов из чата (имена, числа, предпочтения)
  • Команда "запомни" / "remember"
  • TF-IDF поиск (без внешних зависимостей)
  • Категории: fact, preference, instruction, context
  • Авто-инъекция релевантных воспоминаний в промпт
"""
from __future__ import annotations

import math
import re
import sqlite3
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

DB_PATH = Path("data/smart_memory.db")
DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# ═══════════════════════════════════════════════════════════════
# DATABASE
# ═══════════════════════════════════════════════════════════════

def _conn():
    c = sqlite3.connect(DB_PATH)
    c.row_factory = sqlite3.Row
    return c


def init_memory_db():
    c = _conn()
    c.execute("""
        CREATE TABLE IF NOT EXISTS memories (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            text TEXT NOT NULL,
            category TEXT NOT NULL DEFAULT 'fact',
            source TEXT NOT NULL DEFAULT 'auto',
            importance INTEGER NOT NULL DEFAULT 5,
            access_count INTEGER NOT NULL DEFAULT 0,
            created_at TEXT DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT DEFAULT CURRENT_TIMESTAMP
        )
    """)
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_cat ON memories(category)")
    c.execute("CREATE INDEX IF NOT EXISTS idx_mem_imp ON memories(importance DESC)")
    c.commit()
    c.close()


init_memory_db()


# ═══════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════

def add_memory(text: str, category: str = "fact", source: str = "auto", importance: int = 5) -> dict:
    text = text.strip()
    if not text or len(text) < 3:
        return {"ok": False, "error": "Текст слишком короткий"}

    # Проверка дубликатов (нечёткая)
    existing = search_memory(text, limit=3)
    for item in existing.get("items", []):
        if _similarity(text.lower(), item["text"].lower()) > 0.85:
            # Обновляем importance существующего
            c = _conn()
            try:
                c.execute("UPDATE memories SET importance = MIN(importance + 1, 10), updated_at = CURRENT_TIMESTAMP WHERE id = ?", (item["id"],))
                c.commit()
            finally:
                c.close()
            return {"ok": True, "action": "updated", "id": item["id"], "text": text}

    c = _conn()
    try:
        cur = c.execute(
            "INSERT INTO memories (text, category, source, importance) VALUES (?, ?, ?, ?)",
            (text, category, source, importance)
        )
        mem_id = cur.lastrowid
        c.commit()
    finally:
        c.close()
    return {"ok": True, "action": "created", "id": mem_id, "text": text, "category": category}


def list_memories(category: str = None, limit: int = 50) -> dict:
    c = _conn()
    if category:
        rows = c.execute(
            "SELECT * FROM memories WHERE category = ? ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (category, limit)
        ).fetchall()
    else:
        rows = c.execute(
            "SELECT * FROM memories ORDER BY importance DESC, updated_at DESC LIMIT ?",
            (limit,)
        ).fetchall()
    c.close()
    return {"ok": True, "items": [dict(r) for r in rows], "count": len(rows)}


def delete_memory(mem_id: int) -> dict:
    c = _conn()
    try:
        c.execute("DELETE FROM memories WHERE id = ?", (mem_id,))
        c.commit()
    finally:
        c.close()
    return {"ok": True, "deleted_id": mem_id}


def clear_all_memories() -> dict:
    c = _conn()
    try:
        c.execute("DELETE FROM memories")
        c.commit()
    finally:
        c.close()
    return {"ok": True}


# ═══════════════════════════════════════════════════════════════
# TF-IDF ПОИСК (без зависимостей)
# ═══════════════════════════════════════════════════════════════

_STOP_WORDS = {
    "и", "в", "на", "с", "по", "для", "не", "от", "за", "из", "к", "до",
    "что", "как", "это", "он", "она", "они", "мой", "моя", "моё", "мне",
    "ты", "вы", "я", "мы", "его", "её", "их", "но", "а", "или", "то",
    "the", "a", "an", "is", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did", "will", "would", "could",
    "should", "may", "might", "can", "to", "of", "in", "for", "on", "with",
    "at", "by", "from", "up", "about", "into", "through", "during", "before",
    "after", "and", "but", "or", "if", "then", "than", "that", "this",
}


def _tokenize(text: str) -> list[str]:
    words = re.findall(r"[a-zа-яёA-ZА-ЯЁ0-9]+", text.lower())
    return [w for w in words if w not in _STOP_WORDS and len(w) > 1]


def _similarity(a: str, b: str) -> float:
    """Jaccard similarity между токенами."""
    ta = set(_tokenize(a))
    tb = set(_tokenize(b))
    if not ta or not tb:
        return 0.0
    return len(ta & tb) / len(ta | tb)


def search_memory(query: str, limit: int = 10, min_score: float = 0.1) -> dict:
    """TF-IDF-подобный поиск по памяти."""
    query = (query or "").strip()
    if not query:
        return {"ok": True, "items": [], "count": 0}

    c = _conn()
    all_rows = c.execute("SELECT * FROM memories ORDER BY importance DESC").fetchall()
    c.close()

    if not all_rows:
        return {"ok": True, "items": [], "count": 0}

    query_tokens = _tokenize(query)
    if not query_tokens:
        return {"ok": True, "items": [], "count": 0}

    # Подсчёт IDF
    n_docs = len(all_rows)
    doc_freq: Counter = Counter()
    doc_tokens_list = []
    for row in all_rows:
        tokens = set(_tokenize(row["text"]))
        doc_tokens_list.append(tokens)
        for t in tokens:
            doc_freq[t] += 1

    scored = []
    for i, row in enumerate(all_rows):
        doc_tokens = doc_tokens_list[i]
        score = 0.0
        for qt in query_tokens:
            if qt in doc_tokens:
                idf = math.log(n_docs / (1 + doc_freq.get(qt, 0)))
                score += (1 + idf)

        # Бонус за importance
        score *= (1 + row["importance"] / 20.0)

        if score >= min_score:
            scored.append((score, dict(row)))

    scored.sort(key=lambda x: -x[0])
    items = [item for _, item in scored[:limit]]

    # Обновляем access_count
    if items:
        c = _conn()
        try:
            for item in items:
                c.execute("UPDATE memories SET access_count = access_count + 1 WHERE id = ?", (item["id"],))
            c.commit()
        finally:
            c.close()

    return {"ok": True, "items": items, "count": len(items), "query": query}


# ═══════════════════════════════════════════════════════════════
# АВТО-ИЗВЛЕЧЕНИЕ ФАКТОВ ИЗ ЧАТА
# ═══════════════════════════════════════════════════════════════

# Паттерны для "запомни" команд
_REMEMBER_PATTERNS = [
    r"запомни[,:]?\s+(?:что\s+)?(.+)",
    r"сохрани[,:]?\s+(?:что\s+)?(.+)",
    r"remember[,:]?\s+(?:that\s+)?(.+)",
    r"save[,:]?\s+(?:that\s+)?(.+)",
    r"мой (?:сервер|ip|адрес|номер|пароль|ключ|api).+",
    r"я (?:живу|работаю|учусь|люблю|предпочитаю|использую).+",
    r"меня зовут\s+.+",
    r"my name is\s+.+",
]

# Паттерны для авто-извлечения фактов
_FACT_PATTERNS = [
    (r"(?:мой|моя|моё|мои)\s+((?:сервер|ip|адрес|api|ключ|токен|email|почта|имя|название)\s*(?:—|:|-|это)?\s*.+)", "preference"),
    (r"(?:я\s+(?:живу|работаю|учусь|люблю|предпочитаю|использую))\s+(.+)", "preference"),
    (r"(?:меня зовут|my name is)\s+(.+)", "fact"),
    (r"(?:ip|сервер|server)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
    (r"(?:api.?key|token|ключ)\s*(?:—|:|-|=)\s*(\S+)", "fact"),
]


def is_memory_command(text: str) -> bool:
    text = (text or "").strip().lower()
    if not text:
        return False
    return bool(re.match(r"^(запомни|сохрани|remember|save)", text))


def _classify_memory_text(text: str) -> str:
    t = (text or "").strip().lower()
    if not t:
        return "fact"
    if re.search(r"(для |всегда |никогда |используй|не используй|отвечай|пиши|говори|remember to|always|never)", t):
        return "instruction"
    if re.search(r"(люблю|нравит|предпочита|хочу|нужно|важно|удобно|коротк|подробн|минимализм|новости)", t):
        return "preference"
    return "fact"


def extract_and_save(user_message: str, assistant_message: str = "") -> list[dict]:
    """
    Извлекает факты из сообщения пользователя и сохраняет.
    Возвращает список сохранённых записей.
    """
    saved = []
    text = user_message.strip()
    if not text:
        return saved

    # Проверка: явная команда "запомни"
    for pattern in _REMEMBER_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fact = match.group(1) if match.lastindex else match.group(0)
            fact = fact.strip().rstrip(".")
            if len(fact) > 5:
                result = add_memory(fact, category=_classify_memory_text(fact), source="user_command", importance=8)
                if result.get("ok"):
                    saved.append(result)
                return saved  # Явная команда — не ищем дальше

    # Авто-извлечение фактов
    for pattern, category in _FACT_PATTERNS:
        match = re.search(pattern, text, re.IGNORECASE)
        if match:
            fact = match.group(1) if match.lastindex else match.group(0)
            fact = fact.strip().rstrip(".")
            if len(fact) > 3:
                result = add_memory(fact, category=category, source="auto_extract", importance=6)
                if result.get("ok"):
                    saved.append(result)

    return saved


def get_relevant_context(query: str, max_items: int = 5, max_chars: int = 1500) -> str:
    """
    Возвращает релевантные воспоминания для промпта.
    Форматирует как текст для LLM.
    """
    result = search_memory(query, limit=max_items)
    items = result.get("items", [])
    if not items:
        return ""

    lines = []
    total = 0
    for item in items:
        line = f"- [{item['category']}] {item['text']}"
        if total + len(line) > max_chars:
            break
        lines.append(line)
        total += len(line)

    if not lines:
        return ""

    return "Из памяти Jarvis:\n" + "\n".join(lines)


def get_stats() -> dict:
    """Статистика памяти."""
    c = _conn()
    total = c.execute("SELECT COUNT(*) FROM memories").fetchone()[0]
    by_cat = c.execute("SELECT category, COUNT(*) as cnt FROM memories GROUP BY category").fetchall()
    by_source = c.execute("SELECT source, COUNT(*) as cnt FROM memories GROUP BY source").fetchall()
    c.close()
    return {
        "ok": True,
        "total": total,
        "by_category": {r["category"]: r["cnt"] for r in by_cat},
        "by_source": {r["source"]: r["cnt"] for r in by_source},
    }
