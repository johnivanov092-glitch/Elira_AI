"""Tests for the MCP streamable-HTTP client (the D1 remote transport).

Two halves:

  * **Transport behaviour** — handshake, tools/list, tools/call, resources,
    prompts, the SSE response shape, redirects, bounded retry. These run
    against an in-process httpx.MockTransport (no sockets), with the SSRF
    guard patched to a no-op so a fake "https://fake-mcp/..." target is
    reachable. The guard itself is tested separately below.

  * **Security guard** — the SSRF / scheme rules exercised through the REAL
    `_guard_url` + `start()`: blocked literal IPs, metadata endpoint,
    localhost, IPv4-mapped IPv6, bad scheme, plain-http opt-in, and a
    redirect that lands on a private address. Plus the runtime flag-off
    refusal (`ELIRA_REMOTE_MCP` unset → http server won't start).
"""
from __future__ import annotations

import importlib
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import httpx


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers import mcp_http_client as hc  # noqa: E402
from app.application.tool_providers.mcp_http_client import (  # noqa: E402
    McpError,
    McpHttpClient,
    McpSecurityError,
)


FAKE_URL = "https://fake-mcp.test/mcp"


# ── Fake server ──────────────────────────────────────────────────


def _jsonrpc_result(req: dict, result: dict) -> dict:
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": result}


def _jsonrpc_error(req: dict, code: int, message: str) -> dict:
    return {"jsonrpc": "2.0", "id": req.get("id"), "error": {"code": code, "message": message}}


class FakeHttpServer:
    """An httpx.MockTransport handler that speaks just enough MCP.

    Mirrors tests/_mcp_fake_server.py (stdio) but over HTTP. Toggle flags on
    the instance to drive error/edge paths; `as_json` / `as_sse` choose how
    the *next* (and every) response is framed.
    """

    def __init__(
        self,
        *,
        fail_init: bool = False,
        protocol_version: str = "2024-11-05",
        no_resources: bool = False,
        no_prompts: bool = False,
        sse: bool = False,
        session_id: str | None = "sess-123",
    ) -> None:
        self.fail_init = fail_init
        self.protocol_version = protocol_version
        self.no_resources = no_resources
        self.no_prompts = no_prompts
        self.sse = sse
        self.session_id = session_id
        self.seen_headers: list[dict[str, str]] = []
        self.seen_sessions: list[str | None] = []

    # -- framing --

    def _frame(self, message: dict | None, *, status: int = 200) -> httpx.Response:
        headers = {}
        if self.session_id:
            headers["mcp-session-id"] = self.session_id
        if message is None:
            return httpx.Response(status, headers=headers)
        if self.sse:
            body = f"event: message\ndata: {json.dumps(message)}\n\n"
            headers["content-type"] = "text/event-stream"
            return httpx.Response(status, headers=headers, content=body.encode("utf-8"))
        headers["content-type"] = "application/json"
        return httpx.Response(status, headers=headers, content=json.dumps(message).encode("utf-8"))

    # -- dispatch --

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.seen_headers.append({k.lower(): v for k, v in request.headers.items()})
        self.seen_sessions.append(request.headers.get("mcp-session-id"))
        payload = json.loads(request.content.decode("utf-8")) if request.content else {}
        method = payload.get("method")

        if method == "notifications/initialized":
            return self._frame(None, status=202)
        if method == "initialize":
            if self.fail_init:
                return self._frame(_jsonrpc_error(payload, -32000, "init boom"))
            caps: dict = {"tools": {}}
            if not self.no_resources:
                caps["resources"] = {}
            if not self.no_prompts:
                caps["prompts"] = {}
            return self._frame(_jsonrpc_result(payload, {
                "protocolVersion": self.protocol_version,
                "serverInfo": {"name": "fake-http-mcp", "version": "0.1"},
                "capabilities": caps,
            }))
        if method == "tools/list":
            return self._frame(_jsonrpc_result(payload, {"tools": [
                {"name": "echo", "description": "echo", "inputSchema": {"type": "object"}},
                {"name": "search", "description": "search", "inputSchema": {"type": "object"}},
            ]}))
        if method == "tools/call":
            name = (payload.get("params") or {}).get("name")
            if name == "echo":
                text = (payload.get("params") or {}).get("arguments", {}).get("text", "")
                return self._frame(_jsonrpc_result(payload, {
                    "content": [{"type": "text", "text": f"echo: {text}"}], "isError": False,
                }))
            return self._frame(_jsonrpc_error(payload, -32602, f"unknown tool {name!r}"))
        if method == "resources/list":
            return self._frame(_jsonrpc_result(payload, {"resources": [
                {"uri": "file:///fake/readme.md", "name": "readme"},
            ]}))
        if method == "resources/read":
            return self._frame(_jsonrpc_result(payload, {"contents": [
                {"uri": "file:///fake/readme.md", "text": "Fake http resource text."},
            ]}))
        if method == "prompts/list":
            return self._frame(_jsonrpc_result(payload, {"prompts": [{"name": "review"}]}))
        if method == "prompts/get":
            return self._frame(_jsonrpc_result(payload, {"messages": [
                {"role": "user", "content": {"type": "text", "text": "Please review: x"}},
            ]}))
        return self._frame(_jsonrpc_error(payload, -32601, f"method not found: {method}"))


