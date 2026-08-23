from __future__ import annotations

from typing import Any, Callable

CHECKLIST_STATUSES = frozenset({"pending", "in_progress", "completed"})
SUBAGENT_ROLES = frozenset({"explore", "plan", "verify"})
SUBAGENT_STATUSES = frozenset({"in_progress", "completed", "failed"})

def init_db(*, connect_func: Callable[[], Any]) -> None:
    conn = connect_func()
    try:
        conn.executescript(
            """
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
                max_context_tokens INTEGER NOT NULL DEFAULT 0,
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


def _normalize_run_id(run_id: Any) -> str:
    return str(run_id or "").strip()


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
        # position -> existing item_id, so a re-sent plan that omits ids upserts
        # the row already holding that slot instead of inserting a duplicate.
        # Local models routinely re-emit the full checklist without the ids we
        # generated earlier; without this they'd pile up copies sharing a pos.
        by_position = {int(row.get("position") or 0): str(row["item_id"]) for row in existing.values()}

        prepared_items: list[dict[str, Any]] = []
        next_pos = max_pos + 1
        for raw in raw_items:
            if not isinstance(raw, dict):
                return {"ok": False, "error": "invalid_item", "items": _list_checklist_items(conn, rid)}
            text = str(raw.get("text") or "").strip()
            if not text:
                return {"ok": False, "error": "item_text_required", "items": _list_checklist_items(conn, rid)}
            status = _normalize_checklist_status(raw.get("status"))
            position = int(raw.get("position")) if raw.get("position") is not None else next_pos
            next_pos = max(next_pos, position + 1)
            raw_id = str(raw.get("id") or raw.get("item_id") or "").strip()
            # No explicit id: reuse the id already occupying this position (if any)
            # so the upsert lands on the existing row; only mint a fresh id for a
            # genuinely new slot.
            if raw_id:
                item_id = raw_id
            else:
                item_id = by_position.get(position) or str(id_func()).strip()
            by_position[position] = item_id
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
    # Old databases may still carry these now-inert columns. Never expose them
    # as runtime controls.
    data.pop("max_steps", None)
    data.pop("tool_allowlist_json", None)
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
    max_context_tokens: int = 0,
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
        # The live model server is the source of truth for physical context.
        safe_ctx = max(0, int(max_context_tokens or 0))
    except (TypeError, ValueError):
        return {"ok": False, "error": "invalid_subagent_context"}
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
                 max_context_tokens, result_text, error, created_at, updated_at)
            VALUES (?, ?, ?, ?, 'in_progress', ?, ?, '', '', ?, ?)
            """,
            (
                subagent_run_id,
                parent,
                normalized_role,
                cleaned_task,
                safe_depth,
                safe_ctx,
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
            "max_context_tokens": safe_ctx,
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
