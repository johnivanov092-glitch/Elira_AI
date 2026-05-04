"""Tests for application/response_cache — policy (pure) and runtime (patched DB)."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
BACKEND_ROOT = ROOT / "backend"

if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

from app.application.response_cache import policy as cache_policy   # noqa: E402
import app.application.response_cache.runtime as rc                  # noqa: E402


# ─────────────────────────────────────────────────────────────────────────────
# policy — normalize_query
# ─────────────────────────────────────────────────────────────────────────────

class NormalizeQueryTest(unittest.TestCase):
    def test_lowercased(self) -> None:
        self.assertEqual(cache_policy.normalize_query("HELLO WORLD"), "hello world")

    def test_punctuation_stripped(self) -> None:
        result = cache_policy.normalize_query("Hello, World!")
        self.assertNotIn(",", result)
        self.assertNotIn("!", result)

    def test_extra_spaces_collapsed(self) -> None:
        result = cache_policy.normalize_query("  too   many   spaces  ")
        self.assertNotIn("  ", result)
        self.assertEqual(result, result.strip())

    def test_empty_string_returns_empty(self) -> None:
        self.assertEqual(cache_policy.normalize_query(""), "")

    def test_none_returns_empty(self) -> None:
        self.assertEqual(cache_policy.normalize_query(None), "")  # type: ignore[arg-type]


# ─────────────────────────────────────────────────────────────────────────────
# policy — query_hash
# ─────────────────────────────────────────────────────────────────────────────

class QueryHashTest(unittest.TestCase):
    def test_same_inputs_same_hash(self) -> None:
        h1 = cache_policy.query_hash("hello world", "gemma", "default")
        h2 = cache_policy.query_hash("hello world", "gemma", "default")
        self.assertEqual(h1, h2)

    def test_different_query_different_hash(self) -> None:
        h1 = cache_policy.query_hash("query a", "gemma", "default")
        h2 = cache_policy.query_hash("query b", "gemma", "default")
        self.assertNotEqual(h1, h2)

    def test_different_model_different_hash(self) -> None:
        h1 = cache_policy.query_hash("same query", "gemma", "default")
        h2 = cache_policy.query_hash("same query", "llama", "default")
        self.assertNotEqual(h1, h2)

    def test_different_profile_different_hash(self) -> None:
        h1 = cache_policy.query_hash("same", "model", "profile_a")
        h2 = cache_policy.query_hash("same", "model", "profile_b")
        self.assertNotEqual(h1, h2)

    def test_returns_hex_string(self) -> None:
        h = cache_policy.query_hash("x", "y", "z")
        self.assertIsInstance(h, str)
        int(h, 16)  # must be valid hex


# ─────────────────────────────────────────────────────────────────────────────
# policy — should_cache_query
# ─────────────────────────────────────────────────────────────────────────────

def _no_web(query: str) -> dict:
    return {"requires_web": False, "freshness_sensitive": False}

def _needs_web(query: str) -> dict:
    return {"requires_web": True, "freshness_sensitive": True}


class ShouldCacheQueryTest(unittest.TestCase):
    def test_generic_chat_query_should_cache(self) -> None:
        self.assertTrue(
            cache_policy.should_cache_query(
                query="Что такое рекурсия?",
                route="chat",
                detect_temporal_intent_func=_no_web,
            )
        )

    def test_memory_command_not_cached(self) -> None:
        self.assertFalse(
            cache_policy.should_cache_query(
                query="запомни моё имя Алиса",
                route="chat",
                detect_temporal_intent_func=_no_web,
            )
        )

    def test_code_route_not_cached(self) -> None:
        self.assertFalse(
            cache_policy.should_cache_query(
                query="Напиши функцию сортировки",
                route="code",
                detect_temporal_intent_func=_no_web,
            )
        )

    def test_project_route_not_cached(self) -> None:
        self.assertFalse(
            cache_policy.should_cache_query(
                query="Обнови проект",
                route="project",
                detect_temporal_intent_func=_no_web,
            )
        )

    def test_temporal_query_not_cached(self) -> None:
        self.assertFalse(
            cache_policy.should_cache_query(
                query="Какие новости сегодня?",
                route="chat",
                detect_temporal_intent_func=_needs_web,
            )
        )

    def test_сейчас_keyword_not_cached(self) -> None:
        self.assertFalse(
            cache_policy.should_cache_query(
                query="что происходит сейчас",
                route="chat",
                detect_temporal_intent_func=_no_web,
            )
        )

    def test_research_route_can_be_cached(self) -> None:
        self.assertTrue(
            cache_policy.should_cache_query(
                query="Объясни принципы SOLID в программировании",
                route="research",
                detect_temporal_intent_func=_no_web,
            )
        )


# ─────────────────────────────────────────────────────────────────────────────
# runtime — get_cached / set_cached / clear / stats (patched _DB_PATH)
# ─────────────────────────────────────────────────────────────────────────────

_LONG_QUERY    = "What is the difference between supervised and unsupervised machine learning?"
_LONG_RESPONSE = "Supervised learning uses labeled data to train models that predict outcomes; " \
                 "unsupervised learning finds patterns in unlabeled data."


class ResponseCacheRuntimeTest(unittest.TestCase):
    def setUp(self) -> None:
        self._tmpdir = tempfile.TemporaryDirectory()
        self._orig_db = rc._DB_PATH
        rc._DB_PATH = Path(self._tmpdir.name) / "cache.db"
        rc._init_db()

    def tearDown(self) -> None:
        rc._DB_PATH = self._orig_db
        self._tmpdir.cleanup()
        super().tearDown()

    def test_cache_miss_returns_none(self) -> None:
        result = rc.get_cached(_LONG_QUERY, "gemma", "default")
        self.assertIsNone(result)

    def test_set_then_get_returns_cached(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        result = rc.get_cached(_LONG_QUERY, "gemma", "default")
        self.assertEqual(result, _LONG_RESPONSE)

    def test_different_model_is_separate_entry(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        result = rc.get_cached(_LONG_QUERY, "llama3", "default")
        self.assertIsNone(result)

    def test_different_profile_is_separate_entry(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        result = rc.get_cached(_LONG_QUERY, "gemma", "work")
        self.assertIsNone(result)

    def test_short_query_not_stored(self) -> None:
        rc.set_cached("short", "gemma", "default", _LONG_RESPONSE)
        result = rc.get_cached("short", "gemma", "default")
        self.assertIsNone(result)

    def test_short_response_not_stored(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", "too short")
        result = rc.get_cached(_LONG_QUERY, "gemma", "default")
        self.assertIsNone(result)

    def test_clear_cache_empties_entries(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        rc.clear_cache()
        result = rc.get_cached(_LONG_QUERY, "gemma", "default")
        self.assertIsNone(result)

    def test_cache_stats_empty(self) -> None:
        stats = rc.cache_stats()
        self.assertEqual(stats["total_entries"], 0)
        self.assertEqual(stats["total_hits"], 0)
        self.assertIn("max_size", stats)
        self.assertIn("ttl_seconds", stats)

    def test_cache_stats_after_set(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        stats = rc.cache_stats()
        self.assertEqual(stats["total_entries"], 1)

    def test_hit_count_increments_on_get(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", _LONG_RESPONSE)
        rc.get_cached(_LONG_QUERY, "gemma", "default")
        rc.get_cached(_LONG_QUERY, "gemma", "default")
        stats = rc.cache_stats()
        self.assertGreaterEqual(stats["total_hits"], 2)

    def test_error_response_not_cached(self) -> None:
        rc.set_cached(_LONG_QUERY, "gemma", "default", "⚠️ Произошла ошибка при обработке запроса.")
        result = rc.get_cached(_LONG_QUERY, "gemma", "default")
        self.assertIsNone(result)

    def test_should_cache_delegates_to_policy(self) -> None:
        # stable knowledge question → should cache
        self.assertTrue(rc.should_cache("Что такое числа Фибоначчи?", "chat"))
        # code route → should NOT cache
        self.assertFalse(rc.should_cache("Напиши сортировку", "code"))


if __name__ == "__main__":
    unittest.main()
