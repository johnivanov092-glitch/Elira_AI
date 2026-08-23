"""LSP server configuration + lifecycle.

Sibling of `mcp_runtime.py`. Stores the user's list of configured
language servers at `data/lsp_servers.json` and keeps a process-local
registry of running `LspClient` instances.

Config shape:
    {"servers": [
        {
            "id": "pyright",
            "language": "python",
            "command": "pyright-langserver",
            "args": ["--stdio"],
            "enabled": false
        },
        ...
    ]}

Differences from MCP:
  * **Disabled by default.** A server spec's `enabled` defaults to
    `False` (MCP defaults `True`). Combined with "no config file ⇒ no
    servers", this makes the whole LSP subsystem opt-in: nothing spawns
    until the user both configures *and* enables a server *and* hits the
    explicit start route.
  * **`language` is required** and is what the provider dispatches on.
  * **`start_server` takes a `root_path`** (the project root) which it
    turns into a `file://` rootUri for the handshake. Lazily, per-start,
    because the same configured server can analyse different projects.

Concurrency mirrors MCP: a module-level lock serializes all mutations
and lookups; slow start/stop run with the lock held to keep the
`id → client` mapping consistent.
"""
from __future__ import annotations

import json
import logging
import threading
from pathlib import Path
from typing import Any
from urllib.parse import urljoin
from urllib.request import pathname2url

from app.application.tool_providers.lsp_client import LspClient, LspError
from app.core.data_files import data_file


logger = logging.getLogger(__name__)


CONFIG_PATH: Path = data_file("lsp_servers.json")
_LOCK = threading.Lock()
# id → LspClient (only entries for servers we've actually started)
_LIVE_CLIENTS: dict[str, LspClient] = {}
# id → last start-attempt error message (or None on success)
_LAST_ERROR: dict[str, str | None] = {}


# ── Config persistence ──────────────────────────────────────────


