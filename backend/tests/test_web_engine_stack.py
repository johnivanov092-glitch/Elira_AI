from __future__ import annotations

import os
import sys
import unittest
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.core.web import DEFAULT_SEARCH_ENGINES, SUPPORTED_SEARCH_ENGINES, _rerank_results, get_web_engine_status, resolve_search_engines, search_web  # noqa: E402
from app.main import app  # noqa: E402


EXPECTED = ("searxng", "duckduckgo", "wikipedia")


class WebEngineStackTest(unittest.TestCase):
    def test_supported_and_default_engines_match_expected_stack(self) -> None:
        self.assertEqual(tuple(SUPPORTED_SEARCH_ENGINES), EXPECTED)
        self.assertEqual(tuple(DEFAULT_SEARCH_ENGINES), EXPECTED)

    def test_runtime_falls_back_to_duckduckgo_without_searxng(self) -> None:
        with patch.dict(os.environ, {"SEARXNG_URL": ""}, clear=False):
            status = get_web_engine_status()

        self.assertEqual(status["primary_engine"], "duckduckgo")
        self.assertTrue(status["degraded_mode"])
        self.assertIn("duckduckgo", status["available_engines"])
        self.assertIn("wikipedia", status["available_engines"])
        self.assertFalse(status["api_keys_present"]["searxng"])

    def test_runtime_prefers_searxng_when_url_set(self) -> None:
        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False):
            engines = resolve_search_engines()
            status = get_web_engine_status()

        self.assertEqual(tuple(engines), EXPECTED)
        self.assertEqual(status["primary_engine"], "searxng")
        self.assertIn("duckduckgo", status["fallback_engines"])
        self.assertFalse(status["degraded_mode"])

    def test_searxng_failure_falls_back_without_leaking_error_rows(self) -> None:
        def _raise_searxng(*args, **kwargs):
            raise RuntimeError("503 simulated")

        duck_results = [
            {
                "title": "Duck result",
                "href": "https://example.com/result",
                "body": "fallback works",
                "engine": "duckduckgo",
            }
        ]

        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False):
            with patch.dict(
                "app.core.web.ENGINE_FUNCS",
                {
                    "searxng": _raise_searxng,
                    "duckduckgo": lambda query, max_results=5: duck_results,
                    "wikipedia": lambda query, max_results=5: [],
                },
                clear=True,
            ):
                results = search_web("новости за сегодня", max_results=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["engine"], "duckduckgo")
        self.assertEqual(results[0]["href"], "https://example.com/result")

    def test_searxng_query_params_auto_language_and_filters(self) -> None:
        from unittest.mock import MagicMock
        import app.core.web_engines as we

        captured: dict = {}

        def fake_get(url, params=None, timeout=None):
            captured["params"] = params
            resp = MagicMock()
            resp.json.return_value = {"results": []}
            resp.raise_for_status.return_value = None
            return resp

        fake_session = MagicMock()
        fake_session.get.side_effect = fake_get

        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False), \
             patch.object(we, "session", return_value=fake_session):
            # Cyrillic query → auto language=ru; news category + time_range passed through.
            we.search_searxng("курс доллара", categories="news", time_range="week")
            self.assertEqual(captured["params"]["language"], "ru")
            self.assertEqual(captured["params"]["categories"], "news")
            self.assertEqual(captured["params"]["time_range"], "week")
            # English query → SearXNG default (no forced language); bad time_range dropped.
            captured.clear()
            we.search_searxng("fastapi latest version", time_range="bogus")
            self.assertNotIn("language", captured["params"])
            self.assertNotIn("time_range", captured["params"])

    def test_tool_web_search_threads_targeting_to_searxng(self) -> None:
        import app.core.web as core_web
        from app.application.code_agent.tools import tool_web_search

        captured: dict = {}

        def fake_searxng(query, max_results=5, **kw):
            captured.clear()
            captured.update(kw)
            return [{"title": "t", "href": "https://example.com/x", "body": "b", "engine": "searxng"}]

        def fake_other(query, max_results=5):  # DDG/Wiki reject extra kwargs — must never get them
            return []

        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False), \
             patch.dict(
                 core_web.ENGINE_FUNCS,
                 {"searxng": fake_searxng, "duckduckgo": fake_other, "wikipedia": fake_other},
                 clear=True,
             ):
            # Valid targeting reaches SearXNG.
            tool_web_search(query="regex in python", categories="it", time_range="week")
            self.assertEqual(captured.get("categories"), "it")
            self.assertEqual(captured.get("time_range"), "week")
            # Invalid values are dropped (no kwargs passed at all).
            tool_web_search(query="x", categories="bogus", time_range="decade")
            self.assertEqual(captured, {})

    def test_web_engines_route_exposes_only_new_stack(self) -> None:
        client = TestClient(app)
        response = client.get("/api/web/engines")
        self.assertEqual(response.status_code, 200)

        payload = response.json()
        engine_ids = tuple(item["id"] for item in payload["engines"])
        self.assertEqual(engine_ids, EXPECTED)
        self.assertEqual(tuple(payload["default"]), EXPECTED)

    def test_geo_news_rerank_boosts_local_kz_sources(self) -> None:
        results = [
            {
                "title": "Wikipedia overview",
                "href": "https://ru.wikipedia.org/wiki/Алматы",
                "body": "Общая статья про город",
                "engine": "wikipedia",
            },
            {
                "title": "Generic result",
                "href": "https://example.com/almaty",
                "body": "Что-то про Алматы",
                "engine": "duckduckgo",
            },
            {
                "title": "NUR news",
                "href": "https://www.nur.kz/incident",
                "body": "Происшествие в Алматы сегодня",
                "engine": "searxng",
            },
        ]

        reranked = _rerank_results(
            results,
            intent_kind="geo_news",
            geo_scope="алматы",
            local_first=True,
            preferred_domains=("nur.kz", "tengrinews.kz"),
        )

        self.assertEqual(reranked[0]["href"], "https://www.nur.kz/incident")
        self.assertNotEqual(reranked[0]["engine"], "wikipedia")

    def test_finance_rerank_boosts_high_confidence_domains(self) -> None:
        results = [
            {
                "title": "Wikipedia KZT",
                "href": "https://en.wikipedia.org/wiki/Kazakhstani_tenge",
                "body": "Reference page",
                "engine": "wikipedia",
            },
            {
                "title": "Generic rate page",
                "href": "https://example.com/rates",
                "body": "USD KZT rate today",
                "engine": "duckduckgo",
            },
            {
                "title": "National bank rate",
                "href": "https://nationalbank.kz/rates",
                "body": "Курс USD KZT на сегодня",
                "engine": "searxng",
            },
        ]

        reranked = _rerank_results(
            results,
            intent_kind="finance",
            geo_scope="kazakhstan",
            local_first=True,
            preferred_domains=("nationalbank.kz", "wise.com"),
        )

        self.assertEqual(reranked[0]["href"], "https://nationalbank.kz/rates")
        self.assertNotEqual(reranked[0]["engine"], "wikipedia")


if __name__ == "__main__":
    unittest.main()
