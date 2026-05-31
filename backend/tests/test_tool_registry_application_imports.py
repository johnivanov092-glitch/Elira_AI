from __future__ import annotations

import inspect
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.tool_registry import builtins as tool_builtins  # noqa: E402
from app.application.tool_registry import service as tool_service  # noqa: E402


class ToolRegistryApplicationImportsTest(unittest.TestCase):
    def test_application_modules_do_not_import_service_facades(self) -> None:
        self.assertNotIn("app.services", inspect.getsource(tool_builtins))
        self.assertNotIn("app.services", inspect.getsource(tool_service))

    def test_project_map_handlers_return_structured_stub_result(self) -> None:
        tools = {tool["name"]: tool for tool in tool_builtins.build_builtin_tools()}

        scan = tools["project_map_scan"]["handler"]({})
        search = tools["project_map_search"]["handler"]({"query": "auth"})

        self.assertIsInstance(scan, dict)
        self.assertIsInstance(search, dict)
        self.assertIn("ok", scan)
        self.assertIn("ok", search)

    def test_browser_handlers_return_structured_stub_result(self) -> None:
        tools = {tool["name"]: tool for tool in tool_builtins.build_builtin_tools()}

        search = tools["browser_search"]["handler"]({"query": "docs"})
        run = tools["browser_run"]["handler"]({"start_url": "https://example.com"})

        self.assertIsInstance(search, dict)
        self.assertIsInstance(run, dict)
        self.assertIn("ok", search)
        self.assertIn("ok", run)


if __name__ == "__main__":
    unittest.main()