def _read_config() -> dict[str, Any]:
    if not CONFIG_PATH.exists():
        return {"servers": []}
    try:
        raw = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, OSError) as exc:
        logger.warning("lsp_runtime: failed to read %s: %s — treating as empty", CONFIG_PATH, exc)
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

    Strict, because these specs spawn subprocesses with user-provided
    args. `language` is required (the provider dispatches on it) and
    `enabled` defaults to **False** — LSP is opt-in.
    """
    if not isinstance(spec, dict):
        return None
    sid = spec.get("id")
    language = spec.get("language")
    command = spec.get("command")
    if not isinstance(sid, str) or not sid.strip():
        return None
    if not isinstance(language, str) or not language.strip():
        return None
    if not isinstance(command, str) or not command.strip():
        return None
    args = spec.get("args", [])
    if not isinstance(args, list) or any(not isinstance(a, str) for a in args):
        return None
    enabled = bool(spec.get("enabled", False))
    return {
        "id": sid.strip(),
        "language": language.strip(),
        "command": command.strip(),
        "args": [a for a in args],
        "enabled": enabled,
    }


def list_servers() -> list[dict[str, Any]]:
    """Configured servers, with live status injected.

    Status is one of:
      "stopped"   — never started or stopped cleanly
      "running"   — subprocess alive, handshake done
      "crashed"   — process died after starting
      "error"     — last start attempt failed (see `last_error`)
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
                status = "error" if _LAST_ERROR.get(sid) else "stopped"
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
    language/command/args/enabled changed (a settings change must
    propagate to the live process). Returns the persisted version with
    status injected — same shape as `list_servers`.
    """
    normalized: list[dict[str, Any]] = []
    seen_ids: set[str] = set()
    for spec in servers or []:
        n = _validate_server(spec)
        if n is None:
            continue
        if n["id"] in seen_ids:
            continue  # drop dup — silent last-write-wins would confuse
        seen_ids.add(n["id"])
        normalized.append(n)

    with _LOCK:
        prev_specs: dict[str, dict[str, Any]] = {
            s.get("id"): s for s in _read_config().get("servers", []) if isinstance(s, dict)
        }
        for sid in list(_LIVE_CLIENTS.keys()):
            new_spec = next((n for n in normalized if n["id"] == sid), None)
            old_spec = prev_specs.get(sid)
            if new_spec is None or _spec_changed(old_spec, new_spec):
                _stop_locked(sid)

        _write_config({"servers": normalized})

    return list_servers()


def _spec_changed(old: dict[str, Any] | None, new: dict[str, Any]) -> bool:
    if old is None:
        return True
    for key in ("language", "command", "args", "enabled"):
        if old.get(key) != new.get(key):
            return True
    return False


# ── Lifecycle ───────────────────────────────────────────────────


def _path_to_uri(root_path: str | None) -> str | None:
    if not root_path:
        return None
    try:
        resolved = Path(root_path).resolve()
    except (OSError, ValueError):
        return None
    # pathname2url + file: base yields a correct file:// URI on both
    # Windows (file:///C:/...) and POSIX (file:///home/...).
    return urljoin("file:", pathname2url(str(resolved)))


def start_server(server_id: str, root_path: str | None = None) -> dict[str, Any]:
    """Bring up the configured server with this id, analysing `root_path`.

    Idempotent if already running. Never raises — a spawn/handshake
    failure is captured in `_LAST_ERROR` and returned as
    `{"ok": False, "error": ...}`, so the agent keeps working on grep.
    """
    with _LOCK:
        spec = _find_spec_locked(server_id)
        if spec is None:
            return {"ok": False, "error": f"server '{server_id}' not configured"}
        existing = _LIVE_CLIENTS.get(server_id)
        if existing is not None and existing.is_alive():
            return {"ok": True, "already_running": True, "server_id": server_id}
        if existing is not None:
            _stop_locked(server_id)

        client = LspClient(
            language=spec["language"],
            command=spec["command"],
            args=spec["args"],
            root_uri=_path_to_uri(root_path),
            cwd=root_path or None,
        )
        try:
            client.start()
        except LspError as exc:
            _LAST_ERROR[server_id] = str(exc)
            return {"ok": False, "error": str(exc)}
        except Exception as exc:  # defense-in-depth: never let start() escape
            _LAST_ERROR[server_id] = str(exc)
            return {"ok": False, "error": str(exc)}

        _LIVE_CLIENTS[server_id] = client
        _LAST_ERROR[server_id] = None
        return {
            "ok": True,
            "already_running": False,
            "server_id": server_id,
            "language": spec["language"],
        }


def stop_server(server_id: str) -> dict[str, Any]:
    """Stop a running server. Idempotent (ok=True even if not running).
    This is the **explicit shutdown** the deferred-track spec requires."""
    with _LOCK:
        was_running = server_id in _LIVE_CLIENTS
        _stop_locked(server_id)
        _LAST_ERROR.pop(server_id, None)
        return {"ok": True, "was_running": was_running, "server_id": server_id}


def _stop_locked(server_id: str) -> None:
    """Internal helper. Caller must hold _LOCK."""
    client = _LIVE_CLIENTS.pop(server_id, None)
    if client is None:
        return
    try:
        client.stop()
    except Exception as exc:
        logger.warning("lsp_runtime: stop_server(%r) raised: %s", server_id, exc)


def restart_server(server_id: str, root_path: str | None = None) -> dict[str, Any]:
    stop_server(server_id)
    return start_server(server_id, root_path)


def get_live_client(server_id: str) -> LspClient | None:
    """Return the live client for `server_id`, or None if not running.
    Used by LspToolProvider for dispatch + is_enabled."""
    with _LOCK:
        client = _LIVE_CLIENTS.get(server_id)
        if client is None or not client.is_alive():
            return None
        return client


def live_clients() -> dict[str, LspClient]:
    """Snapshot of {id → live client} for all currently-alive servers.
    Used by the provider to pick a server by language or fall back to the
    single live one."""
    with _LOCK:
        return {
            sid: client
            for sid, client in _LIVE_CLIENTS.items()
            if client.is_alive()
        }


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
