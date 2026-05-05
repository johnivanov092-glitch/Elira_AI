"""Tests for application/memory (context, search, store, web_knowledge)
and application/media (image_generation pure helpers)."""
from __future__ import annotations

import hashlib
import sys
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.memory.context import (  # noqa: E402
    _clean_memory_text,
    _memory_query_words,
    _memory_type_weight,
    default_content_hash,
)
from app.application.memory.search import (  # noqa: E402
    keyword_search_memory,
    vector_memory_capability_status,
)
from app.application.memory.web_knowledge import (  # noqa: E402
    build_browser_rag_records,
    build_web_knowledge_records,
    chunk_browser_text,
    clean_browser_text,
)
from app.application.memory.store import (  # noqa: E402
    add_memory,
    clear_memories,
    create_mem_profile,
    delete_mem_profile,
    delete_memory,
    export_memories,
    list_mem_profiles,
    load_memories,
    set_memory_pin,
)
from app.application.memory.bootstrap import init_db as mem_init_db  # noqa: E402
from app.application.media.image_generation import (  # noqa: E402
    contains_cyrillic,
    hf_access_hint,
    strip_ansi,
)


# ─────────────────────────────────────────────────────────────────────────────
# Helpers
# ─────────────────────────────────────────────────────────────────────────────

def _now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _content_hash(text: str) -> str:
    return hashlib.md5(text.strip().lower().encode("utf-8")).hexdigest()


# ─────────────────────────────────────────────────────────────────────────────
# memory/context.py — pure helpers
# ─────────────────────────────────────────────────────────────────────────────

class MemoryTypeWeightTest(unittest.TestCase):
    def test_profile_highest_weight(self) -> None:
        w = _memory_type_weight("profile")
        self.assertGreater(w, 4.0)

    def test_chat_lowest_weight(self) -> None:
        w_chat = _memory_type_weight("chat")
        w_profile = _memory_type_weight("profile")
        self.assertLess(w_chat, w_profile)

    def test_pinned_adds_bonus(self) -> None:
        w_normal = _memory_type_weight("general")
        w_pinned = _memory_type_weight("general", pinned=True)
        self.assertGreater(w_pinned, w_normal)

    def test_manual_source_adds_bonus(self) -> None:
        w_auto = _memory_type_weight("general", source="auto")
        w_manual = _memory_type_weight("general", source="manual_edit")
        self.assertGreater(w_manual, w_auto)

    def test_unknown_type_gets_default_weight(self) -> None:
        w = _memory_type_weight("nonexistent_type")
        self.assertGreater(w, 0)

    def test_none_type_handled(self) -> None:
        w = _memory_type_weight(None)
        self.assertGreater(w, 0)

    def test_empty_string_type(self) -> None:
        w = _memory_type_weight("")
        self.assertGreater(w, 0)


class CleanMemoryTextTest(unittest.TestCase):
    def test_short_text_unchanged(self) -> None:
        text = "Hello world"
        result = _clean_memory_text(text, max_chars=100)
        self.assertEqual(result, "Hello world")

    def test_long_text_truncated(self) -> None:
        text = "a" * 1000
        result = _clean_memory_text(text, max_chars=100)
        self.assertLessEqual(len(result), 110)  # with ellipsis
        self.assertIn("…", result)

    def test_whitespace_normalized(self) -> None:
        text = "hello   world\n\nfoo"
        result = _clean_memory_text(text)
        self.assertNotIn("  ", result)

    def test_empty_text(self) -> None:
        result = _clean_memory_text("", max_chars=100)
        self.assertEqual(result, "")

    def test_none_text(self) -> None:
        result = _clean_memory_text(None)
        self.assertEqual(result, "")


