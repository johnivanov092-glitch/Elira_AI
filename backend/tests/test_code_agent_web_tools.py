"""Tests for the web-research tools wired into the code-agent."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.code_agent.tools import (  # noqa: E402
    build_tool_dispatch,
    build_tool_schemas,
    tool_web_fetch,
    tool_web_search,
)


class WebSearchToolTest(unittest.TestCase):
    def test_empty_query_returns_error(self) -> None:
        result = tool_web_search(query="")
        self.assertIn("ERROR", result["text"])

    def test_whitespace_query_returns_error(self) -> None:
        result = tool_web_search(query="   ")
        self.assertIn("ERROR", result["text"])

    def test_no_results_returns_message(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value={"sources": [], "engines_used": []},
        ):
            result = tool_web_search(query="abracadabra_no_hits")
        self.assertIn("No web results", result["text"])

    def test_formats_results_with_index_title_url(self) -> None:
        fake = {
            "sources": [
                {"title": "First Result", "url": "https://example.com/a", "snippet": "Snippet A"},
                {"title": "Second", "url": "https://example.com/b", "snippet": "Snippet B"},
            ],
            "engines_used": ["DuckDuckGo"],
        }
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value=fake,
        ):
            result = tool_web_search(query="python tutorials", top_k=2)
        text = result["text"]
        self.assertIn("First Result", text)
        self.assertIn("https://example.com/a", text)
        self.assertIn("Snippet A", text)
        self.assertIn("[1]", text)
        self.assertIn("[2]", text)
        self.assertIn("DuckDuckGo", text)

    def test_top_k_clamps_to_max(self) -> None:
        sources = [{"title": f"T{i}", "url": f"https://x/{i}", "snippet": "s"} for i in range(20)]
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value={"sources": sources, "engines_used": ["test"]},
        ):
            result = tool_web_search(query="any", top_k=99)
        # 10 is the documented max
        self.assertIn("[10]", result["text"])
        self.assertNotIn("[11]", result["text"])

    def test_long_snippet_truncated(self) -> None:
        big_snippet = "X" * 1000
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value={
                "sources": [{"title": "T", "url": "https://x/", "snippet": big_snippet}],
                "engines_used": ["test"],
            },
        ):
            result = tool_web_search(query="any")
        self.assertIn("[…]", result["text"])
        # Truncated to ~350 chars + marker, not 1000
        self.assertLess(len(result["text"]), 1200)


class WebFetchToolTest(unittest.TestCase):
    def test_empty_url_returns_error(self) -> None:
        result = tool_web_fetch(url="")
        self.assertIn("ERROR", result["text"])

    def test_unsupported_scheme_returns_error(self) -> None:
        result = tool_web_fetch(url="ftp://example.com/file.txt")
        self.assertIn("ERROR", result["text"])
        self.assertIn("http", result["text"])

    def test_local_file_url_rejected(self) -> None:
        result = tool_web_fetch(url="file:///etc/passwd")
        self.assertIn("ERROR", result["text"])

    def test_empty_body_returns_error(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            return_value="",
        ):
            result = tool_web_fetch(url="https://example.com/empty")
        self.assertIn("ERROR", result["text"])
        self.assertIn("empty", result["text"].lower())

    def test_successful_fetch_returns_body_with_source_header(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            return_value="The capital of France is Paris.\n\nIt is located on the Seine.",
        ):
            result = tool_web_fetch(url="https://example.com/france")
        self.assertIn("Paris", result["text"])
        self.assertIn("https://example.com/france", result["text"])

    def test_max_chars_lower_bound_enforced(self) -> None:
        captured: dict[str, int] = {}

        def fake_fetch(url, max_chars):
            captured["max_chars"] = max_chars
            return "ok"

        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            side_effect=fake_fetch,
        ):
            tool_web_fetch(url="https://example.com/", max_chars=10)
        # Documented lower bound is 500
        self.assertEqual(captured["max_chars"], 500)

    def test_max_chars_upper_bound_enforced(self) -> None:
        captured: dict[str, int] = {}

        def fake_fetch(url, max_chars):
            captured["max_chars"] = max_chars
            return "ok"

        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            side_effect=fake_fetch,
        ):
            tool_web_fetch(url="https://example.com/", max_chars=1_000_000)
        self.assertEqual(captured["max_chars"], 50000)

    def test_fetch_exception_returns_error(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            side_effect=RuntimeError("network down"),
        ):
            result = tool_web_fetch(url="https://example.com/")
        self.assertIn("ERROR", result["text"])
        self.assertIn("network down", result["text"])


class ToolRegistrationTest(unittest.TestCase):
    """The two new tools must be visible to the LLM (schemas) AND
    callable via the dispatch table."""

    def test_schemas_include_web_search_and_web_fetch(self) -> None:
        names = [s["function"]["name"] for s in build_tool_schemas()]
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_dispatch_includes_web_search_and_web_fetch(self) -> None:
        dispatch = build_tool_dispatch(Path("."))
        self.assertIn("web_search", dispatch)
        self.assertIn("web_fetch", dispatch)
        self.assertTrue(callable(dispatch["web_search"]))
        self.assertTrue(callable(dispatch["web_fetch"]))

    def test_web_search_schema_requires_query(self) -> None:
        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        params = schemas["web_search"]["function"]["parameters"]
        self.assertEqual(params["required"], ["query"])

    def test_web_fetch_schema_requires_url(self) -> None:
        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        params = schemas["web_fetch"]["function"]["parameters"]
        self.assertEqual(params["required"], ["url"])


if __name__ == "__main__":
    unittest.main()
