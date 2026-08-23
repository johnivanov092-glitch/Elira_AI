"""Tests for the MCP runtime + provider.

`mcp_runtime` owns config persistence and per-server lifecycle;
`McpToolProvider` adapts a running server's tools into the
ToolProvider Protocol. Both are exercised end-to-end against the
fake MCP server from tests/_mcp_fake_server.py.
"""
from __future__ import annotations

import base64
import importlib
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


FAKE_SERVER = Path(__file__).parent / "_mcp_fake_server.py"


class McpProviderTestBase(unittest.TestCase):
    """Each test gets a fresh ELIRA_DATA_DIR + reloaded runtime so
    config + live-clients state is isolated."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.tool_providers import mcp_runtime, mcp_provider
        importlib.reload(mcp_runtime)
        importlib.reload(mcp_provider)
        self.runtime = mcp_runtime
        self.provider_mod = mcp_provider

    def tearDown(self) -> None:
        try:
            self.runtime.stop_all_servers()
        except Exception:
            pass
        os.environ.pop("ELIRA_DATA_DIR", None)
        from app.core import data_files
        importlib.reload(data_files)
        for module_name in (
            "app.application.tool_registry.runtime",
            "app.application.tool_providers.mcp_runtime",
            "app.application.tool_providers.mcp_provider",
        ):
            module = sys.modules.get(module_name)
            if module is not None:
                importlib.reload(module)
        self._tmp.cleanup()

    def _fake_spec(self, server_id: str = "fake", **overrides) -> dict:
        spec = {
            "id": server_id,
            "command": sys.executable,
            "args": [str(FAKE_SERVER)],
            "env": {},
            "enabled": True,
        }
        spec.update(overrides)
        return spec


# ── Config persistence ──────────────────────────────────────────


class ConfigPersistenceTest(McpProviderTestBase):
    def test_empty_by_default(self) -> None:
        self.assertEqual(self.runtime.list_servers(), [])

    def test_save_and_reload(self) -> None:
        self.runtime.save_servers([self._fake_spec("a"), self._fake_spec("b")])
        servers = self.runtime.list_servers()
        self.assertEqual([s["id"] for s in servers], ["a", "b"])
        # All start in "stopped" status
        self.assertTrue(all(s["status"] == "stopped" for s in servers))

    def test_save_rejects_invalid_specs(self) -> None:
        # Mix valid + invalid: missing id, missing command, bad args type
        invalid = [
            {"id": "ok1", "command": "cmd"},      # valid
            {"command": "no_id"},                  # invalid: no id
            {"id": "no_cmd"},                      # invalid: no command
            {"id": "bad_args", "command": "x", "args": "notalist"},  # invalid
            {"id": "ok2", "command": "cmd"},      # valid
        ]
        result = self.runtime.save_servers(invalid)
        self.assertEqual([s["id"] for s in result], ["ok1", "ok2"])

    def test_save_dedups_by_id(self) -> None:
        result = self.runtime.save_servers([
            self._fake_spec("dup"),
            self._fake_spec("dup", command="other"),
        ])
        self.assertEqual(len(result), 1)

    def test_save_stops_removed_server(self) -> None:
        self.runtime.save_servers([self._fake_spec("a")])
        self.runtime.start_server("a")
        self.assertTrue(self.runtime.get_live_client("a") is not None)
        # Remove from config → server gets stopped
        self.runtime.save_servers([])
        self.assertIsNone(self.runtime.get_live_client("a"))


# ── Lifecycle ───────────────────────────────────────────────────


class LifecycleTest(McpProviderTestBase):
    def test_start_unknown_server_fails(self) -> None:
        result = self.runtime.start_server("not_configured")
        self.assertFalse(result["ok"])
        self.assertIn("not configured", result["error"])

    def test_disabled_config_does_not_block_explicit_start(self) -> None:
        self.runtime.save_servers([self._fake_spec("disabled", enabled=False)])
        result = self.runtime.start_server("disabled")
        self.assertTrue(result["ok"])

    def test_start_then_stop_lifecycle(self) -> None:
        self.runtime.save_servers([self._fake_spec("x")])
        r = self.runtime.start_server("x")
        self.assertTrue(r["ok"])
        self.assertFalse(r["already_running"])
        # Listing shows status=running
        status = next(s for s in self.runtime.list_servers() if s["id"] == "x")
        self.assertEqual(status["status"], "running")
        # Start again → idempotent
        r2 = self.runtime.start_server("x")
        self.assertTrue(r2["already_running"])
        # Stop
        self.runtime.stop_server("x")
        self.assertIsNone(self.runtime.get_live_client("x"))
        status_after = next(s for s in self.runtime.list_servers() if s["id"] == "x")
        self.assertEqual(status_after["status"], "stopped")

    def test_stop_unknown_server_is_noop(self) -> None:
        result = self.runtime.stop_server("never_started")
        self.assertTrue(result["ok"])
        self.assertFalse(result["was_running"])

    def test_restart_cycles_the_process(self) -> None:
        self.runtime.save_servers([self._fake_spec("x")])
        self.runtime.start_server("x")
        first_pid = self.runtime.get_live_client("x")._proc.pid  # type: ignore[union-attr]
        self.runtime.restart_server("x")
        second_pid = self.runtime.get_live_client("x")._proc.pid  # type: ignore[union-attr]
        self.assertNotEqual(first_pid, second_pid)


# ── Provider ────────────────────────────────────────────────────


class ProviderSchemaTest(McpProviderTestBase):
    def test_provider_disabled_when_server_stopped(self) -> None:
        self.runtime.save_servers([self._fake_spec("x")])
        provider = self.provider_mod.McpToolProvider("x")
        self.assertFalse(provider.is_enabled())

    def test_provider_enabled_when_server_running(self) -> None:
        self.runtime.save_servers([self._fake_spec("x")])
        self.runtime.start_server("x")
        provider = self.provider_mod.McpToolProvider("x")
        self.assertTrue(provider.is_enabled())

    def test_get_schemas_namespaces_tool_names(self) -> None:
        self.runtime.save_servers([self._fake_spec("github")])
        self.runtime.start_server("github")
        provider = self.provider_mod.McpToolProvider("github")
        schemas = provider.get_schemas()
        names = {s["function"]["name"] for s in schemas}
        # Fake server exposes echo + search → both must show up with
        # github__ prefix
        self.assertEqual(names, {"github__echo", "github__search"})

    def test_get_schemas_includes_description_and_parameters(self) -> None:
        self.runtime.save_servers([self._fake_spec("x")])
        self.runtime.start_server("x")
        provider = self.provider_mod.McpToolProvider("x")
        schemas = {s["function"]["name"]: s for s in provider.get_schemas()}
        echo_schema = schemas["x__echo"]
        self.assertIn("[mcp:x]", echo_schema["function"]["description"])
        self.assertIn("Echo", echo_schema["function"]["description"])
        params = echo_schema["function"]["parameters"]
        self.assertEqual(params["type"], "object")
        self.assertIn("text", params["properties"])


class ProviderDispatchTest(McpProviderTestBase):
    def setUp(self) -> None:
        super().setUp()
        self.runtime.save_servers([self._fake_spec("x")])
        self.runtime.start_server("x")
        self.provider = self.provider_mod.McpToolProvider("x")

    def test_dispatch_echo_returns_text(self) -> None:
        result = self.provider.dispatch("x__echo", {"text": "hello"})
        self.assertNotIn("ERROR", result["text"])
        self.assertIn("hello", result["text"])
        # Tag tells the SSE event which server this came from
        self.assertEqual(result["mcp_server"], "x")

    def test_dispatch_multiple_content_chunks_joined(self) -> None:
        result = self.provider.dispatch("x__search", {"q": "needle"})
        self.assertIn("hit 1", result["text"])
        self.assertIn("hit 2", result["text"])

    def test_dispatch_unknown_tool_returns_error(self) -> None:
        result = self.provider.dispatch("x__nonexistent", {})
        self.assertIn("ERROR", result["text"])

    def test_dispatch_when_server_stopped_returns_error(self) -> None:
        self.runtime.stop_server("x")
        result = self.provider.dispatch("x__echo", {"text": "hi"})
        self.assertIn("ERROR", result["text"])
        self.assertIn("not running", result["text"])

    def test_iserror_result_surfaces_as_error_meta(self) -> None:
        # Re-spawn with call-fail env so the server returns isError=True
        self.runtime.stop_server("x")
        self.runtime.save_servers([self._fake_spec("x", env={"FAKE_MCP_CALL_FAIL": "1"})])
        self.runtime.start_server("x")
        provider = self.provider_mod.McpToolProvider("x")
        result = provider.dispatch("x__echo", {"text": "anything"})
        self.assertIn("ERROR", result["text"])

    def test_inline_image_is_described_by_existing_vision_runtime(self) -> None:
        payload = base64.b64encode(b"small-png-payload").decode("ascii")
        with (
            mock.patch(
                "app.infrastructure.llm.vision_ocr.is_vision_enabled",
                return_value=True,
            ),
            mock.patch(
                "app.infrastructure.llm.vision_ocr.describe_image",
                return_value="A Unity scene with a clipped button.",
            ) as describe,
        ):
            result = self.provider_mod._flatten_mcp_result({
                "content": [{"type": "image", "mimeType": "image/png", "data": payload}],
            })

        self.assertIn("MCP image description", result["text"])
        self.assertIn("clipped button", result["text"])
        self.assertNotIn(payload, result["text"])
        args, kwargs = describe.call_args
        self.assertEqual(args[0], "mcp-capture-1.png")
        self.assertEqual(args[1], b"small-png-payload")
        self.assertIn("actionable visual defects", kwargs["prompt"])

    def test_inline_image_failure_never_echoes_payload(self) -> None:
        secret_payload = "not-base64-secret-payload"
        result = self.provider_mod._flatten_mcp_result({
            "content": [{
                "type": "image",
                "mimeType": "image/png",
                "data": secret_payload,
            }],
        })
        self.assertIn("invalid base64", result["text"])
        self.assertNotIn(secret_payload, result["text"])

    def test_inline_image_disabled_is_explicit_and_bounded(self) -> None:
        payload = base64.b64encode(b"image").decode("ascii")
        with mock.patch(
            "app.infrastructure.llm.vision_ocr.is_vision_enabled",
            return_value=False,
        ):
            result = self.provider_mod._flatten_mcp_result({
                "content": [
                    {"type": "text", "text": "Screenshot captured."},
                    {"type": "image", "mimeType": "image/jpeg", "data": payload},
                ],
            })
        self.assertIn("Screenshot captured.", result["text"])
        self.assertIn("vision is disabled", result["text"])
        self.assertNotIn(payload, result["text"])


class CreativeEditorMcpTest(McpProviderTestBase):
    @staticmethod
    def _provider(module, server_id: str, original_name: str):
        provider = module.McpToolProvider(server_id)
        qualified = f"{server_id}__{original_name}"
        provider._populated = True
        provider._owned = {qualified}
        provider._qualified_to_original = {qualified: original_name}
        return provider, qualified

    def test_blender_code_gets_backup_and_inline_screenshot(self) -> None:
        provider, qualified = self._provider(
            self.provider_mod, "blender", "execute_blender_code"
        )
        client = mock.Mock()
        client.call_tool.side_effect = [
            {"content": [{"type": "text", "text": "backup ok"}]},
            {"content": [{"type": "text", "text": "scene changed"}]},
        ]
        with mock.patch.object(self.provider_mod, "get_live_client", return_value=client):
            result = provider.dispatch(qualified, {"code": "import bpy"})
        self.assertTrue(result["ok"])
        self.assertTrue(result["state_changed"])
        self.assertEqual(client.call_tool.call_count, 2)
        backup_call, execute_call = client.call_tool.call_args_list
        self.assertEqual(backup_call.args[0], "execute_blender_code")
        self.assertIn(".elira-backups", backup_call.args[1]["code"])
        self.assertEqual(execute_call.args[0], "execute_blender_code")
        self.assertEqual(execute_call.args[1]["code"], "import bpy")
        self.assertTrue(execute_call.args[1]["return_screenshot"])

    def test_unity_code_gets_backup_then_screenshot(self) -> None:
        provider, qualified = self._provider(self.provider_mod, "unity", "execute_code")
        client = mock.Mock()
        client.call_tool.side_effect = [
            {"content": [{"type": "text", "text": "backup ok"}]},
            {"content": [{"type": "text", "text": "code ok"}]},
            {"content": [{"type": "text", "text": "screenshot ok"}]},
        ]
        with mock.patch.object(self.provider_mod, "get_live_client", return_value=client):
            result = provider.dispatch(
                qualified, {"action": "execute", "code": "return 1;"}
            )
        self.assertTrue(result["ok"])
        self.assertTrue(result["state_changed"])
        self.assertIn("screenshot ok", result["text"])
        self.assertEqual(
            [call.args[0] for call in client.call_tool.call_args_list],
            ["execute_code", "execute_code", "manage_camera"],
        )
        self.assertIn(".elira-backups", client.call_tool.call_args_list[0].args[1]["code"])
        screenshot_args = client.call_tool.call_args_list[2].args[1]
        self.assertEqual(screenshot_args["action"], "screenshot")
        self.assertTrue(screenshot_args["include_image"])

    def test_creative_workflow_prompt_has_no_iteration_cap(self) -> None:
        absent = self.provider_mod.creative_workflow_prompt({"github__search"})
        self.assertEqual(absent, "")
        prompt = self.provider_mod.creative_workflow_prompt({
            "blender__batch_edit",
            "blender__execute_blender_code",
            "unity__batch_execute",
            "unity__execute_code",
        })
        self.assertIn("inspect", prompt)
        self.assertNotIn("максимум", prompt)
        self.assertIn("резервную копию", prompt)

    def test_provider_mutation_signal_counts_without_fake_file_path(self) -> None:
        from app.application.code_agent.loop_helpers import tool_state_changed

        with mock.patch(
            "app.application.tool_registry.runtime.get_tool",
            return_value={"side_effect": True},
        ):
            self.assertTrue(tool_state_changed(
                "blender__batch_edit",
                {"ok": True, "state_changed": True},
                exec_ok=True,
            ))
            self.assertFalse(tool_state_changed(
                "blender__batch_edit",
                {"ok": False, "state_changed": True},
                exec_ok=False,
            ))


class BuildProvidersTest(McpProviderTestBase):
    def test_build_mcp_providers_only_for_running(self) -> None:
        # Configure two servers; start only one
        self.runtime.save_servers([self._fake_spec("a"), self._fake_spec("b")])
        self.runtime.start_server("a")
        # Don't start b
        providers = self.provider_mod.build_mcp_providers()
        names = [p.name for p in providers]
        self.assertIn("mcp:a", names)
        self.assertNotIn("mcp:b", names)


# ── Integration with ToolRegistry ──────────────────────────────


class RegistryIntegrationTest(McpProviderTestBase):
    def test_registry_aggregates_mcp_tools_with_namespace(self) -> None:
        from app.application.tool_providers import ToolRegistry

        self.runtime.save_servers([self._fake_spec("srv")])
        self.runtime.start_server("srv")
        provider = self.provider_mod.McpToolProvider("srv")
        reg = ToolRegistry([provider])
        names = reg.known_tools()
        self.assertEqual(names, {"srv__echo", "srv__search"})

    def test_two_servers_same_tool_name_dont_collide(self) -> None:
        from app.application.tool_providers import ToolRegistry

        self.runtime.save_servers([self._fake_spec("a"), self._fake_spec("b")])
        self.runtime.start_server("a")
        self.runtime.start_server("b")
        reg = ToolRegistry([
            self.provider_mod.McpToolProvider("a"),
            self.provider_mod.McpToolProvider("b"),
        ])
        names = reg.known_tools()
        # Both servers expose `echo` and `search`; namespacing keeps them
        # distinct in the agent's tool list.
        self.assertEqual(names, {"a__echo", "a__search", "b__echo", "b__search"})

    def test_dispatch_through_registry(self) -> None:
        from app.application.tool_providers import ToolRegistry

        self.runtime.save_servers([self._fake_spec("srv")])
        self.runtime.start_server("srv")
        reg = ToolRegistry([self.provider_mod.McpToolProvider("srv")])
        result = reg.dispatch("srv__echo", {"text": "via-registry"})
        self.assertIn("via-registry", result.tool_meta["text"])


if __name__ == "__main__":
    unittest.main()
