"""Workflow-facing control plane for integrations and IT Ops runtimes.

This is a thin adapter over existing runtimes, not a second registry/executor.
Every operation returns JSON-safe status. Secrets enter only as opaque
``secret_ref`` values and are resolved inside the owning runtime.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any

from app.application.code_agent.tools._runtime_control_contract import (
    RESULT_STATUSES as _RESULT_STATUSES,
    RuntimeRequest as _RuntimeRequest,
    completed as _completed,
    failed as _failed,
    input_request as _input_request,
    requested as _requested,
    require_id as _require_id,
    secret_request as _secret_request,
    with_text as _text,
)
from app.application.code_agent.tools._runtime_control_data import (
    library_control as _library_control,
    memory_control as _memory_control,
    project_control as _project_control,
)
from app.application.code_agent.tools._runtime_control_workflows import (
    workflow_control as _workflow_control,
)


def _runtime_status() -> dict[str, Any]:
    from app.application.telegram import telegram_bot_status
    from app.application.tool_providers import lsp_runtime, mcp_runtime
    from app.infrastructure.it_ops import store
    from app.infrastructure.secrets import vault
    from app.application.plugins import list_plugins

    try:
        store.init_db()
        assets = store.list_assets()
        profiles = store.list_connection_profiles()
    except Exception as exc:
        assets, profiles = [], []
        itops_error = str(exc)
    else:
        itops_error = ""
    plugin_status = list_plugins()
    try:
        from app.application.workflows.triggers import scheduler_status

        workflow_status = scheduler_status()
    except Exception as exc:
        workflow_status = {"error": str(exc)}
    try:
        from app.application.memory.facade import fact_stats

        memory_status = fact_stats()
    except Exception as exc:
        memory_status = {"error": str(exc)}
    try:
        from app.application.library.runtime import list_library_files

        library_status = list_library_files()
    except Exception as exc:
        library_status = {"error": str(exc)}
    return {
        "ok": True,
        "vault": vault.status(),
        "telegram": telegram_bot_status(),
        "mcp": mcp_runtime.public_server_view(mcp_runtime.list_servers()),
        "lsp": lsp_runtime.list_servers(),
        "plugins": plugin_status,
        "workflow_scheduler": workflow_status,
        "memory": memory_status,
        "library": library_status,
        "itops": {
            "assets": len(assets),
            "profiles": len(profiles),
            **({"error": itops_error} if itops_error else {}),
        },
    }


def _plugin_control(
    operation: str,
    name: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.application import plugins

    if operation == "plugin_list":
        return plugins.list_plugins()
    if operation == "plugin_reload":
        return plugins.reload_plugins()
    name = _require_id(name or str(config.get("name", "")), "name", "имя plugin")
    if operation == "plugin_info":
        return plugins.get_plugin_info(name)
    if operation == "plugin_enable":
        return plugins.enable_plugin(name)
    if operation == "plugin_disable":
        return plugins.disable_plugin(name)
    if operation == "plugin_configure":
        settings = config.get("settings")
        if not isinstance(settings, dict):
            raise ValueError("config.settings must be an object")
        return plugins.update_plugin_settings(name, settings)
    if operation == "plugin_run":
        args = config.get("args")
        if args is not None and not isinstance(args, dict):
            raise ValueError("config.args must be an object")
        return plugins.run_plugin(name, args or {})
    raise ValueError(f"unsupported plugin operation: {operation}")


def _upsert_by_id(current: list[dict[str, Any]], spec: dict[str, Any]) -> list[dict[str, Any]]:
    server_id = _require_id(str(spec.get("id", "")), "server_id", "ID runtime")
    return [item for item in current if str(item.get("id", "")) != server_id] + [spec]


def _mcp_control(operation: str, server_id: str, config: dict[str, Any]) -> dict[str, Any]:
    from app.application.tool_providers import mcp_runtime

    if operation == "mcp_list":
        return {"ok": True, "servers": mcp_runtime.public_server_view(mcp_runtime.list_servers())}
    sid = _require_id(
        server_id or str(config.get("id", "")),
        "server_id",
        "ID MCP server",
    )
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
        candidate = {**config, "id": sid}
        if candidate.get("env") or candidate.get("secret_headers"):
            raise _secret_request(
                "MCP credential нужно сохранить через write-only карточку, затем "
                "передать secret_ref в env_secret_refs или secret_header_refs."
            )
        saved = mcp_runtime.save_servers(_upsert_by_id(current, candidate))
        if not any(item.get("id") == sid for item in saved):
            raise ValueError("MCP server config is invalid")
        return {"ok": True, "servers": mcp_runtime.public_server_view(saved)}
    raise ValueError(f"unsupported MCP operation: {operation}")


def _ssh_control(operation: str, config: dict[str, Any]) -> dict[str, Any]:
    from app.application.tool_providers.ssh_acl import get_allowed_hosts, set_allowed_hosts

    if operation == "ssh_hosts":
        return {"ok": True, "hosts": get_allowed_hosts()}
    if operation == "ssh_set_hosts":
        hosts = config.get("hosts")
        if not isinstance(hosts, list):
            raise ValueError("config.hosts must be an array")
        return {"ok": True, "hosts": set_allowed_hosts([str(item) for item in hosts])}
    raise ValueError(f"unsupported SSH operation: {operation}")


def _lsp_control(
    operation: str,
    server_id: str,
    name: str,
    kind: str,
    root_path: str,
    config: dict[str, Any],
) -> dict[str, Any]:
    from app.application.tool_providers import lsp_runtime

    if operation == "lsp_list":
        return {"ok": True, "servers": lsp_runtime.list_servers()}
    sid = _require_id(
        server_id or str(config.get("id", "")) or name,
        "server_id",
        "ID LSP server",
    )
    if operation == "lsp_start":
        return lsp_runtime.start_server(sid, root_path or None)
    if operation == "lsp_stop":
        return lsp_runtime.stop_server(sid)
    if operation == "lsp_restart":
        return lsp_runtime.restart_server(sid, root_path or None)
    current = lsp_runtime.list_servers()
    if operation == "lsp_remove":
        return {
            "ok": True,
            "servers": lsp_runtime.save_servers([
                item for item in current if str(item.get("id", "")) != sid
            ]),
        }
    if operation == "lsp_upsert":
        candidate = {**config, "id": sid}
        language = str(candidate.get("language") or candidate.get("kind") or kind).strip()
        if language.casefold() in {"", "lsp", "language-server", "language_server"}:
            identity = " ".join((sid, str(candidate.get("command") or ""))).casefold()
            if "pyright" in identity or "pylsp" in identity:
                language = "python"
            elif "typescript" in identity or "tsserver" in identity:
                language = "typescript"
            elif "rust-analyzer" in identity or "rust_analyzer" in identity:
                language = "rust"
        candidate["language"] = language
        saved = lsp_runtime.save_servers(_upsert_by_id(current, candidate))
        if not any(item.get("id") == sid for item in saved):
            raise ValueError("LSP server config is invalid")
        return {"ok": True, "servers": saved}
    raise ValueError(f"unsupported LSP operation: {operation}")


def _telegram_control(
    operation: str,
    secret_ref: str,
    config: dict[str, Any],
    chat_id: int | None,
    allowed: bool | None,
    query: str,
) -> dict[str, Any]:
    from app.application import telegram
    from app.application.telegram.store import get_config_value, set_config_value

    if operation == "telegram_status":
        return telegram.telegram_bot_status()
    if operation == "telegram_start":
        token_ref = get_config_value("bot_token_ref", "").strip()
        if not token_ref:
            raise _secret_request("Для запуска Telegram нужен bot token.")
        from app.infrastructure.secrets import vault

        if vault.status().get("locked"):
            raise _secret_request(
                "Разблокируйте portable vault для запуска Telegram.",
                existing_secret_ref=token_ref,
            )
        return telegram.start_telegram_bot()
    if operation == "telegram_stop":
        return telegram.stop_telegram_bot()
    if operation == "telegram_test":
        token_ref = get_config_value("bot_token_ref", "").strip()
        if not token_ref:
            raise _secret_request("Для проверки Telegram нужен bot token.")
        from app.infrastructure.secrets import vault

        if vault.status().get("locked"):
            raise _secret_request(
                "Разблокируйте portable vault для проверки Telegram.",
                existing_secret_ref=token_ref,
            )
        return telegram.test_telegram_connection()
    if operation == "telegram_send":
        if chat_id is None:
            raise _input_request("Укажите Telegram chat_id.", "chat_id", "Telegram chat ID")
        text = query or str(config.get("text") or "")
        if not text.strip():
            raise _input_request(
                "Укажите текст Telegram-сообщения.",
                "query",
                "Текст сообщения",
            )
        token_ref = get_config_value("bot_token_ref", "").strip()
        if not token_ref:
            raise _secret_request("Для отправки в Telegram нужен bot token.")
        from app.infrastructure.secrets import vault

        if vault.status().get("locked"):
            raise _secret_request(
                "Разблокируйте portable vault для отправки в Telegram.",
                existing_secret_ref=token_ref,
            )
        return telegram.send_telegram_message(
            chat_id=int(chat_id),
            text=text,
            parse_mode=str(config.get("parse_mode") or "Markdown"),
        )
    if operation == "telegram_messages":
        return telegram.get_telegram_log(
            limit=min(max(1, int(config.get("limit") or 50)), 500),
            chat_id=int(chat_id) if chat_id is not None else None,
        )
    if operation == "telegram_users":
        return telegram.list_telegram_users()
    if operation == "telegram_toggle_user":
        if chat_id is None or allowed is None:
            raise ValueError("chat_id and allowed are required")
        return telegram.toggle_user_access(int(chat_id), bool(allowed))
    if operation == "telegram_configure":
        update = dict(config or {})
        if update.get("bot_token"):
            raise _secret_request(
                "Bot token вводится только в write-only карточке Workflow."
            )
        if secret_ref:
            update["bot_token_ref"] = secret_ref.strip()
        if not update:
            raise _secret_request("Укажите bot token для настройки Telegram.")
        return telegram.update_telegram_config(update)
    if operation == "telegram_migrate_legacy_token":
        existing_ref = get_config_value("bot_token_ref", "").strip()
        if existing_ref:
            return {"ok": True, "secret_ref": existing_ref, "already_migrated": True}
        legacy = get_config_value("bot_token", "")
        if not legacy:
            raise _secret_request(
                "Legacy token не найден. Введите актуальный bot token."
            )
        from app.infrastructure.secrets import vault

        migrated_ref = vault.put_secret(kind="token", value=legacy, lifecycle="persistent")
        set_config_value("bot_token_ref", migrated_ref)
        set_config_value("bot_token", "")
        return {"ok": True, "secret_ref": migrated_ref, "already_migrated": False}
    raise ValueError(f"unsupported Telegram operation: {operation}")


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

        host = _require_id(
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
            user=_require_id(
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
        asset_id = _require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        return {
            "ok": True,
            "asset": store.upsert_asset(
                asset_id=asset_id,
                label=str(config.get("label") or asset_id),
                kind=_require_id(
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
        asset_id = _require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        return {"ok": True, "asset_id": asset_id, "deleted": store.delete_asset(asset_id)}
    if operation == "itops_profile_upsert":
        profile_id = _require_id(
            profile_id or str(config.get("profile_id", "")),
            "profile_id",
            "ID профиля подключения",
        )
        asset_id = _require_id(
            asset_id or str(config.get("asset_id", "")),
            "asset_id",
            "ID актива",
        )
        auth_ref = str(secret_ref or config.get("auth_ref") or "").strip() or None
        if config.get("password") or config.get("token") or config.get("private_key"):
            raise _secret_request(
                "Credential вводится только через write-only карточку Workflow.",
                kind=(
                    "private_key" if config.get("private_key")
                    else "token" if config.get("token")
                    else "password"
                ),
            )
        if bool(config.get("auth_required")) and not auth_ref:
            raise _secret_request(
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
        profile_id = _require_id(
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


def tool_runtime_control(
    project_root: Path,
    *,
    operation: str,
    server_id: str = "",
    workflow_id: str = "",
    run_id: str = "",
    trigger_id: str = "",
    asset_id: str = "",
    profile_id: str = "",
    kind: str = "",
    memory_id: int | str | None = None,
    filename: str = "",
    name: str = "",
    query: str = "",
    root_path: str = "",
    secret_ref: str = "",
    config: dict[str, Any] | None = None,
    chat_id: int | None = None,
    allowed: bool | None = None,
    path: str = "",
) -> dict[str, Any]:
    """Manage hidden integration runtimes through one Workflow-facing tool."""
    op = str(operation or "").strip().lower()
    try:
        if config is not None and not isinstance(config, dict):
            raise ValueError("config must be an object")
        settings = config or {}
        if op == "status":
            result = _runtime_status()
        elif op.startswith("mcp_"):
            result = _mcp_control(op, server_id, settings)
        elif op.startswith("lsp_"):
            result = _lsp_control(
                op,
                server_id,
                name,
                kind,
                root_path or str(project_root),
                settings,
            )
        elif op.startswith("ssh_"):
            result = _ssh_control(op, settings)
        elif op.startswith("telegram_"):
            result = _telegram_control(
                op,
                secret_ref,
                settings,
                chat_id,
                allowed,
                query,
            )
        elif op.startswith("itops_"):
            result = _itops_control(
                op,
                secret_ref,
                asset_id,
                profile_id,
                kind,
                settings,
            )
        elif op.startswith("plugin_"):
            result = _plugin_control(op, name, settings)
        elif op.startswith("workflow_"):
            from app.application.agent_kernel.execution_context import (
                get_permission_mode,
            )

            result = _workflow_control(
                op,
                workflow_id,
                run_id,
                trigger_id,
                get_permission_mode(),
                settings,
            )
        elif op.startswith("memory_"):
            result = _memory_control(op, memory_id, query, settings, project_root)
        elif op.startswith("project_"):
            result = _project_control(op, project_root, root_path, settings)
        elif op.startswith("library_"):
            result = _library_control(
                op,
                project_root,
                path,
                filename,
                query,
                settings,
            )
        elif op == "vault_status":
            from app.infrastructure.secrets import vault

            result = {"ok": True, "vault": vault.status()}
        elif op == "vault_lock":
            from app.infrastructure.secrets import vault

            result = {"ok": True, "vault": vault.lock()}
        elif op == "vault_backup":
            from app.infrastructure.secrets import vault

            result = vault.backup(_require_id(path, "path", "путь backup"))
        elif op == "vault_restore":
            from app.infrastructure.secrets import vault

            result = {
                "ok": True,
                "vault": vault.restore(_require_id(path, "path", "путь backup")),
            }
        else:
            raise ValueError(f"unsupported runtime operation: {op}")
        if not isinstance(result, dict):
            raise TypeError("runtime operation returned a non-object result")
        raw_status = str(result.get("status") or "").strip()
        if raw_status == "cancelled":
            return _text({
                "ok": False,
                "status": "cancelled",
                "operation": op,
                "result": result,
            })
        if raw_status in _RESULT_STATUSES - {"completed", "failed", "cancelled"}:
            request = result.get("request")
            if not isinstance(request, dict):
                request = {}
            return _text({
                "ok": False,
                "status": raw_status,
                "operation": op,
                "request": request,
                "result": result,
            })
        if raw_status == "failed" or not bool(result.get("ok", True)):
            raw_error = result.get("error") or "runtime operation failed"
            if isinstance(raw_error, dict):
                message = str(raw_error.get("message") or raw_error)
                retryable = bool(raw_error.get("retryable", False))
                code = str(raw_error.get("code") or "runtime_operation_failed")
            else:
                message = str(raw_error)
                retryable = False
                code = "runtime_operation_failed"
            return _failed(op, message, code=code, retryable=retryable)
        return _completed(op, result)
    except _RuntimeRequest as exc:
        return _requested(op, exc)
    except Exception as exc:
        return _failed(
            op,
            str(exc),
            code=exc.__class__.__name__,
            retryable=isinstance(exc, (ConnectionError, OSError))
            and not isinstance(exc, (FileNotFoundError, PermissionError)),
        )
