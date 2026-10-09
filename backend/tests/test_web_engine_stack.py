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

from webskill.core.web import DEFAULT_SEARCH_ENGINES, SUPPORTED_SEARCH_ENGINES, _rerank_results, get_web_engine_status, resolve_search_engines, search_web  # noqa: E402
from app.main import app  # noqa: E402


EXPECTED = ("searxng",)


class WebEngineStackTest(unittest.TestCase):
    def test_supported_and_default_engines_match_expected_stack(self) -> None:
        self.assertEqual(tuple(SUPPORTED_SEARCH_ENGINES), EXPECTED)
        self.assertEqual(tuple(DEFAULT_SEARCH_ENGINES), EXPECTED)

    def test_runtime_is_unavailable_without_searxng(self) -> None:
        with patch.dict(os.environ, {"SEARXNG_URL": ""}, clear=False):
            status = get_web_engine_status()

        self.assertEqual(status["primary_engine"], "searxng")
        self.assertTrue(status["degraded_mode"])
        self.assertEqual(status["available_engines"], [])
        self.assertEqual(status["fallback_engines"], [])
        self.assertIn("SEARXNG_URL", status["warnings"][0])
        self.assertFalse(status["api_keys_present"]["searxng"])

    def test_runtime_prefers_searxng_when_url_set(self) -> None:
        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False):
            engines = resolve_search_engines()
            status = get_web_engine_status()

        self.assertEqual(tuple(engines), EXPECTED)
        self.assertEqual(status["primary_engine"], "searxng")
        self.assertEqual(status["fallback_engines"], [])
        self.assertFalse(status["degraded_mode"])

    def test_searxng_failure_propagates_without_calling_retired_adapters(self) -> None:
        from unittest.mock import Mock
        retired = Mock(side_effect=AssertionError("retired adapter executed"))
        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}), patch.dict(
            "webskill.core.web.ENGINE_FUNCS",
            {"searxng": Mock(side_effect=RuntimeError("503 simulated")),
             "duckduckgo": retired, "wikipedia": retired}, clear=True,
        ):
            with self.assertRaisesRegex(RuntimeError, "503 simulated"):
                search_web("current release", engines=("duckduckgo", "wikipedia"))
        retired.assert_not_called()

    def test_missing_url_fails_general_news_and_wrappers_without_egress(self) -> None:
        from webskill.core.web import search_news
        from webskill.core import web_engines
        from webskill.infrastructure.search.multisearch import multi_search, news_search
        with patch.dict(os.environ, {"SEARXNG_URL": ""}), patch.object(web_engines, "session") as client:
            for operation in (search_web, search_news):
                with self.subTest(operation=operation.__name__), self.assertRaisesRegex(RuntimeError, "SEARXNG_URL"):
                    operation("current release")
            for operation in (multi_search, news_search):
                result = operation("current release")
                self.assertIs(result["ok"], False)
                self.assertIn("SEARXNG_URL", result["error"])
            client.assert_not_called()

    def test_empty_searxng_response_is_distinct_from_known_upstream_failure(self) -> None:
        from unittest.mock import MagicMock
        from webskill.core import web_engines
        from webskill.infrastructure.search.web_search import search_web as facade
        client = MagicMock()
        response = client.get.return_value
        with patch.dict(os.environ, {"SEARXNG_URL": "http://search.local"}), patch.object(
            web_engines, "session", return_value=client,
        ):
            response.json.return_value = {"results": []}
            result = facade("release")
            self.assertIs(result["ok"], True)
            self.assertEqual(result["sources"], [])
            self.assertEqual([link["name"] for link in result["engine_links"]], ["SearXNG"])
            response.json.return_value = {"results": [], "unresponsive_engines": [["yep", "access denied"]]}
            with self.assertRaisesRegex(RuntimeError, "access denied"):
                facade("release")
            response.json.return_value = {"results": None}
            with self.assertRaisesRegex(RuntimeError, "Invalid SearXNG response"):
                facade("release")

    def test_searxng_query_params_auto_language_and_filters(self) -> None:
        from unittest.mock import MagicMock
        import webskill.core.web_engines as we

        captured: dict = {}

        def fake_get(url, params=None, timeout=None):
            captured.setdefault("calls", []).append(params)
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
            self.assertEqual([item["categories"] for item in captured["calls"]], ["news", "general"])
            self.assertTrue(all(item["time_range"] == "week" and item["language"] == "ru"
                                for item in captured["calls"]))
            self.assertEqual(captured["params"]["time_range"], "week")
            # English query → SearXNG default (no forced language); bad time_range dropped.
            captured.clear()
            we.search_searxng("fastapi latest version", time_range="bogus")
            self.assertNotIn("language", captured["params"])
            self.assertNotIn("time_range", captured["params"])

    def test_searxng_preserves_image_result_urls(self) -> None:
        from unittest.mock import MagicMock
        import webskill.core.web_engines as we

        response = MagicMock()
        response.json.return_value = {
            "results": [{
                "title": "Pangu illustration",
                "url": "https://example.com/pangu",
                "content": "Ancient Chinese mythology",
                "img_src": "https://cdn.example.com/pangu.jpg",
                "thumbnail_src": "https://cdn.example.com/pangu-thumb.jpg",
            }],
        }
        response.raise_for_status.return_value = None
        fake_session = MagicMock()
        fake_session.get.return_value = response

        with patch.dict(os.environ, {"SEARXNG_URL": "http://searxng.local:8003"}, clear=False), \
             patch.object(we, "session", return_value=fake_session):
            results = we.search_searxng("Паньгу", categories="images")

        self.assertEqual(results[0]["img_src"], "https://cdn.example.com/pangu.jpg")
        self.assertEqual(results[0]["thumbnail_src"], "https://cdn.example.com/pangu-thumb.jpg")

    def test_retired_adapters_are_not_executable(self) -> None:
        from webskill.core import web_engines
        self.assertFalse(hasattr(web_engines, "search_duckduckgo"))
        self.assertFalse(hasattr(web_engines, "search_wikipedia"))
        self.assertFalse(hasattr(web_engines, "DDGS"))

    def test_sparse_news_uses_one_general_recovery_and_preserves_dates(self) -> None:
        from unittest.mock import MagicMock
        from webskill.core import web_engines
        from webskill.core.web import search_news
        client = MagicMock()
        client.get.return_value.json.return_value = {"results": [{
            "title": "Current release", "url": "https://example.org/current", "content": "Release notes",
            "publishedDate": "2026-10-01T08:00:00Z",
        }]}
        with patch.dict(os.environ, {"SEARXNG_URL": "http://search.local"}), patch.object(
            web_engines, "session", return_value=client,
        ):
            results = search_news("current release", max_results=30)
        self.assertEqual(client.get.call_count, 2)
        self.assertEqual(client.get.call_args.args[0], "http://search.local/search")
        self.assertEqual([call.kwargs["params"]["categories"] for call in client.get.call_args_list],
                         ["news", "general"])
        self.assertEqual(results[0]["engine"], "searxng")
        self.assertEqual(results[0]["date"], "2026-10-01T08:00:00Z")

    def test_tool_web_search_threads_targeting_to_searxng(self) -> None:
        import webskill.core.web as core_web
        from webskill.application.code_agent.tools._web import tool_web_search

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
