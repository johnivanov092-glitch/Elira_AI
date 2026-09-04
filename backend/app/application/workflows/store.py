from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.infrastructure.db.connection import connect_sqlite


CREATE_SQL = """
CREATE TABLE IF NOT EXISTS workflow_templates (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    name_ru TEXT NOT NULL DEFAULT '',
    description TEXT NOT NULL DEFAULT '',
    description_ru TEXT NOT NULL DEFAULT '',
    graph_json TEXT NOT NULL DEFAULT '{}',
    input_schema_json TEXT NOT NULL DEFAULT '{}',
    output_schema_json TEXT NOT NULL DEFAULT '{}',
    enabled INTEGER NOT NULL DEFAULT 1,
    version INTEGER NOT NULL DEFAULT 1,
    source TEXT NOT NULL DEFAULT 'custom',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_templates_source ON workflow_templates(source);
CREATE INDEX IF NOT EXISTS idx_workflow_templates_enabled ON workflow_templates(enabled);

CREATE TABLE IF NOT EXISTS workflow_runs (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    status TEXT NOT NULL,
    current_step_id TEXT NOT NULL DEFAULT '',
    input_json TEXT NOT NULL DEFAULT '{}',
    context_json TEXT NOT NULL DEFAULT '{}',
    step_results_json TEXT NOT NULL DEFAULT '{}',
    pending_steps_json TEXT NOT NULL DEFAULT '[]',
    error_json TEXT NOT NULL DEFAULT '{}',
    requested_pause INTEGER NOT NULL DEFAULT 0,
    started_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    finished_at TEXT,
    trigger_source TEXT NOT NULL DEFAULT 'api',
    permission_mode TEXT NOT NULL DEFAULT 'ask'
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_id ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_started ON workflow_runs(started_at);

CREATE TABLE IF NOT EXISTS workflow_requests (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    request_id TEXT NOT NULL UNIQUE,
    workflow_id TEXT NOT NULL,
    run_id TEXT NOT NULL,
    step_id TEXT NOT NULL,
    kind TEXT NOT NULL,
    status TEXT NOT NULL,
    message TEXT NOT NULL DEFAULT '',
    schema_json TEXT NOT NULL DEFAULT '{}',
    sensitive INTEGER NOT NULL DEFAULT 0,
    provider_ref TEXT NOT NULL DEFAULT '',
    action TEXT NOT NULL DEFAULT '',
    resolution_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL,
    resolved_at TEXT
);
CREATE INDEX IF NOT EXISTS idx_workflow_requests_run ON workflow_requests(run_id, id);
CREATE INDEX IF NOT EXISTS idx_workflow_requests_status ON workflow_requests(status, id);
CREATE UNIQUE INDEX IF NOT EXISTS idx_workflow_requests_active_step
    ON workflow_requests(run_id, step_id)
    WHERE status IN ('pending', 'resolving', 'needs_reconciliation');

CREATE TABLE IF NOT EXISTS workflow_triggers (
    id TEXT PRIMARY KEY,
    workflow_id TEXT NOT NULL,
    name TEXT NOT NULL DEFAULT '',
    interval_minutes INTEGER NOT NULL DEFAULT 60,
    enabled INTEGER NOT NULL DEFAULT 1,
    permission_mode TEXT NOT NULL DEFAULT 'ask',
    input_json TEXT NOT NULL DEFAULT '{}',
    context_json TEXT NOT NULL DEFAULT '{}',
    last_run_id TEXT NOT NULL DEFAULT '',
    last_run_at TEXT,
    next_run_at TEXT NOT NULL,
    last_status TEXT NOT NULL DEFAULT '',
    last_error TEXT NOT NULL DEFAULT '',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_workflow_triggers_due
    ON workflow_triggers(enabled, next_run_at);
CREATE INDEX IF NOT EXISTS idx_workflow_triggers_workflow
    ON workflow_triggers(workflow_id);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalize_utc_iso(value: str) -> str:
    raw = str(value or "").strip()
    if raw.endswith("Z"):
        raw = f"{raw[:-1]}+00:00"
    parsed = datetime.fromisoformat(raw)
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


@contextmanager
def _connect(db_path: str | Path):
    connection = connect_sqlite(db_path)
    try:
        yield connection
        connection.commit()
    except Exception:
        connection.rollback()
        raise
    finally:
        connection.close()


def init_db(*, db_path: str | Path) -> None:
    with _connect(db_path) as connection:
        connection.executescript(CREATE_SQL)
        columns = {
            str(row["name"])
            for row in connection.execute("PRAGMA table_info(workflow_runs)").fetchall()
        }
        if "permission_mode" not in columns:
            connection.execute(
                "ALTER TABLE workflow_runs "
                "ADD COLUMN permission_mode TEXT NOT NULL DEFAULT 'ask'"
            )
        connection.execute("DROP INDEX IF EXISTS idx_workflow_requests_active_step")
        connection.execute(
            """
            CREATE UNIQUE INDEX idx_workflow_requests_active_step
            ON workflow_requests(run_id, step_id)
            WHERE status IN ('pending', 'resolving', 'needs_reconciliation')
            """
        )


def _dumps(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def _loads(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def _as_bool(value: Any) -> bool:
    return bool(value)


def _row_to_template(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["graph"] = _loads(data.pop("graph_json", "{}"), {})
    data["input_schema"] = _loads(data.pop("input_schema_json", "{}"), {})
    data["output_schema"] = _loads(data.pop("output_schema_json", "{}"), {})
    data["enabled"] = _as_bool(data.get("enabled"))
    return data


def _row_to_run(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["input"] = _loads(data.pop("input_json", "{}"), {})
    data["context"] = _loads(data.pop("context_json", "{}"), {})
    data["step_results"] = _loads(data.pop("step_results_json", "{}"), {})
    data["pending_steps"] = _loads(data.pop("pending_steps_json", "[]"), [])
    data["error"] = _loads(data.pop("error_json", "{}"), {})
    data["requested_pause"] = _as_bool(data.get("requested_pause"))
    data["permission_mode"] = str(data.get("permission_mode") or "ask")
    return data


def _row_to_request(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["type"] = "item/request"
    data["schema"] = _loads(data.pop("schema_json", "{}"), {})
    data["resolution"] = _loads(data.pop("resolution_json", "{}"), {})
    data["sensitive"] = _as_bool(data.get("sensitive"))
    return data


def _row_to_trigger(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["input"] = _loads(data.pop("input_json", "{}"), {})
    data["context"] = _loads(data.pop("context_json", "{}"), {})
    data["enabled"] = _as_bool(data.get("enabled"))
    return data


def normalize_graph(graph: dict[str, Any]) -> dict[str, Any]:
    steps = graph.get("steps", []) if isinstance(graph, dict) else []
    if not isinstance(steps, list) or not steps:
        raise ValueError("workflow graph must contain non-empty steps")

    ids: list[str] = []
    normalized_steps: list[dict[str, Any]] = []
    for raw_step in steps:
        if not isinstance(raw_step, dict):
            raise ValueError("workflow step must be object")
        step_id = str(raw_step.get("id", "")).strip()
        if not step_id:
            raise ValueError("workflow step id is required")
        if step_id in ids:
            raise ValueError(f"duplicate workflow step id: {step_id}")
        step_type = str(raw_step.get("type", "")).strip()
        if step_type not in {"agent", "tool", "request"}:
            raise ValueError(f"unsupported workflow step type: {step_type}")
        if step_type == "agent" and not str(raw_step.get("agent_id", "")).strip():
            raise ValueError(f"agent step '{step_id}' requires agent_id")
        if step_type == "tool" and not str(raw_step.get("tool_name", "")).strip():
            raise ValueError(f"tool step '{step_id}' requires tool_name")
        if step_type == "request":
            config = raw_step.get("config", {})
            if not isinstance(config, dict):
                raise ValueError(f"workflow request step '{step_id}' requires config")
            request_kind = str(config.get("kind", "")).strip()
            if request_kind not in {"input", "secret", "elevation", "approval"}:
                raise ValueError(
                    f"workflow request step '{step_id}' has invalid request kind"
                )
            if not isinstance(config.get("schema", {}), dict):
                raise ValueError(
                    f"workflow request step '{step_id}' has invalid schema"
                )

        next_value = raw_step.get("next")
        if next_value is not None and not isinstance(next_value, (str, list)):
            raise ValueError(f"workflow step '{step_id}' has invalid next")
        if isinstance(next_value, list):
            for item in next_value:
                if not isinstance(item, dict):
                    raise ValueError(f"workflow step '{step_id}' transition must be object")
                when = str(item.get("when", "always")).strip()
                if when not in {"always", "on_success", "on_failure"}:
                    raise ValueError(f"workflow step '{step_id}' has invalid transition when")
                if not str(item.get("to", "")).strip():
                    raise ValueError(f"workflow step '{step_id}' transition requires 'to'")

        ids.append(step_id)
        normalized_steps.append(
            {
                "id": step_id,
                "type": step_type,
                "agent_id": str(raw_step.get("agent_id", "")),
                "tool_name": str(raw_step.get("tool_name", "")),
                "input_map": raw_step.get("input_map", {}) if isinstance(raw_step.get("input_map", {}), dict) else {},
                "save_as": str(raw_step.get("save_as", "")).strip(),
                "next": next_value,
                "on_error": str(raw_step.get("on_error", "")).strip(),
                "pause_after": bool(raw_step.get("pause_after", False)),
                "config": raw_step.get("config", {}) if isinstance(raw_step.get("config", {}), dict) else {},
            }
        )

    entry_step = str((graph or {}).get("entry_step", "")).strip() or ids[0]
    if entry_step not in ids:
        raise ValueError("workflow graph entry_step must reference existing step")

    return {"entry_step": entry_step, "steps": normalized_steps}


def upsert_workflow_template(*, db_path: str | Path, template: dict[str, Any], now_func=now_utc) -> dict[str, Any]:
    workflow_id = str(template.get("id") or f"workflow-{uuid.uuid4().hex[:10]}")
    now = now_func()
    existing = get_workflow_template(db_path=db_path, workflow_id=workflow_id)
    created_at = existing["created_at"] if existing else now
    graph = normalize_graph(template.get("graph", {}))

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_templates
                (id, name, name_ru, description, description_ru, graph_json,
                 input_schema_json, output_schema_json, enabled, version, source,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                name = excluded.name,
                name_ru = excluded.name_ru,
                description = excluded.description,
                description_ru = excluded.description_ru,
                graph_json = excluded.graph_json,
                input_schema_json = excluded.input_schema_json,
                output_schema_json = excluded.output_schema_json,
                enabled = excluded.enabled,
                version = excluded.version,
                source = excluded.source,
                updated_at = excluded.updated_at
            """,
            (
                workflow_id,
                str(template.get("name", workflow_id)),
                str(template.get("name_ru", "")),
                str(template.get("description", "")),
                str(template.get("description_ru", "")),
                _dumps(graph),
                _dumps(template.get("input_schema", {})),
                _dumps(template.get("output_schema", {})),
                1 if template.get("enabled", True) else 0,
                int(template.get("version", 1)),
                str(template.get("source", "custom")),
                created_at,
                now,
            ),
        )

    return get_workflow_template(db_path=db_path, workflow_id=workflow_id) or {}


