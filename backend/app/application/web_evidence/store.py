"""W1 corpus store — full read pages in the app-data layer, NOT the project.

Keyed by (run_id, doc_id). The corpus lives under app-data (contract §7): a
corpus inside the project would become "project facts" via grep/read_file
(rule 20 trusts project tools) — an injection vector, not just untidy.

Lifecycle (contract §7): per-run quota (docs + bytes), a global LRU cap
(unbounded-growth guard), content-hash dedup, TTL, run-scoped cleanup. Every
document carries trust='untrusted' — web content is never an instruction.

SQLite, WAL, fail-soft: a store failure degrades web_fetch to its old
no-store behavior; it must never crash a run.
"""
from __future__ import annotations

import json
import sqlite3
import threading
import time
from typing import Any

from app.application.web_evidence.analyzer import ANALYZER_VERSION

# scope==run for W1 (contract note): true per-scope partitioning is deferred;
# the global cap below is the unbounded-growth guard that actually matters.
_RUN_MAX_DOCS = 40
_RUN_MAX_BYTES = 15 * 1024 * 1024
_GLOBAL_MAX_DOCS = 200
_GLOBAL_MAX_BYTES = 60 * 1024 * 1024
_TTL_SECONDS = 7 * 24 * 3600

_LOCK = threading.Lock()
_DB_PATH_OVERRIDE: str | None = None   # tests point this at a temp file


def _now() -> float:
    return time.time()


def _db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    from app.core.data_files import data_file
    return str(data_file("web_corpus.sqlite3"))


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(_db_path(), timeout=10)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.executescript(
        """
        CREATE TABLE IF NOT EXISTS documents (
            doc_id TEXT PRIMARY KEY, run_id TEXT NOT NULL, url TEXT, final_url TEXT,
            fetched_at REAL NOT NULL, last_access REAL NOT NULL, content_hash TEXT NOT NULL,
            mime TEXT, title TEXT, outline TEXT, dates TEXT, tier TEXT,
            trust TEXT NOT NULL DEFAULT 'untrusted', analyzer_ver TEXT NOT NULL,
            nbytes INTEGER NOT NULL, canonical_text TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            doc_id TEXT NOT NULL, chunk_id INTEGER NOT NULL, offset INTEGER NOT NULL,
            length INTEGER NOT NULL, text TEXT NOT NULL,
            PRIMARY KEY (doc_id, chunk_id),
            FOREIGN KEY (doc_id) REFERENCES documents(doc_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_docs_run ON documents(run_id);
        CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(content_hash);
        """
    )
    return conn


def _expire(conn: sqlite3.Connection) -> None:
    conn.execute("DELETE FROM documents WHERE fetched_at < ?", (_now() - _TTL_SECONDS,))


def _evict_global(conn: sqlite3.Connection) -> None:
    """Global LRU cap across all runs — the unbounded-growth guard."""
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(nbytes),0) FROM documents").fetchone()
    n, nbytes = int(row[0]), int(row[1])
    while n > _GLOBAL_MAX_DOCS or nbytes > _GLOBAL_MAX_BYTES:
        victim = conn.execute(
            "SELECT doc_id, nbytes FROM documents ORDER BY last_access ASC LIMIT 1").fetchone()
        if not victim:
            break
        conn.execute("DELETE FROM documents WHERE doc_id=?", (victim[0],))
        n -= 1
        nbytes -= int(victim[1])


def run_usage(conn: sqlite3.Connection, run_id: str) -> tuple[int, int]:
    row = conn.execute(
        "SELECT COUNT(*), COALESCE(SUM(nbytes),0) FROM documents WHERE run_id=?",
        (run_id,)).fetchone()
    return int(row[0]), int(row[1])


class QuotaExceeded(Exception):
    """Raised when a store would breach the per-run quota — surfaced as ok=False."""


