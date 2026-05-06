"""Tests for two previously uncovered modules:
  chat/stream_service — CachedStreamHit (frozen dataclass),
    build_stream_phase_event, build_selected_tools_phase_event,
    build_chat_meta, build_stream_done_event,
    prepare_cached_stream_hit (mock callbacks),
    finalize_stream_response (mock callbacks)
  multi_agent_chain/runtime — _clip, _is_llm_error (both pure)
"""
from __future__ import annotations

import sys
import unittest
from dataclasses import FrozenInstanceError
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.stream_service import (  # noqa: E402
    CachedStreamHit,
    build_stream_phase_event,
    build_selected_tools_phase_event,
    build_chat_meta,
    build_stream_done_event,
    prepare_cached_stream_hit,
    finalize_stream_response,
)
from app.application.multi_agent_chain.runtime import (  # noqa: E402
    _clip,
    _is_llm_error,
)


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — CachedStreamHit
# ─────────────────────────────────────────────────────────────────────────────

class CachedStreamHitTest(unittest.TestCase):
    def _make(self) -> CachedStreamHit:
        return CachedStreamHit(
            full_text="cached answer",
            done_event={"token": "", "done": True},
        )

    def test_creates_instance(self) -> None:
        hit = self._make()
        self.assertIsInstance(hit, CachedStreamHit)

    def test_full_text_field(self) -> None:
        hit = self._make()
        self.assertEqual(hit.full_text, "cached answer")

    def test_done_event_field(self) -> None:
        hit = self._make()
        self.assertEqual(hit.done_event["done"], True)

    def test_immutable_full_text(self) -> None:
        hit = self._make()
        with self.assertRaises(FrozenInstanceError):
            hit.full_text = "mutated"  # type: ignore[misc]

    def test_immutable_done_event(self) -> None:
        hit = self._make()
        with self.assertRaises(FrozenInstanceError):
            hit.done_event = {}  # type: ignore[misc]

    def test_equality(self) -> None:
        a = CachedStreamHit(full_text="x", done_event={"done": True})
        b = CachedStreamHit(full_text="x", done_event={"done": True})
        self.assertEqual(a, b)

    def test_inequality_different_text(self) -> None:
        a = CachedStreamHit(full_text="x", done_event={"done": True})
        b = CachedStreamHit(full_text="y", done_event={"done": True})
        self.assertNotEqual(a, b)


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — build_stream_phase_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildStreamPhaseEventTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        self.assertIsInstance(build_stream_phase_event(phase="loading"), dict)

    def test_has_token_empty_string(self) -> None:
        event = build_stream_phase_event(phase="loading")
        self.assertEqual(event["token"], "")

    def test_done_is_false(self) -> None:
        event = build_stream_phase_event(phase="loading")
        self.assertFalse(event["done"])

    def test_phase_stored(self) -> None:
        event = build_stream_phase_event(phase="searching")
        self.assertEqual(event["phase"], "searching")

    def test_message_included_when_provided(self) -> None:
        event = build_stream_phase_event(phase="searching", message="Ищу...")
        self.assertEqual(event["message"], "Ищу...")

    def test_message_absent_when_not_provided(self) -> None:
        event = build_stream_phase_event(phase="searching")
        self.assertNotIn("message", event)

    def test_full_text_included_when_provided(self) -> None:
        event = build_stream_phase_event(phase="done", full_text="answer")
        self.assertEqual(event["full_text"], "answer")

    def test_full_text_absent_when_not_provided(self) -> None:
        event = build_stream_phase_event(phase="done")
        self.assertNotIn("full_text", event)


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — build_selected_tools_phase_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildSelectedToolsPhaseEventTest(unittest.TestCase):
    def test_web_search_returns_searching_phase(self) -> None:
        event = build_selected_tools_phase_event(["web_search"])
        self.assertIsNotNone(event)
        self.assertEqual(event["phase"], "searching")  # type: ignore[index]

    def test_other_tool_returns_tools_phase(self) -> None:
        event = build_selected_tools_phase_event(["calculator"])
        self.assertIsNotNone(event)
        self.assertEqual(event["phase"], "tools")  # type: ignore[index]

    def test_empty_list_returns_none(self) -> None:
        result = build_selected_tools_phase_event([])
        self.assertIsNone(result)

    def test_web_search_phase_has_message(self) -> None:
        event = build_selected_tools_phase_event(["web_search"])
        self.assertIn("message", event)  # type: ignore[operator]

    def test_other_tool_phase_has_message(self) -> None:
        event = build_selected_tools_phase_event(["sql"])
        self.assertIn("message", event)  # type: ignore[operator]

    def test_web_search_takes_priority(self) -> None:
        event = build_selected_tools_phase_event(["sql", "web_search"])
        self.assertEqual(event["phase"], "searching")  # type: ignore[index]


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — build_chat_meta
# ─────────────────────────────────────────────────────────────────────────────