def create_workflow_template(*, db_path: str | Path, template: dict[str, Any]) -> dict[str, Any]:
    return upsert_workflow_template(db_path=db_path, template=template)


def get_workflow_template(*, db_path: str | Path, workflow_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM workflow_templates WHERE id = ?",
            (workflow_id,),
        ).fetchone()
    return _row_to_template(row)


def list_workflow_templates(
    *,
    db_path: str | Path,
    include_disabled: bool = False,
    source: str | None = None,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if not include_disabled:
        clauses.append("enabled = 1")
    if source:
        clauses.append("source = ?")
        params.append(source)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(db_path) as connection:
        total_row = connection.execute(
            f"SELECT COUNT(*) AS cnt FROM workflow_templates {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"SELECT * FROM workflow_templates {where} ORDER BY source, name, id",
            params,
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    items = [_row_to_template(row) for row in rows]
    return [item for item in items if item], total


def update_workflow_template(*, db_path: str | Path, workflow_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    existing = get_workflow_template(db_path=db_path, workflow_id=workflow_id)
    if not existing:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    merged = {**existing, **{key: value for key, value in updates.items() if value is not None}}
    merged["id"] = workflow_id
    return upsert_workflow_template(db_path=db_path, template=merged)


def delete_workflow_template(*, db_path: str | Path, workflow_id: str) -> dict[str, Any]:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM workflow_templates WHERE id = ?",
            (workflow_id,),
        )
    return {"workflow_id": workflow_id, "removed": cursor.rowcount > 0}


def upsert_workflow_trigger(
    *,
    db_path: str | Path,
    trigger: dict[str, Any],
    now_func=now_utc,
) -> dict[str, Any]:
    trigger_id = str(trigger.get("id") or f"trigger-{uuid.uuid4().hex[:10]}").strip()
    workflow_id = str(trigger.get("workflow_id") or "").strip()
    if not workflow_id:
        raise ValueError("workflow trigger requires workflow_id")
    if not get_workflow_template(db_path=db_path, workflow_id=workflow_id):
        raise ValueError(f"Workflow '{workflow_id}' not found")
    interval_minutes = int(trigger.get("interval_minutes") or 60)
    if interval_minutes < 1:
        raise ValueError("workflow trigger interval_minutes must be at least 1")
    permission_mode = str(trigger.get("permission_mode") or "ask").strip().lower()
    if permission_mode not in {"ask", "accept_edits", "bypass"}:
        raise ValueError(f"Unsupported workflow permission mode: {permission_mode}")
    workflow_input = trigger.get("input", {})
    context = trigger.get("context", {})
    if not isinstance(workflow_input, dict) or not isinstance(context, dict):
        raise ValueError("workflow trigger input and context must be objects")

    now = now_func()
    existing = get_workflow_trigger(db_path=db_path, trigger_id=trigger_id)
    created_at = str((existing or {}).get("created_at") or now)
    requested_next_run = str(trigger.get("next_run_at") or "").strip()
    if requested_next_run:
        next_run_at = _normalize_utc_iso(requested_next_run)
    else:
        next_run_at = (
            datetime.fromisoformat(_normalize_utc_iso(now))
            + timedelta(minutes=interval_minutes)
        ).isoformat()

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_triggers
                (id, workflow_id, name, interval_minutes, enabled,
                 permission_mode, input_json, context_json, last_run_id,
                 last_run_at, next_run_at, last_status, last_error,
                 created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(id) DO UPDATE SET
                workflow_id = excluded.workflow_id,
                name = excluded.name,
                interval_minutes = excluded.interval_minutes,
                enabled = excluded.enabled,
                permission_mode = excluded.permission_mode,
                input_json = excluded.input_json,
                context_json = excluded.context_json,
                next_run_at = excluded.next_run_at,
                updated_at = excluded.updated_at
            """,
            (
                trigger_id,
                workflow_id,
                str(trigger.get("name") or trigger_id),
                interval_minutes,
                1 if trigger.get("enabled", True) else 0,
                permission_mode,
                _dumps(workflow_input),
                _dumps(context),
                str((existing or {}).get("last_run_id") or ""),
                (existing or {}).get("last_run_at"),
                next_run_at,
                str((existing or {}).get("last_status") or ""),
                str((existing or {}).get("last_error") or ""),
                created_at,
                now,
            ),
        )
    return get_workflow_trigger(db_path=db_path, trigger_id=trigger_id) or {}


def get_workflow_trigger(
    *, db_path: str | Path, trigger_id: str
) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM workflow_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
    return _row_to_trigger(row)


def list_workflow_triggers(
    *, db_path: str | Path, enabled: bool | None = None
) -> tuple[list[dict[str, Any]], int]:
    where = ""
    params: list[Any] = []
    if enabled is not None:
        where = "WHERE enabled = ?"
        params.append(1 if enabled else 0)
    with _connect(db_path) as connection:
        rows = connection.execute(
            f"SELECT * FROM workflow_triggers {where} ORDER BY next_run_at, id",
            params,
        ).fetchall()
    items = [_row_to_trigger(row) for row in rows]
    triggers = [item for item in items if item]
    return triggers, len(triggers)


def list_due_workflow_triggers(
    *, db_path: str | Path, due_at: str
) -> list[dict[str, Any]]:
    with _connect(db_path) as connection:
        rows = connection.execute(
            """
            SELECT * FROM workflow_triggers
            WHERE enabled = 1 AND next_run_at <= ?
            ORDER BY next_run_at, id
            """,
            (due_at,),
        ).fetchall()
    items = [_row_to_trigger(row) for row in rows]
    return [item for item in items if item]


def claim_workflow_trigger(
    *,
    db_path: str | Path,
    trigger_id: str,
    due_at: str,
    now_func=now_utc,
) -> tuple[dict[str, Any] | None, bool]:
    now = now_func()
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT interval_minutes FROM workflow_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
        if not row:
            return None, False
        next_run_at = (
            datetime.fromisoformat(_normalize_utc_iso(now))
            + timedelta(minutes=max(1, int(row["interval_minutes"])))
        ).isoformat()
        cursor = connection.execute(
            """
            UPDATE workflow_triggers
            SET last_run_at = ?, next_run_at = ?, last_status = 'running',
                last_error = '', updated_at = ?
            WHERE id = ? AND enabled = 1 AND next_run_at <= ?
            """,
            (now, next_run_at, now, trigger_id, due_at),
        )
        current = connection.execute(
            "SELECT * FROM workflow_triggers WHERE id = ?",
            (trigger_id,),
        ).fetchone()
    return _row_to_trigger(current), cursor.rowcount == 1


def finish_workflow_trigger_run(
    *,
    db_path: str | Path,
    trigger_id: str,
    run_id: str,
    status: str,
    error: str = "",
    now_func=now_utc,
) -> dict[str, Any]:
    with _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE workflow_triggers
            SET last_run_id = ?, last_status = ?, last_error = ?, updated_at = ?
            WHERE id = ?
            """,
            (run_id, status, error, now_func(), trigger_id),
        )
    return get_workflow_trigger(db_path=db_path, trigger_id=trigger_id) or {}


def delete_workflow_trigger(
    *, db_path: str | Path, trigger_id: str
) -> dict[str, Any]:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            "DELETE FROM workflow_triggers WHERE id = ?",
            (trigger_id,),
        )
    return {"trigger_id": trigger_id, "removed": cursor.rowcount > 0}


def get_workflow_run(*, db_path: str | Path, run_id: str) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM workflow_runs WHERE run_id = ?",
            (run_id,),
        ).fetchone()
    return _row_to_run(row)


