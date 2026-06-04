from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
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


def _parse_iso_datetime(value: Any) -> datetime | None:
    if not value:
        return None
    raw = str(value).strip()
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _task_updated_at(task: dict[str, Any]) -> datetime | None:
    return _parse_iso_datetime(task.get("updated_at")) or _parse_iso_datetime(task.get("created_at"))


def _emit_recovery_event(
    emit_event_func: Callable[..., Any] | None,
    event_type: str,
    payload: dict[str, Any],
) -> None:
    if emit_event_func is None:
        return
    try:
        emit_event_func(
            event_type=event_type,
            source_agent_id="task_planner",
            payload=payload,
        )
    except Exception:
        pass


def recover_stale_tasks(
    *,
    connect_func: Callable[[], Any],
    now_func: Callable[[], str],
    stale_after_seconds: int = 3600,
    limit: int = 50,
    backoff_base_seconds: int = 60,
    emit_event_func: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Recover stale in-progress tasks after backend restart.

    Conservative rules:
    - only bounded ``in_progress`` rows are considered;
    - ``waiting_approval`` rows are counted but left untouched;
    - idempotent rows (non-empty idempotency_key) use existing bounded retry;
    - keyless rows become ``blocked`` for manual resume.
    """
    safe_limit = max(1, min(int(limit or 50), 500))
    safe_stale_after = max(1, int(stale_after_seconds or 3600))
    now_raw = now_func()
    now_dt = _parse_iso_datetime(now_raw) or datetime.now(timezone.utc)
    cutoff = now_dt - timedelta(seconds=safe_stale_after)

    conn = connect_func()
    try:
        waiting_approval = conn.execute(
            "SELECT COUNT(*) FROM tasks WHERE status='waiting_approval'"
        ).fetchone()[0]
        rows = conn.execute(
            """
            SELECT * FROM tasks
            WHERE status='in_progress' AND COALESCE(dead_letter, 0)=0
            ORDER BY COALESCE(updated_at, created_at) ASC
            LIMIT ?
            """,
            (safe_limit,),
        ).fetchall()
        candidates = [dict(row) for row in rows]
    finally:
        conn.close()

    items: list[dict[str, Any]] = []
    skipped_fresh = 0
    rescheduled = 0
    blocked = 0
    failed = 0

    for task in candidates:
        tid = str(task.get("id") or "")
        updated_at = _task_updated_at(task)
        if updated_at is not None and updated_at > cutoff:
            skipped_fresh += 1
            continue

        idempotency_key = str(task.get("idempotency_key") or "").strip()
        if not idempotency_key:
            conn = connect_func()
            try:
                conn.execute(
                    "UPDATE tasks SET status='blocked', updated_at=? WHERE id=?",
                    (now_raw, tid),
                )
                conn.commit()
                row = conn.execute("SELECT * FROM tasks WHERE id=?", (tid,)).fetchone()
                recovered = dict(row) if row else {"id": tid, "status": "blocked"}
            finally:
                conn.close()
            blocked += 1
            item = {
                "id": tid,
                "action": "blocked",
                "status": recovered.get("status"),
                "reason": "stale_in_progress_without_idempotency_key",
            }
            items.append(item)
            _emit_recovery_event(emit_event_func, "task.recovery.blocked", item)
            continue

        recovered = bump_retry(
            connect_func=connect_func,
            now_func=now_func,
            tid=tid,
            backoff_base_seconds=backoff_base_seconds,
        )
        status = str(recovered.get("status") or "")
        if status == "todo":
            rescheduled += 1
            action = "rescheduled"
            event_type = "task.recovery.rescheduled"
        elif status == "failed" or recovered.get("dead_letter"):
            failed += 1
            action = "dead_letter"
            event_type = "task.recovery.dead_letter"
        else:
            blocked += 1
            action = "blocked"
            event_type = "task.recovery.blocked"

        item = {
            "id": tid,
            "action": action,
            "status": status,
            "retry_count": recovered.get("retry_count"),
            "next_retry_at": recovered.get("next_retry_at"),
            "dead_letter": recovered.get("dead_letter"),
        }
        items.append(item)
        _emit_recovery_event(emit_event_func, event_type, item)

    return {
        "ok": True,
        "checked": len(candidates),
        "rescheduled": rescheduled,
        "blocked": blocked,
        "failed": failed,
        "skipped_fresh": skipped_fresh,
        "waiting_approval": int(waiting_approval or 0),
        "limit": safe_limit,
        "items": items,
    }


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