class BuildChatMetaTest(unittest.TestCase):
    def _meta(self, **overrides) -> dict[str, Any]:
        defaults: dict[str, Any] = dict(
            model_name="llama3",
            profile_name="default",
            route="chat",
            tools=[],
            run_id="r1",
            temporal={},
            web_plan={},
        )
        defaults.update(overrides)
        return build_chat_meta(**defaults)

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._meta(), dict)

    def test_model_name_stored(self) -> None:
        self.assertEqual(self._meta(model_name="gpt4")["model_name"], "gpt4")

    def test_profile_name_stored(self) -> None:
        self.assertEqual(self._meta(profile_name="work")["profile_name"], "work")

    def test_route_stored(self) -> None:
        self.assertEqual(self._meta(route="agent")["route"], "agent")

    def test_run_id_stored(self) -> None:
        self.assertEqual(self._meta(run_id="abc-123")["run_id"], "abc-123")

    def test_tools_stored(self) -> None:
        self.assertEqual(self._meta(tools=["web_search"])["tools"], ["web_search"])

    def test_cached_absent_by_default(self) -> None:
        self.assertNotIn("cached", self._meta())

    def test_cached_true_when_flagged(self) -> None:
        self.assertTrue(self._meta(cached=True)["cached"])

    def test_persona_absent_when_none(self) -> None:
        self.assertNotIn("persona", self._meta())

    def test_persona_present_when_provided(self) -> None:
        meta = self._meta(persona={"name": "Elira"})
        self.assertEqual(meta["persona"]["name"], "Elira")

    def test_identity_guard_none_when_not_changed(self) -> None:
        meta = self._meta(identity_guard={"changed": False, "text": "x"})
        self.assertIsNone(meta["identity_guard"])

    def test_identity_guard_stored_when_changed(self) -> None:
        meta = self._meta(identity_guard={"changed": True, "text": "x"})
        self.assertIsNotNone(meta["identity_guard"])

    def test_provenance_guard_none_when_not_changed(self) -> None:
        meta = self._meta(provenance_guard={"changed": False})
        self.assertIsNone(meta["provenance_guard"])

    def test_provenance_guard_stored_when_changed(self) -> None:
        meta = self._meta(provenance_guard={"changed": True, "text": "y"})
        self.assertIsNotNone(meta["provenance_guard"])


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — build_stream_done_event
# ─────────────────────────────────────────────────────────────────────────────

class BuildStreamDoneEventTest(unittest.TestCase):
    def _event(self) -> dict[str, Any]:
        return build_stream_done_event(
            full_text="hello world",
            meta={"model_name": "llama3"},
            timeline=[{"label": "start"}],
        )

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._event(), dict)

    def test_token_is_empty_string(self) -> None:
        self.assertEqual(self._event()["token"], "")

    def test_done_is_true(self) -> None:
        self.assertTrue(self._event()["done"])

    def test_full_text_stored(self) -> None:
        self.assertEqual(self._event()["full_text"], "hello world")

    def test_meta_stored(self) -> None:
        self.assertEqual(self._event()["meta"]["model_name"], "llama3")

    def test_timeline_stored(self) -> None:
        self.assertEqual(self._event()["timeline"], [{"label": "start"}])

    def test_empty_timeline(self) -> None:
        event = build_stream_done_event(full_text="x", meta={}, timeline=[])
        self.assertEqual(event["timeline"], [])


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — prepare_cached_stream_hit
# ─────────────────────────────────────────────────────────────────────────────