def _mock_client_factory(handler) -> type:
    """Return an httpx.Client subclass that injects our MockTransport.

    McpHttpClient.start() builds its own httpx.Client(...); we patch the
    symbol it references so the constructed client routes through the fake.
    The transport kwarg supersedes follow_redirects=False — fine, the fake
    never redirects on the happy path; redirect tests use the real Client.
    """
    transport = httpx.MockTransport(handler)

    class _PatchedClient(httpx.Client):
        def __init__(self, *args, **kwargs):
            kwargs["transport"] = transport
            kwargs.pop("follow_redirects", None)
            super().__init__(*args, follow_redirects=False, **kwargs)

    return _PatchedClient


class _TransportTestBase(unittest.TestCase):
    """Patches the SSRF guard off and httpx.Client onto a fake, so the rest
    of the transport can be exercised without sockets."""

    def _start(self, server: FakeHttpServer, **client_kwargs) -> McpHttpClient:
        patched = _mock_client_factory(server)
        self._p_client = patch.object(hc.httpx, "Client", patched)
        self._p_guard = patch.object(hc, "_guard_url", lambda *a, **k: None)
        self._p_client.start()
        self._p_guard.start()
        self.addCleanup(self._p_client.stop)
        self.addCleanup(self._p_guard.stop)
        client = McpHttpClient(url=FAKE_URL, **client_kwargs)
        self.addCleanup(client.stop)
        client.start()
        return client


# ── Transport behaviour ──────────────────────────────────────────


class HandshakeTest(_TransportTestBase):
    def test_initialize_caches_server_info_and_session(self) -> None:
        server = FakeHttpServer()
        client = self._start(server)
        self.assertTrue(client.is_alive())
        self.assertEqual(client.server_info.get("name"), "fake-http-mcp")
        self.assertEqual(client.protocol_version, "2024-11-05")
        self.assertIn("tools", client.server_capabilities)
        # The session id handed back on initialize is echoed on later calls.
        client.list_tools()
        self.assertIn("sess-123", server.seen_sessions)

    def test_initialize_error_raises_and_stops(self) -> None:
        server = FakeHttpServer(fail_init=True)
        with self.assertRaises(McpError):
            self._start(server)

    def test_unsupported_protocol_version_rejected(self) -> None:
        server = FakeHttpServer(protocol_version="1999-01-01")
        with self.assertRaises(McpError) as ctx:
            self._start(server)
        self.assertIn("unsupported MCP protocol version", str(ctx.exception))

    def test_supported_fallback_version_accepted(self) -> None:
        server = FakeHttpServer(protocol_version="2024-10-07")
        client = self._start(server)
        self.assertEqual(client.protocol_version, "2024-10-07")

    def test_secret_headers_sent_but_kept_separate(self) -> None:
        server = FakeHttpServer()
        client = self._start(
            server,
            headers={"X-Public": "pub"},
            secret_headers={"Authorization": "Bearer s3cret"},
        )
        client.list_tools()
        merged = server.seen_headers[-1]
        self.assertEqual(merged.get("authorization"), "Bearer s3cret")
        self.assertEqual(merged.get("x-public"), "pub")
        # The secret never enters the client's loggable header set.
        self.assertNotIn("authorization", {k.lower() for k in client._headers})


class SseTest(_TransportTestBase):
    def test_sse_framed_response_is_parsed(self) -> None:
        server = FakeHttpServer(sse=True)
        client = self._start(server)
        tools = client.list_tools()
        self.assertEqual({t["name"] for t in tools}, {"echo", "search"})

    def test_parse_sse_picks_last_jsonrpc_event(self) -> None:
        raw = (
            "event: log\ndata: {\"jsonrpc\":\"2.0\",\"method\":\"x\"}\n\n"
            "event: message\ndata: {\"jsonrpc\":\"2.0\",\"id\":1,\"result\":{\"ok\":true}}\n\n"
        )
        msg = McpHttpClient._parse_sse(raw)
        self.assertEqual(msg, {"jsonrpc": "2.0", "id": 1, "result": {"ok": True}})

    def test_parse_sse_multiline_data_concatenated(self) -> None:
        # Two data: lines in one event are joined with "\n" per the SSE spec,
        # so the payload only parses once both halves are present.
        raw = 'data: {"jsonrpc":"2.0",\ndata: "id":1,"result":{}}\n\n'
        msg = McpHttpClient._parse_sse(raw)
        self.assertEqual(msg, {"jsonrpc": "2.0", "id": 1, "result": {}})


