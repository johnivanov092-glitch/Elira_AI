"""Web-evidence corpus store — SQLite in the APP-DATA layer via the shared
infrastructure connection helper (no second DB layer; standards: SQLite adapters
live in infrastructure).

Schema v3 (John's W1 review + strict quote offsets):
  * PRIMARY KEY (run_id, doc_id) — the SAME page fetched by two runs is two
    independent rows; run B can never replace/steal run A's document (v1 used a
    global doc_id PK + INSERT OR REPLACE and broke run isolation);
  * TTL is enforced on EVERY read path, not only on store;
  * dedup refreshes fetched_at AND last_access (contract §7);
  * a failed schema version → drop & recreate (the corpus is a cache by design).

All functions raise StoreUnavailable on infrastructure failures so callers can
degrade fail-soft (web_fetch falls back to its old no-store path).
"""
from __future__ import annotations

import json
import threading
import time
import uuid
from typing import Any

from app.application.web_evidence.analyzer import ANALYZER_VERSION

_SCHEMA_VERSION = 3

_RUN_MAX_DOCS = 40
_RUN_MAX_BYTES = 15 * 1024 * 1024
_GLOBAL_MAX_DOCS = 200
_GLOBAL_MAX_BYTES = 60 * 1024 * 1024
_TTL_SECONDS = 7 * 24 * 3600

_LOCK = threading.Lock()
_DB_PATH_OVERRIDE: str | None = None   # tests point this at a temp file


class QuotaExceeded(Exception):
    """A store would breach the per-run quota — surfaced to the model as ok=False."""


class StoreUnavailable(Exception):
    """Infrastructure failure (locked/corrupt DB, IO error) — callers degrade
    fail-soft instead of crashing the run."""


def _now() -> float:
    return time.time()


def _db_path() -> str:
    if _DB_PATH_OVERRIDE:
        return _DB_PATH_OVERRIDE
    from app.core.data_files import data_file
    return str(data_file("web_corpus.sqlite3"))


