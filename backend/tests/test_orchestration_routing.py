"""Tests for the auto-routing / orchestration sentinel logic."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.config import (  # noqa: E402
    DEFAULT_MODEL,
    is_auto_route,
    pick_model_for_route,
)


_FAKE_ROUTE_MAP = {
    "code":     ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "research": ["mistral-nemo:latest", "qwen3:8b"],
    "chat":     ["gemma4:e2b", "qwen3:8b"],
    "project":  ["qwen2.5-coder:7b", "gemma3:4b"],
}


class AutoRouteSentinelTest(unittest.TestCase):
    def test_empty_string_is_auto(self):
        self.assertTrue(is_auto_route(""))

    def test_none_is_auto(self):
        self.assertTrue(is_auto_route(None))

    def test_literal_auto_is_auto(self):
        self.assertTrue(is_auto_route("auto"))
        self.assertTrue(is_auto_route("AUTO"))
        self.assertTrue(is_auto_route("Auto"))

    def test_russian_auto_is_auto(self):
        self.assertTrue(is_auto_route("авто"))
        self.assertTrue(is_auto_route("АВТО"))

    def test_whitespace_only_is_auto(self):
        self.assertTrue(is_auto_route("   "))

    def test_legacy_default_model_is_auto(self):
        """Backwards compat: existing clients sending DEFAULT_MODEL still trigger orchestration."""
        self.assertTrue(is_auto_route(DEFAULT_MODEL))

    def test_explicit_model_is_not_auto(self):
        self.assertFalse(is_auto_route("qwen2.5-coder:7b"))
        self.assertFalse(is_auto_route("gemma4:e2b"))
        self.assertFalse(is_auto_route("llama3.2:3b"))


class PickModelForRouteTest(unittest.TestCase):
    def setUp(self):
        # Patch the BD lookup so tests are deterministic
        self.patcher = patch("app.core.config._get_route_map", return_value=_FAKE_ROUTE_MAP)
        self.patcher.start()

    def tearDown(self):
        self.patcher.stop()

    def test_explicit_model_bypasses_orchestration(self):
        """User picks qwen2.5-coder explicitly — they get qwen2.5-coder, route ignored."""
        result = pick_model_for_route("code", "qwen2.5-coder:7b", available_models=["qwen2.5-coder:7b"])
        self.assertEqual(result, "qwen2.5-coder:7b")

    def test_explicit_model_kept_even_when_route_map_disagrees(self):
        """Explicit user_model wins even if it's not in route_map[route] candidates."""
        # User asks gemma4:e2b for code task — we don't override
        result = pick_model_for_route("code", "gemma4:e2b", available_models=["gemma4:e2b"])
        self.assertEqual(result, "gemma4:e2b")

    def test_auto_empty_string_triggers_orchestration(self):
        result = pick_model_for_route("code", "", available_models=["qwen2.5-coder:7b", "gemma3:4b"])
        self.assertEqual(result, "qwen2.5-coder:7b")

    def test_auto_literal_triggers_orchestration(self):
        result = pick_model_for_route("code", "auto", available_models=["qwen2.5-coder:7b"])
        self.assertEqual(result, "qwen2.5-coder:7b")

    def test_auto_russian_triggers_orchestration(self):
        result = pick_model_for_route("research", "авто", available_models=["mistral-nemo:latest", "qwen3:8b"])
        self.assertEqual(result, "mistral-nemo:latest")

    def test_auto_cascade_to_second_candidate_when_first_missing(self):
        # qwen2.5-coder NOT in available — fall through to qwen3:8b
        result = pick_model_for_route("code", "auto", available_models=["qwen3:8b", "gemma3:4b"])
        self.assertEqual(result, "qwen3:8b")

    def test_auto_returns_first_candidate_when_no_available_list(self):
        result = pick_model_for_route("research", "auto")
        self.assertEqual(result, "mistral-nemo:latest")

    def test_auto_unknown_route_falls_back_to_chat(self):
        result = pick_model_for_route("nonexistent_route", "auto", available_models=["gemma4:e2b"])
        self.assertEqual(result, "gemma4:e2b")

    def test_legacy_default_model_still_routes_via_orchestration(self):
        """Existing chat code that sends DEFAULT_MODEL keeps working."""
        result = pick_model_for_route("code", DEFAULT_MODEL, available_models=["qwen2.5-coder:7b"])
        self.assertEqual(result, "qwen2.5-coder:7b")


if __name__ == "__main__":
    unittest.main()
