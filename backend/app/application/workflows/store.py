from __future__ import annotations

import json
import sqlite3
import uuid
from datetime import datetime, timezone
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
    trigger_source TEXT NOT NULL DEFAULT 'api'
);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_workflow_id ON workflow_runs(workflow_id);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_status ON workflow_runs(status);
CREATE INDEX IF NOT EXISTS idx_workflow_runs_started ON workflow_runs(started_at);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def _connect(db_path: str | Path) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def init_db(*, db_path: str | Path) -> None:
    with _connect(db_path) as connection:
        connection.executescript(CREATE_SQL)


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
        if step_type not in {"agent", "tool"}:
            raise ValueError(f"unsupported workflow step type: {step_type}")
        if step_type == "agent" and not str(raw_step.get("agent_id", "")).strip():
            raise ValueError(f"agent step '{step_id}' requires agent_id")
        if step_type == "tool" and not str(raw_step.get("tool_name", "")).strip():
            raise ValueError(f"tool step '{step_id}' requires tool_name")

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
            f"UPDATE workflow_runs SET {', '.join(sets)} WHERE run_id = ?",
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
                 started_at, updated_at, finished_at, trigger_source)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
            ),
        )

    return get_workflow_run(db_path=db_path, run_id=run_id) or {}
