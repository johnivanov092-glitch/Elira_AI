"""Tests — Plugin manifest + subprocess isolation (P6 Step 14, P9.1 hardening)."""
from __future__ import annotations

import json
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.infrastructure.plugins.plugin_system as psys  # noqa: E402
from app.infrastructure.plugins.plugin_system import (  # noqa: E402
    PLUGIN_DEFAULT_TIMEOUT,
    _load_plugin_manifest,
    _run_plugin_subprocess,
    load_plugins,
)

_SIMPLE_PLUGIN_SRC = """\
DESCRIPTION = "Simple test plugin"
CATEGORY = "testing"

def run(args):
    return {"ok": True, "echo": args.get("value", "default")}
"""

_TIMEOUT_PLUGIN_SRC = """\
import time
def run(args):
    time.sleep(60)
    return {"ok": True}
"""

_CRASH_IMPORT_SRC = """\
raise RuntimeError("plugin crash at import time")

def run(args):
    return {"ok": True}
"""

_HOOK_PLUGIN_SRC = """\
def on_message(data):
    return f"hook received: {data}"

def run(args):
    return {"ok": True}
"""

_INFINITE_HOOK_SRC = """\
import time

def on_start():
    time.sleep(60)

def run(args):
    return {"ok": True}
"""

# --- P9.1 fixup sources ---

_STDOUT_OVERFLOW_SRC = """\
import sys

def run(args):
    # Write > PLUGIN_MAX_OUTPUT_CHARS * 3 bytes to trigger stdout overflow
    chunk = b"X" * 4096
    for _ in range(40):          # 40 * 4096 = 163840 > 150000 limit
        sys.stdout.buffer.write(chunk)
        sys.stdout.buffer.flush()
    return {"ok": True}
"""

_STDERR_OVERFLOW_SRC = """\
import sys

def run(args):
    # Write > _STDERR_MAX_BYTES (2000) to stderr
    sys.stderr.write("E" * 10_000)
    return {"ok": True}
"""

_TIMEOUT_HOOK_SRC = """\
import time

def on_message(data):
    time.sleep(60)
    return "late result"

def run(args):
    return {"ok": True}
"""

_DICT_HOOK_SRC = """\
def on_message(data):
    return {"context": "from_hook", "value": 42}

def run(args):
    return {"ok": True}
"""

# Produces ~60 024 chars of ASCII output — exceeds PLUGIN_MAX_OUTPUT_CHARS (50 000)
# but stays below _STDOUT_MAX_BYTES (150 000 bytes), so exercises the char check.
_ASCII_NEAR_BYTE_CAP_SRC = """\
def run(args):
    return {"ok": True, "data": "A" * 60_000}
"""

_MANIFEST = {
    "name": "Test Plugin",
    "version": "1.0.0",
    "capabilities": ["testing"],
    "enabled": False,
}


def _write_plugin(tmp: Path, name: str, src: str, manifest: dict | None = _MANIFEST):
    (tmp / f"{name}.py").write_text(src, encoding="utf-8")
    if manifest is not None:
        (tmp / f"{name}.manifest.json").write_text(
            json.dumps(manifest), encoding="utf-8"
        )


