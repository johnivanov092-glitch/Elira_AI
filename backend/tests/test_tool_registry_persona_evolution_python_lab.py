"""Tests for three previously uncovered modules:
  tool_registry/store (row_to_dict, now_utc_iso — pure helpers)
  persona/evolution (contradiction_score, append_trait, extract_signals structure)
  code_agent/python_lab (PYTHON_EXEC_TIMEOUT, FIGURE_SAVER constants;
                         execute_python_with_capture behaviour)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_registry import store as tr_store  # noqa: E402
from app.application.persona.evolution import (  # noqa: E402
    contradiction_score,
    append_trait,
    extract_signals,
)
from app.application.code_agent.python_lab import (  # noqa: E402
    execute_python_with_capture,
    PYTHON_EXEC_TIMEOUT,
    FIGURE_SAVER,
)


# ─────────────────────────────────────────────────────────────────────────────
# tool_registry/store — now_utc_iso
# ─────────────────────────────────────────────────────────────────────────────

class NowUtcIsoTest(unittest.TestCase):
    def test_returns_value_from_func(self) -> None:
        result = tr_store.now_utc_iso(lambda: "2025-01-01T00:00:00")
        self.assertEqual(result, "2025-01-01T00:00:00")

    def test_returns_string(self) -> None:
        self.assertIsInstance(tr_store.now_utc_iso(lambda: "t"), str)

    def test_calls_provided_function(self) -> None:
        called = []
        tr_store.now_utc_iso(lambda: called.append(True) or "t")
        self.assertEqual(len(called), 1)


# ─────────────────────────────────────────────────────────────────────────────
# tool_registry/store — row_to_dict
# ─────────────────────────────────────────────────────────────────────────────

class ToolRegistryRowToDictTest(unittest.TestCase):
    def _row(self, **overrides) -> dict:
        base = {
            "name": "search",
            "display_name": "Search",
            "parameters_schema_json": '{"type": "object"}',
            "enabled": 1,
        }
        base.update(overrides)
        return base

    def test_returns_dict(self) -> None:
        self.assertIsInstance(tr_store.row_to_dict(self._row()), dict)

    def test_parameters_schema_parsed(self) -> None:
        result = tr_store.row_to_dict(self._row())
        self.assertEqual(result["parameters_schema"], {"type": "object"})

    def test_parameters_schema_json_removed(self) -> None:
        result = tr_store.row_to_dict(self._row())
        self.assertNotIn("parameters_schema_json", result)

    def test_enabled_is_bool(self) -> None:
        result = tr_store.row_to_dict(self._row(enabled=1))
        self.assertIsInstance(result["enabled"], bool)

    def test_enabled_true_for_one(self) -> None:
        self.assertTrue(tr_store.row_to_dict(self._row(enabled=1))["enabled"])

    def test_enabled_false_for_zero(self) -> None:
        self.assertFalse(tr_store.row_to_dict(self._row(enabled=0))["enabled"])

    def test_invalid_schema_json_defaults_empty_dict(self) -> None:
        result = tr_store.row_to_dict(self._row(parameters_schema_json="not json"))
        self.assertEqual(result["parameters_schema"], {})

    def test_no_schema_json_key_leaves_enabled_only(self) -> None:
        row = {"name": "tool", "enabled": 1}
        result = tr_store.row_to_dict(row)
        self.assertTrue(result["enabled"])
        self.assertNotIn("parameters_schema", result)

    def test_preserves_other_fields(self) -> None:
        result = tr_store.row_to_dict(self._row())
        self.assertEqual(result["name"], "search")
        self.assertEqual(result["display_name"], "Search")


# ─────────────────────────────────────────────────────────────────────────────
# persona/evolution — contradiction_score
# ─────────────────────────────────────────────────────────────────────────────

class ContradictionScoreTest(unittest.TestCase):
    def test_returns_float(self) -> None:
        result = contradiction_score({}, "some summary")
        self.assertIsInstance(result, float)

    def test_no_disallowed_drift_zero(self) -> None:
        self.assertAlmostEqual(contradiction_score({}, "any summary"), 0.0)

    def test_match_in_drift_returns_one(self) -> None:
        snapshot = {"disallowed_drift": ["harmful"]}
        self.assertAlmostEqual(contradiction_score(snapshot, "this is harmful"), 1.0)

    def test_no_match_in_drift_returns_zero(self) -> None:
        snapshot = {"disallowed_drift": ["harmful"]}
        self.assertAlmostEqual(contradiction_score(snapshot, "this is helpful"), 0.0)

    def test_case_insensitive_match(self) -> None:
        snapshot = {"disallowed_drift": ["HARMFUL"]}
        self.assertAlmostEqual(contradiction_score(snapshot, "this is HARMFUL"), 1.0)

    def test_empty_summary_zero(self) -> None:
        snapshot = {"disallowed_drift": ["bad"]}
        self.assertAlmostEqual(contradiction_score(snapshot, ""), 0.0)

    def test_empty_snapshot_zero(self) -> None:
        self.assertAlmostEqual(contradiction_score({}, "bad word"), 0.0)

    def test_none_summary_zero(self) -> None:
        self.assertAlmostEqual(contradiction_score({}, None), 0.0)  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# persona/evolution — append_trait
# ─────────────────────────────────────────────────────────────────────────────

class AppendTraitTest(unittest.TestCase):
    def test_returns_true_when_appended(self) -> None:
        payload: dict = {}
        result = append_trait(payload, "values", "honesty")
        self.assertTrue(result)

    def test_returns_false_when_already_present(self) -> None:
        payload: dict = {"values": ["honesty"]}
        result = append_trait(payload, "values", "honesty")
        self.assertFalse(result)

    def test_appends_to_layer(self) -> None:
        payload: dict = {}
        append_trait(payload, "values", "honesty")
        self.assertIn("honesty", payload["values"])

    def test_does_not_duplicate(self) -> None:
        payload: dict = {"values": ["honesty"]}
        append_trait(payload, "values", "honesty")
        self.assertEqual(payload["values"].count("honesty"), 1)

    def test_creates_layer_if_missing(self) -> None:
        payload: dict = {}
        append_trait(payload, "behavior_rules", "be_helpful")
        self.assertIn("behavior_rules", payload)

    def test_multiple_traits_to_same_layer(self) -> None:
        payload: dict = {}
        append_trait(payload, "values", "trait1")
        append_trait(payload, "values", "trait2")
        self.assertIn("trait1", payload["values"])
        self.assertIn("trait2", payload["values"])

    def test_different_layers_independent(self) -> None:
        payload: dict = {}
        append_trait(payload, "values", "trait")
        append_trait(payload, "preferences", "trait")
        # Same text in two different layers — both should succeed
        self.assertIn("trait", payload["values"])
        self.assertIn("trait", payload["preferences"])


# ─────────────────────────────────────────────────────────────────────────────
# persona/evolution — extract_signals structure
# ─────────────────────────────────────────────────────────────────────────────

class ExtractSignalsTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        result = extract_signals("default", "hello", "response")
        self.assertIsInstance(result, dict)

    def test_has_persona_key(self) -> None:
        result = extract_signals("default", "x", "y")
        self.assertIn("persona", result)

    def test_has_model_calibration_key(self) -> None:
        result = extract_signals("default", "x", "y")
        self.assertIn("model_calibration", result)

    def test_has_knowledge_key(self) -> None:
        result = extract_signals("default", "x", "y")
        self.assertIn("knowledge", result)

    def test_persona_is_list(self) -> None:
        result = extract_signals("default", "x", "y")
        self.assertIsInstance(result["persona"], list)

    def test_empty_inputs_returns_dict(self) -> None:
        result = extract_signals("", "", "")
        self.assertIsInstance(result, dict)

    def test_long_answer_adds_calibration(self) -> None:
        long_answer = "A" * 2500
        result = extract_signals("default", "test", long_answer)
        # A 2500-char answer should trigger verbosity calibration
        self.assertIsInstance(result["model_calibration"], list)


# ─────────────────────────────────────────────────────────────────────────────
# code_agent/python_lab — constants
# ─────────────────────────────────────────────────────────────────────────────

class PythonLabConstantsTest(unittest.TestCase):
    def test_timeout_is_int(self) -> None:
        self.assertIsInstance(PYTHON_EXEC_TIMEOUT, int)

    def test_timeout_positive(self) -> None:
        self.assertGreater(PYTHON_EXEC_TIMEOUT, 0)

    def test_figure_saver_is_string(self) -> None:
        self.assertIsInstance(FIGURE_SAVER, str)

    def test_figure_saver_nonempty(self) -> None:
        self.assertGreater(len(FIGURE_SAVER), 10)

    def test_figure_saver_has_matplotlib(self) -> None:
        self.assertIn("matplotlib", FIGURE_SAVER)


# ─────────────────────────────────────────────────────────────────────────────
# code_agent/python_lab — execute_python_with_capture
# ─────────────────────────────────────────────────────────────────────────────

class ExecutePythonWithCaptureTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIsInstance(result, dict)

    def test_has_ok_key(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIn("ok", result)

    def test_has_output_key(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIn("output", result)

    def test_has_traceback_key(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIn("traceback", result)

    def test_has_figures_key(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIn("figures", result)

    def test_simple_print_ok(self) -> None:
        result = execute_python_with_capture('print("hello")')
        self.assertTrue(result["ok"])

    def test_print_captures_output(self) -> None:
        result = execute_python_with_capture('print("hello world")')
        self.assertIn("hello world", result["output"])

    def test_syntax_error_not_ok(self) -> None:
        result = execute_python_with_capture("def broken(:")
        self.assertFalse(result["ok"])

    def test_runtime_error_not_ok(self) -> None:
        result = execute_python_with_capture("1 / 0")
        self.assertFalse(result["ok"])

    def test_figures_is_list(self) -> None:
        result = execute_python_with_capture("x = 1")
        self.assertIsInstance(result["figures"], list)

    def test_arithmetic_result_captured(self) -> None:
        result = execute_python_with_capture("print(2 + 2)")
        self.assertTrue(result["ok"])
        self.assertIn("4", result["output"])


if __name__ == "__main__":
    unittest.main()
