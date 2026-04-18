"""
Strategy and learning tracking for agent memory.

Extracted from core/memory.py -- V8 strategy usage recording and preferences,
self-improvement run tracking, and web learning run recording and context building.
"""
from __future__ import annotations

import json
from datetime import datetime
from typing import Any, Dict

from app.core.config import DB_PATH
from app.infrastructure.db.connection import connect_sqlite


def _conn():
    return connect_sqlite(DB_PATH, row_factory=None, journal_mode=None)


def record_self_improve_run(
    task_text: str,
    iteration: int,
    answer_text: str,
    critique,
    reflection,
    profile_name: str = "",
):
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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
    with connect_sqlite(DB_PATH, row_factory=None, journal_mode=None) as conn:
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