class ToolsTest(_TransportTestBase):
    def test_list_tools(self) -> None:
        client = self._start(FakeHttpServer())
        names = [t["name"] for t in client.list_tools()]
        self.assertEqual(names, ["echo", "search"])

    def test_call_tool_returns_content(self) -> None:
        client = self._start(FakeHttpServer())
        result = client.call_tool("echo", {"text": "hi"})
        self.assertFalse(result.get("isError"))
        self.assertIn("hi", result["content"][0]["text"])

    def test_unknown_tool_raises(self) -> None:
        client = self._start(FakeHttpServer())
        with self.assertRaises(McpError):
            client.call_tool("does_not_exist", {})


class ResourcesPromptsTest(_TransportTestBase):
    def test_read_resource_wraps_untrusted(self) -> None:
        client = self._start(FakeHttpServer())
        result = client.read_resource("file:///fake/readme.md")
        text = result["contents"][0]["text"]
        self.assertIn("Fake http resource text.", text)
        self.assertIn("UNTRUSTED MCP RESOURCE", text)

    def test_get_prompt_wraps_untrusted(self) -> None:
        client = self._start(FakeHttpServer())
        result = client.get_prompt("review", {"code": "x"})
        text = result["messages"][0]["content"]["text"]
        self.assertIn("Please review: x", text)
        self.assertIn("UNTRUSTED MCP PROMPT", text)

    def test_resources_capability_required(self) -> None:
        client = self._start(FakeHttpServer(no_resources=True))
        with self.assertRaises(McpError) as ctx:
            client.list_resources()
        self.assertIn("resources", str(ctx.exception))

    def test_prompts_capability_required(self) -> None:
        client = self._start(FakeHttpServer(no_prompts=True))
        with self.assertRaises(McpError) as ctx:
            client.list_prompts()
        self.assertIn("prompts", str(ctx.exception))


