"""Tests for pure helpers across four modules.

  application/workflows/store.py          - _as_bool, _loads, _dumps,
                                             normalize_graph
  application/agent_registry/sandbox.py    - _make_error, SandboxPolicyError
  application/tool_registry/runtime.py    - _noop_handler
  application/agent_registry/builtins.py  - iter_builtin_agent_defs

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

from app.application.workflows.store import (  # noqa: E402
    _as_bool,
    _loads,
    _dumps,
    normalize_graph,
)
from app.application.agent_registry.sandbox import (  # noqa: E402
    _make_error,
    SandboxPolicyError,
)
from app.application.tool_registry.runtime import _noop_handler  # noqa: E402
from app.application.agent_registry.builtins import (  # noqa: E402
    iter_builtin_agent_defs,
)


# workflows/store.py - _as_bool

class AsBoolTest(unittest.TestCase):

    def test_returns_bool(self) -> None:
        self.assertIsInstance(_as_bool(1), bool)

    def test_truthy_int_true(self) -> None:
        self.assertTrue(_as_bool(1))

    def test_zero_false(self) -> None:
        self.assertFalse(_as_bool(0))

    def test_none_false(self) -> None:
        self.assertFalse(_as_bool(None))

    def test_nonempty_string_true(self) -> None:
        self.assertTrue(_as_bool("yes"))

    def test_empty_string_false(self) -> None:
        self.assertFalse(_as_bool(""))

    def test_list_truthy(self) -> None:
        self.assertTrue(_as_bool([1, 2]))

    def test_empty_list_false(self) -> None:
        self.assertFalse(_as_bool([]))


# workflows/store.py - _loads

class LoadsTest(unittest.TestCase):

    def test_none_returns_default(self) -> None:
        self.assertEqual(_loads(None, {}), {})

    def test_empty_string_returns_default(self) -> None:
        self.assertEqual(_loads("", []), [])

    def test_invalid_json_returns_default(self) -> None:
        self.assertEqual(_loads("{bad}", 42), 42)

    def test_valid_dict_parsed(self) -> None:
        result = _loads('{"key": "val"}', {})
        self.assertEqual(result, {"key": "val"})

    def test_valid_list_parsed(self) -> None:
        result = _loads("[1, 2, 3]", [])
        self.assertEqual(result, [1, 2, 3])

    def test_zero_string_returns_zero(self) -> None:
        # "0" is valid JSON for the integer 0
        result = _loads("0", 99)
        self.assertEqual(result, 0)

    def test_false_string_returns_false(self) -> None:
        result = _loads("false", True)
        self.assertFalse(result)

    def test_roundtrip_with_dumps(self) -> None:
        data = {"a": [1, 2], "b": True}
        serialized = _dumps(data)
        self.assertEqual(_loads(serialized, {}), data)

    def test_default_preserved_on_type_error(self) -> None:
        result = _loads(12345, "fallback")
        # int is not valid string JSON -> TypeError -> default
        self.assertEqual(result, "fallback")


# workflows/store.py - _dumps

class DumpsTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(_dumps({}), str)

    def test_none_becomes_empty_dict(self) -> None:
        self.assertEqual(_dumps(None), "{}")

    def test_empty_dict(self) -> None:
        self.assertEqual(_dumps({}), "{}")

    def test_dict_serialized(self) -> None:
        result = _dumps({"key": "value"})
        self.assertIn('"key"', result)
        self.assertIn('"value"', result)

    def test_list_serialized(self) -> None:
        result = _dumps([1, 2, 3])
        self.assertIn("1", result)

    def test_nested_dict(self) -> None:
        result = _dumps({"a": {"b": 1}})
        self.assertIn('"b"', result)

    def test_unicode_preserved(self) -> None:
        value = "\u041f\u0440\u0438\u0432\u0435\u0442"
        result = _dumps({"name": value})
        self.assertIn(value, result)

    def test_integer(self) -> None:
        result = _dumps({"n": 42})
        self.assertIn("42", result)


# workflows/store.py - normalize_graph

class NormalizeGraphTest(unittest.TestCase):

    def _agent_step(self, step_id: str = "s1", agent_id: str = "builtin-universal") -> dict:
        return {"id": step_id, "type": "agent", "agent_id": agent_id}

    def _tool_step(self, step_id: str = "s1", tool_name: str = "web_search") -> dict:
        return {"id": step_id, "type": "tool", "tool_name": tool_name}

    # Error cases

    def test_empty_dict_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({})

    def test_no_steps_key_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"entry_step": "s1"})

    def test_empty_steps_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": []})

    def test_non_dict_step_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": ["not a dict"]})

    def test_missing_step_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [{"type": "agent", "agent_id": "x"}]})

    def test_duplicate_step_id_raises(self) -> None:
        step = self._agent_step("s1")
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [step, step]})

    def test_unknown_step_type_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [{"id": "s1", "type": "unknown"}]})

    def test_agent_step_missing_agent_id_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [{"id": "s1", "type": "agent"}]})

    def test_tool_step_missing_tool_name_raises(self) -> None:
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [{"id": "s1", "type": "tool"}]})

    def test_nonexistent_entry_step_raises(self) -> None:
        step = self._agent_step("s1")
        with self.assertRaises(ValueError):
            normalize_graph({"steps": [step], "entry_step": "does_not_exist"})

    # Success cases

    def test_returns_dict(self) -> None:
        step = self._agent_step()
        result = normalize_graph({"steps": [step]})
        self.assertIsInstance(result, dict)

    def test_entry_step_defaults_to_first(self) -> None:
        step = self._agent_step("s1")
        result = normalize_graph({"steps": [step]})
        self.assertEqual(result["entry_step"], "s1")

    def test_entry_step_explicit(self) -> None:
        step1 = self._agent_step("s1")
        step2 = self._agent_step("s2", "builtin-researcher")
        result = normalize_graph({"steps": [step1, step2], "entry_step": "s2"})
        self.assertEqual(result["entry_step"], "s2")

    def test_steps_key_present(self) -> None:
        step = self._agent_step()
        result = normalize_graph({"steps": [step]})
        self.assertIn("steps", result)

    def test_steps_count_preserved(self) -> None:
        step1 = self._agent_step("s1")
        step2 = self._tool_step("s2")
        result = normalize_graph({"steps": [step1, step2]})
        self.assertEqual(len(result["steps"]), 2)

    def test_normalized_step_has_required_keys(self) -> None:
        step = self._agent_step()
        result = normalize_graph({"steps": [step]})
        for key in ("id", "type", "agent_id", "tool_name", "input_map",
                    "save_as", "next", "on_error", "pause_after", "config"):
            self.assertIn(key, result["steps"][0])

    def test_tool_step_normalized(self) -> None:
        step = self._tool_step("t1", "memory_search")
        result = normalize_graph({"steps": [step]})
        self.assertEqual(result["steps"][0]["tool_name"], "memory_search")

    def test_pause_after_defaults_false(self) -> None:
        step = self._agent_step()
        result = normalize_graph({"steps": [step]})
        self.assertFalse(result["steps"][0]["pause_after"])

    def test_next_none_by_default(self) -> None:
        step = self._agent_step()
        result = normalize_graph({"steps": [step]})
        self.assertIsNone(result["steps"][0]["next"])


# agent_sandbox/runtime.py - _make_error / SandboxPolicyError

class MakeErrorTest(unittest.TestCase):

    def test_returns_sandbox_policy_error(self) -> None:
        err = _make_error(agent_id="a1", reason="rate_limit", message="Too many runs")
        self.assertIsInstance(err, SandboxPolicyError)

    def test_message_set(self) -> None:
        err = _make_error(agent_id="a1", reason="r", message="my message")
        self.assertEqual(err.message, "my message")

    def test_agent_id_set(self) -> None:
        err = _make_error(agent_id="builtin-universal", reason="r", message="msg")
        self.assertEqual(err.agent_id, "builtin-universal")

    def test_reason_set(self) -> None:
        err = _make_error(agent_id="a1", reason="context_limit_exceeded", message="m")
        self.assertEqual(err.reason, "context_limit_exceeded")

    def test_details_set(self) -> None:
        err = _make_error(agent_id="a1", reason="r", message="m", details={"k": "v"})
        self.assertEqual(err.details, {"k": "v"})

    def test_none_details_becomes_empty_dict(self) -> None:
        err = _make_error(agent_id="a1", reason="r", message="m", details=None)
        self.assertEqual(err.details, {})

    def test_str_returns_message(self) -> None:
        err = _make_error(agent_id="a1", reason="r", message="err message")
        self.assertEqual(str(err), "err message")

    def test_is_runtime_error(self) -> None:
        err = _make_error(agent_id="a1", reason="r", message="m")
        self.assertIsInstance(err, RuntimeError)

    def test_details_copied(self) -> None:
        original = {"x": 1}
        err = _make_error(agent_id="a1", reason="r", message="m", details=original)
        original["y"] = 2
        # details should not be affected by mutation of original
        self.assertNotIn("y", err.details)


# tool_registry/runtime.py - _noop_handler

class NoopHandlerTest(unittest.TestCase):

    def test_returns_dict(self) -> None:
        self.assertIsInstance(_noop_handler({}), dict)

    def test_ok_is_false(self) -> None:
        result = _noop_handler({})
        self.assertFalse(result["ok"])

    def test_error_key_present(self) -> None:
        result = _noop_handler({})
        self.assertIn("error", result)

    def test_error_is_nonempty_string(self) -> None:
        result = _noop_handler({})
        self.assertIsInstance(result["error"], str)
        self.assertGreater(len(result["error"]), 0)

    def test_result_consistent_for_any_args(self) -> None:
        r1 = _noop_handler({})
        r2 = _noop_handler({"key": "value", "other": 42})
        self.assertEqual(r1["ok"], r2["ok"])
        self.assertEqual(r1["error"], r2["error"])


# agent_registry/builtins.py - iter_builtin_agent_defs

class IterBuiltinAgentDefsTest(unittest.TestCase):

    def _defs(self) -> list:
        return list(iter_builtin_agent_defs())

    def test_returns_iterable(self) -> None:
        # Should be iterable without error
        result = list(iter_builtin_agent_defs())
        self.assertIsInstance(result, list)

    def test_nonempty(self) -> None:
        self.assertGreater(len(self._defs()), 0)

    def test_each_item_is_dict(self) -> None:
        for item in iter_builtin_agent_defs():
            self.assertIsInstance(item, dict)

    def test_each_has_id_key(self) -> None:
        for item in iter_builtin_agent_defs():
            self.assertIn("id", item)

    def test_each_has_role_key(self) -> None:
        for item in iter_builtin_agent_defs():
            self.assertIn("role", item)

    def test_each_has_name_key(self) -> None:
        for item in iter_builtin_agent_defs():
            self.assertIn("name", item)

    def test_ids_start_with_builtin(self) -> None:
        for item in iter_builtin_agent_defs():
            self.assertTrue(str(item["id"]).startswith("builtin-"))

    def test_deterministic_count(self) -> None:
        # Same number of defs on repeated calls
        self.assertEqual(len(self._defs()), len(self._defs()))

    def test_ids_are_unique(self) -> None:
        ids = [item["id"] for item in iter_builtin_agent_defs()]
        self.assertEqual(len(ids), len(set(ids)))

    def test_each_has_system_prompt_or_description(self) -> None:
        # Each builtin should have at least one of these
        for item in iter_builtin_agent_defs():
            has_either = "system_prompt" in item or "description" in item
            self.assertTrue(has_either)


if __name__ == "__main__":
    unittest.main()
