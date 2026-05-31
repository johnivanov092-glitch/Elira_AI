"""Cross-source search backing the Spotlight Cmd+K overlay.

Fans a single query out to:
  - elira_state.db  → chats (title) + messages (content) → results of type "chat"
  - code_agent_sessions.db → sessions (title + turn text) → type "session"
  - rag_memory.db   → embeddings via search_rag → type "rag"
  - library.db      → uploaded files (filename + preview) → type "file"

Each source is querried independently with a strict per-source limit
so a single noisy domain (e.g. a 5K-row RAG with lots of false
positives) can't drown out matches from the others.

The endpoint deliberately does NOT do cross-source ranking — the UI
shows results grouped by source, and within each group the source's
own scoring decides the order. This keeps the contract simple and
makes it obvious to the user where a result came from.
"""
from __future__ import annotations

import json
import sqlite3
from typing import Any


_PER_SOURCE_LIMIT = 5


def _snippet(text: str, query: str, max_chars: int = 140) -> str:
    """Pick a short window around the first occurrence of `query` in
    `text`. Falls back to the head of `text` if the query isn't a
    literal substring (e.g. semantic RAG match)."""
    body = (text or "").strip()
    if not body:
        return ""
    if len(body) <= max_chars:
        return body
    q = (query or "").strip().lower()
    if q:
        idx = body.lower().find(q)
        if idx >= 0:
            start = max(0, idx - max_chars // 4)
            end = min(len(body), start + max_chars)
            prefix = "…" if start > 0 else ""
            suffix = "…" if end < len(body) else ""
            return prefix + body[start:end] + suffix
    return body[:max_chars] + "…"


def _search_chats(query: str) -> list[dict[str, Any]]:
    """Title match OR message-content match. Returns up to
    _PER_SOURCE_LIMIT results, most-recent first. Any DB error
    yields [] instead of bubbling — Spotlight should degrade
    gracefully when one source is broken."""
    try:
        from app.application.elira_memory.service import DB_PATH
    except Exception:
        return []

    pattern = f"%{query.lower()}%"
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        # Title hits
        title_hits = conn.execute(
            """
            SELECT id, title, updated_at
            FROM chats
            WHERE LOWER(COALESCE(title, '')) LIKE ?
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (pattern, _PER_SOURCE_LIMIT),
        ).fetchall()
        title_ids = {row["id"] for row in title_hits}

        # Message-content hits (recent messages only, to keep query fast)
        msg_hits = conn.execute(
            """
            SELECT m.chat_id, m.content, c.title, c.updated_at
            FROM messages m
            JOIN chats c ON c.id = m.chat_id
            WHERE LOWER(m.content) LIKE ?
            ORDER BY m.id DESC
            LIMIT ?
            """,
            (pattern, _PER_SOURCE_LIMIT * 4),  # over-fetch to dedup against title hits
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in title_hits:
        results.append({
            "type": "chat",
            "id": str(row["id"]),
            "title": row["title"] or "(без названия)",
            "snippet": "",
            "updated_at": row["updated_at"],
        })

    seen_chats = set(title_ids)
    for row in msg_hits:
        cid = row["chat_id"]
        if cid in seen_chats:
            continue
        seen_chats.add(cid)
        results.append({
            "type": "chat",
            "id": str(cid),
            "title": row["title"] or "(без названия)",
            "snippet": _snippet(row["content"], query),
            "updated_at": row["updated_at"],
        })
        if len(results) >= _PER_SOURCE_LIMIT:
            break
    return results[:_PER_SOURCE_LIMIT]


def _search_sessions(query: str) -> list[dict[str, Any]]:
    """Code-agent session title match. Turn-content search is also
    folded in by scanning the JSON blob — fine for the typical case
    where sessions have <100 turns. DB error → [] (graceful degrade)."""
    try:
        from app.application.code_agent.sessions import DB_PATH
    except Exception:
        return []

    pattern = f"%{query.lower()}%"
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        # Title hits
        title_hits = conn.execute(
            """
            SELECT id, title, updated_at, turns_json
            FROM sessions
            WHERE LOWER(COALESCE(title, '')) LIKE ?
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (pattern, _PER_SOURCE_LIMIT),
        ).fetchall()
        title_ids = {row["id"] for row in title_hits}

        # Body hits — only on rows not already matched by title
        body_hits = conn.execute(
            """
            SELECT id, title, updated_at, turns_json
            FROM sessions
            WHERE LOWER(turns_json) LIKE ?
            ORDER BY pinned DESC, updated_at DESC
            LIMIT ?
            """,
            (pattern, _PER_SOURCE_LIMIT * 2),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    results: list[dict[str, Any]] = []
    for row in title_hits:
        results.append({
            "type": "session",
            "id": row["id"],
            "title": row["title"] or "(без названия)",
            "snippet": "",
            "updated_at": row["updated_at"],
        })

    for row in body_hits:
        if row["id"] in title_ids:
            continue
        # Find the actual turn that matched so we have a useful snippet
        snippet = ""
        try:
            turns = json.loads(row["turns_json"] or "[]")
            q_lower = query.lower()
            for turn in turns:
                text = (turn.get("text") or "").strip()
                if q_lower in text.lower():
                    snippet = _snippet(text, query)
                    break
            if not snippet and turns:
                # Fall back to last turn's text
                snippet = _snippet((turns[-1].get("text") or ""), query)
        except (json.JSONDecodeError, TypeError, AttributeError):
            snippet = ""
        results.append({
            "type": "session",
            "id": row["id"],
            "title": row["title"] or "(без названия)",
            "snippet": snippet,
            "updated_at": row["updated_at"],
        })
        if len(results) >= _PER_SOURCE_LIMIT:
            break
    return results[:_PER_SOURCE_LIMIT]


def _search_rag(query: str) -> list[dict[str, Any]]:
    """Semantic RAG search via the existing service."""
    try:
        from app.application.rag_memory.service import search_rag as rag_search
    except Exception:
        return []
    hit = rag_search(query=query, limit=_PER_SOURCE_LIMIT, min_score=0.2)
    if not hit.get("ok"):
        return []
    out: list[dict[str, Any]] = []
    for item in hit.get("items", []) or []:
        text = (item.get("text") or "").strip()
        out.append({
            "type": "rag",
            "id": str(item.get("id", "")),
            "title": _snippet(text, query, max_chars=70) or "(пусто)",
            "snippet": _snippet(text, query, max_chars=200),
            "category": item.get("category") or "fact",
            "score": item.get("score"),
        })
    return out


def _search_library(query: str) -> list[dict[str, Any]]:
    """Library file name + preview match.

    library.db stores file metadata with `name` (not filename) plus
    `preview` (extracted text snippet). Both are matched case-
    insensitively. Results are ordered by recency.
    """
    try:
        from app.infrastructure.db.library_db import DB_PATH
    except Exception:
        return []

    pattern = f"%{query.lower()}%"
    try:
        conn = sqlite3.connect(DB_PATH)
    except sqlite3.Error:
        return []
    conn.row_factory = sqlite3.Row
    try:
        rows = conn.execute(
            """
            SELECT id, name, preview, created_at
            FROM files
            WHERE LOWER(name) LIKE ? OR LOWER(COALESCE(preview, '')) LIKE ?
            ORDER BY created_at DESC
            LIMIT ?
            """,
            (pattern, pattern, _PER_SOURCE_LIMIT),
        ).fetchall()
    except sqlite3.Error:
        return []
    finally:
        conn.close()

    return [
        {
            "type": "file",
            "id": str(row["id"]),
            "title": row["name"] or "(без имени)",
            "snippet": _snippet(row["preview"] or "", query),
            "updated_at": row["created_at"],
        }
        for row in rows
    ]


def search_everywhere(query: str) -> dict[str, Any]:
    """Public entrypoint. Always returns the four buckets, possibly empty.

    The grouped shape (instead of one flat ranked list) is intentional:
    the UI renders sources as separate sections with their own icons.
    """
    clean = (query or "").strip()
    if not clean or len(clean) < 2:
        return {"query": clean, "chats": [], "sessions": [], "rag": [], "files": [], "total": 0}

    chats = _search_chats(clean)
    sessions = _search_sessions(clean)
    rag = _search_rag(clean)
    files = _search_library(clean)
    return {
        "query": clean,
        "chats": chats,
        "sessions": sessions,
        "rag": rag,
        "files": files,
        "total": len(chats) + len(sessions) + len(rag) + len(files),
    }
