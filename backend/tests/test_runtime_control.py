from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools._runtime_control import tool_runtime_control  # noqa: E402
from app.application.agent_kernel.execution_context import (  # noqa: E402
    reset_permission_mode,
    set_permission_mode,
)
from app.application.workflows import db_path as workflow_db_path  # noqa: E402


class RuntimeControlContractTest(unittest.TestCase):
    def test_failure_is_typed(self) -> None:
        result = tool_runtime_control(ROOT, operation="not_supported")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "failed")
        self.assertEqual(result["operation"], "not_supported")
        self.assertEqual(result["error"]["code"], "ValueError")
        self.assertFalse(result["error"]["retryable"])

    def test_missing_scalar_returns_needs_input(self) -> None:
        result = tool_runtime_control(ROOT, operation="plugin_info")

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_input")
        self.assertEqual(result["request"]["kind"], "input")
        self.assertIn("name", result["request"]["schema"]["required"])

    def test_plaintext_credential_returns_needs_secret(self) -> None:
        result = tool_runtime_control(
            ROOT,
            operation="telegram_configure",
            config={"bot_token": "must-not-be-persisted"},
        )

        self.assertFalse(result["ok"])
        self.assertEqual(result["status"], "needs_secret")
        self.assertTrue(result["request"]["sensitive"])
        self.assertNotIn("must-not-be-persisted", result["text"])

    def test_memory_read_is_wrapped_as_completed(self) -> None:
        with patch(
            "app.application.memory.facade.fact_stats",
            return_value={"ok": True, "count": 4},
        ):
            result = tool_runtime_control(ROOT, operation="memory_stats")

        self.assertTrue(result["ok"])
        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["result"]["count"], 4)

    def test_workflow_template_and_trigger_share_workflow_store(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            previous = workflow_db_path.get_workflow_db_path()
            workflow_db_path.set_workflow_db_path(Path(tmpdir) / "workflow.db")
            permission_token = set_permission_mode("bypass")
            try:
                created = tool_runtime_control(
                    ROOT,
                    operation="workflow_upsert",
                    workflow_id="test.runtime.workflow",
                    config={
                        "name": "Runtime workflow",
                        "graph": {
                            "entry_step": "input",
                            "steps": [
                                {
                                    "id": "input",
                                    "type": "request",
                                    "next": None,
                                    "config": {
                                        "kind": "input",
                                        "message": "Value",
                                        "schema": {},
                                    },
                                }
                            ],
                        },
                    },
                )
                trigger = tool_runtime_control(
                    ROOT,
                    operation="workflow_trigger_upsert",
                    workflow_id="test.runtime.workflow",
                    trigger_id="test.runtime.trigger",
                    config={"interval_minutes": 15, "permission_mode": "bypass"},
                )
                listed = tool_runtime_control(
                    ROOT,
                    operation="workflow_trigger_list",
                )
            finally:
                reset_permission_mode(permission_token)
                workflow_db_path.set_workflow_db_path(previous)

        self.assertEqual(created["status"], "completed")
        self.assertEqual(trigger["status"], "completed")
        self.assertEqual(trigger["result"]["trigger"]["permission_mode"], "bypass")
        self.assertEqual(listed["result"]["total"], 1)


if __name__ == "__main__":
    unittest.main()
