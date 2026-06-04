from __future__ import annotations

import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.entrypoint_models import ChatAgentDeps  # noqa: E402
from app.application.chat.entrypoint_stream import run_agent_stream_impl  # noqa: E402
from app.application.chat.entrypoint_sync import run_agent_impl  # noqa: E402
from app.application.chat.freshness_gate import evaluate_freshness_gate  # noqa: E402


_FRESH_TEMPORAL = {
    "mode": "hard",
    "requires_web": True,
    "freshness_sensitive": True,
}


class FreshnessGatePureTest(unittest.TestCase):
    def test_non_fresh_query_is_inert(self):
        result = evaluate_freshness_gate(
            temporal={"requires_web": False, "freshness_sensitive": False},
            selected_tools=[],
            tool_results=[],
        )
        self.assertTrue(result.ok)

    def test_web_disabled_blocks_fresh_query(self):
        result = evaluate_freshness_gate(
            temporal=_FRESH_TEMPORAL,
            selected_tools=[],
            tool_results=[],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "web_search_not_enabled")

    def test_web_selected_but_not_run_blocks(self):
        result = evaluate_freshness_gate(
            temporal=_FRESH_TEMPORAL,
            selected_tools=["web_search"],
            tool_results=[],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "web_search_not_run")

    def test_web_no_evidence_blocks(self):
        result = evaluate_freshness_gate(
            temporal=_FRESH_TEMPORAL,
            selected_tools=["web_search"],
            tool_results=[{"tool": "web_search", "result": {"found": 0, "news": 0, "fetched_pages": 0}}],
        )
        self.assertFalse(result.ok)
        self.assertEqual(result.reason, "web_search_no_evidence")

    def test_any_web_evidence_allows_llm_answer(self):
        result = evaluate_freshness_gate(
            temporal=_FRESH_TEMPORAL,
            selected_tools=["web_search"],
            tool_results=[{"tool": "web_search", "result": {"found": 2, "freshness_state": "standard_web"}}],
        )
        self.assertTrue(result.ok)


