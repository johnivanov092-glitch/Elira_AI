"""Ф2 — MemoryService facade (the single "one door" over both engines).

Pins that the facade routes facts to smart_memory and semantic/episodic to
rag_memory, and that `recall()` combines both into one context blob.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application import memory  # noqa: E402


class MemoryFacadeTest(unittest.TestCase):
    def setUp(self) -> None:
        from app.application import smart_memory

        smart_memory.clear_all_memories(profile_name=smart_memory.DEFAULT_PROFILE)

    def tearDown(self) -> None:
        from app.application import smart_memory

        smart_memory.clear_all_memories(profile_name=smart_memory.DEFAULT_PROFILE)

    def test_add_fact_routes_to_smart_memory(self) -> None:
        from app.application import smart_memory

        memory.add_fact("Python мой любимый язык", category="preference")
        found = smart_memory.search_memory("Python", profile_name=smart_memory.DEFAULT_PROFILE)
        self.assertGreaterEqual(found["count"], 1)

    def test_add_semantic_routes_to_rag(self) -> None:
        from app.application.rag_memory import service as rag

        res = memory.add_semantic("уникальный семантический маркер альфа", category="fact")
        self.assertTrue(res.get("ok"))
        items = rag.list_rag(limit=200)["items"]
        self.assertTrue(any("альфа" in (it.get("text") or "") for it in items))

    def test_recall_combines_facts_and_semantic(self) -> None:
        memory.add_fact("Меня зовут Иван", category="fact")
        memory.add_semantic(
            "Обсуждали деплой на сервер 192.168.88.15", category="episode", importance=6
        )
        out = memory.recall("Иван сервер деплой", fact_limit=5, semantic_limit=3)
        self.assertTrue(out["ok"])
        for key in ("facts", "semantic", "context"):
            self.assertIn(key, out)
        # the curated fact surfaces in the combined context
        self.assertIn("Иван", out["context"])

    def test_recall_no_match_returns_empty_context(self) -> None:
        out = memory.recall("совершенно несвязанный запрос zzz", fact_limit=5, semantic_limit=3)
        self.assertTrue(out["ok"])
        self.assertEqual(out["context"], "")

    def test_code_agent_recall_tool_surfaces_facts(self) -> None:
        # Ф3: the code-agent `recall` tool now returns curated facts too, not
        # just RAG — so facts the user told Elira are reachable during a run.
        from app.application.code_agent.tools import tool_recall

        memory.add_fact("Меня зовут Иван Тестовый", category="fact")
        out = tool_recall(Path(BACKEND_ROOT), query="Иван", top_k=5)
        self.assertIn("Иван", out.get("text", ""))
        self.assertIn("Known facts", out.get("text", ""))


if __name__ == "__main__":
    unittest.main()
