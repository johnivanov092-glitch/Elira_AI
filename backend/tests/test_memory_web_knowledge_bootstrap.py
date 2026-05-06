"""Tests for two previously uncovered memory sub-modules:
  memory/web_knowledge — clean_browser_text, chunk_browser_text,
    build_browser_rag_records, build_web_knowledge_records (all pure)
  memory/bootstrap — SETTINGS_DEFAULTS constant, load_settings,
    save_settings (file I/O with temp dir)
"""
from __future__ import annotations

import json
import sys
import tempfile
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
from app.application.memory.bootstrap import (  # noqa: E402
    SETTINGS_DEFAULTS,
    load_settings,
    save_settings,
)


# ─────────────────────────────────────────────────────────────────────────────
# memory/web_knowledge — clean_browser_text
# ─────────────────────────────────────────────────────────────────────────────

class CleanBrowserTextTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(clean_browser_text("hello"), str)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(None), "")  # type: ignore[arg-type]

    def test_collapses_double_spaces(self) -> None:
        result = clean_browser_text("hello  world")
        self.assertEqual(result, "hello world")

    def test_replaces_tabs_with_space(self) -> None:
        result = clean_browser_text("col1\tcol2")
        self.assertNotIn("\t", result)

    def test_replaces_carriage_return(self) -> None:
        result = clean_browser_text("line1\rline2")
        self.assertNotIn("\r", result)

    def test_strips_leading_trailing_whitespace(self) -> None:
        result = clean_browser_text("  hello  ")
        self.assertEqual(result, "hello")

    def test_normal_text_unchanged(self) -> None:
        result = clean_browser_text("hello world")
        self.assertEqual(result, "hello world")

    def test_multiple_spaces_collapsed(self) -> None:
        result = clean_browser_text("a   b   c")
        self.assertNotIn("  ", result)


# ─────────────────────────────────────────────────────────────────────────────
# memory/web_knowledge — chunk_browser_text
# ─────────────────────────────────────────────────────────────────────────────

class ChunkBrowserTextTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(chunk_browser_text("hello world"), list)

    def test_empty_returns_empty_list(self) -> None:
        self.assertEqual(chunk_browser_text(""), [])

    def test_short_text_returns_one_chunk(self) -> None:
        result = chunk_browser_text("hello world")
        self.assertEqual(len(result), 1)

    def test_short_chunk_equals_input(self) -> None:
        result = chunk_browser_text("hello world")
        self.assertEqual(result[0], "hello world")

    def test_long_text_splits_into_chunks(self) -> None:
        text = "A" * 2500
        result = chunk_browser_text(text, size=1200)
        self.assertGreater(len(result), 1)

    def test_chunks_are_strings(self) -> None:
        for chunk in chunk_browser_text("test text", size=3):
            self.assertIsInstance(chunk, str)

    def test_chunk_length_respects_size(self) -> None:
        text = "A" * 2500
        result = chunk_browser_text(text, size=1200)
        for chunk in result:
            self.assertLessEqual(len(chunk), 1200)

    def test_whitespace_only_not_in_chunks(self) -> None:
        result = chunk_browser_text("  \t  \r  ")
        self.assertEqual(result, [])


# ─────────────────────────────────────────────────────────────────────────────
# memory/web_knowledge — build_browser_rag_records
# ─────────────────────────────────────────────────────────────────────────────

class BuildBrowserRagRecordsTest(unittest.TestCase):
    def _call(self, *, url="http://x.com", goal="goal", summary="", page_text=""):
        return build_browser_rag_records(
            url=url, goal=goal, summary=summary, page_text=page_text
        )

    def test_returns_list(self) -> None:
        self.assertIsInstance(self._call(), list)

    def test_empty_inputs_returns_empty(self) -> None:
        self.assertEqual(self._call(), [])

    def test_summary_creates_browser_summary_record(self) -> None:
        result = self._call(summary="This is a summary of the page")
        types = [r["type"] for r in result]
        self.assertIn("browser_summary", types)

    def test_page_text_creates_browser_page_records(self) -> None:
        result = self._call(page_text="This is long page text content here")
        types = [r["type"] for r in result]
        self.assertIn("browser_page", types)

    def test_records_have_url(self) -> None:
        result = self._call(summary="a summary", url="http://example.com")
        for record in result:
            self.assertEqual(record["url"], "http://example.com")

    def test_records_have_goal(self) -> None:
        result = self._call(summary="summary text", goal="my goal")
        for record in result:
            self.assertEqual(record["goal"], "my goal")

    def test_records_have_content_key(self) -> None:
        result = self._call(summary="summary")
        for record in result:
            self.assertIn("content", record)


# ─────────────────────────────────────────────────────────────────────────────
# memory/web_knowledge — build_web_knowledge_records
# ─────────────────────────────────────────────────────────────────────────────

class BuildWebKnowledgeRecordsTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        result = build_web_knowledge_records(query="test", web_context="some text")
        self.assertIsInstance(result, list)

    def test_empty_web_context_returns_empty(self) -> None:
        result = build_web_knowledge_records(query="test", web_context="")
        self.assertEqual(result, [])

    def test_none_web_context_returns_empty(self) -> None:
        result = build_web_knowledge_records(query="test", web_context=None)  # type: ignore[arg-type]
        self.assertEqual(result, [])

    def test_has_web_summary_record(self) -> None:
        result = build_web_knowledge_records(query="Python", web_context="Python is great")
        types = [r["type"] for r in result]
        self.assertIn("web_summary", types)

    def test_has_web_chunk_records(self) -> None:
        text = "A word " * 500  # Long enough to create chunks
        result = build_web_knowledge_records(query="test", web_context=text)
        types = [r["type"] for r in result]
        self.assertIn("web_chunk", types)

    def test_records_have_goal_from_query(self) -> None:
        result = build_web_knowledge_records(query="my query", web_context="some text")
        for record in result:
            self.assertEqual(record["goal"], "my query")

    def test_source_kind_reflected(self) -> None:
        result = build_web_knowledge_records(
            query="q", web_context="text content here", source_kind="custom_source"
        )
        for record in result:
            self.assertEqual(record["source_kind"], "custom_source")

    def test_records_have_content(self) -> None:
        result = build_web_knowledge_records(query="q", web_context="some content")
        for record in result:
            self.assertIn("content", record)


# ─────────────────────────────────────────────────────────────────────────────
# memory/bootstrap — SETTINGS_DEFAULTS
# ─────────────────────────────────────────────────────────────────────────────

class SettingsDefaultsTest(unittest.TestCase):
    def test_is_dict(self) -> None:
        self.assertIsInstance(SETTINGS_DEFAULTS, dict)

    def test_not_empty(self) -> None:
        self.assertGreater(len(SETTINGS_DEFAULTS), 0)

    def test_has_active_mem_profile(self) -> None:
        self.assertIn("active_mem_profile", SETTINGS_DEFAULTS)

    def test_has_model(self) -> None:
        self.assertIn("model", SETTINGS_DEFAULTS)

    def test_values_are_strings(self) -> None:
        for v in SETTINGS_DEFAULTS.values():
            self.assertIsInstance(v, str)


# ─────────────────────────────────────────────────────────────────────────────
# memory/bootstrap — load_settings
# ─────────────────────────────────────────────────────────────────────────────

class LoadSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._settings_path = Path(self._tmpdir.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_missing_file_returns_defaults(self) -> None:
        result = load_settings(settings_path=self._settings_path)
        self.assertEqual(result, SETTINGS_DEFAULTS)

    def test_returns_dict(self) -> None:
        result = load_settings(settings_path=self._settings_path)
        self.assertIsInstance(result, dict)

    def test_existing_file_merged_with_defaults(self) -> None:
        self._settings_path.write_text(
            json.dumps({"model": "custom-model"}), encoding="utf-8"
        )
        result = load_settings(settings_path=self._settings_path)
        self.assertEqual(result["model"], "custom-model")
        # Defaults fill in missing keys
        self.assertIn("active_mem_profile", result)

    def test_existing_file_override_active_mem_profile(self) -> None:
        self._settings_path.write_text(
            json.dumps({"active_mem_profile": "work"}), encoding="utf-8"
        )
        result = load_settings(settings_path=self._settings_path)
        self.assertEqual(result["active_mem_profile"], "work")

    def test_invalid_json_returns_defaults(self) -> None:
        self._settings_path.write_text("not valid json!!", encoding="utf-8")
        result = load_settings(settings_path=self._settings_path)
        self.assertEqual(result, SETTINGS_DEFAULTS)


# ─────────────────────────────────────────────────────────────────────────────
# memory/bootstrap — save_settings
# ─────────────────────────────────────────────────────────────────────────────

class SaveSettingsTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._settings_path = Path(self._tmpdir.name) / "settings.json"

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_returns_none(self) -> None:
        result = save_settings(
            settings_path=self._settings_path, settings={"key": "val"}
        )
        self.assertIsNone(result)

    def test_creates_file(self) -> None:
        save_settings(settings_path=self._settings_path, settings={"a": "b"})
        self.assertTrue(self._settings_path.exists())

    def test_saved_data_is_json(self) -> None:
        save_settings(settings_path=self._settings_path, settings={"x": "y"})
        data = json.loads(self._settings_path.read_text(encoding="utf-8"))
        self.assertEqual(data["x"], "y")

    def test_roundtrip_load_after_save(self) -> None:
        settings = {"model": "saved-model", "active_mem_profile": "test"}
        save_settings(settings_path=self._settings_path, settings=settings)
        result = load_settings(settings_path=self._settings_path)
        self.assertEqual(result["model"], "saved-model")
        self.assertEqual(result["active_mem_profile"], "test")

    def test_does_not_raise_on_invalid_path(self) -> None:
        try:
            save_settings(
                settings_path="/nonexistent/dir/settings.json",
                settings={"k": "v"},
            )
        except Exception as exc:
            self.fail(f"save_settings raised unexpectedly: {exc}")


if __name__ == "__main__":
    unittest.main()