def list_workflow_runs(
    *,
    db_path: str | Path,
    workflow_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if workflow_id:
        clauses.append("workflow_id = ?")
        params.append(workflow_id)
    if status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""

    with _connect(db_path) as connection:
        total_row = connection.execute(
            f"SELECT COUNT(*) AS cnt FROM workflow_runs {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"SELECT * FROM workflow_runs {where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    runs = [_row_to_run(row) for row in rows]
    return [run for run in runs if run], total


def update_workflow_run(*, db_path: str | Path, run_id: str, now_func=now_utc, **fields: Any) -> dict[str, Any]:
    """Update a non-cancelled run and return its current durable state.

    Once cancellation is committed it is terminal. The predicate is part of the
    UPDATE itself so a worker finishing concurrently cannot revive a run after
    Stop with ``completed``, ``failed``, ``paused`` or ``running``.
    """
    if not fields:
        return get_workflow_run(db_path=db_path, run_id=run_id) or {}

    sets: list[str] = []
    params: list[Any] = []
    mapping = {
        "status": "status",
        "current_step_id": "current_step_id",
        "input": "input_json",
        "context": "context_json",
        "step_results": "step_results_json",
        "pending_steps": "pending_steps_json",
        "error": "error_json",
        "requested_pause": "requested_pause",
        "updated_at": "updated_at",
        "finished_at": "finished_at",
        "trigger_source": "trigger_source",
        "permission_mode": "permission_mode",
    }

    for key, column in mapping.items():
        if key not in fields:
            continue
        value = fields[key]
        if key in {"input", "context", "step_results", "pending_steps", "error"}:
            value = _dumps(value if value is not None else ({} if key != "pending_steps" else []))
        elif key == "requested_pause":
            value = 1 if value else 0
        sets.append(f"{column} = ?")
        params.append(value)

    if "updated_at" not in fields:
        sets.append("updated_at = ?")
        params.append(now_func())

    params.append(run_id)
    with _connect(db_path) as connection:
        connection.execute(
            (
                f"UPDATE workflow_runs SET {', '.join(sets)} "
                "WHERE run_id = ? AND status != 'cancelled'"
            ),
            params,
        )

    return get_workflow_run(db_path=db_path, run_id=run_id) or {}


def create_workflow_run_record(
    *,
    db_path: str | Path,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
    permission_mode: str = "ask",
    now_func=now_utc,
) -> dict[str, Any]:
    template = get_workflow_template(db_path=db_path, workflow_id=workflow_id)
    if not template:
        raise ValueError(f"Workflow '{workflow_id}' not found")
    if not template.get("enabled", True):
        raise ValueError(f"Workflow '{workflow_id}' is disabled")

    graph = template.get("graph", {})
    entry_step = str(graph.get("entry_step", "")).strip()
    run_id = f"wfr-{uuid.uuid4().hex}"
    now = now_func()
    input_payload = workflow_input or {}
    context_payload = context or {}

    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, status, current_step_id, input_json, context_json,
                 step_results_json, pending_steps_json, error_json, requested_pause,
                 started_at, updated_at, finished_at, trigger_source, permission_mode)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                run_id,
                workflow_id,
                "running",
                entry_step,
                _dumps(input_payload),
                _dumps(context_payload),
                _dumps({}),
                _dumps([entry_step] if entry_step else []),
                _dumps({}),
                0,
                now,
                now,
                None,
                trigger_source,
                permission_mode,
            ),
        )

    return get_workflow_run(db_path=db_path, run_id=run_id) or {}


