"""MCP server configuration + lifecycle.

Stores the user's list of configured MCP servers at
`data/mcp_servers.json` and keeps a process-local registry of
running `McpClient` instances.

Config shape:
    {"servers": [
        {
            "id": "github",
            "description": "GitHub: репозитории, issues, pull requests",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env_secret_refs": {"GITHUB_PERSONAL_ACCESS_TOKEN": "sref_..."},
            "enabled": true
        },
        ...
    ]}

Optional fields: ``description`` (one line for the UI and ``mcp(list)``),
``skill`` (the instruction skill, default ``<id>-mcp``) and, for stdio,
``cwd`` (absolute, or relative to the folder of this file — a server installed
into ``data/mcp/<id>/`` uses ``mcp/<id>``). The file is the only source of
truth: it is read on every call, so an edit by the model, the user or the UI is
seen at once, and ``start_server`` restarts a live process whose entry changed.
A server with ``enabled: false`` is switched off by the user and does not start.

Concurrency:
    Client publication and lookups use a module-level lock. Slow handshakes
    run outside it; starts are serialized separately. A cancelled start or
    changed config cannot publish its client after stop/shutdown.
"""
from __future__ import annotations

import json
import logging
import re
import threading
from pathlib import Path
from typing import Any

from app.application.tool_providers.mcp_client import McpClient, McpError
from app.core.data_files import data_file


logger = logging.getLogger(__name__)


CONFIG_PATH: Path = data_file("mcp_servers.json")
_LOCK = threading.Lock()
_START_LOCK = threading.Lock()
_STARTING: dict[str, dict[str, Any]] = {}
_WARM_LOCK = threading.Lock()
_WARM_THREAD: threading.Thread | None = None
_WARM_READY = threading.Event()
_WARM_READY.set()
_WARM_CANCEL = threading.Event()
AUTOSTART_WAIT_SECONDS = 15.0
# id → McpClient (only entries for servers we've actually started)
_LIVE_CLIENTS: dict[str, McpClient] = {}
# id → the config entry the live client was started with
_LIVE_SPECS: dict[str, dict[str, Any]] = {}
# id → last connect-attempt error message (or None on success)
_LAST_ERROR: dict[str, str | None] = {}
_MAX_REPORTED_TOOL_NAMES = 50


# ── Config persistence ──────────────────────────────────────────


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"servers": []}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("mcp_runtime: failed to read %s: %s — treating as empty", CONFIG_PATH, exc)
        return {"servers": []}
    if not isinstance(raw, dict) or not isinstance(raw.get("servers"), list):
        return {"servers": []}
    return raw


def _write_config(payload: dict[str, Any]) -> None:
    CONFIG_PATH.parent.mkdir(parents=True, exist_ok=True)
    CONFIG_PATH.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
        newline="\n",
    )


def _str_str_map(value: Any) -> dict[str, str] | None:
    """Validate a {str: str} mapping. Returns None if malformed."""
    if not isinstance(value, dict):
        return None
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        return None
    return dict(value)


def _optional_text(spec: dict[str, Any], fields: tuple[str, ...]) -> dict[str, str] | None:
    """Optional one-line text fields; absent or empty ones are left out."""
    out: dict[str, str] = {}
    for field in fields:
        value = spec.get(field)
        if value is None:
            continue
        if not isinstance(value, str):
            return None
        if value.strip():
            out[field] = value.strip()
    return out


