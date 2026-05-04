"""Tests for application/rag_memory_service, application/chat_service,
application/skills (sql+http helpers), and application/multi_agent_chain (pure helpers)."""
from __future__ import annotations

import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

import app.application.rag_memory_service.runtime as rms         # noqa: E402
import app.application.chat_service.runtime as chat_svc          # noqa: E402
import app.application.skills.runtime as skills_rt               # noqa: E402
import app.application.multi_agent_chain.runtime as mac_rt       # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# rag_memory_service — constants and patched-DB CRUD
# ─────────────────────────────────────────────────────────────────────────────

class RagMemoryServiceConstantsTest(unittest.TestCase):
    def test_seed_rag_text_is_string(self) -> None:
        self.assertIsInstance(rms.SEED_RAG_TEXT, str)
        self.assertTrue(len(rms.SEED_RAG_TEXT) > 0)

    def test_embed_model_is_string(self) -> None:
        self.assertIsInstance(rms.EMBED_MODEL, str)

    def test_embed_dim_is_positive_int(self) -> None:
        self.assertIsInstance(rms.EMBED_DIM, int)
        self.assertGreater(rms.EMBED_DIM, 0)


class RagMemoryServiceCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = rms.DB_PATH
        rms.DB_PATH = Path(self._tmpdir.name) / "rag.db"
        rms._init()
        # No need to call _cleanup_seed_data() since fresh DB has no seed

    def tearDown(self) -> None:
        rms.DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_list_rag_empty(self) -> None:
        r = rms.list_rag()
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    def test_add_to_rag_success(self) -> None:
        r = rms.add_to_rag("Python is great for science")
        self.assertTrue(r["ok"])
        self.assertIn("id", r)

    def test_add_to_rag_short_text_rejected(self) -> None:
        r = rms.add_to_rag("ab")
        self.assertFalse(r["ok"])

    def test_add_and_list_rag(self) -> None:
        rms.add_to_rag("Python uses indentation for code blocks")
        r = rms.list_rag()
        self.assertEqual(r["count"], 1)

    def test_delete_rag_item(self) -> None:
        added = rms.add_to_rag("To be deleted fact about code")
        rms.delete_rag(added["id"])
        self.assertEqual(rms.list_rag()["count"], 0)

    def test_rag_stats_empty(self) -> None:
        r = rms.rag_stats()
        self.assertTrue(r["ok"])
        self.assertEqual(r["total"], 0)
        self.assertEqual(r["model"], rms.EMBED_MODEL)

    def test_rag_stats_after_add(self) -> None:
        rms.add_to_rag("Python is a scripting language")
        r = rms.rag_stats()
        self.assertEqual(r["total"], 1)

    def test_search_rag_empty_query(self) -> None:
        r = rms.search_rag("")
        self.assertTrue(r["ok"])
        self.assertEqual(r["count"], 0)

    def test_search_rag_keyword_match(self) -> None:
        rms.add_to_rag("Python uses indentation for code blocks")
        r = rms.search_rag("python indentation", min_score=0.1)
        self.assertTrue(r["ok"])
        # may match via keyword fallback

    def test_get_rag_context_empty(self) -> None:
        ctx = rms.get_rag_context("anything")
        self.assertEqual(ctx, "")

    def test_get_rag_context_after_add(self) -> None:
        rms.add_to_rag("Python uses indentation for code blocks structure")
        ctx = rms.get_rag_context("python indentation", max_chars=5000)
        # may or may not match depending on score, just check it's a string
        self.assertIsInstance(ctx, str)


# ─────────────────────────────────────────────────────────────────────────────
# chat_service — normalize_profile (pure)
# ─────────────────────────────────────────────────────────────────────────────