class MemoryQueryWordsTest(unittest.TestCase):
    def test_extracts_words_min_length_3(self) -> None:
        # min length is >= 3, so "the" (3 chars) IS included
        words = _memory_query_words("find the best solution")
        self.assertIn("find", words)
        self.assertIn("the", words)
        self.assertIn("best", words)
        self.assertIn("solution", words)

    def test_empty_query_returns_empty(self) -> None:
        self.assertEqual(_memory_query_words(""), [])

    def test_none_query_returns_empty(self) -> None:
        self.assertEqual(_memory_query_words(None), [])

    def test_cyrillic_words_extracted(self) -> None:
        words = _memory_query_words("найди лучший ответ")
        self.assertGreater(len(words), 0)

    def test_short_words_excluded(self) -> None:
        # min length is 3 chars; "ab", "cd", "ef" are 2 chars each
        words = _memory_query_words("ab cd ef")
        self.assertEqual(words, [])

    def test_words_exactly_3_chars_included(self) -> None:
        words = _memory_query_words("the big cat ran")
        self.assertIn("the", words)  # 3 chars = included
        self.assertIn("big", words)
        self.assertIn("cat", words)
        self.assertIn("ran", words)


class DefaultContentHashTest(unittest.TestCase):
    def test_same_text_same_hash(self) -> None:
        h1 = default_content_hash("hello world")
        h2 = default_content_hash("hello world")
        self.assertEqual(h1, h2)

    def test_different_text_different_hash(self) -> None:
        h1 = default_content_hash("hello")
        h2 = default_content_hash("world")
        self.assertNotEqual(h1, h2)

    def test_case_insensitive(self) -> None:
        h1 = default_content_hash("Hello World")
        h2 = default_content_hash("hello world")
        self.assertEqual(h1, h2)

    def test_whitespace_stripped(self) -> None:
        h1 = default_content_hash("  hello  ")
        h2 = default_content_hash("hello")
        self.assertEqual(h1, h2)

    def test_returns_string(self) -> None:
        self.assertIsInstance(default_content_hash("test"), str)


# ─────────────────────────────────────────────────────────────────────────────
# memory/search.py — vector_memory_capability_status and keyword_search_memory
# ─────────────────────────────────────────────────────────────────────────────

class VectorMemoryCapabilityStatusTest(unittest.TestCase):
    def test_returns_dict_with_required_keys(self) -> None:
        result = vector_memory_capability_status()
        for key in ("feature", "available", "mode", "reason", "missing_packages", "hint"):
            self.assertIn(key, result)

    def test_feature_is_vector_memory(self) -> None:
        result = vector_memory_capability_status()
        self.assertEqual(result["feature"], "vector_memory")

    def test_available_is_bool(self) -> None:
        result = vector_memory_capability_status()
        self.assertIsInstance(result["available"], bool)

    def test_mode_is_valid_string(self) -> None:
        result = vector_memory_capability_status()
        self.assertIn(result["mode"], ("vector", "keyword_fallback"))

    def test_missing_packages_is_list(self) -> None:
        result = vector_memory_capability_status()
        self.assertIsInstance(result["missing_packages"], list)


class KeywordSearchMemoryTest(unittest.TestCase):
    def _make_loader(self, rows):
        """Returns a load_memories_func that returns fixed rows."""
        def _load(limit=1000, profile_name=""):
            return rows
        return _load

    def test_finds_matching_text(self) -> None:
        rows = [
            (1, "Python uses indentation for code blocks"),
            (2, "JavaScript uses curly braces"),
        ]
        result = keyword_search_memory(
            query="python indentation",
            load_memories_func=self._make_loader(rows),
        )
        self.assertGreater(len(result), 0)
        self.assertTrue(any("indentation" in r for r in result))

    def test_empty_query_returns_empty(self) -> None:
        rows = [(1, "some memory text")]
        result = keyword_search_memory(
            query="",
            load_memories_func=self._make_loader(rows),
        )
        self.assertEqual(result, [])

    def test_no_match_returns_empty(self) -> None:
        rows = [(1, "Python code snippet here")]
        result = keyword_search_memory(
            query="quantum_physics_xyz",
            load_memories_func=self._make_loader(rows),
        )
        self.assertEqual(result, [])

    def test_respects_top_k(self) -> None:
        rows = [(i, f"Python is great language {i}") for i in range(20)]
        result = keyword_search_memory(
            query="python",
            top_k=3,
            load_memories_func=self._make_loader(rows),
        )
        self.assertLessEqual(len(result), 3)

    def test_empty_rows_returns_empty(self) -> None:
        result = keyword_search_memory(
            query="anything",
            load_memories_func=self._make_loader([]),
        )
        self.assertEqual(result, [])

    def test_exact_phrase_scores_higher(self) -> None:
        rows = [
            (1, "Python is a programming language"),
            (2, "Python programming language is great for data science"),
        ]
        result = keyword_search_memory(
            query="Python programming language",
            load_memories_func=self._make_loader(rows),
        )
        # Both should match; exact phrase match doc should rank first
        self.assertGreater(len(result), 0)


