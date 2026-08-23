from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


class MemoryPolicyTest(unittest.TestCase):
    def test_current_operational_state_is_volatile(self) -> None:
        from app.application.memory.policy import is_volatile_fact

        samples = (
            "Активная модель сейчас Qwen3.6-27B.",
            "VRAM занято 30.8 GB, свободно 3.4 GB.",
            "Uptime сервера 38 минут.",
            "Последняя перезагрузка: 14.07.2026 18:09.",
        )
        for text in samples:
            with self.subTest(text=text):
                self.assertTrue(is_volatile_fact(text))

    def test_durable_identity_and_preferences_are_not_volatile(self) -> None:
        from app.application.memory.policy import is_volatile_fact

        self.assertFalse(is_volatile_fact("Пользователя зовут Евгений."))
        self.assertFalse(is_volatile_fact("Пользователь предпочитает краткие технические ответы."))
        self.assertFalse(is_volatile_fact("Elira использует женский род."))

    def test_dynamic_fact_category_is_normalized_on_write(self) -> None:
        from app.application.memory.policy import normalize_fact_category

        self.assertEqual(
            normalize_fact_category("Текущая активная модель — 27B.", "user_fact"),
            "volatile_fact",
        )
        self.assertEqual(
            normalize_fact_category("Пользователя зовут Евгений.", "user_fact"),
            "user_fact",
        )

    def test_stopped_service_state_is_volatile(self) -> None:
        from app.application.memory.policy import is_volatile_fact

        self.assertTrue(is_volatile_fact("Plex установлен, но не запущен."))


class AuthoritativeRecallTest(unittest.TestCase):
    def test_authoritative_facts_are_relevant_and_exclude_volatile_rows(self) -> None:
        from app.application.memory import facade

        rows = [
            {
                "id": 1,
                "text": "Активная модель сейчас 27B.",
                "category": "user_fact",
                "source": "user_correction",
            },
            {
                "id": 2,
                "text": "Пользователь предпочитает Python для автоматизации.",
                "category": "preference",
                "source": "user",
            },
            {
                "id": 3,
                "text": "Нерелевантный факт про сад.",
                "category": "user_fact",
                "source": "user",
            },
        ]
        with patch.object(facade, "search_facts", return_value={"ok": True, "items": rows}):
            selected = facade.authoritative_facts("Python автоматизация", limit=5)

        self.assertEqual([item["id"] for item in selected], [2, 3])
        self.assertNotIn(1, [item["id"] for item in selected])

    def test_fact_context_never_injects_volatile_state(self) -> None:
        from app.application.memory import facade

        rows = [
            {
                "id": 1,
                "text": "VRAM сейчас занято 30 GB.",
                "category": "user_fact",
                "source": "user",
            },
            {
                "id": 2,
                "text": "Пользователь предпочитает Python.",
                "category": "preference",
                "source": "user",
            },
        ]
        with patch.object(facade, "authoritative_facts", return_value=[rows[1]]):
            context = facade.fact_context("Python", max_items=5)

        self.assertIn("предпочитает Python", context)
        self.assertNotIn("VRAM", context)


class VerifiedTurnMemoryTest(unittest.TestCase):
    def test_machine_generated_verified_turns_are_in_default_decay_scope(self) -> None:
        from app.application.rag_memory import service

        with patch.object(service.rag_runtime, "prune_rag", return_value={"ok": True}) as prune:
            service.prune_rag()

        self.assertEqual(prune.call_args.kwargs["max_importance"], 4)
        self.assertEqual(
            prune.call_args.kwargs["categories"],
            ("agent_turn", "verified_turn"),
        )

    def test_unverified_or_read_only_turn_is_not_auto_remembered(self) -> None:
        from app.application.code_agent import loop_helpers

        with patch("app.application.rag_memory.service.add_to_rag") as add:
            loop_helpers._try_remember_turn(
                user_message="объясни проект",
                response_text="свободный ответ модели",
                project_root=BACKEND_ROOT,
                verified=False,
                mutation_targets=("src/app.py",),
            )
            loop_helpers._try_remember_turn(
                user_message="проверь проект",
                response_text="свободный ответ модели",
                project_root=BACKEND_ROOT,
                verified=True,
                mutation_targets=(),
            )

        add.assert_not_called()

    def test_verified_mutation_uses_deterministic_summary_not_model_prose(self) -> None:
        from app.application.code_agent import loop_helpers

        with patch("app.application.rag_memory.service.add_to_rag") as add:
            loop_helpers._try_remember_turn(
                user_message="исправь parser.py",
                response_text="ВЫДУМАННЫЙ ИТОГ МОДЕЛИ",
                project_root=BACKEND_ROOT,
                verified=True,
                mutation_targets=("parser.py", "parser.py"),
                verification_targets=("pytest -q",),
            )

        self.assertEqual(add.call_count, 1)
        kwargs = add.call_args.kwargs
        self.assertEqual(kwargs["category"], "verified_turn")
        self.assertIn("parser.py", kwargs["text"])
        self.assertIn("pytest -q", kwargs["text"])
        self.assertNotIn("ВЫДУМАННЫЙ", kwargs["text"])


class RecallPresentationTest(unittest.TestCase):
    def test_explicit_recall_labels_volatile_fact_for_live_recheck(self) -> None:
        from app.application.code_agent.tools import tool_recall

        with (
            patch(
                "app.application.memory.search_facts",
                return_value={
                    "ok": True,
                    "items": [{
                        "text": "Активная модель сейчас 27B.",
                        "category": "volatile_fact",
                    }],
                },
            ),
            patch(
                "app.application.memory.search_semantic",
                return_value={"ok": True, "items": []},
            ),
        ):
            result = tool_recall(BACKEND_ROOT, query="активная модель")

        self.assertIn("требуется live-проверка", result["text"])

    def test_remember_reports_volatile_classification_honestly(self) -> None:
        from app.application.code_agent.tools import tool_remember

        with patch(
            "app.application.memory.add_fact",
            return_value={"ok": True, "category": "volatile_fact", "id": 1},
        ):
            result = tool_remember(
                BACKEND_ROOT,
                fact="Активная модель сейчас 27B.",
                correction=True,
            )

        self.assertTrue(result["ok"])
        self.assertIn("временное состояние", result["text"])
        self.assertIn("live-проверка", result["text"])


class PromptMemorySelectionTest(unittest.TestCase):
    def test_prompt_uses_relevant_authoritative_selector(self) -> None:
        from app.application.code_agent.prompts import _build_system_prompt

        selected = [{
            "text": "Пользователь предпочитает Python.",
            "source": "user",
            "category": "preference",
        }]
        with (
            patch(
                "app.application.monitoring.runtime.list_accepted_candidates",
                return_value=[],
            ),
            patch(
                "app.application.memory.authoritative_facts",
                return_value=selected,
            ) as authoritative,
        ):
            prompt = _build_system_prompt(
                BACKEND_ROOT,
                active_tools=("read_file",),
                task_text="Исправь Python parser",
            )

        authoritative.assert_called_once_with("Исправь Python parser", limit=8)
        self.assertIn("Пользователь предпочитает Python", prompt)


if __name__ == "__main__":
    unittest.main()
