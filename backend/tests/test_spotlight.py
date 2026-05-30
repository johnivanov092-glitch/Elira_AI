"""Tests for the Spotlight global search service.

Each test sets ELIRA_DATA_DIR to a temp dir and reloads the four
source modules so their DB connections point at clean databases.
That lets us seed exactly the rows we want and assert on the
shape of search_everywhere's output deterministically.
"""
from __future__ import annotations

import importlib
import os
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class SpotlightSearchTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmp = tempfile.TemporaryDirectory()
        os.environ["ELIRA_DATA_DIR"] = self._tmp.name

        from app.core import data_files
        importlib.reload(data_files)

        # Reload every source module so its module-level DB_PATH
        # picks up the new DATA_DIR.
        from app.application.elira_memory import service as elira_memory_service
        importlib.reload(elira_memory_service)
        elira_memory_service.init_db()

        from app.application.code_agent import sessions as code_sessions
        importlib.reload(code_sessions)

        from app.application.rag_memory import service as rag_service
        importlib.reload(rag_service)

        from app.infrastructure.db import library_db
        importlib.reload(library_db)

        # Spotlight imports the others — reload last so it captures
        # the new DB paths via its inner imports.
        from app.application.spotlight import runtime as spotlight_runtime
        importlib.reload(spotlight_runtime)

        self.spotlight = spotlight_runtime
        self.elira_memory = elira_memory_service
        self.code_sessions = code_sessions
        self.rag_service = rag_service
        self.library_db_path = library_db.DB_PATH

    def tearDown(self) -> None:
        self._tmp.cleanup()
        os.environ.pop("ELIRA_DATA_DIR", None)

    # ── helpers ────────────────────────────────────────────────

    def _seed_chat(self, title: str, messages: list[tuple[str, str]] | None = None) -> int:
        chat = self.elira_memory.create_chat(title=title)
        cid = chat["id"]
        for role, content in messages or []:
            self.elira_memory.add_message(cid, role, content)
        return cid

    def _seed_session(self, title: str, turn_text: str = "") -> str:
        sess = self.code_sessions.create_session(title=title)
        if turn_text:
            self.code_sessions.update_session(
                sess["id"],
                {"turns": [{"kind": "user", "id": "u1", "text": turn_text, "ts": 1}]},
            )
        return sess["id"]

    def _seed_library_file(self, name: str, preview: str) -> None:
        conn = sqlite3.connect(self.library_db_path)
        try:
            conn.execute(
                "INSERT INTO files (name, size, type, preview, use_in_context, source) "
                "VALUES (?, ?, ?, ?, ?, ?)",
                (name, len(preview), "text/plain", preview, 1, "upload"),
            )
            conn.commit()
        finally:
            conn.close()

    # ── shape ──────────────────────────────────────────────────

    def test_empty_query_returns_all_empty_buckets(self) -> None:
        result = self.spotlight.search_everywhere("")
        self.assertEqual(result["chats"], [])
        self.assertEqual(result["sessions"], [])
        self.assertEqual(result["rag"], [])
        self.assertEqual(result["files"], [])
        self.assertEqual(result["total"], 0)

    def test_one_char_query_returns_empty(self) -> None:
        """Single-char queries are too noisy — verified by impl threshold."""
        self._seed_chat("about authentication")
        result = self.spotlight.search_everywhere("a")
        self.assertEqual(result["total"], 0)

    def test_response_always_has_all_four_buckets(self) -> None:
        result = self.spotlight.search_everywhere("anything")
        for key in ("chats", "sessions", "rag", "files", "query", "total"):
            self.assertIn(key, result)

    # ── chats ──────────────────────────────────────────────────

    def test_chat_title_match(self) -> None:
        self._seed_chat("Discussion about auth.py")
        self._seed_chat("Other topic")
        result = self.spotlight.search_everywhere("auth")
        titles = [c["title"] for c in result["chats"]]
        self.assertIn("Discussion about auth.py", titles)
        self.assertNotIn("Other topic", titles)

    def test_chat_message_body_match(self) -> None:
        self._seed_chat(
            "Random title",
            messages=[("user", "How do I parse JSON with structured tokens?")],
        )
        result = self.spotlight.search_everywhere("structured")
        self.assertEqual(len(result["chats"]), 1)
        # Snippet should contain (or surround) the match
        self.assertIn("structured", result["chats"][0]["snippet"].lower())

    def test_chat_title_takes_priority_over_body_dedup(self) -> None:
        """If both title AND a message in the same chat match, the
        chat should appear exactly once, with empty snippet (title hit
        is treated as the canonical entry)."""
        self._seed_chat(
            "auth flow",
            messages=[("user", "auth question here")],
        )
        result = self.spotlight.search_everywhere("auth")
        # Only one result for this chat
        self.assertEqual(len([c for c in result["chats"] if c["title"] == "auth flow"]), 1)

    def test_chat_case_insensitive(self) -> None:
        self._seed_chat("My Important Chat")
        result_lower = self.spotlight.search_everywhere("important")
        result_upper = self.spotlight.search_everywhere("IMPORTANT")
        self.assertEqual(len(result_lower["chats"]), 1)
        self.assertEqual(len(result_upper["chats"]), 1)

    # ── code-agent sessions ───────────────────────────────────

    def test_session_title_match(self) -> None:
        self._seed_session("refactor auth module")
        self._seed_session("unrelated work")
        result = self.spotlight.search_everywhere("refactor")
        titles = [s["title"] for s in result["sessions"]]
        self.assertIn("refactor auth module", titles)
        self.assertNotIn("unrelated work", titles)

    def test_session_turn_text_match(self) -> None:
        self._seed_session("plain title", turn_text="Let's fix the WebSocket reconnect bug")
        result = self.spotlight.search_everywhere("websocket")
        self.assertEqual(len(result["sessions"]), 1)
        self.assertIn("WebSocket", result["sessions"][0]["snippet"])

    # ── library files ─────────────────────────────────────────

    def test_library_filename_match(self) -> None:
        self._seed_library_file("auth_notes.md", "some content about authentication")
        self._seed_library_file("other.txt", "random text")
        result = self.spotlight.search_everywhere("auth_notes")
        names = [f["title"] for f in result["files"]]
        self.assertIn("auth_notes.md", names)
        self.assertNotIn("other.txt", names)

    def test_library_preview_match(self) -> None:
        self._seed_library_file("plain.md", "this file talks about KAFKA consumer lag")
        result = self.spotlight.search_everywhere("kafka")
        self.assertEqual(len(result["files"]), 1)
        self.assertIn("KAFKA", result["files"][0]["snippet"])

    # ── per-source limit ──────────────────────────────────────

    def test_per_source_limit_enforced(self) -> None:
        for i in range(12):
            self._seed_chat(f"common-term-{i}")
        result = self.spotlight.search_everywhere("common")
        # Per-source limit is 5 — see _PER_SOURCE_LIMIT
        self.assertLessEqual(len(result["chats"]), 5)

    # ── total ────────────────────────────────────────────────

    def test_total_matches_sum_of_buckets(self) -> None:
        self._seed_chat("auth chat")
        self._seed_session("auth session")
        result = self.spotlight.search_everywhere("auth")
        self.assertEqual(
            result["total"],
            len(result["chats"]) + len(result["sessions"]) + len(result["rag"]) + len(result["files"]),
        )

    # ── unicode ───────────────────────────────────────────────

    def test_cyrillic_query_works(self) -> None:
        self._seed_chat("Заметки про авторизацию")
        result = self.spotlight.search_everywhere("авториз")
        self.assertEqual(len(result["chats"]), 1)

    # ── graceful degrade ──────────────────────────────────────

    def test_broken_chat_db_doesnt_break_other_sources(self) -> None:
        """If one source's DB is malformed, the others still return
        results — Spotlight should never propagate a 500 to the UI."""
        # Seed a session that should still match
        self._seed_session("auth session")

        # Corrupt chats DB by writing garbage over its file
        self.elira_memory.DB_PATH.write_bytes(b"not a sqlite file at all")

        # The whole query should still succeed
        result = self.spotlight.search_everywhere("auth")
        self.assertEqual(result["chats"], [])
        # Sessions bucket still populated
        self.assertEqual(len(result["sessions"]), 1)


if __name__ == "__main__":
    unittest.main()
