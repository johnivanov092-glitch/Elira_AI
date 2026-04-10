from __future__ import annotations

import json
import sqlite3
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from app.core.data_files import sqlite_data_file
from app.infrastructure.db.connection import connect_sqlite


DB_PATH: Path = sqlite_data_file("agent_monitor.db")

DEFAULT_MAX_RUNS_PER_HOUR = 120
DEFAULT_MAX_EXECUTION_SECONDS = 180
DEFAULT_MAX_CONTEXT_TOKENS = 16384
WORKFLOW_ENGINE_AGENT_ID = "workflow-engine"
_LIMIT_SEED_DONE = False

_CREATE_SQL = """
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


def _row_to_limit(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["allowed_tools"] = _loads(data.pop("allowed_tools_json", "[]"), [])
    return data


def _row_to_metric(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = _loads(data.pop("details_json", "{}"), {})
    if data.get("ok") is not None:
        data["ok"] = bool(data["ok"])
    return data


def _row_to_usage(row: sqlite3.Row | None) -> dict[str, Any] | None:
    if not row:
        return None
    data = dict(row)
    data["details"] = _loads(data.pop("details_json", "{}"), {})
    return data


def _planner_tool_aliases() -> list[str]:
    return [
        "web_search",
        "memory_search",
        "library_context",
        "project_mode",
        "project_context",
        "python_executor",
        "project_patch",
    ]


def _all_known_tools() -> list[str]:
    tool_names: list[str] = []
    try:
        from app.services.tool_service import list_tools

        payload = list_tools()
        for item in payload.get("tools", []):
            name = str((item or {}).get("name", "")).strip()
            if name:
                tool_names.append(name)
    except Exception:
        pass

    tool_names.extend(_planner_tool_aliases())
    deduped: list[str] = []
    seen: set[str] = set()
    for name in tool_names:
        key = name.strip()
        if not key or key in seen:
            continue
        seen.add(key)
        deduped.append(key)
    return deduped


def _default_limit_payload(agent_id: str) -> dict[str, Any]:
    now = _now()
    return {
        "agent_id": agent_id,
        "max_runs_per_hour": DEFAULT_MAX_RUNS_PER_HOUR,
        "max_execution_seconds": DEFAULT_MAX_EXECUTION_SECONDS,
        "max_context_tokens": DEFAULT_MAX_CONTEXT_TOKENS,
        "allowed_tools": _all_known_tools(),
        "created_at": now,
        "updated_at": now,
    }


def _upsert_limit(payload: dict[str, Any], *, emit_event_bus: bool = False) -> dict[str, Any]:
    agent_id = str(payload.get("agent_id", "")).strip()
    if not agent_id:
        raise ValueError("agent_id is required")

    now = _now()
    existing = get_agent_limit(agent_id)
    created_at = existing["created_at"] if existing else now
    with _conn() as con:
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
                _dumps(payload.get("allowed_tools", [])),
                created_at,
                now,
            ),
        )

    updated = get_agent_limit(agent_id) or {}
    if emit_event_bus:
        try:
            from app.services.event_bus import emit_event

            emit_event(
                event_type="agent.limit.updated",
                source_agent_id=agent_id,
                payload={
                    "agent_id": agent_id,
                    "max_runs_per_hour": updated.get("max_runs_per_hour", DEFAULT_MAX_RUNS_PER_HOUR),
                    "max_execution_seconds": updated.get("max_execution_seconds", DEFAULT_MAX_EXECUTION_SECONDS),
                    "max_context_tokens": updated.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS),
                    "allowed_tools": updated.get("allowed_tools", []),
                },
            )
        except Exception:
            pass
        record_metric(
            metric_type="agent.limit.updated",
            agent_id=agent_id,
            ok=True,
            details={"agent_id": agent_id},
        )
    return updated


def seed_default_limits() -> int:
    global _LIMIT_SEED_DONE
    if _LIMIT_SEED_DONE:
        return 0

    created = 0
    agent_ids: list[str] = []
    try:
        from app.services.agent_registry import list_agents, seed_builtin_agents

        seed_builtin_agents()
        agent_ids = [str(item.get("id", "")).strip() for item in list_agents(enabled_only=False)]
    except Exception:
        agent_ids = []

    if WORKFLOW_ENGINE_AGENT_ID not in agent_ids:
        agent_ids.append(WORKFLOW_ENGINE_AGENT_ID)

    for agent_id in agent_ids:
        if not agent_id:
            continue
        if get_agent_limit(agent_id):
            continue
        _upsert_limit(_default_limit_payload(agent_id))
        created += 1

    _LIMIT_SEED_DONE = True
    return created


def list_agent_limits() -> list[dict[str, Any]]:
    seed_default_limits()
    with _conn() as con:
        rows = con.execute("SELECT * FROM agent_limits ORDER BY agent_id").fetchall()
    items = [_row_to_limit(row) for row in rows]
    return [item for item in items if item]


def get_agent_limit(agent_id: str) -> dict[str, Any] | None:
    with _conn() as con:
        row = con.execute("SELECT * FROM agent_limits WHERE agent_id = ?", (agent_id,)).fetchone()
    return _row_to_limit(row)


def ensure_agent_limit(agent_id: str) -> dict[str, Any]:
    seed_default_limits()
    existing = get_agent_limit(agent_id)
    if existing:
        return existing
    return _upsert_limit(_default_limit_payload(agent_id))


def update_agent_limit(agent_id: str, updates: dict[str, Any]) -> dict[str, Any]:
    current = ensure_agent_limit(agent_id)
    merged = {
        "agent_id": agent_id,
        "max_runs_per_hour": int(updates.get("max_runs_per_hour", current.get("max_runs_per_hour", DEFAULT_MAX_RUNS_PER_HOUR))),
        "max_execution_seconds": int(updates.get("max_execution_seconds", current.get("max_execution_seconds", DEFAULT_MAX_EXECUTION_SECONDS))),
        "max_context_tokens": int(updates.get("max_context_tokens", current.get("max_context_tokens", DEFAULT_MAX_CONTEXT_TOKENS))),
        "allowed_tools": list(updates.get("allowed_tools", current.get("allowed_tools", []))),
    }
    return _upsert_limit(merged, emit_event_bus=True)


def record_metric(
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
    timestamp = created_at or _now()
    with _conn() as con:
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
                _dumps(details or {}),
                timestamp,
            ),
        )
        row = con.execute("SELECT * FROM agent_metrics WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_metric(row) or {}


def record_resource_usage(
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
    timestamp = created_at or _now()
    with _conn() as con:
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
                _dumps(details or {}),
                timestamp,
            ),
        )
        row = con.execute("SELECT * FROM resource_usage WHERE id = ?", (cursor.lastrowid,)).fetchone()
    return _row_to_usage(row) or {}


def record_agent_run_metric(
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
            agent_id=agent_id,
            run_id=run_id,
            resource="context_tokens",
            amount=int(num_ctx),
            unit="tokens",
            details={"route": route},
        )
    record_resource_usage(
        agent_id=agent_id,
        run_id=run_id,
        resource="selected_tools",
        amount=len(list(tools or [])),
        unit="count",
        details={"route": route},
    )


def record_workflow_run_metric(
    *,
    workflow_id: str,
    run_id: str,
    status: str,
    duration_ms: int = 0,
    details: dict[str, Any] | None = None,
) -> None:
    record_metric(
        metric_type="workflow.run",
        agent_id=WORKFLOW_ENGINE_AGENT_ID,
        run_id=run_id,
        workflow_id=workflow_id,
        ok=status == "completed",
        duration_ms=duration_ms,
        details={"status": status, **(details or {})},
    )


def record_workflow_step_metric(
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
        metric_type="workflow.step",
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=ok,
        duration_ms=duration_ms,
        details={"step_type": step_type, **(details or {})},
    )


def record_sandbox_block(
    *,
    agent_id: str,
    reason: str,
    run_id: str = "",
    workflow_id: str = "",
    step_id: str = "",
    details: dict[str, Any] | None = None,
) -> None:
    payload = {"reason": reason, **(details or {})}
    record_metric(
        metric_type="sandbox.blocked",
        agent_id=agent_id,
        run_id=run_id,
        workflow_id=workflow_id,
        step_id=step_id,
        ok=False,
        details=payload,
    )
    try:
        from app.services.event_bus import emit_event

        emit_event(
            event_type="sandbox.policy.blocked",
            source_agent_id=agent_id,
            payload={"agent_id": agent_id, "run_id": run_id, "workflow_id": workflow_id, "step_id": step_id, **payload},
        )
    except Exception:
        pass


def count_agent_runs_last_hour(agent_id: str) -> int:
    since = (datetime.now(timezone.utc) - timedelta(hours=1)).isoformat()
    with _conn() as con:
        row = con.execute(
            """
            SELECT COUNT(*) AS cnt
            FROM agent_metrics
            WHERE metric_type = 'agent.run' AND agent_id = ? AND created_at >= ?
            """,
            (agent_id, since),
        ).fetchone()
    return int(row["cnt"]) if row else 0


def get_recent_blocked_runs(hours: int = 24, limit: int = 10) -> list[dict[str, Any]]:
    since = (datetime.now(timezone.utc) - timedelta(hours=hours)).isoformat()
    with _conn() as con:
        rows = con.execute(
            """
            SELECT * FROM agent_metrics
            WHERE metric_type = 'sandbox.blocked' AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT ?
            """,
            (since, max(1, int(limit))),
        ).fetchall()
    items = [_row_to_metric(row) for row in rows]
    return [item for item in items if item]


def get_agent_os_health() -> dict[str, Any]:
    seed_default_limits()
    components: list[dict[str, Any]] = []

    checks = [
        ("agent_registry", lambda: __import__("app.services.agent_registry", fromlist=["list_agents"]).list_agents(enabled_only=False)),
        ("event_bus", lambda: __import__("app.services.event_bus", fromlist=["list_events"]).list_events(limit=1)),
        ("workflow_engine", lambda: __import__("app.services.workflow_engine", fromlist=["list_workflow_templates"]).list_workflow_templates(include_disabled=True)),
        ("agent_monitor", lambda: list_agent_limits()),
    ]

    for name, fn in checks:
        try:
            result = fn()
            detail = ""
            if isinstance(result, tuple):
                maybe_count = result[1] if len(result) > 1 else 0
                detail = f"available ({maybe_count})"
            elif isinstance(result, list):
                detail = f"available ({len(result)})"
            else:
                detail = "available"
            components.append({"component": name, "ok": True, "detail": detail})
        except Exception as exc:
            components.append({"component": name, "ok": False, "detail": str(exc)})

    ok = all(item["ok"] for item in components)
    warnings = [item["detail"] for item in components if not item["ok"]]
    return {"ok": ok, "components": components, "warnings": warnings}


def get_agent_os_dashboard(window_hours: int = 24) -> dict[str, Any]:
    seed_default_limits()
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    with _conn() as con:
        totals = {
            "total_agent_runs": int(
                (con.execute(
                    "SELECT COUNT(DISTINCT run_id) AS cnt FROM agent_metrics WHERE metric_type = 'agent.run' AND created_at >= ?",
                    (since,),
                ).fetchone() or {"cnt": 0})["cnt"]
            ),
            "blocked_runs": int(
                (con.execute(
                    "SELECT COUNT(*) AS cnt FROM agent_metrics WHERE metric_type = 'sandbox.blocked' AND created_at >= ?",
                    (since,),
                ).fetchone() or {"cnt": 0})["cnt"]
            ),
            "workflow_runs": int(
                (con.execute(
                    "SELECT COUNT(DISTINCT run_id) AS cnt FROM agent_metrics WHERE metric_type = 'workflow.run' AND created_at >= ?",
                    (since,),
                ).fetchone() or {"cnt": 0})["cnt"]
            ),
        }
        avg_row = con.execute(
            """
            SELECT AVG(duration_ms) AS avg_duration_ms
            FROM agent_metrics
            WHERE metric_type = 'agent.run' AND created_at >= ?
            """,
            (since,),
        ).fetchone()
        top_rows = con.execute(
            """
            SELECT agent_id, COUNT(*) AS run_count
            FROM agent_metrics
            WHERE metric_type = 'agent.run' AND created_at >= ?
            GROUP BY agent_id
            ORDER BY run_count DESC, agent_id ASC
            LIMIT 5
            """,
            (since,),
        ).fetchall()
        violation_rows = con.execute(
            """
            SELECT * FROM agent_metrics
            WHERE metric_type = 'sandbox.blocked' AND created_at >= ?
            ORDER BY created_at DESC, id DESC
            LIMIT 10
            """,
            (since,),
        ).fetchall()

    top_agents = [
        {"agent_id": str(row["agent_id"]), "run_count": int(row["run_count"])}
        for row in top_rows
        if str(row["agent_id"] or "").strip()
    ]
    recent_violations = [_row_to_metric(row) for row in violation_rows]
    limits_summary = [
        ensure_agent_limit(agent_id)
        for agent_id in [
            "builtin-universal",
            "builtin-researcher",
            "builtin-programmer",
            "builtin-analyst",
            "builtin-socrat",
            "builtin-orchestrator",
            "builtin-reviewer",
            WORKFLOW_ENGINE_AGENT_ID,
        ]
    ]
    warnings: list[str] = []
    if totals["blocked_runs"] > 0:
        warnings.append("Есть заблокированные Agent OS запуски за последние 24 часа.")

    return {
        "ok": True,
        "window_hours": int(window_hours),
        "total_agent_runs": totals["total_agent_runs"],
        "blocked_runs": totals["blocked_runs"],
        "workflow_runs": totals["workflow_runs"],
        "avg_duration_ms": int(round(float(avg_row["avg_duration_ms"] or 0))) if avg_row else 0,
        "top_agents": top_agents,
        "recent_violations": [item for item in recent_violations if item],
        "limits_summary": limits_summary,
        "warnings": warnings,
    }