class PrepareCachedStreamHitTest(unittest.TestCase):
    def _call(self, cached_text: str = "cached answer") -> CachedStreamHit:
        timeline: list[dict] = []

        def append_timeline_func(tl, key, label, status, msg):
            tl.append({"key": key, "label": label})

        def apply_identity_guard_func(user_input, text, tl):
            return {"changed": False, "text": text}

        def apply_provenance_guard_func(user_input, text, tl):
            return {"changed": False, "text": text}

        def finalize_stream_success_func(**kwargs):
            return {
                "token": "",
                "done": True,
                "full_text": kwargs.get("full_text", ""),
            }

        return prepare_cached_stream_hit(
            cached_text=cached_text,
            raw_user_input="user question",
            timeline=timeline,
            append_timeline_func=append_timeline_func,
            apply_identity_guard_func=apply_identity_guard_func,
            apply_provenance_guard_func=apply_provenance_guard_func,
            finalize_stream_success_func=finalize_stream_success_func,
            history_service=None,
            run_id="r1",
            session_id="s1",
            profile_name="default",
            model_name="llama3",
            route="chat",
            temporal={},
            web_plan={},
            num_ctx=4096,
            agent_id="",
            source_agent_id="",
            selected_tools=[],
            started_at=0.0,
            monotonic_now_func=lambda: 1.0,
        )

    def test_returns_cached_stream_hit(self) -> None:
        result = self._call()
        self.assertIsInstance(result, CachedStreamHit)

    def test_full_text_preserved(self) -> None:
        result = self._call(cached_text="the cached answer")
        self.assertEqual(result.full_text, "the cached answer")

    def test_done_event_has_done_true(self) -> None:
        result = self._call()
        self.assertTrue(result.done_event["done"])

    def test_done_event_is_dict(self) -> None:
        result = self._call()
        self.assertIsInstance(result.done_event, dict)

    def test_empty_cached_text(self) -> None:
        result = self._call(cached_text="")
        self.assertEqual(result.full_text, "")


# ─────────────────────────────────────────────────────────────────────────────
# chat/stream_service — finalize_stream_response
# ─────────────────────────────────────────────────────────────────────────────

class FinalizeStreamResponseTest(unittest.TestCase):
    def _call(self, full_text: str = "final answer", should_cache: bool = False) -> dict:
        def should_cache_func(planner_input, route):
            return should_cache

        set_cached_calls: list = []

        def set_cached_func(planner_input, model_name, profile_name, text):
            set_cached_calls.append(text)

        def finalize_stream_success_func(**kwargs):
            return {"ok": True, "full_text": kwargs.get("full_text", ""), "token": "", "done": True}

        result = finalize_stream_response(
            planner_input="user input",
            route="chat",
            profile_name="default",
            model_name="llama3",
            raw_user_input="user input",
            full_text=full_text,
            selected_tools=[],
            temporal={},
            web_plan={},
            identity_guard=None,
            provenance_guard=None,
            history_service=None,
            run_id="r1",
            session_id="s1",
            num_ctx=4096,
            agent_id="",
            source_agent_id="",
            timeline=[],
            started_at=0.0,
            monotonic_now_func=lambda: 1.0,
            should_cache_func=should_cache_func,
            set_cached_func=set_cached_func,
            finalize_stream_success_func=finalize_stream_success_func,
        )
        return result

    def test_returns_dict(self) -> None:
        self.assertIsInstance(self._call(), dict)

    def test_ok_key_present(self) -> None:
        self.assertIn("ok", self._call())

    def test_full_text_in_result(self) -> None:
        result = self._call(full_text="my answer")
        self.assertEqual(result["full_text"], "my answer")

    def test_done_is_true(self) -> None:
        self.assertTrue(self._call()["done"])

    def test_no_cache_set_when_disabled(self) -> None:
        # should_cache=False → set_cached_func not called (no side effect to verify,
        # but result should still be fine)
        result = self._call(should_cache=False)
        self.assertIsNotNone(result)

    def test_cache_set_when_enabled_with_non_empty_text(self) -> None:
        set_called: list = []

        def set_cached_func(planner_input, model_name, profile_name, text):
            set_called.append(text)

        finalize_stream_response(
            planner_input="q",
            route="chat",
            profile_name="default",
            model_name="llama3",
            raw_user_input="q",
            full_text="non-empty answer",
            selected_tools=[],
            temporal={},
            web_plan={},
            identity_guard=None,
            provenance_guard=None,
            history_service=None,
            run_id="r1",
            session_id="s1",
            num_ctx=4096,
            agent_id="",
            source_agent_id="",
            timeline=[],
            started_at=0.0,
            monotonic_now_func=lambda: 1.0,
            should_cache_func=lambda *a: True,
            set_cached_func=set_cached_func,
            finalize_stream_success_func=lambda **kw: {"ok": True},
        )
        self.assertEqual(len(set_called), 1)
        self.assertEqual(set_called[0], "non-empty answer")

    def test_cache_not_set_when_full_text_blank(self) -> None:
        set_called: list = []

        finalize_stream_response(
            planner_input="q",
            route="chat",
            profile_name="default",
            model_name="llama3",
            raw_user_input="q",
            full_text="   ",  # blank → strip() is falsy
            selected_tools=[],
            temporal={},
            web_plan={},
            identity_guard=None,
            provenance_guard=None,
            history_service=None,
            run_id="r1",
            session_id="s1",
            num_ctx=4096,
            agent_id="",
            source_agent_id="",
            timeline=[],
            started_at=0.0,
            monotonic_now_func=lambda: 1.0,
            should_cache_func=lambda *a: True,
            set_cached_func=lambda *a: set_called.append(a),
            finalize_stream_success_func=lambda **kw: {"ok": True},
        )
        self.assertEqual(len(set_called), 0)


