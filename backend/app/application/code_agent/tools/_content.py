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
    from app.application.skills_extra.runtime import analyze_csv

    target = _resolve_safe(project_root, file_path)
    if not target.is_file():
        return {"ok": False, "error": "file_not_found", "text": f"ERROR: not a file or does not exist: {file_path}"}
    if filters or group_by or aggregate:
        from app.application.calculation.table import TableError, aggregate_csv

        try:
            result = aggregate_csv(target, filters=filters, group_by=group_by, aggregates=aggregate)
        except TableError as exc:
            return {"ok": False, "error": "csv_query_error", "text": f"ERROR: {exc}"}
        return _format_runtime_result("CSV query", result)
    return _format_runtime_result("CSV analysis", analyze_csv(str(target), query=query))


def tool_http_api(
    project_root: Path,
    *,
    url: str,
    method: str = "GET",
    headers: dict[str, Any] | None = None,
    body: Any = None,
    timeout: int = 15,
) -> dict[str, Any]:
    from app.application.code_agent.tools._run import active_server_ports
    from app.application.skills.runtime import http_request

    result = http_request(
        url, method=method, headers=headers, body=body, timeout=int(timeout),
        allow_loopback_ports=active_server_ports(),
    )
    # Blocked / timeout / connection error: the check could NOT run → ok=False and
    # NO verifier flag, so a matched page_open criterion stays unconfirmed (not failed).
    if not result.get("ok"):
        return {"text": f"ERROR: {result.get('error') or 'unknown error'}", "ok": False}
    # A completed request IS a verdict: 2xx/3xx → page opens (pass); 4xx/5xx → fail.
    status = int(result.get("status") or 0)
    page_ok = 200 <= status < 400
    final_url = str(result.get("url") or url)
    return {
        "text": f"HTTP {status} {final_url}:\n{json.dumps(result, ensure_ascii=False, indent=2)}",
        "ok": page_ok,
        "verifier": True,
        "evidence": f"HTTP {status} {final_url}",
    }
