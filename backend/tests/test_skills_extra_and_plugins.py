"""Tests for application/skills_extra and application/plugins runtimes."""
from __future__ import annotations

import json
import sys
import tempfile
import unittest
import zipfile as _zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.skills_extra.runtime as se       # noqa: E402
import app.application.plugins.runtime as plugins_rt    # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# skills_extra — regex helper
# ─────────────────────────────────────────────────────────────────────────────

class RegexHelperTest(unittest.TestCase):
    def test_basic_match(self) -> None:
        result = se.test_regex(r"\d+", "abc123def456")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertTrue(result["has_match"])

    def test_no_match(self) -> None:
        result = se.test_regex(r"\d+", "no digits here")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)
        self.assertFalse(result["has_match"])

    def test_case_insensitive_flag(self) -> None:
        result = se.test_regex(r"hello", "HELLO world", flags="i")
        self.assertTrue(result["ok"])
        self.assertTrue(result["has_match"])

    def test_multiline_flag(self) -> None:
        result = se.test_regex(r"^line", "line1\nline2", flags="m")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)

    def test_groups_captured(self) -> None:
        result = se.test_regex(r"(\w+)@(\w+)", "user@example.com")
        self.assertTrue(result["has_match"])
        groups = result["matches"][0]["groups"]
        self.assertIn("user", groups)
        self.assertIn("example", groups)

    def test_match_has_start_end(self) -> None:
        result = se.test_regex(r"\d+", "abc123")
        m = result["matches"][0]
        self.assertEqual(m["start"], 3)
        self.assertEqual(m["end"], 6)

    def test_invalid_regex_returns_error(self) -> None:
        result = se.test_regex(r"[invalid", "text")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_result_has_required_keys(self) -> None:
        result = se.test_regex(r"x", "x")
        for key in ("ok", "pattern", "text", "matches", "count", "has_match"):
            self.assertIn(key, result)


# ─────────────────────────────────────────────────────────────────────────────
# skills_extra — zip archiver (patched OUTPUT_DIR + WORKSPACE)
# ─────────────────────────────────────────────────────────────────────────────

class ZipArchiverTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_output = se.OUTPUT_DIR
        self._orig_ws = se.WORKSPACE
        se.OUTPUT_DIR = tmp / "output"
        se.OUTPUT_DIR.mkdir()
        se.WORKSPACE = tmp / "workspace"
        se.WORKSPACE.mkdir()

    def tearDown(self) -> None:
        se.OUTPUT_DIR = self._orig_output
        se.WORKSPACE = self._orig_ws
        self._tmpdir.cleanup()
        super().tearDown()

    def test_create_zip_from_file(self) -> None:
        src = se.WORKSPACE / "sample.txt"
        src.write_text("zip me!", encoding="utf-8")
        result = se.create_zip(str(src))
        self.assertTrue(result["ok"])
        self.assertTrue(Path(result["path"]).exists())
        self.assertTrue(result["filename"].endswith(".zip"))

    def test_create_zip_from_dir(self) -> None:
        src_dir = se.WORKSPACE / "mydir"
        src_dir.mkdir()
        (src_dir / "a.txt").write_text("aaa", encoding="utf-8")
        (src_dir / "b.txt").write_text("bbb", encoding="utf-8")
        result = se.create_zip(str(src_dir))
        self.assertTrue(result["ok"])
        self.assertGreater(result["size"], 0)

    def test_create_zip_custom_output_name(self) -> None:
        src = se.WORKSPACE / "data.txt"
        src.write_text("content", encoding="utf-8")
        result = se.create_zip(str(src), "myarchive.zip")
        self.assertTrue(result["ok"])
        self.assertEqual(result["filename"], "myarchive.zip")

    def test_create_zip_nonexistent_returns_error(self) -> None:
        result = se.create_zip("ghost_file.txt")
        self.assertFalse(result["ok"])

    def test_extract_zip_success(self) -> None:
        zip_path = se.OUTPUT_DIR / "test.zip"
        with _zipfile.ZipFile(zip_path, "w") as zf:
            zf.writestr("hello.txt", "hello content")
        dest = str(se.WORKSPACE / "extracted")
        result = se.extract_zip(str(zip_path), dest)
        self.assertTrue(result["ok"])
        self.assertIn("hello.txt", result["files"])
        self.assertEqual(result["count"], 1)

    def test_extract_zip_nonexistent_returns_error(self) -> None:
        result = se.extract_zip("does_not_exist.zip")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# skills_extra — webhooks (in-memory state)
# ─────────────────────────────────────────────────────────────────────────────

