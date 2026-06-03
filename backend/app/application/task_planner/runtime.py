from __future__ import annotations

import json
from typing import Any, Callable


_DURABILITY_COLUMNS = [
    ("idempotency_key",  "TEXT"),
    ("retry_count",      "INTEGER NOT NULL DEFAULT 0"),
    ("max_retries",      "INTEGER NOT NULL DEFAULT 3"),
    ("next_retry_at",    "TEXT"),
    ("dead_letter",      "INTEGER NOT NULL DEFAULT 0"),
]

def init_db(*, connect_func: Callable[[], Any]) -> None:
    conn = connect_func()
    try:
        conn.executescript(
            """
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
            """
        )
        conn.commit()
    finally:
        conn.close()
    migrate_durability(connect_func=connect_func)


def migrate_durability(*, connect_func: Callable[[], Any]) -> None:
    """Additive migration: add durability columns if missing."""
    conn = connect_func()
    try:
        existing = {row[1] for row in conn.execute("PRAGMA table_info(tasks)").fetchall()}
        for col_name, col_def in _DURABILITY_COLUMNS:
            if col_name not in existing:
                conn.execute(f"ALTER TABLE tasks ADD COLUMN {col_name} {col_def}")
        conn.commit()
    finally:
        conn.close()


def create_task(
    *,
    connect_func: Callable[[], Any],
    id_func: Callable[[], str],
    now_func: Callable[[], str],
    title: str,
    description: str = "",
    category: str = "general",
    priority: str = "medium",
    due_date: str | None = None,
    tags: list[str] | None = None,
) -> dict[str, Any]:
    tid = id_func()
    now = now_func()
    conn = connect_func()
    try:
        conn.execute(
            "INSERT INTO tasks (id, title, description, category, priority, due_date, tags, created_at) VALUES (?,?,?,?,?,?,?,?)",
            (tid, title, description, category, priority or "medium", due_date, json.dumps(tags or []), now),
        )
        conn.commit()
        return {"ok": True, "id": tid, "title": title}
    finally:
        conn.close()


def list_tasks(
    *,
    connect_func: Callable[[], Any],
    status: str | None = None,
    category: str | None = None,
    limit: int = 100,
) -> dict[str, Any]:
    conn = connect_func()
    try:
        q = "SELECT * FROM tasks"
        params: list[Any] = []
        wheres: list[str] = []
        if status:
            wheres.append("status = ?")
            params.append(status)
        if category:
            wheres.append("category = ?")
            params.append(category)
        if wheres:
            q += " WHERE " + " AND ".join(wheres)

        q += " ORDER BY CASE priority WHEN 'urgent' THEN 0 WHEN 'high' THEN 1 WHEN 'medium' THEN 2 ELSE 3 END, created_at DESC"
        q += " LIMIT ?"
        params.append(limit)

        rows = conn.execute(q, params).fetchall()
        items = []
        for row in rows:
            item = dict(row)
            try:
                item["tags"] = json.loads(item.get("tags") or "[]")
            except Exception:
                item["tags"] = []
            items.append(item)
        return {"ok": True, "tasks": items, "count": len(items)}
    finally:
        conn.close()


def get_task(*, connect_func: Callable[[], Any], tid: str) -> dict[str, Any]:
    conn = connect_func()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Задача не найдена"}
        item = dict(row)
        try:
            item["tags"] = json.loads(item.get("tags") or "[]")
        except Exception:
            item["tags"] = []
        return {"ok": True, **item}
    finally:
        conn.close()


def update_task(
    *,
    connect_func: Callable[[], Any],
    now_func: Callable[[], str],
    tid: str,
    **kwargs: Any,
) -> dict[str, Any]:
    allowed = {
        "title", "description", "category", "priority", "status", "due_date", "tags",
        "idempotency_key", "max_retries",
    }
    updates = ["updated_at = ?"]
    values = [now_func()]

    for key, value in kwargs.items():
        if key not in allowed:
            continue
        if key == "tags" and isinstance(value, list):
            value = json.dumps(value)
        updates.append(f"{key} = ?")
        values.append(value)

    if kwargs.get("status") == "done":
        updates.append("completed_at = ?")
        values.append(now_func())

    conn = connect_func()
    try:
        values.append(tid)
        conn.execute(f"UPDATE tasks SET {', '.join(updates)} WHERE id = ?", values)
        conn.commit()
        return {"ok": True, "id": tid}
    finally:
        conn.close()


