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
from app.infrastructure.search.web_runtime import _extract_readable_text  # noqa: E402


class WebSearchToolTest(unittest.TestCase):
    def test_empty_query_returns_error(self) -> None:
        result = tool_web_search(query="")
        self.assertIn("ERROR", result["text"])
        self.assertIs(result["ok"], False)

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
        self.assertIs(result["ok"], True)

    def test_backend_failure_is_not_reported_as_success(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.search_web",
            side_effect=RuntimeError("upstream unavailable"),
        ):
            result = tool_web_search(query="python")
        self.assertIs(result["ok"], False)
        self.assertIn("ERROR", result["text"])

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

    def test_formats_results_with_real_engine_keys_href_body(self) -> None:
        # Engines (SearXNG/DDG/Wikipedia) return href + body, NOT url + snippet.
        # Reading only url/snippet left every link AND snippet blank. Guard that.
        fake = {
            "sources": [
                {"title": "SO answer", "href": "https://stackoverflow.com/q/1", "body": "use asyncio.gather", "engine": "searxng"},
            ],
            "engines_used": ["SearXNG"],
        }
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value=fake,
        ):
            result = tool_web_search(query="asyncio gather")
        text = result["text"]
        self.assertIn("https://stackoverflow.com/q/1", text)  # link present
        self.assertIn("use asyncio.gather", text)             # snippet present

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

    def test_image_search_returns_structured_media_cards(self) -> None:
        fake = {
            "sources": [
                {
                    "title": "Pangu",
                    "href": "https://93.184.216.34/pangu",
                    "body": "Chinese creation myth",
                    "img_src": "https://93.184.216.34/pangu.jpg",
                    "thumbnail_src": "https://93.184.216.34/pangu-thumb.jpg",
                },
                {
                    "title": "Blocked local image",
                    "href": "https://93.184.216.34/source",
                    "img_src": "http://127.0.0.1/private.jpg",
                },
            ],
            "engines_used": ["DuckDuckGo"],
        }
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value=fake,
        ):
            result = tool_web_search(query="Паньгу", categories="images", top_k=3)

        self.assertEqual(result["media"], [
            {
                "type": "image",
                "url": "https://93.184.216.34/pangu-thumb.jpg",
                "source_url": "https://93.184.216.34/pangu",
                "title": "Pangu",
                "source": "93.184.216.34",
            },
            {
                "type": "image",
                "url": "http://127.0.0.1/private.jpg",
                "source_url": "https://93.184.216.34/source",
                "title": "Blocked local image",
                "source": "93.184.216.34",
            },
        ])

    def test_general_search_does_not_auto_attach_image_fields(self) -> None:
        fake = {
            "sources": [{
                "title": "Article",
                "href": "https://93.184.216.34/article",
                "img_src": "https://93.184.216.34/image.jpg",
            }],
            "engines_used": ["test"],
        }
        with patch(
            "app.infrastructure.search.web_search.search_web",
            return_value=fake,
        ):
            result = tool_web_search(query="article", categories="general")

        self.assertNotIn("media", result)


