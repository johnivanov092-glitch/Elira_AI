"""Tests for pure helpers in two memory sub-modules:
  memory/context — default_content_hash, _memory_type_weight,
    _clean_memory_text, _memory_query_words
  memory/search — vector_memory_capability_status, keyword_search_memory,
    semantic_search_memory (with mock load_memories_func)
"""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.memory.context import (  # noqa: E402
    default_content_hash,
    _memory_type_weight,
    _clean_memory_text,
    _memory_query_words,
)
from app.application.memory.search import (  # noqa: E402
    vector_memory_capability_status,
    keyword_search_memory,
    semantic_search_memory,
)


# ─────────────────────────────────────────────────────────────────────────────
# memory/context — default_content_hash
# ─────────────────────────────────────────────────────────────────────────────

class DefaultContentHashTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(default_content_hash("hello"), str)

    def test_same_input_same_hash(self) -> None:
        self.assertEqual(
            default_content_hash("hello world"),
            default_content_hash("hello world"),
        )

    def test_different_inputs_different_hash(self) -> None:
        self.assertNotEqual(
            default_content_hash("hello"),
            default_content_hash("world"),
        )

    def test_case_insensitive(self) -> None:
        self.assertEqual(
            default_content_hash("Hello World"),
            default_content_hash("hello world"),
        )

    def test_strips_whitespace(self) -> None:
        self.assertEqual(
            default_content_hash("  hello  "),
            default_content_hash("hello"),
        )

    def test_hash_is_32_chars_md5_hex(self) -> None:
        result = default_content_hash("test")
        self.assertEqual(len(result), 32)
        # Valid hex string
        int(result, 16)

    def test_empty_string_stable(self) -> None:
        h = default_content_hash("")
        self.assertIsInstance(h, str)
        self.assertEqual(len(h), 32)


# ─────────────────────────────────────────────────────────────────────────────
# memory/context — _memory_type_weight
# ─────────────────────────────────────────────────────────────────────────────

class MemoryTypeWeightTest(unittest.TestCase):
    def test_returns_float(self) -> None:
        self.assertIsInstance(_memory_type_weight("general"), float)

    def test_profile_has_high_weight(self) -> None:
        self.assertGreater(_memory_type_weight("profile"), _memory_type_weight("chat"))

    def test_pinned_adds_bonus(self) -> None:
        w_normal = _memory_type_weight("general", pinned=False)
        w_pinned = _memory_type_weight("general", pinned=True)
        self.assertGreater(w_pinned, w_normal)

    def test_manual_source_adds_bonus(self) -> None:
        w_auto = _memory_type_weight("general", source="auto")
        w_manual = _memory_type_weight("general", source="manual_add")
        self.assertGreater(w_manual, w_auto)

    def test_unknown_type_returns_default(self) -> None:
        w = _memory_type_weight("unknown_type")
        self.assertGreater(w, 0.0)

    def test_empty_type_returns_default(self) -> None:
        w = _memory_type_weight("")
        self.assertGreater(w, 0.0)

    def test_pinned_type_has_high_weight(self) -> None:
        self.assertGreater(_memory_type_weight("pinned"), 3.0)


# ─────────────────────────────────────────────────────────────────────────────
# memory/context — _clean_memory_text
# ─────────────────────────────────────────────────────────────────────────────

