from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._sandbox import _resolve_safe


def _format_runtime_result(label: str, result: dict[str, Any]) -> dict[str, Any]:
    if not result.get("ok"):
        error = str(result.get("error") or "unknown_error")
        return {"ok": False, "error": error, "text": f"ERROR: {error}"}
    return {
        "ok": True,
        "text": f"{label}:\n{json.dumps(result, ensure_ascii=False, indent=2)}",
    }


def tool_csv(
    project_root: Path,
    *,
    file_path: str,
    query: str = "",
    filters: list[Any] | None = None,
    group_by: list[Any] | None = None,
    aggregate: list[Any] | None = None,
) -> dict[str, Any]:
    from app.application.skill_services.table_overview import analyze_csv

    target = _resolve_safe(project_root, file_path)
    if not target.is_file():
        return {"ok": False, "error": "file_not_found", "text": f"ERROR: not a file or does not exist: {file_path}"}
    if filters or group_by or aggregate:
        from app.application.skill_services.table_query import TableError, aggregate_csv

        try:
            result = aggregate_csv(target, filters=filters, group_by=group_by, aggregates=aggregate)
        except TableError as exc:
            return {"ok": False, "error": "csv_query_error", "text": f"ERROR: {exc}"}
        return _format_runtime_result("CSV query", result)
    return _format_runtime_result("CSV analysis", analyze_csv(str(target), query=query))