def _connect():
    import sqlite3
    from app.infrastructure.db.connection import connect_sqlite
    try:
        conn = connect_sqlite(_db_path(), row_factory=None)
        conn.execute("PRAGMA foreign_keys=ON")
        ver = int(conn.execute("PRAGMA user_version").fetchone()[0])
        if ver != _SCHEMA_VERSION:
            # The corpus is a CACHE — on schema change we drop & recreate rather
            # than migrate (documents re-fetch on demand; TTL would purge them anyway).
            conn.executescript(
                "DROP TABLE IF EXISTS claim_evidence; DROP TABLE IF EXISTS claims; "
                "DROP TABLE IF EXISTS chunks; DROP TABLE IF EXISTS documents;")
            conn.execute(f"PRAGMA user_version={_SCHEMA_VERSION}")
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS documents (
                run_id TEXT NOT NULL, doc_id TEXT NOT NULL, url TEXT, final_url TEXT,
                fetched_at REAL NOT NULL, last_access REAL NOT NULL, content_hash TEXT NOT NULL,
                mime TEXT, title TEXT, outline TEXT, dates TEXT, tier TEXT,
                trust TEXT NOT NULL DEFAULT 'untrusted', analyzer_ver TEXT NOT NULL,
                nbytes INTEGER NOT NULL, canonical_text TEXT NOT NULL,
                PRIMARY KEY (run_id, doc_id)
            );
            CREATE TABLE IF NOT EXISTS chunks (
                run_id TEXT NOT NULL, doc_id TEXT NOT NULL, chunk_id INTEGER NOT NULL,
                offset INTEGER NOT NULL, length INTEGER NOT NULL, text TEXT NOT NULL,
                PRIMARY KEY (run_id, doc_id, chunk_id),
                FOREIGN KEY (run_id, doc_id) REFERENCES documents(run_id, doc_id) ON DELETE CASCADE
            );
            CREATE INDEX IF NOT EXISTS idx_docs_hash ON documents(run_id, content_hash);
            CREATE TABLE IF NOT EXISTS site_availability (
                target_key TEXT PRIMARY KEY, host TEXT NOT NULL,
                failures INTEGER NOT NULL DEFAULT 0, reason TEXT NOT NULL DEFAULT '',
                retry_at REAL NOT NULL DEFAULT 0, stopped INTEGER NOT NULL DEFAULT 0,
                checked_at REAL NOT NULL DEFAULT 0,
                probe_token TEXT NOT NULL DEFAULT '', probe_until REAL NOT NULL DEFAULT 0
            );
            """
        )
        return conn
    except sqlite3.Error as exc:
        raise StoreUnavailable(f"web corpus store unavailable: {exc}") from exc


def _wrap(fn):
    """Run a store operation under the lock, translating sqlite errors into
    StoreUnavailable (fail-soft contract)."""
    import sqlite3
    with _LOCK:
        conn = _connect()
        try:
            try:
                return fn(conn)
            except (QuotaExceeded, StoreUnavailable):
                raise
            except sqlite3.Error as exc:
                raise StoreUnavailable(f"web corpus store failure: {exc}") from exc
        finally:
            conn.close()


def _expire(conn) -> None:
    conn.execute("DELETE FROM documents WHERE fetched_at < ?", (_now() - _TTL_SECONDS,))


def _evict_global(conn) -> None:
    row = conn.execute("SELECT COUNT(*), COALESCE(SUM(nbytes),0) FROM documents").fetchone()
    n, nbytes = int(row[0]), int(row[1])
    while n > _GLOBAL_MAX_DOCS or nbytes > _GLOBAL_MAX_BYTES:
        victim = conn.execute(
            "SELECT run_id, doc_id, nbytes FROM documents ORDER BY last_access ASC LIMIT 1"
        ).fetchone()
        if not victim:
            break
        conn.execute("DELETE FROM documents WHERE run_id=? AND doc_id=?", (victim[0], victim[1]))
        n -= 1
        nbytes -= int(victim[2])


def store_document(*, run_id: str, doc: dict[str, Any], chunks: list[dict]) -> dict[str, Any]:
    def op(conn):
        _expire(conn)
        dup = conn.execute(
            "SELECT doc_id, final_url, url, dates, tier FROM documents WHERE run_id=? AND content_hash=?",
            (run_id, doc["content_hash"])).fetchone()
        if dup:
            # contract §7: a re-fetch refreshes BOTH timestamps (the page was seen
            # again NOW — TTL restarts; v1 only touched last_access).
            dates = json.loads(dup[3] or "{}")
            tier = dup[4]
            # Identical text from another URL does not change the stored source's
            # publication dates or publisher classification.
            if (doc.get("final_url") or doc.get("url")) == (dup[1] or dup[2]):
                dates.update({key: value for key, value in (doc.get("dates") or {}).items() if value})
                if doc.get("tier") and doc["tier"] != "unknown":
                    tier = doc["tier"]
            conn.execute(
                "UPDATE documents SET last_access=?, fetched_at=?, dates=?, tier=? WHERE run_id=? AND doc_id=?",
                (_now(), _now(), json.dumps(dates, ensure_ascii=False), tier, run_id, dup[0]))
            conn.commit()
            return {"doc_id": dup[0], "deduped": True}

        row = conn.execute(
            "SELECT COUNT(*), COALESCE(SUM(nbytes),0) FROM documents WHERE run_id=?",
            (run_id,)).fetchone()
        if int(row[0]) + 1 > _RUN_MAX_DOCS or int(row[1]) + doc["nbytes"] > _RUN_MAX_BYTES:
            raise QuotaExceeded(
                f"run corpus quota ({_RUN_MAX_DOCS} docs / "
                f"{_RUN_MAX_BYTES // (1024 * 1024)}MB) exceeded")

        ts = _now()
        conn.execute(
            """INSERT INTO documents
               (run_id, doc_id, url, final_url, fetched_at, last_access, content_hash,
                mime, title, outline, dates, tier, trust, analyzer_ver, nbytes, canonical_text)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (run_id, doc["doc_id"], doc.get("url"), doc.get("final_url"), ts, ts,
             doc["content_hash"], doc.get("mime"), doc.get("title"),
             json.dumps(doc.get("outline") or [], ensure_ascii=False),
             json.dumps(doc.get("dates") or {}, ensure_ascii=False),
             doc.get("tier"), "untrusted", ANALYZER_VERSION, doc["nbytes"],
             doc["canonical_text"]),
        )
        conn.executemany(
            "INSERT INTO chunks (run_id, doc_id, chunk_id, offset, length, text) VALUES (?,?,?,?,?,?)",
            [(run_id, doc["doc_id"], c["chunk_id"], c["offset"], c["length"], c["text"])
             for c in chunks],
        )
        _evict_global(conn)
        conn.commit()
        return {"doc_id": doc["doc_id"], "deduped": False}
    return _wrap(op)


