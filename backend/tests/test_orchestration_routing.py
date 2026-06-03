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
    ModelRouteDecision,
    effective_context_limit,
    is_auto_route,
    pick_model_for_route,
    resolve_model_for_route,
    route_to_role,
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

    def test_default_model_is_explicit_direct_model(self):
        self.assertFalse(is_auto_route(DEFAULT_MODEL))

    def test_explicit_model_is_not_auto(self):
        self.assertFalse(is_auto_route("qwen2.5-coder:7b"))
        self.assertFalse(is_auto_route("gemma4:e2b"))
        self.assertFalse(is_auto_route("llama3.2:3b"))


class PickModelForRouteTest(unittest.TestCase):
    def setUp(self):
        # Patch the DB lookups so tests are deterministic: fixed route map and
        # no model profile (P9.3 profile step inert) -> pure route_map path.
        self.route_patcher = patch("app.core.config._get_route_map", return_value=_FAKE_ROUTE_MAP)
        self.profile_patcher = patch("app.core.config._get_profile_for_role", return_value=None)
        self.route_patcher.start()
        self.profile_patcher.start()

    def tearDown(self):
        self.route_patcher.stop()
        self.profile_patcher.stop()

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

    def test_default_model_does_not_route_via_orchestration(self):
        result = pick_model_for_route("code", DEFAULT_MODEL, available_models=["qwen2.5-coder:7b"])
        self.assertEqual(result, DEFAULT_MODEL)


def _profile(model: str, role: str = "fast", *, cloud: bool = False) -> dict:
    """Build a model_profiles row dict for patching _get_profile_for_role."""
    return {
        "id": f"p-{model}",
        "provider": "anthropic" if cloud else "ollama",
        "model": model,
        "role": role,
        "context_limit": 16384,
        "timeout_seconds": 30,
        "enabled": True,
        "cloud_consent_required": cloud,
    }


class RouteToRoleTest(unittest.TestCase):
    def test_known_routes_map_to_roles(self):
        self.assertEqual(route_to_role("chat"), "fast")
        self.assertEqual(route_to_role("research"), "strong")
        self.assertEqual(route_to_role("code"), "code")
        self.assertEqual(route_to_role("project"), "code")

    def test_unknown_or_empty_route_defaults_to_fast(self):
        self.assertEqual(route_to_role("nonexistent"), "fast")
        self.assertEqual(route_to_role(""), "fast")
        self.assertEqual(route_to_role(None), "fast")

    def test_route_is_case_insensitive(self):
        self.assertEqual(route_to_role("CODE"), "code")
        self.assertEqual(route_to_role("  Research "), "strong")


class ModelProfileRoutingTest(unittest.TestCase):
    """P9.3: an enabled profile for the route's role is step 2 — between the
    explicit user choice and the route_model_map — gated on model
    availability and cloud consent."""

    def setUp(self):
        self.route_patcher = patch("app.core.config._get_route_map", return_value=_FAKE_ROUTE_MAP)
        self.route_patcher.start()

    def tearDown(self):
        self.route_patcher.stop()

    def _patch_profile(self, profile):
        patcher = patch("app.core.config._get_profile_for_role", return_value=profile)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_enabled_profile_used_when_model_available(self):
        self._patch_profile(_profile("qwen2.5-coder:7b", role="code"))
        result = pick_model_for_route(
            "code", "auto", available_models=["gemma3:4b", "qwen2.5-coder:7b"]
        )
        self.assertEqual(result, "qwen2.5-coder:7b")

    def test_profile_overrides_route_map_first_candidate(self):
        # route 'research' route_map first = mistral-nemo, but profile says qwen3:8b.
        self._patch_profile(_profile("qwen3:8b", role="strong"))
        result = pick_model_for_route(
            "research", "auto", available_models=["mistral-nemo:latest", "qwen3:8b"]
        )
        self.assertEqual(result, "qwen3:8b")

    def test_unavailable_profile_model_falls_back_to_route_map(self):
        # profile model not installed -> bounded fallback to a route_map candidate.
        self._patch_profile(_profile("qwen2.5:4b", role="code"))
        result = pick_model_for_route(
            "code", "auto", available_models=["qwen2.5-coder:7b", "gemma3:4b"]
        )
        self.assertEqual(result, "qwen2.5-coder:7b")

    def test_profile_skipped_when_availability_unknown(self):
        # available_models=None -> cannot confirm availability -> route_map (back-compat).
        self._patch_profile(_profile("qwen2.5:4b", role="research"))
        result = pick_model_for_route("research", "auto")
        self.assertEqual(result, "mistral-nemo:latest")

    def test_cloud_profile_skipped_without_consent(self):
        # cloud_consent_required profile is skipped by the string API -> route_map.
        self._patch_profile(_profile("claude-sonnet-4-5", role="strong", cloud=True))
        result = pick_model_for_route(
            "research", "auto", available_models=["claude-sonnet-4-5", "mistral-nemo:latest"]
        )
        self.assertEqual(result, "mistral-nemo:latest")

    def test_explicit_choice_beats_profile(self):
        self._patch_profile(_profile("qwen2.5-coder:7b", role="code"))
        result = pick_model_for_route(
            "code", "gemma3:4b", available_models=["qwen2.5-coder:7b", "gemma3:4b"]
        )
        self.assertEqual(result, "gemma3:4b")

    def test_no_profile_uses_route_map(self):
        self._patch_profile(None)
        result = pick_model_for_route("code", "auto", available_models=["qwen2.5-coder:7b"])
        self.assertEqual(result, "qwen2.5-coder:7b")


