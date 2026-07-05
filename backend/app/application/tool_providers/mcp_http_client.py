"""Minimal MCP (Model Context Protocol) streamable-HTTP client.

Sibling of the stdio `McpClient`: same public contract (start/stop/
is_alive/list_tools/list_resources/list_resource_templates/read_resource/
list_prompts/get_prompt/call_tool, plus server_info/server_capabilities/
protocol_version attributes), different transport. Where the stdio client
speaks JSON-RPC over a child process's pipes, this one speaks JSON-RPC over
HTTP POST and parses either a plain JSON body or an SSE stream of events
(the two response shapes the "Streamable HTTP" MCP transport allows).

This is the **remote** transport. It is disabled by default at the runtime
layer (ELIRA_REMOTE_MCP) — this module never decides policy, it just
implements the transport with the security properties D1 requires:

  * SSRF guard — the target host must resolve to a public address. Private,
    loopback, link-local, and cloud-metadata ranges are refused. The guard
    re-runs on every redirect hop (defends DNS-rebind / redirect-to-private).
  * HTTPS by default — a plain-http URL is refused unless the server spec
    opted in with allow_insecure_http=True.
  * Secrets isolation — auth headers live in a separate `secret_headers`
    field that is merged into the wire request but never logged and never
    placed in audit payloads.
  * Bounded everything — connect/read timeouts and a small retry budget on
    transient transport failures; sanitized + length-capped resource/prompt
    payloads (shared with the stdio client via mcp_sanitize).
"""
from __future__ import annotations

import ipaddress
import json
import logging
import os
import re
import socket
import threading
from typing import Any, Optional
from urllib.parse import urlsplit, urljoin

import httpx

# Reuse the stdio client's McpError as the base so a single `except McpError`
# catches failures from BOTH transports. mcp_provider._refresh_schemas only
# catches that one class — without this inheritance an http-client error would
# leak past it and break schema building.
from app.application.tool_providers.mcp_client import McpError as _StdioMcpError
from app.application.tool_providers.mcp_sanitize import (
    DEFAULT_CONTEXT_RESULT_LIMIT,
    sanitize_prompt_messages,
    sanitize_resource_contents,
)


logger = logging.getLogger(__name__)


_ENV_REF = re.compile(r"\$\{([A-Za-z_][A-Za-z0-9_]*)\}")


def _resolve_header_value(value: Any) -> tuple[Any, list[str]]:
    """Expand ``${VAR}`` references in a header value from the process env.

    Lets a config keep secrets OUT of the file — an HTTP MCP server's
    ``secret_headers`` can be ``{"Authorization": "Bearer ${HF_TOKEN}"}`` with the
    real token living in the backend's environment (.env.local).

    Returns ``(expanded, missing)`` where ``missing`` lists every referenced var
    that is unset or empty. When ``missing`` is non-empty the caller DROPS the
    header rather than forward a half-expanded value: sending ``"Bearer "`` (no
    token) is worse than sending nothing — httpx rejects it as an *Illegal
    header value* (a cryptic transport crash) whereas omitting the header yields
    a clean auth error from the server. A literal ``${VAR}`` is never forwarded.
    """
    if not isinstance(value, str):
        return value, []
    missing: list[str] = []

    def _sub(m: "re.Match[str]") -> str:
        name = m.group(1)
        resolved = os.environ.get(name, "")
        if not resolved:
            missing.append(name)
        return resolved

    return _ENV_REF.sub(_sub, value), missing


JSONRPC_VERSION = "2.0"
MCP_PROTOCOL_VERSION = "2024-11-05"
SUPPORTED_PROTOCOL_VERSIONS = (MCP_PROTOCOL_VERSION, "2024-10-07")

# Transport timeouts (seconds). The initialize handshake gets a longer read
# budget because a cold remote server may be slow on the first request.
DEFAULT_REQUEST_TIMEOUT = 30.0
INITIALIZE_TIMEOUT = 120.0
DEFAULT_CONNECT_TIMEOUT = 10.0

# Retry budget for transient transport errors (connect/read timeouts,
# connection resets). JSON-RPC errors and HTTP 4xx are NOT retried — they
# are deterministic server answers, not transient faults.
MAX_TRANSPORT_RETRIES = 2

# Hard cap on a single HTTP response body we'll buffer (defense against a
# server streaming an unbounded SSE/JSON blob). Sanitizers cap context
# separately; this caps raw bytes before parse.
MAX_RESPONSE_BYTES = 8 * 1024 * 1024

# Bound redirect chasing so a redirect loop can't spin forever.
MAX_REDIRECTS = 5


