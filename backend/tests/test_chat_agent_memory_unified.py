"""Ф1 — unified facts store.

The "Память чата" API (/api/chat-agent/memory) is now backed by the same
`smart_memory` store the agent's `search_memory` tool reads. These pin that a
fact written via the chat-agent route is visible through smart_memory (and vice
versa) — i.e. the UI and recall are no longer split-brain.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.api.routes import chat_agent as route  # noqa: E402
from app.application import smart_memory  # noqa: E402


class ChatAgentMemoryUnifiedTest(unittest.TestCase):
    def setUp(self) -> None:
        smart_memory.clear_all_memories(profile_name=smart_memory.DEFAULT_PROFILE)

    def tearDown(self) -> None:
        smart_memory.clear_all_memories(profile_name=smart_memory.DEFAULT_PROFILE)

    def test_fact_added_via_route_is_visible_in_smart_memory(self) -> None:
        resp = route.memory_add(
            route.MemoryCreateRequest(text="Мой любимый язык — Python", category="preference")
        )
        self.assertTrue(resp["ok"])
        # Same store the recall tool queries — no separate chat_agent.db copy.
        found = smart_memory.search_memory("Python", profile_name=smart_memory.DEFAULT_PROFILE)
        self.assertGreaterEqual(found["count"], 1)
        self.assertTrue(any("Python" in (it.get("text") or "") for it in found["items"]))

    def test_route_list_reflects_smart_memory_writes(self) -> None:
        # A fact written straight to smart_memory (e.g. by auto-extract) must
        # show up in the "Память чата" UI listing.
        smart_memory.add_memory(
            "Сервер живёт на 192.168.88.15",
            category="fact",
            profile_name=smart_memory.DEFAULT_PROFILE,
        )
        items = route.memory_list(limit=100)["items"]
        self.assertTrue(any("192.168.88.15" in (it.get("text") or "") for it in items))

    def test_delete_via_route(self) -> None:
        add = route.memory_add(route.MemoryCreateRequest(text="временный факт для удаления"))
        mem_id = add["item"]["id"]
        resp = route.memory_delete(str(mem_id))
        self.assertTrue(resp["ok"])
        items = route.memory_list(limit=100)["items"]
        self.assertFalse(any(int(it["id"]) == int(mem_id) for it in items))


if __name__ == "__main__":
    unittest.main()
