"""Tests for the ToolProvider abstraction.

Covers the registry's contract (schema aggregation, dispatch routing,
collision handling, exception containment) and the BuiltinToolProvider
adapter that wraps existing code-agent tools.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_providers import (  # noqa: E402
    BuiltinToolProvider,
    ToolProvider,
    ToolRegistry,
)


# ── Test-only providers ─────────────────────────────────────────


class _FakeProvider:
    """Minimal provider used by registry tests. Lets each test fully
    control schemas, owned names, dispatch return values, and the
    enabled flag without standing up the real built-in stack."""

    def __init__(
        self,
        name: str,
        *,
        tool_names: list[str] | None = None,
        enabled: bool = True,
        dispatch_result: dict[str, Any] | None = None,
        raise_in_dispatch: Exception | None = None,
    ) -> None:
        self.name = name
        self._enabled = enabled
        self._tool_names = tool_names or []
        self._dispatch_result = dispatch_result
        self._raise = raise_in_dispatch
        self.dispatch_calls: list[tuple[str, dict[str, Any]]] = []

    def is_enabled(self) -> bool:
        return self._enabled

    def get_schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": name,
                    "description": f"{self.name}.{name}",
                    "parameters": {"type": "object", "properties": {}},
                },
            }
            for name in self._tool_names
        ]

    def owns(self, tool_name: str) -> bool:
        return tool_name in self._tool_names

    def dispatch(self, tool_name: str, args: dict[str, Any]) -> dict[str, Any]:
        self.dispatch_calls.append((tool_name, dict(args)))
        if self._raise is not None:
            raise self._raise
        if self._dispatch_result is not None:
            return self._dispatch_result
        return {"text": f"{self.name}:{tool_name} ran"}


# ── ToolProvider protocol contract ──────────────────────────────


class ProtocolConformanceTest(unittest.TestCase):
    """BuiltinToolProvider must satisfy the ToolProvider Protocol."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_builtin_satisfies_protocol_at_runtime(self) -> None:
        provider = BuiltinToolProvider(self.root)
        # @runtime_checkable Protocol — isinstance check actually works
        self.assertIsInstance(provider, ToolProvider)

    def test_fake_provider_satisfies_protocol(self) -> None:
        # Sanity check for the test helper itself.
        self.assertIsInstance(_FakeProvider("fake"), ToolProvider)


# ── Registry: schema collection ─────────────────────────────────


class CollectSchemasTest(unittest.TestCase):
    def test_empty_registry_returns_empty_list(self) -> None:
        reg = ToolRegistry([])
        self.assertEqual(reg.collect_schemas(), [])
        self.assertEqual(reg.known_tools(), set())

    def test_aggregates_schemas_from_all_enabled_providers(self) -> None:
        a = _FakeProvider("A", tool_names=["alpha", "beta"])
        b = _FakeProvider("B", tool_names=["gamma"])
        reg = ToolRegistry([a, b])
        names = {s["function"]["name"] for s in reg.collect_schemas()}
        self.assertEqual(names, {"alpha", "beta", "gamma"})

    def test_disabled_provider_contributes_nothing(self) -> None:
        on = _FakeProvider("on", tool_names=["a", "b"])
        off = _FakeProvider("off", tool_names=["c"], enabled=False)
        reg = ToolRegistry([on, off])
        names = {s["function"]["name"] for s in reg.collect_schemas()}
        self.assertEqual(names, {"a", "b"})
        self.assertNotIn("c", reg.known_tools())

    def test_first_provider_wins_on_name_collision(self) -> None:
        first = _FakeProvider("first", tool_names=["shared"])
        second = _FakeProvider("second", tool_names=["shared"])
        reg = ToolRegistry([first, second])
        # Only one schema in the flat list
        self.assertEqual(len(reg.collect_schemas()), 1)
        # And the first provider receives the dispatch
        reg.dispatch("shared", {})
        self.assertEqual(len(first.dispatch_calls), 1)
        self.assertEqual(len(second.dispatch_calls), 0)

    def test_malformed_schema_is_skipped(self) -> None:
        class BadSchemaProvider:
            name = "bad"
            def is_enabled(self): return True
            def owns(self, _): return False
            def get_schemas(self):
                # Missing function.name → registry must skip
                return [{"type": "function", "function": {"description": "no name"}}]
            def dispatch(self, *_a, **_k): return {"text": ""}
        reg = ToolRegistry([BadSchemaProvider(), _FakeProvider("ok", tool_names=["good"])])
        # Bad one is dropped, good one survives
        self.assertEqual(reg.known_tools(), {"good"})


# ── Registry: dispatch routing ─────────────────────────────────