def delete_task(*, connect_func: Callable[[], Any], tid: str) -> dict[str, Any]:
    conn = connect_func()
    try:
        conn.execute("DELETE FROM tasks WHERE id = ?", (tid,))
        conn.commit()
        return {"ok": True, "deleted": tid}
    finally:
        conn.close()


def bump_retry(
    *,
    connect_func: Callable[[], Any],
    now_func: Callable[[], str],
    tid: str,
    backoff_base_seconds: int = 60,
) -> dict[str, Any]:
    """Increment retry_count and set next_retry_at with exponential backoff.

    If retry_count + 1 > max_retries the task is marked dead_letter=1
    and status='failed'. Returns the updated task dict.
    """
    conn = connect_func()
    try:
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Task not found"}
        task = dict(row)
        retry_count = int(task.get("retry_count") or 0) + 1
        max_retries = int(task.get("max_retries") or 3)
        now = now_func()

        # P9.2C: only a task carrying an idempotency_key may be auto-retried. A
        # keyless (generic) retry could re-execute a non-idempotent side effect,
        # so block it for manual resume instead of rescheduling. Bounded keyed
        # retry is preserved below; full persisted tool replay is deferred to P12.0.
        idempotency_key = str(task.get("idempotency_key") or "").strip()
        if not idempotency_key:
            conn.execute(
                "UPDATE tasks SET status='blocked', retry_count=?, updated_at=? WHERE id=?",
                (retry_count, now, tid),
            )
            conn.commit()
            row_blocked = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
            return {"ok": False, "error": "retry_blocked_no_idempotency_key", **dict(row_blocked)}

        if retry_count > max_retries:
            conn.execute(
                "UPDATE tasks SET dead_letter=1, status='failed', retry_count=?, updated_at=? WHERE id=?",
                (retry_count, now, tid),
            )
        else:
            delay_secs = backoff_base_seconds * (2 ** (retry_count - 1))
            from datetime import datetime, timedelta, timezone
            next_retry = (
                datetime.now(timezone.utc) + timedelta(seconds=delay_secs)
            ).isoformat()
            conn.execute(
                "UPDATE tasks SET retry_count=?, next_retry_at=?, status='todo', updated_at=? WHERE id=?",
                (retry_count, next_retry, now, tid),
            )
        conn.commit()
        row2 = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        return {"ok": True, **dict(row2)}
    finally:
        conn.close()


def set_waiting_approval(
    *,
    connect_func: Callable[[], Any],
    now_func: Callable[[], str],
    tid: str,
) -> dict[str, Any]:
    """Set task status to 'waiting_approval' — pauses automatic execution."""
    conn = connect_func()
    try:
        now = now_func()
        conn.execute(
            "UPDATE tasks SET status='waiting_approval', updated_at=? WHERE id=?",
            (now, tid),
        )
        conn.commit()
        row = conn.execute("SELECT * FROM tasks WHERE id = ?", (tid,)).fetchone()
        if not row:
            return {"ok": False, "error": "Task not found"}
        return {"ok": True, **dict(row)}
    finally:
        conn.close()


def task_stats(*, connect_func: Callable[[], Any], now_func: Callable[[], str]) -> dict[str, Any]:
    conn = connect_func()
    try:
        total = conn.execute("SELECT COUNT(*) FROM tasks").fetchone()[0]
        by_status: dict[str, int] = {}
        for row in conn.execute("SELECT status, COUNT(*) as cnt FROM tasks GROUP BY status").fetchall():
            by_status[row["status"]] = row["cnt"]
        by_priority: dict[str, int] = {}
        for row in conn.execute(
            "SELECT priority, COUNT(*) as cnt FROM tasks WHERE status != 'done' AND status != 'cancelled' GROUP BY priority"
        ).fetchall():
            by_priority[row["priority"]] = row["cnt"]
        overdue = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status IN ('todo','in_progress') AND due_date IS NOT NULL AND due_date < ?",
            (now_func(),),
        ).fetchone()[0]
        return {"ok": True, "total": total, "by_status": by_status, "by_priority": by_priority, "overdue": overdue}
    finally:
        conn.close()