def create_runtime_workflow_run_record(
    *,
    db_path: str | Path,
    run_id: str,
    workflow_id: str,
    context: dict[str, Any] | None = None,
    trigger_source: str,
    permission_mode: str,
    now_func=now_utc,
) -> dict[str, Any]:
    """Create or reactivate a Workflow control record for an existing runtime.

    Composer streaming already owns execution through ``stream_code_agent``. This
    record gives that same run durable Workflow events/requests without creating a
    second executor or requiring a separately stored Workflow template.
    """
    now = now_func()
    existing = get_workflow_run(db_path=db_path, run_id=run_id)
    if existing:
        return update_workflow_run(
            db_path=db_path,
            run_id=run_id,
            status="running",
            current_step_id="agent",
            context=context or existing.get("context", {}),
            pending_steps=["agent"],
            error={},
            finished_at=None,
            trigger_source=trigger_source,
            permission_mode=permission_mode,
        )
    with _connect(db_path) as connection:
        connection.execute(
            """
            INSERT INTO workflow_runs
                (run_id, workflow_id, status, current_step_id, input_json, context_json,
                 step_results_json, pending_steps_json, error_json, requested_pause,
                 started_at, updated_at, finished_at, trigger_source, permission_mode)
            VALUES (?, ?, 'running', 'agent', '{}', ?, '{}', ?, '{}', 0,
                    ?, ?, NULL, ?, ?)
            """,
            (
                run_id,
                workflow_id,
                _dumps(context or {}),
                _dumps(["agent"]),
                now,
                now,
                trigger_source,
                permission_mode,
            ),
        )
    return get_workflow_run(db_path=db_path, run_id=run_id) or {}


