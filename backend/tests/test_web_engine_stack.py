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


EXPECTED = ("tavily", "duckduckgo", "wikipedia")


class WebEngineStackTest(unittest.TestCase):
    def test_supported_and_default_engines_match_expected_stack(self) -> None:
        self.assertEqual(tuple(SUPPORTED_SEARCH_ENGINES), EXPECTED)
        self.assertEqual(tuple(DEFAULT_SEARCH_ENGINES), EXPECTED)

    def test_runtime_falls_back_to_duckduckgo_without_api_keys(self) -> None:
        with patch.dict(os.environ, {"TAVILY_API_KEY": ""}, clear=False):
            status = get_web_engine_status()

        self.assertEqual(status["primary_engine"], "duckduckgo")
        self.assertTrue(status["degraded_mode"])
        self.assertIn("duckduckgo", status["available_engines"])
        self.assertIn("wikipedia", status["available_engines"])
        self.assertFalse(status["api_keys_present"]["tavily"])

    def test_runtime_prefers_tavily_when_key_exists(self) -> None:
        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-tavily"}, clear=False):
            engines = resolve_search_engines()
            status = get_web_engine_status()

        self.assertEqual(tuple(engines), EXPECTED)
        self.assertEqual(status["primary_engine"], "tavily")
        self.assertIn("duckduckgo", status["fallback_engines"])
        self.assertFalse(status["degraded_mode"])

    def test_tavily_http_failure_falls_back_without_leaking_error_rows(self) -> None:
        def _raise_tavily(*args, **kwargs):
            raise RuntimeError("402 simulated")

        duck_results = [
            {
                "title": "Duck result",
                "href": "https://example.com/result",
                "body": "fallback works",
                "engine": "duckduckgo",
            }
        ]

        with patch.dict(os.environ, {"TAVILY_API_KEY": "test-tavily"}, clear=False):
            with patch.dict(
                "app.core.web.ENGINE_FUNCS",
                {
                    "tavily": _raise_tavily,
                    "duckduckgo": lambda query, max_results=5: duck_results,
                    "wikipedia": lambda query, max_results=5: [],
                },
                clear=True,
            ):
                results = search_web("новости за сегодня", max_results=5)

        self.assertEqual(len(results), 1)
        self.assertEqual(results[0]["engine"], "duckduckgo")
        self.assertEqual(results[0]["href"], "https://example.com/result")

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
                "engine": "tavily",
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
                "engine": "tavily",
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