def _validate_server(spec: Any) -> dict[str, Any] | None:
    """Return a normalized server dict or None if `spec` is malformed.

    Two transports:
      * "stdio" (default) — spawns a subprocess from command/args/env. We're
        strict here because these specs end up running whatever the user gave.
      * "http" — connects to a remote MCP server at `url`. Carries optional
        non-secret `headers`, secret `secret_headers` (kept separate so they
        never get logged/audited). Legacy `allow_insecure_http` and
        `allow_private_address` fields are accepted but do not restrict
        HTTP. URL-shape validation lives in McpHttpClient; here we normalize
        configuration only.
    """
    if not isinstance(spec, dict):
        return None
    sid = spec.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    enabled = bool(spec.get("enabled", True))
    autostart = spec.get("autostart", False)
    if not isinstance(autostart, bool):
        return None
    transport = spec.get("transport", "stdio")
    if transport not in ("stdio", "http"):
        return None

    if transport == "http":
        extra = _optional_text(spec, ("description", "skill"))
        url = spec.get("url")
        if not isinstance(url, str) or not url.strip():
            return None
        headers = _str_str_map(spec.get("headers", {}))
        secret_headers = _str_str_map(spec.get("secret_headers", {}))
        secret_header_refs = _str_str_map(spec.get("secret_header_refs", {}))
        if headers is None or secret_headers is None or secret_header_refs is None or extra is None:
            return None
        return {
            "id": sid.strip(),
            **extra,
            "transport": "http",
            "url": url.strip(),
            "headers": headers,
            "secret_headers": secret_headers,
            "secret_header_refs": secret_header_refs,
            "allow_insecure_http": bool(spec.get("allow_insecure_http", False)),
            "allow_private_address": bool(spec.get("allow_private_address", False)),
            "enabled": enabled,
            "autostart": autostart,
        }

    # transport == "stdio"
    command = spec.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    args = spec.get("args", [])
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        return None
    env = _str_str_map(spec.get("env", {}))
    env_secret_refs = _str_str_map(spec.get("env_secret_refs", {}))
    extra = _optional_text(spec, ("description", "skill", "cwd"))
    if env is None or env_secret_refs is None or extra is None:
        return None
    return {
        "id": sid.strip(),
        **{key: value for key, value in extra.items() if key != "cwd"},
        "transport": "stdio",
        "command": command.strip(),
        "args": [a for a in args],
        **({"cwd": extra["cwd"]} if "cwd" in extra else {}),
        "env": env,
        "env_secret_refs": env_secret_refs,
        "enabled": enabled,
        "autostart": autostart,
    }


def list_servers() -> list[dict[str, Any]]:
    """Configured servers, with live status injected.

    The persisted config + a `status` field that reflects whether
    we currently hold a live client connection. Status is one of:
      "stopped"   — never started or stopped cleanly
      "running"   — subprocess alive, handshake done
      "crashed"   — process died after starting
      "error"     — last connect attempt failed (see `last_error`)
    """
    raw = _read_config()
    out: list[dict[str, Any]] = []
    with _LOCK:
        for spec in raw.get("servers", []):
            normalized = _validate_server(spec)
            if not normalized:
                continue
            sid = normalized["id"]
            client = _LIVE_CLIENTS.get(sid)
            if sid in _STARTING:
                status = "starting"
            elif client is None:
                status = "error" if _LAST_ERROR.get(sid) else "stopped"
            elif client.is_alive():
                status = "running"
            else:
                status = "crashed"
            normalized["status"] = status
            normalized["last_error"] = _LAST_ERROR.get(sid)
            out.append(normalized)
    return out


_SECRET_SERVER_FIELDS = ("env", "secret_headers", "env_secret_refs", "secret_header_refs")
SECRET_MASK = "●●●"
_CREDENTIAL_NAME = re.compile(
    r"(?:^|[_-])(?:password|passwd|pwd|secret|token|api[_-]?key|apikey|"
    r"authorization|credentials?|private[_-]?key)$",
    re.IGNORECASE,
)


def plaintext_credentials(old: dict[str, Any] | None, new: dict[str, Any]) -> list[str]:
    """Names of credentials the new entry would store in plain text.

    Environment also carries ordinary settings (URLs, paths, CPU counts): match
    credential names, not substrings such as TOKENIZERS_PARALLELISM or
    MAX_TOKENS. Values already stored before stay; only new or changed ones count.
    A credential belongs in the vault and reaches the config as an sref_ value in
    env_secret_refs / secret_header_refs.
    """
    old = old or {}
    names: list[str] = []
    for field, every_key in (("env", False), ("secret_headers", True)):
        before = old.get(field) if isinstance(old.get(field), dict) else {}
        for key, value in (new.get(field) or {}).items():
            if value and before.get(key) != value and (every_key or _CREDENTIAL_NAME.search(key)):
                names.append(key)
    return names