def create_workflow_request_record(
    *,
    db_path: str | Path,
    workflow_id: str,
    run_id: str,
    step_id: str,
    kind: str,
    message: str,
    schema: dict[str, Any] | None = None,
    sensitive: bool = False,
    provider_ref: str = "",
    now_func=now_utc,
) -> dict[str, Any]:
    now = now_func()
    with _connect(db_path) as connection:
        existing = connection.execute(
            """
            SELECT * FROM workflow_requests
            WHERE run_id = ? AND step_id = ? AND status IN ('pending', 'resolving')
            ORDER BY id DESC LIMIT 1
            """,
            (run_id, step_id),
        ).fetchone()
        if existing:
            if str(existing["status"]) == "pending":
                return _row_to_request(existing) or {}
            connection.execute(
                """
                UPDATE workflow_requests
                SET status = 'resolved', action = 'accept', updated_at = ?,
                    resolved_at = ?
                WHERE request_id = ? AND status = 'resolving'
                """,
                (now, now, str(existing["request_id"])),
            )

        request_id = f"req-{uuid.uuid4().hex}"
        connection.execute(
            """
            INSERT INTO workflow_requests
                (request_id, workflow_id, run_id, step_id, kind, status, message,
                 schema_json, sensitive, provider_ref, action, resolution_json,
                 created_at, updated_at, resolved_at)
            VALUES (?, ?, ?, ?, ?, 'pending', ?, ?, ?, ?, '', '{}', ?, ?, NULL)
            """,
            (
                request_id,
                workflow_id,
                run_id,
                step_id,
                kind,
                message,
                _dumps(schema or {}),
                1 if sensitive else 0,
                provider_ref,
                now,
                now,
            ),
        )
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(row) or {}


