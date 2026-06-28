"""P0.1 — secret redaction in audit / approval surfaces.

Unit: redact_secrets masks secret values while keeping command structure visible.
Integration: create_approval persists a REDACTED args_json, yet
find_approved_approval still matches on the RAW args — the matching digest
(args_sha256) is computed from raw args, so redaction never breaks approval
matching on retry.
"""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.redaction import REDACTED, redact_secrets, redact_text  # noqa: E402
from app.application.monitoring import store as mon_store  # noqa: E402


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
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        self.db = str(Path(self._tmp.name) / "agent_monitor.db")
        mon_store.init_db(self.db)

    def tearDown(self) -> None:
        self._tmp.cleanup()

    def test_args_json_redacted_but_matching_intact(self) -> None:
        raw_args = {"command": "curl --password hunter2 https://api", "password": "hunter2"}
        created = mon_store.create_approval(
            self.db, id="a1", tool_name="run_bash", agent_id="code-agent",
            source="code-agent", run_id="run-1", project_scope_id="scope:x", args=raw_args,
        )
        # Stored / displayed args are redacted — no secret leaks into the store/UI.
        self.assertEqual(created["args"]["password"], REDACTED)
        self.assertNotIn("hunter2", str(created["args"]))

        # ...yet matching on the RAW args still finds the approval (digest is raw).
        mon_store.update_approval_status(self.db, "a1", status="approved")
        found = mon_store.find_approved_approval(
            self.db, tool_name="run_bash", agent_id="code-agent", source="code-agent",
            run_id="run-1", project_scope_id="scope:x", args=raw_args,
        )
        self.assertIsNotNone(found)
        self.assertEqual(found["id"], "a1")
        self.assertNotIn("hunter2", str(found["args"]))


if __name__ == "__main__":
    unittest.main()
