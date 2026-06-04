"""P9.3 commit 3 — code-agent / workflows / telegram wired to the shared routing.

code-agent has its own loop, so its routing+cap is unit-tested via
_resolve_code_route. workflows + telegram already call run_agent, so we only
assert their default fallback now uses the "auto" sentinel (resolver engages)
while an explicit model still passes through.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.agent_loop import _resolve_code_route  # noqa: E402


_ROUTE_MAP = {
    "code": ["qwen2.5-coder:7b", "qwen3:8b", "gemma3:4b"],
    "chat": ["gemma3:4b"],
}


def _profile(model: str, role: str = "code", *, cloud: bool = False,
             context_limit: int = 16384, timeout: int = 120) -> dict:
    return {
        "id": f"prof-{model}",
        "provider": "anthropic" if cloud else "ollama",
        "model": model,
        "role": role,
        "context_limit": context_limit,
        "timeout_seconds": timeout,
        "enabled": True,
        "cloud_consent_required": cloud,
    }


class CodeAgentRouteResolutionTest(unittest.TestCase):
    def _run(self, model, num_ctx, *, available, profile, monitoring_max):
        models_payload = {
            "ok": available is not None,
            "models": [{"name": m} for m in (available or [])],
        }
        with patch("app.infrastructure.llm.ollama_models.get_models", return_value=models_payload), \
             patch("app.core.config._get_route_map", return_value=_ROUTE_MAP), \
             patch("app.core.config._get_profile_for_role", return_value=profile), \
             patch("app.application.monitoring.runtime.ensure_agent_limit",
                   return_value=({"max_context_tokens": monitoring_max} if monitoring_max else None)):
            return _resolve_code_route(model, num_ctx)

    def test_auto_selects_code_profile_when_installed(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["code-pro:7b", "gemma3:4b"],
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "code-pro:7b")
        self.assertEqual(decision.source, "profile")
        self.assertEqual(decision.route, "code")
        self.assertEqual(decision.role, "code")

    def test_explicit_model_preserved(self):
        model, _effective, decision = self._run(
            "my-explicit:1b", 16384, available=["my-explicit:1b"],
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "my-explicit:1b")
        self.assertEqual(decision.source, "explicit")

    def test_monitoring_cap_applies(self):
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=4096,
        )
        self.assertEqual(effective, 4096)

    def test_profile_context_limit_applies(self):
        _model, effective, decision = self._run(
            "auto", 16384, available=["code-pro:7b"],
            profile=_profile("code-pro:7b", context_limit=2048), monitoring_max=16384,
        )
        self.assertEqual(decision.source, "profile")
        self.assertEqual(effective, 2048)

    def test_model_safe_ctx_does_not_cap_code_agent(self):
        # qwen2.5-coder:7b has MODEL_SAFE_CTX 6144, but code-agent must NOT apply it.
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=16384,
        )
        self.assertEqual(effective, 16384)

    def test_default_num_ctx_preserved_without_caps(self):
        _model, effective, _decision = self._run(
            "qwen2.5-coder:7b", 16384, available=["qwen2.5-coder:7b"],
            profile=None, monitoring_max=None,
        )
        self.assertEqual(effective, 16384)

    def test_unavailable_profile_falls_back_to_route_map(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["gemma3:4b"],  # profile model not installed
            profile=_profile("code-pro:7b"), monitoring_max=16384,
        )
        self.assertEqual(model, "gemma3:4b")
        self.assertEqual(decision.source, "route_map")

    def test_cloud_profile_skipped_without_consent(self):
        model, _effective, decision = self._run(
            "auto", 16384, available=["cloud-x", "qwen2.5-coder:7b"],
            profile=_profile("cloud-x", cloud=True), monitoring_max=16384,
        )
        self.assertEqual(model, "qwen2.5-coder:7b")
        self.assertEqual(decision.source, "route_map")
        self.assertTrue(decision.cloud_skipped)


class WorkflowStepModelFallbackTest(unittest.TestCase):
    def _run_step(self, config, run_context):
        from app.domain.workflows import step_executor

        captured: dict = {}

        def fake_run_agent(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "answer": "x", "meta": {}, "timeline": [], "tool_results": []}

        with patch("app.application.chat.runtime.run_agent", side_effect=fake_run_agent):
            step_executor._execute_agent_step(
                {"id": "s1", "agent_id": "", "config": config},
                {}, run_context, "run-1",
            )
        return captured

    def test_no_model_falls_back_to_auto(self):
        captured = self._run_step({}, {})
        self.assertEqual(captured["model_name"], "auto")

    def test_explicit_step_model_preserved(self):
        captured = self._run_step({"model_name": "qwen3:8b"}, {})
        self.assertEqual(captured["model_name"], "qwen3:8b")

    def test_run_context_model_preserved(self):
        captured = self._run_step({}, {"model_name": "mistral-nemo:latest"})
        self.assertEqual(captured["model_name"], "mistral-nemo:latest")


class TelegramModelFallbackTest(unittest.TestCase):
    def _process(self, config_model):
        from app.application.telegram import runtime as tg

        captured: dict = {}

        def fake_run_agent(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "answer": "hi"}

        cfg = {
            "model": config_model,
            "profile": "Универсальный",
            "use_memory": "false",
            "use_web_search": "false",
        }
        with patch.object(tg, "register_user"), \
             patch.object(tg, "is_user_allowed", return_value=True), \
             patch.object(tg, "log_message"), \
             patch.object(tg, "send_typing"), \
             patch.object(tg, "send_message"), \
             patch.object(tg, "get_config_value", side_effect=lambda key, default="": cfg.get(key, default)), \
             patch("app.application.chat.runtime.run_agent", side_effect=fake_run_agent):
            tg.process_message("token", {"chat": {"id": 1}, "from": {"username": "u"}, "text": "hi"})
        return captured

    def test_empty_model_falls_back_to_auto(self):
        captured = self._process("")
        self.assertEqual(captured["model_name"], "auto")

    def test_explicit_model_preserved(self):
        captured = self._process("qwen3:8b")
        self.assertEqual(captured["model_name"], "qwen3:8b")


class CodeAgentRouteDefaultsTest(unittest.TestCase):
    """P9.3 fixup: the real activation path — /api/code-agent request default
    model is the 'auto' sentinel, and the route forwards it."""

    def test_request_default_model_is_auto(self):
        from app.api.routes import code_agent_routes as routes
        self.assertEqual(routes.CodeAgentRequest(message="m", project_root="/p").model, "auto")
        self.assertEqual(routes.CodeAgentStreamRequest(message="m", project_root="/p").model, "auto")

    def test_run_route_passes_auto_when_model_omitted(self):
        from app.api.routes import code_agent_routes as routes
        captured: dict = {}

        def fake_run(**kwargs):
            captured.update(kwargs)
            return {"ok": True, "response": "", "steps": 0, "tool_calls": [],
                    "stop_reason": "done", "error": None}

        with patch.object(routes, "run_code_agent", side_effect=fake_run):
            routes.run(routes.CodeAgentRequest(message="m", project_root="/p"))
        self.assertEqual(captured["model"], "auto")

    def test_stream_route_passes_auto_when_model_omitted(self):
        import asyncio
        from app.api.routes import code_agent_routes as routes
        captured: dict = {}

        def fake_stream(**kwargs):
            captured.update(kwargs)
            return iter([{"type": "done", "ok": True, "steps": 0, "stop_reason": "done", "error": None}])

        with patch.object(routes, "stream_code_agent", side_effect=fake_stream):
            response = routes.stream(routes.CodeAgentStreamRequest(message="m", project_root="/p"))

            async def _drain():
                async for _chunk in response.body_iterator:
                    pass

            asyncio.run(_drain())
        self.assertEqual(captured["model"], "auto")


class SummarizeHistoryAutoTest(unittest.TestCase):
    """P9.3 fixup: the 'auto' sentinel must never reach Ollama as a literal
    model name from summarize-history; it is resolved through the code route."""

    def test_auto_model_resolved_before_chat(self):
        from app.application.code_agent import agent_loop
        captured: dict = {}

        def fake_chat(**kwargs):
            captured.update(kwargs)
            return {"message": {"content": "summary"}}

        with patch("app.infrastructure.llm.ollama_models.get_models", return_value={"ok": False, "models": []}), \
             patch("app.core.config._get_profile_for_role", return_value=None), \
             patch("app.core.config._get_route_map", return_value={"code": ["qwen2.5-coder:7b"]}), \
             patch("app.application.monitoring.runtime.ensure_agent_limit", return_value={"max_context_tokens": 16384}):
            result = agent_loop.summarize_history(
                messages=[{"role": "user", "content": "hello"},
                          {"role": "assistant", "content": "hi there"}],
                model="auto",
                num_ctx=8192,
                chat_fn=fake_chat,
            )
        self.assertTrue(result["ok"])
        self.assertNotEqual(captured.get("model"), "auto")
        self.assertEqual(captured.get("model"), "qwen2.5-coder:7b")


class CodeAgentEnsureLimitCapTest(unittest.TestCase):
    """P9.3 fixup: _resolve_code_route uses ensure_agent_limit, so the default
    max_context_tokens caps a too-large request to 16384 (vs reaching preflight
    uncapped and being blocked) even on a fresh monitoring DB with no row."""

    def test_request_above_default_cap_becomes_16384(self):
        import tempfile
        from app.application.monitoring import runtime as mon
        from app.application.code_agent.agent_loop import _resolve_code_route

        with tempfile.TemporaryDirectory() as tmp:
            orig_db = mon.DB_PATH
            orig_seed = mon._LIMIT_SEED_DONE
            mon.DB_PATH = Path(tmp) / "agent_monitor.db"
            mon._LIMIT_SEED_DONE = False
            mon._init_db()
            try:
                with patch("app.infrastructure.llm.ollama_models.get_models",
                           return_value={"ok": False, "models": []}):
                    _model, effective, _decision = _resolve_code_route("qwen2.5-coder:7b", 99999)
            finally:
                mon.DB_PATH = orig_db
                mon._LIMIT_SEED_DONE = orig_seed

        self.assertEqual(effective, 16384)


if __name__ == "__main__":
    unittest.main()
