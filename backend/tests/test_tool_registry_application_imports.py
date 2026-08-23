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


if __name__ == "__main__":
    unittest.main()