def site_access_rows(keys: list[str]) -> dict[str, dict[str, Any]]:
    def op(conn):
        if not keys:
            return {}
        rows = conn.execute(
            "SELECT target_key,failures,reason,retry_at,stopped,checked_at,probe_until "
            "FROM site_availability WHERE target_key IN (" + ",".join("?" for _ in keys) + ")", keys,
        ).fetchall()
        return {r[0]: dict(zip(("failures", "reason", "retry_at", "stopped", "checked_at", "probe_until"), r[1:]))
                for r in rows}
    return _wrap(op)


def begin_site_access(target_key: str, origin_key: str, host: str, *, force: bool = False) -> dict[str, Any]:
    """Claim one due probe atomically; a lookup never increments failure history."""
    def op(conn):
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        rows = conn.execute(
            "SELECT target_key,failures,reason,retry_at,stopped,probe_until FROM site_availability "
            "WHERE target_key IN (?,?)", (target_key, origin_key),
        ).fetchall()
        for row in rows:
            if row[5] > now or (not force and (row[4] or row[3] > now)):
                conn.commit()
                return {"allowed": False, "reason": row[2], "retry_at": row[3],
                        "stopped": bool(row[4]), "probing": row[5] > now}
        # A failed origin is rechecked through the requested URL, never via a
        # scheduled background crawl. Successful reading clears that origin.
        key = next((r[0] for r in rows if r[0] == origin_key and r[1]), target_key)
        token = uuid.uuid4().hex
        conn.execute("INSERT OR IGNORE INTO site_availability(target_key,host) VALUES (?,?)", (key, host))
        conn.execute("UPDATE site_availability SET probe_token=?,probe_until=? WHERE target_key=?",
                     (token, now + 300, key))
        conn.commit()
        return {"allowed": True, "key": key, "target_key": target_key,
                "origin_key": origin_key, "token": token, "host": host, "started_at": now}
    return _wrap(op)


def finish_site_access(probe: dict[str, Any], *, success: bool, reason: str = "",
                       origin_failure: bool = False, retry_after: float = 0) -> None:
    """Keep bounded diagnostic metadata, independent of the seven-day text cache."""
    def op(conn):
        now = _now()
        conn.execute("BEGIN IMMEDIATE")
        current = conn.execute("SELECT probe_token FROM site_availability WHERE target_key=?",
                               (probe["key"],)).fetchone()
        if not current or current[0] != probe["token"]:
            conn.commit()
            return  # Late completion must not overwrite a newer probe.
        conn.execute("UPDATE site_availability SET probe_token='',probe_until=0 WHERE target_key=?", (probe["key"],))
        key = probe["origin_key"] if origin_failure else probe["target_key"]
        if success or (reason.startswith("http_") and not origin_failure):
            # An HTTP response proves the origin is reachable. A missing path
            # after DNS recovery must never quarantine the entire origin.
            conn.execute("UPDATE site_availability SET failures=0,reason='',retry_at=0,stopped=0,checked_at=? "
                         "WHERE target_key=? AND (? OR reason IN ('dns_failure','connection_failure'))",
                         (now, probe["origin_key"], int(success)))
        if success:
            conn.execute("UPDATE site_availability SET failures=0,reason='',retry_at=0,stopped=0,checked_at=? "
                         "WHERE target_key=?", (now, key))
        elif reason:
            conn.execute("INSERT OR IGNORE INTO site_availability(target_key,host) VALUES (?,?)", (key, probe["host"]))
            row = conn.execute("SELECT failures,retry_at,stopped,reason,probe_until,checked_at "
                               "FROM site_availability WHERE target_key=?", (key,)).fetchone()
            # Concurrent failures from different URLs cannot jump through weeks
            # of backoff while an origin is already in its cooldown.
            if key == probe["key"] or not (row[1] > now or row[2] or row[4] > now
                                           or row[5] >= probe["started_at"]):
                previous = int(row[0]) if (reason == "http_429") == (row[3] == "http_429") else 0
                failures = min(5, previous + 1)
                pause = (60, 7 * 86400, 14 * 86400, 28 * 86400, 0)[failures - 1]
                stopped = failures >= 5 and reason != "http_429"
                if reason == "http_429":
                    # Rate limiting is not a dead site; use its declared deadline.
                    pause = max(60, retry_after) if retry_after > 0 else 3600
                conn.execute("UPDATE site_availability SET failures=?,reason=?,retry_at=?,stopped=?,checked_at=? "
                             "WHERE target_key=?", (failures, reason, now + pause, int(stopped), now, key))
        conn.execute("DELETE FROM site_availability WHERE target_key IN (SELECT target_key FROM site_availability "
                     "WHERE probe_until <= ? ORDER BY checked_at DESC LIMIT -1 OFFSET 10000)", (now,))
        conn.commit()
    _wrap(op)