class FreshnessGateEntrypointTest(unittest.TestCase):
    def _deps(
        self,
        *,
        tool_results: list[dict] | None = None,
        stream: bool = False,
        should_cache: bool = False,
        cached_text: str | None = None,
    ):
        run_chat = Mock(return_value={"ok": True, "answer": "should not run"})
        run_chat_stream = Mock(return_value=iter(["should", " not", " run"]))
        get_cached = Mock(return_value=cached_text)
        timeline: list[dict] = []

        def append_timeline(tl, step, title, status, detail):
            tl.append({"step": step, "title": title, "status": status, "detail": detail})

        bootstrap = SimpleNamespace(
            history=[],
            disabled_skills=set(),
            timeline=timeline,
            tool_results=list(tool_results or []),
            planner=SimpleNamespace(plan=lambda _query: {}),
            raw_user_input="Какая сегодня цена нефти?",
            planner_input="Какая сегодня цена нефти?",
            run={"run_id": "run-fresh-gate"},
        )
        execution = SimpleNamespace(
            route="research",
            temporal=dict(_FRESH_TEMPORAL),
            web_plan={"is_multi_intent": False, "subqueries": []},
            selected_tools=["web_search"],
            effective_model="test-model",
            effective_num_ctx=8192,
            effective_timeout_seconds=None,
        )
        prompt = SimpleNamespace(context_bundle="", prompt="prompt", task_context="")

        deps = ChatAgentDeps(
            history_service=object(),
            planner_factory=lambda: object(),
            resolve_effective_agent_id_func=lambda **_kw: "builtin-researcher",
            resolve_agent_func=None,
            bootstrap_chat_run_func=lambda **_kw: bootstrap,
            prepare_chat_execution_func=lambda **_kw: execution,
            prepare_chat_prompt_func=lambda **_kw: prompt,
            trim_history_func=lambda history, _max: history,
            strip_frontend_project_context_func=lambda text: text,
            emit_agent_os_event_func=lambda **_kw: None,
            collect_context_func=lambda **_kw: "",
            build_prompt_func=lambda *_args, **_kw: "prompt",
            compose_human_style_rules_func=lambda _temporal: "",
            get_and_clear_attachments_func=lambda: "",
            has_generated_file_attachments_func=lambda: False,
            build_task_context_func=lambda _route, _tools: "",
            append_timeline_func=append_timeline,
            enrich_context_with_memory_func=lambda **kw: (kw.get("context", ""), 0),
            run_chat_func=run_chat,
            run_chat_stream_func=run_chat_stream,
            run_reflection_loop_func=lambda **_kw: {"answer": ""},
            apply_response_guards_func=lambda **_kw: SimpleNamespace(text=_kw["text"], changed=False, identity_guard=None, provenance_guard=None),
            apply_identity_guard_func=lambda *_args, **_kw: {"changed": False},
            apply_provenance_guard_func=lambda *_args, **_kw: {"changed": False},
            maybe_generate_files_func=lambda *_args, **_kw: "",
            maybe_auto_exec_python_func=lambda user_input, answer, timeline, enabled=True: answer,
            finalize_chat_success_func=lambda **_kw: {},
            finalize_chat_failure_func=lambda **_kw: None,
            finalize_stream_success_func=lambda **_kw: {},
            finalize_stream_response_func=lambda **_kw: {},
            build_stream_phase_event_func=lambda **kw: {"token": "", "done": False, **kw},
            build_selected_tools_phase_event_func=lambda tools: {"token": "", "done": False, "phase": "searching"} if "web_search" in tools else None,
            iter_text_stream_events_func=lambda text: iter([{"token": text, "done": False}]),
            prepare_cached_stream_hit_func=lambda **_kw: None,
            record_registry_agent_run_func=lambda **_kw: None,
            is_memory_command_func=lambda _text: False,
            resolve_model_for_route_func=lambda *_args, **_kw: SimpleNamespace(model="test-model"),
            effective_context_limit_func=lambda num_ctx, **_kw: num_ctx,
            available_models_func=lambda: None,
            get_max_context_tokens_func=lambda _agent_id: None,
            record_metric_func=Mock(),
            extract_and_save_func=lambda _text: [],
            preflight_or_raise_func=lambda **_kw: None,
            should_cache_func=lambda *_args, **_kw: should_cache,
            get_cached_func=get_cached,
            set_cached_func=lambda *_args, **_kw: None,
            get_relevant_context_func=lambda *_args, **_kw: "",
            get_rag_context_func=lambda *_args, **_kw: "",
            has_rag=False,
            reflection_routes=set(),
            max_history_pairs=10,
            file_trigger_words=(),
            file_trigger_excel=(),
        )
        return deps, run_chat_stream if stream else run_chat, get_cached

    def test_sync_fresh_query_without_web_evidence_skips_llm(self):
        deps, run_chat, _get_cached = self._deps()
        result = run_agent_impl(
            deps=deps,
            model_name="test-model",
            profile_name="Universal",
            user_input="Какая сегодня цена нефти?",
            use_memory=False,
            use_library=False,
        )
        self.assertTrue(result["ok"])
        self.assertIn("не был выполнен", result["answer"])
        run_chat.assert_not_called()
        self.assertEqual(result["meta"]["freshness_gate"]["reason"], "web_search_not_run")

    def test_stream_fresh_query_without_web_evidence_skips_llm(self):
        deps, run_chat_stream, _get_cached = self._deps(stream=True)
        events = list(
            run_agent_stream_impl(
                deps=deps,
                model_name="test-model",
                profile_name="Universal",
                user_input="Какая сегодня цена нефти?",
                use_memory=False,
                use_library=False,
            )
        )
        run_chat_stream.assert_not_called()
        self.assertTrue(events[-1]["done"])
        self.assertEqual(events[-1]["meta"]["freshness_gate"]["reason"], "web_search_not_run")

    def test_stream_fresh_query_skips_cache_before_gate(self):
        deps, run_chat_stream, get_cached = self._deps(
            stream=True,
            should_cache=True,
            cached_text="stale cached answer",
        )
        events = list(
            run_agent_stream_impl(
                deps=deps,
                model_name="test-model",
                profile_name="Universal",
                user_input="Какая сегодня цена нефти?",
                use_memory=False,
                use_library=False,
            )
        )
        get_cached.assert_not_called()
        run_chat_stream.assert_not_called()
        self.assertTrue(events[-1]["done"])
        self.assertNotEqual(events[-1]["full_text"], "stale cached answer")


if __name__ == "__main__":
    unittest.main()