def store_document(*, run_id: str, doc: dict[str, Any], chunks: list[dict]) -> dict[str, Any]:
    """Persist one document + its chunks. Dedup by content_hash within the run
    (returns the existing doc_id, refreshes last_access). Enforces the per-run
    quota BEFORE insert. Returns {doc_id, deduped}."""
    with _LOCK:
        conn = _connect()
        try:
            _expire(conn)
            dup = conn.execute(
                "SELECT doc_id FROM documents WHERE run_id=? AND content_hash=?",
                (run_id, doc["content_hash"])).fetchone()
            if dup:
                conn.execute("UPDATE documents SET last_access=? WHERE doc_id=?", (_now(), dup[0]))
                conn.commit()
                return {"doc_id": dup[0], "deduped": True}

            n, nbytes = run_usage(conn, run_id)
            if n + 1 > _RUN_MAX_DOCS or nbytes + doc["nbytes"] > _RUN_MAX_BYTES:
                raise QuotaExceeded(
                    f"run corpus quota ({_RUN_MAX_DOCS} docs / "
                    f"{_RUN_MAX_BYTES // (1024 * 1024)}MB) exceeded")

            ts = _now()
            conn.execute(
                """INSERT OR REPLACE INTO documents
                   (doc_id, run_id, url, final_url, fetched_at, last_access, content_hash,
                    mime, title, outline, dates, tier, trust, analyzer_ver, nbytes, canonical_text)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (doc["doc_id"], run_id, doc.get("url"), doc.get("final_url"), ts, ts,
                 doc["content_hash"], doc.get("mime"), doc.get("title"),
                 json.dumps(doc.get("outline") or [], ensure_ascii=False),
                 json.dumps(doc.get("dates") or {}, ensure_ascii=False),
                 doc.get("tier"), "untrusted", ANALYZER_VERSION, doc["nbytes"],
                 doc["canonical_text"]),
            )
            conn.execute("DELETE FROM chunks WHERE doc_id=?", (doc["doc_id"],))
            conn.executemany(
                "INSERT INTO chunks (doc_id, chunk_id, offset, length, text) VALUES (?,?,?,?,?)",
                [(doc["doc_id"], c["chunk_id"], c["offset"], c["length"], c["text"]) for c in chunks],
            )
            _evict_global(conn)
            conn.commit()
            return {"doc_id": doc["doc_id"], "deduped": False}
        finally:
            conn.close()


def list_documents(run_id: str) -> list[dict[str, Any]]:
    with _LOCK:
        conn = _connect()
        try:
            _expire(conn)
            rows = conn.execute(
                """SELECT doc_id, url, final_url, content_hash, mime, title, outline, dates,
                          tier, trust, analyzer_ver, nbytes, fetched_at
                   FROM documents WHERE run_id=? ORDER BY fetched_at ASC""", (run_id,)).fetchall()
            conn.commit()
            out = []
            for r in rows:
                out.append({
                    "doc_id": r[0], "url": r[1], "final_url": r[2], "content_hash": r[3],
                    "mime": r[4], "title": r[5], "outline": json.loads(r[6] or "[]"),
                    "dates": json.loads(r[7] or "{}"), "tier": r[8], "trust": r[9],
                    "analyzer_ver": r[10], "nbytes": r[11], "fetched_at": r[12],
                })
            return out
        finally:
            conn.close()


def load_chunks(run_id: str) -> list[dict[str, Any]]:
    """All chunks of a run's documents (for building the BM25 index), with a touch
    of last_access so retrieval keeps a doc alive."""
    with _LOCK:
        conn = _connect()
        try:
            rows = conn.execute(
                """SELECT c.doc_id, c.chunk_id, c.offset, c.length, c.text
                   FROM chunks c JOIN documents d ON d.doc_id=c.doc_id
                   WHERE d.run_id=?""", (run_id,)).fetchall()
            conn.execute("UPDATE documents SET last_access=? WHERE run_id=?", (_now(), run_id))
            conn.commit()
            return [{"doc_id": r[0], "chunk_id": r[1], "offset": r[2],
                     "length": r[3], "text": r[4]} for r in rows]
        finally:
            conn.close()


def get_document(run_id: str, doc_id: str) -> dict[str, Any] | None:
    with _LOCK:
        conn = _connect()
        try:
            r = conn.execute(
                """SELECT doc_id, run_id, url, final_url, content_hash, mime, title,
                          canonical_text FROM documents WHERE run_id=? AND doc_id=?""",
                (run_id, doc_id)).fetchone()
            return None if not r else {
                "doc_id": r[0], "run_id": r[1], "url": r[2], "final_url": r[3],
                "content_hash": r[4], "mime": r[5], "title": r[6], "canonical_text": r[7],
            }
        finally:
            conn.close()


def cleanup_run(run_id: str) -> int:
    """Drop a run's whole corpus (called when the run is deleted). Returns docs removed."""
    with _LOCK:
        conn = _connect()
        try:
            cur = conn.execute("DELETE FROM documents WHERE run_id=?", (run_id,))
            conn.commit()
            return cur.rowcount
        finally:
            conn.close()
