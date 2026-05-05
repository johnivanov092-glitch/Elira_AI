"""Tests for application/image_generation (pure helpers + status),
application/reflection_loop (mocked chat_service), and
application/plugins (patched PLUGINS_DIR + CONFIG_FILE)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.image_generation.runtime as ig_rt   # noqa: E402
import app.application.reflection_loop.runtime as rl_rt    # noqa: E402
import app.application.plugins.runtime as pl_rt            # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# image_generation — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class ClipPromptTest(unittest.TestCase):
    def test_short_prompt_unchanged(self) -> None:
        prompt = "a red fox in the forest"
        result = ig_rt._clip_prompt(prompt, max_words=60)
        self.assertEqual(result, prompt)

    def test_long_prompt_clipped(self) -> None:
        prompt = " ".join([f"word{i}" for i in range(100)])
        result = ig_rt._clip_prompt(prompt, max_words=60)
        words = result.split()
        self.assertEqual(len(words), 60)

    def test_exact_limit_unchanged(self) -> None:
        prompt = " ".join([f"w{i}" for i in range(60)])
        result = ig_rt._clip_prompt(prompt, max_words=60)
        self.assertEqual(result, prompt)

    def test_empty_prompt_returns_empty(self) -> None:
        result = ig_rt._clip_prompt("", max_words=60)
        self.assertEqual(result, "")

    def test_single_word_unchanged(self) -> None:
        result = ig_rt._clip_prompt("dragon", max_words=5)
        self.assertEqual(result, "dragon")

    def test_custom_max_words(self) -> None:
        prompt = "one two three four five six seven eight nine ten"
        result = ig_rt._clip_prompt(prompt, max_words=3)
        self.assertEqual(result, "one two three")


class GetStatusTest(unittest.TestCase):
    def test_returns_ok_true(self) -> None:
        result = ig_rt.get_status()
        self.assertTrue(result["ok"])

    def test_returns_model_key(self) -> None:
        result = ig_rt.get_status()
        self.assertIn("model", result)
        self.assertIsInstance(result["model"], str)

    def test_returns_loaded_bool(self) -> None:
        result = ig_rt.get_status()
        self.assertIsInstance(result["loaded"], bool)

    def test_loaded_false_when_no_pipe(self) -> None:
        orig = ig_rt._pipe
        ig_rt._pipe = None
        try:
            result = ig_rt.get_status()
            self.assertFalse(result["loaded"])
        finally:
            ig_rt._pipe = orig

    def test_returns_gpu_key(self) -> None:
        result = ig_rt.get_status()
        self.assertIn("gpu", result)


class UnloadModelTest(unittest.TestCase):
    def test_unload_returns_ok(self) -> None:
        result = ig_rt.unload_model()
        self.assertTrue(result["ok"])

    def test_unload_returns_message(self) -> None:
        result = ig_rt.unload_model()
        self.assertIn("message", result)

    def test_unload_sets_pipe_to_none(self) -> None:
        # Even if pipe was None, unload should still return ok
        orig = ig_rt._pipe
        ig_rt._pipe = None
        try:
            result = ig_rt.unload_model()
            self.assertTrue(result["ok"])
            self.assertIsNone(ig_rt._pipe)
        finally:
            ig_rt._pipe = orig


# ─────────────────────────────────────────────────────────────────────────────
# reflection_loop — run_reflection_loop (mocked chat_service)
# ─────────────────────────────────────────────────────────────────────────────

class RunReflectionLoopTest(unittest.TestCase):
    def _mock_run_chat(self, answer="Improved answer"):
        return {
            "ok": True,
            "answer": answer,
            "meta": {"profile": "default"},
            "warnings": [],
        }

    def test_ok_true_when_chat_succeeds(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value=self._mock_run_chat("Great answer!"),
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="explain python",
                draft_text="Python is a language.",
                review_text="Add more detail.",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Great answer!")

    def test_meta_contains_stage(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value=self._mock_run_chat(),
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
                draft_text="draft",
                review_text="review",
            )
        self.assertEqual(result["meta"]["stage"], "reflection_loop")

    def test_meta_used_context_false_when_no_context(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value=self._mock_run_chat(),
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
                draft_text="draft",
                review_text="review",
                context=None,
            )
        self.assertFalse(result["meta"]["used_context"])

    def test_meta_used_context_true_when_context_provided(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value=self._mock_run_chat(),
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
                draft_text="draft",
                review_text="review",
                context="Extra context here",
            )
        self.assertTrue(result["meta"]["used_context"])

    def test_ok_false_when_chat_fails(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value={"ok": False, "answer": "", "meta": {}, "warnings": ["error"]},
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
                draft_text="draft",
                review_text="review",
            )
        self.assertFalse(result["ok"])

    def test_warnings_passed_through(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value={"ok": True, "answer": "ok", "meta": {}, "warnings": ["slow"]},
        ):
            result = rl_rt.run_reflection_loop(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
                draft_text="draft",
                review_text="review",
            )
        self.assertIn("slow", result["warnings"])

    def test_result_has_required_keys(self) -> None:
        with patch(
            "app.application.chat_service.runtime.run_chat",
            return_value=self._mock_run_chat(),
        ):
            result = rl_rt.run_reflection_loop("m", "", "q", "d", "r")
        for key in ("ok", "answer", "meta", "warnings"):
            self.assertIn(key, result)


# ─────────────────────────────────────────────────────────────────────────────
# plugins — with patched PLUGINS_DIR and _CONFIG_FILE
# ─────────────────────────────────────────────────────────────────────────────

class PluginsListTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp = Path(self._tmpdir.name).resolve()
        self._orig_dir = pl_rt.PLUGINS_DIR
        self._orig_cfg = pl_rt._CONFIG_FILE
        self._orig_plugins = dict(pl_rt._plugins)
        self._orig_states = dict(pl_rt._plugin_states)
        pl_rt.PLUGINS_DIR = self._tmp / "plugins"
        pl_rt.PLUGINS_DIR.mkdir()
        pl_rt._CONFIG_FILE = self._tmp / "plugins_config.json"
        # Start fresh
        pl_rt._plugins = {}
        pl_rt._plugin_states = {}

    def tearDown(self) -> None:
        pl_rt.PLUGINS_DIR = self._orig_dir
        pl_rt._CONFIG_FILE = self._orig_cfg
        pl_rt._plugins = self._orig_plugins
        pl_rt._plugin_states = self._orig_states
        self._tmpdir.cleanup()

    def _write_plugin(self, name: str, content: str = None) -> None:
        if content is None:
            content = (
                f"NAME = '{name}'\n"
                f"DESCRIPTION = 'Test plugin {name}'\n"
                f"CATEGORY = 'utility'\n"
                f"ICON = '🔧'\n"
                f"TRIGGERS = ['{name.lower()}']\n"
                f"HOOKS = {{}}\n"
                f"CONFIG = {{}}\n\n"
                f"def run(args):\n"
                f"    return {{'ok': True, 'output': 'result from {name}'}}\n"
            )
        (pl_rt.PLUGINS_DIR / f"{name}.py").write_text(content, encoding="utf-8")

    def test_list_plugins_empty_when_no_plugins(self) -> None:
        result = pl_rt.list_plugins()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_load_plugins_finds_written_plugin(self) -> None:
        self._write_plugin("hello_plugin")
        result = pl_rt.load_plugins()
        self.assertTrue(result["ok"])
        # "loaded" is a list of plugin names, not an int
        self.assertIsInstance(result["loaded"], list)
        self.assertGreater(len(result["loaded"]), 0)

    def test_list_plugins_after_load(self) -> None:
        self._write_plugin("my_tool")
        pl_rt.load_plugins()
        result = pl_rt.list_plugins()
        self.assertGreater(result["count"], 0)

    def test_get_plugin_info_found(self) -> None:
        self._write_plugin("calc_plugin")
        pl_rt.load_plugins()
        result = pl_rt.get_plugin_info("calc_plugin")
        self.assertTrue(result["ok"])
        self.assertIn("name", result)

    def test_get_plugin_info_not_found(self) -> None:
        pl_rt.load_plugins()
        result = pl_rt.get_plugin_info("nonexistent_xyz")
        self.assertFalse(result["ok"])

    def test_enable_plugin_ok(self) -> None:
        self._write_plugin("toggler")
        pl_rt.load_plugins()
        result = pl_rt.enable_plugin("toggler")
        self.assertTrue(result["ok"])
        self.assertTrue(result.get("enabled"))

    def test_disable_plugin_ok(self) -> None:
        self._write_plugin("toggler2")
        pl_rt.load_plugins()
        result = pl_rt.disable_plugin("toggler2")
        self.assertTrue(result["ok"])
        self.assertFalse(result.get("enabled"))

    def test_reload_plugins_ok(self) -> None:
        self._write_plugin("plugin_a")
        pl_rt.load_plugins()
        result = pl_rt.reload_plugins()
        self.assertTrue(result["ok"])

    def test_list_plugins_has_required_fields(self) -> None:
        self._write_plugin("inspector")
        pl_rt.load_plugins()
        result = pl_rt.list_plugins()
        if result["count"] > 0:
            plugin = result["plugins"][0]
            for key in ("name", "enabled"):
                self.assertIn(key, plugin)


if __name__ == "__main__":
    unittest.main()