class DispatchRoutingTest(unittest.TestCase):
    def test_routes_by_tool_name(self) -> None:
        a = _FakeProvider("A", tool_names=["alpha"])
        b = _FakeProvider("B", tool_names=["beta"])
        reg = ToolRegistry([a, b])

        reg.dispatch("alpha", {})
        reg.dispatch("beta", {})

        self.assertEqual(len(a.dispatch_calls), 1)
        self.assertEqual(len(b.dispatch_calls), 1)
        self.assertEqual(a.dispatch_calls[0][0], "alpha")
        self.assertEqual(b.dispatch_calls[0][0], "beta")

    def test_unknown_tool_returns_error_meta(self) -> None:
        reg = ToolRegistry([_FakeProvider("p", tool_names=["a"])])
        result = reg.dispatch("does_not_exist", {})
        self.assertIn("ERROR", result.tool_meta["text"])
        self.assertIn("does_not_exist", result.tool_meta["text"])

    def test_args_dict_passed_through(self) -> None:
        p = _FakeProvider("p", tool_names=["call"])
        ToolRegistry([p]).dispatch("call", {"path": "src/foo.py", "offset": 10})
        self.assertEqual(
            p.dispatch_calls[0][1],
            {"path": "src/foo.py", "offset": 10},
        )

    def test_args_json_string_coerced_to_dict(self) -> None:
        """Some Ollama models return tool args as a JSON-encoded string
        instead of a dict — registry must parse it before handing to
        the provider."""
        p = _FakeProvider("p", tool_names=["call"])
        ToolRegistry([p]).dispatch("call", '{"path": "x"}')
        self.assertEqual(p.dispatch_calls[0][1], {"path": "x"})

    def test_args_malformed_json_string_becomes_empty_dict(self) -> None:
        p = _FakeProvider("p", tool_names=["call"])
        ToolRegistry([p]).dispatch("call", "{not valid json")
        self.assertEqual(p.dispatch_calls[0][1], {})

    def test_args_non_dict_non_string_becomes_empty_dict(self) -> None:
        p = _FakeProvider("p", tool_names=["call"])
        ToolRegistry([p]).dispatch("call", 12345)  # type: ignore[arg-type]
        self.assertEqual(p.dispatch_calls[0][1], {})

    def test_parsed_args_returned_in_result(self) -> None:
        reg = ToolRegistry([_FakeProvider("p", tool_names=["call"])])
        result = reg.dispatch("call", {"a": 1})
        self.assertEqual(result.parsed_args, {"a": 1})


# ── Registry: exception containment ────────────────────────────


class ExceptionContainmentTest(unittest.TestCase):
    """The whole point of the dispatch contract is that exceptions
    never propagate out of the registry — they always show up as
    error tool_meta. The streaming SSE generator depends on this."""

    def test_provider_exception_is_caught_and_returned_as_error(self) -> None:
        bad = _FakeProvider(
            "bad",
            tool_names=["explode"],
            raise_in_dispatch=RuntimeError("boom"),
        )
        result = ToolRegistry([bad]).dispatch("explode", {})
        self.assertIn("ERROR", result.tool_meta["text"])
        self.assertIn("boom", result.tool_meta["text"])

    def test_provider_returning_non_dict_normalized_to_text(self) -> None:
        weird = _FakeProvider("weird", tool_names=["x"], dispatch_result="just a string")  # type: ignore[arg-type]
        result = ToolRegistry([weird]).dispatch("x", {})
        self.assertEqual(result.tool_meta["text"], "just a string")


# ── BuiltinToolProvider integration ────────────────────────────


class BuiltinToolProviderTest(unittest.TestCase):
    """The adapter must expose every built-in tool name and route to
    the real implementation (no behavior change vs. the old
    direct-dispatch path)."""

    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name).resolve()
        # Real provider, real tools.py
        self.provider = BuiltinToolProvider(self.root)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_exposes_canonical_tool_set(self) -> None:
        names = {s["function"]["name"] for s in self.provider.get_schemas()}
        # Names that have been part of the agent's contract from day one
        for required in (
            "read_file", "write_file", "edit_file",
            "glob", "grep", "run_bash", "recall",
            "web_search", "web_fetch",
            "sandbox_run", "sandbox_reset",
        ):
            self.assertIn(required, names)

    def test_dispatch_read_file_returns_error_for_missing_path(self) -> None:
        # Doesn't crash, doesn't raise — returns an error meta as
        # tools.py's tool_read_file does.
        result = self.provider.dispatch("read_file", {"path": "nonexistent.txt"})
        self.assertIn("ERROR", result["text"])

    def test_dispatch_unknown_tool_returns_error_meta(self) -> None:
        result = self.provider.dispatch("does_not_exist", {})
        self.assertIn("ERROR", result["text"])

    def test_dispatch_bad_args_caught_as_typeerror(self) -> None:
        # read_file requires `path`. Calling without it triggers
        # TypeError on **{}, which the provider must catch.
        result = self.provider.dispatch("read_file", {})
        self.assertIn("ERROR", result["text"])

    def test_sandbox_violation_handled(self) -> None:
        # Path-traversal via ../ — provider catches SandboxError.
        result = self.provider.dispatch("read_file", {"path": "../../../etc/passwd"})
        self.assertIn("ERROR", result["text"])
        self.assertIn("sandbox", result["text"].lower())

    def test_registry_with_builtin_exposes_same_tool_set(self) -> None:
        reg = ToolRegistry([self.provider])
        names = {s["function"]["name"] for s in reg.collect_schemas()}
        # Must match the standalone provider's surface exactly
        self.assertEqual(names, {s["function"]["name"] for s in self.provider.get_schemas()})


if __name__ == "__main__":
    unittest.main()
