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

CHECKLIST_STATUSES = frozenset({"pending", "in_progress", "completed", "blocked"})
SUBAGENT_ROLES = frozenset({"explore", "plan", "verify"})
SUBAGENT_STATUSES = frozenset({"in_progress", "completed", "failed", "blocked"})

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

            CREATE TABLE IF NOT EXISTS task_checklist_items (
                run_id TEXT NOT NULL,
                item_id TEXT NOT NULL,
                text TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                position INTEGER NOT NULL DEFAULT 0,
                blocker TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT,
                PRIMARY KEY (run_id, item_id)
            );
            CREATE INDEX IF NOT EXISTS idx_task_checklist_run_order
                ON task_checklist_items(run_id, position, item_id);
            CREATE INDEX IF NOT EXISTS idx_task_checklist_run_status
                ON task_checklist_items(run_id, status);

            CREATE TABLE IF NOT EXISTS task_subagent_runs (
                subagent_run_id TEXT PRIMARY KEY,
                parent_run_id TEXT NOT NULL,
                role TEXT NOT NULL,
                task TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'in_progress',
                depth INTEGER NOT NULL DEFAULT 0,
                max_steps INTEGER NOT NULL DEFAULT 0,
                max_context_tokens INTEGER NOT NULL DEFAULT 0,
                tool_allowlist_json TEXT NOT NULL DEFAULT '[]',
                result_text TEXT DEFAULT '',
                error TEXT DEFAULT '',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                completed_at TEXT
            );
            CREATE INDEX IF NOT EXISTS idx_task_subagent_parent
                ON task_subagent_runs(parent_run_id, created_at);
            CREATE INDEX IF NOT EXISTS idx_task_subagent_status
                ON task_subagent_runs(status);
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


def _normalize_run_id(run_id: Any) -> str:
    return str(run_id or "").strip()


def _normalize_item_id(value: Any, *, id_func: Callable[[], str]) -> str:
    raw = str(value or "").strip()
    return raw or str(id_func()).strip()


def _normalize_checklist_status(value: Any) -> str:
    status = str(value or "pending").strip().lower()
    if status not in CHECKLIST_STATUSES:
        raise ValueError(f"invalid checklist status: {status}")
    return status


def _checklist_row_to_item(row: Any) -> dict[str, Any]:
    data = dict(row)
    data["id"] = data.pop("item_id")
    return data


