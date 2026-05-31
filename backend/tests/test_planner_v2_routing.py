"""Snapshot tests for PlannerV2 routing decisions.

Each row is (query, expected_route). When routes change, intentional
edits update the snapshot. Accidental edits break the build.
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.chat.planner_v2 import PlannerV2Service  # noqa: E402


# Format: (query, expected_route, tag).
# Tags are categories — keep them so it's obvious what the test exercises.
CASES: list[tuple[str, str, str]] = [
    # ── code: simple write/fix ─────────────────────────────────────
    ("напиши функцию sort", "code", "code"),
    ("напиши код на python для сортировки", "code", "code"),
    ("исправь баг в auth.py", "code", "code"),
    ("fix this bug in the parser", "code", "code"),
    ("реализуй метод save", "code", "code"),
    ("refactor this method", "code", "code"),
    ("оптимизируй функцию add", "code", "code"),
    ("напиши тесты для bar.py", "code", "code"),
    ("debug this issue", "code", "code"),

    # ── code_agent: multi-step file operations ─────────────────────
    ("прочитай foo.py и поправь баг", "code_agent", "code_agent"),
    ("создай тест и запусти", "code_agent", "code_agent"),
    ("проверь и почини всё что не работает", "code_agent", "code_agent"),
    ("найди и замени все old_api на new_api", "code_agent", "code_agent"),

    # ── multi_agent: complex orchestration ─────────────────────────
    ("распиши план рефакторинга auth модуля", "multi_agent", "multi_agent"),
    ("сделай полный анализ кодовой базы", "multi_agent", "multi_agent"),
    ("проведи ревью всего проекта", "multi_agent", "multi_agent"),
    ("аудит кода всей системы", "multi_agent", "multi_agent"),
    ("исследуй и реализуй фичу auth", "multi_agent", "multi_agent"),

    # ── project: file exploration ──────────────────────────────────
    ("покажи структуру проекта", "project", "project"),
    ("что в директории backend", "project", "project"),
    ("открой проект", "project", "project"),
    ("какие модули в frontend", "project", "project"),

    # ── research: factual web queries ──────────────────────────────
    ("что нового в python 3.13", "research", "research"),
    ("кто такой Линус Торвальдс", "research", "research"),
    ("найди документацию по Tauri", "research", "research"),
    ("сравни fastapi и flask", "research", "research"),
    ("последние новости про AMD ROCm", "research", "research"),
    ("курс биткоина сейчас", "research", "research"),
    ("погода в Москве", "research", "research"),

    # ── chat: small talk + creative + translation ──────────────────
    ("привет", "chat", "chat"),
    ("как тебя зовут", "chat", "chat"),
    ("спасибо", "chat", "chat"),
    ("переведи на английский: hello world", "chat", "chat"),
    ("придумай слоган для стартапа", "chat", "chat"),
    ("объясни разницу между map и forEach", "chat", "chat"),
    ("напиши письмо коллеге", "chat", "chat"),

    # ── image: image generation ────────────────────────────────────
    ("нарисуй кошку в стиле van gogh", "image", "image"),
    ("сгенерируй картинку заката над морем", "image", "image"),
    ("draw a dragon", "image", "image"),
    ("создай иллюстрацию робота", "image", "image"),

    # ── false-positive guards ──────────────────────────────────────
    ("barcode reader implementation", "code", "guard"),  # "implementation" wins → code (intentional)
    ("штрихкод формата EAN-13", "chat", "guard"),  # No real keyword hit; should be chat
    ("декодируй base64", "code", "guard"),  # legitimate decode action — code route is fine
    ("latest news about React 19", "research", "guard"),
]


class PlannerSnapshotTest(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = PlannerV2Service()

    def test_routing_decisions(self) -> None:
        wrong: list[str] = []
        for query, expected_route, tag in CASES:
            plan = self.planner.plan(query)
            got = plan["route"]
            if got != expected_route:
                wrong.append(f"  [{tag}] '{query}' → expected={expected_route} got={got}  scores={plan.get('scores')}")
        self.assertFalse(wrong, "Routing snapshot drift:\n" + "\n".join(wrong))

    def test_empty_query_is_chat(self) -> None:
        plan = self.planner.plan("")
        self.assertEqual(plan["route"], "chat")
        self.assertEqual(plan["tools"], [])

    def test_image_route_has_image_gen_tool(self) -> None:
        plan = self.planner.plan("нарисуй закат")
        self.assertEqual(plan["route"], "image")
        self.assertIn("image_gen", plan["tools"])

    def test_code_agent_route_has_loop_tool(self) -> None:
        plan = self.planner.plan("прочитай foo.py и поправь баг")
        self.assertEqual(plan["route"], "code_agent")
        self.assertIn("code_agent_loop", plan["tools"])

    def test_multi_agent_route_has_workflow_tool(self) -> None:
        plan = self.planner.plan("распиши план рефакторинга auth")
        self.assertEqual(plan["route"], "multi_agent")
        self.assertIn("multi_agent_workflow", plan["tools"])

    def test_word_boundary_no_barcode_false_positive(self) -> None:
        """'штрихкод' should NOT trigger 'код' keyword."""
        plan = self.planner.plan("штрихкод EAN-13")
        # 'код*' is a prefix matcher — but 'штрихкод' has 'код' at the end,
        # not at word start. The (?<!\w) lookbehind must prevent the match.
        # Expected: code score is 0 (or at most a weak hit from a different
        # keyword), route is chat.
        self.assertEqual(plan["route"], "chat", f"scores={plan['scores']}")

    def test_weights_favour_longer_phrases(self) -> None:
        """Multi-word triggers should outweigh single-word noise."""
        plan = self.planner.plan("напиши код для парсинга")
        # 'напиши код' (weight 3) should dominate
        self.assertEqual(plan["route"], "code")
        self.assertGreaterEqual(plan["scores"]["code"], 3)

    def test_chat_only_suppresses_weak_research(self) -> None:
        """Strong chat-only markers should keep us in chat even if a weak
        research keyword sneaks in."""
        plan = self.planner.plan("привет, как дела, спасибо")
        self.assertEqual(plan["route"], "chat")


if __name__ == "__main__":
    unittest.main()
