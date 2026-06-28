"""Tests for chat reflection → episodic memory.

  1. runtime.reflect_chat: pure orchestration with injected fakes —
     skip-too-short, store-episode (scoped + prior cleared), fail-on-empty.
  2. service.reflect_chat: end-to-end over the isolated test DBs with the
     local LLM disabled (exercises the deterministic fallback summarizer).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.rag_memory import reflection  # noqa: E402


class ReflectChatRuntimeTest(unittest.TestCase):
    def _fakes(self) -> tuple[Any, Any, list[dict], list[tuple]]:
        added: list[dict] = []
        deleted: list[tuple] = []

        def add(**kw: Any) -> dict:
            added.append(kw)
            return {"ok": True, "id": 1, "action": "created"}

        def delete_prior(project: str, category: str) -> int:
            deleted.append((project, category))
            return 0

        return add, delete_prior, added, deleted

    def test_skips_short_chat(self) -> None:
        add, delete_prior, added, _ = self._fakes()
        res = reflection.reflect_chat(
            chat_id=7,
            chat_title="t",
            messages=[{"role": "user", "content": "hi"}],
            summarize_fn=lambda turns: {"ok": True, "summary": "S"},
            add_to_rag_func=add,
            delete_prior_func=delete_prior,
            min_messages=4,
        )
        self.assertEqual(res["action"], "skipped")
        self.assertEqual(added, [])

    def test_stores_episode_scoped_to_chat(self) -> None:
        add, delete_prior, added, deleted = self._fakes()
        msgs = [
            {"role": "user" if i % 2 == 0 else "assistant", "content": f"m{i}"}
            for i in range(6)
        ]
        res = reflection.reflect_chat(
            chat_id=42,
            chat_title="My chat",
            messages=msgs,
            summarize_fn=lambda turns: {"ok": True, "summary": "We discussed X"},
            add_to_rag_func=add,
            delete_prior_func=delete_prior,
            min_messages=4,
        )
        self.assertEqual(res["action"], "stored")
        self.assertEqual(len(added), 1)
        self.assertEqual(added[0]["category"], "episode")
        self.assertEqual(added[0]["project"], "chat:42")
        self.assertIn("We discussed X", added[0]["text"])
        self.assertIn("My chat", added[0]["text"])
        # prior episode for this chat must be cleared before writing (idempotent)
        self.assertEqual(deleted, [("chat:42", "episode")])

    def test_failed_summary_writes_nothing(self) -> None:
        add, delete_prior, added, _ = self._fakes()
        msgs = [{"role": "user", "content": f"m{i}"} for i in range(6)]
        res = reflection.reflect_chat(
            chat_id=1,
            chat_title="t",
            messages=msgs,
            summarize_fn=lambda turns: {"ok": True, "summary": "   "},
            add_to_rag_func=add,
            delete_prior_func=delete_prior,
            min_messages=4,
        )
        self.assertEqual(res["action"], "failed")
        self.assertEqual(added, [])


class ReflectChatServiceTest(unittest.TestCase):
    """End-to-end on isolated DBs; local LLM is disabled in the suite, so this
    drives the deterministic fallback summarizer."""

    def setUp(self) -> None:
        from app.application.elira_memory.service import create_chat, add_message

        chat = create_chat("Поездка в горы")
        self.cid = int(chat["id"])
        add_message(self.cid, "user", "Я планирую поездку в горы летом")
        add_message(self.cid, "assistant", "Отличная идея, какие даты?")
        add_message(self.cid, "user", "Скорее всего июль, люблю палатки")
        add_message(self.cid, "assistant", "Понял: июль и кемпинг")

    def tearDown(self) -> None:
        # Keep the shared session DBs clean for other tests.
        try:
            from app.application.elira_memory.service import delete_chat
            from app.application.rag_memory.service import _conn

            delete_chat(self.cid)
            conn = _conn()
            try:
                conn.execute("DELETE FROM rag_items WHERE project = ?", (f"chat:{self.cid}",))
                conn.commit()
            finally:
                conn.close()
        except Exception:
            pass

    def test_reflect_creates_episode_and_marks_saved(self) -> None:
        from app.application.elira_memory.service import list_chats
        from app.application.rag_memory.service import list_rag, reflect_chat

        res = reflect_chat(self.cid)
        self.assertEqual(res["action"], "stored")
        self.assertEqual(res["project"], f"chat:{self.cid}")

        episodes = [
            it for it in list_rag(limit=200)["items"]
            if it.get("category") == "episode" and "горы" in (it.get("text") or "")
        ]
        self.assertTrue(episodes, "expected an episode row containing chat content")

        saved = next(c for c in list_chats() if int(c["id"]) == self.cid)["memory_saved"]
        self.assertTrue(saved)


if __name__ == "__main__":
    unittest.main()
