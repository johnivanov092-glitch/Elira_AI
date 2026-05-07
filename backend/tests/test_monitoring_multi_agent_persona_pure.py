"""Tests for pure helpers across three modules.

  application/monitoring/store.py   - dumps_json, loads_json,
                                       planner_tool_aliases
  application/workflows/multi_agent.py - _select_multi_agent_workflow_id,
                                          _step_answer,
                                          _build_multi_agent_timeline
  application/persona/store.py      - json_loads, json_dumps

All functions are pure (no DB, no HTTP, no FS side-effects).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.monitoring.store import (  # noqa: E402
    dumps_json as mon_dumps_json,
    loads_json as mon_loads_json,
    planner_tool_aliases,
)
from app.application.workflows.multi_agent import (  # noqa: E402
    _select_multi_agent_workflow_id,
    _step_answer,
    _build_multi_agent_timeline,
    MULTI_AGENT_FULL_WORKFLOW_ID,
    MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
    MULTI_AGENT_REFLECTION_WORKFLOW_ID,
    MULTI_AGENT_DEFAULT_WORKFLOW_ID,
)
from app.application.persona.store import (  # noqa: E402
    json_loads as persona_json_loads,
    json_dumps as persona_json_dumps,
)


# application/monitoring/store.py - dumps_json

class MonDumpsJsonTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(mon_dumps_json({"key": "val"}), str)

    def test_dict_serialized(self) -> None:
        result = mon_dumps_json({"key": "value"})
        self.assertIn('"key"', result)
        self.assertIn('"value"', result)

    def test_none_serialized_as_empty_dict(self) -> None:
        result = mon_dumps_json(None)
        self.assertEqual(result, "{}")

    def test_list_serialized(self) -> None:
        result = mon_dumps_json([1, 2, 3])
        self.assertIn("1", result)
        self.assertIn("2", result)

    def test_nested_dict(self) -> None:
        result = mon_dumps_json({"a": {"b": 1}})
        self.assertIn('"b"', result)

    def test_unicode_not_escaped(self) -> None:
        # ensure_ascii=False means non-ASCII passes through
        value = "caf\u00e9"
        result = mon_dumps_json({"key": value})
        self.assertIn(value, result)

    def test_empty_dict_serialized(self) -> None:
        self.assertEqual(mon_dumps_json({}), "{}")

    def test_integer_value(self) -> None:
        result = mon_dumps_json({"n": 42})
        self.assertIn("42", result)


# application/monitoring/store.py - loads_json

class MonLoadsJsonTest(unittest.TestCase):

    def test_valid_dict_parsed(self) -> None:
        result = mon_loads_json('{"a": 1}', {})
        self.assertEqual(result, {"a": 1})

    def test_valid_list_parsed(self) -> None:
        result = mon_loads_json("[1, 2, 3]", [])
        self.assertEqual(result, [1, 2, 3])

    def test_none_returns_default(self) -> None:
        result = mon_loads_json(None, "fallback")
        self.assertEqual(result, "fallback")

    def test_empty_string_returns_default(self) -> None:
        result = mon_loads_json("", {"default": True})
        self.assertEqual(result, {"default": True})

    def test_invalid_json_returns_default(self) -> None:
        result = mon_loads_json("not valid json", 42)
        self.assertEqual(result, 42)

    def test_roundtrip(self) -> None:
        data = {"x": [1, 2, 3], "y": {"z": True}}
        serialized = mon_dumps_json(data)
        self.assertEqual(mon_loads_json(serialized, {}), data)

    def test_default_preserved_on_failure(self) -> None:
        default = [1, 2, 3]
        result = mon_loads_json("{{{invalid", default)
        self.assertEqual(result, default)

    def test_zero_string_parsed(self) -> None:
        # "0" is valid JSON for the number 0
        result = mon_loads_json("0", 99)
        self.assertEqual(result, 0)


# application/monitoring/store.py - planner_tool_aliases

class PlannerToolAliasesTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(planner_tool_aliases(), list)

    def test_list_nonempty(self) -> None:
        self.assertGreater(len(planner_tool_aliases()), 0)

    def test_all_strings(self) -> None:
        for item in planner_tool_aliases():
            self.assertIsInstance(item, str)

    def test_contains_web_search(self) -> None:
        self.assertIn("web_search", planner_tool_aliases())

    def test_contains_memory_search(self) -> None:
        self.assertIn("memory_search", planner_tool_aliases())

    def test_contains_python_executor(self) -> None:
        self.assertIn("python_executor", planner_tool_aliases())

    def test_deterministic(self) -> None:
        self.assertEqual(planner_tool_aliases(), planner_tool_aliases())


# application/workflows/multi_agent.py - _select_multi_agent_workflow_id

class SelectMultiAgentWorkflowIdTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(
            _select_multi_agent_workflow_id(use_reflection=False, use_orchestrator=False),
            str,
        )

    def test_both_true_returns_full(self) -> None:
        result = _select_multi_agent_workflow_id(use_reflection=True, use_orchestrator=True)
        self.assertEqual(result, MULTI_AGENT_FULL_WORKFLOW_ID)

    def test_orch_only_returns_orchestrated(self) -> None:
        result = _select_multi_agent_workflow_id(use_reflection=False, use_orchestrator=True)
        self.assertEqual(result, MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID)

    def test_reflection_only_returns_reflection(self) -> None:
        result = _select_multi_agent_workflow_id(use_reflection=True, use_orchestrator=False)
        self.assertEqual(result, MULTI_AGENT_REFLECTION_WORKFLOW_ID)

    def test_both_false_returns_default(self) -> None:
        result = _select_multi_agent_workflow_id(use_reflection=False, use_orchestrator=False)
        self.assertEqual(result, MULTI_AGENT_DEFAULT_WORKFLOW_ID)

    def test_four_distinct_ids(self) -> None:
        ids = {
            MULTI_AGENT_FULL_WORKFLOW_ID,
            MULTI_AGENT_ORCHESTRATED_WORKFLOW_ID,
            MULTI_AGENT_REFLECTION_WORKFLOW_ID,
            MULTI_AGENT_DEFAULT_WORKFLOW_ID,
        }
        self.assertEqual(len(ids), 4)

    def test_result_nonempty(self) -> None:
        for r, o in [(True, True), (True, False), (False, True), (False, False)]:
            result = _select_multi_agent_workflow_id(use_reflection=r, use_orchestrator=o)
            self.assertGreater(len(result), 0)


# application/workflows/multi_agent.py - _step_answer

class StepAnswerTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_step_answer({}, "key"), str)

    def test_missing_key_returns_empty(self) -> None:
        self.assertEqual(_step_answer({}, "missing"), "")

    def test_answer_key_used(self) -> None:
        result = _step_answer({"k": {"answer": "The answer"}}, "k")
        self.assertEqual(result, "The answer")

    def test_output_answer_used(self) -> None:
        result = _step_answer({"k": {"output": {"answer": "from output"}}}, "k")
        self.assertEqual(result, "from output")

    def test_output_result_used_when_no_answer(self) -> None:
        result = _step_answer({"k": {"output": {"result": "from result"}}}, "k")
        self.assertEqual(result, "from result")

    def test_empty_dict_value_returns_empty(self) -> None:
        self.assertEqual(_step_answer({"k": {}}, "k"), "")

    def test_non_dict_item_stringified(self) -> None:
        result = _step_answer({"k": "raw string"}, "k")
        self.assertEqual(result, "raw string")

    def test_non_dict_step_results_returns_empty(self) -> None:
        self.assertEqual(_step_answer("not a dict", "k"), "")  # type: ignore[arg-type]

    def test_none_step_results_returns_empty(self) -> None:
        self.assertEqual(_step_answer(None, "k"), "")  # type: ignore[arg-type]

    def test_empty_answer_returns_empty_string(self) -> None:
        result = _step_answer({"k": {"answer": ""}}, "k")
        self.assertEqual(result, "")

    def test_falsy_non_dict_item_returns_empty(self) -> None:
        # None at the key resolves to empty string
        result = _step_answer({"k": None}, "k")
        self.assertEqual(result, "")


# application/workflows/multi_agent.py - _build_multi_agent_timeline

class BuildMultiAgentTimelineTest(unittest.TestCase):

    def _template(self, steps):
        return {"graph": {"steps": steps}}

    def test_returns_list(self) -> None:
        self.assertIsInstance(_build_multi_agent_timeline({}, {}), list)

    def test_empty_template_returns_empty(self) -> None:
        self.assertEqual(_build_multi_agent_timeline({}, {}), [])

    def test_no_steps_returns_empty(self) -> None:
        self.assertEqual(_build_multi_agent_timeline({"graph": {}}, {}), [])

    def test_no_matching_step_results_skipped(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {})
        self.assertEqual(result, [])

    def test_non_dict_result_skipped(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": "not a dict"})
        self.assertEqual(result, [])

    def test_ok_step_has_done_status(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True, "answer": "yes"}})
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["status"], "done")

    def test_failed_step_has_error_status(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": False, "answer": "err"}})
        self.assertEqual(result[0]["status"], "error")

    def test_agent_field_is_step_id(self) -> None:
        template = self._template([{"id": "agent-42", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True}})
        self.assertEqual(result[0]["agent"], "agent-42")

    def test_label_from_config(self) -> None:
        template = self._template([
            {"id": "a1", "save_as": "r1", "config": {"label": "My Agent"}}
        ])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True, "answer": "x"}})
        self.assertEqual(result[0]["label"], "My Agent")

    def test_label_falls_back_to_key(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True}})
        self.assertEqual(result[0]["label"], "r1")

    def test_length_reflects_answer_length(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True, "answer": "hello"}})
        self.assertEqual(result[0]["length"], 5)

    def test_multiple_steps_all_included(self) -> None:
        template = self._template([
            {"id": "a1", "save_as": "r1"},
            {"id": "a2", "save_as": "r2"},
        ])
        step_results = {
            "r1": {"ok": True, "answer": "A"},
            "r2": {"ok": True, "answer": "B"},
        }
        result = _build_multi_agent_timeline(template, step_results)
        self.assertEqual(len(result), 2)

    def test_each_item_has_required_keys(self) -> None:
        template = self._template([{"id": "a1", "save_as": "r1"}])
        result = _build_multi_agent_timeline(template, {"r1": {"ok": True}})
        for key in ("agent", "status", "label", "length"):
            self.assertIn(key, result[0])


# application/persona/store.py - json_dumps

class PersonaJsonDumpsTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(persona_json_dumps({}), str)

    def test_dict_serialized(self) -> None:
        result = persona_json_dumps({"key": "value"})
        self.assertIn('"key"', result)

    def test_list_serialized(self) -> None:
        result = persona_json_dumps([1, 2])
        self.assertIn("1", result)

    def test_unicode_not_escaped(self) -> None:
        # ensure_ascii=False
        value = "\u041f\u0440\u0438\u0432\u0435\u0442"
        result = persona_json_dumps({"name": value})
        self.assertIn(value, result)

    def test_empty_dict(self) -> None:
        self.assertEqual(persona_json_dumps({}), "{}")

    def test_integer(self) -> None:
        self.assertEqual(persona_json_dumps(42), "42")

    def test_bool_true(self) -> None:
        self.assertEqual(persona_json_dumps(True), "true")


# application/persona/store.py - json_loads

class PersonaJsonLoadsTest(unittest.TestCase):

    def test_valid_dict_parsed(self) -> None:
        result = persona_json_loads('{"a": 1}', {})
        self.assertEqual(result, {"a": 1})

    def test_valid_list_parsed(self) -> None:
        result = persona_json_loads("[true, false]", [])
        self.assertEqual(result, [True, False])

    def test_empty_string_returns_fallback(self) -> None:
        self.assertEqual(persona_json_loads("", "fallback"), "fallback")

    def test_none_returns_fallback(self) -> None:
        self.assertEqual(persona_json_loads(None, {"x": 1}), {"x": 1})

    def test_invalid_json_returns_fallback(self) -> None:
        self.assertEqual(persona_json_loads("{bad}", []), [])

    def test_fallback_is_deepcopied(self) -> None:
        original = {"key": "value"}
        result = persona_json_loads(None, original)
        result["new_key"] = "new_value"
        # Original should not be mutated
        self.assertNotIn("new_key", original)

    def test_roundtrip_with_dumps(self) -> None:
        data = {"level": {"nested": [1, 2]}}
        serialized = persona_json_dumps(data)
        self.assertEqual(persona_json_loads(serialized, {}), data)

    def test_zero_value_returns_zero(self) -> None:
        result = persona_json_loads("0", 99)
        self.assertEqual(result, 0)

    def test_false_value_returns_false(self) -> None:
        result = persona_json_loads("false", True)
        self.assertFalse(result)


if __name__ == "__main__":
    unittest.main()
