"""User-operated MCP lifecycle UI over the existing runtime control adapter."""
from __future__ import annotations

from pathlib import Path
from typing import Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.application.code_agent.tools._runtime_control import tool_runtime_control
from app.application.tool_providers import mcp_runtime
from app.core.redaction import redact_text

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop", "restart"]


def _safe_message(value: str, servers: list[dict]) -> str:
    # Exceptions can contain credentials supplied to a transport. Never send
    # config, command arguments, URLs or secret references to this UI.
    if value and any(server.get("env_secret_refs") or server.get("secret_header_refs") for server in servers):
        # The vault may have been locked since the failure. A transport can
        # echo a resolved value that cannot now be reliably redacted.
        return "Ошибка MCP. Проверьте доступность сервера и учётные данные."
    for server in servers:
        for field in ("env", "secret_headers", "headers", "env_secret_refs", "secret_header_refs"):
            for secret in (server.get(field) or {}).values():
                if secret:
                    value = value.replace(str(secret), "[REDACTED]")
    return redact_text(value)[:1000]


@router.get("/servers")
def list_servers():
    servers = mcp_runtime.list_servers()
    return {"servers": [
        {
            "id": server["id"],
            "transport": server["transport"],
            "enabled": server["enabled"],
            "status": server["status"],
            "last_error": _safe_message(str(server.get("last_error") or ""), servers) or None,
        }
        for server in servers
    ]}


@router.post("/servers/{server_id}/lifecycle")
def lifecycle(server_id: str, payload: LifecycleRequest):
    servers = mcp_runtime.list_servers()
    if not any(server["id"] == server_id for server in servers):
        raise HTTPException(status_code=404, detail="MCP server not configured")
    result = tool_runtime_control(Path.cwd(), operation=f"mcp_{payload.action}", server_id=server_id)
    if not result.get("ok"):
        if result.get("status") == "needs_secret":
            message = "Разблокируйте хранилище секретов через карточку Workflow и повторите запуск MCP."
        else:
            message = _safe_message(str((result.get("error") or {}).get("message") or "MCP operation failed"), servers)
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "server_id": server_id, "action": payload.action}