def _list_checklist_items(conn: Any, run_id: str) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT run_id, item_id, text, status, position, blocker,
               created_at, updated_at, completed_at
        FROM task_checklist_items
        WHERE run_id = ?
        ORDER BY position ASC, item_id ASC
        """,
        (run_id,),
    ).fetchall()
    return [_checklist_row_to_item(row) for row in rows]


def _emit_checklist_event(
    emit_event_func: Callable[..., Any] | None,
    payload: dict[str, Any],
) -> None:
    if emit_event_func is None:
        return
    try:
        emit_event_func(
            event_type="task.checklist.updated",
            source_agent_id="task_planner",
            payload=payload,
        )
    except Exception:
        pass


def list_checklist(
    *,
    connect_func: Callable[[], Any],
    run_id: str,
) -> dict[str, Any]:
    rid = _normalize_run_id(run_id)
    if not rid:
        return {"ok": False, "error": "run_id_required", "items": []}
    conn = connect_func()
    try:
        items = _list_checklist_items(conn, rid)
        return {"ok": True, "run_id": rid, "items": items, "count": len(items)}
    finally:
        conn.close()


def update_checklist(
    *,
    connect_func: Callable[[], Any],
    id_func: Callable[[], str],
    now_func: Callable[[], str],
    run_id: str,
    items: list[dict[str, Any]] | None = None,
    updates: list[dict[str, Any]] | None = None,
    emit_event_func: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    """Create/update a durable run checklist.

    ``items`` create or replace full item fields. ``updates`` mutate existing
    items by id. The function validates the whole payload before writing so a
    bad status cannot partially update the checklist.
    """
    rid = _normalize_run_id(run_id)
    if not rid:
        return {"ok": False, "error": "run_id_required", "items": []}

    if items is not None and not isinstance(items, list):
        return {"ok": False, "error": "invalid_items", "items": []}
    if updates is not None and not isinstance(updates, list):
        return {"ok": False, "error": "invalid_updates", "items": []}
    raw_items = items or []
    raw_updates = updates or []

    conn = connect_func()
    try:
        existing_rows = conn.execute(
            "SELECT * FROM task_checklist_items WHERE run_id = ?",
            (rid,),
        ).fetchall()
        existing = {str(row["item_id"]): dict(row) for row in existing_rows}
        max_pos = max((int(row.get("position") or 0) for row in existing.values()), default=-1)

        prepared_items: list[dict[str, Any]] = []
        next_pos = max_pos + 1
        for raw in raw_items:
            if not isinstance(raw, dict):
                return {"ok": False, "error": "invalid_item", "items": _list_checklist_items(conn, rid)}
            item_id = _normalize_item_id(raw.get("id") or raw.get("item_id"), id_func=id_func)
            text = str(raw.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "item_text_required", "item_id": item_id, "items": _list_checklist_items(conn, rid)}
            status = _normalize_checklist_status(raw.get("status"))
            position = int(raw.get("position")) if raw.get("position") is not None else next_pos
            next_pos = max(next_pos, position + 1)
            blocker = str(raw.get("blocker") or "").strip()
            prepared_items.append({
                "id": item_id,
                "text": text,
                "status": status,
                "position": position,
                "blocker": blocker,
            })

        prepared_updates: list[dict[str, Any]] = []
        for raw in raw_updates:
            if not isinstance(raw, dict):
                return {"ok": False, "error": "invalid_update", "items": _list_checklist_items(conn, rid)}
            item_id = str(raw.get("id") or raw.get("item_id") or "").strip()
            if not item_id:
                return {"ok": False, "error": "update_id_required", "items": _list_checklist_items(conn, rid)}
            current = existing.get(item_id)
            if current is None:
                return {"ok": False, "error": "item_not_found", "item_id": item_id, "items": _list_checklist_items(conn, rid)}
            status = _normalize_checklist_status(raw.get("status", current.get("status")))
            prepared_updates.append({
                "id": item_id,
                "text": str(raw.get("text", current.get("text") or "") or "").strip(),
                "status": status,
                "position": int(raw.get("position")) if raw.get("position") is not None else int(current.get("position") or 0),
                "blocker": str(raw.get("blocker", current.get("blocker") or "") or "").strip(),
            })
            if not prepared_updates[-1]["text"]:
                return {"ok": False, "error": "item_text_required", "item_id": item_id, "items": _list_checklist_items(conn, rid)}

        now = now_func()
        changed: list[dict[str, Any]] = []
        for item in prepared_items:
            completed_at = now if item["status"] == "completed" else None
            conn.execute(
                """
                INSERT INTO task_checklist_items
                    (run_id, item_id, text, status, position, blocker, created_at, updated_at, completed_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(run_id, item_id) DO UPDATE SET
                    text = excluded.text,
                    status = excluded.status,
                    position = excluded.position,
                    blocker = excluded.blocker,
                    updated_at = excluded.updated_at,
                    completed_at = excluded.completed_at
                """,
                (
                    rid,
                    item["id"],
                    item["text"],
                    item["status"],
                    item["position"],
                    item["blocker"],
                    now,
                    now,
                    completed_at,
                ),
            )
            changed.append({"id": item["id"], "status": item["status"], "action": "upsert"})

        for item in prepared_updates:
            completed_at = now if item["status"] == "completed" else None
            conn.execute(
                """
                UPDATE task_checklist_items
                SET text = ?, status = ?, position = ?, blocker = ?,
                    updated_at = ?, completed_at = ?
                WHERE run_id = ? AND item_id = ?
                """,
                (
                    item["text"],
                    item["status"],
                    item["position"],
                    item["blocker"],
                    now,
                    completed_at,
                    rid,
                    item["id"],
                ),
            )
            changed.append({"id": item["id"], "status": item["status"], "action": "update"})

        conn.commit()
        result_items = _list_checklist_items(conn, rid)
    except (TypeError, ValueError) as exc:
        return {"ok": False, "error": str(exc), "items": _list_checklist_items(conn, rid)}
    finally:
        conn.close()

    if changed:
        _emit_checklist_event(
            emit_event_func,
            {
                "run_id": rid,
                "changed": changed,
                "count": len(result_items),
            },
        )
    return {"ok": True, "run_id": rid, "items": result_items, "count": len(result_items), "changed": changed}


def _normalize_subagent_role(role: Any) -> str:
    normalized = str(role or "").strip().lower()
    if normalized not in SUBAGENT_ROLES:
        raise ValueError(f"invalid subagent role: {normalized}")
    return normalized


def _normalize_subagent_status(status: Any) -> str:
    normalized = str(status or "").strip().lower()
    if normalized not in SUBAGENT_STATUSES:
        raise ValueError(f"invalid subagent status: {normalized}")
    return normalized


def _subagent_row_to_dict(row: Any) -> dict[str, Any]:
    data = dict(row)
    try:
        data["tool_allowlist"] = json.loads(data.pop("tool_allowlist_json", "[]") or "[]")
    except (TypeError, json.JSONDecodeError):
        data["tool_allowlist"] = []
    return data


def _emit_subagent_event(
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


def start_subagent_run(
    *,
    connect_func: Callable[[], Any],
    id_func: Callable[[], str],
    now_func: Callable[[], str],
    parent_run_id: str,
    role: str,
    task: str,
    depth: int = 0,
    max_steps: int = 0,
    max_context_tokens: int = 0,
    tool_allowlist: list[str] | tuple[str, ...] | None = None,
    emit_event_func: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    parent = _normalize_run_id(parent_run_id)
    if not parent:
        return {"ok": False, "error": "parent_run_id_required"}
    try:
        normalized_role = _normalize_subagent_role(role)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    cleaned_task = str(task or "").strip()
    if not cleaned_task:
        return {"ok": False, "error": "task_required"}
    try:
        safe_depth = max(0, int(depth or 0))
        safe_steps = max(1, min(int(max_steps or 1), 20))
        safe_ctx = max(1024, min(int(max_context_tokens or 1024), 32768))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_subagent_limits"}
    if safe_depth > 1:
        return {"ok": False, "error": "subagent_depth_exceeded"}
    allowlist = [str(name).strip() for name in (tool_allowlist or []) if str(name).strip()]
    subagent_run_id = str(id_func()).strip()
    if not subagent_run_id:
        return {"ok": False, "error": "subagent_run_id_required"}
    now = now_func()

    conn = connect_func()
    try:
        conn.execute(
            """
            INSERT INTO task_subagent_runs
                (subagent_run_id, parent_run_id, role, task, status, depth,
                 max_steps, max_context_tokens, tool_allowlist_json,
                 result_text, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'in_progress', ?, ?, ?, ?, '', '', ?, ?)
            """,
            (
                subagent_run_id,
                parent,
                normalized_role,
                cleaned_task,
                safe_depth,
                safe_steps,
                safe_ctx,
                json.dumps(allowlist, ensure_ascii=False),
                now,
                now,
            ),
        )
        conn.commit()
        row = conn.execute(
            "SELECT * FROM task_subagent_runs WHERE subagent_run_id = ?",
            (subagent_run_id,),
        ).fetchone()
        data = _subagent_row_to_dict(row)
    finally:
        conn.close()

    _emit_subagent_event(
        emit_event_func,
        "task.subagent.started",
        {
            "subagent_run_id": subagent_run_id,
            "parent_run_id": parent,
            "role": normalized_role,
            "depth": safe_depth,
            "max_steps": safe_steps,
            "max_context_tokens": safe_ctx,
            "tool_allowlist": allowlist,
        },
    )
    return {"ok": True, **data}


def finish_subagent_run(
    *,
    connect_func: Callable[[], Any],
    now_func: Callable[[], str],
    subagent_run_id: str,
    status: str,
    result_text: str = "",
    error: str = "",
    emit_event_func: Callable[..., Any] | None = None,
) -> dict[str, Any]:
    sid = str(subagent_run_id or "").strip()
    if not sid:
        return {"ok": False, "error": "subagent_run_id_required"}
    try:
        normalized_status = _normalize_subagent_status(status)
    except ValueError as exc:
        return {"ok": False, "error": str(exc)}
    if normalized_status == "in_progress":
        return {"ok": False, "error": "terminal_status_required"}
    now = now_func()
    safe_result = str(result_text or "")
    safe_error = str(error or "")

    conn = connect_func()
    try:
        cursor = conn.execute(
            """
            UPDATE task_subagent_runs
            SET status = ?, result_text = ?, error = ?, updated_at = ?, completed_at = ?
            WHERE subagent_run_id = ?
            """,
            (normalized_status, safe_result, safe_error, now, now, sid),
        )
        if cursor.rowcount <= 0:
            conn.commit()
            return {"ok": False, "error": "subagent_run_not_found"}
        conn.commit()
        row = conn.execute(
            "SELECT * FROM task_subagent_runs WHERE subagent_run_id = ?",
            (sid,),
        ).fetchone()
        data = _subagent_row_to_dict(row)
    finally:
        conn.close()

    event_type = "task.subagent.completed" if normalized_status == "completed" else "task.subagent.failed"
    _emit_subagent_event(
        emit_event_func,
        event_type,
        {
            "subagent_run_id": sid,
            "parent_run_id": data.get("parent_run_id", ""),
            "role": data.get("role", ""),
            "status": normalized_status,
            "error": safe_error,
        },
    )
    return {"ok": True, **data}


def get_subagent_run(
    *,
    connect_func: Callable[[], Any],
    subagent_run_id: str,
) -> dict[str, Any]:
    sid = str(subagent_run_id or "").strip()
    if not sid:
        return {"ok": False, "error": "subagent_run_id_required"}
    conn = connect_func()
    try:
        row = conn.execute(
            "SELECT * FROM task_subagent_runs WHERE subagent_run_id = ?",
            (sid,),
        ).fetchone()
        if not row:
            return {"ok": False, "error": "subagent_run_not_found"}
        return {"ok": True, **_subagent_row_to_dict(row)}
    finally:
        conn.close()


def list_subagent_runs(
    *,
    connect_func: Callable[[], Any],
    parent_run_id: str,
    limit: int = 100,
) -> dict[str, Any]:
    parent = _normalize_run_id(parent_run_id)
    if not parent:
        return {"ok": False, "error": "parent_run_id_required", "items": []}
    safe_limit = max(1, min(int(limit or 100), 500))
    conn = connect_func()
    try:
        rows = conn.execute(
            """
            SELECT * FROM task_subagent_runs
            WHERE parent_run_id = ?
            ORDER BY created_at DESC, subagent_run_id DESC
            LIMIT ?
            """,
            (parent, safe_limit),
        ).fetchall()
        items = [_subagent_row_to_dict(row) for row in rows]
        return {"ok": True, "parent_run_id": parent, "items": items, "count": len(items)}
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