def list_documents(run_id: str) -> list[dict[str, Any]]:
    def op(conn):
        _expire(conn)
        conn.commit()
        rows = conn.execute(
            """SELECT doc_id, url, final_url, content_hash, mime, title, outline, dates,
                      tier, trust, analyzer_ver, nbytes, fetched_at
               FROM documents WHERE run_id=? ORDER BY fetched_at ASC""", (run_id,)).fetchall()
        return [{
            "doc_id": r[0], "url": r[1], "final_url": r[2], "content_hash": r[3],
            "mime": r[4], "title": r[5], "outline": json.loads(r[6] or "[]"),
            "dates": json.loads(r[7] or "{}"), "tier": r[8], "trust": r[9],
            "analyzer_ver": r[10], "nbytes": r[11], "fetched_at": r[12],
        } for r in rows]
    return _wrap(op)


def load_chunks(run_id: str) -> list[dict[str, Any]]:
    def op(conn):
        _expire(conn)   # TTL holds on READ paths too (John's P2: an expired doc
        # must not surface one last time through web_query)
        rows = conn.execute(
            "SELECT doc_id, chunk_id, offset, length, text FROM chunks WHERE run_id=?",
            (run_id,)).fetchall()
        conn.execute("UPDATE documents SET last_access=? WHERE run_id=?", (_now(), run_id))
        conn.commit()
        return [{"doc_id": r[0], "chunk_id": r[1], "offset": r[2],
                 "length": r[3], "text": r[4]} for r in rows]
    return _wrap(op)


def get_document(run_id: str, doc_id: str) -> dict[str, Any] | None:
    def op(conn):
        _expire(conn)
        conn.commit()
        r = conn.execute(
            """SELECT doc_id, run_id, url, final_url, content_hash, mime, title,
                      canonical_text, trust FROM documents WHERE run_id=? AND doc_id=?""",
            (run_id, doc_id)).fetchone()
        return None if not r else {
            "doc_id": r[0], "run_id": r[1], "url": r[2], "final_url": r[3],
            "content_hash": r[4], "mime": r[5], "title": r[6],
            "canonical_text": r[7], "trust": r[8],
        }
    return _wrap(op)


def corpus_texts(run_id: str) -> list[str]:
    """Canonical texts of a run's live documents — the intent-binding taint check
    scans these for verbatim overlap with side-effect tool arguments."""
    def op(conn):
        _expire(conn)
        conn.commit()
        return [r[0] for r in conn.execute(
            "SELECT canonical_text FROM documents WHERE run_id=?", (run_id,)).fetchall()]
    return _wrap(op)


def has_documents(run_id: str) -> bool:
    def op(conn):
        _expire(conn)
        conn.commit()
        return bool(conn.execute(
            "SELECT 1 FROM documents WHERE run_id=? LIMIT 1", (run_id,)).fetchone())
    return _wrap(op)


def cleanup_run(run_id: str) -> int:
    def op(conn):
        cur = conn.execute("DELETE FROM documents WHERE run_id=?", (run_id,))
        conn.commit()
        return cur.rowcount
    return _wrap(op)


def promote_document(run_id: str, doc_id: str) -> dict[str, Any]:
    """Pin a corpus document into the existing Library (contract §2 lifecycle).
    Preserves source=web, URL, content_hash and trust=untrusted — a pinned page is
    persisted DATA, never a trusted instruction."""
    doc = get_document(run_id, doc_id)
    if not doc:
        return {"ok": False, "error": "документ не найден в корпусе этого рана"}
    header = (
        f"[source=web url={doc.get('final_url') or doc.get('url')} "
        f"content_hash={doc['content_hash']} trust=untrusted]\n\n"
    )
    contents = (header + doc["canonical_text"]).encode("utf-8")
    from app.application.library.runtime import add_file_contents
    res = add_file_contents(
        filename=f"web-{doc_id}.txt", contents=contents,
        content_type="text/plain", use_in_context=False, source="web",
    )
    if not res.get("ok"):
        return {"ok": False, "error": str(res.get("error") or "library add failed")}
    return {"ok": True, "library_id": res.get("id"), "source": "web",
            "content_hash": doc["content_hash"], "trust": "untrusted"}
