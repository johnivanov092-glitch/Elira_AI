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

from app.application.tool_providers.mcp_client import McpClient, McpError
from app.core.data_files import data_file


logger = logging.getLogger(__name__)


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


def _validate_server(spec: Any) -> dict[str, Any] | None:
    """Return a normalized server dict or None if `spec` is malformed.

    We're strict about what we accept here because these specs end up
    spawning subprocesses with whatever args the user provided.
    """
    if not isinstance(spec, dict):
        return None
    sid = spec.get("id")
    command = spec.get("command")
    if not isinstance(sid, str) or not sid.strip():
        return None
    if not isinstance(command, str) or not command.strip():
        return None
    args = spec.get("args", [])
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        return None
    env = spec.get("env", {})
    if not isinstance(env, dict) or any(
        not isinstance(k, str) or not isinstance(v, str) for k, v in env.items()
    ):
        return None
    enabled = bool(spec.get("enabled", True))
    return {
        "id": sid.strip(),
        "command": command.strip(),
        "args": [a for a in args],
        "env": dict(env),
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
    if old is None:
        return True
    for key in ("command", "args", "env", "enabled"):
        if old.get(key) != new.get(key):
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

        client = McpClient(
            command=spec["command"],
            args=spec["args"],
            env=spec["env"] or None,
        )
        try:
            client.start()
        except McpError as exc:
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