def restore_masked_secrets(old: dict[str, Any] | None, new: dict[str, Any]) -> dict[str, Any]:
    """A config edited from public_server_view keeps the stored values behind the mask."""
    out = dict(new)
    for field in _SECRET_SERVER_FIELDS:
        values = out.get(field)
        before = (old or {}).get(field)
        if isinstance(values, dict):
            out[field] = {
                key: (before.get(key, "") if isinstance(before, dict) else "") if value == SECRET_MASK else value
                for key, value in values.items()
            }
    return out


def public_server_view(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """API-boundary redaction: replace the VALUES of secret-bearing fields
    (`env`, `secret_headers`) with a masked marker while KEEPING the key names, so
    the UI can show which secrets are configured without ever exposing a value.

    Applied only at the HTTP read boundary — `list_servers()` itself keeps the real
    values because the launch path (mcp_provider) needs `env` to start a server.
    """
    out: list[dict[str, Any]] = []
    for srv in servers:
        s = dict(srv)
        for field in _SECRET_SERVER_FIELDS:
            val = s.get(field)
            if isinstance(val, dict) and val:
                s[field] = {k: SECRET_MASK for k in val}     # write-only: keys, not values
        out.append(s)
    return out


def save_servers(servers: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Replace the configured-server list atomically.

    Stops any running server whose id is removed AND any whose
    command/args/env/enabled changed (a settings change must propagate
    to the live process). The returned list is the persisted version
    with status injected — same shape as `list_servers`.
    """
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for spec in servers or []:
        n = _validate_server(spec)
        if n is None:
            continue
        if n["id"] in seen_ids:
            continue  # last write wins would be confusing — drop dup
        seen_ids.add(n["id"])
        normalized.append(n)

    with _LOCK:
        # Stop clients whose id is gone OR whose spec changed.
        prev_specs: dict[str, dict[str, Any]] = {
            s.get("id"): s for s in _read_config().get("servers", []) if isinstance(s, dict)
        }
        for sid in set(_LIVE_CLIENTS) | set(_STARTING):
            new_spec = next((n for n in normalized if n["id"] == sid), None)
            old_spec = prev_specs.get(sid)
            if new_spec is None or _spec_changed(old_spec, new_spec):
                _stop_locked(sid)

        _write_config({"servers": normalized})

    # Outside the lock: list_servers() takes the lock again itself.
    return list_servers()


def _spec_changed(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    """True if a settings change must propagate to the live process.

    Compares against the *normalized* new spec but the *raw* old one (that's
    what save_servers passes), so we normalize old here too. Covers both
    transports' connection-relevant fields.
    """
    if old is None:
        return True
    old_n = _validate_server(old)
    if old_n is None:
        return True
    keys = (
        "transport", "enabled",
        # stdio
        "command", "args", "cwd", "env", "env_secret_refs",
        # http
        "url", "headers", "secret_headers", "secret_header_refs",
        "allow_insecure_http", "allow_private_address",
    )
    for key in keys:
        if old_n.get(key) != new.get(key):
            return True
    return False


# ── Lifecycle ───────────────────────────────────────────────────


def _resolve_secret_refs(refs: dict[str, str]) -> dict[str, str]:
    if not refs:
        return {}
    from app.infrastructure.secrets import vault

    return {name: vault.resolve(secret_ref) for name, secret_ref in refs.items()}


def _discover_tool_names(client: Any, server_id: str) -> dict[str, Any]:
    """Return a compact, schema-free summary of the server's callable tools."""
    names: list[str] = []
    seen: set[str] = set()
    for tool in client.list_tools():
        name = tool.get("name") if isinstance(tool, dict) else None
        if not isinstance(name, str) or not name:
            continue
        qualified = f"{server_id}__{name}"
        if qualified in seen:
            continue
        seen.add(qualified)
        names.append(qualified)
    return {
        "available_tool_count": len(names),
        "available_tool_names": names[:_MAX_REPORTED_TOOL_NAMES],
        "available_tool_names_truncated": len(names) > _MAX_REPORTED_TOOL_NAMES,
    }


def discover_tools(server_id: str) -> dict[str, Any]:
    """Return the compact tool catalog for one already-running server."""
    with _LOCK:
        client = _LIVE_CLIENTS.get(str(server_id))
        if client is None or not client.is_alive():
            return {
                "ok": False,
                "error": f"server '{server_id}' is not running",
                "server_id": str(server_id),
            }
        try:
            summary = _discover_tool_names(client, str(server_id))
        except Exception as exc:
            return _tool_discovery_failure(
                str(server_id),
                exc,
                already_running=True,
            )
        return {"ok": True, "server_id": str(server_id), **summary}


def _tool_discovery_failure(
    server_id: str,
    exc: Exception,
    *,
    already_running: bool,
) -> dict[str, Any]:
    msg = f"MCP tool discovery failed: {exc}"
    _LAST_ERROR[server_id] = msg
    return {
        "ok": False,
        "error": msg,
        "server_id": server_id,
        "already_running": already_running,
    }


def _resolve_cwd(value: str | None, server_id: str = "") -> str | None:
    if not value:
        if not server_id:
            return None
        # A server writes its own files (Playwright: .playwright-mcp/ snapshots and
        # logs) into its working folder. Inherited, that is the backend's folder
        # inside the sealed release: on 2026-10-12 two such files broke the seal and
        # Foundation refused to start Elira. Without its own cwd a server works in
        # data/mcp/<id>/, next to the servers installed there.
        safe = re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,63}", server_id) and ".." not in server_id
        folder = CONFIG_PATH.parent / "mcp" / (server_id if safe else "_shared")
        folder.mkdir(parents=True, exist_ok=True)
        return str(folder.resolve())
    path = Path(value)
    return str(path if path.is_absolute() else (CONFIG_PATH.parent / path).resolve())


def start_server(
    server_id: str, *, _cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    """Serialize starts while keeping the inventory responsive during handshake."""
    with _START_LOCK:
        if _cancel_event is not None and _cancel_event.is_set():
            return {"ok": False, "server_id": server_id, "error": "MCP warmup cancelled"}
        return _start_server(server_id, _cancel_event=_cancel_event)


def _start_server(
    server_id: str, *, _cancel_event: threading.Event | None = None,
) -> dict[str, Any]:
    with _LOCK:
        if _cancel_event is not None and _cancel_event.is_set():
            return {"ok": False, "server_id": server_id, "error": "MCP warmup cancelled"}
        spec = _find_spec_locked(server_id)
        if spec is None:
            return {"ok": False, "error": f"server '{server_id}' not configured"}
        if not spec.get("enabled", True):
            return {"ok": False, "error": f"сервер {server_id} выключен (enabled: false) в Настройки → MCP"}
        existing = _LIVE_CLIENTS.get(server_id)
        restarted = False
        if (existing is not None and existing.is_alive() and server_id in _LIVE_SPECS
                and _spec_changed(_LIVE_SPECS[server_id], spec)):
            _stop_locked(server_id)
            existing, restarted = None, True
        if existing is not None and not existing.is_alive():
            _stop_locked(server_id)
            existing = None
        token: dict[str, Any] = {}
        _STARTING[server_id] = token

    try:
        if existing is not None:
            try:
                tool_summary = _discover_tool_names(existing, server_id)
            except Exception as exc:
                with _LOCK:
                    return _tool_discovery_failure(server_id, exc, already_running=True)
            with _LOCK:
                current_spec = _find_spec_locked(server_id)
                if (_STARTING.get(server_id) is not token
                        or _LIVE_CLIENTS.get(server_id) is not existing
                        or current_spec is None or _spec_changed(spec, current_spec)):
                    return {"ok": False, "server_id": server_id,
                            "error": "MCP start cancelled or config changed"}
                _LAST_ERROR[server_id] = None
            return {"ok": True, "already_running": True, "server_id": server_id, **tool_summary}
        transport = spec.get("transport", "stdio")
        if transport == "http":
            try:
                resolved_headers = _resolve_secret_refs(spec.get("secret_header_refs") or {})
            except Exception as exc:
                msg = f"MCP secret resolution failed: {exc}"
                with _LOCK:
                    if _STARTING.get(server_id) is token:
                        _LAST_ERROR[server_id] = msg
                return {"ok": False, "error": msg}
            # Lazy import so the httpx-backed transport (and httpx itself) is
            # only loaded when a remote server is actually started.
            from app.application.tool_providers.mcp_http_client import (
                McpHttpClient,
                McpError as _HttpMcpError,
            )
            client: Any = McpHttpClient(
                url=spec["url"],
                headers=spec.get("headers") or None,
                secret_headers={**(spec.get("secret_headers") or {}), **resolved_headers} or None,
                allow_insecure_http=bool(spec.get("allow_insecure_http", False)),
                allow_private_address=bool(spec.get("allow_private_address", False)),
            )
            start_error: type[Exception] = _HttpMcpError
        else:
            try:
                resolved_env = _resolve_secret_refs(spec.get("env_secret_refs") or {})
            except Exception as exc:
                msg = f"MCP secret resolution failed: {exc}"
                with _LOCK:
                    if _STARTING.get(server_id) is token:
                        _LAST_ERROR[server_id] = msg
                return {"ok": False, "error": msg}
            cwd = _resolve_cwd(spec.get("cwd"), server_id)
            if cwd and not Path(cwd).is_dir():
                msg = f"MCP cwd does not exist: {cwd}"
                with _LOCK:
                    if _STARTING.get(server_id) is token:
                        _LAST_ERROR[server_id] = msg
                return {"ok": False, "error": msg}
            client = McpClient(
                command=spec["command"],
                args=spec["args"],
                env={**(spec["env"] or {}), **resolved_env} or None,
                cwd=cwd,
            )
            start_error = McpError

        with _LOCK:
            if _STARTING.get(server_id) is not token:
                client.stop()
                return {"ok": False, "server_id": server_id, "error": "MCP start cancelled"}
            token["client"] = client
        try:
            client.start()
        except start_error as exc:
            with _LOCK:
                if _STARTING.get(server_id) is token:
                    _LAST_ERROR[server_id] = str(exc)
            return {"ok": False, "error": str(exc)}

        try:
            tool_summary = _discover_tool_names(client, server_id)
        except Exception as exc:
            try:
                client.stop()
            except Exception:
                logger.warning(
                    "mcp_runtime: cleanup after failed tool discovery for %r raised",
                    server_id,
                    exc_info=True,
                )
            with _LOCK:
                return _tool_discovery_failure(
                    server_id, exc, already_running=False,
                )

        with _LOCK:
            current_spec = _find_spec_locked(server_id)
            if ((_cancel_event is not None and _cancel_event.is_set())
                    or _STARTING.get(server_id) is not token or current_spec is None
                    or _spec_changed(spec, current_spec)):
                cancelled = True
            else:
                cancelled = False
                _LIVE_CLIENTS[server_id] = client
                _LIVE_SPECS[server_id] = spec
                _LAST_ERROR[server_id] = None
        if cancelled:
            client.stop()
            return {"ok": False, "server_id": server_id, "error": "MCP start cancelled or config changed"}
        return {
            "ok": True, "already_running": False,
            **({"restarted_for_changed_config": True} if restarted else {}),
            "server_id": server_id, "server_info": client.server_info, **tool_summary,
        }
    finally:
        with _LOCK:
            if _STARTING.get(server_id) is token:
                _STARTING.pop(server_id, None)


def stop_server(server_id: str) -> dict[str, Any]:
    """Stop a running server. Returns ok=True even if it wasn't
    running (idempotent)."""
    with _LOCK:
        was_running = server_id in _LIVE_CLIENTS
        _stop_locked(server_id)
        _LAST_ERROR.pop(server_id, None)
        return {"ok": True, "was_running": was_running, "server_id": server_id}


def _stop_locked(server_id: str) -> None:
    """Internal helper. Caller must hold _LOCK."""
    starting = _STARTING.pop(server_id, None)
    pending_client = starting.get("client") if starting is not None else None
    client = _LIVE_CLIENTS.pop(server_id, None)
    _LIVE_SPECS.pop(server_id, None)
    for client in (client, pending_client):
        if client is None:
            continue
        try:
            client.stop()
        except Exception as exc:
            logger.warning("mcp_runtime: stop_server(%r) raised: %s", server_id, exc)
    try:
        from app.application.tool_registry import runtime as tool_registry

        prefix = f"{server_id}__"
        for tool in tool_registry.list_tools_with_schemas(
            source="mcp",
            enabled_only=False,
        ):
            name = str(tool.get("name") or "")
            if name.startswith(prefix) and bool(tool.get("enabled", False)):
                tool_registry.update_tool(name, {"enabled": False})
    except Exception:
        logger.warning(
            "mcp_runtime: failed to mark tools inactive for %r",
            server_id,
            exc_info=True,
        )


def restart_server(server_id: str) -> dict[str, Any]:
    stop_server(server_id)
    return start_server(server_id)


def get_live_client(server_id: str) -> McpClient | None:
    """Return the live client for `server_id`, or None if not running.
    Used by McpToolProvider for dispatch."""
    with _LOCK:
        client = _LIVE_CLIENTS.get(server_id)
        if client is None or not client.is_alive():
            return None
        return client


def stop_all_servers() -> None:
    """Used by tests + graceful shutdown."""
    with _LOCK:
        _WARM_CANCEL.set()
        for sid in set(_LIVE_CLIENTS) | set(_STARTING):
            _stop_locked(sid)


def _find_spec_locked(server_id: str) -> dict[str, Any] | None:
    for spec in _read_config().get("servers", []):
        normalized = _validate_server(spec)
        if normalized and normalized["id"] == server_id:
            return normalized
    return None


def autostart_server_ids() -> set[str]:
    """Configured warm integrations; no network or lifecycle lock required."""
    return {
        spec["id"] for raw in _read_config().get("servers", [])
        if (spec := _validate_server(raw)) is not None
        and spec.get("enabled", True) and spec.get("autostart", False)
    }


def start_autostart_servers() -> dict[str, Any]:
    """Begin background warmup without delaying ASGI startup/activation."""
    global _WARM_THREAD
    with _WARM_LOCK:
        if _WARM_THREAD is not None and _WARM_THREAD.is_alive():
            return {"ok": True, "already_started": True}
        server_ids = sorted(autostart_server_ids())
        _WARM_CANCEL.clear()
        _WARM_READY.clear()

        def warm() -> None:
            try:
                for server_id in server_ids:
                    if _WARM_CANCEL.is_set():
                        break
                    try:
                        result = start_server(server_id, _cancel_event=_WARM_CANCEL)
                    except Exception as exc:
                        with _LOCK:
                            _LAST_ERROR[server_id] = str(exc)
                        logger.warning("MCP autostart %s failed", server_id, exc_info=True)
                        continue
                    if not result.get("ok"):
                        logger.warning("MCP autostart %s failed: %s", server_id, result.get("error"))
            finally:
                _WARM_READY.set()

        _WARM_THREAD = threading.Thread(target=warm, name="elira-mcp-warmup", daemon=True)
        _WARM_THREAD.start()
        return {"ok": True, "server_ids": server_ids}


def wait_for_autostart(timeout: float = AUTOSTART_WAIT_SECONDS) -> bool:
    """Bound the first agent's readiness wait; other tools remain usable."""
    if _WARM_THREAD is None:
        start_autostart_servers()
    ready = _WARM_READY.wait(max(0.0, timeout))
    if not ready:
        logger.warning("MCP warmup still running after %.1fs; continuing with available tools", timeout)
    return ready