class ResolveModelForRouteTest(unittest.TestCase):
    """The structured resolver underlying pick_model_for_route (P9.3 commit 2)."""

    def setUp(self):
        self.route_patcher = patch("app.core.config._get_route_map", return_value=_FAKE_ROUTE_MAP)
        self.route_patcher.start()

    def tearDown(self):
        self.route_patcher.stop()

    def _patch_profile(self, profile):
        patcher = patch("app.core.config._get_profile_for_role", return_value=profile)
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_explicit_source(self):
        self._patch_profile(None)
        decision = resolve_model_for_route("code", "gemma3:4b", ["gemma3:4b"])
        self.assertIsInstance(decision, ModelRouteDecision)
        self.assertEqual(decision.source, "explicit")
        self.assertEqual(decision.model, "gemma3:4b")
        self.assertEqual(decision.requested_model, "gemma3:4b")
        self.assertEqual(decision.role, "code")

    def test_profile_source_populates_metadata(self):
        self._patch_profile(_profile("qwen2.5-coder:7b", role="code"))
        decision = resolve_model_for_route("code", "auto", ["qwen2.5-coder:7b"])
        self.assertEqual(decision.source, "profile")
        self.assertEqual(decision.model, "qwen2.5-coder:7b")
        self.assertEqual(decision.profile_id, "p-qwen2.5-coder:7b")
        self.assertEqual(decision.provider, "ollama")
        self.assertEqual(decision.context_limit, 16384)
        self.assertEqual(decision.timeout_seconds, 30)

    def test_route_map_source_with_fallback_reason(self):
        self._patch_profile(_profile("qwen2.5:4b", role="code"))  # not installed
        decision = resolve_model_for_route("code", "auto", ["qwen2.5-coder:7b"])
        self.assertEqual(decision.source, "route_map")
        self.assertEqual(decision.model, "qwen2.5-coder:7b")
        self.assertEqual(decision.fallback_reason, "profile_model_unavailable")

    def test_no_profile_fallback_reason(self):
        self._patch_profile(None)
        decision = resolve_model_for_route("code", "auto", ["qwen2.5-coder:7b"])
        self.assertEqual(decision.source, "route_map")
        self.assertEqual(decision.fallback_reason, "no_profile_for_role")

    def test_availability_unknown_fallback_reason(self):
        self._patch_profile(_profile("qwen2.5:4b", role="research"))
        decision = resolve_model_for_route("research", "auto")
        self.assertEqual(decision.source, "route_map")
        self.assertEqual(decision.fallback_reason, "available_models_unknown")

    def test_cloud_skipped_flag(self):
        self._patch_profile(_profile("claude", role="strong", cloud=True))
        decision = resolve_model_for_route("research", "auto", ["claude", "mistral-nemo:latest"])
        self.assertEqual(decision.source, "route_map")
        self.assertTrue(decision.cloud_skipped)
        self.assertEqual(decision.fallback_reason, "cloud_consent_required")

    def test_cloud_used_with_consent(self):
        self._patch_profile(_profile("claude", role="strong", cloud=True))
        decision = resolve_model_for_route("research", "auto", ["claude"], cloud_consent=True)
        self.assertEqual(decision.source, "profile")
        self.assertEqual(decision.model, "claude")

    def test_pick_model_matches_resolve_model(self):
        self._patch_profile(None)
        self.assertEqual(
            pick_model_for_route("research", "auto", ["mistral-nemo:latest"]),
            resolve_model_for_route("research", "auto", ["mistral-nemo:latest"]).model,
        )


class EffectiveContextLimitTest(unittest.TestCase):
    def test_returns_requested_when_no_caps(self):
        self.assertEqual(effective_context_limit(8192), 8192)

    def test_capped_by_monitoring(self):
        self.assertEqual(effective_context_limit(99999, monitoring_max_context=8192), 8192)

    def test_capped_by_profile(self):
        self.assertEqual(
            effective_context_limit(99999, monitoring_max_context=16384, profile_context_limit=4096),
            4096,
        )

    def test_capped_by_known_model_safe_ctx(self):
        # qwen2.5-coder:7b -> 6144 in MODEL_SAFE_CTX
        self.assertEqual(effective_context_limit(32768, model="qwen2.5-coder:7b"), 6144)

    def test_unknown_model_not_cut_to_default(self):
        self.assertEqual(effective_context_limit(8192, model="totally-unknown:1b"), 8192)

    def test_never_increases_requested(self):
        self.assertEqual(effective_context_limit(2048, monitoring_max_context=8192), 2048)


if __name__ == "__main__":
    unittest.main()
