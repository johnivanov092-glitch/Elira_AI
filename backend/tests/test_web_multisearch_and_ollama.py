"""Tests for web_multisearch, ollama_models, and ollama_runtime modules."""
from __future__ import annotations

import asyncio
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.web_multisearch.runtime import (  # noqa: E402
    WebMultiSearchService,
    deep_search,
    fetch_page,
    multi_search,
    news_search,
)
from app.application.ollama_models.runtime import get_models  # noqa: E402
from app.application.ollama_runtime.runtime import list_ollama_models  # noqa: E402


_FAKE_RESULTS = [
    {"title": "A", "url": "https://a.com", "snippet": "alpha", "engine": "ddg"},
    {"title": "B", "url": "https://b.com", "snippet": "beta",  "engine": "tavily"},
]


# ─────────────────────────────────────────────────────────────────────────────
# web_multisearch — multi_search
# ─────────────────────────────────────────────────────────────────────────────

class MultiSearchTest(unittest.TestCase):
    def _search(self, query="test query", results=None):
        with patch("app.core.web.search_web", return_value=_FAKE_RESULTS if results is None else results), \
             patch("app.core.web.format_search_results", return_value="formatted"):
            return multi_search(query)

    def test_success_returns_ok(self) -> None:
        result = self._search()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        self.assertEqual(result["query"], "test query")

    def test_engines_deduplicated(self) -> None:
        results = [{"engine": "ddg"}, {"engine": "ddg"}, {"engine": "tavily"}]
        result = self._search(results=results)
        self.assertEqual(len(result["engines"]), 2)

    def test_empty_results(self) -> None:
        result = self._search(results=[])
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_exception_returns_not_ok(self) -> None:
        with patch("app.core.web.search_web", side_effect=RuntimeError("net error")):
            result = multi_search("q")
        self.assertFalse(result["ok"])
        self.assertIn("net error", result["error"])
        self.assertEqual(result["results"], [])

    def test_formatted_field_present(self) -> None:
        result = self._search()
        self.assertEqual(result["formatted"], "formatted")


# ─────────────────────────────────────────────────────────────────────────────
# web_multisearch — deep_search
# ─────────────────────────────────────────────────────────────────────────────

class DeepSearchTest(unittest.TestCase):
    def test_success(self) -> None:
        with patch("app.core.web.research_web", return_value="page content"):
            result = deep_search("deep question")
        self.assertTrue(result["ok"])
        self.assertEqual(result["content"], "page content")
        self.assertEqual(result["content_length"], len("page content"))

    def test_exception_returns_not_ok(self) -> None:
        with patch("app.core.web.research_web", side_effect=ConnectionError("timeout")):
            result = deep_search("q")
        self.assertFalse(result["ok"])
        self.assertEqual(result["content"], "")


# ─────────────────────────────────────────────────────────────────────────────
# web_multisearch — news_search
# ─────────────────────────────────────────────────────────────────────────────

