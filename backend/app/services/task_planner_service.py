"""
task_planner_service.py — Планировщик задач Elira AI.

Персональный todo/task manager с приоритетами, дедлайнами, категориями.
AI может создавать задачи из чата ("запомни задачу", "добавь в план").
"""
from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from datetime import datetime

from app.core.config import DATA_DIR

logger = logging.getLogger(__name__)

DB_PATH = DATA_DIR / "task_planner.db"
DB_PATH.parent.mkdir(parents=True, exist_ok=True)


def _connect():
    conn = sqlite3.connect(str(DB_PATH))
    conn.row_factory = sqlite3.Row
    return conn


def _init_db():
    conn = _connect()
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id TEXT PRIMARY KEY,
                title TEXT NOT NULL,
                description TEXT DEFAULT '',
                category TEXT DEFAULT 'general',
                priority TEXT DEFAULT 'medium',
                status TEXT DEFAULT 'todo',
                due_date TEXT,
                tags TEXT DEFAULT '[]',
                created_at TEXT NOT NULL,
                updated_at TEXT,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_tasks_status ON tasks(status);
            CREATE INDEX IF NOT EXISTS idx_tasks_priority ON tasks(priority);
        """)
        conn.commit()
    finally:
        conn.close()


_init_db()

PRIORITIES = ["low", "medium", "high", "urgent"]
STATUSES = ["todo", "in_progress", "done", "cancelled"]


# ═══════════════════════════════════════════════════════════════
# CRUD
# ═══════════════════════════════════════════════════════════════

def create_task(
    title: str,
    description: str = "",
    category: str = "general",
    priority: str = "medium",
    due_date: str | None = None,
    tags: list[str] | None = None,
) -> dict:
    tid = str(uuid.uuid4())[:8]
    now = datetime.utcnow().isoformat()
    conn = _connect()
    try:
        conn.execute(
            "INSERT INTO tasks (id, title, description, category, priority, due_date, tags, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, title, description, category, priority or "medium", due_date, json.dumps(tags or []), now),
        )
        conn.commit()
        return {"ok": True, "id": tid, "title": title}
    finally:
        conn.close()


def list_tasks(status: str | None = None, category: str | None = None, limit: int = 100) -> dict:
    conn = _connect()
    try:
        q = "SELECT * FROM tasks"
        params = []
        wheres = []
        if status:
            wheres.append("status = ?")
            params.append(status)
        if category:
            wheres.append("category = ?")
            params.append(category)
        if wheres:
            q += " WHERE " + " AND ".join(wheres)

        # Сортировка: urgent→high→medium→low, потом по дате
        q += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"
        q += " LIMIT ?"
        params.append(limit)

        rows = conn.execute(q, params).fetchall()
        items = []
        for r in rows:
            d = dict(r)
            try:
                d["tags"] = json.loads(d.get("tags") or "[]")
            except Exception:
                d["tags"] = []
            items.append(d)
        return {"ok": True, "tasks": items, "count": len(items)}
    finally:
        conn.close()


def get_task(tid: str) -> dict:
    conn = _connect()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Задача не найдена"}
        d = dict(row)
        try:
            d["tags"] = json.loads(d.get("tags") or "[]")
        except Exception:
            d["tags"] = []
        return {"ok": True, **d}
    finally:
        conn.close()


def update_task(tid: str, **kwargs) -> dict:
    allowed = {"title", "description", "category", "priority", "status", "due_date", "tags"}
    updates = ["updated_at = ?"]
    values = [datetime.utcnow().isoformat()]

    for k, v in kwargs.items():
        if k not in allowed:
            continue
        if k == "tags" and isinstance(v, list):
            v = json.dumps(v)
        updates.append(f"{k} = ?")
        values.append(v)

    # Если статус → done, ставим completed_at
    if kwargs.get("status") == "done":
        updates.append("completed_at = ?")
        values.append(datetime.utcnow().isoformat())

    conn = _connect()
    try:
        values.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()
        return {"ok": True, "id": tid}
    finally:
        conn.close()


def delete_task(tid: str) -> dict:
    conn = _connect()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        return {"ok": True, "deleted": tid}
    finally:
        conn.close()


# ═══════════════════════════════════════════════════════════════
# СТАТИСТИКА
# ═══════════════════════════════════════════════════════════════

def task_stats() -> dict:
    conn = _connect()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        by_status = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]
        by_priority = {}
        for row in conn.execute("SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' AND status != 'cancelled' GROUP BY priority").fetchall():
            by_priority[row["priority"]] = row["cnt"]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date < ?",
            (datetime.utcnow().isoformat(),)
        ).fetchone()[0]
        return {"ok": True, "total": total, "by_status": by_status, "by_priority": by_priority, "overdue": overdue}
    finally:
        conn.close()