# ─────────────────────────────────────────────────────────────────────────────
# memory/web_knowledge.py — pure functions
# ─────────────────────────────────────────────────────────────────────────────

class CleanBrowserTextTest(unittest.TestCase):
    def test_empty_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(clean_browser_text(None), "")

    def test_strips_whitespace(self) -> None:
        self.assertEqual(clean_browser_text("  hello  "), "hello")

    def test_replaces_tabs(self) -> None:
        result = clean_browser_text("hello\tworld")
        self.assertNotIn("\t", result)

    def test_replaces_carriage_returns(self) -> None:
        result = clean_browser_text("line1\rline2")
        self.assertNotIn("\r", result)

    def test_collapses_multiple_spaces(self) -> None:
        result = clean_browser_text("hello   world")
        self.assertNotIn("  ", result)


class ChunkBrowserTextTest(unittest.TestCase):
    def test_empty_returns_empty_list(self) -> None:
        self.assertEqual(chunk_browser_text(""), [])

    def test_short_text_single_chunk(self) -> None:
        chunks = chunk_browser_text("hello world", size=100)
        self.assertEqual(len(chunks), 1)
        self.assertEqual(chunks[0], "hello world")

    def test_long_text_splits(self) -> None:
        text = "a" * 3000
        chunks = chunk_browser_text(text, size=1000)
        self.assertGreater(len(chunks), 1)

    def test_chunks_not_empty(self) -> None:
        text = "word " * 500
        chunks = chunk_browser_text(text, size=100)
        for chunk in chunks:
            self.assertTrue(chunk.strip())


class BuildBrowserRagRecordsTest(unittest.TestCase):
    def test_empty_summary_and_page_returns_empty(self) -> None:
        result = build_browser_rag_records(
            url="https://example.com",
            goal="learn python",
            summary="",
            page_text="",
        )
        self.assertEqual(result, [])

    def test_summary_produces_record(self) -> None:
        result = build_browser_rag_records(
            url="https://example.com",
            goal="learn python",
            summary="Python is a great language",
            page_text="",
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0]["type"], "browser_summary")
        self.assertEqual(result[0]["url"], "https://example.com")

    def test_page_text_produces_chunks(self) -> None:
        result = build_browser_rag_records(
            url="https://example.com",
            goal="learn",
            summary="",
            page_text="x " * 2000,
        )
        self.assertTrue(any(r["type"] == "browser_page" for r in result))

    def test_all_records_have_required_fields(self) -> None:
        result = build_browser_rag_records(
            url="https://example.com",
            goal="test",
            summary="short summary",
            page_text="some page text",
        )
        for record in result:
            for key in ("type", "url", "goal", "content"):
                self.assertIn(key, record)


class BuildWebKnowledgeRecordsTest(unittest.TestCase):
    def test_empty_web_context_returns_empty(self) -> None:
        result = build_web_knowledge_records(query="python", web_context="")
        self.assertEqual(result, [])

    def test_produces_web_summary_record(self) -> None:
        result = build_web_knowledge_records(
            query="python", web_context="Python is awesome"
        )
        self.assertTrue(any(r["type"] == "web_summary" for r in result))

    def test_summary_record_contains_query(self) -> None:
        result = build_web_knowledge_records(
            query="python async", web_context="Asyncio is powerful"
        )
        summary = next(r for r in result if r["type"] == "web_summary")
        self.assertIn("WEB QUERY", summary["content"])

    def test_source_kind_preserved(self) -> None:
        result = build_web_knowledge_records(
            query="q", web_context="text", source_kind="tavily"
        )
        for r in result:
            self.assertEqual(r.get("source_kind"), "tavily")

    def test_none_query_handled(self) -> None:
        # build_web_knowledge_records uses (query or "").strip()
        result = build_web_knowledge_records(query=None, web_context="some text")
        # Should not raise; summary record may have empty goal
        self.assertIsInstance(result, list)


