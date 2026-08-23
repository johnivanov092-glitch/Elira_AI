"""P0.1 — secret redaction in audit / approval surfaces.

Unit: redact_secrets masks secret values while keeping command structure visible.
Integration: the unified executor returns a redacted Workflow request while an
opaque digest still matches the exact raw call once after approval.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.redaction import REDACTED, redact_secrets, redact_text  # noqa: E402
from app.application.agent_kernel.executor import (  # noqa: E402
    ToolExecutionRequest,
    execute_tool,
    workflow_approval_matches,
)


class RedactSecretsUnitTest(unittest.TestCase):
    def test_secret_named_dict_key_value_masked(self) -> None:
        out = redact_secrets({"password": "hunter2", "user": "alice"})
        self.assertEqual(out["password"], REDACTED)
        self.assertEqual(out["user"], "alice")

    def test_nested_dict_and_list(self) -> None:
        out = redact_secrets({"cfg": {"api_key": "abc"}, "items": [{"token": "xyz"}]})
        self.assertEqual(out["cfg"]["api_key"], REDACTED)
        self.assertEqual(out["items"][0]["token"], REDACTED)

    def test_cli_flag_password_in_command(self) -> None:
        out = redact_text("curl --password hunter2 https://x")
        self.assertNotIn("hunter2", out)
        self.assertIn(REDACTED, out)
        self.assertIn("curl", out)        # structure preserved
        self.assertIn("https://x", out)

    def test_bearer_token_masked(self) -> None:
        out = redact_text("Authorization: Bearer sk-secret-123")
        self.assertNotIn("sk-secret-123", out)
        self.assertIn(REDACTED, out)

    def test_key_value_env_masked(self) -> None:
        out = redact_text("export TOKEN=supersecret && run")
        self.assertNotIn("supersecret", out)
        self.assertIn("run", out)

    def test_non_secret_unchanged(self) -> None:
        self.assertEqual(redact_text("git push origin main"), "git push origin main")
        self.assertEqual(
            redact_secrets({"path": "/tmp/x", "n": 3}), {"path": "/tmp/x", "n": 3}
        )


class ApprovalRedactionMatchingTest(unittest.TestCase):
    def test_workflow_request_is_redacted_but_matching_stays_exact(self) -> None:
        raw_args = {"command": "curl --password hunter2 https://api", "password": "hunter2"}
        request = ToolExecutionRequest(
            run_id="run-1",
            agent_id="code-agent",
            project_scope_id="scope:x",
            tool_name="run_bash",
            args=raw_args,
            source="code-agent",
            permission_mode="ask",
        )
        with patch(
            "app.application.tool_registry.runtime.get_tool",
            return_value={"side_effect": True, "max_output_chars": 50000},
        ):
            result = execute_tool(
                request,
                lambda *_: self.fail("approval request must not dispatch"),
            )

        self.assertEqual(result.status, "waiting_approval")
        workflow_request = result.output["request"]
        self.assertNotIn("hunter2", str(workflow_request))
        approved_tool = workflow_request["schema"]["x-elira-tool"]
        self.assertEqual(approved_tool["arguments"]["password"], REDACTED)
        self.assertTrue(
            workflow_approval_matches(approved_tool, "run_bash", raw_args)
        )
        self.assertFalse(
            workflow_approval_matches(
                approved_tool,
                "run_bash",
                {**raw_args, "password": "different"},
            )
        )


if __name__ == "__main__":
    unittest.main()
