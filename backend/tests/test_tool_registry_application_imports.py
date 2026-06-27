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

    def test_project_map_handlers_delegate_to_native_tools(self) -> None:
        import tempfile

        from app.application.advanced import runtime as advanced_runtime

        tools = {tool["name"]: tool for tool in tool_builtins.build_builtin_tools()}

        # With no project open, the handlers report that — not a bare "stub".
        prev = advanced_runtime._project_path
        try:
            advanced_runtime._project_path = None
            scan_closed = tools["project_map_scan"]["handler"]({})
            self.assertIsInstance(scan_closed, dict)
            self.assertFalse(scan_closed["ok"])
            self.assertNotEqual(scan_closed.get("error"), "stub")

            # With a project open, project_map_scan returns a real structural map.
            with tempfile.TemporaryDirectory() as tmp:
                advanced_runtime.open_project(tmp)
                scan = tools["project_map_scan"]["handler"]({})
                search = tools["project_map_search"]["handler"]({"query": "auth"})
        finally:
            advanced_runtime._project_path = prev

        self.assertIsInstance(scan, dict)
        self.assertIsInstance(search, dict)
        self.assertTrue(scan["ok"], scan)
        self.assertIn("Project map for", scan.get("text", ""))
        # search delegates to RAG recall; it returns ok with a text payload
        # (or a structured failure if the embedding server is unreachable).
        self.assertIn("ok", search)

    def test_browser_handlers_return_structured_result(self) -> None:
        tools = {tool["name"]: tool for tool in tool_builtins.build_builtin_tools()}

        # A loopback start_url is SSRF-blocked before any browser launches, so
        # this stays offline and deterministic while still exercising the wiring.
        search = tools["browser_search"]["handler"]({"query": ""})
        run = tools["browser_run"]["handler"]({"start_url": "http://127.0.0.1/"})

        self.assertIsInstance(search, dict)
        self.assertIsInstance(run, dict)
        self.assertIn("ok", search)
        self.assertIn("ok", run)


if __name__ == "__main__":
    unittest.main()
