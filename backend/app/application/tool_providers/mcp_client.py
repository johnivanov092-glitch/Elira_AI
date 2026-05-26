"""Minimal MCP (Model Context Protocol) stdio client.

Speaks JSON-RPC 2.0 over a child process's stdin/stdout pipes — the
transport every "local" MCP server (github, slack, postgres, ...)
ships with. HTTP transport is out of scope for this first pass.

Lifecycle:
    client = McpClient(command="npx", args=["-y", "@modelcontextprotocol/server-github"])
    client.start()                     # spawns subprocess + reader thread + handshake
    schemas = client.list_tools()      # tools/list — list of MCP tool descriptors
    result = client.call_tool(name, args)
    client.stop()                      # closes stdin → server exits → reader thread joins

Thread model:
    One background reader thread per client, blocked on subprocess.stdout.readline().
    Every response carries the request `id`; the reader resolves a per-id
    threading.Event so the requester wakes up. Notifications (no `id`)
    are currently logged-and-dropped — we don't subscribe to any.

What we deliberately DON'T implement yet:
    * resources/prompts (only tools)
    * server-initiated requests (sampling, roots)
    * progress notifications
    * HTTP/SSE transport
These can grow on top of this client without changing its contract.
"""
from __future__ import annotations

import json
import logging
import subprocess
import threading
import time
from dataclasses import dataclass, field
from typing import Any, Optional


logger = logging.getLogger(__name__)


# JSON-RPC protocol version is fixed for MCP.
JSONRPC_VERSION = "2.0"
# MCP protocol version we negotiate. 2024-11-05 is the spec used by
# the publicly-available reference servers (github, slack, postgres,
# filesystem) at the time this client was written.
MCP_PROTOCOL_VERSION = "2024-11-05"

# Time we'll wait for a response to a given request before giving up.
# Server start-up itself can be slow (npm fetching a package the first
# time), so the initialize handshake gets a generous timeout.
DEFAULT_REQUEST_TIMEOUT = 30.0
INITIALIZE_TIMEOUT = 120.0


class McpError(Exception):
    """Raised on protocol-level failures (timeout, JSON-RPC error
    response, server crash). The MCP **provider** wraps these into
    `{"text": "ERROR: ..."}` so they never bubble out of dispatch."""


@dataclass
class _PendingRequest:
    event: threading.Event = field(default_factory=threading.Event)
    response: Optional[dict[str, Any]] = None