class TestLoadPluginManifest(unittest.TestCase):

    def test_loads_valid_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "myplugin.py"
            py.write_text("def run(a): pass")
            mf = Path(tmp) / "myplugin.manifest.json"
            mf.write_text(json.dumps({"name": "My", "version": "1.0", "capabilities": [], "enabled": False}))
            result = _load_plugin_manifest(py)
            self.assertIsNotNone(result)
            self.assertEqual(result["name"], "My")

    def test_returns_none_when_no_manifest(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "nomanifest.py"
            py.write_text("def run(a): pass")
            self.assertIsNone(_load_plugin_manifest(py))

    def test_returns_none_for_malformed_json(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "bad.py"
            py.write_text("def run(a): pass")
            (Path(tmp) / "bad.manifest.json").write_text("NOT JSON")
            self.assertIsNone(_load_plugin_manifest(py))


class TestLoadPluginsManifestRequirement(unittest.TestCase):

    def tearDown(self):
        for name in ("manifest_test_plugin",):
            try:
                import app.application.tool_registry.runtime as r
                r.delete_tool(name)
            except Exception:
                pass

    def test_plugin_without_manifest_rejected(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "manifest_test_plugin.py").write_text(_SIMPLE_PLUGIN_SRC)
            # NO manifest.json written
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                result = load_plugins()
            self.assertNotIn("manifest_test_plugin", result["loaded"])
            error_names = [e["name"] for e in result["errors"]]
            self.assertIn("manifest_test_plugin", error_names)

    def test_plugin_with_manifest_loaded(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "manifest_test_plugin", _SIMPLE_PLUGIN_SRC, {
                "name": "Manifest Test", "version": "1.0", "capabilities": [], "enabled": True,
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                result = load_plugins()
            self.assertIn("manifest_test_plugin", result["loaded"])


class TestDisabledByDefault(unittest.TestCase):

    def tearDown(self):
        for name in ("disabled_by_default_plugin",):
            try:
                import app.application.tool_registry.runtime as r
                r.delete_tool(name)
            except Exception:
                pass

    def test_plugin_disabled_by_default_when_manifest_says_false(self):
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "disabled_by_default_plugin", _SIMPLE_PLUGIN_SRC, {
                "name": "DisabledPlugin", "version": "1.0", "capabilities": [], "enabled": False,
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                load_plugins()
            plugin = psys._plugins.get("disabled_by_default_plugin")
            self.assertIsNotNone(plugin)
            self.assertFalse(plugin["enabled"])


class TestSubprocessExecution(unittest.TestCase):

    def test_subprocess_runs_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "subprocess_test.py"
            py.write_text(_SIMPLE_PLUGIN_SRC)
            result = _run_plugin_subprocess(str(py), {"action": "run", "args": {"value": "hello"}})
            self.assertTrue(result.get("ok"), result)
            self.assertEqual(result.get("echo"), "hello")

    def test_subprocess_default_timeout_constant(self):
        self.assertEqual(PLUGIN_DEFAULT_TIMEOUT, 0)


class TestProductionPathHardened(unittest.TestCase):
    """Verify that app.application.plugins uses the hardened runtime."""

    def test_application_plugins_imports_hardened_run_plugin(self):
        from app.application.plugins import run_plugin
        from app.infrastructure.plugins.plugin_system import run_plugin as infra_run_plugin
        self.assertIs(run_plugin, infra_run_plugin,
                      "app.application.plugins.run_plugin must delegate to infrastructure")

    def test_application_plugins_has_timeout_constant(self):
        from app.application.plugins import PLUGIN_DEFAULT_TIMEOUT
        self.assertEqual(PLUGIN_DEFAULT_TIMEOUT, 0)

    def test_production_path_rejects_plugin_without_manifest(self):
        from app.application.plugins import load_plugins
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            (tmp_path / "no_manifest_plugin.py").write_text(_SIMPLE_PLUGIN_SRC)
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                result = load_plugins()
        error_names = [e["name"] for e in result["errors"]]
        self.assertIn("no_manifest_plugin", error_names)


# ─── P9.1 isolation tests ─────────────────────────────────────────────────────

class TestP91PluginIsolation(unittest.TestCase):
    """P9.1: plugin .py must never be imported into the backend process."""

    def tearDown(self):
        for name in (
            "crash_p91_plugin", "disabled_p91_plugin",
            "hook_p91_plugin", "infinite_hook_p91_plugin",
            "nomod_p91_plugin",
            "timeout_log_p91", "dict_hook_p91",
            "timeout_max_p91", "timeout_min_p91", "timeout_invalid_p91",
        ):
            try:
                import app.application.tool_registry.runtime as r
                r.delete_tool(name)
            except Exception:
                pass

    def test_crashing_plugin_does_not_crash_backend(self):
        """load_plugins() must succeed even when the plugin has a top-level exception."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "crash_p91_plugin", _CRASH_IMPORT_SRC, {
                "name": "Crash", "version": "1.0", "capabilities": [],
                "enabled": False,  # disabled → no subprocess call at load time
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                result = load_plugins()
        # load succeeds — manifest was readable
        self.assertIn("crash_p91_plugin", result["loaded"])
        # Running the plugin returns an error, not an exception
        plugin = psys._plugins.get("crash_p91_plugin")
        self.assertIsNotNone(plugin)
        # Enable and run to verify subprocess captures the crash
        psys._plugins["crash_p91_plugin"]["enabled"] = True
        run_result = psys.run_plugin("crash_p91_plugin", {})
        self.assertFalse(run_result.get("ok"))
        self.assertIn("import error", run_result.get("error", "").lower())

    def test_hook_fires_in_subprocess(self):
        """on_message hook must execute in a child process and return its value."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "hook_p91_plugin", _HOOK_PLUGIN_SRC, {
                "name": "Hook Plugin", "version": "1.0", "capabilities": [],
                "enabled": True, "hooks": ["on_message"],
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                load_plugins()
            # fire_hook must be called while temp dir still exists
            results = psys.fire_hook("on_message", "hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["plugin"], "hook_p91_plugin")
        self.assertEqual(results[0]["result"], "hook received: hello")

    def test_discovery_does_not_execute_on_start_hook(self):
        """Discovery only registers metadata and never executes plugin code."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "infinite_hook_p91_plugin", _INFINITE_HOOK_SRC, {
                "name": "Infinite", "version": "1.0", "capabilities": [],
                "enabled": True, "hooks": ["on_start"], "timeout": 1,
            })
            start = time.monotonic()
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                load_plugins()
            elapsed = time.monotonic() - start
        self.assertLess(elapsed, 5, f"discovery unexpectedly executed plugin code for {elapsed:.1f}s")

    def test_hook_action_dispatched_via_subprocess_runner(self):
        """_run_plugin_subprocess with action='hook' must call the named hook function."""
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "hook_runner_test.py"
            py.write_text(_HOOK_PLUGIN_SRC)
            result = _run_plugin_subprocess(
                str(py),
                {"action": "hook", "hook_name": "on_message", "data": "test_data"},
            )
        self.assertTrue(result.get("ok"), result)
        self.assertEqual(result.get("result"), "hook received: test_data")

    def test_inspect_action_returns_module_metadata(self):
        """action='inspect' must return declared metadata without crashing backend."""
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "inspect_test.py"
            py.write_text(_SIMPLE_PLUGIN_SRC)
            result = _run_plugin_subprocess(str(py), {"action": "inspect"})
        self.assertTrue(result.get("ok"), result)
        self.assertIn("category", result)
        self.assertEqual(result["category"], "testing")

    def test_no_module_key_in_plugin_record(self):
        """Plugin records must not contain a 'module' key (no in-process import)."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "nomod_p91_plugin", _SIMPLE_PLUGIN_SRC, {
                "name": "NoMod", "version": "1.0", "capabilities": [], "enabled": False,
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                load_plugins()
        plugin = psys._plugins.get("nomod_p91_plugin")
        self.assertIsNotNone(plugin)
        self.assertNotIn("module", plugin)

    # ── P9.1 fixup: bounded output, hook envelope, allowlist ──────────────────

    def test_stderr_overflow_is_drained_without_stopping_plugin(self):
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "stderr_overflow.py"
            py.write_text(_STDERR_OVERFLOW_SRC)
            result = _run_plugin_subprocess(str(py), {"action": "run", "args": {}})
        self.assertTrue(result.get("ok"), result)

    def test_hook_returning_dict_preserved(self):
        """A hook that returns a dict must have its value preserved in fire_hook results."""
        with tempfile.TemporaryDirectory() as tmp:
            tmp_path = Path(tmp)
            _write_plugin(tmp_path, "dict_hook_p91", _DICT_HOOK_SRC, {
                "name": "DictHook", "version": "1.0", "capabilities": [],
                "enabled": True, "hooks": ["on_message"],
            })
            with mock.patch.object(psys, "PLUGINS_DIR", tmp_path):
                load_plugins()
            results = psys.fire_hook("on_message", "hello")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["plugin"], "dict_hook_p91")
        self.assertEqual(results[0]["result"], {"context": "from_hook", "value": 42})

    def test_unknown_hook_rejected(self):
        """fire_hook must reject unknown hook names; runner must also reject them."""
        # fire_hook gate
        results = psys.fire_hook("arbitrary_hook", "test")
        self.assertEqual(results, [])

        # subprocess runner gate (defence-in-depth)
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "hook_test.py"
            py.write_text("def on_message(d): return 'ok'\ndef run(a): return {'ok': True}")
            result = _run_plugin_subprocess(str(py), {"action": "hook", "hook_name": "evil_hook"})
        self.assertFalse(result.get("ok"))
        self.assertIn("allowed", result.get("error", "").lower())

    # ── P9.1 final fixup: char limit + timeout bounds ─────────────────────────

    def test_char_limit_enforced_after_decode(self):
        """ASCII output between 50k and 150k chars must fail the char check (not the byte cap)."""
        with tempfile.TemporaryDirectory() as tmp:
            py = Path(tmp) / "ascii_near_cap.py"
            py.write_text(_ASCII_NEAR_BYTE_CAP_SRC)
            result = _run_plugin_subprocess(str(py), {"action": "run", "args": {}})
        self.assertFalse(result.get("ok"), result)
        self.assertIn("char", result.get("error", "").lower())

if __name__ == "__main__":
    unittest.main()