class CleanMemoryTextTest(unittest.TestCase):
    def test_returns_string(self) -> None:
        self.assertIsInstance(_clean_memory_text("hello"), str)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_clean_memory_text(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(_clean_memory_text(None), "")  # type: ignore[arg-type]

    def test_collapses_whitespace(self) -> None:
        result = _clean_memory_text("hello   world")
        self.assertEqual(result, "hello world")

    def test_short_text_unchanged(self) -> None:
        result = _clean_memory_text("hello world")
        self.assertEqual(result, "hello world")

    def test_long_text_truncated(self) -> None:
        long_text = "A" * 1000
        result = _clean_memory_text(long_text, max_chars=100)
        self.assertLessEqual(len(result), 105)  # max_chars + ellipsis

    def test_truncated_text_ends_with_ellipsis(self) -> None:
        result = _clean_memory_text("A" * 1000, max_chars=10)
        self.assertIn("…", result)


# ─────────────────────────────────────────────────────────────────────────────
# memory/context — _memory_query_words
# ─────────────────────────────────────────────────────────────────────────────

class MemoryQueryWordsTest(unittest.TestCase):
    def test_returns_list(self) -> None:
        self.assertIsInstance(_memory_query_words("hello world"), list)

    def test_basic_words_extracted(self) -> None:
        result = _memory_query_words("python programming")
        self.assertIn("python", result)

    def test_short_words_excluded(self) -> None:
        # Words < 3 chars are excluded
        result = _memory_query_words("a is to python")
        self.assertNotIn("a", result)
        self.assertNotIn("is", result)
        self.assertNotIn("to", result)

    def test_lowercases(self) -> None:
        result = _memory_query_words("Hello World")
        self.assertIn("hello", result)

    def test_empty_returns_empty(self) -> None:
        self.assertEqual(_memory_query_words(""), [])

    def test_none_returns_empty(self) -> None:
        self.assertEqual(_memory_query_words(None), [])  # type: ignore[arg-type]

    def test_russian_words_extracted(self) -> None:
        result = _memory_query_words("питон программирование")
        self.assertIn("питон", result)


# ─────────────────────────────────────────────────────────────────────────────
# memory/search — vector_memory_capability_status
# ─────────────────────────────────────────────────────────────────────────────

class VectorMemoryCapabilityStatusTest(unittest.TestCase):
    def test_returns_dict(self) -> None:
        self.assertIsInstance(vector_memory_capability_status(), dict)

    def test_has_feature_key(self) -> None:
        self.assertIn("feature", vector_memory_capability_status())

    def test_has_available_key(self) -> None:
        self.assertIn("available", vector_memory_capability_status())

    def test_has_mode_key(self) -> None:
        self.assertIn("mode", vector_memory_capability_status())

    def test_feature_is_vector_memory(self) -> None:
        self.assertEqual(vector_memory_capability_status()["feature"], "vector_memory")

    def test_available_is_bool(self) -> None:
        self.assertIsInstance(vector_memory_capability_status()["available"], bool)

    def test_missing_packages_is_list(self) -> None:
        self.assertIsInstance(vector_memory_capability_status()["missing_packages"], list)

    def test_mode_is_string(self) -> None:
        self.assertIsInstance(vector_memory_capability_status()["mode"], str)


# ─────────────────────────────────────────────────────────────────────────────
# memory/search — keyword_search_memory
# ─────────────────────────────────────────────────────────────────────────────

class KeywordSearchMemoryTest(unittest.TestCase):
    _ROWS = [
        (1, "Python is great for scripting tasks"),
        (2, "JavaScript is used for web development"),
        (3, "Python machine learning with pandas and numpy"),
    ]

    def _load(self, limit, profile_name=""):
        return list(self._ROWS[:limit])

    def test_returns_list(self) -> None:
        result = keyword_search_memory(
            query="python", load_memories_func=self._load
        )
        self.assertIsInstance(result, list)

    def test_empty_query_returns_empty(self) -> None:
        result = keyword_search_memory(
            query="", load_memories_func=self._load
        )
        self.assertEqual(result, [])

    def test_finds_matching_text(self) -> None:
        result = keyword_search_memory(
            query="python", load_memories_func=self._load
        )
        self.assertTrue(any("python" in r.lower() for r in result))

    def test_returns_strings(self) -> None:
        result = keyword_search_memory(
            query="python", load_memories_func=self._load
        )
        for item in result:
            self.assertIsInstance(item, str)

    def test_no_match_returns_empty(self) -> None:
        result = keyword_search_memory(
            query="fortran cobol assembler", load_memories_func=self._load
        )
        self.assertEqual(result, [])

    def test_top_k_limits_results(self) -> None:
        result = keyword_search_memory(
            query="python", top_k=1, load_memories_func=self._load
        )
        self.assertLessEqual(len(result), 1)


# ─────────────────────────────────────────────────────────────────────────────
# memory/search — semantic_search_memory
# ─────────────────────────────────────────────────────────────────────────────

class SemanticSearchMemoryTest(unittest.TestCase):
    _ROWS = [
        (1, "Python is a programming language"),
        (2, "cats and dogs are pets"),
        (3, "machine learning with Python is powerful"),
    ]

    def _load(self, limit, profile_name=""):
        return list(self._ROWS[:limit])

    def test_returns_list(self) -> None:
        result = semantic_search_memory(
            query="python", load_memories_func=self._load
        )
        self.assertIsInstance(result, list)

    def test_empty_query_returns_empty(self) -> None:
        result = semantic_search_memory(
            query="", load_memories_func=self._load
        )
        self.assertEqual(result, [])

    def test_results_are_strings(self) -> None:
        result = semantic_search_memory(
            query="python", load_memories_func=self._load
        )
        for item in result:
            self.assertIsInstance(item, str)

    def test_no_rows_returns_empty(self) -> None:
        result = semantic_search_memory(
            query="python", load_memories_func=lambda *a, **kw: []
        )
        self.assertEqual(result, [])

    def test_top_k_limits_results(self) -> None:
        result = semantic_search_memory(
            query="python", top_k=1, load_memories_func=self._load
        )
        self.assertLessEqual(len(result), 1)


if __name__ == "__main__":
    unittest.main()