class McpClient:
    """One client = one MCP server subprocess.

    Construction is cheap; the subprocess is spawned by .start(). Each
    instance owns:
      * the Popen object
      * a background thread reading stdout line-by-line
      * a dict of in-flight request_id → _PendingRequest
    """

    def __init__(
        self,
        command: str,
        args: Optional[list[str]] = None,
        env: Optional[dict[str, str]] = None,
        *,
        cwd: Optional[str] = None,
    ) -> None:
        self._command = command
        self._args = list(args or [])
        self._env = env  # passed to Popen; None → inherit
        self._cwd = cwd
        self._proc: Optional[subprocess.Popen[str]] = None
        self._reader: Optional[threading.Thread] = None
        self._pending: dict[int, _PendingRequest] = {}
        self._pending_lock = threading.Lock()
        self._stdout_lock = threading.Lock()  # serializes writes to stdin
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._stopped = False
        # Cached after initialize so callers can introspect.
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Spawn the subprocess + reader thread + run the initialize
        handshake. Raises McpError on any failure (process didn't start,
        handshake timed out, server returned an error)."""
        if self._proc is not None:
            return  # idempotent

        # Build env: by default inherit current; merge user-provided.
        import os
        full_env = dict(os.environ)
        if self._env:
            full_env.update(self._env)

        try:
            self._proc = subprocess.Popen(
                [self._command, *self._args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                bufsize=1,  # line-buffered
                env=full_env,
                cwd=self._cwd,
            )
        except FileNotFoundError:
            raise McpError(f"command not found: {self._command!r}")
        except OSError as exc:
            raise McpError(f"failed to spawn {self._command!r}: {exc}")

        self._reader = threading.Thread(
            target=self._read_loop,
            name=f"mcp-reader-{self._command}",
            daemon=True,
        )
        self._reader.start()

        # MCP initialize handshake.
        try:
            init_response = self._request(
                method="initialize",
                params={
                    "protocolVersion": MCP_PROTOCOL_VERSION,
                    "capabilities": {},
                    "clientInfo": {"name": "elira", "version": "0.1"},
                },
                timeout=INITIALIZE_TIMEOUT,
            )
        except McpError:
            # Failed to handshake — kill subprocess so we don't leak.
            self.stop()
            raise

        # A JSON-RPC server can reply with an "error" envelope to the
        # initialize call (bad protocolVersion, etc.). Treat that as
        # a handshake failure, otherwise the client would happily
        # proceed against a server that explicitly refused it.
        if "error" in init_response:
            err = init_response["error"] or {}
            msg = err.get("message", "?") if isinstance(err, dict) else "?"
            self.stop()
            raise McpError(f"server rejected initialize: {msg}")

        result = init_response.get("result", {}) or {}
        self.server_info = result.get("serverInfo", {}) or {}
        self.server_capabilities = result.get("capabilities", {}) or {}

        # Per spec, client must send `notifications/initialized` after
        # the initialize handshake succeeds. No response expected.
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        """Tear the subprocess down. Idempotent and safe to call from
        any thread."""
        if self._stopped:
            return
        self._stopped = True
        proc = self._proc
        if proc is None:
            return
        # Closing stdin signals shutdown to most MCP servers.
        try:
            if proc.stdin and not proc.stdin.closed:
                proc.stdin.close()
        except Exception:
            pass
        # Give the server a moment to exit cleanly.
        try:
            proc.wait(timeout=2.0)
        except subprocess.TimeoutExpired:
            proc.kill()
            try:
                proc.wait(timeout=1.0)
            except subprocess.TimeoutExpired:
                pass
        # Reader thread should exit on EOF; daemon=True so we don't
        # care if it takes a beat extra.
        # Wake up any pending requesters with an error.
        with self._pending_lock:
            for pending in self._pending.values():
                if pending.response is None:
                    pending.response = {
                        "jsonrpc": JSONRPC_VERSION,
                        "id": -1,
                        "error": {"code": -32000, "message": "server stopped"},
                    }
                pending.event.set()
            self._pending.clear()

    def is_alive(self) -> bool:
        return self._proc is not None and self._proc.poll() is None

    # ── Public API ──────────────────────────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        """Returns the MCP `tools/list` result as a list of dicts with
        keys: name, description, inputSchema."""
        response = self._request("tools/list", {})
        result = response.get("result") or {}
        tools = result.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        """Invoke `tools/call` and return the server's result dict
        (the part inside the JSON-RPC `result` envelope, NOT the
        envelope itself)."""
        response = self._request(
            "tools/call",
            {"name": tool_name, "arguments": arguments or {}},
        )
        if "error" in response:
            err = response["error"] or {}
            raise McpError(
                f"server rejected tools/call({tool_name!r}): "
                f"{err.get('message', '?')} (code {err.get('code', '?')})"
            )
        return response.get("result", {}) or {}

    # ── Internals ───────────────────────────────────────────────

    def _next_request_id(self) -> int:
        with self._id_lock:
            rid = self._next_id
            self._next_id += 1
            return rid

    def _send(self, payload: dict[str, Any]) -> None:
        proc = self._proc
        if proc is None or proc.stdin is None or proc.stdin.closed:
            raise McpError("server not running")
        # Catch the "process already exited" case BEFORE writing.
        # On Windows a closed pipe doesn't always raise BrokenPipeError
        # synchronously, so a write to a dead child can silently
        # succeed (bytes vanish into the OS buffer) and the requester
        # then waits for a response that's never coming.
        if proc.poll() is not None:
            raise McpError("server process has exited")
        line = json.dumps(payload, ensure_ascii=False) + "\n"
        with self._stdout_lock:
            try:
                proc.stdin.write(line)
                proc.stdin.flush()
            except (BrokenPipeError, OSError) as exc:
                raise McpError(f"failed to write to server stdin: {exc}")

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        rid = self._next_request_id()
        pending = _PendingRequest()
        with self._pending_lock:
            self._pending[rid] = pending
        try:
            self._send({
                "jsonrpc": JSONRPC_VERSION,
                "id": rid,
                "method": method,
                "params": params,
            })
            if not pending.event.wait(timeout=timeout):
                raise McpError(f"timeout waiting for response to {method!r} (id={rid})")
            response = pending.response or {}
            return response
        finally:
            with self._pending_lock:
                self._pending.pop(rid, None)

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Fire-and-forget JSON-RPC notification (no `id`)."""
        try:
            self._send({
                "jsonrpc": JSONRPC_VERSION,
                "method": method,
                "params": params,
            })
        except McpError as exc:
            logger.debug("notify(%r) failed: %s", method, exc)

    def _read_loop(self) -> None:
        proc = self._proc
        if proc is None or proc.stdout is None:
            return
        while True:
            try:
                line = proc.stdout.readline()
            except Exception:
                break
            if not line:
                # EOF — server exited. Wake any pending waiters.
                break
            line = line.strip()
            if not line:
                continue
            try:
                msg = json.loads(line)
            except json.JSONDecodeError:
                # Some servers emit log lines on stdout; ignore non-JSON.
                logger.debug("non-JSON line from server: %r", line[:200])
                continue
            if not isinstance(msg, dict):
                continue
            rid = msg.get("id")
            if isinstance(rid, int):
                with self._pending_lock:
                    pending = self._pending.get(rid)
                if pending is not None:
                    pending.response = msg
                    pending.event.set()
            # else: notification — currently no subscribers.

        # Wake all pending requesters so callers don't hang.
        with self._pending_lock:
            for p in self._pending.values():
                if p.response is None:
                    p.response = {
                        "jsonrpc": JSONRPC_VERSION,
                        "id": -1,
                        "error": {"code": -32000, "message": "server stdout closed"},
                    }
                p.event.set()
