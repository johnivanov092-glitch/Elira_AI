"""IT Ops registry as an on-demand agent tool (group itops).

Saved assets, connection profiles and MikroTik routers. Inventory and health
checks are the itops_* provider tools of the same group. Credentials enter only
as an opaque secret_ref created by a Workflow secret card.
"""
from __future__ import annotations

from typing import Any

from app.application.code_agent.tools._tool_contract import (
    require_id,
    run_operation,
    secret_request,
)

_ACTIONS = {
    "list": "itops_assets",
    "asset_upsert": "itops_asset_upsert",
    "asset_remove": "itops_asset_remove",
    "profile_upsert": "itops_profile_upsert",
    "profile_remove": "itops_profile_remove",
    "mikrotik_list": "itops_mikrotik_list",
    "mikrotik_upsert": "itops_mikrotik_upsert",
    "mikrotik_remove": "itops_mikrotik_remove",
    "mikrotik_sync": "itops_mikrotik_sync",
}


def _itops_control(
    operation: str,
    secret_ref: str,
    asset_id: str,
    profile_id: str,
    kind: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.infrastructure.it_ops import store

    store.init_db()
    if operation == "itops_mikrotik_list":
        from app.application.it_ops import mikrotik_registry

        return {"ok": True, "routers": mikrotik_registry.list_routers()}
    if operation == "itops_mikrotik_sync":
        from app.application.it_ops import mikrotik_registry

        return mikrotik_registry.sync_runtime()
    if operation == "itops_mikrotik_upsert":
        from app.application.it_ops import mikrotik_registry

        host = require_id(
            str(config.get("host") or config.get("endpoint") or ""),
            "host",
            "адрес MikroTik",
        )
        if config.get("password") or config.get("token") or config.get("private_key"):
            raise ValueError(
                "MikroTik uses non-interactive typed SSH with an OpenSSH key or ssh-agent; "
                "password/private_key values are not accepted in tool arguments."
            )
        raw_port = config.get("port")
        if isinstance(raw_port, bool):
            raise ValueError("config.port must be an integer")
        port = int(raw_port) if raw_port not in (None, "") else None
        raw_tags = config.get("tags")
        if raw_tags is not None and not isinstance(raw_tags, list):
            raise ValueError("config.tags must be an array")
        return mikrotik_registry.upsert_router(
            host=host,
            label=str(config.get("label") or ""),
            user=require_id(
                str(config.get("user") or ""),
                "user",
                "логин MikroTik",
            ),
            auth_ref=str(secret_ref or config.get("auth_ref") or "").strip(),
            asset_id=asset_id or str(config.get("asset_id") or ""),
            router_id=str(config.get("router_id") or ""),
            ssh_alias=str(config.get("ssh_alias") or ""),
            identity_file=str(config.get("identity_file") or ""),
            port=port,
            ros_version=str(config.get("ros_version") or ""),
            tags=[str(item) for item in (raw_tags or [])],
        )
    if operation == "itops_mikrotik_remove":
        from app.application.it_ops import mikrotik_registry

        return mikrotik_registry.remove_router(
            asset_id=asset_id or str(config.get("asset_id") or ""),
            router_id=str(config.get("router_id") or ""),
            host=str(config.get("host") or config.get("endpoint") or ""),
        )
    if operation == "itops_assets":
        return {
            "ok": True,
            "assets": store.list_assets(),
            "profiles": store.list_connection_profiles(),
        }
    if operation == "itops_asset_upsert":
        asset_id = require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        return {
            "ok": True,
            "asset": store.upsert_asset(
                asset_id=asset_id,
                label=str(config.get("label") or asset_id),
                kind=require_id(
                    kind or str(config.get("kind", "")),
                    "kind",
                    "тип актива",
                ),
                endpoint=str(config.get("endpoint") or ""),
                tags=[str(item) for item in (config.get("tags") or [])],
                owner_scope=str(config.get("owner_scope") or ""),
                lifecycle_state=str(config.get("lifecycle_state") or "enabled"),
            ),
        }
    if operation == "itops_asset_remove":
        asset_id = require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        return {"ok": True, "asset_id": asset_id, "deleted": store.delete_asset(asset_id)}
    if operation == "itops_profile_upsert":
        profile_id = require_id(
            profile_id or str(config.get("profile_id", "")),
            "profile_id",
            "ID профиля подключения",
        )
        asset_id = require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        auth_ref = str(secret_ref or config.get("auth_ref") or "").strip() or None
        if config.get("password") or config.get("token") or config.get("private_key"):
            raise secret_request(
                "Credential вводится только через write-only карточку Workflow.",
                kind=(
                    "private_key" if config.get("private_key")
                    else "token" if config.get("token")
                    else "password"
                ),
            )
        if bool(config.get("auth_required")) and not auth_ref:
            raise secret_request(
                "Для профиля подключения нужен credential secret_ref.",
                kind=str(config.get("secret_kind") or "password"),
            )
        return {
            "ok": True,
            "profile": store.put_connection_profile(
                profile_id=profile_id,
                asset_id=asset_id,
                transport=str(config.get("transport") or "ssh"),
                user=str(config.get("user") or ""),
                auth_ref=auth_ref,
                ssh_alias=str(config.get("ssh_alias") or ""),
                host_key_fingerprint=str(config.get("host_key_fingerprint") or ""),
                os_platform_meta=(
                    dict(config.get("os_platform_meta"))
                    if isinstance(config.get("os_platform_meta"), dict)
                    else {}
                ),
                last_health=(
                    dict(config.get("last_health"))
                    if isinstance(config.get("last_health"), dict)
                    else {}
                ),
            ),
        }
    if operation == "itops_profile_remove":
        profile_id = require_id(
            profile_id or str(config.get("profile_id", "")),
            "profile_id",
            "ID профиля подключения",
        )
        return {
            "ok": True,
            "profile_id": profile_id,
            "deleted": store.delete_connection_profile(profile_id),
        }
    raise ValueError(f"unsupported IT Ops operation: {operation}")


def tool_itops_registry(
    *,
    action: str,
    asset_id: str = "",
    profile_id: str = "",
    kind: str = "",
    secret_ref: str = "",
    config: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """List or change saved IT Ops assets, connection profiles and MikroTik routers."""
    act = str(action or "").strip().lower()
    operation = _ACTIONS.get(act)
    if operation is None:
        return {"ok": False, "error": "unknown_action",
                "text": "ERROR: action должен быть одним из " + ", ".join(_ACTIONS) + "."}
    if config is not None and not isinstance(config, dict):
        return {"ok": False, "error": "invalid_config", "text": "ERROR: config должен быть объектом."}
    return run_operation(operation, lambda: _itops_control(
        operation, secret_ref, asset_id, profile_id, kind, dict(config or {}),
    ))