# ─────────────────────────────────────────────────────────────────────────────
# multi_agent_chain/runtime — _clip
# ─────────────────────────────────────────────────────────────────────────────

class ClipTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_clip("hello", 100), str)

    def test_short_text_unchanged(self) -> None:
        self.assertEqual(_clip("hello", 100), "hello")

    def test_long_text_truncated(self) -> None:
        result = _clip("A" * 200, 50)
        self.assertLessEqual(len(result), 50)

    def test_truncated_ends_with_ellipsis(self) -> None:
        result = _clip("A" * 200, 50)
        self.assertTrue(result.endswith("…"))

    def test_exact_limit_not_truncated(self) -> None:
        text = "A" * 50
        result = _clip(text, 50)
        self.assertEqual(result, text)

    def test_none_treated_as_empty(self) -> None:
        result = _clip(None, 10)  # type: ignore[arg-type]
        self.assertEqual(result, "")

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(_clip("", 10), "")

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = _clip("  hello  ", 100)
        self.assertEqual(result, "hello")

    def test_limit_one_with_content(self) -> None:
        result = _clip("abc", 1)
        self.assertLessEqual(len(result), 1)


# ─────────────────────────────────────────────────────────────────────────────
# multi_agent_chain/runtime — _is_llm_error
# ─────────────────────────────────────────────────────────────────────────────

class IsLlmErrorTest(unittest.TestCase):
    def test_returns_bool(self) -> None:
        self.assertIsInstance(_is_llm_error("hello"), bool)

    def test_normal_text_false(self) -> None:
        self.assertFalse(_is_llm_error("This is a normal answer"))

    def test_russian_error_prefix_true(self) -> None:
        self.assertTrue(_is_llm_error("[Ошибка LLM: connection refused]"))

    def test_english_error_prefix_true(self) -> None:
        self.assertTrue(_is_llm_error("[LLM ERROR: timeout]"))

    def test_empty_string_false(self) -> None:
        self.assertFalse(_is_llm_error(""))

    def test_none_false(self) -> None:
        self.assertFalse(_is_llm_error(None))  # type: ignore[arg-type]

    def test_partial_prefix_not_at_start_false(self) -> None:
        self.assertFalse(_is_llm_error("Note: [Ошибка LLM: ...]"))

    def test_whitespace_before_prefix_false(self) -> None:
        # strip() is applied so leading space before bracket → still False
        self.assertFalse(_is_llm_error("  text [Ошибка LLM: ...]"))

    def test_russian_prefix_with_content(self) -> None:
        self.assertTrue(_is_llm_error("[Ошибка LLM: Failed to connect to ollama]"))

    def test_english_prefix_with_content(self) -> None:
        self.assertTrue(_is_llm_error("[LLM ERROR: model not found]"))


if __name__ == "__main__":
    unittest.main()