class WebFetchToolTest(unittest.TestCase):
    def test_empty_url_returns_error(self) -> None:
        result = tool_web_fetch(url="")
        self.assertIn("ERROR", result["text"])
        self.assertIs(result["ok"], False)

    def test_unsupported_scheme_returns_error(self) -> None:
        result = tool_web_fetch(url="ftp://example.com/file.txt")
        self.assertIn("ERROR", result["text"])
        self.assertIn("http", result["text"])

    def test_local_file_url_rejected(self) -> None:
        result = tool_web_fetch(url="file:///etc/passwd")
        self.assertIn("ERROR", result["text"])

    def test_empty_body_returns_error(self) -> None:
        # Static extraction empty AND the JS-render fallback yields nothing → ERROR.
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            return_value="",
        ), patch(
            "app.application.code_agent.tools._web._render_fallback",
            return_value="",
        ):
            result = tool_web_fetch(url="https://example.com/empty")
        self.assertIn("ERROR", result["text"])
        self.assertIn("empty", result["text"].lower())

    def test_thin_static_triggers_js_render(self) -> None:
        # Phase A: static extraction thin/empty → transparently render with the
        # headless browser and return the richer rendered text (with a JS marker).
        rendered = "USD/RUB rate today: 1 USD = 78.50 RUB. " * 6
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            return_value="",
        ), patch(
            "app.application.code_agent.tools._web._render_fallback",
            return_value=rendered,
        ):
            result = tool_web_fetch(url="https://example.com/spa")
        self.assertIn("78.50", result["text"])
        self.assertIn("JS", result["text"])          # marked as browser-rendered
        self.assertNotIn("ERROR", result["text"])

    def test_successful_fetch_returns_body_with_source_header(self) -> None:
        with patch(
            "app.infrastructure.search.web_search.fetch_page_text",
            return_value=(
                "The capital of France is Paris. It is the country's largest city and "
                "its political, economic and cultural centre, located on the river Seine "
                "in the north of the country. Paris is famous for its museums, wide "
                "boulevards and historic landmarks such as the Eiffel Tower and the Louvre."
            ),
        ):
            result = tool_web_fetch(url="https://example.com/france")
        self.assertIn("Paris", result["text"])
        self.assertIn("https://example.com/france", result["text"])
        self.assertIs(result["ok"], True)

    def test_max_chars_lower_bound_enforced(self) -> None:
        captured: dict[str, int] = {}

        def fake_fetch(url, max_chars):
            captured["max_chars"] = max_chars
            return "x" * 300  # > _THIN_TEXT_THRESHOLD so no JS-render fallback fires

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
            return "x" * 300  # > _THIN_TEXT_THRESHOLD so no JS-render fallback fires

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
        self.assertIs(result["ok"], False)

    def test_nested_main_content_survives_malformed_img_parse_tree(self) -> None:
        # Some real pages (including docs.python.org pathlib) are recovered by
        # html.parser with the remaining main content nested under a void <img>.
        # Destructively removing that image used to erase the whole page.
        from bs4 import BeautifulSoup

        parsed = BeautifulSoup(
            "<html><body><main role='main'><p>Introductory paragraph long enough "
            "to survive line filtering.</p><img src='diagram.png'></main></body></html>",
            "html.parser",
        )
        nested = parsed.new_tag("section")
        paragraph = parsed.new_tag("p")
        paragraph.string = (
            "Path objects expose filesystem semantics and this load-bearing "
            "documentation must remain readable after decorative cleanup."
        )
        nested.append(paragraph)
        parsed.img.append(nested)

        text = _extract_readable_text(parsed, max_chars=4000)

        self.assertIn("load-bearing documentation", text)


