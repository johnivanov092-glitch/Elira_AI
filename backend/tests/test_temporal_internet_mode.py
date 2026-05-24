from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.services.planner_v2_service import PlannerV2Service  # noqa: E402
from app.services.provenance_guard import guard_provenance_response  # noqa: E402
from app.services.response_cache import should_cache  # noqa: E402


class TemporalInternetModeTest(unittest.TestCase):
    def setUp(self) -> None:
        self.planner = PlannerV2Service()

    def test_future_year_forces_web_route(self) -> None:
        plan = self.planner.plan("Что с рынком в 2027 году?")

        self.assertEqual(plan["route"], "research")
        self.assertIn("web_search", plan["tools"])
        self.assertEqual(plan["temporal"]["mode"], "hard")
        self.assertTrue(plan["temporal"]["requires_web"])

    def test_past_explicit_year_defaults_to_soft_temporal(self) -> None:
        plan = self.planner.plan("Цены на жилье в 2021 году")

        self.assertIn("web_search", plan["tools"])
        self.assertEqual(plan["temporal"]["mode"], "soft")
        self.assertTrue(plan["temporal"]["requires_web"])

    def test_stable_historical_question_does_not_force_web(self) -> None:
        plan = self.planner.plan("Что произошло в 1991 году?")

        self.assertEqual(plan["temporal"]["mode"], "stable_historical")
        self.assertFalse(plan["temporal"]["requires_web"])
        self.assertNotIn("web_search", plan["tools"])

    def test_multi_intent_current_world_query_builds_web_plan(self) -> None:
        plan = self.planner.plan("курс доллара к тенге на сегодня и новости происшествия Алматы за 2 дня")

        self.assertIn("web_search", plan["tools"])
        self.assertTrue(plan["temporal"]["requires_web"])
        self.assertTrue(plan["web_plan"]["is_multi_intent"])
        self.assertEqual(len(plan["web_plan"]["subqueries"]), 2)

    def test_temporal_queries_are_not_cacheable(self) -> None:
        self.assertFalse(should_cache("Что с рынком в 2027 году?", "chat"))
        self.assertFalse(should_cache("Какая сегодня цена нефти?", "research"))
        self.assertTrue(should_cache("Объясни разницу между JSON и YAML", "chat"))

    def test_provenance_guard_hides_internal_markers_in_normal_reply(self) -> None:
        guarded = guard_provenance_response(
            "Как меня зовут?",
            'Да, знаю. Из моей памяти, как я получила эту информацию: "Меня зовут Евгений".\n[fact] Меня зовут Евгений.\nRelevant user memory:\n- Меня зовут Евгений\nRAG',
        )

        self.assertNotIn("[fact]", guarded["text"])
        self.assertNotIn("Relevant user memory", guarded["text"])
        self.assertNotIn("RAG", guarded["text"])
        self.assertNotIn("из моей памяти", guarded["text"].lower())

    def test_provenance_guard_keeps_natural_answer_when_source_is_requested(self) -> None:
        guarded = guard_provenance_response(
            "Откуда ты знаешь?",
            'Из моей памяти, как я получила эту информацию: "Меня зовут Евгений".\n[fact] Меня зовут Евгений.',
        )

        self.assertNotIn("[fact]", guarded["text"])
        self.assertNotIn("RAG", guarded["text"])
        self.assertNotIn("Relevant user memory", guarded["text"])
        self.assertTrue(guarded["provenance_question"])

    def test_provenance_guard_hides_visible_source_urls_in_normal_reply(self) -> None:
        guarded = guard_provenance_response(
            "Новости за сегодня по Алматы",
            "По состоянию на сегодня в Алматы обсуждают транспорт и погоду. Источник: https://example.com/news\nЕще один источник: [сайт](https://example.com/alt)",
        )

        self.assertNotIn("http://", guarded["text"])
        self.assertNotIn("https://", guarded["text"])
        self.assertNotIn("Источник:", guarded["text"])

    def test_provenance_guard_rewrites_direct_name_answer_naturally(self) -> None:
        guarded = guard_provenance_response(
            "Как меня зовут?",
            "Меня зовут Евгений. Это подтверждено несколькими источниками, включая данные, которые мы сейчас рассматриваем.",
        )

        self.assertEqual(guarded["text"], "Тебя зовут Евгений.")

    def test_smart_memory_context_has_no_fact_tags(self) -> None:
        with tempfile.TemporaryDirectory() as data_dir:
            script = textwrap.dedent(
                f"""
                import json
                import sys
                sys.path.insert(0, r"{BACKEND_ROOT}")

                from app.services.smart_memory import add_memory, get_relevant_context

                add_memory("Меня зовут Евгений", category="fact", profile_name="default")
                payload = {{"context": get_relevant_context("Как меня зовут?", profile_name="default")}}
                print(json.dumps(payload, ensure_ascii=False))
                """
            )

            env = os.environ.copy()
            env["ELIRA_DATA_DIR"] = data_dir

            proc = subprocess.run(
                [sys.executable, "-c", script],
                cwd=str(ROOT),
                env=env,
                capture_output=True,
                text=True,
                timeout=120,
            )

            self.assertEqual(proc.returncode, 0, proc.stderr)
            payload = json.loads(proc.stdout.strip())
            context = payload["context"]

            self.assertNotIn("[fact]", context)
            self.assertNotIn("Relevant user memory", context)
            self.assertNotIn("RAG", context)
            self.assertIn("Меня зовут Евгений", context)


if __name__ == "__main__":
    unittest.main()
