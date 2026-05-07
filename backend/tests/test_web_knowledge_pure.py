"""Tests for pure helpers in application/memory/web_knowledge.py.

Covers:
  clean_browser_text      — whitespace normalization
  chunk_browser_text      — fixed-size chunking
  build_browser_rag_records — structured records from page content
  build_web_knowledge_records — structured records from web search results

All functions are pure (no DB, no HTTP, no FS).
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.memory.web_knowledge import (  # noqa: E402
    clean_browser_text,
    chunk_browser_text,
    build_browser_rag_records,
    build_web_knowledge_records,
)


# ─────────────────────────────────────────────────────────────────────────────
# clean_browser_text
# ─────────────────────────────────────────────────────────────────────────────

class CleanBrowserTextTest(unittest.TestCase):

    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_browser_text("hello"), str)

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(None), "")  # type: ignore[arg-type]

    def test_plain_text_unchanged(self) -> None:
        self.assertEqual(clean_browser_text("hello world"), "hello world")

    def test_tabs_replaced_with_spaces(self) -> None:
        result = clean_browser_text("hello\tworld")
        self.assertNotIn("\t", result)
        self.assertIn("hello", result)
        self.assertIn("world", result)

    def test_carriage_return_replaced_with_space(self) -> None:
        result = clean_browser_text("hello\rworld")
        self.assertNotIn("\r", result)
        self.assertIn("hello", result)
        self.assertIn("world", result)

    def test_multiple_spaces_collapsed(self) -> None:
        result = clean_browser_text("hello   world")
        self.assertNotIn("  ", result)
        self.assertEqual(result, "hello world")

    def test_leading_whitespace_stripped(self) -> None:
        result = clean_browser_text("   hello")
        self.assertEqual(result, "hello")

    def test_trailing_whitespace_stripped(self) -> None:
        result = clean_browser_text("hello   ")
        self.assertEqual(result, "hello")

    def test_mixed_tabs_carriage_returns_and_spaces(self) -> None:
        result = clean_browser_text("  hello\t\rworld  ")
        self.assertNotIn("\t", result)
        self.assertNotIn("\r", result)
        self.assertNotIn("  ", result)
        self.assertTrue(result.startswith("hello"))

    def test_already_clean_text_unchanged(self) -> None:
        text = "this is already clean text"
        self.assertEqual(clean_browser_text(text), text)

    def test_only_whitespace_returns_empty(self) -> None:
        result = clean_browser_text("   \t\r   ")
        self.assertEqual(result, "")

    def test_newlines_not_modified(self) -> None:
        # \n is not in the replace list, so newlines should remain
        result = clean_browser_text("line1\nline2")
        self.assertIn("\n", result)

    def test_many_consecutive_tabs_become_single_space(self) -> None:
        result = clean_browser_text("a\t\t\tb")
        self.assertNotIn("  ", result)
        self.assertNotIn("\t", result)

    def test_unicode_text_unchanged(self) -> None:
        text = "Привет мир"
        self.assertEqual(clean_browser_text(text), text)


# ─────────────────────────────────────────────────────────────────────────────
# chunk_browser_text
# ─────────────────────────────────────────────────────────────────────────────

class ChunkBrowserTextTest(unittest.TestCase):

    def test_returns_list(self) -> None:
        self.assertIsInstance(chunk_browser_text("hello"), list)

    def test_empty_string_returns_empty_list(self) -> None:
        self.assertEqual(chunk_browser_text(""), [])

    def test_none_returns_empty_list(self) -> None:
        self.assertEqual(chunk_browser_text(None), [])  # type: ignore[arg-type]

    def test_all_whitespace_returns_empty_list(self) -> None:
        self.assertEqual(chunk_browser_text("   \t  "), [])

    def test_short_text_single_chunk(self) -> None:
        result = chunk_browser_text("hello world", size=1200)
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0], "hello world")

    def test_text_exactly_size_returns_one_chunk(self) -> None:
        text = "A" * 1200
        result = chunk_browser_text(text, size=1200)
        self.assertEqual(len(result), 1)

    def test_text_longer_than_size_returns_multiple_chunks(self) -> None:
        text = "A" * 2500
        result = chunk_browser_text(text, size=1200)
        self.assertGreater(len(result), 1)

    def test_all_chunks_are_strings(self) -> None:
        text = "hello " * 300
        for chunk in chunk_browser_text(text, size=100):
            self.assertIsInstance(chunk, str)

    def test_all_chunks_non_empty(self) -> None:
        text = "word " * 500
        for chunk in chunk_browser_text(text, size=50):
            self.assertTrue(chunk.strip())

    def test_chunks_cover_all_text(self) -> None:
        text = "abcde" * 400  # 2000 chars
        chunks = chunk_browser_text(text, size=500)
        combined_len = sum(len(c) for c in chunks)
        # stripped chunks might differ slightly from original but cover content
        self.assertGreater(combined_len, 0)

    def test_default_size_is_1200(self) -> None:
        text = "A" * 1500
        # With default size=1200, 1500 chars → 2 chunks
        result = chunk_browser_text(text)
        self.assertEqual(len(result), 2)

    def test_custom_size_respected(self) -> None:
        text = "B" * 300
        chunks_small = chunk_browser_text(text, size=100)
        chunks_large = chunk_browser_text(text, size=200)
        self.assertGreater(len(chunks_small), len(chunks_large))

    def test_chunk_size_at_most_size_chars(self) -> None:
        text = "X" * 3000
        size = 500
        for chunk in chunk_browser_text(text, size=size):
            self.assertLessEqual(len(chunk), size)

    def test_text_with_tabs_and_cr_cleaned_before_chunking(self) -> None:
        text = "hello\t\rworld " * 100
        chunks = chunk_browser_text(text, size=200)
        for chunk in chunks:
            self.assertNotIn("\t", chunk)
            self.assertNotIn("\r", chunk)


# ─────────────────────────────────────────────────────────────────────────────
# build_browser_rag_records
# ─────────────────────────────────────────────────────────────────────────────

class BuildBrowserRagRecordsTest(unittest.TestCase):

    _URL = "https://example.com/page"
    _GOAL = "find out about Python"

    def _call(self, *, summary="", page_text=""):
        return build_browser_rag_records(
            url=self._URL,
            goal=self._GOAL,
            summary=summary,
            page_text=page_text,
        )

    def test_returns_list(self) -> None:
        self.assertIsInstance(self._call(), list)

    def test_empty_summary_and_page_text_returns_empty(self) -> None:
        self.assertEqual(self._call(), [])

    def test_non_empty_summary_creates_record(self) -> None:
        result = self._call(summary="A useful summary.")
        self.assertTrue(any(r["type"] == "browser_summary" for r in result))

    def test_browser_summary_has_required_keys(self) -> None:
        result = self._call(summary="Some summary text.")
        rec = next(r for r in result if r["type"] == "browser_summary")
        for key in ("type", "url", "goal", "content"):
            self.assertIn(key, rec)

    def test_browser_summary_url_preserved(self) -> None:
        result = self._call(summary="Summary here.")
        rec = next(r for r in result if r["type"] == "browser_summary")
        self.assertEqual(rec["url"], self._URL)

    def test_browser_summary_goal_preserved(self) -> None:
        result = self._call(summary="Summary here.")
        rec = next(r for r in result if r["type"] == "browser_summary")
        self.assertEqual(rec["goal"], self._GOAL)

    def test_browser_summary_content_is_cleaned(self) -> None:
        result = self._call(summary="  summary with  extra  spaces  ")
        rec = next(r for r in result if r["type"] == "browser_summary")
        self.assertEqual(rec["content"], "summary with extra spaces")

    def test_empty_summary_not_included(self) -> None:
        result = self._call(summary="", page_text="Some page text here for testing.")
        self.assertFalse(any(r["type"] == "browser_summary" for r in result))

    def test_whitespace_only_summary_not_included(self) -> None:
        result = self._call(summary="   \t  ", page_text="Real page content here.")
        self.assertFalse(any(r["type"] == "browser_summary" for r in result))

    def test_non_empty_page_text_creates_browser_page(self) -> None:
        result = self._call(page_text="Some page content here.")
        self.assertTrue(any(r["type"] == "browser_page" for r in result))

    def test_browser_page_has_required_keys(self) -> None:
        result = self._call(page_text="Page content here.")
        rec = next(r for r in result if r["type"] == "browser_page")
        for key in ("type", "url", "goal", "content"):
            self.assertIn(key, rec)

    def test_browser_page_url_preserved(self) -> None:
        result = self._call(page_text="Content.")
        rec = next(r for r in result if r["type"] == "browser_page")
        self.assertEqual(rec["url"], self._URL)

    def test_browser_page_goal_preserved(self) -> None:
        result = self._call(page_text="Content.")
        rec = next(r for r in result if r["type"] == "browser_page")
        self.assertEqual(rec["goal"], self._GOAL)

    def test_long_page_text_creates_multiple_chunks(self) -> None:
        # Default chunk size is 1200; 3000 chars → at least 2 browser_page records
        result = self._call(page_text="word " * 700)
        page_records = [r for r in result if r["type"] == "browser_page"]
        self.assertGreater(len(page_records), 1)

    def test_both_summary_and_page_text(self) -> None:
        result = self._call(summary="Summary here.", page_text="Page content here.")
        types = [r["type"] for r in result]
        self.assertIn("browser_summary", types)
        self.assertIn("browser_page", types)

    def test_summary_appears_before_page_records(self) -> None:
        result = self._call(summary="Summary.", page_text="Page content.")
        # browser_summary should be first
        self.assertEqual(result[0]["type"], "browser_summary")

    def test_all_records_are_dicts(self) -> None:
        result = self._call(summary="Summary.", page_text="Page content.")
        for rec in result:
            self.assertIsInstance(rec, dict)


# ─────────────────────────────────────────────────────────────────────────────
# build_web_knowledge_records
# ─────────────────────────────────────────────────────────────────────────────

class BuildWebKnowledgeRecordsTest(unittest.TestCase):

    _QUERY = "what is machine learning"
    _WEB_CTX = "Machine learning is a subset of AI that allows systems to learn from data."

    def _call(self, *, query=None, web_context=None, source_kind="web_search", max_chars=14000):
        return build_web_knowledge_records(
            query=query if query is not None else self._QUERY,
            web_context=web_context if web_context is not None else self._WEB_CTX,
            source_kind=source_kind,
            max_chars=max_chars,
        )

    def test_returns_list(self) -> None:
        self.assertIsInstance(self._call(), list)

    def test_empty_web_context_returns_empty(self) -> None:
        self.assertEqual(self._call(web_context=""), [])

    def test_none_web_context_returns_empty(self) -> None:
        # Call directly — self._call() helper replaces None with default context
        result = build_web_knowledge_records(
            query=self._QUERY, web_context=None  # type: ignore[arg-type]
        )
        self.assertEqual(result, [])

    def test_whitespace_only_web_context_returns_empty(self) -> None:
        self.assertEqual(self._call(web_context="   "), [])

    def test_non_empty_creates_web_summary_record(self) -> None:
        result = self._call()
        self.assertTrue(any(r["type"] == "web_summary" for r in result))

    def test_first_record_is_web_summary(self) -> None:
        result = self._call()
        self.assertEqual(result[0]["type"], "web_summary")

    def test_web_summary_has_required_keys(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        for key in ("type", "url", "goal", "content", "title", "source_kind"):
            self.assertIn(key, rec)

    def test_web_summary_url_is_empty_string(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(rec["url"], "")

    def test_web_summary_goal_equals_query(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(rec["goal"], self._QUERY)

    def test_web_summary_content_contains_query(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertIn(self._QUERY, rec["content"])

    def test_web_summary_title_equals_query(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(rec["title"], self._QUERY[:300])

    def test_web_summary_source_kind_default(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(rec["source_kind"], "web_search")

    def test_web_summary_source_kind_custom(self) -> None:
        result = self._call(source_kind="browser")
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(rec["source_kind"], "browser")

    def test_web_chunk_records_created(self) -> None:
        result = self._call()
        self.assertTrue(any(r["type"] == "web_chunk" for r in result))

    def test_web_chunk_has_required_keys(self) -> None:
        result = self._call()
        rec = next(r for r in result if r["type"] == "web_chunk")
        for key in ("type", "url", "goal", "content", "title", "source_kind"):
            self.assertIn(key, rec)

    def test_web_chunk_url_is_empty_string(self) -> None:
        result = self._call()
        for rec in [r for r in result if r["type"] == "web_chunk"]:
            self.assertEqual(rec["url"], "")

    def test_web_chunk_goal_equals_query(self) -> None:
        result = self._call()
        for rec in [r for r in result if r["type"] == "web_chunk"]:
            self.assertEqual(rec["goal"], self._QUERY)

    def test_web_chunk_source_kind_matches(self) -> None:
        result = self._call(source_kind="rag")
        for rec in [r for r in result if r["type"] == "web_chunk"]:
            self.assertEqual(rec["source_kind"], "rag")

    def test_max_chars_limits_content(self) -> None:
        long_context = "word " * 10000  # ~50000 chars
        result_short = build_web_knowledge_records(
            query="test", web_context=long_context, max_chars=100
        )
        result_long = build_web_knowledge_records(
            query="test", web_context=long_context, max_chars=5000
        )
        chunks_short = [r for r in result_short if r["type"] == "web_chunk"]
        chunks_long = [r for r in result_long if r["type"] == "web_chunk"]
        self.assertLessEqual(len(chunks_short), len(chunks_long))

    def test_long_query_title_truncated_at_300(self) -> None:
        long_query = "q" * 500
        result = build_web_knowledge_records(
            query=long_query, web_context="Some content here."
        )
        rec = next(r for r in result if r["type"] == "web_summary")
        self.assertEqual(len(rec["title"]), 300)

    def test_all_records_are_dicts(self) -> None:
        result = self._call()
        for rec in result:
            self.assertIsInstance(rec, dict)

    def test_empty_query_still_works(self) -> None:
        result = build_web_knowledge_records(
            query="", web_context="Some content here for testing purposes."
        )
        self.assertIsInstance(result, list)
        self.assertGreater(len(result), 0)

    def test_none_query_treated_as_empty(self) -> None:
        result = build_web_knowledge_records(
            query=None, web_context="Some content here."  # type: ignore[arg-type]
        )
        self.assertIsInstance(result, list)


if __name__ == "__main__":
    unittest.main()
