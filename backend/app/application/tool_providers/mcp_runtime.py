"""MCP server configuration + lifecycle.

Stores the user's list of configured MCP servers at
`data/mcp_servers.json` and keeps a process-local registry of
running `McpClient` instances.

Config shape:
    {"servers": [
        {
            "id": "github",
            "command": "npx",
            "args": ["-y", "@modelcontextprotocol/server-github"],
            "env": {"GITHUB_TOKEN": "..."},
            "enabled": true
        },
        ...
    ]}

Concurrency:
    All mutations and lookups are serialized by a module-level lock.
    Start/stop calls themselves can be slow (npm fetch on first run,
    subprocess teardown) — they run with the lock held to keep the
    `id → client` mapping consistent.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any

from app.application.feature_flags import flag_enabled
from app.application.tool_providers.mcp_client import McpClient, McpError
from app.core.data_files import data_file


logger = logging.getLogger(__name__)


# Remote (HTTP) MCP transport is gated and OFF by default. A configured http
# server refuses to start until the operator opts in; stdio remains the
# default and is never affected by this flag. The flag is resolved through
# the shared feature-flags layer: an explicit ``ELIRA_REMOTE_MCP`` env
# override still wins, otherwise the persisted (UI-toggleable)
# ``data/feature_flags.json`` value is used.


def _remote_mcp_enabled() -> bool:
    return flag_enabled("remote_mcp")


CONFIG_PATH: Path = data_file("mcp_servers.json")
_LOCK = threading.Lock()
# id → McpClient (only entries for servers we've actually started)
_LIVE_CLIENTS: dict[str, McpClient] = {}
# id → last connect-attempt error message (or None on success)
_LAST_ERROR: dict[str, str | None] = {}


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
        json.dumps(payload, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _str_str_map(value: Any) -> dict[str, str] | None:
    """Validate a {str: str} mapping. Returns None if malformed."""
    if not isinstance(value, dict):
        return None
    if any(not isinstance(k, str) or not isinstance(v, str) for k, v in value.items()):
        return None
    return dict(value)


def _validate_server(spec: Any) -> dict[str, Any] | None:
    """Return a normalized server dict or None if `spec` is malformed.

    Two transports:
      * "stdio" (default) — spawns a subprocess from command/args/env. We're
        strict here because these specs end up running whatever the user gave.
      * "http" — connects to a remote MCP server at `url`. Carries optional
        non-secret `headers`, secret `secret_headers` (kept separate so they
        never get logged/audited), and `allow_insecure_http` to permit plain
        http. The actual SSRF/scheme enforcement lives in McpHttpClient; here
        we only validate shape.
    """
    if not isinstance(spec, dict):
        return None
    sid = spec.get("id")
    if not isinstance(sid, str) or not sid.strip():
        return None
    enabled = bool(spec.get("enabled", True))
    transport = spec.get("transport", "stdio")
    if transport not in ("stdio", "http"):
        return None

    if transport == "http":
        url = spec.get("url")
        if not isinstance(url, str) or not url.strip():
            return None
        headers = _str_str_map(spec.get("headers", {}))
        secret_headers = _str_str_map(spec.get("secret_headers", {}))
        if headers is None or secret_headers is None:
            return None
        return {
            "id": sid.strip(),
            "transport": "http",
            "url": url.strip(),
            "headers": headers,
            "secret_headers": secret_headers,
            "allow_insecure_http": bool(spec.get("allow_insecure_http", False)),
            "allow_private_address": bool(spec.get("allow_private_address", False)),
            "enabled": enabled,
        }

    # transport == "stdio"
    command = spec.get("command")
    if not isinstance(command, str) or not command.strip():
        return None
    args = spec.get("args", [])
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        return None
    env = _str_str_map(spec.get("env", {}))
    if env is None:
        return None
    return {
        "id": sid.strip(),
        "transport": "stdio",
        "command": command.strip(),
        "args": [a for a in args],
        "env": env,
        "enabled": enabled,
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
            if client is None:
                status = "stopped"
            elif client.is_alive():
                status = "running"
            else:
                status = "crashed"
            normalized["status"] = status
            normalized["last_error"] = _LAST_ERROR.get(sid)
            out.append(normalized)
    return out


_SECRET_SERVER_FIELDS = ("env", "secret_headers")


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
                s[field] = {k: "●●●" for k in val}     # write-only: keys, not values
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
        for sid in list(_LIVE_CLIENTS.keys()):
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
        "command", "args", "env",
        # http
        "url", "headers", "secret_headers", "allow_insecure_http", "allow_private_address",
    )
    for key in keys:
        if old_n.get(key) != new.get(key):
            return True
    return False


# ── Lifecycle ───────────────────────────────────────────────────


def start_server(server_id: str) -> dict[str, Any]:
    """Bring up the configured server with this id. Idempotent if the
    server is already running."""
    with _LOCK:
        spec = _find_spec_locked(server_id)
        if spec is None:
            return {"ok": False, "error": f"server '{server_id}' not configured"}
        if not spec.get("enabled", True):
            return {"ok": False, "error": f"server '{server_id}' is marked disabled"}

        existing = _LIVE_CLIENTS.get(server_id)
        if existing is not None and existing.is_alive():
            return {"ok": True, "already_running": True, "server_id": server_id}
        # Stale entry (crashed process) — clear it.
        if existing is not None:
            _stop_locked(server_id)

        transport = spec.get("transport", "stdio")
        if transport == "http":
            if not _remote_mcp_enabled():
                msg = (
                    "remote MCP disabled: set ELIRA_REMOTE_MCP=1 to enable the "
                    "HTTP transport for server '" + server_id + "'"
                )
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
                secret_headers=spec.get("secret_headers") or None,
                allow_insecure_http=bool(spec.get("allow_insecure_http", False)),
                allow_private_address=bool(spec.get("allow_private_address", False)),
            )
            start_error: type[Exception] = _HttpMcpError
        else:
            client = McpClient(
                command=spec["command"],
                args=spec["args"],
                env=spec["env"] or None,
            )
            start_error = McpError

        try:
            client.start()
        except start_error as exc:
            _LAST_ERROR[server_id] = str(exc)
            return {"ok": False, "error": str(exc)}

        _LIVE_CLIENTS[server_id] = client
        _LAST_ERROR[server_id] = None
        return {
            "ok": True,
            "already_running": False,
            "server_id": server_id,
            "server_info": client.server_info,
        }


def stop_server(server_id: str) -> dict[str, Any]:
    """Stop a running server. Returns ok=True even if it wasn't
    running (idempotent)."""
    with _LOCK:
        was_running = server_id in _LIVE_CLIENTS
        _stop_locked(server_id)
        return {"ok": True, "was_running": was_running, "server_id": server_id}


def _stop_locked(server_id: str) -> None:
    """Internal helper. Caller must hold _LOCK."""
    client = _LIVE_CLIENTS.pop(server_id, None)
    if client is None:
        return
    try:
        client.stop()
    except Exception as exc:
        logger.warning("mcp_runtime: stop_server(%r) raised: %s", server_id, exc)


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
        for sid in list(_LIVE_CLIENTS.keys()):
            _stop_locked(sid)


def _find_spec_locked(server_id: str) -> dict[str, Any] | None:
    for spec in _read_config().get("servers", []):
        normalized = _validate_server(spec)
        if normalized and normalized["id"] == server_id:
            return normalized
    return None


def start_all_enabled() -> dict[str, Any]:
    """Convenience: start every enabled server. Used on agent startup
    so the first chat turn has the tool list ready. Failures are
    captured per-server, never abort the loop."""
    results: dict[str, Any] = {}
    for spec in list_servers():
        if not spec.get("enabled", True):
            continue
        results[spec["id"]] = start_server(spec["id"])
    return {"started": results}