class McpError(_StdioMcpError):
    """Raised on protocol- or transport-level failures. The MCP provider
    wraps these into `{"text": "ERROR: ..."}` so they never bubble out of
    dispatch — identical contract to the stdio client's McpError. Subclasses
    the stdio McpError so a single `except McpError` (in mcp_provider /
    mcp_runtime) catches both transports' errors."""


class McpSecurityError(McpError):
    """A request was refused by the SSRF / scheme guard before any bytes
    left the process. Subclass of McpError so existing provider/runtime
    handling treats it the same, but distinguishable in tests/logs."""


# ── SSRF guard ───────────────────────────────────────────────────


def _is_blocked_ip(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    """True if `ip` is in a range we must never let an MCP server reach.

    Covers loopback, private (RFC1918 / ULA), link-local (incl. the
    169.254.169.254 cloud-metadata endpoint), unspecified, multicast, and
    reserved. IPv4-mapped IPv6 is unwrapped first so `::ffff:127.0.0.1`
    can't sneak a loopback target past the v4 checks.
    """
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped is not None:
        ip = ip.ipv4_mapped
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_unspecified
        or ip.is_multicast
        or ip.is_reserved
    )


def _guard_url(url: str, *, allow_insecure_http: bool, allow_private_address: bool = False) -> None:
    """Refuse a URL whose scheme or resolved address is unsafe.

    Raises McpSecurityError on: a non-http(s) scheme; plain http without
    explicit opt-in; a missing host; a host that fails to resolve; or ANY
    resolved address landing in a blocked range. We check every address the
    host resolves to (not just the first) so a multi-A-record host can't
    smuggle one private answer past the guard.
    """
    parts = urlsplit(url)
    scheme = (parts.scheme or "").lower()
    if scheme not in ("http", "https"):
        raise McpSecurityError(f"unsupported URL scheme {scheme!r} (only http/https)")
    if scheme == "http" and not allow_insecure_http:
        raise McpSecurityError(
            "plain http is refused; use https or set allow_insecure_http on the server"
        )
    host = parts.hostname
    if not host:
        raise McpSecurityError(f"URL has no host: {url!r}")

    # A literal IP is checked directly; a name is resolved and every answer
    # checked. getaddrinfo covers both A and AAAA.
    try:
        literal = ipaddress.ip_address(host)
    except ValueError:
        literal = None
    if literal is not None:
        if _is_blocked_ip(literal) and not allow_private_address:
            raise McpSecurityError(f"blocked address {host} (private/loopback/metadata)")
        return

    try:
        infos = socket.getaddrinfo(host, parts.port or (443 if scheme == "https" else 80),
                                   proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise McpSecurityError(f"cannot resolve host {host!r}: {exc}")
    if not infos:
        raise McpSecurityError(f"host {host!r} resolved to no addresses")
    for info in infos:
        sockaddr = info[4]
        addr = sockaddr[0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if _is_blocked_ip(ip) and not allow_private_address:
            raise McpSecurityError(
                f"host {host!r} resolves to blocked address {addr} (private/loopback/metadata)"
            )


class McpHttpClient:
    """One client = one remote MCP server reached over HTTP.

    Construction is cheap; `.start()` runs the initialize handshake (and the
    first SSRF guard pass). The instance is thread-safe for the agent's
    usage pattern: an httpx.Client plus a monotonic id counter under a lock.
    """

    def __init__(
        self,
        url: str,
        *,
        headers: Optional[dict[str, str]] = None,
        secret_headers: Optional[dict[str, str]] = None,
        allow_insecure_http: bool = False,
        allow_private_address: bool = False,
    ) -> None:
        self._url = url.strip()
        # Non-secret headers may be logged; secret headers must not be.
        # ${ENV_VAR} refs in values expand from the process env so the config
        # file can reference a token (.env.local) instead of storing it. A
        # header whose ref is unset is dropped (see _resolve_header_value).
        self._headers = self._resolve_header_map(headers, kind="header")
        self._secret_headers = self._resolve_header_map(secret_headers, kind="secret header")
        self._allow_insecure_http = bool(allow_insecure_http)
        self._allow_private_address = bool(allow_private_address)
        self._client: Optional[httpx.Client] = None
        # MCP streamable HTTP carries a session id the server hands back on
        # initialize; we echo it on every later request.
        self._session_id: Optional[str] = None
        self._next_id = 1
        self._id_lock = threading.Lock()
        self._stopped = False
        self._started = False
        # Cached after initialize so callers can introspect — same attrs as
        # the stdio client.
        self.server_info: dict[str, Any] = {}
        self.server_capabilities: dict[str, Any] = {}
        self.protocol_version: str = MCP_PROTOCOL_VERSION

    @staticmethod
    def _resolve_header_map(
        raw: Optional[dict[str, str]], *, kind: str
    ) -> dict[str, str]:
        """Expand ${VAR} refs in a header map, dropping any header whose refs
        are unset (logging which vars are missing). ``kind`` is only used for
        the log line; secret values themselves are never logged."""
        resolved: dict[str, str] = {}
        for key, value in (raw or {}).items():
            expanded, missing = _resolve_header_value(value)
            if missing:
                logger.warning(
                    "MCP HTTP %s %r dropped: unset environment variable(s): %s "
                    "(set them in the backend environment, e.g. .env.local)",
                    kind, key, ", ".join(sorted(set(missing))),
                )
                continue
            resolved[key] = expanded
        return resolved

    # ── Lifecycle ───────────────────────────────────────────────

    def start(self) -> None:
        """Open the HTTP client and run the MCP initialize handshake.
        Raises McpError (or McpSecurityError) on any failure. Idempotent."""
        if self._started:
            return
        # Guard once up front so a bad URL fails fast before we build state.
        _guard_url(self._url, allow_insecure_http=self._allow_insecure_http, allow_private_address=self._allow_private_address)

        self._client = httpx.Client(
            timeout=httpx.Timeout(DEFAULT_REQUEST_TIMEOUT, connect=DEFAULT_CONNECT_TIMEOUT),
            # We chase redirects manually so we can re-run the SSRF guard on
            # every hop — httpx's own follow_redirects would skip that.
            follow_redirects=False,
        )
        self._started = True

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
            self.stop()
            raise

        if "error" in init_response:
            err = init_response["error"] or {}
            msg = err.get("message", "?") if isinstance(err, dict) else "?"
            self.stop()
            raise McpError(f"server rejected initialize: {msg}")

        result = init_response.get("result", {}) or {}
        negotiated = str(result.get("protocolVersion") or "")
        if negotiated not in SUPPORTED_PROTOCOL_VERSIONS:
            self.stop()
            raise McpError(
                "unsupported MCP protocol version "
                f"{negotiated!r}; supported={list(SUPPORTED_PROTOCOL_VERSIONS)!r}"
            )
        self.protocol_version = negotiated
        self.server_info = result.get("serverInfo", {}) or {}
        self.server_capabilities = result.get("capabilities", {}) or {}

        # Per spec, the client sends `notifications/initialized` after a
        # successful handshake. No response expected; failure is non-fatal.
        self._notify("notifications/initialized", {})

    def stop(self) -> None:
        """Close the HTTP client. Idempotent and safe to call from any
        thread."""
        if self._stopped:
            return
        self._stopped = True
        client = self._client
        self._client = None
        if client is not None:
            try:
                client.close()
            except Exception:
                pass

    def is_alive(self) -> bool:
        return self._started and not self._stopped and self._client is not None

    # ── Public API (mirrors McpClient) ──────────────────────────

    def list_tools(self) -> list[dict[str, Any]]:
        response = self._request("tools/list", {})
        result = response.get("result") or {}
        tools = result.get("tools") or []
        return [t for t in tools if isinstance(t, dict)]

    def list_resources(self, *, cursor: str | None = None) -> dict[str, Any]:
        self._require_capability("resources")
        params = {"cursor": cursor} if cursor else {}
        result = self._request_result("resources/list", params)
        resources = [r for r in (result.get("resources") or []) if isinstance(r, dict)]
        out: dict[str, Any] = {"resources": resources}
        if result.get("nextCursor"):
            out["nextCursor"] = result.get("nextCursor")
        self._audit("mcp.resources.list", {"count": len(resources)})
        return out

    def list_resource_templates(self, *, cursor: str | None = None) -> dict[str, Any]:
        self._require_capability("resources")
        params = {"cursor": cursor} if cursor else {}
        result = self._request_result("resources/templates/list", params)
        templates = [r for r in (result.get("resourceTemplates") or []) if isinstance(r, dict)]
        out: dict[str, Any] = {"resourceTemplates": templates}
        if result.get("nextCursor"):
            out["nextCursor"] = result.get("nextCursor")
        self._audit("mcp.resources.templates.list", {"count": len(templates)})
        return out

    def read_resource(self, uri: str, *, max_chars: int = DEFAULT_CONTEXT_RESULT_LIMIT) -> dict[str, Any]:
        self._require_capability("resources")
        clean_uri = str(uri or "").strip()
        if not clean_uri:
            raise McpError("resource uri is required")
        result = self._request_result("resources/read", {"uri": clean_uri})
        contents, truncated = sanitize_resource_contents(
            result.get("contents") or [],
            max_chars=max_chars,
            provenance=f"mcp-resource:{clean_uri}",
        )
        self._audit("mcp.resources.read", {"uri": clean_uri, "count": len(contents), "truncated": truncated})
        return {"contents": contents, "truncated": truncated}

    def list_prompts(self, *, cursor: str | None = None) -> dict[str, Any]:
        self._require_capability("prompts")
        params = {"cursor": cursor} if cursor else {}
        result = self._request_result("prompts/list", params)
        prompts = [p for p in (result.get("prompts") or []) if isinstance(p, dict)]
        out: dict[str, Any] = {"prompts": prompts}
        if result.get("nextCursor"):
            out["nextCursor"] = result.get("nextCursor")
        self._audit("mcp.prompts.list", {"count": len(prompts)})
        return out

    def get_prompt(
        self,
        name: str,
        arguments: dict[str, Any] | None = None,
        *,
        max_chars: int = DEFAULT_CONTEXT_RESULT_LIMIT,
    ) -> dict[str, Any]:
        self._require_capability("prompts")
        clean_name = str(name or "").strip()
        if not clean_name:
            raise McpError("prompt name is required")
        params: dict[str, Any] = {"name": clean_name}
        if arguments:
            params["arguments"] = arguments
        result = self._request_result("prompts/get", params)
        messages, truncated = sanitize_prompt_messages(
            result.get("messages") or [],
            max_chars=max_chars,
            provenance=f"mcp-prompt:{clean_name}",
        )
        out: dict[str, Any] = {"messages": messages, "truncated": truncated}
        if isinstance(result.get("description"), str):
            out["description"] = result.get("description")
        self._audit("mcp.prompts.get", {"name": clean_name, "count": len(messages), "truncated": truncated})
        return out

    def call_tool(self, tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
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

    def _wire_headers(self) -> dict[str, str]:
        """Headers actually sent on the wire — public + secret + protocol.
        Secret headers are merged here and ONLY here; never logged/audited."""
        headers = {
            "Content-Type": "application/json",
            # MCP streamable HTTP: advertise we accept either a JSON answer
            # or an SSE stream.
            "Accept": "application/json, text/event-stream",
            **self._headers,
            **self._secret_headers,
        }
        if self._session_id:
            headers["Mcp-Session-Id"] = self._session_id
        return headers

    def _request(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        rid = self._next_request_id()
        payload = {
            "jsonrpc": JSONRPC_VERSION,
            "id": rid,
            "method": method,
            "params": params,
        }
        response = self._post_jsonrpc(payload, timeout=timeout, expect_response=True)
        return response or {}

    def _request_result(
        self,
        method: str,
        params: dict[str, Any],
        *,
        timeout: float = DEFAULT_REQUEST_TIMEOUT,
    ) -> dict[str, Any]:
        response = self._request(method, params, timeout=timeout)
        if "error" in response:
            err = response["error"] or {}
            message = err.get("message", "?") if isinstance(err, dict) else "?"
            code = err.get("code", "?") if isinstance(err, dict) else "?"
            raise McpError(f"server rejected {method!r}: {message} (code {code})")
        result = response.get("result") or {}
        return result if isinstance(result, dict) else {}

    def _require_capability(self, capability: str) -> None:
        if capability not in self.server_capabilities:
            raise McpError(f"server does not support {capability!r} capability")

    def _notify(self, method: str, params: dict[str, Any]) -> None:
        """Fire-and-forget JSON-RPC notification (no `id`, no response)."""
        payload = {"jsonrpc": JSONRPC_VERSION, "method": method, "params": params}
        try:
            self._post_jsonrpc(payload, timeout=DEFAULT_REQUEST_TIMEOUT, expect_response=False)
        except McpError as exc:
            logger.debug("notify(%r) failed: %s", method, exc)

    def _post_jsonrpc(
        self,
        payload: dict[str, Any],
        *,
        timeout: float,
        expect_response: bool,
    ) -> Optional[dict[str, Any]]:
        """POST one JSON-RPC message, following redirects manually (each hop
        re-guarded) and retrying transient transport faults. Returns the
        parsed JSON-RPC response dict, or None for a notification."""
        client = self._client
        if client is None:
            raise McpError("client not started")

        last_exc: Optional[Exception] = None
        for attempt in range(MAX_TRANSPORT_RETRIES + 1):
            try:
                return self._post_once(client, payload, timeout=timeout, expect_response=expect_response)
            except McpSecurityError:
                raise  # never retry a guard failure
            except (httpx.TimeoutException, httpx.TransportError) as exc:
                last_exc = exc
                logger.debug("mcp http transport error (attempt %d): %s", attempt + 1, exc)
                continue
        raise McpError(f"transport failed after {MAX_TRANSPORT_RETRIES + 1} attempts: {last_exc}")

    def _post_once(
        self,
        client: httpx.Client,
        payload: dict[str, Any],
        *,
        timeout: float,
        expect_response: bool,
    ) -> Optional[dict[str, Any]]:
        url = self._url
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        for _hop in range(MAX_REDIRECTS + 1):
            _guard_url(url, allow_insecure_http=self._allow_insecure_http, allow_private_address=self._allow_private_address)
            request = client.build_request(
                "POST", url, content=body, headers=self._wire_headers(),
                timeout=httpx.Timeout(timeout, connect=DEFAULT_CONNECT_TIMEOUT),
            )
            resp = client.send(request, stream=True)
            try:
                if resp.is_redirect:
                    location = resp.headers.get("location")
                    if not location:
                        raise McpError(f"redirect with no Location from {url!r}")
                    # Resolve relative redirects against the current URL,
                    # then loop to re-guard the new target.
                    url = urljoin(url, location)
                    continue
                return self._handle_response(resp, expect_response=expect_response)
            finally:
                resp.close()
        raise McpError(f"too many redirects (>{MAX_REDIRECTS}) starting at {self._url!r}")

    def _handle_response(
        self,
        resp: httpx.Response,
        *,
        expect_response: bool,
    ) -> Optional[dict[str, Any]]:
        # Capture a session id the server may assign on initialize.
        sid = resp.headers.get("mcp-session-id")
        if sid:
            self._session_id = sid

        if resp.status_code == 202:
            # Accepted with no body — valid for a notification.
            return None
        if resp.status_code >= 400:
            raise McpError(f"http {resp.status_code} from server")

        content_type = (resp.headers.get("content-type") or "").split(";")[0].strip().lower()
        raw = self._read_bounded(resp)

        if content_type == "text/event-stream":
            message = self._parse_sse(raw)
        else:
            try:
                message = json.loads(raw) if raw.strip() else None
            except json.JSONDecodeError as exc:
                raise McpError(f"invalid JSON from server: {exc}")

        if not expect_response:
            return None
        if not isinstance(message, dict):
            raise McpError("server returned no JSON-RPC response object")
        return message

    @staticmethod
    def _read_bounded(resp: httpx.Response) -> str:
        """Read the response body up to MAX_RESPONSE_BYTES, then refuse."""
        chunks: list[bytes] = []
        total = 0
        for chunk in resp.iter_bytes():
            total += len(chunk)
            if total > MAX_RESPONSE_BYTES:
                raise McpError(f"response exceeded {MAX_RESPONSE_BYTES} bytes")
            chunks.append(chunk)
        return b"".join(chunks).decode("utf-8", errors="replace")

    @staticmethod
    def _parse_sse(raw: str) -> Optional[dict[str, Any]]:
        """Pull the JSON-RPC response out of an SSE stream.

        The server may emit progress/log events before the answer; we want
        the LAST `data:` payload that parses as a JSON-RPC response carrying
        a matching shape (has `result` or `error`). Multi-line `data:`
        fields are concatenated per the SSE spec.
        """
        result: Optional[dict[str, Any]] = None
        data_lines: list[str] = []

        def flush() -> None:
            nonlocal result
            if not data_lines:
                return
            payload = "\n".join(data_lines)
            try:
                obj = json.loads(payload)
            except json.JSONDecodeError:
                return
            if isinstance(obj, dict) and ("result" in obj or "error" in obj):
                result = obj

        for line in raw.splitlines():
            if line.startswith("data:"):
                data_lines.append(line[5:].lstrip())
            elif line == "":
                flush()
                data_lines = []
        flush()  # trailing event with no blank-line terminator
        return result

    def _audit(self, event_type: str, payload: dict[str, Any]) -> None:
        """Emit an audit event. NOTE: secret_headers are deliberately never
        included here — only server_info + the caller's non-secret payload."""
        try:
            from app.application.event_bus import runtime as event_bus
            event_bus.emit_event(
                event_type=event_type,
                source_agent_id="mcp",
                payload={
                    "protocol_version": self.protocol_version,
                    "server": self.server_info,
                    "transport": "http",
                    **payload,
                },
            )
        except Exception:
            pass