class BatchWebToolsTest(unittest.TestCase):
    """web_search(queries=[...]) / web_fetch(urls=[...]) run in parallel."""

    def test_web_search_batch_merges_and_dedupes(self) -> None:
        import app.application.code_agent.tools._web as w

        def fake_run(query, limit, cat, tr):
            if query == "q1":
                return [{"title": "A", "href": "https://a.com", "body": "a"},
                        {"title": "S", "href": "https://shared.com", "body": "s"}]
            return [{"title": "B", "href": "https://b.com", "body": "b"},
                    {"title": "S", "href": "https://shared.com", "body": "s2"}]

        with patch.object(w, "_run_search", side_effect=fake_run):
            result = tool_web_search(queries=["q1", "q2"])
        text = result["text"]
        self.assertIs(result["ok"], True)
        self.assertIn("2 parallel queries", text)
        self.assertIn("https://a.com", text)
        self.assertIn("https://b.com", text)
        self.assertEqual(text.count("https://shared.com"), 1)  # de-duped across queries

    def test_web_search_batch_all_failures_not_ok(self) -> None:
        import app.application.code_agent.tools._web as w

        with patch.object(w, "_run_search", side_effect=RuntimeError("offline")):
            result = tool_web_search(queries=["q1", "q2"])
        self.assertIs(result["ok"], False)
        self.assertIn("ERROR", result["text"])

    def test_web_fetch_batch_fetches_all(self) -> None:
        import app.application.code_agent.tools._web as w
        urls = ["https://x/1", "https://x/2", "https://x/3"]
        with patch.object(w, "_fetch_one", side_effect=lambda u, limit: f"[fetched: {u}]\n\nbody {u}"):
            result = tool_web_fetch(urls=urls)
        text = result["text"]
        self.assertIs(result["ok"], True)
        self.assertIn("3 pages in parallel", text)
        for u in urls:
            self.assertIn(u, text)

    def test_web_fetch_batch_all_failures_not_ok(self) -> None:
        import app.application.code_agent.tools._web as w

        with patch.object(w, "_fetch_one", return_value="ERROR: unavailable"):
            result = tool_web_fetch(urls=["https://x/1", "https://x/2"])
        self.assertIs(result["ok"], False)

    def test_batch_is_capped(self) -> None:
        import app.application.code_agent.tools._web as w
        seen: list[str] = []
        with patch.object(w, "_fetch_one", side_effect=lambda u, limit: seen.append(u) or "[fetched]"):
            tool_web_fetch(urls=[f"https://x/{i}" for i in range(20)])
        self.assertLessEqual(len(seen), w._WEB_BATCH_MAX)


class ToolRegistrationTest(unittest.TestCase):
    """The new tools must be visible to the LLM (schemas) AND callable
    via the dispatch table."""

    def test_schemas_include_web_search_and_web_fetch(self) -> None:
        names = [s["function"]["name"] for s in build_tool_schemas()]
        self.assertIn("web_search", names)
        self.assertIn("web_fetch", names)

    def test_claim_tool_is_never_exposed(self) -> None:
        names = {
            schema["function"]["name"]
            for schema in build_tool_schemas()
        }
        self.assertNotIn("web_claim_add", names)

    def test_schemas_include_sandbox_run_and_reset(self) -> None:
        names = [s["function"]["name"] for s in build_tool_schemas()]
        self.assertIn("sandbox_run", names)
        self.assertIn("sandbox_reset", names)

    def test_dispatch_includes_web_search_and_web_fetch(self) -> None:
        dispatch = build_tool_dispatch(Path("."))
        self.assertIn("web_search", dispatch)
        self.assertIn("web_fetch", dispatch)
        self.assertTrue(callable(dispatch["web_search"]))
        self.assertTrue(callable(dispatch["web_fetch"]))

    def test_dispatch_includes_sandbox_run_and_reset(self) -> None:
        dispatch = build_tool_dispatch(Path("."))
        self.assertIn("sandbox_run", dispatch)
        self.assertIn("sandbox_reset", dispatch)
        self.assertTrue(callable(dispatch["sandbox_run"]))
        self.assertTrue(callable(dispatch["sandbox_reset"]))

    def test_web_search_schema_offers_query_and_queries(self) -> None:
        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        params = schemas["web_search"]["function"]["parameters"]
        # query OR queries — neither is hard-required (the handler validates).
        self.assertEqual(params["required"], [])
        self.assertIn("query", params["properties"])
        self.assertEqual(params["properties"]["queries"]["type"], "array")

    def test_web_fetch_schema_offers_url_and_urls(self) -> None:
        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        params = schemas["web_fetch"]["function"]["parameters"]
        self.assertEqual(params["required"], [])
        self.assertIn("url", params["properties"])
        self.assertEqual(params["properties"]["urls"]["type"], "array")

    def test_sandbox_run_schema_requires_code(self) -> None:
        schemas = {s["function"]["name"]: s for s in build_tool_schemas()}
        params = schemas["sandbox_run"]["function"]["parameters"]
        self.assertEqual(params["required"], ["code"])


if __name__ == "__main__":
    unittest.main()
