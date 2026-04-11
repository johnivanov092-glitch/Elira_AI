"""
Knowledge base storage and retrieval.

Extracted from core/memory.py -- FAISS-backed knowledge base records
for long-term factual storage with semantic search.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

from app.core.config import DB_PATH


def _conn():
    return sqlite3.connect(str(DB_PATH))


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
