"""MCP control for the agent: list, start, stop, discover tools, change config.

A thin adapter over mcp_runtime (data/mcp_servers.json). Secrets enter only as
opaque secret_ref values; a credential is requested through a Workflow card.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._tool_contract import (
    require_id as _require_id,
    run_operation,
    secret_request as _secret_request,
)


def _model_view(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the model needs to choose a server: id, purpose, state, its skill."""
    return [
        {
            "id": item["id"],
            "description": item.get("description", ""),
            "status": item.get("status", "stopped") if item.get("enabled", True) else "switched_off",
            "skill": item.get("skill") or item["id"].replace("_", "-") + "-mcp",
            **({"last_error": item["last_error"]} if item.get("last_error") else {}),
        }
        for item in servers
    ]


def _upsert_by_id(current: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    server_id = _require_id(str(spec.get("id", "")), "server_id", "ID runtime")
    return [item for item in current if str(item.get("id", "")) != server_id] + [spec]


def _mcp_control(operation: str, server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    from app.application.tool_providers import mcp_runtime

    if operation == "mcp_list":
        return {"ok": True, "servers": _model_view(mcp_runtime.list_servers())}
    sid = str(server_id or config.get("id") or "").strip()
    if not sid:
        # A malformed tool call is repairable by the model. Turning it into a
        # Workflow input request suspends the loop before it can correct itself.
        raise ValueError("MCP actions require server_id or config.id; use mcp(action='list') to find an existing ID")
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
        if not any(str(item.get("id", "")) == sid for item in current):
            raise ValueError(f"MCP server '{sid}' is not configured")
        saved = mcp_runtime.save_servers([
            item for item in current if str(item.get("id", "")) != sid
        ])
        return {"ok": True, "removed": sid, "servers": _model_view(saved)}
    if operation == "mcp_upsert":
        if not isinstance(config, dict):
            raise ValueError("config must be an object")
        # add changes only the given fields of an existing entry: the stored
        # secret references stay without the model ever seeing them.
        existing = next((item for item in current if str(item.get("id", "")) == sid), None)
        merged = mcp_runtime.restore_masked_secrets(existing, {**(existing or {}), **config, "id": sid})
        candidate = mcp_runtime._validate_server(merged)
        if candidate is None:
            raise ValueError("MCP server config is invalid; existing configuration was not changed")
        if mcp_runtime.plaintext_credentials(existing, candidate):
            raise _secret_request(
                "MCP credential нужно сохранить через write-only карточку, затем "
                "передать secret_ref в env_secret_refs или secret_header_refs."
            )
        saved = mcp_runtime.save_servers(_upsert_by_id(current, candidate))
        entry = next((item for item in saved if item.get("id") == sid), None)
        if entry is None:
            raise ValueError("MCP server config is invalid")
        return {"ok": True, "server": mcp_runtime.public_server_view([entry])[0]}
    raise ValueError(f"unsupported MCP operation: {operation}")


# Model-facing action -> runtime operation.
MCP_ACTIONS = {
    "list": "mcp_list", "start": "mcp_start", "stop": "mcp_stop", "restart": "mcp_restart",
    "tools": "mcp_tools", "add": "mcp_upsert", "remove": "mcp_remove",
}


def tool_mcp(
    project_root: Path,
    *,
    action: str,
    server_id: str = "",
    query: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Run one MCP action and return the shared structured result."""
    op = MCP_ACTIONS.get(str(action or "").strip().lower())
    if op is None:
        return {"ok": False, "error": "unsupported_action",
                "text": "ERROR: action должен быть одним из " + ", ".join(MCP_ACTIONS) + "."}
    if config is not None and not isinstance(config, dict):
        return {"ok": False, "error": "invalid_config", "text": "ERROR: config должен быть объектом."}
    return run_operation(op, lambda: _mcp_control(op, server_id, dict(config or {})))
