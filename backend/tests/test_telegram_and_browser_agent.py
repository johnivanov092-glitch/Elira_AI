"""Tests for Telegram storage/runtime helpers and the browser agent facade."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.telegram.runtime as tg_rt  # noqa: E402
import app.application.telegram.store as tg_store  # noqa: E402
from app.infrastructure.browser.agent import BrowserAgent  # noqa: E402


class TelegramStoreCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = tg_store.DB_PATH
        tg_store.DB_PATH = Path(self._tmpdir.name) / "integrations.db"
        tg_store.init_telegram_db()

    def tearDown(self) -> None:
        tg_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def test_get_config_value_default_when_missing(self) -> None:
        result = tg_store.get_config_value("missing_key", "default_val")
        self.assertEqual(result, "default_val")

    def test_set_and_get_config_value(self) -> None:
        tg_store.set_config_value("bot_token", "12345:ABCDEF")
        result = tg_store.get_config_value("bot_token")
        self.assertEqual(result, "12345:ABCDEF")

    def test_set_config_overrides_previous(self) -> None:
        tg_store.set_config_value("model", "gemma3:4b")
        tg_store.set_config_value("model", "llama3:8b")
        result = tg_store.get_config_value("model")
        self.assertEqual(result, "llama3:8b")

    def test_update_telegram_config_ok(self) -> None:
        result = tg_store.update_telegram_config(
            {
                "bot_token": "999:TOKEN",
                "model": "gemma3:4b",
            }
        )
        self.assertTrue(result["ok"])

    def test_update_telegram_config_ignores_unknown_keys(self) -> None:
        result = tg_store.update_telegram_config(
            {
                "unknown_key": "should be ignored",
                "model": "gemma3:4b",
            }
        )
        self.assertTrue(result["ok"])
        stored = tg_store.get_config_value("unknown_key")
        self.assertEqual(stored, "")

    def test_register_user_stores_user(self) -> None:
        tg_store.register_user(chat_id=12345, username="testuser", first_name="Test")
        result = tg_store.list_telegram_users()
        self.assertEqual(result["count"], 1)
        self.assertEqual(result["users"][0]["chat_id"], 12345)

    def test_is_user_allowed_for_all(self) -> None:
        tg_store.set_config_value("allowed_users", "all")
        self.assertTrue(tg_store.is_user_allowed(99999))

    def test_is_user_allowed_for_none(self) -> None:
        tg_store.set_config_value("allowed_users", "none")
        self.assertFalse(tg_store.is_user_allowed(99999))

    def test_register_and_toggle_user_access(self) -> None:
        tg_store.set_config_value("allowed_users", "whitelist")
        tg_store.register_user(chat_id=777, username="alice", first_name="Alice")
        result = tg_store.toggle_user_access(777, True)
        self.assertTrue(result["ok"])
        self.assertTrue(tg_store.is_user_allowed(777))

    def test_list_users_empty_initially(self) -> None:
        result = tg_store.list_telegram_users()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_list_users_after_register(self) -> None:
        tg_store.register_user(chat_id=100, username="bob", first_name="Bob")
        result = tg_store.list_telegram_users()
        self.assertEqual(result["count"], 1)

    def test_log_message_and_get_log(self) -> None:
        tg_store.log_message(chat_id=111, direction="in", text="Hello!")
        result = tg_store.get_telegram_log(limit=10)
        self.assertTrue(result["ok"])
        self.assertGreater(result["count"], 0)

    def test_get_telegram_log_empty_initially(self) -> None:
        result = tg_store.get_telegram_log(limit=10)
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_log_respects_limit(self) -> None:
        for i in range(5):
            tg_store.log_message(chat_id=200 + i, direction="in", text=f"msg {i}")
        result = tg_store.get_telegram_log(limit=3)
        self.assertLessEqual(result["count"], 3)


class TelegramRuntimeConfigTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = tg_store.DB_PATH
        self._orig_running = tg_rt._running
        tg_store.DB_PATH = Path(self._tmpdir.name) / "integrations.db"
        tg_rt._running = False
        tg_store.init_telegram_db()

    def tearDown(self) -> None:
        tg_rt._running = self._orig_running
        tg_store.DB_PATH = self._orig_db
        self._tmpdir.cleanup()

    def test_get_config_returns_ok(self) -> None:
        result = tg_rt.get_telegram_config()
        self.assertTrue(result["ok"])

    def test_get_config_has_required_keys(self) -> None:
        result = tg_rt.get_telegram_config()
        for key in (
            "has_token",
            "model",
            "profile",
            "running",
            "use_memory",
            "use_web_search",
            "max_message_length",
        ):
            self.assertIn(key, result)

    def test_get_config_no_token_shows_empty(self) -> None:
        result = tg_rt.get_telegram_config()
        self.assertFalse(result["has_token"])

    def test_get_config_token_masked_when_set(self) -> None:
        tg_store.set_config_value("bot_token", "1234567890:ABCDefGHIJKL")
        result = tg_rt.get_telegram_config()
        self.assertTrue(result["has_token"])
        self.assertNotIn("ABCDefGHIJKL", result["bot_token"])
        self.assertIn("...", result["bot_token"])

    def test_running_is_bool(self) -> None:
        result = tg_rt.get_telegram_config()
        self.assertIsInstance(result["running"], bool)

    def test_telegram_bot_status_ok(self) -> None:
        result = tg_rt.telegram_bot_status()
        self.assertTrue(result["ok"])

    def test_telegram_bot_status_keys(self) -> None:
        result = tg_rt.telegram_bot_status()
        for key in ("running", "has_token", "bot_token_preview"):
            self.assertIn(key, result)

    def test_telegram_bot_status_running_false_initially(self) -> None:
        result = tg_rt.telegram_bot_status()
        self.assertFalse(result["running"])


class BrowserAgentStubTest(unittest.TestCase):
    def setUp(self) -> None:
        self._agent = BrowserAgent()

    def test_search_returns_not_implemented(self) -> None:
        result = self._agent.search("test query")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_search_accepts_max_results(self) -> None:
        result = self._agent.search("test", max_results=10)
        self.assertFalse(result["ok"])

    def test_run_returns_not_implemented(self) -> None:
        result = self._agent.run()
        self.assertFalse(result["ok"])

    def test_run_accepts_args(self) -> None:
        result = self._agent.run("goal", key="val")
        self.assertFalse(result["ok"])

    def test_screenshot_returns_not_implemented(self) -> None:
        result = self._agent.screenshot()
        self.assertFalse(result["ok"])

    def test_error_message_mentions_stub(self) -> None:
        result = self._agent.search("query")
        self.assertIn("stub", result["error"])

    def test_all_methods_consistently_not_ok(self) -> None:
        results = [
            self._agent.search("q"),
            self._agent.run(),
            self._agent.screenshot(),
        ]
        for result in results:
            self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