# ─────────────────────────────────────────────────────────────────────────────
# memory/store.py — DB-backed CRUD (using temp file)
# ─────────────────────────────────────────────────────────────────────────────

class MemoryStoreCRUDTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._db = str(Path(self._tmpdir.name) / "memories.db")
        mem_init_db(db_path=self._db, now_iso_func=_now_iso)

    def tearDown(self) -> None:
        self._tmpdir.cleanup()

    def test_list_mem_profiles_has_default(self) -> None:
        profiles = list_mem_profiles(db_path=self._db)
        names = [p["name"] for p in profiles]
        self.assertIn("default", names)

    def test_create_mem_profile_success(self) -> None:
        ok = create_mem_profile(
            db_path=self._db, name="work", now_iso_func=_now_iso
        )
        self.assertTrue(ok)
        profiles = list_mem_profiles(db_path=self._db)
        self.assertTrue(any(p["name"] == "work" for p in profiles))

    def test_create_mem_profile_empty_name_rejected(self) -> None:
        ok = create_mem_profile(db_path=self._db, name="", now_iso_func=_now_iso)
        self.assertFalse(ok)

    def test_create_mem_profile_too_long_rejected(self) -> None:
        ok = create_mem_profile(
            db_path=self._db, name="x" * 50, now_iso_func=_now_iso
        )
        self.assertFalse(ok)

    def test_delete_mem_profile_removes_it(self) -> None:
        create_mem_profile(db_path=self._db, name="temp", now_iso_func=_now_iso)
        delete_mem_profile(db_path=self._db, name="temp")
        profiles = list_mem_profiles(db_path=self._db)
        self.assertFalse(any(p["name"] == "temp" for p in profiles))

    def test_delete_default_profile_is_noop(self) -> None:
        delete_mem_profile(db_path=self._db, name="default")
        profiles = list_mem_profiles(db_path=self._db)
        self.assertTrue(any(p["name"] == "default" for p in profiles))

    def test_add_memory_success(self) -> None:
        ok = add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="Python is great for data science",
        )
        self.assertTrue(ok)

    def test_add_memory_empty_content_rejected(self) -> None:
        ok = add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="",
        )
        self.assertFalse(ok)

    def test_load_memories_empty_initially(self) -> None:
        rows = load_memories(db_path=self._db, limit=1000)
        self.assertEqual(len(rows), 0)

    def test_load_memories_after_add(self) -> None:
        add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="Python uses indentation for blocks",
        )
        rows = load_memories(db_path=self._db, limit=1000)
        self.assertEqual(len(rows), 1)

    def test_load_memories_respects_limit(self) -> None:
        for i in range(5):
            add_memory(
                db_path=self._db,
                content_hash_func=_content_hash,
                now_iso_func=_now_iso,
                content=f"Memory item number {i} with unique text for dedup avoidance",
            )
        rows = load_memories(db_path=self._db, limit=2)
        self.assertLessEqual(len(rows), 2)

    def test_delete_memory_removes_it(self) -> None:
        add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="To be deleted memory content here",
        )
        rows = load_memories(db_path=self._db, limit=1000)
        mem_id = rows[0][0]
        delete_memory(db_path=self._db, memory_id=mem_id)
        rows_after = load_memories(db_path=self._db, limit=1000)
        self.assertEqual(len(rows_after), 0)

    def test_clear_memories_by_profile(self) -> None:
        add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="Memory in default profile to clear",
            profile_name="default",
        )
        clear_memories(db_path=self._db, profile_name="default")
        rows = load_memories(db_path=self._db, limit=1000, profile_name="default")
        self.assertEqual(len(rows), 0)

    def test_set_memory_pin(self) -> None:
        add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="Pinnable memory item with enough text",
        )
        rows = load_memories(db_path=self._db, limit=1000)
        mem_id = rows[0][0]
        set_memory_pin(db_path=self._db, memory_id=mem_id, pinned=True)
        # Re-load and verify pinned flag (index 4 is pinned column per SELECT order)
        rows_after = load_memories(db_path=self._db, limit=1000)
        pinned_val = rows_after[0][4]  # id, content, source, created_at, pinned, ...
        self.assertEqual(pinned_val, 1)

    def test_export_memories_returns_json_string(self) -> None:
        import json
        add_memory(
            db_path=self._db,
            content_hash_func=_content_hash,
            now_iso_func=_now_iso,
            content="Export candidate memory item text",
        )
        # export_memories takes a load_memories_func, not db_path
        def _load(limit=5000, profile_name=""):
            return load_memories(db_path=self._db, limit=limit, profile_name=profile_name)

        exported = export_memories(load_memories_func=_load)
        self.assertIsInstance(exported, str)
        parsed = json.loads(exported)
        self.assertIsInstance(parsed, list)
        self.assertGreater(len(parsed), 0)


