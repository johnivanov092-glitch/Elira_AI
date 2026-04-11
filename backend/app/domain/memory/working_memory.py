"""
Working memory and memory compaction.

Extracted from core/memory.py -- short-lived working memory for multi-step tasks,
memory compaction (summarization and dedup), and related run tracking.
"""
from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from typing import Any, Dict, List

from app.core.config import DB_PATH


def _conn():
    return sqlite3.connect(str(DB_PATH))


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
