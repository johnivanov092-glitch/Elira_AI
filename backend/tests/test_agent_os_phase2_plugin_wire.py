"""Tests — Phase 2 plugin→tool-registry wiring.

Verifies that plugins loaded by plugin_system appear in the Tool Registry
with source='plugin' and can be executed via execute_tool().
"""
from __future__ import annotations

import sys
import textwrap
import tempfile
import importlib
import contextlib
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import unittest

import app.application.tool_registry.runtime as reg  # noqa: E402


_PLUGIN_SRC = textwrap.dedent("""\
    DESCRIPTION = "Test plugin for tool-registry wiring"
    CATEGORY = "testing"
    ICON = "🧪"
    TRIGGERS = ["unit test trigger"]

    def run(args: dict) -> dict:
        return {"ok": True, "echoed": args.get("value", "default")}
""")


@contextlib.contextmanager
def _patched_plugins_env(psys, tmp_path: Path):
    """Isolate the plugin system from real on-disk state for one test.

    Patches BOTH PLUGINS_DIR (discovery) and _CONFIG_FILE (persisted enabled/
    disabled state) onto a temp dir, so enable/disable flows never mutate the
    git-tracked data/plugins_config.json. _load_config/_save_config reference
    _CONFIG_FILE via the module global, so patching the attribute is enough.
    """
    cfg_file = tmp_path / "plugins_config.json"
    with mock.patch.object(psys, "PLUGINS_DIR", tmp_path), \
            mock.patch.object(psys, "_CONFIG_FILE", cfg_file):
        yield


class TestPluginToolRegistryWire(unittest.TestCase):
    """Plugin system registers/unregisters tools in the Tool Registry."""

    def setUp(self) -> None:
        # Ensure test tool name is clean before each test
        for name in ("wire_test_plugin",):
            reg.delete_tool(name)

    def tearDown(self) -> None:
        for name in ("wire_test_plugin",):
            reg.delete_tool(name)

    def _make_plugin_dir_with_file(self, tmpdir: Path, name: str, src: str) -> Path:
        py_file = tmpdir / f"{name}.py"
        py_file.write_text(src, encoding="utf-8")
        # P9.1: all metadata is read from manifest, not the module
        manifest = {
            "name": name,
            "version": "1.0.0",
            "capabilities": ["testing"],
            "category": "testing",
            "enabled": True,  # enabled=True so wire tests can verify registration
        }
        (tmpdir / f"{name}.manifest.json").write_text(
            __import__("json").dumps(manifest), encoding="utf-8"
        )
        return py_file

    def test_plugin_registered_after_load(self) -> None:
        """Loading a plugin registers it for explicit Workflow invocation."""
        import app.infrastructure.plugins.plugin_system as psys

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._make_plugin_dir_with_file(tmp_path, "wire_test_plugin", _PLUGIN_SRC)

            with _patched_plugins_env(psys, tmp_path):
                psys.load_plugins()

            tool = reg.get_tool("wire_test_plugin")
            self.assertIsNotNone(tool, "Plugin should be registered as a tool")
            assert tool is not None
            self.assertEqual(tool["source"], "plugin")
            self.assertEqual(tool["category"], "testing")
            self.assertTrue(tool["enabled"])
            self.assertEqual(tool["permission"], "require_approval")
            self.assertTrue(tool["policy_classified"])

    def test_plugin_execute_via_tool_registry(self) -> None:
        """Plugins registered as tools wire registration -> handler via _execute_raw.

        _execute_raw is the raw dispatch primitive; Workflow owns authorization.
        """
        import app.infrastructure.plugins.plugin_system as psys

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._make_plugin_dir_with_file(tmp_path, "wire_test_plugin", _PLUGIN_SRC)

            with _patched_plugins_env(psys, tmp_path):
                psys.load_plugins()

            result = reg._execute_raw("wire_test_plugin", {"value": "hello"})
            self.assertTrue(result.get("ok"), f"Expected ok=True, got: {result}")
            self.assertEqual(result.get("echoed"), "hello")

    def test_disable_plugin_disables_tool(self) -> None:
        """Disabling a plugin disables the corresponding tool in the registry."""
        import app.infrastructure.plugins.plugin_system as psys

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._make_plugin_dir_with_file(tmp_path, "wire_test_plugin", _PLUGIN_SRC)

            with _patched_plugins_env(psys, tmp_path):
                psys.load_plugins()
                psys.disable_plugin("wire_test_plugin")

            tool = reg.get_tool("wire_test_plugin")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertFalse(tool["enabled"], "Tool should be disabled after plugin disable")


    def test_enable_plugin_enables_tool(self) -> None:
        """Re-enabling a plugin re-enables the tool in the registry."""
        import app.infrastructure.plugins.plugin_system as psys

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._make_plugin_dir_with_file(tmp_path, "wire_test_plugin", _PLUGIN_SRC)

            with _patched_plugins_env(psys, tmp_path):
                psys.load_plugins()
                psys.disable_plugin("wire_test_plugin")
                psys.enable_plugin("wire_test_plugin")

            tool = reg.get_tool("wire_test_plugin")
            self.assertIsNotNone(tool)
            assert tool is not None
            self.assertTrue(tool["enabled"])

    def test_reload_reregisters_plugins(self) -> None:
        """reload_plugins() re-registers all plugins (idempotent)."""
        import app.infrastructure.plugins.plugin_system as psys

        with tempfile.TemporaryDirectory() as tmpdir:
            tmp_path = Path(tmpdir)
            self._make_plugin_dir_with_file(tmp_path, "wire_test_plugin", _PLUGIN_SRC)

            with _patched_plugins_env(psys, tmp_path):
                psys.load_plugins()
                psys.reload_plugins()  # second load — should not fail

            tool = reg.get_tool("wire_test_plugin")
            self.assertIsNotNone(tool)
            self.assertEqual(tool["source"], "plugin")


if __name__ == "__main__":
    unittest.main()
