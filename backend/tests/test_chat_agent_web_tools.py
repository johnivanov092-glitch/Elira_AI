from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))


from app.application.chat_agent import web_tools  # noqa: E402


class ChatAgentWebToolsTest(unittest.TestCase):
    def test_collect_web_context_skips_non_current_chat(self) -> None:
        result = web_tools.collect_web_context("explain what a transformer is")

        self.assertFalse(result["attempted"])
        self.assertEqual(result["context"], "")

    def test_collect_web_context_formats_news_results(self) -> None:
        payload = {
            "ok": True,
            "results": [
                {
                    "title": "AI release",
                    "href": "https://example.test/ai",
                    "body": "A current AI item.",
                    "engine": "duckduckgo",
                    "date": "2026-06-13",
                }
            ],
            "engine_errors": {"tavily": "missing key"},
        }

        with patch.object(web_tools.multisearch, "news_multi_search", return_value=payload) as search:
            result = web_tools.collect_web_context("latest AI news today")

        search.assert_called_once()
        self.assertTrue(result["attempted"])
        self.assertTrue(result["used"])
        self.assertEqual(result["mode"], "news")
        self.assertIn("WEB SEARCH CONTEXT", result["context"])
        self.assertIn("https://example.test/ai", result["context"])
        self.assertEqual(result["results"][0]["url"], "https://example.test/ai")


if __name__ == "__main__":
    unittest.main()
