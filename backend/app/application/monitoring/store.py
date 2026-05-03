from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.infrastructure.db.connection import connect_sqlite


DEFAULT_MAX_RUNS_PER_HOUR = 120
DEFAULT_MAX_EXECUTION_SECONDS = 180
DEFAULT_MAX_CONTEXT_TOKENS = 16384
DEFAULT_WORKFLOW_ENGINE_AGENT_ID = "workflow-engine"

CREATE_SQL = """
CREATE TABLE IF NOT EXISTS agent_metrics (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    metric_type TEXT NOT NULL,
    agent_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    workflow_id TEXT NOT NULL DEFAULT '',
    step_id TEXT NOT NULL DEFAULT '',
    ok INTEGER,
    duration_ms INTEGER NOT NULL DEFAULT 0,
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_type ON agent_metrics(metric_type);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_agent ON agent_metrics(agent_id);
CREATE INDEX IF NOT EXISTS idx_agent_metrics_created ON agent_metrics(created_at);

CREATE TABLE IF NOT EXISTS agent_limits (
    agent_id TEXT PRIMARY KEY,
    max_runs_per_hour INTEGER NOT NULL,
    max_execution_seconds INTEGER NOT NULL,
    max_context_tokens INTEGER NOT NULL,
    allowed_tools_json TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL,
    updated_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS resource_usage (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    agent_id TEXT NOT NULL DEFAULT '',
    run_id TEXT NOT NULL DEFAULT '',
    workflow_id TEXT NOT NULL DEFAULT '',
    step_id TEXT NOT NULL DEFAULT '',
    resource TEXT NOT NULL,
    amount REAL NOT NULL DEFAULT 0,
    unit TEXT NOT NULL DEFAULT '',
    details_json TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_resource_usage_agent ON resource_usage(agent_id);
CREATE INDEX IF NOT EXISTS idx_resource_usage_resource ON resource_usage(resource);
CREATE INDEX IF NOT EXISTS idx_resource_usage_created ON resource_usage(created_at);
"""


def now_utc() -> str:
    return datetime.now(timezone.utc).isoformat()


def get_connection(db_path: str | Path) -> sqlite3.Connection:
    return connect_sqlite(db_path)


def init_db(db_path: str | Path) -> None:
    with get_connection(db_path) as con:
        con.executescript(CREATE_SQL)


def dumps_json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False)


def loads_json(raw: Any, default: Any) -> Any:
    if raw in (None, ""):
        return default
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return default