class WebhookTest(unittest.TestCase):
    def setUp(self) -> None:
        se.clear_webhooks()

    def tearDown(self) -> None:
        se.clear_webhooks()
        super().tearDown()

    def test_store_and_list(self) -> None:
        se.store_webhook({"event": "ping"}, source="github")
        result = se.list_webhooks()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["items"][0]["source"], "github")

    def test_clear_webhooks(self) -> None:
        se.store_webhook({"x": 1})
        se.store_webhook({"x": 2})
        se.clear_webhooks()
        self.assertEqual(se.list_webhooks()["count"], 0)

    def test_limit_enforced(self) -> None:
        for i in range(5):
            se.store_webhook({"n": i})
        result = se.list_webhooks(limit=3)
        self.assertLessEqual(len(result["items"]), 3)

    def test_entry_has_expected_keys(self) -> None:
        se.store_webhook({"data": "val"}, source="test")
        item = se.list_webhooks()["items"][0]
        for key in ("id", "received_at", "source", "data"):
            self.assertIn(key, item)

    def test_multiple_entries_accumulate(self) -> None:
        se.store_webhook({"a": 1})
        se.store_webhook({"b": 2})
        se.store_webhook({"c": 3})
        self.assertEqual(se.list_webhooks()["count"], 3)


# ─────────────────────────────────────────────────────────────────────────────
# skills_extra — convert_file (JSON→CSV via temp files)
# ─────────────────────────────────────────────────────────────────────────────

class ConvertFileTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)
        self._orig_output = se.OUTPUT_DIR
        self._orig_ws = se.WORKSPACE
        se.OUTPUT_DIR = tmp / "output"
        se.OUTPUT_DIR.mkdir()
        se.WORKSPACE = tmp / "workspace"
        se.WORKSPACE.mkdir()

    def tearDown(self) -> None:
        se.OUTPUT_DIR = self._orig_output
        se.WORKSPACE = self._orig_ws
        self._tmpdir.cleanup()
        super().tearDown()

    def test_json_to_csv(self) -> None:
        src = se.WORKSPACE / "data.json"
        src.write_text(
            json.dumps([{"name": "Alice", "age": 30}, {"name": "Bob", "age": 25}]),
            encoding="utf-8",
        )
        result = se.convert_file(str(src), "csv")
        self.assertTrue(result["ok"])
        self.assertTrue(result["filename"].endswith(".csv"))
        self.assertTrue((se.OUTPUT_DIR / result["filename"]).exists())

    def test_unsupported_conversion_returns_error(self) -> None:
        src = se.WORKSPACE / "file.txt"
        src.write_text("text", encoding="utf-8")
        result = se.convert_file(str(src), "docx")
        self.assertFalse(result["ok"])

    def test_nonexistent_source_returns_error(self) -> None:
        result = se.convert_file("no_such_file.json", "csv")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# plugins — shared setUp that writes temp plugin .py files and loads them
# ─────────────────────────────────────────────────────────────────────────────

_SIMPLE_PLUGIN = """\
DESCRIPTION = "Test plugin"
AUTHOR = "Tester"
VERSION = "2.0"
ICON = "\\U0001f9ea"
CATEGORY = "test"
TRIGGERS = ["run test", "test trigger"]

def run(args):
    val = args.get("x", 0)
    return {"ok": True, "value": val + 1}

def on_message(text):
    return None
"""

_HOOK_PLUGIN = """\
DESCRIPTION = "Hook plugin"
TRIGGERS = []

def run(args):
    return {"ok": True}

def on_message(text):
    return f"hook saw: {text}"
"""


class PluginsBaseTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        tmp = Path(self._tmpdir.name)

        self._orig_dir = plugins_rt.PLUGINS_DIR
        self._orig_cfg = plugins_rt._CONFIG_FILE
        self._saved_plugins = dict(plugins_rt._plugins)
        self._saved_states = dict(plugins_rt._plugin_states)

        plugins_rt.PLUGINS_DIR = tmp / "plugins"
        plugins_rt.PLUGINS_DIR.mkdir()
        plugins_rt._CONFIG_FILE = tmp / "plugins_config.json"
        plugins_rt._plugins = {}
        plugins_rt._plugin_states = {}

        (plugins_rt.PLUGINS_DIR / "myplugin.py").write_text(_SIMPLE_PLUGIN, encoding="utf-8")
        (plugins_rt.PLUGINS_DIR / "hookplugin.py").write_text(_HOOK_PLUGIN, encoding="utf-8")
        plugins_rt.load_plugins()

    def tearDown(self) -> None:
        plugins_rt.PLUGINS_DIR = self._orig_dir
        plugins_rt._CONFIG_FILE = self._orig_cfg
        plugins_rt._plugins = self._saved_plugins
        plugins_rt._plugin_states = self._saved_states
        self._tmpdir.cleanup()
        super().tearDown()


