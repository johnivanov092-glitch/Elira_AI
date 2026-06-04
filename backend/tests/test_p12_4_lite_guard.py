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
from app.application.chat.entrypoint_sync import run_agent_impl  # noqa: E402
from app.application.chat.service import (  # noqa: E402
    OrchestrationBlocker,
    prepare_chat_execution,
)


class _History:
    def add_event(self, *_args, **_kwargs):
        return None


def _decision(model: str = "test-model"):
    return SimpleNamespace(
        model=model,
        route="chat",
        role="fast",
        source="explicit",
        provider=None,
        profile_id=None,
        context_limit=None,
        timeout_seconds=None,
        requested_model=model,
        fallback_reason=None,
        cloud_skipped=False,
    )


class PlannerLiteGuardTest(unittest.TestCase):
    def test_unknown_planner_tool_blocks_before_model_probe_or_preflight(self) -> None:
        available_models = Mock(side_effect=AssertionError("available_models should not be called"))
        preflight = Mock(side_effect=AssertionError("preflight should not be called"))

        with self.assertRaises(OrchestrationBlocker) as ctx:
            prepare_chat_execution(
                planner_input="do it",
                model_name="test-model",
                plan_runner=lambda _text: {"route": "chat", "tools": ["unknown_tool"]},
                use_memory=True,
                use_library=True,
                use_web_search=True,
                is_memory_command_func=lambda _text: False,
                resolve_model_for_route_func=lambda *_args, **_kw: _decision(),
                effective_context_limit_func=lambda value, **_kw: value,
                available_models_func=available_models,
                get_max_context_tokens_func=lambda _agent_id: None,
                record_metric_func=lambda **_kw: None,
                history_service=_History(),
                run_id="run-lite-guard",
                extract_and_save_func=lambda _text: [],
                preflight_or_raise_func=preflight,
                agent_id="builtin-universal",
                num_ctx=8192,
                streaming=False,
            )

        self.assertEqual(ctx.exception.reason, "unknown_planner_tool")
        self.assertEqual(ctx.exception.details["unknown_tools"], ["unknown_tool"])
        available_models.assert_not_called()
        preflight.assert_not_called()

    def test_sync_entrypoint_returns_structured_blocker_without_llm(self) -> None:
        blocker = OrchestrationBlocker(
            reason="unknown_planner_tool",
            message="Planner selected unknown tool(s): unknown_tool",
            details={"route": "chat", "unknown_tools": ["unknown_tool"], "known_tools": ["web_search"]},
        )
        run_chat = Mock(return_value={"ok": True, "answer": "should not run"})
        record_metric = Mock()
        finalize_failure = Mock()
        registry_run = Mock()
        timeline: list[dict] = []

        def append_timeline(tl, step, title, status, detail):
            tl.append({"step": step, "title": title, "status": status, "detail": detail})

        bootstrap = SimpleNamespace(
            history=[],
            disabled_skills=set(),
            timeline=timeline,
            tool_results=[],
            planner=SimpleNamespace(plan=lambda _query: {}),
            raw_user_input="do it",
            planner_input="do it",
            run={"run_id": "run-lite-guard"},
        )

        deps = ChatAgentDeps(
            history_service=object(),
            planner_factory=lambda: object(),
            resolve_effective_agent_id_func=lambda **_kw: "builtin-universal",
            resolve_agent_func=None,
            bootstrap_chat_run_func=lambda **_kw: bootstrap,
            prepare_chat_execution_func=Mock(side_effect=blocker),
            prepare_chat_prompt_func=lambda **_kw: None,
            trim_history_func=lambda history, _max: history,
            strip_frontend_project_context_func=lambda text: text,
            emit_agent_os_event_func=lambda **_kw: None,
            collect_context_func=lambda **_kw: "",
            build_prompt_func=lambda *_args, **_kw: "",
            compose_human_style_rules_func=lambda _temporal: "",
            get_and_clear_attachments_func=lambda: "",
            has_generated_file_attachments_func=lambda: False,
            build_task_context_func=lambda _route, _tools: "",
            append_timeline_func=append_timeline,
            enrich_context_with_memory_func=lambda **kw: (kw.get("context", ""), 0),
            run_chat_func=run_chat,
            run_chat_stream_func=lambda **_kw: iter(()),
            run_reflection_loop_func=lambda **_kw: {"answer": ""},
            apply_response_guards_func=lambda **_kw: SimpleNamespace(
                text=_kw["text"],
                changed=False,
                identity_guard=None,
                provenance_guard=None,
            ),
            apply_identity_guard_func=lambda *_args, **_kw: {"changed": False},
            apply_provenance_guard_func=lambda *_args, **_kw: {"changed": False},
            maybe_generate_files_func=lambda *_args, **_kw: "",
            maybe_auto_exec_python_func=lambda user_input, answer, timeline, enabled=True: answer,
            finalize_chat_success_func=lambda **_kw: {},
            finalize_chat_failure_func=finalize_failure,
            finalize_stream_success_func=lambda **_kw: {},
            finalize_stream_response_func=lambda **_kw: {},
            build_stream_phase_event_func=lambda **kw: {"token": "", "done": False, **kw},
            build_selected_tools_phase_event_func=lambda _tools: None,
            iter_text_stream_events_func=lambda text: iter([{"token": text, "done": False}]),
            prepare_cached_stream_hit_func=lambda **_kw: None,
            record_registry_agent_run_func=registry_run,
            is_memory_command_func=lambda _text: False,
            resolve_model_for_route_func=lambda *_args, **_kw: _decision(),
            effective_context_limit_func=lambda value, **_kw: value,
            available_models_func=lambda: None,
            get_max_context_tokens_func=lambda _agent_id: None,
            record_metric_func=record_metric,
            extract_and_save_func=lambda _text: [],
            preflight_or_raise_func=lambda **_kw: None,
            should_cache_func=lambda *_args, **_kw: False,
            get_cached_func=lambda *_args, **_kw: None,
            set_cached_func=lambda *_args, **_kw: None,
            get_relevant_context_func=lambda *_args, **_kw: "",
            get_rag_context_func=lambda *_args, **_kw: "",
            has_rag=False,
            reflection_routes=set(),
            max_history_pairs=10,
            file_trigger_words=(),
            file_trigger_excel=(),
        )

        result = run_agent_impl(
            deps=deps,
            model_name="test-model",
            profile_name="Universal",
            user_input="do it",
        )

        self.assertFalse(result["ok"])
        self.assertIn('"blocked":true', result["answer"])
        self.assertEqual(result["meta"]["orchestration_blocker"]["reason"], "unknown_planner_tool")
        run_chat.assert_not_called()
        finalize_failure.assert_called_once()
        registry_run.assert_called_once()
        self.assertEqual(record_metric.call_args.kwargs["metric_type"], "orchestration.blocked")


if __name__ == "__main__":
    unittest.main()
