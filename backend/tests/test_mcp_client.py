"""Tests for the MCP stdio client.

These spawn a real subprocess running tests/_mcp_fake_server.py
(a JSON-RPC fake that speaks just enough of the protocol). End-to-
end coverage of: handshake, tools/list, tools/call, error paths,
shutdown cleanup.
"""
from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers.mcp_client import (  # noqa: E402
    DEFAULT_REQUEST_TIMEOUT,
    INITIALIZE_TIMEOUT,
    McpClient,
    McpError,
)


FAKE_SERVER = Path(__file__).parent / "_mcp_fake_server.py"


def _client(*, fail_init: bool = False, hang_init: bool = False,
            tools_fail: bool = False, call_fail: bool = False,
            no_resources: bool = False, no_prompts: bool = False,
            big_resource: bool = False, protocol_version: str | None = None) -> McpClient:
    env = {}
    if fail_init:
        env["FAKE_MCP_FAIL_INIT"] = "1"
    if hang_init:
        env["FAKE_MCP_HANG_INIT"] = "1"
    if tools_fail:
        env["FAKE_MCP_TOOLS_FAIL"] = "1"
    if call_fail:
        env["FAKE_MCP_CALL_FAIL"] = "1"
    if no_resources:
        env["FAKE_MCP_NO_RESOURCES"] = "1"
    if no_prompts:
        env["FAKE_MCP_NO_PROMPTS"] = "1"
    if big_resource:
        env["FAKE_MCP_BIG_RESOURCE"] = "1"
    if protocol_version:
        env["FAKE_MCP_PROTOCOL_VERSION"] = protocol_version
    return McpClient(
        command=sys.executable,
        args=[str(FAKE_SERVER)],
        env=env,
    )


class HandshakeTest(unittest.TestCase):
    def test_initialize_succeeds_and_caches_server_info(self) -> None:
        client = _client()
        try:
            client.start()
            self.assertTrue(client.is_alive())
            self.assertEqual(client.server_info.get("name"), "fake-mcp")
            self.assertIn("tools", client.server_capabilities)
            self.assertIn("resources", client.server_capabilities)
            self.assertIn("prompts", client.server_capabilities)
        finally:
            client.stop()

    def test_initialize_error_raises_and_kills_process(self) -> None:
        client = _client(fail_init=True)
        with self.assertRaises(McpError):
            client.start()
        # Process should be torn down after the failed handshake.
        self.assertFalse(client.is_alive())

    def test_unknown_command_raises(self) -> None:
        client = McpClient(command="nonexistent_binary_12345xyz")
        with self.assertRaises(McpError):
            client.start()

    def test_stop_is_idempotent(self) -> None:
        client = _client()
        client.start()
        client.stop()
        client.stop()  # no exception
        self.assertFalse(client.is_alive())

    def test_initialize_hang_eventually_times_out(self) -> None:
        # Patch the timeout down so the test doesn't actually wait 120s.
        client = _client(hang_init=True)
        with patch(
            "app.application.tool_providers.mcp_client.INITIALIZE_TIMEOUT",
            0.5,
        ):
            with self.assertRaises(McpError) as ctx:
                client.start()
            self.assertIn("timeout", str(ctx.exception).lower())
        client.stop()

    def test_initialize_accepts_supported_server_fallback_version(self) -> None:
        client = _client(protocol_version="2024-10-07")
        try:
            client.start()
            self.assertEqual(client.protocol_version, "2024-10-07")
        finally:
            client.stop()

    def test_initialize_rejects_unsupported_server_version(self) -> None:
        client = _client(protocol_version="1999-01-01")
        with self.assertRaises(McpError) as ctx:
            client.start()
        self.assertIn("unsupported MCP protocol version", str(ctx.exception))
        self.assertFalse(client.is_alive())


class ToolsListTest(unittest.TestCase):
    def test_list_tools_returns_fake_tools(self) -> None:
        client = _client()
        try:
            client.start()
            tools = client.list_tools()
            names = [t["name"] for t in tools]
            self.assertIn("echo", names)
            self.assertIn("search", names)
        finally:
            client.stop()

    def test_list_tools_server_error_yields_empty(self) -> None:
        """tools/list with a JSON-RPC error response → empty list,
        not an exception. The provider treats no-tools as
        "advertise nothing" rather than failing the whole agent
        loop on a transient list error."""
        client = _client(tools_fail=True)
        try:
            client.start()
            tools = client.list_tools()
            self.assertEqual(tools, [])
        finally:
            client.stop()


