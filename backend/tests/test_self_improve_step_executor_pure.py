"""Tests for pure helpers across three previously zero-covered modules.

  domain/agents/orchestrator_context_runtime.py — build_tool_hint_text
  domain/agents/self_improve_runtime.py:
    build_self_improve_combined_context
    build_self_improve_critique_prompt
    should_self_improve
    build_self_improve_prompt
    is_self_improve_complete
  domain/workflows/step_executor.py:
    _resolve_path
    _resolve_input_expression
    _map_step_inputs
    _stringify_template_value
    _render_prompt_template
    _determine_profile_name
    _step_label
    _resolve_next_step

All functions are pure (no DB, no HTTP, no FS).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.domain.agents.orchestrator_context_runtime import (  # noqa: E402
    build_tool_hint_text,
)
from app.domain.agents.self_improve_runtime import (  # noqa: E402
    build_self_improve_combined_context,
    build_self_improve_critique_prompt,
    should_self_improve,
    build_self_improve_prompt,
    is_self_improve_complete,
)
from app.domain.workflows.step_executor import (  # noqa: E402
    _resolve_path,
    _resolve_input_expression,
    _map_step_inputs,
    _stringify_template_value,
    _render_prompt_template,
    _determine_profile_name,
    _step_label,
    _resolve_next_step,
)


# ─────────────────────────────────────────────────────────────────────────────
# orchestrator_context_runtime.py — build_tool_hint_text
# ─────────────────────────────────────────────────────────────────────────────

class BuildToolHintTextTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(build_tool_hint_text([]), str)

    def test_empty_prefs_returns_header_only(self) -> None:
        result = build_tool_hint_text([])
        self.assertIsInstance(result, str)

    def test_single_pref_tool_name_shown(self) -> None:
        prefs = [{"tool": "browser", "success_rate": 0.9, "uses": 5}]
        result = build_tool_hint_text(prefs)
        self.assertIn("browser", result)

    def test_success_rate_shown(self) -> None:
        prefs = [{"tool": "reasoning", "success_rate": 0.75, "uses": 10}]
        result = build_tool_hint_text(prefs)
        self.assertIn("0.75", result)

    def test_uses_count_shown(self) -> None:
        prefs = [{"tool": "browser", "success_rate": 0.8, "uses": 12}]
        result = build_tool_hint_text(prefs)
        self.assertIn("12", result)

    def test_multiple_prefs_all_included(self) -> None:
        prefs = [
            {"tool": "browser", "success_rate": 0.9, "uses": 5},
            {"tool": "reasoning", "success_rate": 0.7, "uses": 8},
        ]
        result = build_tool_hint_text(prefs)
        self.assertIn("browser", result)
        self.assertIn("reasoning", result)

    def test_computes_success_rate_from_runs_and_success(self) -> None:
        # No success_rate key → computed from success/runs
        prefs = [{"tool": "terminal", "success": 8, "runs": 10, "uses": 10}]
        result = build_tool_hint_text(prefs)
        self.assertIn("terminal", result)
        self.assertIn("0.8", result)

    def test_tool_name_key_fallback(self) -> None:
        prefs = [{"tool_name": "planner", "success_rate": 0.6, "uses": 3}]
        result = build_tool_hint_text(prefs)
        self.assertIn("planner", result)


# ─────────────────────────────────────────────────────────────────────────────
# self_improve_runtime.py — build_self_improve_combined_context
# ─────────────────────────────────────────────────────────────────────────────

class BuildSelfImproveCombinedContextTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(
            build_self_improve_combined_context(mem_ctx="", kb_ctx="", working_context=""),
            str,
        )

    def test_all_empty_produces_whitespace_string(self) -> None:
        result = build_self_improve_combined_context(mem_ctx="", kb_ctx="", working_context="")
        self.assertIsInstance(result, str)

    def test_mem_ctx_included(self) -> None:
        result = build_self_improve_combined_context(
            mem_ctx="memory data", kb_ctx="", working_context=""
        )
        self.assertIn("memory data", result)

    def test_kb_ctx_included(self) -> None:
        result = build_self_improve_combined_context(
            mem_ctx="", kb_ctx="kb data", working_context=""
        )
        self.assertIn("kb data", result)

    def test_working_context_included(self) -> None:
        result = build_self_improve_combined_context(
            mem_ctx="", kb_ctx="", working_context="working data"
        )
        self.assertIn("working data", result)

    def test_all_three_combined(self) -> None:
        result = build_self_improve_combined_context(
            mem_ctx="mem", kb_ctx="kb", working_context="work"
        )
        self.assertIn("mem", result)
        self.assertIn("kb", result)
        self.assertIn("work", result)

    def test_none_values_handled(self) -> None:
        result = build_self_improve_combined_context(
            mem_ctx=None, kb_ctx=None, working_context=None  # type: ignore[arg-type]
        )
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# self_improve_runtime.py — build_self_improve_critique_prompt
# ─────────────────────────────────────────────────────────────────────────────

class BuildSelfImproveCritiquePromptTest(unittest.TestCase):

    def _call(self, task="task", answer="answer", reflection=None, combined="context"):
        return build_self_improve_critique_prompt(
            task=task,
            answer=answer,
            reflection=reflection or {},
            combined_context=combined,
        )

    def test_returns_string(self) -> None:
        self.assertIsInstance(self._call(), str)

    def test_nonempty(self) -> None:
        self.assertTrue(len(self._call()) > 0)

    def test_contains_task(self) -> None:
        result = self._call(task="analyze python code")
        self.assertIn("analyze python code", result)

    def test_contains_answer(self) -> None:
        result = self._call(answer="my answer here")
        self.assertIn("my answer here", result)

    def test_contains_combined_context(self) -> None:
        result = self._call(combined="important context")
        self.assertIn("important context", result)

    def test_contains_json_schema_hint(self) -> None:
        result = self._call()
        self.assertIn("improve", result)
        self.assertIn("score", result)

    def test_different_tasks_different_prompts(self) -> None:
        r1 = self._call(task="task1")
        r2 = self._call(task="task2")
        self.assertNotEqual(r1, r2)

    def test_long_answer_truncated(self) -> None:
        long_answer = "X" * 20000
        result = self._call(answer=long_answer)
        # Answer is sliced at 9000 so the whole 20000 shouldn't appear
        self.assertLessEqual(len(result), 40000)


# ─────────────────────────────────────────────────────────────────────────────
# self_improve_runtime.py — should_self_improve
# ─────────────────────────────────────────────────────────────────────────────

class ShouldSelfImproveTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(should_self_improve(iteration=1, critique={}, reflection={}), bool)

    def test_iteration_1_no_critique_true(self) -> None:
        # critique.get("improve", iteration == 1) → iteration==1 is True
        result = should_self_improve(iteration=1, critique={}, reflection={})
        self.assertTrue(result)

    def test_iteration_2_no_critique_false(self) -> None:
        # critique.get("improve", False) → False; reflection has no retry
        result = should_self_improve(iteration=2, critique={}, reflection={})
        self.assertFalse(result)

    def test_critique_improve_true(self) -> None:
        result = should_self_improve(iteration=5, critique={"improve": True}, reflection={})
        self.assertTrue(result)

    def test_critique_improve_false(self) -> None:
        result = should_self_improve(iteration=1, critique={"improve": False}, reflection={})
        self.assertFalse(result)

    def test_reflection_needs_retry_overrides_to_true(self) -> None:
        result = should_self_improve(
            iteration=2,
            critique={"improve": False},
            reflection={"needs_retry": True, "complete": True},
        )
        self.assertTrue(result)

    def test_reflection_not_complete_overrides_to_true(self) -> None:
        result = should_self_improve(
            iteration=2,
            critique={"improve": False},
            reflection={"complete": False},
        )
        self.assertTrue(result)

    def test_non_dict_reflection_ignored(self) -> None:
        result = should_self_improve(iteration=2, critique={}, reflection="not a dict")
        self.assertFalse(result)

    def test_none_reflection_ignored(self) -> None:
        result = should_self_improve(iteration=2, critique={}, reflection=None)
        self.assertFalse(result)


# ─────────────────────────────────────────────────────────────────────────────
# self_improve_runtime.py — is_self_improve_complete
# ─────────────────────────────────────────────────────────────────────────────

class IsSelfImproveCompleteTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(is_self_improve_complete({}), bool)

    def test_non_dict_false(self) -> None:
        self.assertFalse(is_self_improve_complete("string"))

    def test_none_false(self) -> None:
        self.assertFalse(is_self_improve_complete(None))

    def test_empty_dict_true(self) -> None:
        # complete defaults True, answered defaults True, needs_retry defaults False
        self.assertTrue(is_self_improve_complete({}))

    def test_complete_false_gives_false(self) -> None:
        self.assertFalse(is_self_improve_complete({"complete": False}))

    def test_answered_false_gives_false(self) -> None:
        self.assertFalse(is_self_improve_complete({"answered": False}))

    def test_needs_retry_true_gives_false(self) -> None:
        self.assertFalse(is_self_improve_complete({"needs_retry": True}))

    def test_all_positive_true(self) -> None:
        self.assertTrue(is_self_improve_complete({
            "complete": True, "answered": True, "needs_retry": False
        }))

    def test_needs_retry_false_and_complete_true(self) -> None:
        self.assertTrue(is_self_improve_complete({"complete": True, "needs_retry": False}))


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _resolve_path
# ─────────────────────────────────────────────────────────────────────────────

class ResolvepathTest(unittest.TestCase):

    def test_simple_key(self) -> None:
        self.assertEqual(_resolve_path({"a": 1}, "a"), 1)

    def test_nested_key(self) -> None:
        self.assertEqual(_resolve_path({"a": {"b": 2}}, "a.b"), 2)

    def test_missing_key_returns_none(self) -> None:
        self.assertIsNone(_resolve_path({"a": 1}, "b"))

    def test_empty_path_returns_data(self) -> None:
        self.assertEqual(_resolve_path({"a": 1}, ""), {"a": 1})

    def test_deep_nesting(self) -> None:
        data = {"x": {"y": {"z": 42}}}
        self.assertEqual(_resolve_path(data, "x.y.z"), 42)

    def test_non_dict_mid_path_returns_none(self) -> None:
        data = {"a": "string"}
        self.assertIsNone(_resolve_path(data, "a.b"))

    def test_none_data_returns_none(self) -> None:
        self.assertIsNone(_resolve_path(None, "a"))


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _resolve_input_expression
# ─────────────────────────────────────────────────────────────────────────────

class ResolveInputExpressionTest(unittest.TestCase):

    _WI = {"name": "Alice"}
    _CTX = {"env": "prod"}
    _SR = {"step1": {"result": "done"}}

    def _call(self, expr, wi=None, ctx=None, sr=None):
        return _resolve_input_expression(
            expr,
            workflow_input=wi or self._WI,
            context=ctx or self._CTX,
            step_results=sr or self._SR,
        )

    def test_non_string_returned_as_is(self) -> None:
        self.assertEqual(self._call(42), 42)

    def test_none_returned_as_is(self) -> None:
        self.assertIsNone(self._call(None))

    def test_dollar_input_prefix(self) -> None:
        self.assertEqual(self._call("$.input.name"), "Alice")

    def test_dollar_input_full(self) -> None:
        self.assertEqual(self._call("$.input"), self._WI)

    def test_dollar_context_prefix(self) -> None:
        self.assertEqual(self._call("$.context.env"), "prod")

    def test_dollar_context_full(self) -> None:
        self.assertEqual(self._call("$.context"), self._CTX)

    def test_dollar_steps_prefix(self) -> None:
        self.assertEqual(self._call("$.steps.step1.result"), "done")

    def test_dollar_steps_full(self) -> None:
        self.assertEqual(self._call("$.steps"), self._SR)

    def test_plain_string_returned_as_is(self) -> None:
        self.assertEqual(self._call("hello world"), "hello world")

    def test_missing_path_returns_none(self) -> None:
        self.assertIsNone(self._call("$.input.missing"))


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _map_step_inputs
# ─────────────────────────────────────────────────────────────────────────────

class MapStepInputsTest(unittest.TestCase):

    def test_returns_dict(self) -> None:
        self.assertIsInstance(_map_step_inputs({}, workflow_input={}, context={}, step_results={}), dict)

    def test_no_input_map_returns_empty(self) -> None:
        result = _map_step_inputs({}, workflow_input={"a": 1}, context={}, step_results={})
        self.assertEqual(result, {})

    def test_maps_input_expression(self) -> None:
        step = {"input_map": {"name": "$.input.user"}}
        result = _map_step_inputs(
            step,
            workflow_input={"user": "Bob"},
            context={},
            step_results={},
        )
        self.assertEqual(result["name"], "Bob")

    def test_maps_literal_string(self) -> None:
        step = {"input_map": {"greeting": "hello"}}
        result = _map_step_inputs(step, workflow_input={}, context={}, step_results={})
        self.assertEqual(result["greeting"], "hello")

    def test_multiple_keys_all_mapped(self) -> None:
        step = {"input_map": {"a": "$.input.x", "b": "literal"}}
        result = _map_step_inputs(
            step,
            workflow_input={"x": 99},
            context={},
            step_results={},
        )
        self.assertEqual(result["a"], 99)
        self.assertEqual(result["b"], "literal")


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _stringify_template_value
# ─────────────────────────────────────────────────────────────────────────────

class StringifyTemplateValueTest(unittest.TestCase):

    def test_none_returns_empty_string(self) -> None:
        self.assertEqual(_stringify_template_value(None), "")

    def test_string_returned_as_is(self) -> None:
        self.assertEqual(_stringify_template_value("hello"), "hello")

    def test_int_converted_to_string(self) -> None:
        self.assertEqual(_stringify_template_value(42), "42")

    def test_float_converted(self) -> None:
        result = _stringify_template_value(3.14)
        self.assertIn("3.14", result)

    def test_dict_json_serialized(self) -> None:
        result = _stringify_template_value({"key": "value"})
        self.assertIn("key", result)
        self.assertIn("value", result)

    def test_list_json_serialized(self) -> None:
        result = _stringify_template_value([1, 2, 3])
        self.assertIn("1", result)
        self.assertIn("2", result)

    def test_bool_converted(self) -> None:
        result = _stringify_template_value(True)
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _render_prompt_template
# ─────────────────────────────────────────────────────────────────────────────

class RenderPromptTemplateTest(unittest.TestCase):

    def test_no_placeholders_unchanged(self) -> None:
        self.assertEqual(_render_prompt_template("hello world", {}), "hello world")

    def test_single_placeholder_filled(self) -> None:
        result = _render_prompt_template("Hello {name}!", {"name": "Alice"})
        self.assertEqual(result, "Hello Alice!")

    def test_missing_placeholder_filled_with_empty(self) -> None:
        result = _render_prompt_template("Hello {name}!", {})
        self.assertEqual(result, "Hello !")

    def test_multiple_placeholders(self) -> None:
        result = _render_prompt_template("{a} and {b}", {"a": "X", "b": "Y"})
        self.assertEqual(result, "X and Y")

    def test_dict_value_serialized(self) -> None:
        result = _render_prompt_template("data: {info}", {"info": {"key": "val"}})
        self.assertIn("key", result)

    def test_none_value_becomes_empty(self) -> None:
        result = _render_prompt_template("val: {x}", {"x": None})
        self.assertEqual(result, "val: ")

    def test_extra_values_ignored(self) -> None:
        result = _render_prompt_template("hi {name}", {"name": "Bob", "extra": "unused"})
        self.assertEqual(result, "hi Bob")


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _determine_profile_name
# ─────────────────────────────────────────────────────────────────────────────

class DetermineProfileNameTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_determine_profile_name("builtin-universal", {}), str)

    def test_config_profile_name_takes_priority(self) -> None:
        result = _determine_profile_name("builtin-universal", {"profile_name": "CustomProfile"})
        self.assertEqual(result, "CustomProfile")

    def test_builtin_universal_fallback(self) -> None:
        result = _determine_profile_name("builtin-universal", {})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_builtin_researcher_fallback(self) -> None:
        result = _determine_profile_name("builtin-researcher", {})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_unknown_agent_gets_default(self) -> None:
        result = _determine_profile_name("unknown-agent", {})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)

    def test_empty_config_profile_uses_fallback(self) -> None:
        result = _determine_profile_name("builtin-programmer", {"profile_name": ""})
        self.assertIsInstance(result, str)
        self.assertTrue(len(result) > 0)


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _step_label
# ─────────────────────────────────────────────────────────────────────────────

class StepLabelTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_step_label({}), str)

    def test_config_label_used(self) -> None:
        step = {"config": {"label": "My Step"}, "id": "s1"}
        self.assertEqual(_step_label(step), "My Step")

    def test_save_as_fallback(self) -> None:
        step = {"save_as": "result_key", "id": "s1"}
        self.assertEqual(_step_label(step), "result_key")

    def test_id_fallback(self) -> None:
        step = {"id": "step_one"}
        self.assertEqual(_step_label(step), "step_one")

    def test_empty_step(self) -> None:
        result = _step_label({})
        self.assertIsInstance(result, str)


# ─────────────────────────────────────────────────────────────────────────────
# step_executor.py — _resolve_next_step
# ─────────────────────────────────────────────────────────────────────────────

class ResolveNextStepTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_resolve_next_step({}, success=True), str)

    def test_no_next_key_empty_string(self) -> None:
        self.assertEqual(_resolve_next_step({}, success=True), "")

    def test_string_next_returned_directly(self) -> None:
        step = {"next": "step2"}
        self.assertEqual(_resolve_next_step(step, success=True), "step2")

    def test_list_next_on_success_resolved(self) -> None:
        step = {"next": [{"when": "on_success", "to": "happy_path"}]}
        self.assertEqual(_resolve_next_step(step, success=True), "happy_path")

    def test_list_next_on_failure_resolved(self) -> None:
        step = {"next": [{"when": "on_failure", "to": "error_handler"}]}
        self.assertEqual(_resolve_next_step(step, success=False), "error_handler")

    def test_always_transition_matches_both(self) -> None:
        step = {"next": [{"when": "always", "to": "always_step"}]}
        self.assertEqual(_resolve_next_step(step, success=True), "always_step")
        self.assertEqual(_resolve_next_step(step, success=False), "always_step")

    def test_on_error_fallback_for_failure(self) -> None:
        step = {"on_error": "error_step"}
        self.assertEqual(_resolve_next_step(step, success=False), "error_step")

    def test_no_matching_transition_empty(self) -> None:
        # on_success transition but failed
        step = {"next": [{"when": "on_success", "to": "happy"}]}
        result = _resolve_next_step(step, success=False)
        self.assertEqual(result, "")

    def test_string_next_stripped(self) -> None:
        step = {"next": "  step3  "}
        self.assertEqual(_resolve_next_step(step, success=True), "step3")


if __name__ == "__main__":
    unittest.main()