def get_workflow_request(
    *,
    db_path: str | Path,
    request_id: str,
) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(row)


def list_workflow_requests(
    *,
    db_path: str | Path,
    run_id: str | None = None,
    status: str | None = None,
    limit: int = 100,
    offset: int = 0,
) -> tuple[list[dict[str, Any]], int]:
    clauses: list[str] = []
    params: list[Any] = []
    if run_id:
        clauses.append("run_id = ?")
        params.append(run_id)
    if status == "actionable":
        clauses.append("status IN ('pending', 'needs_reconciliation')")
    elif status:
        clauses.append("status = ?")
        params.append(status)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with _connect(db_path) as connection:
        total_row = connection.execute(
            f"SELECT COUNT(*) AS cnt FROM workflow_requests {where}",
            params,
        ).fetchone()
        rows = connection.execute(
            f"""
            SELECT * FROM workflow_requests {where}
            ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?
            """,
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()
    total = int(total_row["cnt"]) if total_row else 0
    requests = [_row_to_request(row) for row in rows]
    return [request for request in requests if request], total


def claim_workflow_request(
    *,
    db_path: str | Path,
    request_id: str,
    now_func=now_utc,
) -> tuple[dict[str, Any] | None, bool]:
    with _connect(db_path) as connection:
        cursor = connection.execute(
            """
            UPDATE workflow_requests SET status = 'resolving', updated_at = ?
            WHERE request_id = ? AND status IN ('pending', 'needs_reconciliation')
            """,
            (now_func(), request_id),
        )
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(row), cursor.rowcount == 1


def finalize_workflow_request(
    *,
    db_path: str | Path,
    request_id: str,
    status: str,
    action: str,
    resolution: dict[str, Any] | None = None,
    now_func=now_utc,
) -> dict[str, Any] | None:
    now = now_func()
    with _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE workflow_requests
            SET status = ?, action = ?, resolution_json = ?, updated_at = ?, resolved_at = ?
            WHERE request_id = ?
            """,
            (status, action, _dumps(resolution or {}), now, now, request_id),
        )
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(row)


def release_workflow_request_claim(
    *,
    db_path: str | Path,
    request_id: str,
    now_func=now_utc,
) -> dict[str, Any] | None:
    with _connect(db_path) as connection:
        connection.execute(
            """
            UPDATE workflow_requests SET status = 'pending', updated_at = ?
            WHERE request_id = ? AND status = 'resolving'
            """,
            (now_func(), request_id),
        )
        row = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(row)


def mark_workflow_request_reconciliation(
    *,
    db_path: str | Path,
    request_id: str,
    now_func=now_utc,
) -> dict[str, Any] | None:
    warning = (
        "Предыдущий запуск прервался в неопределённой точке. Проверьте внешний "
        "результат перед явным повтором: действие могло уже выполниться."
    )
    with _connect(db_path) as connection:
        row = connection.execute(
            "SELECT message FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
        message = str(row["message"] or "") if row else ""
        if not message.startswith(warning):
            message = f"{warning}\n\n{message}".strip()
        connection.execute(
            """
            UPDATE workflow_requests
            SET status = 'needs_reconciliation', message = ?, updated_at = ?
            WHERE request_id = ? AND status = 'resolving'
            """,
            (message, now_func(), request_id),
        )
        updated = connection.execute(
            "SELECT * FROM workflow_requests WHERE request_id = ?",
            (request_id,),
        ).fetchone()
    return _row_to_request(updated)
