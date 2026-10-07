"""Tests for pure helpers across two modules.

  application/monitoring/store.py   - dumps_json, loads_json
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