class RetryTest(_TransportTestBase):
    """The handshake always succeeds; the FIRST tools/list raises a transient
    transport error and the retry budget (MAX_TRANSPORT_RETRIES) recovers."""

    def test_transient_transport_error_is_retried(self) -> None:
        calls = {"n": 0}
        base = FakeHttpServer()  # delegate non-flaky methods (initialize, ...)

        def flaky(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            if payload.get("method") == "tools/list":
                calls["n"] += 1
                if calls["n"] == 1:
                    raise httpx.ConnectError("boom", request=request)
            return base(request)

        client = self._start(flaky)  # one transport for the whole lifecycle
        tools = client.list_tools()
        self.assertEqual(calls["n"], 2)  # one failure + one success
        self.assertEqual(len(tools), 2)

    def test_exhausted_retries_raise_mcp_error(self) -> None:
        base = FakeHttpServer()

        def always_fail(request: httpx.Request) -> httpx.Response:
            payload = json.loads(request.content.decode("utf-8"))
            if payload.get("method") in ("initialize", "notifications/initialized"):
                return base(request)  # let the handshake through
            raise httpx.ConnectError("down", request=request)

        client = self._start(always_fail)
        with self.assertRaises(McpError) as ctx:
            client.list_tools()
        self.assertIn("transport failed", str(ctx.exception))


# ── SSRF / scheme guard (real guard, no mocking) ─────────────────


class GuardUrlTest(unittest.TestCase):
    """Exercises the real _guard_url — no transport involved."""

    BLOCKED = [
        "http://127.0.0.1/mcp",
        "https://127.0.0.1/mcp",
        "https://localhost/mcp",
        "http://10.0.0.1/mcp",
        "http://172.16.0.1/mcp",
        "http://192.168.1.1/mcp",
        "http://169.254.169.254/latest/meta-data",   # cloud metadata
        "https://[::1]/mcp",                          # IPv6 loopback
        "https://[::ffff:127.0.0.1]/mcp",             # IPv4-mapped loopback
        "http://0.0.0.0/mcp",
    ]

    def test_blocked_addresses_raise_security_error(self) -> None:
        for url in self.BLOCKED:
            with self.subTest(url=url):
                with self.assertRaises(McpSecurityError):
                    hc._guard_url(url, allow_insecure_http=True)

    def test_non_http_scheme_refused(self) -> None:
        for url in ("ftp://example.com/x", "file:///etc/passwd", "ws://example.com/x"):
            with self.subTest(url=url):
                with self.assertRaises(McpSecurityError):
                    hc._guard_url(url, allow_insecure_http=True)

    def test_plain_http_refused_without_optin(self) -> None:
        with self.assertRaises(McpSecurityError) as ctx:
            hc._guard_url("http://example.com/mcp", allow_insecure_http=False)
        self.assertIn("plain http", str(ctx.exception))

    def test_missing_host_refused(self) -> None:
        with self.assertRaises(McpSecurityError):
            hc._guard_url("https:///nohost", allow_insecure_http=False)

    def test_public_host_passes(self) -> None:
        # A literal public IP needs no DNS and is deterministic offline.
        hc._guard_url("https://93.184.216.34/mcp", allow_insecure_http=False)

    def test_start_against_blocked_literal_raises(self) -> None:
        client = McpHttpClient(url="https://127.0.0.1/mcp")
        with self.assertRaises(McpSecurityError):
            client.start()
        self.assertFalse(client.is_alive())


class RedirectGuardTest(unittest.TestCase):
    """A redirect that points at a private address must be refused on the
    hop, even though the initial URL was clean. Uses the real guard with a
    MockTransport that 302s to the metadata endpoint."""

    def test_redirect_to_private_is_blocked(self) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            return httpx.Response(302, headers={"location": "http://169.254.169.254/"})

        transport = httpx.MockTransport(handler)

        class _PatchedClient(httpx.Client):
            def __init__(self, *args, **kwargs):
                kwargs["transport"] = transport
                kwargs.pop("follow_redirects", None)
                super().__init__(*args, follow_redirects=False, **kwargs)

        # Allow the FIRST guard pass (clean public literal) but the redirect
        # target is private → McpSecurityError on the hop.
        with patch.object(hc.httpx, "Client", _PatchedClient):
            client = McpHttpClient(url="https://93.184.216.34/mcp", allow_insecure_http=True)
            with self.assertRaises(McpSecurityError):
                client.start()


# ── Runtime flag-off refusal ─────────────────────────────────────


class RemoteFlagGatingTest(unittest.TestCase):
    """With ELIRA_REMOTE_MCP unset, a configured http server must refuse to
    start (stdio is unaffected — covered by test_mcp_provider.py)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name
        self._prev_flag = os.environ.pop("ELIRA_REMOTE_MCP", None)
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.tool_providers import mcp_runtime
        importlib.reload(mcp_runtime)
        self.runtime = mcp_runtime

    def tearDown(self) -> None:
        try:
            self.runtime.stop_all_servers()
        except Exception:
            pass
        os.environ.pop("ELIRA_DATA_DIR", None)
        if self._prev_flag is not None:
            os.environ["ELIRA_REMOTE_MCP"] = self._prev_flag
        from app.core import data_files
        importlib.reload(data_files)
        importlib.reload(self.runtime)
        self._tmp.cleanup()

    def _http_spec(self) -> dict:
        return {
            "id": "remote",
            "transport": "http",
            "url": "https://example.com/mcp",
            "enabled": True,
        }

    def test_http_server_validates_and_persists(self) -> None:
        saved = self.runtime.save_servers([self._http_spec()])
        self.assertEqual(saved[0]["transport"], "http")
        self.assertEqual(saved[0]["url"], "https://example.com/mcp")

    def test_http_start_refused_when_flag_off(self) -> None:
        os.environ.pop("ELIRA_REMOTE_MCP", None)
        self.runtime.save_servers([self._http_spec()])
        result = self.runtime.start_server("remote")
        self.assertFalse(result["ok"])
        self.assertIn("remote MCP disabled", result["error"])
        # And the refusal is recorded as last_error for the UI.
        servers = {s["id"]: s for s in self.runtime.list_servers()}
        self.assertIn("remote MCP disabled", servers["remote"]["last_error"])

    def test_flag_truthy_values_enable(self) -> None:
        for val in ("1", "on", "true", "YES"):
            with self.subTest(val=val):
                os.environ["ELIRA_REMOTE_MCP"] = val
                self.assertTrue(self.runtime._remote_mcp_enabled())
        os.environ.pop("ELIRA_REMOTE_MCP", None)
        self.assertFalse(self.runtime._remote_mcp_enabled())


if __name__ == "__main__":
    unittest.main()
