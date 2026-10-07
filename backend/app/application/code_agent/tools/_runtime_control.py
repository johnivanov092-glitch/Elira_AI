"""MCP control for the agent: list, start, stop, discover tools, change config.

A thin adapter over mcp_runtime (data/mcp_servers.json). Secrets enter only as
opaque secret_ref values; a credential is requested through a Workflow card.
"""
from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    require_id as _require_id,
    run_operation,
    secret_request as _secret_request,
)

MCP_OPERATIONS = (
    "mcp_list", "mcp_start", "mcp_stop", "mcp_restart", "mcp_tools", "mcp_upsert", "mcp_remove",
)


def _upsert_by_id(current: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    server_id = _require_id(str(spec.get("id", "")), "server_id", "ID runtime")
    return [item for item in current if str(item.get("id", "")) != server_id] + [spec]


def _mcp_control(operation: str, server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    from app.application.tool_providers import mcp_runtime

    if operation == "mcp_list":
        return {"ok": True, "servers": mcp_runtime.public_server_view(mcp_runtime.list_servers())}
    sid = str(server_id or config.get("id") or "").strip()
    if not sid:
        # A malformed tool call is repairable by the model. Turning it into a
        # Workflow input request suspends the loop before it can correct itself.
        raise ValueError("MCP operations require server_id or config.id; use mcp_list to find an existing ID")
    if operation in {"mcp_start", "mcp_restart"}:
        server = next(
            (item for item in mcp_runtime.list_servers() if str(item.get("id")) == sid),
            None,
        )
        secret_refs: list[str] = []
        if isinstance(server, dict):
            for field in ("env_secret_refs", "secret_header_refs"):
                refs = server.get(field)
                if isinstance(refs, dict):
                    secret_refs.extend(
                        str(value).strip() for value in refs.values() if str(value).strip()
                    )
        if secret_refs:
            from app.infrastructure.secrets import vault

            if vault.status().get("locked"):
                raise _secret_request(
                    "Разблокируйте portable vault в карточке Workflow, чтобы запустить MCP.",
                    existing_secret_ref=secret_refs[0],
                )
    if operation == "mcp_start":
        return mcp_runtime.start_server(sid)
    if operation == "mcp_stop":
        return mcp_runtime.stop_server(sid)
    if operation == "mcp_restart":
        return mcp_runtime.restart_server(sid)
    if operation == "mcp_tools":
        return mcp_runtime.discover_tools(sid)
    current = mcp_runtime.list_servers()
    if operation == "mcp_remove":
        saved = mcp_runtime.save_servers([
            item for item in current if str(item.get("id", "")) != sid
        ])
        return {"ok": True, "servers": mcp_runtime.public_server_view(saved)}
    if operation == "mcp_upsert":
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        candidate = mcp_runtime._validate_server({**config, "id": sid})
        if candidate is None:
            raise ValueError("MCP server config is invalid; existing configuration was not changed")
        # Environment also carries ordinary settings (URLs, paths, CPU counts).
        # Match credential names, not substrings such as TOKENIZERS_PARALLELISM
        # or MAX_TOKENS. Explicit credential values still use the vault flow.
        credential_env = any(
            value and re.search(
                r"(?:^|[_-])(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
                r"authorization|credentials?|private[_-]?key)$", key, re.IGNORECASE,
            )
            for key, value in candidate.get("env", {}).items()
        )
        if credential_env or candidate.get("secret_headers"):
            raise _secret_request(
                "MCP credential нужно сохранить через write-only карточку, затем "
                "передать secret_ref в env_secret_refs или secret_header_refs."
            )
        saved = mcp_runtime.save_servers(_upsert_by_id(current, candidate))
        if not any(item.get("id") == sid for item in saved):
            raise ValueError("MCP server config is invalid")
        return {"ok": True, "servers": mcp_runtime.public_server_view(saved)}
    raise ValueError(f"unsupported MCP operation: {operation}")


def tool_runtime_control(
    project_root: Path,
    *,
    operation: str,
    server_id: str = "",
    query: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one MCP operation and return the shared structured result."""
    op = str(operation or "").strip().lower()
    if op not in MCP_OPERATIONS:
        return {"ok": False, "error": "unsupported_operation",
                "text": "ERROR: operation должен быть одним из " + ", ".join(MCP_OPERATIONS) + "."}
    if config is not None and not isinstance(config, dict):
        return {"ok": False, "error": "invalid_config", "text": "ERROR: config должен быть объектом."}
    return run_operation(op, lambda: _mcp_control(op, server_id, dict(config or {})))
