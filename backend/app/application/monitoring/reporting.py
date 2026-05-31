from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable

from app.application.monitoring.store import list_agent_limits, row_to_metric
from app.application.workflows.store import (
    init_db as workflow_store_init_db,
    list_workflow_templates as workflow_store_list_workflow_templates,
)
from app.infrastructure.db.connection import connect_sqlite


def health_list_workflow_templates(workflow_db_path: str | Path) -> tuple[list[dict[str, Any]], int]:
    workflow_store_init_db(db_path=workflow_db_path)
    return workflow_store_list_workflow_templates(
        db_path=workflow_db_path,
        include_disabled=True,
    )


def get_agent_os_health(
    *,
    db_path: str | Path,
    workflow_db_path: str | Path,
) -> dict[str, Any]:
    components: list[dict[str, Any]] = []

    checks = [
        (
            "agent_registry",
            lambda: __import__("app.application.agent_registry.runtime", fromlist=["list_agents"]).list_agents(enabled_only=False),
        ),
        (
            "event_bus",
            lambda: __import__("app.application.event_bus.runtime", fromlist=["list_events"]).list_events(limit=1),
        ),
        (
            "workflow_engine",
            lambda: health_list_workflow_templates(workflow_db_path),
        ),
        ("agent_monitor", lambda: list_agent_limits(db_path)),
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


def get_agent_os_dashboard(
    *,
    db_path: str | Path,
    window_hours: int,
    ensure_agent_limit: Callable[[str], dict[str, Any]],
    workflow_engine_agent_id: str,
) -> dict[str, Any]:
    since = (datetime.now(timezone.utc) - timedelta(hours=window_hours)).isoformat()
    with connect_sqlite(db_path) as con:
        totals = {
            "total_agent_runs": int(
                (
                    con.execute(
                        "SELECT COUNT(DISTINCT run_id) AS cnt FROM agent_metrics WHERE metric_type = 'agent.run' AND created_at >= ?",
                        (since,),
                    ).fetchone()
                    or {"cnt": 0}
                )["cnt"]
            ),
            "blocked_runs": int(
                (
                    con.execute(
                        "SELECT COUNT(*) AS cnt FROM agent_metrics WHERE metric_type = 'sandbox.blocked' AND created_at >= ?",
                        (since,),
                    ).fetchone()
                    or {"cnt": 0}
                )["cnt"]
            ),
            "workflow_runs": int(
                (
                    con.execute(
                        "SELECT COUNT(DISTINCT run_id) AS cnt FROM agent_metrics WHERE metric_type = 'workflow.run' AND created_at >= ?",
                        (since,),
                    ).fetchone()
                    or {"cnt": 0}
                )["cnt"]
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
    recent_violations = [row_to_metric(row) for row in violation_rows]
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
            workflow_engine_agent_id,
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
