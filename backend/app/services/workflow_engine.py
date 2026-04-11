"""Legacy workflow-engine monolith kept for compatibility during extraction."""
from __future__ import annotations

import json
import sqlite3
import time
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Formatter
from typing import Any, Callable

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite
from app.services.agent_monitor import (
    WORKFLOW_ENGINE_AGENT_ID,
    record_resource_usage,
    record_workflow_run_metric,
    record_workflow_step_metric,
)
from app.services.agent_sandbox import SandboxPolicyError, preflight_or_raise


DB_PATH: Path = sqlite_data_file("workflow_engine.db")

TERMINAL_STATUSES = {"completed", "failed", "paused", "cancelled"}
STEP_SUCCESS = "on_success"
STEP_FAILURE = "on_failure"

MULTI_AGENT_DEFAULT_WORKFLOW_ID = "builtin.workflow.multi_agent.default"
MULTI_AGENT_REFLECTION_WORKFLOW_ID = "builtin.workflow.multi_agent.reflection"
MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID = "builtin.workflow.multi_agent.orchestrated"
MULTI_AGENT_FULL_WORKFLOW_ID = "builtin.workflow.multi_agent.full"

_CREATE_SQL = """
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


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _conn() -> sqlite3.Connection:
    return connect_sqlite(DB_PATH)


def _init_db() -> None:
    with _conn() as con:
        con.executescript(_CREATE_SQL)


_init_db()


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


def _normalize_graph(graph: dict[str, Any]) -> dict[str, Any]:
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


def _upsert_workflow_template(template: dict[str, Any]) -> dict[str, Any]:
    workflow_id = str(template.get("id") or f"workflow-{uuid.uuid4().hex[:10]}")
    now = _now()
    existing = get_workflow_template(workflow_id)
    created_at = existing["created_at"] if existing else now
    graph = _normalize_graph(template.get("graph", {}))

    with _conn() as con:
        con.execute(
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

    return get_workflow_template(workflow_id) or {}


def create_workflow_template(template: dict[str, Any]) -> dict[str, Any]:
    return _upsert_workflow_template(template)


def get_workflow_template(workflow_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM workflow_templates WHERE id = ?", (workflow_id,)).fetchone()
    return _row_to_template(row)


def list_workflow_templates(
    *,
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

    with _conn() as con:
        total_row = con.execute(f"SELECT COUNT(*) AS cnt FROM workflow_templates {where}", params).fetchone()
        rows = con.execute(f"SELECT * FROM workflow_templates {where} ORDER BY source, name, id", params).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    items = [_row_to_template(row) for row in rows]
    return [item for item in items if item], total


def update_workflow_template(workflow_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    existing = get_workflow_template(workflow_id)
    if not existing:
        raise ValueError(f"Workflow '{workflow_id}' not found")

    merged = {**existing, **{k: v for k, v in updates.items() if v is not None}}
    merged["id"] = workflow_id
    return _upsert_workflow_template(merged)


def delete_workflow_template(workflow_id: str) -> dict[str, Any]:
    with _conn() as con:
        cursor = con.execute("DELETE FROM workflow_templates WHERE id = ?", (workflow_id,))
    return {"workflow_id": workflow_id, "removed": cursor.rowcount > 0}


def get_workflow_run(run_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM workflow_runs WHERE run_id = ?", (run_id,)).fetchone()
    return _row_to_run(row)


def list_workflow_runs(
    *,
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

    with _conn() as con:
        total_row = con.execute(f"SELECT COUNT(*) AS cnt FROM workflow_runs {where}", params).fetchone()
        rows = con.execute(
            f"SELECT * FROM workflow_runs {where} ORDER BY started_at DESC, id DESC LIMIT ? OFFSET ?",
            [*params, max(1, int(limit)), max(0, int(offset))],
        ).fetchall()

    total = int(total_row["cnt"]) if total_row else 0
    runs = [_row_to_run(row) for row in rows]
    return [run for run in runs if run], total


def _update_workflow_run(run_id: str, **fields: Any) -> dict[str, Any]:
    if not fields:
        return get_workflow_run(run_id) or {}

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
        params.append(_now())

    params.append(run_id)

    with _conn() as con:
        con.execute(f"UPDATE workflow_runs SET {', '.join(sets)} WHERE run_id = ?", params)

    return get_workflow_run(run_id) or {}


def _create_workflow_run_record(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
) -> dict[str, Any]:
    template = get_workflow_template(workflow_id)
    if not template:
        raise ValueError(f"Workflow '{workflow_id}' not found")
    if not template.get("enabled", True):
        raise ValueError(f"Workflow '{workflow_id}' is disabled")

    graph = template.get("graph", {})
    entry_step = str(graph.get("entry_step", "")).strip()
    run_id = f"wfr-{uuid.uuid4().hex}"
    now = _now()
    input_payload = workflow_input or {}
    context_payload = context or {}

    with _conn() as con:
        con.execute(
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

    return get_workflow_run(run_id) or {}


def _emit_workflow_event(event_type: str, workflow_id: str, run_id: str, payload: dict[str, Any] | None = None) -> None:
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type=event_type,
            source_agent_id=workflow_id,
            payload={"workflow_id": workflow_id, "run_id": run_id, **(payload or {})},
        )
    except Exception:
        return


def _parse_dt(raw: str | None) -> datetime | None:
    if not raw:
        return None
    try:
        parsed = datetime.fromisoformat(str(raw))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def _workflow_duration_ms(run: dict[str, Any]) -> int:
    started_at = _parse_dt(str(run.get("started_at", "")))
    if not started_at:
        return 0
    return max(0, int((datetime.now(timezone.utc) - started_at).total_seconds() * 1000))


def _record_workflow_run_state(run: dict[str, Any], *, status: str, details: dict[str, Any] | None = None) -> None:
    try:
        record_workflow_run_metric(
            workflow_id=str(run.get("workflow_id", "")),
            run_id=str(run.get("run_id", "")),
            status=status,
            duration_ms=_workflow_duration_ms(run),
            details=details or {},
        )
    except Exception:
        return


def _resolve_path(data: Any, path: str) -> Any:
    current = data
    for part in path.split("."):
        if not part:
            continue
        if isinstance(current, dict):
            current = current.get(part)
        else:
            return None
    return current


def _resolve_input_expression(
    expression: Any,
    *,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
) -> Any:
    if not isinstance(expression, str):
        return expression

    if expression.startswith("$.input."):
        return _resolve_path(workflow_input, expression[len("$.input."):])
    if expression == "$.input":
        return workflow_input
    if expression.startswith("$.context."):
        return _resolve_path(context, expression[len("$.context."):])
    if expression == "$.context":
        return context
    if expression.startswith("$.steps."):
        return _resolve_path(step_results, expression[len("$.steps."):])
    if expression == "$.steps":
        return step_results
    return expression


def _map_step_inputs(
    step: dict[str, Any],
    *,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
) -> dict[str, Any]:
    mapped: dict[str, Any] = {}
    for key, expr in (step.get("input_map") or {}).items():
        mapped[key] = _resolve_input_expression(
            expr,
            workflow_input=workflow_input,
            context=context,
            step_results=step_results,
        )
    return mapped


def _stringify_template_value(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, (dict, list)):
        return json.dumps(value, ensure_ascii=False, indent=2)
    return str(value)


class _SafeFormatDict(dict):
    def __missing__(self, key: str) -> str:
        return ""


def _render_prompt_template(template: str, values: dict[str, Any]) -> str:
    prepared = {key: _stringify_template_value(value) for key, value in values.items()}
    required = {field_name for _, field_name, _, _ in Formatter().parse(template) if field_name}
    for field in required:
        prepared.setdefault(field, "")
    return template.format_map(_SafeFormatDict(prepared))


def _determine_profile_name(agent_id: str, config: dict[str, Any]) -> str:
    profile_name = str(config.get("profile_name", "")).strip()
    if profile_name:
        return profile_name

    fallback_map = {
        "builtin-universal": "Универсальный",
        "builtin-researcher": "Исследователь",
        "builtin-programmer": "Программист",
        "builtin-analyst": "Аналитик",
        "builtin-socrat": "Сократ",
        "builtin-orchestrator": "Универсальный",
        "builtin-reviewer": "Аналитик",
    }
    return fallback_map.get(agent_id, "Универсальный")


def _execute_agent_step(
    step: dict[str, Any],
    mapped_inputs: dict[str, Any],
    run_context: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    from app.services.agents_service import run_agent

    config = step.get("config", {}) or {}
    prompt_template = str(config.get("prompt_template", "")).strip()
    prompt = _render_prompt_template(prompt_template, mapped_inputs) if prompt_template else _stringify_template_value(mapped_inputs)
    model_name = str(config.get("model_name") or run_context.get("model_name") or "gemma3:4b")
    profile_name = _determine_profile_name(str(step.get("agent_id", "")), config)
    result = run_agent(
        model_name=model_name,
        profile_name=profile_name,
        user_input=prompt,
        session_id=f"{run_id}:{step['id']}",
        agent_id=str(step.get("agent_id", "")).strip() or None,
        use_memory=bool(config.get("use_memory", False)),
        use_library=bool(config.get("use_library", False)),
        use_reflection=bool(config.get("use_reflection", False)),
        use_web_search=bool(config.get("use_web_search", False)),
        use_python_exec=bool(config.get("use_python_exec", False)),
        use_image_gen=bool(config.get("use_image_gen", False)),
        use_file_gen=bool(config.get("use_file_gen", False)),
        use_http_api=bool(config.get("use_http_api", False)),
        use_sql=bool(config.get("use_sql", False)),
        use_screenshot=bool(config.get("use_screenshot", False)),
        use_encrypt=bool(config.get("use_encrypt", False)),
        use_archiver=bool(config.get("use_archiver", False)),
        use_converter=bool(config.get("use_converter", False)),
        use_regex=bool(config.get("use_regex", False)),
        use_translator=bool(config.get("use_translator", False)),
        use_csv=bool(config.get("use_csv", False)),
        use_webhook=bool(config.get("use_webhook", False)),
        use_plugins=bool(config.get("use_plugins", False)),
    )

    answer = str(result.get("answer", ""))
    return {
        "ok": bool(result.get("ok")),
        "answer": answer,
        "agent_id": str(step.get("agent_id", "")),
        "profile_name": profile_name,
        "prompt": prompt,
        "meta": result.get("meta", {}),
        "timeline": result.get("timeline", []),
        "tool_results": result.get("tool_results", []),
        "raw": result,
        "error": result.get("meta", {}).get("error", "") if not result.get("ok") else "",
    }


def _execute_tool_step(
    step: dict[str, Any],
    mapped_inputs: dict[str, Any],
    run_context: dict[str, Any],
    workflow_id: str,
    run_id: str,
) -> dict[str, Any]:
    from app.services.tool_service import run_tool

    tool_name = str(step.get("tool_name", "")).strip()
    args = mapped_inputs if isinstance(mapped_inputs, dict) else {"input": mapped_inputs}
    preflight_or_raise(
        agent_id=WORKFLOW_ENGINE_AGENT_ID,
        num_ctx=int(run_context.get("num_ctx") or 0),
        selected_tools=[tool_name],
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=str(step.get("id", "")),
        route="workflow.tool",
        streaming=False,
    )
    result = run_tool(tool_name, args)
    ok = bool(result.get("ok"))
    _emit_workflow_event(
        "tool.executed",
        workflow_id,
        run_id,
        payload={"step_id": step["id"], "tool_name": tool_name, "ok": ok},
    )
    return {
        "ok": ok,
        "tool_name": tool_name,
        "output": result,
        "raw": result,
        "error": result.get("error", "") if not ok else "",
    }


def _execute_step(
    step: dict[str, Any],
    *,
    workflow_id: str,
    workflow_input: dict[str, Any],
    context: dict[str, Any],
    step_results: dict[str, Any],
    run_id: str,
) -> dict[str, Any]:
    mapped_inputs = _map_step_inputs(
        step,
        workflow_input=workflow_input,
        context=context,
        step_results=step_results,
    )
    if step["type"] == "agent":
        return _execute_agent_step(step, mapped_inputs, context, run_id)
    return _execute_tool_step(step, mapped_inputs, context, workflow_id, run_id)


def _resolve_next_step(step: dict[str, Any], *, success: bool) -> str:
    transitions = step.get("next")
    desired = STEP_SUCCESS if success else STEP_FAILURE
    if isinstance(transitions, str):
        return transitions.strip()
    if isinstance(transitions, list):
        for transition in transitions:
            when = str(transition.get("when", "always")).strip()
            if when == "always" or when == desired:
                return str(transition.get("to", "")).strip()
    if not success:
        return str(step.get("on_error", "")).strip()
    return ""


def _step_label(step: dict[str, Any]) -> str:
    config = step.get("config", {}) or {}
    return str(config.get("label") or step.get("save_as") or step.get("id"))


def _run_state_for_execution(run: dict[str, Any], template: dict[str, Any]) -> tuple[dict[str, Any], dict[str, dict[str, Any]], list[str]]:
    graph = template.get("graph", {})
    steps = graph.get("steps", []) if isinstance(graph, dict) else []
    steps_by_id = {str(step.get("id")): step for step in steps}
    ordered_ids = [str(step.get("id")) for step in steps]
    return graph, steps_by_id, ordered_ids


def _execute_workflow_run(
    run_id: str,
    *,
    resume_event: bool = False,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")

    template = get_workflow_template(run["workflow_id"])
    if not template:
        raise ValueError(f"Workflow '{run['workflow_id']}' not found")

    _, steps_by_id, ordered_ids = _run_state_for_execution(run, template)
    total_steps = len(ordered_ids)
    workflow_input = run.get("input", {})
    context = run.get("context", {})
    step_results = run.get("step_results", {})
    current_step_id = str(run.get("current_step_id", "")).strip()
    run_state = get_workflow_run(run_id) or run

    if resume_event:
        _emit_workflow_event("workflow.run.resumed", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _record_workflow_run_state(run_state, status="resumed", details={"current_step_id": current_step_id})
    else:
        _emit_workflow_event("workflow.run.started", run["workflow_id"], run_id, payload={"current_step_id": current_step_id})
        _record_workflow_run_state(run_state, status="started", details={"current_step_id": current_step_id, "total_steps": total_steps})
        try:
            record_resource_usage(
                agent_id=WORKFLOW_ENGINE_AGENT_ID,
                run_id=run_id,
                workflow_id=run["workflow_id"],
                resource="workflow_total_steps",
                amount=total_steps,
                unit="count",
                details={"trigger_source": run.get("trigger_source", "api")},
            )
        except Exception:
            pass

    while current_step_id:
        step = steps_by_id.get(current_step_id)
        if not step:
            error = {"message": f"Workflow step '{current_step_id}' not found"}
            failed_run = _update_workflow_run(
                run_id,
                status="failed",
                current_step_id=current_step_id,
                error=error,
                finished_at=_now(),
            )
            _record_workflow_run_state(failed_run, status="failed", details={"current_step_id": current_step_id, "reason": "step_not_found"})
            _emit_workflow_event("workflow.step.failed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "error": error["message"]})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": False, "status": "failed"})
            return failed_run

        step_index = ordered_ids.index(current_step_id) + 1 if current_step_id in ordered_ids else len(step_results) + 1
        if progress_callback:
            progress_callback(step_index, total_steps, _step_label(step))

        _emit_workflow_event("workflow.step.started", run["workflow_id"], run_id, payload={"step_id": current_step_id, "index": step_index})
        step_started = time.monotonic()
        try:
            step_result = _execute_step(
                step,
                workflow_id=run["workflow_id"],
                workflow_input=workflow_input,
                context=context,
                step_results=step_results,
                run_id=run_id,
            )
        except SandboxPolicyError as exc:
            step_result = {
                "ok": False,
                "error": str(exc),
                "sandbox_reason": exc.reason,
                "sandbox_details": exc.details,
                "raw": {"ok": False, "error": str(exc)},
            }
        except Exception as exc:
            step_result = {
                "ok": False,
                "error": str(exc),
                "raw": {"ok": False, "error": str(exc)},
            }
        step_duration_ms = int((time.monotonic() - step_started) * 1000)

        save_key = str(step.get("save_as") or current_step_id)
        step_results[save_key] = step_result
        success = bool(step_result.get("ok"))
        next_step_id = _resolve_next_step(step, success=success)
        step_agent_id = str(step.get("agent_id", "")).strip() if step.get("type") == "agent" else WORKFLOW_ENGINE_AGENT_ID
        step_details = {
            "label": _step_label(step),
            "index": step_index,
            "next_step_id": next_step_id or None,
        }
        if step.get("type") == "tool":
            step_details["tool_name"] = str(step.get("tool_name", "")).strip()
        if step_result.get("error"):
            step_details["error"] = str(step_result.get("error", ""))[:500]
        if step_result.get("sandbox_reason"):
            step_details["sandbox_reason"] = step_result.get("sandbox_reason")
        try:
            record_workflow_step_metric(
                agent_id=step_agent_id,
                workflow_id=run["workflow_id"],
                run_id=run_id,
                step_id=current_step_id,
                step_type=str(step.get("type", "")),
                ok=success,
                duration_ms=step_duration_ms,
                details=step_details,
            )
            record_resource_usage(
                agent_id=step_agent_id,
                run_id=run_id,
                workflow_id=run["workflow_id"],
                step_id=current_step_id,
                resource="step_duration",
                amount=step_duration_ms,
                unit="ms",
                details={"step_type": str(step.get("type", "")), "label": _step_label(step)},
            )
        except Exception:
            pass

        if success:
            _emit_workflow_event("workflow.step.completed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "save_as": save_key, "next_step_id": next_step_id or None})
        else:
            _emit_workflow_event("workflow.step.failed", run["workflow_id"], run_id, payload={"step_id": current_step_id, "error": step_result.get("error", ""), "next_step_id": next_step_id or None})

        should_pause = bool(step.get("pause_after")) or bool(step_result.get("pause_requested"))
        if should_pause and success and next_step_id:
            paused_run = _update_workflow_run(
                run_id,
                status="paused",
                current_step_id=next_step_id,
                step_results=step_results,
                pending_steps=[next_step_id],
                requested_pause=False,
            )
            _record_workflow_run_state(paused_run, status="paused", details={"current_step_id": next_step_id, "after_step_id": current_step_id})
            _emit_workflow_event("workflow.run.paused", run["workflow_id"], run_id, payload={"current_step_id": next_step_id})
            return paused_run

        if not success and not next_step_id:
            failed_run = _update_workflow_run(
                run_id,
                status="failed",
                current_step_id=current_step_id,
                step_results=step_results,
                pending_steps=[],
                error={"step_id": current_step_id, "message": step_result.get("error", "step failed")},
                finished_at=_now(),
            )
            _record_workflow_run_state(failed_run, status="failed", details={"current_step_id": current_step_id})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": False, "status": "failed", "step_id": current_step_id})
            return failed_run

        if not next_step_id:
            completed_run = _update_workflow_run(
                run_id,
                status="completed",
                current_step_id="",
                step_results=step_results,
                pending_steps=[],
                error={},
                finished_at=_now(),
            )
            _record_workflow_run_state(completed_run, status="completed", details={"completed_from_step_id": current_step_id})
            _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": True, "status": "completed"})
            return completed_run

        current_step_id = next_step_id
        _update_workflow_run(
            run_id,
            status="running",
            current_step_id=current_step_id,
            step_results=step_results,
            pending_steps=[current_step_id],
            error={},
        )

    completed_run = _update_workflow_run(run_id, status="completed", current_step_id="", pending_steps=[], finished_at=_now())
    _record_workflow_run_state(completed_run, status="completed", details={"completed_from_step_id": ""})
    _emit_workflow_event("workflow.run.completed", run["workflow_id"], run_id, payload={"ok": True, "status": "completed"})
    return completed_run


def start_workflow_run(
    *,
    workflow_id: str,
    workflow_input: dict[str, Any] | None = None,
    context: dict[str, Any] | None = None,
    trigger_source: str = "api",
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = _create_workflow_run_record(
        workflow_id=workflow_id,
        workflow_input=workflow_input,
        context=context,
        trigger_source=trigger_source,
    )
    return _execute_workflow_run(run["run_id"], progress_callback=progress_callback)


def resume_workflow_run(
    run_id: str,
    *,
    context_patch: dict[str, Any] | None = None,
    progress_callback: Callable[[int, int, str], None] | None = None,
) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] != "paused":
        raise ValueError("Only paused workflow runs can be resumed")

    merged_context = dict(run.get("context", {}))
    merged_context.update(context_patch or {})
    _update_workflow_run(run_id, status="running", context=merged_context, error={}, requested_pause=False)
    return _execute_workflow_run(run_id, resume_event=True, progress_callback=progress_callback)


def cancel_workflow_run(run_id: str) -> dict[str, Any]:
    run = get_workflow_run(run_id)
    if not run:
        raise ValueError(f"Workflow run '{run_id}' not found")
    if run["status"] in {"completed", "failed", "cancelled"}:
        raise ValueError("Terminal workflow runs cannot be cancelled")

    cancelled = _update_workflow_run(run_id, status="cancelled", pending_steps=[], finished_at=_now())
    _record_workflow_run_state(cancelled, status="cancelled", details={"current_step_id": cancelled.get("current_step_id", "")})
    _emit_workflow_event("workflow.run.cancelled", cancelled["workflow_id"], run_id, payload={"status": "cancelled"})
    return cancelled




# ═══════════════════════════════════════════════════════════════
# MULTI-AGENT WORKFLOWS -- extracted to application/workflows/multi_agent.py
# ═══════════════════════════════════════════════════════════════

from app.application.workflows.multi_agent import (  # noqa: E402
    MULTI_AGENT_DEFAULT_WORKFLOW_ID,
    MULTI_AGENT_REFLECTION_WORKFLOW_ID,
    MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
    MULTI_AGENT_FULL_WORKFLOW_ID,
    seed_builtin_workflows,
    run_multi_agent_workflow,
    run_legacy_multi_agent_workflow,
)
