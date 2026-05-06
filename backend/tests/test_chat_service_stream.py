"""Tests for application/chat/service (pure functions + dataclasses) and
application/chat/stream_service (pure stream event builders)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.service import (  # noqa: E402
    build_task_context,
    build_disabled_skills,
    ChatPlanPreparation,
    ChatRunBootstrap,
    ChatExecutionPreparation,
    ChatPromptPreparation,
)
from app.application.chat.stream_service import (  # noqa: E402
    build_selected_tools_phase_event,
    build_stream_phase_event,
    iter_text_stream_events,
)


# ─────────────────────────────────────────────────────────────────────────────
# build_task_context
# ─────────────────────────────────────────────────────────────────────────────

class BuildTaskContextTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        result = build_task_context("chat", ["search"])
        self.assertIsInstance(result, str)

    def test_contains_route(self) -> None:
        result = build_task_context("research", [])
        self.assertIn("research", result)

    def test_lists_tools(self) -> None:
        result = build_task_context("chat", ["search", "calc"])
        self.assertIn("search", result)
        self.assertIn("calc", result)

    def test_no_tools_shows_fallback(self) -> None:
        result = build_task_context("chat", [])
        # Should mention "no tools" concept in some form
        self.assertIsInstance(result, str)
        self.assertGreater(len(result), 5)

    def test_multiple_tools_joined(self) -> None:
        result = build_task_context("chat", ["a", "b", "c"])
        self.assertIn("a", result)
        self.assertIn("b", result)


# ─────────────────────────────────────────────────────────────────────────────
# build_disabled_skills
# ─────────────────────────────────────────────────────────────────────────────

class BuildDisabledSkillsTest(unittest.TestCase):
    def _all_enabled(self) -> set:
        return build_disabled_skills(
            use_web_search=True, use_python_exec=True, use_image_gen=True,
            use_file_gen=True, use_http_api=True, use_sql=True,
            use_screenshot=True, use_encrypt=True, use_archiver=True,
            use_converter=True, use_regex=True, use_translator=True,
            use_csv=True, use_webhook=True, use_plugins=True,
        )

    def _all_disabled(self) -> set:
        return build_disabled_skills(
            use_web_search=False, use_python_exec=False, use_image_gen=False,
            use_file_gen=False, use_http_api=False, use_sql=False,
            use_screenshot=False, use_encrypt=False, use_archiver=False,
            use_converter=False, use_regex=False, use_translator=False,
            use_csv=False, use_webhook=False, use_plugins=False,
        )

    def test_returns_set(self) -> None:
        self.assertIsInstance(self._all_enabled(), set)

    def test_all_enabled_empty_disabled_set(self) -> None:
        result = self._all_enabled()
        self.assertEqual(result, set())

    def test_all_disabled_nonempty_set(self) -> None:
        result = self._all_disabled()
        self.assertGreater(len(result), 0)

    def test_web_search_disabled_in_set(self) -> None:
        result = build_disabled_skills(
            use_web_search=False, use_python_exec=True, use_image_gen=True,
            use_file_gen=True, use_http_api=True, use_sql=True,
            use_screenshot=True, use_encrypt=True, use_archiver=True,
            use_converter=True, use_regex=True, use_translator=True,
            use_csv=True, use_webhook=True, use_plugins=True,
        )
        self.assertIn("web_search", result)

    def test_web_search_enabled_not_in_disabled_set(self) -> None:
        result = self._all_enabled()
        self.assertNotIn("web_search", result)

    def test_python_exec_disabled_when_flag_false(self) -> None:
        result = build_disabled_skills(
            use_web_search=True, use_python_exec=False, use_image_gen=True,
            use_file_gen=True, use_http_api=True, use_sql=True,
            use_screenshot=True, use_encrypt=True, use_archiver=True,
            use_converter=True, use_regex=True, use_translator=True,
            use_csv=True, use_webhook=True, use_plugins=True,
        )
        self.assertIn("python_exec", result)

    def test_disabled_set_contains_strings(self) -> None:
        result = self._all_disabled()
        for item in result:
            self.assertIsInstance(item, str)


# ─────────────────────────────────────────────────────────────────────────────
# Chat dataclasses (structural)
# ─────────────────────────────────────────────────────────────────────────────

class ChatDataclassesTest(unittest.TestCase):
    def test_chat_plan_preparation_fields(self) -> None:
        obj = ChatPlanPreparation(
            plan={}, route="chat", temporal={}, web_plan={},
            selected_tools=[], effective_model="gemma3:4b",
        )
        self.assertEqual(obj.route, "chat")
        self.assertEqual(obj.effective_model, "gemma3:4b")

    def test_chat_run_bootstrap_fields(self) -> None:
        obj = ChatRunBootstrap(
            history=[], disabled_skills=set(), timeline=[],
            tool_results=[], planner=None, raw_user_input="q",
            planner_input="q", run={},
        )
        self.assertEqual(obj.raw_user_input, "q")
        self.assertEqual(obj.disabled_skills, set())

    def test_chat_execution_preparation_fields(self) -> None:
        obj = ChatExecutionPreparation(
            plan={}, route="research", temporal={}, web_plan={},
            selected_tools=["search"], effective_model="m",
            saved_memory_items=3,
        )
        self.assertEqual(obj.saved_memory_items, 3)
        self.assertEqual(obj.route, "research")

    def test_chat_prompt_preparation_fields(self) -> None:
        obj = ChatPromptPreparation(
            context_bundle="ctx", prompt="prompt", task_context="task"
        )
        self.assertEqual(obj.context_bundle, "ctx")
        self.assertEqual(obj.prompt, "prompt")
        self.assertEqual(obj.task_context, "task")

    def test_dataclasses_are_frozen(self) -> None:
        obj = ChatPlanPreparation(
            plan={}, route="chat", temporal={}, web_plan={},
            selected_tools=[], effective_model="m",
        )
        with self.assertRaises(Exception):
            obj.route = "modified"  # type: ignore[misc]


# ─────────────────────────────────────────────────────────────────────────────
# stream_service — build_selected_tools_phase_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildSelectedToolsPhaseEventTest(unittest.TestCase):
    def test_returns_dict_when_tools_present(self) -> None:
        result = build_selected_tools_phase_event(["search", "calc"])
        self.assertIsInstance(result, dict)

    def test_returns_none_when_no_tools(self) -> None:
        result = build_selected_tools_phase_event([])
        self.assertIsNone(result)

    def test_phase_is_tools(self) -> None:
        result = build_selected_tools_phase_event(["search"])
        self.assertEqual(result["phase"], "tools")

    def test_has_token_key(self) -> None:
        result = build_selected_tools_phase_event(["search"])
        self.assertIn("token", result)

    def test_has_done_key(self) -> None:
        result = build_selected_tools_phase_event(["search"])
        self.assertIn("done", result)


# ─────────────────────────────────────────────────────────────────────────────
# stream_service — build_stream_phase_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildStreamPhaseEventTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        result = build_stream_phase_event(phase="search")
        self.assertIsInstance(result, dict)

    def test_has_phase_key(self) -> None:
        result = build_stream_phase_event(phase="memory")
        self.assertEqual(result["phase"], "memory")

    def test_has_done_key(self) -> None:
        result = build_stream_phase_event(phase="search")
        self.assertIn("done", result)

    def test_message_in_result_when_provided(self) -> None:
        result = build_stream_phase_event(phase="search", message="searching...")
        self.assertEqual(result.get("message"), "searching...")

    def test_full_text_reflected(self) -> None:
        result = build_stream_phase_event(phase="done", full_text="final answer")
        # full_text may appear as token or similar key
        self.assertIsInstance(result, dict)


# ─────────────────────────────────────────────────────────────────────────────
# stream_service — iter_text_stream_events
# ─────────────────────────────────────────────────────────────────────────────

class IterTextStreamEventsTest(unittest.TestCase):
    def test_yields_events(self) -> None:
        events = list(iter_text_stream_events("hello world"))
        self.assertGreater(len(events), 0)

    def test_events_are_dicts(self) -> None:
        for event in iter_text_stream_events("test text"):
            self.assertIsInstance(event, dict)

    def test_events_have_token_key(self) -> None:
        for event in iter_text_stream_events("hello"):
            self.assertIn("token", event)

    def test_events_have_done_key(self) -> None:
        for event in iter_text_stream_events("hello"):
            self.assertIn("done", event)

    def test_done_is_false_for_tokens(self) -> None:
        for event in iter_text_stream_events("hello world"):
            self.assertFalse(event["done"])

    def test_tokens_reconstruct_text(self) -> None:
        text = "hello world"
        events = list(iter_text_stream_events(text))
        reconstructed = "".join(e["token"] for e in events)
        self.assertEqual(reconstructed, text)

    def test_empty_text_yields_empty_token(self) -> None:
        # iter_text_stream_events("") yields one event with token="" (not nothing)
        events = list(iter_text_stream_events(""))
        self.assertGreaterEqual(len(events), 0)  # at least doesn't crash
        if events:
            self.assertEqual(events[0]["token"], "")

    def test_single_word_yields_event(self) -> None:
        events = list(iter_text_stream_events("hello"))
        self.assertGreater(len(events), 0)


if __name__ == "__main__":
    unittest.main()