def row_to_limit(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["allowed_tools"] = loads_json(data.pop("allowed_tools_json", "[]"), [])
    return data


def row_to_metric(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = loads_json(data.pop("details_json", "{}"), {})
    if data.get("ok") is not None:
        data["ok"] = bool(data["ok"])
    return data


def row_to_usage(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = loads_json(data.pop("details_json", "{}"), {})
    return data


def planner_tool_aliases() -> list[str]:
    return [
        "web_search",
        "memory_search",
        "library_context",
        "project_mode",
        "project_context",
        "python_executor",
        "project_patch",
    ]


def all_known_tools() -> list[str]:
    tool_names: list[str] = []
    try:
        from app.application.tool_service.runtime import list_tools

        payload = list_tools()
        for item in payload.get("tools", []):
            name = str((item or {}).get("name", "")).strip()
            if name:
                tool_names.append(name)
    except Exception:
        pass

    tool_names.extend(planner_tool_aliases())
    deduped: list[str] = []
    seen: set[str] = set()
    for name in tool_names:
        key = name.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def default_limit_payload(agent_id: str) -> dict[str, Any]:
    timestamp = now_utc()
    return {
        "agent_id": agent_id,
        "max_runs_per_hour": DEFAULT_MAX_RUNS_PER_HOUR,
        "max_execution_seconds": DEFAULT_MAX_EXECUTION_SECONDS,
        "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
        "allowed_tools": all_known_tools(),
        "created_at": timestamp,
        "updated_at": timestamp,
    }


def list_agent_limits(db_path: str | Path) -> list[dict[str, Any]]:
    with get_connection(db_path) as con:
        rows = con.execute("SELECT * FROM agent_limits ORDER BY agent_id").fetchall()
    items = [row_to_limit(row) for row in rows]
    return [item for item in items if item]


def get_agent_limit(db_path: str | Path, agent_id: str) -> dict[str, Any] | None:
    with get_connection(db_path) as con:
        row = con.execute(
            "SELECT * FROM agent_limits WHERE agent_id = ?",
            (agent_id,),
        ).fetchone()
    return row_to_limit(row)


def upsert_limit(db_path: str | Path, payload: dict[str, Any]) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", "")).strip()
    if not agent_id:
        raise ValueError("agent_id is required")

    timestamp = now_utc()
    existing = get_agent_limit(db_path, agent_id)
    created_at = existing["created_at"] if existing else payload.get("created_at", timestamp)
    with get_connection(db_path) as con:
        con.execute(
            """
            INSERT INTO agent_limits
                (agent_id, max_runs_per_hour, max_execution_seconds, max_context_tokens,
                 allowed_tools_json, created_at, updated_at)
            VALUES (?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(agent_id) DO UPDATE SET
                max_runs_per_hour = excluded.max_runs_per_hour,
                max_execution_seconds = excluded.max_execution_seconds,
                max_context_tokens = excluded.max_context_tokens,
                allowed_tools_json = excluded.allowed_tools_json,
                updated_at = excluded.updated_at
            """,
            (
                agent_id,
                int(payload.get("max_runs_per_hour", DEFAULT_MAX_RUNS_PER_HOUR)),
                int(payload.get("max_execution_seconds", DEFAULT_MAX_EXECUTION_SECONDS)),
                int(payload.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS)),
                dumps_json(payload.get("allowed_tools", [])),
                str(created_at),
                timestamp,
            ),
        )
    return get_agent_limit(db_path, agent_id) or {}


def record_metric(
    db_path: str | Path,
    *,
    metric_type: str,
    agent_id: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    ok: bool | None = None,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or now_utc()
    with get_connection(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO agent_metrics
                (metric_type, agent_id, run_id, workflow_id, step_id, ok, duration_ms, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(metric_type or ""),
                str(agent_id or ""),
                str(run_id or ""),
                str(workflow_id or ""),
                str(step_id or ""),
                None if ok is None else (1 if ok else 0),
                int(duration_ms or 0),
                dumps_json(details or {}),
                timestamp,
            ),
        )
        row = con.execute(
            "SELECT * FROM agent_metrics WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_metric(row) or {}


def record_resource_usage(
    db_path: str | Path,
    *,
    agent_id: str,
    resource: str,
    amount: float,
    unit: str = "",
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    details: dict[str, Any] | None = None,
    created_at: str | None = None,
) -> dict[str, Any]:
    timestamp = created_at or now_utc()
    with get_connection(db_path) as con:
        cursor = con.execute(
            """
            INSERT INTO resource_usage
                (agent_id, run_id, workflow_id, step_id, resource, amount, unit, details_json, created_at)
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                str(agent_id or ""),
                str(run_id or ""),
                str(workflow_id or ""),
                str(step_id or ""),
                str(resource or ""),
                float(amount or 0),
                str(unit or ""),
                dumps_json(details or {}),
                timestamp,
            ),
        )
        row = con.execute(
            "SELECT * FROM resource_usage WHERE id = ?",
            (cursor.lastrowid,),
        ).fetchone()
    return row_to_usage(row) or {}


def record_agent_run_metric(
    db_path: str | Path,
    *,
    agent_id: str,
    run_id: str,
    route: str,
    model_name: str,
    ok: bool,
    duration_ms: int,
    streaming: bool = False,
    num_ctx: int = 0,
    tools: list[str] | None = None,
) -> None:
    record_metric(
        db_path,
        metric_type="agent.run",
        agent_id=agent_id,
        run_id=run_id,
        ok=ok,
        duration_ms=duration_ms,
        details={
            "route": route,
            "model_name": model_name,
            "streaming": streaming,
            "tools": list(tools or []),
            "num_ctx": int(num_ctx or 0),
        },
    )
    if num_ctx:
        record_resource_usage(
            db_path,
            agent_id=agent_id,
            run_id=run_id,
            resource="context_tokens",
            amount=int(num_ctx),
            unit="tokens",
            details={"route": route},
        )
    record_resource_usage(
        db_path,
        agent_id=agent_id,
        run_id=run_id,
        resource="selected_tools",
        amount=len(list(tools or [])),
        unit="count",
        details={"route": route},
    )


def record_workflow_run_metric(
    db_path: str | Path,
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
    workflow_engine_agent_id: str = DEFAULT_WORKFLOW_ENGINE_AGENT_ID,
) -> None:
    record_metric(
        db_path,
        metric_type="workflow.run",
        agent_id=workflow_engine_agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        ok=status == "completed",
        duration_ms=duration_ms,
        details={"status": status, **(details or {})},
    )


def record_workflow_step_metric(
    db_path: str | Path,
    *,
    agent_id: str,
    workflow_id: str,
    run_id: str,
    step_id: str,
    step_type: str,
    ok: bool,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    record_metric(
        db_path,
        metric_type="workflow.step",
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=ok,
        duration_ms=duration_ms,
        details={"step_type": step_type, **(details or {})},
    )


def count_agent_runs_last_hour(db_path: str | Path, agent_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with get_connection(db_path) as con:
        row = con.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM agent_metrics
            WHERE metric_type = 'agent.run' AND agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def get_recent_blocked_runs(
    db_path: str | Path,
    hours: int = 24,
    limit: int = 10,
) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with get_connection(db_path) as con:
        rows = con.execute(
            """
            SELECT * FROM agent_metrics
            WHERE metric_type = 'sandbox.blocked' AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (since, max(1, int(limit))),
        ).fetchall()
    items = [row_to_metric(row) for row in rows]
    return [item for item in items if item]