# ─────────────────────────────────────────────────────────────────────────────
# plugins — load / metadata
# ─────────────────────────────────────────────────────────────────────────────

class PluginLoadTest(PluginsBaseTest):
    def test_plugins_loaded(self) -> None:
        result = plugins_rt.list_plugins()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)

    def test_plugin_names_present(self) -> None:
        result = plugins_rt.list_plugins()
        names = [p["name"] for p in result["plugins"]]
        self.assertIn("myplugin", names)
        self.assertIn("hookplugin", names)

    def test_plugin_info(self) -> None:
        result = plugins_rt.get_plugin_info("myplugin")
        self.assertTrue(result["ok"])
        self.assertEqual(result["version"], "2.0")
        self.assertEqual(result["category"], "test")
        self.assertEqual(result["description"], "Test plugin")

    def test_get_nonexistent_plugin_info(self) -> None:
        result = plugins_rt.get_plugin_info("ghost_plugin")
        self.assertFalse(result["ok"])

    def test_skip_file_starting_with_underscore(self) -> None:
        (plugins_rt.PLUGINS_DIR / "_private.py").write_text("def run(a): return {}", encoding="utf-8")
        plugins_rt.load_plugins()
        names = [p["name"] for p in plugins_rt.list_plugins()["plugins"]]
        self.assertNotIn("_private", names)


# ─────────────────────────────────────────────────────────────────────────────
# plugins — run / enable / disable / settings
# ─────────────────────────────────────────────────────────────────────────────

class PluginRunTest(PluginsBaseTest):
    def test_run_plugin_returns_result(self) -> None:
        result = plugins_rt.run_plugin("myplugin", {"x": 5})
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 6)

    def test_run_plugin_default_args(self) -> None:
        result = plugins_rt.run_plugin("myplugin", {})
        self.assertTrue(result["ok"])
        self.assertEqual(result["value"], 1)  # 0 + 1

    def test_run_nonexistent_plugin(self) -> None:
        result = plugins_rt.run_plugin("ghost", {})
        self.assertFalse(result["ok"])

    def test_disable_prevents_run(self) -> None:
        plugins_rt.disable_plugin("myplugin")
        result = plugins_rt.run_plugin("myplugin", {})
        self.assertFalse(result["ok"])

    def test_enable_after_disable(self) -> None:
        plugins_rt.disable_plugin("myplugin")
        r = plugins_rt.enable_plugin("myplugin")
        self.assertTrue(r["ok"])
        self.assertTrue(r["enabled"])
        run_result = plugins_rt.run_plugin("myplugin", {})
        self.assertTrue(run_result["ok"])

    def test_update_plugin_settings(self) -> None:
        result = plugins_rt.update_plugin_settings("myplugin", {"color": "blue"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["settings"]["color"], "blue")

    def test_update_settings_nonexistent(self) -> None:
        result = plugins_rt.update_plugin_settings("ghost", {})
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# plugins — triggers and hooks
# ─────────────────────────────────────────────────────────────────────────────

class PluginTriggersTest(PluginsBaseTest):
    def test_check_triggers_match(self) -> None:
        matches = plugins_rt.check_triggers("please run test now")
        self.assertTrue(any(m["name"] == "myplugin" for m in matches))

    def test_check_triggers_no_match(self) -> None:
        matches = plugins_rt.check_triggers("nothing relevant here xyz")
        self.assertEqual(len(matches), 0)

    def test_run_triggered_executes_plugin(self) -> None:
        results = plugins_rt.run_triggered("run test please")
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["plugin"], "myplugin")

    def test_fire_hook_on_message_returns_result(self) -> None:
        results = plugins_rt.fire_hook("on_message", "hello")
        # hookplugin.on_message returns a truthy string; myplugin returns None (filtered)
        hook_results = [r for r in results if r["plugin"] == "hookplugin"]
        self.assertEqual(len(hook_results), 1)
        self.assertIn("hook saw:", hook_results[0]["result"])

    def test_disabled_plugin_skipped_in_trigger(self) -> None:
        plugins_rt.disable_plugin("myplugin")
        matches = plugins_rt.check_triggers("run test please")
        self.assertFalse(any(m["name"] == "myplugin" for m in matches))


if __name__ == "__main__":
    unittest.main()