class NewsSearchTest(unittest.TestCase):
    _FAKE_NEWS = [
        {"title": "News 1", "href": "https://news.com/1", "body": "snippet", "date": "2026-01-01", "source": "BBC"},
        {"title": "News 2", "url": "https://news.com/2",  "body": "other",   "date": "2026-01-02", "source": "CNN"},
        {"title": "No URL", "href": "",                   "body": "skip me"},
    ]

    def test_news_returns_ok(self) -> None:
        with patch("app.core.web.search_news", return_value=self._FAKE_NEWS):
            result = news_search("AI news")
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)       # no-URL item filtered out
        self.assertEqual(result["query"], "AI news")

    def test_items_have_expected_keys(self) -> None:
        with patch("app.core.web.search_news", return_value=self._FAKE_NEWS):
            result = news_search("q")
        item = result["items"][0]
        for key in ("title", "url", "snippet", "date", "source"):
            self.assertIn(key, item)

    def test_exception_returns_not_ok(self) -> None:
        with patch("app.core.web.search_news", side_effect=ValueError("boom")):
            result = news_search("q")
        self.assertFalse(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# web_multisearch — fetch_page
# ─────────────────────────────────────────────────────────────────────────────

class FetchPageTest(unittest.TestCase):
    def test_success(self) -> None:
        with patch("app.core.web.fetch_page_text", return_value="page text"):
            result = fetch_page("https://example.com")
        self.assertTrue(result["ok"])
        self.assertEqual(result["text"], "page text")
        self.assertEqual(result["length"], len("page text"))

    def test_max_chars_truncation(self) -> None:
        with patch("app.core.web.fetch_page_text", return_value="A" * 200):
            result = fetch_page("https://example.com", max_chars=50)
        self.assertEqual(len(result["text"]), 50)

    def test_exception_returns_not_ok(self) -> None:
        with patch("app.core.web.fetch_page_text", side_effect=ConnectionError("404")):
            result = fetch_page("https://bad.com")
        self.assertFalse(result["ok"])
        self.assertEqual(result["text"], "")


# ─────────────────────────────────────────────────────────────────────────────
# web_multisearch — WebMultiSearchService façade
# ─────────────────────────────────────────────────────────────────────────────

class WebMultiSearchServiceTest(unittest.TestCase):
    def setUp(self) -> None:
        self.svc = WebMultiSearchService()

    def test_search_delegates_to_multi_search(self) -> None:
        with patch(
            "app.application.web_multisearch.runtime.multi_search",
            return_value={"ok": True, "count": 3},
        ) as mock:
            result = self.svc.search("q", max_results=5)
        mock.assert_called_once()
        self.assertTrue(result["ok"])

    def test_news_delegates_to_news_search(self) -> None:
        with patch(
            "app.application.web_multisearch.runtime.news_search",
            return_value={"ok": True, "count": 2},
        ) as mock:
            result = self.svc.news("headlines")
        mock.assert_called_once()
        self.assertTrue(result["ok"])


# ─────────────────────────────────────────────────────────────────────────────
# ollama_models — get_models
# ─────────────────────────────────────────────────────────────────────────────

class GetModelsTest(unittest.TestCase):
    def _mock_response(self, models: list):
        resp = MagicMock()
        resp.json.return_value = {"models": models}
        resp.raise_for_status.return_value = None
        return resp

    def test_success_returns_model_list(self) -> None:
        fake_models = [
            {"name": "llama3", "model": "llama3", "size": 4_000_000, "modified_at": "2026-01-01", "digest": "abc123"},
            {"name": "mistral", "model": "mistral", "size": 3_000_000, "modified_at": "2026-01-02", "digest": "def456"},
        ]
        with patch("requests.get", return_value=self._mock_response(fake_models)):
            result = get_models()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 2)
        names = [m["name"] for m in result["models"]]
        self.assertIn("llama3", names)
        self.assertIn("mistral", names)

    def test_model_fields_present(self) -> None:
        fake = [{"name": "phi3", "model": "phi3", "size": 1000, "modified_at": "now", "digest": "x"}]
        with patch("requests.get", return_value=self._mock_response(fake)):
            result = get_models()
        model = result["models"][0]
        for key in ("name", "model", "size", "modified_at", "digest"):
            self.assertIn(key, model)

    def test_empty_model_list(self) -> None:
        with patch("requests.get", return_value=self._mock_response([])):
            result = get_models()
        self.assertTrue(result["ok"])
        self.assertEqual(result["count"], 0)

    def test_connection_error_returns_not_ok(self) -> None:
        with patch("requests.get", side_effect=ConnectionError("ollama not running")):
            result = get_models()
        self.assertFalse(result["ok"])
        self.assertIn("error", result)
        self.assertEqual(result["models"], [])


# ─────────────────────────────────────────────────────────────────────────────
# ollama_runtime — list_ollama_models (async)
# ─────────────────────────────────────────────────────────────────────────────

def _run(coro):
    return asyncio.get_event_loop().run_until_complete(coro)


class ListOllamaModelsTest(unittest.TestCase):
    def test_new_api_object_response(self) -> None:
        """Handles ollama>=0.3 object response with .models attribute."""
        model_obj = SimpleNamespace(name="llama3", size=4_000_000)
        fake_resp = SimpleNamespace(models=[model_obj])
        with patch("ollama.list", return_value=fake_resp):
            result = _run(list_ollama_models())
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "llama3")
        self.assertEqual(result["models"][0]["size"], 4_000_000)

    def test_old_api_dict_response(self) -> None:
        """Handles ollama<0.3 dict response with models key."""
        fake_resp = {"models": [{"name": "phi3", "size": 2_000_000}]}
        with patch("ollama.list", return_value=fake_resp):
            result = _run(list_ollama_models())
        self.assertEqual(len(result["models"]), 1)
        self.assertEqual(result["models"][0]["name"], "phi3")

    def test_model_with_model_attribute_fallback(self) -> None:
        """Model object uses .model attribute when .name absent."""
        model_obj = SimpleNamespace(model="mistral", size=3_000_000)
        fake_resp = SimpleNamespace(models=[model_obj])
        with patch("ollama.list", return_value=fake_resp):
            result = _run(list_ollama_models())
        self.assertEqual(result["models"][0]["name"], "mistral")

    def test_empty_model_list(self) -> None:
        with patch("ollama.list", return_value=SimpleNamespace(models=[])):
            result = _run(list_ollama_models())
        self.assertEqual(result["models"], [])

    def test_exception_returns_error(self) -> None:
        with patch("ollama.list", side_effect=ConnectionError("no ollama")):
            result = _run(list_ollama_models())
        self.assertIn("error", result)
        self.assertEqual(result["models"], [])


if __name__ == "__main__":
    unittest.main()