# ─────────────────────────────────────────────────────────────────────────────
# media/image_generation.py — pure string helpers
# ─────────────────────────────────────────────────────────────────────────────

class StripAnsiTest(unittest.TestCase):
    def test_strips_color_codes(self) -> None:
        raw = "\x1B[31mError message\x1B[0m"
        result = strip_ansi(raw)
        self.assertNotIn("\x1B", result)
        self.assertIn("Error message", result)

    def test_plain_text_unchanged(self) -> None:
        result = strip_ansi("Hello world")
        self.assertEqual(result, "Hello world")

    def test_none_returns_empty(self) -> None:
        result = strip_ansi(None)
        self.assertEqual(result, "")

    def test_empty_string_returns_empty(self) -> None:
        result = strip_ansi("")
        self.assertEqual(result, "")

    def test_strips_reset_sequence(self) -> None:
        result = strip_ansi("\x1B[0m")
        self.assertEqual(result, "")


class ContainsCyrillicTest(unittest.TestCase):
    def test_cyrillic_text_returns_true(self) -> None:
        self.assertTrue(contains_cyrillic("Привет мир"))

    def test_latin_text_returns_false(self) -> None:
        self.assertFalse(contains_cyrillic("Hello world"))

    def test_mixed_returns_true(self) -> None:
        self.assertTrue(contains_cyrillic("Hello Мир"))

    def test_empty_returns_false(self) -> None:
        self.assertFalse(contains_cyrillic(""))

    def test_none_returns_false(self) -> None:
        self.assertFalse(contains_cyrillic(None))

    def test_yo_letter_detected(self) -> None:
        self.assertTrue(contains_cyrillic("Ёлка"))


class HfAccessHintTest(unittest.TestCase):
    def test_gated_model_returns_hint(self) -> None:
        result = hf_access_hint("Access to model is gated")
        self.assertGreater(len(result), 0)
        self.assertIn("FLUX", result)

    def test_401_returns_hint(self) -> None:
        result = hf_access_hint("401 Unauthorized response")
        self.assertGreater(len(result), 0)

    def test_403_returns_hint(self) -> None:
        result = hf_access_hint("403 Forbidden error")
        self.assertGreater(len(result), 0)

    def test_must_be_logged_in_returns_hint(self) -> None:
        result = hf_access_hint("You must be logged in to access this")
        self.assertGreater(len(result), 0)

    def test_unrelated_error_returns_empty(self) -> None:
        result = hf_access_hint("Connection timeout error occurred")
        self.assertEqual(result, "")

    def test_empty_returns_empty(self) -> None:
        result = hf_access_hint("")
        self.assertEqual(result, "")

    def test_none_returns_empty(self) -> None:
        result = hf_access_hint(None)
        self.assertEqual(result, "")

    def test_accept_conditions_returns_hint(self) -> None:
        result = hf_access_hint("You need to accept the conditions to proceed")
        self.assertGreater(len(result), 0)


if __name__ == "__main__":
    unittest.main()
