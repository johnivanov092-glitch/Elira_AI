"""
response_cache.py — кэш ответов LLM.

Кэширует похожие вопросы чтобы не гонять модель повторно.
SQLite-хранилище с TTL и fuzzy matching.
"""
import sqlite3
import hashlib
import time
import re
from pathlib import Path

_DB_PATH = Path("data/response_cache.db")
_DB_PATH.parent.mkdir(parents=True, exist_ok=True)

# TTL кэша в секундах (2 часа — баланс между свежестью и экономией)
CACHE_TTL = 7200

# Максимальный размер кэша (записей)
MAX_CACHE_SIZE = 500


def _connect():
    conn = sqlite3.connect(_DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _connect()
    try:
        conn.execute("""
            CREATE TABLE IF NOT EXISTS cache (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                query_hash TEXT NOT NULL UNIQUE,
                query_normalized TEXT NOT NULL,
                model_name TEXT NOT NULL,
                profile_name TEXT NOT NULL,
                response TEXT NOT NULL,
                created_at REAL NOT NULL,
                hit_count INTEGER NOT NULL DEFAULT 0
            )
        """)
        conn.execute("CREATE INDEX IF NOT EXISTS idx_cache_hash ON cache(query_hash)")
        conn.commit()
    finally:
        conn.close()


_init_db()


def _normalize_query(text: str) -> str:
    """Нормализует запрос для сравнения: lowercase, убирает пунктуацию, лишние пробелы."""
    t = text.lower().strip()
    t = re.sub(r'[^\w\s]', ' ', t)  # убираем пунктуацию
    t = re.sub(r'\s+', ' ', t).strip()  # убираем двойные пробелы
    return t


def _query_hash(normalized: str, model: str, profile: str) -> str:
    """SHA-256 хэш от нормализованного запроса + модели + профиля."""
    key = f"{normalized}|{model}|{profile}"
    return hashlib.sha256(key.encode("utf-8")).hexdigest()


def get_cached(query: str, model_name: str, profile_name: str) -> str | None:
    """
    Ищет кэшированный ответ. Возвращает текст ответа или None.
    Автоматически удаляет просроченные записи.
    """
    normalized = _normalize_query(query)
    if len(normalized) < 10:  # слишком короткие запросы не кэшируем
        return None

    qhash = _query_hash(normalized, model_name, profile_name)
    now = time.time()

    conn = _connect()
    try:
        row = conn.execute(
            "SELECT id, response, created_at FROM cache WHERE query_hash = ?",
            (qhash,)
        ).fetchone()

        if not row:
            return None

        # Проверяем TTL
        if now - row["created_at"] > CACHE_TTL:
            conn.execute("DELETE FROM cache WHERE id = ?", (row["id"],))
            conn.commit()
            return None

        # Увеличиваем счётчик попаданий
        conn.execute("UPDATE cache SET hit_count = hit_count + 1 WHERE id = ?", (row["id"],))
        conn.commit()
        return row["response"]
    finally:
        conn.close()


def set_cached(query: str, model_name: str, profile_name: str, response: str):
    """
    Сохраняет ответ в кэш. Автоматически чистит старые записи.
    НЕ кэширует: короткие запросы, пустые ответы, ошибки.
    """
    normalized = _normalize_query(query)
    if len(normalized) < 10 or len(response) < 20:
        return

    # Не кэшируем ответы с ошибками
    if response.startswith("⚠️") or "ошибка" in response.lower()[:50]:
        return

    qhash = _query_hash(normalized, model_name, profile_name)
    now = time.time()

    conn = _connect()
    try:
        # Upsert
        conn.execute("""
            INSERT INTO cache (query_hash, query_normalized, model_name, profile_name, response, created_at, hit_count)
            VALUES (?, ?, ?, ?, ?, ?, 0)
            ON CONFLICT(query_hash) DO UPDATE SET
                response = excluded.response,
                created_at = excluded.created_at,
                hit_count = 0
        """, (qhash, normalized, model_name, profile_name, response, now))

        # Чистим старые записи если кэш переполнен
        count = conn.execute("SELECT COUNT(*) as cnt FROM cache").fetchone()["cnt"]
        if count > MAX_CACHE_SIZE:
            # Удаляем 20% самых старых и наименее используемых
            delete_count = MAX_CACHE_SIZE // 5
            conn.execute("""
                DELETE FROM cache WHERE id IN (
                    SELECT id FROM cache ORDER BY hit_count ASC, created_at ASC LIMIT ?
                )
            """, (delete_count,))

        conn.commit()
    finally:
        conn.close()


def should_cache(query: str, route: str) -> bool:
    """
    Решает нужно ли кэшировать этот запрос.
    НЕ кэшируем: команды памяти, проектные задачи (они зависят от состояния файлов),
    очень персональные запросы.
    """
    q = query.lower()

    # Не кэшируем команды памяти
    if any(w in q for w in ("запомни", "забудь", "сохрани в память", "удали из памяти")):
        return False

    # Не кэшируем проектные задачи (зависят от текущего состояния файлов)
    if route in ("project", "code"):
        return False

    # Не кэшируем вопросы про "сейчас", "сегодня" (данные устаревают)
    if any(w in q for w in ("сейчас", "сегодня", "прямо сейчас", "только что", "right now")):
        return False

    return True


def clear_cache():
    """Полная очистка кэша."""
    conn = _connect()
    try:
        conn.execute("DELETE FROM cache")
        conn.commit()
    finally:
        conn.close()


def cache_stats() -> dict:
    """Статистика кэша."""
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) as cnt FROM cache").fetchone()["cnt"]
        hits = conn.execute("SELECT SUM(hit_count) as total_hits FROM cache").fetchone()["total_hits"] or 0
        return {"total_entries": total, "total_hits": hits, "max_size": MAX_CACHE_SIZE, "ttl_seconds": CACHE_TTL}
    finally:
        conn.close()
