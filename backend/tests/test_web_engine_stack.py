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

from app.core.web import DEFAULT_SEARCH_ENGINES, SUPPORTED_SEARCH_ENGINES, get_web_engine_status, resolve_search_engines, search_web  # noqa: E402
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


if __name__ == "__main__":
    unittest.main()
