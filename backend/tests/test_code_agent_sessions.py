"""Tests for the SQLite-backed code-agent session store."""
from __future__ import annotations

import importlib
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class CodeSessionStoreTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        # Re-import the module with a fresh DB pointing to the temp dir.
        # We patch the DB path by setting ELIRA_DATA_DIR before import.
        import os
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name
        # Reload data_files so it picks up new ELIRA_DATA_DIR
        from app.core import data_files
        importlib.reload(data_files)
        from app.application.code_agent import sessions
        importlib.reload(sessions)
        self.sessions = sessions

    def tearDown(self) -> None:
        self._tmp.cleanup()
        import os
        os.environ.pop("ELIRA_DATA_DIR", None)

    def test_create_and_get(self) -> None:
        sess = self.sessions.create_session(title="Test session", model="qwen2.5-coder:7b", project_root="C:/x")
        self.assertEqual(sess["title"], "Test session")
        self.assertEqual(sess["model"], "qwen2.5-coder:7b")
        self.assertEqual(sess["project_root"], "C:/x")
        self.assertFalse(sess["pinned"])
        self.assertEqual(sess["turns"], [])
        fetched = self.sessions.get_session(sess["id"])
        self.assertIsNotNone(fetched)
        self.assertEqual(fetched["title"], "Test session")

    def test_list_orders_pinned_first(self) -> None:
        a = self.sessions.create_session(title="A")
        b = self.sessions.create_session(title="B")
        c = self.sessions.create_session(title="C")
        # Pin B
        self.sessions.update_session(b["id"], {"pinned": True})
        # Touch A to bump its updated_at (so it's most recent un-pinned)
        self.sessions.update_session(a["id"], {"title": "A updated"})
        items = self.sessions.list_sessions()
        self.assertEqual(items[0]["id"], b["id"])  # pinned first
        self.assertTrue(items[0]["pinned"])
        # After pinned, unpinned by most-recent
        unpinned_ids = [s["id"] for s in items if not s["pinned"]]
        self.assertEqual(unpinned_ids[0], a["id"])  # A is more recently updated than C

    def test_update_turns_persists_json(self) -> None:
        sess = self.sessions.create_session(title="T")
        turns = [
            {"kind": "user", "id": "u1", "text": "hi", "ts": 1},
            {"kind": "agent", "id": "a1", "text": "hello", "ts": 2, "tool_calls": []},
        ]
        self.sessions.update_session(sess["id"], {"turns": turns})
        fetched = self.sessions.get_session(sess["id"])
        self.assertEqual(len(fetched["turns"]), 2)
        self.assertEqual(fetched["turns"][1]["text"], "hello")

    def test_delete(self) -> None:
        sess = self.sessions.create_session(title="X")
        self.assertTrue(self.sessions.delete_session(sess["id"]))
        self.assertIsNone(self.sessions.get_session(sess["id"]))
        self.assertFalse(self.sessions.delete_session(sess["id"]))  # already gone

    def test_search_by_title(self) -> None:
        self.sessions.create_session(title="refactor auth")
        self.sessions.create_session(title="write tests for parser")
        self.sessions.create_session(title="another auth thing")
        hits = self.sessions.list_sessions(query="auth")
        titles = [s["title"] for s in hits]
        self.assertEqual(len(hits), 2)
        self.assertTrue(all("auth" in t.lower() for t in titles))

    def test_patch_unknown_keys_ignored(self) -> None:
        sess = self.sessions.create_session(title="ok")
        # Should not raise even with junk keys
        self.sessions.update_session(sess["id"], {"nonexistent": 123, "title": "ok2"})
        fetched = self.sessions.get_session(sess["id"])
        self.assertEqual(fetched["title"], "ok2")

    def test_unicode_title_round_trips(self) -> None:
        sess = self.sessions.create_session(title="Привет 🌅 закат")
        fetched = self.sessions.get_session(sess["id"])
        self.assertEqual(fetched["title"], "Привет 🌅 закат")


if __name__ == "__main__":
    unittest.main()
