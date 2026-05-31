from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes.registry import ALL_ROUTERS  # noqa: E402
from app.main import app  # noqa: E402


class RouteRegistryTest(unittest.TestCase):
    def test_registry_contains_unique_router_objects(self) -> None:
        router_ids = [id(router) for router in ALL_ROUTERS]
        self.assertEqual(len(router_ids), len(set(router_ids)))

    def test_main_registers_all_registry_routes(self) -> None:
        expected_paths = {
            route.path
            for router in ALL_ROUTERS
            for route in router.routes
            if hasattr(route, "path")
        }
        actual_paths = {route.path for route in app.routes if hasattr(route, "path")}

        self.assertLessEqual(expected_paths, actual_paths)


if __name__ == "__main__":
    unittest.main()
