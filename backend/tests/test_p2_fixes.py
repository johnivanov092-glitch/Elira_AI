from __future__ import annotations

import inspect
import unittest


class ClearAllWorkflowPermissionTest(unittest.TestCase):
    """The direct route has no second confirmation mechanism."""

    def test_clear_contract_has_no_confirm_parameter(self):
        from app.api.routes import advanced_routes

        parameters = inspect.signature(
            advanced_routes.rag_clear_category
        ).parameters
        self.assertEqual(set(parameters), {"category"})


if __name__ == "__main__":
    unittest.main()
