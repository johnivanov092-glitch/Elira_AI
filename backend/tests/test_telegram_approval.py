"""Tests — Telegram approval inbox (P7 Шаг 15)."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.telegram.runtime import (  # noqa: E402
    handle_approval_command,
    send_approval_notification,
)


class TestHandleApprovalCommand(unittest.TestCase):

    def test_approve_command(self):
        result = handle_approval_command("/approve abc123", chat_id=42)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "approve")
        self.assertEqual(result["approval_id"], "abc123")
        self.assertEqual(result["chat_id"], 42)

    def test_reject_command(self):
        result = handle_approval_command("/reject def456", chat_id=99)
        self.assertIsNotNone(result)
        self.assertEqual(result["action"], "reject")
        self.assertEqual(result["approval_id"], "def456")

    def test_case_insensitive(self):
        self.assertIsNotNone(handle_approval_command("/APPROVE abc", chat_id=1))

    def test_non_approval_command_returns_none(self):
        self.assertIsNone(handle_approval_command("/start", chat_id=1))
        self.assertIsNone(handle_approval_command("hello world", chat_id=1))
        self.assertIsNone(handle_approval_command("/approve", chat_id=1))  # no id

    def test_empty_input_returns_none(self):
        self.assertIsNone(handle_approval_command("", chat_id=1))


class TestSendApprovalNotification(unittest.TestCase):

    def test_returns_false_when_no_token(self):
        with mock.patch("app.application.telegram.runtime.get_config_value",
                        return_value=""):
            result = send_approval_notification({"id": "x", "tool_name": "run_bash"})
        self.assertFalse(result)

    def test_returns_false_when_no_admin_chat(self):
        def cfg(key, default=""):
            return "fake_token" if key == "bot_token" else ""
        with mock.patch("app.application.telegram.runtime.get_config_value",
                        side_effect=cfg):
            result = send_approval_notification({"id": "x", "tool_name": "run_bash"})
        self.assertFalse(result)

    def test_sends_message_when_configured(self):
        def cfg(key, default=""):
            return {"bot_token": "fake_tok", "admin_chat_id": "123"}[key] if key in ("bot_token", "admin_chat_id") else default
        with mock.patch("app.application.telegram.runtime.get_config_value",
                        side_effect=cfg), \
             mock.patch("app.application.telegram.runtime.tg_request",
                        return_value={"ok": True}) as mock_tg:
            result = send_approval_notification({
                "id": "apr-1",
                "tool_name": "run_bash",
                "agent_id": "code-agent",
                "args": {"command": "git status"},
            })
        self.assertTrue(result)
        mock_tg.assert_called_once()
        call_args = mock_tg.call_args
        self.assertEqual(call_args[0][0], "sendMessage")
        msg_text = call_args[0][2]["text"]
        self.assertIn("apr-1", msg_text)
        self.assertIn("run_bash", msg_text)

    def test_returns_false_on_tg_error(self):
        def cfg(key, default=""):
            return {"bot_token": "tok", "admin_chat_id": "99"}[key] if key in ("bot_token", "admin_chat_id") else default
        with mock.patch("app.application.telegram.runtime.get_config_value",
                        side_effect=cfg), \
             mock.patch("app.application.telegram.runtime.tg_request",
                        side_effect=Exception("network error")):
            result = send_approval_notification({"id": "x", "tool_name": "test"})
        self.assertFalse(result)


class TestApprovalCallbackRoute(unittest.TestCase):

    def setUp(self):
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        from app.api.routes.telegram_routes import router
        app = FastAPI()
        app.include_router(router)
        self.client = TestClient(app)

    def _mock_mon(self, approval: dict | None, *, patch_update=True):
        """Context manager patching monitoring runtime for approval tests."""
        from app.application.monitoring import runtime as mon
        return mock.patch.multiple(
            "app.application.monitoring.runtime",
            get_approval=mock.MagicMock(return_value=approval),
            update_approval_status=mock.MagicMock(return_value={
                **(approval or {}), "status": "approved" if approval else "approved"
            }),
        )

    def test_approve_pending(self):
        pending = {"id": "apr-t1", "tool_name": "write_file", "status": "pending"}
        with self._mock_mon(pending):
            r = self.client.post(
                "/api/telegram/approval_callback",
                json={"approval_id": "apr-t1", "action": "approve"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertTrue(r.json()["ok"])
        self.assertEqual(r.json()["status"], "approved")

    def test_reject_pending(self):
        pending = {"id": "apr-t2", "tool_name": "run_bash", "status": "pending"}
        with self._mock_mon(pending):
            r = self.client.post(
                "/api/telegram/approval_callback",
                json={"approval_id": "apr-t2", "action": "reject"},
            )
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json()["status"], "rejected")

    def test_invalid_action(self):
        r = self.client.post(
            "/api/telegram/approval_callback",
            json={"approval_id": "x", "action": "maybe"},
        )
        self.assertEqual(r.status_code, 400)

    def test_not_found(self):
        with mock.patch("app.application.monitoring.runtime.get_approval", return_value=None):
            r = self.client.post(
                "/api/telegram/approval_callback",
                json={"approval_id": "nope", "action": "approve"},
            )
        self.assertEqual(r.status_code, 404)

    def test_already_approved_returns_400(self):
        already = {"id": "apr-t3", "tool_name": "test", "status": "approved"}
        with self._mock_mon(already):
            r = self.client.post(
                "/api/telegram/approval_callback",
                json={"approval_id": "apr-t3", "action": "approve"},
            )
        self.assertEqual(r.status_code, 400)

    def test_set_admin_chat(self):
        with mock.patch("app.application.telegram.store.set_config_value") as m:
            r = self.client.post("/api/telegram/set_admin_chat?chat_id=12345")
        self.assertEqual(r.status_code, 200)
        m.assert_called_once_with("admin_chat_id", "12345")


if __name__ == "__main__":
    unittest.main()
