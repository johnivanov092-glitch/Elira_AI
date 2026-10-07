"""Settings → MCP: the user's view of data/mcp_servers.json over the same runtime.

Lifecycle goes through the agent's `mcp` tool; config edits go through
mcp_runtime.save_servers. Secret values never leave the backend: the editor gets
public_server_view (masked values), and a masked value saved back keeps the
stored one. A new plain-text credential is refused — it belongs in the vault.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict

from app.application.code_agent.tools._mcp import tool_mcp
from app.application.tool_providers import mcp_runtime
from app.core.redaction import redact_text

router = APIRouter(prefix="/api/mcp", tags=["mcp"])


class LifecycleRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    action: Literal["start", "stop", "restart"]


class ConfigRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    config: dict[str, Any]


class EnabledRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    enabled: bool


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


def _server(server_id: str) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    servers = mcp_runtime.list_servers()
    server = next((item for item in servers if item["id"] == server_id), None)
    if server is None:
        raise HTTPException(status_code=404, detail="MCP server not configured")
    return server, servers


def _save(server_id: str, config: dict[str, Any], existing: dict[str, Any] | None,
          servers: list[dict[str, Any]]) -> dict[str, Any]:
    merged = mcp_runtime.restore_masked_secrets(existing, {**config, "id": server_id})
    candidate = mcp_runtime._validate_server(merged)
    if candidate is None:
        raise HTTPException(status_code=422, detail=(
            "Запись MCP не прошла проверку: нужен command (stdio) или url (http); "
            "args — список строк; env, headers и *_refs — объекты строк."))
    plain = mcp_runtime.plaintext_credentials(existing, candidate)
    if plain:
        raise HTTPException(status_code=422, detail=(
            f"Секрет открытым текстом ({', '.join(plain)}) не сохраняется. Добавьте его в "
            "Настройки → Секреты и укажите ссылку sref_… в env_secret_refs или secret_header_refs."))
    if existing is None:
        updated = [*servers, candidate]
    else:
        updated = [candidate if item["id"] == server_id else item for item in servers]
    saved = mcp_runtime.save_servers(updated)
    entry = next((item for item in saved if item["id"] == server_id), None)
    if entry is None:
        raise HTTPException(status_code=422, detail="Запись MCP не сохранилась")
    return mcp_runtime.public_server_view([entry])[0]


@router.get("/servers")
def list_servers():
    servers = mcp_runtime.list_servers()
    return {"servers": [
        {
            "id": server["id"],
            "description": server.get("description", ""),
            "transport": server["transport"],
            "enabled": server["enabled"],
            "status": server["status"],
            "last_error": _safe_message(str(server.get("last_error") or ""), servers) or None,
        }
        for server in servers
    ]}


@router.get("/servers/{server_id}/config")
def get_config(server_id: str):
    server, _ = _server(server_id)
    view = mcp_runtime.public_server_view([server])[0]
    return {"config": {key: value for key, value in view.items() if key not in {"status", "last_error"}}}


@router.put("/servers/{server_id}")
def update_server(server_id: str, payload: ConfigRequest):
    server, servers = _server(server_id)
    return {"ok": True, "config": _save(server_id, payload.config, server, servers)}


@router.post("/servers")
def add_server(payload: ConfigRequest):
    server_id = str(payload.config.get("id") or "").strip()
    if not server_id:
        raise HTTPException(status_code=422, detail="Укажите id сервера")
    servers = mcp_runtime.list_servers()
    if any(item["id"] == server_id for item in servers):
        raise HTTPException(status_code=409, detail=f"Сервер {server_id} уже есть")
    return {"ok": True, "config": _save(server_id, payload.config, None, servers)}


@router.post("/servers/{server_id}/enabled")
def set_enabled(server_id: str, payload: EnabledRequest):
    server, servers = _server(server_id)
    if not payload.enabled:
        mcp_runtime.stop_server(server_id)
    mcp_runtime.save_servers([{**item, "enabled": payload.enabled} if item["id"] == server_id else item
                              for item in servers])
    return {"ok": True, "server_id": server_id, "enabled": payload.enabled}


@router.delete("/servers/{server_id}")
def delete_server(server_id: str):
    _, servers = _server(server_id)
    mcp_runtime.save_servers([item for item in servers if item["id"] != server_id])
    return {"ok": True, "server_id": server_id}


@router.post("/servers/{server_id}/lifecycle")
def lifecycle(server_id: str, payload: LifecycleRequest):
    _, servers = _server(server_id)
    result = tool_mcp(Path.cwd(), action=payload.action, server_id=server_id)
    if not result.get("ok"):
        if result.get("status") == "needs_secret":
            message = "Разблокируйте хранилище в Настройки → Секреты и повторите запуск MCP."
        else:
            message = _safe_message(str((result.get("error") or {}).get("message") or "MCP operation failed"), servers)
        raise HTTPException(status_code=409, detail=message)
    return {"ok": True, "server_id": server_id, "action": payload.action}