class ToolCallTest(unittest.TestCase):
    def test_call_tool_returns_content(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.call_tool("echo", {"text": "hello"})
            self.assertFalse(result.get("isError"))
            chunks = result.get("content", [])
            self.assertGreaterEqual(len(chunks), 1)
            self.assertIn("hello", chunks[0]["text"])
        finally:
            client.stop()

    def test_call_tool_multiple_content_chunks(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.call_tool("search", {"q": "needle"})
            chunks = result.get("content", [])
            self.assertEqual(len(chunks), 2)
            for chunk in chunks:
                self.assertIn("needle", chunk["text"])
        finally:
            client.stop()

    def test_unknown_tool_raises(self) -> None:
        client = _client()
        try:
            client.start()
            with self.assertRaises(McpError):
                client.call_tool("does_not_exist", {})
        finally:
            client.stop()

    def test_iserror_true_still_returned(self) -> None:
        # When the server signals isError in the RESULT (not JSON-RPC
        # error), call_tool returns the dict for the provider to flatten.
        client = _client(call_fail=True)
        try:
            client.start()
            result = client.call_tool("echo", {"text": "x"})
            self.assertTrue(result.get("isError"))
        finally:
            client.stop()


class ResourcesTest(unittest.TestCase):
    def test_list_resources_returns_fake_resource(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.list_resources()
            self.assertEqual(result["resources"][0]["uri"], "file:///fake/readme.md")
        finally:
            client.stop()

    def test_read_resource_returns_untrusted_text(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.read_resource("file:///fake/readme.md")
            self.assertFalse(result["truncated"])
            text = result["contents"][0]["text"]
            self.assertIn("Fake resource text.", text)
            self.assertIn("UNTRUSTED MCP RESOURCE", text)
            self.assertIn("file:///fake/readme.md", text)
        finally:
            client.stop()

    def test_read_large_resource_is_truncated(self) -> None:
        client = _client(big_resource=True)
        try:
            client.start()
            result = client.read_resource("file:///fake/readme.md", max_chars=64)
            self.assertTrue(result["truncated"])
            self.assertTrue(result["contents"][0]["truncated"])
            self.assertIn("[truncated by limit]", result["contents"][0]["text"])
        finally:
            client.stop()

    def test_list_resource_templates(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.list_resource_templates()
            self.assertEqual(result["resourceTemplates"][0]["uriTemplate"], "file:///fake/{name}.md")
        finally:
            client.stop()

    def test_resources_capability_required(self) -> None:
        client = _client(no_resources=True)
        try:
            client.start()
            with self.assertRaises(McpError) as ctx:
                client.list_resources()
            self.assertIn("resources", str(ctx.exception))
        finally:
            client.stop()


class PromptsTest(unittest.TestCase):
    def test_list_prompts_returns_fake_prompt(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.list_prompts()
            self.assertEqual(result["prompts"][0]["name"], "review")
        finally:
            client.stop()

    def test_get_prompt_returns_untrusted_messages(self) -> None:
        client = _client()
        try:
            client.start()
            result = client.get_prompt("review", {"code": "x = 1"})
            self.assertFalse(result["truncated"])
            message = result["messages"][0]
            self.assertEqual(message["role"], "user")
            text = message["content"]["text"]
            self.assertIn("Please review: x = 1", text)
            self.assertIn("UNTRUSTED MCP PROMPT", text)
        finally:
            client.stop()

    def test_prompts_capability_required(self) -> None:
        client = _client(no_prompts=True)
        try:
            client.start()
            with self.assertRaises(McpError) as ctx:
                client.list_prompts()
            self.assertIn("prompts", str(ctx.exception))
        finally:
            client.stop()

    def test_get_prompt_audits_best_effort(self) -> None:
        client = _client()
        try:
            client.start()
            with patch("app.application.event_bus.runtime.emit_event") as emit:
                client.get_prompt("review", {"code": "x"})
            self.assertTrue(emit.called)
            self.assertEqual(emit.call_args.kwargs["event_type"], "mcp.prompts.get")
        finally:
            client.stop()


class ServerCrashTest(unittest.TestCase):
    def test_pending_request_after_crash_unblocks_with_error(self) -> None:
        """If the server dies, follow-up requests must NOT hang —
        they should get an McpError instead of blocking forever."""
        client = _client()
        try:
            client.start()
            # Kill the server and wait for the OS to actually reap
            # it, so `proc.poll()` returns a non-None code by the
            # time we make the next request.
            assert client._proc is not None
            client._proc.kill()
            client._proc.wait(timeout=5.0)
            # Next request must fail fast — _send sees proc.poll() != None.
            with self.assertRaises(McpError):
                with patch(
                    "app.application.tool_providers.mcp_client.DEFAULT_REQUEST_TIMEOUT",
                    2.0,
                ):
                    client.list_tools()
        finally:
            client.stop()


if __name__ == "__main__":
    unittest.main()