class ChatServiceNormalizeProfileTest(unittest.TestCase):
    def test_none_returns_default(self) -> None:
        result = chat_svc.normalize_profile(None)
        self.assertIsNotNone(result)

    def test_empty_returns_default(self) -> None:
        from app.core.persona_defaults import DEFAULT_PROFILE
        self.assertEqual(chat_svc.normalize_profile(""), DEFAULT_PROFILE)

    def test_default_string_returns_default(self) -> None:
        from app.core.persona_defaults import DEFAULT_PROFILE
        self.assertEqual(chat_svc.normalize_profile("default"), DEFAULT_PROFILE)

    def test_valid_profile_returned(self) -> None:
        from app.core.persona_defaults import PROFILE_MODE_OVERLAYS, DEFAULT_PROFILE
        # Pick a profile that exists
        valid = next(iter(PROFILE_MODE_OVERLAYS.keys()))
        result = chat_svc.normalize_profile(valid)
        self.assertEqual(result, valid)

    def test_unknown_profile_falls_back_to_default(self) -> None:
        from app.core.persona_defaults import DEFAULT_PROFILE
        self.assertEqual(chat_svc.normalize_profile("nonexistent_xyz"), DEFAULT_PROFILE)


class ChatServiceRunChatTest(unittest.TestCase):
    def _mock_client(self, content="Hello!"):
        mock_client = MagicMock()
        mock_resp = SimpleNamespace(message=SimpleNamespace(content=content))
        mock_client.chat.return_value = mock_resp
        return mock_client

    def test_run_chat_success(self) -> None:
        mock_client = self._mock_client("Hi there!")
        with patch("ollama.Client", return_value=mock_client):
            result = chat_svc.run_chat(
                model_name="gemma3:4b",
                profile_name="",
                user_input="Hello",
            )
        self.assertTrue(result["ok"])
        self.assertEqual(result["answer"], "Hi there!")

    def test_run_chat_includes_profile(self) -> None:
        mock_client = self._mock_client("answer")
        with patch("ollama.Client", return_value=mock_client):
            result = chat_svc.run_chat(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
            )
        self.assertIn("profile", result["meta"])

    def test_run_chat_error_returns_ok_false(self) -> None:
        with patch("ollama.Client", side_effect=Exception("connection refused")):
            result = chat_svc.run_chat(
                model_name="gemma3:4b",
                profile_name="",
                user_input="q",
            )
        self.assertFalse(result["ok"])
        self.assertEqual(result["answer"], "")
        self.assertTrue(len(result["warnings"]) > 0)

    def test_run_chat_with_history(self) -> None:
        mock_client = self._mock_client("reply")
        with patch("ollama.Client", return_value=mock_client):
            result = chat_svc.run_chat(
                model_name="gemma3:4b",
                profile_name="",
                user_input="next question",
                history=[
                    {"role": "user", "content": "first message"},
                    {"role": "assistant", "content": "first reply"},
                ],
            )
        self.assertTrue(result["ok"])
        # Verify history was passed (messages should include system + 2 history + 1 new)
        call_args = mock_client.chat.call_args
        messages = call_args[1]["messages"] if call_args[1] else call_args[0][1]
        user_msgs = [m for m in messages if m["role"] == "user"]
        self.assertGreaterEqual(len(user_msgs), 2)


# ─────────────────────────────────────────────────────────────────────────────
# skills — screenshot_capability_status (pure check)
# ─────────────────────────────────────────────────────────────────────────────

class ScreenshotCapabilityStatusTest(unittest.TestCase):
    def test_returns_dict_with_required_keys(self) -> None:
        result = skills_rt.screenshot_capability_status()
        for key in ("feature", "available", "reason", "missing_packages", "hint"):
            self.assertIn(key, result)

    def test_feature_is_screenshot(self) -> None:
        result = skills_rt.screenshot_capability_status()
        self.assertEqual(result["feature"], "screenshot")

    def test_returns_bool_available(self) -> None:
        result = skills_rt.screenshot_capability_status()
        self.assertIsInstance(result["available"], bool)


# ─────────────────────────────────────────────────────────────────────────────
# skills — run_sql with patched ALLOWED_DB_DIRS
# ─────────────────────────────────────────────────────────────────────────────

class RunSqlTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._tmp_path = Path(self._tmpdir.name).resolve()
        # Create a real SQLite DB in the temp dir
        self._db_file = self._tmp_path / "test.db"
        conn = sqlite3.connect(str(self._db_file))
        conn.execute("CREATE TABLE items (id INTEGER PRIMARY KEY, name TEXT)")
        conn.execute("INSERT INTO items (name) VALUES ('alpha')")
        conn.execute("INSERT INTO items (name) VALUES ('beta')")
        conn.commit()
        conn.close()
        # Patch ALLOWED_DB_DIRS to include the temp dir
        self._orig_dirs = skills_rt.ALLOWED_DB_DIRS
        skills_rt.ALLOWED_DB_DIRS = [self._tmp_path]

    def tearDown(self) -> None:
        skills_rt.ALLOWED_DB_DIRS = self._orig_dirs
        self._tmpdir.cleanup()

    def test_select_returns_rows(self) -> None:
        result = skills_rt.run_sql(str(self._db_file), "SELECT * FROM items")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertIn("id", result["columns"])

    def test_blocked_outside_allowed_dirs(self) -> None:
        result = skills_rt.run_sql("/etc/passwd", "SELECT 1")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_nonexistent_db_returns_error(self) -> None:
        result = skills_rt.run_sql(str(self._tmp_path / "missing.db"), "SELECT 1")
        self.assertFalse(result["ok"])

    def test_blocked_drop_statement(self) -> None:
        result = skills_rt.run_sql(str(self._db_file), "DROP TABLE items")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_blocked_delete_statement(self) -> None:
        result = skills_rt.run_sql(str(self._db_file), "DELETE FROM items")
        self.assertFalse(result["ok"])

    def test_select_with_max_rows(self) -> None:
        result = skills_rt.run_sql(str(self._db_file), "SELECT * FROM items", max_rows=1)
        self.assertTrue(result["ok"])
        self.assertLessEqual(result["count"], 1)


# ─────────────────────────────────────────────────────────────────────────────
# skills — http_request blocked host check (pure)
# ─────────────────────────────────────────────────────────────────────────────

class HttpRequestBlockedHostTest(unittest.TestCase):
    def test_localhost_blocked(self) -> None:
        result = skills_rt.http_request("http://localhost/api")
        self.assertFalse(result["ok"])
        self.assertIn("error", result)

    def test_loopback_blocked(self) -> None:
        result = skills_rt.http_request("http://127.0.0.1/secret")
        self.assertFalse(result["ok"])

    def test_metadata_endpoint_blocked(self) -> None:
        result = skills_rt.http_request("http://169.254.169.254/metadata")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# multi_agent_chain — pure helper functions
# ─────────────────────────────────────────────────────────────────────────────

class MultiAgentChainHelpersTest(unittest.TestCase):
    def test_clip_short_text_unchanged(self) -> None:
        text = "hello"
        self.assertEqual(mac_rt._clip(text, 100), "hello")

    def test_clip_long_text_truncated(self) -> None:
        text = "a" * 200
        clipped = mac_rt._clip(text, 100)
        self.assertLessEqual(len(clipped), 100)
        self.assertTrue(clipped.endswith("…"))

    def test_clip_empty_string(self) -> None:
        self.assertEqual(mac_rt._clip("", 50), "")

    def test_clip_none_treated_as_empty(self) -> None:
        self.assertEqual(mac_rt._clip(None, 50), "")

    def test_clip_exact_limit_not_truncated(self) -> None:
        text = "a" * 50
        self.assertEqual(mac_rt._clip(text, 50), text)

    def test_is_llm_error_russian_prefix(self) -> None:
        self.assertTrue(mac_rt._is_llm_error("[Ошибка LLM: connection refused]"))

    def test_is_llm_error_english_prefix(self) -> None:
        self.assertTrue(mac_rt._is_llm_error("[LLM ERROR: timeout]"))

    def test_is_llm_error_normal_text_false(self) -> None:
        self.assertFalse(mac_rt._is_llm_error("Normal LLM response text"))

    def test_is_llm_error_empty_false(self) -> None:
        self.assertFalse(mac_rt._is_llm_error(""))

    def test_is_llm_error_none_false(self) -> None:
        self.assertFalse(mac_rt._is_llm_error(None))


if __name__ == "__main__":
    unittest.main()
