"""Тесты Agent OS Phase 2 — Tool Registry."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services import tool_registry as reg  # noqa: E402


def _dummy_handler(args: dict) -> dict:
    return {"ok": True, "echo": args.get("input", "")}


def _fail_handler(args: dict) -> dict:
    raise ValueError("Intentional test error")


class ToolRegistryTestCase(unittest.TestCase):
    def tearDown(self) -> None:
        for name in ("test-tool", "test-tool2", "test-custom", "test-fail", "test-disabled"):
            reg.delete_tool(name)
        super().tearDown()


class TestToolCRUD(ToolRegistryTestCase):
    def test_register_and_get(self) -> None:
        reg.register_tool(
            name="test-tool",
            handler=_dummy_handler,
            display_name="Test Tool",
            display_name_ru="Тестовый инструмент",
            category="testing",
            description="A test tool",
            parameters_schema={"type": "object", "properties": {"input": {"type": "string"}}},
        )
        tool = reg.get_tool("test-tool")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool["name"], "test-tool")
        self.assertEqual(tool["display_name"], "Test Tool")
        self.assertEqual(tool["category"], "testing")
        self.assertTrue(tool["has_handler"])
        self.assertIn("input", tool["parameters_schema"].get("properties", {}))

    def test_list_tools(self) -> None:
        reg.register_tool(name="test-tool", handler=_dummy_handler, category="cat-a")
        reg.register_tool(name="test-tool2", handler=_dummy_handler, category="cat-b")

        all_tools = reg.list_tools_with_schemas()
        names = [t["name"] for t in all_tools]
        self.assertIn("test-tool", names)
        self.assertIn("test-tool2", names)

        filtered = reg.list_tools_with_schemas(category="cat-a")
        self.assertTrue(all(t["category"] == "cat-a" for t in filtered))

    def test_update_tool(self) -> None:
        reg.register_tool(name="test-tool", handler=_dummy_handler, display_name="Before")
        updated = reg.update_tool("test-tool", {"display_name": "After", "category": "updated"})
        self.assertEqual(updated["display_name"], "After")
        self.assertEqual(updated["category"], "updated")

    def test_delete_tool(self) -> None:
        reg.register_tool(name="test-tool", handler=_dummy_handler)
        reg.delete_tool("test-tool")
        self.assertIsNone(reg.get_tool("test-tool"))

    def test_get_nonexistent(self) -> None:
        self.assertIsNone(reg.get_tool("no-such-tool"))


class TestToolExecution(ToolRegistryTestCase):
    def test_execute_success(self) -> None:
        reg.register_tool(name="test-tool", handler=_dummy_handler)
        result = reg.execute_tool("test-tool", {"input": "hello"})
        self.assertTrue(result["ok"])
        self.assertEqual(result["echo"], "hello")

    def test_execute_unknown(self) -> None:
        result = reg.execute_tool("nonexistent-tool", {})
        self.assertFalse(result["ok"])
        self.assertIn("No handler", result.get("error", ""))

    def test_execute_error_handling(self) -> None:
        reg.register_tool(name="test-fail", handler=_fail_handler)
        result = reg.execute_tool("test-fail", {})
        self.assertFalse(result["ok"])
        self.assertIn("Intentional", result.get("error", ""))

    def test_execute_disabled(self) -> None:
        reg.register_tool(name="test-disabled", handler=_dummy_handler)
        reg.update_tool("test-disabled", {"enabled": False})
        result = reg.execute_tool("test-disabled", {})
        self.assertFalse(result["ok"])
        self.assertIn("disabled", result.get("error", ""))


class TestToolValidation(ToolRegistryTestCase):
    def test_validate_required_ok(self) -> None:
        reg.register_tool(
            name="test-tool",
            handler=_dummy_handler,
            parameters_schema={"type": "object", "required": ["query"]},
        )
        errors = reg.validate_tool_args("test-tool", {"query": "test"})
        self.assertEqual(errors, [])

    def test_validate_required_missing(self) -> None:
        reg.register_tool(
            name="test-tool",
            handler=_dummy_handler,
            parameters_schema={"type": "object", "required": ["query", "limit"]},
        )
        errors = reg.validate_tool_args("test-tool", {"query": "test"})
        self.assertEqual(len(errors), 1)
        self.assertIn("limit", errors[0])

    def test_validate_nonexistent(self) -> None:
        errors = reg.validate_tool_args("no-such-tool", {})
        self.assertEqual(len(errors), 1)


class TestRegisterFromDict(ToolRegistryTestCase):
    def test_register_from_dict(self) -> None:
        reg.register_tool_from_dict(
            {"name": "test-custom", "display_name": "Custom", "category": "custom", "source": "plugin"},
            handler=_dummy_handler,
        )
        tool = reg.get_tool("test-custom")
        self.assertIsNotNone(tool)
        assert tool is not None
        self.assertEqual(tool["source"], "plugin")
        self.assertTrue(tool["has_handler"])


class TestSeedBuiltinTools(ToolRegistryTestCase):
    def test_seed_creates_tools(self) -> None:
        reg._BUILTIN_SEEDED = False
        count = reg.seed_builtin_tools()
        self.assertGreater(count, 0)

        tools = reg.list_tools_with_schemas()
        names = [t["name"] for t in tools]
        self.assertIn("search_web", names)
        self.assertIn("python_execute", names)
        self.assertIn("git_status", names)

    def test_seed_idempotent(self) -> None:
        reg._BUILTIN_SEEDED = False
        reg.seed_builtin_tools()
        reg._BUILTIN_SEEDED = False
        count2 = reg.seed_builtin_tools()
        # Re-registration updates, doesn't fail
        self.assertGreaterEqual(count2, 0)


class TestBackwardCompatibility(ToolRegistryTestCase):
    """Проверяем что tool_service.py всё ещё работает."""

    def test_tool_service_list(self) -> None:
        reg._BUILTIN_SEEDED = False
        reg.seed_builtin_tools()
        from app.services.tool_service import list_tools
        result = list_tools()
        self.assertTrue(result["ok"])
        self.assertGreater(result["count"], 0)

    def test_tool_service_run(self) -> None:
        reg._BUILTIN_SEEDED = False
        reg.seed_builtin_tools()
        from app.services.tool_service import run_tool
        result = run_tool("git_status")
        # git_status might fail if not in a git repo, but should not crash
        self.assertIsInstance(result, dict)


if __name__ == "__main__":
    unittest.main()
